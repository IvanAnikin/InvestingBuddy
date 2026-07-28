"""
Event-trigger reference connectors — Phase 29D.1 (procurement) + 29D.2 (patents).

Establishes the EVENT-TRIGGER evidence category as a set of **reference-only**
venues, mirroring the 29C macro reference layer but with an EVENT flavour. An
event connector is network-free at report time and fabricates nothing: for a
relevant theme / region it emits ONE bounded **SOURCE REFERENCE** — a pointer to
a fixed, public, token-free official venue plus a short description of *which
records that venue publishes for the theme* — and an explicit honest
``SourceGap`` recording that the live records were NOT fetched.

Two kinds of event venue share this one generic connector:

  * **Procurement / tender venues (29D.1)** — EU TED, USAspending.gov. Reference
    text names *which tenders / awards a venue publishes*; never a specific
    award, contractor, amount, contract number, or date.
  * **Patent office / index venues (29D.2)** — Google Patents (an aggregator
    INDEX of patent publications), USPTO (PatentsView), EPO Espacenet. Reference
    text names *which patent filings a venue publishes for an innovation / R&D /
    IP theme*; never a specific patent number, title, inventor, assignee, claim,
    filing date, or grant date, and — critically — **never a legal, infringement,
    validity, priority, or patentability conclusion or any materiality claim**.

Crucially, EVERY event reference (procurement or patent) is a **WEAK internal
research-priority signal only**. It says "this theme has a public venue worth
checking", never that a specific award happened or a specific patent was filed,
never a materiality claim, and never a trade signal. Every reference is stamped
``needs_human_review`` and carries an explicit weak-signal marker.

Hard guarantees:
  * **No fabricated specifics.** No contractor / award amount / contract number
    (procurement) and no patent number / title / inventor / assignee / claim /
    filing or grant date (patents) is ever emitted — only the identity of the
    venue and the themes it publishes, plus an honest "live records not fetched"
    gap. A patent reference additionally draws **no legal / infringement /
    validity / patentability conclusion** of any kind.
  * **No network at report time.** The reference URL + theme description come from
    the code-defined ``EVENT_SOURCES`` / ``PATENT_SOURCES`` tables; nothing is
    fetched here. The keyed USPTO PatentsView and EPO OPS APIs are NOT used.
  * **No API keys / secrets / tokenised URLs.** Every URL is a fixed public
    landing page with no query string. ``EvidenceItem`` strips any
    credential-bearing query param as a backstop anyway.
  * **Weak, needs-review, recommendation-free.** The reference text carries no
    rating / valuation / trading-signal language and no materiality claim, so it
    passes the report safety gate unchanged; an event reference must never read as
    a company recommendation or catalyst.

One generic ``EventReferenceConnector`` is parameterised by a small immutable
``EventSourceSpec`` (and a per-kind ``_EventFlavor`` selected from its
``provider_type``) so the registry can register one connector per event source
from a single source of truth (``ALL_EVENT_SOURCES``).
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.sources.connector_base import (
    ConnectorHealth,
    ConnectorResult,
    QueryContext,
    SourceConnector,
    _now,
)
from app.services.sources.evidence import EvidenceItem, build_evidence_item
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.taxonomy import (
    T2_REGULATOR_OR_GOV,
    T5_API_AGGREGATOR,
    ConnectorStatus,
    ProviderType,
)

# Follow-up phase that will (optionally) bind bounded live tender / award / patent
# data.
_EVENT_FOLLOWUP_PHASE = "Phase 29D"

# The evidence source_type stamped on every event reference. Uses
# "government_data" (accepted by the source schema's ``VALID_SOURCE_TYPES``) — NOT
# "government_contract", which would falsely imply a real, specific award. Patent
# offices are government bodies; Google Patents is an aggregator index OF
# government patent publications, so "government_data" is honest for all of them.
_EVENT_SOURCE_TYPE = "government_data"


@dataclass(frozen=True)
class EventSourceSpec:
    """Immutable identity + coverage of one reference-only event venue.

    Carries no secret and no fabricated record — only *which official venue* it
    is, the fixed public landing page, and (as plain English) *which themes* it
    publishes. ``theme_keywords`` are lower-case substrings matched against a
    query's theme. ``refresh_cadence_days`` is a FRESHNESS hint: how quickly the
    underlying venue turns over, used to stamp ``stale_after_days`` on the emitted
    reference. ``tier`` is the source's transport/content tier — T2 for a
    government procurement / patent-office publisher, T5 for an aggregator index
    such as Google Patents. ``provider_type`` selects the reference *flavour*
    (procurement vs patent) via ``_flavor_for``.
    """

    source_id: str
    display_name: str
    url: str
    provider_type: ProviderType
    jurisdiction: str | None
    region: str | None
    publishes: str
    theme_keywords: tuple[str, ...]
    refresh_cadence_days: int
    reliability_note: str
    tier: str = T2_REGULATOR_OR_GOV


# ---------------------------------------------------------------------------
# Per-kind reference vocabulary (flavour)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _EventFlavor:
    """The human-readable vocabulary for one KIND of event venue.

    The generic ``EventReferenceConnector`` emits the SAME bounded structure for
    every event source — one tiered ``government_data`` source reference plus an
    honest ``data_not_sourced`` gap, WEAK + ``needs_human_review``, with no
    fabricated specifics — regardless of kind. Only the nouns differ between a
    procurement / tender venue (29D.1) and a patent office / index venue (29D.2),
    so those nouns live here and the connector logic stays single-sourced.
    """

    kind: str  # short noun, e.g. "procurement / tender" | "patent"
    venue_desc: str  # parenthetical venue descriptor
    catalog: str  # content_source catalog suffix
    excerpt_scope: str  # "official ... venue only: no specific ... ." clause
    provenance_disclaimer: str  # the "... source reference only — no specific ..." line
    warning: str  # the single warning string
    gap_scope: str  # text after "{name}: " in the data_not_sourced gap
    health_publishes: str  # what the venue "publishes" in the health detail
    health_extra: str  # extra disclaimer clause in the health detail


_PROCUREMENT_FLAVOR = _EventFlavor(
    kind="procurement / tender",
    venue_desc="official public procurement / spending venue",
    catalog="procurement / tender venue catalog",
    excerpt_scope=(
        "official procurement / tender venue only: no specific tender, award, "
        "contractor, contract value, contract number, or date is fetched or "
        "fabricated."
    ),
    provenance_disclaimer=(
        "Procurement / tender source reference only — no specific tenders, "
        "awards, contractors, amounts, contract numbers, or dates fetched"
    ),
    warning=(
        "Procurement / tender source reference only; live tenders and awards are "
        "not fetched at report time. Weak internal research-priority signal — not "
        "a materiality claim, not a trade signal. Human review required."
    ),
    gap_scope=(
        "procurement / tender venue reference only; live tenders / awards not "
        "fetched at report time. Only a pointer to the venue and the procurement "
        "themes it covers is provided."
    ),
    health_publishes="tenders / awards",
    health_extra="",
)

# Patent flavour (29D.2). Deliberately avoids the literal words "infringement",
# "validity", and "patentability" — even inside a negated disclaimer — so the
# reference text can never read as a legal claim; instead it states, plainly, that
# NO legal / ownership / competitive-strength conclusion of any kind is drawn.
_PATENT_FLAVOR = _EventFlavor(
    kind="patent",
    venue_desc="official public patent office / index venue",
    catalog="patent filing / publication venue catalog",
    # Kept concise so the "no legal conclusion" disclaimer survives the 400-char
    # excerpt bound; the full patent-field enumeration lives in the (unbounded)
    # provenance + warning.
    excerpt_scope=(
        "official patent venue only: no specific patent is fetched or fabricated, "
        "and no legal or competitive-strength conclusion is drawn."
    ),
    provenance_disclaimer=(
        "Patent source reference only — no specific patent number, title, "
        "inventor, assignee, claim, filing date, or grant date fetched, and no "
        "legal, ownership, or competitive-strength conclusion drawn"
    ),
    warning=(
        "Patent source reference only; live patent filings are not fetched at "
        "report time. Weak internal research-priority signal — not a materiality "
        "claim, not a trade signal, and not a legal, ownership, or "
        "competitive-strength conclusion about any patent. Human review required."
    ),
    gap_scope=(
        "patent venue reference only; live patent filings not fetched at report "
        "time. Only a pointer to the venue and the patent / innovation themes it "
        "covers is provided."
    ),
    health_publishes="patent filings",
    health_extra=(
        " No legal, ownership, or competitive-strength conclusion about any "
        "patent is drawn."
    ),
)


def _flavor_for(provider_type: ProviderType) -> _EventFlavor:
    """Select the reference vocabulary for a spec's kind (patent vs procurement)."""
    if provider_type == ProviderType.patents:
        return _PATENT_FLAVOR
    return _PROCUREMENT_FLAVOR


