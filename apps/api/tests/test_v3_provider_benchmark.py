"""V3.4 Slice 4.5 — the provider benchmark harness.

Most of these tests assert that the harness **refuses to produce a number**. That is
what a benchmark is for: the failure mode is not an inaccurate comparison, it is a
confident one built from a cost of unknown or a rate over nothing.
"""

from __future__ import annotations

import pytest

from app.services.consumption import PriceBook
from app.services.providers.benchmark import (
    UNAVAILABLE_NOT_APPROVED,
    UNAVAILABLE_REASONS,
    BenchmarkReport,
    BenchmarkTask,
    ProviderScore,
    render_report,
    run_benchmark,
    run_task,
)
from app.services.providers.contracts import (
    LEAD_PENDING,
    LEAD_REJECTED,
    LEAD_UNVERIFIABLE,
    LEAD_VERIFIED,
    STATUS_TIMEOUT,
    ResearchLead,
)
from app.services.providers.fakes import FakeResearchProvider


def _lead(url: str | None = "https://issuer.example/ar.pdf", text: str = "a claim"):
    return ResearchLead(
        claim_text=text, provider="fake_research", claimed_source_url=url
    )


def _task(**overrides):
    base = {"task_id": "t1", "question": "what was revenue?"}
    base.update(overrides)
    return BenchmarkTask(**base)


def _verifier(mapping: dict[str, str]):
    async def verify(lead: ResearchLead) -> str:
        return mapping.get(lead.claim_text, LEAD_PENDING)

    return verify


# --------------------------------------------------------------------------- #
# The primary metric, and the three ways it could lie
# --------------------------------------------------------------------------- #


class TestCostPerVerifiedFinding:
    def test_an_unpriced_provider_has_an_unknown_cost_never_zero(self) -> None:
        score = ProviderScore(provider="deepseek", verified_count=5)
        assert score.estimated_cost_usd is None
        assert score.cost_per_verified_finding is None
        assert score.is_priced is False

    def test_a_priced_provider_that_verified_nothing_has_no_ratio(self) -> None:
        score = ProviderScore(
            provider="deepseek", verified_count=0, estimated_cost_usd=4.0
        )
        assert score.cost_per_verified_finding is None

    def test_the_ratio_is_cost_over_verified_not_over_leads(self) -> None:
        """A provider cannot score well by being confident."""
        score = ProviderScore(
            provider="deepseek",
            lead_count=100,
            verified_count=2,
            estimated_cost_usd=10.0,
        )
        assert score.cost_per_verified_finding == pytest.approx(5.0)

    def test_an_unpriced_provider_is_never_the_cheapest(self) -> None:
        report = BenchmarkReport(
            scores=[
                ProviderScore(provider="unpriced", verified_count=100),
                ProviderScore(
                    provider="priced", verified_count=1, estimated_cost_usd=9.0
                ),
            ]
        )
        assert report.cheapest().provider == "priced"

    def test_no_winner_is_declared_when_none_is_measurable(self) -> None:
        report = BenchmarkReport(
            scores=[ProviderScore(provider="deepseek", verified_count=5)]
        )
        assert report.cheapest() is None
        assert "NOT DETERMINED" in render_report(report)


class TestRatesOverNothing:
    def test_a_provider_with_no_leads_has_an_unknown_survival_rate(self) -> None:
        """Not 0.0. A vendor that never answered is not a vendor that answered badly,
        and the second is much the better of the two."""
        assert ProviderScore(provider="x").verification_survival_rate is None
        assert ProviderScore(provider="x").unverifiable_rate is None

    def test_a_provider_whose_leads_all_failed_has_a_rate_of_zero(self) -> None:
        score = ProviderScore(provider="x", lead_count=3, rejected_count=3)
        assert score.verification_survival_rate == 0.0

    def test_undecided_leads_are_not_counted_against_the_provider(self) -> None:
        """A gate that could not read the document is a statement about the platform's
        reader, not about the vendor. It must not appear in a survival rate."""
        score = ProviderScore(
            provider="x", lead_count=4, verified_count=1, undecided_count=3
        )
        assert score.decided_count == 1
        assert score.verification_survival_rate == 1.0


# --------------------------------------------------------------------------- #
# Running
# --------------------------------------------------------------------------- #


