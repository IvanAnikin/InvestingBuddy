"""
Phase 32A dedicated slice — financial excerpt relevance ranking.

Follow-up to PR #107-#110: a fresh live CFR staging run against the fully
-deployed column-reconstruction + cache-invalidation fixes found that the
GOOD, correctly-reconstructed text was present in the extracted document,
but the bounded excerpt RANKER (flat keyword-density scoring) did not
reliably select it — a short, precise "headline metric + value" sentence
routinely scored below a long, generic multi-topic paragraph that merely
mentioned many DIFFERENT financial-sounding keywords.

Fully offline and deterministic: no network, no LLM, no Azure. Exercises
``document_text_extractor._relevance`` / ``primary_document_extractor.
_rank_and_build_excerpts`` directly with synthetic (non-issuer-specific)
text blocks modelling the real failure shape.
"""

from __future__ import annotations

from app.services.sources.document_text_extractor import _relevance
from app.services.sources.financial_metric_signal import (
    excerpt_diversity_key,
    looks_like_boilerplate,
    metric_value_matches,
)
from app.services.sources.primary_document_extractor import _rank_and_build_excerpts

# =========================================================================== #
# A. Ranking quality                                                          #
# =========================================================================== #


def test_headline_metric_passage_outranks_generic_keyword_dense_prose():
    headline = "Group sales rose by 5% to EUR22,420 million in fiscal 2026."
    generic = (
        "The Group continued to invest in its retail network, brand equity, "
        "employees, and manufacturing capacity throughout the year, with "
        "dividend policy, cash and cash equivalents, and earnings per share "
        "all reflecting the Group's overall strategy and business model."
    )
    assert _relevance(headline) > _relevance(generic)


def test_metric_value_pairing_outranks_keyword_only_mention():
    with_value = "Operating profit for the year grew by 1% to EUR4,492 million."
    keyword_only = (
        "Management discussed operating profit, revenue, and net income "
        "trends generally without stating any specific figures this year."
    )
    assert _relevance(with_value) > _relevance(keyword_only)
    assert metric_value_matches(with_value)
    assert not metric_value_matches(keyword_only)


def test_table_of_contents_is_penalized_relative_to_narrative():
    toc = (
        "Page\n23. Trade payables and other current liabilities\n44\n"
        "24. Revenue\n45\n25. Other operating income and expense\n45\n"
        "26. Operating profit\n46"
    )
    narrative = (
        "Operating profit for the year grew by 1% to EUR4,492 million, "
        "corresponding to 20.0% of sales."
    )
    assert looks_like_boilerplate(toc)
    assert not looks_like_boilerplate(narrative)
    assert _relevance(toc) < _relevance(narrative)


def test_cash_flow_and_cash_debt_passages_score_above_generic_narrative():
    ocf = "Cash flow generated from operating activities amounted to EUR4,880 million."
    net_cash = "The Group's net cash position reached EUR8,496 million at year end."
    generic = "The Group remains committed to its long-term sustainability strategy."
    assert _relevance(ocf) > _relevance(generic)
    assert _relevance(net_cash) > _relevance(generic)


def test_segment_passage_retained_and_never_becomes_group_scoped_by_ranking():
    """Ranking may legitimately select a segment-scoped passage — it must
    never itself claim or imply Group scope. Scope resolution remains
    exclusively primary_fact_parser/extracted_fact_validator's job; ranking
    only decides which excerpt SURVIVES the bounded selection.
    """
    segment = "Specialist Watchmakers reported an operating result of EUR107 million."
    assert _relevance(segment) > 0
    matches = metric_value_matches(segment)
    assert matches and matches[0][0] == "operating_profit"
    # The diversity key carries no scope claim of its own (category/field/
    # value only) — it cannot fabricate or upgrade this to Group scope.
    category, field, _digits = excerpt_diversity_key(segment)
    assert category == "topline_profitability"
    assert field == "operating_profit"


# =========================================================================== #
# B. Diversity — category coverage under a realistic bounded cap             #
# =========================================================================== #


