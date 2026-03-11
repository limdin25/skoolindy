const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const config = require('./config');
const BROWSER_PROFILES_DIR = config.BROWSER_PROFILES_DIR;

function getProfileDir(profileId) {
  const dir = path.join(BROWSER_PROFILES_DIR, profileId, 'browser');
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

function slugifyName(s) {
  return (s || '')
    .toString()
    .toLowerCase()
    .trim()
    .replace(/['"]/g, '')
    .replace(/[^a-z0-9]+/g, '-')
    .replace(/^-+|-+$/g, '');
}

// Canonical field patterns for fuzzy matching
const FIELD_PATTERNS = {
  email:      /email|mail|adresse|correo|e-mail/i,
  first_name: /first.*name|given.*name|vorname|nome|prénom|prenom|your.*name|full.*name|fullname/i,
  full_name: /full\s*name|fullname|what.*your\s+name/i,
  last_name:  /last.*name|surname|family.*name|nachname|cognome/i,
  phone:      /phone|mobile|tel|telefone|numero|whatsapp|cell/i,
  company:    /company|firma|entreprise|empresa|organization|org|business/i,
  website:    /website|site|url|domain|web|link/i,
  linkedin:   /linkedin/i,
  instagram:  /instagram|ig\b/i,
  twitter:    /twitter|x\.com/i,
  facebook:   /facebook|fb/i,
  youtube:    /youtube/i,
  tiktok:     /tiktok/i,
  how_found:  /how.*find|how.*hear|where.*find|referr|how.*discover|source/i,
  occupation: /what.*do|business|occupation|living|profession|role|work|job|career/i,
  why_join:   /why.*join|goal|looking|interest|expect|hope|want|reason|motivation|excited/i,
  experience: /experience|level|background|skill|how.*long|familiar/i,
  bio:        /about|tell.*us|describe|bio|introduce|yourself|who.*are/i,
};

const ACCEPT_PATTERNS = /^(yes|sure|agree|accept|subscribe|join|opt.*in|confirm|continue|start|sign.*up|i.*agree|definitely|absolutely|of.*course|ready|let.*go|i.*do|i.*am|i.*have|interested|already|regularly|daily|intermediate|beginner|advanced|expert|checked|true|1)$/i;
const DECLINE_PATTERNS = /^(no|decline|opt.*out|do.*not|unsubscribe|leave|cancel|disagree|never|not.*yet|false|0)$/i;

// Join-style button patterns
const JOIN_BUTTON_PATTERNS = /join.*(community|group|team|waitlist|list|form|free)?|sign.*up|register|subscribe|request.*to.*join|apply|submit|send|continue|next|start/i;

/**
 * Join a Skool community — god-mode browser automation.
 */
async function joinCommunity(profileId, groupSlug, profileInfo = {}) {
  const userDataDir = getProfileDir(profileId);

  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1600, height: 1100 },
    args: [
      '--disable-blink-features=AutomationControlled',
      '--no-sandbox',
      '--disable-dev-shm-usage',
    ],
  });

  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(30000);
  page.on('request', r => { const u = r.url(); if (u.includes('api2.skool.com') || u.includes('skool.com')) console.log('REQ ' + r.method() + ' ' + u); });

  try {
    // Navigate and capture HTTP status
    const response = await page.goto(`https://www.skool.com/${groupSlug}`, { timeout: 20000 });
    const httpStatus = response ? response.status() : 0;
    await page.waitForLoadState('domcontentloaded');
    try { await page.waitForLoadState('networkidle', { timeout: 5000 }); } catch {}
    await page.waitForTimeout(1500);

    // TRUE 404: only if HTTP status is 404/5xx
    if (httpStatus === 404 || httpStatus >= 500) {
      await context.close();
      return { success: false, status: 'failed', message: `HTTP ${httpStatus} — page not found` };
    }

    // Session expired?
    if (page.url().toLowerCase().includes('/login')) {
      await context.close();
      return { success: false, status: 'failed', message: 'Session expired — redirected to login' };
    }

    const pageText = await page.textContent('body').catch(() => '');

    // Already a member?
    if (/leave\s+group/i.test(pageText)) {
      await context.close();
      return { success: true, status: 'joined', message: 'Already a member' };
    }

    // Find join button using fuzzy matching
    let joinBtn = null;
    const allButtons = await page.locator('button, a[role="button"]').all();
    
    for (const btn of allButtons) {
      try {
        const text = (await btn.textContent() || '').trim();
        const aria = (await btn.getAttribute('aria-label') || '').trim();
        const title = (await btn.getAttribute('title') || '').trim();
        const combined = `${text} ${aria} ${title}`;
        
        if (JOIN_BUTTON_PATTERNS.test(combined) && await btn.isVisible()) {
          // Check if free trial (button says "START FREE TRIAL" etc.)
          if (/free\s*trial|start\s*trial/i.test(text)) {
            await context.close();
            return { success: false, status: 'free_trial', message: `Free trial community — button: ${text.substring(0, 50)}` };
          }
          // Check if paid (button text has price)
          if (/\$\d|£\d|€\d|\/month|\/year|per\s+month/i.test(text)) {
            await context.close();
            return { success: false, status: 'skipped_paid', message: `Paid community — button: ${text.substring(0, 50)}` };
          }
          joinBtn = btn;
          break;
        }
      } catch {}
    }

    if (!joinBtn) {
      // Page is live (HTTP 200) but no join button — might already be joined or private
      if (/leave\s+group/i.test(pageText)) {
        await context.close();
        return { success: true, status: 'joined', message: 'Already a member' };
      }
      await context.close();
      return { success: false, status: 'failed', message: 'No join button found (page is live but no join CTA)' };
    }

    await joinBtn.click();
    await page.waitForTimeout(2500);

    // Debug screenshot
    const debugDir = path.join(__dirname, 'debug_screenshots');
    fs.mkdirSync(debugDir, { recursive: true });
    await page.screenshot({ path: path.join(debugDir, `after_join_${groupSlug}_${Date.now()}.png`), fullPage: true }).catch(() => {});

    // Check if already joined directly (no survey at all) - BEFORE checking for pending popup
    const afterText = await page.textContent('body').catch(() => '');
    if (/leave\s+group/i.test(afterText)) {
      await context.close();
      return { success: true, status: 'joined', message: 'Joined directly (no survey)' };
    }

    // ========== GOD-MODE SURVEY HANDLER ==========
    // If the page has ANY form elements, treat it as a survey and fill everything.
    
    const hasFormElements = await page.evaluate(() => {
      const inputs = document.querySelectorAll('input[type="text"], input[type="email"], textarea');
      const radios = document.querySelectorAll('input[type="radio"]');
      const checkboxes = document.querySelectorAll('input[type="checkbox"]');
      // Buttons that look like options (Yes/No/Agree)
      const optionButtons = Array.from(document.querySelectorAll('button')).filter(b => 
        /^(yes|no|agree|not|sure|maybe|definitely|already|interested|beginner|intermediate|advanced|expert)/i.test(b.textContent?.trim())
      );
      return inputs.length + radios.length + checkboxes.length + optionButtons.length;
    });

    if (hasFormElements === 0) {
      // Check if this is a paid/trial community (pricing page shown instead of form)
      const afterJoinText = await page.textContent('body').catch(() => '');
      const afterJoinLower = afterJoinText.toLowerCase();
      if (/free\s*trial|start\s*trial|join.*\$\d|price|pricing|pay|month|year|subscription|credit\s*card|billing/i.test(afterJoinLower)) {
        await context.close();
        return { success: false, status: 'free_trial', message: 'Paid/Trial community - pricing page shown instead of join form' };
      }
      await context.close();
      return { success: true, status: 'pending', message: 'Join clicked — no form detected, marking pending' };
    }

    console.log(`[${groupSlug}] Survey detected: ${hasFormElements} form elements`);
    await page.waitForTimeout(500);

    // --- FILL TEXT FIELDS (using Playwright .fill() for React compat) ---
    const textFields = await page.locator('input[type="text"], input[type="email"], textarea').all();
    let fieldsFilled = 0;
    
    for (const field of textFields) {
      try {
        if (!await field.isVisible()) continue;
        const currentVal = await field.inputValue().catch(() => '');
        if (currentVal && currentVal.length > 2) { fieldsFilled++; continue; }

        // Gather all context about this field
        const placeholder = (await field.getAttribute('placeholder') || '').toLowerCase();
        const fieldType = (await field.getAttribute('type') || '').toLowerCase();
        const fieldName = (await field.getAttribute('name') || '').toLowerCase();
        const fieldId = (await field.getAttribute('id') || '').toLowerCase();
        const ariaLabel = (await field.getAttribute('aria-label') || '').toLowerCase();
        
        // Get parent/nearby text
        let parentText = '';
        try {
          parentText = await field.evaluate(el => {
            // Walk up to find label or question text
            let node = el;
            for (let i = 0; i < 5; i++) {
              node = node.parentElement;
              if (!node) break;
              const text = node.textContent || '';
              if (text.length > 5 && text.length < 500) return text;
            }
            return '';
          });
          parentText = (parentText || '').toLowerCase();
        } catch {}

        const ctx = [placeholder, fieldType, fieldName, fieldId, ariaLabel, parentText].join(' ');

        // Fuzzy match against canonical patterns
        let value = '';
        for (const [key, pattern] of Object.entries(FIELD_PATTERNS)) {
          if (pattern.test(ctx)) {
            value = getFieldValue(key, profileInfo);
            break;
          }
        }

        // Fallback: if no match and it looks like email type, use email
        if (!value && fieldType === 'email') {
          value = profileInfo.email || 'hugords100@gmail.com';
        }
        
        // Fallback: fill with a generic answer
        if (!value) {
          const GENERIC_FALLBACKS = ['Looking forward to learning and contributing!', 'I want to grow, connect with like-minded people, and learn from the community.', 'Through social media']; value = GENERIC_FALLBACKS[Math.floor(Math.random() * 3)];
        }

        await field.click();
        await field.fill(value);
        await page.waitForTimeout(150);
        fieldsFilled++;
        console.log(`  [${groupSlug}] Filled: "${ctx.substring(0, 60)}" → "${value.substring(0, 40)}"`);
      } catch (e) {
        console.log(`  [${groupSlug}] Field error: ${e.message.substring(0, 60)}`);
      }
    }

    // --- CLICK OPTION BUTTONS (Yes/No rendered as buttons, not radios) ---
    let optionsClicked = 0;
    const allBtns = await page.locator('button').all();
    const clickedTexts = new Set();
    
    for (const btn of allBtns) {
      try {
        if (!await btn.isVisible()) continue;
        const text = (await btn.textContent() || '').trim();
        if (text.length > 50) continue; // Too long to be an option button
        if (clickedTexts.has(text.toLowerCase())) continue;
        
        // Check if this looks like an option (not a navigation/submit button)
        if (JOIN_BUTTON_PATTERNS.test(text)) continue; // Don't click submit/join in option phase
        if (ACCEPT_PATTERNS.test(text)) {
          await btn.click();
          clickedTexts.add(text.toLowerCase());
          optionsClicked++;
          console.log(`  [${groupSlug}] Clicked accept option: "${text}"`);
          await page.waitForTimeout(300);
        }
      } catch {}
    }

    // If no accept option was clicked, try clicking first non-decline short button
    if (optionsClicked === 0) {
      for (const btn of allBtns) {
        try {
          if (!await btn.isVisible()) continue;
          const text = (await btn.textContent() || '').trim();
          if (text.length > 30) continue;
          if (DECLINE_PATTERNS.test(text)) continue;
          if (JOIN_BUTTON_PATTERNS.test(text)) continue; // Don't click submit yet
          if (/log\s*in|sign\s*in/i.test(text)) continue; // Auth links - would navigate away
          if (/close|cancel|back|×|✕/i.test(text)) continue;
          
          // Looks like a short option — click it
          await btn.click();
          optionsClicked++;
          console.log(`  [${groupSlug}] Fallback option click: "${text}"`);
          await page.waitForTimeout(300);
          break;
        } catch {}
      }
    }

    // --- HANDLE ACTUAL RADIO INPUTS (if they exist) ---
    const radioInputs = await page.locator('input[type="radio"]').all();
    const radioGroups = new Set();
    
    for (const radio of radioInputs) {
      try {
        if (await radio.isChecked()) continue;
        const name = await radio.getAttribute('name');
        if (name && radioGroups.has(name)) continue;

        // Get label
        let labelText = '';
        try {
          const id = await radio.getAttribute('id');
          if (id) { const lbl = await page.$(`label[for="${id}"]`); if (lbl) labelText = await lbl.textContent(); }
        } catch {}
        if (!labelText) {
          try { labelText = await radio.evaluate(el => el.closest('label')?.textContent || ''); } catch {}
        }

        const value = (await radio.getAttribute('value') || '').toLowerCase();
        const ctx = value + ' ' + (labelText || '').toLowerCase();

        if (ACCEPT_PATTERNS.test(ctx.trim()) || !DECLINE_PATTERNS.test(ctx.trim())) {
          await radio.click();
          if (name) radioGroups.add(name);
          await page.waitForTimeout(200);
        }
      } catch {}
    }

    // Fallback: click first unchecked radio per group
    for (const radio of radioInputs) {
      try {
        const name = await radio.getAttribute('name');
        if (name && radioGroups.has(name)) continue;
        if (!await radio.isChecked()) {
          await radio.click();
          if (name) radioGroups.add(name);
        }
      } catch {}
    }

    // --- HANDLE CHECKBOXES (check all that look like consent) ---
    const checkboxes = await page.locator('input[type="checkbox"]').all();
    for (const cb of checkboxes) {
      try {
        if (await cb.isChecked()) continue;
        
        let labelText = '';
        try {
          const id = await cb.getAttribute('id');
          if (id) { const lbl = await page.$(`label[for="${id}"]`); if (lbl) labelText = await lbl.textContent(); }
        } catch {}
        if (!labelText) {
          try { labelText = await cb.evaluate(el => el.closest('label')?.textContent || ''); } catch {}
        }

        const ctx = (labelText || '').toLowerCase();
        // Check all UNLESS it looks like opt-out
        if (!DECLINE_PATTERNS.test(ctx)) {
          await cb.click();
          await page.waitForTimeout(150);
        }
      } catch {}
    }

    // --- HANDLE SELECT DROPDOWNS ---
    const selects = await page.locator('select').all();
    for (const select of selects) {
      try {
        const options = await select.locator('option').all();
        if (options.length > 1) {
          const val = await options[1].getAttribute('value');
          if (val) await select.selectOption(val);
        }
      } catch {}
    }

    await page.waitForTimeout(800);

    // Screenshot after filling
    await page.screenshot({ path: path.join(debugDir, `survey_filled_${groupSlug}_${Date.now()}.png`), fullPage: true }).catch(() => {});

    console.log(`[${groupSlug}] Survey filled: ${fieldsFilled} fields, ${optionsClicked} options`);

    // --- CLICK SUBMIT/JOIN BUTTON ---
    let submitted = false;
    
    // Re-scan buttons after filling (some may have become enabled)
    const submitButtons = await page.locator('button').all();
    for (const btn of submitButtons) {
      try {
        if (!await btn.isVisible()) continue;
        const isDisabled = await btn.isDisabled();
        const text = (await btn.textContent() || '').trim();
        const aria = (await btn.getAttribute('aria-label') || '').trim();
        const combined = `${text} ${aria}`;

        if (JOIN_BUTTON_PATTERNS.test(combined) && !isDisabled) {
          console.log(`  [${groupSlug}] Clicking submit: "${text}" (disabled=${isDisabled})`);
          submitted = true;
          await btn.click();

          break;
        }
      } catch {}
    }

    await page.waitForTimeout(3000);

    // Check final result
    const finalText = await page.textContent('body').catch(() => '');
    
    if (/leave\s+group/i.test(finalText)) {
      await context.close();
      return { success: true, status: 'joined', message: 'Joined after survey' };
    }
    
    if (/membership.*pending|pending.*review|request.*sent|your.*request|submitted|thank/i.test(finalText)) {
      try { await page.locator('button:has-text("GOT IT")').click({ timeout: 2000 }); } catch {}
      await context.close();
      return { success: true, status: 'survey_submitted', message: 'Survey submitted — pending approval' };
    }

    // Screenshot final state
    await page.screenshot({ path: path.join(debugDir, `final_${groupSlug}_${Date.now()}.png`), fullPage: true }).catch(() => {});

    if (submitted) {
      await context.close();
      return { success: true, status: 'survey_submitted', message: `Survey filled (${fieldsFilled} fields, ${optionsClicked} options) — submitted` };
    }

    await context.close();
    return { success: false, status: 'failed', message: `Survey detected but submit button still disabled after filling ${fieldsFilled} fields and ${optionsClicked} options` };

  } catch (err) {
    try { await context.close(); } catch {}
    return { success: false, status: 'failed', message: err.message };
  }
}

