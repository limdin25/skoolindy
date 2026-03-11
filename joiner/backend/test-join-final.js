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
    if (u.includes('api2.skool.com')) {
      console.log('REQ ' + r.method() + ' ' + u);
    }
  });
  
  page.on('response', r => {
    const u = r.url();
    if (u.includes('join-group') || u.includes('answers') || u.includes('pending')) {
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

    // Check if already member (no join button)
    const joinBtnOuter = await page.$('button:has-text("JOIN GROUP")');
    if (!joinBtnOuter) {
      console.log('[TEST] No outer Join button - might already be member');
      await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
      await page.waitForTimeout(2000);
      const url = page.url();
      console.log(`[PROOF] Classroom URL: ${url}`);
      console.log(`[PROOF] Classroom accessible: ${url.includes('/classroom')}`);
      await page.screenshot({ path: path.join(__dirname, 'test-join-final.png') });
      await context.close();
      return;
    }

    console.log('[TEST] Clicking outer JOIN GROUP...');
    await joinBtnOuter.click();
    await page.waitForTimeout(2000);

    // Wait for modal to appear
    await page.waitForSelector('div[role="dialog"], [class*="Modal"]', { timeout: 5000 }).catch(() => {});
    await page.waitForTimeout(1000);
    
    console.log('[TEST] Survey modal detected, filling form...');

    // 1. Fill email
    const emailInput = await page.$('input[placeholder*="Email"], input[type="email"]');
    if (emailInput) {
      await emailInput.fill('hugords100@gmail.com');
      console.log('[TEST] Filled: email');
    }

    // 2. Click first radio button (Just starting out)
    const radioBtn = await page.$('input[type="radio"]');
    if (radioBtn) {
      await radioBtn.click({ force: true });
      console.log('[TEST] Selected: first radio option');
    } else {
      // Try clicking the label text
      const radioLabel = await page.$('text="Just starting out"');
      if (radioLabel) {
        await radioLabel.click();
        console.log('[TEST] Clicked: "Just starting out" label');
      }
    }

    // 3. Fill textarea
    const textarea = await page.$('textarea, input[placeholder*="Your answer"]');
    if (textarea) {
      await textarea.fill('Excited to learn AI automation and build profitable systems!');
      console.log('[TEST] Filled: textarea');
    }

    await page.waitForTimeout(500);
    await page.screenshot({ path: path.join(__dirname, 'test-join-filled.png') });
    console.log('[TEST] Screenshot: test-join-filled.png');

    // 4. Click JOIN GROUP button inside modal
    console.log('[TEST] Looking for modal submit button...');
    
    // Find all buttons with "JOIN GROUP" text and click the one in the modal
    const modalJoinBtn = await page.evaluateHandle(() => {
      const modal = document.querySelector('[class*="Modal"], [role="dialog"]');
      if (modal) {
        const btns = modal.querySelectorAll('button');
        for (const btn of btns) {
          if (/join.*group/i.test(btn.textContent)) {
            return btn;
          }
        }
      }
      return null;
    });

    if (modalJoinBtn) {
      console.log('[TEST] Found modal JOIN GROUP button, clicking...');
      await modalJoinBtn.asElement().click({ force: true });
      console.log('[TEST] ✅ JOIN GROUP clicked');
      await page.waitForTimeout(4000);
    } else {
      console.log('[TEST] No modal JOIN GROUP button found');
      // Try alternative selector
      const altBtn = await page.$('button:has-text("JOIN GROUP"):not([disabled])');
      if (altBtn) {
        await altBtn.click({ force: true });
        console.log('[TEST] Clicked alternative JOIN GROUP button');
        await page.waitForTimeout(4000);
      }
    }

    await page.screenshot({ path: path.join(__dirname, 'test-join-after-modal.png') });
    console.log(`[TEST] Current URL: ${page.url()}`);

    // PROOF: Navigate to classroom
    console.log('\n[PROOF] Navigating to classroom...');
    await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    
    const classroomUrl = page.url();
    const title = await page.title();
    
    console.log(`[PROOF] Classroom URL: ${classroomUrl}`);
    console.log(`[PROOF] Page title: ${title}`);
    
    // Check join button
    const joinInClassroom = await page.$('button:has-text("Join")');
    console.log(`[PROOF] Join button exists: ${!!joinInClassroom}`);
    
    // Check for classroom content
    const bodyText = await page.textContent('body');
    console.log(`[PROOF] Has "classroom" in URL: ${classroomUrl.includes('/classroom')}`);
    console.log(`[PROOF] Has module/lesson content: ${/module|lesson/i.test(bodyText)}`);
    
    // Check for redirect
    if (!classroomUrl.includes('/classroom')) {
      console.log(`[PROOF] ⚠️ REDIRECTED away from classroom`);
    }
    
    await page.screenshot({ path: path.join(__dirname, 'test-join-final.png'), fullPage: false });
    console.log('[PROOF] Screenshot: test-join-final.png');

  } catch (err) {
    console.error('[TEST] Error:', err.message);
    await page.screenshot({ path: path.join(__dirname, 'test-join-error.png') }).catch(() => {});
  } finally {
    await context.close();
    console.log('[TEST] Done');
  }
}

testJoin().catch(console.error);
