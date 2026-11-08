"""ADR-085 — statement facts from what the SEC already tags.

Two structured sources the research held and never read:

1. SEC companyfacts ``ifrs-full`` (a 20-F / 40-F / 6-K filer), read by
   ``sec_fundamentals_normalizer.normalize_company_facts``;
2. the ``ix:nonFraction`` facts inside a held filing document, read by
   ``inline_xbrl`` + ``inline_xbrl_facts`` on the SEC filing-body path, into the fact
   store the issuer statements view reads.

Fixtures only — no network. The companyfacts fixture is a trimmed copy of Cellectis
S.A.'s real payload (CIK 1627281, retrieved 2026-11-03); the iXBRL documents are
hand-written to the shapes EDGAR serves.
"""

from __future__ import annotations

import copy
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
from app.integrations.xbrl_statement_concepts import (
    IFRS_BORROWINGS,
    IFRS_CURRENT_BORROWINGS,
    IFRS_NONCURRENT_BORROWINGS,
)
from app.services.sources.inline_xbrl import (
    REFUSED_TOO_LARGE,
    looks_like_inline_xbrl,
    parse_inline_xbrl,
)
from app.services.sources.inline_xbrl_facts import (
    EXTRACTION_METHOD_INLINE_XBRL,
    merge_with_text_facts,
    statement_facts_from_inline_xbrl,
    with_inline_xbrl_facts,
)

