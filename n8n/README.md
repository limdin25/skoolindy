# Skoolindy n8n workflow pack

Import these JSON files into n8n (**Workflows → Import**).

| File | Trigger | Purpose |
|------|---------|--------|
| `skoollindy-healthcheck.json` | Manual | Health + runtime-config sanity check |
| `skoollindy-scan-and-queue.json` | Manual | Scan community → AI comment → queue |
| `skoollindy-scan-and-queue-cron.json` | Cron | Same as above on a schedule |
| `skoollindy-execute-queue.json` | Manual | Execute first queue item |
| `skoollindy-execute-queue-cron.json` | Cron (e.g. 5 min) | Execute when mode is n8n |
| `skoollindy-daily-reset.json` | Cron | POST reset-daily |

**Prerequisites**

- Backend `automation_settings.orchestrationMode` = `n8n` for execute cron guard.
- Env on n8n host: `SKOOLLINDY_BASE_URL`, `SKOOLLINDY_N8N_KEY` (see `docs/N8N_ENV_VARIABLES.md`).

**Activate via API**

```bash
export N8N_INSTANCE_API_KEY="..."   # n8n Settings → API
./scripts/n8n-activate-skoollindy.sh
```

Full API contract: `docs/N8N_FULL_REPORT.md`.
