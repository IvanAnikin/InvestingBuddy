"""Ranked evidence packs — open-web W4 (spec §17.1).

The Council receives the best evidence; the corpus keeps everything useful. Pack items
are chunks and verified leads, each scored with VERSIONED weights:

``score = w1·source_class_prior + w2·relevance + w3·freshness + w4·company_specificity
+ w5·claim_coverage + w6·primary_preference − w7·duplicate_origin_penalty
− w8·injection_suspect``

Selection is greedy and deterministic (claim coverage and the duplicate-origin penalty
depend on what was already chosen; ties keep the input order). Constraints:

* at most ``MAX_ITEMS_PER_ORIGIN`` (3) items per origin — the rest are DROPPED and
  counted, never silently lost;
* at least one primary item where one exists: the best primary item is placed FIRST,
  so no character budget downstream can trim it away;
* an ``injection_suspect`` item is down-weighted, not removed (it is still evidence);
* the investigator's existing ceilings (``MAX_EVIDENCE_CHARS``, ``MAX_ITEMS_PER_TOOL``)
  still apply after the pack is ordered.

Nothing here reads a search snippet or a title: a pack item carries ids, classes,
origins, dates and the stored text's claim fields only (PI-09).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date

from app.services import research_fields as rf
from app.services.web_research.classify import CLASS_TABLE
from app.services.web_research.trust import FILING_CLASSES, ISSUER_CLASSES, independence_key

PACK_WEIGHTS_VERSION = "2026-10-04.1"


@dataclass(frozen=True)
class PackWeights:
    version: str = PACK_WEIGHTS_VERSION
    source_class_prior: float = 1.0
    relevance: float = 1.0
    freshness: float = 0.5
    company_specificity: float = 0.5
    claim_coverage: float = 0.5
    primary_preference: float = 1.0
    duplicate_origin_penalty: float = 0.5
    injection_suspect: float = 2.0


DEFAULT_WEIGHTS = PackWeights()
MAX_ITEMS_PER_ORIGIN = 3
#: Freshness decays linearly to zero over this many days; undated items score this.
FRESHNESS_HORIZON_DAYS = 5 * 365
UNDATED_FRESHNESS = 0.3

#: Prior by tier (spec §13.1 table), applied through the class → tier mapping.
_TIER_PRIOR: dict[str, float] = {
    "T1_primary_filing": 1.0,
    "T1_primary_company_source": 0.8,
    "T2_regulator_or_gov": 0.9,
    "T3_industry_specialist": 0.8,
    "T4_quality_media": 0.6,
    "T5_api_aggregator": 0.2,
}

DROP_ORIGIN_CAP = "origin_cap"


@dataclass(frozen=True)
class PackItem:
    """One candidate for the pack. ``key`` is the caller's id (an evidence id)."""

    key: str
    source_class: str | None = None
    origin_key: str | None = None
    #: The retrieval score, normalised by the caller or here (0..1 after normalising).
    relevance: float | None = None
    published_at: date | None = None
    #: 1.0 the subject's own document, 0.5 a document naming it, 0.0 theme context.
    company_specificity: float = 0.5
    #: Research fields the item's stored text mentions (``research_fields``).
    claim_keys: tuple[str, ...] = ()
    injection_suspect: bool = False
    #: Admitted through a subject row (the document MENTIONS the company): never primary.
    via_subject: bool = False
    #: Platform evidence (a typed fact, the company's own corpus filing) as opposed to an
    #: open-web item. A web item with NO origin (a legacy version stored before origins
    #: existed) is not primary: nothing says whose it is.
    platform: bool = False


@dataclass
class PackResult:
    items: list[PackItem]
    dropped: dict[str, list[str]] = field(default_factory=dict)
    scores: dict[str, float] = field(default_factory=dict)
    weights_version: str = PACK_WEIGHTS_VERSION


def is_primary(item: PackItem, issuer_origin: str | None = None) -> bool:
    """A filing or the issuer's own material.

    Never a mention-scope hit (its class is the mentioning document's, not the
    company's), and never a company page that is not THIS run's company's: a web item
    with an origin is primary only when that origin is the run's verified issuer
    (``issuer_origin``) — in a theme run, where there is none, no web item is primary
    (review M4). Platform evidence carries no web origin and keeps its class.
    """
    if item.via_subject:
        return False
    if item.source_class not in FILING_CLASSES and item.source_class not in ISSUER_CLASSES:
        return False
    if item.origin_key is None:
        return item.platform
    return issuer_origin is not None and item.origin_key == issuer_origin


def source_class_prior(source_class: str | None) -> float:
    tier = CLASS_TABLE.get(source_class or "", (None, None))[0]
    return _TIER_PRIOR.get(tier or "", 0.2)


def freshness(published_at: date | None, *, today: date) -> float:
    if published_at is None:
        return UNDATED_FRESHNESS
    age = max(0, (today - published_at).days)
    return max(0.0, 1.0 - age / FRESHNESS_HORIZON_DAYS)


def claim_keys_for(text: str | None) -> tuple[str, ...]:
    """The research fields a stored text mentions — the pack's claim-coverage unit."""
    return tuple(sorted(rf.fields_mentioned(text)))


