const fs = require('fs');
const path = '/root/.openclaw/workspace/community-join-manager/backend/server.js';
let s = fs.readFileSync(path, 'utf8');

const oldBlock = `async function fetchCommunitiesForProfile(profileId) {
  const profile = db.prepare('SELECT * FROM profiles WHERE id = ?').get(profileId);
  if (!profile || !profile.cookie_json) return { error: 'No cookie', added: 0, resolved: 0 };
  if (fetchProfileProgress.running) return { error: 'Fetch already in progress', resolved: 0 };
  fetchProfileProgress = { running: true, profileId, status: 'Starting...', current: 0, total: 0, community: '', resolved: 0, joinedCount: 0, pendingCount: 0 };
  try {
    const r = await fetchCommunitiesFromSkoolSettings(profileId, (p) => {
      fetchProfileProgress.status = p.status || '';
      fetchProfileProgress.current = p.current || 0;
      fetchProfileProgress.total = p.total || 0;
      fetchProfileProgress.community = p.community || '';
    }, profile.cookie_json);
    fetchProfileProgress.running = false;
    if (r.error) return { error: r.error, resolved: 0 };`;

const newBlock = `async function parseGroupsFromApi(data) {
  const groups = data?.groups || data?.data || data || [];
  const joined = []; const pending = [];
  for (const g of groups) {
    const slug = (g.slug || '').toString().trim();
    if (!slug) continue;
    const name = (g.metadata?.display_name || g.name || slug).toString().trim();
    let member = g.metadata?.member;
    if (typeof member === 'string') { try { member = JSON.parse(member); } catch (e) { continue; } }
    if (typeof member === 'string') { try { member = JSON.parse(member); } catch (e) { continue; } }
    const role = (member?.role || 'member').toLowerCase();
    const status = role === 'pending' ? 'pending' : 'member';
    if (status === 'member' || role === 'member') {
      joined.push({ slug, name, joinedAt: member?.approved_at || new Date().toISOString() });
    } else {
      const ns = member?.metadata?.requested_at;
      const requestedAt = ns != null ? new Date(Math.floor(Number(ns) / 1e6)).toISOString() : null;
      pending.push({ slug, name, requestedAt });
    }
  }
  return { joined, pending };
}
async function fetchCommunitiesForProfile(profileId) {
  const profile = db.prepare('SELECT * FROM profiles WHERE id = ?').get(profileId);
  if (!profile || !profile.cookie_json) return { error: 'No cookie', added: 0, resolved: 0 };
  if (fetchProfileProgress.running) return { error: 'Fetch already in progress', resolved: 0 };
  fetchProfileProgress = { running: true, profileId, status: 'Fetching from API...', current: 0, total: 1, community: '', resolved: 0, joinedCount: 0, pendingCount: 0 };
  try {
    const data = await getGroups(profile.cookie_json);
    const r = parseGroupsFromApi(data);
    fetchProfileProgress.running = false;
    if (!r) return { error: 'Parse failed', resolved: 0 };
    const toSlug = (x) => (typeof x === 'string' ? x : (x && x.slug) || '').toLowerCase();
    const joinedSet = new Set((r.joined || []).map(toSlug).filter(Boolean));
    const pendingSet = new Set((r.pending || []).map(toSlug).filter(Boolean));
    fetchProfileProgress.joinedCount = joinedSet.size;
    fetchProfileProgress.pendingCount = pendingSet.size;
    lastFetchResults[profileId] = { joined: r.joined || [], pending: r.pending || [], fetchedAt: new Date().toISOString() };
    if (r.error) return { error: r.error, resolved: 0 };`;

if (!s.includes("fetchCommunitiesFromSkoolSettings(profileId")) {
  console.log("Already patched or structure changed");
  process.exit(1);
}
s = s.replace(oldBlock, newBlock);
fs.writeFileSync(path, s);
console.log("Patched fetchCommunitiesForProfile");
