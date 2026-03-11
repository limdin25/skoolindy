#!/usr/bin/env node
/**
 * Deploy communities persistence, toast, table UI, cancel/delete flows.
 * Run on server: node deploy-communities-persistence.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = '/root/.openclaw/workspace/community-join-manager';
const DB = path.join(ROOT, 'backend/db.js');
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

// 1. db.js: Add profile_communities table
patch('db: profile_communities table', () => {
  let s = fs.readFileSync(DB, 'utf8');
  if (s.includes('profile_communities')) return;
  s = s.replace(
    "CREATE TABLE IF NOT EXISTS community_pool (",
    "CREATE TABLE IF NOT EXISTS profile_communities (\n    id TEXT PRIMARY KEY,\n    profile_id TEXT NOT NULL,\n    slug TEXT NOT NULL,\n    name TEXT,\n    status TEXT NOT NULL DEFAULT 'joined',\n    requested_at TEXT,\n    joined_at TEXT,\n    created_at TEXT DEFAULT (datetime('now')),\n    FOREIGN KEY (profile_id) REFERENCES profiles(id) ON DELETE CASCADE,\n    UNIQUE(profile_id, slug)\n  );\n\n  CREATE TABLE IF NOT EXISTS community_pool ("
  );
  fs.writeFileSync(DB, s);
});

// 2. server.js: Upsert on fetch
patch('server: profile_communities upsert', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes("upsertComm.run(uuidv4()")) return;
  s = s.replace(
    "lastFetchResults[profileId] = { joined: r.joined || [], pending: r.pending || [], fetchedAt: new Date().toISOString() };",
    "lastFetchResults[profileId] = { joined: r.joined || [], pending: r.pending || [], fetchedAt: new Date().toISOString() };\n    const upsertComm = db.prepare(\"INSERT INTO profile_communities (id, profile_id, slug, name, status, requested_at, joined_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(profile_id, slug) DO UPDATE SET name=excluded.name, status=excluded.status, requested_at=excluded.requested_at, joined_at=excluded.joined_at)\");\n    const toSlug = (x) => (typeof x === 'string' ? x : (x && x.slug) || '').toString().trim().toLowerCase();\n    const toName = (x) => (typeof x === 'string' ? x : (x && (x.name || x.slug)) || '').toString();\n    for (const c of (r.joined || [])) {\n      const slug = toSlug(c);\n      if (!slug) continue;\n      upsertComm.run(uuidv4(), profileId, slug, toName(c), 'joined', null, new Date().toISOString());\n    }\n    for (const c of (r.pending || [])) {\n      const slug = toSlug(c);\n      if (!slug) continue;\n      const reqAt = (typeof c === 'object' && c.requestedAt) ? c.requestedAt : null;\n      upsertComm.run(uuidv4(), profileId, slug, toName(c), 'pending', reqAt, null);\n    }"
  );
  fs.writeFileSync(SERVER, s);
});

// 3. server: cancel -> update DB to canceled + move to canceled in mem
patch('server: cancel-request update DB', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes("UPDATE profile_communities SET status = 'canceled'")) return;
  s = s.replace(
    "if (result.success) {\n      const data = lastFetchResults[profileId];\n      if (data && data.pending) {\n        data.pending = data.pending.filter(p => ((p.slug || p).toLowerCase()) !== communitySlug.toLowerCase());\n      }\n      res.json({ success: true });\n    }",
    "if (result.success) {\n      try {\n        db.prepare(\"UPDATE profile_communities SET status = 'canceled' WHERE profile_id = ? AND lower(slug) = lower(?)\").run(profileId, communitySlug);\n      } catch (e) {}\n      const data = lastFetchResults[profileId];\n      if (data && data.pending) {\n        const slugLower = communitySlug.toLowerCase();\n        const found = data.pending.find(p => ((p.slug || p).toLowerCase()) === slugLower);\n        if (found) {\n          data.pending = data.pending.filter(p => ((p.slug || p).toLowerCase()) !== slugLower);\n          (data.canceled = data.canceled || []).push({ slug: found.slug || found, name: found.name || found, requestedAt: found.requestedAt });\n        }\n      }\n      res.json({ success: true });\n    }"
  );
  fs.writeFileSync(SERVER, s);
});

// 4. server: GET results from DB, DELETE remove
patch('server: GET results + DELETE', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes('FROM profile_communities')) return;
  s = s.replace(
    "app.get('/api/communities/fetch/:profileId/results', (req, res) => {\n  const { profileId } = req.params;\n  const data = lastFetchResults[profileId] || null;\n  res.json(data);\n});",
    "app.get('/api/communities/fetch/:profileId/results', (req, res) => {\n  const { profileId } = req.params;\n  const mem = lastFetchResults[profileId];\n  const rows = db.prepare(\"SELECT slug, name, status, requested_at, joined_at FROM profile_communities WHERE profile_id = ? ORDER BY status, name\").all(profileId);\n  if (rows.length > 0) {\n    const joined = rows.filter(r => r.status === 'joined').map(r => ({ slug: r.slug, name: r.name || r.slug, joinedAt: r.joined_at }));\n    const pending = rows.filter(r => r.status === 'pending').map(r => ({ slug: r.slug, name: r.name || r.slug, requestedAt: r.requested_at }));\n    const canceled = rows.filter(r => r.status === 'canceled').map(r => ({ slug: r.slug, name: r.name || r.slug, requestedAt: r.requested_at }));\n    return res.json({ joined, pending, canceled, fetchedAt: mem?.fetchedAt || new Date().toISOString() });\n  }\n  res.json(mem || null);\n});\n\napp.delete('/api/communities/remove/:profileId/:slug', (req, res) => {\n  const { profileId, slug } = req.params;\n  if (!profileId || !slug) return res.status(400).json({ error: 'profileId and slug required' });\n  try {\n    const r = db.prepare('DELETE FROM profile_communities WHERE profile_id = ? AND lower(slug) = lower(?)').run(profileId, decodeURIComponent(slug));\n    res.json({ success: true, deleted: r.changes > 0 });\n  } catch (e) {\n    res.status(500).json({ success: false, error: e.message });\n  }\n});"
  );
  fs.writeFileSync(SERVER, s);
});

// 5. api.ts: removeCommunity
patch('api: removeCommunity', () => {
  let s = fs.readFileSync(API, 'utf8');
  if (s.includes('removeCommunity')) return;
  s = s.replace(
    "cancelRequest: (profileId: string, communitySlug: string) => request('/api/communities/cancel-request', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),",
    "cancelRequest: (profileId: string, communitySlug: string) => request('/api/communities/cancel-request', { method: 'POST', body: JSON.stringify({ profileId, communitySlug }) }),\n  removeCommunity: (profileId: string, slug: string) => request(\`/api/communities/remove/\${profileId}/\${encodeURIComponent(slug)}\`, { method: 'DELETE' }),"
  );
  fs.writeFileSync(API, s);
});

// 6. AccountsTab: toast import
patch('AccountsTab: import toast', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('from \"sonner\"')) return;
  s = s.replace(
    'import { api } from "@/lib/api";',
    'import { api } from "@/lib/api";\nimport { toast } from "sonner";'
  );
  fs.writeFileSync(ACCOUNTS, s);
});

// 7. AccountsTab: replace alert with toast
patch('AccountsTab: toast instead of alert', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  s = s.replace(
    "alert(\`Fetch complete. \${s.resolved || 0} queue items updated to Joined. Found \${s.joinedCount || 0} joined, \${s.pendingCount || 0} pending on Skool.\`);",
    "toast.success(\`Fetch complete. \${s.resolved || 0} queue items updated. Found \${s.joinedCount || 0} joined, \${s.pendingCount || 0} pending on Skool.\`);"
  );
  fs.writeFileSync(ACCOUNTS, s);
});

console.log('Deploy patches done.');

// 8. AccountsTab: add loadCommunitiesForProfile and load on expand
patch('AccountsTab: loadCommunitiesForProfile', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('loadCommunitiesForProfile')) return;
  s = s.replace(
    "const [fetchResultsByProfile, setFetchResultsByProfile] = useState<Record<string, { joined: { slug: string; name: string }[]; pending: { slug: string; name: string; requestedAt: string | null }[]; fetchedAt: string } | null>>({});",
    "const [fetchResultsByProfile, setFetchResultsByProfile] = useState<Record<string, { joined: { slug: string; name: string; joinedAt?: string }[]; pending: { slug: string; name: string; requestedAt: string | null }[]; canceled?: { slug: string; name: string; requestedAt: string | null }[]; fetchedAt: string } | null>>({});"
  );
  s = s.replace(
    "const fetchProfiles = useCallback(async () => {",
    "const loadCommunitiesForProfile = useCallback(async (pid: string) => {\n    try { const res = await api.getFetchResults(pid); if (res) setFetchResultsByProfile(prev => ({ ...prev, [pid]: res })); } catch (_) {}\n  }, []);\n  const fetchProfiles = useCallback(async () => {"
  );
  s = s.replace(
    "useEffect(() => { fetchProfiles(); }, [fetchProfiles]);",
    "useEffect(() => { fetchProfiles(); }, [fetchProfiles]);\n  useEffect(() => { profiles.forEach(p => loadCommunitiesForProfile(p.id)); }, [profiles.length, loadCommunitiesForProfile]);"
  );
  fs.writeFileSync(ACCOUNTS, s);
});

// 9. AccountsTab: Communities table UI - replace block
patch('AccountsTab: Communities table', () => {
  let s = fs.readFileSync(ACCOUNTS, 'utf8');
  if (s.includes('fmtDate(c.status')) return;
  s = s.replace(
    "if (!data || (data.joined.length === 0 && data.pending.length === 0)) return null;",
    "const canceled = data?.canceled || []; const allItems = [...(data?.joined || []).map((c: any) => ({ ...c, status: 'joined' })), ...(data?.pending || []).map((c: any) => ({ ...c, status: 'pending' })), ...canceled.map((c: any) => ({ ...c, status: 'canceled' }))]; if (allItems.length === 0) return null;"
  );
  s = s.replace(
    "const daysPending = (req: string | null) => {\n                                    if (!req) return null;\n                                    const m = req.match(/(\\d{1,2})\\/(\\d{1,2})\\/(\\d{4})/);\n                                    if (!m) return null;\n                                    const d = new Date(parseInt(m[3]), parseInt(m[1]) - 1, parseInt(m[2]));\n                                    return Math.floor((Date.now() - d.getTime()) / 86400000);\n                                  };\n                                  return (\n                                    <div className=\"mt-3 pt-3 border-t border-border\">\n                                      <span className=\"text-muted-foreground text-xs font-medium\">Communities from Skool</span>\n                                      <div className=\"mt-2 space-y-1.5 max-h-48 overflow-y-auto\">\n                                        {data.joined.map((c: any) => (\n                                          <div key={(c.slug || c) + \"-j\"} className=\"flex items-center justify-between gap-2 text-xs py-1\">\n                                            <span className=\"truncate\">{(c.name || c.slug || c)}</span>\n                                            <span className=\"text-green-600 text-xs font-medium\">Already inside</span>\n                                          </div>\n                                        ))}\n                                        {data.pending.map((c: any) => {\n                                          const slug = c.slug || c;\n                                          const days = daysPending(c.requestedAt);\n                                          return (\n                                            <div key={(c.slug || c) + \"-p\"} className=\"flex items-center justify-between gap-2 text-xs py-1\">\n                                              <div className=\"min-w-0 flex-1\">\n                                                <span className=\"truncate block\">{(c.name || c.slug || c)}</span>\n                                                <span className=\"text-yellow-600 text-[10px] font-medium\">Pending</span>\n                                                <span className=\"text-muted-foreground text-[10px] block\">\n                                                  {c.requestedAt ? `Requested ${c.requestedAt}` : \"Pending\"}\n                                                  {days != null ? ` · ${days} day${days !== 1 ? \"s\" : \"\"} pending` : \"\"}\n                                                </span>\n                                              </div>\n                                              <Button variant=\"ghost\" size=\"sm\" className=\"h-6 text-xs gap-1 text-destructive shrink-0\"\n                                                onClick={async () => { if (!confirm(\"Cancel this request?\")) return; try { await api.cancelRequest(p.id, slug); setFetchResultsByProfile(prev => ({ ...prev, [p.id]: prev[p.id] ? { ...prev[p.id], pending: prev[p.id].pending.filter((x: any) => (x.slug || x) !== slug) } : prev[p.id] })); } catch (e: any) { alert(e.message); } }}>\n                                                <XCircle className=\"h-3 w-3\" /> Cancel Request\n                                              </Button>\n                                            </div>\n                                          );\n                                        })}\n                                      </div>\n                                    </div>\n                                  );",
    "const fmtDate = (st: string, req: string | null, joined: string | null) => { if (st === 'joined' && joined) return joined.slice(0, 10); if ((st === 'pending' || st === 'canceled') && req) return req; return '—'; };\n                                  return (\n                                    <div className=\"mt-3 pt-3 border-t border-border col-span-2 md:col-span-5\">\n                                      <span className=\"text-muted-foreground text-xs font-medium block mb-2\">Communities</span>\n                                      <div className=\"rounded border border-border overflow-hidden max-h-64 overflow-y-auto\">\n                                        <Table>\n                                          <TableHeader>\n                                            <TableRow className=\"bg-muted/50\">\n                                              <TableHead className=\"text-xs\">Community</TableHead>\n                                              <TableHead className=\"text-xs\">Date</TableHead>\n                                              <TableHead className=\"text-xs\">Status</TableHead>\n                                              <TableHead className=\"text-xs text-right w-20\">Action</TableHead>\n                                            </TableRow>\n                                          </TableHeader>\n                                          <TableBody>\n                                            {allItems.map((c: any) => {\n                                              const slug = c.slug || c;\n                                              return (\n                                                <TableRow key={slug + '-' + c.status}>\n                                                  <TableCell className=\"text-xs truncate max-w-[120px]\">{(c.name || c.slug || c)}</TableCell>\n                                                  <TableCell className=\"text-xs text-muted-foreground\">{fmtDate(c.status, c.requestedAt, c.joinedAt)}</TableCell>\n                                                  <TableCell className=\"text-xs\">\n                                                    {c.status === 'joined' && <span className=\"text-green-600 font-medium\">Already inside</span>}\n                                                    {c.status === 'pending' && (\n                                                      <span className=\"flex items-center gap-1\">\n                                                        <span className=\"text-yellow-600 font-medium\">Pending</span>\n                                                        <Button variant=\"ghost\" size=\"sm\" className=\"h-5 text-[10px] gap-0.5 text-destructive p-1\"\n                                                          onClick={async () => { if (!confirm(\"Cancel this request?\")) return; try { await api.cancelRequest(p.id, slug); await loadCommunitiesForProfile(p.id); toast.success(\"Request canceled\"); } catch (e: any) { toast.error(e.message); } }}>\n                                                          <XCircle className=\"h-3 w-3\" /> Cancel Request\n                                                        </Button>\n                                                      </span>\n                                                    )}\n                                                    {c.status === 'canceled' && <span className=\"text-muted-foreground font-medium\">Canceled</span>}\n                                                  </TableCell>\n                                                  <TableCell className=\"text-right\">\n                                                    <Button variant=\"ghost\" size=\"sm\" className=\"h-6 w-6 p-0 text-muted-foreground hover:text-destructive\"\n                                                      onClick={async () => { try { await api.removeCommunity(p.id, slug); await loadCommunitiesForProfile(p.id); toast.success(\"Removed from list\"); } catch (e: any) { toast.error(e.message); } }}>\n                                                      <Trash2 className=\"h-3 w-3\" />\n                                                    </Button>\n                                                  </TableCell>\n                                                </TableRow>\n                                              );\n                                            })}\n                                          </TableBody>\n                                        </Table>\n                                      </div>\n                                    </div>\n                                  );"
  );
  fs.writeFileSync(ACCOUNTS, s);
});
