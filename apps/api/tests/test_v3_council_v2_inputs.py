"""V3.7 Slice 7.1 — Council V2 inputs.

TWO PROPERTIES V2 COULD NOT HAVE
================================
1. **The Council can be refused.** V2's council always ran, because nothing could say no.
2. **A citation resolves.** `E1` meant "the first item in the pack this run happened to
   build"; a `finding_id` is a row, and the finding it names already carries evidence.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.council_v2.inputs import (
    REFUSAL_REASONS,
    REFUSED_BLOCKING_GAP,
    REFUSED_BLOCKING_QUESTION,
    REFUSED_NO_FINDINGS,
    CouncilInput,
    assemble,
    unresolvable_citations,
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


async def _run(session, **overrides):  # noqa: ANN001
    return await ledger.open_run(session, mode="standard", **overrides)


async def _finding(session, run, **overrides):  # noqa: ANN001
    base = {
        "statement": "Group revenue was 31,338m DKK.",
        "evidence_ids": ["ev:abc"],
    }
    base.update(overrides)
    return await ledger.record_finding(session, run, **base)


class TestRefusal:
    async def test_an_open_blocking_question_refuses_the_council(
        self, session
    ) -> None:
        """The run reports insufficient evidence rather than analysing around the hole.
        V2's council could not express this: it always ran."""
        run = await _run(session)
        await ledger.add_question(
            session,
            run,
            question_key="cash_runway",
            text="runway?",
            origin=ledger.ORIGIN_PLAYBOOK,
            blocking=True,
        )
        await _finding(session, run)
        await session.commit()
        result = await assemble(session, run)
        assert result.convened is False
        assert result.refusal_reason == REFUSED_BLOCKING_QUESTION
        assert result.findings == ()

    async def test_a_blocking_gap_refuses_the_council(self, session) -> None:
        run = await _run(session)
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_ENTITY_AMBIGUOUS,
            description="Two candidate issuers.",
            blocks_council=True,
        )
        await _finding(session, run)
        await session.commit()
        result = await assemble(session, run)
        assert result.refusal_reason == REFUSED_BLOCKING_GAP
        # The gaps still travel: the reader is told WHY it was refused.
        assert result.gaps

    async def test_a_run_with_no_findings_refuses(self, session) -> None:
        """A Council with nothing evidence-linked to reason over would produce prose."""
        run = await _run(session)
        await session.commit()
        result = await assemble(session, run)
        assert result.refusal_reason == REFUSED_NO_FINDINGS

    async def test_the_refusal_vocabulary_is_closed(self) -> None:
        with pytest.raises(ValueError, match="list of sentences"):
            CouncilInput(
                research_run_id=uuid.uuid4(),
                convened=False,
                refusal_reason="it felt thin",
            )
        assert len(REFUSAL_REASONS) == 3

    async def test_a_reason_beside_a_convened_council_cannot_be_built(self) -> None:
        with pytest.raises(ValueError, match="face value"):
            CouncilInput(
                research_run_id=uuid.uuid4(),
                convened=True,
                refusal_reason=REFUSED_NO_FINDINGS,
            )


class TestConvening:
    async def test_a_clean_run_convenes(self, session) -> None:
        run = await _run(session)
        await _finding(session, run)
        await session.commit()
        result = await assemble(session, run)
        assert result.convened is True
        assert result.refusal_reason is None
        assert len(result.findings) == 1

    async def test_the_playbook_versions_travel_to_the_council(self, session) -> None:
        """A report states which methodology produced it, and that has to be a fact
        about the run."""
        run = await _run(session, playbook_versions={"luxury": 1})
        await _finding(session, run)
        await session.commit()
        result = await assemble(session, run)
        assert result.playbook_versions == {"luxury": 1}

    async def test_a_withdrawn_finding_does_not_come_back(self, session) -> None:
        """One the Red Team retired must not return through the Council's front door."""
        run = await _run(session)
        await _finding(session, run, statement="kept")
        await _finding(
            session, run, statement="retired", verification_status="withdrawn"
        )
        await session.commit()
        result = await assemble(session, run)
        assert [f.statement for f in result.findings] == ["kept"]

    async def test_verified_findings_sort_ahead_of_unverified(self, session) -> None:
        """When truncation bites it must drop the least supported material."""
        run = await _run(session)
        await _finding(session, run, statement="unverified one")
        await _finding(
            session, run, statement="verified one", verification_status="verified"
        )
        await session.commit()
        result = await assemble(session, run)
        assert result.findings[0].statement == "verified one"

    async def test_truncation_is_reported_not_silent(self, session) -> None:
        from app.services.council_v2 import inputs

        run = await _run(session)
        for i in range(inputs.MAX_FINDINGS + 5):
            await _finding(session, run, statement=f"finding {i}")
        await session.commit()
        result = await assemble(session, run)
        assert len(result.findings) == inputs.MAX_FINDINGS
        assert result.truncated["findings"] == 5


