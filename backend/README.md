# Backend (FastAPI + Automation Engine)

## Requirements

- Python 3.11+
- `pip`

## Run Locally

```sh
cd backend
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

API root: `http://localhost:8000`

## Run in Docker (from project root)

```sh
docker compose up --build -d backend
```

## Environment

Main config file: `backend/.env`

Important blocks:
- automation/scheduler settings
- chat background sync settings
- logging settings

After any `.env` update, restart backend container/service.

## Runtime Data

Created at runtime:
- `backend/engageflow.db`
- `backend/skool_accounts/`
- `backend/logs/`
- `backend/skool_run_state.json`

## Cleanup (runtime only)

```powershell
Get-ChildItem backend/logs -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem backend/skool_accounts -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item backend/__pycache__ -Recurse -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path backend/logs -Force | Out-Null
New-Item -ItemType Directory -Path backend/skool_accounts -Force | Out-Null
```
