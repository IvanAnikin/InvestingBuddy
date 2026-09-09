"""A re-asked question must keep the tools its definition requires.

WHAT WENT WRONG IN PRODUCTION
=============================
The first live MRNA run searched the web: five searches, six sources retrieved, one
promoted to evidence. **Every run after it silently did not** — zero searches, zero
external leads, and the whole V3 stage finishing in under four seconds.

The mechanism is a two-step interaction that only appears from a company's SECOND run:

1. Run one leaves `recent_external_developments` unanswered, so it is recorded as a gap.
2. Run two turns that gap into a question — with **empty** `required_tools`, because a
   gap record does not carry them. `questions.setdefault(...)` then cannot supply the
   real definition, because the key is already present.

With no external tool required, `wants_external` is False, and the V3.12 eligibility
rule — which exists to stop a flag flip re-routing corpus work to a paid vendor —
disqualifies every role holding an external tool. **The external role is excluded from
its own question**, which is handed to a role that cannot search.

Nothing reported it. The run succeeded, the question was "answered", and the answer was
a gap. It was found by asking why a production run took four seconds.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.services.director.planner import plan_research
from app.services.research_mode import parse_mode

pytestmark = pytest.mark.anyio

EXTERNAL = "recent_external_developments"

#: The gap text as the ledger actually writes it for an unanswered question.
CARRY_FORWARD = (
    (EXTERNAL, f"No citable evidence was retrieved for '{EXTERNAL}'."),
    ("revenue_trajectory", "No citable evidence was retrieved for 'revenue_trajectory'."),
)


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        "v3_deepseek_search_enabled": True,
        "deepseek_api_key": "not-a-real-key-for-planning-only",
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


async def _plan(gaps: tuple, **over: Any):  # noqa: ANN202
    return await plan_research(
        subject="MRNA:US",
        mode=parse_mode("standard"),
        playbooks=[],
        prior_open_gaps=gaps,
        cfg=_cfg(**over),
    )


class TestTheExternalRoleKeepsItsOwnQuestion:
    async def test_first_run_assigns_it_to_the_external_role(self) -> None:
        plan = await _plan(())
        owners = [t.role_id for t in plan.tasks if EXTERNAL in t.question_keys]
        assert owners == ["external_research_analyst"]

    async def test_a_re_asked_question_still_reaches_the_external_role(self) -> None:
        """THE regression. On the code that shipped, this returns `financial_analyst`
        — a role with no external tool — and the run makes no search at all."""
        plan = await _plan(CARRY_FORWARD)
        owners = [t.role_id for t in plan.tasks if EXTERNAL in t.question_keys]
        assert owners == ["external_research_analyst"], (
            f"a re-asked external question went to {owners}, which cannot search"
        )

    async def test_the_re_asked_question_keeps_its_required_tools(self) -> None:
        plan = await _plan(CARRY_FORWARD)
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        assert set(question.required_tools) == {"search_web", "fetch_public_source"}


class TestItDoesNotReintroduceWhatTheRulePrevents:
    async def test_a_flag_that_is_off_grants_no_tools(self) -> None:
        """The eligibility rule exists so turning the flag ON does not re-route work.
        The mirror must hold: with it OFF, nothing acquires an external requirement."""
        plan = await _plan(CARRY_FORWARD, v3_deepseek_search_enabled=False)
        question = next((q for q in plan.questions if q.key == EXTERNAL), None)
        if question is not None:
            assert not (set(question.required_tools) & {"search_web", "fetch_public_source"})

    async def test_a_corpus_question_is_not_handed_to_the_external_role(self) -> None:
        """The defect the eligibility rule was written for: the external role also holds
        `search_company_corpus`, and once took `recent_disclosure` away from the business
        analyst — sending a question the corpus can answer to a paid vendor."""
        plan = await _plan(CARRY_FORWARD)
        owners = [
            t.role_id for t in plan.tasks if "recent_disclosure" in t.question_keys
        ]
        assert "external_research_analyst" not in owners

    async def test_an_unrelated_carry_forward_gap_gains_no_tools(self) -> None:
        """Only questions with a KNOWN definition are restored. An arbitrary gap key
        must not acquire requirements from nowhere."""
        plan = await _plan((("some_unknown_gap", "A gap with no definition."),))
        question = next((q for q in plan.questions if q.key == "some_unknown_gap"), None)
        assert question is not None
        assert not question.required_tools


class TestTheReAskedQuestionIsStillAQuestion:
    async def test_the_canonical_text_is_restored_not_the_gap_description(self) -> None:
        """Restoring the route without the question makes the platform pay a provider
        to search for its own bookkeeping note. Found by review, after the first fix
        restored `required_tools` alone and every test still passed."""
        plan = await _plan(CARRY_FORWARD)
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        assert "No citable evidence" not in question.text
        assert "headline figures" in question.text

    async def test_the_re_ask_is_still_visibly_a_re_ask(self) -> None:
        """`origin` must stay ORIGIN_PRIOR_GAP — restoring the text must not disguise
        the fact that this question is being asked again."""
        plan = await _plan(CARRY_FORWARD)
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        assert question.origin == "prior_gap"

    async def test_the_canonical_priority_is_restored(self) -> None:
        plan = await _plan(CARRY_FORWARD)
        question = next(q for q in plan.questions if q.key == EXTERNAL)
        assert question.priority == 3
