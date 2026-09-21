"""V3.18.1 — no model may invent the meaning of a ratio after seeing it.

WHAT WENT WRONG IN PRODUCTION
=============================
The live SCCO council wrote that net income was "approximately 88.4% of net operating
cash flow" and then that *"the decline in net income as a percentage of operating cash
flow from 113.4% in H1 2025 to 88.4% in H1 2026 suggests weakening cash conversion"*.

The platform computes that ratio nowhere: the model divided two evidence items, chose
which went on top, and chose what the result meant. On the conventional definition —
operating cash flow over net income — the same two half-years show conversion moving
from 88% to 113%, i.e. **improving**. The arithmetic was right; the sentence was backwards.

Three controls, asserted here together because each covers the others' blind spot:
the definition carries its direction, the value reaches the model with its reading, and a
percentage the evidence does not contain is refused whatever the model concluded from it.
"""

from __future__ import annotations

import dataclasses
from types import SimpleNamespace

import pytest

from app.services.calculations import definitions
from app.services.calculations.definitions import (
    DEFINITIONS,
    DIRECTIONALITIES,
    HIGHER_IS_BETTER,
    CalculationDefinition,
)
from app.services.calculations.statement_metrics import (
    compute_statement_metrics,
    metrics_from_sec_summary,
)
from app.services.llm import citation_checker
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.ratio_guard import percentages_in, unsupported_percentages
from app.services.llm.schemas import (
    AgentImplication,
    AgentKeyPoint,
    CouncilAgentOutput,
    EvidenceItem,
)

#: SCCO FY2025, USD m, from the filed statements (10-K 0001104659-26-021492).
FY2025 = {
    "revenue": 13420.0,
    "operating_profit": 7001.7,
    "net_income": 4348.2,
    "operating_cash_flow": 4752.1,
    "capital_expenditure": 1325.3,
    "free_cash_flow": 3426.8,
    "total_debt": 6750.7,
    "cash_and_equivalents": 4304.6,
    "shareholders_equity": 11038.0,
    "dividends_paid": 2485.1,
}


class TestEveryDefinitionSaysHowItIsRead:
    @pytest.mark.parametrize("key", sorted(DEFINITIONS))
    def test_direction_and_interpretation_are_declared(self, key: str) -> None:
        d = DEFINITIONS[key]
        assert d.directionality in DIRECTIONALITIES
        assert len(d.interpretation) >= 20
        sem = d.semantics()
        for field in ("metric_id", "definition", "unit", "directionality", "interpretation",
                      "period_rule", "scope_rule"):
            assert sem[field], f"{key} is missing {field}"

    def test_a_definition_without_a_direction_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="directionality"):
            dataclasses.replace(DEFINITIONS["net_margin"], directionality="")

    def test_a_definition_without_an_interpretation_cannot_be_constructed(self) -> None:
        with pytest.raises(ValueError, match="interpretation"):
            dataclasses.replace(DEFINITIONS["net_margin"], interpretation="good")

    def test_numerator_must_be_one_of_the_inputs(self) -> None:
        with pytest.raises(ValueError, match="numerator"):
            dataclasses.replace(DEFINITIONS["net_margin"], numerator="ebitda")