function getFieldValue(key, profileInfo) {
  const defaults = {
    email: 'hugords100@gmail.com',
    first_name: 'Hugo',
    last_name: 'Rodriguez',
    phone: '+44 7412 345678',
    company: 'Digital Marketing Solutions',
    website: 'https://hugorodriguez.com',
    linkedin: 'https://linkedin.com/in/hugorodriguez',
    instagram: '@hugo_marketing',
    twitter: '@hugo_mkt',
    facebook: 'Hugo Rodriguez',
    youtube: 'Hugo Rodriguez',
    tiktok: '@hugo_marketing',
    how_found: 'Found through Skool search while looking for communities in this niche',
    occupation: 'Digital marketer and entrepreneur focused on affiliate marketing and AI automation',
    why_join: 'Looking to learn new strategies, connect with like-minded people, and grow my business',
    experience: 'Intermediate — been in digital marketing for a few years, always learning new approaches',
    bio: 'Digital marketer focused on affiliate marketing, passive income, and AI automation. Always looking to learn and connect with others.',
  };

  // Check profileInfo first, then defaults
  const fn = profileInfo.full_name || profileInfo.fullname || ''; if (key === 'full_name') return fn || defaults.full_name || defaults.first_name + ' ' + defaults.last_name; if (key === 'first_name') return (fn && fn.split(' ')[0]) || profileInfo.first_name || defaults.first_name;   return profileInfo[key] || defaults[key] || '';
}

