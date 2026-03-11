const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

// Persistent browser profiles directory
const config = require('./config');
const BROWSER_PROFILES_DIR = config.BROWSER_PROFILES_DIR;

function getProfileDir(profileId) {
  const dir = path.join(BROWSER_PROFILES_DIR, profileId, 'browser');
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}

const SELECTORS = {
  loginEmail: 'input#email',
  loginPassword: 'input#password',
  loginSubmit: "button[type='submit']",
};

const AUTH_MARKERS = [
  'button[class*="ChatNotificationsIconButton"]',
  'a[href*="/chat?ch="]',
  'a[href^="/@"]',
  'div[class*="TopNav"]',
];

function hasAuthMarkers(page) {
  for (const selector of AUTH_MARKERS) {
    try { if (page.locator(selector).first) return true; } catch {}
  }
  return false;
}

async function loginAndStoreCookies(profileId, email, password, proxy) {
  const userDataDir = getProfileDir(profileId);

  const launchOpts = {
    userDataDir,
    headless: true,
    viewport: { width: 1600, height: 1100 },
    args: [
      '--disable-blink-features=AutomationControlled',
      '--no-sandbox',
      '--disable-dev-shm-usage',
    ],
  };

  if (proxy) {
    const parts = proxy.split(':');
    if (parts.length >= 2) {
      launchOpts.proxy = { server: `http://${parts[0]}:${parts[1]}` };
      if (parts.length === 4) {
        launchOpts.proxy.username = parts[2];
        launchOpts.proxy.password = parts[3];
      }
    }
  }

  let context;
  try {
    context = await chromium.launchPersistentContext(userDataDir, launchOpts);
  } catch (err) {
    // Retry once on connection error
    if (err.message.toLowerCase().includes('connection closed')) {
      context = await chromium.launchPersistentContext(userDataDir, launchOpts);
    } else {
      throw err;
    }
  }

  const page = context.pages[0] || await context.newPage();
  page.setDefaultTimeout(30000);

  try {
    // Step 1: Navigate to skool.com and check if already logged in
    await page.goto('https://www.skool.com/', { timeout: 14000 });
    await page.waitForLoadState('domcontentloaded');
    try { await page.waitForLoadState('networkidle', { timeout: 2000 }); } catch {}
    await page.waitForTimeout(900);

    const currentUrl = page.url().toLowerCase();

    // Already logged in?
    if (!currentUrl.includes('/login')) {
      // Check for auth markers
      let authenticated = false;
      for (const selector of AUTH_MARKERS) {
        try {
          const el = await page.$(selector);
          if (el) { authenticated = true; break; }
        } catch {}
      }
      // Also check page content
      if (!authenticated) {
        const content = await page.content();
        if (content.toLowerCase().includes('log out') || content.toLowerCase().includes('logout')) {
          authenticated = true;
        }
      }

      if (authenticated) {
        const cookies = await context.cookies();
        const cookieJson = JSON.stringify(cookies);
        await context.close();
        return { success: true, cookieJson, method: 'existing_session' };
      }
    }

    // Step 2: Go to login page
    await page.goto('https://www.skool.com/login', { timeout: 14000 });
    await page.waitForLoadState('domcontentloaded');
    try { await page.waitForLoadState('networkidle', { timeout: 2000 }); } catch {}

    // Check for blocks
    const pageContent = await page.content().catch(() => '');
    const blockKeywords = ['account suspended', 'temporarily blocked', 'access denied', 'unusual activity', 'verify you are human'];
    for (const kw of blockKeywords) {
      if (pageContent.toLowerCase().includes(kw)) {
        await context.close();
        return { success: false, error: 'blocked', message: `Blocked: ${kw}` };
      }
    }

    // Check for captcha
    const captchaFrame = await page.$('iframe[src*="captcha"]');
    if (captchaFrame || pageContent.toLowerCase().includes('captcha')) {
      await context.close();
      return { success: false, error: 'captcha', message: 'Captcha detected' };
    }

    // Step 3: Fill login form
    await page.waitForSelector(SELECTORS.loginEmail, { timeout: 8000 });
    await page.fill(SELECTORS.loginEmail, email);
    await page.fill(SELECTORS.loginPassword, password);
    await page.click(SELECTORS.loginSubmit);

    // Step 4: Wait for result
    await page.waitForTimeout(1800);

    const postLoginUrl = page.url().toLowerCase();

    if (postLoginUrl.includes('/login')) {
      // Still on login page = failed
      await context.close();
      return { success: false, error: 'invalid_credentials', message: 'Login failed — still on login page after submit' };
    }

    // Check for post-login blocks
    const postContent = await page.content().catch(() => '');
    for (const kw of blockKeywords) {
      if (postContent.toLowerCase().includes(kw)) {
        await context.close();
        return { success: false, error: 'blocked', message: `Blocked after login: ${kw}` };
      }
    }

    // Success — extract cookies
    const cookies = await context.cookies();
    const cookieJson = JSON.stringify(cookies);
    await context.close();
    return { success: true, cookieJson, method: 'login' };

  } catch (err) {
    try { await context.close(); } catch {}
    return { success: false, error: 'exception', message: err.message };
  }
}

async function validateCookies(cookieJson) {
  try {
    const cookies = JSON.parse(cookieJson);
    const cookieHeader = cookies.map(c => `${c.name}=${c.value}`).join('; ');
    const res = await fetch('https://api2.skool.com/self', {
      headers: { cookie: cookieHeader }
    });
    if (res.ok) {
      const data = await res.json();
      return { valid: true, user: data };
    }
    return { valid: false, error: `HTTP ${res.status}` };
  } catch (err) {
    return { valid: false, error: err.message };
  }
}

module.exports = { loginAndStoreCookies, validateCookies };
