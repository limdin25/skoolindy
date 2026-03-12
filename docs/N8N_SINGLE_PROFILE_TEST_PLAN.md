# Skoollindy n8n — Single Profile Test Plan

Use this sequence to validate one profile and one community end-to-end with the n8n workflow pack.

## Prerequisites

- Skoollindy backend running with n8n migration (VPS or local).
- `N8N_API_KEY` set in backend env; same value available in n8n as `SKOOLLINDY_N8N_KEY`.
- In Skoollindy UI: **Comment automation orchestration** set to **n8n**.
- n8n workflows A–D imported; `SKOOLLINDY_BASE_URL` and `SKOOLLINDY_N8N_KEY` set in n8n.

## Test Sequence

### 1. Health check

- **Action:** Run **Workflow A (skoollindy-healthcheck)** manually.
- **Expect:**
  - HTTP 200 on GET /api/n8n/health and GET /api/n8n/runtime-config.
  - Output includes `masterEnabled`, `orchestrationMode`, `enabledProfiles` with length > 0.
- **If 401:** Check `X-N8N-KEY` header and that key matches backend `N8N_API_KEY`.

### 2. Runtime config fetch

- **Action:** From Workflow A output (or run Workflow B and use its config step), note:
  - One `profile_id` from `enabledProfiles` (e.g. first).
  - One `community_id` from `communitiesByProfile[profile_id]` (e.g. first).
- **Expect:** Valid UUIDs; community belongs to the chosen profile.

### 3. Scan one community

- **Action:** Run **Workflow B** with:
  - `profile_id`: chosen profile UUID
  - `community_id`: chosen community UUID
  - `max_posts`: 5
- **Expect:**
  - POST /api/n8n/scan-community returns 200.
  - Body has `posts` array; optional `error` null or absent.
- **If error:** e.g. `login_required` → fix profile cookies in Skoollindy; `community_not_found` → check community_id.

### 4. Generate one comment (inside Workflow B)

- Workflow B filters posts (exclude `already_commented`, `blacklisted_match`), matches keyword rules from runtime-config, then generates comment text (AI in n8n or fallback).
- **Expect:** At least one post selected and one comment string produced (max ~40 words, natural tone).

### 5. Queue one comment

- **Action:** Workflow B calls POST /api/n8n/queue-comment with:
  - profile_id, community_id, post_url (from scan), keyword, generated_comment, scheduled_for (e.g. now + 1 min), optional keyword_rule_id, fallback_level_used.
- **Expect:** Response `{ "success": true, "queue_item_id": "<uuid>" }`.
- **If 400:** Check required fields: `profile_id`, `community_id`, `post_url`, `keyword`, `generated_comment`, `scheduled_for`.

### 6. Execute one comment

- **Action:** Run **Workflow C (skoollindy-execute-queue)** manually.
- **Expect:**
  - GET /queue returns 200 with `items` containing at least the queued item.
  - POST /api/n8n/execute-comment returns `{ "success": true, "queue_item_id": "...", "error": null }`.
- **If success: false:** Check `error` (e.g. `generated_comment_missing`, `login_required`). Backend does not remove the item; fix and re-run C or delete item in UI.

### 7. Verify result in Skoollindy

- **Queue:** In Skoollindy UI, Action Queue should no longer show the executed item (backend removes it on success).
- **Logs:** Automation logs should show entries for n8n_scan, n8n_queue, n8n_execute.
- **Community:** On Skool, the post should show the posted comment.

## Expected Results Summary

| Step | Expected result |
|------|-----------------|
| Health | 200; masterEnabled, orchestrationMode, enabledProfiles present |
| Config | Valid profile_id and community_id for that profile |
| Scan | 200; posts array; no error or handled error |
| Generate | One comment string (short, human) |
| Queue | 200; queue_item_id returned |
| Execute | 200; success true; item removed from queue |
| UI | Queue empty for that item; logs show n8n actions; comment visible on post |

## Notes

- **GET /queue** does not return `generatedComment` in the API response (backend model omits it). Workflow C picks the first queue item and executes it; items queued via POST /api/n8n/queue-comment have `generatedComment` stored in the DB, so execute-comment will succeed.
- For the first test, use `scheduled_for` = now + 1 minute so the item is due when you run Workflow C.
- If no posts pass the filter in step 4, use a community that has recent, non-blacklisted posts and ensure the profile has not already commented on them.
