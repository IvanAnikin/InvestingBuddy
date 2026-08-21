"""
Deterministic evidence-pack budgeter — Phase 29B.2 (+ Phase 32A Slice 2).

Compresses a built ``EvidencePack`` so a larger primary-source pack (now that
Phase 29B.2 can add real annual-report excerpts + parsed facts) cannot balloon
the council prompt and trip the Azure OpenAI TPM quota — which was already
partially failing large AAPL packs. It is a pure, deterministic transform: same
input → same output, no model call.

Two selection strategies live here:

  * **Flat (Phase 29B.2, default):** dedup → rank by (tier, factual-excerpt
    bonus, order) → truncate to ``max_items`` → bound chars → re-id. Category
    -blind; a flood of high-tier catalyst/news events can evict lower-tier
    financial datapoints.
  * **Category-aware (Phase 32A Slice 2, gated by
    ``llm_council_evidence_budgets_enabled``):** classifies every item into a
    stable category, reserves a FLOOR of structured financial-fact slots, and
    CAPS price/trend + news categories so news volume can never consume the whole
    pack and structured SEC/XBRL facts always survive. Adds a near-duplicate news
    dedup (normalised title) on top of the exact-hash dedup.

The flat path is preserved byte-for-byte so the whole Phase 28A/29A/29B test
suite is unchanged when the flag is off (the default).

Source gaps (``known_gaps``) are compressed for duplicates but **never** fully
dropped — a shrunk pack must still tell the council what is missing. Raw document
text / full JSON is never introduced here; the budgeter only ever *removes* or
*trims*, never adds content.
"""

from __future__ import annotations

import hashlib
import re

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.llm.schemas import (
    TIER_T1_PRIMARY_COMPANY_SOURCE,
    EvidenceItem,
    EvidencePack,
)
from app.services.sources.financial_fact_categories import (
    financial_fact_diversity_key,
    primary_fact_field,
    primary_fact_period_rank,
    select_category_diverse,
)
from app.services.sources.taxonomy import tier_rank

# Content types that carry a real factual excerpt (worth more than metadata).
_METADATA_QUALITIES = {"metadata_only", "link_metadata_only"}

# ── Evidence categories (Phase 32A Slice 2) ────────────────────────────────
CATEGORY_COMPANY_IDENTITY = "company_identity"
CATEGORY_FINANCIAL_FACT = "financial_fact"
CATEGORY_STATEMENT_TABLE = "statement_table_content"
CATEGORY_PRIMARY_DOCUMENT = "primary_document"
CATEGORY_FINANCIAL_SUMMARY = "financial_summary"
CATEGORY_PRICE_TREND_METRIC = "price_trend_metric"
CATEGORY_COMPANY_PRESS = "company_press"
CATEGORY_REGULATOR_EVENT = "regulator_event"
CATEGORY_MATERIAL_NEWS = "material_news"
CATEGORY_LOW_TIER_NEWS = "low_tier_news"
CATEGORY_SOURCE_REFERENCE = "source_reference"

# News-ish categories the aggregate ``news_cap`` applies to. Company press +
# regulator events are primary/regulator sources and are intentionally excluded.
_NEWS_CATEGORIES = frozenset({CATEGORY_MATERIAL_NEWS, CATEGORY_LOW_TIER_NEWS})

