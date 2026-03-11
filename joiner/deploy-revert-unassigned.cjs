#!/usr/bin/env node
/** Revert /api/queue to exclude unassigned (undo deploy-unassigned-in-queue) */
const fs = require('fs');
const path = require('path');
const ROOT = process.env.COMMUNITY_JOIN_ROOT || '/root/.openclaw/workspace/community-join-manager';
const SERVER = path.join(ROOT, 'backend/server.js');

const newHandler = `app.get('/api/queue', (req, res) => {
  const { profile_id, sort_by = 'created_at', order = 'asc' } = req.query;
  const limit = Math.min(parseInt(req.query.limit) || 100000, 100000);
  const dir = order === 'desc' ? 'DESC' : 'ASC';
  const showUnassigned = !profile_id || profile_id === 'all';

  let orderBy = 'COALESCE(jq.sort_order, 999999) ASC, jq.created_at ASC';
  if (sort_by === 'status') orderBy = \`CASE jq.status WHEN 'queued' THEN 0 WHEN 'processing' THEN 1 WHEN 'joined' THEN 2 WHEN 'pending' THEN 3 WHEN 'survey_submitted' THEN 4 ELSE 5 END \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'account') orderBy = \`p.email \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'community') orderBy = \`LOWER(COALESCE(jq.group_name, jq.group_slug)) \${dir}\`;
  else if (sort_by === 'error') orderBy = \`COALESCE(jq.error_msg, '') \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'finished_at') orderBy = \`jq.finished_at \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'created_at') orderBy = \`jq.created_at \${dir}\`;

  let rows = db.prepare(\`
    SELECT jq.*, p.email as profile_email
    FROM join_queue jq
    LEFT JOIN profiles p ON jq.profile_id = p.id
    \${profile_id && profile_id !== 'all' ? 'WHERE jq.profile_id = ?' : ''}
    ORDER BY \${orderBy}
    LIMIT \${limit}
  \`).all(...(profile_id && profile_id !== 'all' ? [profile_id] : []));

  if (showUnassigned) {
    const unassigned = db.prepare('SELECT id, group_slug, group_name, created_at FROM unassigned_queue ORDER BY created_at DESC').all();
    const mapped = unassigned.map(u => ({
      id: u.id, group_slug: u.group_slug, group_name: u.group_name, profile_id: null,
      profile_email: 'Unassigned', status: 'unassigned', error_msg: null, created_at: u.created_at,
      finished_at: null, sort_order: 999999
    }));
    rows = [...mapped, ...rows];
    if (sort_by === 'community') rows.sort((a, b) => (String((a.group_name || a.group_slug) || '').toLowerCase()).localeCompare((String((b.group_name || b.group_slug) || '').toLowerCase())) * (order === 'desc' ? -1 : 1));
    else if (sort_by === 'account') rows.sort((a, b) => (a.profile_email || '').localeCompare(b.profile_email || '') * (order === 'desc' ? -1 : 1));
    else if (sort_by === 'created_at') rows.sort((a, b) => (new Date(a.created_at || 0) - new Date(b.created_at || 0)) * (order === 'desc' ? -1 : 1));
    else if (sort_by === 'status') rows.sort((a, b) => { const ord = { unassigned: -1, queued: 0, processing: 1, joined: 2, pending: 3, survey_submitted: 4 }; return ((ord[a.status] ?? 5) - (ord[b.status] ?? 5)) * (order === 'desc' ? -1 : 1); });
    rows = rows.slice(0, limit);
  }
  res.json(rows);
});`;

