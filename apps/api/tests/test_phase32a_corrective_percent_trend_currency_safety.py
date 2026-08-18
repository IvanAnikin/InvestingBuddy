"""
Phase 32A corrective — percent trend/level safety + currency substring fix.

An independent correctness review of PR #107 proved two further GENERIC
defects, both still reachable through the real extraction -> parser ->
validator path:

1. ``_percent_pattern`` had no trend-clause handling (unlike
   ``_money_pattern``), so a year-over-year percentage CHANGE could be
   captured as though it were the metric's own absolute level:

       "Operating margin was up by 23% versus prior year."
       => (before this fix) operating_margin = 23.0, VALIDATED

2. ``_find_currency`` matched currency words as raw substrings, so ordinary
   words containing "eur"/"usd"/etc. as a substring (Europe, European,
   entrepreneurial, amateur, ...) could be misread as a currency mention.

This file proves both fixes: a trend-only percentage never becomes an
absolute-level fact (fail closed, consistent with the module's "no fact is
better than a wrong fact" philosophy), while a genuine absolute level —
including one stated AFTER a trend clause ("... rose 120 basis points to
20.0%") — still parses correctly. It also proves the money-field trend
clause is now non-backtrackable, so a bare trend percentage can no longer
be captured as a money value even when an unrelated currency exists nearby.
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
    FIELD_OPERATING_MARGIN,
    FIELD_OPERATING_PROFIT,
    FIELD_RECURRING_OPERATING_MARGIN,
    FIELD_REVENUE,
    _find_currency,
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
# Negative regressions — a trend-only percentage must NOT become an          #
# absolute margin-level fact                                                 #
# =========================================================================== #


def test_percent_trend_versus_prior_year_not_fabricated_as_level():
    text = "Operating margin was up by 23% versus prior year."
    fields = _fields(text)
    assert FIELD_OPERATING_MARGIN not in fields


def test_percent_trend_increased_by_not_fabricated_as_level():
    text = "Operating margin increased by 2.5%."
    fields = _fields(text)
    assert FIELD_OPERATING_MARGIN not in fields


def test_recurring_percent_trend_rose_by_not_fabricated_as_level():
    text = "Recurring operating margin rose by 3%."
    fields = _fields(text)
    assert FIELD_RECURRING_OPERATING_MARGIN not in fields


def test_percent_trend_basis_points_not_fabricated_as_level():
    text = "Operating margin improved 120 basis points."
    fields = _fields(text)
    assert FIELD_OPERATING_MARGIN not in fields


def test_percent_trend_with_leading_fiscal_year_qualifier_not_fabricated():
    text = "For fiscal year 2024, operating margin was up by 23% versus prior year."
    fields = _fields(text)
    assert FIELD_OPERATING_MARGIN not in fields


def test_percent_trend_in_flattened_highlights_with_unrelated_percentages():
    # A realistic flattened financial-highlights block, no preserved
    # newlines, with an unrelated percentage (tax rate) nearby — the trend
    # figure must never leak into operating_margin, and the unrelated
    # percentage must never be mistaken for it either.
    text = (
        "Financial highlights. The effective tax rate was 24.0%. Operating "
        "margin was up by 23% versus prior year reflecting cost discipline "
        "in Europe."
    )
    fields = _fields(text)
    assert FIELD_OPERATING_MARGIN not in fields


# =========================================================================== #
# Positive regressions — legitimate absolute-level extraction must survive,  #
# including a level stated AFTER a trend clause                              #
# =========================================================================== #


def test_percent_level_was_still_parses():
    fields = _fields("Operating margin was 20.0%.")
    assert fields[FIELD_OPERATING_MARGIN] == 20.0


def test_percent_level_reached_still_parses():
    fields = _fields("Operating margin reached 20.0%.")
    assert fields[FIELD_OPERATING_MARGIN] == 20.0


def test_percent_level_increased_to_still_parses():
    fields = _fields("Operating margin increased to 20.0%.")
    assert fields[FIELD_OPERATING_MARGIN] == 20.0


def test_percent_trend_percentage_points_then_level_parses_level_not_trend():
    fields = _fields("Operating margin increased by 2 percentage points to 20.0%.")
    assert fields[FIELD_OPERATING_MARGIN] == 20.0


def test_percent_trend_then_level_parses_level_not_trend():
    fields = _fields("Operating margin was up by 3% to 20.0%.")
    assert fields[FIELD_OPERATING_MARGIN] == 20.0


def test_percent_trend_basis_points_then_level_parses_level_not_trend():
    fields = _fields("Operating margin rose 120 basis points to 20.0%.")
    assert fields[FIELD_OPERATING_MARGIN] == 20.0


def test_recurring_percent_level_reached_still_parses():
    fields = _fields("Recurring operating margin reached 22.5%.")
    assert fields[FIELD_RECURRING_OPERATING_MARGIN] == 22.5


# =========================================================================== #
# Money-field trend/currency regression — a bare trend percentage must not   #
# leak into a money field via a falsely-detected nearby currency word        #
# =========================================================================== #


def test_operating_profit_trend_percent_in_europe_not_fabricated_as_money():
    text = "Operating profit was up by 23% in Europe."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


def test_revenue_trend_percent_in_europe_not_fabricated_as_money():
    text = "Revenue was up by 23% in Europe."
    fields = _fields(text)
    assert FIELD_REVENUE not in fields


def test_operating_profit_trend_percent_with_unrelated_currency_elsewhere():
    # The unrelated EUR figure elsewhere in the excerpt must not let the
    # trend percentage "borrow" a currency and pass the scale/currency gate.
    text = (
        "Revenue was EUR 4,492 million in Europe. Operating profit was up "
        "by 23% in the region."
    )
    fields = _fields(text)
    assert fields.get(FIELD_REVENUE) == 4492.0
    assert FIELD_OPERATING_PROFIT not in fields


# =========================================================================== #
# Currency substring fix — must NOT detect a currency from an ordinary word  #
# =========================================================================== #


def test_currency_not_detected_from_europe():
    assert _find_currency("Europe") is None


def test_currency_not_detected_from_european():
    assert _find_currency("Strong growth across European markets.") is None


def test_currency_not_detected_from_entrepreneurial():
    assert _find_currency("An entrepreneurial culture drives growth.") is None


def test_currency_not_detected_from_amateur():
    assert _find_currency("Not a professional or amateur distinction.") is None


def test_currency_still_detected_from_eur_word():
    assert _find_currency("Revenue was EUR 4,492 million.") == "EUR"


def test_currency_still_detected_from_eur_amount():
    assert _find_currency("EUR 4,492m") == "EUR"


def test_currency_still_detected_from_euro_symbol():
    assert _find_currency("€4,492m") == "EUR"


def test_currency_still_detected_from_usd():
    assert _find_currency("USD 1.2bn") == "USD"


def test_currency_still_detected_from_gbp():
    assert _find_currency("GBP 890m") == "GBP"


def test_currency_still_detected_from_chf():
    assert _find_currency("CHF 500 million") == "CHF"


# =========================================================================== #
# Preserve final clause-safety fix (e77dde7) — must remain blocked           #
# =========================================================================== #


def test_clause_safety_operating_profit_strong_debt_still_blocked():
    text = "Operating profit was strong. Debt of EUR 1,234 million was drawn."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


def test_clause_safety_revenue_resilient_cash_still_blocked():
    text = "Revenue remained resilient. Cash was EUR 890 million."
    fields = _fields(text)
    assert FIELD_REVENUE not in fields


def test_clause_safety_operating_profit_but_debt_still_blocked():
    text = "Operating profit was strong but debt reached EUR 1,234 million."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


def test_clause_safety_revenue_while_cash_still_blocked():
    text = "Revenue was resilient while cash reached EUR 890 million."
    fields = _fields(text)
    assert FIELD_REVENUE not in fields


def test_clause_safety_operating_profit_semicolon_net_debt_still_blocked():
    text = "Operating profit improved; net debt was EUR 1,234 million."
    fields = _fields(text)
    assert FIELD_OPERATING_PROFIT not in fields


# =========================================================================== #
# End-to-end — the reported bad operating-margin example must never reach   #
# VALIDATION_VALIDATED / become an active fact, through the real            #
# extraction -> parser -> validator path.                                   #
# =========================================================================== #


def test_end_to_end_percent_trend_never_validated():
    html = (
        "<html><body><h1>Annual Report 2024</h1>"
        "<p>For fiscal year 2024, operating margin was up by 23% versus "
        "prior year, reflecting strong cost discipline in Europe.</p>"
        "</body></html>"
    )
    extraction = extract_html(html.encode("utf-8"), cfg=_cfg())
    assert extraction.status == STATUS_EXTRACTED

    facts = validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    margin_facts = [f for f in facts if f.label == FIELD_OPERATING_MARGIN]
    assert margin_facts == []
    assert all(f.validation_status != VALIDATION_VALIDATED for f in margin_facts)
