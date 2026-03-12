# n8n — Full report (Skoolindy / Engageflow)

**Purpose:** Single reference for workflow JSON locations, backend entry points, request/response shapes, DB usage, and ops docs.

**Source tree:** `engageflow-repo/` (same n8n pack can live in `skoolindy` repo under `n8n/` if synced).

---

## 1. Workflow JSON files (import into n8n)

| File | Purpose |
|------|--------|
| `engageflow-repo/n8n/skoollindy-healthcheck.json` | Manual: GET `/api/n8n/health` + GET `/api/n8n/runtime-config` → validate |
| `engageflow-repo/n8n/skoollindy-scan-and-queue.json` | Manual: config → scan-community → filter/match → AI node → queue-comment |
| `engageflow-repo/n8n/skoollindy-scan-and-queue-cron.json` | Cron: periodic scan+queue (same API chain, scheduled) |
| `engageflow-repo/n8n/skoollindy-execute-queue.json` | Manual: GET `/queue` → pick first → POST execute-comment |
| `engageflow-repo/n8n/skoollindy-execute-queue-cron.json` | Cron every 5 min: health → guard `orchestrationMode === n8n` → GET `/queue` → execute-comment |
| `engageflow-repo/n8n/skoollindy-daily-reset.json` | Cron: POST `/api/n8n/reset-daily` |

**Common URL pattern in JSON:**  
`{{ $env.SKOOLLINDY_BASE_URL || 'http://localhost:3113' }}/api/n8n/...`

**Common header:**  
`X-N8N-KEY: {{ $env.SKOOLLINDY_N8N_KEY }}`  
(must match backend `N8N_API_KEY` or `SKOOLLINDY_N8N_API_KEY`)

**Execute cron note:** GET `/queue` node in the cron workflow often has **no** `X-N8N-KEY` header (public queue read); POST execute-comment **does** send the key.

---

## 2. Backend entry points (API)

Base: `SKOOLLINDY_BASE_URL` (e.g. `http://38.242.229.161:3113`).  
All `/api/n8n/*` routes go through middleware: if `N8N_API_KEY` (or `SKOOLLINDY_N8N_API_KEY`) is set on the backend, every request must send header **`X-N8N-KEY`** or **401** `{ "success": false, "error": "unauthorized", ... }`.

| Method | Path | Auth | Purpose |
|--------|------|------|--------|
| GET | `/api/n8n/health` | Key if set | masterEnabled, orchestrationMode, queueCount, enabledProfilesCount, timestamp |
| GET | `/api/n8n/runtime-config` | Key if set | Normalized config for n8n (read-only from DB) |
| POST | `/api/n8n/scan-community` | Key if set | Playwright scan one community; returns posts list + errors |
| POST | `/api/n8n/queue-comment` | Key if set | Upsert queue row with `generated_comment`, `scheduled_for`, etc. |
| POST | `/api/n8n/execute-comment` | Key if set | Post one queued comment (loads `generatedComment` from DB) |
| POST | `/api/n8n/reset-daily` | Key if set | Idempotent daily counter reset |
| POST | `/api/n8n/execute-due` | Key if set | **Skoolindy-push only:** execute all items with `scheduledFor <= now` (batch) |
| GET | `/queue` | Usually none | List queue items (response omits `generatedComment` in API model; DB still has it) |
| GET | `/automation/n8n-timing` | None | Dashboard: last scan/execute/queue timestamps, pending count (**skoolindy-push**) |

**Orchestration switch:** `automation_settings` JSON key `orchestrationMode`: `"internal"` | `"n8n"`. When `"n8n"`, internal scheduler does **not** prefill/execute comment queue; n8n must call the APIs above.

---

## 3. Request/response schemas (Pydantic / JSON)

### POST `/api/n8n/scan-community`

**Body:**
```json
{
  "profile_id": "<uuid>",
  "community_id": "<uuid>",
  "max_posts": 20
}
```
- `max_posts` optional, clamped 1–50.

**Response:** JSON from `engine.run_scan_community_sync` — includes `posts[]` with fields like `post_url`, `post_text`, `already_commented`, `blacklisted_match`, or `error` (e.g. `login_required`).

### POST `/api/n8n/queue-comment`

**Body:**
```json
{
  "profile_id": "<uuid>",
  "community_id": "<uuid>",
  "post_id": "<optional>",
  "post_url": "<string>",
  "keyword": "<string>",
  "keyword_rule_id": "<optional>",
  "prompt_used": "<optional>",
  "generated_comment": "<string>",
  "scheduled_for": "<iso or display time parseable>",
  "priority_score": <optional int>,
  "fallback_level_used": "keyword_rule" | "general_comment_fallback"
}
```

**Response:** `{ "success": true, "queue_item_id": "<uuid>" }`  
DB upsert: same `profileId` + `postId` (post_url) updates existing row.

### POST `/api/n8n/execute-comment`

**Body:** `{ "queue_item_id": "<uuid>" }`  
**Response:** Result from `run_execute_comment_sync` — `success`, `error`, etc.

### POST `/api/n8n/execute-due` (skoolindy-push only)

**Body:** none  
**Response:** `{ "success": true, "executed": n, "failed": n, "skipped": n, "items": [...] }`  
Requires `orchestrationMode === "n8n"` and `masterEnabled`; otherwise 400.

### GET `/api/n8n/health`

**Response:**
```json
{
  "masterEnabled": true,
  "orchestrationMode": "n8n",
  "schedulerDrivesComments": false,
  "queueCount": 0,
  "enabledProfilesCount": 3,
  "timestamp": "2026-03-12T..."
}
```

### GET `/api/n8n/runtime-config`

