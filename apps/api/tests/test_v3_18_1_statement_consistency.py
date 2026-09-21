"""V3.18.1 — statement lines are checked against each other, not only against a source.

The live SCCO report put a gross profit of 2,914.8 beside an operating income of 7,001.7
under one FY2025 label, and nothing noticed: every guard the platform had compared a
figure with its SOURCE, none with its neighbours. The cause (a period defect) is closed
in ``test_v3_18_1_own_period_rule``. These tests pin the control that catches the
SYMPTOM, whatever the next cause turns out to be.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
from app.services.statement_consistency import (
    SEVERITY_CONTRADICTION,
    SEVERITY_IMPLAUSIBLE,
    StatementFigure,
    check_statement_consistency,
    find_conflicting_duplicates,
    same_reporting_period,
)

FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_discontinued_concept.json"
)
END = "2025-12-31"


def _fig(metric: str, value: float, end: str = END, **kw) -> StatementFigure:
    return StatementFigure(metric=metric, value=value, period_end=end, **kw)


class TestTheProductionPair:
    def test_gross_profit_below_operating_income_is_surfaced(self) -> None:
        report = check_statement_consistency(
            [_fig("revenue", 13420.0), _fig("gross_profit", 2914.8), _fig("operating_income", 7001.7)]
        )
        codes = {i.code: i for i in report.inconsistencies}
        found = codes["operating_income_exceeds_gross_profit"]
        assert found.severity == SEVERITY_IMPLAUSIBLE, (
            "not a contradiction: other operating income can legitimately do this, and "
            "a rule with no exceptions would suppress correct figures"
        )
        assert "gross_margin" in report.withheld_derived

    def test_a_normal_income_statement_is_clean(self) -> None:
        report = check_statement_consistency(
            [_fig("revenue", 13420.0), _fig("gross_profit", 8060.8), _fig("operating_income", 7001.7)]
        )
        assert report.is_clean and report.checks_run >= 3

    def test_printing_precision_is_not_a_finding(self) -> None:
        report = check_statement_consistency(
            [_fig("revenue", 1000.0), _fig("gross_profit", 1000.4)]
        )
        assert report.is_clean


class TestContradictions:
    def test_gross_profit_above_revenue(self) -> None:
        report = check_statement_consistency([_fig("revenue", 100.0), _fig("gross_profit", 180.0)])
        assert report.has_contradiction
        assert report.inconsistencies[0].severity == SEVERITY_CONTRADICTION

    def test_cash_above_total_assets(self) -> None:
        report = check_statement_consistency(
            [_fig("cash_and_equivalents", 500.0), _fig("total_assets", 300.0)]
        )
        assert [i.code for i in report.inconsistencies] == ["cash_exceeds_total_assets"]

    def test_a_balance_sheet_mixing_two_years(self) -> None:
        report = check_statement_consistency(
            [
                _fig("total_assets", 21000.0),
                _fig("total_liabilities", 4000.0),
                _fig("shareholders_equity", 5000.0),
            ]
        )
        assert "balance_sheet_does_not_balance" in {i.code for i in report.inconsistencies}
        assert {"debt_to_equity", "return_on_equity"} <= report.withheld_derived

    def test_non_controlling_interests_do_not_trip_the_identity(self) -> None:
        report = check_statement_consistency(
            [
                _fig("total_assets", 21381.0),
                _fig("total_liabilities", 10270.0),
                _fig("shareholders_equity", 11038.0),
            ]
        )
        assert report.is_clean


class TestFiguresThatMayNotBeCompared:
    def test_two_periods_are_reported_not_compared(self) -> None:
        """This module is not a period check. A cross-period pair is `not_comparable`,
        and saying so is what separates 'nothing found' from 'nothing checked'."""
        report = check_statement_consistency(
            [_fig("revenue", 100.0), _fig("gross_profit", 180.0, end="2019-12-31")]
        )
        assert report.is_clean
        assert report.checks_run == 0
        assert report.not_comparable and "2019-12-31" in report.not_comparable[0]

    def test_a_segment_is_not_compared_with_the_group(self) -> None:
        report = check_statement_consistency(
            [_fig("revenue", 100.0, scope="segment:watches"), _fig("gross_profit", 180.0)]
        )
        assert report.is_clean and report.not_comparable

    def test_two_currencies_are_not_compared(self) -> None:
        report = check_statement_consistency(
            [_fig("revenue", 100.0, currency="USD"), _fig("gross_profit", 180.0, currency="DKK")]
        )
        assert report.is_clean and report.not_comparable


class TestConflictingDuplicates:
    def test_two_channels_disagreeing_is_a_contradiction_and_neither_wins(self) -> None:
        found = find_conflicting_duplicates(
            [
                _fig("revenue", 13420.0, source="sec_edgar_xbrl"),
                _fig("revenue", 11433.4, source="issuer_document"),
            ]
        )
        assert len(found) == 1 and found[0].severity == SEVERITY_CONTRADICTION
        assert "13,420.0" in found[0].message and "11,433.4" in found[0].message

    def test_two_channels_agreeing_is_corroboration(self) -> None:
        assert not find_conflicting_duplicates(
            [_fig("revenue", 13420.0, source="a"), _fig("revenue", 13420.04, source="b")]
        )

    def test_the_same_metric_in_two_periods_is_not_a_duplicate(self) -> None:
        assert not find_conflicting_duplicates(
            [_fig("revenue", 13420.0), _fig("revenue", 11433.4, end="2024-12-31")]
        )


class TestSameReportingPeriod:
    @pytest.mark.parametrize(
        ("a", "b", "expected"),
        [
            ("2025-12-31", "2025-12-31", True),
            ("2025-12-31", "2025-12-28", True),  # a 52/53-week filer
            ("2025-12-31", "2024-12-31", False),
            ("2025-12-31", "2025-09-30", False),
            ("2025-12-31", None, True),  # unknown is not a mismatch
            ("not-a-date", "2025-12-31", True),
        ],
    )
    def test_cases(self, a, b, expected) -> None:
        assert same_reporting_period(a, b) is expected


class TestTheNormalizerRunsIt:
    """Producer → consumer: the check has to change what the bundle ships."""

    def test_a_same_period_implausible_pair_loses_its_margin(self) -> None:
        facts = json.loads(FIXTURE.read_text())
        facts["facts"]["us-gaap"]["GrossProfit"]["units"]["USD"].append(
            {
                "start": "2025-01-01", "end": "2025-12-31", "val": 2914800000,
                "fy": 2025, "fp": "FY", "form": "10-K", "filed": "2026-02-27",
            }
        )
        n = normalize_company_facts(facts, "ANY", "1")
        assert n.gross_profit == 2914.8, "reported, so kept — not this function's to delete"
        assert n.gross_margin is None, "a ratio built on a doubted pair carries the doubt unseen"
        codes = {i["code"] for i in n.consistency["inconsistencies"]}
        assert "operating_income_exceeds_gross_profit" in codes
        assert any("statement consistency" in w for w in n.warnings)
        names = {dp.field_name for dp in n.to_datapoints()}
        assert "sec_edgar.statement_consistency" in names
        assert "sec_edgar.gross_margin" not in names

    def test_a_clean_bundle_carries_no_consistency_record(self) -> None:
        n = normalize_company_facts(json.loads(FIXTURE.read_text()), "SCCO", "1001838")
        assert n.consistency == {}
        assert "sec_edgar.statement_consistency" not in {
            dp.field_name for dp in n.to_datapoints()
        }
