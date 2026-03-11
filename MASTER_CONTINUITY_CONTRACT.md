# MASTER_CONTINUITY_CONTRACT.md

Project: EngageFlow  
Authority Level: Canonical  
Status: Active - Bug Present  
Scope: 24/7 Autonomous Automation System  

---

# 0. PURPOSE

This document is the single source of truth for EngageFlow. Any AI agent or engineer taking over this project MUST:

1. Read this entire document.
2. Inspect current repository state.
3. Inspect runtime state.
4. Validate system invariants.
5. Only then begin modifications.

No prior chat history is required if this document is followed.

This project is designed for deterministic 24/7 operation. Stability, restart safety, and state correctness override speed of development.

---

# 1. PRODUCT MISSION

EngageFlow is a deterministic, multi-profile, multi-community automation engine. It must:

- Run 24/7 without silent failure
- Enforce daily limits strictly
- Respect schedule windows
- Maintain correct counters
- Resume safely after restart
- Never drift from database truth
- Never enter infinite idle states due to stale memory

**Primary principle:** Database is source of truth. In-memory state must never contradict DB state.

---

# 2. STRATEGIC GOALS

1. **Deterministic Scheduler**
   - No hidden transitions
   - No stale in-memory counters
   - No race conditions

2. **24/7 Operational Stability**
   - Heartbeat endpoint always live
   - Scheduler tick always fresh
   - Idle state observable and diagnosable
   - Restart-safe behavior

3. **Debuggability**
   - `/health` endpoint
   - `/debug/scheduler` endpoint
   - Clear idle_reason states
   - Accurate counts matching DB

4. **Controlled Automation**
   - No action beyond daily limits
   - No schedule violation
   - No silent retry storms

---

# 3. HIGH-LEVEL ARCHITECTURE

**Backend:**
- FastAPI application
- PM2-managed uvicorn process (port 3103)
- SQLite database (engageflow.db)
- Automation engine loop in `automation/engine.py`

**Frontend:**
- React + Vite + TypeScript
- TailwindCSS
- Port 4002 (nginx reverse proxy on VPS)

**Core Loop:**
- `_scheduler_loop()` in `automation/engine.py`
- Loads runtime config from DB
- Resets daily counters
- Refreshes in-memory runtime state
- Executes round-robin profile passes
- Enters idle when limits reached

**Idle Modes:**
- `paused` - Master automation disabled
- `outside_schedule` - All profiles outside run window
- `limits_reached` - All communities hit daily limit
- `backoff` - Network backoff delay
- `connection_rest` - Profile connection rest period

**24/7 Contract:**
- `last_scheduler_tick_ts` must update continuously
- Heartbeat must stay under 60s
- Scheduler must wake on:
  - Daily reset (00:00:05 local time)
  - Settings change
  - Profile capacity restored
  - Restart

---

# 4. CURRENT KNOWN CRITICAL LOGIC AREA

## Scheduler Loop Ordering (CRITICAL BUG)

**Current bug location:** `automation/engine.py` lines 1184-1202

**Incorrect sequence (CURRENT CODE):**
```python
# 1. Load profiles from DB (has OLD counter values)
db_profiles, db_settings = await asyncio.to_thread(self._load_runtime_config_from_db)

# 2. Reset counters in DB (sets actionsToday=0)
await asyncio.to_thread(self._reset_daily_counters_if_needed)

# 3. Refresh in-memory profiles from db_profiles loaded in step 1 (STALE DATA!)
self._refresh_runtime_profiles_locked(db_profiles)
```

**Correct sequence (FIX NEEDED):**
```python
# 1. Reset counters in DB FIRST
await asyncio.to_thread(self._reset_daily_counters_if_needed)

# 2. Load profiles from DB (now has FRESH counter values)
db_profiles, db_settings = await asyncio.to_thread(self._load_runtime_config_from_db)

# 3. Refresh in-memory profiles with fresh data
self._refresh_runtime_profiles_locked(db_profiles)
```

**Impact of bug:**
- Daily reset at midnight clears DB counters correctly
- In-memory state still holds yesterday's counters
- Scheduler thinks limits reached when they're actually at 0
- System enters indefinite idle state until manual restart

**Any modification to scheduler logic MUST preserve correct order.**

---

# 5. 24/7 INVARIANTS

The following must ALWAYS be true:

- `heartbeat_age_seconds` < 60
- `last_scheduler_tick_age_seconds` < 15 (while running)
- `idle_reason` must reflect real DB state
- `communities_reached_limit` must match SQL truth
- `actionsToday` sum must never exceed `dailyLimit`
- Restart must not corrupt counters
- Daily reset must clear both DB and in-memory counters

---

# 6. DEBUG PROCEDURE (MANDATORY BEFORE ANY FIX)

Before modifying code:

```bash
# 1. Check recent commits
git log -3 --oneline

# 2. Check PM2 state
pm2 list
pm2 show engageflow-backend

# 3. Check health
curl -sS http://127.0.0.1:3103/health

# 4. Check scheduler state
curl -sS http://127.0.0.1:3103/debug/scheduler | jq

# 5. Check DB counters
cd backend
sqlite3 engageflow.db "
  SELECT status, COUNT(*) as count, 
         SUM(actionsToday) as total_actions, 
         SUM(dailyLimit) as total_limits
  FROM communities 
  GROUP BY status;
"

sqlite3 engageflow.db "
  SELECT id, name, dailyLimit, actionsToday 
  FROM communities 
  WHERE status='active' 
  ORDER BY actionsToday DESC 
  LIMIT 10;
"
```

