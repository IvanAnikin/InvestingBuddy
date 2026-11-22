"""Obscurity score — V3.20 slice 6.3 (spec §3.6).

Obscurity is a **positive** discovery input: a real thesis on a company few people look
at is the edge the mandate is after. The score is built from components, each shown with
its value, source, date and the points it contributed — never a bare number.

Components (each 0–1 points; 1 = most obscure)
==============================================
* ``analyst_count`` — EPS analyst count of the newest consensus snapshot (slice 4.2).
* ``passive_ownership_pct`` — % of shares outstanding held by US-registered funds (N-PORT,
  slice 3.4).
* ``theme_fund_holders`` — listed fund holders whose NAME carries a theme flag (N-PORT).
* ``english_news_volume`` — English-language articles, last 3 months (GDELT, optional).
* ``sector_mis_tag`` — the layer classifier (5.2) places the company in a layer its
  sector / industry label does not describe.
* ``primary_disclosure_language`` — the listing country's regulatory-filing language.
* ``independent_research_coverage`` (V3.21 slice 21.7, ADR-114) — research providers with
  a coverage page for the company in the coverage index (Edison sitemap; search-only
  providers when searched): 0 → 1.0, 1 → 0.5, 2+ → 0.1. UNKNOWN (left out) until the
  index has been refreshed once. Only when ``V3_RESEARCH_COVERAGE_ENABLED`` passes an
  input; without one the component is absent and the block keeps the previous version.

**Unavailable is not zero.** A component with no data is ``not_available`` and is left
out of the average; the score states how many components it rests on. With fewer than
``MIN_COMPONENTS`` the score is withheld (``score: null``).

GDELT's DOC API covers a rolling three-month window, so the news component is a
three-month count, labelled as such — not the schema's twelve-month field.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AVAILABLE = "available"
NOT_AVAILABLE = "not_available"

MIN_COMPONENTS = 3
#: 2026-11-22.1 adds ``independent_research_coverage`` (V3.21 slice 21.7).
SCORE_VERSION = "2026-11-22.1"
#: The version of a block built without the coverage component (flag off): the formula
#: is then the earlier one, and saying so keeps such a block byte-identical to before.
PREVIOUS_SCORE_VERSION = "2026-11-16.1"

#: Industries / sectors whose label already says "electricity" (the electricity
#: playbook's own declaration, slice 5.3). Classified into a layer while labelled as
#: something else ⇒ mis-tagged, hidden from thematic screens.
ELECTRICITY_LABELS = frozenset({
    "utilities", "electrical equipment", "electrical components & equipment",
    "heavy electrical equipment", "electric utilities",
    "independent power producers & energy traders", "renewable electricity",
})

#: Language of the primary regulatory filing by listing country. ``bilingual`` = filings
#: in the local language and routinely also in English.
_ENGLISH = {"United States", "United Kingdom", "Ireland", "Australia", "New Zealand",
            "Canada", "South Africa", "India"}
_BILINGUAL = {"Hong Kong": "zh/en", "Switzerland": "de/fr/en", "Netherlands": "nl/en",
              "Denmark": "da/en", "Finland": "fi/en", "Norway": "no/en", "Sweden": "sv/en"}
_LOCAL = {"Germany": "de", "Austria": "de", "France": "fr", "Belgium": "fr/nl",
          "Italy": "it", "Spain": "es", "Portugal": "pt", "Poland": "pl", "Japan": "ja",
          "South Korea": "ko", "Taiwan": "zh", "China": "zh", "Brazil": "pt", "Mexico": "es"}


def _component(key: str, value: Any, points: float | None, *, source: str | None,
               as_of: str | None = None, basis: str | None = None,
               note: str | None = None) -> dict[str, Any]:
    return {
        "key": key, "value": value,
        "points": None if points is None else round(points, 2),
        "state": AVAILABLE if points is not None else NOT_AVAILABLE,
        "source": source, "as_of": as_of, "basis": basis, "note": note,
    }


def analyst_points(count: int | None) -> float | None:
    if count is None or count < 0:
        return None
    if count == 0:
        return 1.0
    if count <= 2:
        return 0.8
    if count <= 5:
        return 0.5
    if count <= 10:
        return 0.2
    return 0.0


def passive_points(pct: float | None) -> float | None:
    if pct is None or pct < 0:
        return None
    if pct < 5:
        return 1.0
    if pct < 15:
        return 0.6
    if pct < 30:
        return 0.3
    return 0.0


def theme_points(count: int | None) -> float | None:
    if count is None or count < 0:
        return None
    return 1.0 if count == 0 else 0.6 if count <= 2 else 0.2


def news_points(count: int | None) -> float | None:
    if count is None or count < 0:
        return None
    if count < 20:
        return 1.0
    if count < 100:
        return 0.6
    if count < 500:
        return 0.3
    return 0.0


def coverage_points(provider_count: int | None) -> float | None:
    if provider_count is None or provider_count < 0:
        return None
    if provider_count == 0:
        return 1.0
    if provider_count == 1:
        return 0.5
    return 0.1


def disclosure_language(country: str | None) -> tuple[str | None, float | None]:
    if not country:
        return None, None
    if country in _ENGLISH:
        return "en", 0.0
    if country in _BILINGUAL:
        return _BILINGUAL[country], 0.5
    if country in _LOCAL:
        return _LOCAL[country], 1.0
    return None, None


def mis_tag(layers_block: Mapping[str, Any] | None, sector: str | None,
            industry: str | None) -> tuple[bool | None, float | None, str | None]:
    """(mis-tagged?, points, note). Unknown unless the classifier ran and classified."""
    if not layers_block or layers_block.get("status") != "classified":
        return None, None, None
    labels = {x.strip().casefold() for x in (sector, industry) if x}
    if not labels:
        return None, None, None
    tagged = not (labels & ELECTRICITY_LABELS)
    note = (f"classified as {layers_block.get('primary_layer')} but labelled "
            f"{' / '.join(x for x in (sector, industry) if x)}")
    return tagged, 1.0 if tagged else 0.0, note


def obscurity_block(
    *,
    analyst: Mapping[str, Any] | None,
    passive_block: Mapping[str, Any] | None,
    news: Mapping[str, Any] | None,
    layers_block: Mapping[str, Any] | None,
    sector: str | None,
    industry: str | None,
    listing_country: str | None,
    coverage: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The obscurity block (pure). ``analyst`` / ``news`` are ``{count, as_of, source…}``.

    ``coverage`` is ``research_coverage.coverage.obscurity_input`` (``{state: known |
    unknown, provider_count, providers, as_of, source, looked_at}``); ``None`` leaves the
    component out and the block exactly as before slice 21.7.
    """
    comps: list[dict[str, Any]] = []
    a_count = (analyst or {}).get("count")
    comps.append(_component(
        "analyst_count", a_count, analyst_points(a_count),
        source=(analyst or {}).get("source"), as_of=(analyst or {}).get("as_of"),
        basis="EPS analyst count of the newest consensus snapshot"))
    held = bool(passive_block) and passive_block.get("state") == "held"  # type: ignore[union-attr]
    pct = passive_block.get("pct_of_shares_outstanding") if held else None  # type: ignore[union-attr]
    try:
        pct_f: float | None = float(pct) if pct is not None else None
    except (TypeError, ValueError):
        pct_f = None
    comps.append(_component(
        "passive_ownership_pct", pct_f, passive_points(pct_f), source="SEC Form N-PORT",
        as_of=passive_block.get("holdings_as_of") if held else None,  # type: ignore[union-attr]
        basis="% of shares outstanding held by US-registered funds"))
    themed = passive_block.get("themed_fund_count") if held else None  # type: ignore[union-attr]
    comps.append(_component(
        "theme_fund_holders", themed, theme_points(themed), source="SEC Form N-PORT",
        as_of=passive_block.get("holdings_as_of") if held else None,  # type: ignore[union-attr]
        basis="listed fund holders whose name carries a theme flag"))
    n_count = (news or {}).get("count")
    comps.append(_component(
        "english_news_volume", n_count, news_points(n_count),
        source=(news or {}).get("source"), as_of=(news or {}).get("as_of"),
        basis="English-language articles, last 3 months (GDELT window)"))
    tagged, tag_points, tag_note = mis_tag(layers_block, sector, industry)
    comps.append(_component(
        "sector_mis_tag", tagged, tag_points, source="electricity layer classifier",
        basis="layer vs sector/industry label", note=tag_note))
    lang, lang_points = disclosure_language(listing_country)
    comps.append(_component(
        "primary_disclosure_language", lang, lang_points, source="exchange registry",
        basis=f"listing country {listing_country}" if listing_country else None))
    if coverage is not None:
        known = coverage.get("state") == "known"
        count = coverage.get("provider_count") if known else None
        count_i = int(count) if isinstance(count, int) else None
        names = [str(n) for n in coverage.get("providers") or [] if n]
        comps.append(_component(
            "independent_research_coverage", count_i, coverage_points(count_i),
            source=coverage.get("source"), as_of=coverage.get("as_of"),
            basis="research providers with a coverage page (" + str(
                coverage.get("looked_at") or "coverage index") + ")",
            note=(", ".join(names) if names else
                  ("none found in the indexed providers" if known else
                   "unknown: the coverage index has never been refreshed"))))

    available = [c for c in comps if c["state"] == AVAILABLE]
    score = (round(100 * sum(c["points"] for c in available) / len(available))
             if len(available) >= MIN_COMPONENTS else None)
    return {
        "version": SCORE_VERSION if coverage is not None else PREVIOUS_SCORE_VERSION,
        "score": score,
        "components_available": len(available),
        "components_total": len(comps),
        "components": comps,
        "note": (
            "Higher = fewer people are looking. Each component is shown with its source; "
            "a component with no data is left out, never counted as zero"
            + ("" if score is not None else
               f"; fewer than {MIN_COMPONENTS} components were available, so no score")
            + ". Obscurity is a discovery input, not a judgement of the company."
        ),
    }


__all__ = [
    "AVAILABLE",
    "MIN_COMPONENTS",
    "NOT_AVAILABLE",
    "PREVIOUS_SCORE_VERSION",
    "SCORE_VERSION",
    "analyst_points",
    "coverage_points",
    "disclosure_language",
    "mis_tag",
    "news_points",
    "obscurity_block",
    "passive_points",
    "theme_points",
]
