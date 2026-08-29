"""Exact lineage resolution for final-report REGENERATION.

WHY THIS MODULE EXISTS
======================
Generating a final report from an existing report/run is a *regeneration*, not
a fresh discovery. The lineage that produced the original report — which
company it is about, which agent run analysed it, which discovery run surfaced
it and which candidate row it was — is already known, exactly, by foreign key.
Regeneration must carry that lineage forward rather than re-derive it.

It did not. ``generate_from_report`` passed ``candidate=None`` and no
``discovery_lineage``, and it only resolved a ``company_record`` when the
re-parsed workflow state carried no company snapshot. Regenerating the accepted
Pandora report therefore produced a report whose body said

    legal_name: "PNDORA"                     (the TICKER, from the snapshot stub)
    "No screening candidate is linked"
    Discovery Rationale: not available
    Why It Surfaced: not available

beside a ``source_summary_json`` that still carried the right ``company_id``
and ``created_by_agent_run_id``. The exact linkage existed the whole time; the
regeneration path simply never asked for it.

WHAT THIS MODULE GUARANTEES
===========================
* Lineage is read from EXPLICIT signals only — persisted lineage on the source
  report, and foreign keys (``Report.company_id``,
  ``Report.created_by_agent_run_id``, ``DiscoveryCandidate.analysis_report_id``,
  ``DiscoveryCandidate.agent_run_id``, ``Scorecard.company_id`` /
  ``Scorecard.screening_candidate_id``). NEVER from a ticker/name match, never
  "the latest candidate for this company".
* Conflicts FAIL CLOSED. Two different candidates, or two different discovery
  runs, reachable by equally-exact signals is not something to pick a winner
  from — it is a linkage the code cannot resolve, and it raises
  ``AmbiguousReportLineageError`` instead of silently choosing.
* Absence is preserved HONESTLY. A run that genuinely had no discovery
  candidate resolves to ``None``, and the report says so. Nothing is
  fabricated and nothing is inferred to fill the hole.
* No issuer-specific behaviour. Nothing here keys off a ticker, an exchange or
  a company name.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.report import Report
from app.models.scorecard import Scorecard
from app.models.screening import ScreeningCandidate
from app.services.discovery_signal_extractor import is_placeholder_company_name

logger = logging.getLogger(__name__)


class AmbiguousReportLineageError(Exception):
    """Two equally-exact linkages disagree — resolve by hand, never by guess.

    Deliberately NOT a ``ValueError``: the final-report routes map ``ValueError``
    to 404 ("not found"), and an ambiguous lineage is the opposite problem —
    too much linkage, not too little. It maps to 409 Conflict.
    """

    def __init__(self, subject: str, candidates: list[str]) -> None:
        self.subject = subject
        self.candidates = sorted(candidates)
        super().__init__(
            f"Ambiguous {subject} lineage for this report: "
            f"{', '.join(self.candidates)}. Regeneration fails closed rather "
            "than choosing one — link the report to a single "
            f"{subject} and retry."
        )


@dataclass(frozen=True)
class ResolvedReportLineage:
    """Everything a regeneration must carry forward from its source."""

    company_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    company_record: dict[str, Any] | None = None
    discovery_lineage: dict[str, Any] | None = None
    screening_candidate: ScreeningCandidate | None = None
    parent_report_id: uuid.UUID | None = None
    #: Ordered, auditable record of WHICH explicit signal supplied what.
    resolved_from: list[str] = field(default_factory=list)

    def as_provenance(self) -> dict[str, Any]:
        """Bounded, secret-free lineage metadata for ``source_summary_json``."""
        return {
            "parent_report_id": (
                str(self.parent_report_id) if self.parent_report_id else None
            ),
            "company_id": str(self.company_id) if self.company_id else None,
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "discovery_run_id": (
                (self.discovery_lineage or {}).get("discovery_run_id")
            ),
            "discovery_candidate_id": (
                (self.discovery_lineage or {}).get("discovery_candidate_id")
            ),
            "screening_candidate_id": (
                str(self.screening_candidate.id) if self.screening_candidate else None
            ),
            "resolved_from": list(self.resolved_from),
        }


# ---------------------------------------------------------------------------
# Company identity
# ---------------------------------------------------------------------------


def _company_record(company: Company) -> dict[str, Any]:
    return {
        "id": str(company.id),
        "name": company.name,
        "ticker": company.ticker,
        "exchange": company.exchange,
        "country": company.country,
        "sector": company.sector,
        "industry": company.industry,
    }


def _candidate_record(cand: DiscoveryCandidate) -> dict[str, Any] | None:
    name = cand.legal_name or cand.company_name
    if not name and not cand.ticker:
        return None
    return {
        "name": name,
        "ticker": cand.ticker,
        "exchange": cand.exchange,
        "country": cand.country,
        "sector": cand.sector,
    }


def resolve_display_company_name(
    snapshot_legal_name: str | None,
    ticker: str | None,
    company_record: dict[str, Any] | None,
) -> str | None:
    """The best KNOWN name for this issuer, preferring the snapshot.

    ``free_real_provider._not_sourced_profile()`` deliberately sets
    ``legal_name = ticker`` as a safety stub for venues SEC EDGAR does not
    cover (never guess a wrong company from an unrelated SEC index entry). That
    stub is truthy, so every caller that tested truthiness kept the ticker and
    the report announced "PNDORA" as a legal name while the company row said
    "Pandora A/S". ``is_placeholder_company_name`` is the right test; this is
    the one place that applies it, so identity, the report title and the
    council's issuer context can never disagree about it.

    Generic: a genuine snapshot-resolved name always wins, and a company record
    that is ITSELF a bare ticker never displaces anything.
    """
    if not ticker:
        return snapshot_legal_name or (company_record or {}).get("name")
    if not is_placeholder_company_name(snapshot_legal_name, ticker):
        return snapshot_legal_name
    record_name = (company_record or {}).get("name")
    if record_name and not is_placeholder_company_name(record_name, ticker):
        return record_name
    return snapshot_legal_name


async def resolve_company_record(
    db: AsyncSession,
    source_report: Report | None,
    scorecard: Scorecard | None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Recover the company record from EXPLICIT lineage, most exact first.

      1. ``Report.company_id``            — the report's own first-class FK
      2. ``Scorecard.company_id``         — the scorecard's FK
      3. ``DiscoveryCandidate.agent_run_id == report.created_by_agent_run_id``
      4. ``DiscoveryCandidate.analysis_report_id == report.id``

    Steps 3–4 recover identity only (name/ticker/exchange/country/sector) — the
    candidate's ``snapshot_json`` is deliberately never promoted, so stale
    candidate numbers can not be presented as sourced.

    Returns ``(record, signal)``; ``(None, None)`` when identity is genuinely
    unresolvable. Never fabricated, never matched on ticker or name.
    """
    if source_report is not None and source_report.company_id:
        result = await db.execute(
            select(Company).where(Company.id == source_report.company_id)
        )
        company = result.scalar_one_or_none()
        if company is not None:
            return _company_record(company), "report.company_id"

    if scorecard is not None and scorecard.company_id:
        result = await db.execute(
            select(Company).where(Company.id == scorecard.company_id)
        )
        company = result.scalar_one_or_none()
        if company is not None:
            return _company_record(company), "scorecard.company_id"

    if source_report is not None and source_report.created_by_agent_run_id:
        by_run = await db.execute(
            select(DiscoveryCandidate).where(
                DiscoveryCandidate.agent_run_id
                == source_report.created_by_agent_run_id
            )
        )
        for cand in by_run.scalars().all():
            rec = _candidate_record(cand)
            if rec is not None:
                return rec, "discovery_candidate.agent_run_id"

    if source_report is not None:
        by_report = await db.execute(
            select(DiscoveryCandidate).where(
                DiscoveryCandidate.analysis_report_id == source_report.id
            )
        )
        for cand in by_report.scalars().all():
            rec = _candidate_record(cand)
            if rec is not None:
                return rec, "discovery_candidate.analysis_report_id"

    return None, None


