"""The durable job id, and the rule that it is never guessed. V3.17.9.

WHAT WAS BROKEN
===============
``research_jobs.id`` is the id of the **durable job** a worker leases and executes. Five
tables carry a ``research_job_id`` foreign key back to it — ``research_runs``,
``research_leads``, ``research_tool_calls``, ``calculation_records`` and
``research_run_consumption`` — and between them they are the entire record of what one
research job retrieved, computed and spent.

Every one of them was NULL in production, for two different reasons stacked on top of
each other:

1. **The id never left the handler.** ``company_research_job.run_company_research_job``
   holds the real durable id in ``ctx.job.id`` and passed ``content_run_id`` — an
   **AgentRun** id — down the only parameter the pipeline had. The V3 safety guard
   (V3.13.1) correctly recognised that it named no ``research_jobs`` row and stored NULL
   rather than poisoning the transaction, so the failure was silent and the report
   survived. Correct behaviour, on a wrong input.
2. **Three writers never passed it at all.** ``ledger.open_run``, ``persist_lead`` and
   the calculation record simply did not take it, so even a correct id would have
   reached only one of the five tables.

The consequence is the one this slice exists to end: research spend could not be
attributed to the job that caused it, so ``ResearchDecision.cost_usd_total`` could never
be anything but unknown — and **"unlinked" is not the same unknown as "unpriced"**. The
first is a defect; the second is an honest answer. They were indistinguishable.

THE RULE
========
A durable job id is **threaded explicitly or it is NULL**. It is never derived from an
AgentRun id, a company id, a timestamp, or "the most recent job for this company" — every
one of those is a guess that is right often enough to be trusted and wrong often enough
to attribute one company's spend to another company's decision.

:func:`resolve_durable_job_id` is the single gate. It is **fail-closed**: an id that does
not name a real row is dropped, because the column is a foreign key and a broken link
costs the whole transaction — and with it the V2 report that had already been produced.
That is not hypothetical; it is measured, at head 038, in
``tests/test_v3_pipeline_postgres_integrity.py``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

__all__ = [
    "JobLineage",
    "coerce_job_id",
    "counts_for_job",
    "resolve_durable_job_id",
]


def coerce_job_id(value: Any) -> uuid.UUID | None:
    """A ``research_jobs.id`` as a UUID, or None. Never raises.

    ``JobView.id`` is a string by design — the contract is deliberately decoupled from
    the ORM — so the handler has to convert, and a handler that raises on its own
    bookkeeping would fail a research run over a telemetry link.
    """
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError):
        return None


async def resolve_durable_job_id(session: Any, candidate: Any) -> uuid.UUID | None:
    """Return ``candidate`` only if it really names a ``research_jobs`` row.

    Fail-closed, and deliberately cheap: one primary-key lookup per research run, whose
    cost is invisible next to the run itself.

    Returning None rather than raising is the contract the callers depend on — a run must
    never fail because its audit link could not be established, and an unlinked row is
    worth more than no row. What must never happen is a *wrong* link, which is why this
    checks rather than trusts.
    """
    job_id = coerce_job_id(candidate)
    if job_id is None:
        return None
    try:
        from sqlalchemy import select

        from app.models.research_job import ResearchJob

        found = (
            await session.execute(
                select(ResearchJob.id).where(ResearchJob.id == job_id)
            )
        ).scalar_one_or_none()
    except Exception:  # noqa: BLE001 - never fail a run over a link
        return None
    return job_id if found is not None else None


@dataclass(frozen=True)
class JobLineage:
    """What one durable job is on the record for.

    Row counts, not money. The cost of a decision is
    ``escalation.cost.spend_for_decision``; this answers the narrower question an
    operator actually asks after a run — *did this job's work get attributed to it?* —
    and it answers it over HTTP, because this platform's database is not reachable from
    outside its own virtual network.

    A row here is a row whose ``research_job_id`` **equals this job's id**. Nothing is
    matched by company, by AgentRun or by time, so a count of zero means exactly what it
    says: nothing was attributed to this job.
    """

    job_id: uuid.UUID
    exists: bool = False
    agent_run_id: uuid.UUID | None = None
    research_runs: int = 0
    tool_calls: int = 0
    research_leads: int = 0
    calculation_records: int = 0
    consumption_rows: int = 0
    #: Summed from ``research_tool_calls.consumption_json``, which is written whatever
    #: ``V3_RUN_CONSUMPTION_ENABLED`` says.
    tool_call_model_calls: int = 0
    tool_call_model_tokens: int = 0
    #: ``None`` when unpriced or unrecorded. **Never 0.0** — see CLAUDE.md #6.
    estimated_cost_usd: float | None = None
    priced_consumption_rows: int = 0
    unpriced_consumption_rows: int = 0

    @property
    def attributed(self) -> bool:
        """Did ANY V3 row name this job? The single question this endpoint exists for."""
        return bool(
            self.research_runs
            or self.tool_calls
            or self.research_leads
            or self.calculation_records
            or self.consumption_rows
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_id": str(self.job_id),
            "exists": self.exists,
            "agent_run_id": str(self.agent_run_id) if self.agent_run_id else None,
            "attributed": self.attributed,
            "research_runs": self.research_runs,
            "tool_calls": self.tool_calls,
            "research_leads": self.research_leads,
            "calculation_records": self.calculation_records,
            "consumption_rows": self.consumption_rows,
            "tool_call_model_calls": self.tool_call_model_calls,
            "tool_call_model_tokens": self.tool_call_model_tokens,
            "estimated_cost_usd": self.estimated_cost_usd,
            "priced_consumption_rows": self.priced_consumption_rows,
            "unpriced_consumption_rows": self.unpriced_consumption_rows,
        }


async def counts_for_job(session: Any, job_id: uuid.UUID) -> JobLineage:
    """Every V3 row that names this durable job. Read-only.

    Counted per table rather than summed into one number, because the five writers fail
    independently: three of them never passed the column at all before V3.17.9, so a
    single total would have looked merely low rather than structurally wrong.
    """
    from sqlalchemy import func, select

    from app.models.calculation import CalculationRecord
    from app.models.ledger import ResearchRun
    from app.models.research_job import ResearchJob
    from app.models.research_lead import ResearchLeadRecord
    from app.models.research_run_consumption import ResearchRunConsumption
    from app.models.research_tool_call import ResearchToolCall

    job = await session.get(ResearchJob, job_id)
    if job is None:
        return JobLineage(job_id=job_id, exists=False)

    async def _count(column: Any) -> int:
        return int(
            (await session.execute(select(func.count()).where(column == job_id)))
            .scalar_one()
            or 0
        )

    tool_rows = (
        await session.execute(
            select(ResearchToolCall.consumption_json).where(
                ResearchToolCall.research_job_id == job_id
            )
        )
    ).all()
    calls, tokens = _tool_call_units(tool_rows)

    costs = (
        (
            await session.execute(
                select(ResearchRunConsumption.estimated_cost_usd).where(
                    ResearchRunConsumption.research_job_id == job_id
                )
            )
        )
        .scalars()
        .all()
    )
    priced = [c for c in costs if c is not None]
    # NULL when anything is unpriced or nothing was recorded. A partial sum presented as
    # a cost is the error this whole slice is about.
    total = (
        round(sum(float(c) for c in priced), 6)
        if priced and len(priced) == len(costs)
        else None
    )

    return JobLineage(
        job_id=job_id,
        exists=True,
        agent_run_id=job.agent_run_id,
        research_runs=await _count(ResearchRun.research_job_id),
        tool_calls=len(tool_rows),
        research_leads=await _count(ResearchLeadRecord.research_job_id),
        calculation_records=await _count(CalculationRecord.research_job_id),
        consumption_rows=len(costs),
        tool_call_model_calls=calls,
        tool_call_model_tokens=tokens,
        estimated_cost_usd=total,
        priced_consumption_rows=len(priced),
        unpriced_consumption_rows=len(costs) - len(priced),
    )


def _tool_call_units(rows: Any) -> tuple[int, int]:
    """Model calls and tokens from tool-call consumption payloads.

    Tolerant: ``consumption_json`` is NULL on a tool that declared no instrumented unit,
    and an absent unit is **not zero** — it is unmeasured. Both contribute nothing, which
    is the only honest handling, and neither is ever presented as money.
    """
    calls = tokens = 0
    for (payload,) in rows:
        if not isinstance(payload, dict):
            continue
        calls += int(payload.get("model_calls") or 0)
        tokens += int(payload.get("model_input_tokens") or 0)
        tokens += int(payload.get("model_output_tokens") or 0)
    return calls, tokens
