const { chromium } = require('playwright');
const path = require('path');
const fs = require('fs');

const BROWSER_PROFILES_DIR = path.join(__dirname, 'skool_accounts');
const PROFILE_ID = 'd56f73d2-08bc-4412-a018-960fe89362ad';
const GROUP_SLUG = 'freegroup';

async function testJoin() {
  const userDataDir = path.join(BROWSER_PROFILES_DIR, PROFILE_ID, 'browser');
  
  console.log(`[TEST] Using profile dir: ${userDataDir}`);
  console.log(`[TEST] Joining: https://www.skool.com/${GROUP_SLUG}`);
  
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1600, height: 1100 },
    args: [
      '--disable-blink-features=AutomationControlled',
      '--no-sandbox',
      '--disable-dev-shm-usage',
    ],
  });

  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(30000);
  
  // Log all API requests
  page.on('request', r => {
    const u = r.url();
    if (u.includes('api2.skool.com') || u.includes('skool.com/api')) {
      console.log('REQ ' + r.method() + ' ' + u);
    }
  });

  try {
    // Navigate to group
    console.log(`[TEST] Navigating to group page...`);
    const response = await page.goto(`https://www.skool.com/${GROUP_SLUG}`, { timeout: 20000 });
    const httpStatus = response ? response.status() : 0;
    console.log(`[TEST] HTTP status: ${httpStatus}`);
    
    await page.waitForLoadState('domcontentloaded');
    try { await page.waitForLoadState('networkidle', { timeout: 5000 }); } catch {}
    await page.waitForTimeout(1500);

    // Check if redirected to login
    if (page.url().toLowerCase().includes('/login')) {
      console.log('[TEST] FAILED: Session expired — redirected to login');
      await context.close();
      return;
    }

    const pageText = await page.textContent('body').catch(() => '');
    console.log(`[TEST] Page URL: ${page.url()}`);

    // Already a member?
    if (/leave\s+group/i.test(pageText)) {
      console.log('[TEST] Already a member — Leave Group button found');
    }

    // Find join button
    const joinBtnText = await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      const joinBtn = btns.find(b => /^join/i.test(b.textContent.trim()));
      return joinBtn ? joinBtn.textContent.trim() : null;
    });
    
    if (joinBtnText) {
      console.log(`[TEST] Found join button: "${joinBtnText}"`);
      
      // Click join button
      console.log('[TEST] Clicking join button...');
      await page.click('button:has-text("Join")');
      await page.waitForTimeout(3000);
      
      console.log('[TEST] Join button clicked');
      console.log(`[TEST] Current URL: ${page.url()}`);
    } else {
      console.log('[TEST] No join button found');
    }

    // PROOF: Navigate to classroom
    console.log('\n[PROOF] Navigating to classroom...');
    await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    
    const classroomUrl = page.url();
    const title = await page.title();
    const classroomText = await page.textContent('body').catch(() => '');
    
    console.log(`[PROOF] Classroom URL: ${classroomUrl}`);
    console.log(`[PROOF] Page title: ${title}`);
    
    // Check for join button in classroom
    const hasJoinBtn = /join/i.test(await page.evaluate(() => {
      const btns = Array.from(document.querySelectorAll('button'));
      return btns.map(b => b.textContent).join(' ');
    }));
    
    console.log(`[PROOF] Join button exists: ${hasJoinBtn}`);
    console.log(`[PROOF] Has classroom content: ${/classroom|module|lesson|course/i.test(classroomText)}`);
    
    // Check for WAF or redirect
    if (classroomUrl.includes('waf') || classroomUrl.includes('blocked')) {
      console.log('[PROOF] WARNING: WAF detected!');
    }
    if (classroomUrl.includes('login')) {
      console.log('[PROOF] WARNING: Redirected to login!');
    }

    // Take screenshot
    const screenshotPath = path.join(__dirname, 'test-join-proof.png');
    await page.screenshot({ path: screenshotPath, fullPage: false });
    console.log(`[PROOF] Screenshot saved: ${screenshotPath}`);

  } catch (err) {
    console.error('[TEST] Error:', err.message);
  } finally {
    await context.close();
    console.log('[TEST] Browser closed');
  }
}

testJoin().catch(console.error);
