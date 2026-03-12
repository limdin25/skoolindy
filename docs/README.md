# Skoolindy documentation (canonical)

**Single source of truth for how we run and change Skoolindy.**  
Edit here, then push to [github.com/limdin25/skoolindy](https://github.com/limdin25/skoolindy) — don’t maintain a second copy elsewhere.

| Doc | Purpose |
|-----|--------|
| **DISCIPLINE.md** | Non-negotiables (hybrid joiner, locks, no secrets) |
| **PROJECT_STATE.md** | Current objective, PM2 names, ports, paths, n8n pointer |
| **PROJECT_HISTORY.md** | Append-only changelog |
| **N8N_FULL_REPORT.md** | n8n API contract, JSON pack index, DB touchpoints |
| **N8N_ENV_VARIABLES.md** | Safe env var list (no real keys) |
| **N8N_*.md** / **SKOOLLINDY_N8N_*.md** | Runbooks, rollout, go-live |
| **SKOOLINDY_STANDALONE.md** | No separate EngageFlow repo; legacy names only (`engageflow.db`, `ENGAGEFLOW_DB_PATH`) |

**VPS sync (after GitHub updates):**

```bash
cd /root/.openclaw/workspace/skoolindy
git fetch origin && git checkout stable && git merge origin/main --no-edit || true
# or if tracking main:
git pull origin main
```

**OpenClaw workspace dumps** (`SKOOLINDY_DUMP_*`, `SKOOLINDY_ARCHITECTURE_DUMP_INDEX.md`) are snapshots for debugging — not the living process docs.
