"""Whether to research a company again — decided without a model. V3.17.2.

WHY THIS IS DETERMINISTIC, AND WHY THAT IS NOT NEGOTIABLE
=========================================================
The discovery chair emits ``internal_action: "research_next"``. It would be very easy to
let that start work. This repository has already answered that question in the opposite
direction and written the reason down: ``playbooks/schema.py`` refuses to let a model set
``blocking=True`` because *"a model able to set one could stop every run."*

**The inverse is worse. A model able to create work could start unbounded PAID work.**

So the council's action is **one input to a predicate**, never an instruction.
``discovery_citation_checker.py`` already coerces an out-of-vocabulary action before it
reaches here, and clause 1 below treats anything outside the allowlist as a refusal rather
than an error. The model influences *which* company is considered; it never decides *what
the platform does*. Same boundary V3.15 drew with ``matching_sector``.

PURE, BY CONSTRUCTION
=====================
Nothing here opens a session, reads settings, calls a clock or touches a provider. Every
fact the decision needs arrives in :class:`EscalationInputs`, already gathered by the
caller. That is what makes every branch — including the six refusals — testable without a
database, and it is why the refusals can be enumerated in tests rather than hoped for.

The caller that gathers those facts is V3.17.3. This module cannot escalate anything; it
can only answer "should it?".

THE CLAUSE THAT WILL SURPRISE YOU
=================================
**Unknown cost blocks. It never reads as zero.** See :func:`_clause_budget` — the
reasoning, and the operational consequence, are written out there because it is the one
rule here that stops a feature working rather than stopping it running away.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.research_decision import (
    DECISION_RESEARCH_NEXT,
    DECISIONS,
    TERMINAL_COST_CAP,
    TERMINAL_COST_UNKNOWN,
    TERMINAL_MAX_ROUNDS,
)

#: Below this, a candidate is uncertain enough that more evidence could change the answer.
#: Above it, the platform already has a view and another round buys little.
DEFAULT_CONFIDENCE_THRESHOLD = 0.7

#: A company researched moments ago has nothing new to find. Twelve hours, because filings
#: and disclosures do not appear faster than that and a shorter window mostly re-reads the
#: same corpus at full price.
DEFAULT_COOLDOWN_SECONDS = 12 * 60 * 60

#: How many decisions one discovery run may create. A thesis run can return fifty
#: candidates; without this, one council could queue fifty paid research runs.
DEFAULT_MAX_DECISIONS_PER_RUN = 5


# --- Refusal codes. Closed vocabulary: a UI renders them, a test enumerates them. ----- #

REFUSED_NOT_RESEARCH_NEXT = "action_not_research_next"
REFUSED_NO_UNCERTAINTY = "no_blocking_gap_and_confident"
REFUSED_OPEN_DECISION = "open_decision_exists"
REFUSED_COOLDOWN = "researched_too_recently"
REFUSED_MAX_ROUNDS = TERMINAL_MAX_ROUNDS
REFUSED_COST_CAP = TERMINAL_COST_CAP
REFUSED_COST_UNKNOWN = TERMINAL_COST_UNKNOWN
REFUSED_RUN_FAN_OUT = "max_decisions_per_run_reached"

REFUSAL_CODES: frozenset[str] = frozenset(
    {
        REFUSED_NOT_RESEARCH_NEXT,
        REFUSED_NO_UNCERTAINTY,
        REFUSED_OPEN_DECISION,
        REFUSED_COOLDOWN,
        REFUSED_MAX_ROUNDS,
        REFUSED_COST_CAP,
        REFUSED_COST_UNKNOWN,
        REFUSED_RUN_FAN_OUT,
    }
)


@dataclass(frozen=True)
class EscalationInputs:
    """Every fact the decision rests on, gathered by the caller.

    Deliberately flat and primitive. An ORM object here would let a lazy load turn a pure
    function into a database call, and the whole point of this module is that it has no
    way to reach anything.
    """

    #: The council's action, **already coerced** to the allowlist upstream. ``None`` when
    #: the council never ran for this candidate — a different thing from "it said no".
    coerced_action: str | None

    #: ``None`` means not measured, which is NOT the same as zero. A candidate whose gaps
    #: were never counted has not been shown to be complete.
    blocking_gap_count: int | None = None
    confidence: float | None = None

    #: Is a decision for this company already in one of the open states? The database
    #: enforces this too (``ux_research_decisions_one_open``); this clause exists so the
    #: controller can refuse *with a reason* instead of catching an IntegrityError and
    #: guessing why.
    has_open_decision: bool = False

    #: ``None`` means this company has never completed research — no cooldown to serve.
    seconds_since_last_completed_research: float | None = None

    escalation_round: int = 0
    max_rounds: int = 2

    #: Cumulative spend on THIS decision so far. ``None`` means **unknown**, and unknown
    #: is not zero. See :func:`_clause_budget`.
    cost_usd_so_far: float | None = 0.0
    #: ``None`` means no cap is configured.
    cost_cap_usd: float | None = None

    decisions_created_this_run: int = 0
    max_decisions_per_run: int = DEFAULT_MAX_DECISIONS_PER_RUN

    confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD
    cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS


@dataclass(frozen=True)
class EscalationVerdict:
    """The answer, and the sentence that will be stored in ``research_decisions.reason``.

    ``reason`` is prose because a human reads it in the queue UI; ``refused_clause`` is a
    code because a query groups by it. Both, not one: a code alone tells an operator
    nothing, and prose alone cannot be counted.
    """

    should_create: bool
    reason: str
    refused_clause: str | None = None

    def __post_init__(self) -> None:
        if self.should_create and self.refused_clause is not None:
            raise ValueError("a created decision cannot carry a refusal code")
        if not self.should_create and self.refused_clause is None:
            raise ValueError("a refusal must say which clause refused")


def _clause_action(i: EscalationInputs) -> EscalationVerdict | None:
    """1. The model's signal — and only a signal."""
    if i.coerced_action is None:
        return EscalationVerdict(
            False,
            "The discovery council did not review this candidate, so there is no "
            "signal to act on.",
            REFUSED_NOT_RESEARCH_NEXT,
        )
    if i.coerced_action not in DECISIONS:
        # Not an error. An unrecognised action is exactly what the upstream coercion
        # exists to produce, and treating it as a refusal is what keeps a novel string
        # from ever meaning "spend money".
        return EscalationVerdict(
            False,
            f"The council's action {i.coerced_action!r} is not in the known vocabulary, "
            "so it is treated as no instruction at all.",
            REFUSED_NOT_RESEARCH_NEXT,
        )
    if i.coerced_action != DECISION_RESEARCH_NEXT:
        return EscalationVerdict(
            False,
            f"The council said {i.coerced_action!r}, not 'research_next'.",
            REFUSED_NOT_RESEARCH_NEXT,
        )
    return None