# ---------------------------------------------------------------------------
# Source tables
# ---------------------------------------------------------------------------

# The procurement / tender event layer (29D.1). Every URL is a fixed, public,
# token-free official landing page.
EVENT_SOURCES: tuple[EventSourceSpec, ...] = (
    EventSourceSpec(
        source_id="eu_ted",
        display_name="EU TED (Tenders Electronic Daily)",
        url="https://ted.europa.eu/",
        provider_type=ProviderType.procurement,
        jurisdiction=None,
        region="Europe",
        publishes=(
            "public procurement notices and contract award notices from "
            "contracting authorities across the European Union and European "
            "Economic Area, spanning defense, infrastructure, rail, energy, grid "
            "and construction procurement and government spending"
        ),
        theme_keywords=(
            "procurement", "tender", "tenders", "contract", "contracts",
            "contract award", "contract awards", "public procurement",
            "government spending", "government contract", "defense", "defence",
            "infrastructure", "rail", "grid", "energy", "construction",
            "framework agreement",
        ),
        refresh_cadence_days=1,
        reliability_note=(
            "Procurement / tender venue reference; live tenders / awards not "
            "fetched at report time; weak internal research-priority signal — "
            "Phase 29D follow-up for live fetch. EU TED publishes European "
            "procurement and contract-award notices — no specific tender, award, "
            "contractor, amount, contract number, or date emitted."
        ),
    ),
    EventSourceSpec(
        source_id="usaspending",
        display_name="USAspending.gov",
        url="https://www.usaspending.gov/",
        provider_type=ProviderType.procurement,
        jurisdiction="US",
        region="North America",
        publishes=(
            "United States federal award and contract data reported by federal "
            "agencies, spanning defense, infrastructure, energy, grid and rail "
            "procurement, grants and government spending"
        ),
        theme_keywords=(
            "procurement", "tender", "tenders", "contract", "contracts",
            "federal contract", "federal award", "award", "awards",
            "government spending", "federal spending", "government contract",
            "defense", "defence", "infrastructure", "rail", "grid", "energy",
            "grant", "grants",
        ),
        refresh_cadence_days=1,
        reliability_note=(
            "Procurement / tender venue reference; live tenders / awards not "
            "fetched at report time; weak internal research-priority signal — "
            "Phase 29D follow-up for live fetch. USAspending.gov publishes US "
            "federal award and contract data — no specific award, contract, "
            "contractor, amount, contract number, or date emitted."
        ),
    ),
)


