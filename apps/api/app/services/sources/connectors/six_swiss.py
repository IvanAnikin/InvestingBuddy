"""
SIX Swiss Exchange regulated-disclosure reference connector — Phase 29B.4C.

Mirrors the Phase 29B.4A (UK FCA NSM) / 29B.4B (Euronext) connectors, but for a
NEW source (there was no Swiss scaffold — this task registers ``six_swiss`` as a
new enabled regulator source). Its report-time job is honest and bounded:

  * For a company that resolves to a **verified Swiss issuer** (its venue is a SIX
    Swiss venue *and* ``get_verified_issuer_source`` resolves it to a Swiss
    entity), it emits ONE bounded **T2 regulator-transport SOURCE REFERENCE** — a
    pointer to the issuer's regulated-disclosure venue (SIX Swiss Exchange
    regulatory disclosures, supervised by SIX Exchange Regulation), carrying the
    issuer's identity and a fixed public venue URL. It is deliberately **not a
    filing**: no specific disclosure, headline, date, or notice number is
    invented. The same call also emits an explicit honest ``SourceGap`` recording
    that the actual T1 filing *content* is not fetched at report time (live
    content retrieval is a Phase 29B.4 follow-up, Task 2).

  * **Translation:** Switzerland is multilingual and its major issuers (e.g.
    Richemont, Swatch) publish English annual reports, so this connector does NOT
    assert ``requires_translation`` by default and emits NO translation gap. It
    only notes neutrally that the original regulated filings may be published in a
    Swiss national language (German / French / Italian). It never claims a
    translation is required without a concrete per-issuer language signal.

  * For anything that does not resolve to a verified Swiss issuer, it returns an
    honest ``source_not_eligible`` gap and **no** reference — never a US SEC
    lookup, never a fabricated Swiss notice.

Guarantees (mirrors the company-IR static/metadata report path):
  * **No network call at report time.** Identity + the disclosure-venue
    reference come from the code-defined verified-issuer registry and fixed
    public constants; nothing is fetched here.
  * **No fabrication.** Only the venue is cited; no filing/notice is
    manufactured.
  * URLs are stripped of any credential-bearing query parameter by
    ``EvidenceItem`` before storage; the SIX reference carries none.
"""

from __future__ import annotations

from app.services.exchange_registry import normalize_exchange
from app.services.sources.connector_base import (
    CompanyContext,
    ConnectorHealth,
    ConnectorResult,
    QueryContext,
    SourceConnector,
    _now,
)
from app.services.sources.evidence import EvidenceItem, build_evidence_item
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.taxonomy import T2_REGULATOR_OR_GOV, ConnectorStatus
from app.services.sources.verified_issuer_sources import (
    VerifiedIssuerSource,
    get_verified_issuer_source,
)

# Public, fixed reference to the SIX Swiss Exchange regulatory-disclosure venue.
# This is the venue's own official-notices landing page, NOT a per-disclosure
# URL — no notice is fabricated.
SIX_SWISS_DISCLOSURE_NAME = "SIX Swiss Exchange regulatory disclosures"
SIX_SWISS_DISCLOSURE_URL = (
    "https://www.six-group.com/en/products-services/the-swiss-stock-exchange/"
    "market-data/news-tools/official-notices.html"
)
_REGULATOR = "SIX Exchange Regulation Ltd (SER)"
_TRANSPORT_LABEL = "SIX Swiss Exchange regulatory-disclosure venue (venue-operated)"

# SIX venues this connector is eligible for (SIX Swiss Exchange / blue-chip).
_SWISS_VENUES = frozenset({"SW", "VX"})
_SWISS_COUNTRIES = frozenset({"Switzerland"})

# Follow-up phase that will bind the flag-gated live content fetch (Task 2).
_CONTENT_FOLLOWUP_PHASE = "Phase 29B.4"


