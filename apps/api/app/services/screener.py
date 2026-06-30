"""
Phase 14: Deterministic theme-based company screener.

The screener takes a universe definition (region, exchange, sector, theme,
max_candidates) and a provider, then:

  1. Builds a list of candidate company inputs from the provider.
  2. Resolves each input to a canonical ticker/exchange.
  3. Attempts to fetch fundamentals (non-fatal; EODHD only when key present).
  4. Classifies available/missing data.
  5. Generates discovery reasons based on theme filters.
  6. Returns a list of CandidateInput objects for the service to persist.

Constraints:
  - No BUY/SELL/HOLD/WATCH/price_target/fair_value/upside produced.
  - No live EODHD calls in CI (mock provider used when key absent).
  - Source tier stays T5_api_aggregator for any EODHD data.
  - If only T5 data is available, a mandatory warning is added.
  - Tests are fixture-based and offline.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Warning enforced for all T5-only candidates
# ---------------------------------------------------------------------------

T5_VALIDATION_WARNING = (
    "Candidate requires primary-source validation before final analysis."
)

# ---------------------------------------------------------------------------
# Source tier and data quality constants
# ---------------------------------------------------------------------------

SOURCE_TIER_T5 = "T5_api_aggregator"
SOURCE_TIER_T6 = "T6_model_estimate"
DATA_QUALITY_B = "B_single_credible"
DATA_QUALITY_D = "D_weak_or_stale"

# ---------------------------------------------------------------------------
# Theme → keyword mapping for discovery-reason generation
# ---------------------------------------------------------------------------

THEME_KEYWORDS: dict[str, list[str]] = {
    "energy_transition": [
        "renewable",
        "solar",
        "wind",
        "hydrogen",
        "battery",
        "storage",
        "clean energy",
        "decarbonization",
        "electrolysis",
        "offshore",
    ],
    "electrification_grid": [
        "grid",
        "transmission",
        "distribution",
        "substation",
        "transformer",
        "cable",
        "interconnection",
        "smart grid",
        "electricity network",
    ],
    "defense_security": [
        "defense",
        "defence",
        "military",
        "aerospace",
        "radar",
        "surveillance",
        "cybersecurity",
        "armament",
        "missile",
        "nato",
    ],
    "industrial_resilience": [
        "manufacturing",
        "industrial",
        "automation",
        "robotics",
        "logistics",
        "infrastructure",
        "rail",
        "port",
        "reshoring",
        "supply chain",
    ],
    "real_assets": [
        "real estate",
        "infrastructure",
        "utilities",
        "pipeline",
        "storage",
        "port",
        "airport",
        "toll road",
        "reit",
    ],
    "materials_mining": [
        "mining",
        "mineral",
        "copper",
        "lithium",
        "nickel",
        "cobalt",
        "rare earth",
        "iron ore",
        "aluminium",
        "aluminum",
        "zinc",
        "gold",
    ],
}

# ---------------------------------------------------------------------------
# Sector → theme affinity mapping
# ---------------------------------------------------------------------------

SECTOR_THEME_AFFINITY: dict[str, list[str]] = {
    "energy": ["energy_transition", "real_assets"],
    "utilities": ["energy_transition", "electrification_grid", "real_assets"],
    "materials": ["materials_mining"],
    "industrials": ["industrial_resilience", "defense_security"],
    "information technology": ["defense_security"],
    "real estate": ["real_assets"],
    "financials": [],
    "consumer staples": [],
    "health care": [],
    "consumer discretionary": [],
    "communication services": [],
}

# ---------------------------------------------------------------------------
# Mock universe: predefined candidate companies by theme
# ---------------------------------------------------------------------------

_MOCK_UNIVERSE_BY_THEME: dict[str, list[dict[str, Any]]] = {
    "energy_transition": [
        {
            "ticker": "ORSTED",
            "exchange": "CPH",
            "name": "Ørsted A/S",
            "country": "Denmark",
            "sector": "Utilities",
            "description": "offshore wind and renewable energy",
        },
        {
            "ticker": "ENPH",
            "exchange": "NASDAQ",
            "name": "Enphase Energy Inc.",
            "country": "United States",
            "sector": "Energy",
            "description": "solar microinverters and battery storage",
        },
        {
            "ticker": "NEL",
            "exchange": "OSE",
            "name": "Nel ASA",
            "country": "Norway",
            "sector": "Energy",
            "description": "green hydrogen electrolysis",
        },
    ],
    "electrification_grid": [
        {
            "ticker": "ABB",
            "exchange": "SWX",
            "name": "ABB Ltd",
            "country": "Switzerland",
            "sector": "Industrials",
            "description": "power grid automation and transformers",
        },
        {
            "ticker": "PRYSMIAN",
            "exchange": "MIL",
            "name": "Prysmian SpA",
            "country": "Italy",
            "sector": "Industrials",
            "description": "submarine and high-voltage cable manufacturing",
        },
        {
            "ticker": "NKT",
            "exchange": "CPH",
            "name": "NKT A/S",
            "country": "Denmark",
            "sector": "Industrials",
            "description": "high-voltage power cable interconnection",
        },
    ],
    "defense_security": [
        {
            "ticker": "SAAB",
            "exchange": "STO",
            "name": "Saab AB",
            "country": "Sweden",
            "sector": "Industrials",
            "description": "defense aerospace radar systems",
        },
        {
            "ticker": "RHM",
            "exchange": "XETRA",
            "name": "Rheinmetall AG",
            "country": "Germany",
            "sector": "Industrials",
            "description": "military vehicles and armament systems",
        },
        {
            "ticker": "LDOS",
            "exchange": "NYSE",
            "name": "Leidos Holdings Inc.",
            "country": "United States",
            "sector": "Industrials",
            "description": "defense information technology surveillance",
        },
    ],
    "industrial_resilience": [
        {
            "ticker": "SIE",
            "exchange": "XETRA",
            "name": "Siemens AG",
            "country": "Germany",
            "sector": "Industrials",
            "description": "industrial automation manufacturing",
        },
        {
            "ticker": "FANUC",
            "exchange": "TSE",
            "name": "Fanuc Corp",
            "country": "Japan",
            "sector": "Industrials",
            "description": "industrial robotics and automation",
        },
        {
            "ticker": "DSV",
            "exchange": "CPH",
            "name": "DSV A/S",
            "country": "Denmark",
            "sector": "Industrials",
            "description": "global logistics and supply chain",
        },
    ],
    "real_assets": [
        {
            "ticker": "GETLINK",
            "exchange": "EPA",
            "name": "Getlink SE",
            "country": "France",
            "sector": "Industrials",
            "description": "cross-channel rail infrastructure",
        },
        {
            "ticker": "TERNA",
            "exchange": "MIL",
            "name": "Terna SpA",
            "country": "Italy",
            "sector": "Utilities",
            "description": "Italian electricity transmission network",
        },
        {
            "ticker": "RWE",
            "exchange": "XETRA",
            "name": "RWE AG",
            "country": "Germany",
            "sector": "Utilities",
            "description": "renewable energy and real asset portfolio",
        },
    ],
    "materials_mining": [
        {
            "ticker": "GLEN",
            "exchange": "LSE",
            "name": "Glencore PLC",
            "country": "United Kingdom",
            "sector": "Materials",
            "description": "copper nickel cobalt mining and trading",
        },
        {
            "ticker": "LITH",
            "exchange": "ASX",
            "name": "Lithium Australia NL",
            "country": "Australia",
            "sector": "Materials",
            "description": "lithium mineral processing and recycling",
        },
        {
            "ticker": "MP",
            "exchange": "NYSE",
            "name": "MP Materials Corp.",
            "country": "United States",
            "sector": "Materials",
            "description": "rare earth mining and processing",
        },
    ],
}

_MOCK_DEFAULT_UNIVERSE: list[dict[str, Any]] = [
    entry for entries in _MOCK_UNIVERSE_BY_THEME.values() for entry in entries
]


# ---------------------------------------------------------------------------
# CandidateInput — output of the screener, input to the service
# ---------------------------------------------------------------------------


@dataclass
class CandidateInput:
    """
    A screened candidate ready for database persistence.

    This is an internal research funnel entry — NOT a public recommendation.
    candidate_status must be one of the allowed internal values.
    """

    ticker: str
    exchange: str | None
    name: str | None
    country: str | None
    sector: str | None
    provider_symbol: str | None
    market_cap: float | None
    currency: str | None
    candidate_status: str  # must be in CANDIDATE_STATUS_VALUES
    discovery_reasons: list[str] = field(default_factory=list)
    available_data: list[str] = field(default_factory=list)
    missing_data: list[str] = field(default_factory=list)
    source_tier: str = SOURCE_TIER_T6
    data_quality: str = DATA_QUALITY_D
    warnings: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Screener
# ---------------------------------------------------------------------------


class CompanyScreener:
    """
    Deterministic first-pass screener that produces CandidateInput objects.

    No investment recommendations are produced.
    No price targets, fair values, or upside percentages are produced.
    Source tier stays T5 for EODHD data and T6 for mock/inferred data.
    """

    def screen(
        self,
        region: str | None,
        exchange: str | None,
        sector: str | None,
        theme: str | None,
        max_candidates: int,
        provider_name: str,
        market_cap_min: float | None = None,
        market_cap_max: float | None = None,
        keyword_search: str | None = None,
        eodhd_search_results: list[dict[str, Any]] | None = None,
    ) -> list[CandidateInput]:
        """
        Run the screen and return a list of CandidateInput objects.

        When provider_name is 'eodhd' and eodhd_search_results is supplied
        (from CompanyIdentifierResolver.search), those results are used instead
        of the mock universe.  This allows offline tests with fixture data.

        Returns at most max_candidates candidates.
        """
        if provider_name == "eodhd" and eodhd_search_results is not None:
            raw_candidates = self._from_eodhd_results(
                eodhd_search_results, theme, sector, region, exchange
            )
            source_tier = SOURCE_TIER_T5
            data_quality = DATA_QUALITY_B
        else:
            raw_candidates = self._from_mock_universe(
                theme, sector, region, exchange, keyword_search
            )
            source_tier = SOURCE_TIER_T6
            data_quality = DATA_QUALITY_D

        results: list[CandidateInput] = []
        for raw in raw_candidates[:max_candidates]:
            candidate = self._build_candidate(
                raw=raw,
                theme=theme,
                source_tier=source_tier,
                data_quality=data_quality,
                market_cap_min=market_cap_min,
                market_cap_max=market_cap_max,
            )
            results.append(candidate)

        return results

    # ── Private helpers ──────────────────────────────────────────────────────

    def _from_mock_universe(
        self,
        theme: str | None,
        sector: str | None,
        region: str | None,
        exchange: str | None,
        keyword_search: str | None,
    ) -> list[dict[str, Any]]:
        """
        Filter the mock universe by parameters.
        """
        if theme and theme in _MOCK_UNIVERSE_BY_THEME:
            candidates = list(_MOCK_UNIVERSE_BY_THEME[theme])
        else:
            candidates = list(_MOCK_DEFAULT_UNIVERSE)

        if sector:
            sector_lower = sector.lower()
            candidates = [
                c
                for c in candidates
                if c.get("sector", "").lower() == sector_lower
            ]

        if exchange:
            exchange_upper = exchange.upper()
            candidates = [
                c for c in candidates if c.get("exchange", "").upper() == exchange_upper
            ]

        if region:
            region_lower = region.lower()
            region_country_map = {
                "europe": {
                    "germany",
                    "france",
                    "italy",
                    "sweden",
                    "norway",
                    "denmark",
                    "switzerland",
                    "netherlands",
                    "spain",
                    "finland",
                    "belgium",
                    "united kingdom",
                    "uk",
                    "austria",
                    "poland",
                },
                "us": {"united states"},
                "north america": {"united states", "canada"},
                "asia": {"japan", "south korea", "china", "india", "australia"},
                "global": set(),
            }
            allowed_countries = region_country_map.get(region_lower, set())
            if allowed_countries:
                candidates = [
                    c
                    for c in candidates
                    if c.get("country", "").lower() in allowed_countries
                ]

        if keyword_search:
            kw = keyword_search.lower()
            candidates = [
                c
                for c in candidates
                if kw in c.get("name", "").lower()
                or kw in c.get("description", "").lower()
            ]

        return candidates

    def _from_eodhd_results(
        self,
        results: list[dict[str, Any]],
        theme: str | None,
        sector: str | None,
        region: str | None,
        exchange: str | None,
    ) -> list[dict[str, Any]]:
        """
        Convert EODHD search results to the raw candidate format.
        Each result dict is expected to have keys from EODHD /search API:
          Code, Exchange, Name, Country, Type, Currency, ISIN
        """
        candidates = []
        for r in results:
            if r.get("Type", "").lower() not in ("common stock", "equity", "stock", ""):
                continue
            if exchange and r.get("Exchange", "").upper() != exchange.upper():
                continue
            candidates.append(
                {
                    "ticker": r.get("Code", ""),
                    "exchange": r.get("Exchange", ""),
                    "name": r.get("Name", ""),
                    "country": r.get("Country", ""),
                    "sector": r.get("Sector", "") or sector,
                    "description": r.get("Name", ""),
                    "currency": r.get("Currency", ""),
                    "provider_symbol": f"{r.get('Code', '')}.{r.get('Exchange', '')}",
                }
            )
        return candidates

    def _build_candidate(
        self,
        raw: dict[str, Any],
        theme: str | None,
        source_tier: str,
        data_quality: str,
        market_cap_min: float | None,
        market_cap_max: float | None,
    ) -> CandidateInput:
        """
        Build a CandidateInput from a raw company dict.
        """
        ticker = raw.get("ticker", "")
        exchange = raw.get("exchange") or None
        name = raw.get("name") or None
        country = raw.get("country") or None
        sector = raw.get("sector") or None
        description = raw.get("description", "")
        market_cap = raw.get("market_cap") or None
        currency = raw.get("currency") or None
        provider_symbol = raw.get("provider_symbol") or (
            f"{ticker}.{exchange}" if exchange else ticker
        )

        # ── Market cap filter ────────────────────────────────────────────────
        if market_cap is not None:
            if market_cap_min is not None and market_cap < market_cap_min:
                return CandidateInput(
                    ticker=ticker,
                    exchange=exchange,
                    name=name,
                    country=country,
                    sector=sector,
                    provider_symbol=provider_symbol,
                    market_cap=market_cap,
                    currency=currency,
                    candidate_status="rejected_by_screen",
                    discovery_reasons=["Market cap below minimum filter"],
                    available_data=["market_cap"],
                    missing_data=[],
                    source_tier=source_tier,
                    data_quality=data_quality,
                    warnings=[],
                )
            if market_cap_max is not None and market_cap > market_cap_max:
                return CandidateInput(
                    ticker=ticker,
                    exchange=exchange,
                    name=name,
                    country=country,
                    sector=sector,
                    provider_symbol=provider_symbol,
                    market_cap=market_cap,
                    currency=currency,
                    candidate_status="rejected_by_screen",
                    discovery_reasons=["Market cap above maximum filter"],
                    available_data=["market_cap"],
                    missing_data=[],
                    source_tier=source_tier,
                    data_quality=data_quality,
                    warnings=[],
                )

        # ── Available vs missing data ─────────────────────────────────────────
        available_data: list[str] = []
        missing_data: list[str] = []

        for field_name, value in [
            ("ticker", ticker),
            ("exchange", exchange),
            ("name", name),
            ("country", country),
            ("sector", sector),
            ("market_cap", market_cap),
            ("currency", currency),
        ]:
            if value is not None and value != "":
                available_data.append(field_name)
            else:
                missing_data.append(field_name)

        # Fundamentals/financials always missing from mock provider
        fundamental_fields = [
            "market_cap_usd_m",
            "revenue_ttm",
            "ebitda",
            "ev_ebitda",
            "pe_ratio",
            "fcf_ttm",
            "net_debt",
            "shares_outstanding",
        ]
        if source_tier == SOURCE_TIER_T6:
            missing_data.extend(fundamental_fields)
        else:
            # T5 (EODHD) may have some fundamentals
            missing_data.extend(["ev_ebitda", "fcf_ttm", "net_debt"])
            available_data.extend(["market_cap_usd_m", "revenue_ttm"])

        # ── Discovery reasons ─────────────────────────────────────────────────
        discovery_reasons = _build_discovery_reasons(
            theme=theme,
            sector=sector,
            description=description,
            name=name or "",
        )

        # ── Candidate status ──────────────────────────────────────────────────
        if not ticker:
            status = "error"
        elif len(missing_data) > 5:
            if source_tier == SOURCE_TIER_T5:
                status = "needs_primary_sources"
            else:
                status = "needs_data"
        else:
            status = "candidate_found"

        # ── Warnings ──────────────────────────────────────────────────────────
        warnings: list[str] = []
        if source_tier == SOURCE_TIER_T5:
            warnings.append(T5_VALIDATION_WARNING)
        if source_tier == SOURCE_TIER_T6:
            warnings.append(
                "Mock/synthetic data only — all values are demo placeholders."
            )
        if "market_cap" in missing_data:
            warnings.append("Market cap not available from current provider.")

        return CandidateInput(
            ticker=ticker,
            exchange=exchange,
            name=name,
            country=country,
            sector=sector,
            provider_symbol=provider_symbol,
            market_cap=market_cap,
            currency=currency,
            candidate_status=status,
            discovery_reasons=discovery_reasons,
            available_data=available_data,
            missing_data=missing_data,
            source_tier=source_tier,
            data_quality=data_quality,
            warnings=warnings,
        )


def _build_discovery_reasons(
    theme: str | None,
    sector: str | None,
    description: str,
    name: str,
) -> list[str]:
    """
    Generate human-readable discovery reasons for a candidate.
    """
    reasons: list[str] = []

    if theme:
        keywords = THEME_KEYWORDS.get(theme, [])
        matched = [
            kw
            for kw in keywords
            if kw in description.lower() or kw in name.lower()
        ]
        if matched:
            reasons.append(
                f"Theme match '{theme}': keywords found — {', '.join(matched[:3])}"
            )
        else:
            if sector:
                affinities = SECTOR_THEME_AFFINITY.get(sector.lower(), [])
                if theme in affinities:
                    reasons.append(
                        f"Theme match '{theme}': sector '{sector}' has affinity"
                    )
                else:
                    reasons.append(
                        f"Universe theme '{theme}': sector '{sector}' included in screen"
                    )
            else:
                reasons.append(f"Universe theme '{theme}': included in screen")

    if sector:
        reasons.append(f"Sector filter match: '{sector}'")

    if not reasons:
        reasons.append("Company matched universe filter parameters")

    return reasons
