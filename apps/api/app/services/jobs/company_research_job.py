"""Company research on the durable job contract — V3.0 Slice 3.

WHY THIS MODULE EXISTS
======================
Slices 1 and 2 built a durable job record, a state machine and a worker, and
deliberately wired none of them to anything. This is the wiring, for exactly one
entry point: ``POST /api/v1/company-research/jobs``.

It is a thin adapter, and being thin is the point. The research itself is still
``company_research_service.execute_company_research`` — the ONE company-research
workflow — and this module does not reimplement a line of it. What it replaces
is only *how the work is scheduled and owned*: a FastAPI ``BackgroundTasks`` call
inside the API process becomes a committed row that a leased worker claims.

WHAT THE READER MUST NOT NOTICE
===============================
The web app polls this endpoint and renders its envelope. So the envelope this
module composes is the V2 envelope, field for field, and the API response model
is untouched apart from three optional additions. A reader on the durable path
sees the same shape, the same stage vocabulary and the same words.

The one thing that genuinely changes is the *set* of terminal states: durability
introduces ``dead_letter`` and ``cancelled``, which could not exist before
because nothing retried and nothing could be cancelled. Hiding them behind
``failed`` was considered and rejected — a ``dead_letter`` job hit transient
errors and may well succeed later, while a ``failed`` job hit a permanent one
and will not, and that is exactly the distinction an operator needs. The frontend
learns the two words instead.

TWO RECORDS, ON PURPOSE
=======================
A durable job is a ``research_jobs`` row. The reader-facing *content* — company,
provider, stages reached, the report that came out, warnings — stays in the
``AgentRun``/``AgentStep`` envelope V2 already writes, and the two are linked by
``research_jobs.agent_run_id``.

That split is not incidental. The job row is contended for by workers and
rewritten on every heartbeat; the envelope is an audit record written once per
stage. Putting lease churn on the audit trail would be the wrong shape, and it
would also mean rewriting every existing reader of the envelope. Instead:

    lifecycle  (status, stage, attempt, cancellation)  <- research_jobs
    content    (company, report, warnings, stages)     <- AgentStep envelope

A poll composes one envelope from both, with the **lifecycle always winning**.
That is what stops the two disagreeing: the envelope can never claim a job is
running when the job row says it was dead-lettered, because the envelope is not
asked.

EXISTING JOB IDS KEEP RESOLVING
===============================
A durable job's id is its ``research_jobs.id``; a V2 job's id is its
``AgentRun.id``. Both are UUIDs and the read paths try the durable store first,
then fall back to the V2 lookup — so every job created before the flag was turned
on still resolves, which is the compatibility guarantee the migration plan makes.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.structured_logging import log_event
from app.models.company import Company
from app.services import research_job
from app.services.jobs import job_contract as contract
from app.services.jobs.job_contract import JobView
from app.services.jobs.job_store import JobStore
from app.services.jobs.worker import JobContext, JobOutcome, register_handler

logger = logging.getLogger(__name__)

#: The ``research_jobs.job_type`` for a full single-company research run.
JOB_TYPE = "company_research"


def durable_enabled() -> bool:
    """Whether this entry point runs on the durable contract.

    Read at call time, never captured at import: a test that flips the setting
    must take effect, and a captured module-level boolean is how a feature flag
    quietly becomes permanent.
    """
    from app.core.config import settings

    return bool(settings.v3_durable_jobs_enabled)


def dedup_key(company_id: uuid.UUID) -> str:
    """The idempotency base key for one company's research.

    Scoped to the company and nothing else, which is deliberately the same rule
    the V2 in-flight check applies: a double-click, a browser retry and a network
    retry all arrive as a second POST for the same company, and all three must be
    answered with the first job. The store turns this base into ``base#N``
    generations so a legitimate re-run next quarter is still possible.
    """
    return f"{JOB_TYPE}:{company_id}"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _session_factory() -> async_sessionmaker[AsyncSession]:
    from app.db.session import async_session_factory

    return async_session_factory


# ---------------------------------------------------------------------------
# Envelope composition
# ---------------------------------------------------------------------------

#: Content fields owned by the AgentStep envelope. The job row has no opinion on
#: any of them, so they pass through untouched.
_CONTENT_FIELDS = (
    "stages_completed",
    "analysis_report_id",
    "agent_run_id",
    "legacy_draft_report_id",
    "report",
    "workflow_status",
    "warnings",
)


async def _inner_envelope(agent_run_id: str | None) -> dict[str, Any]:
    """The V2 envelope this job's workflow is writing, or an empty dict."""
    if not agent_run_id:
        return {}
    from app.services.company_research_service import get_job_envelope

    async with _session_factory()() as session:
        return await get_job_envelope(session, uuid.UUID(agent_run_id)) or {}


