"""
Nordic (Nasdaq Nordic) regulated-disclosure reference connector — Phase 29B.4C.

Mirrors the Phase 29B.4A (UK FCA NSM) / 29B.4B (Euronext) connectors: it upgrades
the former generic ``nordic_disclosures`` *scaffold* into a dedicated connector
for Nordic issuers. Its report-time job is honest and bounded:

  * For a company that resolves to a **verified Nordic issuer** (its venue is a
    Nasdaq Nordic venue *and* ``get_verified_issuer_source`` resolves it to a
    Nordic entity), it emits ONE bounded **T2 regulator-transport SOURCE
    REFERENCE** — a pointer to the issuer's regulated-disclosure venue (Nasdaq
    Nordic company disclosures + the home financial supervisory authority, e.g.
    Finanstilsynet / the Danish FSA), carrying the issuer's identity and a fixed
    public venue URL. It is deliberately **not a filing**: no specific
    disclosure, headline, date, or notice number is invented. The same call also
    emits an explicit honest ``SourceGap`` recording that the actual T1 filing
    *content* is not fetched at report time (live content retrieval is a Phase
    29B.4 follow-up, Task 2).

  * Nordic regulated disclosures are local-language, so the reference item is
    marked ``requires_translation`` and an honest ``translation_required`` gap is
    added — translation is a Phase 30 follow-up.

  * For anything that does not resolve to a verified Nordic issuer, it returns an
    honest ``source_not_eligible`` gap and **no** reference — never a US SEC
    lookup, never a fabricated Nordic notice.

The eligibility surface is written to generalise across the Nordic venues
(Copenhagen / Stockholm / Helsinki / Oslo); only the venues with a verified
issuer are wired into the venue -> regulator routing (Phase 29B.1 posture: wire
only what is verified).

Guarantees (mirrors the company-IR static/metadata report path):
  * **No network call at report time.** Identity + the disclosure-venue
    reference come from the code-defined verified-issuer registry and fixed
    public constants; nothing is fetched here.
  * **No fabrication.** Only the venue is cited; no filing/notice is
    manufactured.
  * URLs are stripped of any credential-bearing query parameter by
    ``EvidenceItem`` before storage; the Nordic reference carries none.
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
from app.services.sources.disclosure_events import DisclosureFeed
from app.services.sources.evidence import EvidenceItem, build_evidence_item
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.taxonomy import T2_REGULATOR_OR_GOV, ConnectorStatus
from app.services.sources.venue_disclosures import (
    disclosure_events_to_evidence,
    fetch_nasdaq_nordic_disclosures,
)
from app.services.sources.verified_issuer_sources import (
    VerifiedIssuerSource,
    get_verified_issuer_source,
)

# Public, fixed reference to the Nasdaq Nordic company-disclosure venue. This is
# the service's own landing page, NOT a per-disclosure URL — no notice is
# fabricated.
NORDIC_DISCLOSURE_NAME = "Nasdaq Nordic company disclosures"
NORDIC_DISCLOSURE_URL = "https://www.nasdaqomxnordic.com/news/companynews"
_TRANSPORT_LABEL = "Nasdaq Nordic company-disclosure venue (venue/regulator-operated)"

# Nordic venues this connector is eligible for and the home financial supervisory
# authority + primary-disclosure language per country.
_NORDIC_VENUES = frozenset({"CO", "ST", "HE", "OL"})
_COUNTRY_REGULATOR: dict[str, str] = {
    "Denmark": "Finanstilsynet (Danish FSA)",
    "Sweden": "Finansinspektionen (Swedish FSA)",
    "Finland": "Finanssivalvonta (Finnish FSA)",
    "Norway": "Finanstilsynet (Norwegian FSA)",
}
_COUNTRY_LANGUAGE: dict[str, str] = {
    "Denmark": "Danish",
    "Sweden": "Swedish",
    "Finland": "Finnish",
    "Norway": "Norwegian",
}
# Eligible countries are exactly those with a mapped home regulator above.
_NORDIC_COUNTRIES = frozenset(_COUNTRY_REGULATOR)

# Follow-up phase that will bind the flag-gated live content fetch (Task 2).
_CONTENT_FOLLOWUP_PHASE = "Phase 29B.4"


class NordicDisclosuresConnector(SourceConnector):
    """Dedicated Nasdaq Nordic regulated-disclosure reference connector.

    Emits a bounded T2 regulator-transport *source reference* (not filing
    content) for a verified Nordic issuer, plus an honest content gap and an
    honest ``translation_required`` gap. It is a live evidence path for that
    *reference* only; the honest limitation that the primary filing *content* is
    not fetched is carried on every result as a gap.
    """

    connector_key = "nordic_disclosures"
    supported_source_ids = ("nordic_disclosures",)
    status = ConnectorStatus.enabled

    def __init__(
        self,
        *,
        verified_source: VerifiedIssuerSource | None = None,
        cfg: "Settings | None" = None,
        disclosure_fetcher=None,
    ) -> None:
        # An explicitly injected verified source (tests / preview) takes
        # precedence; otherwise the connector resolves identity itself.
        self._verified = verified_source
        self._cfg = cfg or default_settings
        # Private-use readiness PR-E — injectable so tests exercise the live
        # path against a fixture without a network call. Defaults to the real,
        # SSRF-guarded venue retrieval.
        self._disclosure_fetcher = (
            disclosure_fetcher or fetch_nasdaq_nordic_disclosures
        )

    # -- Eligibility -------------------------------------------------------

    def _nordic_issuer(self, company: CompanyContext) -> VerifiedIssuerSource | None:
        """Resolve a company to a verified Nordic issuer, or None.

        Requires BOTH a Nasdaq Nordic venue and a verified-registry match whose
        country is a Nordic country. Refuses to guess — an unresolvable issuer
        yields None (the caller emits an honest ``source_not_eligible`` gap),
        never a fabricated notice.
        """
        verified = self._verified or get_verified_issuer_source(
            company.ticker, company.exchange
        )
        if verified is None:
            return None
        country_ok = (verified.country or "").strip() in _NORDIC_COUNTRIES
        venue_ok = (
            normalize_exchange(company.exchange or verified.exchange) in _NORDIC_VENUES
        )
        return verified if (country_ok and venue_ok) else None

    @staticmethod
    def _regulator(verified: VerifiedIssuerSource) -> str:
        return _COUNTRY_REGULATOR.get((verified.country or "").strip(), "")

    @staticmethod
    def _language(verified: VerifiedIssuerSource) -> str | None:
        return _COUNTRY_LANGUAGE.get((verified.country or "").strip())

    # -- Result builders ---------------------------------------------------

    def _reference_item(self, verified: VerifiedIssuerSource) -> EvidenceItem:
        """One bounded T2 source reference to the issuer's Nordic disclosure venue."""
        ident = verified.company_name
        regulator = self._regulator(verified)
        language = self._language(verified)
        excerpt = (
            f"Regulated disclosures for {ident} ({verified.ticker}.{verified.exchange}) "
            "— periodic financial reports and regulated announcements — are "
            f"published via {NORDIC_DISCLOSURE_NAME}. This item is a source "
            "reference to that regulated-disclosure venue only: no individual "
            "filing, announcement, headline, date, or notice number is fetched or "
            "fabricated."
        )
        warnings = [
            "Source reference to the Nordic regulated-disclosure venue "
            f"({NORDIC_DISCLOSURE_NAME} / {regulator}); the primary filing CONTENT "
            "is not fetched at report time. Human review required.",
            f"{verified.country} regulated disclosures may be {language}-language "
            "and are not translated in this phase; translation is a Phase 30 "
            "follow-up.",
        ]
        provenance = [
            f"{NORDIC_DISCLOSURE_NAME} + {regulator} (regulated-disclosure venue)",
            "Source reference only — no filing content retrieved",
            "needs_human_review=true",
        ]
        content_source = (
            f"{NORDIC_DISCLOSURE_NAME} + {regulator} — regulated-disclosure venue"
        )
        return build_evidence_item(
            id="NORDICREF",
            source_id="nordic_disclosures",
            source_name=ident,
            provider_transport=_TRANSPORT_LABEL,
            provider_transport_tier=T2_REGULATOR_OR_GOV,
            content_source=content_source,
            content_source_tier=T2_REGULATOR_OR_GOV,
            source_type="nordic_disclosures_reference",
            title=(
                f"{ident} — {verified.country} regulated disclosures via "
                f"{NORDIC_DISCLOSURE_NAME} ({regulator})"
            ),
            url=NORDIC_DISCLOSURE_URL,
            excerpt=excerpt,
            data_quality="metadata_only",
            confidence=verified.source_confidence,
            requires_translation=True,
            original_language=language,
            provenance=provenance,
            warnings=warnings,
        )

    def _content_gap(self, verified: VerifiedIssuerSource) -> SourceGap:
        """Honest gap: the T1 filing content behind the venue is not fetched."""
        return SourceGap(
            connector_key=self.connector_key,
            source_id="nordic_disclosures",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                f"{verified.country} primary filing content for "
                f"{verified.company_name} is published via {NORDIC_DISCLOSURE_NAME} "
                f"({self._regulator(verified)}) but is not fetched at report time; "
                "only a source reference to the regulated-disclosure venue is "
                "provided."
            ),
            suggested_followup_phase=_CONTENT_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _translation_gap(self, verified: VerifiedIssuerSource) -> SourceGap:
        """Honest gap: Nordic regulated disclosures are not translated this phase."""
        language = self._language(verified)
        return SourceGap(
            connector_key=self.connector_key,
            source_id="nordic_disclosures",
            gap_type=GapType.translation_required,
            severity=GapSeverity.info,
            message=(
                f"{verified.country} regulated disclosures for "
                f"{verified.company_name} may be {language}-language and are not "
                "translated in this phase."
            ),
            suggested_followup_phase="Phase 30",
            blocks_research_complete=False,
        )

    def _not_eligible_gap(self) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id="nordic_disclosures",
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                f"The {NORDIC_DISCLOSURE_NAME} service covers Nasdaq Nordic issuers "
                "(Copenhagen / Stockholm / Helsinki / Oslo) only; this issuer does "
                "not resolve to a verified Nordic issuer, so no Nordic "
                "regulated-disclosure reference is provided."
            ),
            blocks_research_complete=False,
        )

    def _reference_result(self, company: CompanyContext) -> ConnectorResult:
        verified = self._nordic_issuer(company)
        if verified is None:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[self._not_eligible_gap()],
            )
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item(verified)],
            source_gaps=[self._content_gap(verified), self._translation_gap(verified)],
        )

    # -- Fetch surface -----------------------------------------------------

    async def fetch_filings(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        return self._reference_result(company)

    async def fetch_events(
        self, company: CompanyContext, query: QueryContext
    ) -> ConnectorResult:
        """Live regulated disclosures when enabled, else the venue reference.

        Private-use readiness PR-E. With ``source_live_disclosures_enabled``
        off this is byte-for-byte the previous reference-only behaviour. With
        it on, the exchange's OWN company-news service is queried under the
        bounds in ``venue_disclosures`` and each announcement becomes a typed
        event; a venue that fails degrades back to the reference plus an
        honest, machine-readable limitation — never a fabricated announcement.
        """
        if not getattr(self._cfg, "source_live_disclosures_enabled", False):
            return self._reference_result(company)
        verified = self._nordic_issuer(company)
        if verified is None:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[self._not_eligible_gap()],
            )

        feed = await self._disclosure_fetcher(
            issuer_ticker=verified.ticker,
            issuer_name=verified.company_name,
            exchange=company.exchange or verified.exchange,
            country=verified.country,
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

        items = disclosure_events_to_evidence(
            feed.events,
            source_id="nordic_disclosures",
            transport_label=_TRANSPORT_LABEL,
            id_prefix="NORDICEVT",
            max_items=int(getattr(self._cfg, "live_disclosure_max_events", 15)),
        )
        # The venue reference is retained alongside the events: it is what tells
        # a reader WHERE these came from and where to look for more.
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item(verified), *items],
            source_gaps=[self._translation_gap(verified)],
        )

    def _live_unavailable_gap(
        self, verified: VerifiedIssuerSource, feed: "DisclosureFeed"
    ) -> SourceGap:
        """Honest, machine-readable record of WHY no live event was retrieved."""
        detail = "; ".join(feed.limitations) or "no disclosures in the lookback window"
        return SourceGap(
            connector_key=self.connector_key,
            source_id="nordic_disclosures",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                f"Live regulated disclosures for {verified.company_name} were "
                f"requested from {NORDIC_DISCLOSURE_NAME} but none were "
                f"retrieved ({detail}). The venue reference is provided instead; "
                "no announcement was assumed or fabricated."
            ),
            blocks_research_complete=False,
        )

    # -- Health ------------------------------------------------------------

    def healthcheck(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=(
                "Emits a T2 regulator-transport source reference to the Nasdaq "
                "Nordic company-disclosure venue (national FSA, e.g. Finanstilsynet) "
                "for verified Nordic issuers; local-language docs require "
                "translation. Primary filing CONTENT is not fetched at report time "
                f"({_CONTENT_FOLLOWUP_PHASE} follow-up)."
            ),
        )


__all__ = [
    "NordicDisclosuresConnector",
    "NORDIC_DISCLOSURE_NAME",
    "NORDIC_DISCLOSURE_URL",
]
