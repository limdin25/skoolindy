#!/usr/bin/env node
/**
 * Verification + Unassigned: unassigned_queue table, GET/POST API, api.ts, db migration.
 * Run on server: cd /root/.openclaw/workspace/community-join-manager && node deploy-unassigned-verification.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = process.env.COMMUNITY_JOIN_ROOT || '/root/.openclaw/workspace/community-join-manager';
const DB = path.join(ROOT, 'backend/db.js');
const SERVER = path.join(ROOT, 'backend/server.js');
const API = path.join(ROOT, 'src/lib/api.ts');

function patch(name, fn) {
  try {
    fn();
    console.log('OK:', name);
  } catch (e) {
    console.error('FAIL:', name, e.message);
  }
}

// 1) server.js: Migration (run on load) + GET /api/unassigned, POST /api/unassigned/assign
patch('server: unassigned_queue migration + routes', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes("app.get('/api/unassigned'")) return;
  const migration = `\ntry { db.exec("CREATE TABLE IF NOT EXISTS unassigned_queue (id TEXT PRIMARY KEY, group_slug TEXT NOT NULL, group_name TEXT, created_at TEXT DEFAULT (datetime('now')), source TEXT, notes TEXT)"); } catch (e) { console.warn('unassigned_queue migration:', e.message); }`;
  if (!s.includes('unassigned_queue')) {
    s = s.replace(/(require\s*\(\s*['"]\.\/db['"]\s*\)\s*;?\s*\n)/, `$1${migration}\n`);
  }
  const routes = `
app.get('/api/unassigned', (req, res) => {
  try {
    const rows = db.prepare('SELECT id, group_slug, group_name, created_at, source, notes FROM unassigned_queue ORDER BY created_at DESC').all();
    res.json(rows);
  } catch (e) { res.status(500).json({ error: e.message }); }
});
app.post('/api/unassigned', (req, res) => {
  const { group_slug, group_name, source, notes } = req.body || {};
  if (!group_slug) return res.status(400).json({ error: 'group_slug required' });
  try {
    const id = uuidv4();
    db.prepare('INSERT INTO unassigned_queue (id, group_slug, group_name, source, notes) VALUES (?, ?, ?, ?, ?)')
      .run(id, String(group_slug).trim(), group_name || null, source || null, notes || null);
    res.json({ ok: true, id });
  } catch (e) { res.status(500).json({ error: e.message }); }
});
app.post('/api/unassigned/assign', (req, res) => {
  const { id, profile_id } = req.body || {};
  if (!id || !profile_id) return res.status(400).json({ error: 'id and profile_id required' });
  try {
    const row = db.prepare('SELECT * FROM unassigned_queue WHERE id = ?').get(id);
    if (!row) return res.status(404).json({ error: 'Unassigned item not found' });
    db.prepare('INSERT INTO join_queue (id, profile_id, group_slug, group_name, status) VALUES (?, ?, ?, ?, ?)')
      .run(uuidv4(), profile_id, row.group_slug, row.group_name || row.group_slug || null, 'queued');
    db.prepare('DELETE FROM unassigned_queue WHERE id = ?').run(id);
    res.json({ ok: true });
  } catch (e) { res.status(500).json({ error: e.message }); }
});
`;
  const markers = [
    '// ==================== SETTINGS',
    '// ==================== LOGS',
    'app.listen('
  ];
  for (const m of markers) {
    if (s.includes(m)) {
      s = s.replace(new RegExp('\\n(' + m.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')'), routes + '\n$1');
      break;
    }
  }
  if (!s.includes("app.get('/api/unassigned'")) {
    s = s.replace(/(app\.get\('\/api\/queue')/, routes + '$1');
  }
  fs.writeFileSync(SERVER, s);
});

// 2) api.ts: getUnassigned, assignUnassigned
patch('api: getUnassigned assignUnassigned', () => {
  let s = fs.readFileSync(API, 'utf8');
  if (s.includes('getUnassigned')) return;
  s = s.replace(
    /getQueueForProfile:\s*\([^)]+\)\s*=>\s*request\([^)]+\),/,
    (m) => m + "\n  getUnassigned: () => request('/api/unassigned'),\n  assignUnassigned: (id: string, profileId: string) => request('/api/unassigned/assign', { method: 'POST', body: JSON.stringify({ id, profile_id: profileId }) }),"
  );
  fs.writeFileSync(API, s);
});

console.log('Deploy unassigned verification done.');