# Structured SEC/XBRL statement-fact + high-confidence issuer-fact source types.
_FINANCIAL_FACT_TYPES = frozenset(
    {"sec_financial_statement", "company_filing", "company_ir_financial_fact"}
)
# Derived / price / market / trend metric items (Slice-2 tier-split + legacy).
_PRICE_TREND_TYPES = frozenset(
    {
        "derived_financial_metric",
        "market_metric",
        "price_metric",
        "trend_signal",
        "financial_snapshot",
    }
)
# Connector-extracted primary-document excerpt / validated-fact source types.
# ``sec_filing_financial_fact`` (Phase 32A Slice 5B.1) is a fact validated from a
# table inside the issuer's OWN SEC filing body. It is listed here so it is
# budgeted as primary-document evidence rather than falling through to the
# lowest-priority ``source_reference`` bucket and being dropped first under
# pressure. It deliberately does NOT join ``_FINANCIAL_FACT_TYPES``: the
# structured SEC/XBRL facts stay authoritative and keep their own floor.
_PRIMARY_DOCUMENT_TYPES = frozenset(
    {
        "company_ir_annual_report_text",
        "company_ir_annual_report_excerpt",
        "company_ir_business_description",
        "company_ir_risk_excerpt",
        "sec_filing_financial_fact",
    }
)
# Statement/table-derived financial content (Phase 32A Problem C):
# ``company_ir_statement_excerpt`` is either a prose excerpt whose heading
# classified as a balance sheet / cash-flow statement / income statement /
# segment note, or a table row that matched a known financial-statement label
# but was demoted short of the stricter validated-fact bar
# (``extracted_fact_validator``). Classified BEFORE ``_PRIMARY_DOCUMENT_TYPES``
# (whose broad ``"excerpt" in st`` check would otherwise swallow it) so it gets
# its own budget category instead of losing an order tie-break against generic
# narrative excerpts within ``CATEGORY_PRIMARY_DOCUMENT``.
_STATEMENT_TABLE_TYPES = frozenset({"company_ir_statement_excerpt"})
# Price/market/trend field names (so a legacy financial-snapshot item whose
# ``source_type`` is a provider name still classifies as a price/trend metric).
_PRICE_TREND_FIELDS = frozenset(
    {
        "latest_close",
        "market_cap_usd_m",
        "market_cap_mln",
        "enterprise_value_usd_m",
        "enterprise_value_mln",
        "ebitda_ttm_usd_m",
        "revenue_ttm_usd_m",
        "pe_ratio",
        "week52_high",
        "week52_low",
        "52_week_high",
        "52_week_low",
        "trend_signal",
    }
)

_MATERIALITY_RANK = {"high": 0, "medium": 1, "low": 2, "irrelevant": 3}

_WS_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^a-z0-9 ]+")


def _excerpt_hash(text: str | None) -> str:
    if not text:
        return ""
    return hashlib.sha1(text.strip().lower().encode("utf-8", "replace")).hexdigest()[:16]


def _dedup_key(item: EvidenceItem) -> tuple[str, str, str]:
    url = (item.url or "").split("?")[0].strip().lower()
    title = (item.title or "").strip().lower()
    return (url, title, _excerpt_hash(item.excerpt))


def _semantic_fact_key(item: EvidenceItem) -> tuple[str, str, str, str, str, str] | None:
    """Cross-document dedup key for a STRUCTURED financial fact (Phase 32A
    corrective, Problem 8): ``None`` for anything without a ``primary_fact``
    (the exact-hash ``_dedup_key`` already handles those).

    The same figure can arrive via more than one document for one issuer (an
    HTML press release AND the PDF annual report both stating Group revenue,
    say). Two items collapse to one ONLY when metric + scope + period +
    currency + scale + the numeric value itself all agree — a genuine
    disagreement (same metric/scope/period, different value) is a real
    conflict and must stay explicit as two distinct items, never silently
    merged or overwritten.
    """
    fact = item.primary_fact
    if not fact:
        return None
    numeric_value = fact.get("numeric_value")
    if numeric_value is None:
        return None
    return (
        str(fact.get("field") or ""),
        str(fact.get("scope") or ""),
        str(fact.get("period") or ""),
        str(fact.get("currency") or "").upper(),
        str(fact.get("scale") or "").lower(),
        f"{float(numeric_value):.4f}",
    )


def _financial_fact_field(item: EvidenceItem) -> str | None:
    """Best-effort metric-field name for category-diversity classification.

    Prefers the structured ``primary_fact.field`` (company-IR / SEC filing-body
    facts); a plain SEC/XBRL ``sec_financial_statement``/``company_filing`` item
    carries no ``primary_fact`` but names its metric in ``fields_supported`` —
    used as a fallback so those items still classify (an unrecognized name
    degrades to ``CATEGORY_OTHER``, never an error).
    """
    field = primary_fact_field(item.primary_fact)
    if field:
        return field
    fields = item.fields_supported or []
    return fields[0] if fields else None