/**
 * Fetch communities from skool.com/settings?t=communities.
 * Clicks Settings on each community to get status and requestedAt from modal.
 * Falls back to visiting each page if no Settings buttons found.
 */
async function fetchCommunitiesFromSkoolSettings(profileId, onProgress = () => {}, fallbackCookieJson = null) {
  const userDataDir = getProfileDir(profileId);
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(20000);
  const result = { joined: [], pending: [], error: null };
  try {
    onProgress({ status: 'Opening settings page', current: 0, total: 1 });
    const res = await page.goto('https://www.skool.com/settings?t=communities', { timeout: 20000 });
    if (res && (res.status() === 404 || res.status() >= 500)) {
      result.error = 'Page not found';
      await context.close();
      return result;
    }
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    if (page.url().toLowerCase().includes('/login')) {
      result.error = 'Session expired — please re-connect cookies';
      await context.close();
      return result;
    }
    onProgress({ status: 'Finding communities list', current: 0, total: 1 });
    const settingsBtns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
    if (settingsBtns.length > 0) {
      const total = settingsBtns.length;
      onProgress({ status: 'Checking communities', current: 0, total });
      for (let i = 0; i < settingsBtns.length; i++) {
        try {
          const btns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
          const btn = btns[i];
          if (!btn) continue;
          onProgress({ status: `Opening Settings (${i + 1}/${total})`, current: i + 1, total });
          await btn.click();
          await page.waitForTimeout(2000);
          const bodyText = await page.textContent('body');
          const bodyLower = (bodyText || '').toLowerCase();
          const requestedMatch = bodyText.match(/you requested membership to ([^.©]+?)\s*[©]?\s*on\s+(\d{1,2}\/\d{1,2}\/\d{4})/i);
          let name = '';
          let requestedAt = null;
          if (requestedMatch) {
            name = requestedMatch[1].trim();
            requestedAt = requestedMatch[2];
          } else {
            const titleMatch = bodyText.match(/([A-Z0-9][A-Za-z0-9\s]+(?:©)?)\s*Membership settings/i);
            if (titleMatch) name = titleMatch[1].replace(/\s*©\s*$/, '').trim();
          }
          const slug = name ? slugifyName(name) : `community-${i + 1}`;
          const isPending = /you requested membership/i.test(bodyLower) || /cancel request/i.test(bodyLower);
          if (isPending) result.pending.push({ slug, name: name || slug.replace(/-/g, ' '), requestedAt });
          else result.joined.push({ slug, name: name || slug.replace(/-/g, ' ') });
          await page.keyboard.press('Escape');
          await page.waitForTimeout(800);
        } catch (e) { console.warn('[fetch] Row', i, e.message); }
      }
      await context.close();
      return result;
    }
    const slugSet = new Set();
    const skip = ['settings', 'profile', 'discover', 'login', 'logout', 'chat', 'notifications', 'affiliates', 'payouts', 'account', 'payment', 'theme'];
    const allLinks = await page.locator('a[href*="skool.com/"], a[href^="/"]').all();
    for (const a of allLinks) {
      try {
        const href = (await a.getAttribute('href')) || '';
        const m = href.match(/skool\.com\/([a-zA-Z0-9-_]+)/) || href.match(/^\/([a-zA-Z0-9-_]+)/);
        if (m) {
          const slug = m[1].toLowerCase();
          const first = slug.split('/')[0];
          if (slug.length > 2 && !slugSet.has(slug) && !skip.includes(first)) {
            slugSet.add(slug);
          }
        }
      } catch {}
    }
    let communitySlugs = [...slugSet];
    if (communitySlugs.length === 0 && fallbackCookieJson) {
      try {
        const { getGroups } = require('./skoolApi');
        const data = await getGroups(fallbackCookieJson);
        const groups = data?.groups || data?.data || data || [];
        for (const g of groups) {
          const slug = (g.slug || g.groupSlug || g.name || '').toString().trim().toLowerCase();
          if (slug) communitySlugs.push(slug);
        }
      } catch (_) {}
    }
    const total = communitySlugs.length;
    onProgress({ status: total ? 'Checking communities' : 'No communities found', current: 0, total });
    for (let i = 0; i < communitySlugs.length; i++) {
      const slug = communitySlugs[i];
      onProgress({ status: `Visiting community: ${slug}`, current: i + 1, total, community: slug });
      let status = 'joined';
      try {
        const res2 = await page.goto(`https://www.skool.com/${slug}`, { timeout: 15000 });
        await page.waitForLoadState('domcontentloaded');
        await page.waitForTimeout(2500);
        if (res2 && (res2.status() === 404 || res2.status() >= 500)) { status = 'error'; }
        else if (page.url().toLowerCase().includes('/login')) { status = 'error'; }
        else {
          const txt = (await page.textContent('body')).toLowerCase();
          if (/invite\s+people|invite\s+others|invite\s+members|leave\s+group/i.test(txt)) status = 'joined';
          else if (/pending/i.test(txt) || /you requested membership/i.test(txt) || /cancel request/i.test(txt)) status = 'pending';
          else status = 'joined';
        }
        if (status === 'pending') result.pending.push({ slug, name: slug.replace(/-/g, ' '), requestedAt: null });
        else if (status !== 'error') result.joined.push({ slug, name: slug.replace(/-/g, ' ') });
      } catch (e) { result.joined.push({ slug, name: slug.replace(/-/g, ' ') }); }
    }
    await context.close();
    return result;
  } catch (e) {
    result.error = e.message;
    try { await context.close(); } catch {}
    return result;
  }
}

