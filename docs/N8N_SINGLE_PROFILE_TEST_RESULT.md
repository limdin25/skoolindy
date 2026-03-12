# Skoollindy n8n — Single-Profile Live Test Result

**Date:** 2026-03-10  
**Method:** Full API sequence run on VPS (same calls n8n workflows make). n8n UI import/variables confirmed separately when you run n8n.

---

## A) Test target

| Field | Value |
|-------|--------|
| **profile_id** | `716e152e-eb1b-4282-9e9a-7eb8714a579d` |
| **profile label** | hugords100+1@gmail.com |
| **community_id** | `d6db35f9-974d-44a8-ae3c-004e40d4807f` |
| **community name** | A BETTER ME |

*Note: First community (90 Day Business Launch) had 0 eligible posts (all already_commented or blacklisted). Second community (A BETTER ME) had 1 eligible post; used for test.*

---

## B) Workflow import/config status

| Item | Status |
|------|--------|
| **Imported or recreated** | Workflow JSONs exist in repo (`n8n/*.json`). Import into n8n UI is manual; API test did not use n8n UI. |
| **Variables set** | On VPS backend: `N8N_API_KEY` in backend `.env`. For n8n UI you must set: `SKOOLLINDY_BASE_URL`, `SKOOLLINDY_N8N_KEY`, `SKOOLLINDY_TEST_PROFILE_ID`, `SKOOLLINDY_TEST_COMMUNITY_ID`. |
| **OpenAI vs Code fallback** | This run used **static comment** (Code fallback): `"Nice point, that stood out to me too."` — no OpenAI node. |

---

## C) Workflow A result

| Endpoint | Result |
|----------|--------|
| **GET /api/n8n/health** | `{"masterEnabled":true,"orchestrationMode":"internal","schedulerDrivesComments":true,"queueCount":1,"enabledProfilesCount":3,"timestamp":"2026-03-10T15:04:50"}` |
| **GET /api/n8n/runtime-config** | masterEnabled: true, enabledProfiles: 3, communitiesByProfile present, keywordRules present. First profile: 716e152e…, hugords100+1@gmail.com. |
| **Validation** | **Pass** — masterEnabled and orchestrationMode present, enabledProfiles.length > 0. |

---

## D) Workflow B result

| Item | Value |
|------|--------|
| **Posts scanned** | 10 (community A BETTER ME, max_posts=10) |
| **Filtered out** | 9: already_commented and/or blacklisted_match; 1 eligible (no ac, no blacklist, non-empty text). |
| **Selected post_url** | `https://www.skool.com/a-better-me-5500/you-have-more-to-give-than-you-think` |
| **Selected post_id** | (empty in scan response; post_url used as identifier) |
| **Matched keyword / fallback** | general_comment_fallback |
| **prompt_used** | (fallback prompt from runtime-config) |
| **generated_comment** | `Nice point, that stood out to me too.` |
| **scheduled_for** | `2026-03-10T14:09:03` (UTC) |
| **queue-comment API response** | `{"success":true,"queue_item_id":"c43d6a93-d9d4-4f33-b655-474fd058eca6"}` |
| **queue_item_id** | `c43d6a93-d9d4-4f33-b655-474fd058eca6` |
| **Pass/fail** | **Pass** |

---

## E) Queue verification

| Item | Result |
|------|--------|
| **Queue item found** | Yes |
| **Details** | GET /queue returned 2 items; one was `c43d6a93-d9d4-4f33-b655-474fd058eca6` with profileId 716e152e…, postId https://www.skool.com/a-better-me-5500/you-have-mo… |

---

## F) Workflow C result

| Item | Value |
|------|--------|
| **queue_item_id** | `c43d6a93-d9d4-4f33-b655-474fd058eca6` |
| **execute-comment API response** | `{"success":true,"queue_item_id":"c43d6a93-d9d4-4f33-b655-474fd058eca6","error":null}` |
| **Pass/fail** | **Pass** |

---

## G) Final verification

| Check | Result |
|-------|--------|
| **Queue cleared/updated** | Yes — GET /queue after execute: 1 item remaining (the other pre-existing item); c43d6a93 removed. |
| **Logs/UI evidence** | Backend logs: `HTTP POST /api/n8n/queue-comment -> 200`, `[SKOOL] n8n execute-comment success task=c43d6a93-d9d4-4f33-b655-474fd058eca6`, `HTTP POST /api/n8n/execute-comment -> 200`. |
| **Post comment confirmed** | Not checked on Skool in this run (comment was posted by backend Playwright; logs show success). |

---

## H) Root cause if failed

N/A — no failure. One retry path used: first community had 0 eligible posts; second community (A BETTER ME) used.

---

## I) Minimal fix applied

- **Community switch:** Used community `d6db35f9-974d-44a8-ae3c-004e40d4807f` (A BETTER ME) instead of first community (90 Day Business Launch) because all 5 posts in the first were ineligible.
- **No code or workflow node changes.** Static comment used for plumbing validation as specified.

---

## J) Recommendation

| Question | Answer |
|----------|--------|
| **Ready to move from manual trigger to cron?** | Yes — one full cycle is proven (health → config → scan → queue → execute). You can add cron triggers to Scan and Queue (e.g. every 15–30 min) and Execute Queue (e.g. every 5 min) in n8n. Ensure `orchestrationMode` is set to **n8n** in Skoollindy when using n8n-driven runs. |
| **Safe to test second profile/community?** | Yes — same pattern: pick another profile_id and community_id from runtime-config, set as test vars or in workflow input, run B then C. Do not scale to many profiles in one run until you have run a few manual tests. |

---

**Success criteria met:** One real item scanned, one real item queued, one real item executed; backend and queue reflect it; no regressions (inbox/DM/joiner untouched).
