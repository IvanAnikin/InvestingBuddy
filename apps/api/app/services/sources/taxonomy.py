"""
Canonical source taxonomy — Phase 29A.

One place that names every source tier, provider type, cost model, access mode
and status the source framework understands. Everything downstream (the
registry, connectors, evidence items, source gaps and the API) imports its
vocabulary from here so the platform speaks a single language about *where a
fact came from* and *how trustworthy the path was*.

The most important rule this module encodes is the **transport-vs-content**
distinction:

  - A *transport* is the infrastructure a document was retrieved through. SEC
    EDGAR / ``data.sec.gov`` is a transport operated by a regulator, so its
    transport tier is ``T2_regulator_or_gov``.
  - The *content* is the document itself. A company's Form 10-K pulled through
    EDGAR is a primary filing, so its content tier is ``T1_primary_filing``.

Both tiers are recorded on every evidence item. ``sec_tier_pair()`` returns the
canonical (transport, content) pair for SEC-retrieved filings.

These string values intentionally mirror
``app.integrations.financial_data_provider.SourceTier`` and the tier constants
in ``app.services.llm.schemas`` so the whole codebase agrees; a test asserts the
three stay consistent.
"""

from __future__ import annotations

from enum import Enum
from typing import TypedDict


class TierMeta(TypedDict):
    """Static metadata for one source tier (used by the registry tier legend)."""

    code: str
    rank: int
    label: str
    description: str


class SourceTier(str, Enum):
    """The six canonical source tiers, best (T1) to weakest (T6).

    ``T1_primary_company_source`` is a recognised *content* sub-variant of T1
    (an issuer's own press release / IR page). It is intentionally not one of
    the six headline tiers but is accepted everywhere a tier is validated.
    """

    T1_primary_filing = "T1_primary_filing"
    T2_regulator_or_gov = "T2_regulator_or_gov"
    T3_industry_specialist = "T3_industry_specialist"
    T4_quality_media = "T4_quality_media"
    T5_api_aggregator = "T5_api_aggregator"
    T6_model_estimate = "T6_model_estimate"


# Bare-string constants (import these to avoid ``.value`` churn at call sites).
T1_PRIMARY_FILING = SourceTier.T1_primary_filing.value
T2_REGULATOR_OR_GOV = SourceTier.T2_regulator_or_gov.value
T3_INDUSTRY_SPECIALIST = SourceTier.T3_industry_specialist.value
T4_QUALITY_MEDIA = SourceTier.T4_quality_media.value
T5_API_AGGREGATOR = SourceTier.T5_api_aggregator.value
T6_MODEL_ESTIMATE = SourceTier.T6_model_estimate.value

# Recognised T1 content sub-variant for an issuer's own primary material.
T1_PRIMARY_COMPANY_SOURCE = "T1_primary_company_source"

# Ordered, canonical tier list with human-readable metadata. Rank 1 is the
# strongest evidence; it is used for the ``/sources/registry`` tier legend.
CANONICAL_TIERS: tuple[TierMeta, ...] = (
    {
        "code": T1_PRIMARY_FILING,
        "rank": 1,
        "label": "Primary filing",
        "description": (
            "The company's own regulatory filing content (e.g. 10-K, 20-F, "
            "annual report). Highest-trust factual evidence."
        ),
    },
    {
        "code": T2_REGULATOR_OR_GOV,
        "rank": 2,
        "label": "Regulator or government",
        "description": (
            "A regulator or government body as the transport or publisher "
            "(e.g. SEC EDGAR, GLEIF, a statistics office)."
        ),
    },
    {
        "code": T3_INDUSTRY_SPECIALIST,
        "rank": 3,
        "label": "Industry specialist",
        "description": (
            "A specialist agency or standards body for a domain (e.g. USGS, "
            "IEA, ENTSO-E)."
        ),
    },
    {
        "code": T4_QUALITY_MEDIA,
        "rank": 4,
        "label": "Quality media",
        "description": "Reputable, editorially-accountable media coverage.",
    },
    {
        "code": T5_API_AGGREGATOR,
        "rank": 5,
        "label": "API aggregator",
        "description": (
            "A data aggregator/API that repackages an upstream source (e.g. "
            "EODHD, Stooq). Down-weight unless the underlying source is known."
        ),
    },
    {
        "code": T6_MODEL_ESTIMATE,
        "rank": 6,
        "label": "Model estimate",
        "description": (
            "A value derived by a model or heuristic. Never a primary fact; "
            "must carry its derivation method."
        ),
    },
)