class SixSwissConnector(SourceConnector):
    """Dedicated SIX Swiss Exchange regulated-disclosure reference connector.

    Emits a bounded T2 regulator-transport *source reference* (not filing
    content) for a verified Swiss issuer, plus an honest content gap. It is a
    live evidence path for that *reference* only; the honest limitation that the
    primary filing *content* is not fetched is carried on every result as a gap.
    Unlike the German / Nordic connectors it does NOT assert a translation
    requirement (Swiss majors publish English reports).
    """

    connector_key = "six_swiss"
    supported_source_ids = ("six_swiss",)
    status = ConnectorStatus.enabled

    def __init__(self, *, verified_source: VerifiedIssuerSource | None = None) -> None:
        # An explicitly injected verified source (tests / preview) takes
        # precedence; otherwise the connector resolves identity itself.
        self._verified = verified_source

    # -- Eligibility -------------------------------------------------------

    def _swiss_issuer(self, company: CompanyContext) -> VerifiedIssuerSource | None:
        """Resolve a company to a verified Swiss issuer, or None.

        Requires BOTH a SIX Swiss venue and a verified-registry match whose
        country is Switzerland. Refuses to guess — an unresolvable issuer yields
        None (the caller emits an honest ``source_not_eligible`` gap), never a
        fabricated notice.
        """
        verified = self._verified or get_verified_issuer_source(
            company.ticker, company.exchange
        )
        if verified is None:
            return None
        country_ok = (verified.country or "").strip() in _SWISS_COUNTRIES
        venue_ok = (
            normalize_exchange(company.exchange or verified.exchange) in _SWISS_VENUES
        )
        return verified if (country_ok and venue_ok) else None

    # -- Result builders ---------------------------------------------------

    def _reference_item(self, verified: VerifiedIssuerSource) -> EvidenceItem:
        """One bounded T2 source reference to the issuer's SIX disclosure venue."""
        ident = verified.company_name
        excerpt = (
            f"Regulated disclosures for {ident} ({verified.ticker}.{verified.exchange}) "
            "— periodic financial reports and ad hoc announcements — are published "
            f"via {SIX_SWISS_DISCLOSURE_NAME} ({_REGULATOR}). This item is a source "
            "reference to that regulated-disclosure venue only: no individual "
            "filing, announcement, headline, date, or notice number is fetched or "
            "fabricated. Original regulated filings may be published in a Swiss "
            "national language (German / French / Italian)."
        )
        warnings = [
            "Source reference to the Swiss regulated-disclosure venue "
            f"({SIX_SWISS_DISCLOSURE_NAME} / {_REGULATOR}); the primary filing "
            "CONTENT is not fetched at report time. Human review required.",
            "Original regulated filings may be in a Swiss national language "
            "(German / French / Italian); no translation is asserted as required "
            "for this issuer.",
        ]
        provenance = [
            f"{SIX_SWISS_DISCLOSURE_NAME} + {_REGULATOR} (regulated-disclosure venue)",
            "Source reference only — no filing content retrieved",
            "needs_human_review=true",
        ]
        content_source = (
            f"{SIX_SWISS_DISCLOSURE_NAME} + {_REGULATOR} — regulated-disclosure venue"
        )
        return build_evidence_item(
            id="SIXSWISSREF",
            source_id="six_swiss",
            source_name=ident,
            provider_transport=_TRANSPORT_LABEL,
            provider_transport_tier=T2_REGULATOR_OR_GOV,
            content_source=content_source,
            content_source_tier=T2_REGULATOR_OR_GOV,
            source_type="six_swiss_reference",
            title=(
                f"{ident} — {verified.country} regulated disclosures via "
                f"{SIX_SWISS_DISCLOSURE_NAME} ({_REGULATOR})"
            ),
            url=SIX_SWISS_DISCLOSURE_URL,
            excerpt=excerpt,
            data_quality="metadata_only",
            confidence=verified.source_confidence,
            provenance=provenance,
            warnings=warnings,
        )

    def _content_gap(self, verified: VerifiedIssuerSource) -> SourceGap:
        """Honest gap: the T1 filing content behind the venue is not fetched."""
        return SourceGap(
            connector_key=self.connector_key,
            source_id="six_swiss",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                f"Swiss primary filing content for {verified.company_name} is "
                f"published via {SIX_SWISS_DISCLOSURE_NAME} ({_REGULATOR}) but is "
                "not fetched at report time; only a source reference to the "
                "regulated-disclosure venue is provided."
            ),
            suggested_followup_phase=_CONTENT_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _not_eligible_gap(self) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id="six_swiss",
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                f"The {SIX_SWISS_DISCLOSURE_NAME} venue covers SIX Swiss Exchange "
                "issuers only; this issuer does not resolve to a verified Swiss "
                "issuer, so no Swiss regulated-disclosure reference is provided."
            ),
            blocks_research_complete=False,
        )

    def _reference_result(self, company: CompanyContext) -> ConnectorResult:
        verified = self._swiss_issuer(company)
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
        return self._reference_result(company)

    # -- Health ------------------------------------------------------------

    def healthcheck(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=(
                "Emits a T2 regulator-transport source reference to the SIX Swiss "
                "Exchange regulatory-disclosure venue (SIX Exchange Regulation) for "
                "verified Swiss issuers; no translation is asserted (Swiss majors "
                "publish English reports). Primary filing CONTENT is not fetched at "
                f"report time ({_CONTENT_FOLLOWUP_PHASE} follow-up)."
            ),
        )


__all__ = [
    "SixSwissConnector",
    "SIX_SWISS_DISCLOSURE_NAME",
    "SIX_SWISS_DISCLOSURE_URL",
]
