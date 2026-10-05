"""Read shapes for the research-escalation queue. V3.17.5.

Everything here is a projection of what the backend already measured and stored. The UI
must not recompute an evidence delta — two implementations of the same arithmetic drift,
and the one on screen is the one a human acts on.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class EvidenceDeltaRead(BaseModel):
    """What one round changed, as the backend computed it."""

    #: V3.17.8. **Read this before the numbers.** ``False`` means no pre-round snapshot
    #: existed, so no delta was computed and every count below is a schema default rather
    #: than a measurement. The backend writes no numeric dimensions at all in that case,
    #: precisely so that a zero on screen is always a measured zero.
    measurable: bool = True

    indexed_chunks_added: int = 0
    searchable_documents_added: int = 0
    #: Non-negative. Gaps that CLOSED during the round.
    closable_gaps_closed: int = 0
    #: Non-negative. Gaps the round newly DISCOVERED — reported, never counted against
    #: improvement. Two counts rather than one signed number, because "-14 gaps closed"
    #: is not a statement anything can render honestly, and production carried four of
    #: them.
    closable_gaps_opened: int = 0
    facts_added: int = 0
    #: Secondary telemetry. Shown, never used to decide anything.
    verified_findings_added: int = 0
    improved: bool = False
    reasons: list[str] = Field(default_factory=list)


class DecisionSpendRead(BaseModel):
    """What this decision's durable jobs consumed, and whether it can be stated in money.

    V3.17.9. Exists so that the three things behind a NULL cost are **separately
    visible**: whether the jobs are linked, whether their consumption was measured, and
    whether anything priced it. A single null column answers none of those, and the
    difference between "unattributed" (a defect) and "unpriced" (an honest gap) is the
    whole subject of this slice.
    """

    #: Every durable ``research_jobs.id`` attributed to this decision. The lineage
    #: itself, listed rather than counted, so an operator can check one against the
    #: ``research_job_id`` on a tool-call or run row.
    job_ids: list[uuid.UUID] = Field(default_factory=list)
    job_count: int = 0

    consumption_rows: int = 0
    priced_rows: int = 0
    unpriced_rows: int = 0

    #: The sum over PRICED rows only. Shown so measurement is visible; it is not the
    #: total and the UI must never present it as one.
    priced_subtotal_usd: float | None = None
    #: ``None`` means unknown. Mirrors ``ResearchDecisionRead.cost_usd_total``.
    cost_usd_total: float | None = None

    #: ``no_jobs`` | ``no_consumption_recorded`` | ``unpriced_consumption`` |
    #: ``attributed_from_priced_consumption`` — why the total is what it is.
    basis: str = "no_jobs"

    #: Consumption that IS known, whatever the price situation.
    model_calls: int = 0
    model_tokens: int = 0
    tool_calls: int = 0
    research_runs: int = 0
    #: Summed from ``research_tool_calls.consumption_json``, which is written whatever
    #: ``V3_RUN_CONSUMPTION_ENABLED`` says. With the recorder off this is the ONLY place
    #: consumption is visible, and it is what distinguishes "we did not look" from "we
    #: looked and nothing priced it". It overlaps the run record and is never money.
    tool_call_model_calls: int = 0
    tool_call_model_tokens: int = 0

    detail: str = ""


class ResearchDecisionRead(BaseModel):
    """One decision, and everything it caused."""

    id: uuid.UUID
    company_id: uuid.UUID | None
    ticker: str | None = None
    exchange: str | None = None
    company_name: str | None = None

    discovery_run_id: uuid.UUID | None
    discovery_candidate_id: uuid.UUID | None

    source: str
    decision: str
    status: str
    reason: str
    priority: int

    escalation_round: int
    max_rounds: int

    last_job_id: uuid.UUID | None = None
    #: The queue's own view of the current job, when there is one.
    job_status: str | None = None
    job_attempt: int | None = None
    job_max_attempts: int | None = None

    evidence_before: dict[str, Any] | None = None
    evidence_after: dict[str, Any] | None = None
    improvement: EvidenceDeltaRead | None = None

    terminal_reason: str | None = None

    #: ``None`` means **unknown**, and the UI must render it as unknown. It is not 0.
    #: Production reports cost as NULL because no price book is configured, and a 0 on
    #: screen would tell an operator the work was free.
    cost_usd_total: float | None = None

    #: V3.17.9. Why ``cost_usd_total`` is what it is, and what IS known instead. On the
    #: single-decision read this is recomputed live; in a list it is the record written
    #: when the round closed.
    spend: DecisionSpendRead | None = None

    created_at: datetime
    updated_at: datetime


class ResearchDecisionList(BaseModel):
    decisions: list[ResearchDecisionRead]
    total: int
    disclaimer: str = (
        "INTERNAL ADMIN ONLY. A research decision is an internal prioritisation and "
        "execution record. It is not investment advice, not a recommendation, and "
        "carries no price target or valuation."
    )