# ---------------------------------------------------------------------------
# Discovery lineage
# ---------------------------------------------------------------------------


def _lineage_from_candidate(
    cand: DiscoveryCandidate, run: DiscoveryRun | None
) -> dict[str, Any]:
    """The same plain-dict shape ``run_candidate_analysis`` threads at run time.

    Only fields that already exist on the candidate / its run. Never inferred,
    never fabricated.
    """
    return {
        "discovery_run_id": str(cand.discovery_run_id),
        "discovery_candidate_id": str(cand.id),
        "ticker": cand.ticker,
        "exchange": cand.exchange,
        "rank": cand.rank,
        "candidate_score": cand.candidate_score,
        "candidate_score_grade": cand.candidate_score_grade,
        "score_explanation": cand.score_explanation,
        "thesis_relevance_score": cand.thesis_relevance_score,
        "thesis_match_json": cand.thesis_match_json,
        "thesis_text": run.thesis_text if run is not None else None,
    }


def persisted_discovery_lineage(report: Report | None) -> dict[str, Any] | None:
    """The discovery lineage the SOURCE report was itself generated with.

    ``_generate_and_save`` stores it under ``source_summary_json`` on every
    final report, so a report generated from a discovery candidate carries its
    own exact lineage forever — including after the candidate row is edited or
    its ``analysis_report_id`` is re-pointed at a newer report.
    """
    if report is None:
        return None
    summary = report.source_summary_json or {}
    lineage = summary.get("discovery_lineage")
    if isinstance(lineage, dict) and lineage.get("discovery_candidate_id"):
        return dict(lineage)
    return None


