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
