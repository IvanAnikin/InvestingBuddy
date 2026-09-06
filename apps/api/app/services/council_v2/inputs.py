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

from sqlalchemy import select

from app.models.ledger import (
    ResearchDisagreement,
    ResearchFinding,
    ResearchGap,
    ResearchQuestion,
)
from app.services.ledger import store as ledger

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

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "verified"

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

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": str(self.gap_id),
            "gap_type": self.gap_type,
            "description": self.description,
            "why_it_matters": self.why_it_matters,
            "status": self.status,
            "closable": self.closable,
        }


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
    def citable_finding_ids(self) -> frozenset[str]:
        """The **only** handles a council key_point may cite.

        A citation to anything outside this set is unresolvable, which is the property
        that replaces V2's positional ``E1``/``E2``.
        """
        return frozenset(str(f.finding_id) for f in self.findings)

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_run_id": str(self.research_run_id),
            "convened": self.convened,
            "refusal_reason": self.refusal_reason,
            "refusal_detail": self.refusal_detail,
            "finding_count": len(self.findings),
            "verified_finding_count": len(self.verified_findings),
            "gap_count": len(self.gaps),
            "disagreement_count": len(self.disagreements),
            "unresolved_disagreement_count": len(self.unresolved_disagreements),
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
        ResearchGap.research_run_id == run.id,
        ResearchGap.status != ledger.GAP_CLOSED,
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
    return tuple(
        FindingRef(
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
        )
        for row in rows
    )


async def _gaps(session: Any, run: Any) -> tuple[GapRef, ...]:
    stmt = (
        select(ResearchGap)
        .where(
            ResearchGap.research_run_id == run.id,
            ResearchGap.status != ledger.GAP_CLOSED,
        )
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
        )
        for row in rows
    )


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
    "GapRef",
    "assemble",
    "unresolvable_citations",
]