FIXTURE = Path(__file__).parent / "fixtures" / "sec_companyfacts_clls_ifrs_trimmed.json"
SEC_URL = (
    "https://www.sec.gov/Archives/edgar/data/1627281/000119312526389336/clls-ex99_1.htm"
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _clls() -> dict:
    return json.loads(FIXTURE.read_text())


def _drop_filed_on_or_after(data: dict, cutoff: str) -> dict:
    out = copy.deepcopy(data)
    for taxonomy in out["facts"].values():
        for concept in taxonomy.values():
            for unit, entries in list(concept["units"].items()):
                concept["units"][unit] = [e for e in entries if e.get("filed", "") < cutoff]
    return out


# ═══════════════════════════════════════════════════════════════════════════
# 1. SEC companyfacts ifrs-full
# ═══════════════════════════════════════════════════════════════════════════


class TestIfrsCompanyFacts:
    def test_fy2025_statements_are_read_with_the_issuers_terms(self) -> None:
        n = normalize_company_facts(_clls(), "CLLS")
        assert n.taxonomy == "ifrs-full"
        assert n.reporting_currency == "USD"
        assert (n.fiscal_year, n.form_type, n.period_basis) == (2025, "20-F", "annual")
        assert n.reporting_period_end == "2025-12-31"
        assert n.revenue == 72.95
        assert n.net_income == -67.59
        assert n.cash_and_equivalents == 61.53
        assert n.operating_cash_flow == -39.4
        assert n.capital_expenditures == 2.36
        assert n.operating_income == -33.08
        # The issuer's words travel with the figures: profit (loss), not "net income";
        # operating profit, never EBIT.
        assert n.taxonomy_labels["net_income"] == "Profit (loss)"
        assert n.taxonomy_labels["operating_income"] == "Operating profit (loss)"
        assert n.field_periods["net_income"]["concept"] == "ProfitLoss"
        notes = {dp.field_name: dp for dp in n.to_datapoints()}
        net = notes["sec_edgar.net_income"]
        assert "ifrs-full concept ProfitLoss" in net.note
        assert 'taxonomy label "Profit (loss)"' in net.note
        assert net.unit == "USD_m" and net.currency == "USD"

    def test_prior_year_comparative_is_the_same_filings_restated_figure(self) -> None:
        n = normalize_company_facts(_clls(), "CLLS")
        # FY2024 revenue 41,505k as the FY2025 20-F presents it.
        assert n.revenue_yoy_growth == pytest.approx((72.95 - 41.51) / 41.51 * 100, abs=0.1)

    def test_a_6k_a_interim_newer_than_the_20f_is_the_current_period(self) -> None:
        # Before the FY2025 20-F was filed, the H1 2025 6-K/A was newer than FY2024's.
        n = normalize_company_facts(_drop_filed_on_or_after(_clls(), "2026-01-01"), "CLLS")
        assert n.fiscal_year == 2024
        by_field = {f["field"]: f for f in n.current_period_facts}
        assert by_field["revenue"]["period"] == "H1 2025"
        assert by_field["revenue"]["numeric_value"] == 27.38  # six months, not Q2
        assert by_field["net_income"]["taxonomy_label"] == "Profit (loss)"
        assert by_field["cash_and_equivalents"]["period"] == "H1 2025"
        assert by_field["cash_and_equivalents"]["numeric_value"] == 59.81
        assert by_field["revenue"]["document_title"].startswith("Form 6-K/A")
        assert by_field["revenue"]["currency"] == "USD"

    def test_an_interim_older_than_the_annual_report_is_not_current(self) -> None:
        n = normalize_company_facts(_clls(), "CLLS")
        assert n.current_period_facts == []

    def test_us_gaap_wins_when_it_carries_the_statements(self) -> None:
        data = _clls()
        data["facts"]["us-gaap"] = {
            "Revenues": {"units": {"USD": [{
                "start": "2025-01-01", "end": "2025-12-31", "val": 5_000_000_000,
                "accn": "0000000000-26-000001", "fy": 2025, "fp": "FY", "form": "10-K",
                "filed": "2026-02-01"}]}},
        }
        n = normalize_company_facts(data, "XX")
        assert n.taxonomy == "us-gaap"
        assert n.revenue == 5000.0
        assert n.taxonomy_labels == {}

    def _eur_filer(self) -> dict:
        data = _clls()
        for concept in data["facts"]["ifrs-full"].values():
            concept["units"] = {
                ("EUR" if u == "USD" else ("EUR/shares" if u == "USD/shares" else u)): es
                for u, es in concept["units"].items()
            }
        return data

    def test_another_currency_is_kept_never_converted(self) -> None:
        n = normalize_company_facts(self._eur_filer(), "XEUR")
        assert n.reporting_currency == "EUR"
        assert n.revenue == 72.95
        units = {dp.field_name: dp.unit for dp in n.to_datapoints()}
        assert units["sec_edgar.revenue"] == "EUR_m"
        assert units["sec_edgar.eps_basic"] == "EUR"

    def test_another_currency_never_enters_the_usd_summary_or_peer_tables(self) -> None:
        from app.integrations.providers.sec_edgar_fundamentals import merge_fundamentals
        from app.services.agent_tools.peers import statement_items

        merged, warnings = merge_fundamentals(self._eur_filer(), "XEUR", "1627281", [])
        assert merged == []
        assert any("reports in EUR" in w and "no figure is converted" in w
                   for w in warnings)
        items, gaps = statement_items("XEUR", self._eur_filer())
        assert items == []
        assert any("reported in EUR" in g for g in gaps)

    def test_a_currency_tie_normalizes_nothing(self) -> None:
        data = _clls()
        rev = data["facts"]["ifrs-full"]["Revenue"]["units"]["USD"]
        data["facts"]["ifrs-full"] = {
            "Revenue": {"units": {"USD": rev, "EUR": copy.deepcopy(rev)}},
        }
        n = normalize_company_facts(data, "XX")
        assert n.revenue is None
        assert any("no single reporting currency" in w for w in n.warnings)

    def _with_borrowings(self, **values: float) -> dict:
        data = _clls()
        for concept, val in values.items():
            data["facts"]["ifrs-full"][concept] = {"units": {"USD": [{
                "end": "2025-12-31", "val": val, "accn": "0001193125-26-116428",
                "fy": 2025, "fp": "FY", "form": "20-F", "filed": "2026-03-20"}]}}
        return data

    def test_ifrs_borrowings_total_agreeing_with_its_legs_is_total_debt(self) -> None:
        n = normalize_company_facts(self._with_borrowings(**{
            IFRS_BORROWINGS: 30_000_000, IFRS_CURRENT_BORROWINGS: 10_000_000,
            IFRS_NONCURRENT_BORROWINGS: 20_000_000}), "CLLS")
        assert (n.total_debt, n.short_term_debt, n.long_term_debt) == (30.0, 10.0, 20.0)
        assert n.debt_basis["formula"] == "ifrs-full:Borrowings"
        assert n.taxonomy_labels["total_debt"] == "Borrowings"

    def test_the_borrowings_legs_are_the_ias_1_concept_names(self) -> None:
        # Review: "CurrentBorrowings" / "NoncurrentBorrowings" are not ifrs-full names.
        assert IFRS_CURRENT_BORROWINGS == (
            "CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings")
        assert IFRS_NONCURRENT_BORROWINGS == "NoncurrentPortionOfNoncurrentBorrowings"

    def test_one_leg_of_ifrs_borrowings_is_never_total_debt(self) -> None:
        n = normalize_company_facts(
            self._with_borrowings(**{IFRS_NONCURRENT_BORROWINGS: 20_000_000}), "CLLS")
        assert n.total_debt is None and n.debt_to_equity is None
        assert "partial sum" in n.debt_basis["withheld_reason"]

    def test_a_total_that_disagrees_with_its_legs_is_withheld(self) -> None:
        n = normalize_company_facts(self._with_borrowings(**{
            IFRS_BORROWINGS: 40_000_000, IFRS_CURRENT_BORROWINGS: 10_000_000,
            IFRS_NONCURRENT_BORROWINGS: 20_000_000}), "CLLS")
        assert n.total_debt is None
        assert "does not equal" in n.debt_basis["withheld_reason"]


# ═══════════════════════════════════════════════════════════════════════════
# 2. The inline-XBRL parser
# ═══════════════════════════════════════════════════════════════════════════


def _context(cid: str, *, start: str | None = None, end: str | None = None,
             instant: str | None = None, member: tuple[str, str] | None = None) -> str:
    segment = ""
    if member:
        segment = (
            "<xbrli:segment><xbrldi:explicitMember dimension=\"%s\">%s"
            "</xbrldi:explicitMember></xbrli:segment>" % member
        )
    period = (
        f"<xbrli:instant>{instant}</xbrli:instant>" if instant else
        f"<xbrli:startDate>{start}</xbrli:startDate><xbrli:endDate>{end}</xbrli:endDate>"
    )
    return (
        f'<xbrli:context id="{cid}"><xbrli:entity>'
        '<xbrli:identifier scheme="http://www.sec.gov/CIK">0001627281</xbrli:identifier>'
        f"{segment}</xbrli:entity><xbrli:period>{period}</xbrli:period></xbrli:context>"
    )


_UNITS = (
    '<xbrli:unit id="usd"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>'
    '<xbrli:unit id="eur"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit>'
    '<xbrli:unit id="pure"><xbrli:measure>xbrli:pure</xbrli:measure></xbrli:unit>'
    '<xbrli:unit id="usdPerShare"><xbrli:divide><xbrli:unitNumerator>'
    "<xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unitNumerator>"
    "<xbrli:unitDenominator><xbrli:measure>xbrli:shares</xbrli:measure>"
    "</xbrli:unitDenominator></xbrli:divide></xbrli:unit>"
)


def _dei(period_end_ctx: str, fy: str | None, fp: str | None, doc_type: str) -> str:
    out = (
        f'<ix:nonNumeric name="dei:DocumentType" contextRef="{period_end_ctx}">'
        f"{doc_type}</ix:nonNumeric>"
        f'<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="{period_end_ctx}"'
        ' format="ixt:date-monthname-day-year-en">June 30, 2026</ix:nonNumeric>'
    )
    if fy:
        out += (f'<ix:nonNumeric name="dei:DocumentFiscalYearFocus" '
                f'contextRef="{period_end_ctx}">{fy}</ix:nonNumeric>')
    if fp:
        out += (f'<ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" '
                f'contextRef="{period_end_ctx}">{fp}</ix:nonNumeric>')
    return out


def _doc(contexts: list[str], hidden: str, body: str) -> bytes:
    return (
        '<?xml version="1.0" encoding="utf-8"?>'
        '<html xmlns="http://www.w3.org/1999/xhtml" '
        'xmlns:ix="http://www.xbrl.org/2013/inlineXBRL"><head><title>x</title></head><body>'
        '<div style="display:none"><ix:header><ix:hidden>' + hidden + "</ix:hidden>"
        "<ix:resources>" + "".join(contexts) + _UNITS + "</ix:resources></ix:header></div>"
        + body + "</body></html>"
    ).encode()


def _nf(name: str, ctx: str, text: str, *, unit: str = "usd", scale: int = 3,
        decimals: str = "-3", sign: str = "", fmt: str = "ixt:num-dot-decimal") -> str:
    sign_attr = f' sign="{sign}"' if sign else ""
    return (
        f'<ix:nonFraction name="{name}" contextRef="{ctx}" unitRef="{unit}" '
        f'decimals="{decimals}" scale="{scale}" format="{fmt}"{sign_attr}>{text}'
        "</ix:nonFraction>"
    )


def _ifrs_half_year_6k() -> bytes:
    """A 20-F filer's H1 2026 results 6-K/A, statements in USD thousands."""
    contexts = [
        _context("h1", start="2026-01-01", end="2026-06-30"),
        _context("q2", start="2026-04-01", end="2026-06-30"),
        _context("h1py", start="2025-01-01", end="2025-06-30"),
        _context("ytd9", start="2025-10-01", end="2026-06-30"),
        _context("bs", instant="2026-06-30"),
        _context("bspy", instant="2025-12-31"),
        _context("h1seg", start="2026-01-01", end="2026-06-30",
                 member=("ifrs-full:SegmentsAxis", "clls:ClinicalProgramsMember")),
        _context("h1two", start="2026-01-01", end="2026-06-30",
                 member=("ifrs-full:SegmentsAxis", "clls:GroupMember")),
    ]
    hidden = _dei("h1", "2026", "Q2", "6-K") + _nf("ifrs-full:Equity", "bs", "97,110")
    body = (
        "<table><tr><td>Revenue</td><td>"
        + _nf("ifrs-full:Revenue", "h1", "27,380") + "</td><td>"
        + _nf("ifrs-full:Revenue", "h1py", "12,589") + "</td></tr>"
        + "<tr><td>Revenue Q2</td><td>" + _nf("ifrs-full:Revenue", "q2", "16,725")
        + "</td></tr><tr><td>Revenue 9M</td><td>"
        + _nf("ifrs-full:Revenue", "ytd9", "40,000") + "</td></tr>"
        + "<tr><td>Segment</td><td>" + _nf("ifrs-full:Revenue", "h1seg", "5,000")
        + _nf("ifrs-full:Revenue", "h1two", "1,000") + "</td></tr>"
        + "<tr><td>Profit (loss)</td><td>("
        + _nf("ifrs-full:ProfitLoss", "h1", "41,860", sign="-") + ")</td></tr>"
        + "<tr><td>Operating cash flow</td><td>("
        + _nf("ifrs-full:CashFlowsFromUsedInOperatingActivities", "h1", "27,470", sign="-")
        + ")</td></tr><tr><td>Capex</td><td>"
        + _nf("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
              "h1", "1,200")
        + "</td></tr><tr><td>Cash</td><td>"
        + _nf("ifrs-full:CashAndCashEquivalents", "bs", "59,809") + "</td><td>"
        + _nf("ifrs-full:CashAndCashEquivalents", "bspy", "143,251")
        + "</td></tr><tr><td>Basic EPS</td><td>"
        + _nf("ifrs-full:BasicEarningsLossPerShare", "h1", "0.42", unit="usdPerShare",
              scale=0, decimals="2", sign="-")
        + "</td></tr></table>"
    )
    return _doc(contexts, hidden, body)


class TestInlineXbrlParser:
    def test_scale_sign_and_contexts_are_read_from_the_tags(self) -> None:
        doc = parse_inline_xbrl(_ifrs_half_year_6k())
        assert doc.is_inline_xbrl and doc.refused is None
        assert (doc.period_end, doc.fiscal_year_focus, doc.fiscal_period_focus) == (
            date(2026, 6, 30), 2026, "Q2")
        assert doc.document_type == "6-K"
        by = {(f.concept, f.context_id): f for f in doc.facts}
        revenue = by[("ifrs-full:Revenue", "h1")]
        assert revenue.value == 27_380_000 and revenue.scale == 3
        assert revenue.unit == "USD" and revenue.duration_days == 180
        loss = by[("ifrs-full:ProfitLoss", "h1")]
        assert loss.value == -41_860_000 and loss.displayed == -41_860
        cash = by[("ifrs-full:CashAndCashEquivalents", "bs")]
        assert cash.is_instant and cash.instant == date(2026, 6, 30)
        seg = by[("ifrs-full:Revenue", "h1seg")]
        assert seg.dimensions == (("ifrs-full:SegmentsAxis", "clls:ClinicalProgramsMember"),)
        assert by[("ifrs-full:Revenue", "h1")].dimensions == ()
        eps = by[("ifrs-full:BasicEarningsLossPerShare", "h1")]
        assert eps.unit == "USD/shares"

    def test_a_hidden_fact_is_a_real_fact_and_is_marked(self) -> None:
        doc = parse_inline_xbrl(_ifrs_half_year_6k())
        equity = next(f for f in doc.facts if f.concept == "ifrs-full:Equity")
        assert equity.hidden is True
        assert equity.value == 97_110_000
        assert not next(f for f in doc.facts if f.concept == "ifrs-full:Revenue").hidden

    def test_oversized_input_is_refused_before_parsing(self) -> None:
        data = _ifrs_half_year_6k()
        doc = parse_inline_xbrl(data, max_bytes=len(data) - 1)
        assert doc.refused == REFUSED_TOO_LARGE
        assert doc.facts == []

    def test_a_document_without_inline_xbrl_yields_nothing(self) -> None:
        assert not looks_like_inline_xbrl(b"<html><body>Revenue 1,234</body></html>")
        assert parse_inline_xbrl(b"<html><body>Revenue 1,234</body></html>").facts == []

    def test_declared_entities_are_never_expanded(self) -> None:
        data = (
            b'<!DOCTYPE html [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            + _ifrs_half_year_6k().replace(b"<title>x</title>", b"<title>&xxe;</title>")
        )
        doc = parse_inline_xbrl(data)
        assert doc.refused is None
        assert any(f.concept == "ifrs-full:Revenue" for f in doc.facts)

    def test_comma_decimal_zero_dash_and_nil_formats(self) -> None:
        contexts = [_context("fy", start="2025-01-01", end="2025-12-31")]
        hidden = _dei("fy", "2025", "FY", "20-F")
        body = (
            _nf("ifrs-full:Revenue", "fy", "1.234,5", unit="eur", scale=6, decimals="-5",
                fmt="ixt:num-comma-decimal")
            + _nf("ifrs-full:AdministrativeExpense", "fy", "—", unit="eur",
                  fmt="ixt:fixed-zero")
            + '<ix:nonFraction name="ifrs-full:ProfitLoss" contextRef="fy" unitRef="eur" '
            'xsi:nil="true"/>'
        )
        doc = parse_inline_xbrl(_doc(contexts, hidden, body))
        by = {f.concept: f for f in doc.facts}
        assert by["ifrs-full:Revenue"].value == 1_234_500_000
        assert by["ifrs-full:AdministrativeExpense"].value == 0
        assert "ifrs-full:ProfitLoss" not in by
        assert doc.skipped == 1


# ═══════════════════════════════════════════════════════════════════════════
# 3. Tagged facts → statement facts
# ═══════════════════════════════════════════════════════════════════════════


def _facts(data: bytes) -> dict[tuple[str, str | None, str | None], object]:
    facts = statement_facts_from_inline_xbrl(parse_inline_xbrl(data))
    return {(f.label, f.period, f.scope): f for f in facts}


class TestStatementFacts:
    def test_an_ifrs_half_year_yields_its_own_periods_scopes_and_currency(self) -> None:
        facts = _facts(_ifrs_half_year_6k())
        revenue = facts[("revenue", "2026-H1", "Group")]
        assert revenue.value_numeric == 27_380 and revenue.scale == "thousand"
        assert revenue.currency == "USD" and revenue.unit == "currency_amount"
        assert revenue.extraction_method == EXTRACTION_METHOD_INLINE_XBRL
        assert revenue.validation_status == "validated" and revenue.confidence >= 0.75
        assert revenue.table_location == "ixbrl:ifrs-full:Revenue@h1"
        # The prior-year comparative gets ITS OWN period, never the document's.
        assert facts[("revenue", "2025-H1", "Group")].value_numeric == 12_589
        assert facts[("revenue", "2026-Q2", "Group")].value_numeric == 16_725
        # A nine-month span is not a period this store represents: skipped.
        assert not any(p and "Q3" in p for (_l, p, _s) in facts)
        # H1, prior H1, Q2, and two single-member segment facts.
        assert len([k for k in facts if k[0] == "revenue"]) == 5

    def test_a_dimensional_fact_is_a_segment_never_the_group(self) -> None:
        from app.services.sources.fact_scope import parse_scope

        facts = _facts(_ifrs_half_year_6k())
        segment = facts[("revenue", "2026-H1", "Clinical Programs")]
        assert parse_scope(segment.scope).is_segment
        # A member literally named "Group" is still a segment.
        named_group = [f for (lbl, p, s), f in facts.items()
                       if lbl == "revenue" and s and s.startswith("Group (")]
        assert len(named_group) == 1 and parse_scope(named_group[0].scope).is_segment
        assert facts[("revenue", "2026-H1", "Group")].value_numeric == 27_380

    def test_profit_loss_keeps_sign_and_balances_take_the_half_year(self) -> None:
        facts = _facts(_ifrs_half_year_6k())
        loss = facts[("net_income", "2026-H1", "Group")]
        assert loss.value_numeric == -41_860 and loss.value_text == "-41,860"
        assert "taxonomy label: Profit (loss)" in loss.validation_notes
        assert facts[("cash_and_equivalents", "2026-H1", "Group")].value_numeric == 59_809
        # The prior fiscal year-end balance is the FY2025 balance.
        assert facts[("cash_and_equivalents", "2025", "Group")].value_numeric == 143_251
        # Spend is positive; the hidden equity fact is kept.
        assert facts[("capital_expenditure", "2026-H1", "Group")].value_numeric == 1_200
        assert facts[("total_equity", "2026-H1", "Group")].value_numeric == 97_110
        # No per-share field in the statements view: EPS is not stored here.
        assert not any(k[0].startswith("eps") for k in facts)

    def test_without_the_dei_fiscal_focus_no_fact_is_produced(self) -> None:
        data = _ifrs_half_year_6k().replace(b"dei:DocumentFiscalPeriodFocus", b"dei:Other")
        assert statement_facts_from_inline_xbrl(parse_inline_xbrl(data)) == []

    def test_a_us_10q_yields_the_current_quarter_and_one_net_income(self) -> None:
        contexts = [
            _context("q", start="2026-04-01", end="2026-06-30"),
            _context("ytd", start="2026-01-01", end="2026-06-30"),
            _context("qpy", start="2025-04-01", end="2025-06-30"),
            _context("bs", instant="2026-06-30"),
            _context("fye", instant="2025-12-31"),
        ]
        hidden = _dei("ytd", "2026", "Q2", "10-Q")
        body = (
            _nf("us-gaap:Revenues", "q", "8,293", scale=6, decimals="-6")
            + _nf("us-gaap:Revenues", "ytd", "16,100", scale=6, decimals="-6")
            + _nf("us-gaap:Revenues", "qpy", "7,885", scale=6, decimals="-6")
            + _nf("us-gaap:NetIncomeLoss", "q", "1,284", scale=6, decimals="-6")
            + _nf("us-gaap:ProfitLoss", "q", "1,300", scale=6, decimals="-6")
            + _nf("us-gaap:NetCashProvidedByUsedInOperatingActivities", "ytd", "2,700",
                  scale=6, decimals="-6")
            + _nf("us-gaap:CashAndCashEquivalentsAtCarryingValue", "bs", "6,100", scale=6,
                  decimals="-6")
            + _nf("us-gaap:CashAndCashEquivalentsAtCarryingValue", "fye", "6,561", scale=6,
                  decimals="-6")
            # The same figure tagged again in the MD&A, in billions: one reading.
            + _nf("us-gaap:Revenues", "q", "8.3", scale=9, decimals="-8")
        )
        facts = _facts(_doc(contexts, hidden, body))
        assert facts[("revenue", "2026-Q2", "Group")].value_numeric == 8_293
        assert facts[("revenue", "2026-Q2", "Group")].scale == "million"
        assert facts[("revenue", "2026-H1", "Group")].value_numeric == 16_100
        assert facts[("revenue", "2025-Q2", "Group")].value_numeric == 7_885
        net = facts[("net_income", "2026-Q2", "Group")]
        assert net.value_numeric == 1_284  # NetIncomeLoss outranks ProfitLoss
        assert net.table_location.startswith("ixbrl:us-gaap:NetIncomeLoss@")
        assert facts[("operating_cash_flow", "2026-H1", "Group")].value_numeric == 2_700
        assert facts[("cash_and_equivalents", "2026-H1", "Group")].value_numeric == 6_100
        assert facts[("cash_and_equivalents", "2025", "Group")].value_numeric == 6_561

    def test_two_readings_of_one_concept_that_disagree_fill_nothing(self) -> None:
        contexts = [_context("q", start="2026-04-01", end="2026-06-30"),
                    _context("ytd", start="2026-01-01", end="2026-06-30")]
        hidden = _dei("ytd", "2026", "Q2", "10-Q")
        body = (_nf("us-gaap:Revenues", "q", "8,293", scale=6, decimals="-6")
                + _nf("us-gaap:Revenues", "q", "9,000", scale=6, decimals="-6")
                + _nf("us-gaap:Revenues", "ytd", "16,100", scale=6, decimals="-6"))
        facts = _facts(_doc(contexts, hidden, body))
        assert ("revenue", "2026-H1", "Group") in facts  # the frame itself is accepted
        assert ("revenue", "2026-Q2", "Group") not in facts

    def test_a_52_53_week_fiscal_year_is_labelled_by_its_fiscal_year(self) -> None:
        contexts = [
            _context("fy", start="2024-09-29", end="2025-09-27"),
            _context("py", start="2023-10-01", end="2024-09-28"),
        ]
        hidden = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="fy">'
            "September 27, 2025</ix:nonNumeric>"
            '<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="fy">2025'
            '</ix:nonNumeric><ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" '
            'contextRef="fy">FY</ix:nonNumeric>'
        )
        body = (_nf("us-gaap:Revenues", "fy", "416,161", scale=6, decimals="-6")
                + _nf("us-gaap:Revenues", "py", "391,035", scale=6, decimals="-6"))
        facts = _facts(_doc(contexts, hidden, body))
        assert facts[("revenue", "2025", "Group")].value_numeric == 416_161
        assert facts[("revenue", "2024", "Group")].value_numeric == 391_035

    def test_a_bank_yields_its_own_lines_and_no_industrial_derivation(self) -> None:
        from app.services.pipeline.issuer_financials import build_financial_statements_state

        contexts = [
            _context("q", start="2026-07-01", end="2026-09-30"),
            _context("ytd", start="2026-01-01", end="2026-09-30"),
            _context("bs", instant="2026-09-30"),
        ]
        hidden = (
            '<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="ytd">'
            "2026-09-30</ix:nonNumeric>"
            '<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="ytd">2026'
            '</ix:nonNumeric><ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" '
            'contextRef="ytd">Q3</ix:nonNumeric>'
        )
        body = (
            _nf("us-gaap:InterestIncomeExpenseNet", "q", "23,500", scale=6, decimals="-6")
            + _nf("us-gaap:ProvisionForLoanLeaseAndOtherLosses", "q", "3,100", scale=6,
                  decimals="-6")
            + _nf("us-gaap:Deposits", "bs", "2,450,000", scale=6, decimals="-6")
            + _nf("us-gaap:FinancingReceivableAllowanceForCreditLosses", "bs", "26,900",
                  scale=6, decimals="-6")
            + _nf("us-gaap:CommonEquityTierOneCapitalToRiskWeightedAssets", "bs", "15.7",
                  unit="pure", scale=-2, decimals="3")
            + _nf("us-gaap:CashAndCashEquivalentsAtCarryingValue", "bs", "300,000", scale=6,
                  decimals="-6")
            + _nf("us-gaap:NetCashProvidedByUsedInOperatingActivities", "q", "40,000",
                  scale=6, decimals="-6", sign="-")
            + _nf("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", "q", "1,000",
                  scale=6, decimals="-6")
        )
        validated = statement_facts_from_inline_xbrl(parse_inline_xbrl(
            _doc(contexts, hidden, body)))
        facts = {(f.label, f.period): f for f in validated}
        assert facts[("net_interest_income", "2026-Q3")].value_numeric == 23_500
        assert facts[("provision_for_credit_losses", "2026-Q3")].value_numeric == 3_100
        assert facts[("deposits", "2026-Q3")].value_numeric == 2_450_000
        assert facts[("allowance_for_credit_losses", "2026-Q3")].value_numeric == 26_900
        # No standard us-gaap CET1 concept is mapped (review: banks tag an extension).
        assert ("cet1_ratio", "2026-Q3") not in facts
        assert ("taxonomy label: Interest income (expense), net"
                in facts[("net_interest_income", "2026-Q3")].validation_notes)

        state = build_financial_statements_state(
            [_as_loaded(f, i) for i, f in enumerate(validated)],
            now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        )
        slots = state["slots"]
        assert "net_interest_income_current_period" in slots
        assert "deposits_current_period" in slots
        assert not any("free_cash_flow" in k or "ebitda" in k for k in slots)
        assert all(d.get("status") != "computed" for d in state["derived"])
        assert any(d.get("status") == "not_meaningful" for d in state["derived"])


