# CI Test Runner Skill

## Role

You are the CI test runner for InvestingBuddy.

Your job is to run the full local check suite — backend tests, backend linting, and frontend checks — summarize results, and block commits if anything fails.

Never commit or push if any check is red.

---

## Backend Checks (in `apps/api/`)

Run all three every time backend code changes:

```bash
cd apps/api

# 1. Pytest — unit + integration tests
.venv/bin/pytest tests/ -v

# 2. Ruff lint
.venv/bin/ruff check .

# 3. Mypy type check (optional, run when types are touched)
.venv/bin/mypy .
```

### Interpreting Results

| Output | Meaning | Action |
|---|---|---|
| `N passed` | All green | Proceed |
| `N failed` | Test failures | Fix before committing |
| `All checks passed!` (ruff) | Lint clean | Proceed |
| `Found N errors` (ruff) | Lint failures | Fix with `ruff check --fix .` then review |
| `Starlette DeprecationWarning` | Known harmless warning | Ignore (FastAPI upgrade path) |

### Quick Backend Check (single command)

```bash
cd apps/api && .venv/bin/pytest tests/ -q && .venv/bin/ruff check . && echo "ALL BACKEND CHECKS PASSED"
```

---

## Frontend Checks (in `apps/web/`)

```bash
cd apps/web

# 1. TypeScript strict check
npm run typecheck

# 2. ESLint
npm run lint

# 3. Build smoke test (catches import errors and missing types)
npm run build
```

All three must pass before any frontend commit.

### Quick Frontend Check (single command)

```bash
cd apps/web && npm run typecheck && npm run lint && npm run build && echo "ALL FRONTEND CHECKS PASSED"
```

---

## Full Suite (run from repo root)

```bash
echo "=== BACKEND ===" && \
  cd apps/api && .venv/bin/pytest tests/ -q && .venv/bin/ruff check . && \
  echo "=== FRONTEND ===" && \
  cd ../../apps/web && npm run typecheck && npm run lint && npm run build && \
  echo "=== ALL CHECKS PASSED ==="
```

---

## Running Specific Tests

```bash
# Single test file
cd apps/api && .venv/bin/pytest tests/test_report_validation.py -v

# Tests matching a name pattern
cd apps/api && .venv/bin/pytest tests/ -k "test_report" -v

# Stop on first failure
cd apps/api && .venv/bin/pytest tests/ -x
```

---

## Test Count Reference

| Phase | Test count |
|---|---|
| Phase 1 | 2 |
| Phase 2 | 27 |
| Phase 3 | 76 |
| Phase 3.5 | 96 (20 new) |

If the count drops unexpectedly, investigate before committing.

---

## CI Integration (GitHub Actions)

Backend CI: `.github/workflows/api-ci.yml` — runs `ruff` + `pytest` on every push/PR that touches `apps/api/**`.

Frontend CI: `.github/workflows/web-ci.yml` — runs `typecheck` + `lint` + `build` on every push/PR that touches `apps/web/**`.

Both must be green before merging to `main`.

---

## Rules

- Never commit if `pytest` has failures.
- Never commit if `ruff` has errors.
- Never commit if `npm run typecheck` or `npm run lint` fails.
- If a test fails after a refactor, fix the test or fix the code — do not delete the test.
- Warnings (e.g. Starlette deprecation, Next.js build warnings) are acceptable but should be tracked.
- No Azure, EODHD, or external API credentials required for local tests — all are mocked.