class TestCashConversionIsDefinedTheConventionalWay:
    def test_it_is_operating_cash_flow_over_net_income(self) -> None:
        d = DEFINITIONS["cash_conversion"]
        assert (d.numerator, d.denominator) == ("operating_cash_flow", "net_income")
        assert d.directionality == HIGHER_IS_BETTER
        assert "OPPOSITE" in d.interpretation, "the inverse ratio must be warned against"

    def test_the_two_half_years_read_as_improving(self) -> None:
        """The production figures, on the real definition."""
        h1_2025, _ = compute_statement_metrics(
            {"operating_cash_flow": 100.0, "net_income": 113.4},
            period_label="H1 2025", keys=("cash_conversion",),
        )
        h1_2026, _ = compute_statement_metrics(
            {"operating_cash_flow": 100.0, "net_income": 88.4},
            period_label="H1 2026", keys=("cash_conversion",),
        )
        assert h1_2025[0].value == pytest.approx(88.18, abs=0.01)
        assert h1_2026[0].value == pytest.approx(113.12, abs=0.01)
        assert h1_2026[0].value > h1_2025[0].value, "higher is better ⇒ conversion improved"

    def test_a_loss_over_a_loss_is_not_printed_as_healthy_conversion(self) -> None:
        """Found by review: FCF −30 over NI −100 printed "fcf_conversion = 30.0% …
        higher is better" — the inverted-reading defect, reintroduced WITH authority."""
        readings, refusals = compute_statement_metrics(
            {"free_cash_flow": -30.0, "net_income": -100.0, "shareholders_equity": -200.0},
            period_label="FY2025", keys=("fcf_conversion", "return_on_equity"),
        )
        assert readings == []
        assert {r.key for r in refusals} == {"fcf_conversion", "return_on_equity"}

    def test_a_loss_has_no_conversion_reading(self) -> None:
        readings, refusals = compute_statement_metrics(
            {"operating_cash_flow": 50.0, "net_income": -20.0},
            period_label="FY2025", keys=("cash_conversion",),
        )
        assert not readings and refusals[0].key == "cash_conversion"


class TestStatementMetrics:
    def test_values_match_a_hand_calculation(self) -> None:
        readings, _ = compute_statement_metrics(FY2025, period_label="FY2025")
        by_key = {r.key: r.value for r in readings}
        assert by_key["cash_conversion"] == pytest.approx(109.29, abs=0.01)
        assert by_key["capex_to_ocf"] == pytest.approx(27.89, abs=0.01)
        assert by_key["operating_margin"] == pytest.approx(52.17, abs=0.01)
        assert by_key["dividend_cover_by_fcf"] == pytest.approx(1.38, abs=0.01)
        assert by_key["net_debt"] == pytest.approx(2446.1, abs=0.1)

    def test_missing_input_is_a_refusal_never_a_zero(self) -> None:
        readings, refusals = compute_statement_metrics(FY2025, period_label="FY2025")
        assert "gross_margin" not in {r.key for r in readings}
        refusal = next(r for r in refusals if r.key == "gross_margin")
        assert refusal.reason == "missing_input"

    def test_value_and_meaning_travel_in_one_line(self) -> None:
        readings, _ = compute_statement_metrics(FY2025, period_label="FY2025")
        line = next(r for r in readings if r.key == "cash_conversion").as_evidence_line()
        assert "109.3%" in line
        assert "operating_cash_flow / net_income" in line
        assert "higher is better" in line
        assert "FY2025" in line and "group" in line

    def test_an_interim_bundle_is_not_computed_under_an_annual_label(self) -> None:
        assert metrics_from_sec_summary(
            {"fiscal_year": 2026, "period_basis": "quarterly", "revenue_usd_m": 1.0}
        ) == ([], [])

    def test_net_debt_is_not_defined_on_a_partial_debt_total(self) -> None:
        """Found by review. With a debt leg withheld, `total_debt` is a partial sum."""
        fs = {
            "fiscal_year": 2025, "period_basis": "annual",
            "total_debt_usd_m": 6750.7, "cash_and_equivalents_usd_m": 4304.6,
            "withheld_fields": [{"field": "short_term_debt", "end": "2024-12-31"}],
        }
        readings, _ = metrics_from_sec_summary(fs)
        assert "net_debt" not in {r.key for r in readings}

    def test_a_metric_the_consistency_check_withheld_stays_withheld(self) -> None:
        fs = {
            "fiscal_year": 2025, "period_basis": "annual",
            "revenue_usd_m": 100.0, "gross_profit_usd_m": 20.0,
            "statement_consistency": {
                "inconsistencies": [{"withhold_derived": ["gross_margin"]}]
            },
        }
        readings, _ = metrics_from_sec_summary(fs)
        assert "gross_margin" not in {r.key for r in readings}


