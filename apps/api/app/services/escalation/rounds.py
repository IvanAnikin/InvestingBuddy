"""What happens after a round. V3.17.4.

THE DISTINCTION THIS MODULE EXISTS TO PROTECT
=============================================
``exhausted_no_improvement`` is a claim about the WORLD: *research ran, and there was
nothing new to find.* It is the answer that stops the loop and tells a human the evidence
does not exist.

A provider timeout, a dead-lettered job, a missing credential or a worker that died is a
claim about the PLATFORM: *we did not manage to look.* Recording that as
``exhausted_no_improvement`` would put a false statement about the world into the record
— and it is the more dangerous of the two errors, because it looks like a finished
investigation and nobody goes back.

So :func:`decide_next_state` takes the job's own outcome, and a job that did not complete
can never reach ``exhausted``. Every branch below is pure and every one is tested.

V3.17.8 ADDS A THIRD CLAIM, WEAKER THAN BOTH
============================================
``evidence_baseline_missing`` claims nothing about the world *or* the platform's ability
to look. It says only that no pre-round snapshot was taken, so the delta cannot be
computed — and a round whose delta cannot be computed must never authorise a paid one.
Production reached ``improved: true`` on every round 0 for exactly this reason, because a
missing baseline was being read as a baseline of zero.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.models.research_decision import (
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_EXHAUSTED,
    STATUS_REANALYSIS,
    TERMINAL_COST_CAP,
    TERMINAL_COST_UNKNOWN,
    TERMINAL_EVIDENCE_BASELINE_MISSING,
    TERMINAL_EVIDENCE_SUFFICIENT,
    TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
    TERMINAL_JOB_DEAD_LETTERED,
    TERMINAL_MAX_ROUNDS,
    TERMINAL_RESEARCH_DID_NOT_COMPLETE,
)


@dataclass(frozen=True)
class RoundInputs:
    """Everything the transition rests on, gathered by the caller. No I/O here."""

    #: Did the research job actually finish its work? False for a timeout, a
    #: dead-letter, a cancelled run, a lost worker — anything that means we did not look.
    job_completed: bool
    #: True only when the job ended in the queue's dead-letter state.
    job_dead_lettered: bool = False

    improved: bool = False
    open_closable_gaps_remain: bool = False

    #: V3.17.8. Was a real pre-round snapshot taken? ``False`` means ``improved`` above
    #: carries no information — it was computed against a baseline that does not exist,
    #: or was not computed at all. Defaulting to ``True`` is safe **only** because the
    #: single caller that can have a missing baseline passes it explicitly; every pure
    #: test that omits it is describing a measured round.
    evidence_baseline_known: bool = True

    escalation_round: int = 0
    max_rounds: int = 2

    #: ``None`` means unknown, and unknown is never zero.
    cost_usd_so_far: float | None = 0.0
    cost_cap_usd: float | None = None


@dataclass(frozen=True)
class RoundVerdict:
    status: str
    terminal_reason: str | None
    #: Prose for the queue UI and the report provenance line.
    detail: str

    @property
    def is_terminal(self) -> bool:
        return self.status != STATUS_REANALYSIS


def decide_next_state(i: RoundInputs) -> RoundVerdict:
    """Where this decision goes after a round.

    Ordered so that the most honest answer wins: *did we actually look?* is asked before
    *did we find anything?*, because the second question is meaningless if the first is
    no.
    """
    # --- 1. Did research actually happen? -------------------------------------
    if i.job_dead_lettered:
        return RoundVerdict(
            STATUS_ABANDONED,
            TERMINAL_JOB_DEAD_LETTERED,
            "The research job exhausted its attempts and was dead-lettered, so this "
            "round never produced a result. This is NOT a finding that no new evidence "
            "exists — the platform did not manage to look.",
        )
    if not i.job_completed:
        return RoundVerdict(
            STATUS_ABANDONED,
            TERMINAL_RESEARCH_DID_NOT_COMPLETE,
            "The research job did not complete, so no conclusion about the available "
            "evidence can be drawn from this round. Retry is a platform decision, not "
            "an evidence one.",
        )

    # --- 2. It ran. COULD we tell what it acquired? ---------------------------
    #
    # Asked before "did it acquire anything?", because without a pre-round snapshot that
    # question has no answer — and the branch below would answer it anyway, in whichever
    # direction the missing baseline happened to bias it. In production that direction
    # was `improved: true` on every round 0, because a NULL baseline read as a company
    # holding no evidence at all, so weeks of accumulated corpus counted as this round's
    # acquisition. With a price book configured that would have bought a second round on
    # the strength of evidence the platform already had.
    if not i.evidence_baseline_known:
        return RoundVerdict(
            STATUS_ABANDONED,
            TERMINAL_EVIDENCE_BASELINE_MISSING,
            "No pre-round evidence snapshot exists for this decision, so what this "
            "round acquired cannot be measured. This is NOT a finding that nothing was "
            "acquired — it is the platform refusing to guess, and refusing to authorise "
            "further paid work on a guess.",
        )

    # --- 3. It ran and we can measure it. Did it acquire anything? ------------
    if not i.improved:
        return RoundVerdict(
            STATUS_EXHAUSTED,
            TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
            "Research completed and acquired no new searchable evidence, so further "
            "rounds would repeat it at the same cost.",
        )

    # --- 4. It acquired something. Is there anything left to ask? -------------
    if not i.open_closable_gaps_remain:
        return RoundVerdict(
            STATUS_COMPLETED,
            TERMINAL_EVIDENCE_SUFFICIENT,
            "Research completed, acquired new evidence, and no closable gap remains "
            "open.",
        )

    # --- 5. There is more to ask. May we? -------------------------------------
    if i.escalation_round + 1 >= i.max_rounds:
        return RoundVerdict(
            STATUS_ABANDONED,
            TERMINAL_MAX_ROUNDS,
            f"Evidence improved and closable gaps remain, but this decision has used "
            f"all {i.max_rounds} permitted round(s).",
        )
    if i.cost_usd_so_far is None:
        # Same rule as the entry predicate, for the same reason: unknown spend is
        # unaffordable, never free.
        return RoundVerdict(
            STATUS_ABANDONED,
            TERMINAL_COST_UNKNOWN,
            "Evidence improved and closable gaps remain, but the cost of the work so "
            "far could not be determined, and unknown spend is treated as unaffordable "
            "rather than as free.",
        )
    if i.cost_cap_usd is not None and i.cost_usd_so_far >= i.cost_cap_usd:
        return RoundVerdict(
            STATUS_ABANDONED,
            TERMINAL_COST_CAP,
            f"Evidence improved and closable gaps remain, but spend has reached "
            f"${i.cost_usd_so_far:.4f} against a cap of ${i.cost_cap_usd:.4f}.",
        )

    return RoundVerdict(
        STATUS_REANALYSIS,
        None,
        f"Evidence improved and closable gaps remain; authorising round "
        f"{i.escalation_round + 2} of {i.max_rounds}.",
    )


def verdict_payload(delta: dict[str, Any], verdict: RoundVerdict) -> dict[str, Any]:
    """What gets persisted in ``improvement_json``.

    The delta and the decision it produced, together — so a reader six months later can
    check the conclusion against the numbers instead of trusting it.
    """
    return {
        **delta,
        "next_status": verdict.status,
        "terminal_reason": verdict.terminal_reason,
        "detail": verdict.detail,
    }


__all__ = [
    "TERMINAL_EVIDENCE_BASELINE_MISSING",
    "TERMINAL_RESEARCH_DID_NOT_COMPLETE",
    "RoundInputs",
    "RoundVerdict",
    "decide_next_state",
    "verdict_payload",
]
