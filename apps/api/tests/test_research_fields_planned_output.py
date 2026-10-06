"""Planned-output and first-production wording — the second live Rainbow Rare Earths run.

WHAT THESE TESTS PIN
====================
Run 2 of Rainbow (after the retrieval fix) put the right facts in the findings, and the
reconciler still could not use them, because the vocabulary did not recognise how a mine
actually words them:

  * "Phalaborwa … extraction targeted from 2028" (April) and "initial production H1 2029"
    (September) are the SAME milestone with two dates — the newer must supersede the older,
    and neither wording named a tracked field, so nothing was ever compared;
  * a gap "No planned annual output tonnage for Phalaborwa" mapped to NO field
    (`field_unknown`), so no finding could close it even though a finding stated "targeting
    ca. 1,850t/yr";
  * "ca." ended a clause, cutting "targeting ca. 1,850t/yr" into a fragment with no rate.

What must NOT change: a withdrawn or negated value still states nothing, an actual
historical output is not a target, a sentence still ends at a full stop, and a gap about
something else is not closed by an output rate.
"""

from __future__ import annotations

from datetime import date

import pytest

from app.services import research_fields as rf
from app.services.ledger import store as ledger
from tests.test_report_reconciliation import _finding, _gap, gr

# --------------------------------------------------------------------------- #
# first production
# --------------------------------------------------------------------------- #


class TestFirstProductionWording:
    @pytest.mark.parametrize(
        ("text", "year"),
        [
            ("Phalaborwa feed is 35 million tons of dunes; extraction targeted from 2028.", "2028"),
            ("Full construction 2028, initial production H1 2029.", "h1 2029"),
            ("Initial commercial production is expected in 2027.", "2027"),
            ("Extraction from the dunes aimed to start 2028.", "2028"),
            ("The company plans to start mining in H2 2027.", "h2 2027"),
        ],
    )
    def test_a_mine_that_extracts_states_first_production_with_its_target(
        self, text: str, year: str
    ) -> None:
        assert "milestone:first_production" in rf.fields_stated(text)
        assert year in rf.target_years(text)

    def test_the_newer_statement_supersedes_the_older_one(self) -> None:
        # The two statements from the live run, with their real publication dates.
        april = _finding(
            "a", "Phalaborwa feed is 35 million tons of dunes; extraction targeted from 2028.",
            published_at=date(2026, 4, 19),
        )
        september = _finding(
            "b",
            "Rainbow's Phalaborwa plan: feasibility numbers early 2027, early works Q4 2027, "
            "full construction 2028, initial production H1 2029.",
            published_at=date(2026, 9, 10),
        )
        supersessions, _ = gr.supersede([april, september])
        assert [(s.older_id, s.newer_id) for s in supersessions] == [("a", "b")]

    @pytest.mark.parametrize(
        ("text", "years"),
        [
            ("Initial production in 2019 delivered 5kt.", set()),
            ("Initial production achieved in 2019 and expected to ramp by 2027.", {"2027"}),
            ("First production in 2019 was followed by a shutdown, with restart scheduled "
             "for 2027.", {"2027"}),
        ],
    )
    def test_a_year_that_is_history_is_not_a_target(self, text: str, years: set[str]) -> None:
        assert set(rf.target_years(text)) == years

    def test_an_achieved_finding_is_not_superseded_by_a_later_target(self) -> None:
        achieved = _finding(
            "a", "Initial production in 2019 delivered 5kt.", published_at=date(2020, 1, 1),
        )
        target = _finding(
            "b", "Full construction 2028, initial production H1 2029.",
            published_at=date(2026, 9, 10),
        )
        assert gr.supersede([achieved, target]) == ([], [])

    def test_a_withdrawn_target_still_states_nothing(self) -> None:
        assert rf.fields_stated(
            "Initial production H1 2029 has been withdrawn pending the funding decision."
        ) == ()

    def test_a_former_target_is_not_a_target(self) -> None:
        assert not rf.target_years("Initial production was originally planned for 2024.")


# --------------------------------------------------------------------------- #
# planned output / capacity
# --------------------------------------------------------------------------- #


