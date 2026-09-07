"""V3.7 Slice 7.2 — the bounded Red Team challenge round.

WHAT THE ROUND IS FOR
=====================
V2's red team can say a claim looks weak; nothing can answer, and nothing records what
happened next. One round with a **mandatory evidence-backed response** forces the
challenged claim either to acquire support or to be marked weak — and **an unresolved
challenge is one of the run's more valuable outputs**, not a failure of it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.challenge import ResearchChallenge
from app.services.council_v2.inputs import assemble
from app.services.council_v2.red_team import (
    MAX_CHALLENGES,
    ROUND_INDEX,
    WEAKNESS_CLASSES,
    WEAKNESS_PERIOD_MISMATCH,
    WEAKNESS_SINGLE_SOURCE,
    Challenge,
    Response,
    challenges_for_chair,
    run_challenge_round,
)
from app.services.ledger import store as ledger


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as s:
        yield s
    await engine.dispose()


@dataclass
class _RedTeam:
    challenges: list[Challenge] = field(default_factory=list)
    raises: Exception | None = None
    seen: int = 0

    async def select(self, *, findings, max_challenges):  # noqa: ANN001, ANN201
        self.seen = max_challenges
        if self.raises is not None:
            raise self.raises
        return self.challenges


@dataclass
class _Responder:
    response: Response | None = None
    by_finding: dict[str, Response] = field(default_factory=dict)
    raises: Exception | None = None

    async def respond(self, *, challenge, finding):  # noqa: ANN001, ANN201
        if self.raises is not None:
            raise self.raises
        return self.by_finding.get(str(challenge.finding_id), self.response)


async def _prepared(session, **finding_overrides):  # noqa: ANN001
    run = await ledger.open_run(session, mode="standard")
    base = {
        "statement": "Margins expanded on mix.",
        "evidence_ids": ["ev:a"],
        "verification_status": "verified",
        "confidence": 0.8,
    }
    base.update(finding_overrides)
    finding = await ledger.record_finding(session, run, **base)
    await session.commit()
    council = await assemble(session, run)
    return run, finding, council


class TestVocabulary:
    def test_an_invented_weakness_class_is_refused(self) -> None:
        with pytest.raises(ValueError, match="keep producing"):
            Challenge(
                finding_id=uuid.uuid4(), weakness_class="feels_wrong", text="x"
            )

    def test_a_challenge_with_no_stated_reason_is_refused(self) -> None:
        """An objection is not a challenge."""
        with pytest.raises(ValueError, match="not a challenge"):
            Challenge(
                finding_id=uuid.uuid4(),
                weakness_class=WEAKNESS_SINGLE_SOURCE,
                text="  ",
            )

    def test_the_vocabulary_is_closed_at_seven(self) -> None:
        assert len(WEAKNESS_CLASSES) == 7


class TestOneRound:
    async def test_a_second_round_is_unstorable(self, session) -> None:
        """Multi-round debate between language models produces text, not truth: agents
        converge on whoever wrote last and the cost grows with nothing to show."""
        run, finding, _ = await _prepared(session)
        session.add(
            ResearchChallenge(
                id=uuid.uuid4(),
                research_run_id=run.id,
                finding_id=finding.id,
                weakness_class=WEAKNESS_SINGLE_SOURCE,
                challenge_text="x",
                round_index=2,
                outcome=ledger.UNRESOLVED,
            )
        )
        with pytest.raises(IntegrityError):
            await session.commit()
        await session.rollback()

    async def test_the_round_index_is_always_one(self, session) -> None:
        run, finding, council = await _prepared(session)
        await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="one filing only",
                    )
                ]
            ),
            responder=_Responder(),
        )
        await session.commit()
        rows = (await session.execute(select(ResearchChallenge))).scalars().all()
        assert [r.round_index for r in rows] == [ROUND_INDEX]

    async def test_more_than_five_challenges_are_truncated(self, session) -> None:
        """A Red Team that challenges everything has ranked nothing."""
        run, finding, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text=f"challenge {i}",
                    )
                    for i in range(20)
                ]
            ),
            responder=_Responder(),
        )
        assert result.challenges == MAX_CHALLENGES


class TestThePlatformDecides:
    async def test_a_response_with_no_evidence_cannot_resolve_anything(
        self, session
    ) -> None:
        """Whatever it claims about itself. A responder that could declare itself
        resolved would make the round decorative."""
        run, finding, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="one filing only",
                    )
                ]
            ),
            responder=_Responder(
                response=Response(
                    text="it is obviously fine", claims_resolved=True, evidence_ids=()
                )
            ),
        )
        assert result.resolved == 0
        assert result.unresolved == 1

    async def test_a_response_with_evidence_can_resolve(self, session) -> None:
        run, finding, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="one filing only",
                    )
                ]
            ),
            responder=_Responder(
                response=Response(
                    text="a second filing confirms it",
                    evidence_ids=("ev:b",),
                    claims_resolved=True,
                    responding_role="financial_analyst",
                )
            ),
        )
        assert result.resolved == 1

    async def test_evidence_without_a_resolution_claim_is_partial(
        self, session
    ) -> None:
        run, finding, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="x",
                    )
                ]
            ),
            responder=_Responder(
                response=Response(text="partly", evidence_ids=("ev:b",))
            ),
        )
        assert result.partially_resolved == 1

    async def test_a_responder_that_explodes_leaves_the_challenge_unresolved(
        self, session
    ) -> None:
        run, finding, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="x",
                    )
                ]
            ),
            responder=_Responder(raises=RuntimeError("boom")),
        )
        assert result.unresolved == 1

    async def test_a_red_team_that_explodes_does_not_end_the_run(
        self, session
    ) -> None:
        run, _, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(raises=RuntimeError("boom")),
            responder=_Responder(),
        )
        assert result.challenges == 0


class TestEffectOnFindings:
    async def test_confidence_can_be_lowered(self, session) -> None:
        run, finding, council = await _prepared(session, confidence=0.8)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="x",
                    )
                ]
            ),
            responder=_Responder(
                response=Response(
                    text="weaker than stated",
                    evidence_ids=("ev:b",),
                    revised_confidence=0.4,
                )
            ),
        )
        await session.commit()
        await session.refresh(finding)
        assert result.confidence_lowered == 1
        assert finding.confidence == pytest.approx(0.4)

    async def test_confidence_is_never_raised(self, session) -> None:
        """A challenge that ended in a stronger claim than it started with means the
        responder used it as an opportunity. "This survived scrutiny" is recorded by the
        challenge row, not by an inflated number on the finding."""
        run, finding, council = await _prepared(session, confidence=0.5)
        await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="x",
                    )
                ]
            ),
            responder=_Responder(
                response=Response(
                    text="stronger than stated",
                    evidence_ids=("ev:b",),
                    revised_confidence=0.95,
                    claims_resolved=True,
                )
            ),
        )
        await session.commit()
        await session.refresh(finding)
        assert finding.confidence == pytest.approx(0.5)

    async def test_a_finding_can_be_withdrawn_and_is_kept(self, session) -> None:
        """Retiring a claim and erasing it are different things."""
        run, finding, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_PERIOD_MISMATCH,
                        text="the figure is interim",
                    )
                ]
            ),
            responder=_Responder(
                response=Response(
                    text="the challenge is right",
                    evidence_ids=("ev:b",),
                    withdraw=True,
                    claims_resolved=True,
                )
            ),
        )
        await session.commit()
        await session.refresh(finding)
        assert result.withdrawn_findings == 1
        assert finding.verification_status == "withdrawn"
        # Kept, and excluded from the Council by 7.1.
        assert (await assemble(session, run)).refusal_reason == "no_findings"


class TestUnresolvedReachesTheChair:
    async def test_an_unresolved_challenge_becomes_a_disagreement(
        self, session
    ) -> None:
        run = await ledger.open_run(session, mode="standard")
        a = await ledger.record_finding(
            session,
            run,
            statement="Revenue was 31,338m.",
            evidence_ids=["ev:a"],
            question_key="revenue",
        )
        await ledger.record_finding(
            session,
            run,
            statement="Revenue was 30,000m.",
            evidence_ids=["ev:b"],
            question_key="revenue",
        )
        await session.commit()
        council = await assemble(session, run)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=a.id,
                        weakness_class=WEAKNESS_PERIOD_MISMATCH,
                        text="that is an interim figure",
                    )
                ]
            ),
            responder=_Responder(response=None),
        )
        await session.commit()
        assert result.unresolved == 1
        assert result.disagreements_created == 1
        summary = await ledger.summarise(session, run)
        assert summary.disagreements_unresolved == 1

    async def test_no_counterpart_means_no_invented_second_finding(
        self, session
    ) -> None:
        """A disagreement needs two findings and the Red Team's objection is not one —
        it has no evidence of its own by construction. Inventing a second finding to
        satisfy the schema would put an unsupported statement in the ledger."""
        run, finding, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="x",
                    )
                ]
            ),
            responder=_Responder(response=None),
        )
        await session.commit()
        assert result.unresolved == 1
        assert result.disagreements_created == 0
        # It still reaches the Chair, through the challenge table.
        chair_input = await challenges_for_chair(session, run)
        assert [c.outcome for c in chair_input] == [ledger.UNRESOLVED]

    async def test_the_chair_sees_unresolved_challenges_first(self, session) -> None:
        """A Chair that read the resolved ones first would be reading a defence before
        the objection."""
        run, finding, council = await _prepared(session)
        second = await ledger.record_finding(
            session, run, statement="another", evidence_ids=["ev:c"]
        )
        await session.commit()
        council = await assemble(session, run)
        await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=finding.id,
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="resolved one",
                    ),
                    Challenge(
                        finding_id=second.id,
                        weakness_class=WEAKNESS_PERIOD_MISMATCH,
                        text="unresolved one",
                    ),
                ]
            ),
            responder=_Responder(
                by_finding={
                    str(finding.id): Response(
                        text="answered",
                        evidence_ids=("ev:d",),
                        claims_resolved=True,
                    )
                }
            ),
        )
        await session.commit()
        ordered = await challenges_for_chair(session, run)
        assert ordered[0].outcome == ledger.UNRESOLVED


class TestGuards:
    async def test_a_challenge_against_a_finding_outside_the_run_is_skipped(
        self, session
    ) -> None:
        """It targets a paragraph, which is the thing the finding_id exists to
        replace."""
        run, _, council = await _prepared(session)
        result = await run_challenge_round(
            session,
            run,
            council,
            red_team=_RedTeam(
                challenges=[
                    Challenge(
                        finding_id=uuid.uuid4(),
                        weakness_class=WEAKNESS_SINGLE_SOURCE,
                        text="x",
                    )
                ]
            ),
            responder=_Responder(),
        )
        assert result.challenges == 0
        assert result.skipped_unchallengeable == [
            (result.skipped_unchallengeable[0][0], "finding_not_in_this_run")
        ]

    async def test_a_council_that_did_not_convene_is_not_challenged(
        self, session
    ) -> None:
        run = await ledger.open_run(session, mode="standard")
        await session.commit()
        council = await assemble(session, run)
        assert council.convened is False
        red_team = _RedTeam()
        result = await run_challenge_round(
            session, run, council, red_team=red_team, responder=_Responder()
        )
        assert result.challenges == 0
        assert red_team.seen == 0
