"""
Local-language business-press reference connector — Phase 30B.

Mirrors the Phase 29B.4C regulator connectors (Deutsche Börse / Nordic / SIX)
in shape, but for an allowlisted, reference-only tier of *local-language business
press* rather than a regulator. Its report-time job is honest, bounded and
network-free:

  * For a company that resolves to a **verified non-US issuer** whose home market
    is French / German / Italian / Danish (FR / DE / IT / DA), it emits ONE
    bounded **T4 quality-media SOURCE REFERENCE** — a pointer to a well-known,
    reputable local-language business-press venue for that language (a fixed
    public landing page), carrying the issuer's identity. The excerpt is a
    GENUINE, short **local-language descriptive sentence** stating that the venue
    is local-language business press covering the issuer and that the full article
    content is NOT fetched here. It is deliberately **not an article**: no
    headline, quote, figure, or date is invented. Because the excerpt is
    non-English, the item is marked ``requires_translation`` (consumed by the
    Phase 30A translation layer) and carries an honest ``translation_required``
    gap plus a content-not-fetched gap.

  * For anything that does not resolve to a verified FR / DE / IT / DA issuer it
    returns an honest ``source_not_eligible`` gap and **no** reference — never a
    fabricated news story, never a broad web search.

This item deliberately **lowers** source quality: T4 (quality media), ``low``
confidence, ``metadata_only`` data quality, ``needs_human_review`` on every
result. It is a WEAK, human-review-required research-priority signal, never a
recommendation, catalyst, materiality claim, or valuation.

Guarantees (mirror the regulator reference path):
  * **No network call at report time.** Identity comes from the code-defined
    verified-issuer registry and the venue from fixed public constants; nothing
    is fetched here.
  * **No fabrication.** Only the venue is cited; no article / headline / quote /
    figure / date is manufactured. The excerpt is a factual description of the
    venue, written in the local language.
  * URLs are stripped of any credential-bearing query parameter by
    ``EvidenceItem`` before storage; the venue landing pages carry none.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.exchange_registry import is_sec_eligible, is_us_exchange
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
from app.services.sources.taxonomy import T4_QUALITY_MEDIA, ConnectorStatus
from app.services.sources.verified_issuer_sources import (
    VerifiedIssuerSource,
    get_verified_issuer_source,
)

# Registry id (reused from the former ``local_language_business_press`` planned
# row, now promoted) and connector key. Kept identical so the registry, the
# collector and the health surface all speak one name.
SOURCE_ID = "local_language_business_press"
CONNECTOR_KEY = "local_language_business_press"

_TRANSPORT_LABEL = "Local-language business-press venue (editorial media, reference only)"

# Follow-up phase that will bind any flag-gated live fetch + real translation.
_CONTENT_FOLLOWUP_PHASE = "Phase 30B"
_TRANSLATION_FOLLOWUP_PHASE = "Phase 30"


@dataclass(frozen=True)
class LocalLanguagePressSource:
    """One allowlisted local-language business-press venue for a market.

    Holds only safe metadata: a language, a fixed public landing-page URL, and a
    GENUINE local-language descriptive sentence about the venue. The excerpt is
    NOT a news story — it never contains a fabricated headline, quote, figure, or
    date.
    """

    language_code: str
    language_name: str
    venue_name: str
    venue_url: str
    excerpt: str


# Allowlisted, reputable local-language business-press venues, keyed by the
# issuer's home country. One well-known official/reputable business-press landing
# page per language (fixed public HTTPS, no query, no token, no API key). The
# excerpt is a real, honest sentence in the local language that describes the
# venue and states the full content is not fetched here — never a fabricated
# article. Each is written to be unambiguously detectable by ``detect_language``.
LOCAL_LANGUAGE_PRESS_SOURCES: dict[str, LocalLanguagePressSource] = {
    "France": LocalLanguagePressSource(
        language_code="fr",
        language_name="French",
        venue_name="Les Échos (French-language business press)",
        venue_url="https://www.lesechos.fr/",
        excerpt=(
            "Presse économique de langue française couvrant la société émettrice "
            "et les publications financières des maisons du groupe; le contenu "
            "intégral des articles n'est pas récupéré ici."
        ),
    ),
    "Germany": LocalLanguagePressSource(
        language_code="de",
        language_name="German",
        venue_name="Handelsblatt (German-language business press)",
        venue_url="https://www.handelsblatt.com/",
        excerpt=(
            "Deutschsprachige Wirtschaftspresse über die Gesellschaft und das "
            "Unternehmen der Gruppe; der vollständige Inhalt der Artikel wird "
            "hier nicht abgerufen."
        ),
    ),
    "Italy": LocalLanguagePressSource(
        language_code="it",
        language_name="Italian",
        venue_name="Milano Finanza (Italian-language business press)",
        venue_url="https://www.milanofinanza.it/",
        excerpt=(
            "Stampa economica in lingua italiana che copre la società emittente "
            "e gli articoli finanziari del gruppo; il contenuto completo non "
            "viene recuperato qui."
        ),
    ),
    "Denmark": LocalLanguagePressSource(
        language_code="da",
        language_name="Danish",
        venue_name="Børsen (Danish-language business press)",
        venue_url="https://borsen.dk/",
        excerpt=(
            "Dansksproget erhvervspresse, der dækker selskabet og dets "
            "årsrapport; det fulde indhold af artiklerne er ikke hentet her."
        ),
    ),
}

# Countries eligible for a local-language business-press reference.
LOCAL_LANGUAGE_PRESS_COUNTRIES = frozenset(LOCAL_LANGUAGE_PRESS_SOURCES)


def local_language_press_source_for(
    company: CompanyContext,
    *,
    verified: VerifiedIssuerSource | None = None,
) -> LocalLanguagePressSource | None:
    """Resolve the local-language press venue for a verified non-US issuer, or None.

    Requires a verified-registry match (so an issuer's home market is never
    guessed) whose country maps to a supported local-language market
    (FR / DE / IT / DA). A US / SEC-eligible venue is never eligible. Returns
    None for everything else — the caller then emits an honest gap, never a
    fabricated reference.
    """
    if is_us_exchange(company.exchange) or is_sec_eligible(company.exchange):
        return None
    verified = verified or get_verified_issuer_source(company.ticker, company.exchange)
    if verified is None:
        return None
    country = (verified.country or "").strip()
    return LOCAL_LANGUAGE_PRESS_SOURCES.get(country)


class LocalLanguagePressConnector(SourceConnector):
    """Allowlisted local-language business-press reference connector.

    Emits a bounded T4 quality-media *source reference* (not article content) for
    a verified FR / DE / IT / DA issuer, with a GENUINE local-language descriptive
    excerpt, plus an honest content-not-fetched gap and a ``translation_required``
    gap. It deliberately lowers source quality (T4 / low confidence /
    metadata-only / needs human review) and never fabricates a news story.
    """

    connector_key = CONNECTOR_KEY
    supported_source_ids = (SOURCE_ID,)
    status = ConnectorStatus.enabled

    def __init__(self, *, verified_source: VerifiedIssuerSource | None = None) -> None:
        # An explicitly injected verified source (tests / preview / collector)
        # takes precedence; otherwise the connector resolves identity itself.
        self._verified = verified_source

    # -- Eligibility -------------------------------------------------------

    def _resolve(
        self, company: CompanyContext
    ) -> tuple[VerifiedIssuerSource | None, LocalLanguagePressSource | None]:
        verified = self._verified or get_verified_issuer_source(
            company.ticker, company.exchange
        )
        spec = local_language_press_source_for(company, verified=verified)
        return (verified if spec is not None else None), spec

    # -- Result builders ---------------------------------------------------

    def _reference_item(
        self, verified: VerifiedIssuerSource, spec: LocalLanguagePressSource
    ) -> EvidenceItem:
        """One bounded T4 local-language business-press source reference."""
        ident = verified.company_name
        warnings = [
            f"Source reference to {spec.language_name}-language business press "
            f"({spec.venue_name}); no article CONTENT is fetched at report time, "
            "and no headline, quote, figure, or date is fabricated. Human review "
            "required.",
            f"The excerpt is written in {spec.language_name} and requires "
            "machine-assisted translation (Phase 30A) plus human review before "
            "it can be used in English.",
            "Weak research-priority signal only — not a catalyst, materiality "
            "claim, or valuation.",
        ]
        provenance = [
            f"{spec.venue_name} — local-language business-press coverage reference",
            "Source reference only — no article content retrieved",
            "requires_translation=true",
            "needs_human_review=true",
        ]
        content_source = (
            f"{spec.venue_name} — local-language business-press venue"
        )
        return build_evidence_item(
            id="LOCALPRESSREF",
            source_id=SOURCE_ID,
            source_name=ident,
            provider_transport=_TRANSPORT_LABEL,
            provider_transport_tier=T4_QUALITY_MEDIA,
            content_source=content_source,
            content_source_tier=T4_QUALITY_MEDIA,
            source_type="news_article",
            title=(
                f"{ident} — {spec.language_name}-language business-press coverage "
                f"reference ({spec.venue_name})"
            ),
            url=spec.venue_url,
            # A GENUINE, short local-language description of the venue — never a
            # fabricated article. Kept non-English so Phase 30A translates it.
            excerpt=spec.excerpt,
            data_quality="metadata_only",
            confidence="low",
            requires_translation=True,
            original_language=spec.language_name,
            provenance=provenance,
            warnings=warnings,
        )

    def _content_gap(
        self, verified: VerifiedIssuerSource, spec: LocalLanguagePressSource
    ) -> SourceGap:
        """Honest gap: local-language article content is not fetched at report time."""
        return SourceGap(
            connector_key=self.connector_key,
            source_id=SOURCE_ID,
            gap_type=GapType.data_not_sourced,
            severity=GapSeverity.info,
            message=(
                f"{spec.language_name}-language business-press articles about "
                f"{verified.company_name} ({spec.venue_name}) are not fetched at "
                "report time; only a bounded local-language coverage reference is "
                "provided (no headline, quote, figure, or date)."
            ),
            suggested_followup_phase=_CONTENT_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _translation_gap(
        self, verified: VerifiedIssuerSource, spec: LocalLanguagePressSource
    ) -> SourceGap:
        """Honest gap: the local-language reference needs translation + review."""
        return SourceGap(
            connector_key=self.connector_key,
            source_id=SOURCE_ID,
            gap_type=GapType.translation_required,
            severity=GapSeverity.info,
            message=(
                f"{spec.language_name}-language business-press coverage for "
                f"{verified.company_name} is non-English and requires "
                "machine-assisted translation plus human review; pending "
                f"{_TRANSLATION_FOLLOWUP_PHASE} translation."
            ),
            suggested_followup_phase=_TRANSLATION_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _not_eligible_gap(self) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id=SOURCE_ID,
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                "Local-language business-press references cover verified issuers "
                "in French / German / Italian / Danish markets only; this issuer "
                "does not resolve to such a verified issuer, so no local-language "
                "press reference is provided."
            ),
            blocks_research_complete=False,
        )

    def _reference_result(self, company: CompanyContext) -> ConnectorResult:
        verified, spec = self._resolve(company)
        if verified is None or spec is None:
            return ConnectorResult(
                connector_key=self.connector_key,
                source_gaps=[self._not_eligible_gap()],
            )
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item(verified, spec)],
            source_gaps=[
                self._content_gap(verified, spec),
                self._translation_gap(verified, spec),
            ],
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
                "Emits a bounded T4 quality-media source reference to an "
                "allowlisted local-language business-press venue (French / German "
                "/ Italian / Danish) for verified issuers, with a genuine "
                "local-language descriptive excerpt. Requires translation "
                "(Phase 30A) + human review. No article content is fetched at "
                "report time and no news is fabricated."
            ),
        )


def build_local_language_press_connectors() -> dict[str, SourceConnector]:
    """Return the local-language press connector keyed by its connector key.

    Mirrors ``build_macro_connectors`` / ``build_event_connectors`` so the
    registry wires this layer from one place.
    """
    return {CONNECTOR_KEY: LocalLanguagePressConnector()}


__all__ = [
    "SOURCE_ID",
    "CONNECTOR_KEY",
    "LocalLanguagePressSource",
    "LOCAL_LANGUAGE_PRESS_SOURCES",
    "LOCAL_LANGUAGE_PRESS_COUNTRIES",
    "local_language_press_source_for",
    "LocalLanguagePressConnector",
    "build_local_language_press_connectors",
]