async def compose_envelope(
    view: JobView, payload: dict[str, Any], *, now: datetime | None = None
) -> dict[str, Any]:
    """One reader-facing envelope from the job row plus its content record.

    The job row wins on every lifecycle field. The envelope is never consulted
    for status, which is what makes it impossible for a poll to report a job as
    running when the contract has dead-lettered it.
    """
    now = now or _utcnow()
    inner = await _inner_envelope(view.agent_run_id)
    status = contract.derive_status(view, now)

    envelope: dict[str, Any] = {
        "job_id": view.id,
        "status": status,
        "stage": view.stage or inner.get("stage") or research_job.STAGE_QUEUED,
        "company": payload.get("company") or inner.get("company") or {},
        "provider_name": payload.get("provider_name")
        or inner.get("provider_name")
        or "unknown",
        "started_at": _iso(view.started_at) or inner.get("started_at"),
        "completed_at": _iso(view.finished_at) or inner.get("completed_at"),
        "attempt": view.attempt,
        "max_attempts": view.max_attempts,
    }
    for field in _CONTENT_FIELDS:
        envelope[field] = inner.get(field)
    envelope["stages_completed"] = list(
        inner.get("stages_completed") or [research_job.STAGE_QUEUED]
    )
    envelope["warnings"] = list(inner.get("warnings") or [])

    # The job row is authoritative about what the run produced: it was written
    # by the contract on completion, whereas the envelope is written by the work
    # itself and a run killed after linking a report never got to say so.
    if view.result_type == "report" and view.result_ref:
        envelope["analysis_report_id"] = view.result_ref

    # An error a reader may see: the exception TYPE the worker recorded, or the
    # content record's own sanitized reason. Never a message, never a trace.
    envelope["error"] = inner.get("error") or (
        view.error_class if status == contract.STATUS_FAILED else None
    )

    if status == research_job.STATUS_INTERRUPTED:
        envelope["recoverable"] = True
        envelope["interrupted_reason"] = (
            "The worker that owned this run stopped reporting progress on every "
            f"attempt ({view.max_attempts}), so no further attempt will be made "
            "automatically. Nothing already saved was lost, and re-running is "
            "safe — it will not duplicate a completed report."
        )
    if status == contract.STATUS_DEAD_LETTER:
        envelope["recoverable"] = True
        envelope["dead_letter_reason"] = view.dead_letter_reason
    if view.cancel_requested and status in research_job.IN_FLIGHT:
        envelope["cancel_requested"] = True
    return envelope


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).isoformat()


# ---------------------------------------------------------------------------
# Submission and reads
# ---------------------------------------------------------------------------