def _is_factual_excerpt(item: EvidenceItem) -> bool:
    """True when the item carries real excerpt text (not metadata-only)."""
    if (item.data_quality or "") in _METADATA_QUALITIES:
        return False
    return bool(item.excerpt and item.excerpt.strip())


def _financial_fact_period_rank(item: EvidenceItem) -> int:
    """More-recent-first rank for a structured financial fact's own period.

    Thin wrapper over the shared ``primary_fact_period_rank`` (see its
    docstring for the full rationale) — falls back to the top-level
    ``item.period`` field when the item carries no ``primary_fact`` payload
    at all, a shape only this ``EvidenceItem`` type has.
    """
    if item.primary_fact is not None:
        return primary_fact_period_rank(item.primary_fact)
    if item.period:
        return primary_fact_period_rank({"period": item.period})
    return primary_fact_period_rank(None)


def _rank_key(item: EvidenceItem, order: int) -> tuple[int, int, int]:
    tier = item.content_tier or item.source_tier
    # Lower is better: tier rank (1=T1 best), then metadata penalty, then order.
    return (tier_rank(tier), 0 if _is_factual_excerpt(item) else 1, order)


def _compress_gaps(gaps: list[str], *, limit: int = 30) -> list[str]:
    """De-duplicate gap messages (case-insensitive), preserving order + bound."""
    seen: dict[str, str] = {}
    for g in gaps:
        if not g:
            continue
        key = g.strip().lower()
        if key not in seen:
            seen[key] = g
        if len(seen) >= limit:
            break
    return list(seen.values())


# ---------------------------------------------------------------------------
# Category-aware selection (Phase 32A Slice 2)
# ---------------------------------------------------------------------------


def evidence_category(item: EvidenceItem) -> str:
    """Classify an evidence item into a stable budget category.

    Pure function of ``content_tier`` / ``source_type`` / ``data_quality`` /
    ``fields_supported``. Metadata-only references NEVER classify as a financial
    fact (CFR invariant) — they are always ``source_reference``.
    """
    dq = (item.data_quality or "").strip().lower()
    st = (item.source_type or "").strip().lower()
    tier = item.content_tier or item.source_tier
    rank = tier_rank(tier)
    fields = {str(f).strip().lower() for f in (item.fields_supported or [])}

    # 1. Metadata-only references are never facts — highest-priority rule.
    if dq in _METADATA_QUALITIES:
        return CATEGORY_SOURCE_REFERENCE

    # 2. Structured SEC/XBRL statement facts + high-confidence issuer facts.
    if st in _FINANCIAL_FACT_TYPES:
        return CATEGORY_FINANCIAL_FACT

    # 3. Derived / price / market / trend metrics.
    if st in _PRICE_TREND_TYPES or (fields & _PRICE_TREND_FIELDS):
        return CATEGORY_PRICE_TREND_METRIC

    # 4. Financial-data availability summary (re-presentation, no new numbers).
    if st == "financial_data_summary":
        return CATEGORY_FINANCIAL_SUMMARY

    # 4b. Statement/table-derived financial content (Problem C) — checked BEFORE
    # the broad "excerpt" substring match below so it does not fall into the
    # generic primary-document bucket and lose its priority.
    if st in _STATEMENT_TABLE_TYPES:
        return CATEGORY_STATEMENT_TABLE

    # 5. Connector-extracted primary-document excerpts.
    if st in _PRIMARY_DOCUMENT_TYPES or "excerpt" in st or "annual_report" in st:
        return CATEGORY_PRIMARY_DOCUMENT

    # 6. Catalyst / news / event items.
    is_catalyst = "catalyst" in fields or "event" in st or "news" in st or st == "catalyst_event"
    if is_catalyst:
        if tier == TIER_T1_PRIMARY_COMPANY_SOURCE or "press" in st:
            return CATEGORY_COMPANY_PRESS
        if rank <= 2:
            return CATEGORY_REGULATOR_EVENT
        if rank in (3, 4):
            return CATEGORY_MATERIAL_NEWS
        return CATEGORY_LOW_TIER_NEWS

    # 7. Issuer press outside the catalyst channel.
    if "press" in st:
        return CATEGORY_COMPANY_PRESS

    # 8. Plain source row / reference.
    return CATEGORY_SOURCE_REFERENCE


