const express = require('express');
const cors = require('cors');
const { v4: uuidv4 } = require('uuid');
const { parse } = require('csv-parse/sync');
const { engageflowDb, joinerDb } = require('./db');
const config = require('./config');
const { acquireLock, releaseLock } = require('./browserLock');
const { loginAndStoreCookies, validateCookies } = require('./skoolLogin');
const { getGroups } = require('./skoolApi');
const cron = require('node-cron');
const { joinCommunity, checkCommunityMembershipStatus, fetchCommunitiesFromSkoolSettings, cancelRequestOnSkool, cancelJoinViaFetch, leaveViaFetch } = require('./joinCommunity');

const app = express();
app.use(cors());
app.use(express.json({ limit: '10mb' }));
const PORT = process.env.PORT || 3100;

// ==================== HELPERS ====================
function getSetting(key) {
  const row = joinerDb.prepare('SELECT value FROM settings WHERE key = ?').get(key);
  return row ? row.value : null;
}

function writeLog(profileId, level, event, groupSlug, message, meta) {
  const now = new Date().toISOString();
  try {
    joinerDb.prepare('INSERT INTO join_logs (id, profile_id, timestamp, level, event, group_slug, message, meta_json) VALUES (?,?,?,?,?,?,?,?)')
      .run(uuidv4(), profileId, now, level || 'info', event || 'event', groupSlug || null, message || '', meta ? JSON.stringify(meta) : null);
  } catch (err) {
    if (/locked|SQLITE_BUSY|SQLITE_LOCKED/i.test(err.message)) {
      console.warn('[writeLog] Skipped due to sqlite lock:', profileId, event);
      return;
    }
    throw err;
  }
}

// Build profile email lookup from EngageFlow DB
function getProfileEmailMap() {
  const rows = engageflowDb.prepare('SELECT id, email, name FROM profiles').all();
  const map = {};
  for (const r of rows) map[r.id] = r.email || r.name || r.id;
  return map;
}

// Get full profile (EngageFlow base + joiner state)
function getFullProfile(profileId) {
  const base = engageflowDb.prepare('SELECT * FROM profiles WHERE id = ?').get(profileId);
  if (!base) return null;
  const state = joinerDb.prepare('SELECT * FROM joiner_profile_state WHERE profile_id = ?').get(profileId) || {};
  return {
    ...base,
    // Joiner-specific fields
    daily_count: state.daily_count || 0,
    daily_cap: state.daily_cap || 50,
    is_running: state.is_running || 0,
    join_delay_seconds: state.join_delay_seconds || 30,
    max_joins_per_run: state.max_joins_per_run || 10,
    next_action_at: state.next_action_at || null,
    last_action_at: state.last_action_at || null,
    last_action_type: state.last_action_type || null,
    last_login_at: state.last_login_at || null,
    auth_error: state.auth_error || null,
    password_plain: state.password_plain || null,
    // Derived
    auth_status: base.cookie_json ? 'connected' : 'disconnected',
  };
}

// Ensure joiner_profile_state row exists
function ensureProfileState(profileId) {
  joinerDb.prepare('INSERT OR IGNORE INTO joiner_profile_state (profile_id) VALUES (?)').run(profileId);
}

// Update joiner profile state
function updateProfileState(profileId, fields) {
  ensureProfileState(profileId);
  const clauses = [];
  const params = [];
  for (const [key, val] of Object.entries(fields)) {
    clauses.push(`${key} = ?`);
    params.push(val);
  }
  if (clauses.length === 0) return;
  params.push(profileId);
  joinerDb.prepare(`UPDATE joiner_profile_state SET ${clauses.join(', ')} WHERE profile_id = ?`).run(...params);
}

// Active runners map (profileId -> { running: bool })
const activeRunners = new Map();
let lastFetchResults = {};

// ==================== PROFILES (read from EngageFlow) ====================
app.get('/api/profiles', (req, res) => {
  const profiles = engageflowDb.prepare(`
    SELECT id, name, email, proxy, status, cookie_json, avatar
    FROM profiles ORDER BY name
  `).all();

  // Enrich with joiner-specific state + queue stats
  const result = profiles.map(p => {
    const state = joinerDb.prepare('SELECT * FROM joiner_profile_state WHERE profile_id = ?').get(p.id) || {};
    const queueTotal = joinerDb.prepare('SELECT COUNT(*) as c FROM join_queue WHERE profile_id = ?').get(p.id)?.c || 0;
    const joinedCount = joinerDb.prepare("SELECT COUNT(*) as c FROM join_queue WHERE profile_id = ? AND status = 'joined'").get(p.id)?.c || 0;
    return {
      ...p,
      auth_status: p.cookie_json ? 'connected' : 'disconnected',
      auth_error: state.auth_error || null,
      daily_count: state.daily_count || 0,
      daily_cap: state.daily_cap || 50,
      is_running: state.is_running || 0,
      join_delay_seconds: state.join_delay_seconds || 30,
      max_joins_per_run: state.max_joins_per_run || 10,
      next_action_at: state.next_action_at || null,
      last_action_at: state.last_action_at || null,
      last_action_type: state.last_action_type || null,
      last_login_at: state.last_login_at || null,
      password_plain: state.password_plain || null,
      queue_total: queueTotal,
      joined_count: joinedCount,
      auth_method: p.cookie_json ? 'cookies' : 'none',
      created_at: null, // Not available in EngageFlow profiles
    };
  });
  res.json(result);
});

// Store password for auto-login (joiner needs plaintext for Playwright)
app.post('/api/profiles/:id/store-password', (req, res) => {
  const { password } = req.body;
  if (!password) return res.status(400).json({ error: 'Password required' });
  const profile = engageflowDb.prepare('SELECT id FROM profiles WHERE id = ?').get(req.params.id);
  if (!profile) return res.status(404).json({ error: 'Profile not found in EngageFlow' });
  updateProfileState(req.params.id, { password_plain: password });
  res.json({ success: true });
});