async def _candidates_by_exact_link(
    db: AsyncSession, source_report: Report, extra_report_ids: list[uuid.UUID]
) -> list[DiscoveryCandidate]:
    """Every DiscoveryCandidate reachable from this report by an exact FK.

    ``analysis_report_id`` covers the report itself and any report the
    regeneration recovered its workflow state from (the originating
    analysis-council draft of the same run). ``agent_run_id`` covers the run.
    De-duplicated by candidate id.
    """
    report_ids = [source_report.id, *extra_report_ids]
    clauses: list[Any] = [DiscoveryCandidate.analysis_report_id.in_(report_ids)]
    if source_report.created_by_agent_run_id is not None:
        clauses.append(
            DiscoveryCandidate.agent_run_id == source_report.created_by_agent_run_id
        )
    result = await db.execute(select(DiscoveryCandidate).where(or_(*clauses)))
    found: dict[uuid.UUID, DiscoveryCandidate] = {}
    for cand in result.scalars().all():
        found.setdefault(cand.id, cand)
    return list(found.values())


async def resolve_discovery_lineage(
    db: AsyncSession,
    source_report: Report | None,
    *,
    state_recovered_from: uuid.UUID | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    """Recover the discovery-run lineage for a regeneration. Fails closed.

    Signals, all exact:

      1. ``DiscoveryCandidate`` rows linked to this report / its recovered
         workflow draft / its agent run. More than one DISTINCT candidate (or
         one candidate per discovery run) raises
         ``AmbiguousReportLineageError`` — regeneration never picks a winner.
      2. The lineage PERSISTED on the source report when no candidate row is
         reachable any more (the row was deleted, or its
         ``analysis_report_id`` was re-pointed at a sibling report).

    When both are present they must AGREE on the candidate id; disagreement is
    a conflict and fails closed.

    Returns ``(lineage, signal)``, or ``(None, None)`` when this report
    genuinely has no discovery-run origin — the honest state for a report
    launched directly from a company.
    """
    if source_report is None:
        return None, None

    persisted = persisted_discovery_lineage(source_report)
    extra_ids = [state_recovered_from] if state_recovered_from else []
    candidates = await _candidates_by_exact_link(db, source_report, extra_ids)

    if len(candidates) > 1:
        raise AmbiguousReportLineageError(
            "discovery candidate", [str(c.id) for c in candidates]
        )

    if candidates:
        cand = candidates[0]
        if persisted and persisted.get("discovery_candidate_id") != str(cand.id):
            raise AmbiguousReportLineageError(
                "discovery candidate",
                [str(cand.id), str(persisted.get("discovery_candidate_id"))],
            )
        run = None
        if cand.discovery_run_id is not None:
            run_result = await db.execute(
                select(DiscoveryRun).where(DiscoveryRun.id == cand.discovery_run_id)
            )
            run = run_result.scalar_one_or_none()
        return _lineage_from_candidate(cand, run), "discovery_candidate.exact_fk"

    if persisted:
        return persisted, "source_report.source_summary_json"

    return None, None


# ---------------------------------------------------------------------------
# Legacy ScreeningCandidate
# ---------------------------------------------------------------------------


async def resolve_screening_candidate(
    db: AsyncSession, scorecard: Scorecard | None
) -> ScreeningCandidate | None:
    """The legacy ``ScreeningCandidate`` this report's scorecard points at.

    Exact FK only (``Scorecard.screening_candidate_id``). There is deliberately
    NO "latest screening candidate for this company" fallback here — that is a
    global-newest lookup, and it is what makes a regeneration attach some other
    run's rationale to this report.
    """
    if scorecard is None or not scorecard.screening_candidate_id:
        return None
    result = await db.execute(
        select(ScreeningCandidate).where(
            ScreeningCandidate.id == scorecard.screening_candidate_id
        )
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def resolve_report_lineage(
    db: AsyncSession,
    source_report: Report | None,
    scorecard: Scorecard | None,
    *,
    state_recovered_from: uuid.UUID | None = None,
) -> ResolvedReportLineage:
    """Resolve the FULL lineage a regeneration must preserve.

    Raises ``AmbiguousReportLineageError`` when two exact linkages conflict.
    """
    company_record, company_signal = await resolve_company_record(
        db, source_report, scorecard
    )
    discovery_lineage, discovery_signal = await resolve_discovery_lineage(
        db, source_report, state_recovered_from=state_recovered_from
    )
    screening_candidate = await resolve_screening_candidate(db, scorecard)

    resolved_from: list[str] = []
    if company_signal:
        resolved_from.append(f"company:{company_signal}")
    if discovery_signal:
        resolved_from.append(f"discovery:{discovery_signal}")
    if screening_candidate is not None:
        resolved_from.append("screening_candidate:scorecard.screening_candidate_id")

    lineage = ResolvedReportLineage(
        company_id=source_report.company_id if source_report else None,
        agent_run_id=(
            source_report.created_by_agent_run_id if source_report else None
        ),
        company_record=company_record,
        discovery_lineage=discovery_lineage,
        screening_candidate=screening_candidate,
        parent_report_id=source_report.id if source_report else None,
        resolved_from=resolved_from,
    )
    logger.info(
        "report_lineage_resolved parent_report=%s company=%s agent_run=%s "
        "discovery_run=%s candidate=%s signals=%s",
        lineage.parent_report_id,
        lineage.company_id,
        lineage.agent_run_id,
        (discovery_lineage or {}).get("discovery_run_id"),
        (discovery_lineage or {}).get("discovery_candidate_id"),
        ",".join(resolved_from) or "none",
    )
    return lineage
