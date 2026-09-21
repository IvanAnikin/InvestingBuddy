"""V3.18.1 — a fact's own period is never replaced by the bundle's headline period.

WHAT WENT WRONG IN PRODUCTION
=============================
The live SCCO report (``cdfc7781``, 2026-09-20) presented, as FY2025:

    revenue            13,420.0
    gross profit        2,914.8
    operating income    7,001.7

A gross profit below operating income for one scope and period is impossible, and the
reason is that it was not one period. Southern Copper's FY2025 statement of earnings
**has no gross-profit line** (10-K accession 0001104659-26-021492). The filer last tagged
``us-gaap:GrossProfit`` for **FY2019**, and ``_select_metric`` picks the freshest entry
*within a concept* — so a concept the filer stopped using wins with its last year for
ever. The same run reported total debt of 7,250.5, which added a current-debt figure
from the PRIOR year-end (499.8 at 2024-12-31) to FY2025 long-term debt (6,750.7).

This is the third instance of one class — Moderna's FY2022 revenue shipped as FY2025 was
the first — and V3.14's fix ("freshness beats alias order") only helps when a newer
ALIAS exists. The general rule is the one asserted here.

The fixture is cut from SCCO's real SEC companyfacts payload (retrieved 2026-09-21),
reduced to recent entries per concept. Nothing about the defect is synthetic, and no
assertion names the issuer in production code: the rule is about periods.
"""

from __future__ import annotations

import copy
import json
import pathlib

import pytest

from app.integrations import sec_fundamentals_normalizer as normalizer
from app.integrations.providers.sec_edgar_fundamentals import (
    merge_fundamentals,
    parse_company_facts,
)
from app.integrations.sec_fundamentals_normalizer import normalize_company_facts

FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_discontinued_concept.json"
)

#: FY2025, USD millions — read off the filed statement of earnings, not off the API.
FY2025 = {
    "revenue": 13420.0,
    "operating_income": 7001.7,
    "net_income": 4348.2,
    "operating_cash_flow": 4752.1,
    "capital_expenditures": 1325.3,
}
STALE_FY2019_GROSS_PROFIT = 2914.8
PRIOR_YEAR_END_CURRENT_DEBT = 499.8
FY2025_LONG_TERM_DEBT = 6750.7


@pytest.fixture
def facts() -> dict:
    return json.loads(FIXTURE.read_text())


class TestTheFiledStatementIsWhatIsReported:
    def test_every_current_period_figure_matches_the_filing(self, facts) -> None:
        n = normalize_company_facts(facts, "SCCO", "1001838")
        assert n.fiscal_year == 2025
        for name, expected in FY2025.items():
            assert getattr(n, name) == expected, name
        assert n.free_cash_flow == pytest.approx(3426.8)
        assert n.operating_margin == pytest.approx(52.17)

    def test_a_concept_the_filer_stopped_tagging_is_missing_not_stale(self, facts) -> None:
        n = normalize_company_facts(facts, "SCCO", "1001838")
        assert n.gross_profit is None
        assert n.gross_margin is None, (
            "a margin dividing FY2019 profit by FY2025 revenue is not a margin"
        )
        assert "gross_profit" not in n.field_periods
        withheld = n.withheld_fields["gross_profit"]
        assert withheld["value"] == STALE_FY2019_GROSS_PROFIT
        assert withheld["end"] == "2019-12-31"
        assert withheld["reason"] == "stale_period"

    def test_a_prior_year_end_balance_is_not_added_to_this_years_debt(self, facts) -> None:
        """The comparative column of the newest 10-K carries the NEWEST filing year, so
        ranking on filing year alone reads last year's balance as this year's."""
        n = normalize_company_facts(facts, "SCCO", "1001838")
        assert n.short_term_debt is None
        assert n.withheld_fields["short_term_debt"]["value"] == PRIOR_YEAR_END_CURRENT_DEBT
        assert n.withheld_fields["short_term_debt"]["end"] == "2024-12-31"
        assert n.total_debt == FY2025_LONG_TERM_DEBT
        assert n.total_debt != pytest.approx(7250.5), "the figure the live report showed"

    def test_the_withholding_is_said_out_loud(self, facts) -> None:
        n = normalize_company_facts(facts, "SCCO", "1001838")
        warning = next(w for w in n.warnings if "NOT REPORTED FOR THE CURRENT PERIOD" in w)
        assert "gross_profit" in warning and "2019-12-31" in warning

    def test_no_datapoint_is_emitted_for_a_withheld_field(self, facts) -> None:
        n = normalize_company_facts(facts, "SCCO", "1001838")
        names = {dp.field_name for dp in n.to_datapoints()}
        assert "sec_edgar.gross_profit" not in names
        assert "sec_edgar.gross_margin" not in names
        assert "sec_edgar.revenue" in names


