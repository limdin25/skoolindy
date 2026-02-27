#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

PASS=0
FAIL=0

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [ "$expected" = "$actual" ]; then
        echo "  OK: $label (expected=$expected, got=$actual)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (expected=$expected, got=$actual)"
        FAIL=$((FAIL + 1))
    fi
}

assert_ne() {
    local label="$1" unexpected="$2" actual="$3"
    if [ "$unexpected" != "$actual" ]; then
        echo "  OK: $label (not=$unexpected, got=$actual)"
        PASS=$((PASS + 1))
    else
        echo "  FAIL: $label (should not be $unexpected, got=$actual)"
        FAIL=$((FAIL + 1))
    fi
}

# Force-save in-memory state to disk by stopping the engine.
# This writes self._state.profiles (with _current_community_index) to skool_run_state.json.
force_save_and_read() {
    curl -sS -X POST http://127.0.0.1:3103/automation/stop >/dev/null 2>&1 || true
    sleep 1
    python3 -c '
import json, datetime
s = json.load(open("skool_run_state.json"))
today = datetime.date.today().isoformat()
state_day = s.get("state_day", "MISSING")
indices = []
statuses = []
visits = []
for p in s.get("profiles", []):
    indices.append(str(p.get("_current_community_index", "MISSING")))
    statuses.append(str(p.get("status", "MISSING")))
    visits.append(str(p.get("visitsCompleted", "MISSING")))
print("state_day=" + state_day)
print("indices=" + ",".join(indices))
print("statuses=" + ",".join(statuses))
print("visits=" + ",".join(visits))
print("today=" + today)
'
}

echo "========================================="
echo "REGRESSION: community rotation across days"
echo "========================================="

echo ""
echo "[1/4] Injecting _current_community_index=5, state_day=today, run_state=paused..."
python3 -c '
import json, datetime
p = "skool_run_state.json"
s = json.load(open(p))
s["state_day"] = datetime.date.today().isoformat()
s["run_state"] = "paused"
for prof in s.get("profiles", []):
    prof["_current_community_index"] = 5
    prof["status"] = "idle"
    prof["visitsCompleted"] = 0
    prof["repliesCompleted"] = 0
json.dump(s, open(p, "w"))
print("OK: injected _current_community_index=5, state_day=" + s["state_day"] + ", run_state=paused")
'

echo ""
echo "[2/4] Restarting PM2 (same-day restart test)..."
pm2 restart engageflow-backend >/dev/null 2>&1
sleep 6

echo "  Stopping engine to force-save recovered in-memory state..."
RESULT=$(force_save_and_read)
echo "$RESULT"

INDICES=$(echo "$RESULT" | grep "^indices=" | cut -d= -f2)
IFS=',' read -ra IDXARR <<< "$INDICES"
for i in "${!IDXARR[@]}"; do
    assert_eq "profile[$i] index after same-day restart" "5" "${IDXARR[$i]}"
done

echo ""
echo "[3/4] Injecting stale state_day=1999-01-01 (day-boundary test)..."
python3 -c '
import json
p = "skool_run_state.json"
s = json.load(open(p))
s["state_day"] = "1999-01-01"
s["run_state"] = "paused"
for prof in s.get("profiles", []):
    prof["_current_community_index"] = 5
    prof["status"] = "finished"
    prof["visitsCompleted"] = 99
    prof["repliesCompleted"] = 99
json.dump(s, open(p, "w"))
print("OK: injected stale day + finished statuses + index=5")
'

# Zero DB dailyUsage to simulate post-reset state
sqlite3 engageflow.db "UPDATE profiles SET dailyUsage = 0;"
echo "  Zeroed DB dailyUsage"

echo "  Restarting PM2 (stale-day recovery test)..."
pm2 restart engageflow-backend >/dev/null 2>&1
sleep 6

echo "  Stopping engine to force-save recovered in-memory state..."
RESULT=$(force_save_and_read)
echo "$RESULT"

STATE_DAY=$(echo "$RESULT" | grep "^state_day=" | cut -d= -f2)
INDICES=$(echo "$RESULT" | grep "^indices=" | cut -d= -f2)
STATUSES=$(echo "$RESULT" | grep "^statuses=" | cut -d= -f2)
VISITS=$(echo "$RESULT" | grep "^visits=" | cut -d= -f2)
TODAY=$(echo "$RESULT" | grep "^today=" | cut -d= -f2)

assert_eq "state_day is today" "$TODAY" "$STATE_DAY"

# Rotation pointers must survive day boundary
IFS=',' read -ra IDXARR2 <<< "$INDICES"
for i in "${!IDXARR2[@]}"; do
    assert_eq "profile[$i] rotation index survived day boundary" "5" "${IDXARR2[$i]}"
done

# Daily counters must be reset (not 99)
IFS=',' read -ra VISITARR <<< "$VISITS"
for i in "${!VISITARR[@]}"; do
    assert_eq "profile[$i] visitsCompleted reset to 0" "0" "${VISITARR[$i]}"
done

# Statuses must not be "finished" (stale)
IFS=',' read -ra STATUSARR <<< "$STATUSES"
for i in "${!STATUSARR[@]}"; do
    assert_ne "profile[$i] status not finished" "finished" "${STATUSARR[$i]}"
done

echo ""
echo "[4/4] Restoring engine to running state for production..."
# After tests, run_state is "idle" (from stop). Set it back to "running" so recovery triggers.
python3 -c '
import json, datetime
p = "skool_run_state.json"
s = json.load(open(p))
s["run_state"] = "running"
s["state_day"] = datetime.date.today().isoformat()
json.dump(s, open(p, "w"))
print("OK: restored run_state=running for production")
'
pm2 restart engageflow-backend >/dev/null 2>&1
sleep 6
DEBUG=$(curl -sS http://127.0.0.1:3103/debug/scheduler)
echo "$DEBUG" | python3 -c '
import json, sys
d = json.load(sys.stdin)
print("  engine_running=" + str(d.get("engine_running")))
print("  state_day=" + str(d.get("state_day")))
print("  state_day_is_stale=" + str(d.get("state_day_is_stale")))
print("  profiles_active=" + str(d.get("counts", {}).get("profiles_active")))
'

ENGINE=$(echo "$DEBUG" | python3 -c 'import json,sys; print(str(json.load(sys.stdin).get("engine_running","")))')
assert_eq "engine_running after final restart" "True" "$ENGINE"

echo ""
echo "========================================="
echo "RESULT: $PASS passed, $FAIL failed"
if [ "$FAIL" -gt 0 ]; then
    echo "VERDICT: FAIL"
    exit 1
else
    echo "VERDICT: PASS"
fi
echo "========================================="
