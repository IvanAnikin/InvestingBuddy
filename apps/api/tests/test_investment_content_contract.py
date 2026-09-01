"""
The council's job is INTERPRETATION, not recitation.

Measured against the live reports for four issuers before this contract
existed: 8% of the council's bullets were economic interpretation, 51% were
bare restatements of figures already in the evidence pack, and 41% were
statements about what data was missing. All eight agents produced near-
identical text.

That was not a rendering problem. The output had nowhere to put an
interpretation: the JSON contract asked for a "factual" summary, citable FACTS
in ``key_points``, and everything else in ``risks_or_gaps``. So the agents
restated numbers and listed absences, which is exactly what they were asked
for.

These tests pin the fix: the slot exists, it is safety- and citation-gated like
every other claim, the prompts ask for it, and the vocabularies that carry a
directional judgement are closed.
"""

import asyncio

import pytest

from app.services import safety_terms
from app.services.llm import discovery_prompts
from app.services.llm import prompts as company_prompts
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.discovery_schemas import (
    ALLOWED_COMPARISON_DIMENSIONS,
    CandidateNote,
)
from app.services.llm.fake_client import FakeLLMClient
from app.services.llm.schemas import (
    AGENT_COMMITTEE_CHAIR,
    AGENT_FINANCIAL_ANALYST,
    AGENT_RED_TEAM,
    ALLOWED_FUNDAMENTAL_SETUPS,
    ALLOWED_IMPLICATION_DIRECTIONS,
    COUNCIL_AGENT_ORDER,
    DEFAULT_FUNDAMENTAL_SETUP,
    DEFAULT_IMPLICATION_DIRECTION,
    AgentImplication,
    CommitteeSynthesis,
    CouncilAgentOutput,
)

# ---------------------------------------------------------------------------
# The slot exists and survives round-tripping
# ---------------------------------------------------------------------------


def test_agent_output_carries_implications() -> None:
    out = CouncilAgentOutput(
        agent_name=AGENT_FINANCIAL_ANALYST,
        implications=[
            AgentImplication(
                statement="Margin expanded while revenue grew.",
                mechanism="revenue growth + margin expansion -> faster EBIT growth",
                direction="supportive",
                citation_ids=["E1"],
            )
        ],
    )
    payload = out.to_dict()
    assert payload["implications"][0]["direction"] == "supportive"
    assert payload["implications"][0]["mechanism"]


def test_implications_default_to_empty_so_old_reports_still_parse() -> None:
    """A report generated before this field existed is a normal state."""
    out = CouncilAgentOutput.model_validate(
        {"agent_name": AGENT_FINANCIAL_ANALYST, "summary": "legacy"}
    )
    assert out.implications == []
    assert out.synthesis is None


# ---------------------------------------------------------------------------
# An interpretation is gated exactly as hard as a fact
# ---------------------------------------------------------------------------


def test_uncited_material_implication_is_moved_to_unsupported() -> None:
    """An interpretation the reader cannot check is not an interpretation."""
    out = CouncilAgentOutput(
        agent_name=AGENT_FINANCIAL_ANALYST,
        implications=[
            AgentImplication(
                statement=(
                    "Cash generation looks structurally stronger than the "
                    "reported margin implies."
                ),
                citation_ids=[],
            )
        ],
    )
    cleaned, issues = check_and_sanitize(out, {"E1"})
    assert cleaned.implications == []
    assert "structurally stronger" in " ".join(cleaned.unsupported_claims)
    assert any("un-cited material implication" in i for i in issues)


def test_implication_citing_an_unknown_id_loses_that_id() -> None:
    out = CouncilAgentOutput(
        agent_name=AGENT_FINANCIAL_ANALYST,
        implications=[
            AgentImplication(
                statement="Leverage limits reinvestment capacity.",
                citation_ids=["E1", "E999"],
            )
        ],
    )
    cleaned, issues = check_and_sanitize(out, {"E1"})
    assert cleaned.implications[0].citation_ids == ["E1"]
    assert any("implication citation id" in i for i in issues)


def test_implication_direction_is_a_closed_vocabulary() -> None:
    """No rating word can enter through the direction field."""
    out = CouncilAgentOutput(
        agent_name=AGENT_FINANCIAL_ANALYST,
        implications=[
            AgentImplication(
                statement="Free cash flow covers interest comfortably.",
                direction="strong_buy",
                citation_ids=["E1"],
            )
        ],
    )
    cleaned, issues = check_and_sanitize(out, {"E1"})
    assert cleaned.implications[0].direction == DEFAULT_IMPLICATION_DIRECTION
    assert any("direction" in i for i in issues)
    assert "buy" not in cleaned.implications[0].direction


def test_forbidden_language_in_an_implication_quarantines_the_agent() -> None:
    out = CouncilAgentOutput(
        agent_name=AGENT_FINANCIAL_ANALYST,
        implications=[
            AgentImplication(
                statement="Fair value is well above the current price.",
                citation_ids=["E1"],
            )
        ],
    )
    cleaned, issues = check_and_sanitize(out, {"E1"})
    assert cleaned.status == "failed"
    assert cleaned.implications == []
    assert any("quarantined" in i for i in issues)


