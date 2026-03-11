#!/usr/bin/env node
/**
 * Instrument and fix GET /api/communities empty response.
 * Run on server: node deploy-debug-communities.cjs
 */
const fs = require('fs');
const path = require('path');

const ROOT = '/root/.openclaw/workspace/community-join-manager';
const SERVER = path.join(ROOT, 'backend/server.js');
const SKOOL = path.join(ROOT, 'backend/skoolApi.js');

function patch(name, fn) {
  try {
    fn();
    console.log('OK:', name);
  } catch (e) {
    console.error('FAIL:', name, e.message);
  }
}

// STEP 1+3: fetchCommunitiesFromApi - log profile, return 400 on missing (no empty arrays)
// fetchCommunitiesFromApi instrumentation
patch('server: fetchCommunitiesFromApi add logs', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('[fetchCommunitiesFromApi] profileId:')) return;
  s = s.replace(
    `async function fetchCommunitiesFromApi(profileId) {
  const profile = db.prepare('SELECT * FROM profiles WHERE id = ?').get(profileId);
  if (!profile || !profile.cookie_json) return { error: 'No profile or cookie' };`,
    `async function fetchCommunitiesFromApi(profileId) {
  const pid = (profileId || '').toString().trim();
  const profile = db.prepare('SELECT * FROM profiles WHERE id = ?').get(pid);
  console.log('[fetchCommunitiesFromApi] profileId:', JSON.stringify(pid), 'exists:', !!profile, 'email:', profile?.email || 'N/A', 'cookie_json len:', profile?.cookie_json ? String(profile.cookie_json).length : 0);
  if (!profile) return { error: 'Profile not found' };
  if (!profile.cookie_json || String(profile.cookie_json).trim() === '') return { error: 'No cookie' };`
  );
  s = s.replace(
    `  try {
    const data = await getGroups(profile.cookie_json);
    const r = parseGroupsFromApi(data);`,
    `  try {
    const data = await getGroups(profile.cookie_json);
    const groups = data?.groups || data?.data || (Array.isArray(data) ? data : []);
    console.log('[fetchCommunitiesFromApi] upstream group count:', groups.length, 'data keys:', data ? Object.keys(data).join(',') : 'null');
    const r = parseGroupsFromApi(data);`
  );
  s = s.replace(
    `  } catch (e) {
    return { error: e.message };
  }
}

async function parseGroupsFromApi`,
    `  } catch (e) {
    console.error('[fetchCommunitiesFromApi] error:', e.message);
    return { error: e.message };
  }
}

async function parseGroupsFromApi`
  );
  fs.writeFileSync(SERVER, s);
});

// Ensure GET handler uses trimmed profileId
patch('server: GET communities trim profileId', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes("(req.query.profileId || '').toString().trim()")) return;
  s = s.replace(
    "app.get('/api/communities', async (req, res) => {\n  const profileId = req.query.profileId;",
    "app.get('/api/communities', async (req, res) => {\n  const profileId = (req.query.profileId || '').toString().trim();"
  );
  fs.writeFileSync(SERVER, s);
});

// STEP 2+4: skoolApi.js - log status, cookie count, throw on parse fail
patch('skoolApi: add instrumentation', () => {
  let s = fs.readFileSync(SKOOL, 'utf8');
  if (s.includes('[skoolRequest]')) return;
  s = s.replace(
    `async function skoolRequest(cookieJson, method, endpoint, body) {
  let cookies = '';
  try {
    const c = typeof cookieJson === 'string' ? JSON.parse(cookieJson) : cookieJson;
    cookies = Array.isArray(c) ? c.map(x => (x.name||'') + '=' + (x.value||'')).join('; ') : '';
  } catch {}
  const opts = {
    method,
    headers: {
      Cookie: cookies,
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0',
      Origin: 'https://www.skool.com',
      Referer: 'https://www.skool.com/',
    },
  };
  if (body && method !== 'GET') opts.body = typeof body === 'string' ? body : JSON.stringify(body);
  const res = await fetch(BASE + endpoint, opts);
  if (!res.ok) throw new Error('Skool API ' + res.status);
  return res.json();
}`,
    `async function skoolRequest(cookieJson, method, endpoint, body) {
  let cookies = '';
  let cookieCount = 0;
  try {
    const c = typeof cookieJson === 'string' ? JSON.parse(cookieJson) : cookieJson;
    cookieCount = Array.isArray(c) ? c.length : 0;
    cookies = Array.isArray(c) ? c.map(x => (x.name||'') + '=' + (x.value||'')).join('; ') : '';
  } catch (e) {
    console.warn('[skoolRequest] cookie parse failed:', e.message);
  }
  console.log('[skoolRequest] cookieCount:', cookieCount, 'cookieHeaderLen:', cookies.length, 'endpoint:', endpoint);
  const opts = {
    method,
    headers: {
      Cookie: cookies,
      'Content-Type': 'application/json',
      'User-Agent': 'Mozilla/5.0',
      Origin: 'https://www.skool.com',
      Referer: 'https://www.skool.com/',
    },
  };
  if (body && method !== 'GET') opts.body = typeof body === 'string' ? body : JSON.stringify(body);
  const res = await fetch(BASE + endpoint, opts);
  console.log('[skoolRequest] status:', res.status, 'endpoint:', endpoint);
  if (!res.ok) {
    const text = await res.text();
    console.warn('[skoolRequest] non-200 response, first 200 chars:', text.slice(0, 200));
    throw new Error('Skool API ' + res.status + ': ' + text.slice(0, 100));
  }
  const text = await res.text();
  if (!text || text.trim() === '') throw new Error('Skool API empty response');
  try {
    return JSON.parse(text);
  } catch (e) {
    console.warn('[skoolRequest] JSON parse failed, first 200 chars:', text.slice(0, 200));
    throw new Error('Skool API parse failed: ' + e.message);
  }
}`
  );
  fs.writeFileSync(SKOOL, s);
});

// STEP 5: debug endpoint
patch('server: add debug endpoint', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('/api/communities/debug')) return;
  s = s.replace(
    "app.get('/api/communities', async (req, res) => {",
    `app.get('/api/communities/debug', async (req, res) => {
  const profileId = (req.query.profileId || '').toString().trim();
  if (!profileId) return res.status(400).json({ error: 'profileId required' });
  try {
    const profile = db.prepare('SELECT id, email, length(cookie_json) as cookie_len FROM profiles WHERE id = ?').get(profileId);
    const result = await fetchCommunitiesFromApi(profileId);
    res.json({
      profileId,
      profileExists: !!profile,
      profileEmail: profile?.email,
      cookieLen: profile?.cookie_len ?? 0,
      resultError: result.error || null,
      joinedCount: result.joined?.length ?? 0,
      pendingCount: result.pending?.length ?? 0,
      counts: result.counts,
    });
  } catch (e) {
    res.status(500).json({ error: e.message });
  }
});

app.get('/api/communities', async (req, res) => {`
  );
  fs.writeFileSync(SERVER, s);
});

console.log('Deploy debug complete.');
