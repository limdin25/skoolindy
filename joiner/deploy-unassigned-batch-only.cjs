#!/usr/bin/env node
/** Add POST /api/unassigned/batch + api.importUnassignedCsv. Run on server. */
const fs = require('fs');
const path = require('path');
const ROOT = process.env.COMMUNITY_JOIN_ROOT || '/root/.openclaw/workspace/community-join-manager';
const SERVER = path.join(ROOT, 'backend/server.js');
const API = path.join(ROOT, 'src/lib/api.ts');

let s = fs.readFileSync(SERVER, 'utf8');
if (!s.includes("app.post('/api/unassigned/batch'")) {
  const batch = `
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
`;
  s = s.replace("app.post('/api/unassigned/assign',", batch.trim() + "\napp.post('/api/unassigned/assign',");
  fs.writeFileSync(SERVER, s);
  console.log('OK: added POST /api/unassigned/batch');
}

s = fs.readFileSync(API, 'utf8');
if (!s.includes('importUnassignedCsv')) {
  s = s.replace(
    /importQueueCsv:\s*\([^)]+\)[^,]+,/,
    (m) => m + "\n  importUnassignedCsv: (items) => request('/api/unassigned/batch', { method: 'POST', body: JSON.stringify({ items }) }),"
  );
  fs.writeFileSync(API, s);
  console.log('OK: added importUnassignedCsv');
}
console.log('Done.');
