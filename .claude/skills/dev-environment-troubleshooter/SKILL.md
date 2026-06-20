# Dev Environment Troubleshooter Skill

## Role

You are the development environment troubleshooter for InvestingBuddy.

Your job is to diagnose and fix local dev environment problems: broken installs, missing environment files, port conflicts, Docker issues, database connectivity failures, and dependency mismatches.

You are systematic: check prerequisites, identify root causes, fix them, then verify the stack is healthy before declaring done.

---

## Diagnostic Sequence

When something is broken, follow this order:

### 1. Check Docker

```bash
docker info >/dev/null 2>&1 && echo "Docker: OK" || echo "Docker: NOT RUNNING"
docker compose ps
```

**Docker not running:** `open -a Docker` → wait ~15s → retry.

**Container stopped unexpectedly:**
```bash
docker compose logs postgres --tail=50
docker compose up -d
```

### 2. Check PostgreSQL Connectivity

```bash
docker compose exec -T postgres pg_isready -U investingbuddy
```

Expected: `localhost:5432 - accepting connections`

If not ready, check:
```bash
docker compose logs postgres --tail=20
```

Common causes:
- First launch: give it 10–20 seconds
- Port 5432 occupied by local Postgres: `lsof -i :5432` — kill conflicting process or change `docker-compose.yml` port mapping

### 3. Check Python venv

```bash
ls apps/api/.venv/bin/uvicorn 2>/dev/null && echo "venv OK" || echo "venv MISSING"
```

**Missing venv:**
```bash
cd apps/api
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

**Installed but packages missing:**
```bash
cd apps/api && .venv/bin/pip install -e ".[dev]"
```

### 4. Check Environment File

```bash
ls apps/api/.env 2>/dev/null && echo ".env OK" || echo ".env MISSING"
```

**Missing:**
```bash
cp .env.example apps/api/.env
```

Then verify the database URL in `.env` matches the Docker Compose service:
```
DATABASE_URL=postgresql+asyncpg://investingbuddy:investingbuddy@localhost:5432/investingbuddy
```

### 5. Check Migrations

```bash
cd apps/api && .venv/bin/alembic current
```

If behind:
```bash
cd apps/api && .venv/bin/alembic upgrade head
```

### 6. Check Node / Frontend

```bash
ls apps/web/node_modules/.bin/next 2>/dev/null && echo "node_modules OK" || echo "node_modules MISSING"
```

**Missing:**
```bash
cd apps/web && npm install
```

Check `.env.local`:
```bash
ls apps/web/.env.local 2>/dev/null || echo "NEXT_PUBLIC_API_BASE_URL=http://localhost:8000" > apps/web/.env.local
```

---

## Port Conflict Resolution

```bash
# Find and kill process on port 8000 (API)
lsof -i :8000
kill -9 $(lsof -ti:8000) 2>/dev/null

# Find and kill process on port 3000 (frontend)
lsof -i :3000
kill -9 $(lsof -ti:3000) 2>/dev/null

# Find and kill process on port 5432 (local postgres, not Docker)
lsof -i :5432
```

---

## Common Problems and Fixes

| Symptom | Root Cause | Fix |
|---|---|---|
| `Cannot connect to Docker daemon` | Docker Desktop not running | `open -a Docker` |
| `connection refused 5432` | PostgreSQL not ready | Wait and retry; check `docker compose logs postgres` |
| `uvicorn: command not found` | Using system Python, not venv | Use `.venv/bin/uvicorn` |
| `ModuleNotFoundError: No module named 'app'` | Not in `apps/api/` directory or not installed as package | `cd apps/api && .venv/bin/pip install -e .` |
| `alembic.util.exc.CommandError: Can't locate revision` | Migrations out of sync | `alembic upgrade head` |
| API returns `{"detail":"Not Found"}` on `/health` | Wrong port or wrong path | Confirm `http://localhost:8000/health` not `http://localhost:8000/` |
| `EADDRINUSE 3000` | Next.js still running from a previous session | `kill $(lsof -ti:3000)` |
| `npm run dev` hangs | Node version mismatch | Check `node -v` — needs 22+ |
| `ruff: command not found` | Using wrong Python | `cd apps/api && .venv/bin/ruff check .` |
| `pytest` finds 0 tests | Wrong working directory | Run from `apps/api/` |
| `.env` changes not picked up | Uvicorn didn't restart | Restart uvicorn (Ctrl-C and rerun) |

---

## Environment Sanity Check Script

Run this to get a full system status in one go:

```bash
echo "=== DOCKER ===" && docker info >/dev/null 2>&1 && echo "OK" || echo "NOT RUNNING"
echo "=== POSTGRES ===" && docker compose exec -T postgres pg_isready -U investingbuddy 2>/dev/null || echo "NOT READY"
echo "=== VENV ===" && ls apps/api/.venv/bin/uvicorn 2>/dev/null && echo "OK" || echo "MISSING"
echo "=== .ENV ===" && ls apps/api/.env 2>/dev/null && echo "OK" || echo "MISSING"
echo "=== NODE ===" && ls apps/web/node_modules/.bin/next 2>/dev/null && echo "OK" || echo "MISSING"
echo "=== API PORT ===" && lsof -i :8000 2>/dev/null | head -2 || echo "free"
echo "=== WEB PORT ===" && lsof -i :3000 2>/dev/null | head -2 || echo "free"
```

---

## Rules

- Always fix root causes. Do not patch symptoms (e.g. deleting lock files without understanding why the lock exists).
- Do not delete the `.venv/` or `node_modules/` directory unless you are certain they are corrupt — a full reinstall takes time.
- Do not modify `.env.example` — it is a template committed to git; real values go in `.env`.
- Never commit `.env` or `.env.local`.
- If Docker Compose configuration changes are needed, ask before modifying `docker-compose.yml`.
