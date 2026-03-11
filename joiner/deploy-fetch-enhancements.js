#!/usr/bin/env node
/**
 * Deploy fetch enhancements: requestedAt from Settings modal, cancel request, expandable communities.
 * Run on server: node deploy-fetch-enhancements.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = '/root/.openclaw/workspace/community-join-manager';
const JOIN = path.join(ROOT, 'backend/joinCommunity.js');
const SERVER = path.join(ROOT, 'backend/server.js');
const API = path.join(ROOT, 'src/lib/api.ts');
const ACCOUNTS = path.join(ROOT, 'src/components/AccountsTab.tsx');

function patch(name, fn) {
  try {
    fn();
    console.log('OK:', name);
  } catch (e) {
    console.error('FAIL:', name, e.message);
  }
}

// 1. joinCommunity.js: Update fetchCommunitiesFromSkoolSettings to use Settings modal, extract requestedAt
patch('joinCommunity: Settings modal + requestedAt', () => {
  let s = fs.readFileSync(JOIN, 'utf8');
  if (s.includes('slugifyName')) return; // already patched

  const slugifyName = `
function slugifyName(name) {
  return (name || '').toString().trim().replace(/\\s+/g, '-').replace(/[^a-zA-Z0-9-]/g, '').toLowerCase();
}
`;
  s = s.replace("const result = { joined: [], pending: [], error: null };", `const result = { joined: [], pending: [], error: null };
${slugifyName}`);
  fs.writeFileSync(JOIN, s);
});

// Replace the community-checking loop to prefer Settings modal
patch('joinCommunity: Settings modal loop', () => {
  let s = fs.readFileSync(JOIN, 'utf8');
  if (s.includes('You requested membership to')) return;

  const oldLoop = `    const total = communitySlugs.length;
    onProgress({ status: total ? 'Checking communities' : 'No communities found', current: 0, total });
    for (let i = 0; i < communitySlugs.length; i++) {
      const slug = communitySlugs[i];
      onProgress({ status: \`Visiting community: \${slug}\`, current: i + 1, total, community: slug });
      let status = 'joined';
      try {
        const res2 = await page.goto(\`https://www.skool.com/\${slug}\`, { timeout: 15000 });
        await page.waitForLoadState('domcontentloaded');
        await page.waitForTimeout(2500);
        if (res2 && (res2.status() === 404 || res2.status() >= 500)) { status = 'error'; }
        else if (page.url().toLowerCase().includes('/login')) { status = 'error'; }
        else {
          const txt = (await page.textContent('body')).toLowerCase();
          if (/invite\\s+people|invite\\s+others|invite\\s+members|leave\\s+group/i.test(txt)) status = 'joined';
          else if (/pending/i.test(txt) || /you requested membership/i.test(txt) || /cancel request/i.test(txt)) status = 'pending';
          else status = 'joined';
        }
        if (status === 'pending') result.pending.push(slug);
        else if (status !== 'error') result.joined.push(slug);
      } catch (e) { result.joined.push(slug); }
    }`;

  const newLoop = `    const total = communitySlugs.length;
    onProgress({ status: total ? 'Checking communities' : 'No communities found', current: 0, total });
    const settingsBtns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
    if (settingsBtns.length > 0) {
      for (let i = 0; i < settingsBtns.length; i++) {
        const btns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
        if (i >= btns.length) break;
        onProgress({ status: \`Reading community: \${i + 1}/\${settingsBtns.length}\`, current: i + 1, total: settingsBtns.length, community: '...' });
        try {
          const allBtns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
          if (i >= allBtns.length) break;
          await allBtns[i].click();
          await page.waitForTimeout(2500);
          const bodyText = await page.textContent('body');
          const bodyLower = bodyText.toLowerCase();
          const requestedMatch = bodyText.match(/You requested membership to ([^\\n]+?) on (\\d{1,2}\\/\\d{1,2}\\/\\d{4})/i);
          if (requestedMatch) {
            const name = requestedMatch[1].replace(/©|™|®/g, '').trim();
            const requestedAt = requestedMatch[2];
            const slug = slugifyName(name) || (communitySlugs[i] || '').toString();
            result.pending.push({ slug, name, requestedAt });
          } else if (/cancel request/i.test(bodyLower) && /membership/i.test(bodyLower)) {
            const nameMatch = bodyText.match(/Membership settings[\\s\\S]*?([A-Za-z0-9][^\\n]{5,80})/);
            const name = nameMatch ? nameMatch[1].replace(/©|™|®/g, '').trim() : communitySlugs[i];
            result.pending.push({ slug: slugifyName(name) || communitySlugs[i], name: name || communitySlugs[i], requestedAt: null });
          } else {
            const nameMatch = bodyText.match(/Membership settings[\\s\\S]*?([A-Za-z0-9][^\\n]{5,80})/);
            const name = nameMatch ? nameMatch[1].replace(/©|™|®/g, '').trim() : communitySlugs[i];
            result.joined.push({ slug: slugifyName(name) || communitySlugs[i], name: name || communitySlugs[i] });
          }
        } catch (e) {
          const idx = Math.min(i, communitySlugs.length - 1);
          const s = idx >= 0 ? communitySlugs[idx] : 'unknown';
          result.joined.push(typeof s === 'string' ? { slug: s, name: s } : s);
        }
        await page.keyboard.press('Escape');
        await page.waitForTimeout(800);
      }
    } else {
      for (let i = 0; i < communitySlugs.length; i++) {
        const slug = communitySlugs[i];
        onProgress({ status: \`Visiting community: \${slug}\`, current: i + 1, total, community: slug });
        let status = 'joined';
        try {
          const res2 = await page.goto(\`https://www.skool.com/\${slug}\`, { timeout: 15000 });
          await page.waitForLoadState('domcontentloaded');
          await page.waitForTimeout(2500);
          if (res2 && (res2.status() === 404 || res2.status() >= 500)) { status = 'error'; }
          else if (page.url().toLowerCase().includes('/login')) { status = 'error'; }
          else {
            const txt = (await page.textContent('body')).toLowerCase();
            if (/invite\\s+people|invite\\s+others|invite\\s+members|leave\\s+group/i.test(txt)) status = 'joined';
            else if (/pending/i.test(txt) || /you requested membership/i.test(txt) || /cancel request/i.test(txt)) status = 'pending';
            else status = 'joined';
          }
          if (status === 'pending') result.pending.push({ slug, name: slug, requestedAt: null });
          else if (status !== 'error') result.joined.push({ slug, name: slug });
        } catch (e) { result.joined.push({ slug, name: slug }); }
      }
    }`;

  s = s.replace(oldLoop, newLoop);
  fs.writeFileSync(JOIN, s);
});

// 2. joinCommunity.js: Add cancelRequestOnSkool
patch('joinCommunity: cancelRequestOnSkool', () => {
  let s = fs.readFileSync(JOIN, 'utf8');
  if (s.includes('cancelRequestOnSkool')) return;

  const cancelFn = `
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
    const slugLower = (communitySlug || '').toLowerCase();
    for (let i = 0; i < settingsBtns.length; i++) {
      const btns = await page.locator('button:has-text("SETTINGS"), button:has-text("Settings")').all();
      if (i >= btns.length) break;
      await btns[i].click();
      await page.waitForTimeout(2000);
      const bodyFull = await page.textContent('body');
      const bodyLower = bodyFull.toLowerCase();
      if (!/cancel request/i.test(bodyLower)) { await page.keyboard.press('Escape'); await page.waitForTimeout(800); continue; }
      const requestedMatch = bodyFull.match(/You requested membership to ([^\\n]+?) on (\\d{1,2}\\/\\d{1,2}\\/\\d{4})/i);
      const modalSlug = requestedMatch ? slugifyName(requestedMatch[1].replace(/©|™|®/g, '').trim()) : '';
        if (modalSlug === slugLower) {
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
`;
  s = s.replace('module.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings };',
    cancelFn + '\nmodule.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool };');
  fs.writeFileSync(JOIN, s);
});

// 3. server.js: Handle object arrays, lastFetchResults, GET results, POST cancel
patch('server: require cancelRequestOnSkool', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('cancelRequestOnSkool')) return;
  s = s.replace(
    "const { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings } = require('./joinCommunity');",
    "const { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool } = require('./joinCommunity');"
  );
  fs.writeFileSync(SERVER, s);
});

patch('server: lastFetchResults + object arrays', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('lastFetchResults')) return;
  s = s.replace(
    'let fetchProfileProgress = { running: false, profileId: null, status: \'\', current: 0, total: 0, community: \'\', resolved: 0, joinedCount: 0, pendingCount: 0 };',
    'let fetchProfileProgress = { running: false, profileId: null, status: \'\', current: 0, total: 0, community: \'\', resolved: 0, joinedCount: 0, pendingCount: 0 };\nlet lastFetchResults = {};'
  );
  s = s.replace(
    'if (r.error) return { error: r.error, resolved: 0 };\n    const joinedSet = new Set((r.joined || []).map(s => s.toLowerCase()));\n    const pendingSet = new Set((r.pending || []).map(s => s.toLowerCase()));\n    fetchProfileProgress.joinedCount = joinedSet.size;\n    fetchProfileProgress.pendingCount = pendingSet.size;',
    'if (r.error) return { error: r.error, resolved: 0 };\n    const joinedList = (r.joined || []).map(x => typeof x === \'string\' ? { slug: x, name: x } : x);\n    const pendingList = (r.pending || []).map(x => typeof x === \'string\' ? { slug: x, name: x, requestedAt: null } : x);\n    lastFetchResults[profileId] = { joined: joinedList, pending: pendingList, fetchedAt: new Date().toISOString() };\n    const joinedSet = new Set(joinedList.map(x => (x.slug || x).toLowerCase()));\n    const pendingSet = new Set(pendingList.map(x => (x.slug || x).toLowerCase()));\n    fetchProfileProgress.joinedCount = joinedList.length;\n    fetchProfileProgress.pendingCount = pendingList.length;'
  );
  s = s.replace(
    'return { resolved, joined: r.joined?.length || 0, pending: r.pending?.length || 0 };',
    'return { resolved, joined: joinedList.length, pending: pendingList.length };'
  );
  fs.writeFileSync(SERVER, s);
});

patch('server: GET results + POST cancel-request', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('/api/communities/fetch/:profileId/results')) return;
  s = s.replace(
    `res.json({ running: p.running, status: p.status, current: p.current, total: p.total, community: p.community, resolved: p.resolved, joinedCount: p.joinedCount, pendingCount: p.pendingCount });
});

app.post('/api/communities/fetch-all',`,
    `res.json({ running: p.running, status: p.status, current: p.current, total: p.total, community: p.community, resolved: p.resolved, joinedCount: p.joinedCount, pendingCount: p.pendingCount });
});

app.get('/api/communities/fetch/:profileId/results', (req, res) => {
  const { profileId } = req.params;
  const data = lastFetchResults[profileId] || null;
  res.json(data);
});

app.post('/api/communities/cancel-request', async (req, res) => {
  const { profileId, communitySlug } = req.body;
  if (!profileId || !communitySlug) return res.status(400).json({ error: 'profileId and communitySlug required' });
  try {
    const result = await cancelRequestOnSkool(profileId, communitySlug);
    if (result.success) {
      const data = lastFetchResults[profileId];
      if (data && data.pending) {
        data.pending = data.pending.filter(p => (p.slug || p).toLowerCase() !== communitySlug.toLowerCase());
      }
      res.json({ success: true });
    } else {
      res.status(400).json({ success: false, error: result.error || 'Failed to cancel' });
    }
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});

app.post('/api/communities/fetch-all',`
  );
  fs.writeFileSync(SERVER, s);
});

// 4. api.ts: getFetchResults, cancelRequest
patch('api: getFetchResults + cancelRequest', () => {
  let s = fs.readFileSync(API, 'utf8');
  if (s.includes('getFetchResults')) return;
  s = s.replace(
    'getFetchStatus: (profileId: string) => request(`/api/communities/fetch/${profileId}/status`),',
    'getFetchStatus: (profileId: string) => request(`/api/communities/fetch/${profileId}/status`),\n  getFetchResults: (profileId: string) => request(`/api/communities/fetch/${profileId}/results`),\n  cancelRequest: (profileId: string, communitySlug: string) => request(\'/api/communities/cancel-request\', { method: \'POST\', body: JSON.stringify({ profileId, communitySlug }) }),'
  );
  fs.writeFileSync(API, s);
});

// 5. AccountsTab.tsx: fetch results state, load after fetch, expandable communities with Cancel
patch('AccountsTab: fetch results state', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('fetchResultsByProfile')) return;
  s = s.replace(
    'const [fetchProgress, setFetchProgress] = useState<{ status: string; current: number; total: number; community: string } | null>(null);',
    'const [fetchProgress, setFetchProgress] = useState<{ status: string; current: number; total: number; community: string } | null>(null);\n  const [fetchResultsByProfile, setFetchResultsByProfile] = useState<Record<string, { joined: { slug: string; name: string }[]; pending: { slug: string; name: string; requestedAt: string | null }[]; fetchedAt: string } | null>>({});'
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('AccountsTab: load fetch results after fetch', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('api.getFetchResults')) return;
  s = s.replace(
    `if (s.running) setTimeout(poll, 800);
        else {
          fetchProfiles();
          setFetchingId(null);
          setFetchProgress(null);
          alert(\`Fetch complete. \${s.resolved || 0} queue items updated to Joined. Found \${s.joinedCount || 0} joined, \${s.pendingCount || 0} pending on Skool.\`);
        }`,
    `if (s.running) setTimeout(poll, 800);
        else {
          fetchProfiles();
          setFetchingId(null);
          setFetchProgress(null);
          try {
            const res = await api.getFetchResults(id);
            if (res) setFetchResultsByProfile(prev => ({ ...prev, [id]: res }));
          } catch {}
          alert(\`Fetch complete. \${s.resolved || 0} queue items updated to Joined. Found \${s.joinedCount || 0} joined, \${s.pendingCount || 0} pending on Skool.\`);
        }`
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('AccountsTab: communities list + Cancel button', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('Communities from Skool')) return;
  s = s.replace(
    'import { Plus, Upload, TestTube, Pencil, ChevronDown, ChevronUp, Eye, EyeOff, Play, Square, Settings2, Timer, Plug, Trash2, Cookie, Download } from "lucide-react";',
    'import { Plus, Upload, TestTube, Pencil, ChevronDown, ChevronUp, Eye, EyeOff, Play, Square, Settings2, Timer, Plug, Trash2, Cookie, Download, XCircle } from "lucide-react";'
  );
  const patchPath = path.join(ROOT, 'accounts-tab-communities-patch.txt');
  const oldBlockPath = path.join(ROOT, 'accounts-tab-old-block.txt');
  const newBlock = fs.existsSync(patchPath) ? fs.readFileSync(patchPath, 'utf8').trim() : null;
  const oldBlock = fs.existsSync(oldBlockPath) ? fs.readFileSync(oldBlockPath, 'utf8').trim() : null;
  if (!newBlock || !oldBlock) { console.log('  (skip: patch files not found)'); return; }
  s = s.replace(oldBlock, newBlock);
  fs.writeFileSync(ACCOUNTS, s);
});

</think>
Fixing the deploy script — the Settings-button loop must not navigate away.
<｜tool▁calls▁begin｜><｜tool▁call▁begin｜>
WebSearch