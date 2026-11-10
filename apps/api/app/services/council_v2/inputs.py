"""Council V2 inputs — V3.7 Slice 7.1.

WHAT CHANGES, AND WHAT DELIBERATELY DOES NOT
============================================
The Council runs **after** investigation and verification, over the **ledger** — not over
a raw evidence dump, and not to rediscover facts the pipeline already owns.

What does **not** change: the five allowed chair labels
(``ALLOWED_COMMITTEE_LABELS``), the implication/synthesis contract ADR-041 established,
and the rule that ``BUY``/``SELL``/``HOLD``/``WATCH`` are absent from the type rather than
filtered from the text. V3.7 is not an excuse to reopen a safety vocabulary that took
several live corrections to get right.

THE ONE STRUCTURAL CHANGE
=========================
> A ``key_point`` now references a **``finding_id``** that carries its own evidence,
> instead of a positional run-local ``E1``/``E2`` handle.

``E1`` means "the first item in the pack this run happened to build". It cannot be
resolved next quarter, cannot be compared across runs, and is the reason research memory
had nothing stable to point at. A ``finding_id`` is a row, and the finding it names is
already refused if it has no evidence behind it (5.1).

THE COUNCIL CAN BE REFUSED
==========================
``assemble`` returns a **refusal** when the ledger says the Council may not convene — a
blocking question open, or a gap that blocks it. The run then reports insufficient
evidence rather than analysing around the hole. That is a *product* decision that V2 could
not express: its council always ran, because there was nothing that could say no.

CONFLICT IS AN INPUT
====================
Unresolved disagreements are assembled and handed to the Chair, whose job is to **surface**
them and never to silently pick a number. A Chair that received a tidy pack could not do
that, because the conflict would already have been resolved by whoever built the pack.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import or_, select

from app.models.ledger import (
    ResearchDisagreement,
    ResearchFinding,
    ResearchGap,
    ResearchQuestion,
)
from app.services.ledger import store as ledger

#: Source kinds that are the issuer's OWN documents (``director.contracts``).
ISSUER_SOURCE_KINDS: frozenset[str] = frozenset({"issuer_filing", "issuer_ir"})

#: Why the Council was not convened. Closed: "we did not run the council" aggregated by
#: reason is a coverage finding, and aggregated by free text it is a list of sentences.
REFUSED_BLOCKING_QUESTION = "blocking_question_open"
REFUSED_BLOCKING_GAP = "gap_blocks_council"
REFUSED_NO_FINDINGS = "no_findings"

REFUSAL_REASONS: frozenset[str] = frozenset(
    {REFUSED_BLOCKING_QUESTION, REFUSED_BLOCKING_GAP, REFUSED_NO_FINDINGS}
)

#: A bound on what one Council sees. Not a quality knob: a Chair handed 900 findings is a
#: Chair handed a corpus, and the run's own budget already paid for the investigation.
MAX_FINDINGS = 120
MAX_GAPS = 60
MAX_DISAGREEMENTS = 40


#: ``period_type`` values that say how a finding's period was set (ADR-086 / ADR-093).
_PERIOD_BASES: frozenset[str] = frozenset(
    {"text_stated", "statement_duration", "source_duration"})
#: ``scope_type`` values the segment hierarchy writes (ADR-093).
_SCOPE_TYPES: frozenset[str] = frozenset({"segment", "business_line"})


@dataclass(frozen=True)
class FindingRef:
    """One finding, as the Council sees it. **Its id is the citable handle.**"""

    finding_id: uuid.UUID
    statement: str
    mechanism: str | None
    direction: str | None
    confidence: float | None
    evidence_ids: tuple[str, ...]
    calculation_ids: tuple[str, ...]
    period_key: str | None
    scope_key: str | None
    originating_role: str | None
    verification_status: str
    #: V3.18.2 — which domain and topic the finding belongs to, and the kinds of source
    #: behind it. Defaults keep every existing constructor valid.
    domain: str | None = None
    topic_key: str | None = None
    question_key: str | None = None
    source_kinds: tuple[str, ...] = ()
    references_finding_ids: tuple[str, ...] = ()
    #: Migration 043 — when its source published it, and the newer finding that
    #: replaced it as current guidance (``None`` = current).
    source_published_at: str | None = None
    superseded_by_finding_id: str | None = None
    #: ADR-088 — ``actual | guidance | forecast | target | estimate | unknown``; ``None``
    #: for a finding written before the kind was declared.
    statement_kind: str | None = None

    #: ADR-086 — ``"text_stated"`` when ``period_key`` was read from the statement's
    #: own words (a web finding) rather than inherited from its evidence.
    period_basis: str | None = None
    #: ADR-093 — ``segment`` / ``business_line`` when the issuer's segment hierarchy
    #: scoped the finding (the ``scope_type`` column).
    scope_type: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "FindingRef":
        """ONE mapping from a ledger row, used by every reader of findings."""
        return cls(
            finding_id=row.id,
            statement=row.statement,
            mechanism=row.mechanism,
            direction=row.direction,
            confidence=row.confidence,
            evidence_ids=tuple(row.evidence_ids_json or ()),
            calculation_ids=tuple(row.calculation_ids_json or ()),
            period_key=row.period_key,
            scope_key=row.scope_key,
            originating_role=row.originating_role,
            verification_status=row.verification_status,
            domain=getattr(row, "domain", None),
            topic_key=getattr(row, "topic_key", None),
            question_key=getattr(row, "question_key", None),
            source_kinds=tuple(getattr(row, "source_kinds_json", None) or ()),
            references_finding_ids=tuple(
                getattr(row, "references_finding_ids_json", None) or ()
            ),
            source_published_at=_iso(getattr(row, "source_published_at", None)),
            superseded_by_finding_id=_str_or_none(
                getattr(row, "superseded_by_finding_id", None)
            ),
            statement_kind=getattr(row, "statement_kind", None),

            period_basis=(
                getattr(row, "period_type", None)
                if getattr(row, "period_type", None) in _PERIOD_BASES
                else None
            ),
            scope_type=(
                getattr(row, "scope_type", None)
                if getattr(row, "scope_type", None) in _SCOPE_TYPES
                else None
            ),
        )

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "verified"

    @property
    def is_from_issuer_documents(self) -> bool:
        """Cites the issuer's own filings or investor-relations documents."""
        return bool(set(self.source_kinds) & ISSUER_SOURCE_KINDS)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": str(self.finding_id),
            "statement": self.statement,
            "mechanism": self.mechanism,
            "direction": self.direction,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "calculation_ids": list(self.calculation_ids),
            "period_key": self.period_key,
            "scope_key": self.scope_key,
            "originating_role": self.originating_role,
            "verification_status": self.verification_status,
            "domain": self.domain,
            "topic_key": self.topic_key,
            "question_key": self.question_key,
            "source_kinds": list(self.source_kinds),
            "references_finding_ids": list(self.references_finding_ids),
            "source_published_at": self.source_published_at,
            "superseded_by_finding_id": self.superseded_by_finding_id,
            # Emitted only when declared, so a finding written before ADR-088 serialises
            # exactly as it did.
            **({"statement_kind": self.statement_kind} if self.statement_kind else {}),

            # Only when set, so the pinned key set of an evidence-dated finding holds.
            **({"period_basis": self.period_basis} if self.period_basis else {}),
            # ADR-093 — only when the segment hierarchy scoped it.
            **({"scope_type": self.scope_type} if self.scope_type else {}),
        }


