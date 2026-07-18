from fastapi import FastAPI

from app.api.v1.admin_reports import router as admin_reports_router
from app.api.v1.backtesting import router as backtesting_router
from app.api.v1.citations import router as citations_router
from app.api.v1.companies import router as companies_router
from app.api.v1.discovery import router as discovery_router
from app.api.v1.final_reports import router as final_reports_router
from app.api.v1.financial_data import router as financial_data_router
from app.api.v1.health import router as health_router
from app.api.v1.market_discovery import router as market_discovery_router
from app.api.v1.reports import router as reports_router
from app.api.v1.scoring import router as scoring_router
from app.api.v1.sources import router as sources_router
from app.api.v1.workflows import router as workflows_router
from app.core.config import settings
from app.core.staging_auth import install_staging_basic_auth

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
