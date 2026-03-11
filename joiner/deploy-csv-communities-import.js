#!/usr/bin/env node
/**
 * CSV Communities Import: default to unassigned, batch POST with dedupe.
 * Run on server: cd /root/.openclaw/workspace/community-join-manager && node deploy-csv-communities-import.js
 */
const fs = require('fs');
const path = require('path');

const ROOT = process.env.COMMUNITY_JOIN_ROOT || '/root/.openclaw/workspace/community-join-manager';
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

// POST /api/unassigned/batch: { items: [{ group_slug, group_name }] } -> { imported, skipped }
patch('server: POST /api/unassigned/batch', () => {
  let s = fs.readFileSync(SERVER, 'utf8');
  if (s.includes("app.post('/api/unassigned/batch'")) return;
  const batchRoute = `
app.post('/api/unassigned/batch', (req, res) => {
  const items = req.body?.items;
  if (!Array.isArray(items)) return res.status(400).json({ error: 'items array required' });
  try {
    const existing = new Set(db.prepare('SELECT lower(trim(group_slug)) as s FROM unassigned_queue').all().map(r => r.s).filter(Boolean));
    let imported = 0, skipped = 0;
    for (const it of items) {
      const slug = (it.group_slug || '').toString().trim();
      if (!slug) { skipped++; continue; }
      const slugLower = slug.toLowerCase();
      if (existing.has(slugLower)) { skipped++; continue; }
      existing.add(slugLower);
      const groupName = (it.group_name || slug || '').toString().trim() || null;
      db.prepare('INSERT INTO unassigned_queue (id, group_slug, group_name, source, notes) VALUES (?, ?, ?, ?, ?)')
        .run(uuidv4(), slug, groupName, 'csv-import', null);
      imported++;
    }
    res.json({ imported, skipped });
  } catch (e) { res.status(500).json({ error: e.message }); }
});

app.post('/api/queue/import', (req, res) => {
  const { profile_id, items } = req.body || {};
  if (!profile_id || !Array.isArray(items)) return res.status(400).json({ error: 'profile_id and items array required' });
  try {
    const existing = new Set(db.prepare('SELECT lower(trim(group_slug)) as s FROM join_queue WHERE profile_id = ?').all(profile_id).map(r => r.s).filter(Boolean));
    let imported = 0, skipped = 0;
    for (const it of items) {
      const slug = (it.group_slug || '').toString().trim().toLowerCase();
      if (!slug) { skipped++; continue; }
      if (existing.has(slug)) { skipped++; continue; }
      existing.add(slug);
      const groupName = (it.group_name || slug || '').toString().trim() || null;
      db.prepare('INSERT INTO join_queue (id, profile_id, group_slug, group_name, status) VALUES (?, ?, ?, ?, ?)')
        .run(uuidv4(), profile_id, slug, groupName, 'queued');
      imported++;
    }
    res.json({ imported, skipped });
  } catch (e) { res.status(500).json({ error: e.message }); }
});
`;
  s = s.replace(
    "app.post('/api/unassigned/assign',",
    batchRoute.trim() + "\napp.post('/api/unassigned/assign',"
  );
  fs.writeFileSync(SERVER, s);
});

// api.ts: importUnassignedCsv
patch('api: importUnassignedCsv', () => {
  if (!fs.existsSync(API)) return;
  let s = fs.readFileSync(API, 'utf8');
  if (s.includes('importUnassignedCsv')) return;
  s = s.replace(
    /assignUnassigned:\s*\([^)]+\)[^,]+,/,
    (m) => m + "\n  importUnassignedCsv: (items: { group_slug: string; group_name?: string }[]) => request('/api/unassigned/batch', { method: 'POST', body: JSON.stringify({ items }) }),\n  importQueueCsv: (profileId: string, items: { group_slug: string; group_name?: string }[]) => request('/api/queue/import', { method: 'POST', body: JSON.stringify({ profile_id: profileId, items }) }),"
  );
  fs.writeFileSync(API, s);
});

console.log('Deploy CSV communities import done.');
