"""The web research audit, readable. Open-web W1 (spec §22.2).

ADMIN/INTERNAL ONLY, and guarded exactly as ``research_decisions`` is: mounted under
``/api/v1`` behind the backend's Basic Auth (every route except ``/health``), and
reachable from the browser only through the authenticated, admin-allowlisted Next.js
proxy (``/api/v1/admin/web-research`` is on its prefix list). Read-only.

``GET /api/v1/admin/web-research/jobs/{research_job_id}`` and
``GET /api/v1/admin/web-research/discovery-runs/{discovery_run_id}`` return every query
with its network fact, every result with its disposition, every fetch attempt, totals
and cost units. 404 when the job or run does not exist; 503 when migration 042 has not
been applied here — a missing table is a different fact from "no searches".
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.research_decisions import _schema_missing
from app.db.session import get_db
from app.models.discovery import DiscoveryRun
from app.models.research_job import ResearchJob
from app.schemas.web_research import WebResearchAuditRead
from app.services.web_research.audit import web_research_audit

router = APIRouter(prefix="/admin/web-research", tags=["admin-web-research"])


def _schema_missing_error() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail=(
            "The web research provenance tables are not present in this environment. "
            "Migration 042 has not been applied here. This is a schema state, not a "
            "failure, and it is not answered with an empty audit."
        ),
    )


async def _audit(
    db: AsyncSession,
    *,
    research_job_id: uuid.UUID | None = None,
    discovery_run_id: uuid.UUID | None = None,
) -> WebResearchAuditRead:
    try:
        return await web_research_audit(
            db, research_job_id=research_job_id, discovery_run_id=discovery_run_id
        )
    except Exception as exc:
        if _schema_missing(exc):
            raise _schema_missing_error() from exc
        raise


@router.get(
    "/jobs/{research_job_id}",
    response_model=WebResearchAuditRead,
    summary="Web search audit for one research job (admin only)",
)
async def get_job_web_research(
    research_job_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> WebResearchAuditRead:
    if await db.get(ResearchJob, research_job_id) is None:
        raise HTTPException(status_code=404, detail="Research job not found")
    return await _audit(db, research_job_id=research_job_id)


@router.get(
    "/discovery-runs/{discovery_run_id}",
    response_model=WebResearchAuditRead,
    summary="Web search audit for one discovery run (admin only)",
)
async def get_discovery_run_web_research(
    discovery_run_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> WebResearchAuditRead:
    if await db.get(DiscoveryRun, discovery_run_id) is None:
        raise HTTPException(status_code=404, detail="Discovery run not found")
    return await _audit(db, discovery_run_id=discovery_run_id)
