"""V3.4 Slice 4.11 — research modes as bounded depth presets (ADR-052).

The decision this closes was accepted and not implemented: the campaign's record said
the technical ceilings were real numbers while ``ResearchBudget`` still defaulted every
one of them to unbounded.
"""

from __future__ import annotations

import pytest

from app.core.config import Settings
from app.services.consumption import UNBOUNDED, ConsumptionUnits
from app.services.research_mode import (
    DEFAULT_MODE,
    MODE_LIMITS,
    RESEARCH_MODES,
    ModeLimits,
    ResearchMode,
    budget_for,
    limits_for,
    parse_mode,
)


class TestTheCeilingsAreReal:
    def test_every_mode_has_limits(self) -> None:
        assert set(MODE_LIMITS) == set(RESEARCH_MODES)

    @pytest.mark.parametrize("mode", RESEARCH_MODES)
    def test_every_limit_of_every_mode_is_finite_and_positive(
        self, mode: ResearchMode
    ) -> None:
        limits = limits_for(mode)
        for field in limits.__dataclass_fields__:
            value = getattr(limits, field)
            assert value > 0, f"{mode.value}.{field} is not a ceiling"
            assert value != float("inf")

    def test_max_is_the_deepest_and_is_still_finite(self) -> None:
        """An unbounded ceiling is not a generous one, it is an absent one."""
        deepest = limits_for(ResearchMode.MAX)
        for mode in RESEARCH_MODES:
            other = limits_for(mode)
            for field in deepest.__dataclass_fields__:
                assert getattr(deepest, field) >= getattr(other, field)
        assert deepest.max_wall_seconds < float("inf")

    def test_the_modes_are_ordered_by_depth(self) -> None:
        order = [
            ResearchMode.QUICK,
            ResearchMode.STANDARD,
            ResearchMode.DEEP,
            ResearchMode.MAX,
        ]
        for shallower, deeper in zip(order, order[1:]):
            a, b = limits_for(shallower), limits_for(deeper)
            for field in a.__dataclass_fields__:
                assert getattr(b, field) >= getattr(a, field), field

    def test_a_ceiling_of_zero_cannot_be_declared(self) -> None:
        with pytest.raises(ValueError, match="absent one"):
            ModeLimits(
                max_rounds=0,
                max_tasks=1,
                max_tool_calls=1,
                max_web_searches=1,
                max_provider_research_runs=1,
                max_documents=1,
                max_model_calls=1,
                max_model_tokens=1,
                max_wall_seconds=1.0,
            )

    def test_a_quick_run_cannot_outlast_the_deployed_worker_timeout(self) -> None:
        """The invariant that cost six live outages, applied to the shallowest mode.

        The deployed gunicorn `--timeout` is 300s. A QUICK run whose own ceiling exceeds
        it would be a run the platform promises to finish and the process kills.
        """
        assert limits_for(ResearchMode.QUICK).max_wall_seconds < 300.0


class TestParsing:
    def test_an_unknown_mode_runs_standard_not_max(self) -> None:
        assert parse_mode("thorough") is DEFAULT_MODE
        assert parse_mode(None) is DEFAULT_MODE
        assert parse_mode("") is DEFAULT_MODE
        assert DEFAULT_MODE is not ResearchMode.MAX

    def test_a_known_mode_parses(self) -> None:
        assert parse_mode(" DEEP ") is ResearchMode.DEEP


class TestBudget:
    def test_a_mode_produces_a_bounded_budget(self) -> None:
        budget = budget_for(ResearchMode.STANDARD, Settings())
        assert not budget.is_unbounded
        assert "max_wall_seconds" in budget.active_limits
        assert "max_web_searches" in budget.active_limits

    def test_the_monetary_ceiling_is_the_one_that_stays_unset(self) -> None:
        """Two instructions pull apart and both are honoured: the absence of a business
        budget is not unlimited execution, and V3 is not blocked on pricing."""
        budget = budget_for(ResearchMode.MAX, Settings())
        assert budget.max_external_cost_usd == UNBOUNDED
        assert "max_external_cost_usd" not in budget.active_limits
        assert budget.active_limits, "everything else must still be bounded"

    def test_configuration_narrows_a_mode_and_never_widens_it(self) -> None:
        tight = budget_for(
            ResearchMode.MAX, Settings(v3_run_max_web_searches=5)
        )
        assert tight.max_web_searches == 5
        wide = budget_for(
            ResearchMode.QUICK, Settings(v3_run_max_web_searches=10_000)
        )
        assert wide.max_web_searches == limits_for(ResearchMode.QUICK).max_web_searches

    def test_an_operator_may_set_the_monetary_ceiling(self) -> None:
        budget = budget_for(
            ResearchMode.STANDARD, Settings(v3_run_max_external_cost_usd=25.0)
        )
        assert budget.max_external_cost_usd == 25.0

    def test_a_quick_budget_actually_stops_a_run(self) -> None:
        """The enforcement point, exercised — not merely constructed.

        It raises rather than returning a verdict, and it raises **before** the spend:
        a budget satisfied by noticing afterwards is a budget that was exceeded.
        """
        from app.services.consumption import BudgetExceeded

        budget = budget_for(ResearchMode.QUICK, Settings())
        spent = ConsumptionUnits(
            web_search_calls=limits_for(ResearchMode.QUICK).max_web_searches
        )
        with pytest.raises(BudgetExceeded) as caught:
            budget.check(spent, about_to_spend=ConsumptionUnits(web_search_calls=1))
        assert "max_web_searches" in str(caught.value)

    def test_a_quick_budget_permits_what_is_within_it(self) -> None:
        budget = budget_for(ResearchMode.QUICK, Settings())
        budget.check(
            ConsumptionUnits(web_search_calls=1),
            about_to_spend=ConsumptionUnits(web_search_calls=1),
        )

    def test_the_default_mode_comes_from_configuration(self) -> None:
        assert parse_mode(Settings().v3_research_mode_default) is ResearchMode.STANDARD
        assert (
            parse_mode(Settings(v3_research_mode_default="deep").v3_research_mode_default)
            is ResearchMode.DEEP
        )