@dataclass(frozen=True)
class GapRef:
    """One gap the Council must be explicit about."""

    gap_id: uuid.UUID
    gap_type: str
    description: str
    why_it_matters: str | None
    status: str
    closable: bool
    #: Migration 043 — the final reconciliation's verdict (``partially_closed`` or
    #: ``still_open`` here: closed and superseded gaps never reach the Council), and the
    #: findings that partly address it.
    reconciliation_status: str | None = None
    addressed_by_finding_ids: tuple[str, ...] = ()
    reconciliation_reasons: tuple[str, ...] = ()
    #: ADR-086 — the report's gap status (``gap_reconciliation.GAP_STATUSES``).
    gap_status: str | None = None
    #: ADR-094 — the gap queue's result (``gap_resolution.RESULTS``) and its wording.
    resolution_result: str | None = None
    resolution_words: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": str(self.gap_id),
            "gap_type": self.gap_type,
            "description": self.description,
            "why_it_matters": self.why_it_matters,
            "status": self.status,
            "closable": self.closable,
            "reconciliation_status": self.reconciliation_status,
            "addressed_by_finding_ids": list(self.addressed_by_finding_ids),
            "reconciliation_reasons": list(self.reconciliation_reasons),
            # Only when reconciliation ran, so the pinned key set of an older gap holds.
            **({"gap_status": self.gap_status} if self.gap_status else {}),
            # ADR-094 — only when the gap queue ran.
            **({"resolution_result": self.resolution_result,
                "resolution_words": self.resolution_words}
               if self.resolution_result else {}),
        }


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None and hasattr(value, "isoformat") else None


