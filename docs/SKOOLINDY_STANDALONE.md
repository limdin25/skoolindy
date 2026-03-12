# Skoolindy is standalone (no EngageFlow repo)

**There is no separate EngageFlow service or repo you must link to** to run Skoolindy. One app, one repo: **[limdin25/skoolindy](https://github.com/limdin25/skoolindy)**.

## What “EngageFlow” still means in this codebase (legacy only)

| Thing | What it is | Action |
|-------|------------|--------|
| **`backend/engageflow.db`** | SQLite filename kept from earlier lineage | Optional future rename; today’s code and `ENGAGEFLOW_DB_PATH` expect this path or env override. |
| **`ENGAGEFLOW_DB_PATH`** | Env var pointing at that SQLite file | Legacy name; set it to your Skoolindy DB path (e.g. `/root/.openclaw/workspace/skoolindy/backend/engageflow.db`). |
| **`useEngageFlow.ts`** (frontend) | React Query hooks filename | Legacy name only; no dependency on another product. |
| **Root `ENGAGEFLOW_*.md`, `TRANSFER.md`, etc.** | Old handoff / continuity docs | Historical; **canonical process docs are `docs/README.md` and siblings.** Don’t use old PM2 names (`engageflow-backend`) — use `skoolindy-backend`. |

## PM2 / paths (current)

- **Backend:** `skoolindy-backend` — cwd should be Skoolindy backend dir.
- **Frontend:** `skoolindy-frontend`
- **Joiner:** `skoolindy-joiner`
- **VPS root:** `/root/.openclaw/workspace/skoolindy`

## Summary

Skoolindy does **not** pull from or deploy alongside a separate EngageFlow project. Any “EngageFlow” string in code or docs is either a **legacy filename/env** or **historical markdown** — not a required external link.
