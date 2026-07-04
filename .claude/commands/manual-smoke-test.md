# Command: manual-smoke-test

Run a manual smoke test against the running local stack.

## Pre-Conditions

- Stack is running (`launch-local` command completed successfully).

## Steps

1. Hit `/health` and confirm `{"status":"ok"}`.
2. Open `http://localhost:8000/docs` — Swagger UI must load.
3. Open `http://localhost:3000` — frontend must load without JS console errors.
4. Run the Phase 3.5 QA checklist from the manual-qa skill.
5. Run the regression checklist (test count, health, frontend 200).
6. Report any QA findings in the standard format.

## Skill to Use

Delegate to `.claude/skills/manual-qa/SKILL.md` for the full checklist and finding format.

## Definition of Done

- `/health` returns 200
- Swagger UI loads
- Frontend loads at port 3000
- Phase-specific QA checklist all items checked
- Regression checklist passed
- Any findings documented and filed