# The patent office / index event layer (29D.2). Every URL is a fixed, public,
# token-free official landing page. Patents are a WEAK innovation / R&D
# research-priority theme only — a patent reference is never a candidate, a
# catalyst, a materiality claim, a trade signal, or a legal / infringement /
# validity conclusion. The keyed USPTO PatentsView + EPO OPS APIs are NOT used.
_PATENT_THEME_KEYWORDS: tuple[str, ...] = (
    "patent", "patents", "patented", "patenting",
    "innovation", "innovative",
    "r&d", "research and development",
    "intellectual property", "ip",
    "technology", "tech",
    "semiconductor", "semiconductors", "chip", "chips",
    "pharma", "pharmaceutical", "biotech", "drug",
    "battery", "batteries", "electric vehicle", "ev",
    "materials",
)

# Patent publication gazettes turn over roughly weekly, so a 7-day freshness hint
# is honest for the venue reference (used only to stamp stale_after_days).
_PATENT_REFRESH_DAYS = 7

_PATENT_RELIABILITY_NOTE_TAIL = (
    "patent office/index venue reference; live patent filings not fetched at "
    "report time; no legal / infringement conclusions; weak internal "
    "research-priority signal — Phase 29D follow-up for live fetch. No specific "
    "patent number, title, inventor, assignee, claim, or date emitted."
)

