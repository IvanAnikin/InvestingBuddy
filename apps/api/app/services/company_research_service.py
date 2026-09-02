"""ONE company-research workflow, run asynchronously, from any entry point.

WHY THIS MODULE EXISTS
======================
``/research/company`` — the product's front door — ran the whole pipeline
inside the browser's HTTP request, as two synchronous calls: the
company-analysis workflow, then the final-report generator. Measured on the
live environment that is ~154s of primary-document ingestion plus ~145-190s of
council, against an Azure gateway ceiling of ~230s. The observed result was a
502 at ~206s or a 504 at ~240s, a rolled-back transaction, and a user who
selected Pandora, waited five minutes, and got an error.

The discovery-candidate CTA had already solved exactly this, and its fix was
already live-verified: commit a job envelope, return 202, drive the work from a
background task with its own session, poll a plain GET. What it had NOT done
was generalise — the executor and the job lifecycle were inside
``market_discovery_service``, keyed on a ``DiscoveryCandidate``, so the front
door could not use any of it.

This module is that generalisation:

  * ``execute_company_research`` is the ONE implementation of "research this
    company end to end". ``market_discovery_service.run_candidate_analysis``
    calls it too, passing its discovery lineage — so there is exactly one
    company-research workflow, not two that must be kept in step.
  * The job lifecycle (states, staleness, ``interrupted``) is
    ``app.services.research_job``, shared with the candidate path.
  * The durable job record is an ``AgentRun`` plus one ``AgentStep`` holding
    the envelope. Those tables already exist (no migration), they are the
    system's own auditable record of "a workflow ran", and CLAUDE.md's rule 9
    asks for exactly that. The candidate path keeps its own storage — its
    envelope hangs off the candidate row and its poller is live-verified — but
    both are driven by the same lifecycle and the same executor.

WHAT SURVIVES WHAT
==================
Committed before any expensive work starts: the job row and its ``pending``
envelope. So the browser closing, navigating away or losing the network cannot
affect the run, and the state is recoverable afterwards by job id or by
company.

NOT survived: an app-process recycle mid-run. Execution is process-local —
this deployment has no queue broker or worker service. That is reported
honestly rather than hidden: such a job reads as ``interrupted`` and
``recoverable`` (derived, see ``research_job``), everything already persisted
by the ingestion and workflow stages stays persisted, and re-running is safe.
"""

from __future__ import annotations

import logging
import time
import uuid
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.structured_logging import log_event
from app.db.session import async_session_factory
from app.models.agent_run import AgentRun, AgentStep
from app.models.company import Company
from app.models.report import Report
from app.services import research_job
from app.services.company_service import get_company_by_ticker
from app.workflows.company_analysis import run_company_analysis

logger = logging.getLogger(__name__)

#: Injectable for tests — neither ever touches the network in CI.
AnalysisRunner = Callable[..., Awaitable[dict[str, Any]]]
FinalReportRunner = Callable[..., Awaitable[Any]]

#: The ``AgentRun.workflow_name`` that identifies a company-research job.
JOB_WORKFLOW_NAME = "company_research_job"
JOB_WORKFLOW_VERSION = "1.0.0"

#: The ``AgentStep`` that carries the job envelope. One per job.
JOB_AGENT_NAME = "company_research_job"
JOB_STEP_NAME = "job_envelope"

#: How many recent job steps to scan when answering "the latest job for this
#: company". ``AgentStep.input_json`` is a portable ``sa.JSON`` column, and a
#: JSON-path predicate would render differently on PostgreSQL and on the
#: SQLite the tests run against — so the window is bounded and the match is
#: made in Python. At private-use volumes (a handful of runs a day) this is a
#: few rows; it is bounded so it stays cheap whatever the history grows to.
_RECENT_JOB_SCAN = 200


# ---------------------------------------------------------------------------
# The executor — the ONE company-research workflow
# ---------------------------------------------------------------------------


async def _default_generate_final_report(db: AsyncSession, **kwargs: Any) -> Any:
    """Default final-report runner — the Phase 28A generator from live state."""
    from app.services.final_report_generator import FinalReportGeneratorService

    return await FinalReportGeneratorService().generate_from_workflow_state(
        db, **kwargs
    )


