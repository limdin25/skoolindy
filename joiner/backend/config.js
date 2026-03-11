const path = require('path');

module.exports = {
    // EngageFlow DB — READ ONLY for profiles + READ/WRITE for browser_locks
    ENGAGEFLOW_DB_PATH: path.resolve(__dirname, '../../backend/engageflow.db'),

    // Joiner's own DB — join_queue, discovery, logs
    JOINER_DB_PATH: path.resolve(__dirname, 'joiner.db'),

    // Shared browser profiles
    BROWSER_PROFILES_DIR: path.resolve(__dirname, '../../backend/skool_accounts'),

    // EngageFlow API for webhook
    ENGAGEFLOW_API: 'http://127.0.0.1:3103',
};
