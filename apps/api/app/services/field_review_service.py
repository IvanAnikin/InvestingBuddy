"""
Deep Field Review orchestration service — Phase 32A Slice 6D.

Resolves which of a discovery run's candidates have a USABLE completed full
analysis, builds the bounded comparative pack from their already-persisted
reports, runs the Deep Field Review council as an ASYNC background job, and
persists the result (plus one honest row per candidate — included OR excluded).

WHAT THIS IS NOT: it is neither the discovery council (which triages a candidate
LIST before any analysis exists) nor the single-company council (which analyses
ONE company). It compares completed analyses; it never re-runs one.

Input-resolution rules (deliberate, and load-bearing):

  * ``DiscoveryCandidate.analysis_report_id`` is the SINGLE authoritative
    per-candidate linkage. There is intentionally NO "latest report for this
    company_id" fallback: substituting a report that was generated for a
    DIFFERENT run of the same company is exactly the class of bug this project
    already fixed once (Phase 32A from-company scoping hotfix), and it would
    silently corrupt a comparison.
  * A candidate that cannot be compared is NEVER silently dropped. It gets a row
    with ``included=False`` and a closed-vocabulary ``exclusion_reason``
    (CLAUDE.md rule 8: rejected/failed cases are learning data).
  * A report whose data provenance is mock/unknown is INCLUDED, with an explicit
    caveat — never dropped and never presented as real.
  * Fewer than ``field_review_min_candidates`` comparable candidates yields an
    explicit ``insufficient_candidates`` terminal state. The council never runs
    on a degenerate field.

Logging is structured and safe: ids, statuses, counts, durations — never
prompts, completions, report bodies, or credentials.
"""

from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.structured_logging import log_event
from app.db.session import async_session_factory
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.field_review import FieldReviewCandidateSummary, FieldReviewRun
from app.models.report import Report
from app.services import safety_terms
from app.services.field_review_evidence_pack import (
    build_company_summary,
    build_field_review_pack,
)
from app.services.llm.field_review_council import (
    field_review_council_enabled,
    maybe_run_field_review_council,
)
from app.services.llm.field_review_schemas import (
    FieldReviewCompanySummary,
    FieldReviewPack,
    FieldReviewResult,
)
from app.services.primary_document_view_service import get_report_primary_documents

logger = logging.getLogger(__name__)

__all__ = [
    "FieldReviewDisabledError",
    "InsufficientAnalyzedCandidatesError",
    "CandidateResolution",
    "FieldEligibilityRow",
    "FieldEligibilitySummary",
    "resolve_field_candidates",
    "summarize_field_eligibility",
    "required_candidate_count",
    "get_latest_field_review",
    "start_field_review",
    "process_field_review_by_id",
    "process_field_review_task",
    "field_review_enabled",
]

# Terminal statuses — the job has finished (successfully or not).
_TERMINAL = {
    "completed",
    "completed_with_warnings",
    "failed",
    "insufficient_candidates",
}
# Non-terminal statuses — a job is queued or in flight.
_IN_FLIGHT = {"pending", "running"}
# A usable completed review exists in one of these states.
_HAS_REVIEW = {"completed", "completed_with_warnings"}

# ``do_not_infer`` enumerates what must NOT be produced, so it necessarily names
# the forbidden phrases. Scanning it would be a guaranteed false positive.
_SAFETY_EXEMPT_KEYS = frozenset({"do_not_infer"})


class FieldReviewDisabledError(Exception):
    """The Deep Field Review is disabled (flag off or no provider available)."""


class InsufficientAnalyzedCandidatesError(Exception):
    """Fewer than ``field_review_min_candidates`` candidates are comparable.

    Carries the honest breakdown so the API can tell the admin exactly which
    candidates exist and why each one could not be compared.
    """

    def __init__(
        self,
        message: str,
        *,
        included: int,
        required: int,
        missing: list[dict[str, Any]],
    ) -> None:
        super().__init__(message)
        self.included = included
        self.required = required
        self.missing = missing


