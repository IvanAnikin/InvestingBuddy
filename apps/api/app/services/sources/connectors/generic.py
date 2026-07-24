"""
Generic connectors — Phase 29A.

``PlannedConnector`` stands in for every source that has a registry entry but no
implementation yet (SEDAR+, ASX, FRED, …). Every fetch method returns a safe
source gap; it never pretends to have data.

``WrappedProviderConnector`` represents an existing, migrated provider (SEC IR,
GLEIF, EODHD, Stooq, GDELT). In 29A it reports accurate, network-free health
(does the provider have what it needs to run?) and declares its capabilities.
Live fetch delegation lands per-source in Phase 29B onward.
"""

from __future__ import annotations

from app.services.sources.connector_base import (
    ConnectorHealth,
    SourceConnector,
    _now,
)
from app.services.sources.rate_limit import RateLimitPolicy
from app.services.sources.taxonomy import ConnectorStatus


class PlannedConnector(SourceConnector):
    """Placeholder connector for a planned, not-yet-implemented source."""

    def __init__(
        self,
        *,
        connector_key: str,
        source_ids: tuple[str, ...],
        planned_phase: str | None = None,
    ) -> None:
        self.connector_key = connector_key
        self.supported_source_ids = source_ids
        self.status = ConnectorStatus.planned
        self.planned_phase = planned_phase

    def healthcheck(self) -> ConnectorHealth:
        detail = (
            f"Planned for {self.planned_phase}." if self.planned_phase else "Planned."
        )
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=ConnectorStatus.planned,
            enabled=False,
            last_checked_at=_now(),
            detail=detail,
        )


class WrappedProviderConnector(SourceConnector):
    """A live, migrated provider exposed through the connector interface.

    Health is computed deterministically from ``configured`` (does the provider
    have the credentials/config it needs?) — never by touching the network and
    never by revealing what the credential is.
    """

    def __init__(
        self,
        *,
        connector_key: str,
        source_ids: tuple[str, ...],
        configured: bool = True,
        needs_credentials: bool = False,
        rate_limit_policy: RateLimitPolicy | None = None,
        detail: str | None = None,
    ) -> None:
        self.connector_key = connector_key
        self.supported_source_ids = source_ids
        self.needs_credentials = needs_credentials
        self.rate_limit_policy = rate_limit_policy
        self._detail = detail
        if needs_credentials and not configured:
            self.status = ConnectorStatus.not_configured
        elif configured:
            self.status = ConnectorStatus.configured
        else:
            self.status = ConnectorStatus.enabled

    def healthcheck(self) -> ConnectorHealth:
        detail = self._detail
        if self.status == ConnectorStatus.not_configured and not detail:
            detail = "Credentials not configured; connector idle until set."
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=detail,
        )


__all__ = ["PlannedConnector", "WrappedProviderConnector"]