// NOTE: POST /api/profiles and DELETE /api/profiles/:id are REMOVED
// Profiles are created and deleted in EngageFlow only

// ==================== AUTH ====================
app.get('/api/profiles/:id/skool-auth', async (req, res) => {
  const profile = engageflowDb.prepare('SELECT * FROM profiles WHERE id = ?').get(req.params.id);
  if (!profile) return res.status(404).json({ error: 'Not found' });
  if (!profile.cookie_json) return res.json({ valid: false, error: 'No cookies' });

  const result = await validateCookies(profile.cookie_json);
  const now = new Date().toISOString();
  if (result.valid) {
    updateProfileState(req.params.id, { last_action_at: now, last_action_type: 'test_auth', auth_error: null });
  } else {
    updateProfileState(req.params.id, { last_action_at: now, last_action_type: 'test_auth_failed', auth_error: result.error });
  }
  writeLog(req.params.id, result.valid ? 'info' : 'error', 'test_auth', null, result.valid ? 'Auth valid' : `Auth failed: ${result.error}`);
  res.json(result);
});

app.post('/api/profiles/:id/paste-cookies', async (req, res) => {
  const profile = engageflowDb.prepare('SELECT * FROM profiles WHERE id = ?').get(req.params.id);
  if (!profile) return res.status(404).json({ error: 'Not found' });

  const { cookies } = req.body;
  if (!cookies) return res.status(400).json({ error: 'No cookies provided' });

  let cookieJson;
  try {
    if (typeof cookies === 'string' && cookies.trim().startsWith('[')) {
      cookieJson = cookies.trim();
      JSON.parse(cookieJson);
    } else if (Array.isArray(cookies)) {
      cookieJson = JSON.stringify(cookies);
    } else {
      const pairs = cookies.split(';').map(p => p.trim()).filter(Boolean);
      const cookieArr = pairs.map(p => {
        const eq = p.indexOf('=');
        return { name: p.substring(0, eq), value: p.substring(eq + 1), domain: '.skool.com', path: '/' };
      });
      cookieJson = JSON.stringify(cookieArr);
    }
  } catch (e) {
    return res.status(400).json({ error: 'Invalid cookie format' });
  }

  const result = await validateCookies(cookieJson);
  const now = new Date().toISOString();

  if (result.valid) {
    // Write cookies to EngageFlow's shared profiles table
    engageflowDb.prepare('UPDATE profiles SET cookie_json = ? WHERE id = ?').run(cookieJson, req.params.id);
    updateProfileState(req.params.id, { last_login_at: now, last_action_at: now, auth_error: null });
    writeLog(req.params.id, 'info', 'cookie_paste', null, 'Cookie paste successful — authenticated');
    res.json({ success: true, message: 'Cookies validated and stored' });
  } else {
    writeLog(req.params.id, 'error', 'cookie_paste_failed', null, `Cookie validation failed: ${result.error}`);
    res.status(401).json({ error: `Cookie validation failed: ${result.error}` });
  }
});

app.post('/api/profiles/:id/connect', async (req, res) => {
  const profile = engageflowDb.prepare('SELECT * FROM profiles WHERE id = ?').get(req.params.id);
  if (!profile) return res.status(404).json({ error: 'Not found' });

  updateProfileState(req.params.id, { auth_error: null });
  res.json({ success: true, message: 'Connecting...' });

  const state = joinerDb.prepare('SELECT password_plain FROM joiner_profile_state WHERE profile_id = ?').get(req.params.id);
  const email = req.body.email || profile.email;
  const password = req.body.password || (state && state.password_plain);
  if (!password) {
    updateProfileState(req.params.id, { auth_error: 'No plaintext password available — use store-password endpoint or paste cookies' });
    return;
  }

  connectProfile(req.params.id, email, password, profile.proxy).catch(console.error);
});

async function connectProfile(profileId, email, password, proxy) {
  writeLog(profileId, 'info', 'login_attempt', null, `Login attempt for ${email}`);

  let result;
  try {
    acquireLock(profileId, 'joiner');
    result = await loginAndStoreCookies(profileId, email, password, proxy);
  } finally {
    releaseLock(profileId);
  }

  const now = new Date().toISOString();

  if (result.success) {
    // Write cookies to EngageFlow's shared profiles table
    engageflowDb.prepare('UPDATE profiles SET cookie_json = ? WHERE id = ?').run(result.cookieJson, profileId);
    updateProfileState(profileId, { last_login_at: now, last_action_at: now, auth_error: null, password_plain: password });
    writeLog(profileId, 'info', 'login_success', null, 'Login successful — cookies stored');
  } else {
    updateProfileState(profileId, { auth_error: result.message, last_action_at: now });
    writeLog(profileId, 'error', 'login_failed', null, `Login failed: ${result.message}`);
  }
}

// ==================== PER-ACCOUNT SETTINGS ====================
app.patch('/api/profiles/:id/settings', (req, res) => {
  const { join_delay_seconds, max_joins_per_run, daily_cap } = req.body;
  const profile = engageflowDb.prepare('SELECT id FROM profiles WHERE id = ?').get(req.params.id);
  if (!profile) return res.status(404).json({ error: 'Not found' });

  const updates = {};
  if (join_delay_seconds !== undefined) updates.join_delay_seconds = join_delay_seconds;
  if (max_joins_per_run !== undefined) updates.max_joins_per_run = max_joins_per_run;
  if (daily_cap !== undefined) updates.daily_cap = daily_cap;
  if (Object.keys(updates).length > 0) updateProfileState(req.params.id, updates);

  res.json({ success: true });
});