def field_review_enabled(cfg: Any | None = None) -> bool:
    """True only when BOTH the shared client gate and the field-review gate are on."""
    return field_review_council_enabled(cfg or settings)


# ---------------------------------------------------------------------------
# Input resolution
# ---------------------------------------------------------------------------


# Closed vocabulary of exclusion reasons emitted by ``resolve_field_candidates``.
# Kept immediately next to the resolver so the two can never drift apart.
#
#   no_analysis_run  — step 1 failed: the candidate was never analysed at all.
#   report_deleted   — step 2 failed: the linked report row no longer exists.
#   draft_only       — step 3 failed: the report is a draft, not a FINAL report.
#   not_schema_valid — step 4 failed: the report did not pass schema validation.
#   over_company_cap — steps 1–4 PASSED; only the max_companies cap kept the
#                      candidate out of THIS review. It still legitimately "has a
#                      completed full analysis".
EXCLUSION_REASON_NO_ANALYSIS = "no_analysis_run"
EXCLUSION_REASON_REPORT_DELETED = "report_deleted"
EXCLUSION_REASON_DRAFT_ONLY = "draft_only"
EXCLUSION_REASON_NOT_SCHEMA_VALID = "not_schema_valid"
EXCLUSION_REASON_CAPPED = "over_company_cap"
# Every reason that implies the candidate DID have an ``analysis_report_id``
# (i.e. it was actually analysed) but could not be compared in this review.
EXCLUSION_REASONS_WITH_ANALYSIS = frozenset(
    {
        EXCLUSION_REASON_REPORT_DELETED,
        EXCLUSION_REASON_DRAFT_ONLY,
        EXCLUSION_REASON_NOT_SCHEMA_VALID,
        EXCLUSION_REASON_CAPPED,
    }
)


class CandidateResolution:
    """The honest outcome of resolving one run's candidates for comparison."""

    def __init__(self) -> None:
        # (candidate, report, citation_ref) for every comparable candidate.
        self.included: list[tuple[DiscoveryCandidate, Report, str]] = []
        # One dict per NON-comparable candidate, with its exclusion reason.
        self.missing: list[dict[str, Any]] = []
        # How many candidates had an analysis_report_id at all.
        self.analyzed_candidate_count = 0
        # Total candidates in the run.
        self.candidate_count = 0


def _candidate_sort_key(candidate: DiscoveryCandidate) -> tuple[int, int]:
    """Rank ascending, NULLs last — the run's own prioritization order."""
    if candidate.rank is None:
        return (1, 0)
    return (0, candidate.rank)


def _schema_valid(report: Report) -> bool:
    validation = report.schema_validation_json
    if not isinstance(validation, dict):
        return False
    return bool(validation.get("schema_valid") or validation.get("is_valid"))


async def resolve_field_candidates(
    db: AsyncSession, run: DiscoveryRun, *, cfg: Any | None = None
) -> CandidateResolution:
    """Resolve which of THIS run's candidates have a usable completed analysis.

    Scoped strictly to ``run.id``: a field review for run A can never see run B's
    candidates, even when both runs contain the same company.
    """
    cfg = cfg or settings
    resolution = CandidateResolution()

    result = await db.execute(
        select(DiscoveryCandidate).where(
            DiscoveryCandidate.discovery_run_id == run.id
        )
    )
    candidates = sorted(result.scalars().all(), key=_candidate_sort_key)
    resolution.candidate_count = len(candidates)

    max_companies = max(1, int(cfg.llm_field_review_council_max_companies))

    for candidate in candidates:
        base = {
            "discovery_candidate_id": str(candidate.id),
            "report_id": None,
            "ticker": candidate.ticker,
            "exchange": candidate.exchange,
        }

        # 1. Never analysed. The candidate exists but "Run Full Analysis" was
        #    never completed for it.
        if candidate.analysis_report_id is None:
            resolution.missing.append(
                {**base, "exclusion_reason": EXCLUSION_REASON_NO_ANALYSIS}
            )
            continue

        resolution.analyzed_candidate_count += 1

        # 2. The authoritative per-candidate report link. Deliberately NO
        #    company_id-latest fallback (see the module docstring).
        report_result = await db.execute(
            select(Report).where(Report.id == candidate.analysis_report_id)
        )
        report = report_result.scalar_one_or_none()
        base["report_id"] = str(candidate.analysis_report_id)
        if report is None:
            resolution.missing.append(
                {**base, "exclusion_reason": EXCLUSION_REASON_REPORT_DELETED}
            )
            continue

        # 3. Only a FINAL report is comparable; a draft has no assembled sections.
        if report.final_report_version is None:
            resolution.missing.append(
                {**base, "exclusion_reason": EXCLUSION_REASON_DRAFT_ONLY}
            )
            continue

        # 4. A schema-invalid report cannot be compared field-for-field.
        if not _schema_valid(report):
            resolution.missing.append(
                {**base, "exclusion_reason": EXCLUSION_REASON_NOT_SCHEMA_VALID}
            )
            continue

        # 5. Bound the pack. Candidates beyond the cap are excluded HONESTLY
        #    (recorded with a reason), never quietly truncated away.
        if len(resolution.included) >= max_companies:
            resolution.missing.append(
                {**base, "exclusion_reason": EXCLUSION_REASON_CAPPED}
            )
            continue

        citation_ref = f"F{len(resolution.included) + 1}"
        resolution.included.append((candidate, report, citation_ref))

    return resolution


