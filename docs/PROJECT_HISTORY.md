# PROJECT HISTORY — Skoolindy

Append-only log of significant changes.

---

## 2026-03-12 — Skoolindy naming + GitHub + n8n docs

- **Branding/docs:** DISCIPLINE, PROJECT_STATE, PROJECT_HISTORY aligned to **Skoolindy** (VPS paths, PM2 names, ports). EngageFlow referenced only as legacy/historical where useful.
- **GitHub:** Public repo [limdin25/skoolindy](https://github.com/limdin25/skoolindy); `main` tracks production snapshot; VPS branch `stable` can push as `stable:main`.
- **n8n:** Workflow JSON pack + runbooks pushed under `n8n/` and `docs/N8N_*.md`; `N8N_FULL_REPORT.md` as single reference. Secrets redacted from docs.

---

## 2026-03-02 — Hybrid Integration Complete (legacy EngageFlow naming)

**Phase 4–5: Frontend + PM2** (original handoff used EngageFlow naming; same codebase lineage as Skoolindy.)

- **AccountsTab**: Patched for hybrid — removed Add Account and Delete Account buttons. Profiles read from backend via joiner API.
- **QueueTab, SurveyTab, LogsTab**: Switched to joiner-api.
- **joiner-api.ts**: BASE set to `/api/joiner` for nginx proxy where used.
- **CommunitiesPage**: Join tab — Accounts, Survey Info, Communities & Queue, Live Logs.
- **nginx**: `location /api/joiner/` → proxy to joiner backend; rewrite strips prefix where configured.
- **PM2**: Joiner process (today: `skoolindy-joiner` on VPS).
- **Frontend**: Vite dev / build as per repo.

**Docs**: DISCIPLINE.md, PROJECT_STATE.md originally created under engageflow/docs/; later mirrored under Skoolindy.

---

## Earlier

- Automation engine, inbox, n8n orchestration mode, and queue/execute APIs evolved in `backend/app.py` and `backend/automation/engine.py`. See `docs/N8N_FULL_REPORT.md` for n8n contract.
