"""What a research decision actually spent. V3.17.9.

``ResearchDecision.cost_usd_total`` had **no producer**. Not a wrong one — none at all.
The column was declared in V3.17.1, read by ``rounds.decide_next_state`` and by the queue
API, and written by nothing, so every decision reported unknown spend and the round
transition stopped at ``cost_unknown`` whenever it got that far. That reads as "no price
book is configured", which is true, and hid the fact that the spend was **unattributable**
as well — a different and more serious problem, because it would not have been fixed by
configuring prices.

THE CANONICAL COST RECORD, AND WHY THERE IS ONLY ONE
====================================================
Two tables persist consumption and this module sums exactly one of them.

``research_run_consumption``
    One row per research run. Vendor-neutral units *and* ``estimated_cost_usd`` derived
    from the configured price book. **This is the money record**, and the only thing
    summed here.

``research_tool_calls``
    One row per attempted tool call, with ``consumption_json`` units. It is the audit and
    debugging trace. Its ``estimated_cost_usd`` column has never been written by anything,
    and — decisively — the units it records are *already folded into* the run row by
    ``company_research_service._record_consumption`` (V3.17.9). Summing both tables would
    count the V3 investigator's tokens twice.

So: **tool calls prove lineage; the run row carries the money.** A future reader tempted
to "also add the tool calls" is looking at the double count, not at missing spend.

UNKNOWN IS NOT ZERO, AND PARTIAL IS NOT TOTAL
=============================================
``estimated_cost_usd`` is NULL whenever no price covers a run's units — which is the state
production is in right now, because no price book is configured. The rules here follow
from CLAUDE.md #6 and from what a cost cap is for:

* **No consumption row for any of this decision's jobs** → ``None``. Not 0.0: the recorder
  may be switched off, and "we did not measure" is not "it was free".
* **Any attributable row unpriced** → ``None``. A sum over just the priced rows is a
  *subtotal*, and a subtotal presented as a total lets a cost cap pass on spend it never
  saw. The subtotal is reported separately, clearly named, and is never the total.
* **Every attributable row priced** → their sum, which is then a real number.

DERIVED, NEVER ACCUMULATED
==========================
:func:`spend_for_decision` recomputes the whole total from the rows every time. It never
adds this round's cost to the stored column. That is what makes it safe under the two
things that certainly happen: a **retried** job (a second attempt writes a second
consumption row, and both were genuinely paid for, so both belong in the total) and a
**replayed round closure** (``job_hook``'s terminal observer *and* the startup sweep may
both close the same round — recomputing yields the same number, ``+=`` would double it).

ATTRIBUTION IS BY LINEAGE, NEVER BY COMPANY
===========================================
Rows are matched on ``research_run_consumption.research_job_id`` alone. Not company, not
AgentRun, not "the most recent run". Two concurrent jobs for one company — an operator's
manual research and an escalation round — share a ``company_id`` and nothing else, and
attributing by company would bill each one for both.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

#: The decision produced no job at all, so there is nothing to attribute yet.
BASIS_NO_JOBS = "no_jobs"
#: Jobs exist, but no consumption row names any of them. The recorder is off, or the run
#: did not reach the point of recording. Unknown, not free.
BASIS_NO_CONSUMPTION = "no_consumption_recorded"
#: Consumption was measured and at least one row carries no price. Unknown, not partial.
BASIS_UNPRICED = "unpriced_consumption"
#: Every attributable row is priced. The total is a real number.
BASIS_PRICED = "attributed_from_priced_consumption"


@dataclass(frozen=True)
class DecisionSpend:
    """What one decision's jobs consumed, and whether that can be stated in money.

    Every field is a count of something real. Nothing here is estimated, and
    ``cost_usd_total`` is ``None`` unless it is genuinely known.
    """

    decision_id: uuid.UUID | None = None
    #: Every durable job this decision ordered, oldest first. The lineage itself.
    job_ids: tuple[uuid.UUID, ...] = ()
    consumption_rows: int = 0
    priced_rows: int = 0
    unpriced_rows: int = 0
    #: The sum over the priced rows ONLY. Present so a reader can see that spend was
    #: measured; it is **not** the total and must never be rendered as one.
    priced_subtotal_usd: float | None = None
    #: ``None`` means unknown. Never 0.0 for an unmeasured or unpriced decision.
    cost_usd_total: float | None = None
    basis: str = BASIS_NO_JOBS
    #: Model/tool usage attributable to this decision, so "consumption known, price
    #: unknown" is demonstrable without database access.
    model_calls: int = 0
    model_tokens: int = 0
    tool_calls: int = 0
    research_runs: int = 0
    #: Model tokens summed from ``research_tool_calls.consumption_json``. Written
    #: UNCONDITIONALLY — unlike the run record, which ``V3_RUN_CONSUMPTION_ENABLED``
    #: gates — so this is where "consumption was measured" is demonstrable when the
    #: recorder is off, which is the production state.
    #:
    #: It OVERLAPS the run record wherever one exists (``_record_consumption`` folds the
    #: same units in), so it is reported and **never summed as money**. Reporting it is
    #: what separates "we did not look" from "we looked and nothing priced it".
    tool_call_model_calls: int = 0
    tool_call_model_tokens: int = 0
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "job_ids": [str(j) for j in self.job_ids],
            "job_count": len(self.job_ids),
            "consumption_rows": self.consumption_rows,
            "priced_rows": self.priced_rows,
            "unpriced_rows": self.unpriced_rows,
            "priced_subtotal_usd": self.priced_subtotal_usd,
            "cost_usd_total": self.cost_usd_total,
            "basis": self.basis,
            "model_calls": self.model_calls,
            "model_tokens": self.model_tokens,
            "tool_calls": self.tool_calls,
            "research_runs": self.research_runs,
            "tool_call_model_calls": self.tool_call_model_calls,
            "tool_call_model_tokens": self.tool_call_model_tokens,
            "detail": self.detail,
        }


def decision_job_key_prefix(decision_id: uuid.UUID) -> str:
    """The ``idempotency_key`` prefix every job of one decision carries.

    ``controller.round_idempotency_key`` mints ``escalation:<decision>:round:<n>`` and
    ``JobStore.enqueue`` appends a ``#<generation>`` suffix. The decision id is therefore
    *inside the key of every job it ordered* — which is what the controller already means
    when it says the link "is a fact rather than a convention".

    Contains no SQL ``LIKE`` metacharacter: ``escalation``, ``round`` and a hyphenated hex
    UUID have no ``%`` and no ``_``.
    """
    return f"escalation:{decision_id}:round:"


async def jobs_for_decision(
    session: AsyncSession, decision: Any
) -> tuple[uuid.UUID, ...]:
    """Every durable job this decision ordered, oldest first.

    TWO EXPLICIT LINKS, AND NO THIRD
    --------------------------------
    * ``research_jobs.idempotency_key`` begins with this decision's id, because the
      decision minted it. This is what reaches **every** round, including the ones
      ``last_job_id`` has already moved past.
    * ``decision.last_job_id`` — the decision's own stored pointer at its current round.
      Included because it is a written FK, and because it is the only link a decision
      created before this slice has.

    There is deliberately no fallback to "jobs for this company" or "the most recent job".
    Both would be right most of the time and would, the rest of the time, bill one
    decision for another's research.
    """
    from app.models.research_job import ResearchJob

    decision_id = getattr(decision, "id", None)
    found: list[uuid.UUID] = []
    if decision_id is not None:
        rows = (
            await session.execute(
                select(ResearchJob.id)
                .where(
                    ResearchJob.idempotency_key.like(
                        decision_job_key_prefix(decision_id) + "%"
                    )
                )
                .order_by(ResearchJob.created_at, ResearchJob.id)
            )
        ).scalars().all()
        found.extend(rows)

    last = getattr(decision, "last_job_id", None)
    if last is not None and last not in found:
        found.append(last)
    return tuple(found)


async def spend_for_decision(
    session: AsyncSession, decision: Any
) -> DecisionSpend:
    """Sum what this decision's jobs consumed, and say what the number means.

    Reads only. The caller decides whether to persist the result, so this can be used by
    a read endpoint without a write side effect.
    """
    from app.models.ledger import ResearchRun
    from app.models.research_run_consumption import ResearchRunConsumption
    from app.models.research_tool_call import ResearchToolCall

    decision_id = getattr(decision, "id", None)
    job_ids = await jobs_for_decision(session, decision)
    if not job_ids:
        return DecisionSpend(
            decision_id=decision_id,
            basis=BASIS_NO_JOBS,
            detail=(
                "This decision has not produced a durable research job, so there is "
                "nothing to attribute spend to. Unknown, not zero."
            ),
        )

    rows = (
        (
            await session.execute(
                select(
                    ResearchRunConsumption.estimated_cost_usd,
                    ResearchRunConsumption.model_calls,
                    ResearchRunConsumption.model_tokens,
                ).where(ResearchRunConsumption.research_job_id.in_(job_ids))
            )
        )
        .all()
    )

    tool_rows = (
        await session.execute(
            select(ResearchToolCall.consumption_json).where(
                ResearchToolCall.research_job_id.in_(job_ids)
            )
        )
    ).all()
    tool_calls = len(tool_rows)
    tool_call_model_calls, tool_call_model_tokens = _tool_call_units(tool_rows)
    research_runs = int(
        (
            await session.execute(
                select(_count()).where(ResearchRun.research_job_id.in_(job_ids))
            )
        ).scalar_one()
        or 0
    )

    priced = [r for r in rows if r[0] is not None]
    unpriced = [r for r in rows if r[0] is None]
    subtotal = round(sum(float(r[0]) for r in priced), 6) if priced else None
    model_calls = sum(int(r[1] or 0) for r in rows)
    model_tokens = sum(int(r[2] or 0) for r in rows)

    # One measured base, three verdicts on it. Built as an object rather than a kwargs
    # dict so the field names stay checkable: a typo in a `**dict` is a silent default,
    # and a silent default here is a fabricated zero.
    measured = DecisionSpend(
        decision_id=decision_id,
        job_ids=job_ids,
        consumption_rows=len(rows),
        priced_rows=len(priced),
        unpriced_rows=len(unpriced),
        priced_subtotal_usd=subtotal,
        model_calls=model_calls,
        model_tokens=model_tokens,
        tool_calls=tool_calls,
        research_runs=research_runs,
        tool_call_model_calls=tool_call_model_calls,
        tool_call_model_tokens=tool_call_model_tokens,
    )

    if not rows:
        return replace(
            measured,
            cost_usd_total=None,
            basis=BASIS_NO_CONSUMPTION,
            detail=(
                f"{len(job_ids)} durable job(s) are attributed to this decision, with "
                f"{tool_calls} tool call(s) and {research_runs} research run(s) linked "
                f"to them, and those calls measured {tool_call_model_tokens} model "
                "token(s). No run-consumption row names any of those jobs, because "
                "V3_RUN_CONSUMPTION_ENABLED is off or no run reached the point of "
                "recording — so the LINEAGE is known and the money record was never "
                "written. Spend is unknown, not zero."
            ),
        )

    if unpriced:
        return replace(
            measured,
            cost_usd_total=None,
            basis=BASIS_UNPRICED,
            detail=(
                f"{len(rows)} run-consumption row(s) are attributed to this decision and "
                f"{len(unpriced)} of them carry no cost, because no configured price "
                "covers their units. The spend is MEASURED and UNPRICED — a different "
                "state from unattributed. A sum over the priced rows alone would be a "
                "subtotal, and a cost cap comparing against a subtotal passes on spend "
                "it never saw, so the total stays unknown."
            ),
        )

    return replace(
        measured,
        cost_usd_total=subtotal,
        basis=BASIS_PRICED,
        detail=(
            f"${subtotal:.4f} over {len(rows)} run-consumption row(s) belonging to "
            f"{len(job_ids)} durable job(s) of this decision. Every attributable row is "
            "priced, so this is a total rather than a subtotal."
        ),
    )


def _tool_call_units(rows: Any) -> tuple[int, int]:
    """Model calls and tokens summed from tool-call consumption payloads.

    Tolerant by design: ``consumption_json`` is NULL on a tool that declared no
    instrumented unit, and an absent unit is **not zero** — it is unmeasured. Both read
    as "contributes nothing to this sum", which is the only honest handling, and neither
    is ever presented as money.
    """
    calls = tokens = 0
    for (payload,) in rows:
        if not isinstance(payload, dict):
            continue
        calls += int(payload.get("model_calls") or 0)
        tokens += int(payload.get("model_input_tokens") or 0)
        tokens += int(payload.get("model_output_tokens") or 0)
    return calls, tokens


def _count():  # noqa: ANN202
    from sqlalchemy import func

    return func.count()


__all__ = [
    "BASIS_NO_CONSUMPTION",
    "BASIS_NO_JOBS",
    "BASIS_PRICED",
    "BASIS_UNPRICED",
    "DecisionSpend",
    "decision_job_key_prefix",
    "jobs_for_decision",
    "spend_for_decision",
]
