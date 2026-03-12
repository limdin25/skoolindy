# DISCIPLINE — Skoolindy + Joiner

Non-negotiable rules for the Skoolindy backend + joiner integration. **Skoolindy is standalone** — no separate EngageFlow repo (see `docs/SKOOLINDY_STANDALONE.md` for legacy filenames only).

## 1) Hybrid Architecture Invariants

- **Profiles**: Managed in **Skoolindy backend only**. Joiner READs profiles from `engageflow.db` (SQLite). No Add/Delete in joiner UI for core profiles.
- **Browser locks**: Skoolindy backend and joiner acquire/release locks before using `skool_accounts/`. No concurrent browser use.
- **Communities**: Joiner WRITES via webhook only (auto-register after successful join). Skoolindy backend owns `communities` table.
- **Joiner DB**: `join_queue`, profile discovery, join logs, joiner state — joiner owns. Skoolindy backend must not write joiner-only tables directly.

## 2) Minimal Diff, One Intent

- One logical change per deploy.
- No unrelated edits.

## 3) No Guessing

- If missing info blocks correctness, stop and request proof.

## 4) Security Checklist

- No secrets in code or logs (including n8n docs — use placeholders).
- Validate external inputs.
- Timeouts on HTTP and spawn.
- Never commit `.env`, `*.db`, or `venv/`.

## 5) Completion Standard

- `docs/PROJECT_STATE.md` updated after material changes.
- `docs/PROJECT_HISTORY.md` appended.

## 6) Source of Truth

- **Repo:** [github.com/limdin25/skoolindy](https://github.com/limdin25/skoolindy)
- **VPS root:** `/root/.openclaw/workspace/skoolindy`
- **Main DB:** `backend/engageflow.db` (name retained; app is Skoolindy)
