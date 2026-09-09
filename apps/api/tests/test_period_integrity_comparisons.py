"""A quarter compared with a full year cannot produce a growth or decline claim.

WHAT WENT WRONG IN PRODUCTION
=============================
The live MRNA report asserted, and then repeated across the Red Team, the bear case and
the chair:

    "Q2 2026 revenue compared to FY2025 annual revenue indicates a continuing revenue
     decline trend, raising concerns about growth sustainability."

Moderna's Q2 2026 revenue was $145m. Q2 2025 was $142m — a RISE. The "decline" came
entirely from setting one quarter against a full year, an arithmetic nobody performed
and which the report's own snapshot forbids in as many words: "Separate, simultaneously
-true states — never comparable with each other and never annualised."

Two things made it survivable. The instruction against it was a model instruction, which
is not a control. And every numeric check in `numeric_verification` skipped the sentence,
because **it quotes no figures at all** — the claim was invalid on its periods alone.

THE RULE ENFORCED
=================
Same-frequency comparison, OR non-comparative coexistence. Deliberately *frequency* and
not `ReportingPeriod.comparable_with`: that method answers a stricter question — may
these sit on one trend line — and refuses Q1 against Q2. Quarter-on-quarter is a real
comparison, and refusing it would suppress true statements. This codebase has made that
mistake before, withholding 32 correct segment sentences from one report.
"""

from __future__ import annotations

import pytest

from app.services.numeric_verification import (
    comparative_period_conflict,
    incompatible_periods,
    is_comparative,
)

#: The sentence as the live report actually published it.
LIVE_DEFECT = (
    "Q2 2026 revenue compared to FY2025 annual revenue indicates a continuing "
    "revenue decline trend, raising concerns about growth sustainability and "
    "business model adaptation."
)


class TestTheRequiredNegativeCase:
    """quarter revenue vs annual revenue -> cannot produce growth/decline comparison."""

    def test_the_live_sentence_is_refused(self) -> None:
        assert comparative_period_conflict(LIVE_DEFECT) is not None

    def test_it_is_refused_without_quoting_a_single_number(self) -> None:
        """The property that matters: the numeric checks never saw this sentence."""
        assert "145" not in LIVE_DEFECT and "1.9" not in LIVE_DEFECT
        assert comparative_period_conflict(LIVE_DEFECT) is not None

    @pytest.mark.parametrize(
        "sentence",
        [
            "Q2 2026 revenue fell versus FY2025",
            "H1 2026 revenue grew compared with FY2025",
            "revenue declined from FY2024 to Q1 2026",
            "H1 2026 grew versus Q2 2026",
        ],
    )
    def test_every_cross_frequency_comparison_is_refused(self, sentence: str) -> None:
        assert comparative_period_conflict(sentence) is not None


class TestTheRequiredPositiveCase:
    """Q2 2026 $145m vs Q2 2025 $142m -> comparison allowed."""

    def test_like_for_like_quarters_are_allowed(self) -> None:
        sentence = (
            "Revenue rose from $142 million in Q2 2025 to $145 million in Q2 2026."
        )
        assert comparative_period_conflict(sentence) is None

    def test_annual_against_annual_is_allowed(self) -> None:
        assert comparative_period_conflict("FY2025 revenue declined versus FY2024") is None

    def test_quarter_on_quarter_is_allowed(self) -> None:
        """Same frequency. Refusing it would suppress a real comparison."""
        assert comparative_period_conflict("Revenue grew in Q2 2026 versus Q1 2026") is None


class TestCoexistenceIsNotComparison:
    def test_two_periods_stated_side_by_side_are_untouched(self) -> None:
        """The canonical rule allows explicitly non-comparative coexistence. Two figures
        printed together assert nothing; only a direction word makes a claim."""
        sentence = (
            "Q2 2026 revenue was $145 million and FY2025 revenue was $1.944 billion."
        )
        assert not is_comparative(sentence)
        assert comparative_period_conflict(sentence) is None
        # The periods themselves ARE incompatible — it is the absence of a comparison
        # that makes the sentence acceptable, not the absence of a conflict.
        assert incompatible_periods(sentence) is not None

    def test_a_single_period_can_never_conflict(self) -> None:
        assert comparative_period_conflict("Q2 2026 revenue declined sharply") is None
        assert incompatible_periods("Q2 2026 revenue was $145 million") is None


class TestItIsWiredIntoTheGate:
    def test_the_sentence_check_returns_conflicting(self) -> None:
        """Through `check_sentence`, which is what `verify_report_content` calls and
        what the report's withholding is driven by. Testing the helper alone would
        prove the rule exists without proving anything applies it."""
        from app.services.numeric_verification import CanonicalIndex, check_sentence

        verdict = check_sentence(LIVE_DEFECT, CanonicalIndex())
        assert verdict.conflicting, (
            "the report gate must refuse this sentence; a helper nothing calls is "
            "not a control"
        )

    def test_a_clean_sentence_is_not_made_conflicting(self) -> None:
        from app.services.numeric_verification import CanonicalIndex, check_sentence

        verdict = check_sentence(
            "Revenue rose from $142 million in Q2 2025 to $145 million in Q2 2026.",
            CanonicalIndex(),
        )
        assert not verdict.conflicting