async def _load_final_report_inputs(
    db: AsyncSession, legacy_draft_id: str | None
) -> tuple[Report | None, list[Any], list[Any]]:
    """Best-effort load of the workflow draft plus its citations and sources.

    Never fatal — a failure here just means the final report is built from the
    in-memory workflow state alone.
    """
    if not legacy_draft_id:
        return None, [], []
    try:
        from app.services.final_report_generator import (
            _load_citations_for_report,
            _load_report_by_id,
            _load_sources_for_citations,
        )

        source_report = await _load_report_by_id(db, uuid.UUID(legacy_draft_id))
        if source_report is None:
            return None, [], []
        citations = await _load_citations_for_report(db, source_report.id)
        sources = await _load_sources_for_citations(db, citations)
        return source_report, citations, sources
    except Exception:  # noqa: BLE001 - evidence enrichment is best-effort
        return None, [], []


def company_record_of(company: Company) -> dict[str, Any]:
    """The identity block the final-report generator reads.

    Built from the Company ROW, never from display text. This is what keeps a
    run launched for Pandora a run about Pandora: the ticker, exchange and name
    come from the record the caller selected, and no later step re-derives
    identity from a label.
    """
    return {
        "id": str(company.id),
        "name": company.name,
        "ticker": company.ticker,
        "exchange": company.exchange,
        "country": getattr(company, "country", None),
        "sector": getattr(company, "sector", None),
        "industry": getattr(company, "industry", None),
    }