def test_directional_research_language_is_allowed() -> None:
    """§3's preferred vocabulary must survive the gate — it is the point."""
    for statement in [
        "Margin expansion could support future equity value if it persists.",
        "Rising leverage increases balance-sheet fragility.",
        "Net cash improves downside resilience in a demand slowdown.",
        "Customer concentration threatens margin durability.",
    ]:
        out = CouncilAgentOutput(
            agent_name=AGENT_FINANCIAL_ANALYST,
            implications=[
                AgentImplication(
                    statement=statement,
                    direction="supportive",
                    citation_ids=["E1"],
                )
            ],
        )
        cleaned, _ = check_and_sanitize(out, {"E1"})
        assert cleaned.status != "failed", statement
        assert cleaned.implications, statement


# ---------------------------------------------------------------------------
# Only the chair synthesizes, and its verdict vocabulary is closed
# ---------------------------------------------------------------------------


def test_non_chair_agent_cannot_speak_for_the_committee() -> None:
    out = CouncilAgentOutput(
        agent_name=AGENT_RED_TEAM,
        synthesis=CommitteeSynthesis(fundamental_setup="constructive"),
    )
    cleaned, issues = check_and_sanitize(out, {"E1"})
    assert cleaned.synthesis is None
    assert any("non-chair" in i for i in issues)


def test_fundamental_setup_is_coerced_to_the_allowed_set() -> None:
    out = CouncilAgentOutput(
        agent_name=AGENT_COMMITTEE_CHAIR,
        committee_label="requires_more_evidence",
        synthesis=CommitteeSynthesis(fundamental_setup="very_constructive"),
    )
    cleaned, issues = check_and_sanitize(out, {"E1"})
    assert cleaned.synthesis is not None
    assert cleaned.synthesis.fundamental_setup == DEFAULT_FUNDAMENTAL_SETUP
    assert any("fundamental_setup" in i for i in issues)


def test_a_rating_word_in_the_setup_quarantines_the_chair() -> None:
    """Coercion is the second line. The safety gate is the first."""
    out = CouncilAgentOutput(
        agent_name=AGENT_COMMITTEE_CHAIR,
        committee_label="requires_more_evidence",
        synthesis=CommitteeSynthesis(fundamental_setup="strong buy"),
    )
    cleaned, issues = check_and_sanitize(out, {"E1"})
    assert cleaned.status == "failed"
    assert cleaned.synthesis is None
    assert any("quarantined" in i for i in issues)


def test_no_allowed_setup_is_a_rating() -> None:
    """The setup vocabulary must contain nothing that reads as an action."""
    joined = " ".join(ALLOWED_FUNDAMENTAL_SETUPS).lower()
    for token in ("buy", "sell", "hold", "watch", "overweight", "underweight"):
        assert token not in joined
    assert safety_terms.scan_value(sorted(ALLOWED_FUNDAMENTAL_SETUPS)) == []


def test_no_allowed_direction_is_a_rating() -> None:
    joined = " ".join(ALLOWED_IMPLICATION_DIRECTIONS).lower()
    for token in ("buy", "sell", "hold", "watch"):
        assert token not in joined
    assert safety_terms.scan_value(sorted(ALLOWED_IMPLICATION_DIRECTIONS)) == []


# ---------------------------------------------------------------------------
# The prompts ask for the analysis, not for the data inventory
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "agent", [a for a in COUNCIL_AGENT_ORDER if a != AGENT_COMMITTEE_CHAIR]
)
def test_every_agent_prompt_demands_implications(agent: str) -> None:
    prompt = company_prompts.system_prompt_for(agent)
    assert "implications" in prompt
    assert "INTERPRETATION" in prompt
    # And it says what an interpretation IS, with a worked example.
    assert "mechanism" in prompt


def test_the_summary_brief_no_longer_asks_for_facts_only() -> None:
    """The literal instruction 'summary: factual' is what produced recitation."""
    prompt = company_prompts.system_prompt_for(AGENT_FINANCIAL_ANALYST)
    assert '"summary": "<=600 chars, factual' not in prompt
    assert "CONCLUSION about the business" in prompt


def test_missing_data_is_demoted_in_every_prompt() -> None:
    for agent in COUNCIL_AGENT_ORDER:
        prompt = (
            company_prompts.committee_chair_system_prompt()
            if agent == AGENT_COMMITTEE_CHAIR
            else company_prompts.system_prompt_for(agent)
        )
        assert "MISSING DATA IS NOT THE ANALYSIS" in prompt
        # Identity fields are explicitly disqualified as business risks.
        assert "ISIN" in prompt


