"""Request/response shapes for team watchlists and monitoring — V3.21.2 (spec §3)."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

_TICKER = r"^[A-Za-z0-9.\-]{1,20}$"

EntrySource = Literal["manual", "discovery_candidate", "report", "auto_report"]


class WatchlistSummary(BaseModel):
    id: uuid.UUID
    name: str
    description: str | None
    is_active: bool
    is_team_list: bool
    created_at: datetime | None
    entry_count: int
    open_signal_count: int


class CompanyRef(BaseModel):
    id: uuid.UUID
    ticker: str | None
    exchange: str | None
    name: str | None


class LatestRecord(BaseModel):
    id: uuid.UUID
    research_run_id: uuid.UUID | None
    as_of: date
    #: The ADR-098 triage label (``PASS`` | ``WATCHLIST`` | ``SHORTLIST``) when the record
    #: carries a verdict; NULL otherwise. A research triage, never advice.
    verdict: str | None
    verdict_meaning: str | None
    composite_score: float | None


class SignalRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    kind: str
    summary: str
    status: str
    relates_to_question_key: str | None
    relates_to_run_id: uuid.UUID | None
    source_ref: str | None
    detail: dict[str, Any]
    observed_at: datetime
    acknowledged_at: datetime | None
    #: Shown with a "Re-run research" link. Research is proposed, never started.
    material: bool


class EntryRead(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID | None
    company: CompanyRef | None
    note: str | None
    source: str | None
    added_at: datetime
    added_by_hash: str | None
    latest_record: LatestRecord | None
    open_signals: list[SignalRead]


class WatchlistDetail(WatchlistSummary):
    entries: list[EntryRead]


class EntryCreate(BaseModel):
    """``company_id``, or ``ticker`` (+ optional ``exchange``) of a known company."""

    company_id: uuid.UUID | None = None
    ticker: str | None = Field(default=None, pattern=_TICKER)
    exchange: str | None = Field(default=None, pattern=_TICKER)
    note: str | None = Field(default=None, max_length=500)
    source: EntrySource = "manual"

    @model_validator(mode="after")
    def _one_identity(self) -> EntryCreate:
        if self.company_id is None and not self.ticker:
            raise ValueError("give company_id, or ticker (and exchange)")
        self.ticker = self.ticker.upper() if self.ticker else None
        self.exchange = self.exchange.upper() if self.exchange else None
        return self


class EntryCreated(BaseModel):
    entry_id: uuid.UUID
    watchlist_id: uuid.UUID
    company_id: uuid.UUID
    created: bool


class MonitoringRunRequest(BaseModel):
    watchlist_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _exactly_one(self) -> MonitoringRunRequest:
        if (self.watchlist_id is None) == (self.company_id is None):
            raise ValueError("give exactly one of watchlist_id or company_id")
        return self


class MonitoringRunResult(BaseModel):
    #: ``enqueued`` (a durable ``monitoring_pass`` job) or ``inline`` (run in the request).
    mode: Literal["enqueued", "inline"]
    job_id: str | None = None
    created: bool | None = None
    result: dict[str, Any] | None = None
