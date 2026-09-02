"""ONE lifecycle for every long-running research job.

WHY THIS MODULE EXISTS
======================
A full company research run takes minutes: primary-document ingestion alone
measured ~154s on staging and the council another ~145-190s. Azure App
Service's gateway gives up at ~230s. So any entry point that runs the pipeline
inside the HTTP request fails in the same way — the browser gets a 502 around
206s or a 504 around 240s, the transaction rolls back, and the user who waited
five minutes has nothing.

The discovery-candidate CTA solved that first: write a job envelope, COMMIT it,
return 202, and drive the real work from a background task with its own DB
session while the UI polls a plain GET. That is the mechanism this corrective
reuses; ``/research/company`` was still synchronous and blew the same ceiling.

What sat in the way of reuse was that the lifecycle RULES — which states are in
flight, when a ``running`` job has been abandoned, how long "too long" is —
lived inside ``market_discovery_service`` next to its candidate-specific
storage. They are not discovery concepts. They are here now, once, so the two
entry points cannot drift apart on the question of whether a job is still
alive.

WHAT "DURABLE" MEANS HERE, EXACTLY
==================================
This is worth stating plainly because it is easy to overclaim.

The job STATE is durable: it is committed to PostgreSQL before any expensive
work starts, and every terminal path commits its outcome. Closing the browser,
navigating away, losing the network — none of them affect the run, and the
state is recoverable by id or by company afterwards.

The job EXECUTION is process-local. There is no queue broker and no separate
worker service in this deployment (Service Bus is a later phase), so the work
runs in the API process that accepted it. If that process recycles mid-run, the
work stops. That is not hidden: a ``running`` envelope with no progress inside
the derived worst-case duration reads as ``interrupted`` and ``recoverable``,
computed at read time from the same threshold the restart decision uses, so the
two can never disagree. Nothing already persisted is lost, and re-running is
safe.

Deriving ``interrupted`` rather than storing it is deliberate. A stored status
would be a second source of truth about the same job, and it would need a
writer that is itself running — which is precisely what is absent in the case
it describes.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.core.config import DEPLOYED_GUNICORN_WORKERS, settings

# ---------------------------------------------------------------------------
# States
# ---------------------------------------------------------------------------

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_COMPLETED_WITH_WARNINGS = "completed_with_warnings"
STATUS_FAILED = "failed"

#: DERIVED at read time, never stored. See the module docstring.
STATUS_INTERRUPTED = "interrupted"

#: A job in one of these states is in flight — a second submit must NEVER start
#: a duplicate (expensive) run.
IN_FLIGHT: frozenset[str] = frozenset({STATUS_PENDING, STATUS_RUNNING})

#: A job in one of these states produced a linked report.
HAS_RESULT: frozenset[str] = frozenset(
    {STATUS_COMPLETED, STATUS_COMPLETED_WITH_WARNINGS}
)

TERMINAL: frozenset[str] = HAS_RESULT | frozenset({STATUS_FAILED})


# ---------------------------------------------------------------------------
# Stages
# ---------------------------------------------------------------------------
#
# The stage names a reader sees. They are the WORKFLOW'S OWN node names mapped
# onto plain words — not a parallel vocabulary invented for the UI, and not a
# percentage. The pipeline cannot know how long a node will take, so claiming
# "62% complete" would be a fabrication; naming the stage that is running is
# the honest alternative and it is the one thing a waiting reader actually
# wants to know.

STAGE_QUEUED = "queued"
STAGE_COMPANY_IDENTITY = "company_identity"
STAGE_SOURCE_DISCOVERY = "source_discovery"
STAGE_DOCUMENT_INGESTION = "primary_document_ingestion"
STAGE_FINANCIAL_EXTRACTION = "financial_extraction"
STAGE_EVIDENCE_VALIDATION = "evidence_validation"
STAGE_COUNCIL_ANALYSIS = "council_analysis"
STAGE_REPORT_ASSEMBLY = "final_report_assembly"
STAGE_COMPLETED = "completed"
STAGE_FAILED = "failed"

#: Stage order, for a UI that wants to show what is still ahead.
STAGE_ORDER: tuple[str, ...] = (
    STAGE_QUEUED,
    STAGE_COMPANY_IDENTITY,
    STAGE_SOURCE_DISCOVERY,
    STAGE_DOCUMENT_INGESTION,
    STAGE_FINANCIAL_EXTRACTION,
    STAGE_EVIDENCE_VALIDATION,
    STAGE_COUNCIL_ANALYSIS,
    STAGE_REPORT_ASSEMBLY,
    STAGE_COMPLETED,
)

STAGE_LABELS: dict[str, str] = {
    STAGE_QUEUED: "Queued",
    STAGE_COMPANY_IDENTITY: "Resolving the company's identity",
    STAGE_SOURCE_DISCOVERY: "Locating the issuer's sources",
    STAGE_DOCUMENT_INGESTION: "Reading the issuer's own documents",
    STAGE_FINANCIAL_EXTRACTION: "Extracting period-labelled financial facts",
    STAGE_EVIDENCE_VALIDATION: "Validating and citing the evidence",
    STAGE_COUNCIL_ANALYSIS: "Running the research council",
    STAGE_REPORT_ASSEMBLY: "Assembling the research report",
    STAGE_COMPLETED: "Complete",
    STAGE_FAILED: "Failed",
}

#: Which stage each company-analysis graph node belongs to.
#:
#: The graph's node names are the source of truth — this maps them onto the
#: reader-facing stage they serve. A node absent from this map does not move
#: the stage, which is why adding a node to the graph cannot silently make the
#: UI claim progress it has no basis for.
NODE_TO_STAGE: dict[str, str] = {
    "load_company": STAGE_COMPANY_IDENTITY,
    "fetch_provider_data": STAGE_SOURCE_DISCOVERY,
    "create_source_records": STAGE_SOURCE_DISCOVERY,
    "build_company_snapshot": STAGE_DOCUMENT_INGESTION,
    "financial_data_agent": STAGE_FINANCIAL_EXTRACTION,
    "source_quality_agent": STAGE_EVIDENCE_VALIDATION,
    "generate_research_sections": STAGE_EVIDENCE_VALIDATION,
    "create_citations": STAGE_EVIDENCE_VALIDATION,
    "validate_report_schema": STAGE_EVIDENCE_VALIDATION,
    "research_completeness_agent": STAGE_EVIDENCE_VALIDATION,
    "citation_validator_v2": STAGE_EVIDENCE_VALIDATION,
    "bull_case_agent": STAGE_EVIDENCE_VALIDATION,
    "bear_case_agent": STAGE_EVIDENCE_VALIDATION,
    "risk_agent": STAGE_EVIDENCE_VALIDATION,
    "valuation_guard_agent": STAGE_EVIDENCE_VALIDATION,
    "investment_committee_chair": STAGE_EVIDENCE_VALIDATION,
    "catalyst_discovery_agent": STAGE_EVIDENCE_VALIDATION,
    "score_research_attractiveness": STAGE_EVIDENCE_VALIDATION,
    "save_draft_report": STAGE_EVIDENCE_VALIDATION,
    "log_agent_steps": STAGE_EVIDENCE_VALIDATION,
}


def stage_for_node(node_name: str) -> str | None:
    """The reader-facing stage one graph node belongs to, or None."""
    return NODE_TO_STAGE.get(node_name)


def stage_label(stage: str | None) -> str:
    """Human words for a stage. Never emits a raw identifier into the UI."""
    if not stage:
        return STAGE_LABELS[STAGE_QUEUED]
    return STAGE_LABELS.get(stage, stage.replace("_", " ").capitalize())


# ---------------------------------------------------------------------------
# Abandonment
# ---------------------------------------------------------------------------

#: Fixed allowance (seconds) for everything in a research job that is NOT the
#: council or primary-document ingestion: data fetching, snapshot build, report
#: assembly/persistence, commits.
_OVERHEAD_SECONDS = 300.0

#: Safety margin (minutes) on top of the derived worst-case duration before a
#: ``running`` envelope may be treated as abandoned.
_STALE_MARGIN_MINUTES = 10


def stale_after_minutes(cfg: Any | None = None) -> int:
    """Effective abandoned-job threshold (minutes), coherent BY CONSTRUCTION.

    ``max(configured base, derived worst case)`` where the worst case = council
    wall budget + one full pacing wait (an in-flight attempt can wait past the
    council deadline) + primary-document ingestion budget + fixed orchestration
    overhead + margin. Raising any council budget automatically raises this
    threshold with it, so a legitimately long run can never be declared dead
    because a budget was tuned somewhere else.
    """
    cfg = cfg or settings
    worst_case_seconds = (
        float(cfg.llm_council_total_budget_seconds)
        + float(cfg.llm_council_pacing_max_wait_seconds)
        + float(cfg.primary_document_ingestion_budget_seconds)
        + _OVERHEAD_SECONDS
    )
    derived_minutes = int(worst_case_seconds // 60) + 1 + _STALE_MARGIN_MINUTES
    return max(int(cfg.analysis_job_stale_after_minutes), derived_minutes)


def _aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


#: When THIS process booted. A job whose ``started_at`` precedes this cannot be
#: running in this process — see :func:`is_orphaned`.
PROCESS_BOOT_AT = datetime.now(timezone.utc)


def _started_at_of(envelope: dict[str, Any]) -> datetime | None:
    """The envelope's ``started_at`` as an aware datetime, or None if unusable."""
    started = envelope.get("started_at")
    if not isinstance(started, str) or not started:
        return None
    try:
        return _aware(datetime.fromisoformat(started))
    except ValueError:
        return None


