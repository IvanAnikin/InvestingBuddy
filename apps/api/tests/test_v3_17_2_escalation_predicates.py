"""V3.17.2 — the predicate that decides whether to spend money researching again.

THE BOUNDARY THIS FILE DEFENDS
==============================
The discovery chair emits ``internal_action: "research_next"``, and it would be very easy
to let that start work. This repository already answered that question in the opposite
direction and wrote the reason down: ``playbooks/schema.py`` refuses to let a model set
``blocking=True`` because *"a model able to set one could stop every run."*

**The inverse is worse. A model able to create work could start unbounded PAID work.**

So the tests below are not really about a function returning a boolean. They are about a
boundary: the model chooses *which* company is considered; the platform decides *what it
does*. Every test that says "the council said research_next and the answer was still no"
is a test of that boundary.

WHY EVERY REFUSAL GETS ITS OWN TEST
===================================
The design's gate for this slice is "predicate unit tests incl. **every negative
branch**". That is not box-ticking. A clause that is never exercised is a clause that can
be deleted, inverted or short-circuited with nothing turning red — and each of these
clauses is the only thing standing between a chatty model and a bill.

The approval path gets far fewer tests than the refusals, on purpose: an approval that
should have been a refusal costs money, and a refusal that should have been an approval
costs nothing but a manual click.
"""

from __future__ import annotations

import pytest

from app.models.research_decision import DECISIONS
from app.services.escalation.predicates import (
    DEFAULT_CONFIDENCE_THRESHOLD,
    DEFAULT_COOLDOWN_SECONDS,
    DEFAULT_MAX_DECISIONS_PER_RUN,
    REFUSAL_CODES,
    REFUSED_COOLDOWN,
    REFUSED_COST_CAP,
    REFUSED_COST_UNKNOWN,
    REFUSED_MAX_ROUNDS,
    REFUSED_NO_UNCERTAINTY,
    REFUSED_NOT_RESEARCH_NEXT,
    REFUSED_OPEN_DECISION,
    REFUSED_RUN_FAN_OUT,
    EscalationInputs,
    EscalationVerdict,
    evaluate,
)


def _ok(**over) -> EscalationInputs:  # noqa: ANN003
    """Inputs that pass every clause — so each test changes exactly one thing."""
    base = {
        "coerced_action": "research_next",
        "blocking_gap_count": 3,
        "confidence": 0.4,
        "has_open_decision": False,
        "seconds_since_last_completed_research": None,
        "escalation_round": 0,
        "max_rounds": 2,
        "cost_usd_so_far": 0.0,
        "cost_cap_usd": 5.0,
        "decisions_created_this_run": 0,
    }
    base.update(over)
    return EscalationInputs(**base)


# --------------------------------------------------------------------------- #
# 1. The boundary: the model is a signal, never an instruction
# --------------------------------------------------------------------------- #


class TestTheModelCannotStartWorkByItself:
    @pytest.mark.parametrize(
        "action", ["monitor_for_evidence", "reject_for_now", "insufficient_data"]
    )
    def test_any_other_council_action_refuses(self, action: str) -> None:
        verdict = evaluate(_ok(coerced_action=action))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_NOT_RESEARCH_NEXT

    def test_a_council_that_never_ran_is_not_a_yes(self) -> None:
        """Absence of a signal is not a signal. It is a different state from "no"."""
        verdict = evaluate(_ok(coerced_action=None))

        assert verdict.should_create is False
        assert "did not review" in verdict.reason

    @pytest.mark.parametrize(
        "action",
        [
            "RESEARCH_NEXT",  # case games
            "research_next ",  # whitespace
            "research_next_now",  # plausible-looking novelty
            "escalate",
            "",
            "'; DROP TABLE research_decisions; --",
        ],
    )
    def test_a_novel_action_string_can_never_mean_spend_money(
        self, action: str
    ) -> None:
        """An unrecognised action is a refusal, not an error and never an approval.

        Upstream coercion should already have caught these. "Should already" is why this
        test exists: the clause must be safe on its own, because it is the last thing
        between a model's output and a paid run.
        """
        verdict = evaluate(_ok(coerced_action=action))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_NOT_RESEARCH_NEXT

    @pytest.mark.parametrize("action", ["escalate", "RESEARCH_NEXT", "research_next "])
    def test_a_vocabulary_BREACH_reads_differently_from_a_genuine_no(
        self, action: str
    ) -> None:
        """Two refusals with the same code are not the same event.

        "The council said reject_for_now" is the system working. "The council emitted
        'escalate'" is a word that should not exist reaching the last gate before spend —
        prompt drift, a changed model, or an injection attempt — and it deserves a reason
        an operator can grep for.

        This assertion exists because mutation testing found the branch was unreachable
        by any test: deleting the vocabulary check changed no result, since the next
        clause refuses everything that is not exactly "research_next". Identical outcomes
        are why the message has to carry the difference.
        """
        breach = evaluate(_ok(coerced_action=action))
        genuine_no = evaluate(_ok(coerced_action="reject_for_now"))

        assert breach.should_create is genuine_no.should_create is False
        assert "not in the known vocabulary" in breach.reason
        assert "not in the known vocabulary" not in genuine_no.reason

    def test_the_vocabulary_is_the_councils_own(self) -> None:
        """A parallel vocabulary would need a mapping layer; meaning goes missing there."""
        from app.services.llm.discovery_council import _ACTION_TO_FIELD

        assert set(DECISIONS) == set(_ACTION_TO_FIELD)