// ==================== QUEUE STATS ====================
app.get('/api/profiles/:id/queue-stats', (req, res) => {
  const stats = joinerDb.prepare(`
    SELECT
      SUM(CASE WHEN status='queued' THEN 1 ELSE 0 END) as queued,
      SUM(CASE WHEN status='processing' THEN 1 ELSE 0 END) as processing,
      SUM(CASE WHEN status='joined' THEN 1 ELSE 0 END) as joined,
      SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
      SUM(CASE WHEN status='survey_submitted' THEN 1 ELSE 0 END) as survey_submitted,
      SUM(CASE WHEN status='skipped_paid' THEN 1 ELSE 0 END) as skipped_paid,
      SUM(CASE WHEN status IN ('failed','error') THEN 1 ELSE 0 END) as failed
    FROM join_queue WHERE profile_id = ?
  `).get(req.params.id);
  res.json(stats || { queued:0, processing:0, joined:0, pending:0, survey_submitted:0, skipped_paid:0, failed:0 });
});

// ==================== JOIN QUEUE ====================
app.get('/api/queue', (req, res) => {
  const { profile_id, sort_by = 'created_at', order = 'asc' } = req.query;
  const limit = Math.min(parseInt(req.query.limit) || 100000, 100000);
  const dir = order === 'desc' ? 'DESC' : 'ASC';
  let orderBy = 'COALESCE(jq.sort_order, 999999) ASC, jq.created_at ASC';
  if (sort_by === 'status') orderBy = `CASE jq.status WHEN 'queued' THEN 0 WHEN 'processing' THEN 1 WHEN 'joined' THEN 2 WHEN 'pending' THEN 3 WHEN 'survey_submitted' THEN 4 ELSE 5 END ${dir}, jq.created_at ASC`;
  else if (sort_by === 'account') orderBy = `jq.profile_id ${dir}, jq.created_at ASC`;
  else if (sort_by === 'community') orderBy = `LOWER(COALESCE(jq.group_name, jq.group_slug)) ${dir}`;
  else if (sort_by === 'error') orderBy = `COALESCE(jq.error_msg, '') ${dir}, jq.created_at ASC`;
  else if (sort_by === 'finished_at') orderBy = `jq.finished_at ${dir}, jq.created_at ASC`;
  else if (sort_by === 'created_at') orderBy = `jq.created_at ${dir}`;

  let rows;
  if (profile_id === '__UNASSIGNED__') {
    rows = joinerDb.prepare(`SELECT jq.* FROM join_queue jq WHERE jq.profile_id IS NULL ORDER BY ${orderBy} LIMIT ${limit}`).all();
  } else if (profile_id && profile_id !== 'all') {
    rows = joinerDb.prepare(`SELECT jq.* FROM join_queue jq WHERE jq.profile_id = ? ORDER BY ${orderBy} LIMIT ${limit}`).all(profile_id);
  } else {
    rows = joinerDb.prepare(`SELECT jq.* FROM join_queue jq ORDER BY ${orderBy} LIMIT ${limit}`).all();
  }

  // Enrich with profile_email from EngageFlow DB
  const emailMap = getProfileEmailMap();
  res.json(rows.map(r => ({ ...r, profile_email: emailMap[r.profile_id] || null })));
});

app.post('/api/queue', (req, res) => {
  const { profile_id, group_slug, group_name, group_id, group_members, keyword } = req.body;
  if (!profile_id || !group_slug) return res.status(400).json({ error: 'profile_id and group_slug required' });

  const id = uuidv4();
  const nextOrder = joinerDb.prepare('SELECT COALESCE(MAX(sort_order), 0) + 1 AS n FROM join_queue WHERE profile_id = ?').get(profile_id).n;
  try {
    joinerDb.prepare(`INSERT INTO join_queue (id, profile_id, group_slug, group_name, group_id, group_members, keyword, sort_order)
      VALUES (?,?,?,?,?,?,?,?)`).run(id, profile_id, group_slug, group_name || group_slug, group_id || null, group_members || 0, keyword || null, nextOrder);
    res.json({ success: true, id });
  } catch (err) {
    if (err.message.includes('UNIQUE')) return res.status(409).json({ error: 'Already in queue' });
    res.status(500).json({ error: err.message });
  }
});

app.post('/api/queue/add-to-account', (req, res) => {
  const { target_profile_id, ids } = req.body;
  if (!target_profile_id || !Array.isArray(ids) || ids.length === 0) return res.status(400).json({ error: 'target_profile_id and ids required' });
  let added = 0, skipped = 0;
  const nextOrderStmt = joinerDb.prepare('SELECT COALESCE(MAX(sort_order), 0) AS n FROM join_queue WHERE profile_id = ?');
  let nextOrder = (nextOrderStmt.get(target_profile_id) || {}).n || 0;
  const moveStmt = joinerDb.prepare('UPDATE join_queue SET profile_id = ?, sort_order = ? WHERE id = ?');
  const insertStmt = joinerDb.prepare('INSERT INTO join_queue (id, profile_id, group_slug, group_name, group_members, status, sort_order) VALUES (?,?,?,?,?,?,?)');
  for (const queueId of ids) {
    const row = joinerDb.prepare('SELECT * FROM join_queue WHERE id = ?').get(queueId);
    if (!row) { skipped++; continue; }
    nextOrder++;
    if (!row.profile_id) {
      const exists = joinerDb.prepare('SELECT 1 FROM join_queue WHERE profile_id = ? AND group_slug = ?').get(target_profile_id, row.group_slug);
      if (exists) {
        joinerDb.prepare('DELETE FROM join_queue WHERE id = ?').run(queueId);
        skipped++;
        continue;
      }
      try {
        moveStmt.run(target_profile_id, nextOrder, queueId);
        added++;
      } catch (err) {
        if (err.message.includes('UNIQUE')) skipped++;
        else throw err;
      }
    } else {
      try {
        insertStmt.run(uuidv4(), target_profile_id, row.group_slug, row.group_name || row.group_slug, row.group_members || 0, 'queued', nextOrder);
        added++;
      } catch (err) {
        if (err.message.includes('UNIQUE')) skipped++;
        else throw err;
      }
    }
  }
  writeLog(target_profile_id, 'info', 'add_to_account', null, `Added ${added} communities${skipped ? `, ${skipped} already in queue` : ''}`);
  res.json({ success: true, added, skipped });
});

