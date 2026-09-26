"""The official U.S. List of Critical Minerals — V3.19.1.

WHY THIS EXISTS
===============
V3.18 accepted with ``KNOWN LIMITATION 1``: the thesis dimension ``critical_materials``
asks *whether the company's products are on official critical-mineral lists*, and the
platform held no such list, so the answer was "not established" for every company — MP
Materials, whose product is rare-earth oxide, included. A regex over the company's own
wording ("a critical input") cannot answer it: a company calling its product critical is
marketing, and a designation is a legal act by a government.

WHAT THIS IS
============
The United States Geological Survey's List of Critical Minerals, published by the
Secretary of the Interior in the Federal Register under section 7002 of the Energy Act of
2020, as code-defined reference data with its provenance — the same pattern as
``verified_issuer_sources``: reviewed in a pull request, never scraped per request. Each
version is kept beside the others; a newer list never overwrites an older one.

* **2022** — 50 minerals, 87 FR 10381, 2022-02-24.
* **2025** — 60 minerals (the 50 of 2022 plus boron, copper, lead, metallurgical coal,
  phosphate, potash, rhenium, silicon, silver and uranium), 90 FR 50494, 2025-11-07,
  document 2025-19813. Every one of the 60 names below was checked against the text of
  the official PDF on govinfo.gov on 2026-09-26 (``verify_against_text``).

The CURRENT list is the newest one. A designation names the list version, its date and
its official URL, so a reader can check it.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

from app.services.sources.taxonomy import T2_REGULATOR_OR_GOV


@dataclass(frozen=True)
class CriticalMineralsList:
    version: str
    published: str
    citation: str
    document_number: str | None
    source_url: str
    publisher: str
    minerals: tuple[str, ...]
    retrieved_at: str
    tier: str = T2_REGULATOR_OR_GOV

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "published": self.published,
            "citation": self.citation,
            "document_number": self.document_number,
            "source_url": self.source_url,
            "publisher": self.publisher,
            "tier": self.tier,
            "mineral_count": len(self.minerals),
        }


_LIST_2022_MINERALS: tuple[str, ...] = (
    "aluminum", "antimony", "arsenic", "barite", "beryllium", "bismuth", "cerium",
    "cesium", "chromium", "cobalt", "dysprosium", "erbium", "europium", "fluorspar",
    "gadolinium", "gallium", "germanium", "graphite", "hafnium", "holmium", "indium",
    "iridium", "lanthanum", "lithium", "lutetium", "magnesium", "manganese", "neodymium",
    "nickel", "niobium", "palladium", "platinum", "praseodymium", "rhodium", "rubidium",
    "ruthenium", "samarium", "scandium", "tantalum", "tellurium", "terbium", "thulium",
    "tin", "titanium", "tungsten", "vanadium", "ytterbium", "yttrium", "zinc", "zirconium",
)

_ADDED_2025: tuple[str, ...] = (
    "boron", "copper", "lead", "metallurgical coal", "phosphate", "potash", "rhenium",
    "silicon", "silver", "uranium",
)

LIST_2022 = CriticalMineralsList(
    version="2022",
    published="2022-02-24",
    citation="87 FR 10381",
    document_number="2022-04027",
    source_url="https://www.federalregister.gov/documents/2022/02/24/2022-04027/2022-final-list-of-critical-minerals",
    publisher="U.S. Geological Survey (Department of the Interior)",
    minerals=_LIST_2022_MINERALS,
    retrieved_at="2026-09-26",
)

LIST_2025 = CriticalMineralsList(
    version="2025",
    published="2025-11-07",
    citation="90 FR 50494",
    document_number="2025-19813",
    source_url="https://www.govinfo.gov/content/pkg/FR-2025-11-07/pdf/2025-19813.pdf",
    publisher="U.S. Geological Survey (Department of the Interior)",
    minerals=tuple(sorted((*_LIST_2022_MINERALS, *_ADDED_2025))),
    retrieved_at="2026-09-26",
)

#: Every version, oldest first. The current list is the last.
LISTS: tuple[CriticalMineralsList, ...] = (LIST_2022, LIST_2025)
CURRENT_LIST: CriticalMineralsList = LISTS[-1]

#: A platform commodity slug (``macro.commodities``) → the list entries it covers. A
#: composite commodity is designated when ANY of its members is: "rare earths" is how
#: companies and the platform name a product the list enumerates element by element.
_COMMODITY_MEMBERS: dict[str, tuple[str, ...]] = {
    "rare_earths": (
        "cerium", "dysprosium", "erbium", "europium", "gadolinium", "holmium", "lanthanum",
        "lutetium", "neodymium", "praseodymium", "samarium", "terbium", "thulium",
        "ytterbium", "yttrium", "scandium",
    ),
    "platinum_group_metals": ("platinum", "palladium", "rhodium", "iridium", "ruthenium"),
}


def members_for(slug: str) -> tuple[str, ...]:
    key = (slug or "").strip().lower()
    if key in _COMMODITY_MEMBERS:
        return _COMMODITY_MEMBERS[key]
    return (key.replace("_", " "),)


def designation(
    slug: str, *, lists: Sequence[CriticalMineralsList] = LISTS
) -> dict[str, Any] | None:
    """Whether a commodity is officially designated critical, on the NEWEST list naming it.

    ``None`` when no list names it — the honest answer for gold, iron ore or molybdenum,
    which is "not designated", not "unknown".
    """
    key = (slug or "").strip().lower()
    members = members_for(key)
    for listing in reversed(tuple(lists)):
        named = [m for m in members if m in listing.minerals]
        if named:
            return {
                "commodity": key,
                "designated": True,
                # A composite names the ENTRIES the list has for it, not the elements this
                # company produces: "the 16 rare-earth element entries", never "neodymium,
                # scandium, yttrium …" as if the company produced each.
                "listed_as": (
                    f"{len(named)} list entries for {key.replace('_', ' ')}"
                    if key in _COMMODITY_MEMBERS
                    else named[0]
                ),
                "list_entries": named,
                "list": listing.to_dict(),
                "current_list": listing is CURRENT_LIST,
            }
    return None


def designations_for(slugs: Iterable[str]) -> list[dict[str, Any]]:
    """Designation per commodity slug; a commodity no list names is reported as such."""
    out: list[dict[str, Any]] = []
    for slug in dict.fromkeys(s for s in slugs if s):
        found = designation(slug)
        out.append(
            found
            if found is not None
            else {
                "commodity": slug,
                "designated": False,
                "list": CURRENT_LIST.to_dict(),
                "current_list": True,
            }
        )
    return out


def verify_against_text(
    text: str, listing: CriticalMineralsList = CURRENT_LIST
) -> list[str]:
    """Names in ``listing`` that the official document's text does NOT contain.

    An empty list means every name was found. Used in acceptance against the bytes of the
    official document, never at request time.
    """
    low = re.sub(r"\s+", " ", (text or "").lower())
    return [name for name in listing.minerals if name not in low]


__all__ = [
    "CURRENT_LIST",
    "CriticalMineralsList",
    "LISTS",
    "LIST_2022",
    "LIST_2025",
    "designation",
    "designations_for",
    "members_for",
    "verify_against_text",
]
