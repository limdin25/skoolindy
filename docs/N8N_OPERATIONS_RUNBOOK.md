# Skoollindy n8n Operations Runbook

## How to pause safely

1. **In n8n:** Open **Skoollindy Scan and Queue (Cron)** and **Skoollindy Execute Queue (Cron)** → toggle workflow **Active** to **Off**. New runs stop; existing queue is unchanged.
2. **Optional (backend):** In Skoollindy UI, set **Master automation** to **Off**. Cron will still run but the scan/queue workflow will skip queueing when it sees `masterEnabled: false`.
3. **Optional (orchestration):** Set **Comment automation orchestration** to **Internal**. Cron execute will skip when it sees `orchestrationMode !== "n8n"`. Internal scheduler will not run comment automation until you switch back to n8n (by design).

**Recommended:** Turn off the n8n workflow(s) first so no new items are queued and no new executions run. Then decide whether to change Skoollindy settings.

---

## How to roll back to internal mode

1. **Skoollindy UI** → **Automation** (or Settings) → set **Comment automation orchestration** to **Internal**.
2. Confirm: `GET /automation/settings` or `GET /api/n8n/health` shows `orchestrationMode: "internal"`.
3. Internal scheduler will resume driving comment automation (prefill + execute) on its schedule. n8n cron workflows can stay installed but inactive, or you can deactivate them in n8n.

**No need to change backend code or DB.** Only the orchestration mode setting is switched.

---

## How to disable workflows fast

- **n8n UI:** Workflows → open each Skoollindy cron workflow → set **Active** to **Off** (or use n8n API to PATCH workflow `active: false`).
- **Immediate:** No new executions after the current run (if any) finishes. Queue is left as-is; nothing is deleted.

---

## What to check if sessions expire

- **Symptom:** scan-community or execute-comment returns `login_required` or similar in response body.
- **Check:** Skoollindy UI → **Accounts/Profiles** → confirm profile shows connected / cookies valid. Reconnect or paste cookies if needed.
- **n8n:** Scan/queue may skip that profile/community for that run; next run will retry. If all profiles fail, fix cookies then re-run manually or wait for next cron.

---

## What to check if comments stop posting

1. **orchestrationMode:** `GET /api/n8n/health` → must be `"n8n"` when using n8n cron. If it was switched to internal, either switch back to n8n or let internal drive.
2. **masterEnabled:** If false, scan/queue cron skips queueing.
3. **Queue:** `GET /queue` → are items present? If queue is empty, scan may be skipping (no eligible posts, or queue cap hit). If queue has items but comments don’t post, run execute-comment manually and check response `error` (e.g. login_required, editor_not_visible).
4. **Backend logs:** Look for `n8n_execute` and `success`/`error`. Playwright/session issues often show in logs.
5. **n8n runs:** Check execution history for Scan and Execute workflows; confirm they run and see skip reasons or errors.

---

## What to check if queue grows unexpectedly

1. **Execute cron:** Is **Skoollindy Execute Queue (Cron)** active and running every 5 min? If it’s off, queue will grow.
2. **Execute errors:** If execute-comment returns `success: false` (e.g. login_required), backend does not remove the item; queue stays high. Fix sessions and retry.
3. **Queue cap in Scan workflow:** Phase 1 queue cap (e.g. 2) should prevent scan from adding when queue is already at cap. If cap was raised or logic changed, verify the “skip when queue length >= cap” condition.
4. **Duplicate prevention:** Backend upserts by (profile_id, post_url), so duplicate URL for same profile should not create extra rows. If you see many similar post_urls, check for multiple profiles or different communities.

---

## Quick reference

| Action | Where |
|--------|--------|
| Pause n8n automation | n8n: workflow Active → Off |
| Rollback to internal | Skoollindy UI: orchestration → Internal |
| Check mode | `GET /api/n8n/health` → orchestrationMode |
| Check queue | `GET /queue` |
| Check backend logs | PM2 / skoolindy-backend logs; filter n8n_scan, n8n_queue, n8n_execute |