def is_orphaned(envelope: dict[str, Any], *, cfg: Any | None = None) -> bool:
    """True when the process that owned this job is provably gone.

    The execution of a job is process-local (see the module docstring). With
    exactly ONE gunicorn worker that gives a certainty the elapsed-time
    heuristic cannot: if a job started before this process booted, the process
    that was running it no longer exists, so the job is dead NOW — not
    "possibly dead in another 43 minutes".

    That distinction is the whole point. A worker killed two minutes into a run
    used to keep reporting ``running`` for the full 45-minute worst case, which
    is how a dead job came to look like a slow one.

    Disables itself automatically at 2+ workers, where the premise fails: a peer
    worker may still be running the job, and gunicorn respawns workers
    individually, so a fresh worker's boot time says nothing about its peers'
    jobs. Falling back to the elapsed-time threshold is correct there.
    """
    if envelope.get("status") not in IN_FLIGHT:
        return False
    if DEPLOYED_GUNICORN_WORKERS != 1:
        return False
    started_dt = _started_at_of(envelope)
    if started_dt is None:
        # No timestamp to reason about — treat as in flight, never as abandoned.
        return False
    return started_dt < PROCESS_BOOT_AT


def is_stale(envelope: dict[str, Any], *, cfg: Any | None = None) -> bool:
    """True when a job has clearly been abandoned by a dead worker.

    Two independent ways to know, because they answer the question at different
    speeds and neither subsumes the other:

    * :func:`is_orphaned` — the owning process is provably gone (immediate).
    * elapsed time — a ``running`` job past its derived worst-case duration.

    Deliberately ONE predicate rather than two. This drives both what a reader
    is shown AND whether a resubmit is allowed to start a fresh run, so a split
    would let the UI say "re-running is safe" while the submit endpoint refused
    to start one — a job contradicting its own state.
    """
    if is_orphaned(envelope, cfg=cfg):
        return True
    if envelope.get("status") != STATUS_RUNNING:
        return False
    started_dt = _started_at_of(envelope)
    if started_dt is None:
        return False
    age = datetime.now(timezone.utc) - started_dt
    return age > timedelta(minutes=stale_after_minutes(cfg))