def base_score(
    item: PackItem,
    *,
    weights: PackWeights,
    today: date,
    top: float,
    issuer_origin: str | None = None,
) -> float:
    relevance = (item.relevance or 0.0) / top if top > 0 else 0.5
    return (
        weights.source_class_prior * source_class_prior(item.source_class)
        + weights.relevance * relevance
        + weights.freshness * freshness(item.published_at, today=today)
        + weights.company_specificity * item.company_specificity
        + weights.primary_preference * (1.0 if is_primary(item, issuer_origin) else 0.0)
        - weights.injection_suspect * (1.0 if item.injection_suspect else 0.0)
    )


def build_pack(
    candidates: Sequence[PackItem],
    *,
    weights: PackWeights = DEFAULT_WEIGHTS,
    max_per_origin: int = MAX_ITEMS_PER_ORIGIN,
    max_items: int | None = None,
    today: date | None = None,
    issuer_origin: str | None = None,
) -> PackResult:
    """Rank and cap ``candidates``. Deterministic for a given input.

    ``today`` defaults to the newest publication date among the candidates — a property
    of the INPUT, so the same evidence always ranks the same however late it is read
    (the clock is never consulted). The origin cap and the duplicate penalty count
    independence keys, so a page claiming another origin shares that origin's budget.
    """
    pool = list(dict.fromkeys(candidates))
    day = today or max((c.published_at for c in pool if c.published_at), default=date(1970, 1, 1))
    top = max((c.relevance or 0.0 for c in pool), default=0.0)
    base = {
        c.key: base_score(c, weights=weights, today=day, top=top, issuer_origin=issuer_origin)
        for c in pool
    }
    order = {c.key: index for index, c in enumerate(pool)}
    origin_of = {c.key: independence_key(c.origin_key) for c in pool}
    chosen: list[PackItem] = []
    per_origin: dict[str, int] = {}
    covered: set[str] = set()
    result = PackResult(items=chosen, weights_version=weights.version)
    remaining = list(pool)
    while remaining:
        best: PackItem | None = None
        best_score = 0.0
        for item in remaining:
            fresh_keys = set(item.claim_keys) - covered
            coverage = len(fresh_keys) / len(item.claim_keys) if item.claim_keys else 0.0
            origin = origin_of[item.key]
            repeats = per_origin.get(origin, 0) if origin else 0
            score = (
                base[item.key]
                + weights.claim_coverage * coverage
                - weights.duplicate_origin_penalty * repeats
            )
            if best is None or score > best_score + 1e-12 or (
                abs(score - best_score) <= 1e-12 and order[item.key] < order[best.key]
            ):
                best, best_score = item, score
        assert best is not None
        remaining.remove(best)
        origin = origin_of[best.key]
        if origin and per_origin.get(origin, 0) >= max_per_origin:
            result.dropped.setdefault(DROP_ORIGIN_CAP, []).append(best.key)
            continue
        if max_items is not None and len(chosen) >= max_items:
            result.dropped.setdefault("max_items", []).append(best.key)
            continue
        chosen.append(best)
        result.scores[best.key] = round(best_score, 6)
        if origin:
            per_origin[origin] = per_origin.get(origin, 0) + 1
        covered.update(best.claim_keys)

    # At least one primary item where one exists — FIRST, so no budget can trim it. A
    # suspect page is never FORCED in (it may still rank in on merit): forcing the one
    # item an attacker controls to the top is the wrong tie-break (review M4).
    primaries = [c for c in pool if is_primary(c, issuer_origin) and not c.injection_suspect]
    if primaries:
        in_pack = [c for c in chosen if c in primaries]
        lead = in_pack[0] if in_pack else max(
            primaries, key=lambda c: (base[c.key], -order[c.key])
        )
        if lead in chosen:
            chosen.remove(lead)
        else:
            for reason in list(result.dropped):
                if lead.key in result.dropped[reason]:
                    result.dropped[reason].remove(lead.key)
                    if not result.dropped[reason]:
                        del result.dropped[reason]
            lead_origin = origin_of[lead.key]
            same_origin = [
                c for c in chosen if lead_origin and origin_of[c.key] == lead_origin
            ]
            if len(same_origin) >= max_per_origin:
                # Keep the origin cap: the primary item displaces its origin's weakest.
                evicted = min(same_origin, key=lambda c: result.scores.get(c.key, 0.0))
                chosen.remove(evicted)
                result.dropped.setdefault(DROP_ORIGIN_CAP, []).append(evicted.key)
            elif max_items is not None and len(chosen) >= max_items and chosen:
                # Evict the weakest NON-primary item; a primary is never the casualty.
                pool_evictable = [c for c in chosen if not is_primary(c, issuer_origin)] or chosen
                evicted = min(pool_evictable, key=lambda c: result.scores.get(c.key, 0.0))
                chosen.remove(evicted)
                result.dropped.setdefault("max_items", []).append(evicted.key)
            result.scores[lead.key] = round(base[lead.key], 6)
        chosen.insert(0, lead)
    return result


__all__ = [
    "DEFAULT_WEIGHTS",
    "DROP_ORIGIN_CAP",
    "MAX_ITEMS_PER_ORIGIN",
    "PACK_WEIGHTS_VERSION",
    "PackItem",
    "PackResult",
    "PackWeights",
    "build_pack",
    "claim_keys_for",
    "freshness",
    "is_primary",
    "source_class_prior",
]