def required_candidate_count(cfg: Any | None = None) -> int:
    """How many comparable candidates a Deep Field Review needs (never below 2).

    Single source of truth: both ``start_field_review`` (which raises the 422)
    and the eligibility summary (which gates the admin button) read it, so the
    UI can never advertise a threshold the backend does not enforce.
    """
    cfg = cfg or settings
    return max(2, int(cfg.field_review_min_candidates))


@dataclass(frozen=True)
class FieldEligibilityRow:
    """One candidate's eligibility state, as the field review itself sees it."""

    candidate_id: uuid.UUID
    ticker: str | None
    exchange: str | None
    company_name: str | None
    # Internal candidate-score grade (prioritization signal only, never a rating).
    tier: str | None
    # The candidate has an ``analysis_report_id`` — a full analysis was run.
    has_analysis: bool
    # Steps 1–4 passed: linked report exists, is FINAL, and is schema-valid.
    has_full_analysis: bool
    # Also within ``max_companies`` — would actually be compared right now.
    included: bool
    exclusion_reason: str | None


@dataclass(frozen=True)
class FieldEligibilitySummary:
    """A bounded, honest answer to "what would a field review compare now?"."""

    candidate_count: int
    with_full_analysis_count: int
    included_count: int
    not_comparable_count: int
    not_yet_analyzed_count: int
    required_candidate_count: int
    max_companies: int
    candidates: list[FieldEligibilityRow]


