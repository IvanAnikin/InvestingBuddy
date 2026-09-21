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
        assert normalizer._metrics_outside_reporting_period({"a": metric, "b": anchor}) == []


class TestMutation:
    """Reintroduce the defect; the guard above must notice."""

    def test_removing_the_rule_brings_the_stale_gross_profit_back(
        self, facts, monkeypatch
    ) -> None:
        monkeypatch.setattr(normalizer, "_metrics_outside_reporting_period", lambda _s: [])
        n = normalize_company_facts(facts, "SCCO", "1001838")
        assert n.gross_profit == STALE_FY2019_GROSS_PROFIT
        assert n.gross_margin == pytest.approx(21.72), (
            "this is the figure that reached production; if this assertion ever fails "
            "the fixture no longer reproduces the defect and the tests above prove less "
            "than they appear to"
        )
        assert n.total_debt == pytest.approx(7250.5)
