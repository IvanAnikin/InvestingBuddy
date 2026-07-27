"""
Procurement / tender event-trigger reference connectors — Phase 29D.1.

Establishes the EVENT-TRIGGER evidence category as a set of **reference-only**
procurement / tender venues, mirroring the 29C macro reference layer but with an
EVENT flavour. A procurement event connector is network-free at report time and
fabricates nothing: for a relevant theme / region it emits ONE bounded **T2
procurement SOURCE REFERENCE** — a pointer to a fixed, public, token-free
official tender / award venue plus a short description of *which tenders / awards
that venue publishes for the theme* — and an explicit honest ``SourceGap``
recording that the live tenders / awards were NOT fetched.

Crucially, a procurement / tender reference is a **WEAK internal
research-priority signal only**. It says "this theme has a public tender / award
venue worth checking", never that a specific award happened, never a materiality
claim, and never a trade signal. Every reference is stamped
``needs_human_review`` and carries an explicit weak-signal marker.

Hard guarantees:
  * **No fabricated awards / contracts / tenders.** No contractor name, no award
    amount, no contract number, no specific tender, and no date is ever emitted —
    only the identity of the venue and the procurement themes it publishes, plus
    an honest "live tenders / awards not fetched" gap.
  * **No network at report time.** The reference URL + theme description come from
    the code-defined ``EVENT_SOURCES`` table; nothing is fetched here.
  * **No API keys / secrets / tokenised URLs.** Every URL is a fixed public
    landing page with no query string. ``EvidenceItem`` strips any
    credential-bearing query param as a backstop anyway.
  * **Weak, needs-review, recommendation-free.** The reference text carries no
    rating / valuation / trading-signal language and no materiality claim, so it
    passes the report safety gate unchanged; a procurement reference must never
    read as a company recommendation or catalyst.

One generic ``EventReferenceConnector`` is parameterised by a small immutable
``EventSourceSpec`` so the registry can register one connector per event source
from a single source of truth (``EVENT_SOURCES``).

Task 1 of Phase 29D.1 only *establishes* this layer; wiring these references into
the discovery council / company report is Phase 29D.1 Task 2.
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
    ConnectorStatus,
    ProviderType,
)

# Follow-up phase that will (optionally) bind bounded live tender / award data.
_EVENT_FOLLOWUP_PHASE = "Phase 29D"

# The evidence source_type stamped on procurement / tender references. Uses
# "government_data" (accepted by the source schema's ``VALID_SOURCE_TYPES``) — NOT
# "government_contract", which would falsely imply a real, specific award.
_EVENT_SOURCE_TYPE = "government_data"


@dataclass(frozen=True)
class EventSourceSpec:
    """Immutable identity + coverage of one reference-only procurement venue.

    Carries no secret and no award — only *which official tender / award venue* it
    is, the fixed public landing page, and (as plain English) *which procurement
    themes* it publishes. ``theme_keywords`` are lower-case substrings matched
    against a query's theme. ``refresh_cadence_days`` is a FRESHNESS hint: how
    quickly the underlying venue turns over, used to stamp ``stale_after_days`` on
    the emitted reference. ``tier`` is the source's transport/content tier — T2 for
    a government procurement / spending publisher.
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


# The single source of truth for the procurement / tender event layer. The
# registry builds its enabled event rows AND its connectors from this table; the
# theme collector iterates it. Every URL is a fixed, public, token-free official
# landing page.
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


def event_spec_for(source_id: str) -> EventSourceSpec | None:
    """Return the procurement / tender event spec for ``source_id``, or None."""
    return next((s for s in EVENT_SOURCES if s.source_id == source_id), None)


