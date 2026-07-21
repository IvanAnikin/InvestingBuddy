"""
Phase 27.1B: canonical sector / industry taxonomy.

ONE place that knows what a sector *is*, so an admin typing "Luxury Goods" can
still match a company the registry tags as "Consumer Discretionary".

Why this module exists: before it, sector matching was raw case-insensitive
string equality between whatever the admin typed and whatever the curated
registry stored. That works for "Industrials" and fails for every real research
vocabulary word — "luxury goods", "personal goods", "watches & jewelry" are all
industries *inside* Consumer Discretionary, not sectors, and an equality test
rejects all of them.

Design:
  * ``CANONICAL_SECTORS`` are the eight GICS-style sectors the platform uses,
    plus the two the registry already emits (Real Estate, Communication
    Services). Nothing here is invented — these are standard classification
    labels, not financial claims.
  * Aliases are *lookup conveniences only*. Mapping "luxury" -> Consumer
    Discretionary widens a SEARCH; it never asserts anything about a company.
  * ``normalize_industry`` deliberately keeps industry granularity (it maps
    "jewellery" -> "Watches & Jewelry", NOT up to the sector) because the
    universe builder scores sector and industry separately.

SAFETY: a taxonomy is descriptive metadata. Nothing in this module produces or
influences an investment recommendation, rating, price target, or valuation.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Canonical sectors
# ---------------------------------------------------------------------------

CANONICAL_SECTORS: tuple[str, ...] = (
    "Consumer Discretionary",
    "Consumer Staples",
    "Communication Services",
    "Energy",
    "Financials",
    "Healthcare",
    "Industrials",
    "Materials",
    "Real Estate",
    "Technology",
    "Utilities",
)

# Sector alias -> canonical sector. Keys are matched lower-cased and
# whitespace-collapsed. A canonical sector is always its own alias (added
# programmatically below), so "Industrials" keeps working unchanged.
_SECTOR_ALIASES: dict[str, str] = {
    # --- Consumer Discretionary (the Phase 27.1B addition) -----------------
    "luxury": "Consumer Discretionary",
    "luxury goods": "Consumer Discretionary",
    "luxury brands": "Consumer Discretionary",
    "consumer luxury": "Consumer Discretionary",
    "personal goods": "Consumer Discretionary",
    "premium brands": "Consumer Discretionary",
    "premium consumer brands": "Consumer Discretionary",
    "watches": "Consumer Discretionary",
    "watchmaking": "Consumer Discretionary",
    "watches and jewelry": "Consumer Discretionary",
    "watches & jewelry": "Consumer Discretionary",
    "watches and jewellery": "Consumer Discretionary",
    "watches & jewellery": "Consumer Discretionary",
    "jewelry": "Consumer Discretionary",
    "jewellery": "Consumer Discretionary",
    "apparel": "Consumer Discretionary",
    "apparel & accessories": "Consumer Discretionary",
    "leather goods": "Consumer Discretionary",
    "fashion": "Consumer Discretionary",
    "consumer cyclical": "Consumer Discretionary",
    "consumer discretionary": "Consumer Discretionary",
    "retail": "Consumer Discretionary",
    # --- Other sectors: aliases that already appear in real feeds ----------
    "consumer defensive": "Consumer Staples",
    "consumer staples": "Consumer Staples",
    "industrial": "Industrials",
    "industrials": "Industrials",
    "aerospace & defense": "Industrials",
    "aerospace and defense": "Industrials",
    "defense": "Industrials",
    "defence": "Industrials",
    "capital goods": "Industrials",
    "tech": "Technology",
    "technology": "Technology",
    "information technology": "Technology",
    "semiconductors": "Technology",
    "software": "Technology",
    "energy": "Energy",
    "oil & gas": "Energy",
    "financial": "Financials",
    "financials": "Financials",
    "financial services": "Financials",
    "banks": "Financials",
    "banking": "Financials",
    "health care": "Healthcare",
    "healthcare": "Healthcare",
    "biotechnology": "Healthcare",
    "pharmaceuticals": "Healthcare",
    "basic materials": "Materials",
    "materials": "Materials",
    "mining": "Materials",
    "metals & mining": "Materials",
    "utilities": "Utilities",
    "utility": "Utilities",
    "real estate": "Real Estate",
    "reit": "Real Estate",
    "communication services": "Communication Services",
    "telecom": "Communication Services",
}

# ---------------------------------------------------------------------------
# Canonical industries
#
# Industry granularity is preserved on purpose — the universe builder scores a
# sector hit and an industry hit separately, so collapsing "Watches & Jewelry"
# into its parent sector here would silently drop a scoring signal.
# ---------------------------------------------------------------------------

# canonical industry -> the sector it belongs to.
INDUSTRY_TO_SECTOR: dict[str, str] = {
    # Consumer Discretionary / luxury
    "Luxury Goods": "Consumer Discretionary",
    "Watches & Jewelry": "Consumer Discretionary",
    "Personal Goods": "Consumer Discretionary",
    "Apparel & Accessories": "Consumer Discretionary",
    "Leather Goods": "Consumer Discretionary",
    "Premium Consumer Brands": "Consumer Discretionary",
    "Luxury Apparel": "Consumer Discretionary",
    "Jewelry": "Consumer Discretionary",
    # Pre-existing industries the registry already emits.
    "Aerospace & Defense": "Industrials",
    "Semiconductors": "Technology",
    "Semiconductor Equipment": "Technology",
    "Nuclear": "Energy",
    "Uranium": "Energy",
    "Electrical Equipment": "Industrials",
    "Construction & Engineering": "Industrials",
    "Robotics & Automation": "Industrials",
    "Biotechnology": "Healthcare",
    "Pharmaceuticals": "Healthcare",
    "Banks": "Financials",
    "Financial Technology": "Financials",
    "Mining": "Materials",
    "Metals & Mining": "Materials",
    "Data Centers": "Technology",
    "AI Infrastructure": "Technology",
    "Utilities": "Utilities",
}

# Industry alias -> canonical industry. Canonical industries are their own
# aliases (added programmatically below).
_INDUSTRY_ALIASES: dict[str, str] = {
    "luxury": "Luxury Goods",
    "luxury good": "Luxury Goods",
    "luxury goods": "Luxury Goods",
    "luxury brands": "Luxury Goods",
    "premium brands": "Premium Consumer Brands",
    "premium consumer brands": "Premium Consumer Brands",
    "watches": "Watches & Jewelry",
    "watch": "Watches & Jewelry",
    "watchmaking": "Watches & Jewelry",
    "timepieces": "Watches & Jewelry",
    "watches and jewelry": "Watches & Jewelry",
    "watches & jewelry": "Watches & Jewelry",
    "watches and jewellery": "Watches & Jewelry",
    "watches & jewellery": "Watches & Jewelry",
    "jewelry": "Jewelry",
    "jewellery": "Jewelry",
    "personal goods": "Personal Goods",
    "apparel": "Apparel & Accessories",
    "apparel & accessories": "Apparel & Accessories",
    "apparel and accessories": "Apparel & Accessories",
    "luxury apparel": "Luxury Apparel",
    "leather goods": "Leather Goods",
    "handbags": "Leather Goods",
    "aerospace and defense": "Aerospace & Defense",
    "aerospace & defence": "Aerospace & Defense",
    "defense": "Aerospace & Defense",
    "semiconductor": "Semiconductors",
    "chips": "Semiconductors",
    "semiconductor equipment": "Semiconductor Equipment",
    "metals and mining": "Metals & Mining",
    "robotics": "Robotics & Automation",
    "automation": "Robotics & Automation",
    "data centres": "Data Centers",
    "data centers": "Data Centers",
}


def _norm_key(value: str | None) -> str:
    """Lower-case + collapse whitespace so " Luxury  Goods " keys correctly."""
    if not value:
        return ""
    return " ".join(str(value).strip().lower().split())


# Canonical labels are their own aliases — a value already canonical must
# normalize to itself rather than falling through to None.
for _sector in CANONICAL_SECTORS:
    _SECTOR_ALIASES.setdefault(_norm_key(_sector), _sector)
for _industry in INDUSTRY_TO_SECTOR:
    _INDUSTRY_ALIASES.setdefault(_norm_key(_industry), _industry)


def normalize_sector(value: str | None) -> str | None:
    """
    Map any sector spelling/alias to its canonical sector name.

    Returns ``None`` for unknown input — an unrecognized sector must not be
    guessed into a canonical one, because that would silently widen a search
    into companies the admin never asked for.
    """
    key = _norm_key(value)
    if not key:
        return None
    direct = _SECTOR_ALIASES.get(key)
    if direct:
        return direct
    # An industry name used in the sector field (e.g. sector="Watches &
    # Jewelry") resolves through its parent sector rather than failing.
    industry = _INDUSTRY_ALIASES.get(key)
    if industry:
        return INDUSTRY_TO_SECTOR.get(industry)
    return None


def normalize_industry(value: str | None) -> str | None:
    """
    Map any industry spelling/alias to its canonical industry name.

    Returns ``None`` for unknown input. Granularity is preserved — this never
    collapses an industry up into its sector.
    """
    key = _norm_key(value)
    if not key:
        return None
    return _INDUSTRY_ALIASES.get(key)


def sector_for_industry(industry: str | None) -> str | None:
    """Return the canonical sector an industry belongs to, or ``None``."""
    canonical = normalize_industry(industry)
    if not canonical:
        return None
    return INDUSTRY_TO_SECTOR.get(canonical)


def sector_matches(
    user_sector: str | None,
    company_sector: str | None,
    company_industries: list[str] | str | None = None,
) -> bool:
    """
    True when a company satisfies the admin's requested sector.

    Three ways to match, in order of directness:
      1. Both normalize to the same canonical sector ("Luxury Goods" vs
         "Consumer Discretionary" -> both Consumer Discretionary).
      2. The request is really an *industry* and the company carries it
         ("Watches & Jewelry" requested, company industry "Watches & Jewelry").
      3. The request is an industry whose parent sector is the company's sector
         ("Watches & Jewelry" requested, company sector Consumer Discretionary).

    An empty ``user_sector`` means "no sector filter" and matches everything.
    """
    if not _norm_key(user_sector):
        return True

    if isinstance(company_industries, str):
        industries: list[str] = [company_industries]
    else:
        industries = list(company_industries or [])

    requested_sector = normalize_sector(user_sector)
    company_canonical_sector = normalize_sector(company_sector)

    # 1) canonical sector equality
    if requested_sector and company_canonical_sector:
        if requested_sector == company_canonical_sector:
            return True

    requested_industry = normalize_industry(user_sector)
    if requested_industry:
        # 2) the company carries the requested industry
        for ind in industries:
            if normalize_industry(ind) == requested_industry:
                return True
        # 3) the requested industry's parent sector is the company's sector
        parent = INDUSTRY_TO_SECTOR.get(requested_industry)
        if parent and company_canonical_sector and parent == company_canonical_sector:
            return True
        # …or the company's own industry rolls up to the same parent sector
        for ind in industries:
            if parent and sector_for_industry(ind) == parent:
                return True

    # Last resort: exact (normalized) string equality, so an unknown-but-equal
    # sector pair the taxonomy has never seen still matches.
    if _norm_key(user_sector) and _norm_key(user_sector) == _norm_key(company_sector):
        return True

    return False


def industry_matches(
    user_industry: str | None,
    company_industry: str | None,
) -> bool:
    """True when the requested industry resolves to the company's industry."""
    if not _norm_key(user_industry):
        return True
    requested = normalize_industry(user_industry)
    actual = normalize_industry(company_industry)
    if requested and actual and requested == actual:
        return True
    return _norm_key(user_industry) == _norm_key(company_industry)


def get_supported_sector_aliases() -> list[dict[str, Any]]:
    """
    Describe the taxonomy for the supported-themes API / admin UI.

    One entry per canonical sector: its accepted aliases and the industries that
    roll up into it. Sorted for a stable response body.
    """
    by_sector: dict[str, dict[str, Any]] = {
        sector: {"sector": sector, "aliases": [], "industries": []}
        for sector in CANONICAL_SECTORS
    }
    for alias, sector in _SECTOR_ALIASES.items():
        entry = by_sector.get(sector)
        if entry is not None and alias != _norm_key(sector):
            entry["aliases"].append(alias)
    for industry, sector in INDUSTRY_TO_SECTOR.items():
        entry = by_sector.get(sector)
        if entry is not None:
            entry["industries"].append(industry)

    out: list[dict[str, Any]] = []
    for sector in CANONICAL_SECTORS:
        entry = by_sector[sector]
        entry["aliases"] = sorted(set(entry["aliases"]))
        entry["industries"] = sorted(set(entry["industries"]))
        out.append(entry)
    return out