def _as_loaded(fact, index: int) -> dict:  # noqa: ANN001
    """A ``ValidatedFact`` in the dict shape ``load_statement_facts`` returns."""
    from app.services.sources.primary_document_extractor import _confidence_bucket

    return {
        "field": fact.label, "value": fact.value_text,
        "numeric_value": fact.value_numeric, "unit": fact.unit,
        "currency": fact.currency, "scale": fact.scale, "period": fact.period,
        "scope": fact.scope, "source_url": SEC_URL, "page_number": None,
        "excerpt_id": fact.table_location,
        "confidence": _confidence_bucket(fact.confidence), "fact_id": f"f{index}",
        "document_id": "d1", "document_title": "6-K/A", "document_date": "2026-09-11",
        "document_source_type": "html",
    }


# ═══════════════════════════════════════════════════════════════════════════
# 4. The hook on the SEC filing-body path
# ═══════════════════════════════════════════════════════════════════════════


class TestHook:
    def test_only_an_sec_archives_document_is_read(self) -> None:
        data = _ifrs_half_year_6k()
        assert with_inline_xbrl_facts([], data, source_url=SEC_URL)
        assert with_inline_xbrl_facts([], data, source_url="https://cellectis.com/x.htm") == []
        assert with_inline_xbrl_facts(
            [], data, source_url="http://www.sec.gov/Archives/x.htm") == []

    def test_a_text_reading_of_a_tagged_figure_gives_way_to_the_tag(self) -> None:
        from app.services.sources.extracted_fact_validator import ValidatedFact

        text_same = ValidatedFact(label="revenue", value_numeric=27.4, period="2026-H1",
                                  extraction_method="html", confidence=0.8,
                                  validation_status="validated", scale="million",
                                  currency="USD", scope=None)
        text_other = ValidatedFact(label="employees", value_numeric=230, period="2026-H1",
                                   extraction_method="html", confidence=0.8,
                                   validation_status="validated")
        tagged = statement_facts_from_inline_xbrl(parse_inline_xbrl(_ifrs_half_year_6k()))
        merged = merge_with_text_facts([text_same, text_other], tagged)
        revenue = [f for f in merged if f.label == "revenue" and f.period == "2026-H1"
                   and f.scope == "Group"]
        assert len(revenue) == 1 and revenue[0].extraction_method == "inline_xbrl"
        assert text_same not in merged and text_other in merged

    def test_a_reader_failure_never_costs_the_text_facts(self, monkeypatch) -> None:  # noqa: ANN001
        from app.services.sources import inline_xbrl_facts as mod

        def _boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("bad")

        monkeypatch.setattr(mod, "parse_inline_xbrl", _boom)
        sentinel = object()
        assert mod.with_inline_xbrl_facts([sentinel], _ifrs_half_year_6k(),
                                          source_url=SEC_URL) == [sentinel]

    async def test_the_sec_fetch_path_attaches_tagged_facts(self) -> None:
        from app.core.config import Settings
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        fetched = DocumentFetchResult(
            requested_url=SEC_URL, final_url=SEC_URL, status_code=200,
            content_type="text/html", document_type="html", content=_ifrs_half_year_6k(),
        )
        artifact = await live_fetchers._artifact_from_fetch(
            fetched, title="6-K/A 0001193125-26-389336", original_language=None,
            issuer_context=None, cfg=Settings(v3_corpus_enabled=False), fetch_ms=1,
        )
        assert artifact.status == "extracted"
        tagged = [f for f in artifact.validated_facts
                  if f.extraction_method == EXTRACTION_METHOD_INLINE_XBRL]
        assert {(f.label, f.period) for f in tagged} >= {
            ("revenue", "2026-H1"), ("net_income", "2026-H1"),
            ("cash_and_equivalents", "2026-H1"), ("operating_cash_flow", "2026-H1"),
        }


