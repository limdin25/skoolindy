# n8n Comment Automation (Skoollindy)

This document describes the orchestration model, API contract, and responsibilities when comment automation is driven by n8n.

## Orchestration model

- **UI (Skoollindy)** remains the control layer: master automation, keyword rules, persona/prompt config, assigned profiles, communities, daily limits, max post age, blacklist, delay range, run window, fallback prompt settings. All stored in backend SQLite; no duplicated config lives only in n8n.
- **n8n** is the runtime orchestrator when `orchestrationMode` is set to `n8n`: schedule triggers, scan orchestration, keyword matching (if needed), AI generation, queue creation timing, execution triggering. n8n reads config and state from the backend via the APIs below; it does not own browser sessions, cookies, or Playwright.
- **Backend (Skoollindy)** is the source of truth: SQLite for profiles, communities, keyword rules, automation settings, queue items, logs. It runs Playwright for scanning and for posting comments only. Session/cookie/browser handling stays in the backend.

When `orchestrationMode` is `internal`, the built-in scheduler drives comment automation (prefill + execute) as before. When `orchestrationMode` is `n8n`, the internal scheduler does **not** prefill the queue, run comment scans, generate comment text, or auto-execute the comment queue; n8n is expected to call the n8n endpoints to scan, queue, and execute.

## Endpoints

Base path for all: same as the Skoollindy backend (e.g. `http://host:3113` or your API base).

### Optional API key (recommended for production)

If the backend is exposed beyond localhost, protect n8n endpoints with a shared secret:

1. Set one of these environment variables to a long random string (e.g. 32+ chars):
   - `N8N_API_KEY`, or
   - `SKOOLLINDY_N8N_API_KEY`
2. On every request to any `/api/n8n/*` endpoint, send the header:
   - `X-N8N-KEY: <same value>`
3. If the env is set and the header is missing or wrong, the backend returns `401` with `{"success": false, "error": "unauthorized", "message": "Missing or invalid X-N8N-KEY"}`.
4. If the env is not set, no key is required (backward compatible).

**Protected routes when key is set:**  
`GET /api/n8n/runtime-config`, `POST /api/n8n/scan-community`, `POST /api/n8n/queue-comment`, `POST /api/n8n/execute-comment`, `POST /api/n8n/reset-daily`, `GET /api/n8n/health`.

### GET /api/n8n/runtime-config

**Purpose:** Read-only normalized config for n8n to run comment automation.

**Response:** JSON with:

- `masterEnabled`, `activeDays`, `runFrom`, `runTo`
- `delayMin`, `delayMax`, `roundsBeforeConnectionRest`, `connectionRestMinutes`
- `commentFallbackEnabled`, `commentFallbackPrompt`
- `blacklistTerms` (array)
- `enabledProfiles`: `[{ id, name, status, dailyUsage }]`
- `communitiesByProfile`: `{ profileId: [ { id, profileId, name, url, dailyLimit, maxPostAgeDays, status, actionsToday } ] }`
- `keywordRules`: `[{ id, keyword, persona, promptPreview, commentPrompt, active, assignedProfileIds }]`

### POST /api/n8n/scan-community

**Purpose:** Scan a single community for eligible posts. No queue writes, no AI, no posting.

**Request body:**

```json
{
  "profile_id": "<profile uuid>",
  "community_id": "<community uuid>",
  "max_posts": 20
}
```

**Response:** JSON with:

- `profile_id`, `community_id`, `community_name`
- `posts`: array of `{ post_url, post_id, post_text, post_age_seconds, already_commented, blacklisted_match }`
- `error`: if set, e.g. `community_not_found`, `profile_not_found`, `login_required`, or exception message

### POST /api/n8n/queue-comment

**Purpose:** Upsert a queue item with generated comment. Backend DB remains source of truth.

**Request body:**

```json
{
  "profile_id": "...",
  "community_id": "...",
  "post_id": "...",
  "post_url": "...",
  "keyword": "...",
  "keyword_rule_id": "...",
  "prompt_used": "...",
  "generated_comment": "...",
  "scheduled_for": "2026-03-10T14:00:00",
  "priority_score": 50,
  "fallback_level_used": "keyword_rule"
}
```

`fallback_level_used`: `"keyword_rule"` or `"general_comment_fallback"`.

**Response:** `{ "success": true, "queue_item_id": "<id>" }`

