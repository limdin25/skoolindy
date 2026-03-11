const { chromium } = require('playwright');
const path = require('path');

const BROWSER_PROFILES_DIR = path.join(__dirname, 'skool_accounts');
const PROFILE_ID = 'd56f73d2-08bc-4412-a018-960fe89362ad';
const GROUP_SLUG = 'freegroup';

async function testJoin() {
  const userDataDir = path.join(BROWSER_PROFILES_DIR, PROFILE_ID, 'browser');
  
  console.log(`[TEST] Profile dir: ${userDataDir}`);
  console.log(`[TEST] Target: https://www.skool.com/${GROUP_SLUG}`);
  
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1600, height: 1100 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox'],
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
  
  page.on('response', r => {
    const u = r.url();
    if (u.includes('join-group') || u.includes('pending')) {
      console.log('RES ' + r.status() + ' ' + u);
    }
  });

  try {
    console.log(`[TEST] Navigating to group page...`);
    await page.goto(`https://www.skool.com/${GROUP_SLUG}`, { timeout: 20000 });
    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);

    console.log(`[TEST] URL: ${page.url()}`);

    // Check for session
    if (page.url().includes('/login')) {
      console.log('[TEST] FAILED: Redirected to login');
      await context.close();
      return;
    }

    // Check if already member
    const bodyText = await page.textContent('body');
    if (/leave\s+group/i.test(bodyText)) {
      console.log('[TEST] Already a member (Leave Group visible)');
    }

    // Find join button
    const joinBtn = await page.$('button:has-text("Join")');
    if (!joinBtn) {
      console.log('[TEST] No Join button found - might already be a member');
      // Check classroom
      await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
      await page.waitForTimeout(2000);
      const hasClassroom = !/join/i.test(await page.textContent('body'));
      console.log(`[PROOF] Classroom accessible: ${hasClassroom}`);
      await page.screenshot({ path: path.join(__dirname, 'test-join-proof-v2.png') });
      await context.close();
      return;
    }

    const btnText = await joinBtn.textContent();
    console.log(`[TEST] Found button: "${btnText.trim()}"`);
    
    // Click join
    console.log('[TEST] Clicking join button...');
    await joinBtn.click();
    await page.waitForTimeout(3000);
    
    // Check for survey modal
    console.log('[TEST] Checking for survey modal...');
    const modal = await page.$('div[role="dialog"], .modal, [class*="modal"], [class*="Modal"]');
    if (modal) {
      console.log('[TEST] Modal detected!');
      
      // Look for form fields
      const inputs = await page.$$('input[type="text"], textarea');
      console.log(`[TEST] Found ${inputs.length} form inputs`);
      
      for (const input of inputs) {
        const placeholder = await input.getAttribute('placeholder') || '';
        const label = await page.evaluate(el => {
          const label = el.closest('label') || document.querySelector(`label[for="${el.id}"]`);
          return label ? label.textContent : '';
        }, input);
        console.log(`[TEST] Input: placeholder="${placeholder}" label="${label}"`);
        
        // Fill with generic answers
        if (/email/i.test(placeholder + label)) {
          await input.fill('hugords100@gmail.com');
        } else if (/name/i.test(placeholder + label)) {
          await input.fill('Hugo');
        } else {
          await input.fill('Interested in AI automation and building systems');
        }
      }
      
      // Handle checkboxes
      const checkboxes = await page.$$('input[type="checkbox"]');
      for (const cb of checkboxes) {
        await cb.check().catch(() => {});
      }
      
      // Click submit/continue
      const submitBtn = await page.$('button:has-text("Submit"), button:has-text("Continue"), button:has-text("Join")');
      if (submitBtn) {
        console.log('[TEST] Clicking submit button...');
        await submitBtn.click();
        await page.waitForTimeout(3000);
      }
    }

    // Take screenshot of current state
    await page.screenshot({ path: path.join(__dirname, 'test-join-after-click.png') });
    console.log('[TEST] Screenshot: test-join-after-click.png');

    // Check current URL and state
    console.log(`[TEST] Current URL: ${page.url()}`);
    
    // PROOF: Navigate to classroom
    console.log('\n[PROOF] Navigating to classroom...');
    await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(2000);
    
    const classroomUrl = page.url();
    const title = await page.title();
    
    console.log(`[PROOF] URL: ${classroomUrl}`);
    console.log(`[PROOF] Title: ${title}`);
    
    // Check for join button
    const joinBtnInClassroom = await page.$('button:has-text("Join")');
    console.log(`[PROOF] Join button exists: ${!!joinBtnInClassroom}`);
    
    // Check for classroom content (modules, lessons)
    const classroomContent = await page.textContent('body');
    const hasContent = /module|lesson|course|classroom|locked/i.test(classroomContent);
    console.log(`[PROOF] Has classroom content: ${hasContent}`);
    
    // Check for WAF
    if (classroomUrl.includes('waf') || /cloudflare|blocked|captcha/i.test(classroomContent)) {
      console.log('[PROOF] WARNING: WAF/Cloudflare detected!');
    }
    
    // Final screenshot
    await page.screenshot({ path: path.join(__dirname, 'test-join-proof-v2.png'), fullPage: false });
    console.log('[PROOF] Screenshot: test-join-proof-v2.png');

  } catch (err) {
    console.error('[TEST] Error:', err.message);
    await page.screenshot({ path: path.join(__dirname, 'test-join-error.png') }).catch(() => {});
  } finally {
    await context.close();
    console.log('[TEST] Done');
  }
}

testJoin().catch(console.error);
