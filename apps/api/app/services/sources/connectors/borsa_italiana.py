"""
Italian regulated-disclosure connector — private-use production readiness PR-E.

Italy was the one target venue with NO regulated-disclosure connector at all:
``regulator_connector_for`` had no ``MI`` / ``Italy`` mapping, so an Italian
issuer fell through to the generic region scaffold and its report described its
filings in US vocabulary (PR-C flagged this explicitly as deferred to here).

The venue is **eMarket Storage**, the storage mechanism authorised by CONSOB
(operated by Teleborsa) under the Italian implementation of the Transparency
Directive. It is the official place an Italian issuer's regulated information is
filed and stored, it is publicly readable, and — verified live on 2026-08-25 —
it exposes a per-issuer listing where every row carries a publication timestamp,
a headline and a direct link to the official PDF.

Unlike the other venue connectors this one is LIVE-capable from the start (gated
behind ``source_live_disclosures_enabled``), because that surface exists. With
the flag off it behaves exactly like its siblings: one bounded T2 venue
reference plus an honest content gap.

Guarantees, identical to the sibling connectors:
  * No fabrication. Only what the venue published is reported.
  * Bounded: exact host allowlist, SSRF/DNS/TLS/redirect-guarded fetch, a
    lookback window, an item cap, a byte cap, and no pagination beyond the
    first response.
  * Honest degradation. A venue that fails yields the reference plus a
    machine-readable limitation, never an assumed announcement.
  * The issuer's own site is NOT required — which matters here, because
    ``monclergroup.com`` was serving its own maintenance page at last check.
"""

from __future__ import annotations

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.exchange_registry import normalize_exchange
from app.services.sources.connector_base import (
    CompanyContext,
    ConnectorHealth,
    ConnectorResult,
    QueryContext,
    SourceConnector,
    _now,
)
from app.services.sources.disclosure_events import DisclosureEvent, DisclosureFeed
from app.services.sources.evidence import EvidenceItem, build_evidence_item
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.taxonomy import T2_REGULATOR_OR_GOV, ConnectorStatus
from app.services.sources.venue_disclosures import (
    EMARKET_ISSUER_IDS,
    EMARKET_STORAGE_DOCUMENT_DOMAINS,
    VENUE_EMARKET_STORAGE,
    disclosure_events_to_evidence,
    fetch_emarket_storage_disclosures,
)
from app.services.sources.verified_issuer_sources import (
    VerifiedIssuerSource,
    get_verified_issuer_source,
)

BORSA_ITALIANA_DISCLOSURE_NAME = VENUE_EMARKET_STORAGE
BORSA_ITALIANA_DISCLOSURE_URL = "https://www.emarketstorage.it/it/comunicati-finanziari"
_REGULATOR = "CONSOB (Commissione Nazionale per le Società e la Borsa)"
_TRANSPORT_LABEL = (
    "eMarket Storage — Italian regulated-information storage mechanism "
    "(CONSOB-authorised)"
)

# Italian venues this connector is eligible for.
_ITALIAN_VENUES = frozenset({"MI", "MIL", "BIT"})
_ITALIAN_COUNTRIES = frozenset({"Italy"})


