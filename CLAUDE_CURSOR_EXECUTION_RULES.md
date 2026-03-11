---

# ENGAGEFLOW — EXECUTION GOVERNANCE RULES

## Role Separation

Cursor:
- Terminal operations
- File creation (docs, markdown)
- Git operations
- Logs inspection
- PM2 operations
- Build commands
- Nginx operations
- Environment inspection
- Human-facing documentation
- Non-code updates

Claude:
- Application code changes
- Backend implementation
- Database schema changes
- API endpoints
- Tests
- Refactors
- Scheduler logic
- Worker loops
- Playwright automation

---

## Mandatory Prompt Format

All instructions must include:

WHO:
HOW:
WHAT:

And must explicitly state:

- Scope boundaries
- What must NOT be modified
- STOP conditions

---

## Critical Rule

Markdown documents, planning specs, or architectural documents are NEVER implementation instructions.

Claude must NOT execute documentation unless explicitly instructed to implement Phase X.

---

## Default Safety Behavior

If instruction ambiguity exists:
- Claude must STOP and request clarification.
- Cursor must NOT assume implementation.

---

Status: Active
Last Updated: 2026-02-28
