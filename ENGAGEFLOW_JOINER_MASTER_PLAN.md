# ENGAGEFLOW — COMMUNITY JOINER MASTER PLAN

Status: Phase 1 Complete (UI Mock Only)
Current Production Lock: ENGAGEFLOW_LOCK_eb8faf1
Engine Status: Stable
Comment Scheduler: Untouched

---

# 1. OBJECTIVE

Integrate a Community Join Engine directly into EngageFlow.

Requirements:

- Single backend (FastAPI)
- Single database (engageflow.db)
- Single login system (existing Playwright profile dirs)
- No second server
- No second DB
- No cross-system cookie passing
- Deterministic behavior
- Restart safe
- Zero silent corruption
- Additive architecture only

---

# 2. FINAL ARCHITECTURE

Frontend
  /communities
    Tabs:
      - Joined (existing)
      - Join (new)

Backend (FastAPI)
  - Comment Engine (existing, unchanged)
  - Join Engine (new sidecar module)

Database (SQLite)
  - Existing tables unchanged
  - New joiner-specific tables only

Profile Storage
  - backend/skool_accounts/<profile_id>/browser
  - Shared between comment engine and join engine

---

# 3. NON-NEGOTIABLE INVARIANTS

Join Engine MUST NOT:

- Modify queue_items
- Modify automation_comment_events
- Modify run_state.json
- Modify rotation pointer
- Modify scheduler timing
- Introduce concurrency
- Share mutable runtime state with comment engine

Join Engine MUST:

- Be restart safe
- Persist every state transition
- Be rate limited
- Be disableable via kill switch
- Use its own tables only

---

# 4. PHASE PLAN

## PHASE 1 — UI MOCK (COMPLETE)

- Tabs added to /communities
- Joined tab default
- Join tab fully local useState
- Zero API calls
- Zero backend impact
- Verified via grep + backend log inspection

Lock remains:
ENGAGEFLOW_LOCK_eb8faf1

---

## PHASE 2 — BACKEND JOINER SKELETON

### Goals

- Add joiner persistence tables to engageflow.db
- Add FastAPI endpoints for join jobs and job items
- Enforce deterministic state transitions at API layer
- Provide audit events for every state change
- Add integrity checks to prove joiner does not touch core tables
- joiner_enabled remains false, no worker loop, no Playwright

Out of scope:

- Any browser automation
- Any changes to comment scheduler loop
- Any modifications to run_state.json
- Any changes to queue_items behavior
- Any changes to automation_comment_events

### Database (Additive Only)

join_jobs:

- id TEXT PRIMARY KEY (uuid)
- created_at TEXT (ISO)
- created_by TEXT NULL (future)
- status TEXT NOT NULL (CREATED | RUNNING | PAUSED | COMPLETED | CANCELLED)
- paused INTEGER NOT NULL DEFAULT 0
- total_items INTEGER NOT NULL DEFAULT 0
- completed_items INTEGER NOT NULL DEFAULT 0
- failed_items INTEGER NOT NULL DEFAULT 0
- last_updated_at TEXT NULL

join_job_items:

- id TEXT PRIMARY KEY (uuid)
- job_id TEXT NOT NULL REFERENCES join_jobs(id) ON DELETE CASCADE
- profile_id TEXT NOT NULL REFERENCES profiles(id)
- community_url TEXT NOT NULL
- community_key TEXT NOT NULL (normalized key for dedupe)
- status TEXT NOT NULL (PENDING | RUNNING | JOINED | ALREADY_MEMBER | PENDING_APPROVAL | SKIPPED_PAID | FAILED | CANCELLED)
- attempt_count INTEGER NOT NULL DEFAULT 0
- last_attempt_at TEXT NULL
- fail_reason TEXT NULL
- created_at TEXT (ISO)
- updated_at TEXT (ISO)

join_events:

- id TEXT PRIMARY KEY (uuid)
- job_id TEXT NOT NULL REFERENCES join_jobs(id) ON DELETE CASCADE
- item_id TEXT NULL REFERENCES join_job_items(id) ON DELETE CASCADE
- profile_id TEXT NULL
- event_type TEXT NOT NULL
- detail TEXT NULL
- created_at TEXT (ISO)

Indexes:

- join_job_items(job_id)
- join_job_items(profile_id)
- join_job_items(status)
- join_job_items(community_key)
- UNIQUE(job_id, profile_id, community_key)

Critical: community_url is not trusted for dedupe, only normalized community_key is used.

### Normalization

Function: normalize_community_url(url) -> (canonical_url, community_key)

- trim whitespace
- lowercase host
- remove trailing slashes
- strip query and fragments
- canonical_url = normalized original
- community_key = canonical_url host + path (or extracted slug if stable)

Unit tests must cover 20+ variations.

### API Endpoints

Base path: /joiner

POST /joiner/jobs

Request: { "community_urls": ["..."], "profile_ids": ["..."] } — empty profile_ids means all profiles