async def summarize_field_eligibility(
    db: AsyncSession, run: DiscoveryRun, *, cfg: Any | None = None
) -> FieldEligibilitySummary:
    """Summarize which of a run's candidates a field review could compare NOW.

    This is a pure DERIVATION of ``resolve_field_candidates`` — the eligibility
    rules live in exactly one place. Nothing here re-decides whether a candidate
    is comparable; it only re-groups the resolver's own verdicts and attaches
    display fields (name/tier) so the admin UI never has to guess.
    """
    cfg = cfg or settings
    resolution = await resolve_field_candidates(db, run, cfg=cfg)

    included_ids = {candidate.id for candidate, _report, _ref in resolution.included}
    reason_by_id: dict[str, str | None] = {
        str(entry.get("discovery_candidate_id")): entry.get("exclusion_reason")
        for entry in resolution.missing
    }

    # Display fields only — the eligibility verdict above is never recomputed here.
    result = await db.execute(
        select(DiscoveryCandidate).where(
            DiscoveryCandidate.discovery_run_id == run.id
        )
    )
    candidates = sorted(result.scalars().all(), key=_candidate_sort_key)

    rows: list[FieldEligibilityRow] = []
    for candidate in candidates:
        included = candidate.id in included_ids
        reason = None if included else reason_by_id.get(str(candidate.id))
        rows.append(
            FieldEligibilityRow(
                candidate_id=candidate.id,
                ticker=candidate.ticker,
                exchange=candidate.exchange,
                company_name=candidate.company_name,
                tier=candidate.candidate_score_grade,
                has_analysis=included or reason in EXCLUSION_REASONS_WITH_ANALYSIS,
                # A capped-out candidate still HAS a completed full analysis; it
                # is simply not part of this particular comparison.
                has_full_analysis=included or reason == EXCLUSION_REASON_CAPPED,
                included=included,
                exclusion_reason=reason,
            )
        )

    included_count = len(resolution.included)
    return FieldEligibilitySummary(
        candidate_count=resolution.candidate_count,
        with_full_analysis_count=sum(1 for row in rows if row.has_full_analysis),
        included_count=included_count,
        # Analysed, but not comparable in this review (deleted/draft/invalid/capped).
        not_comparable_count=resolution.analyzed_candidate_count - included_count,
        not_yet_analyzed_count=(
            resolution.candidate_count - resolution.analyzed_candidate_count
        ),
        required_candidate_count=required_candidate_count(cfg),
        max_companies=max(1, int(cfg.llm_field_review_council_max_companies)),
        candidates=rows,
    )


async def _build_pack(
    db: AsyncSession, run: DiscoveryRun, resolution: CandidateResolution
) -> tuple[FieldReviewPack, list[FieldReviewCompanySummary]]:
    """Build the bounded comparative pack from already-persisted data only."""
    summaries: list[FieldReviewCompanySummary] = []
    for candidate, report, citation_ref in resolution.included:
        documents = await get_report_primary_documents(
            db,
            report_company_id=report.company_id,
            report_agent_run_id=report.created_by_agent_run_id,
            report_id=report.id,
        )
        summaries.append(
            build_company_summary(
                citation_ref=citation_ref,
                candidate=candidate,
                report=report,
                document_summary=documents.summary,
            )
        )
    pack = build_field_review_pack(
        run=run,
        companies=summaries,
        missing=resolution.missing,
        analyzed_candidate_count=resolution.analyzed_candidate_count,
    )
    return pack, summaries


# ---------------------------------------------------------------------------
# Persistence helpers
# ---------------------------------------------------------------------------