class BorsaItalianaConnector(SourceConnector):
    """Italian regulated-disclosure connector (CONSOB-authorised storage)."""

    connector_key = "borsa_italiana"
    supported_source_ids = ("borsa_italiana",)
    status = ConnectorStatus.enabled

    #: Where THIS venue's own documents live. A document is only ever fetched
    #: under this explicit allowlist, never under a host read off the URL.
    disclosure_document_domains: tuple[str, ...] = EMARKET_STORAGE_DOCUMENT_DOMAINS

    def __init__(
        self,
        *,
        verified_source: VerifiedIssuerSource | None = None,
        cfg: Settings | None = None,
        disclosure_fetcher=None,
    ) -> None:
        self._verified = verified_source
        self._cfg = cfg or default_settings
        self._disclosure_fetcher = (
            disclosure_fetcher or fetch_emarket_storage_disclosures
        )
        # Current-period acceptance — the events THIS connector retrieved, kept
        # so the evidence collector can decide whether one of them holds a
        # current-period document worth opening. Mirrors the existing
        # ``CompanyIrConnector.collected_primary_document_artifacts`` seam:
        # the connector retrieves and states, the collector decides.
        self.collected_disclosure_events: list[DisclosureEvent] = []

    # -- Eligibility -------------------------------------------------------

    def _italian_issuer(self, company: CompanyContext) -> VerifiedIssuerSource | None:
        """Resolve a company to a verified Italian issuer, or None.

        Requires BOTH an Italian venue and a verified-registry match whose
        country is Italy. Refuses to guess — an unresolvable issuer yields None
        and the caller emits an honest ``source_not_eligible`` gap.
        """
        verified = self._verified or get_verified_issuer_source(
            company.ticker, company.exchange
        )
        if verified is None:
            return None
        country_ok = (verified.country or "").strip() in _ITALIAN_COUNTRIES
        venue_ok = (
            normalize_exchange(company.exchange or verified.exchange) in _ITALIAN_VENUES
        )
        return verified if (country_ok and venue_ok) else None

    # -- Result builders ---------------------------------------------------

    def _reference_item(self, verified: VerifiedIssuerSource) -> EvidenceItem:
        ident = verified.company_name
        excerpt = (
            f"Regulated disclosures for {ident} ({verified.ticker}."
            f"{verified.exchange}) — periodic financial reports, price-sensitive "
            "announcements and shareholding notifications — are filed and stored "
            f"via {BORSA_ITALIANA_DISCLOSURE_NAME}, the storage mechanism "
            f"authorised by {_REGULATOR}. This item is a source reference to that "
            "venue: no individual filing, announcement, headline, date, or "
            "protocol number is fabricated."
        )
        return build_evidence_item(
            id="BORSAITREF",
            source_id="borsa_italiana",
            source_name=ident,
            provider_transport=_TRANSPORT_LABEL,
            provider_transport_tier=T2_REGULATOR_OR_GOV,
            content_source=(
                f"{BORSA_ITALIANA_DISCLOSURE_NAME} + {_REGULATOR} — "
                "regulated-disclosure storage mechanism"
            ),
            content_source_tier=T2_REGULATOR_OR_GOV,
            source_type="borsa_italiana_reference",
            title=(
                f"{ident} — Italian regulated disclosures via "
                f"{BORSA_ITALIANA_DISCLOSURE_NAME} ({_REGULATOR})"
            ),
            url=BORSA_ITALIANA_DISCLOSURE_URL,
            excerpt=excerpt,
            data_quality="metadata_only",
            confidence=verified.source_confidence,
            provenance=[
                f"{BORSA_ITALIANA_DISCLOSURE_NAME} + {_REGULATOR}",
                "Source reference to the regulated-disclosure venue",
                "needs_human_review=true",
            ],
            warnings=[
                "Source reference to the Italian regulated-disclosure storage "
                f"mechanism ({BORSA_ITALIANA_DISCLOSURE_NAME} / {_REGULATOR}). "
                "Human review required.",
                "The venue publishes an Italian and an English edition of the "
                "same announcement; both are retained with their own official "
                "URL and language.",
            ],
        )

    def _content_gap(self, verified: VerifiedIssuerSource) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id="borsa_italiana",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                f"Italian regulated-disclosure content for {verified.company_name} "
                f"is filed via {BORSA_ITALIANA_DISCLOSURE_NAME} ({_REGULATOR}) but "
                "live retrieval is disabled; only a source reference to the venue "
                "is provided."
            ),
            blocks_research_complete=False,
        )

    def _not_eligible_gap(self) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id="borsa_italiana",
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                f"{BORSA_ITALIANA_DISCLOSURE_NAME} covers Italian regulated "
                "issuers only; this issuer does not resolve to a verified "
                "Italian issuer, so no Italian regulated-disclosure reference "
                "is provided."
            ),
            blocks_research_complete=False,
        )

    def _live_unavailable_gap(
        self, verified: VerifiedIssuerSource, feed: DisclosureFeed
    ) -> SourceGap:
        detail = "; ".join(feed.limitations) or "no disclosures in the lookback window"
        return SourceGap(
            connector_key=self.connector_key,
            source_id="borsa_italiana",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                f"Live regulated disclosures for {verified.company_name} were "
                f"requested from {BORSA_ITALIANA_DISCLOSURE_NAME} but none were "
                f"retrieved ({detail}). The venue reference is provided instead; "
                "no announcement was assumed or fabricated."
            ),
            blocks_research_complete=False,
        )

    def _reference_result(self, company: CompanyContext) -> ConnectorResult:
        verified = self._italian_issuer(company)
        if verified is None:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[self._not_eligible_gap()],
            )
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item(verified)],
            source_gaps=[self._content_gap(verified)],
        )

    # -- Fetch surface -----------------------------------------------------

    async def fetch_filings(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._reference_result(company)

    async def fetch_events(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        """Live regulated disclosures when enabled, else the venue reference."""
        if not getattr(self._cfg, "source_live_disclosures_enabled", False):
            return self._reference_result(company)
        verified = self._italian_issuer(company)
        if verified is None:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[self._not_eligible_gap()],
            )

        feed = await self._disclosure_fetcher(
            issuer_ticker=verified.ticker,
            issuer_name=verified.company_name,
            cfg=self._cfg,
            max_events=int(getattr(self._cfg, "live_disclosure_max_events", 15)),
            lookback_days=int(
                getattr(self._cfg, "live_disclosure_lookback_days", 400)
            ),
        )
        if not feed.events:
            result = self._reference_result(company)
            result.source_gaps.append(self._live_unavailable_gap(verified, feed))
            return result

        self.collected_disclosure_events = list(feed.events)
        items = disclosure_events_to_evidence(
            feed.events,
            source_id="borsa_italiana",
            transport_label=_TRANSPORT_LABEL,
            id_prefix="BORSAITEVT",
            max_items=int(getattr(self._cfg, "live_disclosure_max_events", 15)),
        )
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item(verified), *items],
            source_gaps=[],
        )

    # -- Health ------------------------------------------------------------

    def healthcheck(self) -> ConnectorHealth:
        live = bool(getattr(self._cfg, "source_live_disclosures_enabled", False))
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=(
                "Italian regulated disclosures via the CONSOB-authorised "
                f"eMarket Storage mechanism. Live retrieval is "
                f"{'ENABLED' if live else 'disabled'}; issuers with a curated "
                f"venue id: {', '.join(sorted(EMARKET_ISSUER_IDS)) or 'none'}."
            ),
        )


__all__ = [
    "BORSA_ITALIANA_DISCLOSURE_NAME",
    "BORSA_ITALIANA_DISCLOSURE_URL",
    "BorsaItalianaConnector",
]