If a queue item already exists for the same `profile_id` + `post_url`, it is updated (upsert).

### POST /api/n8n/execute-comment

**Purpose:** Execute a single queue item: load it from DB and post the stored `generatedComment` via Playwright.

**Request body:**

```json
{
  "queue_item_id": "..."
}
```

**Response:** `{ "success": true|false, "queue_item_id": "...", "error": null|<string> }`

- Comment text is taken from the queue item (`generatedComment`); it is not recomputed.
- On success: queue item is removed, daily counters incremented, success logged.
- On failure: `error` is set (e.g. `generated_comment_missing`, `login_required`, `editor_not_visible`, `submit_failed:...`).

### POST /api/n8n/reset-daily

**Purpose:** Reset daily counters (profiles’ dailyUsage, communities’ actionsToday/matchesToday). Idempotent: same calendar day only the first call has effect.

**Response:** `{ "success": true }`

### GET /api/n8n/health

**Purpose:** Health/status for n8n.

**Response:** JSON with:

- `masterEnabled`, `orchestrationMode` (`"internal"` | `"n8n"`)
- `schedulerDrivesComments`: true when `orchestrationMode === "internal"`
- `queueCount`, `enabledProfilesCount`, `timestamp`

## Queue lifecycle

1. n8n gets config from `GET /api/n8n/runtime-config`.
2. n8n triggers scans (e.g. by profile/community) via `POST /api/n8n/scan-community`.
3. n8n decides which posts to target, generates comment text (outside the app), then creates/updates queue items via `POST /api/n8n/queue-comment` with `generated_comment` and `scheduled_for`.
4. When n8n decides to run a comment, it calls `POST /api/n8n/execute-comment` with `queue_item_id`. Backend loads the item, uses stored `generatedComment`, and posts via Playwright.
5. Optionally n8n calls `POST /api/n8n/reset-daily` at day boundary (idempotent).

## What the UI controls

All automation rules and settings: master on/off, keyword rules (persona, prompts, assigned profiles), communities (urls, limits, max post age), blacklist, delay range, run window, fallback prompts, and **orchestration mode** (Internal vs n8n). No duplicated config should live only in n8n; n8n reads from the backend.

## What n8n controls (when mode is n8n)

When orchestration mode is **n8n**:

- When to trigger scans and for which profile/community.
- When to create/update queue items and their `scheduled_for`.
- When to call execute-comment for which `queue_item_id`.
- When to reset daily (if at all).
- AI generation of comment text (n8n or external); the backend only stores and posts the text via queue-comment and execute-comment.

## What remains in the backend

- SQLite: profiles, communities, keyword_rules, automation_settings, queue_items, logs.
- Playwright: scanning (scan-community) and posting (execute-comment). Session/cookie/browser handling only in backend.
- Inbox sync, DM sync, joiner, auth: unchanged; not part of n8n comment automation.

## GET /queue (for n8n execute workflow)

The standard **GET /queue** endpoint returns `{ items, dailyCapExhausted, nextResetAt }`. Each item in `items` includes: `id`, `profile`, `profileId`, `community`, `communityId`, `postId`, `keyword`, `keywordId`, `scheduledTime`, `scheduledFor`, `priorityScore`, `countdown`. The API response **does not** include `generatedComment` (the model omits it). When n8n calls **POST /api/n8n/execute-comment** with a queue item `id`, the backend loads the full row from the DB (including `generatedComment`) and posts it. So n8n can safely pick the first item from GET /queue and execute it; items queued via POST /api/n8n/queue-comment have the comment stored in the DB.

## Failure handling

- **scan-community:** Returns `error` in the JSON (e.g. `login_required`, `community_not_found`). n8n can retry or skip.
- **queue-comment:** Validation errors return 400. Success returns `queue_item_id`.
- **execute-comment:** Returns `success: false` and `error` string. Backend does not remove the queue item on failure so n8n can retry or abandon.
- All n8n endpoints log to the `logs` table with `module='automation'` and `action` one of `n8n_scan`, `n8n_queue`, `n8n_execute`, `n8n_reset` for visibility in the Skoollindy UI.

## Rollback

To revert to internal-driven comment automation:

1. In the Skoollindy UI, set **Comment automation orchestration** back to **Internal**.
2. No need to change n8n workflows; the internal scheduler will resume prefill and execution when mode is `internal`.
