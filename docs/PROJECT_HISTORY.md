# PROJECT HISTORY — EngageFlow + Joiner

Append-only log of significant changes.

---

## 2026-03-02 — Hybrid Integration Complete

**Phase 4–5: Frontend + PM2**

- **AccountsTab**: Patched for hybrid — removed Add Account and Delete Account buttons. Profiles read from EngageFlow via joiner API. Uses joiner-api and ./StatusBadge.
- **QueueTab, SurveyTab, LogsTab**: Switched to joiner-api (./joiner-api).
- **joiner-api.ts**: BASE set to `/api/joiner` for nginx proxy.
- **CommunitiesPage**: Join tab mock replaced with real 4-tab layout: Accounts, Survey Info, Communities & Queue, Live Logs.
- **nginx**: Added `location /api/joiner/` → proxy to joiner backend (3100). Rewrite strips `/api/joiner` prefix.
- **PM2**: Started `engageflow-joiner` from `engageflow/joiner/backend`.
- **Frontend**: Rebuilt successfully.

**Docs**: DISCIPLINE.md, PROJECT_STATE.md created in engageflow/docs/.