def _clause_uncertainty(i: EscalationInputs) -> EscalationVerdict | None:
    """2. There must be something left to learn.

    Either a blocking gap is open, or confidence is low enough that evidence could still
    move the answer. Researching a company that is already answered and already confident
    spends money to confirm what is known.

    ``blocking_gap_count is None`` means **not measured**, and an unmeasured candidate is
    not a complete one — but it is not evidence of a gap either, so it cannot satisfy this
    clause on its own. Confidence must then carry it.
    """
    if (i.blocking_gap_count or 0) > 0:
        return None
    if i.confidence is not None and i.confidence < i.confidence_threshold:
        return None
    if i.blocking_gap_count is None and i.confidence is None:
        return EscalationVerdict(
            False,
            "Neither a blocking-gap count nor a confidence score was measured for this "
            "candidate, so there is no evidence that more research would change "
            "anything.",
            REFUSED_NO_UNCERTAINTY,
        )
    return EscalationVerdict(
        False,
        "No blocking gap is open and confidence is at or above the threshold, so "
        "another round would confirm what is already known.",
        REFUSED_NO_UNCERTAINTY,
    )


def _clause_no_open_decision(i: EscalationInputs) -> EscalationVerdict | None:
    """3. One open decision per company.

    The database enforces this as well, and that is the guarantee that actually holds
    under concurrency. This clause exists so the ordinary case refuses with an explanation
    rather than surfacing as a constraint violation nobody can read.
    """
    if i.has_open_decision:
        return EscalationVerdict(
            False,
            "This company already has an open research decision; a second would be "
            "duplicate paid work.",
            REFUSED_OPEN_DECISION,
        )
    return None


def _clause_cooldown(i: EscalationInputs) -> EscalationVerdict | None:
    """4. Nothing new appears in minutes.

    ``None`` means never researched, which serves no cooldown.
    """
    elapsed = i.seconds_since_last_completed_research
    if elapsed is not None and elapsed < i.cooldown_seconds:
        return EscalationVerdict(
            False,
            f"Research completed for this company {int(elapsed)}s ago, inside the "
            f"{int(i.cooldown_seconds)}s cooldown; the corpus will not have changed.",
            REFUSED_COOLDOWN,
        )
    return None


