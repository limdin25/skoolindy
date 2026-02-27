# Frontend (React + Vite)

## Requirements

- Node.js 18+
- npm

## Run Locally

```sh
cd frontend
npm i
npm run dev
```

Dev URL: `http://localhost:8080`

## Build

```sh
cd frontend
npm run build
npm run preview
```

## Docker (from project root)

```sh
docker compose up --build -d frontend
```

## Notes

- Nginx config: `frontend/nginx.conf`
- In docker mode, API calls are proxied via `/api/*`.

## Cleanup

```powershell
Remove-Item frontend/dist -Recurse -Force -ErrorAction SilentlyContinue
```