# ═══════════════════════════════════════════════════════════════════════════
# 5. Persisted, then read by the statements view (SQLite)
# ═══════════════════════════════════════════════════════════════════════════


@pytest.fixture
async def session():  # noqa: ANN201
    from app.db.base import Base

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", future=True, poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


async def _company(session):  # noqa: ANN001, ANN202
    from app.models.company import Company

    company = Company(id=uuid.uuid4(), ticker="CLLS", exchange="NASDAQ",
                      name="Cellectis S.A.", status="new")
    session.add(company)
    await session.flush()
    return company


class TestStatementsView:
    async def test_a_held_6k_a_yields_facts_read_and_a_current_period(self, session) -> None:  # noqa: ANN001
        from app.core.config import Settings
        from app.services.extracted_document_service import (
            persist_primary_document_artifacts,
        )
        from app.services.pipeline.issuer_financials import financial_statements_for
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        company = await _company(session)
        cfg = Settings(primary_document_ingestion_enabled=True,
                       report_citation_persistence_enabled=True, v3_corpus_enabled=False)
        fetched = DocumentFetchResult(
            requested_url=SEC_URL, final_url=SEC_URL, status_code=200,
            content_type="text/html", document_type="html", content=_ifrs_half_year_6k(),
        )
        artifact = await live_fetchers._artifact_from_fetch(
            fetched, title="6-K/A 0001193125-26-389336", original_language=None,
            issuer_context=None, cfg=cfg, fetch_ms=1,
            # ADR-090: the SEC leg dates every filing document with its filing date;
            # an undated one's bare-year comparative is never read as annual.
            published_at=date(2026, 10, 30),
        )
        await persist_primary_document_artifacts(
            session, artifacts=[artifact], company_id=company.id, agent_run_id=None,
            cfg=cfg,
        )
        state = await financial_statements_for(
            session, company, core_disclosures=None, core_filings=None,
            now=datetime(2026, 11, 3, tzinfo=timezone.utc),
        )
        assert state["facts_read"] > 0
        assert state["current_period"]["state"] == "facts_extracted"
        # H1 and Q2 end together; the existing slot rule prefers the narrower quarter.
        revenue = state["slots"]["revenue_current_period"]
        assert revenue["numeric_value"] == 16_725 and revenue["period"] == "2026-Q2"
        assert revenue["currency"] == "USD" and revenue["scale"] == "thousand"
        assert state["slots"]["operating_cash_flow_current_period"]["period"] == "2026-H1"
        assert revenue["xbrl_concept"] == "ifrs-full:Revenue"
        net = state["slots"]["net_income_current_period"]
        assert net["taxonomy_label"] == "Profit (loss)"
        # The FY2025 year-end cash from the balance sheet comparative fills the annual slot.
        assert state["slots"]["cash_and_equivalents_primary_filing"]["period"] == "2025"
        # Runway over one coherent H1 basis: cash, operating cash flow and capex.
        runway = [d for d in state["derived"] if d.get("basis") == "current_period"]
        assert runway and runway[0]["status"] == "computed"


# ═══════════════════════════════════════════════════════════════════════════
# 6. A held filing read before this change is re-read once (bridge state A)
# ═══════════════════════════════════════════════════════════════════════════