def _str_or_none(value: Any) -> str | None:
    return str(value) if value else None


@dataclass(frozen=True)
class DisagreementRef:
    """Two findings that conflict, and whether anything resolved it."""

    disagreement_id: uuid.UUID
    finding_a_id: uuid.UUID
    finding_b_id: uuid.UUID
    nature: str
    description: str | None
    resolution: str
    resolution_note: str | None

    @property
    def is_unresolved(self) -> bool:
        return self.resolution == ledger.UNRESOLVED

    def to_dict(self) -> dict[str, Any]:
        return {
            "disagreement_id": str(self.disagreement_id),
            "finding_a_id": str(self.finding_a_id),
            "finding_b_id": str(self.finding_b_id),
            "nature": self.nature,
            "description": self.description,
            "resolution": self.resolution,
            "resolution_note": self.resolution_note,
        }


@dataclass(frozen=True)
class LedgerCounts:
    """What the LEDGER records for the run, whatever the Council was shown (ADR-082).

    A refused Council carries no findings (the refusal withholds the synthesis) and a
    convened one may be truncated. Neither is the ledger's count, and every reader that
    states "N findings" must state the ledger's: the Cellectis chair said "0 finding(s)"
    on four runs whose ledgers recorded 32-38 (QA 2026-11, CLLS2-3).
    """

    findings: int = 0
    verified: int = 0
    primary_source: int = 0
    gaps_open: int = 0
    unresolved_disagreements: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "findings": self.findings,
            "verified": self.verified,
            "primary_source": self.primary_source,
            "gaps_open": self.gaps_open,
            "unresolved_disagreements": self.unresolved_disagreements,
        }


