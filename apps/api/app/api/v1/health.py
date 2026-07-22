from fastapi import APIRouter
from pydantic import BaseModel

from app.core.build_info import BUILD_INFO
from app.core.config import settings

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: str
    environment: str
    version: str
    # Phase 19.2.1: build/release identifiers so the deploy smoke check can verify
    # the NEW container is serving (not the old one during async recycle).
    # "unknown" locally; the deploy workflow bundles the real commit SHA.
    commit_sha: str
    build_id: str
    # Phase 27.1D: additional SAFE deploy metadata for staging observability.
    # ``app`` names the running service; ``build_time`` is the deploy build
    # timestamp. Both are public build identifiers — never secrets.
    app: str
    build_time: str


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    return HealthResponse(
        status="ok",
        environment=settings.app_env,
        version="0.1.0",
        commit_sha=BUILD_INFO["commit_sha"],
        build_id=BUILD_INFO["build_id"],
        app=settings.app_name,
        build_time=BUILD_INFO["build_time"],
    )