# Every tier code the framework accepts on an evidence item (the six canonical
# tiers plus the primary-company-source content variant).
VALID_TIER_CODES: frozenset[str] = frozenset(
    t["code"] for t in CANONICAL_TIERS
) | {T1_PRIMARY_COMPANY_SOURCE}

_TIER_RANK: dict[str, int] = {t["code"]: t["rank"] for t in CANONICAL_TIERS}
# The company-source variant ranks alongside primary filings.
_TIER_RANK[T1_PRIMARY_COMPANY_SOURCE] = 1


def is_valid_tier(tier: str | None) -> bool:
    """True when ``tier`` is a recognised source-tier code."""
    return isinstance(tier, str) and tier in VALID_TIER_CODES


def tier_rank(tier: str | None) -> int:
    """Numeric rank for a tier (1 = strongest). Unknown tiers rank last."""
    if not isinstance(tier, str):
        return 99
    return _TIER_RANK.get(tier, 99)


# SEC EDGAR is a T2 regulator transport; a filing pulled through it is T1
# content. This is the single source of truth for that pairing.
SEC_TRANSPORT_LABEL = "SEC EDGAR / data.sec.gov"


def sec_tier_pair() -> tuple[str, str]:
    """Return the canonical ``(transport_tier, content_tier)`` for SEC filings."""
    return (T2_REGULATOR_OR_GOV, T1_PRIMARY_FILING)


class ProviderType(str, Enum):
    """What *kind* of upstream a source represents."""

    primary_filing = "primary_filing"          # issuer filings via a regulator
    regulator = "regulator"                     # regulator/gov disclosure portal
    identity = "identity"                       # legal-entity identity (GLEIF)
    company_source = "company_source"           # issuer IR / newsroom / PR
    price_aggregator = "price_aggregator"       # market price data API
    fundamentals_aggregator = "fundamentals_aggregator"
    news = "news"                               # news / media feed
    macro_statistics = "macro_statistics"       # macro / national statistics
    commodity = "commodity"                     # commodity / energy data
    trade_policy = "trade_policy"               # trade / tariff / policy
    procurement = "procurement"                 # public procurement / spending
    patents = "patents"                         # patents / IP
    aggregator_toolkit = "aggregator_toolkit"   # multi-source toolkit (OpenBB)


class CostModel(str, Enum):
    free = "free"
    freemium = "freemium"
    paid = "paid"
    unknown = "unknown"


class AccessMode(str, Enum):
    rest_api = "rest_api"
    bulk_download = "bulk_download"
    rss_atom = "rss_atom"
    web_scrape = "web_scrape"
    sdk = "sdk"
    unknown = "unknown"


class SourceStatus(str, Enum):
    """Registry-level lifecycle status for a source."""

    enabled = "enabled"      # wired + usable now (live evidence path)
    scaffolded = "scaffolded"  # connector class exists, returns honest gaps only
    planned = "planned"      # placeholder for a future phase, not wired
    disabled = "disabled"    # wired but intentionally turned off
    error = "error"          # wired but currently unhealthy


class ConnectorStatus(str, Enum):
    """Connector health status. Never leaks secrets or raw error bodies.

    ``scaffolded`` (Phase 29B) is distinct from ``planned``: a scaffolded
    connector has a real class that returns honest ``SourceGap`` objects (never
    fabricated evidence), whereas a planned connector is a bare registry
    placeholder. Neither is "live" — only ``enabled`` / ``configured`` produce
    evidence.
    """

    enabled = "enabled"              # implemented + usable
    configured = "configured"        # implemented + credentials present
    not_configured = "not_configured"  # implemented, credentials missing
    scaffolded = "scaffolded"        # class exists, returns honest gaps only
    planned = "planned"              # placeholder, not implemented yet
    disabled = "disabled"            # implemented, intentionally off
    not_implemented = "not_implemented"
    error = "error"


__all__ = [
    "SourceTier",
    "T1_PRIMARY_FILING",
    "T1_PRIMARY_COMPANY_SOURCE",
    "T2_REGULATOR_OR_GOV",
    "T3_INDUSTRY_SPECIALIST",
    "T4_QUALITY_MEDIA",
    "T5_API_AGGREGATOR",
    "T6_MODEL_ESTIMATE",
    "CANONICAL_TIERS",
    "VALID_TIER_CODES",
    "is_valid_tier",
    "tier_rank",
    "sec_tier_pair",
    "SEC_TRANSPORT_LABEL",
    "ProviderType",
    "CostModel",
    "AccessMode",
    "SourceStatus",
    "ConnectorStatus",
]