def _materiality_rank(item: EvidenceItem) -> int:
    """0 (best) .. 3 (irrelevant). Non-news / unscored items rank 0 (neutral)."""
    return _MATERIALITY_RANK.get((item.relevance_level or "").strip().lower(), 0)


def _category_rank_key(item: EvidenceItem, order: int) -> tuple[int, int, int, int]:
    tier = item.content_tier or item.source_tier
    return (
        tier_rank(tier),
        _materiality_rank(item),
        0 if _is_factual_excerpt(item) else 1,
        order,
    )


def _normalize_news_title(title: str | None) -> str:
    if not title:
        return ""
    t = _PUNCT_RE.sub(" ", title.strip().lower())
    return _WS_RE.sub(" ", t).strip()


def _bound_and_reid(
    selected: list[EvidenceItem],
    *,
    max_chars: int,
    max_chars_per_item: int,
) -> list[EvidenceItem]:
    """Trim per-item excerpts, keep the running total under ``max_chars`` and
    re-id survivors E1..En. Always keeps at least the first item."""
    survivors: list[EvidenceItem] = []
    total_chars = 0
    for item in selected:
        excerpt = item.excerpt or ""
        if len(excerpt) > max_chars_per_item:
            excerpt = excerpt[: max_chars_per_item - 1].rstrip() + "…"
        item_chars = len(excerpt) + len(item.title or "")
        if survivors and total_chars + item_chars > max_chars:
            continue
        total_chars += item_chars
        survivors.append(
            item.model_copy(
                update={
                    "id": f"E{len(survivors) + 1}",
                    "excerpt": excerpt or None,
                }
            )
        )
    return survivors


