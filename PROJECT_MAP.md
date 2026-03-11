# EngageFlow – Project Map (Auto-Generated)

## 1. Runtime Architecture
- Backend entrypoint:
- Scheduler loop location:
- Queue system location:
- Comment execution flow:
- Inbox sync flow:
- Blacklist logic:
- Rotation pointer logic:

## 2. Persistence
- SQLite schema summary
- run_state.json structure
- skool_global_blacklist.json structure

## 3. API Surface
List all FastAPI routes with:
- Method
- Path
- Purpose
- Read-only or Mutating

## 4. Scheduler Flow (Step-by-step)
From tick start → profile selection → community selection → queue prefill → execution → persistence.

## 5. Critical Invariants
List all enforced invariants and where in code they are protected.

## 6. Config Settings
All automation_settings keys and meaning.

## 7. Lock Tags
List existing git tags and what each protects.

## 8. Known Limits
Daily caps, scan intervals, queue limits.

