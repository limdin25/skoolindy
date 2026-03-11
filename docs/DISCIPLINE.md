# DISCIPLINE — EngageFlow + Joiner Integration

Non-negotiable rules for the hybrid EngageFlow + community-join-manager integration.

## 1) Hybrid Architecture Invariants

- **Profiles**: Managed in EngageFlow only. Joiner READS profiles from engageflow.db. No Add/Delete in joiner UI.
- **Browser locks**: Both EngageFlow and joiner acquire/release locks before using skool_accounts/. No concurrent browser use.
- **Communities**: Joiner WRITES via webhook only (auto-register after successful join). EngageFlow owns communities table.
- **Joiner DB**: join_queue, profile_discovery_info, join_logs, joiner_profile_state — joiner owns. EngageFlow never touches.

## 2) Minimal Diff, One Intent

- One logical change per deploy.
- No unrelated edits.

## 3) No Guessing

- If missing info blocks correctness, stop and request proof.

## 4) Security Checklist

- No secrets in code or logs.
- Validate external inputs.
- Timeouts on HTTP and spawn.

## 5) Completion Standard

- docs/PROJECT_STATE.md updated after changes.
- docs/PROJECT_HISTORY.md appended.