class TestRunning:
    async def test_leads_are_scored_by_the_verification_gate(self) -> None:
        provider = FakeResearchProvider(
            leads=[_lead(text="good"), _lead(text="bad"), _lead(url=None, text="naked")]
        )
        outcome = await run_task(
            provider,
            _task(),
            verifier=_verifier(
                {
                    "good": LEAD_VERIFIED,
                    "bad": LEAD_REJECTED,
                    "naked": LEAD_UNVERIFIABLE,
                }
            ),
        )
        assert outcome.lead_count == 3
        assert outcome.verifiable_lead_count == 2
        assert (outcome.verified_count, outcome.rejected_count) == (1, 1)
        assert outcome.unverifiable_count == 1

    async def test_a_provider_that_raises_does_not_end_the_benchmark(self) -> None:
        """One vendor failing must not delete the others' results."""
        report = await run_benchmark(
            {
                "broken": FakeResearchProvider(raises=RuntimeError("boom")),
                "working": FakeResearchProvider(leads=[_lead(text="good")]),
            },
            [_task()],
            verifier=_verifier({"good": LEAD_VERIFIED}),
        )
        assert report.score_for("broken").tasks_run == 0
        assert report.score_for("broken").unavailable_reasons == ("error",)
        assert report.score_for("working").verified_count == 1

    async def test_an_empty_timeout_result_is_recorded_as_not_run(self) -> None:
        provider = FakeResearchProvider(status=STATUS_TIMEOUT, leads=[])
        outcome = await run_task(provider, _task(), verifier=_verifier({}))
        assert not outcome.ran
        assert outcome.unavailable_reason == "timeout"

    async def test_cited_hosts_are_recorded_against_what_the_task_expected(
        self,
    ) -> None:
        """A provider that answers well from a content farm is answering a different
        question. Invisible from the answer; visible here."""
        provider = FakeResearchProvider(
            leads=[
                _lead(url="https://ir.issuer.example/ar.pdf", text="a"),
                _lead(url="https://contentfarm.example/summary", text="b"),
            ]
        )
        outcome = await run_task(
            provider,
            _task(expected_source_hosts=("issuer.example",)),
            verifier=_verifier({}),
        )
        assert outcome.expected_host_hits == 1
        assert outcome.distinct_cited_hosts == (
            "contentfarm.example",
            "ir.issuer.example",
        )

    async def test_consumption_is_summed_across_tasks(self) -> None:
        report = await run_benchmark(
            {"p": FakeResearchProvider(leads=[_lead(text="good")])},
            [_task(task_id="t1"), _task(task_id="t2")],
            verifier=_verifier({"good": LEAD_VERIFIED}),
        )
        assert report.score_for("p").consumption.provider_research_runs == 2
        assert report.score_for("p").verified_count == 2


class TestUnavailability:
    async def test_a_provider_nobody_may_run_appears_in_the_report(self) -> None:
        """Omitting it would read as a provider nobody considered. "We were not
        permitted to run it" is a result."""
        report = await run_benchmark(
            {},
            [_task()],
            verifier=_verifier({}),
            unavailable={"exa": UNAVAILABLE_NOT_APPROVED},
        )
        assert report.unavailable == {"exa": UNAVAILABLE_NOT_APPROVED}
        assert "exa" in render_report(report)
        # And no score exists for it. There is no code path that produces a number
        # for a provider that did not run.
        assert report.score_for("exa") is None

    async def test_an_invented_unavailability_reason_is_refused(self) -> None:
        with pytest.raises(ValueError, match="list of sentences"):
            await run_benchmark(
                {},
                [_task()],
                verifier=_verifier({}),
                unavailable={"exa": "we did not fancy it"},
            )

    def test_the_reason_vocabulary_is_closed(self) -> None:
        assert UNAVAILABLE_NOT_APPROVED in UNAVAILABLE_REASONS
        assert len(UNAVAILABLE_REASONS) == 5


class TestReport:
    async def test_the_report_records_what_it_would_take_to_repeat_it(self) -> None:
        report = await run_benchmark(
            {"p": FakeResearchProvider(leads=[_lead(text="good")])},
            [_task(task_id="t1"), _task(task_id="t2")],
            verifier=_verifier({"good": LEAD_VERIFIED}),
        )
        payload = report.to_dict()
        assert payload["task_ids"] == ["t1", "t2"]
        assert payload["price_book_is_empty"] is True
        assert payload["scores"][0]["verification_survival_rate"] == 1.0

    def test_unknown_is_rendered_as_unknown_never_as_zero(self) -> None:
        report = BenchmarkReport(scores=[ProviderScore(provider="deepseek")])
        rendered = render_report(report)
        assert "unknown" in rendered
        assert " 0.000" not in rendered

    async def test_an_empty_price_book_prices_nothing_and_says_so(self) -> None:
        report = await run_benchmark(
            {"p": FakeResearchProvider(leads=[_lead(text="good")])},
            [_task()],
            verifier=_verifier({"good": LEAD_VERIFIED}),
            prices=PriceBook(),
        )
        score = report.score_for("p")
        assert score.estimated_cost_usd is None
        assert score.unpriced_units, "unpriced units must be named, not implied"

    async def test_a_configured_price_book_produces_a_real_ratio(self) -> None:
        report = await run_benchmark(
            {"p": FakeResearchProvider(leads=[_lead(text="good")])},
            [_task()],
            verifier=_verifier({"good": LEAD_VERIFIED}),
            prices=PriceBook(usd_per_provider_research_run=2.0),
        )
        score = report.score_for("p")
        assert score.estimated_cost_usd == pytest.approx(2.0)
        assert score.cost_per_verified_finding == pytest.approx(2.0)
        assert report.cheapest().provider == "p"


class TestTheScriptsTaskSet:
    def test_every_benchmark_task_names_a_regression_set_issuer(self) -> None:
        """Fixtures are necessary and insufficient. Each of these issuers exposed a
        defect that only live data found, which is what makes a good score mean
        something."""
        import importlib.util
        from pathlib import Path

        script = (
            Path(__file__).resolve().parents[3] / "scripts" / "v3-provider-benchmark.py"
        )
        spec = importlib.util.spec_from_file_location("v3_bench", script)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        tickers = {task.company_ticker for task in module.TASKS}
        assert {"PNDORA", "CFR", "MRNA", "ASML"} <= tickers
        for task in module.TASKS:
            assert task.expected_source_hosts, task.task_id
            assert task.notes, task.task_id
