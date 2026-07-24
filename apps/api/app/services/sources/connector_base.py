"""
Connector interface — Phase 29A.

A ``SourceConnector`` is the uniform adapter every present and future source is
reached through. Phase 29A defines the interface and its safe defaults; it does
*not* wire any connector into the live report / discovery pipeline (that is
Phase 29B onward). The councils still read the existing deterministic evidence
packs.

Contract guarantees:
  * Not every connector implements every method. The base returns a safe empty
    result (or an informational source gap for planned/disabled connectors) —
    it never raises ``NotImplementedError`` at the caller.
  * ``call_safe()`` wraps any method so a connector failure degrades to a
    ``ConnectorResult`` carrying a warning + a source gap. A connector can never
    crash a report or a discovery run.
  * ``healthcheck()`` returns only safe fields — never a secret, never a raw
    upstream error body.
"""

from __future__ import annotations

from abc import ABC
from collections.abc import Awaitable, Callable
from datetime import datetime, timezone

from pydantic import BaseModel, Field

from app.services.sources.errors import ConnectorError, ConnectorErrorCode
from app.services.sources.evidence import EvidenceItem
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.rate_limit import RateLimitPolicy
from app.services.sources.redaction import redact_text
from app.services.sources.taxonomy import ConnectorStatus


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Call contexts (inputs)
# ---------------------------------------------------------------------------


class CompanyContext(BaseModel):
    """Minimal identity a connector needs to look a company up. No secrets."""

    ticker: str | None = None
    exchange: str | None = None
    company_name: str | None = None
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    cik: str | None = None
    lei: str | None = None


class QueryContext(BaseModel):
    """What/when to fetch. Recommendation-free by construction."""

    query: str | None = None
    lookback_days: int | None = None
    region: str | None = None
    country: str | None = None
    language: str | None = None
    max_items: int = 20


# ---------------------------------------------------------------------------
# Results (outputs)
# ---------------------------------------------------------------------------


class ConnectorHealth(BaseModel):
    """Safe connector health. Exposed by ``GET /api/v1/sources/health``."""

    connector_key: str
    status: ConnectorStatus
    enabled: bool
    last_checked_at: datetime
    detail: str | None = None
    latency_ms: int | None = None


class ConnectorResult(BaseModel):
    """Everything one connector call produced — evidence, warnings, gaps, meta."""

    connector_key: str
    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    error_code: str | None = None
    latency_ms: int | None = None
    rate_limit: dict[str, int | float | str | None] | None = None
    source_gaps: list[SourceGap] = Field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error_code is None


# ---------------------------------------------------------------------------
# Connector base
# ---------------------------------------------------------------------------


class SourceConnector(ABC):
    """Base adapter for one (or a few related) source(s)."""

    connector_key: str = ""
    supported_source_ids: tuple[str, ...] = ()
    status: ConnectorStatus = ConnectorStatus.enabled
    rate_limit_policy: RateLimitPolicy | None = None

    @property
    def is_live(self) -> bool:
        """True when the connector is implemented and turned on."""
        return self.status in {ConnectorStatus.enabled, ConnectorStatus.configured}

    @property
    def is_planned(self) -> bool:
        return self.status in {
            ConnectorStatus.planned,
            ConnectorStatus.not_implemented,
        }

    # -- Fetch surface (override the ones a connector supports) -------------

    async def search_company(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._default_result("search_company")

    async def fetch_filings(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._default_result("fetch_filings")

    async def fetch_events(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._default_result("fetch_events")

    async def fetch_macro_context(self, query: QueryContext) -> ConnectorResult:
        return self._default_result("fetch_macro_context")

    # -- Health -------------------------------------------------------------

    def healthcheck(self) -> ConnectorHealth:
        """Deterministic, network-free health. Override to reflect config."""
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=None,
        )

    # -- Safe defaults / wrappers ------------------------------------------

    def _default_result(self, method: str) -> ConnectorResult:
        """Safe default for an unimplemented method.

        Planned/disabled connectors return a source gap so the critic sees the
        missing coverage; a live connector that simply does not offer this
        method returns an empty result with no gap.
        """
        if self.is_planned:
            gap = SourceGap(
                connector_key=self.connector_key,
                source_id=self.supported_source_ids[0]
                if self.supported_source_ids
                else None,
                gap_type=GapType.connector_planned,
                severity=GapSeverity.info,
                message=f"{self.connector_key} connector is planned; {method} not available.",
                blocks_research_complete=False,
            )
            return ConnectorResult(
                connector_key=self.connector_key,
                error_code=ConnectorErrorCode.not_implemented.value,
                warnings=[f"{self.connector_key}.{method} is not implemented yet."],
                source_gaps=[gap],
            )
        if self.status == ConnectorStatus.disabled:
            gap = SourceGap(
                connector_key=self.connector_key,
                source_id=self.supported_source_ids[0]
                if self.supported_source_ids
                else None,
                gap_type=GapType.connector_disabled,
                severity=GapSeverity.low,
                message=f"{self.connector_key} connector is disabled.",
                blocks_research_complete=False,
            )
            return ConnectorResult(
                connector_key=self.connector_key,
                error_code=ConnectorErrorCode.disabled.value,
                warnings=[f"{self.connector_key} is disabled."],
                source_gaps=[gap],
            )
        # Implemented + live, but this method not offered — empty, no gap.
        return ConnectorResult(connector_key=self.connector_key)

    async def call_safe(
        self,
        method: Callable[..., Awaitable[ConnectorResult]],
        *args: object,
    ) -> ConnectorResult:
        """Run a connector method, converting any failure into a safe result.

        A ``ConnectorError`` becomes a warning + gap keyed by its code; any other
        exception becomes a generic upstream gap. Never raises.
        """
        try:
            return await method(*args)
        except ConnectorError as exc:
            return self._error_result(exc.code, exc.safe_message)
        except Exception as exc:  # noqa: BLE001 - a connector must never crash a run
            return self._error_result(
                ConnectorErrorCode.upstream_error,
                redact_text(f"{type(exc).__name__}"),
            )

    def _error_result(self, code: ConnectorErrorCode, message: str) -> ConnectorResult:
        gap = SourceGap(
            connector_key=self.connector_key,
            source_id=self.supported_source_ids[0]
            if self.supported_source_ids
            else None,
            gap_type=GapType.connector_error,
            severity=GapSeverity.medium,
            message=f"{self.connector_key} failed: {message}",
            blocks_research_complete=False,
        )
        return ConnectorResult(
            connector_key=self.connector_key,
            error_code=code.value,
            warnings=[gap.message],
            source_gaps=[gap],
        )


__all__ = [
    "CompanyContext",
    "QueryContext",
    "ConnectorHealth",
    "ConnectorResult",
    "SourceConnector",
]
