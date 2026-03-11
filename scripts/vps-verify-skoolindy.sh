#!/usr/bin/env bash
# Skoollindy VPS live verification script.
# Run on the VPS: bash scripts/vps-verify-skoolindy.sh
# Or: ssh root@38.242.229.161 "cd /root/.openclaw/workspace/skoolindy && bash scripts/vps-verify-skoolindy.sh"

set -e
REPO="${REPO:-/root/.openclaw/workspace/skoolindy}"
cd "$REPO"

echo "=== A) READ RECEIPT ==="
echo "--- Git ---"
git branch -v 2>/dev/null || true
git status -sb 2>/dev/null || true
git log -1 --oneline 2>/dev/null || true
echo "--- PM2 ---"
pm2 list 2>/dev/null || true
echo "--- Backend log (last 100 lines) ---"
pm2 logs skoolindy-backend --lines 100 --nostream 2>/dev/null || tail -100 ~/.pm2/logs/skoolindy-backend-out.log 2>/dev/null || true
echo "--- Backend port 3113 ---"
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:3113/health 2>/dev/null || echo "fail"
echo ""
echo "--- Frontend port 4014 ---"
curl -s -o /dev/null -w "%{http_code}" http://127.0.0.1:4014/ 2>/dev/null || echo "fail"
echo ""

echo "=== B) BACKUP ==="
TS=$(date +%Y%m%d-%H%M%S)
BACKEND_DB="${REPO}/backend/engageflow.db"
if [ -f "$BACKEND_DB" ]; then
  cp -a "$BACKEND_DB" "${BACKEND_DB}.backup-${TS}"
  echo "Backed up: ${BACKEND_DB}.backup-${TS}"
else
  echo "DB not found at $BACKEND_DB; checking backend dir..."
  ls -la "${REPO}/backend/"*.db 2>/dev/null || true
fi

echo ""
echo "=== D) API VERIFICATION (localhost:3113) ==="
BASE="http://127.0.0.1:3113"
# If N8N_API_KEY is set in env, pass it
HDR=""
if [ -n "$N8N_API_KEY" ]; then HDR="-H X-N8N-KEY: $N8N_API_KEY"; fi

echo "GET /api/n8n/health"
curl -s -w "\nHTTP %{http_code}\n" $HDR "$BASE/api/n8n/health" | tail -5

echo "GET /api/n8n/runtime-config"
curl -s -w "\nHTTP %{http_code}\n" $HDR "$BASE/api/n8n/runtime-config" | tail -3

echo "GET /automation/settings"
curl -s -w "\nHTTP %{http_code}\n" "$BASE/automation/settings" | tail -3

echo "GET /queue"
curl -s -w "\nHTTP %{http_code}\n" "$BASE/queue" | tail -3

echo ""
echo "=== Done ==="