PATENT_SOURCES: tuple[EventSourceSpec, ...] = (
    EventSourceSpec(
        source_id="google_patents",
        display_name="Google Patents",
        url="https://patents.google.com/",
        provider_type=ProviderType.patents,
        jurisdiction=None,
        region=None,
        publishes=(
            "an aggregated worldwide index of patent applications and grants, for "
            "innovation and R&D themes"
        ),
        theme_keywords=_PATENT_THEME_KEYWORDS,
        refresh_cadence_days=_PATENT_REFRESH_DAYS,
        reliability_note=(
            "Aggregator-index " + _PATENT_RELIABILITY_NOTE_TAIL + " Google Patents "
            "is an INDEX of patent publications across offices, not itself a "
            "patent office."
        ),
        tier=T5_API_AGGREGATOR,
    ),
    EventSourceSpec(
        source_id="uspto",
        display_name="USPTO (PatentsView)",
        url="https://www.uspto.gov/",
        provider_type=ProviderType.patents,
        jurisdiction="US",
        region="North America",
        publishes=(
            "US patent applications and grants from the US Patent and Trademark "
            "Office, for innovation and R&D themes"
        ),
        theme_keywords=_PATENT_THEME_KEYWORDS,
        refresh_cadence_days=_PATENT_REFRESH_DAYS,
        reliability_note="US " + _PATENT_RELIABILITY_NOTE_TAIL,
        tier=T2_REGULATOR_OR_GOV,
    ),
    EventSourceSpec(
        source_id="epo_espacenet",
        display_name="EPO Espacenet",
        url="https://worldwide.espacenet.com/",
        provider_type=ProviderType.patents,
        jurisdiction=None,
        region="Europe",
        publishes=(
            "European and worldwide patent applications and grants from the "
            "European Patent Office, for innovation and R&D themes"
        ),
        theme_keywords=_PATENT_THEME_KEYWORDS,
        refresh_cadence_days=_PATENT_REFRESH_DAYS,
        reliability_note="European " + _PATENT_RELIABILITY_NOTE_TAIL,
        tier=T2_REGULATOR_OR_GOV,
    ),
)


# The full event reference table the registry, collector and connector builder all
# iterate: the 29D.1 procurement / tender venues plus the 29D.2 patent office /
# index venues.
ALL_EVENT_SOURCES: tuple[EventSourceSpec, ...] = EVENT_SOURCES + PATENT_SOURCES


def event_spec_for(source_id: str) -> EventSourceSpec | None:
    """Return the event spec (procurement or patent) for ``source_id``, or None."""
    return next((s for s in ALL_EVENT_SOURCES if s.source_id == source_id), None)


