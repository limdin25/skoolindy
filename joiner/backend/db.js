const Database = require('better-sqlite3');
const config = require('./config');

// EngageFlow DB — profiles, browser_locks (READ for profiles, READ/WRITE for locks)
const engageflowDb = new Database(config.ENGAGEFLOW_DB_PATH, {
    readonly: false  // Need write access for browser_locks only
});

// Joiner DB — join_queue, profile_discovery_info, join_logs
const joinerDb = new Database(config.JOINER_DB_PATH);
joinerDb.pragma('journal_mode = WAL');
joinerDb.pragma('foreign_keys = ON');

// Initialize joiner tables
joinerDb.exec(`
    CREATE TABLE IF NOT EXISTS join_queue (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        group_slug TEXT NOT NULL,
        group_id TEXT,
        group_name TEXT,
        group_members INTEGER DEFAULT 0,
        status TEXT DEFAULT 'queued',
        keyword TEXT,
        questions_json TEXT,
        error_msg TEXT,
        started_at TEXT,
        finished_at TEXT,
        joined_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        sort_order INTEGER DEFAULT 0,
        UNIQUE(profile_id, group_slug)
    );

    CREATE TABLE IF NOT EXISTS profile_discovery_info (
        profile_id TEXT PRIMARY KEY,
        full_name TEXT,
        email TEXT,
        phone TEXT,
        instagram TEXT,
        linkedin TEXT,
        website TEXT,
        bio TEXT,
        preferred_option TEXT,
        ai_generated INTEGER DEFAULT 0,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS join_logs (
        id TEXT PRIMARY KEY,
        profile_id TEXT,
        timestamp TEXT DEFAULT (datetime('now')),
        level TEXT DEFAULT 'info',
        event TEXT,
        group_slug TEXT,
        message TEXT,
        meta_json TEXT
    );

    CREATE TABLE IF NOT EXISTS community_pool (
        id TEXT PRIMARY KEY,
        group_slug TEXT NOT NULL UNIQUE,
        group_name TEXT,
        group_members INTEGER DEFAULT 0,
        source_url TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS profile_communities (
        id TEXT PRIMARY KEY,
        profile_id TEXT NOT NULL,
        slug TEXT NOT NULL,
        name TEXT,
        status TEXT NOT NULL DEFAULT 'joined',
        requested_at TEXT,
        joined_at TEXT,
        created_at TEXT DEFAULT (datetime('now')),
        UNIQUE(profile_id, slug)
    );

    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    );

    CREATE TABLE IF NOT EXISTS joiner_profile_state (
        profile_id TEXT PRIMARY KEY,
        password_plain TEXT,
        daily_count INTEGER DEFAULT 0,
        daily_cap INTEGER DEFAULT 50,
        is_running INTEGER DEFAULT 0,
        join_delay_seconds INTEGER DEFAULT 30,
        max_joins_per_run INTEGER DEFAULT 10,
        next_action_at TEXT,
        last_action_at TEXT,
        last_action_type TEXT,
        last_login_at TEXT,
        auth_error TEXT
    );
`);

// Migrate data from old DB if it exists and joiner tables are empty
const path = require('path');
const fs = require('fs');
const oldDbPath = path.resolve(__dirname, 'community-join-manager.db');
if (fs.existsSync(oldDbPath)) {
    const queueCount = joinerDb.prepare('SELECT COUNT(*) as c FROM join_queue').get().c;
    if (queueCount === 0) {
        try {
            const oldDb = new Database(oldDbPath, { readonly: true });
            const oldQueue = oldDb.prepare('SELECT * FROM join_queue').all();
            const oldDiscovery = oldDb.prepare('SELECT * FROM profile_discovery_info').all();
            const oldLogs = oldDb.prepare('SELECT * FROM logs').all();
            const oldPool = oldDb.prepare('SELECT * FROM community_pool').all();
            const oldComms = oldDb.prepare('SELECT * FROM profile_communities').all();

            if (oldQueue.length > 0) {
                const insertQueue = joinerDb.prepare(`INSERT OR IGNORE INTO join_queue
                    (id, profile_id, group_slug, group_id, group_name, group_members, status, keyword, questions_json, error_msg, started_at, finished_at, joined_at, created_at, sort_order)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
                for (const r of oldQueue) {
                    insertQueue.run(r.id, r.profile_id, r.group_slug, r.group_id, r.group_name, r.group_members || 0, r.status, r.keyword, r.questions_json, r.error_msg, r.started_at, r.finished_at, r.joined_at, r.created_at, r.sort_order || 0);
                }
                console.log(`[db] Migrated ${oldQueue.length} queue items from old DB`);
            }

            if (oldDiscovery.length > 0) {
                const insertDisc = joinerDb.prepare(`INSERT OR IGNORE INTO profile_discovery_info
                    (profile_id, full_name, email, phone, instagram, linkedin, website, bio, preferred_option, ai_generated, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`);
                for (const r of oldDiscovery) {
                    insertDisc.run(r.profile_id, r.full_name, r.email, r.phone, r.instagram, r.linkedin, r.website, r.bio, r.preferred_option, r.ai_generated || 0, r.updated_at);
                }
                console.log(`[db] Migrated ${oldDiscovery.length} discovery records from old DB`);
            }

            if (oldLogs.length > 0) {
                const insertLog = joinerDb.prepare(`INSERT OR IGNORE INTO join_logs
                    (id, profile_id, timestamp, level, event, group_slug, message, meta_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)`);
                for (const r of oldLogs) {
                    insertLog.run(r.id, r.profile_id, r.timestamp, r.level, r.event, r.group_slug, r.message, r.meta_json);
                }
                console.log(`[db] Migrated ${oldLogs.length} log entries from old DB`);
            }

            if (oldPool.length > 0) {
                const insertPool = joinerDb.prepare(`INSERT OR IGNORE INTO community_pool
                    (id, group_slug, group_name, group_members, source_url, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)`);
                for (const r of oldPool) {
                    insertPool.run(r.id, r.group_slug, r.group_name, r.group_members || 0, r.source_url, r.created_at);
                }
                console.log(`[db] Migrated ${oldPool.length} community pool entries from old DB`);
            }

            if (oldComms.length > 0) {
                const insertComm = joinerDb.prepare(`INSERT OR IGNORE INTO profile_communities
                    (id, profile_id, slug, name, status, requested_at, joined_at, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)`);
                for (const r of oldComms) {
                    insertComm.run(r.id, r.profile_id, r.slug, r.name, r.status, r.requested_at, r.joined_at, r.created_at);
                }
                console.log(`[db] Migrated ${oldComms.length} community records from old DB`);
            }

            oldDb.close();
            console.log('[db] Migration from old DB complete');
        } catch (err) {
            console.warn('[db] Migration from old DB failed (non-fatal):', err.message);
        }
    }
}

module.exports = { engageflowDb, joinerDb };
