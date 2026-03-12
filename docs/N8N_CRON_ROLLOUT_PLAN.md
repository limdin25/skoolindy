# Skoollindy n8n Cron Rollout Plan

## Overview

Controlled rollout of cron-driven comment automation: one profile → one profile + more communities → add second profile → all profiles. Guardrails prevent duplicate queueing and runaway execution.

## Prerequisites

- **orchestrationMode** set to **n8n** in Skoollindy (Automation settings).
- Backend `N8N_API_KEY` set; n8n env: `SKOOLLINDY_BASE_URL`, `SKOOLLINDY_N8N_KEY`.
- Phase 1: set `SKOOLLINDY_CRON_PROFILE_ID` and optionally `SKOOLLINDY_CRON_COMMUNITY_IDS` (comma-separated) or use first profile + first community from config.

---

## Cron Workflow Architecture

| Workflow | Trigger | Purpose | Phase 1 caps |
|----------|---------|---------|----------------|
| **Skoollindy Scan and Queue (Cron)** | Every 15 min | Health/config checks → GET queue → if queue below cap, scan one profile/community → filter → queue at most 1 item. | 1 profile, 1–3 communities, max 1 queued per run, queue cap 2. |
| **Skoollindy Execute Queue (Cron)** | Every 5 min | Health check → if n8n mode, GET queue → execute at most 1 item. | Max 1 executed per run. |

**Guardrails (Scan and Queue Cron)**

- If `masterEnabled` is false → stop (no scan).
- If `orchestrationMode` !== `"n8n"` → stop.
- If GET /queue `items.length` >= `QUEUE_CAP` (Phase 1: 2) → skip queueing (output skip reason).
- No eligible post → do nothing (output skip reason).
- Backend queue-comment is upsert by (profile_id, post_url) → no duplicate row for same post; we still cap “new” queueing by running scan/queue only when queue is below cap.
- Phase 1: single profile and 1–3 communities from env or config; max_posts = 5.

**Guardrails (Execute Queue Cron)**

- If GET /api/n8n/health returns `orchestrationMode` !== `"n8n"` → stop (no execute).
- If backend health fails (4xx/5xx) → stop.
- Execute at most 1 item per run (pick first from GET /queue).

---

## Phased Rollout

### Phase 1 — One profile, 1–3 communities

| Item | Value |
|------|--------|
| **Profiles** | 1 (set via `SKOOLLINDY_CRON_PROFILE_ID` or first enabled) |
| **Communities** | 1 to 3 (same profile) |
| **Cron (scan)** | Every 15 minutes |
| **Cron (execute)** | Every 5 minutes |
| **Queue cap** | 2 (do not queue if queue length >= 2) |
| **Max queued per run** | 1 |
| **Max executed per run** | 1 |
| **max_posts** | 5 |
| **Success criteria** | No duplicate queue entries; no more than 1 new item per scan run; execute succeeds and item removed; logs show n8n_scan, n8n_queue, n8n_execute. |
| **Rollback trigger** | Any duplicate queue entry; queue length > 3; execute errors repeatedly; session/login_required; or manual decision. |

### Phase 2 — Same profile, more communities

| Item | Value |
|------|--------|
| **Profiles** | 1 (same) |
| **Communities** | All for that profile (or capped e.g. 10) |
| **Cron (scan)** | Every 15 minutes |
| **Cron (execute)** | Every 5 minutes |
| **Queue cap** | 5 |
| **Max queued per run** | 2 |
| **Max executed per run** | 1 |
| **Success criteria** | Multiple communities scanned in rotation; queue stays under cap; no duplicates. |
| **Rollback trigger** | Same as Phase 1; or revert to Phase 1 community list. |

### Phase 3 — Second profile added

| Item | Value |
|------|--------|
| **Profiles** | 2 (add second via env or config list) |
| **Communities** | Per profile as in Phase 2 |
| **Cron (scan)** | Every 15 minutes |
| **Cron (execute)** | Every 5 minutes |
| **Queue cap** | 8 |
| **Max queued per run** | 2 |
| **Max executed per run** | 1 |
| **Success criteria** | Both profiles receive scans; queue balanced; no cross-profile duplicate. |
| **Rollback trigger** | Remove second profile from cron config; revert to Phase 2. |

### Phase 4 — All profiles

| Item | Value |
|------|--------|
| **Profiles** | All enabled (from runtime-config) |
| **Communities** | All per profile |
| **Cron (scan)** | Every 15 min (or 10 min if needed) |
| **Cron (execute)** | Every 5 min (or 3 min if queue grows) |
| **Queue cap** | 15–30 (tune by daily limits) |
| **Max queued per run** | 3–5 |
| **Max executed per run** | 2 |
| **Success criteria** | All profiles and communities covered; queue stable; daily limits respected. |
| **Rollback trigger** | Revert to Phase 3; or set orchestrationMode back to internal. |

---

## Files

- **Workflows:** `n8n/skoollindy-scan-and-queue-cron.json`, `n8n/skoollindy-execute-queue-cron.json`
- **Runbook:** `docs/N8N_OPERATIONS_RUNBOOK.md`
- **This plan:** `docs/N8N_CRON_ROLLOUT_PLAN.md`

---

## Phase 1 Validation (manual first run)

Before enabling cron:

1. Set orchestrationMode to n8n; confirm via GET /automation/settings.
2. Run Scan and Queue Cron **once** manually (same logic as cron).
3. Confirm: health and mode checked; queue length checked; if queue already >= 2, run skips queueing; if < 2, at most one new item queued.
4. Run Execute Queue Cron **once** manually; confirm at most one item executed.
5. GET /queue and logs: no duplicate for same post_url+profile_id; item count as expected.
6. Then activate cron triggers in n8n.