Behavior: Validates joiner feature exists, resolves profile_ids, normalizes and dedupes URLs, creates join_job (CREATED), inserts join_job_items (PENDING), emits JOB_CREATED + ITEMS_CREATED.

Response: { "job": {...}, "items_created": N }

GET /joiner/jobs?limit=50&status=CREATED — list jobs newest first

GET /joiner/jobs/{job_id} — job + counters + last_updated_at

GET /joiner/jobs/{job_id}/items?limit=200&status=PENDING — items for job

POST /joiner/jobs/{job_id}/pause — job.status=PAUSED, paused=1, JOB_PAUSED event

POST /joiner/jobs/{job_id}/resume — job.status=CREATED, paused=0, JOB_RESUMED event

POST /joiner/jobs/{job_id}/cancel — job.status=CANCELLED, all non-terminal items CANCELLED, JOB_CANCELLED event

GET /joiner/jobs/{job_id}/events?limit=200 — join_events newest first

No endpoint in Phase 2 triggers Playwright or performs JOIN actions.

### Safety and Isolation (Phase 2)

- Joiner writes only to join_jobs, join_job_items, join_events
- Joiner never writes to: queue_items, automation_comment_events, conversations, messages, profiles, automation_settings, run_state.json
- Comment scheduler code is not modified
- Any reference to Playwright is prohibited

### Integrity

Extend GET /integrity:

- join_job_items UNIQUE(job_id, profile_id, community_key) holds
- join_job counters match item statuses
- no join table triggers reference non-existent profiles
- join tables exist and reachable
- no writes to core tables during joiner API calls (counts before/after in tests)

### Tests (Mandatory)

Framework: pytest

Unit: test_normalize_url_variants, test_community_key_dedupe, test_state_transition_rules

Contract: test_create_job_creates_items, test_create_job_empty_profiles_means_all, test_pause_resume_cancel, test_events_written, test_uniqueness_enforced

Behavioral: test_restart_safety_simulated (create job, pause, re-open DB, confirm state persists)

Invariant: snapshot core tables, call joiner endpoints, assert core table counts unchanged

### Acceptance Criteria

- POST /joiner/jobs creates deterministic items with dedupe
- Jobs and items visible via GET endpoints
- Pause/resume/cancel works and emits events
- All tests pass
- /integrity returns ok
- No modifications to comment engine behavior or timing

### Lock

ENGAGEFLOW_LOCK_joiner_phase2

---

## PHASE 3 — WORKER LOOP (NO PLAYWRIGHT YET)

- joiner_enabled flag
- background loop
- rate limiting
- transition PENDING -> READY
- restart safe
- no browser calls

Acceptance:
- scheduler tick <5s
- no cross-table mutation
- pause/cancel stable

Lock:
ENGAGEFLOW_LOCK_joiner_phase3

---

## PHASE 4 — PLAYWRIGHT EXECUTION (CANARY)

- maxConcurrentProfiles = 1
- 1 join per hour
- exponential backoff
- failure auto-disable
- kill switch enforced

Acceptance:
- 24h stable
- no scheduler degradation
- no duplicate joins
- failure categories visible

Lock:
ENGAGEFLOW_LOCK_joiner_phase4

---

# 5. SAFETY CONTROLS

- joiner_enabled flag
- maxJoinAttemptsPerProfilePerHour
- maxConcurrentProfiles
- exponential backoff
- auto-disable on error spike
- /integrity enforcement
- kill switch immediate stop

---

# 6. ROLLBACK

Backend only:
git reset --hard ENGAGEFLOW_LOCK_eb8faf1
pm2 restart engageflow-backend

Frontend only:
git checkout ENGAGEFLOW_LOCK_eb8faf1 -- frontend/
cd frontend && npm run build

Full rollback:
git reset --hard ENGAGEFLOW_LOCK_eb8faf1
cd frontend && npm run build
pm2 restart engageflow-backend

DB rollback:
Not required (tables additive)

---

# 7. UPDATE LOG

[2026-02-28]
- Join tab integrated in UI
- Verified strings in dist bundle
- Verified zero backend calls from Join tab
- Phase 2 approved to begin
- Consolidated Phase 2: removed duplicate Appendix, single source of truth

---

END OF DOCUMENT

[2026-02-28 — Phase 2 Complete]
- Commit: 058bf85 — feat: add community joiner Phase 2
- Tag: ENGAGEFLOW_LOCK_058bf85
- New files: backend/joiner.py (494 lines), backend/tests/ (590 lines)
- app.py changes: +5 lines (import + ensure_joiner_tables + router registration)
- 3 new tables: join_jobs, join_job_items (UNIQUE dedupe index), join_events
- 8 API endpoints: POST/GET jobs, GET items, POST pause/resume/cancel, GET events, GET integrity
- 60/60 pytest: unit=37, contract=19, behavioral=1, invariant=3
- Core table invariant: queue_items=0, automation_comment_events=71, conversations=82, messages=298, profiles=3, automation_settings=1 — UNCHANGED after all joiner API calls
- /joiner/integrity: ok=true, all 6 checks pass
- No Playwright, no worker loop, no mutation of comment engine
