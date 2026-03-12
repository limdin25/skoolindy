# Skoollindy n8n Phase-1 Production Activation Report

**Date:** 2026-03-10  
**Backend:** http://38.242.229.161:3113  
**n8n UI:** http://38.242.229.161:5678  

---

## What was done (automated)

- **n8n reachability:** Confirmed http://38.242.229.161:5678 returns HTTP 200. n8n API requires header `X-N8N-API-KEY` (401 without it).
- **Backend key:** Retrieved from VPS: `N8N_API_KEY` is set in `skoolindy/backend/.env`. This value must be set in n8n as **SKOOLLINDY_N8N_KEY** so workflows can call the backend.
- **Activation script:** Added `scripts/n8n-activate-skoollindy.sh`. With `N8N_INSTANCE_API_KEY` set (from n8n Settings > n8n API), the script imports both workflow JSONs via n8n API and sets them Active. Requires `jq`.
- **Runbook:** Added `docs/N8N_ACTIVATION_RUNBOOK.md` with steps: connect, create API key, set variables, run script (or manual import), confirm schedules, manual run, verify.

**Done via API (2026-03-10):** Using the provided n8n JWT/API key, both workflows were created and activated. **You must set in n8n UI:** **SKOOLLINDY_BASE_URL** = `http://38.242.229.161:3113` and **SKOOLLINDY_N8N_KEY** = backend `N8N_API_KEY` so the HTTP nodes can call the Skoollindy backend.

---

## Output (to fill after you activate)

### A) n8n workflow IDs

| Workflow | ID |
|----------|-----|
| Skoollindy Scan and Queue (Cron) | **NcXhooNZim17HBzg** |
| Skoollindy Execute Queue (Cron) | **zmq85jBqTU5fcd4z** |

---

### B) Confirmation both workflows ACTIVE

- [x] Skoollindy Scan and Queue (Cron) — **Active** in n8n  
- [x] Skoollindy Execute Queue (Cron) — **Active** in n8n  

*(Activated via API 2026-03-10.)*  

---

### C) First execution results

**Scan workflow (manual run):**

- GET /api/n8n/health: _  
- GET /api/n8n/runtime-config: _  
- GET /queue: _  
- POST /api/n8n/scan-community: _  
- POST /api/n8n/queue-comment (if eligible): _  
- Errors: _  

**Execute workflow (manual run):**

- GET /queue: _  
- POST /api/n8n/execute-comment: _  
- Errors: _  

---

### D) Queue before / after

- **Before first Scan run:** _ (e.g. 0)  
- **After Scan run (if 1 queued):** _ (e.g. 1)  
- **After Execute run:** _ (e.g. 0)  

---

### E) Skool comment proof

- [ ] Comment visible on target post in community  
- Post URL: _  
- Community: _  

---

### F) Cron next run times

- **Scan (every 15 min):** next at :00, :15, :30, :45  
- **Execute (every 5 min):** next at :00, :05, :10, :15, …  

Note exact next run in n8n **Executions** or schedule view if available: _  

---

### G) Any errors

- n8n execution errors: _  
- Backend log errors (scan/queue/execute): _  
- 401/5xx from backend: _  

---

## Goal checklist

- [ ] Both workflows imported and **Active** in n8n  
- [ ] Variables **SKOOLLINDY_BASE_URL** and **SKOOLLINDY_N8N_KEY** set in n8n  
- [ ] One successful manual Scan run (and Queue if eligible)  
- [ ] One successful manual Execute run when queue had an item  
- [ ] Skoollindy fully driven by n8n cron: scan → queue → execute → comment, no manual API calls required  

---

## Quick commands (after activation)

**Backend health:**
```bash
curl -s -H "X-N8N-KEY: YOUR_BACKEND_KEY" http://38.242.229.161:3113/api/n8n/health
```

**Queue:**
```bash
curl -s http://38.242.229.161:3113/queue
```

**Run activation script (from repo root, after creating n8n API key):**
```bash
export N8N_INSTANCE_API_KEY="<from n8n Settings > n8n API>"
./scripts/n8n-activate-skoollindy.sh
```
