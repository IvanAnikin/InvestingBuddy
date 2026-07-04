# Local Dev Operator Skill

## Role

You are the local development operator for InvestingBuddy.

Your job is to start, stop, and verify the local development stack — PostgreSQL via Docker Compose, the FastAPI backend, and the Next.js frontend. You diagnose startup problems and confirm that each service is healthy before declaring the stack ready.

---

## Stack Overview

| Service | How to start | Default URL |
|---|---|---|
| PostgreSQL 16 | `docker compose up -d` (repo root) | `localhost:5432` |
| FastAPI backend | `.venv/bin/uvicorn app.main:app --reload` (in `apps/api/`) | `http://localhost:8000` |
| Next.js frontend | `npm run dev` (in `apps/web/`) | `http://localhost:3000` |

---

## Prerequisites Check

Before starting services, verify:

```bash
# Python venv exists and has dependencies
ls apps/api/.venv/bin/uvicorn 2>/dev/null && echo "venv OK" || echo "MISSING — run: cd apps/api && python -m venv .venv && .venv/bin/pip install -e '.[dev]'"

# Node modules installed
ls apps/web/node_modules/.bin/next 2>/dev/null && echo "node_modules OK" || echo "MISSING — run: cd apps/web && npm install"

# Docker running
docker info >/dev/null 2>&1 && echo "Docker OK" || echo "Docker not running — open Docker Desktop"

# .env exists for API
ls apps/api/.env 2>/dev/null && echo ".env OK" || echo "MISSING — run: cp .env.example apps/api/.env"
```

---

## Starting the Stack

### Step 1 — PostgreSQL

```bash
# From repo root
docker compose up -d

# Wait for readiness
until docker compose exec -T postgres pg_isready -U investingbuddy -q 2>/dev/null; do
  echo "Waiting for PostgreSQL..."; sleep 2
done
echo "PostgreSQL ready"
```

### Step 2 — Run Migrations

```bash
cd apps/api
.venv/bin/alembic upgrade head
```

Only needed on first start or after a new migration is added.

### Step 3 — FastAPI Backend

```bash
cd apps/api
.venv/bin/uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload &
```

Health check:
```bash
curl -s http://localhost:8000/health
# Expected: {"status":"ok","environment":"development","version":"..."}
```

### Step 4 — Next.js Frontend

```bash
cd apps/web
npm run dev &
```

Check: open `http://localhost:3000` in a browser or:
```bash
curl -s -o /dev/null -w "%{http_code}" http://localhost:3000
# Expected: 200
```

---

## Stopping the Stack

```bash
kill $(lsof -ti:8000) 2>/dev/null; echo "API stopped"
kill $(lsof -ti:3000) 2>/dev/null; echo "Frontend stopped"
docker compose down
```

---

## Port Conflict Resolution

If a port is already in use:

```bash
# Find what is using port 8000
lsof -i :8000

# Kill it
kill -9 $(lsof -ti:8000)
```

---

## Common Issues

| Symptom | Cause | Fix |
|---|---|---|
| `uvicorn: command not found` | venv not activated / not installed | Use `.venv/bin/uvicorn` explicitly, or activate venv first |
| `connection refused :5432` | Docker not running / container not started | `open -a Docker && docker compose up -d` |
| `alembic: command not found` | venv issue | `.venv/bin/alembic upgrade head` |
| `Module not found` on API start | Missing pip install | `cd apps/api && .venv/bin/pip install -e '.[dev]'` |
| `npm: command not found` | Node not installed | Install Node.js 22+ from nodejs.org |
| `EADDRINUSE :3000` | Port already in use | `kill $(lsof -ti:3000)` |
| API returns 500 on DB calls | DB not running or migrations not applied | Start Docker, run `alembic upgrade head` |

---

## Environment File Rules

- `apps/api/.env` must exist (copy from `.env.example`). It is gitignored — never commit it.
- `apps/web/.env.local` is optional (sets `NEXT_PUBLIC_API_BASE_URL`). Also gitignored.
- Azure credentials can be left empty for local development — they are only needed for agent LLM calls.

---

## Rules

- Never commit `.env` files.
- Never start the stack without confirming PostgreSQL is healthy first.
- Always run `alembic upgrade head` after pulling changes that include new migrations.
- Use the `.venv/bin/` prefix for all Python tools — do not rely on system Python.
- Do not use `sudo` to resolve port conflicts; find and kill the blocking process instead.
