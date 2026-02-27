#!/bin/bash
cd /root/.openclaw/workspace/engageflow/backend
source venv/bin/activate
exec uvicorn app:app --host 0.0.0.0 --port 3103
