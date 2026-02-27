#!/usr/bin/env bash
set -euo pipefail

echo "Cleaning runtime/cache artifacts..."

rm -rf backend/logs/* || true
rm -rf backend/skool_accounts/* || true
rm -rf backend/__pycache__ || true
rm -rf frontend/dist || true
rm -f _engine_diff.txt || true

mkdir -p backend/logs backend/skool_accounts

echo "Cleanup complete."
