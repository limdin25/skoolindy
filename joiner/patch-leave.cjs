const fs = require('fs');
const path = '/root/.openclaw/workspace/community-join-manager/backend/joinCommunity.js';
let s = fs.readFileSync(path, 'utf8');

// Add leaveGroupOnSkool before module.exports
const oldExports = `module.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool };`;

const leaveFn = `
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
        body: '{}'
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
`;

const newExports = leaveFn + `\nmodule.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool, leaveGroupOnSkool };`;

if (s.includes('cancelRequestOnSkool };') && !s.includes('leaveGroupOnSkool')) {
  s = s.replace(oldExports, newExports);
  fs.writeFileSync(path, s);
  console.log("Added leaveGroupOnSkool");
} else {
  console.log("Already has leaveGroupOnSkool or structure different");
}
