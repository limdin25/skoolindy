# SkoolProjectUP

Automation platform for Skool workflows:
- backend: FastAPI API + automation engine
- frontend: React/Vite dashboard

## Project Structure

```text
.
|- backend/
|  |- README.md
|- frontend/
|  |- README.md
|- docker-compose.yml
`- README.md
```

## Quick Start (Docker, recommended)

From repo root:

```sh
docker compose up --build -d
```

Open:
- UI: `http://localhost`
- API through nginx: `http://localhost/api/...`

Stop:

```sh
docker compose down
```

## Local Dev (without Docker)

Backend:

```sh
cd backend
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000 --reload
```

Frontend:

```sh
cd frontend
npm i
npm run dev
```

## Cleanup Before Handover

Run from repo root to clean runtime/cache artifacts:

```sh
# Linux/macOS
rm -rf backend/logs/* backend/skool_accounts/* backend/__pycache__ frontend/dist _engine_diff.txt
mkdir -p backend/logs backend/skool_accounts
```

```powershell
# Windows PowerShell
Get-ChildItem backend/logs -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Get-ChildItem backend/skool_accounts -Force | Remove-Item -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item backend/__pycache__ -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item frontend/dist -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item _engine_diff.txt -Force -ErrorAction SilentlyContinue
New-Item -ItemType Directory -Path backend/logs -Force | Out-Null
New-Item -ItemType Directory -Path backend/skool_accounts -Force | Out-Null
```

Or use prepared scripts:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/cleanup_handover.ps1
```

```sh
bash scripts/cleanup_handover.sh
```

## Extra Docs

- Backend setup/details: `backend/README.md`
- Frontend setup/details: `frontend/README.md`