const oldHandler = `app.get('/api/queue', (req, res) => {
  const { profile_id, sort_by = 'created_at', order = 'asc' } = req.query;
  const limit = Math.min(parseInt(req.query.limit) || 100000, 100000);
  const dir = order === 'desc' ? 'DESC' : 'ASC';
  let orderBy = 'COALESCE(jq.sort_order, 999999) ASC, jq.created_at ASC';
  if (sort_by === 'status') orderBy = \`CASE jq.status WHEN 'queued' THEN 0 WHEN 'processing' THEN 1 WHEN 'joined' THEN 2 WHEN 'pending' THEN 3 WHEN 'survey_submitted' THEN 4 ELSE 5 END \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'account') orderBy = \`p.email \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'community') orderBy = \`LOWER(COALESCE(jq.group_name, jq.group_slug)) \${dir}\`;
  else if (sort_by === 'error') orderBy = \`COALESCE(jq.error_msg, '') \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'finished_at') orderBy = \`jq.finished_at \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'created_at') orderBy = \`jq.created_at \${dir}\`;

  let rows = db.prepare(\`
    SELECT jq.*, p.email as profile_email
    FROM join_queue jq
    LEFT JOIN profiles p ON jq.profile_id = p.id
    \${profile_id && profile_id !== 'all' ? 'WHERE jq.profile_id = ?' : ''}
    ORDER BY \${orderBy}
    LIMIT \${limit}
  \`).all(...(profile_id && profile_id !== 'all' ? [profile_id] : []));

  if (showUnassigned) {
    const unassigned = db.prepare('SELECT id, group_slug, group_name, created_at FROM unassigned_queue ORDER BY created_at DESC').all();
    const mapped = unassigned.map(u => ({
      id: u.id, group_slug: u.group_slug, group_name: u.group_name, profile_id: null,
      profile_email: 'Unassigned', status: 'unassigned', error_msg: null, created_at: u.created_at,
      finished_at: null, sort_order: 999999
    }));
    rows = [...mapped, ...rows];
    if (sort_by === 'community') rows.sort((a, b) => (String((a.group_name || a.group_slug) || '').toLowerCase()).localeCompare((String((b.group_name || b.group_slug) || '').toLowerCase())) * (order === 'desc' ? -1 : 1));
    else if (sort_by === 'account') rows.sort((a, b) => (a.profile_email || '').localeCompare(b.profile_email || '') * (order === 'desc' ? -1 : 1));
    else if (sort_by === 'created_at') rows.sort((a, b) => (new Date(a.created_at || 0) - new Date(b.created_at || 0)) * (order === 'desc' ? -1 : 1));
    else if (sort_by === 'status') rows.sort((a, b) => { const ord = { unassigned: -1, queued: 0, processing: 1, joined: 2, pending: 3, survey_submitted: 4 }; return ((ord[a.status] ?? 5) - (ord[b.status] ?? 5)) * (order === 'desc' ? -1 : 1); });
    rows = rows.slice(0, limit);
  }
  res.json(rows);
});`;

const revertedHandler = `app.get('/api/queue', (req, res) => {
  const { profile_id, sort_by = 'created_at', order = 'asc' } = req.query;
  const limit = Math.min(parseInt(req.query.limit) || 100000, 100000);
  const dir = order === 'desc' ? 'DESC' : 'ASC';
  let orderBy = 'COALESCE(jq.sort_order, 999999) ASC, jq.created_at ASC';
  if (sort_by === 'status') orderBy = \`CASE jq.status WHEN 'queued' THEN 0 WHEN 'processing' THEN 1 WHEN 'joined' THEN 2 WHEN 'pending' THEN 3 WHEN 'survey_submitted' THEN 4 ELSE 5 END \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'account') orderBy = \`p.email \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'community') orderBy = \`LOWER(COALESCE(jq.group_name, jq.group_slug)) \${dir}\`;
  else if (sort_by === 'error') orderBy = \`COALESCE(jq.error_msg, '') \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'finished_at') orderBy = \`jq.finished_at \${dir}, jq.created_at ASC\`;
  else if (sort_by === 'created_at') orderBy = \`jq.created_at \${dir}\`;
  const rows = db.prepare(\`
    SELECT jq.*, p.email as profile_email
    FROM join_queue jq
    LEFT JOIN profiles p ON jq.profile_id = p.id
    \${profile_id && profile_id !== 'all' ? 'WHERE jq.profile_id = ?' : ''}
    ORDER BY \${orderBy}
    LIMIT \${limit}
  \`).all(...(profile_id && profile_id !== 'all' ? [profile_id] : []));
  res.json(rows);
});`;

let s = fs.readFileSync(SERVER, 'utf8');
if (s.includes('showUnassigned')) {
  s = s.replace(newHandler, revertedHandler);
  fs.writeFileSync(SERVER, s);
  console.log('OK: reverted /api/queue');
} else {
  console.log('Already reverted or patch not applied.');
}
