# PROJECT STATE — Skoolindy

**Last updated:** 2026-03-12  
**Max ~300 lines.**

---

## Current Objective

**Skoolindy** — automation for Skool engagement: FastAPI backend (Playwright scan/execute), Vite/React dashboard, Node joiner for community joins. Optional **n8n** orchestration when `automation_settings.orchestrationMode` is `n8n`.

---

## System Status (VPS reference)

| Component | PM2 name | Typical port | Notes |
|-----------|----------|--------------|--------|
| Backend | `skoolindy-backend` | 3113 | FastAPI, `backend/engageflow.db` |
| Frontend | `skoolindy-frontend` | 4014 | Vite dev (`npm run dev` in `frontend/`) |
| Joiner | `skoolindy-joiner` | (see joiner config) | Node `joiner/backend/server.js` |

**Paths on VPS:** `/root/.openclaw/workspace/skoolindy`  
**GitHub:** [github.com/limdin25/skoolindy](https://github.com/limdin25/skoolindy)

---

## Architecture

```text
skoolindy/   (repo root)
├── backend/           # FastAPI app.py, automation engine, engageflow.db
├── frontend/          # Vite + React dashboard
├── joiner/
│   └── backend/       # server.js, joiner DBs
├── n8n/               # Workflow JSON (import into n8n)
├── scripts/           # e.g. n8n-activate-skoollindy.sh
└── docs/              # DISCIPLINE, PROJECT_STATE, PROJECT_HISTORY, N8N_*
```

**Database (main):** `backend/engageflow.db` — profiles, communities, keyword_rules, queue_items, conversations, join_jobs, etc. (see `docs/N8N_FULL_REPORT.md` for n8n-related tables.)

**Joiner DBs:** under `joiner/backend/` — joiner-owned only.

---

## Hybrid Invariants (unchanged)

| Table / concern | Main DB | Joiner | Skoolindy backend |
|-----------------|--------|--------|-------------------|
| profiles | engageflow.db | READ | RW |
| browser_locks | engageflow.db | RW | RW |
| communities | engageflow.db | Webhook write | RW |
| join_queue / joiner state | joiner DB | RW | — |

---

## n8n (when orchestrationMode = n8n)

- Internal scheduler does not prefill/execute comment queue; n8n calls `/api/n8n/*` (see `docs/N8N_FULL_REPORT.md`).
- Env on n8n host: `SKOOLLINDY_BASE_URL`, `SKOOLLINDY_N8N_KEY` (must match backend `N8N_API_KEY` if set).

---

## Next Actions

1. Keep docs in sync when PM2 names, ports, or paths change.
2. After deploys: append PROJECT_HISTORY; refresh PROJECT_STATE if behavior or layout changes.
3. Rotate any exposed API keys; never commit real keys (use `docs/N8N_ENV_VARIABLES.md` pattern).
