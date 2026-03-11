# ENGAGEFLOW_MASTER_CONTINUITY.md

**Project:** EngageFlow
**Authority Level:** Canonical Continuity Contract
**Status:** Active 24/7 Autonomous System
**Purpose:** Deterministic AI / Engineer Takeover Without Chat History

## 0. MANDATORY RULE

Before modifying this project, any AI agent or engineer MUST:
1. Read this entire document.
2. Inspect repository state.
3. Inspect runtime state.
4. Validate invariants.
5. Only then modify code. No prior chat history is required if this document is followed. This document is the authoritative continuity layer.

## 1. PRODUCT MISSION

EngageFlow is a deterministic, multi-profile, multi-community automation engine. It must:
* Run 24/7 without silent failure
* Enforce daily limits strictly
* Respect schedule windows
* Maintain correct counters
* Resume safely after restart
* Never drift from database truth
* Never enter infinite idle due to stale in-memory state

**Primary principle:** The database is the source of truth. In-memory state must never contradict DB state.

## 2. BUSINESS CONTEXT

EngageFlow automates engagement actions across communities. The system operates:
* Multiple profiles
* Multiple communities
* Daily limits per community
* Daily usage per profile
* Schedule windows
* Controlled wake/sleep logic

**Primary objective:** Stable, predictable automation within platform constraints. The system must never:
* Exceed limits
* Enter retry storms
* Drift counters
* Hide failures
* Freeze silently

## 3. STRATEGIC GOALS

1. **Deterministic Scheduler**
   * No hidden state
   * No race conditions
   * No stale memory

2. **24/7 Operational Stability**
   * Continuous heartbeat
   * Continuous scheduler ticks
   * Observable idle states
   * Restart-safe behavior

3. **Observability**
   * /health endpoint
   * /debug/scheduler endpoint
   * Accurate counters
   * Transparent idle_reason

4. **Controlled Automation**
   * Strict dailyLimit enforcement
   * Profile dailyUsage enforcement
   * Schedule enforcement
   * Network backoff safety

## 4. HIGH-LEVEL ARCHITECTURE

**Backend:**
* FastAPI
* Uvicorn
* Managed by PM2
* SQLite (engageflow.db)

**Core engine:** automation/engine.py
**Primary loop:** _scheduler_loop()

**Deployment topology:**

Mac:
* Claude Code
* SSH control

VPS:
* EngageFlow backend
* PM2
* SQLite DB
* Claude remote socket

**PM2 CWD must match:** `/root/.openclaw/workspace/engageflow/backend`

## 5. SCHEDULER LOOP CONTRACT

Correct sequence MUST be:
1. Reset daily counters (DB)
2. Load runtime config (DB)
3. Refresh in-memory profiles
4. Evaluate limits
5. Execute round-robin pass
6. Enter idle if required

If load occurs before reset, in-memory state becomes stale, causing false "limits_reached". This ordering is mandatory.

## 6. SYSTEM STATE MACHINE (AUTHORITATIVE)

**Scheduler states:**
* RUNNING
* PAUSED
* IDLE_LIMITS_REACHED
* IDLE_OUTSIDE_SCHEDULE
* IDLE_MASTER_DISABLED
* IDLE_CONNECTION_REST
* STOPPED

**Transitions must:**
* Update run_state
* Update idle_mode
* Update idle_reason
* Update last_scheduler_tick_ts
* Be logged

No silent transitions allowed.

## 7. 24/7 OPERATIONAL INVARIANTS

Must always hold:
* heartbeat_age_seconds < 60
* last_scheduler_tick_age_seconds < 15 while running
* idle_reason reflects DB truth
* communities_reached_limit equals SQL reality
* actionsToday <= dailyLimit
* dailyUsage <= profile limits
* Restart rebuilds state from DB only

**Scheduler must wake on:**
* Daily reset
* Settings change
* Capacity restored
* Restart

## 8. DATA CONTRACTS

**communities:**
* id
* status
* dailyLimit
* actionsToday
* matchesToday

**profiles:**
* id
* status
* dailyUsage
* groupsConnected

**Invariant:**
* actionsToday <= dailyLimit
* dailyUsage <= profile limit

All scheduler decisions derive from DB truth only.

## 9. FAILURE MODES & RECOVERY RULES

**Known failure classes:**
1. Stale in-memory counters
2. Idle not exiting after reset
3. Scheduler tick freeze
4. DB write failure
5. PM2 restart mid-loop
6. Worktree divergence
7. Claude remote socket freeze

**Recovery rules:**
* DB always authoritative
* Restart must reload from DB
* No persistent memory counters across restart
* No hidden background counter updates
* Scheduler must re-evaluate limits on each loop

## 10. DEBUG PROTOCOL (MANDATORY BEFORE ANY FIX)

Before modifying code:
1. `git log -3 --oneline`
2. `pm2 list`
3. `curl /health`
4. `curl /debug/scheduler`
5. SQLite: `select actionsToday, dailyLimit from communities where status='active';`

**Verify:**
* DB counters
* In-memory counters
* idle_reason consistency
* Tick freshness

No fix valid without reproducing mismatch.

## 11. ARCHAEOLOGY PROTOCOL

When taking over:
1. Inspect commit history: `git log --graph --decorate --oneline -20`
2. Inspect `.claude/worktrees/`
3. Confirm PM2 CWD
4. Confirm HEAD matches expected commit
5. Confirm scheduler ordering matches contract

If behavior contradicts logs, assume ordering bug or stale memory before assuming infra failure.

## 12. NO MAGIC RULE

No hidden timers. No implicit resets. No background thread modifying counters. No logic outside scheduler loop modifying limits. All limit changes must originate from:
* Daily reset
* Explicit DB update
* Explicit settings change

## 13. CHANGE MANAGEMENT RULES

Every change must include:
* Smallest possible diff
* One logical intent per commit
* Test plan
* Rollback command
* Success metric

No production config, migration, or data changes without explicit approval.

## 14. AI CONTINUITY MEMORY RULE

After every meaningful code change: Append to AI_RUNTIME_LOG.md:
* UTC timestamp
* Summary
* Files modified
* Reason
* Acceptance test
* Current /debug snapshot
* Next action

No change is valid without log entry.

## 15. TAKEOVER ACCEPTANCE TEST

System passes takeover validation if:
* 24/7 heartbeat stable 5+ minutes
* Scheduler tick updates during idle
* Limits reset correctly
* Idle exits when capacity restored
* Debug endpoint matches DB truth

If any fails, system is not production-safe.

---

**END OF CONTRACT**
