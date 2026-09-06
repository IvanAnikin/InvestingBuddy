"""The bounded Red Team challenge round — V3.7 Slice 7.2.

THE SHAPE
=========
::

    verified findings
          │
    Red Team selects the 3-5 weakest assumptions      ← by finding_id, with a stated class
          │
    the responsible analyst responds WITH EVIDENCE    ← bounded; may call tools
          │
    resolved   → the finding is updated (confidence lowered, or withdrawn)
    unresolved → a ResearchDisagreement is persisted
          │
        Chair

EXACTLY ONE ROUND, AND THAT IS A DESIGN DECISION
================================================
Multi-round debate between language models produces text, not truth: agents converge on
whoever wrote last and the token cost grows with nothing to show for it. One round with a
**mandatory evidence-backed response** is where the value is, because it forces the
challenged claim either to acquire support or to be marked weak.

The database enforces it (``ck_research_challenges_one_round``), so allowing a second round
later is a reviewed change to a constraint rather than something a caller can do by passing
a 2.

AN UNRESOLVED CHALLENGE IS AN OUTPUT, NOT A FAILURE
===================================================
It becomes a ``ResearchDisagreement`` and reaches the Chair intact. A Red Team whose
challenges all "resolved" would be a Red Team that was talked out of every one of them,
which is not a quality signal.

WHAT THE ROUND MAY AND MAY NOT DO TO A FINDING
==============================================
It may **lower** confidence and it may **withdraw** a finding. It may not raise confidence:
a challenge that ended in a stronger claim than it started with means the responder used
the challenge as an opportunity, and the honest record of "this survived scrutiny" is the
challenge row itself, not an inflated number on the finding.

A withdrawn finding is **kept** with `verification_status='withdrawn'`, not deleted, and
7.1 already excludes it from the Council — retiring a claim and erasing it are different
things.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Protocol, runtime_checkable

from sqlalchemy import select

from app.models.challenge import ResearchChallenge
from app.models.ledger import ResearchFinding
from app.services.council_v2.inputs import CouncilInput, FindingRef
from app.services.ledger import store as ledger

# ── The weakness vocabulary, closed ─────────────────────────────────────────── #

WEAKNESS_UNSUPPORTED_EXTRAPOLATION = "unsupported_extrapolation"
WEAKNESS_SINGLE_SOURCE = "single_source"
WEAKNESS_PERIOD_MISMATCH = "period_mismatch"
WEAKNESS_SCOPE_MISMATCH = "scope_mismatch"
WEAKNESS_SURVIVORSHIP = "survivorship"
WEAKNESS_STALE_EVIDENCE = "stale_evidence"
WEAKNESS_CONTRADICTED = "contradicted_by_evidence"

WEAKNESS_CLASSES: frozenset[str] = frozenset(
    {
        WEAKNESS_UNSUPPORTED_EXTRAPOLATION,
        WEAKNESS_SINGLE_SOURCE,
        WEAKNESS_PERIOD_MISMATCH,
        WEAKNESS_SCOPE_MISMATCH,
        WEAKNESS_SURVIVORSHIP,
        WEAKNESS_STALE_EVIDENCE,
        WEAKNESS_CONTRADICTED,
    }
)

#: Which weakness class maps to which kind of disagreement when it stays unresolved, so
#: the Chair sees the conflict in the vocabulary it already reads.
_WEAKNESS_TO_NATURE: dict[str, str] = {
    WEAKNESS_UNSUPPORTED_EXTRAPOLATION: "interpretation",
    WEAKNESS_SINGLE_SOURCE: "source_quality",
    WEAKNESS_PERIOD_MISMATCH: "period",
    WEAKNESS_SCOPE_MISMATCH: "scope",
    WEAKNESS_SURVIVORSHIP: "interpretation",
    WEAKNESS_STALE_EVIDENCE: "source_quality",
    WEAKNESS_CONTRADICTED: "value",
}

#: Three to five, per the architecture. The lower bound is not enforced — a run with two
#: findings cannot have three weakest ones — but the upper bound is: a Red Team that
#: challenges everything has ranked nothing.
MAX_CHALLENGES = 5

#: The one round.
ROUND_INDEX = 1

_TEXT_MAX = 2000


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clip(value: str | None, limit: int) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text[:limit] if text else None


@dataclass(frozen=True)
class Challenge:
    """One selected weakness, before it is written."""

    finding_id: uuid.UUID
    weakness_class: str
    text: str

    def __post_init__(self) -> None:
        if self.weakness_class not in WEAKNESS_CLASSES:
            raise ValueError(
                f"{self.weakness_class!r} is not a weakness class. A class invented at "
                "a call site is one nothing can aggregate on, and 'which weakness does "
                "this platform keep producing' is the question that improves it."
            )
        if not (self.text or "").strip():
            raise ValueError(
                "a challenge with no stated reason is an objection, not a challenge."
            )


@dataclass
class Response:
    """The responsible analyst's answer. **Evidence is not optional.**"""

    text: str
    evidence_ids: tuple[str, ...] = ()
    responding_role: str | None = None
    #: The responder's own view of whether the challenge stands. The platform still
    #: decides the outcome — a responder that could declare itself resolved would make
    #: the round decorative.
    claims_resolved: bool = False
    #: A lower confidence the responder accepts. Never higher: a challenge that ended in
    #: a stronger claim than it started with means the responder used it as an
    #: opportunity.
    revised_confidence: float | None = None
    withdraw: bool = False