def _apply_category_budget(
    pack: EvidencePack,
    cfg: Settings,
    *,
    max_items: int,
    max_chars: int,
    max_chars_per_item: int,
) -> EvidencePack:
    """Category-aware selection: reserve a financial-fact floor, cap price/trend
    and news categories, near-dup-dedup news, then fill by global rank."""
    financial_floor = max(0, int(getattr(cfg, "llm_council_evidence_financial_floor", 3)))
    # Phase 32A Problem C: a floor of statement/table-derived financial content
    # (balance sheet / cash-flow / segment excerpts, demoted table facts) so
    # narrative prose can never crowd out every slot within the same category.
    statement_floor = max(0, int(getattr(cfg, "llm_council_evidence_statement_floor", 3)))
    price_trend_cap = max(0, int(getattr(cfg, "llm_council_evidence_price_trend_cap", 3)))
    news_cap = max(0, int(getattr(cfg, "llm_council_evidence_news_cap", 8)))
    low_tier_news_cap = max(0, int(getattr(cfg, "llm_council_evidence_low_tier_news_cap", 4)))
    caps = {
        CATEGORY_PRICE_TREND_METRIC: price_trend_cap,
        CATEGORY_LOW_TIER_NEWS: low_tier_news_cap,
    }

    # Phase 32A Slice 5: guarantee a FLOOR of primary-document slots and CAP the
    # category so one large ingested filing cannot consume the whole budget. This
    # is applied ONLY when ``primary_document_ingestion_enabled`` is on; with the
    # flag off (the default) the category stays uncapped/unfloored and this path
    # is byte-identical to Slice 2.
    pd_ingestion_enabled = bool(getattr(cfg, "primary_document_ingestion_enabled", False))
    primary_document_cap = max(0, int(getattr(cfg, "primary_document_evidence_cap", 6)))
    primary_document_floor = max(0, int(getattr(cfg, "primary_document_evidence_floor", 1)))
    if pd_ingestion_enabled:
        caps[CATEGORY_PRIMARY_DOCUMENT] = primary_document_cap
        # A floor larger than the cap is incoherent — the cap is the hard ceiling.
        primary_document_floor = min(primary_document_floor, primary_document_cap)

    original_count = len(pack.evidence_items)

    # 1. Dedup: exact-hash (first wins) + cross-document semantic fact dedup
    # (same metric+scope+period+currency+scale+value from >1 document, e.g. an
    # HTML press release AND the PDF annual report both stating Group revenue
    # — see ``_semantic_fact_key``) + near-duplicate news dedup by title.
    seen: set[tuple[str, str, str]] = set()
    seen_facts: set[tuple[str, str, str, str, str, str]] = set()
    seen_news_titles: set[str] = set()
    deduped: list[tuple[int, EvidenceItem, str]] = []
    for order, item in enumerate(pack.evidence_items):
        key = _dedup_key(item)
        if key != ("", "", "") and key in seen:
            continue
        fact_key = _semantic_fact_key(item)
        if fact_key is not None:
            if fact_key in seen_facts:
                continue
            seen_facts.add(fact_key)
        seen.add(key)
        category = evidence_category(item)
        if category in _NEWS_CATEGORIES:
            ntitle = _normalize_news_title(item.title)
            if ntitle and ntitle in seen_news_titles:
                continue
            if ntitle:
                seen_news_titles.add(ntitle)
        deduped.append((order, item, category))

    # 2+3. Rank globally by (tier, materiality, factual, order).
    ranked = sorted(deduped, key=lambda t: _category_rank_key(t[1], t[0]))

    # Phase 32A corrective (Problem A): the financial-fact floor is now a
    # CATEGORY-DIVERSE selection (topline / earnings / cash / position /
    # segment — see ``financial_fact_categories``) over the rank-ordered
    # candidates, not a blind "first N in rank order" cut — so e.g. a Group
    # operating margin AND a segment operating margin AND a net-cash figure
    # can all survive together instead of the floor being exhausted by
    # several redundant same-category facts that merely ranked first.
    financial_candidates = [
        (order, item) for order, item, category in ranked if category == CATEGORY_FINANCIAL_FACT
    ]
    # Phase 32A corrective (LVMH H1 2026) — the (tier, materiality, factual)
    # priority across DIFFERENT fields is preserved (those three components
    # are unchanged); period-recency is inserted as an additional tiebreaker
    # BEFORE raw list order, so within one diversity key (same field/scope) a
    # current-period figure is never silently dropped in favor of a
    # comparison-period one merely because it happened to be built earlier.
    financial_candidates = sorted(
        financial_candidates,
        key=lambda pair: (
            *_category_rank_key(pair[1], 0)[:-1],
            _financial_fact_period_rank(pair[1]),
            pair[0],
        ),
    )
    diverse_financial_orders = {
        order
        for order, _item in select_category_diverse(
            financial_candidates,
            cap=financial_floor,
            diversity_key_of=lambda pair: financial_fact_diversity_key(
                _financial_fact_field(pair[1]), getattr(pair[1], "scope", None)
            ),
        )
    }

    # 4. Reserve floors first (by rank, bounded by max_items): the financial-fact
    #    floor always, and the primary-document floor only when ingestion is on.
    reserved: set[int] = set()
    financial_reserved = 0
    statement_reserved = 0
    pd_reserved = 0
    for order, _item, category in ranked:
        if len(reserved) >= max_items:
            break
        if (
            category == CATEGORY_FINANCIAL_FACT
            and order in diverse_financial_orders
            and financial_reserved < financial_floor
        ):
            reserved.add(order)
            financial_reserved += 1
        elif (
            category == CATEGORY_STATEMENT_TABLE
            and statement_reserved < statement_floor
        ):
            reserved.add(order)
            statement_reserved += 1
        elif (
            pd_ingestion_enabled
            and category == CATEGORY_PRIMARY_DOCUMENT
            and pd_reserved < primary_document_floor
        ):
            reserved.add(order)
            pd_reserved += 1

    # 5. Fill: reserved first, then global rank skipping any capped category.
    selected: list[tuple[int, EvidenceItem]] = []
    cat_counts: dict[str, int] = {}
    news_total = 0
    taken: set[int] = set()

    def _take(order: int, item: EvidenceItem, category: str) -> None:
        nonlocal news_total
        selected.append((order, item))
        taken.add(order)
        cat_counts[category] = cat_counts.get(category, 0) + 1
        if category in _NEWS_CATEGORIES:
            news_total += 1

    for order, item, category in ranked:
        if order in reserved:
            _take(order, item, category)

    for order, item, category in ranked:
        if order in taken:
            continue
        if len(selected) >= max_items:
            break
        cap = caps.get(category)
        if cap is not None and cat_counts.get(category, 0) >= cap:
            continue
        if category in _NEWS_CATEGORIES and news_total >= news_cap:
            continue
        _take(order, item, category)

    # Deterministic final order by global rank (reserved facts are high-tier).
    selected.sort(key=lambda t: _category_rank_key(t[1], t[0]))

    # 6. Bound chars + re-id.
    survivors = _bound_and_reid(
        [item for _order, item in selected],
        max_chars=max_chars,
        max_chars_per_item=max_chars_per_item,
    )

    omitted = original_count - len(survivors)
    omitted_reason = None
    if omitted > 0:
        # The primary-document clause is appended ONLY when ingestion is on AND the
        # pack actually carried primary-document items, so the wording stays
        # byte-identical to Slice 2 for the flag-off (and no-primary-document) path.
        pd_present = any(
            cat == CATEGORY_PRIMARY_DOCUMENT for _o, _it, cat in deduped
        )
        pd_clause = ""
        if pd_ingestion_enabled and pd_present:
            pd_clause = (
                f" primary-document reserved up to {primary_document_floor} slot(s) "
                f"and capped at {primary_document_cap};"
            )
        # The statement-table clause is appended ONLY when the pack actually
        # carried statement/table-derived items, so the wording stays
        # byte-identical for packs with none (Phase 32A Problem C).
        statement_clause = ""
        if any(cat == CATEGORY_STATEMENT_TABLE for _o, _it, cat in deduped):
            statement_clause = (
                f" statement/table content reserved up to {statement_floor} slot(s);"
            )
        omitted_reason = (
            f"{omitted} lower-priority / duplicate / capped evidence item(s) were "
            f"compressed out to fit the category-aware council evidence budget "
            f"(kept {len(survivors)} of {original_count}; reserved up to "
            f"{financial_floor} structured financial-fact slot(s); price/trend "
            f"capped at {price_trend_cap}, news at {news_cap} (low-tier news at "
            f"{low_tier_news_cap});{statement_clause}{pd_clause} near-duplicate "
            f"events removed; higher-tier factual items preserved). Source gaps "
            f"are retained."
        )

    return pack.model_copy(
        update={
            "evidence_items": survivors,
            "known_gaps": _compress_gaps(pack.known_gaps),
            "omitted_evidence_count": omitted,
            "omitted_reason": omitted_reason,
        }
    )