async def submit(
    company: Company,
    *,
    provider_name: str | None = None,
    use_llm: bool = False,
    llm_provider: str | None = None,
    require_schema_valid: bool = False,
    store: JobStore | None = None,
) -> tuple[dict[str, Any], bool]:
    """Commit a durable research job for one company, or join the live one.

    Returns ``(envelope, created)``. The row is committed before this returns —
    the 202 the caller sends is backed by a job that exists whatever happens to
    the connection next, and by one that survives the process too.

    Takes no request session: the job store owns its own, and the request-scoped
    session is closed the moment the response is sent.
    """
    from app.core.config import settings

    store = store or JobStore()
    provider = provider_name or settings.discovery_default_provider
    view, created = await store.enqueue(
        job_type=JOB_TYPE,
        idempotency_key=dedup_key(company.id),
        company_id=company.id,
        payload={
            # Job INPUTS. No credentials ever go in here — see the job-store
            # security note; this column is not printed by any logging path.
            "company_id": str(company.id),
            "provider_name": provider,
            "use_llm": bool(use_llm),
            "llm_provider": llm_provider,
            "require_schema_valid": bool(require_schema_valid),
            # Identity resolved ONCE, from the Company row, and carried. Nothing
            # downstream re-derives which company this is from a label.
            "company": {
                "id": str(company.id),
                "ticker": company.ticker,
                "exchange": company.exchange,
                "name": company.name,
            },
        },
    )
    log_event(
        logger,
        "v3_company_research_submitted" if created else "v3_company_research_joined",
        job_id=view.id,
        company_id=company.id,
        attempt=view.attempt,
        status=view.status,
    )
    payload = await store.get_payload(view.id) or {}
    return await compose_envelope(view, payload), created


async def get_envelope(
    job_id: uuid.UUID, *, store: JobStore | None = None
) -> dict[str, Any] | None:
    """One durable job's envelope, or None when this id is not a durable job."""
    store = store or JobStore()
    found = await store.get_with_payload(job_id)
    if found is None:
        return None
    view, payload = found
    return await compose_envelope(view, payload)


async def latest_envelope_for_company(
    company_id: uuid.UUID, *, store: JobStore | None = None
) -> dict[str, Any] | None:
    """The most recent durable job for ONE company. Never a global lookup."""
    store = store or JobStore()
    found = await store.latest_for_company(company_id, job_type=JOB_TYPE)
    if found is None:
        return None
    view, payload = found
    return await compose_envelope(view, payload)


# ---------------------------------------------------------------------------
# The handler
# ---------------------------------------------------------------------------


@register_handler(JOB_TYPE)
async def run_company_research_job(ctx: JobContext) -> JobOutcome:
    """Execute one company-research job under a lease.

    Creates the content record on the first attempt and REUSES it on a retry, so
    a retried job keeps one audit trail rather than accumulating an orphaned
    ``AgentRun`` per attempt.

    Every failure is raised, not swallowed: the durable contract decides whether
    it is transient (retry with backoff), permanent (fail now, with attempts
    left, because retrying a bug reproduces the bug at full research cost) or a
    cancellation.
    """
    from app.services import company_research_service as svc

    store = JobStore()
    factory = _session_factory()
    company_id = uuid.UUID(str(ctx.payload["company_id"]))

    content_run_id = ctx.job.agent_run_id
    if content_run_id is None:
        async with factory() as session:
            company = await svc.resolve_company(session, company_id=company_id)
            if company is None:
                raise svc.CompanyResearchFailed("company_not_found")
            inner = await svc.create_job_record(
                session,
                company,
                provider_name=ctx.payload.get("provider_name"),
                use_llm=bool(ctx.payload.get("use_llm")),
                llm_provider=ctx.payload.get("llm_provider"),
                require_schema_valid=bool(ctx.payload.get("require_schema_valid")),
            )
        content_run_id = str(inner["job_id"])
        await store.link_agent_run(
            ctx.job.id, agent_run_id=uuid.UUID(content_run_id)
        )

    await ctx.checkpoint(research_job.STAGE_COMPANY_IDENTITY)

    envelope = await svc.process_company_research_by_id(
        uuid.UUID(content_run_id),
        progress=ctx.checkpoint,
        raise_on_error=True,
    )
    envelope = envelope or {}

    # The council and the report assembly happen inside the final-report
    # generator, which is not a graph node and reports no progress of its own.
    # Recording them at the end is a statement about what RAN, not a claim about
    # when — the same convention the V2 path uses.
    await ctx.checkpoint(research_job.STAGE_COMPLETED)

    report_id = envelope.get("analysis_report_id")
    if not report_id:
        raise svc.CompanyResearchFailed("no_report_produced")
    return JobOutcome(
        result_type="report",
        result_ref=uuid.UUID(str(report_id)),
        warnings=tuple(envelope.get("warnings") or []),
    )