@dataclass
class CouncilInput:
    """Everything the Council may reason over, and nothing else.

    Assembled from the ledger, so every statement in it already carries evidence. There is
    no "supporting context" field: a Council that could be handed unattributed prose would
    reintroduce exactly the failure the ledger removed.
    """

    research_run_id: uuid.UUID
    convened: bool
    refusal_reason: str | None = None
    refusal_detail: str | None = None
    findings: tuple[FindingRef, ...] = ()
    gaps: tuple[GapRef, ...] = ()
    disagreements: tuple[DisagreementRef, ...] = ()
    open_question_keys: tuple[str, ...] = ()
    playbook_versions: dict[str, int] = field(default_factory=dict)
    truncated: dict[str, int] = field(default_factory=dict)
    #: ADR-082 — the ledger's own counts, on EVERY path including a refusal. ``None``
    #: only for an input built by hand (tests, replays); readers then fall back to what
    #: the Council was shown.
    ledger_counts: LedgerCounts | None = None

    def __post_init__(self) -> None:
        if not self.convened and self.refusal_reason not in REFUSAL_REASONS:
            raise ValueError(
                f"{self.refusal_reason!r} is not a recognised refusal reason. "
                "'The council did not run' aggregated by reason is a coverage finding; "
                "aggregated by free text it is a list of sentences."
            )
        if self.convened and self.refusal_reason is not None:
            raise ValueError(
                "a refusal reason beside a convened council is exactly what a reader "
                "takes at face value."
            )

    @property
    def unresolved_disagreements(self) -> tuple[DisagreementRef, ...]:
        """What the Chair must surface rather than silently resolve."""
        return tuple(d for d in self.disagreements if d.is_unresolved)

    @property
    def verified_findings(self) -> tuple[FindingRef, ...]:
        return tuple(f for f in self.findings if f.is_verified)

    @property
    def primary_source_findings(self) -> tuple[FindingRef, ...]:
        """Findings citing the issuer's own documents. NOT "verified": nothing in the
        pipeline verifies a finding yet, and a count named so would say otherwise."""
        return tuple(f for f in self.findings if f.is_from_issuer_documents)

    @property
    def citable_finding_ids(self) -> frozenset[str]:
        """The **only** handles a council key_point may cite.

        A citation to anything outside this set is unresolvable, which is the property
        that replaces V2's positional ``E1``/``E2``.
        """
        return frozenset(str(f.finding_id) for f in self.findings)

    @property
    def finding_count(self) -> int:
        """The LEDGER's finding count (ADR-082), not the size of the Council's pack."""
        if self.ledger_counts is not None:
            return self.ledger_counts.findings
        return len(self.findings) + int(self.truncated.get("findings", 0))

    @property
    def verified_count(self) -> int:
        if self.ledger_counts is not None:
            return self.ledger_counts.verified
        return len(self.verified_findings)

    @property
    def primary_source_count(self) -> int:
        if self.ledger_counts is not None:
            return self.ledger_counts.primary_source
        return len(self.primary_source_findings)

    @property
    def open_gap_count(self) -> int:
        if self.ledger_counts is not None:
            return self.ledger_counts.gaps_open
        return len([g for g in self.gaps if g.status == "open"])

    @property
    def unresolved_count(self) -> int:
        if self.ledger_counts is not None:
            return self.ledger_counts.unresolved_disagreements
        return len(self.unresolved_disagreements)

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_run_id": str(self.research_run_id),
            "convened": self.convened,
            "refusal_reason": self.refusal_reason,
            "refusal_detail": self.refusal_detail,
            # ADR-082: the counts are the LEDGER's. What the Council itself was shown is
            # stated separately, so a refused Council no longer reads "0 findings".
            "finding_count": self.finding_count,
            "verified_finding_count": self.verified_count,
            "primary_source_finding_count": self.primary_source_count,
            "council_finding_count": len(self.findings),
            "gap_count": len(self.gaps),
            "disagreement_count": len(self.disagreements),
            "unresolved_disagreement_count": self.unresolved_count,
            "open_question_keys": list(self.open_question_keys),
            "playbook_versions": dict(self.playbook_versions),
            "truncated": dict(self.truncated),
        }


async def assemble(session: Any, run: Any) -> CouncilInput:
    """Build the Council's input from the ledger, or refuse with a reason."""
    summary = await ledger.summarise(session, run)

    findings = await _findings(session, run)
    gaps = await _gaps(session, run)
    disagreements = await _disagreements(session, run)
    open_keys = await _open_question_keys(session, run)
    # ADR-082 — on every path, refusals included: a refusal withholds the synthesis,
    # never the count of what the ledger records.
    counts = await ledger_counts(session, run)

    if summary.questions_blocking_open:
        return CouncilInput(
            research_run_id=run.id,
            convened=False,
            refusal_reason=REFUSED_BLOCKING_QUESTION,
            refusal_detail=(
                f"{summary.questions_blocking_open} blocking question(s) are still "
                "open. The run reports insufficient evidence rather than analysing "
                "around the hole."
            ),
            gaps=gaps,
            open_question_keys=open_keys,
            playbook_versions=dict(run.playbook_versions_json or {}),
            ledger_counts=counts,
        )
    if summary.gaps_blocking_council:
        return CouncilInput(
            research_run_id=run.id,
            convened=False,
            refusal_reason=REFUSED_BLOCKING_GAP,
            refusal_detail=(
                f"{summary.gaps_blocking_council} gap(s) block the Council."
            ),
            gaps=gaps,
            open_question_keys=open_keys,
            playbook_versions=dict(run.playbook_versions_json or {}),
            ledger_counts=counts,
        )
    if not findings:
        # A Council with nothing evidence-linked to reason over would produce prose.
        return CouncilInput(
            research_run_id=run.id,
            convened=False,
            refusal_reason=REFUSED_NO_FINDINGS,
            refusal_detail=(
                "The run produced no evidence-linked findings, so there is nothing for "
                "the Council to reason over that is not prose."
            ),
            gaps=gaps,
            open_question_keys=open_keys,
            playbook_versions=dict(run.playbook_versions_json or {}),
            ledger_counts=counts,
        )

    # Each query fetches ONE row past its cap, which is enough to detect truncation
    # cheaply and NOT enough to count it. So when truncation is detected the real total
    # is counted, because `{"findings": 1}` when five were dropped is a wrong number —
    # and a wrong number in a field called "truncated" tells a reader the pack is nearly
    # complete when it is not.
    truncated: dict[str, int] = {}
    if len(findings) > MAX_FINDINGS:
        total = await _count_findings(session, run)
        truncated["findings"] = max(0, total - MAX_FINDINGS)
        findings = findings[:MAX_FINDINGS]
    if len(gaps) > MAX_GAPS:
        total = await _count_gaps(session, run)
        truncated["gaps"] = max(0, total - MAX_GAPS)
        gaps = gaps[:MAX_GAPS]
    if len(disagreements) > MAX_DISAGREEMENTS:
        total = await _count_disagreements(session, run)
        truncated["disagreements"] = max(0, total - MAX_DISAGREEMENTS)
        disagreements = disagreements[:MAX_DISAGREEMENTS]

    return CouncilInput(
        research_run_id=run.id,
        convened=True,
        findings=findings,
        gaps=gaps,
        disagreements=disagreements,
        open_question_keys=open_keys,
        playbook_versions=dict(run.playbook_versions_json or {}),
        truncated=truncated,
        ledger_counts=counts,
    )


