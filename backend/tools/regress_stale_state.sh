#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo "[1/4] Zeroing DB dailyUsage (simulate post-reset)..."
sqlite3 engageflow.db "UPDATE profiles SET dailyUsage = 0;"

echo "[2/4] Forcing stale state_day=1999-01-01..."
python3 -c '
import json
p="skool_run_state.json"
s=json.load(open(p))
s["state_day"]="1999-01-01"
for prof in s.get("profiles", []):
    prof["status"] = "finished"
    prof["visitsCompleted"] = 99
s["run_state"] = "running"
json.dump(s, open(p, "w"))
print("OK")
'

echo "[3/4] Restarting engageflow-backend..."
pm2 restart engageflow-backend >/dev/null 2>&1
sleep 5

echo "[4/4] Asserting debug shows healed state..."
curl -sS http://127.0.0.1:3103/debug/scheduler | python3 -c '
import json, sys, datetime
d = json.load(sys.stdin)
today = datetime.date.today().isoformat()
assert d.get("state_day") == today, "state_day mismatch: got %s" % d.get("state_day")
assert d.get("state_day_is_stale") is False, "state_day_is_stale=%s" % d.get("state_day_is_stale")
assert d.get("engine_running") is True, "engine_running=%s" % d.get("engine_running")
assert d.get("idle_reason") in (None, "paused"), "idle_reason=%s" % d.get("idle_reason")
assert d.get("counts", {}).get("profiles_active", 0) > 0, "profiles_active=0"
reason = d.get("last_recover_reason") or ""
assert reason.startswith("stale_day:"), "last_recover_reason=%s" % reason
print("PASS: all assertions OK")
print("  state_day=%s" % d["state_day"])
print("  profiles_active=%s" % d["counts"]["profiles_active"])
print("  last_recover_reason=%s" % d["last_recover_reason"])
'
