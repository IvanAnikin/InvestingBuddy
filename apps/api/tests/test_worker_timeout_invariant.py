"""The gunicorn worker timeout must outlast any single blocking stretch.

`ib-stg-api` runs ONE gunicorn worker with an async `UvicornWorker`, so
`--timeout` is a HEARTBEAT timeout: if the event loop is not scheduled for that
long, gunicorn SIGKILLs the worker, destroying every in-flight research run and
502-ing every concurrent request.

These two numbers silently drifted apart in production — `--timeout 120` against
a `primary_document_ingestion_budget_seconds` of 180 — which made a slow document
*permitted* to outlive the worker rather than merely unlucky. Six outages
followed between 2026-08-24 and 2026-09-02.

The parses now run off the loop (`live_fetchers._parse_off_loop`), which is the
actual fix. This invariant is the second line of defence: it keeps the declared
deployment timeout above the work the app is configured to allow, so a future
budget bump cannot quietly re-create the same trap.
"""

from __future__ import annotations

import re
from pathlib import Path

from app.core.config import (
    DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS,
    DEPLOYED_GUNICORN_WORKERS,
    Settings,
)

_REPO_ROOT = Path(__file__).resolve().parents[3]
_BICEP = _REPO_ROOT / "infra" / "azure" / "modules" / "appservice.bicep"


def test_ingestion_budget_stays_under_the_worker_timeout():
    cfg = Settings()
    assert cfg.primary_document_ingestion_budget_seconds < (
        DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS
    ), (
        "primary_document_ingestion_budget_seconds "
        f"({cfg.primary_document_ingestion_budget_seconds}s) is >= the deployed "
        f"gunicorn worker timeout ({DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS}s). "
        "A single document is then allowed to outlive the worker. Raise the "
        "deployed --timeout (bicep + live startup command + docs) or lower the "
        "budget."
    )


def test_single_document_total_timeout_stays_under_the_worker_timeout():
    """One document's own end-to-end cap must also fit inside the heartbeat."""
    cfg = Settings()
    assert cfg.primary_document_total_timeout_seconds < DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS, (
        f"primary_document_total_timeout_seconds "
        f"({cfg.primary_document_total_timeout_seconds}s) is >= the deployed "
        f"gunicorn worker timeout ({DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS}s)."
    )


def test_bicep_startup_command_matches_the_declared_timeout():
    """Infra-as-code must not drift from the constant the budgets are checked against."""
    text = _BICEP.read_text()
    match = re.search(r"appCommandLine: '([^']+)'", text)
    assert match, "no appCommandLine found in appservice.bicep"
    cmd = match.group(1)

    timeout = re.search(r"--timeout (\d+)", cmd)
    assert timeout, f"no --timeout in the bicep startup command: {cmd!r}"
    assert int(timeout.group(1)) == DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS, (
        f"bicep --timeout is {timeout.group(1)}s but "
        f"DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS is "
        f"{DEPLOYED_GUNICORN_WORKER_TIMEOUT_SECONDS}s — update both together."
    )


def test_bicep_pins_a_single_worker_on_b1():
    """docs/DEPLOYMENT.md says never run 2 workers on B1; bicep must agree.

    Bicep said ``--workers 2`` while the live app ran ``--workers 1`` and the
    deployment guide explicitly forbade 2 on B1. No workflow applies this file,
    so the contradiction sat there as a trap for whoever ran it by hand: a second
    worker roughly doubles resident memory on a 1.75 GB plan that was already
    observed at 93-95%.
    """
    text = _BICEP.read_text()
    match = re.search(r"appCommandLine: '([^']+)'", text)
    assert match
    workers = re.search(r"--workers (\d+)", match.group(1))
    assert workers, "no --workers in the bicep startup command"
    assert int(workers.group(1)) == DEPLOYED_GUNICORN_WORKERS, (
        f"bicep pins --workers {workers.group(1)} but "
        f"DEPLOYED_GUNICORN_WORKERS is {DEPLOYED_GUNICORN_WORKERS}. On B1, "
        "docs/DEPLOYMENT.md requires 1 until the plan is scaled to B2/S1+ — and "
        "research_job.is_orphaned changes behaviour when this is not 1."
    )