async def ledger_counts(session: Any, run: Any) -> LedgerCounts:
    """The ledger's own counts for the run (ADR-082). Counts, never a pack.

    Withdrawn findings are excluded and open gaps are the population the Council is
    shown (not closed, not reconciled closed or superseded), so these agree with every
    list a reader sees. Primary source = cites the issuer's own documents.
    """
    rows = (
        await session.execute(
            select(ResearchFinding.verification_status, ResearchFinding.source_kinds_json)
            .where(
                ResearchFinding.research_run_id == run.id,
                ResearchFinding.verification_status != "withdrawn",
            )
        )
    ).all()
    gaps_open = await _count(
        session,
        ResearchGap,
        *_shown_gap_conditions(run),
        ResearchGap.status == ledger.GAP_OPEN,
    )
    unresolved = await _count(
        session,
        ResearchDisagreement,
        ResearchDisagreement.research_run_id == run.id,
        ResearchDisagreement.resolution == ledger.UNRESOLVED,
    )
    return LedgerCounts(
        findings=len(rows),
        verified=sum(1 for status, _ in rows if status == "verified"),
        primary_source=sum(
            1 for _, kinds in rows if set(kinds or ()) & ISSUER_SOURCE_KINDS
        ),
        gaps_open=gaps_open,
        unresolved_disagreements=unresolved,
    )


async def _count(session: Any, model: Any, *where: Any) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(model).where(*where)
    return int((await session.execute(stmt)).scalar_one() or 0)


async def _count_findings(session: Any, run: Any) -> int:
    return await _count(
        session,
        ResearchFinding,
        ResearchFinding.research_run_id == run.id,
        ResearchFinding.verification_status != "withdrawn",
    )


async def _count_gaps(session: Any, run: Any) -> int:
    return await _count(
        session,
        ResearchGap,
        *_shown_gap_conditions(run),
    )


def _shown_gap_conditions(run: Any) -> tuple[Any, ...]:
    """A gap the Council sees: not closed, and not reconciled closed or superseded."""
    return (
        ResearchGap.research_run_id == run.id,
        ResearchGap.status != ledger.GAP_CLOSED,
        or_(
            ResearchGap.reconciliation_status.is_(None),
            ResearchGap.reconciliation_status.not_in(sorted(ledger.RECONCILED_HIDDEN)),
        ),
    )


async def _count_disagreements(session: Any, run: Any) -> int:
    return await _count(
        session, ResearchDisagreement, ResearchDisagreement.research_run_id == run.id
    )