**Response (top-level keys):**
- `masterEnabled`, `activeDays`, `runFrom`, `runTo`
- `delayMin`, `delayMax`, `roundsBeforeConnectionRest`, `connectionRestMinutes`
- `commentFallbackEnabled`, `commentFallbackPrompt`, `blacklistTerms`
- `enabledProfiles`: `[{ id, name, status, dailyUsage }]`
- `communitiesByProfile`: `{ "<profileId>": [{ id, profileId, name, url, dailyLimit, maxPostAgeDays, status, actionsToday }] }`
- `keywordRules`: `[{ id, keyword, persona, promptPreview, commentPrompt, active, assignedProfileIds }]`

Built by `_build_n8n_runtime_config(db)` in `app.py` (read-only).

---

## 4. Database schema (tables n8n touches)

| Table | Use |
|-------|-----|
| `automation_settings` | `orchestrationMode`, caps, windows — source of truth for mode |
| `profiles` | Scan/execute uses profile; `dailyUsage` capped on execute-due |
| `communities` | Scan by `community_id`; names for queue display |
| `keyword_rules` | runtime-config + scan-and-queue code node matching |
| `queue_items` | Insert/update via queue-comment; delete on cap/failure; columns include `generatedComment`, `status`, `scheduledFor`, `postId` (= post URL) |
| `logs` | Backend logs for n8n_scan, n8n_queue, n8n_execute, n8n_reset, n8n_execute_due |
| `activity_feed` | Populated when comment posts successfully (engine) |

**queue_items** relevant columns (from schema dump):  
`id, profile, profileId, community, communityId, postId, keyword, keywordId, scheduledTime, scheduledFor, priorityScore, countdown, generatedComment, status, fallbackLevelUsed, createdAt, updatedAt`

---

## 5. Automation engine behavior

- **`orchestrationMode == "n8n"`:** Scheduler loop publishes log and sleeps (~30s) — no internal prefill/execute for comments.
- **Scan:** `engine.run_scan_community_sync(profile_id, community_id, max_posts)` — Playwright only in backend.
- **Execute:** `engine.run_execute_comment_sync(queue_item_id)` — loads full row from DB, posts via Playwright.

---

## 6. Frontend (dashboard)

- **`api.n8nTiming()`** → GET `/automation/n8n-timing`  
  Type: `lastScanTime`, `lastExecuteTime`, `lastQueueInsert`, `pendingQueueItems`, `nextScheduledFor`, `masterEnabled`, `isN8nMode`, `executorIntervalSeconds`
- **`useN8nTiming(enabled)`** in `useEngageFlow.ts` polls when enabled.

---

## 7. Activation script

**`engageflow-repo/scripts/n8n-activate-skoollindy.sh`**

- Requires `N8N_INSTANCE_API_KEY` (n8n Settings → API).
- POST `${N8N_URL}/api/v1/workflows` with JSON from `skoollindy-scan-and-queue-cron.json` and `skoollindy-execute-queue-cron.json`, then POST `.../workflows/:id/activate`.
- Default `N8N_URL=http://38.242.229.161:5678`.

---

## 8. Documentation index (read in this order)

| Doc | Content |
|-----|--------|
| `engageflow-repo/docs/N8N_COMMENT_AUTOMATION.md` | Contract: who owns what; endpoints; GET /queue note |
| `engageflow-repo/docs/N8N_WORKFLOW_ROLLOUT_PLAN.md` | Workflow A–D table, env vars, import steps |
| `engageflow-repo/docs/N8N_CRON_ROLLOUT_PLAN.md` | Cron scheduling, rollout |
| `engageflow-repo/docs/N8N_OPERATIONS_RUNBOOK.md` | Ops |
| `engageflow-repo/docs/N8N_SET_VARIABLES_NOW.md` | Docker env for n8n container (**contains plaintext key in repo — rotate if exposed**) |
| `engageflow-repo/docs/N8N_ACTIVATION_RUNBOOK.md` | Activation |
| `engageflow-repo/docs/SKOOLLINDY_N8N_FINAL_GO_LIVE.md` | Go-live |
| `engageflow-repo/docs/SKOOLLINDY_N8N_ACTIVATION_REPORT.md` | Report |
| `engageflow-repo/backend/tests/test_n8n_activity_feed.py` | Tests around n8n activity |

---

## 9. Environment variables (checklist)

| Where | Variable | Purpose |
|-------|----------|--------|
| Backend | `N8N_API_KEY` or `SKOOLLINDY_N8N_API_KEY` | Protects `/api/n8n/*` |
| Backend | `ENGAGEFLOW_DB_PATH` | SQLite path |
| n8n container / process | `SKOOLLINDY_BASE_URL` | Backend base URL, no trailing slash |
| n8n container / process | `SKOOLLINDY_N8N_KEY` | Same value as backend key → `X-N8N-KEY` |
| n8n (workflow B) | `OPENAI_API_KEY` | Optional; AI node for comment text |
| n8n (manual test) | `SKOOLLINDY_TEST_PROFILE_ID`, `SKOOLLINDY_TEST_COMMUNITY_ID` | Override scan target |

---

## 10. Quick curl examples (replace BASE and KEY)

```bash
curl -s -H "X-N8N-KEY: $KEY" "$BASE/api/n8n/health" | jq .
curl -s -H "X-N8N-KEY: $KEY" "$BASE/api/n8n/runtime-config" | jq .
curl -s -X POST -H "Content-Type: application/json" -H "X-N8N-KEY: $KEY" \
  -d '{"profile_id":"...","community_id":"...","max_posts":5}' \
  "$BASE/api/n8n/scan-community" | jq .
```

---

*Generated for orchestrator/debug. Workflow JSON is source of truth for exact node graph; this doc summarizes contracts and entry points.*
