const { chromium } = require('playwright');
const path = require('path');

const BROWSER_PROFILES_DIR = path.join(__dirname, 'skool_accounts');
const PROFILE_ID = 'd56f73d2-08bc-4412-a018-960fe89362ad';
const GROUP_SLUG = 'freegroup';

async function testJoin() {
  const userDataDir = path.join(BROWSER_PROFILES_DIR, PROFILE_ID, 'browser');
  
  console.log(`[TEST] Target: https://www.skool.com/${GROUP_SLUG}`);
  
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1600, height: 1100 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox'],
  });

  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(30000);
  
  page.on('request', r => {
    const u = r.url();
    if (u.includes('api2.skool.com') || u.includes('skool.com/api')) {
      console.log('REQ ' + r.method() + ' ' + u);
    }
  });
  
  page.on('response', r => {
    const u = r.url();
    if (u.includes('join-group') || u.includes('pending') || u.includes('survey') || u.includes('answers')) {
      console.log('RES ' + r.status() + ' ' + u);
    }
  });

  try {
    console.log(`[TEST] Navigating...`);
    await page.goto(`https://www.skool.com/${GROUP_SLUG}`, { timeout: 20000 });
    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);

    if (page.url().includes('/login')) {
      console.log('[TEST] FAILED: Redirected to login');
      await context.close();
      return;
    }

    // Find and click join
    const joinBtn = await page.$('button:has-text("JOIN GROUP"), button:has-text("Join")');
    if (!joinBtn) {
      console.log('[TEST] No Join button - checking if already member');
      await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
      await page.waitForTimeout(2000);
      console.log(`[PROOF] Classroom URL: ${page.url()}`);
      const hasJoin = await page.$('button:has-text("Join")');
      console.log(`[PROOF] Join button in classroom: ${!!hasJoin}`);
      await page.screenshot({ path: path.join(__dirname, 'test-join-v3.png') });
      await context.close();
      return;
    }

    console.log('[TEST] Clicking JOIN GROUP...');
    await joinBtn.click();
    await page.waitForTimeout(3000);

    // Handle survey modal
    console.log('[TEST] Looking for survey modal...');
    await page.screenshot({ path: path.join(__dirname, 'test-join-modal.png') });
    
    // Fill all textareas and inputs using JavaScript evaluation
    await page.evaluate(() => {
      const inputs = document.querySelectorAll('input[type="text"], input[type="email"], textarea');
      inputs.forEach(input => {
        const placeholder = (input.placeholder || '').toLowerCase();
        const name = (input.name || '').toLowerCase();
        if (placeholder.includes('email') || name.includes('email')) {
          input.value = 'hugords100@gmail.com';
        } else {
          input.value = 'Interested in learning AI automation and building profitable systems';
        }
        input.dispatchEvent(new Event('input', { bubbles: true }));
        input.dispatchEvent(new Event('change', { bubbles: true }));
      });
      
      // Check all checkboxes
      document.querySelectorAll('input[type="checkbox"]').forEach(cb => {
        cb.checked = true;
        cb.dispatchEvent(new Event('change', { bubbles: true }));
      });
    });
    
    await page.waitForTimeout(1000);
    
    // Click submit with force
    console.log('[TEST] Clicking submit...');
    const submitBtn = await page.$('button:has-text("Submit"), button:has-text("Continue"), button:has-text("Next"), button[type="submit"]');
    if (submitBtn) {
      await submitBtn.click({ force: true });
      console.log('[TEST] Submit clicked (forced)');
      await page.waitForTimeout(4000);
    } else {
      console.log('[TEST] No submit button found');
    }

    await page.screenshot({ path: path.join(__dirname, 'test-join-after-submit.png') });
    console.log(`[TEST] URL after submit: ${page.url()}`);

    // PROOF: Navigate to classroom
    console.log('\n[PROOF] Navigating to classroom...');
    await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    
    const classroomUrl = page.url();
    const title = await page.title();
    
    console.log(`[PROOF] Classroom URL: ${classroomUrl}`);
    console.log(`[PROOF] Page title: ${title}`);
    
    // Check for join button
    const joinBtnInClassroom = await page.$('button:has-text("Join")');
    console.log(`[PROOF] Join button exists: ${!!joinBtnInClassroom}`);
    
    // Check for member-only content
    const bodyText = await page.textContent('body');
    const hasModules = /module|lesson|course/i.test(bodyText);
    const hasLocked = /locked|unlock/i.test(bodyText);
    console.log(`[PROOF] Has modules/lessons: ${hasModules}`);
    console.log(`[PROOF] Has locked content: ${hasLocked}`);
    
    // WAF check
    if (/cloudflare|blocked|captcha|waf/i.test(bodyText)) {
      console.log('[PROOF] WARNING: WAF detected!');
    }
    
    // Redirect check
    if (classroomUrl.includes('/login') || !classroomUrl.includes('/classroom')) {
      console.log(`[PROOF] WARNING: Redirected away from classroom!`);
    }
    
    await page.screenshot({ path: path.join(__dirname, 'test-join-v3.png'), fullPage: false });
    console.log('[PROOF] Screenshot: test-join-v3.png');

  } catch (err) {
    console.error('[TEST] Error:', err.message);
    await page.screenshot({ path: path.join(__dirname, 'test-join-error.png') }).catch(() => {});
  } finally {
    await context.close();
    console.log('[TEST] Done');
  }
}

testJoin().catch(console.error);