class EventReferenceConnector(SourceConnector):
    """A reference-only procurement / tender EVENT connector for ONE venue.

    ``fetch_events`` emits a bounded T2 procurement *source reference* plus an
    honest "live tenders / awards not fetched" gap when the query theme / region
    is relevant; otherwise it returns an empty result (no evidence, no gap). It
    never fetches and never fabricates a tender, award, contractor, amount,
    contract number, or date. The reference is a WEAK internal research-priority
    signal, stamped ``needs_human_review`` and carrying no materiality claim.
    ``fetch_macro_context`` / ``fetch_filings`` / ``search_company`` are not an
    event path and return an honest not-eligible gap.
    """

    status = ConnectorStatus.enabled

    def __init__(self, spec: EventSourceSpec) -> None:
        self._spec = spec
        self.connector_key = spec.source_id
        self.supported_source_ids = (spec.source_id,)

    # -- Relevance ---------------------------------------------------------

    def covers(self, query: QueryContext) -> bool:
        """True when this venue is relevant to the query theme / region.

        A generic ask with no theme and no region is NOT answered — procurement /
        tender venues are always theme-specific, never a default reference.
        """
        theme = (query.query or "").strip().lower()
        region = (query.region or "").strip().lower()
        if theme and any(kw in theme for kw in self._spec.theme_keywords):
            return True
        if region and self._spec.region and self._spec.region.lower() in region:
            return True
        return False

    # -- Result builders ---------------------------------------------------

    def _reference_item(self) -> EvidenceItem:
        spec = self._spec
        excerpt = (
            f"{spec.display_name} publishes {spec.publishes}. This item is a "
            "source reference to that official procurement / tender venue only: "
            "no specific tender, award, contractor, contract value, contract "
            "number, or date is fetched or fabricated. It is a weak internal "
            "research-priority signal, not a materiality claim."
        )
        return build_evidence_item(
            id=f"EVENT_{spec.source_id.upper()}",
            source_id=spec.source_id,
            source_name=spec.display_name,
            provider_transport=(
                f"{spec.display_name} (official public procurement / spending venue)"
            ),
            provider_transport_tier=spec.tier,
            content_source=f"{spec.display_name} — procurement / tender venue catalog",
            content_source_tier=spec.tier,
            source_type=_EVENT_SOURCE_TYPE,
            title=f"{spec.display_name} — procurement / tender source reference",
            url=spec.url,
            excerpt=excerpt,
            data_quality="reference_only",
            confidence="low",
            stale_after_days=spec.refresh_cadence_days,
            provenance=[
                f"{spec.display_name} (official public procurement / spending venue)",
                "Procurement / tender source reference only — no specific "
                "tenders, awards, contractors, amounts, contract numbers, or "
                "dates fetched",
                "weak internal research-priority signal; verify against the live "
                "venue",
                "needs_human_review=true",
            ],
            warnings=[
                "Procurement / tender source reference only; live tenders and "
                "awards are not fetched at report time. Weak internal "
                "research-priority signal — not a materiality claim, not a trade "
                "signal. Human review required.",
            ],
        )

    def _tenders_gap(self) -> SourceGap:
        spec = self._spec
        return SourceGap(
            connector_key=self.connector_key,
            source_id=spec.source_id,
            gap_type=GapType.data_not_sourced,
            severity=GapSeverity.info,
            message=(
                f"{spec.display_name}: procurement / tender venue reference only; "
                "live tenders / awards not fetched at report time. Only a pointer "
                "to the venue and the procurement themes it covers is provided."
            ),
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
                f"{self._spec.display_name} is a procurement / tender EVENT venue "
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
            source_gaps=[self._tenders_gap()],
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
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=(
                f"Emits a {self._spec.tier} procurement / tender SOURCE REFERENCE "
                f"to {self._spec.display_name} (which tenders / awards it "
                "publishes) for a relevant theme/region; live tenders / awards are "
                f"not fetched at report time ({_EVENT_FOLLOWUP_PHASE} follow-up). "
                "Weak internal research-priority signal. No API key used."
            ),
        )


def build_event_connectors() -> dict[str, EventReferenceConnector]:
    """One reference-only connector per procurement / tender event source."""
    return {s.source_id: EventReferenceConnector(s) for s in EVENT_SOURCES}


__all__ = [
    "EventSourceSpec",
    "EVENT_SOURCES",
    "EventReferenceConnector",
    "event_spec_for",
    "build_event_connectors",
]
