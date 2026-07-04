# Command: launch-local

Start the full local development stack.

## Steps

1. Check prerequisites (Docker, venv, node_modules, .env file).
2. Start PostgreSQL via Docker Compose.
3. Wait for PostgreSQL to be ready.
4. Run `alembic upgrade head` to apply any pending migrations.
5. Start FastAPI backend on port 8000 (background).
6. Start Next.js frontend on port 3000 (background).
7. Confirm both services respond.

## Skill to Use

Delegate to `.claude/skills/local-dev-operator/SKILL.md` for exact commands and port handling.

## Definition of Done

- `curl http://localhost:8000/health` returns `{"status":"ok",...}`
- `http://localhost:3000` returns HTTP 200
- No errors in the terminal output
