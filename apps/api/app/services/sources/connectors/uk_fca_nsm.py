"""
UK FCA National Storage Mechanism (NSM) connector — Phase 29B.4A.

Upgrades the former generic ``uk_fca_nsm`` *scaffold* into a dedicated connector
for LSE-listed / UK-regulated issuers. Its report-time job is honest and bounded:

  * For a company that resolves to a **verified UK-regulated issuer** (its venue
    is a UK venue *and* ``get_verified_issuer_source`` resolves it to a UK entity),
    it emits ONE bounded **T2 regulator-transport SOURCE REFERENCE** — a pointer
    to the issuer's regulated-disclosure venue (the FCA National Storage Mechanism
    / RNS), carrying the issuer's identity and the canonical public NSM reference
    URL. It is deliberately **not a filing**: no specific announcement, headline,
    date, or RNS number is invented. The same call also emits an explicit honest
    ``SourceGap`` recording that the actual T1 filing *content* is not fetched at
    report time (live content retrieval is a Phase 29B.4 follow-up, Task 2).

  * For anything that does not resolve to a verified UK-regulated issuer, it
    returns an honest ``source_not_eligible`` gap and **no** reference — never a
    US SEC lookup, never Boeing for ``BA.LSE`` (which is BAE Systems).

Guarantees (mirrors the company-IR static/metadata report path):
  * **No network call at report time.** Identity + the disclosure-venue reference
    come from the code-defined verified-issuer registry and fixed public
    constants; nothing is fetched here.
  * **No fabrication.** Only the venue is cited; no filing/notice is manufactured.
  * URLs are stripped of any credential-bearing query parameter by
    ``EvidenceItem`` before storage; the NSM reference carries none anyway.
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

# Public, fixed reference to the UK regulated-disclosure venue. This is the
# venue's own landing page, NOT a per-filing URL — no notice is fabricated.
FCA_NSM_NAME = "FCA National Storage Mechanism (NSM)"
FCA_NSM_URL = "https://data.fca.org.uk/#/nsm/nationalstoragemechanism"
_RNS_LABEL = "Regulatory News Service (RNS)"
_TRANSPORT_LABEL = "UK FCA National Storage Mechanism (regulator-operated)"

# UK-regulated venues + countries this connector is eligible for.
_UK_VENUES = frozenset({"LSE"})
_UK_COUNTRIES = frozenset({"United Kingdom", "UK", "GB"})

# Follow-up phase that will bind the flag-gated live content fetch (Task 2).
_CONTENT_FOLLOWUP_PHASE = "Phase 29B.4"


class UkFcaNsmConnector(SourceConnector):
    """Dedicated UK FCA NSM / RNS regulated-disclosure reference connector.

    Emits a bounded T2 regulator-transport *source reference* (not filing
    content) for a verified UK-regulated issuer, plus an honest content gap. It
    is a live evidence path for that *reference* — the honest limitation that the
    primary filing *content* is not fetched is carried on every result as a gap.
    """

    connector_key = "uk_fca_nsm"
    supported_source_ids = ("uk_fca_nsm",)
    status = ConnectorStatus.enabled

    def __init__(self, *, verified_source: VerifiedIssuerSource | None = None) -> None:
        # An explicitly injected verified source (tests / preview) takes
        # precedence; otherwise the connector resolves identity itself.
        self._verified = verified_source

    # -- Eligibility -------------------------------------------------------

    def _uk_issuer(self, company: CompanyContext) -> VerifiedIssuerSource | None:
        """Resolve a company to a verified UK-regulated issuer, or None.

        Requires BOTH a UK-regulated venue and a verified-registry match whose
        country is the UK. Refuses to guess — an unresolvable issuer yields None
        (the caller emits an honest ``source_not_eligible`` gap), never a fake.
        """
        verified = self._verified or get_verified_issuer_source(
            company.ticker, company.exchange
        )
        if verified is None:
            return None
        country_ok = (verified.country or "").strip() in _UK_COUNTRIES
        venue_ok = (
            normalize_exchange(company.exchange or verified.exchange) in _UK_VENUES
        )
        return verified if (country_ok and venue_ok) else None

    # -- Result builders ---------------------------------------------------

    def _reference_item(self, verified: VerifiedIssuerSource) -> EvidenceItem:
        """One bounded T2 source reference to the issuer's UK disclosure venue."""
        ident = verified.company_name
        excerpt = (
            f"Regulated disclosures for {ident} ({verified.ticker}.{verified.exchange}) "
            f"— including {_RNS_LABEL} announcements and annual financial reports — "
            f"are published to the {FCA_NSM_NAME}. This item is a source reference "
            "to that regulated-disclosure venue only: no individual filing, "
            "announcement, headline, date, or RNS number is fetched or fabricated."
        )
        return build_evidence_item(
            id="UKNSMREF",
            source_id="uk_fca_nsm",
            source_name=ident,
            provider_transport=_TRANSPORT_LABEL,
            provider_transport_tier=T2_REGULATOR_OR_GOV,
            content_source=f"{FCA_NSM_NAME} — regulated-disclosure venue",
            content_source_tier=T2_REGULATOR_OR_GOV,
            source_type="uk_fca_nsm_reference",
            title=f"{ident} — UK regulated disclosures via {FCA_NSM_NAME}",
            url=FCA_NSM_URL,
            excerpt=excerpt,
            data_quality="metadata_only",
            confidence=verified.source_confidence,
            provenance=[
                "FCA National Storage Mechanism (regulator-operated disclosure venue)",
                "Source reference only — no filing content retrieved",
                "needs_human_review=true",
            ],
            warnings=[
                "Source reference to the UK regulated-disclosure venue (FCA NSM / "
                "RNS); the primary filing CONTENT is not fetched at report time. "
                "Human review required.",
            ],
        )

    def _content_gap(self, verified: VerifiedIssuerSource) -> SourceGap:
        """Honest gap: the T1 filing content behind the venue is not fetched."""
        return SourceGap(
            connector_key=self.connector_key,
            source_id="uk_fca_nsm",
            gap_type=GapType.primary_filing_unavailable,
            severity=GapSeverity.info,
            message=(
                f"UK primary filing content for {verified.company_name} is published "
                f"via the {FCA_NSM_NAME} / {_RNS_LABEL} but is not fetched at report "
                "time; only a source reference to the regulated-disclosure venue is "
                "provided."
            ),
            suggested_followup_phase=_CONTENT_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _not_eligible_gap(self) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id="uk_fca_nsm",
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                f"The {FCA_NSM_NAME} covers UK-regulated issuers only; this issuer "
                "does not resolve to a verified UK-regulated entity, so no UK "
                "regulated-disclosure reference is provided."
            ),
            blocks_research_complete=False,
        )

    def _reference_result(self, company: CompanyContext) -> ConnectorResult:
        verified = self._uk_issuer(company)
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
                "Emits a T2 regulator-transport source reference to the FCA NSM / "
                "RNS venue for verified UK-regulated issuers; primary filing "
                f"CONTENT is not fetched at report time ({_CONTENT_FOLLOWUP_PHASE} "
                "follow-up)."
            ),
        )


__all__ = ["UkFcaNsmConnector", "FCA_NSM_NAME", "FCA_NSM_URL"]