def _pack_with_sec_summary():
    snapshot = {
        "ticker": "ANY",
        "fundamentals_summary": {
            "fiscal_year": 2025, "period_basis": "annual", "form_type": "10-K",
            "source_tier": "T2_regulator_or_gov",
            "revenue_usd_m": 13420.0, "operating_income_usd_m": 7001.7,
            "net_income_usd_m": 4348.2, "operating_cash_flow_usd_m": 4752.1,
            "capital_expenditures_usd_m": 1325.3, "free_cash_flow_usd_m": 3426.8,
            "dividends_paid_usd_m": 2485.1,
        },
    }
    # The PRODUCTION shape: `LLM_COUNCIL_EVIDENCE_BUDGETS_ENABLED` is set on the deployed
    # API, which selects the tier-split path. The dark single-item path predates derived
    # metrics being separated at all and is deliberately left byte-identical.
    cfg = SimpleNamespace(llm_council_evidence_budgets_enabled=True)
    return build_evidence_pack(report_content={}, company_snapshot=snapshot, budget_cfg=cfg)


class TestTheCouncilReceivesTheDefinition:
    """Producer → consumer: the definition has to be IN the pack the model reads."""

    def test_defined_metric_items_are_in_the_pack(self) -> None:
        pack = _pack_with_sec_summary()
        defined = [i for i in pack.evidence_items if "DEFINED METRICS" in (i.title or "")]
        assert defined, "no defined-metric item reached the evidence pack"
        text = " ".join(i.excerpt or "" for i in defined)
        assert "cash_conversion" in text and "operating_cash_flow / net_income" in text
        assert all(i.source_tier == "T6_model_estimate" for i in defined), (
            "a computed metric is never presented at a filing's tier"
        )

    def test_dividends_are_evidence_not_a_gap(self) -> None:
        pack = _pack_with_sec_summary()
        assert any("dividends_paid_usd_m=2485.1" in (i.excerpt or "") for i in pack.evidence_items)

    def test_the_pack_says_whose_gaps_the_gaps_are(self) -> None:
        pack = _pack_with_sec_summary()
        assert "INVESTINGBUDDY HAS NOT YET ACQUIRED" in pack.known_gaps_meaning
        assert "known_gaps_meaning" in pack.model_dump_json()


def _evidence() -> dict[str, EvidenceItem]:
    return {
        "E1": EvidenceItem(
            id="E1", source_tier="T1_primary_filing", source_type="sec_financial_statement",
            title="annual FY2025 — cash flow statement",
            excerpt="operating_cash_flow_usd_m=4752.1; capital_expenditures_usd_m=1325.3",
        ),
        "E2": EvidenceItem(
            id="E2", source_tier="T6_model_estimate", source_type="derived_financial_metric",
            title="annual FY2025 — DEFINED METRICS: cash generation and funding",
            excerpt="Cash conversion [cash_conversion] = 109.3% for FY2025; operating margin 52.17",
        ),
    }


def _output(**kw) -> CouncilAgentOutput:
    return CouncilAgentOutput(agent_name="financial_analyst", summary="s", **kw)