def _clause_rounds(i: EscalationInputs) -> EscalationVerdict | None:
    """5. The loop must end."""
    if i.escalation_round >= i.max_rounds:
        return EscalationVerdict(
            False,
            f"This decision has already had {i.escalation_round} of "
            f"{i.max_rounds} permitted rounds.",
            REFUSED_MAX_ROUNDS,
        )
    return None


def _clause_budget(i: EscalationInputs) -> EscalationVerdict | None:
    """6. Spend must be known and under the cap.

    **UNKNOWN COST BLOCKS. IT IS NEVER READ AS ZERO.** (CLAUDE.md #6.)

    A cost of ``None`` means the platform could not price the work. Treating that as "0
    spent so far" would let a decision whose spend cannot be measured escalate for ever,
    which is the single most expensive failure available here.

    ROUND 0 IS DIFFERENT, AND DELIBERATELY SO
    -----------------------------------------
    Before any round has run, nothing has been spent on this decision. That is a *known*
    zero, not an unknown, so a first round is never blocked by this clause. From round 1
    onward a ``None`` is a genuine measurement failure and blocks.

    THE CONSEQUENCE, STATED PLAINLY
    -------------------------------
    Production currently reports ``estimated_cost_usd`` as NULL, because no price book is
    configured. So **as things stand, escalation will run exactly one round and then stop
    with** ``cost_unknown``. That is the correct behaviour for a platform that cannot see
    its own bill, and it is a limitation to fix by configuring prices — not by softening
    this clause. Anyone tempted to default the cost to 0 here should read this paragraph
    as the reason it was not done.
    """
    if i.escalation_round > 0 and i.cost_usd_so_far is None:
        return EscalationVerdict(
            False,
            "The cost of the previous round could not be determined, and unknown spend "
            "is treated as unaffordable rather than as free.",
            REFUSED_COST_UNKNOWN,
        )
    spent = i.cost_usd_so_far or 0.0
    if i.cost_cap_usd is not None and spent >= i.cost_cap_usd:
        return EscalationVerdict(
            False,
            f"Spend on this decision has reached ${spent:.4f} against a cap of "
            f"${i.cost_cap_usd:.4f}.",
            REFUSED_COST_CAP,
        )
    return None


def _clause_fan_out(i: EscalationInputs) -> EscalationVerdict | None:
    """7. One council must not queue fifty paid runs.

    Not in the design's numbered list, but named in its loop-prevention table as "runaway
    fan-out". A thesis discovery run legitimately returns dozens of candidates, and the
    per-candidate clauses above are all satisfiable for every one of them at once.
    """
    if i.decisions_created_this_run >= i.max_decisions_per_run:
        return EscalationVerdict(
            False,
            f"This discovery run has already created {i.decisions_created_this_run} "
            f"decisions, the configured maximum.",
            REFUSED_RUN_FAN_OUT,
        )
    return None


#: Evaluated in order, and the FIRST refusal is the one reported. Ordered cheapest and
#: most-specific first, so the recorded reason is the most informative true one rather
#: than whichever clause happened to run last.
_CLAUSES = (
    _clause_action,
    _clause_uncertainty,
    _clause_no_open_decision,
    _clause_cooldown,
    _clause_rounds,
    _clause_budget,
    _clause_fan_out,
)


def evaluate(inputs: EscalationInputs) -> EscalationVerdict:
    """Should a research decision be created for this candidate?

    Every clause must pass. The first that refuses supplies both the reason and the code,
    so the answer is deterministic given the inputs — the same candidate evaluated twice
    yields the same sentence, which is what makes the stored reason worth reading.
    """
    for clause in _CLAUSES:
        refusal = clause(inputs)
        if refusal is not None:
            return refusal
    return EscalationVerdict(
        True,
        _approval_reason(inputs),
        None,
    )


def _approval_reason(i: EscalationInputs) -> str:
    """Why this one was accepted, in the words a human will read in the queue."""
    if (i.blocking_gap_count or 0) > 0:
        basis = f"{i.blocking_gap_count} blocking gap(s) are open"
    else:
        basis = f"confidence {i.confidence} is below {i.confidence_threshold}"
    return (
        f"The council proposed research and {basis}; no decision is open for this "
        f"company, and this is round {i.escalation_round + 1} of {i.max_rounds}."
    )


__all__ = [
    "DEFAULT_CONFIDENCE_THRESHOLD",
    "DEFAULT_COOLDOWN_SECONDS",
    "DEFAULT_MAX_DECISIONS_PER_RUN",
    "REFUSAL_CODES",
    "EscalationInputs",
    "EscalationVerdict",
    "evaluate",
]
