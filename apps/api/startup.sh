#!/usr/bin/env bash
# App Service startup (ib-stg-api): apply Alembic migrations, then start the API.
#
# Why (2026-11-22): merges auto-deploy the code but nothing ran migrations, so a
# release with a new column served 500s until `alembic upgrade head` was run by hand.
#
# - App Service activates the Oryx virtualenv (antenv) before this script, as it did
#   for the previous gunicorn-only startup command.
# - The outcome is written to MIGRATION_STATUS_FILE and shown on /health ("migrations"),
#   so the deploy smoke check can require the database to be at the repository head.
# - A failed migration does NOT stop the API from starting: /health reports
#   state=failed and the deploy workflow fails, which is visible and recoverable,
#   while the previous schema keeps serving what it can.
# - RUN_MIGRATIONS_ON_START=false skips the step (state=skipped).
set -u
cd "$(dirname "$0")"
STATUS_FILE="${MIGRATION_STATUS_FILE:-/tmp/ib_migration_status.json}"
TIMEOUT="${MIGRATION_TIMEOUT_SECONDS:-180}"
now() { date -u +%Y-%m-%dT%H:%M:%SZ; }

if [ "${RUN_MIGRATIONS_ON_START:-true}" = "true" ]; then
  echo "[startup] alembic upgrade head (timeout ${TIMEOUT}s)"
  if timeout "$TIMEOUT" python -m alembic upgrade head > /tmp/ib_migration.log 2>&1; then
    REV=$(python -m alembic current 2>/dev/null | grep -oE '^[0-9A-Za-z_]+' | head -1)
    printf '{"state":"ok","revision":"%s","at":"%s"}\n' "${REV:-unknown}" "$(now)" > "$STATUS_FILE"
    echo "[startup] migrations ok, revision ${REV:-unknown}"
  else
    printf '{"state":"failed","revision":null,"at":"%s"}\n' "$(now)" > "$STATUS_FILE"
    echo "[startup] MIGRATION FAILED; last log lines follow (no connection strings are printed by alembic):"
    tail -n 20 /tmp/ib_migration.log | grep -viE 'password|postgres(ql)?://' || true
  fi
else
  printf '{"state":"skipped","revision":null,"at":"%s"}\n' "$(now)" > "$STATUS_FILE"
fi

exec gunicorn -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:8000 --workers 1 --timeout 300 app.main:app