# --------------------------------------------------------------------------- #
# 2. Every refusal branch
# --------------------------------------------------------------------------- #


class TestEveryNegativeBranch:
    def test_nothing_left_to_learn(self) -> None:
        """Confident, and no blocking gap: another round confirms what is known."""
        verdict = evaluate(_ok(blocking_gap_count=0, confidence=0.95))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_NO_UNCERTAINTY

    def test_unmeasured_is_not_evidence_of_a_gap(self) -> None:
        """`blocking_gap_count=None` means NOT MEASURED, not "there are gaps".

        Letting an unmeasured candidate satisfy the uncertainty clause would make every
        candidate that was never scored escalate — the largest population there is.
        """
        verdict = evaluate(_ok(blocking_gap_count=None, confidence=None))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_NO_UNCERTAINTY
        assert "was measured" in verdict.reason or "measured" in verdict.reason

    def test_an_open_decision_blocks_a_second(self) -> None:
        verdict = evaluate(_ok(has_open_decision=True))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_OPEN_DECISION

    def test_the_cooldown_refuses_a_company_researched_moments_ago(self) -> None:
        verdict = evaluate(_ok(seconds_since_last_completed_research=60.0))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_COOLDOWN

    def test_the_round_cap_ends_the_loop(self) -> None:
        verdict = evaluate(_ok(escalation_round=2, max_rounds=2))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_MAX_ROUNDS

    def test_the_cost_cap_refuses(self) -> None:
        verdict = evaluate(
            _ok(escalation_round=1, cost_usd_so_far=5.0, cost_cap_usd=5.0)
        )

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_COST_CAP

    def test_fan_out_is_bounded_per_discovery_run(self) -> None:
        """A thesis run can return fifty candidates, every one of which passes the rest."""
        verdict = evaluate(
            _ok(decisions_created_this_run=DEFAULT_MAX_DECISIONS_PER_RUN)
        )

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_RUN_FAN_OUT

    def test_every_refusal_code_is_reachable(self) -> None:
        """A clause nothing can trigger is a clause that can be deleted unnoticed."""
        reached = {
            evaluate(i).refused_clause
            for i in (
                _ok(coerced_action="reject_for_now"),
                _ok(blocking_gap_count=0, confidence=0.95),
                _ok(has_open_decision=True),
                _ok(seconds_since_last_completed_research=1.0),
                _ok(escalation_round=2),
                _ok(escalation_round=1, cost_usd_so_far=None),
                _ok(escalation_round=1, cost_usd_so_far=9.0, cost_cap_usd=5.0),
                _ok(decisions_created_this_run=99),
            )
        }

        assert reached == REFUSAL_CODES


# --------------------------------------------------------------------------- #
# 3. THE EXPENSIVE ONE — unknown cost must never read as free
# --------------------------------------------------------------------------- #


