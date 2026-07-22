"""
Phase 27.1C: canonical controlled-selector values for thesis discovery.

ONE place that knows the *allowed* Region / Country / Sector / Industry values a
thesis discovery run may be filtered on, so the admin UI can render searchable
select/combobox fields whose options come from the backend (never hard-coded on
the frontend) and the backend can reject anything outside the allowed set.

Design:
  * Regions and countries are derived from the exchange registry
    (``COUNTRY_TO_REGION``) so the selector options can never drift from the
    venues the platform can actually resolve.
  * Sectors are the canonical GICS-style sectors from ``sector_taxonomy``.
    Aliases ("luxury goods") intentionally do NOT appear as selector values —
    they resolve *internally* to a canonical sector, but the value the UI submits
    is always canonical (Part D rule 7/8).
  * Industries are the canonical industries the curated registry emits, tagged
    with their parent sector.

SAFETY: these are descriptive selector options only. Nothing here produces or
influences an investment recommendation, rating, price target, or valuation.
"""

from __future__ import annotations

from typing import Any

from app.services.exchange_registry import COUNTRY_TO_REGION
from app.services.sector_taxonomy import (
    CANONICAL_SECTORS,
    INDUSTRY_TO_SECTOR,
    normalize_sector,
)

# ---------------------------------------------------------------------------
# Option builders (canonical, sorted, stable)
# ---------------------------------------------------------------------------


def supported_regions() -> list[dict[str, str]]:
    """Canonical regions the platform can filter on, as ``{value, label}``."""
    regions = sorted(set(COUNTRY_TO_REGION.values()))
    return [{"value": r, "label": r} for r in regions]


def supported_countries() -> list[dict[str, str]]:
    """Canonical countries, each tagged with its region (for region filtering)."""
    return [
        {"value": country, "label": country, "region": region}
        for country, region in sorted(COUNTRY_TO_REGION.items())
    ]


def supported_sectors() -> list[dict[str, str]]:
    """Canonical sectors (never aliases) as ``{value, label}``."""
    return [{"value": s, "label": s} for s in sorted(CANONICAL_SECTORS)]


def supported_industries() -> list[dict[str, str]]:
    """Canonical industries, each tagged with its parent sector."""
    return [
        {"value": industry, "label": industry, "sector": sector}
        for industry, sector in sorted(INDUSTRY_TO_SECTOR.items())
    ]


def get_supported_filters() -> dict[str, Any]:
    """Assemble the full controlled-selector option set for the admin UI."""
    return {
        "regions": supported_regions(),
        "countries": supported_countries(),
        "sectors": supported_sectors(),
        "industries": supported_industries(),
    }


# ---------------------------------------------------------------------------
# Canonicalization / validation
#
# Region and country lookups are case-insensitive but return the canonical
# casing, so a value typed as "switzerland" still filters against the registry's
# "Switzerland". A value outside the allowed set returns ``None`` — the caller
# refuses it rather than guessing.
# ---------------------------------------------------------------------------

_REGION_LOOKUP: dict[str, str] = {
    r.lower(): r for r in set(COUNTRY_TO_REGION.values())
}
_COUNTRY_LOOKUP: dict[str, str] = {c.lower(): c for c in COUNTRY_TO_REGION}


def canonical_region(value: str | None) -> str | None:
    """Canonical region name for ``value``, or ``None`` if unsupported."""
    if not value or not value.strip():
        return None
    return _REGION_LOOKUP.get(value.strip().lower())


def canonical_country(value: str | None) -> str | None:
    """Canonical country name for ``value``, or ``None`` if unsupported."""
    if not value or not value.strip():
        return None
    return _COUNTRY_LOOKUP.get(value.strip().lower())


def is_supported_sector(value: str | None) -> bool:
    """True when ``value`` resolves to a canonical sector (alias-tolerant)."""
    if not value or not value.strip():
        return True  # empty = "not specified" is always allowed
    return normalize_sector(value) is not None
