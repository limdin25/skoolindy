#!/usr/bin/env node
/**
 * Deploy truthful cancel/leave: page-context fetch + verify via re-fetch.
 * Run on server: cd /root/.openclaw/workspace/community-join-manager && node deploy-cancel-leave-truthful.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = '/root/.openclaw/workspace/community-join-manager';
const SERVER = path.join(ROOT, 'backend/server.js');
const JOIN = path.join(ROOT, 'backend/joinCommunity.js');
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

// ============ TASK 1: fetchCommunitiesFromApi + GET /api/communities ============
patch('server: add fetchCommunitiesFromApi helper', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('async function fetchCommunitiesFromApi')) return;
  s = s.replace(
    'async function parseGroupsFromApi(data) {',
    `async function fetchCommunitiesFromApi(profileId) {
  const profile = db.prepare('SELECT * FROM profiles WHERE id = ?').get(profileId);
  if (!profile || !profile.cookie_json) return { error: 'No profile or cookie' };
  try {
    const data = await getGroups(profile.cookie_json);
    const r = parseGroupsFromApi(data);
    if (!r) return { error: 'Parse failed' };
    const joined = r.joined || [];
    const pending = r.pending || [];
    return {
      joined,
      pending,
      counts: { joined: joined.length, pending: pending.length },
      fetchedAt: new Date().toISOString()
    };
  } catch (e) {
    return { error: e.message };
  }
}

async function parseGroupsFromApi(data) {`
  );
  fs.writeFileSync(SERVER, s);
});

patch('server: add GET /api/communities', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes("app.get('/api/communities', async")) return;
  s = s.replace(
    "app.get('/api/communities/fetch/:profileId/status', (req, res) => {",
    `app.get('/api/communities', async (req, res) => {
  const profileId = req.query.profileId;
  if (!profileId) return res.status(400).json({ error: 'profileId required' });
  try {
    const result = await fetchCommunitiesFromApi(profileId);
    if (result.error) return res.status(400).json({ error: result.error });
    res.json({ joined: result.joined, pending: result.pending, counts: result.counts, fetchedAt: result.fetchedAt });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/communities/fetch/:profileId/status', (req, res) => {`
  );
  fs.writeFileSync(SERVER, s);
});

patch('server: GET results -> API-derived', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  const newHandler = `app.get('/api/communities/fetch/:profileId/results', async (req, res) => {
  const { profileId } = req.params;
  try {
    const result = await fetchCommunitiesFromApi(profileId);
    if (result.error) return res.json(null);
    res.json({ joined: result.joined, pending: result.pending, counts: result.counts, fetchedAt: result.fetchedAt });
  } catch {
    res.json(null);
  }
});`;
  const old = `app.get('/api/communities/fetch/:profileId/results', (req, res) => {
  const { profileId } = req.params;
  const mem = lastFetchResults[profileId];
  const rows = db.prepare("SELECT slug, name, status, requested_at, joined_at FROM profile_communities WHERE profile_id = ? ORDER BY status, name").all(profileId);
  if (rows.length > 0) {
    const joined = rows.filter(r => r.status === 'joined').map(r => ({ slug: r.slug, name: r.name || r.slug, joinedAt: r.joined_at }));
    const pending = rows.filter(r => r.status === 'pending').map(r => ({ slug: r.slug, name: r.name || r.slug, requestedAt: r.requested_at }));
    const canceled = rows.filter(r => r.status === 'canceled').map(r => ({ slug: r.slug, name: r.name || r.slug, requestedAt: r.requested_at }));
    return res.json({ joined, pending, canceled, fetchedAt: mem?.fetchedAt || new Date().toISOString() });
  }
  res.json(mem || null);
});`;
  s = s.replace(old, newHandler);
  fs.writeFileSync(SERVER, s);
});

// ============ TASK 2: cancelJoinViaFetch in joinCommunity.js ============
patch('joinCommunity: add cancelJoinViaFetch', () => {
  let s = fs.readFileSync(JOIN, 'utf8');
  if (s.includes('cancelJoinViaFetch')) return;
  const fn = `
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
        body: '{}',
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
`;
  s = s.replace(
    'module.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool, leaveGroupOnSkool };',
    fn + '\nmodule.exports = { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool, leaveGroupOnSkool, cancelJoinViaFetch };'
  );
  fs.writeFileSync(JOIN, s);
});

// ============ TASK 3: POST /api/communities/cancel (verified) ============
patch('server: require cancelJoinViaFetch', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('cancelJoinViaFetch')) return;
  s = s.replace(
    "const { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool, leaveGroupOnSkool } = require('./joinCommunity');",
    "const { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool, leaveGroupOnSkool, cancelJoinViaFetch } = require('./joinCommunity');"
  );
  fs.writeFileSync(SERVER, s);
});

patch('server: add POST /api/communities/cancel verified', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes("app.post('/api/communities/cancel'")) return;
  const cancelRoute = `
app.post('/api/communities/cancel', async (req, res) => {
  const { profileId, slug } = req.body;
  if (!profileId || !slug) return res.status(400).json({ error: 'profileId and slug required' });
  try {
    const actionResult = await cancelJoinViaFetch(profileId, slug);
    if (!actionResult.ok) return res.status(400).json({ ok: false, error: actionResult.error || 'Cancel failed' });
    const after = await fetchCommunitiesFromApi(profileId);
    if (after.error) return res.status(500).json({ ok: false, error: 'Re-fetch failed' });
    const stillPending = (after.pending || []).some(p => ((p.slug || '').toLowerCase()) === slug.toLowerCase());
    if (stillPending) {
      console.warn('[communities] cancel verify failed: still_pending', profileId, slug);
      return res.json({ ok: false, error: 'still_pending' });
    }
    console.log('[communities] cancel verified', profileId, slug);
    res.json({ ok: true });
  } catch (e) {
    console.error('[communities] cancel error', e);
    res.status(500).json({ ok: false, error: e.message });
  }
});

`;
  s = s.replace(
    "app.post('/api/communities/cancel-request', async (req, res) => {",
    cancelRoute + "app.post('/api/communities/cancel-request', async (req, res) => {"
  );
  fs.writeFileSync(SERVER, s);
});

// ============ TASK 4: POST /api/communities/leave verified ============
patch('server: update leave route with verification', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  const newLeave = `app.post('/api/communities/leave', async (req, res) => {
  const { profileId, communitySlug } = req.body;
  const slug = communitySlug || req.body.slug;
  if (!profileId || !slug) return res.status(400).json({ error: 'profileId and slug required' });
  try {
    const result = await leaveGroupOnSkool(profileId, slug);
    if (!result.success) return res.status(400).json({ ok: false, error: result.error || 'Leave failed' });
    const after = await fetchCommunitiesFromApi(profileId);
    if (after.error) return res.status(500).json({ ok: false, error: 'Re-fetch failed' });
    const stillJoined = (after.joined || []).some(p => ((p.slug || '').toLowerCase()) === slug.toLowerCase());
    if (stillJoined) {
      console.warn('[communities] leave verify failed: still_joined', profileId, slug);
      return res.json({ ok: false, error: 'still_joined' });
    }
    console.log('[communities] leave verified', profileId, slug);
    res.json({ ok: true });
  } catch (e) {
    console.error('[communities] leave error', e);
    res.status(500).json({ ok: false, error: e.message });
  }
});`;
  const oldLeave = `app.post('/api/communities/leave', async (req, res) => {
  const { profileId, communitySlug } = req.body;
  if (!profileId || !communitySlug) return res.status(400).json({ error: 'profileId and communitySlug required' });
  try {
    const result = await leaveGroupOnSkool(profileId, communitySlug);
    if (result.success) {
      try {
        db.prepare("DELETE FROM profile_communities WHERE profile_id = ? AND lower(slug) = lower(?)").run(profileId, communitySlug);
      } catch (e) {}
      const data = lastFetchResults[profileId];
      if (data && data.joined) {
        const slugLower = communitySlug.toLowerCase();
        data.joined = data.joined.filter(j => ((j.slug || j).toLowerCase()) !== slugLower);
      }
      res.json({ success: true });
    } else {
      res.status(400).json({ success: false, error: result.error || 'Failed to leave' });
    }
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});`;
  s = s.replace(oldLeave, newLeave);
  fs.writeFileSync(SERVER, s);
});

// ============ TASK 5: Frontend api.ts ============
patch('api: add getCommunities cancelCommunity leaveCommunity', () => {
  let s = fs.readFileSync(API, 'utf8');
  if (s.includes('getCommunities')) return;
  s = s.replace(
    "getFetchResults: (profileId: string) => request(`/api/communities/fetch/${profileId}/results`),",
    "getFetchResults: (profileId: string) => request(`/api/communities/fetch/${profileId}/results`),\n  getCommunities: (profileId: string) => request(`/api/communities?profileId=${encodeURIComponent(profileId)}`),"
  );
  s = s.replace(
    "cancelRequest: (profileId: string, communitySlug: string) => request('/api/communities/cancel-request', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),",
    "cancelRequest: (profileId: string, communitySlug: string) => request('/api/communities/cancel-request', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),\n  cancelCommunity: (profileId: string, slug: string) => request('/api/communities/cancel', { method: 'POST', body: JSON.stringify({ profileId, slug }) }),"
  );
  s = s.replace(
    "leaveGroup: (profileId: string, communitySlug: string) => request('/api/communities/leave', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),",
    "leaveGroup: (profileId: string, communitySlug: string) => request('/api/communities/leave', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),\n  leaveCommunity: (profileId: string, slug: string) => request('/api/communities/leave', { method: 'POST', body: JSON.stringify({ profileId, slug }) }),"
  );
  fs.writeFileSync(API, s);
});

// ============ TASK 5: Frontend AccountsTab - loadCommunitiesForProfile ============
patch('AccountsTab: loadCommunitiesForProfile use getCommunities', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('api.getCommunities(pid)')) return;
  s = s.replace(
    "const res = await api.getFetchResults(pid);",
    "const res = await api.getCommunities(pid) ?? await api.getFetchResults(pid);"
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('AccountsTab: cancel handler - ok check', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('api.cancelCommunity(p.id, slug)')) return;
  s = s.replace(
    /await api\.cancelRequest\(p\.id, slug\); await loadCommunitiesForProfile\(p\.id\); fetchProfiles\(\); toast\.success\("Request cancelled"\);/,
    'const r = await api.cancelCommunity(p.id, slug); if (r?.ok === true) { await loadCommunitiesForProfile(p.id); fetchProfiles(); toast.success("Request cancelled"); } else { toast.error(r?.error || "Cancel failed"); }'
  );
  fs.writeFileSync(ACCOUNTS, s);
});

patch('AccountsTab: leave handler - ok check', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('api.leaveCommunity(p.id, slug)')) return;
  s = s.replace(
    /await api\.leaveGroup\(p\.id, slug\); await loadCommunitiesForProfile\(p\.id\); fetchProfiles\(\); toast\.success\("Left group"\);/,
    'const r = await api.leaveCommunity(p.id, slug); if (r?.ok === true) { await loadCommunitiesForProfile(p.id); fetchProfiles(); toast.success("Left group"); } else { toast.error(r?.error || "Leave failed"); }'
  );
  fs.writeFileSync(ACCOUNTS, s);
});

// ============ TASK 6: Remove lie paths from cancel-request (keep for backward compat, but remove mutations) ============
// User said keep cancel-request for backward compat. We could leave it as-is for old clients.
// The new /cancel is the verified one. Frontend now uses cancelCommunity -> /cancel.
// Optionally we could strip the optimistic mutations from cancel-request so it doesn't lie.
// The user said "Remove/disable: db UPDATE status='canceled', lastFetchResults.pending mutation, adding to data.canceled"
// So we should remove those from cancel-request when we're done. But that might break old clients that expect success:true.
// Actually - if old clients call cancel-request and we return success:true without verification, we're still lying.
// The user said to remove the lie paths. So we should change cancel-request to either:
// 1) Redirect to the same flow as /cancel (use cancelJoinViaFetch + verify)
// 2) Or remove the mutations so at least we don't persist lies - but we'd still return success:true optimistically.
// The cleanest: make cancel-request also use cancelJoinViaFetch + verify, and return { ok: true } on success.
// That way both endpoints are truthful. Let me update cancel-request to use the same verified flow.
patch('server: cancel-request use verified flow', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  const newCancelRequest = `app.post('/api/communities/cancel-request', async (req, res) => {
  const { profileId, communitySlug } = req.body;
  const slug = communitySlug || req.body.slug;
  if (!profileId || !slug) return res.status(400).json({ error: 'profileId and slug required' });
  try {
    const actionResult = await cancelJoinViaFetch(profileId, slug);
    if (!actionResult.ok) return res.status(400).json({ ok: false, success: false, error: actionResult.error || 'Cancel failed' });
    const after = await fetchCommunitiesFromApi(profileId);
    if (after.error) return res.status(500).json({ ok: false, success: false, error: 'Re-fetch failed' });
    const stillPending = (after.pending || []).some(p => ((p.slug || '').toLowerCase()) === slug.toLowerCase());
    if (stillPending) {
      console.warn('[communities] cancel-request verify failed: still_pending', profileId, slug);
      return res.json({ ok: false, success: false, error: 'still_pending' });
    }
    console.log('[communities] cancel-request verified', profileId, slug);
    res.json({ ok: true, success: true });
  } catch (e) {
    console.error('[communities] cancel-request error', e);
    res.status(500).json({ ok: false, success: false, error: e.message });
  }
});`;
  const oldCancelRequest = `app.post('/api/communities/cancel-request', async (req, res) => {
  const { profileId, communitySlug } = req.body;
  if (!profileId || !communitySlug) return res.status(400).json({ error: 'profileId and communitySlug required' });
  try {
    const result = await cancelRequestOnSkool(profileId, communitySlug);
    if (result.success) {
      try {
        db.prepare("UPDATE profile_communities SET status = 'canceled' WHERE profile_id = ? AND lower(slug) = lower(?)").run(profileId, communitySlug);
      } catch (e) {}
      const data = lastFetchResults[profileId];
      if (data && data.pending) {
        const slugLower = communitySlug.toLowerCase();
        const found = data.pending.find(p => ((p.slug || p).toLowerCase()) === slugLower);
        if (found) {
          data.pending = data.pending.filter(p => ((p.slug || p).toLowerCase()) !== slugLower);
          (data.canceled = data.canceled || []).push({ slug: found.slug || found, name: found.name || found, requestedAt: found.requestedAt });
        }
      }
      res.json({ success: true });
    } else {
      res.status(400).json({ success: false, error: result.error || 'Failed to cancel' });
    }
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});`;
  s = s.replace(oldCancelRequest, newCancelRequest);
  fs.writeFileSync(SERVER, s);
});

console.log('Deploy cancel/leave truthful complete.');
