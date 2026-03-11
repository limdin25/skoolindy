/**
 * Network Capture Script - Deep flow mapping for Skool join
 * Captures: method, path, status, top-level JSON keys
 * Does NOT capture: cookies, headers, PII
 */
const { chromium } = require('playwright');
const path = require('path');

const BROWSER_PROFILES_DIR = path.join(__dirname, 'skool_accounts');
const PROFILE_ID = 'd56f73d2-08bc-4412-a018-960fe89362ad';
const GROUP_SLUG = 'community'; // Fresh community

const requests = [];
const responses = [];

function extractJsonKeys(body) {
  try {
    const parsed = typeof body === 'string' ? JSON.parse(body) : body;
    if (Array.isArray(parsed)) {
      return `[array:${parsed.length}]`;
    }
    return Object.keys(parsed).join(', ');
  } catch {
    return '(not json)';
  }
}

async function captureJoinFlow() {
  const userDataDir = path.join(BROWSER_PROFILES_DIR, PROFILE_ID, 'browser');
  
  console.log('='.repeat(80));
  console.log('STANDALONE COMMUNITY-JOIN-MANAGER - NETWORK CAPTURE');
  console.log('='.repeat(80));
  console.log(`Profile: ${PROFILE_ID}`);
  console.log(`Target: https://www.skool.com/${GROUP_SLUG}`);
  console.log('='.repeat(80));
  
  const context = await chromium.launchPersistentContext(userDataDir, {
    headless: true,
    viewport: { width: 1600, height: 1100 },
    args: ['--disable-blink-features=AutomationControlled', '--no-sandbox'],
  });

  const page = context.pages()[0] || await context.newPage();
  page.setDefaultTimeout(30000);
  
  // Capture requests
  page.on('request', req => {
    const url = req.url();
    if (url.includes('skool.com')) {
      const parsed = new URL(url);
      const entry = {
        step: requests.length + 1,
        method: req.method(),
        path: parsed.pathname + (parsed.search || ''),
        host: parsed.host,
      };
      
      // Capture POST body keys
      if (req.method() === 'POST') {
        const postData = req.postData();
        entry.reqKeys = extractJsonKeys(postData);
      }
      
      requests.push(entry);
    }
  });
  
  // Capture responses
  page.on('response', async res => {
    const url = res.url();
    if (url.includes('skool.com')) {
      const parsed = new URL(url);
      const entry = {
        method: res.request().method(),
        path: parsed.pathname,
        status: res.status(),
      };
      
      // Capture response body keys for JSON
      try {
        const body = await res.text();
        entry.resKeys = extractJsonKeys(body);
      } catch {
        entry.resKeys = '(no body)';
      }
      
      responses.push(entry);
    }
  });

  try {
    // STEP 1: Navigate to community
    console.log('\n[STEP 1] Navigate to community page');
    await page.goto(`https://www.skool.com/${GROUP_SLUG}`, { timeout: 20000 });
    await page.waitForLoadState('networkidle', { timeout: 10000 }).catch(() => {});
    await page.waitForTimeout(2000);
    
    console.log(`  URL: ${page.url()}`);

    // Check login
    if (page.url().includes('/login')) {
      console.log('  FAILED: Redirected to login');
      await context.close();
      return;
    }

    // STEP 2: Check current membership status
    console.log('\n[STEP 2] Check membership status');
    const bodyText = await page.textContent('body');
    const hasLeaveBtn = /leave\s+group/i.test(bodyText);
    const hasJoinBtn = await page.$('button:has-text("JOIN")');
    const hasCancelBtn = await page.$('button:has-text("Cancel")');
    
    console.log(`  Leave Group visible: ${hasLeaveBtn}`);
    console.log(`  Join button visible: ${!!hasJoinBtn}`);
    console.log(`  Cancel request visible: ${!!hasCancelBtn}`);

    if (hasLeaveBtn) {
      console.log('  Already a member - testing LEAVE flow instead');
      // We can test leave flow here
    }

    // STEP 3: Click join button
    if (hasJoinBtn) {
      console.log('\n[STEP 3] Click JOIN button');
      await hasJoinBtn.click();
      await page.waitForTimeout(3000);
      
      // Check for survey modal
      const modal = await page.$('[class*="Modal"], [role="dialog"]');
      if (modal) {
        console.log('  Survey modal detected');
        
        // STEP 4: Fill survey
        console.log('\n[STEP 4] Fill survey fields');
        
        // Email
        const emailInput = await page.$('input[placeholder*="Email"], input[type="email"]');
        if (emailInput) {
          await emailInput.fill('test@example.com');
          console.log('  Filled: email');
        }
        
        // Radio buttons
        const radioLabel = await page.$('text="Just starting out"');
        if (radioLabel) {
          await radioLabel.click();
          console.log('  Selected: radio option');
        }
        
        // Textarea
        const textarea = await page.$('textarea');
        if (textarea) {
          await textarea.fill('Interested in learning and growing');
          console.log('  Filled: textarea');
        }
        
        await page.waitForTimeout(500);
        
        // STEP 5: Submit survey
        console.log('\n[STEP 5] Submit survey');
        const submitBtn = await page.evaluateHandle(() => {
          const modal = document.querySelector('[class*="Modal"], [role="dialog"]');
          if (modal) {
            const btns = modal.querySelectorAll('button');
            for (const btn of btns) {
              if (/join/i.test(btn.textContent)) return btn;
            }
          }
          return null;
        });
        
        if (submitBtn) {
          await submitBtn.asElement().click({ force: true });
          console.log('  Clicked modal submit');
          await page.waitForTimeout(4000);
        }
      }
    }

    // STEP 6: Verify membership
    console.log('\n[STEP 6] Verify membership via classroom');
    await page.goto(`https://www.skool.com/${GROUP_SLUG}/classroom`, { timeout: 20000 });
    await page.waitForTimeout(2000);
    
    const classroomUrl = page.url();
    const hasClassroom = classroomUrl.includes('/classroom');
    const joinBtnAfter = await page.$('button:has-text("Join")');
    
    console.log(`  Classroom URL: ${classroomUrl}`);
    console.log(`  Classroom accessible: ${hasClassroom}`);
    console.log(`  Join button exists: ${!!joinBtnAfter}`);

    // STEP 7: Check self/membership API
    console.log('\n[STEP 7] Check membership via API');
    const membershipCheck = await page.evaluate(async (slug) => {
      try {
        const res = await fetch(`https://api2.skool.com/self`, { credentials: 'include' });
        const data = await res.json();
        return { status: res.status, keys: Object.keys(data) };
      } catch (e) {
        return { error: e.message };
      }
    }, GROUP_SLUG);
    console.log(`  /self API: ${JSON.stringify(membershipCheck)}`);

  } catch (err) {
    console.error('\n[ERROR]', err.message);
  } finally {
    await context.close();
  }

  // Print captured network traffic
  console.log('\n' + '='.repeat(80));
  console.log('CAPTURED NETWORK REQUESTS (skool.com only)');
  console.log('='.repeat(80));
  
  const relevantPaths = requests.filter(r => 
    r.path.includes('/groups/') || 
    r.path.includes('/join') || 
    r.path.includes('/self') ||
    r.path.includes('/survey') ||
    r.path.includes('/pending') ||
    r.path.includes('/cancel') ||
    r.path.includes('/leave')
  );
  
  console.log('\nKey API Calls:');
  console.log('-'.repeat(80));
  console.log('Method | Path | Request Keys');
  console.log('-'.repeat(80));
  
  for (const r of relevantPaths) {
    console.log(`${r.method.padEnd(6)} | ${r.path.substring(0, 60)} | ${r.reqKeys || '-'}`);
  }

  console.log('\n' + '='.repeat(80));
  console.log('CAPTURED RESPONSES');
  console.log('='.repeat(80));
  
  const relevantResponses = responses.filter(r => 
    r.path.includes('/groups/') || 
    r.path.includes('/join') || 
    r.path.includes('/self') ||
    r.path.includes('/survey') ||
    r.path.includes('/pending')
  );
  
  console.log('\nKey API Responses:');
  console.log('-'.repeat(80));
  console.log('Method | Path | Status | Response Keys');
  console.log('-'.repeat(80));
  
  for (const r of relevantResponses) {
    console.log(`${r.method.padEnd(6)} | ${r.path.substring(0, 50).padEnd(50)} | ${String(r.status).padEnd(6)} | ${r.resKeys}`);
  }
  
  console.log('\n[CAPTURE COMPLETE]');
}

captureJoinFlow().catch(console.error);
