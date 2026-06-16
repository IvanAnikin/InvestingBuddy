# Create Migration Command

You are creating or updating a PostgreSQL database schema change for InvestingBuddy using SQLAlchemy and Alembic.

---

## Pre-Migration Checklist

1. Read `docs/DATABASE.md` for current schema
2. Inspect existing models in `apps/api/app/models/`
3. Inspect the latest migration in `apps/api/alembic/versions/`
4. Understand why the schema change is needed
5. Check for foreign key dependencies before adding or removing tables

---

## Migration Process

```
Step 1: Update or add SQLAlchemy model(s) in apps/api/app/models/
Step 2: Update apps/api/app/db/base.py if new model needs registering
Step 3: Generate migration:
    cd apps/api
    alembic revision --autogenerate -m "descriptive_name"
Step 4: Review the generated migration file carefully:
    - Autogenerate can miss: ENUM types, complex constraints, server defaults
    - Autogenerate can falsely include: unchanged tables it detects as modified
Step 5: Add explicit downgrade() method that safely reverses the change
Step 6: Test locally:
    alembic upgrade head
    alembic downgrade -1
    alembic upgrade head
Step 7: Update docs/DATABASE.md
Step 8: Add model-level tests if validation logic is new
```

---

## Migration Rules

- Every schema change requires a migration — no manual `ALTER TABLE` in production.
- Prefer additive migrations — add columns before removing old ones.
- Mark removed columns as `nullable=True` in one migration; drop in a later migration.
- Use `server_default` for new NOT NULL columns on existing tables to avoid locking.
- Add explicit indexes on: all foreign keys, ticker, status, published_at, company_id.
- Never drop a column without verifying no code references it.
- Never drop a table without archiving its data if it contains research history.
- `downgrade()` must be safe — prefer a real reverse operation over `pass`.
- Store timestamps (`created_at`, `updated_at`) on all user-facing tables.
- Store `source_id` on all financial metric records.

---

## Naming Conventions

Migration message format: `add_{table}`, `add_{column}_to_{table}`, `rename_{old}_to_{new}`, `drop_{table}`

Model class names: PascalCase singular (e.g., `Company`, `AgentRun`, `SourceChunk`)

Table names: snake_case plural (e.g., `companies`, `agent_runs`, `source_chunks`)

---

## Output Format

```
## Migration Summary

### Change
<what was added, modified or removed>

### Files changed
- apps/api/app/models/<file>.py
- apps/api/alembic/versions/<timestamp>_<name>.py
- docs/DATABASE.md

### Migration tested
- alembic upgrade head: passed/failed
- alembic downgrade -1: passed/failed

### Risk
<any destructive or irreversible steps>

### Recommended next step
<e.g., run in CI, then in staging>
```
