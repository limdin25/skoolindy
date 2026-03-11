#!/bin/bash
cd /root/.openclaw/workspace-margarita/engageflow/backend
source venv/bin/activate
exec python3 -m uvicorn app:app --host 0.0.0.0 --port 3103