def test_financial_analyst_is_asked_for_the_economics() -> None:
    prompt = company_prompts.system_prompt_for(AGENT_FINANCIAL_ANALYST)
    for topic in [
        "GROWTH",
        "PROFITABILITY",
        "CASH GENERATION",
        "BALANCE SHEET",
        "CAPITAL ALLOCATION",
        "QUALITY OF GROWTH",
        "STRENGTHENING",
        "WEAKENING",
    ]:
        assert topic in prompt, topic


def test_red_team_is_pointed_at_the_thesis_not_the_data_package() -> None:
    prompt = company_prompts.system_prompt_for(AGENT_RED_TEAM)
    assert "not the data package" in prompt
    assert "challenge the reasoning, not the completeness" in prompt


def test_chair_prompt_asks_for_the_investor_synthesis() -> None:
    prompt = company_prompts.committee_chair_system_prompt()
    for field in [
        "fundamental_setup",
        "resilience_factors",
        "fragility_factors",
        "what_would_strengthen",
        "what_would_weaken",
        "what_to_watch",
        "key_debate",
    ]:
        assert field in prompt, field
    assert "never with what is missing from the record" in prompt


def test_no_prompt_permits_an_action_or_a_projection() -> None:
    for agent in COUNCIL_AGENT_ORDER:
        prompt = (
            company_prompts.committee_chair_system_prompt()
            if agent == AGENT_COMMITTEE_CHAIR
            else company_prompts.system_prompt_for(agent)
        )
        assert "no BUY/SELL/HOLD/WATCH" in prompt or "NEVER produce a rating" in prompt
        assert "price target" in prompt


# ---------------------------------------------------------------------------
# Discovery: candidates are compared as businesses
# ---------------------------------------------------------------------------


def test_candidate_note_carries_business_dimensions() -> None:
    note = CandidateNote(
        candidate_ref="C1",
        upside_drivers=["Capacity expansion could lift revenue."],
        downside_drivers=["Concentrated customer base."],
        resilience="Net cash position.",
        key_financial_signal="FCF conversion above 90%.",
        strongest_dimension="cash_generation",
    )
    assert note.strongest_dimension in ALLOWED_COMPARISON_DIMENSIONS
    assert note.model_dump()["upside_drivers"]


def test_candidate_note_business_fields_default_empty() -> None:
    """Reviews persisted before these fields existed still parse."""
    note = CandidateNote.model_validate({"candidate_ref": "C1"})
    assert note.upside_drivers == []
    assert note.strongest_dimension is None


def test_discovery_comparison_dimensions_are_not_gap_counts() -> None:
    """The comparison is about businesses; coverage only qualifies confidence."""
    assert "growth_quality" in ALLOWED_COMPARISON_DIMENSIONS
    assert "balance_sheet_resilience" in ALLOWED_COMPARISON_DIMENSIONS
    for banned in ("missing_fields", "source_count", "blocking_gaps"):
        assert banned not in ALLOWED_COMPARISON_DIMENSIONS


def test_discovery_prompt_demotes_coverage_counts() -> None:
    prompt = discovery_prompts.discovery_chair_system_prompt()
    assert "REDUCE CONFIDENCE" in prompt
    assert "They are not the comparison" in prompt
    assert "upside_drivers" in prompt


def test_discovery_chair_is_asked_for_a_cohort_reading() -> None:
    prompt = discovery_prompts.discovery_chair_system_prompt()
    assert "most resilient" in prompt
    assert "highest fundamental risk" in prompt
    # And is forbidden from inventing a category it cannot support.
    assert "an invented one is not" in prompt


# ---------------------------------------------------------------------------
# End to end over the offline client
# ---------------------------------------------------------------------------


def test_offline_council_produces_implications_for_every_agent() -> None:
    """The offline path must not pass while the analysis slot is empty."""
    from app.core.config import Settings
    from app.services.llm.council import run_council
    from app.services.llm.evidence_pack import build_evidence_pack

    # Reuse the module that already knows how to build a rich pack, so this
    # test exercises the real council path rather than a hand-rolled shape.
    from tests.test_phase28a_llm_council import (  # noqa: PLC0415
        _aapl_report_content,
        _aapl_snapshot,
    )

    pack = build_evidence_pack(
        report_content=_aapl_report_content(), company_snapshot=_aapl_snapshot()
    )
    assert pack.item_count > 0
    result = asyncio.run(
        run_council(pack, FakeLLMClient(), cfg=Settings(llm_council_enabled=True))
    )
    assert result.llm_used is True
    completed = [a for a in result.agents if a.status == "completed"]
    assert completed
    for agent in completed:
        assert agent.implications, f"{agent.agent_name} produced no implications"

    chair = next(a for a in result.agents if a.agent_name == AGENT_COMMITTEE_CHAIR)
    assert chair.synthesis is not None
    assert chair.synthesis.fundamental_setup in ALLOWED_FUNDAMENTAL_SETUPS
    assert chair.synthesis.what_to_watch

    # And nothing the council produced trips the production safety gate.
    assert safety_terms.scan_value(result.to_metadata_dict()) == []
