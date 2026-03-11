# TRANSFER.md - Bidirectional Sync Contract

## PURPOSE
This document enables seamless handoff between Hugo's local machine and VPS for EngageFlow development.

---

## PATHS

**Hugo's Local Machine:**
```
/Users/hugo/Downloads/AI Folder/LocalOpenClaw/engageflow
```

**VPS (Margarita):**
```
/root/.openclaw/workspace/engageflow
```

---

## LOCAL → VPS (Hugo sends changes back)

When you're ready to deploy your changes:

1. **Stop VPS service** (I'll do this):
   ```bash
   pm2 stop engageflow-backend
   ```

2. **Zip your local project**:
   ```bash
   cd "/Users/hugo/Downloads/AI Folder/LocalOpenClaw"
   zip -r engageflow-updated.zip engageflow/ -x "*/node_modules/*" "*/venv/*" "*/__pycache__/*" "*/.git/*"
   ```

3. **Send zip to Margarita via Telegram**

4. **I extract + restart**:
   ```bash
   cd /root/.openclaw/workspace
   rm -rf engageflow-backup
   mv engageflow engageflow-backup
   unzip engageflow-updated.zip
   cd engageflow/backend
   source venv/bin/activate
   pip install -r requirements.txt
   pm2 restart engageflow-backend
   ```

---

## VPS → LOCAL (Initial setup + future snapshots)

**Initial setup on your Mac:**

1. **Extract zip**:
   ```bash
   cd "/Users/hugo/Downloads/AI Folder/LocalOpenClaw"
   unzip engageflow.zip
   cd engageflow
   ```

2. **Backend setup**:
   ```bash
   cd backend
   python3 -m venv venv
   source venv/bin/activate
   pip install -r requirements.txt
   ```

3. **Frontend setup**:
   ```bash
   cd ../frontend
   npm install
   ```

4. **Run locally**:
   ```bash
   # Terminal 1 - Backend
   cd backend
   source venv/bin/activate
   uvicorn app:app --reload --port 3103

   # Terminal 2 - Frontend
   cd frontend
   npm run dev
   ```

5. **Access**:
   - Frontend: http://localhost:5173 (or whatever Vite says)
   - Backend API: http://localhost:3103
   - Health: http://localhost:3103/health
   - Debug: http://localhost:3103/debug/scheduler

---

## DATABASE SYNC

**Included in zip:** `backend/engageflow.db`

This contains:
- 37 active communities
- 3 profiles (with cookies)
- All automation settings
- Queue items
- Logs
- Labels

**Safety:** Database changes on your local copy won't affect VPS until you send it back.

**If you want fresh VPS DB state:** Ask me to generate a new zip.

---

## DEVELOPMENT NOTES

**What to change locally:**
- Source code (backend/frontend)
- Logic fixes (e.g., scheduler race condition)
- UI improvements

**What NOT to change:**
- Community URLs (would break on VPS)
- Profile credentials (live on VPS)

**Testing locally:**
- Scheduler will work with local DB snapshot
- Skool interactions won't work (needs live cookies from VPS profiles)

---

## CURRENT STATUS (when this zip was created)

**Bug:** Scheduler stuck in idle_mode with reason "limits_reached" but DB shows actionsToday=0 for all communities.

**Root cause:** Race condition in engine.py line 1184-1202:
- Loads profiles from DB with OLD counters
- Resets counters in DB
- Refreshes in-memory state from stale profile data

**Fix needed:** Reload `db_profiles` after `_reset_daily_counters_if_needed()` call.

**Location:** `/root/.openclaw/workspace/engageflow/backend/automation/engine.py`

---

## QUESTIONS DURING DEVELOPMENT

**Need fresh VPS state?** Ask me to:
```
pm2 logs engageflow-backend --lines 100
curl http://127.0.0.1:3103/debug/scheduler | jq
sqlite3 backend/engageflow.db "SELECT status, COUNT(*) FROM communities GROUP BY status"
```

**Ready to deploy?** Send zip + tell me deployment instructions if different from standard process above.

---

## SAFETY RULES

1. **Always test locally before deploying to VPS**
2. **Never commit credentials to git**
3. **Keep VPS backup before extracting new zip** (I do this automatically)
4. **Verify service restarts successfully after deployment**

---

**Last updated:** 2026-02-27 17:20 CET
**Status:** EngageFlow idle 17h on VPS (race condition bug present)
