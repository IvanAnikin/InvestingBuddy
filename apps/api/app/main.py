from fastapi import FastAPI

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

app.include_router(health_router)
app.include_router(companies_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
app.include_router(sources_router, prefix="/api/v1")
app.include_router(citations_router, prefix="/api/v1")
app.include_router(financial_data_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(admin_reports_router, prefix="/api/v1")