async def execute_company_research(
    db: AsyncSession,
    company: Company,
    *,
    provider_name: str,
    use_llm: bool = False,
    llm_provider: str | None = None,
    require_schema_valid: bool = False,
    discovery_lineage: dict[str, Any] | None = None,
    on_node: Callable[[str], Awaitable[None]] | None = None,
    run_analysis: AnalysisRunner | None = None,
    generate_final_report: FinalReportRunner | None = None,
) -> dict[str, Any]:
    """Run the full research pipeline for ONE company and return the outcome.

    Two steps, in the order the admin console has always run them: the
    deterministic company-analysis workflow (which produces the raw research
    artefact and persists it as a draft), then the Phase 28A final-report
    generator (which runs the LLM council, when it is enabled and a provider
    resolves, and assembles the structured report).

    The final-report step is guarded exactly as the candidate path guards it: a
    failure there falls back to linking the deterministic draft and is reported
    as a warning, because a run that collected real evidence must not be thrown
    away over its last step.

    Both runners are injectable so tests never touch the network.
    """
    runner = run_analysis or run_company_analysis
    kwargs: dict[str, Any] = {
        "company_id": str(company.id),
        "provider_name": provider_name,
        "use_llm": use_llm,
        "llm_provider": llm_provider,
        "require_schema_valid": require_schema_valid,
    }
    if on_node is not None:
        kwargs["on_node"] = on_node
    final_state = await runner(db, **kwargs)

    legacy_draft_id = final_state.get("draft_report_id")
    agent_run_id = final_state.get("agent_run_id")
    workflow_status = final_state.get("status", "completed")

    warnings: list[str] = []
    report_summary: Any = None
    linked_report_id: uuid.UUID | None = None

    try:
        gen = generate_final_report or _default_generate_final_report
        source_report, citations, sources = await _load_final_report_inputs(
            db, legacy_draft_id
        )
        final_resp = await gen(
            db,
            state=final_state,
            company_record=company_record_of(company),
            # A ScreeningCandidate, not a DiscoveryCandidate — this flow has
            # neither, and identity already comes from the Company row above.
            candidate=None,
            source_report=source_report,
            citations=citations,
            sources=sources,
            discovery_lineage=discovery_lineage,
        )
        linked_report_id = final_resp.report_id
        report_summary = final_resp
    except Exception as exc:  # noqa: BLE001 - never fail the whole run on routing
        logger.warning(
            "final_report_routing_failed company=%s error=%s",
            str(company.id),
            type(exc).__name__,
        )
        warnings.append("final_report_generation_failed")
        if legacy_draft_id:
            linked_report_id = uuid.UUID(legacy_draft_id)

    return {
        "company_id": company.id,
        "ticker": company.ticker,
        "exchange": company.exchange,
        "company_name": company.name,
        "status": workflow_status,
        "analysis_report_id": linked_report_id,
        "agent_run_id": uuid.UUID(agent_run_id) if agent_run_id else None,
        "provider_name": provider_name,
        "final_report_response": report_summary,
        "legacy_draft_report_id": (
            uuid.UUID(legacy_draft_id) if legacy_draft_id else None
        ),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# The durable job record
# ---------------------------------------------------------------------------


def new_envelope(
    *,
    job_id: str,
    status: str,
    stage: str,
    company: dict[str, Any],
    provider_name: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    stages_completed: list[str] | None = None,
    analysis_report_id: str | None = None,
    agent_run_id: str | None = None,
    legacy_draft_report_id: str | None = None,
    report: dict[str, Any] | None = None,
    workflow_status: str | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build one company-research job envelope. Plain JSON-safe values only."""
    return {
        "job_id": job_id,
        "status": status,
        "stage": stage,
        "stages_completed": list(stages_completed or []),
        "started_at": started_at,
        "completed_at": completed_at,
        "company": dict(company),
        "provider_name": provider_name,
        "analysis_report_id": analysis_report_id,
        "agent_run_id": agent_run_id,
        "legacy_draft_report_id": legacy_draft_report_id,
        "report": report,
        "workflow_status": workflow_status,
        "warnings": list(warnings or []),
        "error": error,
    }


async def _load_job_step(db: AsyncSession, job_id: uuid.UUID) -> AgentStep | None:
    row = await db.execute(
        select(AgentStep).where(
            AgentStep.agent_run_id == job_id,
            AgentStep.agent_name == JOB_AGENT_NAME,
        )
    )
    return row.scalar_one_or_none()


async def _write_envelope(
    db: AsyncSession,
    step: AgentStep,
    envelope: dict[str, Any],
    *,
    run: AgentRun | None = None,
) -> None:
    """Persist an envelope and mirror its lifecycle onto the AgentRun row.

    The envelope is REASSIGNED, never mutated in place — an in-place change to
    a JSON column is not tracked by SQLAlchemy and would silently not be saved.
    """
    step.output_json = dict(envelope)
    status = envelope.get("status")
    step.status = "completed" if status in research_job.TERMINAL else "running"
    if run is not None:
        run.status = (
            "failed"
            if status == research_job.STATUS_FAILED
            else "completed"
            if status in research_job.HAS_RESULT
            else "running"
        )
        if status in research_job.TERMINAL:
            run.finished_at = run.finished_at or _utcnow()
            if status == research_job.STATUS_FAILED:
                run.error_message = str(envelope.get("error") or "failed")
    await db.commit()


def _utcnow():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _with_stage(stages: Any, stage: str) -> list[str]:
    """Append ``stage`` to the completed list, once, preserving order."""
    out = list(stages or [])
    if stage not in out:
        out.append(stage)
    return out


async def get_job_envelope(
    db: AsyncSession, job_id: uuid.UUID
) -> dict[str, Any] | None:
    """The stored envelope for one job, or None when no such job exists."""
    step = await _load_job_step(db, job_id)
    if step is None or not isinstance(step.output_json, dict):
        return None
    return dict(step.output_json)


async def latest_job_for_company(
    db: AsyncSession, company_id: uuid.UUID
) -> dict[str, Any] | None:
    """The most recent company-research job for ONE company, or None.

    This is what lets a reader who refreshed the page, or came back an hour
    later, find the run they started — without the browser having had to hold
    a connection open, and without the client being the only place the job id
    existed.

    Scoped strictly to the given company: never a global-latest lookup, and
    never another company's job.
    """
    rows = (
        await db.execute(
            select(AgentStep)
            .where(AgentStep.agent_name == JOB_AGENT_NAME)
            .order_by(AgentStep.started_at.desc())
            .limit(_RECENT_JOB_SCAN)
        )
    ).scalars()
    target = str(company_id)
    for step in rows:
        envelope = step.output_json
        if not isinstance(envelope, dict):
            continue
        company = envelope.get("company")
        if isinstance(company, dict) and str(company.get("id")) == target:
            return dict(envelope)
    return None


async def in_flight_job_for_company(
    db: AsyncSession, company_id: uuid.UUID
) -> dict[str, Any] | None:
    """A pending/running, not-abandoned job for this company, or None.

    This is the whole of the duplicate-submission defence, and it is
    deliberately the whole of it: a double-click, a browser retry and a network
    retry all arrive as a second POST for the same company while the first job
    is still in flight, and all three are answered with the first job. No lock
    table, no client-supplied key, nothing to get out of step with the job
    state itself.
    """
    envelope = await latest_job_for_company(db, company_id)
    if envelope is None:
        return None
    if envelope.get("status") not in research_job.IN_FLIGHT:
        return None
    if research_job.is_stale(envelope):
        # Its worker is gone. Re-running is the right answer, not waiting.
        return None
    return envelope


async def resolve_company(
    db: AsyncSession,
    *,
    company_id: uuid.UUID | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
) -> Company | None:
    """Resolve the company a job is FOR, by id or by (ticker, exchange).

    Identity is resolved ONCE, here, from the database — never re-derived later
    from a report title or a display label.
    """
    if company_id is not None:
        return await db.get(Company, company_id)
    if ticker and exchange:
        return await get_company_by_ticker(db, ticker, exchange)
    return None


async def start_company_research(
    db: AsyncSession,
    company: Company,
    *,
    provider_name: str | None = None,
    use_llm: bool = False,
    llm_provider: str | None = None,
    require_schema_valid: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Create (or return the in-flight) research job for one company.

    Returns ``(envelope, scheduled)``. ``scheduled`` tells the API whether to
    launch the background task. This function NEVER runs the research itself,
    and it commits before returning — so the 202 the caller sends is backed by
    a job that exists whatever happens to the connection next.
    """
    existing = await in_flight_job_for_company(db, company.id)
    if existing is not None:
        log_event(
            logger,
            "company_research_job_duplicate",
            company_id=company.id,
            status=existing.get("status"),
        )
        return existing, False

    provider = provider_name or settings.discovery_default_provider
    run = AgentRun(
        workflow_name=JOB_WORKFLOW_NAME,
        workflow_version=JOB_WORKFLOW_VERSION,
        trigger_type="manual",
        status="running",
    )
    db.add(run)
    await db.flush()

    envelope = new_envelope(
        job_id=str(run.id),
        status=research_job.STATUS_PENDING,
        stage=research_job.STAGE_QUEUED,
        stages_completed=[research_job.STAGE_QUEUED],
        started_at=research_job.now_iso(),
        company={
            "id": str(company.id),
            "ticker": company.ticker,
            "exchange": company.exchange,
            "name": company.name,
        },
        provider_name=provider,
    )
    step = AgentStep(
        agent_run_id=run.id,
        agent_name=JOB_AGENT_NAME,
        step_name=JOB_STEP_NAME,
        status="running",
        input_json={
            "company_id": str(company.id),
            "ticker": company.ticker,
            "exchange": company.exchange,
            "provider_name": provider,
            "use_llm": use_llm,
            "llm_provider": llm_provider,
            "require_schema_valid": require_schema_valid,
        },
        output_json=envelope,
    )
    db.add(step)
    await db.commit()
    log_event(
        logger,
        "company_research_job_queued",
        company_id=company.id,
        job_id=run.id,
        status=research_job.STATUS_PENDING,
    )
    return envelope, True


def _report_summary_dict(final_report_response: Any) -> dict[str, Any] | None:
    """Compact metadata about the generated report, for the UI's link label."""
    if final_report_response is None:
        return None
    try:
        return {
            "report_id": str(final_report_response.report_id),
            "report_kind": "final",
            "llm_used": bool(getattr(final_report_response, "llm_used", False)),
            "schema_valid": getattr(final_report_response, "schema_valid", None),
            "safety_valid": getattr(final_report_response, "safety_valid", None),
        }
    except Exception:  # noqa: BLE001 - a label must never break a job
        return None


async def process_company_research_by_id(
    job_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    run_analysis: AnalysisRunner | None = None,
    generate_final_report: FinalReportRunner | None = None,
) -> None:
    """Background worker: run ONE company-research job in a FRESH session.

    Must NOT reuse the request-scoped session — the 202 has already been sent
    and that session is closed. Every failure path persists a terminal
    envelope, so a job can never stick in ``running`` because of an error we
    saw. Only ids, statuses, stages and durations are logged — never prompts,
    completions, evidence excerpts or credentials.
    """
    factory = session_factory or async_session_factory
    start = time.perf_counter()
    try:
        async with factory() as session:
            step = await _load_job_step(session, job_id)
            if step is None:
                logger.warning(
                    "Company research: job %s not found for background run.", job_id
                )
                return
            run = await session.get(AgentRun, job_id)
            envelope = dict(step.output_json or {})
            request = dict(step.input_json or {})

            company = await resolve_company(
                session, company_id=uuid.UUID(str(request.get("company_id")))
            )
            if company is None:
                envelope.update(
                    status=research_job.STATUS_FAILED,
                    stage=research_job.STAGE_FAILED,
                    completed_at=research_job.now_iso(),
                    error="company_not_found",
                )
                await _write_envelope(session, step, envelope, run=run)
                return

            envelope["status"] = research_job.STATUS_RUNNING
            envelope["stage"] = research_job.STAGE_COMPANY_IDENTITY
            envelope["stages_completed"] = _with_stage(
                envelope.get("stages_completed"), research_job.STAGE_COMPANY_IDENTITY
            )
            await _write_envelope(session, step, envelope, run=run)
            log_event(
                logger,
                "company_research_job_started",
                job_id=job_id,
                company_id=company.id,
                status=research_job.STATUS_RUNNING,
            )

            # Stage progress. The graph reports its OWN node names; this maps
            # them onto reader-facing stages and persists a change only when
            # the stage actually moves, so a five-minute run writes a handful
            # of rows rather than one per node.
            async def on_node(node_name: str) -> None:
                stage = research_job.stage_for_node(node_name)
                if stage is None or stage == envelope.get("stage"):
                    return
                envelope["stage"] = stage
                envelope["stages_completed"] = _with_stage(
                    envelope.get("stages_completed"), stage
                )
                await _write_envelope(session, step, envelope, run=run)

            try:
                result = await execute_company_research(
                    session,
                    company,
                    provider_name=str(
                        request.get("provider_name")
                        or settings.discovery_default_provider
                    ),
                    use_llm=bool(request.get("use_llm")),
                    llm_provider=request.get("llm_provider"),
                    require_schema_valid=bool(request.get("require_schema_valid")),
                    on_node=on_node,
                    run_analysis=run_analysis,
                    generate_final_report=generate_final_report,
                )
            except Exception as exc:  # noqa: BLE001 - persist, never swallow silently
                logger.exception(
                    "Company research job %s failed during execution: %s", job_id, exc
                )
                envelope.update(
                    status=research_job.STATUS_FAILED,
                    stage=research_job.STAGE_FAILED,
                    completed_at=research_job.now_iso(),
                    error="internal_error",
                )
                await _write_envelope(session, step, envelope, run=run)
                log_event(
                    logger,
                    "company_research_job_failed",
                    level=logging.ERROR,
                    job_id=job_id,
                    company_id=company.id,
                    status=research_job.STATUS_FAILED,
                    reason="internal_error",
                    exception_type=type(exc).__name__,
                    duration_ms=int((time.perf_counter() - start) * 1000),
                )
                return

            warnings = list(result.get("warnings") or [])
            report_id = result.get("analysis_report_id")
            if report_id is None:
                status = research_job.STATUS_FAILED
                stage = research_job.STAGE_FAILED
                error: str | None = "no_report_produced"
            else:
                status = (
                    research_job.STATUS_COMPLETED_WITH_WARNINGS
                    if warnings
                    else research_job.STATUS_COMPLETED
                )
                stage = research_job.STAGE_COMPLETED
                error = None

            # The council and the report assembly both happen inside the
            # final-report generator, which is not a graph node and so reports
            # no progress of its own. Recording them here on completion is a
            # statement about what RAN, not a claim about when.
            stages = envelope.get("stages_completed")
            for extra in (
                research_job.STAGE_COUNCIL_ANALYSIS,
                research_job.STAGE_REPORT_ASSEMBLY,
                stage,
            ):
                stages = _with_stage(stages, extra)

            envelope.update(
                status=status,
                stage=stage,
                stages_completed=stages,
                completed_at=research_job.now_iso(),
                analysis_report_id=str(report_id) if report_id else None,
                agent_run_id=(
                    str(result["agent_run_id"]) if result.get("agent_run_id") else None
                ),
                legacy_draft_report_id=(
                    str(result["legacy_draft_report_id"])
                    if result.get("legacy_draft_report_id")
                    else None
                ),
                report=_report_summary_dict(result.get("final_report_response")),
                workflow_status=result.get("status"),
                warnings=warnings,
                error=error,
            )
            await _write_envelope(session, step, envelope, run=run)
            log_event(
                logger,
                "company_research_job_completed",
                job_id=job_id,
                company_id=company.id,
                status=status,
                warning_count=len(warnings),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
    except Exception as exc:  # noqa: BLE001 — must not crash the worker
        logger.exception("Company research job %s crashed: %s", job_id, exc)
        await _mark_failed_fresh(factory, job_id, reason="internal_error")


async def _mark_failed_fresh(
    factory: async_sessionmaker[AsyncSession],
    job_id: uuid.UUID,
    *,
    reason: str,
) -> None:
    """Best-effort: mark a job ``failed`` in a fresh session after a crash."""
    try:
        async with factory() as session:
            step = await _load_job_step(session, job_id)
            if step is None or not isinstance(step.output_json, dict):
                return
            envelope = dict(step.output_json)
            if envelope.get("status") in research_job.HAS_RESULT:
                return
            envelope.update(
                status=research_job.STATUS_FAILED,
                stage=research_job.STAGE_FAILED,
                completed_at=research_job.now_iso(),
                error=reason,
            )
            run = await session.get(AgentRun, job_id)
            await _write_envelope(session, step, envelope, run=run)
    except Exception:  # noqa: BLE001
        logger.exception("Failed to mark company research job %s as failed.", job_id)


async def process_company_research_task(job_id: str) -> None:
    """FastAPI ``BackgroundTasks`` entry point for one company-research job.

    Takes only a primitive id (never an ORM object or the request session), and
    swallows exceptions so a background failure can never surface to — or
    crash — the request handler that already returned 202.
    """
    try:
        await process_company_research_by_id(uuid.UUID(job_id))
    except Exception:  # noqa: BLE001
        logger.exception("Background company research task crashed for job %s", job_id)


async def sweep_interrupted_company_jobs(
    session: AsyncSession, *, limit: int = _RECENT_JOB_SCAN
) -> list[dict[str, Any]]:
    """Report every in-flight company-research job whose worker is gone.

    READ-ONLY, and called at application startup when the process that owned
    any in-flight job has by definition just died. It writes nothing: the
    stored envelope stays exactly as the dead worker left it, so the record of
    what it was doing is intact, and a job genuinely still running under
    another live process is not stolen from it. Its whole job is to make the
    loss VISIBLE in the startup logs, alongside the ``interrupted`` status the
    API already derives.
    """
    rows = (
        await session.execute(
            select(AgentStep)
            .where(AgentStep.agent_name == JOB_AGENT_NAME)
            .order_by(AgentStep.started_at.desc())
            .limit(max(1, limit))
        )
    ).scalars()
    interrupted: list[dict[str, Any]] = []
    for step in rows:
        envelope = step.output_json
        if not isinstance(envelope, dict):
            continue
        if envelope.get("status") not in research_job.IN_FLIGHT:
            continue
        if not research_job.is_stale(envelope):
            continue
        company = envelope.get("company") or {}
        interrupted.append(
            {
                "job_id": envelope.get("job_id"),
                "ticker": company.get("ticker") if isinstance(company, dict) else None,
                "status": envelope.get("status"),
                "started_at": envelope.get("started_at"),
            }
        )
    return interrupted
