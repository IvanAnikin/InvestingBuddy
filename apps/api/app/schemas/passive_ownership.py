"""``GET /api/v1/companies/{id}/passive-ownership`` — V3.20 slice 3.3."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class PassiveOwnershipChange(BaseModel):
    period: str
    fund_count: int
    shares_held: float | None
    change_shares: float | None
    change_pct: float | None
    change_fund_count: int


class PassiveOwnershipSecurity(BaseModel):
    #: ``isin`` / ``cusip`` name the security; ``lei`` names the issuer (ADR-061).
    identifier_kind: Literal["isin", "cusip", "lei"]
    identifier: str
    security_id: str | None
    security_type: str
    #: Only ordinary / class shares count toward the %; see ``not_counted_reason``.
    counted_in_pct: bool
    not_counted_reason: str | None
    issuer_name: str | None
    isin: str | None
    cusip: str | None
    fund_count: int
    etf_count: int
    #: Funds whose ETF status the N-CEN data sets do not state — not "not an ETF".
    etf_unknown_count: int
    index_fund_count: int
    shares_held: float | None
    etf_shares_held: float | None
    value_usd: float | None
    non_share_holdings: int
    report_date_min: str | None
    report_date_max: str | None
    top_funds: list[dict[str, Any]]
    source_url: str | None
    period: str
    previous: PassiveOwnershipChange | None


class CoverShareCount(BaseModel):
    shares: float
    as_of: str
    accession: str | None
    form: str | None
    filed: str | None
    classes: int
    source_url: str
    concept: str


class CompanyPassiveOwnershipRead(BaseModel):
    company_id: str
    legal_entity_id: str | None
    state: Literal["ok", "disabled", "entity_unresolved", "no_identifiers",
                   "not_held_or_not_ingested"]
    #: The N-PORT data set's FILING quarter, e.g. ``2026Q3``.
    period: str | None
    previous_period: str | None
    securities: list[PassiveOwnershipSecurity] = Field(default_factory=list)
    shares_held: float | None
    etf_shares_held: float | None
    pct_of_shares_outstanding: float | None
    etf_pct_of_shares_outstanding: float | None
    #: Always shown beside the %: US-registered funds / ETFs only.
    pct_label: str
    pct_withheld_reason: str | None
    #: SEC cover-page share count; its ``as_of`` is the denominator date.
    denominator: CoverShareCount | None
    holdings_as_of: str | None
    concentration: dict[str, Any] | None
    source: str
    freshness_note: str
