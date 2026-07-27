"""
Macro reference connectors — Phase 29C.1 (macro) + 29C.2 (commodity / energy)
+ 29C.3 (policy / government).

Establishes the MACRO evidence category as a set of **reference-only** sources.
Mirroring the 29B.4 regulator-reference connectors, a macro connector is
network-free at report time and fabricates nothing: for a relevant theme /
region it emits ONE bounded **T2/T3 macro SOURCE REFERENCE** — a pointer to a
fixed, public, token-free official dataset landing page plus a short description
of *which indicators that source covers* — and an explicit honest ``SourceGap``
recording that the live figures / release dates were NOT fetched.

Phase 29C.2 extends the table with COMMODITY + ENERGY reference sources (USGS,
IEA, IRENA, US EIA, ENTSO-E) driven by the *same* generic connector class. They
differ only in tier (T3 industry-specialist for the specialist agencies, T2 for
the US EIA) and in the commodity / energy themes they cover; they carry no
tonnage, price, capacity, production, or reserve figure — only the dataset
identity and an honest "figures not fetched" gap.

Phase 29C.3 extends the table again with POLICY + GOVERNMENT reference sources
(USTR / EU TARIC, UN Comtrade, NATO defence expenditure, SIPRI military
expenditure, OECD) for the campaign's policy themes — defense / NATO spending,
tariffs, subsidies, industrial policy, grid investment and energy transition.
They are driven by the *same* generic connector class and carry the same hard
guarantees: policy / government is thematic CONTEXT only, never a company
recommendation, catalyst, or trading signal on geopolitics. They emit no defence
budget, spending percentage, tariff rate, subsidy amount, or date — only the
official reference identity and an honest "figures not fetched" gap. Procurement
/ tender EVENT venues (EU TED, USAspending) and patents stay PLANNED (Phase 29D).

Hard guarantees:
  * **No fabricated macro data.** No numeric value, no index level, no release
    date, no forecast is ever emitted — only the identity of the dataset and the
    indicators it publishes, plus an honest "figures not fetched" gap.
  * **No network at report time.** The reference URL + indicator description come
    from the code-defined ``MACRO_SOURCES`` table; nothing is fetched here.
  * **No API keys / secrets / tokenised URLs.** FRED-style API keys are
    deliberately not introduced; every URL is a fixed public landing page with no
    query string. ``EvidenceItem`` strips any credential-bearing query param as a
    backstop anyway.
  * **Recommendation-free.** The reference text carries no rating / valuation /
    trading-signal language, so it passes the report safety gate unchanged; a
    macro reference must never read as a company recommendation.

One generic ``MacroReferenceConnector`` is parameterised by a small immutable
``MacroSourceSpec`` so the registry can register one connector per macro source
from a single source of truth (``MACRO_SOURCES``).
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
    T3_INDUSTRY_SPECIALIST,
    ConnectorStatus,
    ProviderType,
)

# Follow-up phase that will (optionally) bind bounded live macro figures.
_MACRO_FOLLOWUP_PHASE = "Phase 29C"

# The evidence source_type stamped on macro references (both are accepted by the
# source schema's ``VALID_SOURCE_TYPES``).
_MACRO_SOURCE_TYPE = "macro_report"


@dataclass(frozen=True)
class MacroSourceSpec:
    """Immutable identity + coverage of one reference-only macro source.

    Carries no secret and no figure — only *which official dataset* it is, the
    fixed public landing page, and (as plain English) *which indicators / themes*
    it publishes. ``theme_keywords`` are lower-case substrings matched against a
    query's theme; ``broad_macro`` marks a source that answers a generic macro
    ask even with no explicit theme. ``tier`` is the source's transport/content
    tier — T2 for a regulator/government publisher (e.g. FRED, US EIA), T3 for an
    industry-specialist agency (e.g. USGS, IEA, IRENA, ENTSO-E).
    """

    source_id: str
    display_name: str
    url: str
    provider: ProviderType
    jurisdiction: str | None
    region: str | None
    broad_macro: bool
    indicators: str
    theme_keywords: tuple[str, ...]
    reliability_note: str
    tier: str = T2_REGULATOR_OR_GOV


# The single source of truth for the macro reference layer. The registry builds
# its enabled macro rows AND its connectors from this table; the theme collector
# iterates it. Every URL is a fixed, public, token-free official landing page.
MACRO_SOURCES: tuple[MacroSourceSpec, ...] = (
    MacroSourceSpec(
        source_id="fred",
        display_name="FRED (Federal Reserve Bank of St. Louis)",
        url="https://fred.stlouisfed.org/",
        provider=ProviderType.macro_statistics,
        jurisdiction="US",
        region="North America",
        broad_macro=True,
        indicators=(
            "US and selected global macro indicator series — consumer price "
            "inflation (CPI, PCE), policy and market interest rates, exchange "
            "rates, GDP, industrial production and employment"
        ),
        theme_keywords=(
            "inflation", "cpi", "pce", "interest rate", "interest rates",
            "rates", "yield", "fx", "exchange rate", "gdp", "growth",
            "industrial production", "unemployment", "employment", "labor",
            "macro", "monetary", "recession", "money supply",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. Federal Reserve (St. Louis Fed) catalog of US / "
            "global macro indicator series — no figures or release dates emitted."
        ),
    ),
    MacroSourceSpec(
        source_id="imf",
        display_name="IMF Data (World Economic Outlook)",
        url="https://www.imf.org/en/Data",
        provider=ProviderType.macro_statistics,
        jurisdiction=None,
        region=None,
        broad_macro=True,
        indicators=(
            "Global macroeconomic aggregates — GDP growth, consumer price "
            "inflation, current-account and fiscal balances and commodity price "
            "index series — from the IMF World Economic Outlook and related "
            "databases"
        ),
        theme_keywords=(
            "inflation", "gdp", "growth", "macro", "current account",
            "fiscal", "commodity", "commodities", "global economy", "imf",
            "weo", "emerging market", "sovereign",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. IMF World Economic Outlook catalog of global "
            "macro aggregates — no figures or forecast values emitted."
        ),
    ),
    MacroSourceSpec(
        source_id="eurostat",
        display_name="Eurostat",
        url="https://ec.europa.eu/eurostat",
        provider=ProviderType.macro_statistics,
        jurisdiction=None,
        region="Europe",
        broad_macro=True,
        indicators=(
            "European Union macro and industry statistics — HICP inflation, "
            "GDP, industrial production, unemployment and external trade"
        ),
        theme_keywords=(
            "inflation", "hicp", "gdp", "growth", "industrial production",
            "unemployment", "employment", "macro", "europe", "eu",
            "euro area", "trade", "eurozone",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. Eurostat catalog of EU macro / industry statistics "
            "— no figures or release dates emitted."
        ),
    ),
    MacroSourceSpec(
        source_id="world_bank_pink_sheet",
        display_name="World Bank Commodity Markets (Pink Sheet)",
        url="https://www.worldbank.org/en/research/commodity-markets",
        provider=ProviderType.commodity,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "Global commodity price benchmark series — energy (crude oil, "
            "natural gas, coal), metals and minerals (copper, aluminum, nickel, "
            "zinc, iron ore, lead, tin) and agricultural commodities"
        ),
        theme_keywords=(
            "commodity", "commodities", "copper", "aluminum", "aluminium",
            "nickel", "zinc", "iron ore", "lead", "tin", "gold", "silver",
            "metal", "metals", "mining", "oil", "crude", "brent", "natural gas",
            "gas", "coal", "energy", "agriculture", "grain", "wheat",
            "fertilizer", "food",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. World Bank commodity price 'Pink Sheet' catalog — "
            "no price levels or dates emitted."
        ),
    ),
    MacroSourceSpec(
        source_id="national_stats_central_banks",
        display_name="National statistics offices / central banks",
        url="https://www.bis.org/cbanks.htm",
        provider=ProviderType.macro_statistics,
        jurisdiction=None,
        region=None,
        broad_macro=True,
        indicators=(
            "Country-level macro series published by national statistics "
            "offices and central banks — CPI inflation, GDP, industrial "
            "production, employment, policy rates and trade balances"
        ),
        theme_keywords=(
            "inflation", "cpi", "interest rate", "interest rates", "rates",
            "policy rate", "gdp", "growth", "industrial production",
            "unemployment", "employment", "macro", "central bank",
            "monetary", "trade balance", "current account",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. Pointer to national statistics offices / central "
            "banks (via the BIS central-bank hub) — no figures emitted."
        ),
    ),
)


# Phase 29C.2 — COMMODITY + ENERGY reference sources. Same generic connector,
# same guarantees (reference-only, network-free, no figures/dates, no API key);
# they differ only in tier and in the commodity / energy themes they cover. Every
# URL is a fixed, public, token-free official landing page.
COMMODITY_ENERGY_SOURCES: tuple[MacroSourceSpec, ...] = (
    MacroSourceSpec(
        source_id="usgs",
        display_name="USGS Mineral Commodity Summaries",
        url=(
            "https://www.usgs.gov/centers/national-minerals-information-center/"
            "mineral-commodity-summaries"
        ),
        provider=ProviderType.commodity,
        jurisdiction="US",
        region="North America",
        broad_macro=False,
        indicators=(
            "US and global mineral commodity supply statistics — production, "
            "reserves and net import reliance for critical minerals and metals "
            "including copper, lithium, cobalt, nickel, rare earths and uranium"
        ),
        theme_keywords=(
            "copper", "lithium", "rare earth", "rare-earth", "critical mineral",
            "critical minerals", "critical metal", "critical metals", "cobalt",
            "nickel", "mining", "uranium",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. USGS National Minerals Information Center Mineral "
            "Commodity Summaries catalog of mineral supply statistics — no "
            "tonnage, reserves, or production figures emitted."
        ),
        tier=T3_INDUSTRY_SPECIALIST,
    ),
    MacroSourceSpec(
        source_id="eia",
        display_name="US EIA (Energy Information Administration)",
        url="https://www.eia.gov/",
        provider=ProviderType.commodity,
        jurisdiction="US",
        region="North America",
        broad_macro=False,
        indicators=(
            "US and international energy statistics — crude oil, natural gas, "
            "coal, nuclear and uranium, electricity generation and power-sector "
            "data"
        ),
        theme_keywords=(
            "uranium", "nuclear", "oil", "crude", "natural gas", "gas",
            "energy", "electricity", "power",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. US EIA (Energy Information Administration) catalog "
            "of US / international energy statistics — no price, production, or "
            "generation figures emitted."
        ),
        tier=T2_REGULATOR_OR_GOV,
    ),
    MacroSourceSpec(
        source_id="iea",
        display_name="IEA (International Energy Agency)",
        url="https://www.iea.org/",
        provider=ProviderType.commodity,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "Global energy statistics and analysis — electricity demand, power "
            "generation, nuclear and renewables capacity trends, power grids and "
            "energy-transition indicators"
        ),
        theme_keywords=(
            "energy", "electricity", "nuclear", "renewable", "renewables",
            "power grid", "power", "grid", "energy transition",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. IEA (International Energy Agency) catalog of global "
            "energy statistics — no demand, generation, or capacity figures "
            "emitted."
        ),
        tier=T3_INDUSTRY_SPECIALIST,
    ),
    MacroSourceSpec(
        source_id="irena",
        display_name="IRENA (Renewable Energy)",
        url="https://www.irena.org/",
        provider=ProviderType.commodity,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "Global renewable-energy statistics — installed capacity and "
            "generation trends for solar, wind, hydrogen and other renewable "
            "sources, and energy-transition indicators"
        ),
        theme_keywords=(
            "renewable", "renewables", "solar", "wind", "energy transition",
            "hydrogen",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. IRENA renewable-energy statistics catalog — no "
            "capacity or generation figures emitted."
        ),
        tier=T3_INDUSTRY_SPECIALIST,
    ),
    MacroSourceSpec(
        source_id="entsoe",
        display_name="ENTSO-E Transparency Platform",
        url="https://transparency.entsoe.eu/",
        provider=ProviderType.commodity,
        jurisdiction=None,
        region="Europe",
        broad_macro=False,
        indicators=(
            "European electricity transmission-system statistics — power "
            "generation, cross-border flows, load and grid transparency data for "
            "the ENTSO-E area"
        ),
        theme_keywords=(
            "power grid", "electricity", "grid", "transmission", "power",
        ),
        reliability_note=(
            "Macro reference only; live figures not fetched at report time; "
            "29C follow-up. ENTSO-E Transparency Platform catalog of European "
            "electricity / grid statistics — no generation, load, or flow "
            "figures emitted."
        ),
        tier=T3_INDUSTRY_SPECIALIST,
    ),
)


# Phase 29C.3 — POLICY + GOVERNMENT reference sources. Same generic connector,
# same guarantees (reference-only, network-free, no figures/rates/amounts/dates,
# no API key). They cover the campaign's policy themes (tariffs / trade policy,
# defense / NATO spending, subsidies / industrial policy, energy transition /
# grid investment) as thematic CONTEXT only — never a company recommendation,
# catalyst, or geopolitical trading signal. ``provider`` uses an existing
# ``ProviderType`` member (there is no dedicated government_data type): the
# trade / tariff / policy sources map to ``trade_policy`` and the OECD statistics
# hub to ``macro_statistics``. Every URL is a fixed, public, token-free official
# landing page. Procurement / tender EVENT venues and patents stay PLANNED (29D).
POLICY_GOVERNMENT_SOURCES: tuple[MacroSourceSpec, ...] = (
    MacroSourceSpec(
        source_id="ustr_taric",
        display_name="USTR / EU TARIC (tariffs)",
        url="https://ustr.gov/",
        provider=ProviderType.trade_policy,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "US and EU tariff and trade-policy references — US Trade "
            "Representative tariff actions and the EU TARIC integrated tariff "
            "schedule covering customs duties, tariff classifications and "
            "import / export measures"
        ),
        theme_keywords=(
            "tariff", "tariffs", "trade policy", "trade", "customs",
            "import", "export", "duty", "duties", "trade barrier",
            "trade barriers",
        ),
        reliability_note=(
            "Policy / government reference only; live figures not fetched at "
            "report time; 29C follow-up. USTR / EU TARIC tariff and "
            "trade-policy landing pages — thematic context only, no tariff "
            "rates, duty percentages, or dates emitted."
        ),
        tier=T2_REGULATOR_OR_GOV,
    ),
    MacroSourceSpec(
        source_id="un_comtrade",
        display_name="UN Comtrade",
        url="https://comtrade.un.org/",
        provider=ProviderType.trade_policy,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "United Nations international trade statistics database — reported "
            "merchandise trade flows by reporter, partner and commodity "
            "classification (import and export values)"
        ),
        theme_keywords=(
            "trade", "tariff", "tariffs", "customs", "import", "export",
            "trade flow", "trade flows", "trade statistics", "trade balance",
        ),
        reliability_note=(
            "Policy / government reference only; live figures not fetched at "
            "report time; 29C follow-up. UN Comtrade international trade "
            "statistics catalog — thematic context only, no trade values, "
            "tariff rates, or dates emitted."
        ),
        tier=T2_REGULATOR_OR_GOV,
    ),
    MacroSourceSpec(
        source_id="nato",
        display_name="NATO defence expenditure",
        url="https://www.nato.int/cps/en/natohq/topics_49198.htm",
        provider=ProviderType.trade_policy,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "NATO member defence-expenditure and burden-sharing references — "
            "aggregate and per-member defence spending relative to gross "
            "domestic product and the share allocated to major equipment"
        ),
        theme_keywords=(
            "defense", "defence", "nato", "military spending", "military",
            "defense budget", "defence budget", "defense spending",
            "defence spending", "procurement", "burden sharing",
            "burden-sharing",
        ),
        reliability_note=(
            "Policy / government reference only; live figures not fetched at "
            "report time; 29C follow-up. NATO defence-expenditure and "
            "burden-sharing publication — thematic context only, no defence "
            "budget figures, spending percentages, or dates emitted."
        ),
        tier=T2_REGULATOR_OR_GOV,
    ),
    MacroSourceSpec(
        source_id="sipri",
        display_name="SIPRI military expenditure database",
        url="https://www.sipri.org/databases/milex",
        provider=ProviderType.trade_policy,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "SIPRI Military Expenditure Database references — national and "
            "regional military spending series and arms-industry / "
            "arms-transfer indicators compiled by the Stockholm International "
            "Peace Research Institute"
        ),
        theme_keywords=(
            "defense", "defence", "military expenditure", "military spending",
            "military", "arms", "defense spending", "defence spending",
            "weapons", "arms trade",
        ),
        reliability_note=(
            "Policy / government reference only; live figures not fetched at "
            "report time; 29C follow-up. SIPRI military-expenditure and "
            "arms-industry catalog — thematic context only, no spending "
            "figures, amounts, or dates emitted."
        ),
        tier=T3_INDUSTRY_SPECIALIST,
    ),
    MacroSourceSpec(
        source_id="oecd",
        display_name="OECD (industrial policy & trade)",
        url="https://www.oecd.org/",
        provider=ProviderType.macro_statistics,
        jurisdiction=None,
        region=None,
        broad_macro=False,
        indicators=(
            "OECD policy and statistics references — industrial policy, "
            "subsidies and state aid, trade and tariff analysis, and "
            "energy-transition and grid-investment indicators across member "
            "and partner economies"
        ),
        theme_keywords=(
            "subsidy", "subsidies", "industrial policy", "state aid",
            "tariff", "tariffs", "trade", "energy transition",
            "grid investment", "grid",
        ),
        reliability_note=(
            "Policy / government reference only; live figures not fetched at "
            "report time; 29C follow-up. OECD industrial-policy, subsidy, "
            "trade and energy-transition catalog — thematic context only, no "
            "subsidy amounts, tariff rates, or dates emitted."
        ),
        tier=T2_REGULATOR_OR_GOV,
    ),
)


# The full macro reference table the registry, collector and connector builder
# all iterate: the 29C.1 macro publishers, the 29C.2 commodity / energy
# reference sources, and the 29C.3 policy / government reference sources.
ALL_MACRO_SOURCES: tuple[MacroSourceSpec, ...] = (
    MACRO_SOURCES + COMMODITY_ENERGY_SOURCES + POLICY_GOVERNMENT_SOURCES
)


def macro_spec_for(source_id: str) -> MacroSourceSpec | None:
    """Return the macro / commodity-energy spec for ``source_id``, or None."""
    return next((s for s in ALL_MACRO_SOURCES if s.source_id == source_id), None)


class MacroReferenceConnector(SourceConnector):
    """A reference-only macro connector for ONE macro source.

    ``fetch_macro_context`` emits a bounded T2 macro *source reference* plus an
    honest "figures not fetched" gap when the query theme / region is relevant;
    otherwise it returns an empty result (no evidence, no gap). It never fetches
    and never fabricates a figure. ``fetch_filings`` / ``fetch_events`` are not a
    macro path and return an honest not-eligible gap.
    """

    status = ConnectorStatus.enabled

    def __init__(self, spec: MacroSourceSpec) -> None:
        self._spec = spec
        self.connector_key = spec.source_id
        self.supported_source_ids = (spec.source_id,)

    # -- Relevance ---------------------------------------------------------

    def covers(self, query: QueryContext) -> bool:
        """True when this macro source is relevant to the query theme / region."""
        theme = (query.query or "").strip().lower()
        region = (query.region or "").strip().lower()
        if theme and any(kw in theme for kw in self._spec.theme_keywords):
            return True
        if region and self._spec.region and self._spec.region.lower() in region:
            return True
        # A generic macro ask (no theme, no region) is answered by the broad
        # macro publishers only — commodity-specific sources stay quiet.
        if not theme and not region:
            return self._spec.broad_macro
        return False

    # -- Result builders ---------------------------------------------------

    def _reference_item(self) -> EvidenceItem:
        spec = self._spec
        excerpt = (
            f"{spec.display_name} publishes {spec.indicators}. This item is a "
            "source reference to that official dataset only: no indicator value, "
            "index level, release date, or forecast is fetched or fabricated."
        )
        return build_evidence_item(
            id=f"MACRO_{spec.source_id.upper()}",
            source_id=spec.source_id,
            source_name=spec.display_name,
            provider_transport=f"{spec.display_name} (official statistics publisher)",
            provider_transport_tier=spec.tier,
            content_source=f"{spec.display_name} — macro indicator catalog",
            content_source_tier=spec.tier,
            source_type=_MACRO_SOURCE_TYPE,
            title=f"{spec.display_name} — macro source reference",
            url=spec.url,
            excerpt=excerpt,
            data_quality="reference_only",
            confidence="medium",
            provenance=[
                f"{spec.display_name} (official macro statistics publisher)",
                "Macro source reference only — no indicator values or release "
                "dates fetched",
                "needs_human_review=true",
            ],
            warnings=[
                "Macro source reference only; live macro figures and release "
                "dates are not fetched at report time. Human review required.",
            ],
        )

    def _figures_gap(self) -> SourceGap:
        spec = self._spec
        return SourceGap(
            connector_key=self.connector_key,
            source_id=spec.source_id,
            gap_type=GapType.data_not_sourced,
            severity=GapSeverity.info,
            message=(
                f"{spec.display_name}: macro reference only; live figures not "
                "fetched at report time. Only a pointer to the dataset and the "
                "indicators it covers is provided."
            ),
            suggested_followup_phase=_MACRO_FOLLOWUP_PHASE,
            blocks_research_complete=False,
        )

    def _not_company_source_gap(self, method: str) -> SourceGap:
        return SourceGap(
            connector_key=self.connector_key,
            source_id=self._spec.source_id,
            gap_type=GapType.source_not_eligible,
            severity=GapSeverity.info,
            message=(
                f"{self._spec.display_name} is a macro reference source; "
                f"company {method.replace('fetch_', '')} are not provided by this "
                "connector."
            ),
            blocks_research_complete=False,
        )

    # -- Fetch surface -----------------------------------------------------

    async def fetch_macro_context(self, query: QueryContext) -> ConnectorResult:
        if not self.covers(query):
            return ConnectorResult(connector_key=self.connector_key)
        return ConnectorResult(
            connector_key=self.connector_key,
            evidence_items=[self._reference_item()],
            source_gaps=[self._figures_gap()],
        )

    async def fetch_filings(self, company, query) -> ConnectorResult:  # type: ignore[no-untyped-def]
        return ConnectorResult(
            connector_key=self.connector_key,
            source_gaps=[self._not_company_source_gap("fetch_filings")],
        )

    async def fetch_events(self, company, query) -> ConnectorResult:  # type: ignore[no-untyped-def]
        return ConnectorResult(
            connector_key=self.connector_key,
            source_gaps=[self._not_company_source_gap("fetch_events")],
        )

    # -- Health ------------------------------------------------------------

    def healthcheck(self) -> ConnectorHealth:
        return ConnectorHealth(
            connector_key=self.connector_key,
            status=self.status,
            enabled=self.is_live,
            last_checked_at=_now(),
            detail=(
                f"Emits a {self._spec.tier} macro SOURCE REFERENCE to "
                f"{self._spec.display_name} (which indicators it covers) for a "
                "relevant theme/region; live figures are not fetched at report "
                f"time ({_MACRO_FOLLOWUP_PHASE} follow-up). No API key used."
            ),
        )


def build_macro_connectors() -> dict[str, MacroReferenceConnector]:
    """One reference-only connector per macro / commodity-energy source."""
    return {s.source_id: MacroReferenceConnector(s) for s in ALL_MACRO_SOURCES}


__all__ = [
    "MacroSourceSpec",
    "MACRO_SOURCES",
    "COMMODITY_ENERGY_SOURCES",
    "POLICY_GOVERNMENT_SOURCES",
    "ALL_MACRO_SOURCES",
    "MacroReferenceConnector",
    "macro_spec_for",
    "build_macro_connectors",
]
