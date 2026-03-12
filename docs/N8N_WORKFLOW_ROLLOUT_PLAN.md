# Skoollindy n8n Workflow Rollout Plan

## Overview

This document describes how to import, configure, and run the n8n workflow pack for Skoollindy comment automation. The backend is the source of truth for DB, queue, and Playwright; n8n orchestrates scan → AI comment generation → queue → execute using the live API.

## Workflow Architecture

| Workflow | Purpose | Trigger | Key nodes |
|----------|---------|---------|-----------|
| **A — skoollindy-healthcheck** | Test backend reachability, auth, and fetch runtime config | Manual | HTTP GET health, HTTP GET runtime-config, validation |
| **B — skoollindy-scan-and-queue** | Scan one community, match keywords, generate comment in n8n, queue via API | Manual or webhook | Config → scan-community → filter posts → AI → queue-comment |
| **C — skoollindy-execute-queue** | Execute one queued comment via backend | Manual then cron | HTTP GET queue, pick item, HTTP POST execute-comment |
| **D — skoollindy-daily-reset** | Reset daily counters once per day | Cron (e.g. 00:05) | HTTP POST reset-daily |

## Environment / Credentials

n8n needs the following. Set as **environment variables** or as **n8n variables** (Settings → Variables).

| Variable | Required | Description |
|----------|----------|--------------|
| `SKOOLLINDY_BASE_URL` | Yes | Backend base URL, e.g. `http://38.242.229.161:3113` or `https://your-backend.example.com`. No trailing slash. |
| `SKOOLLINDY_N8N_KEY` | Yes | Same value as `N8N_API_KEY` (or `SKOOLLINDY_N8N_API_KEY`) on the Skoollindy backend. Sent as header `X-N8N-KEY` on every `/api/n8n/*` request. |
| `OPENAI_API_KEY` | Optional | Used by Workflow B for AI comment generation inside n8n. If not set, you must provide comment text another way (e.g. fixed test comment). |

**Where used**

- Every HTTP Request node that calls `{{ $env.SKOOLLINDY_BASE_URL }}/api/n8n/*` must send header:
  - **Name:** `X-N8N-KEY`
  - **Value:** `{{ $env.SKOOLLINDY_N8N_KEY }}`
- Workflow B uses OpenAI (or similar) node for comment generation; configure its credential to use `OPENAI_API_KEY` or your chosen AI provider.

## Importing Workflows

1. In n8n: **Workflows** → **Import from File** (or **Add workflow** → **Import**).
2. Import each JSON file from the `n8n/` folder:
   - `n8n/skoollindy-healthcheck.json`
   - `n8n/skoollindy-scan-and-queue.json`
   - `n8n/skoollindy-execute-queue.json`
   - `n8n/skoollindy-daily-reset.json`
3. After import, set the variables above (Settings → Variables or `.env` for self-hosted n8n).
4. **Important:** The workflows reference `$env.SKOOLLINDY_BASE_URL` and `$env.SKOOLLINDY_N8N_KEY`. Ensure these are set in the n8n environment or re-create the HTTP Request nodes and set the URL/header to use your n8n variables.

## Testing One Profile + One Community

1. **Backend:** In Skoollindy UI, set **Comment automation orchestration** to **n8n**.
2. **Health:** Run Workflow A (manual). Confirm output shows `masterEnabled`, `orchestrationMode`, and `enabledProfiles.length > 0`.
3. **Config:** From Workflow A or B, note one `profile_id` and one `community_id` from runtime-config (e.g. first enabled profile and first community for that profile).
4. **Scan:** Run Workflow B with input (or set in the workflow):
   - `profile_id`: chosen profile UUID
   - `community_id`: chosen community UUID
   - `max_posts`: 5
