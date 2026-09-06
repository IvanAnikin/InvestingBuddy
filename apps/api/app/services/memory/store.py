"""Research memory — V3.8 Slice 8.1.

MEMORY IS THE PREVIOUS RESEARCH, NOT A COPY OF IT
=================================================
There is **no snapshot table**, and that is the design rather than an omission. A copy of
the prior run's conclusions would be a second source of truth that drifts from the first —
and drift here is uniquely bad, because the copy is what a later run would trust while the
original is what a citation resolves against.

So memory reads the prior run's **ledger**: its findings, its gaps, its disagreements,
already evidence-linked and already refused if they were not. ``research_runs`` is indexed
on ``(company_id, started_at)``, which is what "retrievable by entity" needs.

THREE RULES, FROM THE ARCHITECTURE
==================================
> **New primary evidence always outranks stale memory. Memory proposes; evidence
> disposes.**

A remembered conclusion that contradicts a fresh filing is an *invalidated finding*, never
a tiebreaker. This module therefore returns memory **labelled as memory** — every recalled
finding carries the run it came from and how old it is — and never merges it into a new
run's findings. Merging would make the provenance a matter of reading the statement text.

> **Memory is never a citation source.**

It points at evidence ids. If the underlying evidence no longer resolves, the memory goes
with it — which is why ``recall`` reports ``stale_evidence_ids`` rather than quietly
returning a finding whose support has gone.

> **A gap is what carries forward.**

An open gap from the last run is the next run's question, with ``origin=prior_gap`` (5.2
already consumes exactly that shape). That is the single most useful thing memory does: it
is the difference between a refresh that re-derives everything and one that starts where
the last one stopped.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select

from app.models.ledger import (
    ResearchDisagreement,
    ResearchFinding,
    ResearchGap,
    ResearchRun,
)
from app.services.ledger import store as ledger

#: How many prior findings one recall may return. A bound, not a quality knob: a new run
#: handed nine hundred remembered statements is a run reading a corpus of its own past.
MAX_RECALLED_FINDINGS = 80
MAX_RECALLED_GAPS = 40

#: Beyond this, a remembered finding is labelled stale. It is still returned — age is a
#: property a reader weighs, not a reason to hide something — but a run that treats a
#: two-year-old conclusion as current has confused memory with evidence.
STALE_AFTER_DAYS = 400


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class RecalledFinding:
    """A prior conclusion, **labelled as one.**

    Never merged into a new run's findings: merging would make the provenance a matter of
    reading the statement text, and "memory proposes, evidence disposes" only works if a
    reader can tell which is which.
    """

    finding_id: uuid.UUID
    research_run_id: uuid.UUID
    statement: str
    mechanism: str | None
    direction: str | None
    confidence: float | None
    evidence_ids: tuple[str, ...]
    calculation_ids: tuple[str, ...]
    period_key: str | None
    scope_key: str | None
    question_key: str | None
    verification_status: str
    recorded_at: datetime
    age_days: int

    @property
    def is_stale(self) -> bool:
        return self.age_days > STALE_AFTER_DAYS

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": str(self.finding_id),
            "research_run_id": str(self.research_run_id),
            "statement": self.statement,
            "mechanism": self.mechanism,
            "direction": self.direction,
            "confidence": self.confidence,
            "evidence_ids": list(self.evidence_ids),
            "calculation_ids": list(self.calculation_ids),
            "period_key": self.period_key,
            "scope_key": self.scope_key,
            "question_key": self.question_key,
            "verification_status": self.verification_status,
            "recorded_at": self.recorded_at.isoformat(),
            "age_days": self.age_days,
            "is_stale": self.is_stale,
            # Stated in the payload, not only in the type, because this dict is what a
            # prompt builder sees.
            "kind": "prior_research_memory",
        }


@dataclass(frozen=True)
class RecalledGap:
    """An open gap from a prior run. **The next run's question.**"""

    gap_id: uuid.UUID
    research_run_id: uuid.UUID
    gap_type: str
    description: str
    why_it_matters: str | None
    question_key: str | None
    closable: bool
    status: str

    def as_question(self) -> tuple[str, str]:
        """``(question_key, text)`` in the shape ``plan_research`` already consumes."""
        key = self.question_key or f"gap_{str(self.gap_id)[:8]}"
        return key, self.description