class EventReferenceConnector(SourceConnector):
    """A reference-only EVENT connector for ONE venue (procurement or patent).

    ``fetch_events`` emits a bounded ``government_data`` *source reference* plus an
    honest "live records not fetched" gap when the query theme / region is
    relevant; otherwise it returns an empty result (no evidence, no gap). It never
    fetches and never fabricates a tender / award (procurement) or a patent
    number / inventor / assignee / date (patents), and a patent reference draws no
    legal / infringement / validity conclusion. The reference is a WEAK internal
    research-priority signal, stamped ``needs_human_review`` and carrying no
    materiality claim. ``fetch_macro_context`` / ``fetch_filings`` /
    ``search_company`` are not an event path and return an honest not-eligible gap.
    """

    status = ConnectorStatus.enabled

    def __init__(self, spec: EventSourceSpec) -> None:
        self._spec = spec
        self._flavor = _flavor_for(spec.provider_type)
        self.connector_key = spec.source_id
        self.supported_source_ids = (spec.source_id,)

    # -- Relevance ---------------------------------------------------------

    def covers(self, query: QueryContext) -> bool:
        """True when this venue is relevant to the query theme / region.

        A generic ask with no theme and no region is NOT answered — event venues
        are always theme-specific, never a default reference. Region-based
        surfacing applies to region-scoped procurement / tender venues only; patent
        venues are purely thematic (innovation / R&D / IP) and never surface on a
        bare region query, so their office's home region is metadata, not a match.
        """
        theme = (query.query or "").strip().lower()
        region = (query.region or "").strip().lower()
        if theme and any(kw in theme for kw in self._spec.theme_keywords):
            return True
        if self._spec.provider_type != ProviderType.patents:
            if region and self._spec.region and self._spec.region.lower() in region:
                return True
        return False

    # -- Result builders ---------------------------------------------------

    def _reference_item(self) -> EvidenceItem:
        spec = self._spec
        flavor = self._flavor
        excerpt = (
            f"{spec.display_name} publishes {spec.publishes}. This item is a "
            f"source reference to that {flavor.excerpt_scope} It is a weak "
            "internal research-priority signal, not a materiality claim."
        )
        return build_evidence_item(
            id=f"EVENT_{spec.source_id.upper()}",
            source_id=spec.source_id,
            source_name=spec.display_name,
            provider_transport=f"{spec.display_name} ({flavor.venue_desc})",
            provider_transport_tier=spec.tier,
            content_source=f"{spec.display_name} — {flavor.catalog}",
            content_source_tier=spec.tier,
            source_type=_EVENT_SOURCE_TYPE,
            title=f"{spec.display_name} — {flavor.kind} source reference",
            url=spec.url,
            excerpt=excerpt,
            data_quality="reference_only",
            confidence="low",
            stale_after_days=spec.refresh_cadence_days,
            provenance=[
                f"{spec.display_name} ({flavor.venue_desc})",
                flavor.provenance_disclaimer,
                "weak internal research-priority signal; verify against the live "
                "venue",
                "needs_human_review=true",
            ],
            warnings=[flavor.warning],
        )

    def _reference_gap(self) -> SourceGap:
        spec = self._spec
        return SourceGap(
            connector_key=self.connector_key,
            source_id=spec.source_id,
            gap_type=GapType.data_not_sourced,
            severity=GapSeverity.info,
            message=f"{spec.display_name}: {self._flavor.gap_scope}",
            suggested_followup_phase=_EVENT_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _not_event_source_gap(self, method: str) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id=self._spec.source_id,
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                f"{self._spec.display_name} is a {self._flavor.kind} EVENT venue "
                f"reference; {method.replace('fetch_', '').replace('_', ' ')} are "
                "not provided by this connector."
            ),
            blocks_research_complete=False,
        )

    # -- Fetch surface -----------------------------------------------------

    async def fetch_events(self, query: QueryContext) -> ConnectorResult:  # type: ignore[override]
        if not self.covers(query):
            return ConnectorResult(connector_key=self.connector_key)
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item()],
            source_gaps=[self._reference_gap()],
        )

    async def fetch_macro_context(self, query: QueryContext) -> ConnectorResult:
        return ConnectorResult(
            connector_key=self.connector_key,
            source_gaps=[self._not_event_source_gap("fetch_macro_context")],
        )

    async def fetch_filings(self, company, query) -> ConnectorResult:  # type: ignore[no-untyped-def]
        return ConnectorResult(
            connector_key=self.connector_key,
            source_gaps=[self._not_event_source_gap("fetch_filings")],
        )

    async def search_company(self, company, query) -> ConnectorResult:  # type: ignore[no-untyped-def]
        return ConnectorResult(
            connector_key=self.connector_key,
            source_gaps=[self._not_event_source_gap("search_company")],
        )

    # -- Health ------------------------------------------------------------

    def healthcheck(self) -> ConnectorHealth:
        flavor = self._flavor
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=(
                f"Emits a {self._spec.tier} {flavor.kind} SOURCE REFERENCE "
                f"to {self._spec.display_name} (which {flavor.health_publishes} it "
                "publishes) for a relevant theme/region; live "
                f"{flavor.health_publishes} are not fetched at report time "
                f"({_EVENT_FOLLOWUP_PHASE} follow-up). Weak internal "
                f"research-priority signal.{flavor.health_extra} No API key used."
            ),
        )


def build_event_connectors() -> dict[str, EventReferenceConnector]:
    """One reference-only connector per event source (procurement + patents)."""
    return {s.source_id: EventReferenceConnector(s) for s in ALL_EVENT_SOURCES}


__all__ = [
    "EventSourceSpec",
    "EVENT_SOURCES",
    "PATENT_SOURCES",
    "ALL_EVENT_SOURCES",
    "EventReferenceConnector",
    "event_spec_for",
    "build_event_connectors",
]
