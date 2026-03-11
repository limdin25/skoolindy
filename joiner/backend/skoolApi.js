const BASE = 'https://api2.skool.com';
async function skoolRequest(cookieJson, method, endpoint, body) {
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
  if (!text || text.trim() === '') return { ok: true };
  try {
    return JSON.parse(text);
  } catch (e) {
    console.warn('[skoolRequest] JSON parse failed, first 200 chars:', text.slice(0, 200));
    return { ok: true, raw: text };
  }
}
async function getGroups(cookieJson) {
  const allGroups = [];
  let offset = 0;
  const limit = 30;
  while (true) {
    const data = await skoolRequest(cookieJson, 'GET', `/self/groups?offset=${offset}&limit=${limit}&prefs=false&members=true`);
    const groups = data.groups || [];
    allGroups.push(...groups);
    if (!data.has_more || groups.length < limit) break;
    offset += limit;
  }
  return { groups: allGroups, has_more: false };
}
module.exports = { getGroups, skoolRequest };