/**
 * Check if user is pending or joined on a community page.
 * Uses page button text: "Pending" = pending, "Invite people"/"Invite others"/"Leave group" = joined.
 */
async function checkCommunityMembershipStatus(profileId, groupSlug) {
  const userDataDir = getProfileDir(profileId);
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1280, height: 800 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(15000);
  try {
    const res = await page.goto(`https://www.skool.com/${groupSlug}`, { timeout: 15000 });
    if (res && (res.status() === 404 || res.status() >= 500)) {
      await context.close();
      return { status: 'error', message: `HTTP ${res.status()}` };
    }
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    if (page.url().toLowerCase().includes('/login')) {
      await context.close();
      return { status: 'error', message: 'Session expired' };
    }
    const pageText = (await page.textContent('body')).toLowerCase();
    if (/leave\s+group/i.test(pageText) || /invite\s+people/i.test(pageText) || /invite\s+others/i.test(pageText) || /invite\s+members/i.test(pageText)) {
      await context.close();
      return { status: 'joined' };
    }
    if (/pending/i.test(pageText)) {
      const buttons = await page.locator('button, a[role="button"]').all();
      for (const btn of buttons) {
        const t = (await btn.textContent() || '').trim().toLowerCase();
        if (t === 'pending') {
          await context.close();
          return { status: 'pending' };
        }
        if (t.includes('invite')) {
          await context.close();
          return { status: 'joined' };
        }
      }
      await context.close();
      return { status: 'pending' };
    }
    await context.close();
    return { status: 'error', message: 'Could not determine status' };
  } catch (e) {
    try { await context.close(); } catch {}
    return { status: 'error', message: e.message };
  }
}

