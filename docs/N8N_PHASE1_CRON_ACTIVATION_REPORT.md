# Skoollindy Phase-1 Cron Activation Report

**Date:** 2026-03-10

## A) Orchestration status

| Field | Value |
|-------|--------|
| **orchestrationMode** | n8n |
| **masterEnabled** | true |

Confirmed via `GET /automation/settings`. Expected values met.

---

## B) Phase-1 profile/community chosen

From `GET /api/n8n/runtime-config` (first enabled profile, first community):

| Field | Value |
|-------|--------|
| **profile_id** | 716e152e-eb1b-4282-9e9a-7eb8714a579d |
| **community_id** | 860ffaa1-025d-4e3e-a4d3-d8ce3dcaa63c |
| **community_name** | 90 Day Business Launch 🚀 |

Cron workflow uses first profile and first community from config (no env override in current JSON). To hard-limit to one profile only, set `SKOOLLINDY_CRON_PROFILE_ID` in n8n or keep using first-from-config.

---

## C) Workflow activation confirmation

**Cron workflow files (repo):**

- `n8n/skoollindy-scan-and-queue-cron.json` — Scan + Queue, every 15 min
- `n8n/skoollindy-execute-queue-cron.json` — Execute, every 5 min

**n8n UI (manual):**

- Import the two JSONs into n8n (Workflows → Import).
- Set variables: `SKOOLLINDY_BASE_URL`, `SKOOLLINDY_N8N_KEY` (and optional `SKOOLLINDY_CRON_PROFILE_ID` / `SKOOLLINDY_CRON_COMMUNITY_ID`).
- Activate both workflows (toggle **Active** on).
- Workflow IDs: assigned by n8n on import (not available from API here).
- Next run times: Scan every :00, :15, :30, :45; Execute every :00, :05, :10, :15, etc. (n8n schedule).

Activation cannot be done from this environment; confirm in n8n after import.

---

## D) Dry-run results

**Simulated on VPS (same logic as cron):**

| Step | Result |
|------|--------|
| GET /api/n8n/health | orchestrationMode n8n, masterEnabled true — guards pass |
| GET /queue | queue_length 0 — queue cap allows scan (< 2) |
| POST /api/n8n/scan-community (profile 716e152e, community 860ffaa1, max_posts=5) | **Fixed:** was `community_not_found`; after setting `ENGAGEFLOW_DB_PATH` in backend `.env`, scan returns 200, posts 5, error null. See [SKOOLLINDY_LIVE_DEBUG_FIX.md](SKOOLLINDY_LIVE_DEBUG_FIX.md). |

**Note:** Root cause was backend using a different DB path at runtime. Fix: set `ENGAGEFLOW_DB_PATH=/root/.openclaw/workspace/skoolindy/backend/engageflow.db` in `backend/.env` and restart skoolindy-backend.

**Execute dry-run:** Queue length 0 → no item to execute; execute workflow would correctly skip (at most 1 executed = 0).

**Guardrails:** Mode and master checked; queue cap respected; at most 1 queued (0 this run); at most 1 executed (0).

---

## E) First 3 cycle metrics table

Full “first 3 scan cycles + 9 execute cycles” requires waiting ~45+ minutes and n8n to be active. From this session:

| Cycle | Posts scanned | Items queued | Items executed | Skip reason | Queue length | Execution result |
|-------|----------------|-------------|----------------|-------------|--------------|------------------|
| 1 (dry) | 0 | 0 | 0 | community_not_found (fixed) | 0 | — |
| 2 (dry) | 5 | 0 | 0 | none (after fix) | 0 | — |
| 3 | — | — | — | — | — | — |

Recommendation: After activating workflows in n8n, run for 3 scan cycles and note the same metrics from n8n execution output and GET /queue.

---

## F) Queue health

| Check | Result |
|-------|--------|
| GET /api/n8n/health | queueCount 0, orchestrationMode n8n |
| GET /queue | items count 0 |
| Queue growing? | No |
| Duplicate items? | None |
| Repeated failures this run | Scan returned community_not_found; execute not run (empty queue). |

---

## G) Failures or warnings

- **Scan community_not_found (resolved):** Root cause was backend DB path. Fix applied: `ENGAGEFLOW_DB_PATH` set in `backend/.env` to skoolindy DB absolute path; restart skoolindy-backend. See [SKOOLLINDY_LIVE_DEBUG_FIX.md](SKOOLLINDY_LIVE_DEBUG_FIX.md).

---

## H) Recommendation

| Option | Recommendation |
|--------|----------------|
| **Continue Phase-1** | Yes, once n8n workflows are imported and activated. Keep Phase-1 limits (1 profile, 1–3 communities, queue cap 2, 1 queued / 1 executed per run). |
| **Adjust guardrails** | No change to guardrails. Optional: pin profile/community via n8n env `SKOOLLINDY_CRON_PROFILE_ID` and `SKOOLLINDY_CRON_COMMUNITY_ID` if the cron workflow is updated to use them. |
| **Escalate to Phase-2** | Only after 3+ scan cycles run successfully in n8n and queue/execute behave as expected (no runaway queue, no duplicates). |

**Summary:** Orchestration and backend settings are correct for Phase-1. `community_not_found` has been fixed (DB path pinned in backend `.env`). Cron workflow files are in the repo; import and activate them in n8n. Phase-1 cron is ready to proceed with the current guardrails.
