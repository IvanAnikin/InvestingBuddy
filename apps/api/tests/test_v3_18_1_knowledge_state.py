"""V3.18.1 — "InvestingBuddy has not acquired X" is not "the company lacks X".

The live SCCO report made some version of an absence statement thirty-three times:
*"absence of dividend data"*, *"lack of detailed segment or geographic breakdown"*, *"the
pack lacks … company IR disclosures"*. The issuer prints its dividends on the face of the
cash-flow statement, carries segment and geographic notes in its 10-K and runs an investor
site. Nothing was missing from the world — only from this platform's evidence pack.

The sentences below are the production ones, verbatim.
"""

from __future__ import annotations

import pytest

from app.services import knowledge_state as ks
from app.services.llm import citation_checker
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.schemas import AgentRiskGap, CouncilAgentOutput

PRODUCTION_ABSENCE_SENTENCES = (
    "Lack of reported EBITDA and liquidity ratios limits assessment of operational efficiency",
    "lack of trend data limits assessment of capital intensity changes",
    "Absence of full annual or quarterly EBITDA figures limits assessment of earnings quality",
    "The pack lacks non-US filings and company IR disclosures, restricting governance insights",
    "Absence of EBITDA and dividend yield data restricts full evaluation of cash flow quality",
    "Lack of detailed segment or geographic breakdown restricts understanding of growth drivers",
    "No dividend yield or payout information is available, restricting assessment of shareholder returns",
    "Lack of non-US filings and company IR disclosures restricts insight into governance",
    "No segment or geographic breakdown limits understanding of growth drivers and risk concentration",
)

REAL_BUSINESS_RISKS = (
    # Found by review: the first version's bare "lack of / lacks / missing /
    # unavailable" retyped every one of these as a platform gap, and its issuer-assertion
    # pattern disowned the four after them as unevidenced non-disclosure claims.
    "Lack of pricing power in a commoditised copper market could compress margins",
    "Lack of liquidity in the shares may widen spreads",
    "Unavailable water permits could halt Tia Maria",
    "Missing the 2026 production target would delay cash flows",
    "The company has no dividend cover if copper prices fall",
    "The group has not reported positive free cash flow since 2021",
    "The company did not report a profit in three of the last five years",
    "The firm lacks a second supplier for sulphuric acid",
    "The company has no debt and reported record revenue.",
    "Without new permits, production data suggests output will fall",
    "No growth is expected, and production data confirm it",
    # Found by the second review: each word here is also a disclosure word in some
    # other sentence, and the widened first fix ate these.
    "Limited operating history at the new smelter raises execution risk",
    "Limited insurance coverage for tailings dam failures could leave losses uninsured",
    "No hedging of copper price exposure limits earnings visibility",
    "Missing SEC reporting deadlines could trigger delisting",
    "Limited yield improvement at the concentrator slowed output growth",
    "The company does not provide geographic diversification beyond Peru and Mexico",
    "Commodity price volatility could pressure cash flow and debt servicing ability.",
    "Labor disputes at Peruvian operations have disrupted production in prior years.",
    "Water availability constraints may limit expansion at the Tia Maria project.",
    "A controlling shareholder holds the majority of voting rights.",
    "Management slacks on cost discipline when prices are high.",  # 'lacks' inside a word
    "The refinancing of notes due next year depends on market access.",
)


class TestClassification:
    @pytest.mark.parametrize("sentence", PRODUCTION_ABSENCE_SENTENCES)
    def test_an_uncited_absence_is_a_platform_gap(self, sentence: str) -> None:
        text, kind = ks.word_as_platform_gap(sentence, has_citation=False)
        assert kind == ks.KIND_PLATFORM_EVIDENCE_GAP
        assert "InvestingBuddy" in text
        assert sentence in text, "the topic is kept — only its ownership changes"

    @pytest.mark.parametrize("sentence", REAL_BUSINESS_RISKS)
    def test_a_business_risk_is_left_exactly_as_written(self, sentence: str) -> None:
        """The over-suppression direction. A guard that ate findings would be a worse
        defect than the one it fixes."""
        assert ks.word_as_platform_gap(sentence, has_citation=False) == (
            sentence,
            ks.KIND_BUSINESS_RISK,
        )

    def test_a_cited_absence_is_a_checkable_claim_and_is_kept(self) -> None:
        sentence = "The filing states that no dividend was declared for the period."
        assert ks.word_as_platform_gap(sentence, has_citation=True) == (
            sentence,
            ks.KIND_BUSINESS_RISK,
        )


