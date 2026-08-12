"""
Phase 32A corrective — financial-evidence completeness (post-#101 slice).

Fully offline and deterministic; no network, no LLM, no DB. Traces and fixes
three root causes proven from live staging evidence (MC ``structured_
financial_fact_count=0`` despite a visible revenue excerpt; CFR surfacing
only 5 excerpts / 1 metric from a rich results page):

  A. ``validate_extracted_facts`` previously read ONLY ``extraction.tables`` —
     a figure explicitly stated in PROSE (an HTML press release with no
     ``<table>`` at all) never became a structured fact even when clearly
     stated, because the deep (Slice 5) pipeline never ran the conservative
     prose parser at all. ``extracted_fact_validator._candidates_from_excerpts``
     now promotes prose facts too, through the SAME cross-method
     reconciliation as table facts, with an expanded metric vocabulary
     (margins, net debt/net cash, equity, recurring operating profit/margin,
     operating free cash flow) and Group-vs-segment ``scope`` preserved end to
     end onto the council ``EvidenceItem``/``PrimaryFactRef``.
  B. ``connectors.company_ir._select_diverse_excerpts`` replaces a blind
     ``ext.excerpts[:cap]`` prefix cut that could exhaust the whole
     per-document cap on several near-adjacent narrative excerpts before a
     cash-flow/balance-sheet excerpt further down the extractor's own ranked
     list ever got a chance.
  C. ``primary_document_extractor._select_statement_pages`` adds a SMALL,
     bounded, TARGETED look-beyond pass for a long PDF — reading ONLY the
     PDF's outline/bookmark metadata for pages beyond the leading-page window,
     never a larger sequential prefix.
  D. ``evidence_budget._semantic_fact_key`` — cross-document dedup of the SAME
     structured fact (metric + scope + period + currency + scale + value)
     arriving via more than one document, while a genuine value conflict stays
     explicit (two distinct items).
  E. ``structured_financial_fact_count`` (``final_report_generator.
     _is_financial_fact``) is truthful: non-zero exactly when a real
     structured fact reached the final evidence, never from metadata-only
     items.

Two regression fixtures are real HTML processed end to end through
``extract_html`` (not hand-built model objects):
  * a GENERIC rich issuer-results page — Group + two segments, no company-name
    conditional logic anywhere in this file or in production code;
  * an LVMH-shaped page — Group revenue / recurring operating profit /
    recurring operating margin / net profit / operating free cash flow / net
    financial debt / equity / multiple business-group metrics.

Semantic-safety (Group-vs-segment / macro-relabelling) is already exhaustively
covered by ``test_hotfix_semantic_scope_grounding.py`` (PR #99/#101) — this
file adds ONE end-to-end proof that facts produced by the NEW promotion path
carry a ``scope`` the existing checker can act on, not a re-test of the
checker's own logic.
"""

from __future__ import annotations

from app.core.config import Settings
from app.services.final_report_generator import _is_financial_fact
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.evidence_budget import _semantic_fact_key
from app.services.llm.schemas import AgentKeyPoint, CouncilAgentOutput, PersistableEvidence
from app.services.llm.schemas import EvidenceItem as CouncilEvidenceItem
from app.services.sources.company_evidence import _prioritize_ir_items
from app.services.sources.connectors.company_ir import _select_diverse_excerpts
from app.services.sources.document_text_extractor import DocumentExcerpt
from app.services.sources.evidence import PrimaryFactRef, build_evidence_item
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import (
    METHOD_HTML,
    STATUS_EXTRACTED,
    PrimaryDocumentExcerpt,
    _select_statement_pages,
    classify_statement_type,
    extract_html,
    extract_pdf,
)
from app.services.sources.primary_fact_parser import (
    FIELD_FREE_CASH_FLOW,
    FIELD_NET_CASH,
    FIELD_NET_DEBT,
    FIELD_NET_INCOME,
    FIELD_OPERATING_CASH_FLOW,
    FIELD_OPERATING_FREE_CASH_FLOW,
    FIELD_OPERATING_MARGIN,
    FIELD_OPERATING_PROFIT,
    FIELD_RECURRING_OPERATING_MARGIN,
    FIELD_RECURRING_OPERATING_PROFIT,
    FIELD_REVENUE,
    FIELD_TOTAL_DEBT,
    FIELD_TOTAL_EQUITY,
    _parse_excerpt,
)
from tests.helpers.pdf_fixtures import make_pdf, make_pdf_with_outline