class TestTheLegacyParserCannotHandTheStaleFigureBack:
    def test_a_withheld_field_is_not_backfilled_by_alias_order(self, facts) -> None:
        """`merge_fundamentals` backfills any field the normalizer did not emit with
        the legacy period-blind value. For a field the normalizer REFUSED that would
        return the exact figure just refused."""
        base, _ = parse_company_facts(facts, "SCCO", "1001838")
        merged, warnings = merge_fundamentals(facts, "SCCO", "1001838", base)
        by_name = {dp.field_name: dp.value for dp in merged}
        assert "sec_edgar.gross_profit" not in by_name
        assert by_name.get("sec_edgar.short_term_debt") != PRIOR_YEAR_END_CURRENT_DEBT
        assert by_name["sec_edgar.revenue"] == FY2025["revenue"]


class TestTheRuleIsAboutPeriodsNotAboutThisIssuer:
    def test_a_fully_current_bundle_withholds_nothing(self, facts) -> None:
        data = copy.deepcopy(facts)
        del data["facts"]["us-gaap"]["GrossProfit"]
        del data["facts"]["us-gaap"]["LongTermDebtCurrent"]
        # Also a 2015 QUARTERLY borrowing, which the alias fallback would otherwise
        # surface as this year's short-term debt — the rule catches that too.
        del data["facts"]["us-gaap"]["ShortTermBorrowings"]
        n = normalize_company_facts(data, "ANY", "1")
        assert n.withheld_fields == {}
        assert not any("NOT REPORTED FOR THE CURRENT PERIOD" in w for w in n.warnings)

    def test_a_current_gross_profit_is_kept_and_its_margin_computed(self, facts) -> None:
        """The over-suppression direction: the rule must not cost a filer that DOES
        report gross profit its gross margin."""
        data = copy.deepcopy(facts)
        rows = data["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"]
        rows.append(
            {
                "start": "2025-01-01",
                "end": "2025-12-31",
                "val": 8060800000,
                "fy": 2025,
                "fp": "FY",
                "form": "10-K",
                "filed": "2026-02-27",
                "frame": "CY2025",
            }
        )
        n = normalize_company_facts(data, "ANY", "1")
        assert n.gross_profit == 8060.8
        assert n.gross_margin == pytest.approx(60.07)
        assert "gross_profit" not in n.withheld_fields

    def test_a_quarter_is_never_presented_inside_an_annual_bundle(self, facts) -> None:
        data = copy.deepcopy(facts)
        data["facts"]["us-gaap"]["PaymentsToAcquirePropertyPlantAndEquipment"]["units"]["USD"] = [
            {
                "start": "2026-04-01",
                "end": "2026-06-30",
                "val": 300000000,
                "fy": 2026,
                "fp": "Q2",
                "form": "10-Q",
                "filed": "2026-07-31",
            }
        ]
        n = normalize_company_facts(data, "ANY", "1")
        assert n.capital_expenditures is None
        assert n.free_cash_flow is None, "annual OCF minus one quarter's capex is not FCF"
        assert n.withheld_fields["capital_expenditures"]["reason"] == "interim_in_annual_bundle"

    def test_an_unreadable_period_end_is_left_alone(self) -> None:
        """Unknown is not stale. Withholding on an unreadable date would discard
        correct figures from an irregular payload."""
        metric = normalizer._Metric(value=1.0, end=None, period_type="annual")
        anchor = normalizer._Metric(value=2.0, end="2025-12-31", period_type="annual")
        outside, anchor_end = normalizer._metrics_outside_reporting_period(
            {"a": metric, "b": anchor}
        )
        assert outside == [] and anchor_end == "2025-12-31"


def _entry(end: str, val: float, *, start: str | None = None, fy: int = 2025, form: str = "10-K",
           fp: str = "FY", filed: str = "2026-02-27") -> dict:
    entry = {"end": end, "val": val, "fy": fy, "fp": fp, "form": form, "filed": filed}
    if start:
        entry["start"] = start
    return entry