class TestAssertionsAboutTheIssuer:
    @pytest.mark.parametrize(
        "sentence",
        [
            "The company does not disclose segment information.",
            # Found by review: a NAMED issuer, a ticker and "it" were all missed.
            "Southern Copper does not disclose segment data",
            "It does not publish a geographic breakdown",
            "SCCO lacks an investor relations website",
            "The company has no investor relations website.",
            "The issuer fails to report geographic revenue.",
            "Management does not provide guidance on dividends.",
        ],
    )
    def test_an_unevidenced_non_disclosure_claim_is_disowned(self, sentence: str) -> None:
        text, kind = ks.word_as_platform_gap(sentence, has_citation=False)
        assert kind == ks.KIND_PLATFORM_EVIDENCE_GAP
        assert text.startswith("Platform evidence gap")
        assert "not a finding about the company" in text

    def test_in_a_summary_only_that_sentence_is_replaced(self) -> None:
        summary = (
            "Margins are strong and leverage is moderate. The company does not disclose "
            "dividends. Earnings remain sensitive to the copper price."
        )
        corrected, count = ks.correct_issuer_assertions(summary)
        assert count == 1
        assert corrected.startswith("Margins are strong and leverage is moderate.")
        assert corrected.endswith("Earnings remain sensitive to the copper price.")
        assert "not a finding about the company" in corrected

    def test_a_clumsy_but_true_absence_in_a_summary_is_left_alone(self) -> None:
        summary = "The absence of full annual data limits a complete assessment."
        assert ks.correct_issuer_assertions(summary) == (summary, 0)

    def test_a_summary_about_the_business_is_not_disowned(self) -> None:
        """Found by review: `has no … report\\w*` matched this."""
        summary = "The company has no debt and reported record revenue."
        assert ks.correct_issuer_assertions(summary) == (summary, 0)


class TestLabels:
    def test_the_default_state_is_the_humble_one(self) -> None:
        assert ks.PLATFORM_GAP_LABEL in ks.label_gap("segment_revenue")

    def test_labelling_is_idempotent(self) -> None:
        once = ks.label_gap("segment_revenue")
        assert ks.label_gap(once) == once

    def test_a_line_not_reported_for_the_period_says_it_was_verified(self) -> None:
        label = ks.label_gap("gross_profit", ks.NOT_REPORTED_FOR_PERIOD)
        assert "NOT REPORTED" in label and "older value was not carried" in label


def _output(items: list[AgentRiskGap], summary: str = "s") -> CouncilAgentOutput:
    return CouncilAgentOutput(agent_name="risk_governance", summary=summary, risks_or_gaps=items)


class TestTheCouncilCheckerAppliesIt:
    """Producer → consumer, through the function production calls."""

    def test_gap_items_are_typed_and_worded(self) -> None:
        out = _output(
            [
                AgentRiskGap(item=PRODUCTION_ABSENCE_SENTENCES[0], severity="medium"),
                AgentRiskGap(item=REAL_BUSINESS_RISKS[0], severity="high"),
            ]
        )
        cleaned, issues = check_and_sanitize(out, evidence_ids=set(), known_gaps=[])
        gap, risk = cleaned.risks_or_gaps
        assert gap.kind == ks.KIND_PLATFORM_EVIDENCE_GAP and "InvestingBuddy" in gap.item
        assert risk.kind == ks.KIND_BUSINESS_RISK and risk.item == REAL_BUSINESS_RISKS[0]
        assert any("platform evidence gap" in i for i in issues)

    def test_the_kind_reaches_the_stored_report_shape(self) -> None:
        out = _output([AgentRiskGap(item=PRODUCTION_ABSENCE_SENTENCES[1])])
        cleaned, _ = check_and_sanitize(out, evidence_ids=set(), known_gaps=[])
        assert cleaned.model_dump()["risks_or_gaps"][0]["kind"] == ks.KIND_PLATFORM_EVIDENCE_GAP

    def test_a_summary_claiming_non_disclosure_is_corrected(self) -> None:
        out = _output([], summary="Margins are high. The company does not disclose segments.")
        cleaned, issues = check_and_sanitize(out, evidence_ids=set(), known_gaps=[])
        assert "not a finding about the company" in cleaned.summary
        assert any("issuer does not disclose" in i for i in issues)

    def test_mutation_without_the_guard_the_gap_is_published_as_a_business_risk(
        self, monkeypatch
    ) -> None:
        monkeypatch.setattr(
            citation_checker,
            "word_as_platform_gap",
            lambda text, has_citation: (text, ks.KIND_BUSINESS_RISK),
        )
        out = _output([AgentRiskGap(item="The company has no investor relations website.")])
        cleaned, _ = check_and_sanitize(out, evidence_ids=set(), known_gaps=[])
        assert cleaned.risks_or_gaps[0].kind == ks.KIND_BUSINESS_RISK
        assert cleaned.risks_or_gaps[0].item == "The company has no investor relations website."