ISSUER = IssuerContext(company_name="Example Group SA", ticker="EXG")


def _cfg(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _validated(facts: list[ValidatedFact]) -> list[ValidatedFact]:
    return [f for f in facts if f.validation_status == VALIDATION_VALIDATED]


def _by(
    facts: list[ValidatedFact], label: str, scope: str | None = "__any__"
) -> ValidatedFact | None:
    for f in facts:
        if f.label != label:
            continue
        if scope != "__any__" and f.scope != scope:
            continue
        return f
    return None


# --------------------------------------------------------------------------- #
# Fixtures — real HTML, processed through extract_html end to end            #
# --------------------------------------------------------------------------- #

GENERIC_RICH_HTML = """
<html><body>
<h1>Group Full-Year Results 2026</h1>
<p>Group sales were &euro;1,250 million in 2026. Group operating profit was
&euro;300 million in 2026, representing a Group operating margin of 24.0% in
2026. Group operating cash flow was &euro;280 million in 2026. Group net cash
was &euro;150 million at the end of 2026.</p>
<h2>Segment A</h2>
<p>Segment A sales reached &euro;600 million in 2026, with Segment A
operating profit of &euro;150 million in 2026 and a Segment A operating
margin of 25.0% in 2026.</p>
<h2>Segment B</h2>
<p>Segment B operating result was &euro;40 million in 2026. Elsewhere in
2026, the group continued to navigate macroeconomic headwinds affecting
consumer demand across several markets.</p>
</body></html>
"""

LVMH_SHAPED_HTML = """
<html><body>
<h1>Group H1 2026 Interim Results</h1>
<p>Group revenue was &euro;38,600 million in H1 2026. Recurring operating
profit was &euro;9,300 million in H1 2026, representing a recurring operating
margin of 24.1% in H1 2026. Group net profit was &euro;5,700 million in
H1 2026. Operating free cash flow was &euro;3,200 million in H1 2026. Net
financial debt was &euro;12,000 million at the end of H1 2026. Total equity
was &euro;50,000 million at the end of H1 2026.</p>
<h2>Revenue by business group</h2>
<h3>Wines and Spirits business group</h3>
<p>Wines and Spirits business group revenue was &euro;2,500 million in
H1 2026.</p>
<h3>Fashion and Leather Goods business group</h3>
<p>Fashion and Leather Goods business group revenue was &euro;19,900 million
in H1 2026.</p>
</body></html>
"""


# =========================================================================== #
# A. HTML fact promotion (prose)                                              #
# =========================================================================== #


def test_generic_html_fixture_extracts_successfully():
    extraction = extract_html(GENERIC_RICH_HTML.encode("utf-8"), cfg=_cfg())
    assert extraction.status == STATUS_EXTRACTED
    assert extraction.excerpts


def test_generic_html_fixture_group_facts_promoted_with_correct_scope():
    extraction = extract_html(GENERIC_RICH_HTML.encode("utf-8"), cfg=_cfg())
    facts = _validated(
        validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    )

    revenue = _by(facts, FIELD_REVENUE, "group")
    assert revenue is not None
    assert revenue.value_numeric == 1250.0
    assert revenue.currency == "EUR"
    assert revenue.scale == "million"
    assert revenue.period == "2026"

    op = _by(facts, FIELD_OPERATING_PROFIT, "group")
    assert op is not None and op.value_numeric == 300.0

    margin = _by(facts, FIELD_OPERATING_MARGIN, "group")
    assert margin is not None
    assert margin.value_numeric == 24.0
    assert margin.unit == "percent"

    ocf = _by(facts, FIELD_OPERATING_CASH_FLOW, "group")
    assert ocf is not None and ocf.value_numeric == 280.0

    net_cash = _by(facts, FIELD_NET_CASH, "group")
    assert net_cash is not None and net_cash.value_numeric == 150.0


def test_generic_html_fixture_segment_facts_stay_segment_scoped():
    extraction = extract_html(GENERIC_RICH_HTML.encode("utf-8"), cfg=_cfg())
    facts = _validated(
        validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    )

    seg_a_revenue = _by(facts, FIELD_REVENUE, "Segment A")
    assert seg_a_revenue is not None and seg_a_revenue.value_numeric == 600.0
    seg_a_op = _by(facts, FIELD_OPERATING_PROFIT, "Segment A")
    assert seg_a_op is not None and seg_a_op.value_numeric == 150.0
    seg_a_margin = _by(facts, FIELD_OPERATING_MARGIN, "Segment A")
    assert seg_a_margin is not None and seg_a_margin.value_numeric == 25.0

    # Segment B's operating result — a plain money figure, never inferred as
    # quantifying the nearby macroeconomic-headwinds sentence (that sentence
    # states no number of its own, so there is nothing for the parser to
    # attach to it; the checker-level guarantee against relabelling is
    # covered by test_hotfix_semantic_scope_grounding.py).
    seg_b_result = _by(facts, FIELD_OPERATING_PROFIT, "Segment B")
    assert seg_b_result is not None and seg_b_result.value_numeric == 40.0

    # Group and segment facts for the SAME label/period never collide/merge.
    group_revenue = _by(facts, FIELD_REVENUE, "group")
    assert group_revenue is not None
    assert group_revenue.value_numeric != seg_a_revenue.value_numeric


def test_lvmh_shaped_fixture_group_facts_promoted():
    extraction = extract_html(LVMH_SHAPED_HTML.encode("utf-8"), cfg=_cfg())
    facts = _validated(
        validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    )

    revenue = _by(facts, FIELD_REVENUE, "group")
    assert revenue is not None
    assert revenue.value_numeric == 38600.0
    assert revenue.period == "2026"

    rop = _by(facts, FIELD_RECURRING_OPERATING_PROFIT, "group")
    assert rop is not None and rop.value_numeric == 9300.0
    # Metric identity: "recurring operating profit" must NEVER also promote a
    # plain FIELD_OPERATING_PROFIT fact from the same sentence.
    assert _by(facts, FIELD_OPERATING_PROFIT, "group") is None

    rom = _by(facts, FIELD_RECURRING_OPERATING_MARGIN, "group")
    assert rom is not None and rom.value_numeric == 24.1
    assert _by(facts, FIELD_OPERATING_MARGIN, "group") is None

    net_income = _by(facts, FIELD_NET_INCOME, "group")
    assert net_income is not None and net_income.value_numeric == 5700.0

    ofcf = _by(facts, FIELD_OPERATING_FREE_CASH_FLOW, "group")
    assert ofcf is not None and ofcf.value_numeric == 3200.0
    # "operating free cash flow" must never ALSO promote a plain FCF fact.
    assert _by(facts, FIELD_FREE_CASH_FLOW, "group") is None

    net_debt = _by(facts, FIELD_NET_DEBT, "group")
    assert net_debt is not None and net_debt.value_numeric == 12000.0

    equity = _by(facts, FIELD_TOTAL_EQUITY, "group")
    assert equity is not None and equity.value_numeric == 50000.0


def test_lvmh_shaped_fixture_business_group_facts_scoped_and_distinct():
    extraction = extract_html(LVMH_SHAPED_HTML.encode("utf-8"), cfg=_cfg())
    facts = _validated(
        validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    )

    ws = _by(facts, FIELD_REVENUE, "Wines and Spirits business group")
    assert ws is not None and ws.value_numeric == 2500.0
    flg = _by(facts, FIELD_REVENUE, "Fashion and Leather Goods business group")
    assert flg is not None and flg.value_numeric == 19900.0
    assert ws.value_numeric != flg.value_numeric


# --------------------------------------------------------------------------- #
# A2. Real-world prose robustness — pinned from a live staging PDF extraction #
# --------------------------------------------------------------------------- #
# pdfplumber's naive ``page.extract_text()`` on a genuine multi-column annual-
# report page interleaves unrelated column fragments mid-sentence. These
# fixtures are the ACTUAL (public) excerpt text a live corrective-slice
# acceptance run surfaced from a real issuer's FY26 annual report PDF, kept
# verbatim as regression pins for the resulting parser fixes — a "<label>
# <trend verb> by X% to <value>" clause, additional connector words, and a
# label-collision guard — NOT company-name conditional logic in production
# code (these are literal test strings, not a branch keyed on a ticker/name).


def test_prose_trend_clause_does_not_swallow_percent_change_before_value():
    text = (
        "The Group's net cash position rose by 3% to € 8 496 million at "
        "31 March 2026, an increase of € 239 million."
    )
    exc = DocumentExcerpt(excerpt_id="X1", heading=None, text=text, char_count=len(text))
    facts = {f.field: f for f in _parse_excerpt(exc, None)}
    assert facts["net_cash"].numeric_value == 8496.0
    assert facts["net_cash"].scale == "million"
    assert facts["net_cash"].currency == "EUR"


def test_prose_bare_borrowings_no_longer_matches_unrelated_nearby_number():
    # Real column-interleaving artifact: "external borrowings," sits directly
    # before an unrelated operating-cash-flow figure from a different column.
    text = (
        "Cash flow generated from operating activities amounted to bond and "
        "money market funds as well as external borrowings, € 4 880 "
        "million, up from € 4 443 million in the prior year."
    )
    exc = DocumentExcerpt(excerpt_id="X2", heading=None, text=text, char_count=len(text))
    facts = {f.field: f for f in _parse_excerpt(exc, None)}
    assert FIELD_TOTAL_DEBT not in facts


def test_prose_operating_cash_flow_matches_generated_from_phrasing():
    text = (
        "Cash flow generated from operating activities amounted to "
        "€ 4 880 million, up from € 4 443 million in the prior year."
    )
    exc = DocumentExcerpt(excerpt_id="X3", heading=None, text=text, char_count=len(text))
    facts = {f.field: f for f in _parse_excerpt(exc, None)}
    assert facts[FIELD_OPERATING_CASH_FLOW].numeric_value == 4880.0


def test_prose_profit_for_the_year_never_mislabels_operating_profit():
    text = "Operating profit for the year grew by 1% to € 4 492 million."
    exc = DocumentExcerpt(excerpt_id="X4", heading=None, text=text, char_count=len(text))
    facts = {f.field: f for f in _parse_excerpt(exc, None)}
    assert FIELD_NET_INCOME not in facts


def test_html_prose_reaches_council_evidence_item_with_primary_fact_ref():
    """The exact bug proven live: a revenue figure visible in an extracted
    excerpt must become a structured ``PrimaryFactRef``-bearing EvidenceItem,
    mirroring ``connectors.company_ir._artifact_to_evidence``'s loop 2 wiring
    (which now passes ``scope=fact.scope`` — previously omitted)."""
    extraction = extract_html(LVMH_SHAPED_HTML.encode("utf-8"), cfg=_cfg())
    facts = _validated(
        validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    )
    revenue = _by(facts, FIELD_REVENUE, "group")
    assert revenue is not None

    item = build_evidence_item(
        id="IRFACT1_1",
        source_id="company_ir",
        content_source_tier="T1_primary_filing",
        provider_transport_tier="T1_primary_company_source",
        source_type="company_ir_financial_fact",
        title="H1 2026 Interim Results: revenue",
        excerpt=f"revenue = {revenue.value_numeric}",
        scope=revenue.scope,
        primary_fact=PrimaryFactRef(
            field=revenue.label,
            value=revenue.value_text or str(revenue.value_numeric),
            numeric_value=revenue.value_numeric,
            currency=revenue.currency,
            scale=revenue.scale,
            period=revenue.period,
            scope=revenue.scope,
            needs_human_review=True,
        ),
    )
    assert item.primary_fact is not None
    assert item.primary_fact.numeric_value == 38600.0
    assert item.scope == "group"


# =========================================================================== #
# B. Semantic safety — proof the NEW promotion path feeds the existing gate  #
# =========================================================================== #


def test_group_and_segment_facts_from_new_path_trip_the_existing_scope_gate():
    extraction = extract_html(LVMH_SHAPED_HTML.encode("utf-8"), cfg=_cfg())
    facts = _validated(
        validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    )
    group_rop = _by(facts, FIELD_RECURRING_OPERATING_PROFIT, "group")
    segment_rev = _by(facts, FIELD_REVENUE, "Wines and Spirits business group")
    assert group_rop is not None and segment_rev is not None

    evidence = {
        "E1": CouncilEvidenceItem(
            id="E1",
            source_tier="T1_primary_filing",
            source_type="company_ir_financial_fact",
            excerpt=f"Recurring operating profit = {group_rop.value_numeric}",
            scope=group_rop.scope,
            period=group_rop.period,
        ),
        "E2": CouncilEvidenceItem(
            id="E2",
            source_tier="T1_primary_filing",
            source_type="company_ir_financial_fact",
            excerpt=f"Wines and Spirits business group revenue = {segment_rev.value_numeric}",
            scope=segment_rev.scope,
            period=segment_rev.period,
        ),
    }
    kp = AgentKeyPoint(
        claim=(
            f"Recurring operating profit was EUR{group_rop.value_numeric}m, "
            f"including Wines and Spirits business group revenue of "
            f"EUR{segment_rev.value_numeric}m."
        ),
        citation_ids=["E1", "E2"],
        confidence="medium",
        data_quality="B",
    )
    output = CouncilAgentOutput(
        agent_name="financial_analyst", status="completed", summary="t", key_points=[kp]
    )
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("semantic mismatch" in i for i in issues)


def test_two_group_facts_same_period_survive_the_scope_gate():
    extraction = extract_html(LVMH_SHAPED_HTML.encode("utf-8"), cfg=_cfg())
    facts = _validated(
        validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    )
    revenue = _by(facts, FIELD_REVENUE, "group")
    rop = _by(facts, FIELD_RECURRING_OPERATING_PROFIT, "group")
    assert revenue is not None and rop is not None

    evidence = {
        "E1": CouncilEvidenceItem(
            id="E1",
            source_tier="T1_primary_filing",
            source_type="company_ir_financial_fact",
            excerpt=f"Group revenue = {revenue.value_numeric}",
            scope=revenue.scope,
            period=revenue.period,
        ),
        "E2": CouncilEvidenceItem(
            id="E2",
            source_tier="T1_primary_filing",
            source_type="company_ir_financial_fact",
            excerpt=f"Recurring operating profit = {rop.value_numeric}",
            scope=rop.scope,
            period=rop.period,
        ),
    }
    kp = AgentKeyPoint(
        claim=(
            f"Group revenue was EUR{revenue.value_numeric}m and Group recurring "
            f"operating profit was EUR{rop.value_numeric}m in H1 2026."
        ),
        citation_ids=["E1", "E2"],
        confidence="medium",
        data_quality="B",
    )
    output = CouncilAgentOutput(
        agent_name="financial_analyst", status="completed", summary="t", key_points=[kp]
    )
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert len(sanitized.key_points) == 1
    assert not any("semantic mismatch" in i for i in issues)


# =========================================================================== #
# C. Evidence selection — category diversity under a bounded per-document cap #
# =========================================================================== #


def _excerpt(eid: str, *, section: str | None, text: str = "x" * 60) -> PrimaryDocumentExcerpt:
    return PrimaryDocumentExcerpt(
        excerpt_id=eid,
        text=text,
        section=section,
        heading=section,
        extraction_method=METHOD_HTML,
        confidence=0.6,
        char_count=len(text),
    )


def test_diverse_selection_keeps_statement_categories_under_narrative_pressure():
    # Five near-adjacent narrative excerpts rank ahead of one cash-flow and
    # one balance-sheet excerpt further down the extractor's own ranked list
    # — exactly the live CFR shape (5 lead-ish excerpts survived, cash-flow /
    # balance-sheet content did not).
    excerpts = [_excerpt("X1", section=None)] + [
        _excerpt(f"N{i}", section="Business overview") for i in range(2, 6)
    ] + [
        _excerpt("CF1", section="Cash Flow Statement"),
        _excerpt("BS1", section="Balance Sheet"),
    ]
    selected = _select_diverse_excerpts(excerpts, cap=5)
    ids = {e.excerpt_id for e in selected}
    assert len(selected) == 5
    assert "CF1" in ids
    assert "BS1" in ids
    # The extractor's own top-ranked ("headline") excerpt always survives.
    assert "X1" in ids


def test_diverse_selection_is_a_noop_when_under_the_cap():
    excerpts = [_excerpt("X1", section=None), _excerpt("X2", section="Cash Flow Statement")]
    assert _select_diverse_excerpts(excerpts, cap=5) == excerpts


def test_diverse_selection_never_exceeds_cap():
    excerpts = [_excerpt(f"X{i}", section=None) for i in range(20)]
    assert len(_select_diverse_excerpts(excerpts, cap=5)) == 5


def test_classify_statement_type_recognizes_required_statement_headings():
    assert classify_statement_type("Consolidated Income Statement") is not None
    assert classify_statement_type("Statement of Financial Position") is not None
    assert classify_statement_type("Cash Flow Statement") is not None
    assert classify_statement_type("Segment Information") is not None
    assert classify_statement_type("Chairman's Letter to Shareholders") is None


def _ir_item(eid: str, *, source_type: str, fact: bool) -> CouncilEvidenceItem:
    # Mirrors ``company_ir.py``'s own item shape closely enough to exercise
    # ``_prioritize_ir_items``'s bucketing (source_type + primary_fact
    # presence) without depending on the connector's full construction path.
    return CouncilEvidenceItem(
        id=eid,
        source_tier="T1_primary_filing",
        source_type=source_type,
        excerpt=f"excerpt for {eid}",
        primary_fact={"field": "revenue", "numeric_value": 1.0} if fact else None,
    )


def test_prioritize_ir_items_floors_structured_facts_ahead_of_excerpt_flood():
    """The live MC bug, pinned: 5 prose excerpts appended before 8 structured
    facts (matching ``company_ir._artifact_to_evidence``'s loop order) must
    not let a downstream per-source cap evict every single fact — a stable
    sort on the old single "document" bucket did exactly that."""
    excerpts = [
        _ir_item(f"X{i}", source_type="company_ir_annual_report_excerpt", fact=False)
        for i in range(5)
    ]
    facts = [
        _ir_item(f"F{i}", source_type="company_ir_financial_fact", fact=True)
        for i in range(8)
    ]
    items = excerpts + facts  # excerpts-before-facts, the real append order
    prioritized = _prioritize_ir_items(items)
    top_5 = prioritized[:5]
    fact_count_in_top_5 = sum(1 for it in top_5 if it.primary_fact is not None)
    assert fact_count_in_top_5 >= 3
    # The floor does not consume every slot — excerpt evidence still survives.
    assert any(it.primary_fact is None for it in top_5)


def test_prioritize_ir_items_stable_when_no_facts_present():
    excerpts = [
        _ir_item(f"X{i}", source_type="company_ir_annual_report_excerpt", fact=False)
        for i in range(5)
    ]
    assert [it.id for it in _prioritize_ir_items(excerpts)] == [it.id for it in excerpts]


# =========================================================================== #
# D. Long PDF — bounded, targeted supplemental-page selection                 #
# =========================================================================== #


def test_supplemental_pages_targeted_via_outline_beyond_leading_window():
    pages = ["Cover page." for _ in range(5)] + [
        "Consolidated Income Statement\nRevenue 1,000",
    ]
    raw = make_pdf_with_outline(pages, bookmarks={6: "Consolidated Income Statement"})
    found = _select_statement_pages(raw, total_pages=6, exclude={1, 2, 3, 4}, max_pages=12)
    assert found == [6]


def test_supplemental_pages_excludes_already_covered_pages():
    pages = ["Cover page." for _ in range(3)]
    raw = make_pdf_with_outline(pages, bookmarks={2: "Balance Sheet"})
    found = _select_statement_pages(raw, total_pages=3, exclude={1, 2, 3}, max_pages=12)
    assert found == []


def test_supplemental_pages_bounded_by_max_pages():
    titles = {
        1: "Income Statement",
        2: "Balance Sheet",
        3: "Cash Flow Statement",
        4: "Segment Information",
    }
    pages = ["Body text." for _ in range(4)]
    raw = make_pdf_with_outline(pages, bookmarks=titles)
    found = _select_statement_pages(raw, total_pages=4, exclude=set(), max_pages=2)
    assert len(found) == 2


def test_supplemental_pages_no_outline_yields_no_candidates():
    raw = make_pdf(["Cover page.", "Body text with no outline at all."])
    found = _select_statement_pages(raw, total_pages=2, exclude={1}, max_pages=12)
    assert found == []


def test_extract_pdf_reads_targeted_page_beyond_leading_window():
    pages = ["Cover page filler." for _ in range(3)] + [
        "Consolidated Income Statement\nGroup revenue 1,000 million euros in 2026.",
    ]
    raw = make_pdf_with_outline(pages, bookmarks={4: "Consolidated Income Statement"})
    cfg = _cfg(primary_document_max_pdf_pages=3, primary_document_max_supplemental_pdf_pages=5)
    extraction = extract_pdf(raw, cfg=cfg)
    assert extraction.status == STATUS_EXTRACTED
    assert any(exc.page_number == 4 for exc in extraction.excerpts)
    assert any("Consolidated Income Statement" in (exc.text or "") for exc in extraction.excerpts)


def test_extract_pdf_supplemental_pass_disabled_by_zero_cap():
    pages = ["Cover page filler." for _ in range(3)] + [
        "Consolidated Income Statement\nGroup revenue 1,000 million euros.",
    ]
    raw = make_pdf_with_outline(pages, bookmarks={4: "Consolidated Income Statement"})
    cfg = _cfg(primary_document_max_pdf_pages=3, primary_document_max_supplemental_pdf_pages=0)
    extraction = extract_pdf(raw, cfg=cfg)
    assert all(exc.page_number != 4 for exc in extraction.excerpts)


def test_extract_pdf_never_expands_beyond_leading_window_plus_supplemental_cap():
    # 50-page document; leading window 5, supplemental cap 2 — total pages
    # actually read must never exceed 5 + 2 = 7, regardless of how many
    # bookmarks match (never a larger sequential prefix).
    titles = {p: "Segment Information" for p in range(10, 50)}
    pages = [f"Page {i} filler text." for i in range(1, 51)]
    raw = make_pdf_with_outline(pages, bookmarks=titles)
    cfg = _cfg(primary_document_max_pdf_pages=5, primary_document_max_supplemental_pdf_pages=2)
    extraction = extract_pdf(raw, cfg=cfg)
    read_pages = {exc.page_number for exc in extraction.excerpts if exc.page_number}
    assert read_pages.issubset(set(range(1, 8)))
    assert len(read_pages & set(range(6, 8))) <= 2


# =========================================================================== #
# E. structured_financial_fact_count truthfulness + cross-document dedup      #
# =========================================================================== #


def _council_item_with_fact(eid: str, ref: PrimaryFactRef) -> CouncilEvidenceItem:
    # Mirrors evidence_pack.py's real conversion: the connector-layer
    # ``evidence.py.EvidenceItem.primary_fact`` (a real ``PrimaryFactRef``) is
    # ``.model_dump(mode="json")``-ed onto the council-facing
    # ``llm.schemas.EvidenceItem.primary_fact`` (a plain dict) before the
    # budgeter ever sees it — see ``evidence_pack.py``'s ``pf_dump``.
    return CouncilEvidenceItem(
        id=eid,
        source_tier="T1_primary_filing",
        source_type="company_ir_financial_fact",
        scope=ref.scope,
        period=ref.period,
        primary_fact=ref.model_dump(mode="json"),
    )


def test_semantic_fact_key_dedups_same_fact_across_documents():
    ref = PrimaryFactRef(
        field=FIELD_REVENUE,
        value="38,600",
        numeric_value=38600.0,
        currency="EUR",
        scale="million",
        period="2026",
        scope="group",
    )
    item_html = _council_item_with_fact("E1", ref)
    item_pdf = _council_item_with_fact("E2", ref.model_copy())
    assert _semantic_fact_key(item_html) == _semantic_fact_key(item_pdf)


def test_semantic_fact_key_keeps_genuine_conflicts_distinct():
    ref_a = PrimaryFactRef(
        field=FIELD_REVENUE, value="38,600", numeric_value=38600.0,
        currency="EUR", scale="million", period="2026", scope="group",
    )
    ref_b = ref_a.model_copy(update={"numeric_value": 39100.0, "value": "39,100"})
    item_a = _council_item_with_fact("E1", ref_a)
    item_b = _council_item_with_fact("E2", ref_b)
    assert _semantic_fact_key(item_a) != _semantic_fact_key(item_b)


def test_structured_financial_fact_count_true_when_primary_fact_present():
    """A prose excerpt promoted via the NEW path (Problem A) carries a
    ``primary_fact`` and must count as a structured financial fact — the exact
    live MC gap (revenue visible in an excerpt, count stayed 0)."""
    item = PersistableEvidence(
        uid="u1",
        alias="E1",
        source_type="company_ir_excerpt",  # NOT one of _FINANCIAL_FACT_TYPES
        excerpt="Group revenue = 38600.0",
        primary_fact={"field": "revenue", "numeric_value": 38600.0},
    )
    assert _is_financial_fact(item) is True


def test_structured_financial_fact_count_false_for_plain_prose():
    item = PersistableEvidence(
        uid="u2",
        alias="E2",
        source_type="company_ir_excerpt",
        excerpt="The group discussed its strategy for the coming year.",
        primary_fact=None,
    )
    assert _is_financial_fact(item) is False


def test_semantic_fact_key_none_for_non_fact_items():
    item = CouncilEvidenceItem(
        id="E1",
        source_tier="T1_primary_filing",
        source_type="company_ir_excerpt",
        excerpt="Some narrative text with no structured fact.",
    )
    assert _semantic_fact_key(item) is None
