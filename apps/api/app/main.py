import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1.admin_reports import router as admin_reports_router
from app.api.v1.backtesting import router as backtesting_router
from app.api.v1.citations import router as citations_router
from app.api.v1.companies import router as companies_router
from app.api.v1.company_research import router as company_research_router
from app.api.v1.discovery import router as discovery_router
from app.api.v1.field_review import router as field_review_router
from app.api.v1.final_reports import router as final_reports_router
from app.api.v1.financial_data import router as financial_data_router
from app.api.v1.health import router as health_router
from app.api.v1.market_discovery import router as market_discovery_router
from app.api.v1.reports import router as reports_router
from app.api.v1.scoring import router as scoring_router
from app.api.v1.sources import router as sources_router
from app.api.v1.workflows import router as workflows_router
from app.core.config import settings
from app.core.logging_config import configure_logging
from app.core.request_logging import install_request_logging
from app.core.staging_auth import install_staging_basic_auth
from app.core.structured_logging import log_event

# ── Logging (Phase 27.1D) ───────────────────────────────────────────────────
# Configure a stdout handler at settings.log_level BEFORE the app is built so
# INFO-level structured telemetry events reach the container log stream under
# gunicorn on Azure App Service (the root logger is otherwise unconfigured and
# drops INFO). No secrets are ever configured or logged here.
configure_logging()

@asynccontextmanager
async def _lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Startup/shutdown hooks.

    Private-use readiness PR-F — ORPHANED-JOB VISIBILITY. A full analysis runs
    in a process-local background task, so a restart mid-run leaves its stored
    envelope on ``running`` forever. The state was always recoverable (a fresh
    POST past the stale threshold restarts it) but nothing SAID so, and the
    first process to notice is this one: it is starting up precisely because
    the process that owned those jobs is gone.

    The sweep is READ-ONLY on purpose. It does not rewrite the envelope, so the
    dead worker's audit trail survives and a job still running under another
    live process is never stolen from it. It also never re-enqueues: silently
    restarting an expensive council run on every deploy is not recovery, it is
    a surprise. It logs what was lost; the API reports the same jobs as
    ``interrupted`` with ``recoverable=true``, and a human decides.

    Failure here never blocks startup — a diagnostic that can take the API down
    is worse than the diagnostic is worth.
    """
    try:
        from app.db.session import async_session_factory
        from app.services.company_research_service import (
            sweep_interrupted_company_jobs,
        )
        from app.services.market_discovery_service import (
            sweep_interrupted_analysis_jobs,
        )

        async with async_session_factory() as session:
            interrupted = await sweep_interrupted_analysis_jobs(session)
            # The same sweep for the generic company-research jobs the product
            # front door creates — same reasoning, different durable store.
            company_jobs = await sweep_interrupted_company_jobs(session)
        for job in interrupted:
            log_event(
                logging.getLogger(__name__),
                "analysis_job_interrupted_by_restart",
                candidate_id=job.get("candidate_id"),
                ticker=job.get("ticker"),
                previous_status=job.get("status"),
                started_at=job.get("started_at"),
            )
        for job in company_jobs:
            log_event(
                logging.getLogger(__name__),
                "company_research_job_interrupted_by_restart",
                job_id=job.get("job_id"),
                ticker=job.get("ticker"),
                previous_status=job.get("status"),
                started_at=job.get("started_at"),
            )
        if interrupted or company_jobs:
            log_event(
                logging.getLogger(__name__),
                "analysis_job_interruption_sweep_completed",
                interrupted_count=len(interrupted) + len(company_jobs),
            )
    except Exception as exc:  # noqa: BLE001 - never block startup on a diagnostic
        logging.getLogger(__name__).warning(
            "analysis_job_interruption_sweep_failed error_type=%s",
            type(exc).__name__,
        )
    yield


app = FastAPI(
    title=settings.app_name,
    version="0.8.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    lifespan=_lifespan,
)

# ── Staging Basic Auth (server-to-server) ──────────────────────────────────
# Activated when APP_ENV=staging and STAGING_BASIC_AUTH="username:password".
# Protects all routes except /health. This is the backend's server-to-server
# defense; human admin authentication is enforced at the Next.js layer
# (Phase 23 — Admin/Auth Hardening).
if settings.app_env == "staging" and settings.staging_basic_auth:
    install_staging_basic_auth(app, settings.staging_basic_auth)

# ── Request telemetry (Phase 27.1D) ─────────────────────────────────────────
# Added LAST so it is the OUTERMOST middleware — it therefore times and logs
# every request, including those rejected by staging Basic Auth (401). Logs
# method/path/status/duration/request_id only; never headers, bodies, query
# strings, or secrets.
if settings.request_logging_enabled:
    install_request_logging(app)

app.include_router(health_router)
app.include_router(companies_router, prefix="/api/v1")
# The product's async front door: /research/company submits a job here and
# polls it, instead of holding one HTTP request open for the whole run.
app.include_router(company_research_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(sources_router, prefix="/api/v1")
app.include_router(citations_router, prefix="/api/v1")
app.include_router(financial_data_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(admin_reports_router, prefix="/api/v1")
app.include_router(discovery_router, prefix="/api/v1")
app.include_router(scoring_router, prefix="/api/v1")
app.include_router(final_reports_router, prefix="/api/v1")
app.include_router(backtesting_router, prefix="/api/v1")
app.include_router(market_discovery_router, prefix="/api/v1")
# Phase 32A Slice 6D — Deep Field Review (a THIRD, separate council: it
# compares the ALREADY-COMPLETED analyses of a discovery run's candidates).
app.include_router(field_review_router, prefix="/api/v1")