async def _findings(session: Any, run: Any) -> tuple[FindingRef, ...]:
    """Verified first, then the rest. A withdrawn finding is excluded.

    Ordering matters: when truncation bites it must drop the *least* supported material,
    and a withdrawn finding — one the Red Team retired — must not come back through the
    Council's front door.
    """
    stmt = (
        select(ResearchFinding)
        .where(
            ResearchFinding.research_run_id == run.id,
            ResearchFinding.verification_status != "withdrawn",
        )
        .order_by(
            ResearchFinding.verification_status.desc(),
            ResearchFinding.evidence_count.desc(),
            ResearchFinding.created_at,
        )
        .limit(MAX_FINDINGS + 1)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(FindingRef.from_row(row) for row in rows)


async def _gaps(session: Any, run: Any) -> tuple[GapRef, ...]:
    stmt = (
        select(ResearchGap)
        .where(*_shown_gap_conditions(run))
        .order_by(ResearchGap.blocks_council.desc(), ResearchGap.created_at)
        .limit(MAX_GAPS + 1)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(
        GapRef(
            gap_id=row.id,
            gap_type=row.gap_type,
            description=row.description,
            why_it_matters=row.why_it_matters,
            status=row.status,
            closable=row.closable,
            reconciliation_status=getattr(row, "reconciliation_status", None),
            addressed_by_finding_ids=tuple(
                str(f) for f in ((getattr(row, "reconciliation_json", None) or {})
                                 .get("finding_ids") or ())
            ),
            reconciliation_reasons=tuple(
                str(r) for r in ((getattr(row, "reconciliation_json", None) or {})
                                 .get("reasons") or ())
            ),
            gap_status=(getattr(row, "reconciliation_json", None) or {}).get("gap_status"),
            resolution_result=_resolution_field(row, "result"),
            resolution_words=_resolution_field(row, "words"),
        )
        for row in rows
    )


def _resolution_field(row: Any, key: str) -> str | None:
    """ADR-094 — one field of the gap's persisted resolution."""
    found = (getattr(row, "reconciliation_json", None) or {}).get("resolution")
    value = found.get(key) if isinstance(found, dict) else None
    return str(value) if value else None


async def _disagreements(session: Any, run: Any) -> tuple[DisagreementRef, ...]:
    """Unresolved first — they are the input the Chair must not lose."""
    stmt = (
        select(ResearchDisagreement)
        .where(ResearchDisagreement.research_run_id == run.id)
        .order_by(
            ResearchDisagreement.resolution.desc(), ResearchDisagreement.created_at
        )
        .limit(MAX_DISAGREEMENTS + 1)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(
        DisagreementRef(
            disagreement_id=row.id,
            finding_a_id=row.finding_a_id,
            finding_b_id=row.finding_b_id,
            nature=row.nature,
            description=row.description,
            resolution=row.resolution,
            resolution_note=row.resolution_note,
        )
        for row in rows
    )


async def _open_question_keys(session: Any, run: Any) -> tuple[str, ...]:
    stmt = (
        select(ResearchQuestion.question_key)
        .where(
            ResearchQuestion.research_run_id == run.id,
            ResearchQuestion.resolution_status == ledger.QUESTION_OPEN,
        )
        .order_by(ResearchQuestion.question_key)
    )
    return tuple(row[0] for row in (await session.execute(stmt)).all())


def unresolvable_citations(
    council_input: CouncilInput, cited: "frozenset[str] | set[str]"
) -> tuple[str, ...]:
    """Citations that name nothing in this run's ledger.

    The replacement for V2's positional handles, and the check that makes the replacement
    meaningful: an ``E1`` could never be checked, because it referred to a position in a
    pack nobody kept. A ``finding_id`` either is in this set or is a fabrication.
    """
    return tuple(sorted(set(cited) - council_input.citable_finding_ids))


__all__ = [
    "MAX_DISAGREEMENTS",
    "MAX_FINDINGS",
    "MAX_GAPS",
    "REFUSAL_REASONS",
    "REFUSED_BLOCKING_GAP",
    "REFUSED_BLOCKING_QUESTION",
    "REFUSED_NO_FINDINGS",
    "CouncilInput",
    "DisagreementRef",
    "FindingRef",
    "LedgerCounts",
    "GapRef",
    "assemble",
    "ledger_counts",
    "unresolvable_citations",
]