@runtime_checkable
class RedTeam(Protocol):
    """Selects the weakest assumptions. Supplied by the caller, never implemented here."""

    async def select(
        self, *, findings: "Sequence[FindingRef]", max_challenges: int
    ) -> "Sequence[Challenge]":
        ...  # pragma: no cover - protocol


@runtime_checkable
class Responder(Protocol):
    """Answers one challenge with evidence, or fails to."""

    async def respond(
        self, *, challenge: Challenge, finding: FindingRef
    ) -> Response | None:
        ...  # pragma: no cover - protocol


@dataclass
class ChallengeRoundResult:
    """What one round did, and what it left unresolved."""

    challenges: int = 0
    resolved: int = 0
    partially_resolved: int = 0
    unresolved: int = 0
    withdrawn_findings: int = 0
    confidence_lowered: int = 0
    disagreements_created: int = 0
    skipped_unchallengeable: list[tuple[str, str]] = field(default_factory=list)

    @property
    def all_resolved(self) -> bool:
        return self.challenges > 0 and self.unresolved == 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "challenges": self.challenges,
            "resolved": self.resolved,
            "partially_resolved": self.partially_resolved,
            "unresolved": self.unresolved,
            "withdrawn_findings": self.withdrawn_findings,
            "confidence_lowered": self.confidence_lowered,
            "disagreements_created": self.disagreements_created,
            "skipped_unchallengeable": [
                {"finding_id": fid, "reason": reason}
                for fid, reason in self.skipped_unchallengeable
            ],
        }


async def run_challenge_round(
    session: Any,
    run: Any,
    council_input: CouncilInput,
    *,
    red_team: RedTeam,
    responder: Responder,
    max_challenges: int = MAX_CHALLENGES,
) -> ChallengeRoundResult:
    """Run **one** challenge round. Never raises; always records what happened.

    The platform decides each outcome, not the responder: a responder that could declare
    itself resolved would make the round decorative.
    """
    result = ChallengeRoundResult()
    if not council_input.convened:
        # There is nothing to challenge in a council that did not convene, and
        # challenging a run that already refused would produce findings-about-nothing.
        return result

    citable = council_input.citable_finding_ids
    findings_by_id = {str(f.finding_id): f for f in council_input.findings}

    try:
        selected = list(
            await red_team.select(
                findings=list(council_input.findings),
                max_challenges=min(max_challenges, MAX_CHALLENGES),
            )
        )
    except Exception:  # noqa: BLE001 - a Red Team failure must not end the run
        return result

    for challenge in selected[: min(max_challenges, MAX_CHALLENGES)]:
        key = str(challenge.finding_id)
        if key not in citable:
            # A challenge against something that is not in this run's ledger targets a
            # paragraph, which is the thing the finding_id exists to replace.
            result.skipped_unchallengeable.append((key, "finding_not_in_this_run"))
            continue
        finding_ref = findings_by_id[key]
        row = await session.get(ResearchFinding, challenge.finding_id)
        if row is None:
            result.skipped_unchallengeable.append((key, "finding_row_missing"))
            continue

        try:
            response = await responder.respond(
                challenge=challenge, finding=finding_ref
            )
        except Exception:  # noqa: BLE001 - a responder failure is an unresolved challenge
            response = None

        evidence = tuple(
            str(v).strip()
            for v in (response.evidence_ids if response else ())
            if str(v).strip()
        )
        # THE PLATFORM DECIDES. A response with no evidence cannot resolve anything,
        # whatever it claims about itself.
        if response is None or not evidence:
            outcome = ledger.UNRESOLVED
        elif response.claims_resolved:
            outcome = "resolved"
        else:
            outcome = "partially_resolved"

        confidence_before = row.confidence
        confidence_after = confidence_before
        withdrew = False
        if response is not None:
            if response.withdraw:
                row.verification_status = "withdrawn"
                withdrew = True
                result.withdrawn_findings += 1
            elif response.revised_confidence is not None:
                revised = float(response.revised_confidence)
                if not (0.0 <= revised <= 1.0):
                    revised = confidence_before if confidence_before is not None else 0.0
                # Never upward. "This survived scrutiny" is recorded by the challenge
                # row, not by an inflated number on the finding.
                if confidence_before is None or revised < confidence_before:
                    row.confidence = revised
                    confidence_after = revised
                    result.confidence_lowered += 1

        record = ResearchChallenge(
            id=uuid.uuid4(),
            research_run_id=run.id,
            finding_id=challenge.finding_id,
            weakness_class=challenge.weakness_class,
            challenge_text=_clip(challenge.text, _TEXT_MAX) or "",
            round_index=ROUND_INDEX,
            outcome=outcome,
            response_text=(
                _clip(response.text, _TEXT_MAX)
                if response is not None and outcome != ledger.UNRESOLVED
                else None
            ),
            response_evidence_ids_json=list(evidence) or None,
            response_evidence_count=len(evidence),
            responding_role=(
                _clip(response.responding_role, 60) if response is not None else None
            ),
            finding_confidence_before=confidence_before,
            finding_confidence_after=confidence_after,
            withdrew_finding=withdrew,
        )
        session.add(record)
        await session.flush()

        result.challenges += 1
        if outcome == "resolved":
            result.resolved += 1
        elif outcome == "partially_resolved":
            result.partially_resolved += 1
        else:
            result.unresolved += 1
            disagreement = await _persist_disagreement(
                session, run, row, challenge, response
            )
            if disagreement is not None:
                record.disagreement_id = disagreement.id
                result.disagreements_created += 1
        if response is not None and outcome != ledger.UNRESOLVED:
            record.responded_at = _utcnow()
        await session.flush()

    return result