async function cancelRequestOnSkool(profileId, communitySlug) {
  const userDataDir = getProfileDir(profileId);
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(20000);
  try {
    await page.goto('https://www.skool.com/settings?t=communities', { timeout: 20000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    if (page.url().toLowerCase().includes('/login')) {
      await context.close();
      return { success: false, error: 'Session expired' };
    }
    const settingsBtns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
    for (let i = 0; i < settingsBtns.length; i++) {
      const btns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
      await btns[i].click();
      await page.waitForTimeout(2000);
      const bodyText = await page.textContent('body');
      const bodyLower = (bodyText || '').toLowerCase();
      const requestedMatch = bodyText?.match(/you requested membership to ([^.©]+?)\s*[©]?\s*on\s+/i);
      const titleMatch = bodyText?.match(/([A-Z0-9][A-Za-z0-9\s]+(?:©)?)\s*Membership settings/i);
      const modalName = (requestedMatch?.[1] || titleMatch?.[1]?.replace(/\s*©\s*$/g, '') || '').trim();
      const modalSlug = modalName ? slugifyName(modalName.replace(/©|™|®/g, '')) : '';
      const slugLower = communitySlug.toLowerCase();
      const matches = /cancel request/i.test(bodyLower) && (bodyLower.includes(slugLower) || bodyLower.includes(communitySlug.replace(/-/g, ' ')) || (modalSlug && modalSlug === slugLower));
      if (matches) {
        const cancelLink = page.locator('text=Cancel Request').first();
        if (await cancelLink.isVisible()) {
          await cancelLink.click();
          await page.waitForTimeout(2000);
          await page.keyboard.press('Escape');
          await context.close();
          return { success: true };
        }
      }
      await page.keyboard.press('Escape');
      await page.waitForTimeout(800);
    }
    await context.close();
    return { success: false, error: 'Community not found or not pending' };
  } catch (e) {
    try { await context.close(); } catch {}
    return { success: false, error: e.message };
  }
}


async function leaveGroupOnSkool(profileId, communitySlug) {
  const userDataDir = getProfileDir(profileId);
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(20000);
  try {
    await page.goto('https://www.skool.com/' + communitySlug, { timeout: 20000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    if (page.url().toLowerCase().includes('/login')) {
      await context.close();
      return { success: false, error: 'Session expired' };
    }
    const result = await page.evaluate(async (slug) => {
      const r = await fetch('https://api2.skool.com/groups/' + slug + '/leave', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        
      });
      return { ok: r.ok, status: r.status };
    }, communitySlug);
    await context.close();
    if (result.ok) return { success: true };
    return { success: false, error: 'Leave failed: HTTP ' + result.status };
  } catch (e) {
    try { await context.close(); } catch {}
    return { success: false, error: e.message };
  }
}


async function cancelJoinViaFetch(profileId, slug) {
  const userDataDir = getProfileDir(profileId);
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(20000);
  try {
    await page.goto('https://www.skool.com/settings?t=communities', { timeout: 15000 });
    if (page.url().toLowerCase().includes('/login')) {
      await context.close();
      return { ok: false, status: 401, error: 'Session expired' };
    }
    const result = await page.evaluate(async (s) => {
      const r = await fetch('https://api2.skool.com/groups/' + encodeURIComponent(s) + '/cancel-join', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        
      });
      return { ok: r.ok, status: r.status };
    }, slug);
    await context.close();
    return { ok: result.ok, status: result.status };
  } catch (e) {
    try { await context.close(); } catch {}
    return { ok: false, status: 0, error: e.message };
  }
}

async function leaveViaFetch(profileId, slug) {
  const userDataDir = getProfileDir(profileId);
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1400, height: 900 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox', '--disable-dev-shm-usage'],
  });
  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(20000);
  try {
    await page.goto('https://www.skool.com/settings?t=communities', { timeout: 15000 });
    if (page.url().toLowerCase().includes('/login')) {
      await context.close();
      return { ok: false, status: 401, error: 'Session expired' };
    }
    const result = await page.evaluate(async (s) => {
      const r = await fetch('https://api2.skool.com/groups/' + encodeURIComponent(s) + '/leave', {
        method: 'POST',
        credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
      });
      return { ok: r.ok, status: r.status };
    }, slug);
    await context.close();
    return { ok: result.ok, status: result.status };
  } catch (e) {
    try { await context.close(); } catch {}
    return { ok: false, status: 0, error: e.message };
  }
}

module.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool, leaveGroupOnSkool, cancelJoinViaFetch, leaveViaFetch };
