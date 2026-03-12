# n8n environment variables (safe reference)

**Do not commit real keys.** Set these in the n8n process or Docker container (same as `$env.*` in workflows).

| Variable | Required | Description |
|----------|----------|-------------|
| `SKOOLLINDY_BASE_URL` | Yes | Backend base URL, e.g. `http://your-host:3113` — no trailing slash |
| `SKOOLLINDY_N8N_KEY` | Yes if backend has key | Must equal backend `N8N_API_KEY` or `SKOOLLINDY_N8N_API_KEY` — sent as `X-N8N-KEY` |
| `OPENAI_API_KEY` | Optional | For AI node in scan-and-queue workflow |
| `SKOOLLINDY_TEST_PROFILE_ID` | Optional | Override profile for manual test |
| `SKOOLLINDY_TEST_COMMUNITY_ID` | Optional | Override community for manual test |

**Docker example (placeholders only):**

```bash
-e SKOOLLINDY_BASE_URL=http://localhost:3113 \
-e SKOOLLINDY_N8N_KEY=<your-backend-N8N_API_KEY>
```

See `docs/N8N_FULL_REPORT.md` and `docs/N8N_WORKFLOW_ROLLOUT_PLAN.md` for full setup.
