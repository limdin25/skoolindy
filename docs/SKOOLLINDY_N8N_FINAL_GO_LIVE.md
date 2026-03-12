# Skoollindy n8n Final Go-Live Support Report

**Date:** 2026-03-10  
**Status:** Phase-1 target updated to community with eligible posts; full scan → queue → execute cycle proven. n8n activation must be confirmed in UI.

---

## A) n8n activation

**Required workflows:**

1. **skoollindy-scan-and-queue-cron** — trigger every 15 min, queue cap 2, max 1 queued per run  
2. **skoollindy-execute-queue-cron** — trigger every 5 min, 1 item per run  

**Confirmation:** This environment cannot access the n8n UI or n8n API (n8n is not on the same VPS). You must confirm in n8n:

- **Imported:** yes/no — In n8n → Workflows: both workflows appear (names: "Skoollindy Scan and Queue (Cron)", "Skoollindy Execute Queue (Cron)").
- **Active:** yes/no — Toggle **Active** is ON for both.
- **Workflow IDs:** Shown in n8n (e.g. in workflow URL or list); note them for support.

**Repo files to import if not already:**  
`n8n/skoollindy-scan-and-queue-cron.json`, `n8n/skoollindy-execute-queue-cron.json`.

---

## B) Variable check

**Required in n8n (Variables / Environment):**

- **SKOOLLINDY_BASE_URL** — Backend URL reachable from n8n (e.g. `http://<vps-ip>:3113` or `http://127.0.0.1:3113` if n8n is on same host).
- **SKOOLLINDY_N8N_KEY** — Must match backend `N8N_API_KEY` in `skoolindy/backend/.env`.

**Confirmation:** Check in n8n → Settings / Variables.  
**Present:** yes/no (confirm in UI).  
**Mismatch:** If scan or execute return 401, key is wrong or missing.

---

## C) Final Phase-1 target

**Previous target (0 eligible):**

- profile_id: `716e152e-eb1b-4282-9e9a-7eb8714a579d`  
- community_id: `860ffaa1-025d-4e3e-a4d3-d8ce3dcaa63c` (90 Day Business Launch 🚀)  
- Scan result: 5 posts, **0 eligible** (all blacklisted or already commented).

**Scan check on other communities (same profile):**

| community_id | name                     | posts_scanned | eligible |
|--------------|---------------------------|---------------|----------|
| d6db35f9-... | A BETTER ME               | 10            | 1        |
| cab1eb3e-... | Business Gurus Inner Circle | 10          | 8        |

**Final chosen target:**

- **community_id:** `d6db35f9-974d-44a8-ae3c-004e40d4807f`  
- **community name:** A BETTER ME  
- **Reason for switch:** Current community (90 Day Business Launch) had 0 eligible posts; A BETTER ME has at least 1 eligible post so Phase-1 can queue and execute.

**Using this target in n8n:**  
The cron workflow uses the **first** enabled profile and **first** community from `GET /api/n8n/runtime-config`. Right now the first community for that profile is still 90 Day Business Launch (order comes from backend/DB). To make A BETTER ME the Phase-1 target you can either:

- Reorder communities in the Skoollindy UI so "A BETTER ME" is first for that profile, or  
- Add n8n variables `SKOOLLINDY_CRON_PROFILE_ID` / `SKOOLLINDY_CRON_COMMUNITY_ID` and update the workflow to use them (workflow change).

Until then, if the first community in config stays 90 Day Business Launch, cron will scan it and often get 0 eligible; when you switch the first community to A BETTER ME (or pin via env in the workflow), cron will use the community with eligible posts.

---

## D) Cycle 1

- **posts scanned:** 10  
- **queued count:** 1 (one item queued via POST /api/n8n/queue-comment)  
- **executed count:** 1 (POST /api/n8n/execute-comment, success)  
- **queue size before:** 0 → after queue: 1 → after execute: 0  
- **skip reasons:** none  
- **errors:** none  
- **queue_item_id:** `ffc738cb-773a-4be3-b0a7-f684cc51f5f7`

---

## E) Cycle 2

- **posts scanned:** 10  
- **queued count:** 0 (no second queue in this simulation)  
- **executed count:** 0 (queue already empty)  
- **queue size:** 0  
- **skip reasons:** —  
- **errors:** none  

---

## F) Cycle 3

- **posts scanned:** 5  
- **queued count:** 0  
- **executed count:** 0  
- **queue size:** 0  
- **skip reasons:** —  
- **errors:** none  

---

## G) Queue health

- **Max queue size observed:** 1 (after queue-comment in cycle 1; cap is 2).  
- **Duplicates:** No. Single item was executed and removed; GET /queue showed 0 after execute.

---

## H) Execution success rate

- **Total queued (this run):** 1  
- **Total executed:** 1  
- **Total successful:** 1  

Backend log: `[QUEUE:EXECUTE][SUCCESS][hugords100+1@gmail.com] [SKOOL] n8n execute-comment success task=ffc738cb-773a-4be3-b0a7-f684cc51f5f7`.

---

## I) Recommendation

- **Continue Phase-1:** Yes. Backend is ready; scan → queue → execute is proven with one profile and one community (A BETTER ME).  
- **n8n:** Confirm in UI that both cron workflows are imported and Active, and that `SKOOLLINDY_BASE_URL` and `SKOOLLINDY_N8N_KEY` are set.  
- **Target:** Prefer scanning a community with eligible posts. Set "A BETTER ME" as the first community for the profile in Skoollindy (or pin it in n8n) so the 15‑min cron consistently has a chance to queue and execute.  
- **Expand to Phase-2:** Only after 3+ real cron cycles run with workflows active and no duplicate items / no queue over cap.

---

## Log + UI verification

- **GET /queue:** Checked after queue and after execute; queue length 1 then 0.  
- **Backend logs:** `POST /api/n8n/queue-comment -> 200`, `POST /api/n8n/execute-comment -> 200`, `[QUEUE:EXECUTE][SUCCESS]`. No ERROR in sampled lines.  
- **Duplicates:** None; one item created and one consumed.
