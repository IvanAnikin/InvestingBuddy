"""Computing the ResearchDelta — V3.8 Slice 8.2.

WHAT IT ANSWERS
===============
> **What changed since the previous analysis, and which prior conclusions should be
> revisited?**

Six dimensions, each computed from the two runs' ledgers rather than from prose:

* ``new_evidence`` — evidence ids the new run cites that the old one did not;
* ``changed_facts`` — the same *slot* (question, period, scope) with a different
  statement, which is a restatement or a new period;
* ``resolved_questions`` — previously open, now answered, **with the evidence**;
* ``new_gaps`` / ``closed_gaps``;
* ``invalidated_findings`` — prior conclusions the new evidence undermines.

INVALIDATION IS DETERMINISTIC, AND DELIBERATELY CONSERVATIVE
============================================================
A prior finding is invalidated when the new run has a finding in the **same slot** whose
statement differs and which the prior finding's evidence does not support — i.e. the new
one is built on evidence the old one did not have.

It is **not** invalidated merely because the new run did not repeat it. A run that ran out
of budget before reaching a question has not disproved last quarter's answer, and treating
silence as refutation would make every truncated run look like a reversal. That asymmetry
is the whole reason this is computed rather than asked of a model.

THE OLD ROWS ARE NOT MODIFIED
=============================
Invalidation is a property of the relationship between two runs, not of the old finding —
it was a correct conclusion from the evidence available then. Rewriting it would make the
earlier run's record a lie in service of the later one's convenience.

UNCHANGED IS COMPUTED, NEVER DEFAULTED
======================================
``unchanged_core_thesis`` is False whenever anything was invalidated, and a database CHECK
refuses the contradictory row. A delta with no comparison basis is **not** "unchanged" —
it is a first run, and it says so by having no ``from_run``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select

from app.models.delta import ResearchDelta
from app.models.ledger import ResearchFinding, ResearchGap, ResearchQuestion
from app.services.ledger import store as ledger

#: Bounds on what one delta records. A delta listing nine thousand new evidence ids is a
#: reindex, not a change report.
MAX_LISTED = 200


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def slot_of(finding: ResearchFinding) -> tuple[str, str, str]:
    """What makes two findings comparable: the same question, period and scope.

    Not the statement text. Two runs phrasing one conclusion differently are not a
    change, and two runs reporting different numbers for the same slot are — which is the
    distinction the whole delta depends on.
    """
    return (
        (finding.question_key or "").casefold(),
        (finding.period_key or "").casefold(),
        (finding.scope_key or "").casefold(),
    )


@dataclass
class DeltaResult:
    """The computed change, before it is written."""

    company_id: uuid.UUID | None = None
    from_run_id: uuid.UUID | None = None
    to_run_id: uuid.UUID | None = None
    new_evidence_ids: list[str] = field(default_factory=list)
    changed_facts: list[dict[str, Any]] = field(default_factory=list)
    resolved_question_keys: list[str] = field(default_factory=list)
    new_gap_ids: list[str] = field(default_factory=list)
    closed_gap_ids: list[str] = field(default_factory=list)
    invalidated_finding_ids: list[str] = field(default_factory=list)

    @property
    def is_first_run(self) -> bool:
        """No prior run. **Not the same as "nothing changed"** — everything is new."""
        return self.from_run_id is None

    @property
    def unchanged_core_thesis(self) -> bool:
        """Computed, never defaulted.

        A first run is not "unchanged": it has no thesis to be unchanged from.
        """
        if self.is_first_run:
            return False
        return not self.invalidated_finding_ids

    @property
    def has_any_change(self) -> bool:
        return bool(
            self.new_evidence_ids
            or self.changed_facts
            or self.resolved_question_keys
            or self.new_gap_ids
            or self.closed_gap_ids
            or self.invalidated_finding_ids
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "company_id": str(self.company_id) if self.company_id else None,
            "from_run_id": str(self.from_run_id) if self.from_run_id else None,
            "to_run_id": str(self.to_run_id) if self.to_run_id else None,
            "is_first_run": self.is_first_run,
            "unchanged_core_thesis": self.unchanged_core_thesis,
            "new_evidence": list(self.new_evidence_ids),
            "changed_facts": list(self.changed_facts),
            "resolved_questions": list(self.resolved_question_keys),
            "new_gaps": list(self.new_gap_ids),
            "closed_gaps": list(self.closed_gap_ids),
            "invalidated_findings": list(self.invalidated_finding_ids),
            "counts": {
                "new_evidence": len(self.new_evidence_ids),
                "changed_facts": len(self.changed_facts),
                "resolved_questions": len(self.resolved_question_keys),
                "new_gaps": len(self.new_gap_ids),
                "closed_gaps": len(self.closed_gap_ids),
                "invalidated_findings": len(self.invalidated_finding_ids),
            },
        }


async def compute_delta(
    session: Any, *, to_run: Any, from_run: Any | None = None
) -> DeltaResult:
    """Compare two runs' ledgers. Deterministic; no model is consulted."""
    result = DeltaResult(
        company_id=to_run.company_id,
        from_run_id=from_run.id if from_run is not None else None,
        to_run_id=to_run.id,
    )

    new_findings = await _findings(session, to_run)
    new_evidence = _evidence_ids(new_findings)
    if from_run is None:
        # Everything is new. A first run has no thesis to be unchanged from, and saying
        # "nothing changed" would be the most misleading possible summary of it.
        result.new_evidence_ids = sorted(new_evidence)[:MAX_LISTED]
        result.new_gap_ids = [
            str(gap.id) for gap in await _gaps(session, to_run)
        ][:MAX_LISTED]
        return result

    old_findings = await _findings(session, from_run)
    old_evidence = _evidence_ids(old_findings)
    result.new_evidence_ids = sorted(new_evidence - old_evidence)[:MAX_LISTED]

    old_by_slot: dict[tuple[str, str, str], list[ResearchFinding]] = {}
    for finding in old_findings:
        old_by_slot.setdefault(slot_of(finding), []).append(finding)

    for finding in new_findings:
        slot = slot_of(finding)
        priors = old_by_slot.get(slot)
        if not priors:
            continue
        for prior in priors:
            if _statements_agree(prior, finding):
                continue
            supported_by_new_evidence = bool(
                set(finding.evidence_ids_json or ()) - set(prior.evidence_ids_json or ())
            ) or bool(finding.calculation_ids_json)
            if not supported_by_new_evidence:
                # Same slot, different words, no new evidence. That is a rephrasing, and
                # calling it a changed fact would make every re-run look like a
                # restatement.
                continue
            result.changed_facts.append(
                {
                    "slot": {
                        "question_key": finding.question_key,
                        "period_key": finding.period_key,
                        "scope_key": finding.scope_key,
                    },
                    "previous_finding_id": str(prior.id),
                    "previous_statement": prior.statement,
                    "current_finding_id": str(finding.id),
                    "current_statement": finding.statement,
                }
            )
            if str(prior.id) not in result.invalidated_finding_ids:
                result.invalidated_finding_ids.append(str(prior.id))
    result.changed_facts = result.changed_facts[:MAX_LISTED]
    result.invalidated_finding_ids = result.invalidated_finding_ids[:MAX_LISTED]

    result.resolved_question_keys = await _newly_resolved(session, from_run, to_run)
    old_gaps = {_gap_key(g): g for g in await _gaps(session, from_run)}
    new_gaps = {_gap_key(g): g for g in await _gaps(session, to_run)}
    result.new_gap_ids = [
        str(gap.id) for key, gap in new_gaps.items() if key not in old_gaps
    ][:MAX_LISTED]
    result.closed_gap_ids = [
        str(gap.id) for key, gap in old_gaps.items() if key not in new_gaps
    ][:MAX_LISTED]
    return result


