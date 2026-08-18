"""
Phase 32A corrective — same-region monetary-value fabrication fix.

An independent correctness review of PR #107 proved a GENERIC defect in
``primary_fact_parser._money_pattern``: the free-form gap between a metric
label and its candidate value could cross a sentence boundary, a semicolon,
an adversative conjunction ("but", "while", ...), or another metric's own
label, and still be accepted as belonging to the FIRST label — a length
accident of the (then-unbounded) ``{0,25}`` gap, not a real clause check.
Reproduced independently of any PDF/two-column/layout machinery, in plain
short prose:

    "Operating profit was strong. Debt of EUR 1,234 million was drawn."
    => (before this fix) operating_profit = 1234, VALIDATED

This file proves the fix: every label/value candidate's ``gap`` (the only
free-form span in the match — see ``_iter_clause_safe``) must stay within
one semantic clause, or the match is discarded entirely (fail closed — no
weaker fallback). Positive-regression cases prove legitimate same-clause
extraction is unharmed; one end-to-end case proves a fabricated candidate
never reaches ``VALIDATION_VALIDATED`` / an active fact.
"""

from __future__ import annotations

from app.core.config import Settings
from app.services.sources.document_text_extractor import DocumentExcerpt
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import STATUS_EXTRACTED, extract_html
from app.services.sources.primary_fact_parser import (
    FIELD_CASH,
    FIELD_NET_DEBT,
    FIELD_OPERATING_CASH_FLOW,
    FIELD_OPERATING_FREE_CASH_FLOW,
    FIELD_OPERATING_PROFIT,
    FIELD_REVENUE,
    FIELD_TOTAL_DEBT,
    _parse_excerpt,
)

ISSUER = IssuerContext(company_name="Example Group SA", ticker="EXG")


def _cfg(**overrides: object) -> Settings:
    return Settings(**overrides)  # type: ignore[arg-type]


def _excerpt(text: str) -> DocumentExcerpt:
    return DocumentExcerpt(excerpt_id="e1", text=text, char_count=len(text))


def _fields(text: str) -> dict[str, float]:
    facts = _parse_excerpt(_excerpt(text), source_url=None)
    return {f.field: f.numeric_value for f in facts if f.numeric_value is not None}


# =========================================================================== #
# Negative regressions — must NOT emit a fabricated fact                     #
# =========================================================================== #


def test_cross_sentence_operating_profit_not_fabricated():
    text = "Operating profit was strong. Debt of EUR 1,234 million was drawn."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


def test_cross_sentence_revenue_not_fabricated():
    text = "Revenue remained resilient. Cash was EUR 890 million."
    fields = _fields(text)
    assert FIELD_REVENUE not in fields


def test_same_sentence_unrelated_metric_but_clause_not_fabricated():
    text = "Operating profit was strong but debt reached EUR 1,234 million."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


def test_same_sentence_unrelated_clause_while_not_fabricated():
    text = "Revenue was resilient while cash reached EUR 890 million."
    fields = _fields(text)
    assert FIELD_REVENUE not in fields


def test_intervening_metric_label_not_fabricated():
    text = "Operating profit improved; net debt was EUR 1,234 million."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields
    # The genuinely qualified net_debt value in the SAME clause as its own
    # label must still parse — this fix must not become over-broad.
    assert fields.get(FIELD_NET_DEBT) == 1234.0


def test_realistic_flattened_financial_highlights_block_no_cross_metric_bleed():
    # A realistic PDF-extracted "flattened" highlights block: short lines,
    # no explicit newlines preserved between adjacent metric mentions —
    # exactly the shape a two-column PDF reconstruction can produce.
    text = (
        "Financial highlights. Operating profit was resilient in a "
        "challenging environment. Net debt was EUR 2,345 million at year "
        "end following recent acquisitions."
    )
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields
    assert fields.get(FIELD_NET_DEBT) == 2345.0