5. **Queue:** Workflow B filters posts (exclude already_commented, blacklisted_match), matches keyword rules, generates one comment, and calls POST /api/n8n/queue-comment. Check output for `queue_item_id`.
6. **Execute:** Run Workflow C (manual). It GETs /queue, picks the first item, POSTs /api/n8n/execute-comment. Confirm success in output and in Skoollindy queue/logs/UI.
7. **Reset:** Run Workflow D once to test; then set cron to 00:05 if desired.

## Moving to Broader Rollout

- **Multiple profiles/communities:** Loop over `enabledProfiles` and each profile’s `communitiesByProfile` in Workflow B; call scan-community per pair; aggregate and queue comments (respect daily limits from config).
- **Scheduling:** Add cron triggers to B (e.g. every 15–30 min) and C (e.g. every 5 min); keep `scheduled_for` logic simple (e.g. now + 1–2 min) for V1.
- **Retries:** On execute-comment failure, log and optionally retry with backoff; do not delete the queue item (backend leaves it so you can retry or abandon).

## Failure Handling and Retry Logic

| Endpoint | On failure | Recommendation |
|----------|------------|----------------|
| GET /api/n8n/health | 401 / 5xx | Check `X-N8N-KEY` and backend health; alert. |
| POST /api/n8n/scan-community | `error` in body (e.g. login_required, community_not_found) | Skip that profile/community or retry later; log. |
| POST /api/n8n/queue-comment | 400 validation | Fix payload (required: profile_id, community_id, post_url, keyword, generated_comment, scheduled_for). |
| POST /api/n8n/execute-comment | `success: false`, `error` set | Backend does not remove item; retry or mark for manual review. |
| POST /api/n8n/reset-daily | 5xx | Retry once; idempotent so safe to call again. |

## Node-by-node reconstruction (if import fails)

**Workflow B (Scan and Queue)** — if the OpenAI node type/version differs or you prefer a fixed comment for testing:

1. **Manual Trigger** → **Set**: Set `profile_id`, `community_id`, `max_posts` (use `$env.SKOOLLINDY_TEST_PROFILE_ID` etc. or static UUIDs).
2. **HTTP Request** GET `{{ $env.SKOOLLINDY_BASE_URL }}/api/n8n/runtime-config`, header `X-N8N-KEY`: `{{ $env.SKOOLLINDY_N8N_KEY }}`.
3. **HTTP Request** POST `{{ $env.SKOOLLINDY_BASE_URL }}/api/n8n/scan-community`, body JSON: `profile_id`, `community_id`, `max_posts` from Set.
4. **Code** node: Filter `posts` (exclude `already_commented`, `blacklisted_match`, empty `post_text`); match first post to keyword rules from config; set `keyword`, `keyword_rule_id`, `prompt_used`, `fallback_level_used`; output one item or `skipQueue: true`.
5. **IF** node: when `skipQueue` is false → continue.
6. **Replace “Generate Comment”** with a **Code** node that returns `{ message: { content: "Thanks for sharing. Great point." } }` (or use OpenAI node with your credential).
7. **Code** node: Build payload with `profile_id`, `community_id`, `post_url`, `post_id`, `keyword`, `keyword_rule_id`, `generated_comment` (from previous node), `scheduled_for` (e.g. now + 1 min ISO), `fallback_level_used`.
8. **HTTP Request** POST `{{ $env.SKOOLLINDY_BASE_URL }}/api/n8n/queue-comment`, header `X-N8N-KEY`, body JSON from step 7.

**Workflow C (Execute)** — GET /queue (no n8n key), Code: take `items[0].id`, IF: id not empty → POST /api/n8n/execute-comment with `{ "queue_item_id": "..." }` and X-N8N-KEY.

## File Reference

- **Contract:** `docs/N8N_COMMENT_AUTOMATION.md`
- **Single-profile test steps:** `docs/N8N_SINGLE_PROFILE_TEST_PLAN.md`
- **Workflow JSONs:** `n8n/skoollindy-healthcheck.json`, `n8n/skoollindy-scan-and-queue.json`, `n8n/skoollindy-execute-queue.json`, `n8n/skoollindy-daily-reset.json`
