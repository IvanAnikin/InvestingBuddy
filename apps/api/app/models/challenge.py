"""The Red Team challenge — V3.7 Slice 7.2.

WHAT THIS REPLACES
==================
`CURRENT`: the red team is one agent in a fixed sequence, reasoning over the same frozen
pack as everyone else, with **no mechanism for the challenged analyst to answer**. It can
say a claim looks weak; nothing can respond, and nothing records what happened next.

A CHALLENGE IS A FIRST-CLASS RECORD
===================================
It targets a ``finding_id`` — not a paragraph — states a **weakness class** from a closed
vocabulary, and has exactly one outcome: resolved, partially resolved, or unresolved.

**An unresolved challenge is not a failure of the run. It is one of its more valuable
outputs**, and it reaches the Chair intact as a ``ResearchDisagreement``.

EXACTLY ONE ROUND
=================
Multi-round debate between language models produces text, not truth: agents converge on
whoever wrote last and the token cost grows with nothing to show for it. One round with a
**mandatory evidence-backed response** is where the value is, because it forces the
challenged claim either to acquire support or to be marked weak.

The schema enforces the shape rather than trusting the caller: a resolved challenge must
carry a response, and a response must carry evidence.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchChallenge(Base):
    """One Red Team challenge against one finding, and what came back."""

    __tablename__ = "research_challenges"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_run_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_runs.id",
            ondelete="CASCADE",
            name="fk_research_challenges_run_id",
        ),
        nullable=False,
    )
    #: The finding under challenge. **A finding, never a paragraph** — which is what
    #: makes a challenge checkable and a response addressable.
    finding_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_findings.id",
            ondelete="CASCADE",
            name="fk_research_challenges_finding_id",
        ),
        nullable=False,
    )
    #: ``unsupported_extrapolation`` | ``single_source`` | ``period_mismatch`` |
    #: ``scope_mismatch`` | ``survivorship`` | ``stale_evidence`` |
    #: ``contradicted_by_evidence``.
    weakness_class: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    challenge_text: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    #: The round this challenge belongs to. **Always 1** for now, and the column exists
    #: so that a later decision to allow a second round is a data change rather than a
    #: schema change — while a CHECK keeps the current decision enforced meanwhile.
    round_index: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=1, server_default=sa.text("1")
    )

    #: ``resolved`` | ``partially_resolved`` | ``unresolved``.
    outcome: Mapped[str] = mapped_column(
        sa.String(20),
        nullable=False,
        default="unresolved",
        server_default=sa.text("'unresolved'"),
    )
    response_text: Mapped[str | None] = mapped_column(sa.String(2000))
    #: The evidence the responding analyst produced. **A response without evidence is an
    #: assertion**, and the whole point of the round is that it forces the claim either
    #: to acquire support or to be marked weak.
    response_evidence_ids_json: Mapped[list | None] = mapped_column(JSONB)
    response_evidence_count: Mapped[int] = mapped_column(
        sa.Integer, nullable=False, default=0, server_default=sa.text("0")
    )
    responding_role: Mapped[str | None] = mapped_column(sa.String(60))
    #: Set when the challenge caused the finding's confidence to be lowered or the
    #: finding to be withdrawn. Kept so "what did the Red Team actually change" is a
    #: query rather than a reconstruction.
    finding_confidence_before: Mapped[float | None] = mapped_column(sa.Float)
    finding_confidence_after: Mapped[float | None] = mapped_column(sa.Float)
    withdrew_finding: Mapped[bool] = mapped_column(
        sa.Boolean, nullable=False, default=False, server_default=sa.false()
    )
    #: The disagreement this became when it stayed unresolved.
    disagreement_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_disagreements.id",
            ondelete="SET NULL",
            name="fk_research_challenges_disagreement_id",
        ),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    responded_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.Index("ix_research_challenges_run_outcome", "research_run_id", "outcome"),
        sa.Index("ix_research_challenges_finding_id", "finding_id"),
        sa.CheckConstraint(
            "weakness_class IN ('unsupported_extrapolation', 'single_source', "
            "'period_mismatch', 'scope_mismatch', 'survivorship', 'stale_evidence', "
            "'contradicted_by_evidence')",
            name="ck_research_challenges_weakness_class",
        ),
        sa.CheckConstraint(
            "outcome IN ('resolved', 'partially_resolved', 'unresolved')",
            name="ck_research_challenges_outcome",
        ),
        # EXACTLY ONE ROUND. Multi-round debate between language models produces text,
        # not truth. Allowing a second round later is a change to this line, deliberately
        # made and reviewable, rather than something a caller can do by passing a 2.
        sa.CheckConstraint("round_index = 1", name="ck_research_challenges_one_round"),
        # A resolved challenge must have been answered. "Resolved" with no response is
        # the challenge being dropped rather than met.
        sa.CheckConstraint(
            "outcome = 'unresolved' OR response_text IS NOT NULL",
            name="ck_research_challenges_resolution_has_a_response",
        ),
        # And the answer must carry evidence. A response without it is an assertion, and
        # the round exists precisely to force support or a weakness marking.
        sa.CheckConstraint(
            "outcome = 'unresolved' OR response_evidence_count > 0",
            name="ck_research_challenges_response_has_evidence",
        ),
        sa.CheckConstraint(
            "response_evidence_count >= 0",
            name="ck_research_challenges_evidence_count_not_negative",
        ),
    )


__all__ = ["ResearchChallenge"]
