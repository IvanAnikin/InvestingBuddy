"""Async company research — the product's front-door endpoints.

INTERNAL USE ONLY. No public routes.

WHY THESE EXIST
===============
``/research/company`` used to run the pipeline inside the browser's request:
``POST /workflows/company-analysis/run`` followed by
``POST /final-reports/from-report/{id}``. Together those measured well past
the ~230s Azure gateway ceiling on live data, so the user saw HTTP 502 at
~206s or 504 at ~240s after several minutes of real work, and the transaction
rolled back.

These endpoints replace that with the SAME async job mechanism the
discovery-candidate CTA already uses in production: the POST commits a job and
returns immediately; the work runs in a background task with its own DB
session; the UI polls a plain GET.

  POST /api/v1/company-research/jobs           start (or join) a job
  GET  /api/v1/company-research/jobs/{job_id}  poll one job
  GET  /api/v1/company-research/jobs?company_id=…  recover the latest job

The old synchronous endpoints are untouched — the admin console still uses
them as engineering tools, and nothing about them changes.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.company_research import (
    CompanyResearchJobCreate,
    CompanyResearchJobResponse,
)
from app.services import company_research_service as svc
from app.services import research_job
from app.services.jobs import company_research_job as durable

router = APIRouter(prefix="/company-research", tags=["company-research"])

_INTERNAL = (
    "INTERNAL USE ONLY. Not investment advice. Not a public recommendation. "
    "Human review required."
)

_STATUS_MESSAGES = {
    research_job.STATUS_PENDING: (
        "Research queued. It runs on the server — poll "
        "GET /company-research/jobs/{job_id} for progress. You can leave this "
        "page; the run continues without the browser."
    ),
    research_job.STATUS_RUNNING: "Research is running on the server.",
    research_job.STATUS_FAILED: "Research failed. See 'error'.",
    research_job.STATUS_INTERRUPTED: (
        "Research was INTERRUPTED — the worker that owned it is gone (most "
        "likely an app restart). Nothing already collected was lost; "
        "re-running is safe."
    ),
    # V3 durable path only. Both are terminal states that could not exist before
    # durability, because nothing retried and nothing could be cancelled.
    durable.contract.STATUS_DEAD_LETTER: (
        "Research stopped after every attempt failed. Nothing already collected "
        "was lost, and re-running is safe — see 'dead_letter_reason'."
    ),
    durable.contract.STATUS_CANCELLED: "Research was cancelled before it finished.",
}


def _message(envelope: dict) -> str:
    """Human-facing message for one job state. Never a recommendation."""
    state = str(envelope.get("status") or research_job.STATUS_PENDING)
    base = _STATUS_MESSAGES.get(state)
    if base is not None:
        if state == research_job.STATUS_FAILED and envelope.get("error"):
            base = f"Research failed ({envelope['error']})."
        return f"{base} Internal draft only — human review required."

    company = envelope.get("company") or {}
    label = company.get("ticker") or "the company"
    warnings = envelope.get("warnings") or []
    note = (
        " Note: final-report generation degraded to the deterministic draft."
        if warnings
        else ""
    )
    return (
        f"Research complete for {label}.{note} Internal draft only — human "
        "review required."
    )


async def _read_job(db: AsyncSession, job_id: uuid.UUID) -> dict | None:
    """One job's envelope, from whichever store owns it.

    The durable store is tried first, then the V2 lookup. Both are single UUID
    reads, and the fallback is what keeps every job created before the flag was
    turned on resolvable — the compatibility guarantee the migration plan makes.
    A durable job id is a ``research_jobs.id``; a V2 job id is an ``AgentRun.id``;
    the two id spaces never collide because they are independent UUIDs.
    """
    if durable.durable_enabled():
        envelope = await durable.get_envelope(job_id)
        if envelope is not None:
            return envelope
    v2 = await svc.get_job_envelope(db, job_id)
    return research_job.describe(v2) if v2 is not None else None


@router.post(
    "/jobs",
    response_model=CompanyResearchJobResponse,
    status_code=status.HTTP_202_ACCEPTED,
    summary="Start an async research job for one company",
    description=(
        "Starts the full company-research pipeline (workflow → primary-document "
        "ingestion → financial extraction → evidence validation → LLM council → "
        "final report) ASYNCHRONOUSLY and returns IMMEDIATELY with a job "
        "envelope. It does NOT block until the report is assembled — that "
        "repeatedly exceeded the ~230s gateway ceiling and surfaced as a browser "
        "502/504 while the backend was still working.\n\n"
        "The job row is COMMITTED before any expensive work begins, so closing "
        "the browser or losing the connection cannot cancel the run.\n\n"
        "IDEMPOTENT: while a job for this company is pending/running the current "
        "state is returned and NO second (expensive) run is started — which "
        "covers a double-click, a browser retry and a network retry alike.\n\n"
        + _INTERNAL
    ),
)
async def start_company_research_job(
    payload: CompanyResearchJobCreate,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
) -> CompanyResearchJobResponse:
    company = await svc.resolve_company(
        db,
        company_id=payload.company_id,
        ticker=payload.ticker,
        exchange=payload.exchange,
    )
    if company is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=(
                "Company not found. Register it first (POST /api/v1/companies) "
                "with the ticker exactly as its exchange lists it and the "
                "exchange given separately."
            ),
        )

    if durable.durable_enabled():
        # V3: commit a durable job row and let a leased worker claim it. The
        # work is no longer tied to THIS process, so an App Service recycle
        # suspends the run instead of destroying it.
        envelope, scheduled = await durable.submit(
            company,
            provider_name=payload.provider_name,
            use_llm=payload.use_llm,
            llm_provider=payload.llm_provider,
            require_schema_valid=payload.require_schema_valid,
        )
    else:
        envelope, scheduled = await svc.start_company_research(
            db,
            company,
            provider_name=payload.provider_name,
            use_llm=payload.use_llm,
            llm_provider=payload.llm_provider,
            require_schema_valid=payload.require_schema_valid,
        )
    if scheduled:
        if not durable.durable_enabled():
            # V2: run the (already-committed pending) job in a background task
            # using its OWN DB session — never the request-scoped one, which is
            # closed once this response is sent. Only the primitive job id is
            # handed over. On the durable path there is nothing to schedule: a
            # worker claims the committed row.
            background_tasks.add_task(
                svc.process_company_research_task, str(envelope["job_id"])
            )
        message = (
            "Research started. It runs on the server — you can leave this page. "
            "Internal draft only; human review required."
        )
    else:
        message = (
            "Research is already in progress for this company — no second run "
            "was started. Poll GET /company-research/jobs/{job_id}."
        )
    return CompanyResearchJobResponse.from_envelope(envelope, message=message)


@router.get(
    "/jobs/{job_id}",
    response_model=CompanyResearchJobResponse,
    summary="Get the state of one async company-research job",
    description=(
        "Returns the current state of ONE research job: pending/running with the "
        "stage in flight while the background job works, the completed job with "
        "the FINAL report id when done, or a failed status with a safe reason. "
        "An abandoned job (its worker died) reports 'interrupted' with "
        "recoverable=true, DERIVED from its own timestamps rather than stored. "
        + _INTERNAL
    ),
)
async def get_company_research_job(
    job_id: uuid.UUID,
    db: AsyncSession = Depends(get_db),
) -> CompanyResearchJobResponse:
    envelope = await _read_job(db, job_id)
    if envelope is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No company-research job {job_id} exists.",
        )
    return CompanyResearchJobResponse.from_envelope(
        envelope, message=_message(envelope)
    )


@router.get(
    "/jobs",
    response_model=CompanyResearchJobResponse,
    summary="Recover the latest company-research job for one company",
    description=(
        "Returns the most recent research job for ONE company. This is how a "
        "reader who refreshed the page, closed the tab or came back later finds "
        "the run they started: the job id is not only in the browser. Scoped "
        "strictly to the given company — never a global-latest lookup. 404 when "
        "that company has never been researched. " + _INTERNAL
    ),
)
async def latest_company_research_job(
    company_id: uuid.UUID = Query(
        ..., description="The company whose most recent research job to return."
    ),
    db: AsyncSession = Depends(get_db),
) -> CompanyResearchJobResponse:
    envelope = None
    if durable.durable_enabled():
        envelope = await durable.latest_envelope_for_company(company_id)
    if envelope is None:
        v2 = await svc.latest_job_for_company(db, company_id)
        envelope = research_job.describe(v2) if v2 is not None else None
    if envelope is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="No company-research job has been run for this company.",
        )
    return CompanyResearchJobResponse.from_envelope(
        envelope, message=_message(envelope)
    )
