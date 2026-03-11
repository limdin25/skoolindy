const { chromium } = require('playwright');
const path = require('path');

const PROFILE_DIR = '/root/.openclaw/workspace/community-join-manager/backend/skool_accounts/88fcd80c-0ab6-4562-a4f5-bbd31862b0e1/browser';

(async () => {
  console.log("Launching persistent context...");
  const context = await chromium.launchPersistentContext(PROFILE_DIR, {
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
  
  // Network logger — method + URL only, no headers/body
  page.on('request', r => {
    if (r.url().includes('skool.com')) {
      console.log('REQ', r.method(), r.url().substring(0, 200));
    }
  });
  page.on('response', r => {
    if (r.url().includes('skool.com')) {
      console.log('RES', r.status(), r.url().substring(0, 200));
    }
  });
  
  console.log("Navigating to skool.com/freegroup...");
  try {
    const response = await page.goto('https://www.skool.com/freegroup', { timeout: 20000 });
    console.log('HTTP STATUS:', response ? response.status() : 'null');
    await page.waitForLoadState('domcontentloaded');
    await page.waitForTimeout(3000);
    
    const currentUrl = page.url();
    console.log('CURRENT URL:', currentUrl);
    
    const bodyText = await page.textContent('body').catch(() => '');
    const pageTitle = await page.title().catch(() => '');
    console.log('PAGE TITLE:', pageTitle.substring(0, 100));
    console.log('BODY PREVIEW:', bodyText.substring(0, 300).replace(/\n/g, ' '));
    
    // Check for WAF
    const html = await page.content();
    if (html.includes('edge.sdk.awswaf.com') || html.includes('challenge.js')) {
      console.log('*** WAF DETECTED in HTML ***');
    }
    if (currentUrl.includes('challenge')) {
      console.log('*** WAF DETECTED in URL ***');
    }
    if (pageTitle.toLowerCase().includes('attention required') || pageTitle.toLowerCase().includes('request blocked')) {
      console.log('*** WAF DETECTED in title ***');
    }
    
    // Check for join button
    const joinBtns = await page.locator('button:has-text("Join")').all();
    console.log('JOIN BUTTONS FOUND:', joinBtns.length);
    for (const btn of joinBtns) {
      const text = await btn.textContent().catch(() => '');
      const visible = await btn.isVisible().catch(() => false);
      console.log('  BUTTON:', text.trim().substring(0, 50), 'visible:', visible);
    }
    
    // Check login redirect
    if (currentUrl.includes('/login')) {
      console.log('*** REDIRECTED TO LOGIN — session expired ***');
    }
    
  } catch (e) {
    console.log('NAV ERROR:', e.message.substring(0, 200));
  }
  
  await context.close();
  console.log("Done.");
})();