class TestOneOddConceptCannotWithholdTheBundle:
    """Found by review. The first version anchored on the LATEST period end, so a single
    concept dated after the fiscal year end became the anchor and revenue, net income,
    cash flow and the whole balance sheet were withheld as 'stale' against it. A rule
    built to stop one wrong figure must not be able to remove every right one."""

    def test_an_instant_dated_after_year_end_is_the_outlier(self, facts) -> None:
        data = copy.deepcopy(facts)
        data["facts"]["us-gaap"]["ShortTermBorrowings"] = {
            "units": {"USD": [_entry("2026-02-10", 75_000_000)]}
        }
        del data["facts"]["us-gaap"]["LongTermDebtCurrent"]
        n = normalize_company_facts(data, "ANY", "1")
        assert n.reporting_period_end == "2025-12-31"
        for name, expected in FY2025.items():
            assert getattr(n, name) == expected, f"{name} was withheld by an outlier"
        assert n.fiscal_year == 2025 and n.period_basis == "annual"
        assert n.withheld_fields["short_term_debt"]["reason"] == "later_period_outlier"

    def test_a_flow_mis_tagged_into_the_next_quarter_is_the_outlier(self, facts) -> None:
        data = copy.deepcopy(facts)
        data["facts"]["us-gaap"]["PaymentsOfDividendsCommonStock"] = {
            "units": {"USD": [_entry("2026-03-31", 9e8, start="2025-04-01", fy=2026)]}
        }
        n = normalize_company_facts(data, "ANY", "1")
        assert n.revenue == FY2025["revenue"] and n.net_income == FY2025["net_income"]
        assert n.dividends_paid is None
        assert n.withheld_fields["dividends_paid"]["reason"] == "later_period_outlier"

    def test_a_tie_goes_to_the_later_period(self) -> None:
        a = normalizer._Metric(value=1.0, end="2024-12-31", period_type="annual")
        b = normalizer._Metric(value=2.0, end="2025-12-31", period_type="annual")
        outside, anchor_end = normalizer._metrics_outside_reporting_period({"a": a, "b": b})
        assert anchor_end == "2025-12-31"
        assert [(name, reason) for name, reason, _ in outside] == [("a", "stale_period")]

    def test_a_52_53_week_filer_is_one_period(self) -> None:
        metrics = {
            "revenue": normalizer._Metric(value=1.0, end="2025-12-28", period_type="annual"),
            "net_income": normalizer._Metric(value=1.0, end="2025-12-28", period_type="annual"),
            "total_assets": normalizer._Metric(value=1.0, end="2025-12-31", period_type="annual"),
        }
        outside, _ = normalizer._metrics_outside_reporting_period(metrics)
        assert outside == []


class TestAQuarterlyBundleIsOneDurationClass:
    def test_quarter_and_year_to_date_are_not_mixed(self) -> None:
        """A 10-Q tags every flow twice with ONE period end. Found by review: revenue
        came back as the quarter beside year-to-date net income."""
        def flows(quarter: float, ytd: float) -> dict:
            return {"units": {"USD": [
                _entry("2026-06-30", ytd, start="2026-01-01", fy=2026, form="10-Q", fp="Q2",
                       filed="2026-07-31"),
                _entry("2026-06-30", quarter, start="2026-04-01", fy=2026, form="10-Q", fp="Q2",
                       filed="2026-07-31"),
            ]}}
        data = {"cik": 1, "facts": {"dei": {}, "us-gaap": {
            "Revenues": flows(100e6, 190e6),
            "NetIncomeLoss": flows(10e6, 19e6),
        }}}
        n = normalize_company_facts(data, "ANY", "1")
        assert (n.revenue, n.net_income) == (100.0, 10.0)
        assert n.net_margin == pytest.approx(10.0)


class TestANegativeBaseHasNoReading:
    def test_return_on_negative_equity_is_not_printed(self, facts) -> None:
        data = copy.deepcopy(facts)
        for concept in ("StockholdersEquity",
                        "StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest"):
            for row in data["facts"]["us-gaap"].get(concept, {}).get("units", {}).get("USD", []):
                row["val"] = -abs(row["val"])
        n = normalize_company_facts(data, "ANY", "1")
        assert n.shareholders_equity is not None and n.shareholders_equity < 0
        assert n.return_on_equity is None and n.debt_to_equity is None
        assert any("equity is not positive" in w for w in n.warnings)


class TestMutation:
    """Reintroduce the defect; the guard above must notice."""

    def test_removing_the_rule_brings_the_stale_gross_profit_back(
        self, facts, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            normalizer, "_metrics_outside_reporting_period", lambda _s: ([], None)
        )
        n = normalize_company_facts(facts, "SCCO", "1001838")
        assert n.gross_profit == STALE_FY2019_GROSS_PROFIT
        assert n.gross_margin == pytest.approx(21.72), (
            "this is the figure that reached production; if this assertion ever fails "
            "the fixture no longer reproduces the defect and the tests above prove less "
            "than they appear to"
        )
        assert n.total_debt == pytest.approx(7250.5)