class TestUnknownCostBlocks:
    """CLAUDE.md #6, in the one place where getting it wrong costs real money.

    Production reports `estimated_cost_usd` as NULL because no price book is configured.
    A `cost_usd_so_far or 0.0` written one line higher up would turn every unpriced
    decision into an unlimited budget — and it would look completely reasonable in review.
    """

    def test_a_none_cost_after_a_round_has_run_refuses(self) -> None:
        verdict = evaluate(_ok(escalation_round=1, cost_usd_so_far=None))

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_COST_UNKNOWN

    def test_none_does_not_quietly_become_zero_against_the_cap(self) -> None:
        """The mutation this guards: `spent = i.cost_usd_so_far or 0.0` placed FIRST.

        With that ordering the verdict is an approval, because 0.0 is under every cap.
        The refusal is what proves the None check runs before the arithmetic.
        """
        verdict = evaluate(
            _ok(escalation_round=1, cost_usd_so_far=None, cost_cap_usd=1000.0)
        )

        assert verdict.should_create is False, "unknown cost was treated as free"

    def test_round_zero_is_a_known_zero_not_an_unknown(self) -> None:
        """Nothing has run yet, so nothing has been spent. A first round is not blocked.

        Without this distinction the feature could never start at all — every decision
        would refuse on its own first round and escalation would be dead on arrival while
        appearing to be configured.
        """
        verdict = evaluate(_ok(escalation_round=0, cost_usd_so_far=None))

        assert verdict.should_create is True

    def test_no_cap_configured_does_not_disable_the_unknown_check(self) -> None:
        """Two independent things: "no cap" and "unknown spend"."""
        verdict = evaluate(
            _ok(escalation_round=1, cost_usd_so_far=None, cost_cap_usd=None)
        )

        assert verdict.should_create is False
        assert verdict.refused_clause == REFUSED_COST_UNKNOWN


# --------------------------------------------------------------------------- #
# 4. Approval, and the record it leaves
# --------------------------------------------------------------------------- #


class TestApproval:
    def test_a_blocking_gap_justifies_a_round(self) -> None:
        verdict = evaluate(_ok(blocking_gap_count=2, confidence=0.99))

        assert verdict.should_create is True
        assert verdict.refused_clause is None
        assert "2 blocking gap(s)" in verdict.reason

    def test_low_confidence_alone_justifies_a_round(self) -> None:
        verdict = evaluate(
            _ok(blocking_gap_count=0, confidence=DEFAULT_CONFIDENCE_THRESHOLD - 0.01)
        )

        assert verdict.should_create is True
        assert "confidence" in verdict.reason

    def test_confidence_exactly_at_the_threshold_is_not_uncertain(self) -> None:
        """A boundary that is wrong by one case escalates a whole population of them."""
        verdict = evaluate(
            _ok(blocking_gap_count=0, confidence=DEFAULT_CONFIDENCE_THRESHOLD)
        )

        assert verdict.should_create is False

    def test_the_cooldown_expires(self) -> None:
        verdict = evaluate(
            _ok(seconds_since_last_completed_research=DEFAULT_COOLDOWN_SECONDS + 1)
        )

        assert verdict.should_create is True

    def test_a_never_researched_company_serves_no_cooldown(self) -> None:
        verdict = evaluate(_ok(seconds_since_last_completed_research=None))

        assert verdict.should_create is True

    def test_the_reason_is_a_sentence_a_human_can_act_on(self) -> None:
        """It is stored in `research_decisions.reason`, which is NOT NULL for this reason."""
        verdict = evaluate(_ok(blocking_gap_count=4))

        assert verdict.reason.endswith(".")
        assert "round 1 of 2" in verdict.reason
        assert len(verdict.reason) > 40

    def test_the_same_inputs_always_give_the_same_sentence(self) -> None:
        """Determinism is what makes a stored reason worth reading six months later."""
        assert evaluate(_ok()).reason == evaluate(_ok()).reason


# --------------------------------------------------------------------------- #
# 5. Structural — the module cannot reach anything
# --------------------------------------------------------------------------- #


class TestThePredicateIsPure:
    def test_it_imports_no_session_settings_or_clock(self) -> None:
        """Purity is what makes every branch above testable without a database.

        An import of `datetime.now`, `Settings` or a session here would let the answer
        depend on something no test controls, and the refusals would become "usually".
        """
        from pathlib import Path

        source = Path("app/services/escalation/predicates.py").read_text()
        body = "\n".join(
            line for line in source.splitlines() if not line.lstrip().startswith("#")
        )

        for forbidden in (
            "AsyncSession",
            "get_settings",
            "datetime.now",
            "time.time",
            "await ",
            "requests.",
            "httpx.",
        ):
            assert forbidden not in body, f"predicates.py reaches {forbidden}"

    def test_a_verdict_cannot_be_self_contradictory(self) -> None:
        """An approval carrying a refusal code, or a refusal with no reason, is a bug."""
        with pytest.raises(ValueError):
            EscalationVerdict(True, "approved", REFUSED_COOLDOWN)
        with pytest.raises(ValueError):
            EscalationVerdict(False, "refused but unattributed", None)