app.post('/api/queue/delete', (req, res) => {
  const { ids, all: deleteAll, profile_id } = req.body;
  let deleted = 0;
  if (deleteAll) {
    const where = profile_id && profile_id !== 'all' ? 'WHERE profile_id = ?' : '';
    const result = joinerDb.prepare(`DELETE FROM join_queue ${where}`).run(...(profile_id && profile_id !== 'all' ? [profile_id] : []));
    deleted = result.changes;
  } else if (Array.isArray(ids) && ids.length > 0) {
    const stmt = joinerDb.prepare('DELETE FROM join_queue WHERE id = ?');
    for (const id of ids) {
      const r = stmt.run(id);
      if (r.changes > 0) deleted++;
    }
  } else {
    return res.status(400).json({ error: 'ids array or all: true required' });
  }
  res.json({ success: true, deleted });
});

// ==================== QUEUE CSV IMPORT ====================
app.post('/api/queue/import-csv', (req, res) => {
  const { profile_id, csv_content } = req.body;
  if (!csv_content) return res.status(400).json({ error: 'csv_content required' });
  const effectiveProfileId = profile_id || null;

  try {
    const records = parse(csv_content, { columns: true, skip_empty_lines: true, trim: true, relax_column_count: true, relax_quotes: true });
    const col = (r, ...keys) => { for (const k of keys) { const v = r[k]; if (v !== undefined && v !== '') return String(v).trim(); } const lk = Object.keys(r).find(k => keys.some(x => x.toLowerCase() === k.toLowerCase())); return lk ? String(r[lk] || '').trim() : ''; };
    const results = [];
    const orderQuery = effectiveProfileId
      ? joinerDb.prepare('SELECT COALESCE(MAX(sort_order), 0) AS n FROM join_queue WHERE profile_id = ?').get(effectiveProfileId)
      : joinerDb.prepare('SELECT COALESCE(MAX(sort_order), 0) AS n FROM join_queue WHERE profile_id IS NULL').get();
    let nextOrder = (orderQuery || {}).n || 0;
    for (const row of records) {
      const url = col(row, 'URL', 'url');
      const name = col(row, 'Name', 'name');
      const members = col(row, 'Members', 'members');
      let slug = '';
      if (url) { const match = url.match(/skool\.com\/([^\/\?#]+)/); if (match) slug = match[1]; }
      if (!slug && name) slug = name.toLowerCase().replace(/[^a-z0-9]+/g, '-');
      if (!slug) { results.push({ name, success: false, error: 'No URL or Name' }); continue; }

      if (!effectiveProfileId) {
        const exists = joinerDb.prepare('SELECT 1 FROM join_queue WHERE profile_id IS NULL AND group_slug = ?').get(slug);
        if (exists) { results.push({ name, slug, success: false, error: 'Already in queue' }); continue; }
      }

      const id = uuidv4();
      nextOrder++;
      try {
        joinerDb.prepare(`INSERT INTO join_queue (id, profile_id, group_slug, group_name, group_members, status, sort_order)
          VALUES (?,?,?,?,?,?,?)`).run(id, effectiveProfileId, slug, name || slug, parseInt(members) || 0, 'queued', nextOrder);
        results.push({ name, slug, success: true });
      } catch (err) {
        if (err.message.includes('UNIQUE')) results.push({ name, slug, success: false, error: 'Already in queue' });
        else results.push({ name, slug, success: false, error: err.message });
      }
    }
    const added = results.filter(r => r.success).length;
    const skipped = results.filter(r => !r.success).length;
    writeLog(effectiveProfileId, 'info', 'csv_import', null, `CSV import: ${added} added, ${skipped} skipped`);
    res.json({ success: true, added, skipped, results });
  } catch (err) {
    res.status(400).json({ error: err.message });
  }
});

// ==================== PER-ACCOUNT RUNNER ====================
app.post('/api/profiles/:id/run', async (req, res) => {
  const profile = getFullProfile(req.params.id);
  if (!profile) return res.status(404).json({ error: 'Not found' });
  if (!profile.cookie_json) {
    writeLog(req.params.id, 'info', 'auto_connect', null, 'No cookies — attempting auto-connect before run');
  }
  if (profile.is_running) return res.json({ success: true, message: 'Already running' });

  updateProfileState(req.params.id, { is_running: 1 });
  activeRunners.set(req.params.id, { running: true });
  res.json({ success: true, message: 'Runner started' });

  runProfileWorker(req.params.id).catch(err => {
    console.error(`Runner error for ${req.params.id}:`, err);
    updateProfileState(req.params.id, { is_running: 0 });
  });
});

app.post('/api/profiles/:id/stop', (req, res) => {
  updateProfileState(req.params.id, { is_running: 0 });
  const runner = activeRunners.get(req.params.id);
  if (runner) runner.running = false;
  writeLog(req.params.id, 'info', 'runner_stopped', null, 'Runner stopped by user');
  res.json({ success: true, message: 'Runner stopped' });
});

async function notifyEngageFlow(profileId, community) {
  try {
    const resp = await fetch(`${config.ENGAGEFLOW_API}/communities/auto-register`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        profileId: profileId,
        slug: community.slug,
        name: community.name,
        url: `https://www.skool.com/${community.slug}`,
        auto_created: true
      })
    });
    if (resp.ok) {
      writeLog(profileId, 'info', 'webhook_engageflow', community.slug, `Auto-registered ${community.name} in EngageFlow`);
    }
  } catch (err) {
    console.error('Webhook to EngageFlow failed (non-fatal):', err.message);
  }
}

async function runProfileWorker(profileId) {
  let profile = getFullProfile(profileId);
  if (!profile) return;

  if (!profile.cookie_json) {
    writeLog(profileId, 'info', 'auto_connect_start', null, 'Auto-connecting before run...');
    if (!profile.password_plain) {
      writeLog(profileId, 'error', 'run_blocked', null, 'Cannot auto-connect: cookies missing and no password stored. Use Connect button or paste cookies.');
      updateProfileState(profileId, { is_running: 0 });
      activeRunners.delete(profileId);
      return;
    }
  }

  const delaySeconds = profile.join_delay_seconds || 30;
  const maxJoins = profile.max_joins_per_run || 10;
  const runner = activeRunners.get(profileId) || { running: true };
  activeRunners.set(profileId, runner);

  const queued = joinerDb.prepare(`SELECT * FROM join_queue WHERE profile_id = ? AND status = 'queued' ORDER BY COALESCE(sort_order, 999999) ASC, created_at ASC LIMIT ?`)
    .all(profileId, maxJoins);

  if (queued.length === 0) {
    writeLog(profileId, 'info', 'runner_done', null, 'No queued communities to join');
    updateProfileState(profileId, { is_running: 0, next_action_at: null });
    activeRunners.delete(profileId);
    return;
  }

  const discoveryInfo = joinerDb.prepare('SELECT * FROM profile_discovery_info WHERE profile_id = ?').get(profileId) || {};

  for (let i = 0; i < queued.length; i++) {
    const item = queued[i];

    if (!runner.running) {
      writeLog(profileId, 'info', 'runner_stopped_mid', item.group_slug, 'Runner stopped mid-run');
      break;
    }

    const now = new Date().toISOString();

    joinerDb.prepare('UPDATE join_queue SET status = ?, started_at = ? WHERE id = ?').run('processing', now, item.id);
    updateProfileState(profileId, { last_action_at: now, last_action_type: 'join_start' });
    writeLog(profileId, 'info', 'join_start', item.group_slug, `Joining ${item.group_name || item.group_slug}...`);

    try {
      // Acquire browser lock before Playwright
      acquireLock(profileId, 'joiner');

      // Refresh profile for latest cookies
      profile = getFullProfile(profileId);
      const result = await joinCommunity(profileId, item.group_slug, discoveryInfo);
      const finishedAt = new Date().toISOString();

      const finalStatus = result.status || (result.success ? 'joined' : 'failed');
      joinerDb.prepare('UPDATE join_queue SET status = ?, finished_at = ?, joined_at = ?, error_msg = ? WHERE id = ?')
        .run(finalStatus, finishedAt, (finalStatus === 'joined') ? finishedAt : null, result.message || null, item.id);

      writeLog(profileId, result.success ? 'info' : 'error', 'join_result', item.group_slug,
        `${item.group_name || item.group_slug}: ${finalStatus} — ${result.message}`);

      updateProfileState(profileId, { last_action_at: finishedAt, last_action_type: `join_${finalStatus}` });

      // Webhook to EngageFlow on successful join
      if (finalStatus === 'joined') {
        notifyEngageFlow(profileId, { slug: item.group_slug, name: item.group_name || item.group_slug }).catch(() => {});
      }

    } catch (err) {
      const finishedAt = new Date().toISOString();
      joinerDb.prepare('UPDATE join_queue SET status = ?, finished_at = ?, error_msg = ? WHERE id = ?')
        .run('failed', finishedAt, err.message, item.id);
      writeLog(profileId, 'error', 'join_error', item.group_slug, `Error: ${err.message}`);
    } finally {
      releaseLock(profileId);
    }

    // Sleep between joins
    if (i < queued.length - 1 && runner.running) {
      const jitter = Math.floor(Math.random() * 10);
      const waitMs = (delaySeconds + jitter) * 1000;
      const nextAt = new Date(Date.now() + waitMs).toISOString();
      updateProfileState(profileId, { next_action_at: nextAt });
      await new Promise(r => setTimeout(r, waitMs));
    }
  }

  // Done
  updateProfileState(profileId, { is_running: 0, next_action_at: null });
  activeRunners.delete(profileId);
  writeLog(profileId, 'info', 'runner_complete', null, 'Runner finished all queued items');
}


let fetchProfileProgress = { running: false, profileId: null, status: '', current: 0, total: 0, community: '', resolved: 0, joinedCount: 0, pendingCount: 0 };

async function fetchCommunitiesForProfile(profileId) {
  const profile = engageflowDb.prepare('SELECT * FROM profiles WHERE id = ?').get(profileId);
  if (!profile || !profile.cookie_json) return { error: 'No cookie', added: 0, resolved: 0 };
  if (fetchProfileProgress.running) return { error: 'Fetch already in progress', resolved: 0 };
  fetchProfileProgress = { running: true, profileId, status: 'Fetching from Skool API...', current: 0, total: 0, community: '', resolved: 0, joinedCount: 0, pendingCount: 0 };
  try {
    const { getGroups } = require('./skoolApi');
    const data = await getGroups(profile.cookie_json);
    const groups = data.groups || [];
    fetchProfileProgress.status = `Processing ${groups.length} communities`;
    fetchProfileProgress.total = groups.length;

    const joinedList = [];
    const pendingList = [];

    for (let i = 0; i < groups.length; i++) {
      const g = groups[i];
      const slug = g.name || '';
      const displayName = g.metadata?.display_name || slug;
      let memberObj = null;
      try { memberObj = g.metadata?.member ? JSON.parse(g.metadata.member) : null; } catch (_) {}
      const role = memberObj?.role || 'member';
      const requestedAt = memberObj?.metadata?.requested_at ? new Date(memberObj.metadata.requested_at / 1e6).toISOString() : null;
      const approvedAt = memberObj?.approved_at || null;

      fetchProfileProgress.current = i + 1;
      fetchProfileProgress.community = displayName;

      if (role === 'pending') {
        pendingList.push({ slug, name: displayName, requestedAt });
      } else {
        joinedList.push({ slug, name: displayName, joinedAt: approvedAt });
      }
    }

    const joinedSet = new Set(joinedList.map(c => c.slug.toLowerCase()));
    const pendingSet = new Set(pendingList.map(c => c.slug.toLowerCase()));
    fetchProfileProgress.joinedCount = joinedSet.size;
    fetchProfileProgress.pendingCount = pendingSet.size;
    lastFetchResults[profileId] = { joined: joinedList, pending: pendingList, fetchedAt: new Date().toISOString() };

    const upsertComm = joinerDb.prepare("INSERT INTO profile_communities (id, profile_id, slug, name, status, requested_at, joined_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now')) ON CONFLICT(profile_id, slug) DO UPDATE SET name=excluded.name, status=excluded.status, requested_at=excluded.requested_at, joined_at=excluded.joined_at");
    for (const c of joinedList) {
      if (!c.slug) continue;
      upsertComm.run(uuidv4(), profileId, c.slug, c.name, 'joined', null, c.joinedAt || new Date().toISOString());
    }
    for (const c of pendingList) {
      if (!c.slug) continue;
      upsertComm.run(uuidv4(), profileId, c.slug, c.name, 'pending', c.requestedAt, null);
    }
    let resolved = 0;
    const queueItems = joinerDb.prepare("SELECT id, group_slug FROM join_queue WHERE profile_id = ?").all(profileId);
    for (const item of queueItems) {
      const slug = (item.group_slug || '').toLowerCase();
      if (!slug) continue;
      if (joinedSet.has(slug)) {
        joinerDb.prepare("UPDATE join_queue SET status = 'joined', joined_at = datetime('now'), error_msg = NULL WHERE id = ?").run(item.id);
        writeLog(profileId, 'info', 'join_resolved', item.group_slug, item.group_slug + ': confirmed joined');
        resolved++;
      } else if (pendingSet.has(slug)) {
        joinerDb.prepare("DELETE FROM join_queue WHERE id = ?").run(item.id);
      }
    }
    fetchProfileProgress.resolved = resolved;
    fetchProfileProgress.running = false;
    fetchProfileProgress.status = 'Done';
    return { resolved, joined: joinedList.length, pending: pendingList.length };
  } catch (e) {
    console.error('[fetchCommunitiesForProfile] error:', e.message);
    fetchProfileProgress.running = false;
    fetchProfileProgress.status = 'Error: ' + e.message;
    return { error: e.message, resolved: 0 };
  }
}

let fetchAllProgress = { running: false, total: 0, done: 0, current: null, resolved: 0 };

app.post('/api/communities/fetch/:profileId', async (req, res) => {
  res.json({ started: true });
  (async () => {
    try {
      await fetchCommunitiesForProfile(req.params.profileId);
    } catch (e) { console.error(e); fetchProfileProgress.running = false; }
  })();
});

app.get('/api/communities/fetch/:profileId/status', (req, res) => {
  const { profileId } = req.params;
  const p = fetchProfileProgress;
  if (p.profileId !== profileId) {
    return res.json({ running: false, status: '', current: 0, total: 0, community: '' });
  }
  res.json({ running: p.running, status: p.status, current: p.current, total: p.total, community: p.community, resolved: p.resolved, joinedCount: p.joinedCount, pendingCount: p.pendingCount });
});

app.get('/api/communities/fetch/:profileId/results', (req, res) => {
  const { profileId } = req.params;
  const mem = lastFetchResults[profileId];
  const rows = joinerDb.prepare("SELECT slug, name, status, requested_at, joined_at FROM profile_communities WHERE profile_id = ? ORDER BY status, name").all(profileId);
  if (rows.length > 0) {
    const joined = rows.filter(r => r.status === 'joined').map(r => ({ slug: r.slug, name: r.name || r.slug, joinedAt: r.joined_at }));
    const pending = rows.filter(r => r.status === 'pending').map(r => ({ slug: r.slug, name: r.name || r.slug, requestedAt: r.requested_at }));
    const canceled = rows.filter(r => r.status === 'canceled').map(r => ({ slug: r.slug, name: r.name || r.slug, requestedAt: r.requested_at }));
    const free_trial = rows.filter(r => r.status === 'free_trial').map(r => ({ slug: r.slug, name: r.name || r.slug, requestedAt: r.requested_at }));
    return res.json({ joined, pending, canceled, free_trial, fetchedAt: mem?.fetchedAt || new Date().toISOString() });
  }
  res.json(mem || null);
});

app.delete('/api/communities/remove/:profileId/:slug', (req, res) => {
  const { profileId, slug } = req.params;
  if (!profileId || !slug) return res.status(400).json({ error: 'profileId and slug required' });
  try {
    const r = joinerDb.prepare('DELETE FROM profile_communities WHERE profile_id = ? AND lower(slug) = lower(?)').run(profileId, decodeURIComponent(slug));
    res.json({ success: true, deleted: r.changes > 0 });
  } catch (e) {
    res.status(500).json({ success: false, error: e.message });
  }
});

app.post('/api/communities/leave', async (req, res) => {
  const { profileId, slug } = req.body;
  if (!profileId || !slug) return res.status(400).json({ error: 'profileId and slug required' });
  try {
    acquireLock(profileId, 'joiner');
    const result = await leaveViaFetch(profileId, slug);
    releaseLock(profileId);
    if (!result.ok) return res.status(result.status || 500).json({ ok: false, error: result.error || `Skool API ${result.status}` });

    try { joinerDb.prepare("DELETE FROM profile_communities WHERE profile_id = ? AND lower(slug) = lower(?)").run(profileId, slug); } catch (_) {}
    const data = lastFetchResults[profileId];
    if (data && data.joined) { data.joined = data.joined.filter(c => (c.slug || c).toLowerCase() !== slug.toLowerCase()); }
    res.json({ ok: true });
  } catch (e) {
    releaseLock(profileId);
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.post('/api/communities/cancel-request', async (req, res) => {
  const { profileId, communitySlug, slug: slugAlt } = req.body;
  const slugInput = communitySlug || slugAlt;
  if (!profileId || !slugInput) return res.status(400).json({ error: 'profileId and slug required' });
  try {
    acquireLock(profileId, 'joiner');
    const result = await cancelJoinViaFetch(profileId, slugInput);
    releaseLock(profileId);
    if (!result.ok) return res.status(result.status || 500).json({ ok: false, error: result.error || `Skool API ${result.status}` });

    try { joinerDb.prepare("DELETE FROM profile_communities WHERE profile_id = ? AND lower(slug) = lower(?)").run(profileId, slugInput); } catch (_) {}
    const data = lastFetchResults[profileId];
    if (data && data.pending) {
      data.pending = data.pending.filter(p => ((p.slug || p) + '').toLowerCase() !== slugInput.toLowerCase());
    }
    res.json({ ok: true });
  } catch (e) {
    console.error('[cancel-request] error:', e.message);
    releaseLock(profileId);
    res.status(500).json({ ok: false, error: e.message });
  }
});

app.post('/api/communities/fetch-all', async (req, res) => {
  if (fetchAllProgress.running) return res.status(429).json({ error: 'Already in progress' });
  const profiles = engageflowDb.prepare('SELECT id, email FROM profiles WHERE cookie_json IS NOT NULL AND cookie_json != ""').all();
  fetchAllProgress = { running: true, total: profiles.length, done: 0, current: null, resolved: 0 };
  res.json({ started: true, total: profiles.length });
  (async () => {
    for (const p of profiles) {
      fetchAllProgress.current = p.email;
      const r = await fetchCommunitiesForProfile(p.id);
      if (!r.error) fetchAllProgress.resolved += r.resolved || 0;
      fetchAllProgress.done++;
    }
    fetchAllProgress.running = false;
    fetchAllProgress.current = null;
  })().catch(e => { fetchAllProgress.running = false; console.error(e); });
});

app.get('/api/communities/fetch-all/status', (req, res) => {
  res.json({ ...fetchAllProgress });
});

// ==================== LOGS ====================
app.get('/api/logs', (req, res) => {
  const { profile_id, limit = 200, sort_by = 'timestamp', order = 'desc' } = req.query;
  // Note: join_logs table (not logs) — logs is in old DB
  let query = `SELECT l.* FROM join_logs l WHERE 1=1`;
  const params = [];
  if (profile_id && profile_id !== 'all') { query += ' AND l.profile_id = ?'; params.push(profile_id); }
  const dir = order === 'asc' ? 'ASC' : 'DESC';
  let orderBy = 'l.timestamp DESC';
  if (sort_by === 'event') orderBy = `l.event ${dir}, l.timestamp DESC`;
  else if (sort_by === 'community') orderBy = `l.group_slug ${dir}, l.timestamp DESC`;
  else if (sort_by === 'message') orderBy = `l.message ${dir}, l.timestamp DESC`;
  else if (sort_by === 'timestamp') orderBy = `l.timestamp ${dir}`;
  else if (sort_by === 'account') orderBy = `l.profile_id ${dir}, l.timestamp DESC`;
  query += ` ORDER BY ${orderBy} LIMIT ${parseInt(limit)}`;

  const rows = joinerDb.prepare(query).all(...params);
  // Enrich with profile_email
  const emailMap = getProfileEmailMap();
  res.json(rows.map(r => ({ ...r, profile_email: emailMap[r.profile_id] || null })));
});

app.get('/api/logs/export', (req, res) => {
  const { status = 'failed', format = 'csv' } = req.query;
  const rows = joinerDb.prepare(`
    SELECT jq.profile_id, jq.group_slug,
      'https://www.skool.com/' || jq.group_slug as skool_url,
      jq.error_msg as message, COALESCE(jq.finished_at, jq.created_at) as timestamp
    FROM join_queue jq
    WHERE jq.status = ?
    ORDER BY COALESCE(jq.finished_at, jq.created_at) DESC
  `).all(status);

  const emailMap = getProfileEmailMap();
  const enriched = rows.map(r => ({ ...r, profile_email: emailMap[r.profile_id] || null }));

  if (format === 'csv') {
    const header = ['profile_email', 'group_slug', 'skool_url', 'message', 'timestamp'];
    const esc = (v) => '"' + String(v || '').replace(/"/g, '""') + '"';
    const csv = [header.join(','), ...enriched.map(r => header.map(h => esc(r[h])).join(','))].join('\n');
    res.setHeader('Content-Type', 'text/csv');
    res.setHeader('Content-Disposition', 'attachment; filename="failed-' + Date.now() + '.csv"');
    return res.send(csv);
  }
  res.json(enriched);
});

// ==================== SETTINGS ====================
app.get('/api/settings', (req, res) => {
  const rows = joinerDb.prepare('SELECT * FROM settings').all();
  const settings = {};
  rows.forEach(r => settings[r.key] = r.value);
  res.json(settings);
});

app.post('/api/settings', (req, res) => {
  for (const [key, value] of Object.entries(req.body)) {
    joinerDb.prepare('INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)').run(key, String(value));
  }
  res.json({ success: true });
});

// ==================== DISCOVERY INFO ====================
const EUROPEAN_SURNAMES = ['Müller', 'Schmidt', 'Fischer', 'Weber', 'Meyer', 'Wagner', 'Becker', 'Schulz', 'Hoffmann', 'Koch', 'Schneider', 'Fuchs', 'Berger', 'Schmitt', 'Lange', 'Walter', 'Klein', 'Wolf', 'König', 'Neumann'];
const SURVEY_DOMAINS = ['AgentStack.io', 'FlowKernel.com', 'CodeAscent.io', 'BuildCatalyst.com', 'StackThrive.com', 'DevClarity.io', 'LaunchRelay.com', 'SystemSprint.com', 'CloudThesis.com', 'AIConstructor.io', 'BuilderScale.com', 'DataForgeLabs.com', 'LoopSignal.com', 'StackOperator.com', 'TechBlueprint.io', 'DevLeverage.com', 'ProductNexus.io', 'CodeVelocity.io', 'GrowthKernel.com', 'BuildAuthority.com', 'StackPilot.io', 'AIWorkflowLab.com', 'SystemVector.io', 'LaunchMechanic.com', 'DevMatrix.io', 'TechOperator.com', 'BuilderEngine.io', 'CodeCommand.io', 'ScaleCircuit.com', 'InfraSprint.io'];
const SURVEY_PHONES_UK = ['+44 7700 900214', '+44 7520 123456', '+44 7624 456789', '+44 7900 123321', '+44 7911 234567', '+44 7922 345678', '+44 7933 456123', '+44 7944 567234', '+44 7955 678345', '+44 7966 789456', '+44 7977 890567', '+44 7988 901678', '+44 7999 112233', '+44 7500 334455', '+44 7611 556677'];
const SURVEY_PHONES_US = ['+1 212 555 0107', '+1 213 555 0142', '+1 305 555 0176', '+1 312 555 0199', '+1 415 555 0124', '+1 516 555 0163', '+1 617 555 0188', '+1 646 555 0115', '+1 702 555 0137', '+1 713 555 0159', '+1 818 555 0103', '+1 202 555 0144', '+1 303 555 0171', '+1 404 555 0182', '+1 503 555 0129'];
const BIOS = ['Interested in growing and learning. Looking forward to connecting.', 'Always learning, always growing. Excited to be here.', 'Curious about new ideas and connecting with like-minded people.', 'Here to learn, share, and grow together.', 'Growing my skills and building connections.', 'Interested in the community. Looking forward to contributing.', 'Learning and growing. Happy to be part of this.', 'Passionate about growth and making connections.'];
function getDiscoveryDefaults(profile) {
  if (!profile) return {};
  const username = (profile.email || '').split('@')[0] || 'user';
  const namePart = username.replace(/[._]/g, ' ').replace(/\b\w/g, c => c.toUpperCase()).trim() || 'User';
  const surname = EUROPEAN_SURNAMES[Math.floor(Math.random() * EUROPEAN_SURNAMES.length)];
  const words = ['growth', 'marketing', 'digital', 'creative', 'startup', 'innovate', 'build', 'scale', 'launch'];
  const randWords = () => [words[Math.floor(Math.random() * words.length)], words[Math.floor(Math.random() * words.length)]].join('-');
  const randLetters = () => 'abcdefghijklmnopqrstuvwxyz'.split('').sort(() => Math.random() - 0.5).slice(0, 2 + Math.floor(Math.random() * 2)).join('');
  const instaBase = username.replace(/[^a-z0-9]/gi, '');
  const instaExtras = ['42', '99', '7k', 'xy', '23', '01', 'ab', '88', '12', 'xyz'];
  const phone = Math.random() < 0.5 ? SURVEY_PHONES_UK[Math.floor(Math.random() * SURVEY_PHONES_UK.length)] : SURVEY_PHONES_US[Math.floor(Math.random() * SURVEY_PHONES_US.length)];
  return {
    email: profile.email || '',
    full_name: `${namePart} ${surname}`,
    phone,
    instagram: `instagram.com/${instaBase}${instaExtras[Math.floor(Math.random() * instaExtras.length)]}`,
    linkedin: `linkedin.com/in/${username.replace(/[^a-z0-9]/gi, '-')}-${randLetters()}`,
    website: 'https://' + SURVEY_DOMAINS[Math.floor(Math.random() * SURVEY_DOMAINS.length)],
    bio: BIOS[Math.floor(Math.random() * BIOS.length)]
  };
}
app.get('/api/profiles/:id/discovery-info', (req, res) => {
  const info = joinerDb.prepare('SELECT * FROM profile_discovery_info WHERE profile_id = ?').get(req.params.id);
  if (info && (info.full_name || info.email || info.phone || info.instagram || info.linkedin || info.website || info.bio)) {
    return res.json({ full_name: info.full_name, email: info.email, phone: info.phone, instagram: info.instagram, linkedin: info.linkedin, website: info.website, bio: info.bio });
  }
  const profile = engageflowDb.prepare('SELECT * FROM profiles WHERE id = ?').get(req.params.id);
  const defaults = getDiscoveryDefaults(profile);
  res.json(info ? { ...defaults, ...info } : defaults);
});

app.put('/api/profiles/:id/discovery-info', (req, res) => {
  const { full_name, email, phone, instagram, linkedin, website, bio } = req.body;
  joinerDb.prepare(`INSERT OR REPLACE INTO profile_discovery_info (profile_id, full_name, email, phone, instagram, linkedin, website, bio, updated_at)
    VALUES (?,?,?,?,?,?,?,?,datetime('now'))`).run(req.params.id, full_name || '', email || '', phone || '', instagram || '', linkedin || '', website || '', bio || '');
  res.json({ success: true });
});

// ==================== START ====================
cron.schedule('0 8,20 * * *', async () => {
  const profiles = engageflowDb.prepare('SELECT id FROM profiles WHERE cookie_json IS NOT NULL AND cookie_json != ""').all();
  for (const p of profiles) { try { await fetchCommunitiesForProfile(p.id); } catch (e) { console.error(e.message); } }
});

app.listen(PORT, () => {
  console.log(`EngageFlow Joiner backend running on port ${PORT}`);
});