def interrupted_reason(cfg: Any | None = None, envelope: dict[str, Any] | None = None) -> str:
    """The explanation attached to an ``interrupted`` job. Never a stack trace.

    Names the ACTUAL reason: an orphaned job is known dead because the process
    restarted, which is a different (and more certain) statement than "no
    progress within the worst case". Telling a reader the run timed out when it
    was actually killed by a restart would be a small lie about their run.
    """
    if envelope is not None and is_orphaned(envelope, cfg=cfg):
        return (
            "The API process restarted while this job was running, so the run "
            "stopped. Nothing already saved was lost, and re-running is safe — "
            "it will not duplicate a completed report."
        )
    return (
        "No progress within the expected worst-case duration "
        f"({stale_after_minutes(cfg)} min). The worker that owned this job is "
        "gone — most likely an app restart. Nothing was lost: re-running is "
        "safe and will not duplicate a completed report."
    )


def describe(envelope: dict[str, Any] | None, *, cfg: Any | None = None) -> dict[str, Any]:
    """The envelope a HUMAN should see, with abandonment made explicit.

    Does not mutate the stored envelope: the dead worker's last write stays
    exactly as it left it, so the audit trail of what it was doing is intact,
    and a job genuinely still running under ANOTHER live process is not stolen
    from it.
    """
    if not envelope:
        return {}
    out = dict(envelope)
    if out.get("status") in IN_FLIGHT and is_stale(out, cfg=cfg):
        # Resolve the REASON before overwriting the status: both abandonment
        # rules key off an in-flight status, so asking afterwards would always
        # fall through to the elapsed-time wording.
        reason = interrupted_reason(cfg, out)
        out["status"] = STATUS_INTERRUPTED
        out["recoverable"] = True
        out["interrupted_reason"] = reason
    return out


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()
