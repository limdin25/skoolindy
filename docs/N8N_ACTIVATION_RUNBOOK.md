# Skoollindy n8n Activation Runbook (Phase-1 Production)

**Backend:** http://38.242.229.161:3113  
**n8n UI:** http://38.242.229.161:5678  

---

## Step 1 — Connect to n8n

1. Open **http://38.242.229.161:5678** in a browser.
2. Log in if prompted.
3. Confirm the workspace loads and the workflows list is visible (may be empty).

---

## Step 2 — Create n8n API key (for scripted import)

1. In n8n: **Settings** (gear) → **n8n API**.
2. **Create an API key** (label e.g. "Skoollindy activation", expiration as needed).
3. Copy the key and keep it secret. You will use it as `N8N_INSTANCE_API_KEY`.

---

## Step 3 — Set environment variables (n8n server)

n8n workflows use `$env.SKOOLLINDY_BASE_URL` and `$env.SKOOLLINDY_N8N_KEY`. **`$env.*` reads the OS/process environment of the server where n8n runs, not UI variables.**

- **If n8n runs in Docker** (as on this VPS): add the variables to the container when creating/starting it, e.g.:
  `-e SKOOLLINDY_BASE_URL=http://38.242.229.161:3113 -e SKOOLLINDY_N8N_KEY=<backend N8N_API_KEY>`
  Then restart: `docker stop n8n && docker rm n8n` and re-run `docker run` with the same options plus these `-e` flags (see `N8N_SET_VARIABLES_NOW.md`).
- **If n8n runs under systemd/pm2:** set the variables in the service environment or in `/etc/environment` (then restart n8n).
- Backend key: `grep N8N_API_KEY /root/.openclaw/workspace/skoolindy/backend/.env` on the VPS.

---

## Step 4 — Import and activate workflows (script)

From the repo root (with `jq` installed):

```bash
export N8N_INSTANCE_API_KEY="<your-n8n-api-key>"
./scripts/n8n-activate-skoollindy.sh
```

The script will:

- Create workflow **Skoollindy Scan and Queue (Cron)** and set it **Active**.
- Create workflow **Skoollindy Execute Queue (Cron)** and set it **Active**.

Note the workflow IDs printed at the end.

**Alternative (manual import):**

1. In n8n: **Workflows** → **Add workflow** → **Import from file** (or paste).
2. Import `n8n/skoollindy-scan-and-queue-cron.json`.
3. Import `n8n/skoollindy-execute-queue-cron.json`.
4. Open each workflow and toggle **Active** ON.
5. Ensure variables above are set so HTTP nodes can reach the backend.

---

## Step 5 — Confirm schedules

- **Scan and Queue:** trigger **Every 15 minutes**.
- **Execute Queue:** trigger **Every 5 minutes**.

Check in the workflow editor that the first node shows the correct schedule.

---

## Step 6 — Force manual run (proof)

1. **Scan workflow:** Open **Skoollindy Scan and Queue (Cron)** → **Execute Workflow** (manual run).
   - Expected: GET health → GET config → GET queue → (if guards pass) POST scan-community → if eligible post → POST queue-comment.
2. **Execute workflow:** Open **Skoollindy Execute Queue (Cron)** → **Execute Workflow**.
   - Expected: GET health → GET queue → if item exists → POST execute-comment.

Check **Executions** in n8n for success and any errors.

---

## Step 7 — Verify automation

1. **Backend health:** `curl -s -H "X-N8N-KEY: <key>" http://38.242.229.161:3113/api/n8n/health`
2. **Queue:** `curl -s http://38.242.229.161:3113/queue`
3. **Backend logs:** On VPS, `pm2 logs skoolindy-backend --lines 50` and look for `scan-community`, `queue-comment`, `execute-comment`, `QUEUE:EXECUTE`, `ERROR`.
4. **Skool:** Confirm the comment appears on the target post in the community.

---

## Troubleshooting

| Symptom | Check |
|--------|--------|
| 401 on backend from n8n | `SKOOLLINDY_N8N_KEY` in n8n must equal `N8N_API_KEY` in backend `.env`. |
| Scan returns no eligible posts | First community in config may have 0 eligible; switch to a community with eligible posts (e.g. A BETTER ME) or reorder in Skoollindy. |
| Execute does nothing | Queue may be empty; run Scan first and ensure at least one item is queued. |
| n8n API 401 | Use the key from **Settings > n8n API**, not the backend key. |
