"""Commodities — what they are called, where their numbers live, and which a company sells.

V3.18.4/5. A report on a copper producer said nothing quantitative about copper. Fixing
that needs three things this module owns:

1. **A vocabulary.** The materials a mining or critical-materials company can produce, with
   the words documents use for them ("NdPr", "neodymium-praseodymium" → rare earths).
2. **Where the numbers are.** For each, the public, keyless series that carry benchmark
   prices (the IMF Primary Commodity Price System, served by FRED) and the U.S.
   Geological Survey's Mineral Commodity Summaries chapter (world mine production,
   reserves and the survey's price statistics by country).
3. **Which ones a company sells** — decided from the company's OWN documents, never from
   a ticker table. Southern Copper is a copper, molybdenum, silver and zinc producer
   because its 10-K says so, and a playbook that hard-coded that would be an answer key,
   not research.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass


@dataclass(frozen=True)
class Commodity:
    slug: str
    name: str
    #: Word patterns a document uses for it. Matched case-insensitively on word boundaries.
    patterns: tuple[str, ...]
    #: IMF Primary Commodity Price System series on FRED, or ``None``.
    fred_series: str | None = None
    fred_unit: str | None = None
    #: USGS Mineral Commodity Summaries chapter slug, or ``None``.
    usgs_slug: str | None = None


COMMODITIES: tuple[Commodity, ...] = (
    Commodity("copper", "copper", (r"copper",), "PCOPPUSDM", "USD per metric ton",
              "copper"),
    Commodity("molybdenum", "molybdenum", (r"molybdenum", r"moly"), None, None, "molybdenum"),
    Commodity("silver", "silver", (r"silver",), None, None, "silver"),
    Commodity("gold", "gold", (r"gold",), None, None, "gold"),
    Commodity("zinc", "zinc", (r"zinc",), "PZINCUSDM", "USD per metric ton", "zinc"),
    Commodity("lead", "lead", (r"lead concentrates?", r"refined lead"), "PLEADUSDM",
              "USD per metric ton", "lead"),
    Commodity("nickel", "nickel", (r"nickel",), "PNICKUSDM", "USD per metric ton", "nickel"),
    Commodity("cobalt", "cobalt", (r"cobalt",), None, None, "cobalt"),
    Commodity("lithium", "lithium", (r"lithium",), None, None, "lithium"),
    Commodity(
        "rare_earths",
        "rare earths",
        (r"rare[\s-]earths?", r"ndpr", r"neodymium", r"praseodymium", r"dysprosium",
         r"terbium", r"lanthanides?"),
        None,
        None,
        "rare-earths",
    ),
    Commodity("uranium", "uranium", (r"uranium", r"u3o8"), "PURANUSDM", "USD per pound",
              None),
    Commodity("iron_ore", "iron ore", (r"iron ore",), "PIORECRUSDM", "USD per dry metric ton",
              "iron-ore"),
    Commodity("aluminum", "aluminum", (r"alumin(?:i)?um", r"bauxite", r"alumina"),
              "PALUMUSDM", "USD per metric ton", "aluminum"),
    Commodity("tin", "tin", (r"tin concentrates?", r"refined tin"), "PTINUSDM",
              "USD per metric ton", "tin"),
    Commodity("platinum_group_metals", "platinum-group metals",
              (r"platinum", r"palladium", r"rhodium"), None, None, "platinum-group"),
    Commodity("graphite", "graphite", (r"graphite",), None, None, "graphite"),
    Commodity("manganese", "manganese", (r"manganese",), None, None, "manganese"),
    Commodity("vanadium", "vanadium", (r"vanadium",), None, None, "vanadium"),
    Commodity("antimony", "antimony", (r"antimony",), None, None, "antimony"),
    Commodity("tungsten", "tungsten", (r"tungsten",), None, None, "tungsten"),
    Commodity("gallium", "gallium", (r"gallium",), None, None, "gallium"),
    Commodity("germanium", "germanium", (r"germanium",), None, None, "germanium"),
)

BY_SLUG: dict[str, Commodity] = {c.slug: c for c in COMMODITIES}

_COMPILED: tuple[tuple[Commodity, re.Pattern[str]], ...] = tuple(
    (c, re.compile(r"\b(?:" + "|".join(c.patterns) + r")\b", re.IGNORECASE))
    for c in COMMODITIES
)


@dataclass(frozen=True)
class CommodityMention:
    commodity: Commodity
    mentions: int
    share: float

    def to_dict(self) -> dict[str, object]:
        return {
            "commodity": self.commodity.slug,
            "name": self.commodity.name,
            "mentions": self.mentions,
            "share": round(self.share, 3),
        }


#: A commodity must carry at least this share of all commodity mentions, and at least
#: this many mentions, to count as something the company sells. A copper miner's 10-K
#: names gold once in a list of by-products; it names copper hundreds of times.
MIN_SHARE = 0.05
MIN_MENTIONS = 5


def identify_commodities(
    texts: Iterable[str], *, limit: int = 4
) -> list[CommodityMention]:
    """The commodities a company's OWN documents talk about most, strongest first.

    Counting is deliberately crude — mentions, not revenue — because it only decides
    which commodities to RESEARCH. Revenue exposure by commodity is itself a question
    the research answers from the filing, with citations; this function never states it.
    """
    counts: Counter[str] = Counter()
    for text in texts:
        for commodity, pattern in _COMPILED:
            found = len(pattern.findall(text or ""))
            if found:
                counts[commodity.slug] += found
    total = sum(counts.values())
    if not total:
        return []
    ranked = [
        CommodityMention(BY_SLUG[slug], n, n / total)
        for slug, n in counts.most_common()
        if n >= MIN_MENTIONS and n / total >= MIN_SHARE
    ]
    return ranked[:limit]


def commodity_for(slug: str | None) -> Commodity | None:
    return BY_SLUG.get((slug or "").strip().lower())


__all__ = [
    "BY_SLUG",
    "COMMODITIES",
    "MIN_MENTIONS",
    "MIN_SHARE",
    "Commodity",
    "CommodityMention",
    "commodity_for",
    "identify_commodities",
]
