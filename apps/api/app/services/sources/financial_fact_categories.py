"""Shared financial-fact category classification — Phase 32A corrective.

Single source of truth for grouping a structured financial fact into a small
set of DISTINCT, financially useful semantic categories, so retention logic
(``company_evidence._prioritize_ir_items`` and
``llm.evidence_budget._apply_category_budget``) can prioritise CATEGORY
DIVERSITY within a bounded budget instead of a blind raw-count floor or raw
list-order ("whichever prose excerpt happened to be appended first").

Never company-specific: classification keys off the SAME neutral ``FIELD_*``
label vocabulary already used by ``primary_fact_parser`` /
``extracted_fact_validator``, and off the generic ``scope`` label produced by
``primary_document_extractor._infer_scope`` (a structural heading signal,
never a literal segment/brand name).

Categories (mission-specified):
  A. topline/profitability — revenue, operating profit / recurring operating
     profit, operating margin / recurring operating margin.
  B. earnings — net income / net profit.
  C. cash generation — operating cash flow, free cash flow / operating free
     cash flow.
  D. financial position — cash, debt / net debt / net cash, equity, total
     assets / total liabilities and their components.
  E. segment / business-group — ANY of the above metrics when reported at a
     KNOWN, non-Group scope. A segment-scoped operating margin is a
     genuinely different, independently useful datapoint from the Group-level
     operating margin, so scope always wins over field when both are known:
     a fact never collides with (or crowds out) its Group-level counterpart
     for retention purposes.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Sequence
from typing import TypeVar

CATEGORY_TOPLINE = "topline_profitability"
CATEGORY_EARNINGS = "earnings"
CATEGORY_CASH = "cash_generation"
CATEGORY_POSITION = "financial_position"
CATEGORY_SEGMENT = "segment_business_group"
CATEGORY_OTHER = "other_financial"

# Field-name sets mirror ``primary_fact_parser.FIELD_*`` / the normalized
# labels ``extracted_fact_validator`` promotes a table/prose candidate to.
_TOPLINE_FIELDS = frozenset(
    {
        "revenue",
        "operating_profit",
        "recurring_operating_profit",
        "operating_margin",
        "recurring_operating_margin",
    }
)
_EARNINGS_FIELDS = frozenset({"net_income"})
_CASH_FIELDS = frozenset(
    {"operating_cash_flow", "free_cash_flow", "operating_free_cash_flow"}
)
_POSITION_FIELDS = frozenset(
    {
        "cash_and_equivalents",
        "total_debt",
        "net_debt",
        "net_cash",
        "total_equity",
        "total_assets",
        "total_liabilities",
        "short_term_debt",
        "long_term_debt",
        "total_current_assets",
        "total_non_current_assets",
    }
)

_FIELD_CATEGORY: dict[str, str] = {}
for _f in _TOPLINE_FIELDS:
    _FIELD_CATEGORY[_f] = CATEGORY_TOPLINE
for _f in _EARNINGS_FIELDS:
    _FIELD_CATEGORY[_f] = CATEGORY_EARNINGS
for _f in _CASH_FIELDS:
    _FIELD_CATEGORY[_f] = CATEGORY_CASH
for _f in _POSITION_FIELDS:
    _FIELD_CATEGORY[_f] = CATEGORY_POSITION


def _normalized_scope(scope: str | None) -> str:
    return (scope or "").strip().lower()


def primary_fact_field(primary_fact: object) -> str | None:
    """Read the ``field`` name off an ``EvidenceItem.primary_fact`` payload.

    ``primary_fact`` is EITHER a live ``PrimaryFactRef`` instance (the shape
    it has right after construction in ``company_ir.py`` /
    ``company_evidence.py``) OR a plain dict (the shape it has after a
    JSON/``model_dump`` round-trip, e.g. persisted state or an API
    response) — both occur across the pipeline, so this accessor handles
    either without requiring the caller to know which.
    """
    if primary_fact is None:
        return None
    if isinstance(primary_fact, dict):
        value = primary_fact.get("field")
    else:
        value = getattr(primary_fact, "field", None)
    return str(value) if value else None


_UNKNOWN_PERIOD_RANK = 10_000


def primary_fact_period_rank(primary_fact: object) -> int:
    """More-recent-first rank for a structured fact's own reporting period.

    Lower is better (sorts earlier). A 4-digit year in ``primary_fact.period``
    yields ``-year`` (a later year is a smaller/more-negative key, so it
    sorts first); a missing/unparseable period sorts LAST — never preferred
    over one with a known, dated figure. Handles both a live ``PrimaryFactRef``
    and its plain-dict round-tripped form, same as ``primary_fact_field``.

    Phase 32A corrective (LVMH H1 2026) — ``financial_fact_diversity_key``
    intentionally has NO period component, so a comparison-period and a
    current-period fact for the SAME field/scope compete for one round-robin
    slot in EVERY caller of ``select_category_diverse`` over financial facts
    (``company_evidence._prioritize_ir_items`` AND
    ``llm.evidence_budget._apply_category_budget``). Without sorting
    candidates by period-recency FIRST, ties fell back to each item's
    incidental position in the upstream validator's own (label,
    period-as-STRING) sort, which happens to place an earlier year first —
    so a real, live MC/LVMH run showed the STALE 2025 ``total_equity``
    figure reach Council evidence while the CURRENT 2026 figure was
    silently dropped. Both call sites must use this so neither one
    reintroduces the bug the other already fixed.
    """
    if primary_fact is None:
        return _UNKNOWN_PERIOD_RANK
    raw = (
        primary_fact.get("period")
        if isinstance(primary_fact, dict)
        else getattr(primary_fact, "period", None)
    )
    if raw:
        m = re.search(r"(19|20)\d{2}", str(raw))
        if m:
            return -int(m.group(0))
    return _UNKNOWN_PERIOD_RANK


def financial_fact_category(field: str | None, scope: str | None) -> str:
    """Classify one structured fact into a distinct financial category.

    A fact reported at a KNOWN, non-Group scope is ALWAYS
    ``CATEGORY_SEGMENT`` regardless of field (see module docstring). An
    unrecognized field with no segment scope falls back to
    ``CATEGORY_OTHER`` — still countable, still bounded, never dropped
    outright by the classifier itself.
    """
    norm_scope = _normalized_scope(scope)
    if norm_scope and norm_scope != "group":
        return CATEGORY_SEGMENT
    return _FIELD_CATEGORY.get((field or "").strip().lower(), CATEGORY_OTHER)


def financial_fact_diversity_key(
    field: str | None, scope: str | None
) -> tuple[str, str, str]:
    """A ``(category, sub_key, sub_key_2)`` tuple for round-robin diversity.

    Within ``CATEGORY_SEGMENT`` the sub-key includes the scope label so two
    DIFFERENT segments/business-units each get their own diversity slot
    (e.g. a Jewellery-Maisons margin and a Specialist-Watchmakers result are
    never treated as "the same category slot"); within every other category
    the sub-key is the field, so distinct metrics (revenue vs operating
    margin) each get their own slot too, rather than only the first-listed
    metric in a category ever surviving.
    """
    category = financial_fact_category(field, scope)
    norm_field = (field or "").strip().lower()
    if category == CATEGORY_SEGMENT:
        return (category, _normalized_scope(scope), norm_field)
    return (category, norm_field, "")


_T = TypeVar("_T")


def select_category_diverse(
    items: Sequence[_T],
    *,
    cap: int,
    diversity_key_of: Callable[[_T], Iterable[object]],
) -> list[_T]:
    """Bounded, deterministic category-diverse selection.

    ``items`` is assumed ALREADY ordered by the caller's own priority/rank
    (tier, confidence, original order, ...) — this function only decides
    WHICH ones survive within ``cap``; it never reorders survivors relative
    to each other, and the caller is free to re-sort the result for final
    presentation.

    Algorithm (mission-specified, section 4): round 1 takes the FIRST
    (best-ranked) item for each not-yet-seen diversity key, in input order;
    once every present key has one representative, round 2 takes the NEXT
    item for each key that still has more, and so on. This means a category
    with many facts never crowds out a category with only one representative
    before every present category/sub-key has at least one slot, while extra
    headroom (once every key is covered) still goes to whichever categories
    genuinely have more distinct facts — never an arbitrary raw-count floor,
    never pure list order.
    """
    if cap <= 0:
        return []
    buckets: dict[tuple, list[_T]] = {}
    order: list[tuple] = []
    for it in items:
        key = tuple(diversity_key_of(it))
        bucket = buckets.get(key)
        if bucket is None:
            bucket = []
            buckets[key] = bucket
            order.append(key)
        bucket.append(it)

    selected: list[_T] = []
    round_idx = 0
    while len(selected) < cap:
        progressed = False
        for key in order:
            bucket = buckets[key]
            if round_idx < len(bucket):
                selected.append(bucket[round_idx])
                progressed = True
                if len(selected) >= cap:
                    break
        if not progressed:
            break
        round_idx += 1
    return selected


__all__ = [
    "CATEGORY_TOPLINE",
    "CATEGORY_EARNINGS",
    "CATEGORY_CASH",
    "CATEGORY_POSITION",
    "CATEGORY_SEGMENT",
    "CATEGORY_OTHER",
    "financial_fact_category",
    "financial_fact_diversity_key",
    "select_category_diverse",
    "primary_fact_field",
]