class TestHeldFilingReread:
    async def _held(self, session, pipeline_version: int, url: str = SEC_URL):  # noqa: ANN001, ANN202
        from app.models.extracted_document import ExtractedDocument

        company = await _company(session)
        doc = ExtractedDocument(
            id=uuid.uuid4(), company_id=company.id, canonical_url=url,
            content_hash=uuid.uuid4().hex * 2, source_type="html", provider="sec_edgar",
            source_tier="T1_primary_filing", title="6-K/A", mime_type="text/html",
            status="extracted", extraction_method="html",
            retrieved_at=datetime(2026, 9, 12, tzinfo=timezone.utc),
            pipeline_version=pipeline_version,
        )
        session.add(doc)
        await session.flush()
        return company, doc

    def _ready(self, monkeypatch, doc_id):  # noqa: ANN001, ANN202
        from app.services.corpus import filing_evidence as fe

        async def _state(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            return fe.FilingEvidenceState(
                state=fe.STATE_READY, accession="0001193125-26-389336",
                version_id=uuid.uuid4(), extracted_document_id=doc_id,
                chunk_count=4, indexable_chunk_count=4,
            )

        async def _no_stamp(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            return 0

        monkeypatch.setattr(fe, "filing_evidence_state", _state)
        monkeypatch.setattr(fe, "stamp_filing_date", _no_stamp)

    async def _ensure(self, session, company, acquire, **kw):  # noqa: ANN001, ANN003, ANN202
        from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

        return await ensure_filing_corpus_evidence(
            session, company_id=company.id, cik="1627281",
            accession="0001193125-26-389336", form=kw.pop("form", "6-K/A"),
            cfg=SimpleNamespace(v3_filing_body_bridge_enabled=True, v3_corpus_enabled=True),
            acquire=acquire, document_name="clls-ex99_1.htm", **kw,
        )

    async def test_a_filing_held_before_the_reader_is_re_read_once(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.services.corpus.filing_evidence import AcquireOutcome

        company, doc = await self._held(session, pipeline_version=20)
        self._ready(monkeypatch, doc.id)
        calls: list[dict] = []

        async def _acquire(_s, **kw):  # noqa: ANN001, ANN003, ANN202
            calls.append(kw)
            return AcquireOutcome(acquired=True, fetched=True)

        out = await self._ensure(session, company, _acquire)
        assert len(calls) == 1 and calls[0]["document_name"] == "clls-ex99_1.htm"
        assert out.state == "ready" and out.attempted is True and out.fetched is True
        assert any("ADR-085" in n for n in out.notes)

    async def test_a_current_document_is_reused_without_a_fetch(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.services.sources.extraction_pipeline_version import (
            INLINE_XBRL_MIN_PIPELINE_VERSION,
        )

        company, doc = await self._held(session, INLINE_XBRL_MIN_PIPELINE_VERSION)
        self._ready(monkeypatch, doc.id)

        async def _never(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("a current document is never re-read")

        out = await self._ensure(session, company, _never)
        assert out.reused is True and out.attempted is False

    async def test_no_re_read_with_the_budget_spent_or_for_a_form_without_ixbrl(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        company, doc = await self._held(session, pipeline_version=15)
        self._ready(monkeypatch, doc.id)

        async def _never(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            raise AssertionError("not re-read")

        assert (await self._ensure(session, company, _never, ready_only=True)).reused
        assert (await self._ensure(session, company, _never, form="8-K")).reused

    async def test_a_non_sec_document_is_never_re_read(self, session) -> None:  # noqa: ANN001
        from app.services.corpus.held_filing_facts import held_filing_needs_fact_reread

        _company_, doc = await self._held(session, 15, url="https://cellectis.com/r.htm")
        assert not await held_filing_needs_fact_reread(
            session, extracted_document_id=doc.id, form="6-K")


# ═══════════════════════════════════════════════════════════════════════════
# 7. The shared concept map
# ═══════════════════════════════════════════════════════════════════════════


def test_every_mapped_statement_field_is_in_the_statements_vocabulary() -> None:
    from app.integrations.xbrl_statement_concepts import STATEMENT_CONCEPTS
    from app.services.sources.primary_fact_parser import ISSUER_STATEMENT_FIELDS

    for concept in STATEMENT_CONCEPTS:
        assert concept.statement_field is None or (
            concept.statement_field in ISSUER_STATEMENT_FIELDS), concept.concept
        assert concept.statement_field != "total_debt", "no single tag is total debt"


def test_ifrs_terms_are_not_us_gaap_terms() -> None:
    from app.integrations.xbrl_statement_concepts import taxonomy_label

    assert taxonomy_label("ifrs-full:ProfitLoss") == "Profit (loss)"
    assert "EBIT" not in (taxonomy_label("ifrs-full:ProfitLossFromOperatingActivities") or "")
    assert taxonomy_label("ifrs-full:Borrowings") == "Borrowings"


# ═══════════════════════════════════════════════════════════════════════════
# 8. Review of PR #324 — security (F1–F6) and correctness (B1, B2, M1–M3, …)
# ═══════════════════════════════════════════════════════════════════════════


def _frame(ctx: str, fy: str, fp: str, end: str) -> str:
    return (
        f'<ix:nonNumeric name="dei:DocumentPeriodEndDate" contextRef="{ctx}">{end}'
        "</ix:nonNumeric>"
        f'<ix:nonNumeric name="dei:DocumentFiscalYearFocus" contextRef="{ctx}">{fy}'
        "</ix:nonNumeric>"
        f'<ix:nonNumeric name="dei:DocumentFiscalPeriodFocus" contextRef="{ctx}">{fp}'
        "</ix:nonNumeric>"
    )


def _q1_doc(body: str, extra_contexts: list[str] | None = None, hidden: str = "") -> bytes:
    """A US 10-Q for Q1 2026 (frame: the Q1 year-to-date span)."""
    contexts = [
        _context("q", start="2026-01-01", end="2026-03-31"),
        _context("bs", instant="2026-03-31"),
        *(extra_contexts or []),
    ]
    return _doc(contexts, _frame("q", "2026", "Q1", "2026-03-31") + hidden, body)


def _timed_parse(data: bytes) -> tuple[float, object]:
    import time

    started = time.perf_counter()
    document = parse_inline_xbrl(data)
    return time.perf_counter() - started, document


def _peak_mb(data: bytes) -> float:
    import tracemalloc

    tracemalloc.start()
    try:
        parse_inline_xbrl(data)
        return tracemalloc.get_traced_memory()[1] / 1e6
    finally:
        tracemalloc.stop()


class TestParserBounds:
    """F1 — work per text chunk is bounded; F2/F3/F5 — numbers that are not figures."""

    _NESTED_SKIP = (
        '<ix:nonNumeric name="us-gaap:PolicyTextBlock" contextRef="c">' * 1000
        + "<p>text</p>" * 10_000
        + "</ix:nonNumeric>" * 1000
        + '<ix:nonFraction name="x" contextRef="c">1</ix:nonFraction>'
    ).encode()
    _UNCLOSED = (
        '<ix:nonFraction name="us-gaap:Revenues" contextRef="c" unitRef="u">' * 5000
        + "<b>x</b>" * 5000
    ).encode()

    def test_nested_text_blocks_cost_linear_time_and_bounded_memory(self) -> None:
        # Before the fix: every chunk was appended to all 1,000 open blocks.
        elapsed, document = _timed_parse(self._NESTED_SKIP)
        assert elapsed < 1.0
        assert document.truncated is True  # nesting beyond the bound is ignored
        assert _peak_mb(self._NESTED_SKIP) < 20

    def test_unclosed_numeric_facts_cost_linear_time_and_bounded_memory(self) -> None:
        elapsed, document = _timed_parse(self._UNCLOSED)
        assert elapsed < 1.0 and document.truncated is True
        assert _peak_mb(self._UNCLOSED) < 20

    def test_the_open_stack_and_collected_text_are_bounded(self) -> None:
        from app.services.sources.inline_xbrl import (
            MAX_FACT_TEXT,
            MAX_OPEN_FACTS,
            _Collector,
        )

        collector = _Collector()
        collector.feed(self._UNCLOSED.decode())
        assert len(collector._open) == MAX_OPEN_FACTS
        assert all(b["len"] <= MAX_FACT_TEXT for b in collector._open)
        skip = _Collector()
        skip.feed(self._NESTED_SKIP.decode())
        # A skipped text block collects no text at all.
        assert all(b["text"] == [] for b in skip._open if b["kind"] == "skip")

    def test_a_fact_with_more_text_than_any_figure_is_skipped(self) -> None:
        contexts = [_context("bs", instant="2026-06-30")]
        body = (_nf("us-gaap:Assets", "bs", "1" * 300)
                + _nf("us-gaap:Liabilities", "bs", "1,234"))
        document = parse_inline_xbrl(_doc(contexts, "", body))
        assert [f.concept for f in document.facts] == ["us-gaap:Liabilities"]
        assert document.skipped == 1

    @pytest.mark.parametrize("digits", [31, 40, 400])
    def test_a_run_of_nines_is_not_a_number_and_never_infinite(self, digits: int) -> None:
        contexts = [_context("bs", instant="2026-06-30")]
        body = _nf("us-gaap:Assets", "bs", "9" * digits)
        document = parse_inline_xbrl(_doc(contexts, "", body))
        assert document.facts == [] and document.skipped == 1

    def test_a_non_finite_value_is_never_handed_on(self) -> None:
        from decimal import Decimal

        from app.integrations.xbrl_statement_concepts import STATEMENT_CONCEPT_BY_QNAME
        from app.services.sources.inline_xbrl import InlineXbrlFact
        from app.services.sources.inline_xbrl_facts import _value_fields

        huge = InlineXbrlFact(
            concept="us-gaap:Assets", value=Decimal("1E+400"), displayed=Decimal("1E+400"),
            scale=0, unit="USD", decimals=None, context_id="bs",
            instant=date(2026, 6, 30), start=None, end=None, dimensions=(), text="x",
        )
        assert _value_fields(huge, STATEMENT_CONCEPT_BY_QNAME["us-gaap:Assets"]) is None

    def test_an_absurd_decimals_attribute_never_costs_the_document(self) -> None:
        from app.services.sources.inline_xbrl_facts import _precision

        assert _precision("99999999999") == 0 and _precision("-99999999999") == 0
        contexts = [_context("q", start="2026-01-01", end="2026-03-31")]
        body = (_nf("us-gaap:Revenues", "q", "8,000", decimals="99999999999")
                + _nf("us-gaap:Revenues", "q", "8,000", decimals="-3"))
        facts = _facts(_doc(contexts, _frame("q", "2026", "Q1", "2026-03-31"), body))
        assert facts[("revenue", "2026-Q1", "Group")].value_numeric == 8_000

    def test_one_arithmetic_failure_skips_one_fact_not_the_document(
        self, monkeypatch  # noqa: ANN001
    ) -> None:
        from decimal import Overflow

        from app.services.sources import inline_xbrl as mod

        real = mod._resolve

        def _flaky(builder, collector):  # noqa: ANN001, ANN202
            if builder["name"] == "us-gaap:Assets":
                raise Overflow("crafted")
            return real(builder, collector)

        monkeypatch.setattr(mod, "_resolve", _flaky)
        contexts = [_context("bs", instant="2026-06-30")]
        body = _nf("us-gaap:Assets", "bs", "1") + _nf("us-gaap:Liabilities", "bs", "2")
        document = mod.parse_inline_xbrl(_doc(contexts, "", body))
        assert [f.concept for f in document.facts] == ["us-gaap:Liabilities"]

    def test_only_ascii_digits_are_a_number(self) -> None:
        contexts = [_context("bs", instant="2026-06-30")]
        arabic_indic = "١٢٣"
        document = parse_inline_xbrl(_doc(contexts, "", _nf("us-gaap:Assets", "bs",
                                                              arabic_indic)))
        assert document.facts == [] and document.skipped == 1

    def test_known_transformation_formats_and_nothing_else(self) -> None:
        contexts = [_context("bs", instant="2026-06-30")]

        def read(text: str, fmt: str) -> list[str]:
            body = _nf("us-gaap:Assets", "bs", text, scale=0, decimals="1", fmt=fmt)
            return [str(f.value) for f in parse_inline_xbrl(_doc(contexts, "", body)).facts]

        # TR1 numdotcomma: "." thousands, "," decimal (was read as 1.2345).
        assert read("1.234,5", "ixt:numdotcomma") == ["1234.5"]
        assert read("1 234,5", "ixt:numspacecomma") == ["1234.5"]
        assert read("1,234.5", "ixt:numcommadot") == ["1234.5"]
        assert read("1.234.567,8", "ixt:num-comma-decimal") == ["1234567.8"]
        assert read("1,234.5", "ixt:num-dot-decimal") == ["1234.5"]
        # An unknown or unread format is skipped, never read with a guessed rule.
        assert read("1,234", "ixt-sec:numunitdecimal") == []
        assert read("five", "ixt:num-word-en") == []
        assert read("1,234", "ixt:numfoo") == []


class TestValueTextBound:
    async def test_a_persisted_value_text_is_clipped(self, session) -> None:  # noqa: ANN001
        from sqlalchemy import func, select

        from app.core.config import Settings
        from app.models.extracted_document import ExtractedFact
        from app.services.extracted_document_service import (
            _VALUE_TEXT_MAX,
            persist_primary_document_artifacts,
        )
        from app.services.sources import live_fetchers
        from app.services.sources.document_fetcher import DocumentFetchResult

        company = await _company(session)
        cfg = Settings(primary_document_ingestion_enabled=True,
                       report_citation_persistence_enabled=True, v3_corpus_enabled=False)
        fetched = DocumentFetchResult(
            requested_url=SEC_URL, final_url=SEC_URL, status_code=200,
            content_type="text/html", document_type="html", content=_ifrs_half_year_6k(),
        )
        artifact = await live_fetchers._artifact_from_fetch(
            fetched, title="6-K/A", original_language=None, issuer_context=None, cfg=cfg,
            fetch_ms=1,
        )
        artifact.validated_facts[0].value_text = "9" * 50_000
        await persist_primary_document_artifacts(
            session, artifacts=[artifact], company_id=company.id, agent_run_id=None, cfg=cfg,
        )
        longest = (await session.execute(
            select(func.max(func.length(ExtractedFact.value_text))))).scalar_one()
        assert longest == _VALUE_TEXT_MAX


class TestScopeAxes:
    """B1 — only a segment axis names a segment."""

    def _facts_for(self, axis: str, member: str) -> dict:
        contexts = [_context("seg", start="2026-01-01", end="2026-03-31",
                             member=(axis, member))]
        body = (_nf("us-gaap:Revenues", "q", "9,000") + _nf("us-gaap:Revenues", "seg", "4,000")
                + _nf("us-gaap:NetIncomeLoss", "seg", "52", sign="-"))
        return _facts(_q1_doc(body, contexts))

    @pytest.mark.parametrize(
        ("axis", "member"),
        [
            ("us-gaap:StatementEquityComponentsAxis", "us-gaap:RetainedEarningsMember"),
            ("us-gaap:StatementEquityComponentsAxis", "us-gaap:TreasuryStockCommonMember"),
            ("us-gaap:ReclassificationOutOfAccumulatedOtherComprehensiveIncomeAxis",
             "us-gaap:ReclassificationOutOfAccumulatedOtherComprehensiveIncomeMember"),
            ("us-gaap:AssetPledgedAsCollateralAxis", "us-gaap:AssetPledgedAsCollateralMember"),
            ("srt:ConsolidatedEntitiesAxis",
             "us-gaap:VariableInterestEntityPrimaryBeneficiaryMember"),
            ("us-gaap:FairValueByMeasurementBasisAxis",
             "us-gaap:PortionAtOtherThanFairValueFairValueDisclosureMember"),
            ("us-gaap:ConsolidationItemsAxis", "us-gaap:OperatingSegmentsMember"),
            ("srt:RestatementAxis", "srt:ScenarioPreviouslyReportedMember"),
            ("srt:CondensedFinancialStatementsCaptionsLineItems", "srt:ParentCompanyMember"),
            ("acme:CustomAxis", "acme:WidgetsMember"),
            # A single-segment filer's one segment, and reconciling columns.
            ("us-gaap:StatementBusinessSegmentsAxis", "pypl:ReportableSegmentMember"),
            ("us-gaap:StatementBusinessSegmentsAxis", "us-gaap:CorporateNonSegmentMember"),
            ("us-gaap:StatementBusinessSegmentsAxis", "us-gaap:IntersegmentEliminationMember"),
        ],
    )
    def test_a_non_segment_axis_is_skipped(self, axis: str, member: str) -> None:
        facts = self._facts_for(axis, member)
        assert {k[2] for k in facts} == {"Group"}
        assert facts[("revenue", "2026-Q1", "Group")].value_numeric == 9_000

    @pytest.mark.parametrize(
        ("axis", "member", "label"),
        [
            ("us-gaap:StatementBusinessSegmentsAxis", "jpm:CommercialAndInvestmentBankMember",
             "Commercial And Investment Bank"),
            ("srt:StatementGeographicalAxis", "srt:AmericasMember", "Americas (geographic)"),
            ("us-gaap:StatementGeographicalAxis", "country:US", "US (geographic)"),
            ("srt:ProductOrServiceAxis", "adbe:SubscriptionRevenueMember",
             "Subscription Revenue (product)"),
            ("ifrs-full:SegmentsAxis", "clls:ClinicalProgramsMember", "Clinical Programs"),
        ],
    )
    def test_a_segment_axis_names_a_segment(self, axis: str, member: str, label: str) -> None:
        from app.services.sources.fact_scope import parse_scope

        fact = self._facts_for(axis, member)[("revenue", "2026-Q1", label)]
        assert fact.value_numeric == 4_000 and parse_scope(fact.scope).is_segment

    @pytest.mark.parametrize(
        ("axis", "member", "kind"),
        [
            ("srt:StatementGeographicalAxis", "srt:NorthAmericaMember", "geographic"),
            ("us-gaap:StatementGeographicalAxis", "country:GB", "geographic"),
            ("srt:ProductOrServiceAxis", "us-gaap:ProductMember", "product"),
        ],
    )
    def test_a_geographic_or_product_split_fills_revenue_only(
        self, axis: str, member: str, kind: str
    ) -> None:
        # JPM 10-K: North America 44,573m net income, GB total assets — not segments.
        contexts = [_context("seg", start="2026-01-01", end="2026-03-31",
                             member=(axis, member)),
                    _context("segbs", instant="2026-03-31", member=(axis, member))]
        body = (_nf("us-gaap:Revenues", "seg", "4,000")
                + _nf("us-gaap:NetIncomeLoss", "seg", "1,500")
                + _nf("us-gaap:Assets", "segbs", "90,000"))
        facts = _facts(_q1_doc(body, contexts))
        assert {k[0] for k in facts} == {"revenue"}
        (revenue,) = facts.values()
        assert revenue.scope.endswith(f"({kind})")
        assert f"scope axis: {kind}" in revenue.validation_notes

    def test_a_business_segment_on_any_field_carries_its_axis_kind(self) -> None:
        facts = self._facts_for("us-gaap:StatementBusinessSegmentsAxis",
                                "jpm:CommercialAndInvestmentBankMember")
        net = facts[("net_income", "2026-Q1", "Commercial And Investment Bank")]
        assert "scope axis: business_segment" in net.validation_notes
        assert "scope axis: group" in facts[("revenue", "2026-Q1", "Group")].validation_notes

    def test_a_region_and_a_segment_of_the_same_words_never_share_a_key(self) -> None:
        from app.services.sources.fact_scope import parse_scope

        business = self._facts_for("us-gaap:StatementBusinessSegmentsAxis",
                                   "acme:AmericasMember")
        region = self._facts_for("srt:StatementGeographicalAxis", "srt:AmericasMember")
        b = business[("revenue", "2026-Q1", "Americas")]
        r = region[("revenue", "2026-Q1", "Americas (geographic)")]
        assert parse_scope(b.scope).scope_key != parse_scope(r.scope).scope_key

    @pytest.mark.parametrize(
        ("axis", "member"),
        [
            ("srt:StatementGeographicalAxis", "jpm:TotalInternationalMember"),
            ("srt:StatementGeographicalAxis", "acme:AllOtherCountriesMember"),
            ("srt:StatementGeographicalAxis", "acme:OtherCountriesMember"),
            ("srt:ProductOrServiceAxis", "acme:ConsolidatedProductsMember"),
            ("us-gaap:StatementBusinessSegmentsAxis", "acme:TotalSegmentsMember"),
            ("us-gaap:StatementBusinessSegmentsAxis", "acme:OthersMember"),
            ("ifrs-full:SegmentsAxis", "acme:EuropeSubtotalMember"),
        ],
    )
    def test_a_subtotal_member_is_skipped_on_every_axis(self, axis: str, member: str) -> None:
        assert {k[2] for k in self._facts_for(axis, member)} == {"Group"}

    def test_a_name_that_only_starts_like_a_subtotal_is_kept(self) -> None:
        facts = self._facts_for("us-gaap:StatementBusinessSegmentsAxis",
                                "acme:AlliancePharmaMember")
        assert ("revenue", "2026-Q1", "Alliance Pharma") in facts


class TestBankStatus:
    """B2 — a bank is a filer that reports Group deposits, never one bank-ish line."""

    def _state(self, with_interest: bool, with_deposits: bool = False) -> dict:
        from app.services.pipeline.issuer_financials import build_financial_statements_state

        body = (_nf("us-gaap:CashAndCashEquivalentsAtCarryingValue", "bs", "240,000")
                + _nf("us-gaap:NetCashProvidedByUsedInOperatingActivities", "q", "60,000",
                      sign="-")
                + _nf("us-gaap:PaymentsToAcquirePropertyPlantAndEquipment", "q", "2,000")
                + _nf("us-gaap:ProvisionForLoanLossesExpensed", "q", "300")
                + _nf("us-gaap:FinancingReceivableAllowanceForCreditLosses", "bs", "900"))
        if with_interest:  # Disney's -1,037m "Interest expense, net"; a biotech's income
            body += _nf("us-gaap:InterestIncomeExpenseNet", "q", "2,100")
        if with_deposits:
            body += _nf("us-gaap:Deposits", "bs", "2,000,000")
        facts = statement_facts_from_inline_xbrl(parse_inline_xbrl(_q1_doc(body)))
        return build_financial_statements_state(
            [_as_loaded(f, i) for i, f in enumerate(facts)],
            now=datetime(2026, 5, 10, tzinfo=timezone.utc),
        )

    def test_interest_income_net_of_a_non_bank_is_not_net_interest_income(self) -> None:
        state = self._state(with_interest=True)
        assert "net_interest_income_current_period" not in state["slots"]
        runway = [d for d in state["derived"] if d["basis"] == "current_period"]
        assert runway and runway[0]["status"] == "computed"
        assert not any("bank" in str(d.get("detail")) for d in state["derived"])

    def test_a_provision_or_allowance_alone_never_makes_a_bank(self) -> None:
        # Caterpillar-style financing arm: provision + allowance, no deposits.
        state = self._state(with_interest=False)
        assert "provision_for_credit_losses_current_period" in state["slots"]
        assert all(d["status"] != "not_meaningful" for d in state["derived"])

    def test_group_deposits_make_a_bank_and_its_interest_line_is_read(self) -> None:
        state = self._state(with_interest=True, with_deposits=True)
        nii = state["slots"]["net_interest_income_current_period"]
        assert nii["taxonomy_label"] == "Interest income (expense), net"
        assert all(d["status"] == "not_meaningful" for d in state["derived"])
        assert "deposit-taking bank" in state["derived"][0]["detail"]

    def test_bank_revenue_and_unmapped_concepts(self) -> None:
        from app.integrations.xbrl_statement_concepts import (
            STATEMENT_CONCEPT_BY_QNAME,
            taxonomy_label,
        )

        assert taxonomy_label("us-gaap:RevenuesNetOfInterestExpense") == (
            "Revenues, net of interest expense")
        assert STATEMENT_CONCEPT_BY_QNAME["us-gaap:RevenuesNetOfInterestExpense"] \
            .statement_field == "revenue"
        # No standard CET1 concept is tagged by the sampled banks; extensions are never
        # mapped; NII after provisions is not NII.
        for absent in ("us-gaap:CommonEquityTierOneCapitalToRiskWeightedAssets",
                       "us-gaap:CommonEquityTierOneCapitalRatio",
                       "jpm:CommonEquityTier1CapitaltoRiskWeightedAssets",
                       "us-gaap:InterestIncomeExpenseAfterProvisionForLoanLoss"):
            assert absent not in STATEMENT_CONCEPT_BY_QNAME
        assert all(c.taxonomy in ("us-gaap", "ifrs-full")
                   for c in STATEMENT_CONCEPT_BY_QNAME.values())


class TestFrameChecks:
    """M1 — the dei focus is checked against its own context; M2 — no future actuals."""

    _H1 = [_context("h1", start="2026-01-01", end="2026-06-30"),
           _context("bs", instant="2026-06-30"), _context("bspy", instant="2025-12-31")]
    _H1_BODY = (_nf("ifrs-full:Revenue", "h1", "27,380")
                + _nf("ifrs-full:CashAndCashEquivalents", "bs", "59,809")
                + _nf("ifrs-full:CashAndCashEquivalents", "bspy", "143,251"))

    def test_an_h1_6k_tagged_fy_produces_nothing(self) -> None:
        data = _doc(self._H1, _frame("h1", "2026", "FY", "2026-06-30"), self._H1_BODY)
        assert statement_facts_from_inline_xbrl(parse_inline_xbrl(data)) == []

    def test_the_same_6k_with_a_matching_focus_is_read(self) -> None:
        data = _doc(self._H1, _frame("h1", "2026", "Q2", "2026-06-30"), self._H1_BODY)
        facts = _facts(data)
        assert facts[("revenue", "2026-H1", "Group")].value_numeric == 27_380
        assert facts[("cash_and_equivalents", "2025", "Group")].value_numeric == 143_251

    def test_a_stale_fiscal_year_focus_produces_nothing(self) -> None:
        contexts = [_context("q", start="2026-01-01", end="2026-03-31"),
                    _context("qpy", start="2025-01-01", end="2025-03-31")]
        body = _nf("us-gaap:Revenues", "q", "8,000") + _nf("us-gaap:Revenues", "qpy", "7,000")
        data = _doc(contexts, _frame("q", "2025", "Q1", "2026-03-31"), body)
        assert statement_facts_from_inline_xbrl(parse_inline_xbrl(data)) == []

    def test_a_frame_context_that_is_an_instant_produces_nothing(self) -> None:
        contexts = [_context("bs", instant="2026-03-31"),
                    _context("q", start="2026-01-01", end="2026-03-31")]
        data = _doc(contexts, _frame("bs", "2026", "Q1", "2026-03-31"),
                    _nf("us-gaap:Revenues", "q", "8,000"))
        assert statement_facts_from_inline_xbrl(parse_inline_xbrl(data)) == []

    def test_a_retail_year_ending_in_january_keeps_its_own_year_name(self) -> None:
        from app.services.sources.inline_xbrl_facts import frame_is_consistent

        contexts = [_context("fy", start="2025-02-02", end="2026-01-31")]
        for name in ("2025", "2026"):
            data = _doc(contexts, _frame("fy", name, "FY", "2026-01-31"),
                        _nf("us-gaap:Revenues", "fy", "100"))
            assert frame_is_consistent(parse_inline_xbrl(data))

    def test_a_context_after_the_document_period_is_not_an_actual(self) -> None:
        extra = [_context("fut", instant="2026-09-30"),
                 _context("futq", start="2026-04-01", end="2026-06-30")]
        body = (_nf("us-gaap:CashAndCashEquivalentsAtCarryingValue", "bs", "500")
                + _nf("us-gaap:Liabilities", "fut", "900")
                + _nf("us-gaap:Revenues", "futq", "1,100"))
        facts = _facts(_q1_doc(body, extra))
        assert set(facts) == {("cash_and_equivalents", "2026-Q1", "Group")}


class TestIfrsCurrencyAndEntity:
    def _switcher(self, *, usd_tie: bool = False) -> dict:
        def annual(years: range, per_year: int, unit_val: int) -> list[dict]:
            out = []
            for fy in years:
                for y in range(fy - per_year + 1, fy + 1):
                    out.append({"start": f"{y}-01-01", "end": f"{y}-12-31",
                                "val": unit_val * y, "accn": f"0000000000-{fy}",
                                "fy": fy, "fp": "FY", "form": "20-F", "filed": f"{fy + 1}-03-15"})
            return out

        eur = annual(range(2012, 2022), 3, 1000)
        usd = annual(range(2022, 2026), 3, 1100)
        if usd_tie:  # the latest 20-F also carries a EUR convenience translation
            eur += [dict(e, val=e["val"] * 0.9) for e in usd if e["fy"] == 2025]
        return {"cik": 1234567, "entityName": "Switcher SA", "facts": {
            "ifrs-full": {"Revenue": {"units": {"EUR": eur, "USD": usd}},
                          "ProfitLoss": {"units": {"EUR": eur, "USD": usd}}},
            "dei": {}}}

    def test_the_currency_is_the_latest_annual_reports_not_the_historical_majority(
        self,
    ) -> None:
        n = normalize_company_facts(self._switcher(), "SWCH", "1234567")
        assert n.reporting_currency == "USD" and n.fiscal_year == 2025

    def test_two_currencies_inside_the_latest_annual_report_are_no_answer(self) -> None:
        n = normalize_company_facts(self._switcher(usd_tie=True), "SWCH", "1234567")
        assert n.revenue is None
        assert any("no single reporting currency" in w for w in n.warnings)

    def test_a_document_naming_another_filer_produces_nothing(self) -> None:
        def with_cik(cik: str) -> bytes:
            return _ifrs_half_year_6k().replace(
                b"</ix:hidden>",
                b'<ix:nonNumeric name="dei:EntityCentralIndexKey" contextRef="h1">'
                + cik.encode() + b"</ix:nonNumeric></ix:hidden>")

        assert with_inline_xbrl_facts([], with_cik("0000999999"), source_url=SEC_URL) == []
        assert with_inline_xbrl_facts([], with_cik("0001627281"), source_url=SEC_URL)


class TestOneEquityDefinition:
    def test_equity_including_nci_never_fills_total_equity(self) -> None:
        # A statement of equity tags the incl.-NCI balance at the opening date only.
        extra = [_context("open", instant="2025-12-31")]
        nci = "us-gaap:StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"
        body = (_nf("us-gaap:StockholdersEquity", "bs", "19,483")
                + _nf(nci, "open", "20,600"))
        facts = _facts(_q1_doc(body, extra))
        assert facts[("total_equity", "2026-Q1", "Group")].value_numeric == 19_483
        assert ("total_equity", "2025", "Group") not in facts

    def test_ifrs_parent_equity_is_not_a_fallback_for_equity(self) -> None:
        from app.integrations.xbrl_statement_concepts import (
            STATEMENT_CONCEPT_BY_QNAME,
            aliases_for,
        )

        parent = STATEMENT_CONCEPT_BY_QNAME["ifrs-full:EquityAttributableToOwnersOfParent"]
        assert parent.statement_field is None and parent.normalizer_field is None
        assert aliases_for("ifrs-full", "shareholders_equity") == ["Equity"]

    def test_two_tagged_measures_are_named_as_such_not_as_two_statements(self) -> None:
        from app.services.pipeline.issuer_financials import _conflict_note

        group = [
            {"excerpt_id": "ixbrl:us-gaap:StockholdersEquity@c-1", "document_id": "a"},
            {"excerpt_id": "ixbrl:us-gaap:StockholdersEquityIncludingPortionAttributable"
                           "ToNoncontrollingInterest@c-9", "document_id": "b"},
        ]
        note = _conflict_note(group)
        assert note.startswith("Different measures fill this field")
        assert "state this figure differently" not in note
        # Two text readings from two documents keep the original wording.
        assert _conflict_note([{"excerpt_id": "p1:t1", "document_id": "a"},
                               {"excerpt_id": "p2:t1", "document_id": "b"}]).startswith(
            "The issuer's documents state this figure differently")


class TestLabelsAndReuse:
    def test_the_label_is_called_the_taxonomys_never_the_issuers(self) -> None:
        from app.services.pipeline.issuer_financials import _datapoint

        dp = _datapoint({"field": "net_income", "numeric_value": -41.0, "value": "-41",
                         "excerpt_id": "ixbrl:ifrs-full:ProfitLoss@h1", "period": "2026-H1",
                         "confidence": "high", "source_url": SEC_URL})
        assert dp["taxonomy_label"] == "Profit (loss)"
        assert "issuer_label" not in dp

    def test_regeneration_never_restamps_a_pre_reader_filing_without_its_tags(self) -> None:
        from app.services.extracted_document_service import _requires_full_reextraction

        url = ("https://www.sec.gov/Archives/edgar/data/1633917/000163391726000123/"
               "pypl-20260930.htm")
        doc = SimpleNamespace(pipeline_version=20, excerpts_json=[{"excerpt_id": "e1"}],
                              canonical_url=url)
        prose = SimpleNamespace(table_location="e1", extraction_method="html")
        assert _requires_full_reextraction(doc, []) is True
        assert _requires_full_reextraction(doc, [prose]) is True
        # Read at the reader's version, or not an SEC filing: excerpts-only reuse is fine.
        assert _requires_full_reextraction(
            SimpleNamespace(pipeline_version=22, excerpts_json=doc.excerpts_json,
                            canonical_url=url), [prose]) is False
        assert _requires_full_reextraction(
            SimpleNamespace(pipeline_version=20, excerpts_json=doc.excerpts_json,
                            canonical_url="https://issuer.example/r.htm"), [prose]) is False

    def test_the_reader_ships_at_pipeline_version_22(self) -> None:
        # #323 (ADR-080) took 21; the reader ships at 22. A later version (23, ADR-087)
        # keeps the reader, so the current version never trails it; the re-read checks
        # are all ``>= INLINE_XBRL_MIN_PIPELINE_VERSION``.
        from app.services.sources.extraction_pipeline_version import (
            CURRENT_EXTRACTION_PIPELINE_VERSION,
            INLINE_XBRL_MIN_PIPELINE_VERSION,
        )

        assert INLINE_XBRL_MIN_PIPELINE_VERSION == 22
        assert CURRENT_EXTRACTION_PIPELINE_VERSION >= INLINE_XBRL_MIN_PIPELINE_VERSION


class TestHeldFilingRereadBounds:
    """F6 — the held document, once per pipeline version, at most once per run; a
    re-read that fails still answers from the held copy."""

    _held = TestHeldFilingReread._held
    _ready = TestHeldFilingReread._ready
    _CFG = SimpleNamespace(v3_filing_body_bridge_enabled=True, v3_corpus_enabled=True,
                           primary_document_ingestion_enabled=True,
                           report_citation_persistence_enabled=True)

    async def _run(self, session, company, acquire, **kw):  # noqa: ANN001, ANN003, ANN202
        from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

        return await ensure_filing_corpus_evidence(
            session, company_id=company.id, cik="1627281",
            accession="0001193125-26-389336", form="6-K/A", cfg=self._CFG,
            acquire=acquire, **kw,
        )

    @staticmethod
    def _new_run(session) -> None:  # noqa: ANN001
        session.info.pop("adr085_held_filing_rereads", None)

    async def test_the_held_document_is_the_one_re_read(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.services.corpus.filing_evidence import AcquireOutcome

        company, doc = await self._held(session, pipeline_version=20)
        self._ready(monkeypatch, doc.id)
        calls: list[dict] = []

        async def _acquire(_s, **kw):  # noqa: ANN001, ANN003, ANN202
            calls.append(kw)
            return AcquireOutcome(acquired=True, fetched=True)

        await self._run(session, company, _acquire)  # the caller names no document
        assert calls[0]["document_name"] == "clls-ex99_1.htm"

    async def test_a_failed_re_read_is_not_repeated_in_a_later_run(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.services.corpus.filing_evidence import AcquireOutcome

        company, doc = await self._held(session, pipeline_version=20)
        self._ready(monkeypatch, doc.id)
        calls: list[dict] = []

        async def _acquire(_s, **kw):  # noqa: ANN001, ANN003, ANN202
            calls.append(kw)  # fetched, and definitively unreadable
            return AcquireOutcome(acquired=False, fetched=True, reason="extraction_failed",
                                  failure_codes=("empty_extraction",))

        first = await self._run(session, company, _acquire)
        assert first.state == "ready" and len(calls) == 1
        self._new_run(session)
        second = await self._run(session, company, _acquire)
        assert second.reused is True and second.attempted is False and len(calls) == 1

    @pytest.mark.parametrize(
        "codes", [("fetch_timeout",), ("http_server_error",), ("http_client_error",),
                  ("unknown",), ()],
    )
    async def test_a_transient_failure_is_retried_in_a_later_run(
        self, session, monkeypatch, codes  # noqa: ANN001
    ) -> None:
        from app.services.corpus.filing_evidence import AcquireOutcome

        company, doc = await self._held(session, pipeline_version=20)
        self._ready(monkeypatch, doc.id)
        calls: list[dict] = []

        async def _acquire(_s, **kw):  # noqa: ANN001, ANN003, ANN202
            calls.append(kw)
            return AcquireOutcome(acquired=False, fetched=True, reason="extraction_failed",
                                  failure_codes=codes)

        await self._run(session, company, _acquire)
        # Same run: the per-run bound holds.
        assert (await self._run(session, company, _acquire)).attempted is False
        self._new_run(session)
        await self._run(session, company, _acquire)
        assert len(calls) == 2  # no marker was written, so the next run retried

    async def test_a_fetch_error_writes_no_marker(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        from app.services.corpus.filing_evidence import AcquireOutcome

        company, doc = await self._held(session, pipeline_version=20)
        self._ready(monkeypatch, doc.id)
        calls: list[dict] = []

        async def _acquire(_s, **kw):  # noqa: ANN001, ANN003, ANN202
            calls.append(kw)
            return AcquireOutcome(acquired=False, reason="fetch_error", detail="ConnectError")

        await self._run(session, company, _acquire)
        self._new_run(session)
        await self._run(session, company, _acquire)
        assert len(calls) == 2

    def test_which_outcomes_are_definitive(self) -> None:
        from app.services.corpus.filing_evidence import AcquireOutcome
        from app.services.corpus.held_filing_facts import is_definitive_outcome

        assert is_definitive_outcome(AcquireOutcome(acquired=True, fetched=True))
        assert is_definitive_outcome(AcquireOutcome(
            acquired=False, fetched=True, reason="extraction_failed",
            failure_codes=("unsupported_content_type",)))
        assert not is_definitive_outcome(AcquireOutcome(
            acquired=False, fetched=True, reason="extraction_failed",
            failure_codes=("empty_extraction", "fetch_timeout")))
        assert not is_definitive_outcome(AcquireOutcome(acquired=False,
                                                        reason="no_document_resolved"))
        assert not is_definitive_outcome(None)

    async def test_at_most_one_re_read_per_run(self, session) -> None:  # noqa: ANN001
        from app.models.extracted_document import ExtractedDocument
        from app.services.corpus.held_filing_facts import plan_held_filing_reread

        company, doc = await self._held(session, pipeline_version=20)
        other = ExtractedDocument(
            id=uuid.uuid4(), company_id=company.id,
            canonical_url=SEC_URL.replace("clls-ex99_1", "clls-20f"),
            content_hash=uuid.uuid4().hex * 2, source_type="html", provider="sec_edgar",
            source_tier="T1_primary_filing", title="20-F", mime_type="text/html",
            status="extracted", extraction_method="html",
            retrieved_at=datetime(2026, 9, 12, tzinfo=timezone.utc), pipeline_version=20,
        )
        session.add(other)
        await session.flush()
        assert await plan_held_filing_reread(
            session, company_id=company.id, extracted_document_id=doc.id, form="6-K")
        assert await plan_held_filing_reread(
            session, company_id=company.id, extracted_document_id=other.id,
            form="20-F") is None
        self._new_run(session)
        assert await plan_held_filing_reread(
            session, company_id=company.id, extracted_document_id=other.id, form="20-F")

    async def test_a_re_read_that_raises_still_answers_from_the_held_copy(
        self, session, monkeypatch  # noqa: ANN001
    ) -> None:
        company, doc = await self._held(session, pipeline_version=20)
        self._ready(monkeypatch, doc.id)

        async def _boom(*_a, **_k):  # noqa: ANN002, ANN003, ANN202
            raise RuntimeError("network")

        out = await self._run(session, company, _boom)
        assert out.state == "ready" and out.reused is True and out.attempted is True
        assert any("re-read failed" in n for n in out.notes)
        # A raised error is not definitive: no marker, so a later run retries.
        self._new_run(session)
        again = await self._run(session, company, _boom)
        assert again.state == "ready" and again.attempted is True