def _cfr_shaped_blocks_with_distractors() -> list[tuple[int | None, str | None, str | None, str]]:
    """Many GENERIC distracting blocks plus high-value passages equivalent
    to the real live-blocking failure shape — modelled generically, no
    issuer name/company-specific vocabulary in production code (only in
    this test fixture, matching existing Phase 32A test conventions).
    """
    targets = [
        "The Group reported sales of EUR22,420 million in fiscal 2026.",
        "Operating profit for the year grew by 1% to EUR4,492 million, "
        "corresponding to an operating margin of 20.0%.",
        "Jewellery Maisons generated an operating margin of 30.5% in 2026.",
        "Specialist Watchmakers reported an operating result of EUR107 million.",
        "Cash flow generated from operating activities amounted to "
        "EUR4,880 million, up from EUR4,443 million in the prior year.",
        "The Group's net cash position reached EUR8,496 million at year end.",
    ]
    distractors = [
        "The Group continues to invest in its boutique network and brand "
        "equity across all major markets and distribution channels.",
        "Macroeconomic headwinds, including currency volatility and "
        "elevated input costs, affected the sector broadly this year.",
        "Sales in the Americas region grew by double digits at constant "
        "exchange rates, reflecting sustained domestic demand.",
        "Sales in Asia Pacific returned to growth, led by strength in "
        "several key markets across the region.",
        "The Board proposes an ordinary dividend, subject to shareholder "
        "approval at the Annual General Meeting.",
        "Selling and distribution expenses increased moderately, "
        "considering selective retail expansion and salary inflation.",
        "Segment revenue for the prior year was restated to reflect a "
        "change in the composition of reportable segments.",
        "Risks and uncertainties include regulatory, litigation, and "
        "foreign-exchange exposure, as described elsewhere in this report.",
        "Contents\nChairman's review 1\nFinancial review 3\n"
        "Consolidated financial statements 9\nNotes 15",
        "The weather was generally favourable for retail footfall during "
        "the peak holiday trading period across most regions.",
        "Employees across the Group's manufacturing and retail operations "
        "number in the tens of thousands worldwide.",
        "Total sales in the prior year were EUR21,399 million, restated "
        "for a change in accounting presentation.",
    ]
    blocks: list[tuple[int | None, str | None, str | None, str]] = []
    for i, t in enumerate(targets):
        blocks.append((3, None, None, t))
    for i, d in enumerate(distractors):
        blocks.append((None, None, None, d))
    # A large run of NEAR-DUPLICATE generic sales mentions — must not be
    # allowed to consume every remaining slot ahead of the still-unseen
    # target categories (cash flow / segment) if this fixture's ordering
    # happened to rank them highly.
    for i in range(10):
        blocks.append(
            (
                None,
                None,
                None,
                f"Sales momentum continued into the {i + 1}th consecutive "
                "quarter across the Group's core markets.",
            )
        )
    return blocks


def test_realistic_cap_selection_covers_all_target_categories():
    """Under the real configured excerpt cap, the selected set must contain
    enough high-value evidence to make every target metric category
    reachable — not exact ordering, coverage.
    """
    blocks = _cfr_shaped_blocks_with_distractors()
    excerpts = _rank_and_build_excerpts(
        blocks, method="native_pdf", max_excerpts=20, per_excerpt=1200
    )
    texts = [e.text for e in excerpts]
    joined = " ".join(texts)

    assert "22,420" in joined  # revenue/sales
    assert "4,492" in joined  # operating profit
    assert "20.0%" in joined  # operating margin
    assert "30.5%" in joined  # segment margin (Jewellery Maisons)
    assert "107 million" in joined  # segment operating result (Watchmakers)
    assert "4,880" in joined  # operating cash flow
    assert "8,496" in joined  # net cash


def test_near_duplicate_run_does_not_starve_a_thin_category():
    """A document with MANY near-duplicate low-information sales mentions
    and only ONE cash-flow passage must not let the duplicates consume
    every slot before cash flow gets even one.
    """
    blocks: list[tuple[int | None, str | None, str | None, str]] = [
        (None, None, None, "Overview of the Group's results for the year."),
    ]
    for i in range(30):
        blocks.append(
            (
                None,
                None,
                None,
                f"Sales grew steadily in quarter {i + 1} across core markets, "
                "reflecting continued momentum in the business overall.",
            )
        )
    blocks.append(
        (
            None,
            None,
            None,
            "Cash flow generated from operating activities amounted to "
            "EUR4,880 million in the year.",
        )
    )
    excerpts = _rank_and_build_excerpts(
        blocks, method="native_pdf", max_excerpts=8, per_excerpt=1200
    )
    joined = " ".join(e.text for e in excerpts)
    assert "4,880" in joined


# =========================================================================== #
# C. Safety — ranking changes must never weaken existing correctness gates    #
# =========================================================================== #


def test_ranking_never_promotes_a_trend_percentage_as_a_level_via_diversity_key():
    trend_only = "Operating profit was up by 23% in the region this quarter."
    # No qualified value alongside the trend percentage -> no metric+value
    # match at all; ranking must not invent one just to fill a diversity slot.
    assert metric_value_matches(trend_only) == []


def test_ranking_never_treats_europe_as_a_currency_signal():
    europe_only = "Sales in Europe were up by 7% compared to the prior year."
    matches = metric_value_matches(europe_only)
    assert matches == []


def test_ocf_and_debt_are_distinct_diversity_slots_never_merged():
    ocf = "Cash flow generated from operating activities amounted to EUR4,880 million."
    debt = "Total borrowings stood at EUR4,880 million at the year end."
    key_ocf = excerpt_diversity_key(ocf)
    key_debt = excerpt_diversity_key(debt)
    assert key_ocf[1] == "operating_cash_flow"
    assert key_debt[1] == "total_debt"
    assert key_ocf != key_debt


def test_group_and_segment_values_never_collapse_into_one_dedup_identity():
    """Numbers/scope must matter to deduplication identity — a Group
    margin and a DIFFERENT segment margin must never be treated as
    interchangeable merely because the surrounding language overlaps.
    """
    group_margin = "The Group's operating margin was 20.0% in 2026."
    segment_margin = "Jewellery Maisons generated an operating margin of 30.5% in 2026."
    assert excerpt_diversity_key(group_margin) != excerpt_diversity_key(segment_margin)