def _apply_flat_budget(
    pack: EvidencePack,
    *,
    max_items: int,
    max_chars: int,
    max_chars_per_item: int,
) -> EvidencePack:
    """The original Phase 29B.2 flat budgeter — preserved byte-for-byte."""
    original_count = len(pack.evidence_items)

    # 1. De-duplicate (stable, first wins).
    seen: set[tuple[str, str, str]] = set()
    deduped: list[tuple[int, EvidenceItem]] = []
    for order, item in enumerate(pack.evidence_items):
        key = _dedup_key(item)
        # Empty keys (no url/title/excerpt) are never treated as duplicates.
        if key != ("", "", "") and key in seen:
            continue
        seen.add(key)
        deduped.append((order, item))

    # 2. Rank by tier, factual-excerpt bonus, then original order.
    ranked = sorted(deduped, key=lambda t: _rank_key(t[1], t[0]))

    # 3. Bound by item count + total chars + per-item chars; re-id survivors.
    survivors: list[EvidenceItem] = []
    total_chars = 0
    for _order, item in ranked:
        if len(survivors) >= max_items:
            break
        excerpt = item.excerpt or ""
        if len(excerpt) > max_chars_per_item:
            excerpt = excerpt[: max_chars_per_item - 1].rstrip() + "…"
        item_chars = len(excerpt) + len(item.title or "")
        if survivors and total_chars + item_chars > max_chars:
            # Keep at least one item even if the first exceeds the char budget.
            continue
        total_chars += item_chars
        survivors.append(
            item.model_copy(
                update={
                    "id": f"E{len(survivors) + 1}",
                    "excerpt": excerpt or None,
                }
            )
        )

    omitted = original_count - len(survivors)
    omitted_reason = None
    if omitted > 0:
        omitted_reason = (
            f"{omitted} lower-tier / duplicate / metadata-only evidence item(s) "
            "were compressed out to fit the council evidence budget "
            f"(kept {len(survivors)} of {original_count}; higher-tier factual "
            "items preserved). Source gaps are retained."
        )

    return pack.model_copy(
        update={
            "evidence_items": survivors,
            "known_gaps": _compress_gaps(pack.known_gaps),
            "omitted_evidence_count": omitted,
            "omitted_reason": omitted_reason,
        }
    )


