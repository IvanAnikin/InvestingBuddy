"""
Regulator disclosure scaffold connectors — Phase 29B.

A ``ScaffoldConnector`` is a real connector class for a regulated-disclosure
source whose live fetch path is not implemented yet: SEDAR+ (Canada), ASX
announcements (Australia), UK FCA National Storage Mechanism, and the European
regulated-info venues (Euronext, Deutsche Börse, Nasdaq Nordic).

The scaffold's ENTIRE job is honesty:
  * Every fetch method returns an informational ``SourceGap`` — never a
    fabricated filing, JORC statement, RNS notice, or Appendix 5B.
  * Its status is ``scaffolded`` (distinct from ``planned``): the class exists
    and is wired into the registry, so the gap is specific and the source shows
    up in health, but no evidence is produced.

Binding a live fetch path for any of these is a Phase 29B.x follow-up; until
then the framework prefers a clear gap over a guess.
"""

from __future__ import annotations

from app.services.sources.connector_base import (
    CompanyContext,
    ConnectorHealth,
    ConnectorResult,
    QueryContext,
    SourceConnector,
    _now,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.taxonomy import ConnectorStatus


class ScaffoldConnector(SourceConnector):
    """A wired-but-not-live regulated-disclosure connector.

    Returns an honest, issuer-scoped ``SourceGap`` for every fetch method. Never
    fabricates evidence.
    """

    def __init__(
        self,
        *,
        connector_key: str,
        source_ids: tuple[str, ...],
        display_name: str,
        planned_phase: str | None = "Phase 29B.x",
        note: str | None = None,
    ) -> None:
        self.connector_key = connector_key
        self.supported_source_ids = source_ids
        self.display_name = display_name
        self.planned_phase = planned_phase
        self.note = note
        self.status = ConnectorStatus.scaffolded

    def _gap(self, *, for_issuer: bool = True) -> SourceGap:
        suffix = " for this issuer" if for_issuer else ""
        message = (
            f"{self.display_name} connector scaffold present; live fetch "
            f"pending{suffix}."
        )
        if self.note:
            message = f"{message} {self.note}"
        return SourceGap(
            connector_key=self.connector_key,
            source_id=self.supported_source_ids[0]
            if self.supported_source_ids
            else None,
            gap_type=GapType.connector_scaffolded,
            severity=GapSeverity.info,
            message=message,
            suggested_followup_phase=self.planned_phase,
            blocks_research_complete=False,
        )

    def _result(self) -> ConnectorResult:
        return ConnectorResult(
            connector_key=self.connector_key,
            warnings=[f"{self.display_name} is scaffolded; no live fetch yet."],
            source_gaps=[self._gap()],
        )

    async def fetch_filings(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._result()

    async def fetch_events(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._result()

    async def search_company(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._result()

    def healthcheck(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=ConnectorStatus.scaffolded,
            enabled=False,
            last_checked_at=_now(),
            detail=(
                f"Scaffolded ({self.planned_phase}); returns honest gaps, no "
                "evidence."
            ),
        )


__all__ = ["ScaffoldConnector"]
