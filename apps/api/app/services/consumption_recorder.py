"""Persisting what a research run consumed — V3.0 Slice 5.

Kept apart from ``consumption`` on purpose: that module is pure — units, prices,
budgets, no database and no clock — which is what makes the arithmetic
exhaustively testable. This is the thin layer that writes a row.

RECORDING NEVER FAILS A RUN
===========================
Every call is wrapped. A research run that completed and then lost its report
because a telemetry insert failed would be an unusually expensive way to learn
nothing: the run cost the same either way, and the measurement is the least
valuable thing produced by it.

FAILED RUNS ARE RECORDED TOO
============================
A run that failed after ingesting eleven documents and running six council
agents consumed a real budget. Recording only the successes would make every
average taken from this table an average of the runs that went well, which is
precisely the number nobody needs.
"""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.structured_logging import log_event
from app.models.research_run_consumption import ResearchRunConsumption
from app.services import consumption as units_mod
from app.services.consumption import ConsumptionUnits

logger = logging.getLogger(__name__)

RUN_TYPE_COMPANY_RESEARCH = "company_research"


def enabled(cfg: Any | None = None) -> bool:
    """Whether consumption rows are written.

    Off by default and paired with migration 020: turning it on where 020 has
    not been applied would fail every insert. It is also what unblocks OPEN
    DECISION #14, which asks for the budget numbers to be derived from measured
    live runs rather than guessed — so this is the switch that produces the
    evidence, not one that spends anything.
    """
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    return bool(getattr(cfg, "v3_run_consumption_enabled", False))


async def record_run(
    session: AsyncSession,
    *,
    run_type: str,
    units: ConsumptionUnits,
    company_id: uuid.UUID | None = None,
    agent_run_id: uuid.UUID | None = None,
    research_job_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
    outcome: str | None = None,
    budget_limit_hit: str | None = None,
    cfg: Any | None = None,
) -> uuid.UUID | None:
    """Write one consumption row. Returns its id, or None when nothing was written.

    Never raises. Returns None both when the flag is off and when the write
    failed — the caller has nothing useful to do differently in either case, and
    the failure is logged by type.
    """
    if not enabled(cfg):
        return None
    try:
        if cfg is None:
            from app.core.config import settings as cfg  # noqa: PLW0127
        budget = units_mod.budget_from_settings(cfg)
        prices = units_mod.price_book_from_settings(cfg)
        cost = units_mod.derive_cost(units, prices)
        row = ResearchRunConsumption(
            run_type=run_type,
            research_job_id=research_job_id,
            agent_run_id=agent_run_id,
            company_id=company_id,
            report_id=report_id,
            consumption_json=units_mod.run_record(
                units, budget=budget, prices=prices
            ),
            model_calls=units.model_calls,
            model_tokens=units.model_tokens,
            elapsed_seconds=units.elapsed_seconds,
            # NULL, not 0.0, when no price book is configured: "we do not know"
            # and "it was free" are different statements.
            estimated_cost_usd=cost.estimated_usd,
            budget_limit_hit=budget_limit_hit,
            outcome=outcome,
        )
        session.add(row)
        await session.commit()
        log_event(
            logger,
            "v3_run_consumption_recorded",
            run_type=run_type,
            company_id=company_id,
            model_calls=units.model_calls,
            model_tokens=units.model_tokens,
            elapsed_seconds=units.elapsed_seconds,
            tokens_estimated=units.tokens_estimated,
            # So a reader of the log knows which zeros mean nothing.
            unmeasured=len(set(units_mod.UNIT_NAMES) - units.instrumented),
        )
        return row.id
    except Exception as exc:  # noqa: BLE001 - telemetry must never fail a run
        logger.warning(
            "v3_run_consumption_record_failed error_type=%s", type(exc).__name__
        )
        try:
            await session.rollback()
        except Exception:  # noqa: BLE001
            pass
        return None
