"""
Euronext Regulated Information connector — Phase 29B.4B.

Mirrors the Phase 29B.4A UK FCA NSM connector, one venue over: it upgrades the
former generic ``euronext_regulated_info`` *scaffold* into a dedicated connector
for issuers on Euronext Paris (France) / Euronext Amsterdam (Netherlands). Its
report-time job is honest and bounded:

  * For a company that resolves to a **verified Euronext issuer** (its venue is
    Euronext Paris/Amsterdam *and* ``get_verified_issuer_source`` resolves it to
    a French/Dutch entity), it emits ONE bounded **T2 regulator-transport SOURCE
    REFERENCE** — a pointer to the issuer's regulated-disclosure venue (the
    Euronext Regulated Information service and the home regulator, the AMF for
    France / the AFM for the Netherlands), carrying the issuer's identity and a
    fixed public venue URL. It is deliberately **not a filing**: no specific
    disclosure, headline, date, or notice number is invented. The same call also
    emits an explicit honest ``SourceGap`` recording that the actual T1 filing
    *content* is not fetched at report time (live content retrieval is a Phase
    29B.4 follow-up, Task 2).

  * For a **French** issuer it additionally emits a ``translation_required`` gap
    and marks the reference item ``requires_translation`` — the regulated
    disclosures (Universal Registration Document, etc.) are French-language and
    are not translated in this phase. Dutch issuers (which disclose in English)
    do not carry that signal.

  * For anything that does not resolve to a verified Euronext issuer, it returns
    an honest ``source_not_eligible`` gap and **no** reference — never a US SEC
    lookup, never a fabricated Euronext notice.

Guarantees (mirrors the company-IR static/metadata report path):
  * **No network call at report time.** Identity + the disclosure-venue
    reference come from the code-defined verified-issuer registry and fixed
    public constants; nothing is fetched here.
  * **No fabrication.** Only the venue is cited; no filing/notice is
    manufactured.
  * URLs are stripped of any credential-bearing query parameter by
    ``EvidenceItem`` before storage; the Euronext reference carries none anyway.
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

# Public, fixed reference to the Euronext regulated-information venue. This is
# the service's own landing page, NOT a per-disclosure URL — no notice is
# fabricated.
EURONEXT_REGULATED_INFO_NAME = "Euronext Regulated Information"
EURONEXT_REGULATED_INFO_URL = "https://www.euronext.com/en/regulated-information"
_TRANSPORT_LABEL = "Euronext Regulated Information (venue-operated)"

# Euronext venues this connector is eligible for (Paris / Amsterdam) and the home
# regulator that oversees each issuer's regulated disclosures.
_EURONEXT_VENUES = frozenset({"PA", "AS"})
_COUNTRY_REGULATOR: dict[str, str] = {
    "France": "Autorité des marchés financiers (AMF)",
    "Netherlands": "Autoriteit Financiële Markten (AFM)",
}
# Eligible countries are exactly those with a mapped home regulator above.
_EURONEXT_COUNTRIES = frozenset(_COUNTRY_REGULATOR)
# French regulated disclosures are French-language and not translated this phase.
_TRANSLATION_COUNTRIES = frozenset({"France"})

# Follow-up phase that will bind the flag-gated live content fetch (Task 2).
_CONTENT_FOLLOWUP_PHASE = "Phase 29B.4"


class EuronextRegulatedConnector(SourceConnector):
    """Dedicated Euronext Paris/Amsterdam regulated-disclosure reference connector.

    Emits a bounded T2 regulator-transport *source reference* (not filing
    content) for a verified Euronext issuer, plus an honest content gap — and, for
    French issuers, an honest ``translation_required`` gap. It is a live evidence
    path for that *reference* only; the honest limitation that the primary filing
    *content* is not fetched is carried on every result as a gap.
    """

    connector_key = "euronext_regulated_info"
    supported_source_ids = ("euronext_regulated_info",)
    status = ConnectorStatus.enabled

    def __init__(self, *, verified_source: VerifiedIssuerSource | None = None) -> None:
        # An explicitly injected verified source (tests / preview) takes
        # precedence; otherwise the connector resolves identity itself.
        self._verified = verified_source

    # -- Eligibility -------------------------------------------------------

    def _euronext_issuer(self, company: CompanyContext) -> VerifiedIssuerSource | None:
        """Resolve a company to a verified Euronext Paris/Amsterdam issuer, or None.

        Requires BOTH a Euronext venue (Paris/Amsterdam) and a verified-registry
        match whose country is France or the Netherlands. Refuses to guess — an
        unresolvable issuer yields None (the caller emits an honest
        ``source_not_eligible`` gap), never a fabricated notice.
        """
        verified = self._verified or get_verified_issuer_source(
            company.ticker, company.exchange
        )
        if verified is None:
            return None
        country_ok = (verified.country or "").strip() in _EURONEXT_COUNTRIES
        venue_ok = (
            normalize_exchange(company.exchange or verified.exchange)
            in _EURONEXT_VENUES
        )
        return verified if (country_ok and venue_ok) else None

    @staticmethod
    def _regulator(verified: VerifiedIssuerSource) -> str:
        return _COUNTRY_REGULATOR.get((verified.country or "").strip(), "")

    @staticmethod
    def _requires_translation(verified: VerifiedIssuerSource) -> bool:
        return (verified.country or "").strip() in _TRANSLATION_COUNTRIES

    # -- Result builders ---------------------------------------------------

    def _reference_item(self, verified: VerifiedIssuerSource) -> EvidenceItem:
        """One bounded T2 source reference to the issuer's Euronext venue."""
        ident = verified.company_name
        regulator = self._regulator(verified)
        translate = self._requires_translation(verified)
        excerpt = (
            f"Regulated disclosures for {ident} ({verified.ticker}.{verified.exchange}) "
            "— periodic financial reports and regulated announcements — are "
            f"published via {EURONEXT_REGULATED_INFO_NAME}. This item is a source "
            "reference to that regulated-disclosure venue only: no individual "
            "filing, announcement, headline, date, or notice number is fetched or "
            "fabricated."
        )
        warnings = [
            "Source reference to the Euronext regulated-disclosure venue "
            f"({EURONEXT_REGULATED_INFO_NAME} / {regulator}); the primary filing "
            "CONTENT is not fetched at report time. Human review required.",
        ]
        provenance = [
            f"{EURONEXT_REGULATED_INFO_NAME} + {regulator} (regulated-disclosure venue)",
            "Source reference only — no filing content retrieved",
            "needs_human_review=true",
        ]
        if translate:
            warnings.append(
                "Regulated disclosures are French-language and are not translated "
                "in this phase; translation is a Phase 30 follow-up."
            )
        content_source = (
            f"{EURONEXT_REGULATED_INFO_NAME} + {regulator} "
            "— regulated-disclosure venue"
        )
        return build_evidence_item(
            id="EURONEXTREF",
            source_id="euronext_regulated_info",
            source_name=ident,
            provider_transport=_TRANSPORT_LABEL,
            provider_transport_tier=T2_REGULATOR_OR_GOV,
            content_source=content_source,
            content_source_tier=T2_REGULATOR_OR_GOV,
            source_type="euronext_regulated_info_reference",
            title=(
                f"{ident} — {verified.country} regulated disclosures via "
                f"{EURONEXT_REGULATED_INFO_NAME} ({regulator})"
            ),
            url=EURONEXT_REGULATED_INFO_URL,
            excerpt=excerpt,
            data_quality="metadata_only",
            confidence=verified.source_confidence,
            requires_translation=translate,
            original_language="French" if translate else None,
            provenance=provenance,
            warnings=warnings,
        )

    def _content_gap(self, verified: VerifiedIssuerSource) -> SourceGap:
        """Honest gap: the T1 filing content behind the venue is not fetched."""
        return SourceGap(
            connector_key=self.connector_key,
            source_id="euronext_regulated_info",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                f"{verified.country} primary filing content for "
                f"{verified.company_name} is published via the "
                f"{EURONEXT_REGULATED_INFO_NAME} service ({self._regulator(verified)}) "
                "but is not fetched at report time; only a source reference to the "
                "regulated-disclosure venue is provided."
            ),
            suggested_followup_phase=_CONTENT_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _translation_gap(self, verified: VerifiedIssuerSource) -> SourceGap:
        """Honest gap: French regulated disclosures are not translated this phase."""
        return SourceGap(
            connector_key=self.connector_key,
            source_id="euronext_regulated_info",
            gap_type=GapType.translation_required,
            severity=GapSeverity.info,
            message=(
                f"French regulated disclosures for {verified.company_name} "
                "(e.g. the Universal Registration Document) are French-language "
                "and are not translated in this phase."
            ),
            suggested_followup_phase="Phase 30",
            blocks_research_complete=False,
        )

    def _not_eligible_gap(self) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id="euronext_regulated_info",
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                f"The {EURONEXT_REGULATED_INFO_NAME} service covers issuers on "
                "Euronext Paris / Amsterdam only; this issuer does not resolve to a "
                "verified Euronext issuer, so no Euronext regulated-disclosure "
                "reference is provided."
            ),
            blocks_research_complete=False,
        )

    def _reference_result(self, company: CompanyContext) -> ConnectorResult:
        verified = self._euronext_issuer(company)
        if verified is None:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[self._not_eligible_gap()],
            )
        gaps = [self._content_gap(verified)]
        if self._requires_translation(verified):
            gaps.append(self._translation_gap(verified))
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item(verified)],
            source_gaps=gaps,
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
                "Emits a T2 regulator-transport source reference to the Euronext "
                "Regulated Information venue (AMF / AFM) for verified Euronext "
                "Paris/Amsterdam issuers; French docs require translation. Primary "
                "filing CONTENT is not fetched at report time "
                f"({_CONTENT_FOLLOWUP_PHASE} follow-up)."
            ),
        )


__all__ = [
    "EuronextRegulatedConnector",
    "EURONEXT_REGULATED_INFO_NAME",
    "EURONEXT_REGULATED_INFO_URL",
]
