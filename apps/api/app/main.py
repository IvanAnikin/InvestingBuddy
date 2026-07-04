import base64
import hmac
import os

from fastapi import FastAPI, Request
from fastapi.responses import Response

from app.api.v1.companies import router as companies_router
from app.api.v1.health import router as health_router
from app.api.v1.workflows import router as workflows_router
from app.core.config import settings

app = FastAPI(
    title=settings.app_name,
    version="0.2.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

_STAGING_AUTH_USER = "admin"
_STAGING_AUTH_PASS = os.environ.get("STAGING_BASIC_AUTH_PASSWORD", "")


@app.middleware("http")
async def staging_basic_auth_middleware(request: Request, call_next: object) -> Response:
    if settings.app_env == "staging" and _STAGING_AUTH_PASS:
        if request.url.path != "/health":
            auth = request.headers.get("Authorization", "")
            _expected = base64.b64encode(
                f"{_STAGING_AUTH_USER}:{_STAGING_AUTH_PASS}".encode()
            ).decode()
            if not hmac.compare_digest(auth, f"Basic {_expected}"):
                return Response(
                    status_code=401,
                    headers={"WWW-Authenticate": 'Basic realm="staging"'},
                )
    return await call_next(request)


app.include_router(health_router)
app.include_router(companies_router, prefix="/api/v1")
app.include_router(workflows_router, prefix="/api/v1")
