import base64

from fastapi import FastAPI, Request, Response

from app.api.v1.admin_reports import router as admin_reports_router
from app.api.v1.citations import router as citations_router
from app.api.v1.companies import router as companies_router
from app.api.v1.financial_data import router as financial_data_router
from app.api.v1.health import router as health_router
from app.api.v1.reports import router as reports_router
from app.api.v1.sources import router as sources_router
from app.api.v1.workflows import router as workflows_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.6.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ── Staging Basic Auth middleware ──────────────────────────────────────────
# Activated when APP_ENV=staging and STAGING_BASIC_AUTH="username:password".
# Protects all routes except /health (used by App Service health checks).
# This is a minimal access control for the staging environment — not a
# replacement for proper authentication (planned for Phase 12 with Clerk).
if settings.app_env == "staging" and settings.staging_basic_auth:
    _expected = base64.b64encode(settings.staging_basic_auth.encode()).decode()

    @app.middleware("http")
    async def staging_basic_auth(request: Request, call_next: object) -> Response:
        if request.url.path == "/health":
            return await call_next(request)  # type: ignore[operator]
        auth = request.headers.get("Authorization", "")
        if auth == f"Basic {_expected}":
            return await call_next(request)  # type: ignore[operator]
        return Response(
            content="Staging access restricted",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="InvestingBuddy Staging"'},
        )

app.include_router(health_router)
app.include_router(companies_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(sources_router, prefix="/api/v1")
app.include_router(citations_router, prefix="/api/v1")
app.include_router(financial_data_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(admin_reports_router, prefix="/api/v1")