async def get_latest_field_review(
    db: AsyncSession, run_id: uuid.UUID
) -> FieldReviewRun | None:
    """The most recent field-review row for a discovery run, or None."""
    result = await db.execute(
        select(FieldReviewRun)
        .where(FieldReviewRun.discovery_run_id == run_id)
        .order_by(FieldReviewRun.created_at.desc(), FieldReviewRun.id.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def get_candidate_summaries(
    db: AsyncSession, field_review_run_id: uuid.UUID
) -> list[FieldReviewCandidateSummary]:
    """Every candidate row (included AND excluded) for one field review."""
    result = await db.execute(
        select(FieldReviewCandidateSummary)
        .where(
            FieldReviewCandidateSummary.field_review_run_id == field_review_run_id
        )
        .order_by(FieldReviewCandidateSummary.created_at.asc())
    )
    return list(result.scalars().all())


def _classify_status(result: FieldReviewResult, stored: dict[str, Any]) -> str:
    """Map a completed council result to a terminal status."""
    if (result.agents_completed or 0) <= 0:
        return "failed"
    if (result.agents_failed or 0) > 0 or not stored.get("safety_valid", True):
        return "completed_with_warnings"
    return "completed"


async def _persist_candidate_rows(
    db: AsyncSession,
    review_row: FieldReviewRun,
    summaries: list[FieldReviewCompanySummary],
    missing: list[dict[str, Any]],
    tier_by_ref: dict[str, str],
) -> None:
    """Write one row per candidate — included AND excluded. Never lossy."""
    for summary in summaries:
        db.add(
            FieldReviewCandidateSummary(
                id=uuid.uuid4(),
                field_review_run_id=review_row.id,
                discovery_candidate_id=(
                    uuid.UUID(summary.discovery_candidate_id)
                    if summary.discovery_candidate_id
                    else None
                ),
                report_id=(
                    uuid.UUID(summary.report_id) if summary.report_id else None
                ),
                citation_ref=summary.id,
                ticker=summary.ticker,
                exchange=summary.exchange,
                included=True,
                exclusion_reason=None,
                data_provenance=summary.data_provenance,
                priority_tier=tier_by_ref.get(summary.id),
                summary_json=summary.model_dump(mode="json"),
            )
        )
    for index, entry in enumerate(missing):
        db.add(
            FieldReviewCandidateSummary(
                id=uuid.uuid4(),
                field_review_run_id=review_row.id,
                discovery_candidate_id=(
                    uuid.UUID(entry["discovery_candidate_id"])
                    if entry.get("discovery_candidate_id")
                    else None
                ),
                report_id=(
                    uuid.UUID(entry["report_id"]) if entry.get("report_id") else None
                ),
                # Excluded candidates get an X-prefixed ref so they never collide
                # with a real cited company id (F#).
                citation_ref=f"X{index + 1}",
                ticker=entry.get("ticker"),
                exchange=entry.get("exchange"),
                included=False,
                exclusion_reason=entry.get("exclusion_reason"),
                data_provenance=None,
                priority_tier=None,
                summary_json=None,
            )
        )


# ---------------------------------------------------------------------------
# Async job lifecycle
# ---------------------------------------------------------------------------


async def start_field_review(
    db: AsyncSession,
    run: DiscoveryRun,
    *,
    force: bool = False,
    cfg: Any | None = None,
) -> tuple[FieldReviewRun, bool]:
    """Start (or return the current state of) an async field-review job.

    Returns ``(row, scheduled)`` where ``scheduled`` tells the API whether a
    background task must be launched. Never runs the council itself — that
    happens in ``process_field_review_by_id``.

    Job-lifecycle rules (no duplicate jobs):
      * A queued/running job → returns it, ``scheduled=False``.
      * A completed review and not ``force`` → returns it, ``scheduled=False``.
      * Otherwise a fresh ``pending`` row is written and ``scheduled=True``.

    Raises ``FieldReviewDisabledError`` (→ 409) when a (re)start is requested
    while the review is disabled and no completed review exists, and
    ``InsufficientAnalyzedCandidatesError`` (→ 422) when fewer than
    ``field_review_min_candidates`` candidates are comparable.
    """
    cfg = cfg or settings
    existing = await get_latest_field_review(db, run.id)
    status = existing.status if existing is not None else None

    if status in _IN_FLIGHT and existing is not None:
        log_event(
            logger,
            "field_review_job_duplicate",
            discovery_run_id=run.id,
            field_review_run_id=existing.id,
            status=status,
        )
        return existing, False

    have_completed = status in _HAS_REVIEW
    if have_completed and not force and existing is not None:
        return existing, False

    # From here we intend to (re)start the job — the review must be enabled.
    if not field_review_enabled(cfg):
        if have_completed and existing is not None:
            # Cannot re-run while disabled, but the prior review is still valid.
            return existing, False
        log_event(
            logger,
            "field_review_disabled",
            discovery_run_id=run.id,
            reason="flags_off",
        )
        raise FieldReviewDisabledError("Deep Field Review is disabled.")

    # Resolve the field BEFORE queuing, so an impossible comparison is a clean
    # 422 rather than a background job that fails a minute later.
    resolution = await resolve_field_candidates(db, run, cfg=cfg)
    required = required_candidate_count(cfg)
    if len(resolution.included) < required:
        log_event(
            logger,
            "field_review_insufficient_candidates",
            discovery_run_id=run.id,
            included_candidate_count=len(resolution.included),
            required=required,
            missing_candidate_count=len(resolution.missing),
        )
        raise InsufficientAnalyzedCandidatesError(
            "Insufficient analyzed candidates for a Deep Field Review "
            f"(need >={required}, found {len(resolution.included)}). A field "
            "review compares candidates that ALREADY have a completed full "
            "analysis.",
            included=len(resolution.included),
            required=required,
            missing=resolution.missing,
        )

    now = datetime.now(timezone.utc)
    row = FieldReviewRun(
        id=uuid.uuid4(),
        discovery_run_id=run.id,
        status="pending",
        included_candidate_count=len(resolution.included),
        missing_candidate_count=len(resolution.missing),
        llm_used=False,
        council_version=cfg.llm_field_review_council_version,
        human_review_required=True,
        started_at=now,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    log_event(
        logger,
        "field_review_job_queued",
        discovery_run_id=run.id,
        field_review_run_id=row.id,
        status="pending",
        included_candidate_count=row.included_candidate_count,
        missing_candidate_count=row.missing_candidate_count,
    )
    return row, True


async def _mark_failed(
    session: AsyncSession, row: FieldReviewRun, *, reason: str, status: str = "failed"
) -> None:
    """Persist a terminal failure state for a job, never clobbering a good review."""
    if row.status in _HAS_REVIEW:
        return
    row.status = status
    row.error = reason
    row.completed_at = datetime.now(timezone.utc)
    row.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def _mark_failed_fresh(
    factory: async_sessionmaker[AsyncSession],
    field_review_run_id: uuid.UUID,
    *,
    reason: str,
) -> None:
    """Best-effort: mark a job failed in a fresh session."""
    try:
        async with factory() as session:
            result = await session.execute(
                select(FieldReviewRun).where(FieldReviewRun.id == field_review_run_id)
            )
            row = result.scalar_one_or_none()
            if row is not None:
                await _mark_failed(session, row, reason=reason)
    except Exception:  # noqa: BLE001 — must not crash the worker
        logger.exception(
            "Failed to mark field review job %s as failed.", field_review_run_id
        )


async def process_field_review_by_id(
    field_review_run_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    cfg: Any | None = None,
    client: Any | None = None,
) -> None:
    """Background worker: load the job in a FRESH session and run the council.

    Must NOT reuse the request-scoped session (the response has already been
    returned). Every failure path persists a terminal row, so a job can never
    stick in ``running``. Only ids/statuses/counts/durations are logged.
    """
    factory = session_factory or async_session_factory
    cfg = cfg or settings
    start = time.perf_counter()
    try:
        async with factory() as session:
            result = await session.execute(
                select(FieldReviewRun).where(FieldReviewRun.id == field_review_run_id)
            )
            row = result.scalar_one_or_none()
            if row is None:
                logger.warning(
                    "Deep Field Review: job %s not found for background run.",
                    field_review_run_id,
                )
                return

            run_result = await session.execute(
                select(DiscoveryRun).where(DiscoveryRun.id == row.discovery_run_id)
            )
            run = run_result.scalar_one_or_none()
            if run is None:
                await _mark_failed(session, row, reason="discovery_run_missing")
                return

            row.status = "running"
            row.updated_at = datetime.now(timezone.utc)
            await session.commit()
            log_event(
                logger,
                "field_review_job_started",
                discovery_run_id=run.id,
                field_review_run_id=row.id,
                status="running",
            )

            resolution = await resolve_field_candidates(session, run, cfg=cfg)
            required = max(2, int(cfg.field_review_min_candidates))
            if len(resolution.included) < required:
                row.included_candidate_count = len(resolution.included)
                row.missing_candidate_count = len(resolution.missing)
                await _persist_candidate_rows(
                    session, row, [], resolution.missing, {}
                )
                await _mark_failed(
                    session,
                    row,
                    reason="insufficient_analyzed_candidates",
                    status="insufficient_candidates",
                )
                log_event(
                    logger,
                    "field_review_job_failed",
                    level=logging.WARNING,
                    discovery_run_id=run.id,
                    field_review_run_id=row.id,
                    status="insufficient_candidates",
                    reason="insufficient_analyzed_candidates",
                    duration_ms=int((time.perf_counter() - start) * 1000),
                )
                return

            pack, summaries = await _build_pack(session, run, resolution)
            log_event(
                logger,
                "field_review_pack_built",
                discovery_run_id=run.id,
                field_review_run_id=row.id,
                pack_version=pack.pack_version,
                item_count=pack.item_count,
                company_count=pack.company_count,
                known_gap_count=len(pack.known_gaps),
            )

            council = await maybe_run_field_review_council(
                pack=pack,
                field_review_run_id=str(row.id),
                cfg=cfg,
                client=client,
                logger=logger,
            )
            if not council.llm_used:
                # Flags were on but no provider resolved (e.g. no credentials).
                row.included_candidate_count = len(summaries)
                row.missing_candidate_count = len(resolution.missing)
                await _persist_candidate_rows(
                    session, row, summaries, resolution.missing, {}
                )
                await _mark_failed(session, row, reason="provider_unavailable")
                log_event(
                    logger,
                    "field_review_job_failed",
                    level=logging.WARNING,
                    discovery_run_id=run.id,
                    field_review_run_id=row.id,
                    status="failed",
                    reason="provider_unavailable",
                    duration_ms=int((time.perf_counter() - start) * 1000),
                )
                return

            _finalize(row, council)
            await _persist_candidate_rows(
                session,
                row,
                summaries,
                resolution.missing,
                council.tier_by_company_ref(),
            )
            row.included_candidate_count = len(summaries)
            row.missing_candidate_count = len(resolution.missing)
            await session.commit()
            log_event(
                logger,
                "field_review_job_completed",
                discovery_run_id=run.id,
                field_review_run_id=row.id,
                status=row.status,
                agents_completed=row.agents_completed,
                agents_failed=row.agents_failed,
                field_quality=row.field_quality,
                safety_valid=row.safety_valid,
                included_candidate_count=row.included_candidate_count,
                missing_candidate_count=row.missing_candidate_count,
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
    except Exception as exc:  # noqa: BLE001 — must not crash the worker
        # Structured, secret-free failure event — never the raw exception string.
        log_event(
            logger,
            "field_review_job_failed",
            level=logging.ERROR,
            field_review_run_id=field_review_run_id,
            status="failed",
            reason="internal_error",
            exception_type=type(exc).__name__,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        logger.exception(
            "Deep Field Review job crashed for %s: %s", field_review_run_id, exc
        )
        await _mark_failed_fresh(
            factory, field_review_run_id, reason="internal_error"
        )


def _finalize(row: FieldReviewRun, council: FieldReviewResult) -> None:
    """Fold a completed council result onto the job row (caller commits).

    Runs the DEFENSIVE safety re-scan before persisting: the council already
    quarantines unsafe agent output per-agent, and this is the backstop. A hit
    forces ``safety_valid=False`` — the payload is flagged, never silently
    stripped.
    """
    created_at = datetime.now(timezone.utc)
    stored = council.to_storage_dict(created_at=created_at.isoformat())

    hits = safety_terms.scan_value(stored, exempt_keys=_SAFETY_EXEMPT_KEYS)
    if hits:
        stored["safety_valid"] = False

    row.status = _classify_status(council, stored)
    row.llm_used = council.llm_used
    row.council_version = council.council_version
    row.provider = council.provider
    row.model = council.model
    row.agents_completed = council.agents_completed
    row.agents_failed = council.agents_failed
    row.field_quality = council.field_quality
    row.safety_valid = bool(stored.get("safety_valid", True))
    row.review_json = stored
    row.warnings_json = list(council.warnings)
    row.error = "no_agents_completed" if row.status == "failed" else None
    row.human_review_required = True
    row.completed_at = created_at
    row.updated_at = created_at


async def process_field_review_task(field_review_run_id: str) -> None:
    """FastAPI ``BackgroundTasks`` entry point for an async field-review job.

    Takes only a primitive id (never an ORM object or the request session) and
    drives the fresh-session worker. Swallows exceptions so a background failure
    can never surface to (or crash) the request handler.
    """
    try:
        await process_field_review_by_id(uuid.UUID(field_review_run_id))
    except Exception:  # noqa: BLE001
        logger.exception(
            "Background Deep Field Review task crashed for %s", field_review_run_id
        )