class TestConflictIsAnInput:
    async def test_unresolved_disagreements_reach_the_council(self, session) -> None:
        """The Chair's job is to surface conflict, never to silently pick a number. A
        Chair handed a tidy pack could not do it."""
        run = await _run(session)
        a = await _finding(session, run, statement="Revenue was 31,338m.")
        b = await _finding(session, run, statement="Revenue was 30,000m.")
        await ledger.record_disagreement(
            session, run, finding_a=a, finding_b=b, nature="value"
        )
        await session.commit()
        result = await assemble(session, run)
        assert len(result.unresolved_disagreements) == 1
        assert result.unresolved_disagreements[0].nature == "value"

    async def test_a_resolved_disagreement_carries_its_explanation(
        self, session
    ) -> None:
        run = await _run(session)
        a = await _finding(session, run, statement="A")
        b = await _finding(session, run, statement="B")
        await ledger.record_disagreement(
            session,
            run,
            finding_a=a,
            finding_b=b,
            nature="period",
            resolution=ledger.RESOLVED,
            resolution_note="One was an interim figure.",
        )
        await session.commit()
        result = await assemble(session, run)
        assert result.disagreements[0].resolution_note == "One was an interim figure."
        assert result.unresolved_disagreements == ()

    async def test_gaps_are_an_input_so_the_analysis_can_be_explicit(
        self, session
    ) -> None:
        run = await _run(session)
        await _finding(session, run)
        await ledger.record_gap(
            session,
            run,
            gap_type=ledger.GAP_TRANSCRIPT_UNAVAILABLE,
            description="No FY2025 transcript published.",
            why_it_matters="Management language for the period cannot be read.",
            closable=False,
        )
        await session.commit()
        result = await assemble(session, run)
        assert result.gaps[0].gap_type == ledger.GAP_TRANSCRIPT_UNAVAILABLE
        assert result.gaps[0].why_it_matters


class TestCitationsResolve:
    async def test_a_finding_id_is_the_citable_handle(self, session) -> None:
        run = await _run(session)
        finding = await _finding(session, run)
        await session.commit()
        result = await assemble(session, run)
        assert str(finding.id) in result.citable_finding_ids

    async def test_a_citation_to_nothing_is_detectable(self, session) -> None:
        """An `E1` could never be checked, because it referred to a position in a pack
        nobody kept. A finding_id either is in this set or is a fabrication."""
        run = await _run(session)
        finding = await _finding(session, run)
        await session.commit()
        result = await assemble(session, run)
        bogus = str(uuid.uuid4())
        assert unresolvable_citations(result, {str(finding.id), bogus}) == (bogus,)
        assert unresolvable_citations(result, {str(finding.id)}) == ()

    async def test_every_council_finding_carries_its_own_evidence(
        self, session
    ) -> None:
        """Guaranteed upstream by the ledger's CHECK, and re-asserted here because it is
        the property the whole Council contract rests on."""
        run = await _run(session)
        await _finding(session, run, evidence_ids=["ev:a", "ev:b"])
        await _finding(session, run, calculation_ids=["calc:1"], statement="derived")
        await session.commit()
        result = await assemble(session, run)
        for finding in result.findings:
            assert finding.evidence_ids or finding.calculation_ids


class TestSafetyVocabularyUnchanged:
    def test_v3_7_does_not_reopen_the_chair_label_vocabulary(self) -> None:
        """Several live corrections went into getting this right. V3.7 is not an excuse
        to reopen it."""
        from app.services.llm.schemas import (
            ALLOWED_COMMITTEE_LABELS,
            ALLOWED_FUNDAMENTAL_SETUPS,
        )

        assert ALLOWED_COMMITTEE_LABELS == {
            "internal_research_candidate",
            "requires_more_evidence",
            "insufficient_data",
            "monitor_for_new_evidence",
            "reject_for_now",
        }
        for forbidden in ("buy", "sell", "hold", "watch"):
            assert forbidden not in {label.lower() for label in ALLOWED_COMMITTEE_LABELS}
            assert forbidden not in {s.lower() for s in ALLOWED_FUNDAMENTAL_SETUPS}

    def test_the_council_input_has_no_free_text_context_field(self) -> None:
        """A Council that could be handed unattributed prose would reintroduce exactly
        the failure the ledger removed."""
        fields = set(CouncilInput.__dataclass_fields__)
        assert not (fields & {"context", "summary_text", "notes", "background"})
