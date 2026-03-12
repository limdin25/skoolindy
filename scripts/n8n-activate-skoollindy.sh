#!/usr/bin/env bash
# Activate Skoollindy Phase-1 in n8n: import both cron workflows and set them Active.
# Requires n8n instance API key (create in n8n: Settings > n8n API).
#
# Usage:
#   export N8N_INSTANCE_API_KEY="your-n8n-api-key"
#   ./scripts/n8n-activate-skoollindy.sh
#
# Optional:
#   export N8N_URL="http://38.242.229.161:5678"   # default
#   export SKOOLLINDY_REPO_DIR="."                # dir containing n8n/*.json

set -e
N8N_URL="${N8N_URL:-http://38.242.229.161:5678}"
REPO_DIR="${SKOOLLINDY_REPO_DIR:-$(cd "$(dirname "$0")/.." && pwd)}"
SCAN_JSON="${REPO_DIR}/n8n/skoollindy-scan-and-queue-cron.json"
EXEC_JSON="${REPO_DIR}/n8n/skoollindy-execute-queue-cron.json"

if [ -z "$N8N_INSTANCE_API_KEY" ]; then
  echo "Error: N8N_INSTANCE_API_KEY is required. Create one in n8n: Settings > n8n API."
  exit 1
fi

if [ ! -f "$SCAN_JSON" ] || [ ! -f "$EXEC_JSON" ]; then
  echo "Error: Workflow JSONs not found. SCAN=$SCAN_JSON EXEC=$EXEC_JSON"
  exit 1
fi

# Create workflow payload (name, nodes, connections, settings only)
create_payload() {
  jq -c '{ name, nodes, connections, settings: (.settings // {}) }' "$1"
}

# Create workflow via API and return workflow id
create_workflow() {
  local name="$1"
  local json="$2"
  local payload
  payload=$(create_payload "$json")
  curl -s -X POST "${N8N_URL}/api/v1/workflows" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -H "X-N8N-API-KEY: ${N8N_INSTANCE_API_KEY}" \
    -d "$payload"
}

# Activate workflow (n8n uses POST /workflows/:id/activate, not PATCH)
activate_workflow() {
  local id="$1"
  curl -s -X POST "${N8N_URL}/api/v1/workflows/${id}/activate" \
    -H "Content-Type: application/json" \
    -H "Accept: application/json" \
    -H "X-N8N-API-KEY: ${N8N_INSTANCE_API_KEY}" \
    -d '{}'
}

echo "n8n URL: $N8N_URL"
echo "---"

# 1) Scan and Queue workflow
echo "Creating workflow: Skoollindy Scan and Queue (Cron)..."
SCAN_RESP=$(create_workflow "Skoollindy Scan and Queue (Cron)" "$SCAN_JSON")
SCAN_ID=$(echo "$SCAN_RESP" | jq -r '.id // empty')
if [ -z "$SCAN_ID" ]; then
  echo "Create scan workflow failed: $SCAN_RESP"
  exit 1
fi
echo "  Created id: $SCAN_ID"
activate_workflow "$SCAN_ID" | jq -r '"  Active: " + (.active | tostring)'

# 2) Execute Queue workflow
echo "Creating workflow: Skoollindy Execute Queue (Cron)..."
EXEC_RESP=$(create_workflow "Skoollindy Execute Queue (Cron)" "$EXEC_JSON")
EXEC_ID=$(echo "$EXEC_RESP" | jq -r '.id // empty')
if [ -z "$EXEC_ID" ]; then
  echo "Create execute workflow failed: $EXEC_RESP"
  exit 1
fi
echo "  Created id: $EXEC_ID"
activate_workflow "$EXEC_ID" | jq -r '"  Active: " + (.active | tostring)'

echo "---"
echo "Done. Workflow IDs: Scan=$SCAN_ID Execute=$EXEC_ID"
echo "Set in n8n UI: SKOOLLINDY_BASE_URL=http://38.242.229.161:3113 and SKOOLLINDY_N8N_KEY=<backend N8N_API_KEY>"