# =========================================================================== #
# Positive regressions — legitimate same-clause extraction must survive      #
# =========================================================================== #


def test_operating_profit_simple_still_parses():
    fields = _fields("Operating profit reached EUR 4,492 million.")
    assert fields[FIELD_OPERATING_PROFIT] == 4492.0


def test_operating_profit_trend_clause_still_parses_absolute_not_percent():
    fields = _fields(
        "Operating profit for the year grew by 1% to EUR 4,492 million."
    )
    assert fields[FIELD_OPERATING_PROFIT] == 4492.0


def test_revenue_colon_form_still_parses():
    fields = _fields("Revenue: EUR 22,420 million.")
    assert fields[FIELD_REVENUE] == 22420.0


def test_revenue_trend_clause_still_parses():
    fields = _fields("Revenue increased 4% to EUR 22,420 million.")
    assert fields[FIELD_REVENUE] == 22420.0


def test_cash_and_equivalents_still_parses():
    fields = _fields("Cash and cash equivalents were EUR 8,496 million.")
    assert fields[FIELD_CASH] == 8496.0


def test_operating_free_cash_flow_still_parses():
    fields = _fields("Operating free cash flow was EUR 4,100 million.")
    assert fields[FIELD_OPERATING_FREE_CASH_FLOW] == 4100.0


def test_ocf_still_parses_and_never_becomes_debt():
    text = "Cash flow generated from operating activities EUR 4,880 million"
    fields = _fields(text)
    assert fields[FIELD_OPERATING_CASH_FLOW] == 4880.0
    assert FIELD_TOTAL_DEBT not in fields
    assert FIELD_NET_DEBT not in fields


def test_ocf_nearby_debt_prose_does_not_capture_ocf_value():
    # PR #107's OCF/debt regression, retained: unrelated debt prose sitting
    # near a real OCF figure must never have its value stolen by "debt".
    text = (
        "Cash flow generated from operating activities EUR 4,880 million. "
        "Total debt was EUR 3,210 million at year end."
    )
    fields = _fields(text)
    assert fields[FIELD_OPERATING_CASH_FLOW] == 4880.0
    assert fields[FIELD_TOTAL_DEBT] == 3210.0


# =========================================================================== #
# Ambiguity behaviour preserved (PR #106) — must not be weakened             #
# =========================================================================== #


def test_two_qualified_competing_values_still_ambiguous():
    text = (
        "Operating profit was EUR 4,492 million in 2026. Operating "
        "profit was EUR 4,100 million in 2025."
    )
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


def test_bare_trend_percent_does_not_become_absolute_value():
    text = "Operating profit was up by 23% following a strong quarter."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


# =========================================================================== #
# End-to-end — a fabricated candidate must never reach VALIDATION_VALIDATED  #
# or become an active fact, through the real extraction -> parser ->        #
# validator path.                                                            #
# =========================================================================== #


def test_end_to_end_cross_sentence_candidate_never_validated():
    html = (
        "<html><body><h1>Annual Report 2026</h1>"
        "<p>Operating profit was strong. Debt of EUR 1,234 million was "
        "drawn.</p></body></html>"
    )
    extraction = extract_html(html.encode("utf-8"), cfg=_cfg())
    assert extraction.status == STATUS_EXTRACTED

    facts = validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    op_facts = [f for f in facts if f.label == FIELD_OPERATING_PROFIT]
    assert op_facts == []
    assert all(f.validation_status != VALIDATION_VALIDATED for f in op_facts)


def test_end_to_end_same_sentence_adversative_candidate_never_validated():
    html = (
        "<html><body><h1>Annual Report 2026</h1>"
        "<p>Revenue was resilient while cash reached EUR 890 million.</p>"
        "</body></html>"
    )
    extraction = extract_html(html.encode("utf-8"), cfg=_cfg())
    assert extraction.status == STATUS_EXTRACTED

    facts = validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    revenue_facts = [f for f in facts if f.label == FIELD_REVENUE]
    assert revenue_facts == []
