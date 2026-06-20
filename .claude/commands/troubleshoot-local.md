# Command: troubleshoot-local

Diagnose and fix a broken local development environment.

## When to Use

Use this command when:
- Docker, PostgreSQL, FastAPI, or Next.js fails to start
- Port conflicts block the stack
- Missing `.env`, venv, or `node_modules`
- Alembic migration errors
- `pytest` or `ruff` can't be found

## Steps

1. Run the environment sanity check to identify what is broken.
2. Work through the diagnostic sequence: Docker → PostgreSQL → venv → `.env` → migrations → Node.
3. Fix each issue in order (earlier layers must be healthy before later ones).
4. Re-run the sanity check to confirm everything is green.
5. Attempt to start the stack with `launch-local`.

## Skill to Use

Delegate to `.claude/skills/dev-environment-troubleshooter/SKILL.md` for the full diagnostic sequence, common fixes, and sanity check script.

## Definition of Done

- Environment sanity check reports OK for all components
- Stack starts and passes `manual-smoke-test`