class TestASelfComputedRatioIsRefused:
    def test_the_production_sentence_is_moved_to_unsupported(self) -> None:
        sentence = (
            "The decline in net income as a percentage of operating cash flow from 113.4% "
            "in H1 2025 to 88.4% in H1 2026 suggests weakening cash conversion efficiency."
        )
        out = _output(implications=[AgentImplication(
            statement=sentence, mechanism="m", direction="pressuring", citation_ids=["E1"],
        )])
        evidence = _evidence()
        cleaned, issues = check_and_sanitize(out, set(evidence), evidence)
        assert cleaned.implications == []
        assert sentence in cleaned.unsupported_claims
        assert any("113.4%" in i and "computed" not in i.lower() or "113.4%" in i for i in issues)

    def test_a_defined_metric_quoted_with_rounding_is_kept(self) -> None:
        evidence = _evidence()
        out = _output(key_points=[
            AgentKeyPoint(claim="Cash conversion was 109% in FY2025, above accounting profit.",
                          citation_ids=["E2"]),
            AgentKeyPoint(claim="Operating margin of 52.2% reflects a low-cost position.",
                          citation_ids=["E2"]),
        ])
        cleaned, _ = check_and_sanitize(out, set(evidence), evidence)
        assert len(cleaned.key_points) == 2

    def test_a_claim_with_no_percentage_is_untouched(self) -> None:
        evidence = _evidence()
        out = _output(key_points=[AgentKeyPoint(
            claim="Operating cash flow was $4,752.1 million in FY2025.", citation_ids=["E1"])])
        cleaned, _ = check_and_sanitize(out, set(evidence), evidence)
        assert len(cleaned.key_points) == 1

    def test_mutation_without_the_guard_the_inverted_reading_is_published(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(citation_checker, "unsupported_percentages", lambda *_a: [])
        evidence = _evidence()
        out = _output(implications=[AgentImplication(
            statement="Net income at 88.4% of operating cash flow suggests weakening conversion.",
            mechanism="m", direction="pressuring", citation_ids=["E1"],
        )])
        cleaned, _ = check_and_sanitize(out, set(evidence), evidence)
        assert len(cleaned.implications) == 1, "this is the defect, reproduced"


class TestPercentageParsing:
    def test_forms(self) -> None:
        assert [p[1] for p in percentages_in("up 71.6%, or 12 percent, to 3,5 %")] == [71.6, 12.0, 3.5]

    def test_no_evidence_numbers_refuses_nothing(self) -> None:
        assert unsupported_percentages("margin of 88.4%", []) == []

    def test_a_dollar_figure_is_not_a_percentage(self) -> None:
        assert percentages_in("capex of $1,325.3 million") == []

    def test_a_thousands_comma_is_not_a_decimal_comma(self) -> None:
        """Found by review: `13,420` was read as 13.42, corrupting every evidence
        number above 999."""
        from app.services.llm.ratio_guard import evidence_numbers

        assert 13420.0 in evidence_numbers(["revenue 13,420 and 1,250.5"])
        assert 1250.5 in evidence_numbers(["revenue 13,420 and 1,250.5"])
        assert [p[1] for p in percentages_in("up 1,250%")] == [1250.0]

    def test_both_ends_of_a_range_are_checked(self) -> None:
        assert [p[1] for p in percentages_in("margins of 50-55%")] == [50.0, 55.0]
        assert unsupported_percentages("Operating margins of 50-52% are typical.", [50.0, 52.17]) == []


class TestTheGuardOnlyExaminesRatioShapedClaims:
    """Found by review. The first version refused ANY percentage the pack lacked, and
    every sentence below is a true, sourced statement it moved to unsupported_claims —
    the evidence states them in words, or they are not ratios of statement lines at all."""

    NUMBERS = [4752.1, 13420.0, 52.17, 109.3, 17.43]

    @pytest.mark.parametrize(
        "sentence",
        [
            "A 15% royalty applies to operating income in Peru.",
            "Minera Mexico is a 100% owned subsidiary.",
            "The US statutory rate of 21% applies.",
            "The mine sits in the top 10% of peers on cash cost.",
            "Roughly 75% of sales came from copper.",
            "Net income rose 71.6% in the second quarter.",
            "Grupo Mexico holds 88.9% of the shares.",
        ],
    )
    def test_a_percentage_that_is_not_a_financial_ratio_is_left_alone(self, sentence) -> None:
        assert unsupported_percentages(sentence, self.NUMBERS) == []

    @pytest.mark.parametrize(
        "sentence",
        [
            "The margin impact of the 21% tax rate was small.",
            "Operating margin of 52% compares with a 30% statutory tax rate.",
        ],
    )
    def test_another_percentage_in_a_ratio_sentence_is_left_alone(self, sentence) -> None:
        """Found by the second review: a sentence that MENTIONS a margin may carry a
        percentage that is not the margin."""
        assert unsupported_percentages(sentence, self.NUMBERS + [52.0]) == []

    @pytest.mark.parametrize(
        "sentence",
        [
            (
                "The decline in net income as a percentage of operating cash flow from "
                "113.4% in H1 2025 to 88.4% in H1 2026 suggests weakening cash conversion."
            ),
            "Operating margin rose to 52% in FY2025 from 49% in FY2024.",
            "Net income was approximately 88.4% of net operating cash flow.",
            "EBITDA margin of 58% is among the highest in the sector.",
            "Return on equity of 44% signals strong capital efficiency.",
            "Capital expenditures absorbed 31% of operating cash flow.",
        ],
    )
    def test_an_invented_ratio_is_refused(self, sentence) -> None:
        assert unsupported_percentages(sentence, self.NUMBERS)


def test_the_vocabulary_is_exported() -> None:
    assert isinstance(DEFINITIONS["cash_conversion"], CalculationDefinition)
    assert {"CONTEXTUAL", "LOWER_IS_BETTER"} <= set(definitions.__all__)


class TestTheDefinitionSurvivesTheEvidenceBudget:
    def test_defined_metrics_have_a_category_of_their_own(self) -> None:
        """Not statement FACTS (they are T6), and not the capped price/trend group
        (where they would be the first thing dropped)."""
        from app.services.llm.evidence_budget import CATEGORY_DEFINED_METRIC, evidence_category

        pack = _pack_with_sec_summary()
        defined = [i for i in pack.evidence_items if "DEFINED METRICS" in (i.title or "")]
        assert defined
        assert {evidence_category(i) for i in defined} == {CATEGORY_DEFINED_METRIC}

    def test_they_survive_a_news_flood(self) -> None:
        """The budget is where an item actually lives or dies. Forty aggregator
        headlines must not push the definitions out of a 20-item pack."""
        from app.services.llm.evidence_budget import apply_evidence_budget
        from app.services.llm.schemas import EvidenceItem

        pack = _pack_with_sec_summary()
        flood = [
            EvidenceItem(
                id=f"N{n}", source_tier="T5_api_aggregator", source_type="news_article",
                title=f"Headline number {n} about the company", excerpt=f"story {n}",
                fields_supported=["catalyst"],
            )
            for n in range(40)
        ]
        pack = pack.model_copy(update={"evidence_items": flood + list(pack.evidence_items)})
        from app.core.config import Settings

        # Explicit Settings, never a bare `Settings()`: that reads the developer's .env.
        cfg = Settings(
            llm_council_evidence_budgets_enabled=True,
            source_connector_enabled=True,
            llm_council_evidence_max_items=20,
        )
        budgeted = apply_evidence_budget(pack, max_items=20, cfg=cfg)
        before = [i for i in pack.evidence_items if "DEFINED METRICS" in (i.title or "")]
        kept = [i for i in budgeted.evidence_items if "DEFINED METRICS" in (i.title or "")]
        assert len(budgeted.evidence_items) <= 20
        assert before and len(kept) == len(before), "a definition was dropped under pressure"

    def test_they_survive_the_character_budget_too(self) -> None:
        """Found by review: the item floor held and the definitions still vanished,
        because they sort last and the running character total reached them last."""
        from app.core.config import Settings
        from app.services.llm.evidence_budget import apply_evidence_budget
        from app.services.llm.schemas import EvidenceItem

        pack = _pack_with_sec_summary()
        long_filings = [
            EvidenceItem(
                id=f"F{n}", source_tier="T1_primary_filing", source_type="sec_filing_excerpt",
                title=f"10-K excerpt {n}", excerpt=("filing text " * 120),
            )
            for n in range(17)
        ]
        pack = pack.model_copy(update={"evidence_items": long_filings + list(pack.evidence_items)})
        cfg = Settings(
            llm_council_evidence_budgets_enabled=True,
            source_connector_enabled=True,
            llm_council_evidence_max_items=20,
        )
        budgeted = apply_evidence_budget(pack, max_items=20, max_chars=24000, cfg=cfg)
        before = [i for i in pack.evidence_items if "DEFINED METRICS" in (i.title or "")]
        kept = [i for i in budgeted.evidence_items if "DEFINED METRICS" in (i.title or "")]
        assert len(kept) == len(before)
        total = sum(len(i.excerpt or "") + len(i.title or "") for i in budgeted.evidence_items)
        assert total <= 24000, "the ceiling itself still holds"

    def test_no_reading_is_cut_off_before_its_interpretation(self) -> None:
        """The budgeter truncates at 1,200 characters per item."""
        pack = _pack_with_sec_summary()
        for item in pack.evidence_items:
            if "DEFINED METRICS" in (item.title or ""):
                assert len(item.excerpt or "") < 1200, item.title
                assert not (item.excerpt or "").endswith("…")
