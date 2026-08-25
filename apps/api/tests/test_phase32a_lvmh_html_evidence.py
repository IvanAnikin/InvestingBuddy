"""Phase 32A corrective — preserve official-HTML financial evidence (LVMH H1
2026 gap).

Three real, narrow, generic (non-issuer-specific) parser bugs found while
tracing a live MC/LVMH evidence gap end-to-end:

1. ``_PERIOD_QUALIFIER`` (``primary_fact_parser.py``) did not recognize the
   standard "(the) first/second half of <year>" / "(the) N quarter of <year>"
   period phrasing. The unconsumed phrase fell into the generic label→value
   gap, which then grabbed the trailing YEAR as though it were the metric's
   own money value (e.g. "...for the first half of 2026 came to €8.7
   billion" parsed as ``2026``, not ``8.7 billion``).
2. The table row-label matcher for total equity (``_LABEL_PATTERNS`` in
   ``extracted_fact_validator.py``) required "total equity" / "shareholders'
   equity" and never matched a table row whose ENTIRE label is simply
   "Equity" — a common standalone balance-sheet-summary row label.
3. ``_best_candidate`` picked the representative value by raw excerpt
   confidence BEFORE scale precision — a lead-paragraph prose restatement
   (highly ranked for citation relevance) could outrank an agreeing, more
   precise table figure purely because of its unrelated relevance score.

Fully offline/deterministic — no network, no LLM, no Azure.
"""

from __future__ import annotations

from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import extract_primary_document

# A bounded, realistic official-results HTML fixture modeled on the actual
# structure of a real issuer's H1 results press release: a "Financial
# highlights" table (Group scope) + duplicated responsive markup + navigation
# + a segment table + footnotes + unrelated percentages — none of the
# specific figures below are issuer-specific parser logic, only fixture data.
_ISSUER_HTML = b"""
<html><body>
<nav><a href="/en/">Home</a><a href="/en/investors">Investors</a></nav>
<div class="mobile-nav"><a href="/en/publications">Publications</a></div>
<h1>Accelerating growth in the second quarter Solid first-half results</h1>
<p>The Group, the world's leading high-quality products group, recorded
revenue of &euro;38.6 billion in the first half of 2026.</p>
<p>Profit from recurring operations for the first half of 2026 came to
&euro;8.7 billion, equating to an operating margin that remained high at
22.5%. The Group share of net profit amounted to &euro;5.7 billion, stable
year on year.</p>
<h2>Financial highlights</h2>
<table>
<tr><td><em>In millions of euros</em></td><td><strong>First-half 2025</strong></td>
<td><strong>First-half 2026</strong></td><td><strong>% Change Reported</strong></td>
<td><strong>% Change Organic</strong></td></tr>
<tr><td>Revenue</td><td>39 810</td><td>38 644</td><td>-3%</td><td>+2%</td></tr>
<tr><td>Profit from recurring operations</td><td>9 012</td><td>8 691</td><td>-4%</td></tr>
<tr><td>Net profit, Group share</td><td>5 698</td><td>5 697</td><td>0%</td></tr>
<tr><td>Operating free cash flow</td><td>4 032</td><td>4 100</td><td>+2%</td></tr>
<tr><td>Net financial debt</td><td>10 176</td><td>8 245</td><td>-19%</td></tr>
<tr><td>Equity</td><td>66 875</td><td>69 694</td><td>+4%</td></tr>
</table>
<p>* On a constant perimeter and currency basis. For the Group, the perimeter
impact with respect to the first half of 2025 was -1%.</p>
<h2>Business group review</h2>
<p>The Wines &amp; Spirits business group recorded organic revenue growth of
5% and profit from recurring operations up 11% in the first half of 2026.</p>
<table>
<tr><td>Segment</td><td>First-half 2025</td><td>First-half 2026</td></tr>
<tr><td>Wines &amp; Spirits revenue</td><td>2 800</td><td>2 750</td></tr>
</table>
<p>An interim dividend of &euro;5.50 will be paid on December 3, 2026.</p>
<div class="mobile-nav-footer"><a href="/en/publications">Publications</a></div>
</body></html>
"""


def _validated(company_name: str = "Issuer Group SE"):
    extraction = extract_primary_document(_ISSUER_HTML, document_type="html")
    facts = validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(company_name=company_name, reporting_currency="EUR"),
    )
    return [f for f in facts if f.validation_status == VALIDATION_VALIDATED]


def _get(facts, label: str, period: str, scope=None):
    for f in facts:
        if f.label == label and f.period == period and f.scope == scope:
            return f
    return None


def test_all_seven_group_metrics_survive_the_bounded_evidence_path():
    """Private-use readiness PR-D: the periods below are ``H1 2026`` / ``H1 2025``,
    not ``2026`` / ``2025``. Every figure in this fixture is explicitly labelled
    "first half of 2026" in the prose and "First-half 2026" in the table header;
    stamping them with a bare year presented HALF-YEAR revenue of EUR38.6bn as
    the FULL YEAR's — the ``INTERIM_AS_ANNUAL`` contradiction. The document is
    the authority here, and it says first half."""
    facts = _validated()

    revenue = _get(facts, "revenue", "H1 2026")
    assert revenue is not None, "H1 2026 revenue must validate"
    assert revenue.value_numeric == 38644.0
    assert revenue.scale == "million"

    rop = _get(facts, "recurring_operating_profit", "H1 2026")
    assert rop is not None, "H1 2026 profit from recurring operations must validate"
    assert rop.value_numeric == 8691.0
    assert rop.scale == "million"

    margin = _get(facts, "operating_margin", "H1 2026")
    assert margin is not None
    assert margin.value_numeric == 22.5

    net_income = _get(facts, "net_income", "H1 2026")
    assert net_income is not None
    assert net_income.value_numeric == 5697.0
    assert net_income.scale == "million"

    ofcf = _get(facts, "operating_free_cash_flow", "H1 2026")
    assert ofcf is not None
    assert ofcf.value_numeric == 4100.0
    assert ofcf.scale == "million"

    net_debt = _get(facts, "net_debt", "H1 2026")
    assert net_debt is not None
    assert net_debt.value_numeric == 8245.0
    assert net_debt.scale == "million"

    equity = _get(facts, "total_equity", "H1 2026")
    assert equity is not None, "bare 'Equity' table row must validate"
    assert equity.value_numeric == 69694.0
    assert equity.scale == "million"


def test_prior_year_recurring_operating_profit_still_present():
    """The comparison-period (H1 2025) table value must not regress."""
    facts = _validated()
    prior = _get(facts, "recurring_operating_profit", "H1 2025")
    assert prior is not None
    assert prior.value_numeric == 9012.0


def test_period_qualifier_does_not_capture_the_year_as_a_money_value():
    """Regression guard for the root cause: 'for the first half of 2026'
    must never itself be parsed as an €2,026 (or similar) money figure."""
    facts = _validated()
    rop = _get(facts, "recurring_operating_profit", "H1 2026")
    assert rop is not None
    assert rop.value_numeric != 2026.0


def test_bare_equity_row_label_does_not_match_unrelated_prose():
    """The new bare-'equity' table alternative must stay anchored to a whole
    isolated row-header cell — it must never start matching inside ordinary
    prose mentioning equity in another sense."""
    from app.services.sources.extracted_fact_validator import _match_label

    assert _match_label("Equity") == "total_equity"
    assert _match_label("private equity investments") is None
    assert _match_label("Return on equity") is None
    assert _match_label("Total shareholders' equity") == "total_equity"
