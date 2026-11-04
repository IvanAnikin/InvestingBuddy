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
        assert n.issuer_labels["net_income"] == "Profit (loss)"
        assert n.issuer_labels["operating_income"] == "Operating profit (loss)"
        assert n.field_periods["net_income"]["concept"] == "ProfitLoss"
        notes = {dp.field_name: dp for dp in n.to_datapoints()}
        net = notes["sec_edgar.net_income"]
        assert "ifrs-full concept ProfitLoss" in net.note
        assert 'issuer term "Profit (loss)"' in net.note
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
        assert by_field["net_income"]["issuer_label"] == "Profit (loss)"
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
        assert n.issuer_labels == {}

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
        n = normalize_company_facts(self._with_borrowings(
            Borrowings=30_000_000, CurrentBorrowings=10_000_000,
            NoncurrentBorrowings=20_000_000), "CLLS")
        assert (n.total_debt, n.short_term_debt, n.long_term_debt) == (30.0, 10.0, 20.0)
        assert n.debt_basis["formula"] == "ifrs-full:Borrowings"
        assert n.issuer_labels["total_debt"] == "Borrowings"

    def test_one_leg_of_ifrs_borrowings_is_never_total_debt(self) -> None:
        n = normalize_company_facts(self._with_borrowings(NoncurrentBorrowings=20_000_000),
                                    "CLLS")
        assert n.total_debt is None and n.debt_to_equity is None
        assert "partial sum" in n.debt_basis["withheld_reason"]

    def test_a_total_that_disagrees_with_its_legs_is_withheld(self) -> None:
        n = normalize_company_facts(self._with_borrowings(
            Borrowings=40_000_000, CurrentBorrowings=10_000_000,
            NoncurrentBorrowings=20_000_000), "CLLS")
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
        assert "issuer term: Profit (loss)" in loss.validation_notes
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
        contexts = [_context("q", start="2026-04-01", end="2026-06-30")]
        hidden = _dei("q", "2026", "Q2", "10-Q")
        body = (_nf("us-gaap:Revenues", "q", "8,293", scale=6, decimals="-6")
                + _nf("us-gaap:Revenues", "q", "9,000", scale=6, decimals="-6"))
        assert ("revenue", "2026-Q2", "Group") not in _facts(_doc(contexts, hidden, body))

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
        cet1 = facts[("cet1_ratio", "2026-Q3")]
        assert cet1.value_numeric == pytest.approx(15.7) and cet1.unit == "percent"
        assert cet1.currency is None

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
        assert net["issuer_label"] == "Profit (loss)"
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
    from app.integrations.xbrl_statement_concepts import issuer_label

    assert issuer_label("ifrs-full:ProfitLoss") == "Profit (loss)"
    assert "EBIT" not in (issuer_label("ifrs-full:ProfitLossFromOperatingActivities") or "")
    assert issuer_label("ifrs-full:Borrowings") == "Borrowings"