@dataclass
class PriorResearch:
    """What the platform concluded last time, and how much of it still stands."""

    company_id: uuid.UUID | None
    run_id: uuid.UUID | None
    run_completed_at: datetime | None
    mode: str | None = None
    stopped_by: str | None = None
    playbook_versions: dict[str, int] = field(default_factory=dict)
    findings: tuple[RecalledFinding, ...] = ()
    open_gaps: tuple[RecalledGap, ...] = ()
    unresolved_disagreement_count: int = 0
    #: Evidence ids the prior findings cite that no longer resolve. Memory is never a
    #: citation source: if the support has gone, the memory goes with it.
    stale_evidence_ids: tuple[str, ...] = ()
    truncated: dict[str, int] = field(default_factory=dict)

    @property
    def exists(self) -> bool:
        return self.run_id is not None

    @property
    def stale_findings(self) -> tuple[RecalledFinding, ...]:
        return tuple(f for f in self.findings if f.is_stale)

    @property
    def carry_forward_questions(self) -> tuple[tuple[str, str], ...]:
        """Prior open gaps, as questions for the next run.

        Only **closable** ones. A gap no source can close would be re-asked every run
        forever, which is a memory that has learned nothing.
        """
        return tuple(gap.as_question() for gap in self.open_gaps if gap.closable)

    def to_dict(self) -> dict[str, Any]:
        return {
            "exists": self.exists,
            "company_id": str(self.company_id) if self.company_id else None,
            "run_id": str(self.run_id) if self.run_id else None,
            "run_completed_at": (
                self.run_completed_at.isoformat() if self.run_completed_at else None
            ),
            "mode": self.mode,
            "stopped_by": self.stopped_by,
            "playbook_versions": dict(self.playbook_versions),
            "finding_count": len(self.findings),
            "stale_finding_count": len(self.stale_findings),
            "open_gap_count": len(self.open_gaps),
            "carry_forward_question_count": len(self.carry_forward_questions),
            "unresolved_disagreement_count": self.unresolved_disagreement_count,
            "stale_evidence_ids": list(self.stale_evidence_ids),
            "truncated": dict(self.truncated),
        }


async def previous_run(
    session: Any, *, company_id: uuid.UUID, before: datetime | None = None
) -> ResearchRun | None:
    """The most recent run for this issuer that got far enough to have conclusions.

    ``planning`` is excluded: a run that never investigated has nothing to remember, and
    returning it would make "there is no prior research" indistinguishable from "the last
    attempt did not start".
    """
    stmt = select(ResearchRun).where(
        ResearchRun.company_id == company_id,
        ResearchRun.status.in_(
            [ledger.RUN_COMPLETE, ledger.RUN_REVIEWING, ledger.RUN_STOPPED]
        ),
    )
    if before is not None:
        stmt = stmt.where(ResearchRun.started_at < before)
    stmt = stmt.order_by(ResearchRun.started_at.desc()).limit(1)
    return (await session.execute(stmt)).scalar_one_or_none()