async def _persist_disagreement(
    session: Any,
    run: Any,
    finding: ResearchFinding,
    challenge: Challenge,
    response: Response | None,
) -> Any:
    """Turn an unresolved challenge into a disagreement the Chair receives.

    A ``ResearchDisagreement`` needs **two** findings, and the Red Team's objection is
    not one — it has no evidence of its own by construction. So the challenge is recorded
    against the finding it targets and the counterparty is the *most recent other finding
    on the same question*, when one exists; otherwise the challenge stands alone as a
    ``research_challenges`` row and reaches the Chair through that table.

    Inventing a second finding to satisfy the schema would put an unsupported statement
    in the ledger, which is the one thing it exists to prevent.
    """
    counterpart = (
        await session.execute(
            select(ResearchFinding)
            .where(
                ResearchFinding.research_run_id == run.id,
                ResearchFinding.id != finding.id,
                ResearchFinding.question_key == finding.question_key,
                ResearchFinding.verification_status != "withdrawn",
            )
            .order_by(ResearchFinding.created_at.desc())
            .limit(1)
        )
    ).scalar_one_or_none()
    if counterpart is None:
        return None
    detail = challenge.text
    if response is not None and response.text:
        detail = f"{detail} — response: {response.text}"
    return await ledger.record_disagreement(
        session,
        run,
        finding_a=finding,
        finding_b=counterpart,
        nature=_WEAKNESS_TO_NATURE.get(challenge.weakness_class, "interpretation"),
        description=_clip(detail, 1000),
    )


async def challenges_for_chair(
    session: Any, run: Any
) -> list[ResearchChallenge]:
    """Every challenge, unresolved first. A required Chair input.

    Unresolved first because they are the ones the Chair must surface. A Chair that read
    the resolved ones first would be reading a defence before the objection.
    """
    stmt = (
        select(ResearchChallenge)
        .where(ResearchChallenge.research_run_id == run.id)
        .order_by(ResearchChallenge.outcome.desc(), ResearchChallenge.created_at)
        .limit(50)
    )
    return list((await session.execute(stmt)).scalars().all())


__all__ = [
    "MAX_CHALLENGES",
    "ROUND_INDEX",
    "WEAKNESS_CLASSES",
    "WEAKNESS_CONTRADICTED",
    "WEAKNESS_PERIOD_MISMATCH",
    "WEAKNESS_SCOPE_MISMATCH",
    "WEAKNESS_SINGLE_SOURCE",
    "WEAKNESS_STALE_EVIDENCE",
    "WEAKNESS_SURVIVORSHIP",
    "WEAKNESS_UNSUPPORTED_EXTRAPOLATION",
    "Challenge",
    "ChallengeRoundResult",
    "RedTeam",
    "Responder",
    "Response",
    "challenges_for_chair",
    "run_challenge_round",
]
