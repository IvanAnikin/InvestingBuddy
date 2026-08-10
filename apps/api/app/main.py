from fastapi import FastAPI

from app.api.v1.admin_reports import router as admin_reports_router
from app.api.v1.backtesting import router as backtesting_router
from app.api.v1.citations import router as citations_router
from app.api.v1.companies import router as companies_router
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

# ── Logging (Phase 27.1D) ───────────────────────────────────────────────────
# Configure a stdout handler at settings.log_level BEFORE the app is built so
# INFO-level structured telemetry events reach the container log stream under
# gunicorn on Azure App Service (the root logger is otherwise unconfigured and
# drops INFO). No secrets are ever configured or logged here.
configure_logging()

app = FastAPI(
    title=settings.app_name,
    version="0.8.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
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