def apply_evidence_budget(
    pack: EvidencePack,
    *,
    max_items: int | None = None,
    max_chars: int | None = None,
    max_chars_per_item: int | None = None,
    cfg: Settings | None = None,
) -> EvidencePack:
    """Return a compressed copy of ``pack`` within the configured budget.

    Overrides fall back to the ``llm_council_evidence_*`` settings. The input pack
    is not mutated. Citation ids are reassigned E1..En on the survivors. When
    ``llm_council_evidence_budgets_enabled`` is on (Phase 32A Slice 2) the
    category-aware path is used; otherwise the original flat path (byte-identical
    to Phase 29B.2) runs.
    """
    cfg = cfg or default_settings
    max_items = cfg.llm_council_evidence_max_items if max_items is None else max_items
    max_chars = cfg.llm_council_evidence_max_chars if max_chars is None else max_chars
    max_chars_per_item = (
        cfg.llm_council_evidence_max_chars_per_item
        if max_chars_per_item is None
        else max_chars_per_item
    )
    max_items = max(1, max_items)
    max_chars = max(200, max_chars)
    max_chars_per_item = max(80, max_chars_per_item)

    if getattr(cfg, "llm_council_evidence_budgets_enabled", False):
        return _apply_category_budget(
            pack,
            cfg,
            max_items=max_items,
            max_chars=max_chars,
            max_chars_per_item=max_chars_per_item,
        )
    return _apply_flat_budget(
        pack,
        max_items=max_items,
        max_chars=max_chars,
        max_chars_per_item=max_chars_per_item,
    )


__all__ = [
    "apply_evidence_budget",
    "evidence_category",
    "CATEGORY_COMPANY_IDENTITY",
    "CATEGORY_FINANCIAL_FACT",
    "CATEGORY_STATEMENT_TABLE",
    "CATEGORY_PRIMARY_DOCUMENT",
    "CATEGORY_FINANCIAL_SUMMARY",
    "CATEGORY_PRICE_TREND_METRIC",
    "CATEGORY_COMPANY_PRESS",
    "CATEGORY_REGULATOR_EVENT",
    "CATEGORY_MATERIAL_NEWS",
    "CATEGORY_LOW_TIER_NEWS",
    "CATEGORY_SOURCE_REFERENCE",
]