def _statements_agree(a: ResearchFinding, b: ResearchFinding) -> bool:
    return " ".join(a.statement.split()).casefold() == " ".join(
        b.statement.split()
    ).casefold()


def _evidence_ids(findings: "list[ResearchFinding]") -> set[str]:
    return {
        str(eid)
        for finding in findings
        for eid in (finding.evidence_ids_json or ())
        if str(eid).strip()
    }


def _gap_key(gap: ResearchGap) -> tuple[str, str]:
    """A gap's identity across runs: its type and the question it is about.

    Not its id — a gap raised twice about the same thing is one gap that has not been
    closed, and counting it as both new and closed would report churn where there is
    persistence.
    """
    return (gap.gap_type, (gap.question_key or gap.description[:80]).casefold())


async def _findings(session: Any, run: Any) -> list[ResearchFinding]:
    stmt = (
        select(ResearchFinding)
        .where(
            ResearchFinding.research_run_id == run.id,
            ResearchFinding.verification_status != "withdrawn",
        )
        .order_by(ResearchFinding.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _gaps(session: Any, run: Any) -> list[ResearchGap]:
    stmt = (
        select(ResearchGap)
        .where(
            ResearchGap.research_run_id == run.id,
            ResearchGap.status.in_([ledger.GAP_OPEN, ledger.GAP_ACCEPTED]),
        )
        .order_by(ResearchGap.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


async def _newly_resolved(session: Any, from_run: Any, to_run: Any) -> list[str]:
    """Questions that were open then and are answered now.

    A question the new run never asked is **not** resolved — that is silence, not an
    answer, and the distinction is what stops a narrower run from reading as progress.
    """
    old_open = {
        row[0]
        for row in (
            await session.execute(
                select(ResearchQuestion.question_key).where(
                    ResearchQuestion.research_run_id == from_run.id,
                    ResearchQuestion.resolution_status == ledger.QUESTION_OPEN,
                )
            )
        ).all()
    }
    if not old_open:
        return []
    now_answered = {
        row[0]
        for row in (
            await session.execute(
                select(ResearchQuestion.question_key).where(
                    ResearchQuestion.research_run_id == to_run.id,
                    ResearchQuestion.resolution_status == ledger.QUESTION_ANSWERED,
                )
            )
        ).all()
    }
    return sorted(old_open & now_answered)[:MAX_LISTED]


async def persist_delta(
    session: Any, result: DeltaResult, *, now: datetime | None = None
) -> ResearchDelta:
    """Write the delta. **Counts are derived here and nowhere else.**

    A delta must name the run it is *to*. That rule lives here rather than in a database
    CHECK because both foreign keys are ``SET NULL``: a CHECK requiring a run id would
    refuse the update that pruning a run performs, making the very cleanup the cascade
    exists for impossible. Found by deleting both runs against real PostgreSQL.
    """
    if result.to_run_id is None:
        raise ValueError(
            "a delta must name the run it is TO. One that compares nothing is a row "
            "asserting that nothing changed between two runs it cannot name."
        )
    row = ResearchDelta(
        id=uuid.uuid4(),
        company_id=result.company_id,
        from_run_id=result.from_run_id,
        to_run_id=result.to_run_id,
        unchanged_core_thesis=result.unchanged_core_thesis,
        new_evidence_ids_json=result.new_evidence_ids or None,
        changed_facts_json=result.changed_facts or None,
        resolved_question_keys_json=result.resolved_question_keys or None,
        new_gap_ids_json=result.new_gap_ids or None,
        closed_gap_ids_json=result.closed_gap_ids or None,
        invalidated_finding_ids_json=result.invalidated_finding_ids or None,
        new_evidence_count=len(result.new_evidence_ids),
        changed_fact_count=len(result.changed_facts),
        resolved_question_count=len(result.resolved_question_keys),
        new_gap_count=len(result.new_gap_ids),
        closed_gap_count=len(result.closed_gap_ids),
        invalidated_finding_count=len(result.invalidated_finding_ids),
        computed_at=now or _utcnow(),
    )
    session.add(row)
    await session.flush()
    return row


async def latest_delta(
    session: Any, *, company_id: uuid.UUID
) -> ResearchDelta | None:
    stmt = (
        select(ResearchDelta)
        .where(ResearchDelta.company_id == company_id)
        .order_by(ResearchDelta.computed_at.desc())
        .limit(1)
    )
    return (await session.execute(stmt)).scalar_one_or_none()


__all__ = [
    "MAX_LISTED",
    "DeltaResult",
    "compute_delta",
    "latest_delta",
    "persist_delta",
    "slot_of",
]
