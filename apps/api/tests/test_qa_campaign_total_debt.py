"""Cross-sector QA campaign 2026-11, defect JPM-1 — total debt from compatible concepts.

WHAT WENT WRONG IN PRODUCTION
=============================
A live report said "Total debt 64,776m" and called a debt/equity of 0.179
"conservative". 64,776 was the filer's ``ShortTermBorrowings`` alone: its last
``LongTermDebt`` was for 2013 (withheld, correctly, by the own-period rule) and its
current long-term concept, ``LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities``,
was unknown to the normalizer. The total was then built from whichever leg was there.

The same code summed any long-term alias with any current alias, and the taxonomy's
concepts overlap: ``LongTermDebt`` already contains the current maturities that
``LongTermDebtCurrent`` and ``DebtCurrent`` also contain. Replayed over 19 large filers'
real companyfacts, 7 totals were overstated that way (ADR-067).

Every fixture here is synthetic — invented values on the real US-GAAP concept names —
so each test isolates exactly one combination.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.integrations.providers.sec_edgar_fundamentals import (
    merge_fundamentals,
    parse_company_facts,
)
from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
from app.integrations.sec_total_debt import derive_total_debt
from app.services.agent_tools import peers
from app.services.calculations.statement_metrics import metrics_from_sec_summary

END = "2025-12-31"
PRIOR_END = "2024-12-31"
ACCN = "0000000001-26-000001"
M = 1_000_000


def _row(end: str = END, val: float = 0.0, *, form: str = "10-K", fy: int = 2025,
         filed: str = "2026-02-20", accn: str = ACCN, start: str | None = None) -> dict:
    row: dict[str, Any] = {"end": end, "val": val * M, "fy": fy, "fp": "FY", "form": form,
                           "filed": filed, "accn": accn}
    if start:
        row["start"] = start
    return row


def _facts(**debt: list[dict] | float) -> dict:
    """A small but complete FY2025 bundle plus the given debt concepts.

    A float is one balance at the reporting period in the reporting filing; a list is
    taken as the concept's rows verbatim.
    """
    year = {"start": "2025-01-01"}
    us_gaap: dict[str, Any] = {
        "Revenues": {"units": {"USD": [_row(val=1000.0, **year)]}},
        "NetIncomeLoss": {"units": {"USD": [_row(val=100.0, **year)]}},
        "NetCashProvidedByUsedInOperatingActivities": {
            "units": {"USD": [_row(val=150.0, **year)]}
        },
        "Assets": {"units": {"USD": [_row(val=5000.0)]}},
        "Liabilities": {"units": {"USD": [_row(val=3000.0)]}},
        "StockholdersEquity": {"units": {"USD": [_row(val=2000.0)]}},
        "CashAndCashEquivalentsAtCarryingValue": {"units": {"USD": [_row(val=300.0)]}},
    }
    for concept, value in debt.items():
        rows = value if isinstance(value, list) else [_row(val=float(value))]
        us_gaap[concept] = {"units": {"USD": rows}}
    return {"cik": 1, "facts": {"us-gaap": us_gaap}}


def _n(**debt: list[dict] | float):  # noqa: ANN202
    return normalize_company_facts(_facts(**debt), "ANY", "1")


def _legs_add_up(n) -> None:  # noqa: ANN001
    if n.short_term_debt is not None and n.long_term_debt is not None:
        assert n.short_term_debt + n.long_term_debt == pytest.approx(n.total_debt)


class TestEachCompatibleCombination:
    def test_a_including_current_plus_short_term_borrowings(self) -> None:
        n = _n(LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities=400.0,
               ShortTermBorrowings=60.0)
        assert n.total_debt == 460.0
        assert n.debt_basis["combination"] == "including_current+short_term_borrowings"
        # The current maturities are not tagged, so the total cannot be split into
        # due-within / due-after one year. It still stands: its basis is complete.
        assert n.short_term_debt is None and n.long_term_debt is None
        assert n.debt_to_equity == pytest.approx(0.23)

    def test_b_noncurrent_plus_debt_current(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, DebtCurrent=45.0)
        assert n.total_debt == 345.0
        assert (n.short_term_debt, n.long_term_debt) == (45.0, 300.0)
        assert n.debt_basis["combination"] == "noncurrent+debt_current"

    def test_c_noncurrent_plus_current_maturities_plus_short_term_borrowings(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               ShortTermBorrowings=15.0)
        assert n.total_debt == 345.0
        assert (n.short_term_debt, n.long_term_debt) == (45.0, 300.0)
        _legs_add_up(n)

    def test_the_lease_inclusive_concepts_pair_the_same_way(self) -> None:
        n = _n(LongTermDebtAndCapitalLeaseObligations=270.0,
               LongTermDebtAndCapitalLeaseObligationsCurrent=15.0,
               ShortTermBorrowings=60.0)
        assert n.total_debt == 345.0
        _legs_add_up(n)

    def test_commercial_paper_is_a_short_term_borrowing_only_without_the_total(self) -> None:
        alone = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
                   CommercialPaper=8.0)
        assert alone.total_debt == 338.0
        both = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
                  ShortTermBorrowings=15.0, CommercialPaper=8.0)
        assert both.total_debt == 345.0, "ShortTermBorrowings already contains the CP"


class TestCurrentMaturitiesAreCountedOnce:
    def test_long_term_debt_plus_its_own_current_portion(self) -> None:
        """The ordinary filer: LongTermDebt = Noncurrent + Current. The old code added
        LongTermDebt and LongTermDebtCurrent."""
        n = _n(LongTermDebt=330.0, LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0)
        assert n.total_debt == 330.0
        assert n.total_debt != 360.0
        assert (n.short_term_debt, n.long_term_debt) == (30.0, 300.0)

    def test_long_term_debt_plus_current_portion_without_a_noncurrent_tag(self) -> None:
        n = _n(LongTermDebt=330.0, LongTermDebtCurrent=30.0)
        assert n.total_debt == 330.0
        assert n.long_term_debt == 300.0, "derived: including current minus current"
        assert n.debt_basis["components"][0]["derived"] is True

    def test_long_term_debt_plus_debt_current(self) -> None:
        """DebtCurrent contains the current maturities LongTermDebt also contains."""
        n = _n(LongTermDebt=330.0, LongTermDebtNoncurrent=300.0,
               LongTermDebtCurrent=30.0, DebtCurrent=30.0)
        assert n.total_debt == 330.0
        assert n.debt_basis["combination"] == "noncurrent+debt_current"

    def test_debt_current_is_never_added_to_long_term_debt_including_current(self) -> None:
        n = _n(LongTermDebt=330.0, DebtCurrent=45.0)
        assert n.total_debt == 330.0
        assert n.total_debt != 375.0
        notes = " ".join(n.debt_basis["notes"])
        assert "not combined" in notes and "no short-term borrowings tagged" in notes

    def test_one_line_tagged_as_both_borrowings_and_current_maturities(self) -> None:
        """Two of 19 real filers tag their 'short-term debt' line with BOTH
        ShortTermBorrowings and a current-maturities concept, at one value."""
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=40.0,
               ShortTermBorrowings=40.0)
        assert n.total_debt == 340.0
        assert n.total_debt != 380.0
        assert any("tagged with both concepts" in note for note in n.debt_basis["notes"])

    def test_a_debt_current_smaller_than_its_own_component_is_not_used(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               ShortTermBorrowings=15.0, DebtCurrent=10.0)
        assert n.total_debt == 345.0
        assert n.debt_basis["combination"] == "noncurrent+current_ltd+short_term_borrowings"


class TestShortTermAbsenceNeedsANote:
    def test_no_short_term_borrowings_tagged_is_said(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0)
        assert n.total_debt == 330.0
        assert any("no short-term borrowings tagged for the period" in note
                   for note in n.debt_basis["notes"])
        assert any("total_debt basis" in w for w in n.warnings)

    def test_noncurrent_alone_says_no_current_debt_was_tagged(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0)
        assert n.total_debt == 300.0
        assert n.short_term_debt is None and n.long_term_debt == 300.0
        assert any("no current debt tagged" in note for note in n.debt_basis["notes"])

    def test_the_datapoint_note_names_the_concepts_and_the_gap(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0)
        dp = next(d for d in n.to_datapoints() if d.field_name == "sec_edgar.total_debt")
        assert "LongTermDebtNoncurrent" in dp.note and "LongTermDebtCurrent" in dp.note
        assert "no short-term borrowings tagged" in dp.note
        assert dp.as_of == END

    def test_a_complete_basis_carries_no_warning(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, DebtCurrent=45.0)
        assert not any("total_debt" in w for w in n.warnings)


class TestNoLongTermDebtMeansNoTotalDebt:
    """The JPM-1 shape: short-term borrowings current, long-term concept stale."""

    def _jpm_shape(self, **extra: list[dict] | float):  # noqa: ANN202
        return _n(
            LongTermDebt=[_row("2013-12-31", 260.0, fy=2013, filed="2014-02-20",
                               accn="0000000001-14-000001")],
            ShortTermBorrowings=65.0,
            **extra,
        )

    def test_short_term_borrowings_alone_are_not_total_debt(self) -> None:
        n = self._jpm_shape()
        assert n.total_debt is None, "the live report printed 64,776m here"
        assert n.debt_to_equity is None, "and called 0.179 conservative"
        assert n.short_term_debt is None and n.long_term_debt is None
        assert n.debt_basis == {}

    def test_the_stale_long_term_concept_is_withheld_and_said(self) -> None:
        n = self._jpm_shape()
        withheld = n.withheld_fields["long_term_debt"]
        assert (withheld["concept"], withheld["end"], withheld["reason"]) == (
            "LongTermDebt", "2013-12-31", "stale_period",
        )
        warning = next(w for w in n.warnings if "total_debt, debt_to_equity" in w)
        assert "no long-term debt concept is tagged for the reporting period" in warning
        assert "2013-12-31" in warning

    def test_no_debt_datapoint_is_emitted(self) -> None:
        names = {d.field_name for d in self._jpm_shape().to_datapoints()}
        assert not names & {"sec_edgar.total_debt", "sec_edgar.debt_to_equity",
                            "sec_edgar.short_term_debt", "sec_edgar.long_term_debt"}

    def test_a_filer_that_never_tagged_long_term_debt(self) -> None:
        n = _n(ShortTermBorrowings=65.0, DebtCurrent=65.0)
        assert n.total_debt is None and n.debt_to_equity is None
        assert "long_term_debt" not in n.withheld_fields
        assert any("no long-term debt concept is tagged" in w for w in n.warnings)

    def test_the_current_long_term_concept_restores_the_total(self) -> None:
        n = self._jpm_shape(
            LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities=435.0
        )
        assert n.total_debt == 500.0
        assert "long_term_debt" not in n.withheld_fields

    def test_the_legacy_parser_cannot_hand_a_leg_back(self) -> None:
        """`merge_fundamentals` used to backfill any leg the normalizer left empty with
        the period-blind alias-order value — here the 2013 LongTermDebt."""
        data = _facts(
            LongTermDebt=[_row("2013-12-31", 260.0, fy=2013, filed="2014-02-20",
                               accn="0000000001-14-000001")],
            ShortTermBorrowings=65.0,
        )
        base, _ = parse_company_facts(data, "ANY", "1")
        assert any(d.field_name == "sec_edgar.short_term_debt" for d in base)
        merged, _ = merge_fundamentals(data, "ANY", "1", base)
        names = {d.field_name for d in merged}
        assert not names & {"sec_edgar.total_debt", "sec_edgar.short_term_debt",
                            "sec_edgar.long_term_debt"}

    def test_net_debt_is_refused_downstream(self) -> None:
        items, _ = peers._peer_metrics("ANY", _facts(
            LongTermDebt=[_row("2013-12-31", 260.0, fy=2013, filed="2014-02-20",
                               accn="0000000001-14-000001")],
            ShortTermBorrowings=65.0,
        ))
        assert "net_debt" not in {i["metric_id"] for i in items}
        statement, _ = peers.statement_items("ANY", _facts(
            LongTermDebt=[_row("2013-12-31", 260.0, fy=2013, filed="2014-02-20",
                               accn="0000000001-14-000001")],
            ShortTermBorrowings=65.0,
        ))
        assert "total_debt" not in {i["metric_id"] for i in statement}


class TestOnePeriodOnly:
    def test_a_prior_year_end_current_portion_is_not_added(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0,
               LongTermDebtCurrent=[_row(PRIOR_END, 30.0)])
        assert n.total_debt == 300.0
        withheld = n.withheld_fields["short_term_debt"]
        assert (withheld["end"], withheld["reason"]) == (PRIOR_END, "stale_period")

    def test_a_stale_short_term_concept_is_named_in_the_note(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               ShortTermBorrowings=[_row("2021-12-31", 9.0, fy=2021, filed="2022-02-20",
                                         accn="0000000001-22-000001")])
        assert n.total_debt == 330.0
        assert any("last tagged as ShortTermBorrowings for the period ending 2021-12-31"
                   in note for note in n.debt_basis["notes"])

    def test_a_stale_long_term_alias_does_not_shadow_the_current_one(self) -> None:
        n = _n(LongTermDebt=[_row("2018-12-31", 999.0, fy=2018, filed="2019-02-20",
                                  accn="0000000001-19-000001")],
               LongTermDebtNoncurrent=300.0, DebtCurrent=45.0)
        assert n.total_debt == 345.0

    def test_a_quarterly_balance_is_not_combined_into_an_annual_bundle(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0,
               ShortTermBorrowings=[_row(val=20.0, form="10-Q", filed="2026-05-01",
                                         accn="0000000001-26-000009")])
        assert n.total_debt == 300.0

    def test_the_reporting_filing_wins_over_a_later_restatement_of_the_period(self) -> None:
        later = "0000000001-27-000001"
        debt = derive_total_debt(
            _facts(LongTermDebtNoncurrent=[_row(val=300.0),
                                           _row(val=310.0, fy=2026, filed="2027-02-20",
                                                accn=later)])["facts"]["us-gaap"],
            period_end=END, forms={"10-K"}, prefer_accn=ACCN, tolerance_days=10,
        )
        assert debt.total == 300.0


class TestTheLegsAreSelfConsistent:
    @pytest.mark.parametrize(
        "debt",
        [
            {"LongTermDebtNoncurrent": 300.0, "DebtCurrent": 45.0},
            {"LongTermDebtNoncurrent": 300.0, "LongTermDebtCurrent": 30.0,
             "ShortTermBorrowings": 15.0},
            {"LongTermDebt": 330.0, "LongTermDebtCurrent": 30.0, "ShortTermBorrowings": 15.0},
            {"LongTermDebt": 330.0, "LongTermDebtCurrent": 30.0, "DebtCurrent": 45.0},
            {"LongTermDebtNoncurrent": 300.0, "LongTermDebtCurrent": 30.0},
            {"LongTermDebtNoncurrent": 300.0, "ShortTermBorrowings": 15.0},
        ],
    )
    def test_short_plus_long_is_total(self, debt: dict[str, float]) -> None:
        n = _n(**debt)
        assert n.total_debt is not None
        assert n.long_term_debt is not None
        _legs_add_up(n)


class TestConsumersDoNotRebuildAPartialSum:
    def test_an_unsplit_total_still_gives_net_debt(self) -> None:
        items, _ = peers._peer_metrics("ANY", _facts(
            LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities=400.0,
            ShortTermBorrowings=60.0,
        ))
        by_id = {i["metric_id"]: i["value"] for i in items}
        assert by_id["net_debt"] == pytest.approx(160.0)  # 460 - 300 cash

    def test_a_persisted_summary_with_only_a_short_leg_gives_no_net_debt(self) -> None:
        """A snapshot written before this fix can still carry the JPM-1 shape."""
        fs = {
            "fiscal_year": 2025, "period_basis": "annual",
            "total_debt_usd_m": 64776.0, "short_term_debt_usd_m": 64776.0,
            "long_term_debt_usd_m": None, "cash_and_equivalents_usd_m": 100.0,
        }
        readings, _ = metrics_from_sec_summary(fs)
        assert "net_debt" not in {r.key for r in readings}

    def test_a_summary_with_a_complete_total_gives_net_debt(self) -> None:
        fs = {
            "fiscal_year": 2025, "period_basis": "annual",
            "total_debt_usd_m": 345.0, "short_term_debt_usd_m": 45.0,
            "long_term_debt_usd_m": 300.0, "cash_and_equivalents_usd_m": 100.0,
        }
        readings, _ = metrics_from_sec_summary(fs)
        assert {r.key: r.value for r in readings}["net_debt"] == pytest.approx(245.0)


DUE_12M = "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths"


def _ev(fs: dict) -> float | None:
    from app.integrations.market_metrics_enrichment import derive_market_metrics
    from tests.test_phase19_4_identity_sector_market_metrics import _build_prices

    mm = derive_market_metrics("ANY", fs, _build_prices(), "USD")
    assert mm.market_cap_mln is not None
    return mm.enterprise_value_mln


class TestAKnownPartialIsWithheld:
    """PR #307 review: a total without current maturities was published although the
    filer's own maturity schedule showed principal due within twelve months (one
    industrial: 7,120m, ~20% of its debt; one payments company: 1,397m)."""

    def test_noncurrent_plus_borrowings_with_principal_due_is_withheld(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, ShortTermBorrowings=55.0, **{DUE_12M: 70.0})
        assert n.total_debt is None and n.debt_to_equity is None
        assert n.short_term_debt is None, "borrowings alone are not 'due within a year'"
        assert n.long_term_debt == 300.0, "the noncurrent line is a reported figure"
        assert "principal due within twelve months" in n.debt_basis["withheld_reason"]
        assert any("total_debt, debt_to_equity and net debt withheld" in w
                   and "70.00m" in w for w in n.warnings)

    def test_noncurrent_alone_with_principal_due_is_withheld(self) -> None:
        n = _n(LongTermDebtNoncurrent=100.0, **{DUE_12M: 14.0})
        assert n.total_debt is None and n.debt_to_equity is None
        names = {d.field_name for d in n.to_datapoints()}
        assert "sec_edgar.total_debt" not in names and "sec_edgar.debt_to_equity" not in names

    def test_a_zero_or_absent_schedule_keeps_the_noted_total(self) -> None:
        assert _n(LongTermDebtNoncurrent=100.0, **{DUE_12M: 0.0}).total_debt == 100.0
        assert _n(LongTermDebtNoncurrent=100.0).total_debt == 100.0

    def test_a_basis_that_contains_current_maturities_ignores_the_schedule(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0, **{DUE_12M: 30.0})
        assert n.total_debt == 330.0

    def test_net_debt_and_enterprise_value_are_refused(self) -> None:
        facts = _facts(LongTermDebtNoncurrent=300.0, ShortTermBorrowings=55.0,
                       **{DUE_12M: 70.0})
        items, _ = peers._peer_metrics("ANY", facts)
        assert "net_debt" not in {i["metric_id"] for i in items}
        statement, _ = peers.statement_items("ANY", facts)
        assert "total_debt" not in {i["metric_id"] for i in statement}
        n = normalize_company_facts(facts, "ANY", "1")
        assert _ev({"total_debt_usd_m": n.total_debt,
                    "long_term_debt_usd_m": n.long_term_debt,
                    "cash_and_equivalents_usd_m": 300.0,
                    "shares_outstanding_mln": 10.0}) is None


class TestEnterpriseValueFollowsTheNetDebtRule:
    def test_a_total_beside_a_withheld_leg_gives_no_ev(self) -> None:
        fs: dict[str, Any] = {
            "total_debt_usd_m": 300.0, "long_term_debt_usd_m": 300.0,
            "cash_and_equivalents_usd_m": 50.0, "shares_outstanding_mln": 10.0,
            "withheld_fields": [{"field": "short_term_debt", "end": "2019-12-31"}],
        }
        assert _ev(fs) is None
        fs.pop("withheld_fields")
        assert _ev(fs) == pytest.approx(10.0 * 190.0 + 300.0 - 50.0)

    def test_a_short_leg_without_a_long_leg_gives_no_ev(self) -> None:
        assert _ev({"total_debt_usd_m": 65.0, "short_term_debt_usd_m": 65.0,
                    "cash_and_equivalents_usd_m": 50.0,
                    "shares_outstanding_mln": 10.0}) is None


    def test_a_refused_total_is_called_withheld_not_missing(self) -> None:
        from app.integrations.market_metrics_enrichment import derive_market_metrics
        from tests.test_phase19_4_identity_sector_market_metrics import _build_prices

        mm = derive_market_metrics("ANY", {
            "total_debt_usd_m": 65.0, "short_term_debt_usd_m": 65.0,
            "cash_and_equivalents_usd_m": 50.0, "shares_outstanding_mln": 10.0,
        }, _build_prices(), "USD")
        warning = next(w for w in mm.warnings if w.startswith("Enterprise value"))
        assert "total debt withheld" in warning
        assert "missing input(s)" not in warning


class TestShortTermBorrowingsParts:
    def test_unconfirmed_parts_withhold_the_total(self) -> None:
        """PR #307 round 2: one bank's single balance-sheet line "Short-term borrowings
        48,088" is tagged OtherShortTermBorrowings, and its 20,400 of commercial paper is
        INSIDE that line. Adding them overstated debt by 20.4bn; taking CP alone dropped
        48.1bn. Without a total that says which, neither is a fact."""
        n = _n(LongTermDebt=320.0, CommercialPaper=20.0, OtherShortTermBorrowings=48.0)
        assert n.total_debt is None and n.debt_to_equity is None
        assert n.total_debt not in (388.0, 340.0, 368.0)
        assert "no total confirms" in n.debt_basis["withheld_reason"]

    def test_parts_confirmed_by_a_parent_total_are_added(self) -> None:
        """The other shape: 'Loans and notes payable' 1,551 = CP 1,495 + other 56."""
        n = _n(LongTermDebtAndCapitalLeaseObligations=420.0,
               LongTermDebtAndCapitalLeaseObligationsCurrent=18.0,
               CommercialPaper=15.0, OtherShortTermBorrowings=0.6,
               NotesAndLoansPayable=15.6)
        assert n.total_debt == pytest.approx(453.6)
        assert "CommercialPaper (15.00m) + OtherShortTermBorrowings (0.60m)" in (
            n.debt_basis["formula"]
        )

    def test_a_parent_equal_to_the_larger_part_means_the_other_is_inside(self) -> None:
        n = _n(LongTermDebt=320.0, CommercialPaper=20.0, OtherShortTermBorrowings=48.0,
               ShortTermBankLoansAndNotesPayable=48.0)
        assert n.total_debt == 368.0

    def test_debt_current_minus_current_maturities_confirms_the_sum(self) -> None:
        n = _n(LongTermDebt=330.0, LongTermDebtCurrent=30.0, DebtCurrent=58.0,
               CommercialPaper=20.0, OtherShortTermBorrowings=8.0)
        assert n.total_debt == 358.0

    def test_a_zero_part_does_not_need_confirming(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               CommercialPaper=0.0, OtherShortTermBorrowings=6.0)
        assert n.total_debt == 336.0

    def test_other_short_term_borrowings_alone(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               OtherShortTermBorrowings=6.0)
        assert n.total_debt == 336.0
        _legs_add_up(n)

    def test_the_total_wins_over_its_parts(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               ShortTermBorrowings=15.0, CommercialPaper=8.0, OtherShortTermBorrowings=7.0)
        assert n.total_debt == 345.0


class TestTheFilersOwnTotalIsACheck:
    def test_review_probe_borrowings_inside_current_maturities_are_not_excused(self) -> None:
        """PR #307 round 2: with the filer's total equal to noncurrent + current, ANY
        excess was accepted as borrowings — 11,400 published against a filer total of
        11,000. Borrowings no larger than the current maturities may be that line."""
        n = _n(LongTermDebtNoncurrent=10_000.0, LongTermDebtCurrent=1_000.0,
               ShortTermBorrowings=400.0, DebtLongtermAndShorttermCombinedAmount=11_000.0)
        assert n.total_debt is None
        assert n.total_debt != 11_400.0
        assert "exceeds the filer's own" in n.debt_basis["withheld_reason"]

    def test_a_total_above_the_filers_own_is_withheld(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               ShortTermBorrowings=15.0, DebtAndCapitalLeaseObligations=338.0)
        assert n.total_debt is None and n.debt_to_equity is None
        assert "exceeds the filer's own DebtAndCapitalLeaseObligations" in (
            n.debt_basis["withheld_reason"]
        )

    def test_agreement_within_rounding_keeps_the_total(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, DebtCurrent=45.0,
               DebtLongtermAndShorttermCombinedAmount=346.0)
        assert n.total_debt == 345.0
        assert not any("total_debt" in w for w in n.warnings)

    def test_a_filer_total_that_is_long_term_debt_only_is_recognised(self) -> None:
        """One real filer tags noncurrent + current maturities (28,687m) as
        DebtLongtermAndShorttermCombinedAmount; its 5,893m of borrowings sit outside."""
        n = _n(LongTermDebtAndCapitalLeaseObligations=270.0,
               LongTermDebtAndCapitalLeaseObligationsCurrent=15.0,
               ShortTermBorrowings=60.0, DebtLongtermAndShorttermCombinedAmount=285.0)
        assert n.total_debt == 345.0
        assert any("equals its long-term debt including current maturities" in note
                   for note in n.debt_basis["notes"])

    def test_a_total_below_the_filers_own_is_kept_and_noted(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, DebtCurrent=45.0,
               DebtAndCapitalLeaseObligations=360.0)
        assert n.total_debt == 345.0
        assert any("below the filer's own" in note for note in n.debt_basis["notes"])


class TestTheSameLineRuleNeedsAgreement:
    def test_equal_lines_with_disagreeing_long_term_concepts_withhold(self) -> None:
        n = _n(LongTermDebt=345.0, LongTermDebtNoncurrent=300.0,
               LongTermDebtCurrent=40.0, ShortTermBorrowings=40.0)
        assert n.total_debt is None
        assert "cannot be told" in n.debt_basis["withheld_reason"]

    def test_equal_lines_with_agreeing_long_term_concepts_count_once(self) -> None:
        n = _n(LongTermDebt=340.0, LongTermDebtNoncurrent=300.0,
               LongTermDebtCurrent=40.0, ShortTermBorrowings=40.0)
        assert n.total_debt == 340.0


class TestDebtCurrentMustContainBothParts:
    def test_debt_current_below_maturities_plus_borrowings_is_not_used(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               ShortTermBorrowings=15.0, DebtCurrent=35.0)
        assert n.total_debt == 345.0
        assert n.debt_basis["combination"] == "noncurrent+current_ltd+short_term_borrowings"

    def test_rounding_between_debt_current_and_its_parts_is_tolerated(self) -> None:
        n = _n(LongTermDebtNoncurrent=300.0, LongTermDebtCurrent=30.0,
               ShortTermBorrowings=15.0, DebtCurrent=44.0)
        assert n.debt_basis["combination"] == "noncurrent+debt_current"