**Verify:**
- DB counters vs in-memory counters
- `idle_reason` consistency
- `communities_reached_limit` matches SQL query

**No fix is valid without reproducing state mismatch.**

---

# 7. CURRENT STATE (2026-02-27 17:20 CET)

**Status:** Bug present, system idle 17+ hours

**Database state:**
- 37 active communities
- All have `actionsToday=0`, `matchesToday=0`
- Daily limits range 5-6
- Last reset: `2026-02-27` (confirmed in `skool_daily_counters_state.json`)

**Scheduler state:**
- `idle_mode: true`
- `idle_reason: "limits_reached"`
- `communities_reached_limit: 0` ← **CONTRADICTION**
- `profiles_active: 0`

**Root cause confirmed:** Race condition in line 1184-1202 (see section 4)

**Fix ready:** Reorder lines to reset counters before loading profiles

---

# 8. ARCHAEOLOGY PROTOCOL

When taking over:

1. Inspect commit history:
   ```bash
   git log --graph --decorate --oneline -20
   ```

2. Inspect worktrees:
   ```bash
   ls -la .claude/worktrees/
   ```

3. Confirm PM2 CWD matches expected repo path:
   ```bash
   pm2 show engageflow-backend | grep "exec cwd"
   ```

4. Confirm no stale worktree divergence

5. Confirm scheduler loop logic matches this contract

**Archaeology rule:** If behavior contradicts logs, assume ordering bug or stale memory before assuming infrastructure failure.

---

# 9. CHANGE MANAGEMENT RULES

Every change must include:

- Smallest possible diff
- One logical intent per commit
- Test plan
- Rollback command
- Success metric

**No migrations, data changes, or config changes without explicit approval.**

**For the current bug fix:**
- **Change:** Swap lines 1184-1186 with 1187-1192 in `engine.py`
- **Test:** Verify `/debug/scheduler` shows `idle_mode: false` after deploy
- **Rollback:** `git revert HEAD && pm2 restart engageflow-backend`
- **Success:** System begins outreach within 60s of restart

---

# 10. ACCEPTANCE TEST FOR ANY TAKEOVER

System passes takeover validation if:

- ✅ 24/7 heartbeat stable for 5+ minutes
- ✅ Scheduler tick updates during idle
- ❌ Limits reset correctly at daily boundary (BUG)
- ❌ Idle exits immediately when capacity restored (BUG)
- ❌ Debug endpoint reflects DB truth (BUG - shows limits_reached when 0/37 reached)

**Current status:** 3/5 tests pass. Bug blocks production safety.

---

# 11. DATABASE SCHEMA (CRITICAL TABLES)

**profiles:**
- `id` (PK), `name`, `username`, `password`, `email`
- `status` (ready/paused)
- `dailyUsage` (counter, resets daily)
- `cookie_json` (Skool session cookies)

**communities:**
- `id` (PK), `profileId` (FK), `name`, `url`
- `status` (active/paused/pending)
- `dailyLimit` (max actions per day)
- `actionsToday` (counter, resets daily)
- `matchesToday` (counter, resets daily)
- `lastScanned` (time string HH:MM:SS)

**automation_settings:**
- Key-value JSON config
- `masterEnabled`, `globalDailyCapPerAccount`, schedules, etc.

**queue_items:**
- Prefilled actions waiting execution
- Profile + community + post + keyword combo

---

# 12. DEPLOYMENT CHECKLIST

Before deploying any fix:

1. ✅ Code change tested locally
2. ✅ DB schema unchanged (or migration planned)
3. ✅ No breaking API changes
4. ✅ PM2 restart command ready
5. ✅ Rollback plan documented
6. ✅ Success metric defined
7. ✅ Monitor logs for 5 minutes post-deploy

**Standard deploy:**
```bash
cd /root/.openclaw/workspace/engageflow/backend
git pull  # or extract new zip
pm2 restart engageflow-backend
pm2 logs engageflow-backend --lines 50
curl http://127.0.0.1:3103/debug/scheduler | jq
```

---

# 13. EMERGENCY PROCEDURES

**If scheduler stops ticking:**
```bash
pm2 restart engageflow-backend
```

**If heartbeat exceeds 120s:**
```bash
pm2 logs engageflow-backend --err --lines 100
pm2 restart engageflow-backend
```

**If database corrupted:**
```bash
# Restore from backup (keep daily backups)
cp engageflow.db.backup engageflow.db
pm2 restart engageflow-backend
```

**If counters stuck:**
```bash
# Manual reset (ONLY in emergency)
sqlite3 engageflow.db "
  UPDATE communities SET actionsToday=0, matchesToday=0;
  UPDATE profiles SET dailyUsage=0;
"
pm2 restart engageflow-backend
```

---

# 14. MONITORING ENDPOINTS

**Health check:**
```bash
curl http://127.0.0.1:3103/health
# Returns: {"status":"ok","running":true,"heartbeat_age_seconds":X}
```

**Scheduler debug:**
```bash
curl http://127.0.0.1:3103/debug/scheduler
# Returns full scheduler state including idle_mode, counts, last_action
```

**Live logs:**
```bash
pm2 logs engageflow-backend --lines 0
```

---

END OF CONTRACT

**Last updated:** 2026-02-27 17:20 CET  
**Bug status:** Confirmed, fix ready, awaiting deployment  
**System uptime:** 17h idle (awaiting fix)