async def recall(
    session: Any,
    *,
    company_id: uuid.UUID,
    before: datetime | None = None,
    resolvable_evidence_ids: "Sequence[str] | None" = None,
    now: datetime | None = None,
) -> PriorResearch:
    """What the last run concluded, labelled as memory.

    ``resolvable_evidence_ids`` is the caller's view of which evidence still resolves —
    injected rather than looked up here, because "does this chunk still exist" is a corpus
    question and memory must not become a second implementation of it. When it is
    ``None`` no staleness check is performed and none is claimed.
    """
    stamp = now or _utcnow()
    run = await previous_run(session, company_id=company_id, before=before)
    if run is None:
        return PriorResearch(company_id=company_id, run_id=None, run_completed_at=None)

    findings = await _findings(session, run, now=stamp)
    gaps = await _gaps(session, run)
    truncated: dict[str, int] = {}
    if len(findings) > MAX_RECALLED_FINDINGS:
        truncated["findings"] = len(findings) - MAX_RECALLED_FINDINGS
        findings = findings[:MAX_RECALLED_FINDINGS]
    if len(gaps) > MAX_RECALLED_GAPS:
        truncated["gaps"] = len(gaps) - MAX_RECALLED_GAPS
        gaps = gaps[:MAX_RECALLED_GAPS]

    stale_ids: tuple[str, ...] = ()
    if resolvable_evidence_ids is not None:
        resolvable = set(resolvable_evidence_ids)
        cited = {eid for finding in findings for eid in finding.evidence_ids}
        stale_ids = tuple(sorted(cited - resolvable))

    from sqlalchemy import func

    unresolved = int(
        (
            await session.execute(
                select(func.count())
                .select_from(ResearchDisagreement)
                .where(
                    ResearchDisagreement.research_run_id == run.id,
                    ResearchDisagreement.resolution == ledger.UNRESOLVED,
                )
            )
        ).scalar_one()
        or 0
    )

    return PriorResearch(
        company_id=company_id,
        run_id=run.id,
        run_completed_at=_aware(run.completed_at),
        mode=run.mode,
        stopped_by=run.stopped_by,
        playbook_versions=dict(run.playbook_versions_json or {}),
        findings=findings,
        open_gaps=gaps,
        unresolved_disagreement_count=unresolved,
        stale_evidence_ids=stale_ids,
        truncated=truncated,
    )


async def _findings(
    session: Any, run: ResearchRun, *, now: datetime
) -> tuple[RecalledFinding, ...]:
    """Prior findings, **excluding withdrawn ones.**

    A finding the Red Team retired must not come back a quarter later wearing the
    authority of memory. That is the same rule 7.1 applies to the Council, and memory is
    the path it would otherwise return by.
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
        .limit(MAX_RECALLED_FINDINGS + 1)
    )
    rows = (await session.execute(stmt)).scalars().all()
    out: list[RecalledFinding] = []
    for row in rows:
        recorded = _aware(row.created_at) or now
        out.append(
            RecalledFinding(
                finding_id=row.id,
                research_run_id=run.id,
                statement=row.statement,
                mechanism=row.mechanism,
                direction=row.direction,
                confidence=row.confidence,
                evidence_ids=tuple(row.evidence_ids_json or ()),
                calculation_ids=tuple(row.calculation_ids_json or ()),
                period_key=row.period_key,
                scope_key=row.scope_key,
                question_key=row.question_key,
                verification_status=row.verification_status,
                recorded_at=recorded,
                age_days=max(0, (now - recorded) // timedelta(days=1)),
            )
        )
    return tuple(out)


async def _gaps(session: Any, run: ResearchRun) -> tuple[RecalledGap, ...]:
    """Gaps still open or accepted at the end of the prior run.

    ``accepted`` is included: the run finished with it open and said so, and that is
    exactly the state a later run might be able to close if a source has since published.
    """
    stmt = (
        select(ResearchGap)
        .where(
            ResearchGap.research_run_id == run.id,
            ResearchGap.status.in_([ledger.GAP_OPEN, ledger.GAP_ACCEPTED]),
        )
        .order_by(ResearchGap.closable.desc(), ResearchGap.created_at)
        .limit(MAX_RECALLED_GAPS + 1)
    )
    rows = (await session.execute(stmt)).scalars().all()
    return tuple(
        RecalledGap(
            gap_id=row.id,
            research_run_id=run.id,
            gap_type=row.gap_type,
            description=row.description,
            why_it_matters=row.why_it_matters,
            question_key=row.question_key,
            closable=row.closable,
            status=row.status,
        )
        for row in rows
    )


__all__ = [
    "MAX_RECALLED_FINDINGS",
    "MAX_RECALLED_GAPS",
    "STALE_AFTER_DAYS",
    "PriorResearch",
    "RecalledFinding",
    "RecalledGap",
    "previous_run",
    "recall",
]
