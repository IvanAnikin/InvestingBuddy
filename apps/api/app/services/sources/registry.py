"""
Source registry — Phase 29A.

The single, code-defined catalogue of every source the platform knows about:
the handful that are wired and usable today, and the long tail of planned
external sources that future phases will connect. It is the backing store for
``GET /api/v1/sources/registry`` and ``GET /api/v1/sources/health``.

Design rules:
  * No secrets. A registry entry describes a source's *policy and identity*
    (tier, jurisdiction, cost model, rate-limit policy), never a credential.
    ``assert_registry_safe`` scans the serialised payload as a backstop.
  * "Enabled" means a connector is wired and the source is usable now.
    "Planned" means there is a placeholder and a target phase, but no
    implementation — it surfaces as a source gap, never as silent absence.
  * Health is deterministic and network-free (does a provider have the config it
    needs?), so this module is import-safe and offline-testable.
"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.log_redaction import SENSITIVE_QUERY_SUBSTRINGS
from app.services.sources.connector_base import ConnectorHealth, SourceConnector
from app.services.sources.connectors import (
    MACRO_SOURCES,
    CompanyIrConnector,
    DeutscheBoerseConnector,
    EuronextRegulatedConnector,
    MacroSourceSpec,
    NordicDisclosuresConnector,
    PlannedConnector,
    ScaffoldConnector,
    SecEdgarConnector,
    SixSwissConnector,
    UkFcaNsmConnector,
    WrappedProviderConnector,
    build_macro_connectors,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.rate_limit import RateLimitPolicy
from app.services.sources.taxonomy import (
    CANONICAL_TIERS,
    T1_PRIMARY_COMPANY_SOURCE,
    T2_REGULATOR_OR_GOV,
    T3_INDUSTRY_SPECIALIST,
    T4_QUALITY_MEDIA,
    T5_API_AGGREGATOR,
    AccessMode,
    ConnectorStatus,
    CostModel,
    ProviderType,
    SourceStatus,
    TierMeta,
)

# Phase labels used for planned connectors (documented in ROADMAP.md).
PHASE_29B = "Phase 29B"  # filing / regulator connectors
PHASE_29C = "Phase 29C"  # macro / commodity / policy connectors
PHASE_29D = "Phase 29D"  # event-trigger / patents / local press connectors


class RegisteredSource(BaseModel):
    """A flat, API-safe registry entry. Contains no secrets."""

    source_id: str
    name: str
    provider_type: ProviderType
    tier: str
    status: SourceStatus
    enabled: bool
    jurisdiction: str | None = None
    region: str | None = None
    language: str = "en"
    cost_model: CostModel = CostModel.unknown
    access_mode: AccessMode = AccessMode.unknown
    connector_key: str | None = None
    connector_implemented: bool = False
    planned_phase: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    rate_limit: str | None = None
    reliability_note: str | None = None


class SourceRegistry:
    """An in-memory registry of sources + their connectors."""

    def __init__(
        self,
        sources: list[RegisteredSource],
        connectors: dict[str, SourceConnector],
    ) -> None:
        self._sources = {s.source_id: s for s in sources}
        self._connectors = connectors

    # -- Queries ------------------------------------------------------------

    def all_sources(self) -> list[RegisteredSource]:
        return list(self._sources.values())

    def get(self, source_id: str) -> RegisteredSource | None:
        return self._sources.get(source_id)

    def enabled_sources(self) -> list[RegisteredSource]:
        return [s for s in self._sources.values() if s.status == SourceStatus.enabled]

    def scaffolded_sources(self) -> list[RegisteredSource]:
        return [
            s for s in self._sources.values() if s.status == SourceStatus.scaffolded
        ]

    def planned_sources(self) -> list[RegisteredSource]:
        return [s for s in self._sources.values() if s.status == SourceStatus.planned]

    def disabled_sources(self) -> list[RegisteredSource]:
        return [s for s in self._sources.values() if s.status == SourceStatus.disabled]

    def connectors(self) -> dict[str, SourceConnector]:
        return dict(self._connectors)

    def connector_for(self, source_id: str) -> SourceConnector | None:
        src = self._sources.get(source_id)
        if not src or not src.connector_key:
            return None
        return self._connectors.get(src.connector_key)

    # -- Health -------------------------------------------------------------

    def health(self) -> list[ConnectorHealth]:
        """Safe, network-free health for every distinct connector."""
        return [c.healthcheck() for c in self._connectors.values()]

    # -- Gaps ---------------------------------------------------------------

    def source_gaps(self) -> list[SourceGap]:
        """Normalized gaps for every scaffolded/planned/disabled source."""
        gaps: list[SourceGap] = []
        for s in self.scaffolded_sources():
            gaps.append(
                SourceGap(
                    source_id=s.source_id,
                    connector_key=s.connector_key,
                    gap_type=GapType.connector_scaffolded,
                    severity=GapSeverity.info,
                    message=(
                        f"{s.name} connector scaffold present; live fetch pending."
                    ),
                    suggested_followup_phase=s.planned_phase,
                    blocks_research_complete=False,
                )
            )
        for s in self.planned_sources():
            gaps.append(
                SourceGap(
                    source_id=s.source_id,
                    connector_key=s.connector_key,
                    gap_type=GapType.connector_planned,
                    severity=GapSeverity.info,
                    message=f"{s.name} connector is planned but not implemented yet.",
                    suggested_followup_phase=s.planned_phase,
                    blocks_research_complete=False,
                )
            )
        for s in self.disabled_sources():
            gaps.append(
                SourceGap(
                    source_id=s.source_id,
                    connector_key=s.connector_key,
                    gap_type=GapType.connector_disabled,
                    severity=GapSeverity.low,
                    message=f"{s.name} connector is implemented but disabled.",
                    blocks_research_complete=False,
                )
            )
        return gaps

    def summary(self) -> dict[str, int]:
        # ``configured`` is a connector-health property, not a source lifecycle
        # state — count connectors whose health reports credentials present.
        configured = sum(
            1
            for c in self._connectors.values()
            if c.healthcheck().status == ConnectorStatus.configured
        )
        return {
            "enabled": len(self.enabled_sources()),
            "configured": configured,
            "scaffolded": len(self.scaffolded_sources()),
            "planned": len(self.planned_sources()),
            "disabled": len(self.disabled_sources()),
            "total": len(self._sources),
        }


# ---------------------------------------------------------------------------
# Default registry definition
# ---------------------------------------------------------------------------


def _planned(
    *,
    source_id: str,
    name: str,
    provider_type: ProviderType,
    tier: str,
    phase: str,
    jurisdiction: str | None = None,
    region: str | None = None,
    language: str = "en",
    cost_model: CostModel = CostModel.free,
    access_mode: AccessMode = AccessMode.rest_api,
    capabilities: list[str] | None = None,
    reliability_note: str | None = None,
) -> RegisteredSource:
    return RegisteredSource(
        source_id=source_id,
        name=name,
        provider_type=provider_type,
        tier=tier,
        status=SourceStatus.planned,
        enabled=False,
        jurisdiction=jurisdiction,
        region=region,
        language=language,
        cost_model=cost_model,
        access_mode=access_mode,
        connector_key=source_id,
        connector_implemented=False,
        planned_phase=phase,
        capabilities=capabilities or [],
        reliability_note=reliability_note,
    )


def _scaffolded(
    *,
    source_id: str,
    name: str,
    provider_type: ProviderType,
    tier: str,
    phase: str,
    capabilities: list[str],
    jurisdiction: str | None = None,
    region: str | None = None,
    cost_model: CostModel = CostModel.free,
    access_mode: AccessMode = AccessMode.web_scrape,
    reliability_note: str | None = None,
) -> RegisteredSource:
    """A scaffolded source: connector class exists, returns honest gaps only."""
    return RegisteredSource(
        source_id=source_id,
        name=name,
        provider_type=provider_type,
        tier=tier,
        status=SourceStatus.scaffolded,
        enabled=False,
        jurisdiction=jurisdiction,
        region=region,
        cost_model=cost_model,
        access_mode=access_mode,
        connector_key=source_id,
        connector_implemented=True,
        planned_phase=phase,
        capabilities=capabilities,
        reliability_note=reliability_note
        or "Scaffolded — no live fetch yet; produces honest gaps, never evidence.",
    )


def _macro_reference_source(spec: MacroSourceSpec) -> RegisteredSource:
    """An enabled reference-only macro source (Phase 29C.1).

    Built from the single ``MACRO_SOURCES`` table so the registry and the
    connectors never drift. It is a T2 macro reference: the connector emits a
    bounded SOURCE REFERENCE (which official dataset covers which indicators)
    plus an honest gap; live figures are not fetched at report time.
    """
    return RegisteredSource(
        source_id=spec.source_id,
        name=spec.display_name,
        provider_type=spec.provider,
        tier=T2_REGULATOR_OR_GOV,
        status=SourceStatus.enabled,
        enabled=True,
        jurisdiction=spec.jurisdiction,
        region=spec.region,
        cost_model=CostModel.free,
        access_mode=AccessMode.rest_api,
        connector_key=spec.source_id,
        connector_implemented=True,
        planned_phase=PHASE_29C,
        capabilities=["fetch_macro_context"],
        reliability_note=spec.reliability_note,
    )


# Phase 29B filing / regulator scaffolds. Columns:
#   (source_id, name, provider_type, phase, jurisdiction, region, note)
# Note: uk_fca_nsm (29B.4A), euronext_regulated_info (29B.4B) and
# deutsche_boerse / nordic_disclosures (29B.4C) are no longer generic scaffolds —
# they were promoted to dedicated connectors that emit a T2 regulator-transport
# source reference (see the enabled sources below). These two remain honest
# scaffolds.
_SCAFFOLD_TABLE: list[tuple[str, str, str | None, str | None, str | None]] = [
    ("sedar_plus", "SEDAR+ (Canada)", "CA", "North America",
     "Canadian issuer filings; no fabricated filings."),
    ("asx_announcements", "ASX Announcements", "AU", "Oceania",
     "ASX company announcements; no fabricated JORC / Appendix 5B data."),
]


def build_registry(cfg: Settings | None = None) -> SourceRegistry:
    """Construct the default registry from current settings (network-free)."""
    cfg = cfg or default_settings
    eodhd_configured = bool(cfg.eodhd_api_key)

    sec_rl = RateLimitPolicy(requests_per_minute=30, min_interval_seconds=0.2)
    gleif_rl = RateLimitPolicy(requests_per_minute=60)
    price_rl = RateLimitPolicy(requests_per_minute=60)

    # -- Enabled, migrated sources -----------------------------------------
    enabled: list[RegisteredSource] = [
        RegisteredSource(
            source_id="sec_edgar",
            name="SEC EDGAR",
            provider_type=ProviderType.primary_filing,
            tier=T2_REGULATOR_OR_GOV,
            status=SourceStatus.enabled,
            enabled=True,
            jurisdiction="US",
            region="North America",
            cost_model=CostModel.free,
            access_mode=AccessMode.rest_api,
            connector_key="sec_edgar",
            connector_implemented=True,
            capabilities=["fetch_filings", "fetch_events"],
            rate_limit=sec_rl.describe(),
            reliability_note=(
                "Transport tier T2 (regulator); filing content retrieved through "
                "it is T1_primary_filing."
            ),
        ),
        RegisteredSource(
            source_id="company_ir",
            name="Company IR / Newsroom (press releases)",
            provider_type=ProviderType.company_source,
            tier=T1_PRIMARY_COMPANY_SOURCE,
            status=SourceStatus.enabled,
            enabled=True,
            cost_model=CostModel.free,
            access_mode=AccessMode.rss_atom,
            connector_key="company_ir",
            connector_implemented=True,
            capabilities=["fetch_events", "search_company"],
            reliability_note="Issuer's own primary material (verified-issuer allowlist).",
        ),
        RegisteredSource(
            source_id="uk_fca_nsm",
            name="UK FCA National Storage Mechanism",
            provider_type=ProviderType.regulator,
            tier=T2_REGULATOR_OR_GOV,
            status=SourceStatus.enabled,
            enabled=True,
            jurisdiction="GB",
            region="Europe",
            cost_model=CostModel.free,
            access_mode=AccessMode.web_scrape,
            connector_key="uk_fca_nsm",
            connector_implemented=True,
            capabilities=["fetch_filings", "fetch_events"],
            reliability_note=(
                "Emits a T2 regulator-transport SOURCE REFERENCE to a verified "
                "UK issuer's FCA NSM / RNS disclosure venue (metadata only). The "
                "T1 primary filing CONTENT is not fetched at report time — live "
                "content retrieval is a Phase 29B.4 follow-up. No fabricated "
                "filings, notices, or RNS numbers."
            ),
        ),
        RegisteredSource(
            source_id="euronext_regulated_info",
            name="Euronext Regulated Information",
            provider_type=ProviderType.regulator,
            tier=T2_REGULATOR_OR_GOV,
            status=SourceStatus.enabled,
            enabled=True,
            region="Europe",
            language="mixed",
            cost_model=CostModel.free,
            access_mode=AccessMode.web_scrape,
            connector_key="euronext_regulated_info",
            connector_implemented=True,
            capabilities=["fetch_filings", "fetch_events"],
            reliability_note=(
                "Emits a T2 regulator-transport SOURCE REFERENCE to a verified "
                "Euronext Paris (FR) / Amsterdam (NL) issuer's regulated-disclosure "
                "venue (Euronext Regulated Information + AMF/AFM; metadata only). "
                "The T1 primary filing CONTENT is not fetched at report time — live "
                "content retrieval is a Phase 29B.4 follow-up. French docs require "
                "translation (Phase 30). No fabricated filings, notices, or dates."
            ),
        ),
        RegisteredSource(
            source_id="deutsche_boerse",
            name="Deutsche Börse / Bundesanzeiger Disclosures",
            provider_type=ProviderType.regulator,
            tier=T2_REGULATOR_OR_GOV,
            status=SourceStatus.enabled,
            enabled=True,
            jurisdiction="DE",
            region="Europe",
            language="de",
            cost_model=CostModel.free,
            access_mode=AccessMode.web_scrape,
            connector_key="deutsche_boerse",
            connector_implemented=True,
            capabilities=["fetch_filings", "fetch_events"],
            reliability_note=(
                "Emits a T2 regulator-transport SOURCE REFERENCE to a verified "
                "German (Xetra / Frankfurt) issuer's regulated-disclosure venue "
                "(Deutsche Börse + Bundesanzeiger / BaFin; metadata only). The T1 "
                "primary filing CONTENT is not fetched at report time — live content "
                "retrieval is a Phase 29B.4 follow-up. German docs require "
                "translation (Phase 30). No fabricated filings, notices, or dates."
            ),
        ),
        RegisteredSource(
            source_id="nordic_disclosures",
            name="Nasdaq Nordic Disclosures",
            provider_type=ProviderType.regulator,
            tier=T2_REGULATOR_OR_GOV,
            status=SourceStatus.enabled,
            enabled=True,
            region="Europe",
            language="mixed",
            cost_model=CostModel.free,
            access_mode=AccessMode.web_scrape,
            connector_key="nordic_disclosures",
            connector_implemented=True,
            capabilities=["fetch_filings", "fetch_events"],
            reliability_note=(
                "Emits a T2 regulator-transport SOURCE REFERENCE to a verified "
                "Nasdaq Nordic (Copenhagen / Stockholm / Helsinki / Oslo) issuer's "
                "regulated-disclosure venue (Nasdaq Nordic + national FSA, e.g. "
                "Finanstilsynet; metadata only). The T1 primary filing CONTENT is "
                "not fetched at report time — live content retrieval is a Phase "
                "29B.4 follow-up. Local-language docs require translation (Phase 30). "
                "No fabricated filings, notices, or dates."
            ),
        ),
        RegisteredSource(
            source_id="six_swiss",
            name="SIX Swiss Exchange Regulatory Disclosures",
            provider_type=ProviderType.regulator,
            tier=T2_REGULATOR_OR_GOV,
            status=SourceStatus.enabled,
            enabled=True,
            jurisdiction="CH",
            region="Europe",
            language="mixed",
            cost_model=CostModel.free,
            access_mode=AccessMode.web_scrape,
            connector_key="six_swiss",
            connector_implemented=True,
            capabilities=["fetch_filings", "fetch_events"],
            reliability_note=(
                "Emits a T2 regulator-transport SOURCE REFERENCE to a verified Swiss "
                "(SIX Swiss Exchange) issuer's regulated-disclosure venue (SIX "
                "Exchange Regulation; metadata only). The T1 primary filing CONTENT "
                "is not fetched at report time — live content retrieval is a Phase "
                "29B.4 follow-up. No translation is asserted (Swiss majors publish "
                "English reports); original filings may be in a Swiss national "
                "language. No fabricated filings, notices, or dates."
            ),
        ),
        RegisteredSource(
            source_id="gleif",
            name="GLEIF (Legal Entity Identifier)",
            provider_type=ProviderType.identity,
            tier=T2_REGULATOR_OR_GOV,
            status=SourceStatus.enabled,
            enabled=True,
            jurisdiction="Global",
            cost_model=CostModel.free,
            access_mode=AccessMode.rest_api,
            connector_key="gleif",
            connector_implemented=True,
            capabilities=["search_company"],
            rate_limit=gleif_rl.describe(),
            reliability_note="Identity enrichment only (LEI); name-guarded.",
        ),
        RegisteredSource(
            source_id="eodhd",
            name="EODHD (price / market data)",
            provider_type=ProviderType.price_aggregator,
            tier=T5_API_AGGREGATOR,
            status=SourceStatus.enabled,
            enabled=True,
            cost_model=CostModel.freemium,
            access_mode=AccessMode.rest_api,
            connector_key="eodhd",
            connector_implemented=True,
            capabilities=["search_company"],
            rate_limit=price_rl.describe(),
            reliability_note=(
                "Aggregator (T5); credentials required. "
                + ("Configured." if eodhd_configured else "Not configured — idle.")
            ),
        ),
        RegisteredSource(
            source_id="stooq",
            name="Stooq (price history)",
            provider_type=ProviderType.price_aggregator,
            tier=T5_API_AGGREGATOR,
            status=SourceStatus.enabled,
            enabled=True,
            cost_model=CostModel.free,
            access_mode=AccessMode.bulk_download,
            connector_key="stooq",
            connector_implemented=True,
            capabilities=["search_company"],
            reliability_note="Free price aggregator (T5); no credentials.",
        ),
        RegisteredSource(
            source_id="gdelt",
            name="GDELT (news aggregator)",
            provider_type=ProviderType.news,
            tier=T5_API_AGGREGATOR,
            status=SourceStatus.enabled,
            enabled=True,
            cost_model=CostModel.free,
            access_mode=AccessMode.rest_api,
            connector_key="gdelt",
            connector_implemented=True,
            capabilities=["fetch_events", "search_company"],
            reliability_note=(
                "Aggregator (T5); individual articles resolve to their outlet's "
                "media tier (typically T4)."
            ),
        ),
    ]

    # -- Scaffolded filing / regulator connectors (Phase 29B) --------------
    # Connector classes exist and are wired; they return honest gaps, never
    # fabricated filings. Live fetch is a Phase 29B.x follow-up.
    _FILINGS_EVENTS = ["fetch_filings", "fetch_events"]
    scaffolded: list[RegisteredSource] = [
        _scaffolded(
            source_id=sid,
            name=nm,
            provider_type=ProviderType.regulator,
            tier=T2_REGULATOR_OR_GOV,
            phase=PHASE_29B,
            capabilities=_FILINGS_EVENTS,
            jurisdiction=juris,
            region=region,
            reliability_note=note,
        )
        for sid, nm, juris, region, note in _SCAFFOLD_TABLE
    ]

    # -- Planned placeholders (disabled by default) ------------------------
    # A compact table keeps the long tail readable. Columns:
    #   (source_id, name, provider_type, tier, phase, extra_kwargs)
    _MACRO = ["fetch_macro_context"]
    _SEARCH = ["search_company"]
    com = ProviderType.commodity
    trd = ProviderType.trade_policy
    proc = ProviderType.procurement
    pat = ProviderType.patents
    t2, t3, t5 = T2_REGULATOR_OR_GOV, T3_INDUSTRY_SPECIALIST, T5_API_AGGREGATOR
    planned_table: list[tuple[str, str, ProviderType, str, str, dict]] = [
        # Macro / commodity / policy (Phase 29C). NOTE: fred, imf, eurostat,
        # world_bank_pink_sheet and national_stats_central_banks were promoted to
        # enabled reference-only macro sources (Phase 29C.1, see MACRO_SOURCES);
        # the remaining commodity / trade / procurement venues stay planned.
        ("usgs", "USGS Mineral Commodity Summaries", com, t3, PHASE_29C,
         {"jurisdiction": "US", "capabilities": _MACRO}),
        ("iea", "IEA (International Energy Agency)", com, t3, PHASE_29C,
         {"capabilities": _MACRO}),
        ("irena", "IRENA (Renewable Energy)", com, t3, PHASE_29C,
         {"capabilities": _MACRO}),
        ("eia", "US EIA (Energy Information Administration)", com, t2, PHASE_29C,
         {"jurisdiction": "US", "capabilities": _MACRO}),
        ("entsoe", "ENTSO-E Transparency Platform", com, t3, PHASE_29C,
         {"region": "Europe", "capabilities": _MACRO}),
        ("ustr_taric", "USTR / EU TARIC (tariffs)", trd, t2, PHASE_29C,
         {"capabilities": _MACRO}),
        ("usaspending", "USAspending.gov", proc, t2, PHASE_29C,
         {"jurisdiction": "US", "capabilities": ["fetch_events", "fetch_macro_context"]}),
        ("eu_ted", "EU TED (Tenders Electronic Daily)", proc, t2, PHASE_29C,
         {"region": "Europe", "capabilities": ["fetch_events"]}),
        ("un_comtrade", "UN Comtrade", trd, t2, PHASE_29C, {"capabilities": _MACRO}),
        ("openbb", "OpenBB Platform", ProviderType.aggregator_toolkit, t5, PHASE_29C,
         {"cost_model": CostModel.freemium, "access_mode": AccessMode.sdk,
          "capabilities": ["search_company", "fetch_macro_context"]}),
        # Event-trigger / patents / local press (Phase 29D)
        ("google_patents", "Google Patents", pat, t5, PHASE_29D, {"capabilities": _SEARCH}),
        ("uspto", "USPTO (PatentsView)", pat, t2, PHASE_29D,
         {"jurisdiction": "US", "capabilities": _SEARCH}),
        ("epo_espacenet", "EPO Espacenet", pat, t2, PHASE_29D,
         {"region": "Europe", "capabilities": _SEARCH}),
        ("local_language_business_press", "Local-language business press",
         ProviderType.news, T4_QUALITY_MEDIA, PHASE_29D,
         {"language": "mixed", "cost_model": CostModel.freemium,
          "access_mode": AccessMode.web_scrape,
          "capabilities": ["fetch_events", "search_company"],
          "reliability_note": (
              "Non-English coverage; requires the translation agent planned for "
              "Phase 30 before ingestion."
          )}),
    ]
    planned: list[RegisteredSource] = [
        _planned(source_id=sid, name=nm, provider_type=pt, tier=tr, phase=ph, **extra)
        for sid, nm, pt, tr, ph, extra in planned_table
    ]

    # -- Enabled reference-only macro sources (Phase 29C.1) ----------------
    # Reference-only: a bounded T2 macro SOURCE REFERENCE + honest gap, no live
    # figures, no network at report time, no API key. Built from MACRO_SOURCES.
    macro_enabled: list[RegisteredSource] = [
        _macro_reference_source(spec) for spec in MACRO_SOURCES
    ]

    sources = enabled + macro_enabled + scaffolded + planned

    # -- Connectors ---------------------------------------------------------
    connectors: dict[str, SourceConnector] = {
        "sec_edgar": SecEdgarConnector(),
        "company_ir": CompanyIrConnector(),
        "uk_fca_nsm": UkFcaNsmConnector(),
        "euronext_regulated_info": EuronextRegulatedConnector(),
        "deutsche_boerse": DeutscheBoerseConnector(),
        "nordic_disclosures": NordicDisclosuresConnector(),
        "six_swiss": SixSwissConnector(),
        "gleif": WrappedProviderConnector(
            connector_key="gleif",
            source_ids=("gleif",),
            configured=True,
            rate_limit_policy=gleif_rl,
        ),
        "eodhd": WrappedProviderConnector(
            connector_key="eodhd",
            source_ids=("eodhd",),
            configured=eodhd_configured,
            needs_credentials=True,
            rate_limit_policy=price_rl,
        ),
        "stooq": WrappedProviderConnector(
            connector_key="stooq",
            source_ids=("stooq",),
            configured=True,
        ),
        "gdelt": WrappedProviderConnector(
            connector_key="gdelt",
            source_ids=("gdelt",),
            configured=True,
        ),
    }
    # Reference-only macro connectors (Phase 29C.1), one per MACRO_SOURCES spec.
    connectors.update(build_macro_connectors())
    for s in scaffolded:
        note = next((n for sid, _, _, _, n in _SCAFFOLD_TABLE if sid == s.source_id), None)
        connectors[s.source_id] = ScaffoldConnector(
            connector_key=s.source_id,
            source_ids=(s.source_id,),
            display_name=s.name,
            planned_phase=s.planned_phase,
            note=note,
        )
    for s in planned:
        connectors[s.source_id] = PlannedConnector(
            connector_key=s.source_id,
            source_ids=(s.source_id,),
            planned_phase=s.planned_phase,
        )

    return SourceRegistry(sources, connectors)


def tier_legend() -> list[TierMeta]:
    """The canonical tier list for the registry API (safe, static)."""
    return list(CANONICAL_TIERS)


def assert_registry_safe(registry: SourceRegistry) -> None:
    """Backstop: fail loudly if any registry value looks like a credential.

    Serialises the whole registry + health payload and scans for token-bearing
    ``key=value`` residue and sensitive key substrings. This should be
    impossible by construction (no entry stores a secret), but the guard makes a
    future mistake a test failure rather than a leak.
    """
    payload = {
        "sources": [s.model_dump(mode="json") for s in registry.all_sources()],
        "health": [h.model_dump(mode="json") for h in registry.health()],
        "gaps": [g.model_dump(mode="json") for g in registry.source_gaps()],
    }
    blob = json.dumps(payload).lower()
    for token in SENSITIVE_QUERY_SUBSTRINGS:
        needle = f"{token}="
        if needle in blob:
            raise AssertionError(f"Registry payload contains a secret-like token: {needle}")
    for banned in ("bearer ", "basic ", "authorization:", "api_token", "postgresql://"):
        if banned in blob:
            raise AssertionError(f"Registry payload contains sensitive content: {banned!r}")


def registry_gap_messages(registry: SourceRegistry, *, limit: int = 6) -> list[str]:
    """Bounded, safety-clean known-gap strings for evidence packs.

    Summarised (not one-per-source) so the evidence pack stays small. Every
    message is recommendation-free and contains no rating / price-target
    vocabulary, so it passes the report safety gate unchanged.
    """
    scaffolded = registry.scaffolded_sources()
    planned = registry.planned_sources()
    if not scaffolded and not planned:
        return []
    messages: list[str] = []
    if scaffolded:
        names = ", ".join(s.name for s in scaffolded[:3])
        messages.append(
            f"{len(scaffolded)} regulated-disclosure connectors are scaffolded "
            f"(e.g. {names}); their live fetch is pending, so non-US primary "
            "filings are not yet sourced for those venues."
        )
    if planned:
        names = ", ".join(s.name for s in planned[:3])
        messages.append(
            f"{len(planned)} further external source connectors are planned but "
            f"not implemented yet (e.g. {names}); their evidence is not yet sourced."
        )
    messages.append(
        "Local-language sources require the translation agent planned for Phase "
        "30 before ingestion."
    )
    return messages[:limit]


__all__ = [
    "RegisteredSource",
    "SourceRegistry",
    "build_registry",
    "tier_legend",
    "assert_registry_safe",
    "registry_gap_messages",
]
