const { engageflowDb } = require('./db');

function acquireLock(profileId, locker = 'joiner') {
    // Check if already locked by someone else
    const existing = engageflowDb
        .prepare('SELECT locked_by FROM browser_locks WHERE profile_id = ?')
        .get(profileId);

    if (existing && existing.locked_by !== locker) {
        throw new Error(`Browser locked by ${existing.locked_by}`);
    }

    // Acquire or update lock
    engageflowDb
        .prepare(`INSERT INTO browser_locks (profile_id, locked_by, locked_at)
                  VALUES (?, ?, datetime('now'))
                  ON CONFLICT(profile_id) DO UPDATE SET locked_by = ?, locked_at = datetime('now')`)
        .run(profileId, locker, locker);

    return true;
}

function releaseLock(profileId) {
    engageflowDb
        .prepare('DELETE FROM browser_locks WHERE profile_id = ?')
        .run(profileId);
}

function isLocked(profileId) {
    const lock = engageflowDb
        .prepare('SELECT locked_by, locked_at FROM browser_locks WHERE profile_id = ?')
        .get(profileId);
    return lock || null;
}

module.exports = { acquireLock, releaseLock, isLocked };