class TestPlannedOutputWording:
    @pytest.mark.parametrize(
        "gap",
        [
            "No planned annual output tonnage for Phalaborwa appears in the evidence.",
            "No production tonnage, capacity or utilisation figures for any operation.",
            "No planned production capacity or mining/processing method detail.",
        ],
    )
    def test_a_gap_about_output_tonnage_names_the_capacity_field(self, gap: str) -> None:
        assert "metric:production_capacity" in rf.fields_mentioned(gap)

    @pytest.mark.parametrize(
        "text",
        [
            "Phalaborwa and a pilot plant, targeting ca. 1,850t/yr separated magnet REO.",
            "Longonjo expected output around 20,000 tpa mixed rare earth carbonate.",
            "The plant is designed for 73,000 tonnes per annum of concentrate.",
        ],
    )
    def test_a_planned_tonnage_rate_states_capacity(self, text: str) -> None:
        assert "metric:production_capacity" in rf.fields_stated(text)

    def test_a_finding_that_states_the_rate_closes_the_output_gap(self) -> None:
        gap = _gap("No planned annual output tonnage for Phalaborwa appears in the evidence.")
        finding = _finding(
            "f1", "Phalaborwa and a pilot plant, targeting ca. 1,850t/yr separated magnet REO.",
            published_at=date(2026, 9, 10),
        )
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status in (ledger.RECONCILED_CLOSED, ledger.RECONCILED_PARTIALLY_CLOSED)
        assert "f1" in verdict.finding_ids

    def test_an_output_rate_does_not_close_a_gap_about_something_else(self) -> None:
        gap = _gap("No capex figure for Phalaborwa appears in the evidence.")
        finding = _finding("f1", "Phalaborwa is targeting ca. 1,850t/yr separated magnet REO.")
        (verdict,) = gr.reconcile([gap], [finding])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    @pytest.mark.parametrize(
        "text",
        [
            # an actual, however it is worded
            "The mine, which is expected to be closed, produced 4,000 tpa in 2021.",
            "Output tonnage reached 4,000 tpa in 2022.",
            "Production tonnage, capacity and utilisation: 4,000 tpa achieved in 2022.",
            # somebody else's output, or the market's
            "Peer Arafura is targeting 20,000 tpa of NdPr.",
            "Nameplate of the competitor is 50,000 tpa.",
            "Company A is expected to be rivalled by Company B at 20,000 tpa.",
            "Industry demand is forecast to reach 90,000 tpa by 2030.",
            "Chinese consumption is projected at 120,000 tonnes per annum.",
            "Analysts forecast production of 4,000 tpa for FY2023.",
            # a rate that is not this company's production capacity
            "The processing plant is designed to treat feed of 500,000 tpa.",
            "Planned exports of 10,000 tpa to Japan.",
            "Expected sales of 4,000 tpa to Umicore.",
            "Targeted reduction of 5,000 tpa of emissions.",
            "Signed an offtake for 5,000 tpa, targeting first sales in 2027.",
        ],
    )
    def test_a_rate_that_is_not_this_companys_plan_states_no_capacity(self, text: str) -> None:
        assert "metric:production_capacity" not in rf.fields_stated(text), text

    def test_a_gap_wording_never_lets_a_finding_state_the_field(self) -> None:
        # "output tonnage" and "tonnage, capacity" identify what a GAP asks for only.
        assert "metric:production_capacity" in rf.fields_mentioned("No output tonnage.")
        assert "metric:production_capacity" not in rf.fields_stated(
            "Output tonnage reached 4,000 tpa in 2022."
        )

    def test_a_peer_rate_does_not_close_the_companys_capacity_gap(self) -> None:
        gap = _gap("No planned production capacity figure for the Foo project.")
        peer = _finding("f1", "Peer Arafura is targeting 20,000 tpa of NdPr.")
        (verdict,) = gr.reconcile([gap], [peer])
        assert verdict.status == ledger.RECONCILED_STILL_OPEN

    def test_an_actual_past_output_is_not_a_plan(self) -> None:
        # No planning cue, so nothing here names the capacity field by the new route.
        assert "metric:production_capacity" not in rf.fields_stated(
            "In 2021 the mine produced 4,000 tonnes and was then placed on care and maintenance."
        )

    def test_a_negated_planned_output_states_nothing(self) -> None:
        assert rf.fields_stated(
            "No planned output of 20,000 tpa has been confirmed by the company."
        ) == ()


# --------------------------------------------------------------------------- #
# the clause splitter
# --------------------------------------------------------------------------- #


class TestClauseSplitterAbbreviations:
    @pytest.mark.parametrize("abbr", ["ca.", "approx.", "est.", "incl.", "vs."])
    def test_an_abbreviation_does_not_end_a_clause(self, abbr: str) -> None:
        clauses = rf._raw_clauses(f"The plant is targeting {abbr} 1,850t/yr of oxide.")  # noqa: SLF001
        assert len(clauses) == 1

    def test_a_sentence_still_ends_at_a_full_stop(self) -> None:
        assert len(rf._raw_clauses("Revenue rose. Capex fell; cash was flat.")) == 3  # noqa: SLF001

    def test_a_denial_stays_scoped_to_its_own_sentence(self) -> None:
        # "No capex disclosed." after "…targeting ca. 1,850t/yr" must not swallow the rate.
        text = "The plant is targeting ca. 1,850t/yr of oxide. No capex figure is disclosed."
        assert len(rf._raw_clauses(text)) == 2  # noqa: SLF001
        assert "metric:production_capacity" in rf.fields_stated(text)
        assert "metric:capex" not in rf.fields_stated(text)

    def test_a_semicolon_and_a_contrast_still_split(self) -> None:
        assert len(rf._raw_clauses("Cash rose; debt fell but capex rose.")) == 3  # noqa: SLF001
