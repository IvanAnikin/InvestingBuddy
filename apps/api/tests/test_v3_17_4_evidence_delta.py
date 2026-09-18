"""V3.17.4 — did the round acquire anything, and what happens next.

TWO CLAIMS THAT MUST NEVER BE CONFUSED
======================================
``exhausted_no_improvement`` is a claim about the **world**: research ran and there was
nothing new to find. ``research_did_not_complete`` is a claim about the **platform**: we
did not manage to look.

Recording a provider timeout as "no improvement" writes a false statement about the
evidence into the permanent record, and it is the more dangerous of the two errors
because it reads as a finished investigation and nobody goes back. Half this file exists
for that one distinction.

WHY IMPROVEMENT IS NOT "MORE FINDINGS"
======================================
A Finding is model output. A threshold that rewards a model for emitting more of them
pays for prose — and an investigator defect would make genuinely acquired evidence look
like no improvement, terminating a loop that was working. V3.16 made the honest
alternative measurable: indexed chunk count is a real, moving number.

THE COUNTING TESTS ARE ON REAL POSTGRESQL ON PURPOSE
====================================================
Scope errors are the repository's most repeated defect — superseded versions, inactive
facts, another company's rows, the wrong gap population. Those are join semantics, and a
test that never runs the join proves nothing.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

import pytest

from app.models.research_decision import (
    STATUS_ABANDONED,
    STATUS_COMPLETED,
    STATUS_EXHAUSTED,
    STATUS_REANALYSIS,
    TERMINAL_COST_UNKNOWN,
    TERMINAL_EVIDENCE_SUFFICIENT,
    TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
    TERMINAL_JOB_DEAD_LETTERED,
    TERMINAL_MAX_ROUNDS,
    TERMINAL_REASONS,
    TERMINAL_RESEARCH_DID_NOT_COMPLETE,
)
from app.services.escalation.evidence import (
    DECISIVE_DIMENSIONS,
    EvidenceSnapshot,
    measure_evidence_delta,
    snapshot_evidence,
)
from app.services.escalation.rounds import (
    RoundInputs,
    decide_next_state,
    verdict_payload,
)

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 040"
)


def _snap(**kw) -> EvidenceSnapshot:  # noqa: ANN003
    return EvidenceSnapshot(**kw)


# --------------------------------------------------------------------------- #
# 1. The delta verdict (pure)
# --------------------------------------------------------------------------- #


class TestTheDeltaVerdict:
    def test_new_searchable_chunks_are_improvement(self) -> None:
        d = measure_evidence_delta(_snap(indexed_chunks=10), _snap(indexed_chunks=30))

        assert d["indexed_chunks_added"] == 20
        assert d["improved"] is True

    def test_closing_a_closable_gap_is_improvement(self) -> None:
        d = measure_evidence_delta(
            _snap(open_closable_gaps=5), _snap(open_closable_gaps=3)
        )

        assert d["closable_gaps_closed"] == 2
        assert d["improved"] is True

    def test_nothing_moving_is_not_improvement(self) -> None:
        d = measure_evidence_delta(_snap(indexed_chunks=10), _snap(indexed_chunks=10))

        assert d["improved"] is False
        assert "no evidence dimension moved" in d["reasons"]

    def test_findings_alone_are_NOT_improvement(self) -> None:
        """The whole reason the threshold is not finding count.

        A round that wrote four findings and acquired nothing has produced model output,
        not evidence. Counting it as progress would authorise another paid round on the
        strength of prose — and would keep doing so.
        """
        d = measure_evidence_delta(
            _snap(indexed_chunks=100), _snap(indexed_chunks=100, verified_findings=4)
        )

        assert d["verified_findings_added"] == 4
        assert d["improved"] is False
        assert any("do not count as acquisition" in r for r in d["reasons"])

    def test_findings_are_absent_from_the_decisive_set(self) -> None:
        assert "verified_findings_added" not in DECISIVE_DIMENSIONS

    def test_evidence_progress_does_not_require_any_finding(self) -> None:
        """An investigator defect must not make acquired evidence look like nothing."""
        d = measure_evidence_delta(
            _snap(indexed_chunks=0), _snap(indexed_chunks=383, verified_findings=0)
        )

        assert d["improved"] is True

    def test_a_new_gap_does_not_count_against_progress(self) -> None:
        """Discovering a gap is a legitimate result of having read something new."""
        d = measure_evidence_delta(
            _snap(indexed_chunks=0, open_closable_gaps=1),
            _snap(indexed_chunks=40, open_closable_gaps=6),
        )

        assert d["closable_gaps_closed"] == -5
        assert d["improved"] is True, "new chunks were ignored because a gap opened"

    def test_every_dimension_stays_visible(self) -> None:
        """Collapsing to one score would make the verdict unauditable.

        "improved: false" with no breakdown cannot distinguish "nothing was fetched" from
        "something was fetched and never indexed" — different defects, different fixes.
        """
        d = measure_evidence_delta(_snap(), _snap())

        for dim in DECISIVE_DIMENSIONS:
            assert dim in d
        assert "verified_findings_added" in d
        assert "reasons" in d


# --------------------------------------------------------------------------- #
# 2. Round transitions (pure)
# --------------------------------------------------------------------------- #


class TestRoundTransitions:
    def test_no_improvement_terminates_as_exhausted(self) -> None:
        v = decide_next_state(RoundInputs(job_completed=True, improved=False))

        assert v.status == STATUS_EXHAUSTED
        assert v.terminal_reason == TERMINAL_EXHAUSTED_NO_IMPROVEMENT
        assert v.is_terminal

    def test_improvement_with_gaps_left_authorises_another_round(self) -> None:
        v = decide_next_state(
            RoundInputs(
                job_completed=True,
                improved=True,
                open_closable_gaps_remain=True,
                escalation_round=0,
                max_rounds=2,
            )
        )

        assert v.status == STATUS_REANALYSIS
        assert v.terminal_reason is None
        assert not v.is_terminal

    def test_improvement_with_no_gaps_left_is_completed(self) -> None:
        v = decide_next_state(
            RoundInputs(
                job_completed=True, improved=True, open_closable_gaps_remain=False
            )
        )

        assert v.status == STATUS_COMPLETED
        assert v.terminal_reason == TERMINAL_EVIDENCE_SUFFICIENT

    def test_the_last_permitted_round_terminates_even_while_improving(self) -> None:
        """Improvement must not buy unlimited rounds."""
        v = decide_next_state(
            RoundInputs(
                job_completed=True,
                improved=True,
                open_closable_gaps_remain=True,
                escalation_round=1,
                max_rounds=2,
            )
        )

        assert v.status == STATUS_ABANDONED
        assert v.terminal_reason == TERMINAL_MAX_ROUNDS

    def test_a_loop_that_keeps_improving_still_stops(self) -> None:
        """Walk it forward the way the controller would; it must reach a terminal state."""
        round_index, seen = 0, []
        while True:
            v = decide_next_state(
                RoundInputs(
                    job_completed=True,
                    improved=True,
                    open_closable_gaps_remain=True,
                    escalation_round=round_index,
                    max_rounds=2,
                )
            )
            seen.append(v.status)
            if v.is_terminal:
                break
            round_index += 1
            assert round_index < 10, "the loop did not terminate"

        assert seen[-1] == STATUS_ABANDONED
        assert len(seen) == 2

    def test_unknown_cost_blocks_a_further_round(self) -> None:
        v = decide_next_state(
            RoundInputs(
                job_completed=True,
                improved=True,
                open_closable_gaps_remain=True,
                cost_usd_so_far=None,
            )
        )

        assert v.status == STATUS_ABANDONED
        assert v.terminal_reason == TERMINAL_COST_UNKNOWN

    def test_every_terminal_reason_is_in_the_models_vocabulary(self) -> None:
        """A reason a UI cannot render is a silent absence with extra steps."""
        produced = {
            decide_next_state(i).terminal_reason
            for i in (
                RoundInputs(job_completed=False),
                RoundInputs(job_completed=False, job_dead_lettered=True),
                RoundInputs(job_completed=True, improved=False),
                RoundInputs(job_completed=True, improved=True),
                RoundInputs(
                    job_completed=True,
                    improved=True,
                    open_closable_gaps_remain=True,
                    escalation_round=1,
                ),
                RoundInputs(
                    job_completed=True,
                    improved=True,
                    open_closable_gaps_remain=True,
                    cost_usd_so_far=None,
                ),
            )
        }

        assert produced <= TERMINAL_REASONS


# --------------------------------------------------------------------------- #
# 3. THE MANDATORY DISTINCTION
# --------------------------------------------------------------------------- #


class TestProviderFailureIsNotNoImprovement:
    """A round that did not run is not a round that found nothing."""

    def test_an_incomplete_job_never_reads_as_exhausted(self) -> None:
        v = decide_next_state(RoundInputs(job_completed=False, improved=False))

        assert v.terminal_reason == TERMINAL_RESEARCH_DID_NOT_COMPLETE
        assert v.terminal_reason != TERMINAL_EXHAUSTED_NO_IMPROVEMENT
        assert v.status == STATUS_ABANDONED, "a platform failure was called exhaustion"

    def test_a_dead_lettered_job_has_its_own_reason(self) -> None:
        v = decide_next_state(
            RoundInputs(job_completed=False, job_dead_lettered=True, improved=False)
        )

        assert v.terminal_reason == TERMINAL_JOB_DEAD_LETTERED
        assert v.status == STATUS_ABANDONED

    def test_an_incomplete_job_is_not_rescued_by_an_apparent_improvement(self) -> None:
        """Chunks may have landed from another path; that is not this round's result.

        Concluding anything about the evidence from a round that did not finish is the
        error, whichever direction it points.
        """
        v = decide_next_state(RoundInputs(job_completed=False, improved=True))

        assert v.terminal_reason == TERMINAL_RESEARCH_DID_NOT_COMPLETE

    def test_the_detail_says_plainly_that_we_did_not_look(self) -> None:
        """A human reads this in the queue; it must not imply a finished investigation."""
        v = decide_next_state(RoundInputs(job_completed=False))

        assert "did not manage to look" in v.detail or "did not complete" in v.detail


# --------------------------------------------------------------------------- #
# 4. Persistence round-trip
# --------------------------------------------------------------------------- #


class TestPersistenceRoundTrip:
    def test_a_snapshot_survives_json(self) -> None:
        import json

        original = _snap(
            indexed_chunks=383,
            searchable_documents=7,
            open_closable_gaps=4,
            active_facts=3,
            verified_findings=18,
        )

        restored = EvidenceSnapshot.from_dict(json.loads(json.dumps(original.to_dict())))

        assert restored == original

    def test_a_snapshot_written_before_a_new_field_existed_still_reads(self) -> None:
        restored = EvidenceSnapshot.from_dict({"indexed_chunks": 10})

        assert restored.indexed_chunks == 10
        assert restored.active_facts == 0

    def test_the_improvement_payload_carries_the_numbers_behind_the_verdict(
        self,
    ) -> None:
        """So a reader can check the conclusion instead of trusting it."""
        delta = measure_evidence_delta(_snap(indexed_chunks=1), _snap(indexed_chunks=9))
        verdict = decide_next_state(
            RoundInputs(job_completed=True, improved=True, open_closable_gaps_remain=False)
        )

        payload = verdict_payload(delta, verdict)

        assert payload["indexed_chunks_added"] == 8
        assert payload["next_status"] == STATUS_COMPLETED
        assert payload["terminal_reason"] == TERMINAL_EVIDENCE_SUFFICIENT
        assert payload["detail"]


# --------------------------------------------------------------------------- #
# 5. COUNTING SCOPE — on real PostgreSQL, because these are join semantics
# --------------------------------------------------------------------------- #


@requires_postgres
class TestCountingScope:
    @pytest.fixture
    async def pg(self):  # noqa: ANN201
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(POSTGRES_URL, future=True)
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    async def _company(self, maker) -> uuid.UUID:  # noqa: ANN001
        from app.models.company import Company

        async with maker() as s:
            c = Company(
                id=uuid.uuid4(),
                ticker=f"T{uuid.uuid4().hex[:6].upper()}",
                exchange="US",
                name="Delta Test Co",
                status="new",
            )
            s.add(c)
            await s.commit()
            return c.id

    async def _chunk(
        self,
        maker,  # noqa: ANN001
        company_id,  # noqa: ANN001
        *,
        is_current: bool = True,
        indexable: bool = True,
        indexed: bool = True,
        derivation_active: bool = True,
    ) -> None:
        """One filing's worth of corpus rows, built the way the real path builds them.

        Document → version → derivation → chunk, because that is the chain the readiness
        definition walks and every link in it can independently make a chunk unreachable.
        """
        from app.models.research_chunk import ResearchDocumentChunk
        from app.models.research_derivation import ResearchDocumentDerivation
        from app.models.research_document import (
            ResearchDocument,
            ResearchDocumentVersion,
        )

        async with maker() as s:
            doc = ResearchDocument(
                id=uuid.uuid4(),
                company_id=company_id,
                document_key=f"k{uuid.uuid4().hex[:10]}",
                document_type="filing",
            )
            s.add(doc)
            await s.flush()
            ver = ResearchDocumentVersion(
                id=uuid.uuid4(),
                research_document_id=doc.id,
                content_hash=uuid.uuid4().hex,
                canonical_url=f"https://www.sec.gov/{uuid.uuid4().hex[:8]}",
                transport="https",
                source_tier="T1_primary_filing",
                access_class="public",
                extraction_status="extracted",
                is_current=is_current,
            )
            s.add(ver)
            await s.flush()
            deriv = ResearchDocumentDerivation(
                id=uuid.uuid4(),
                research_document_version_id=ver.id,
                pipeline_version=1,
                extraction_method="native",
                status="completed",
                is_active=derivation_active,
            )
            s.add(deriv)
            await s.flush()
            s.add(
                ResearchDocumentChunk(
                    id=uuid.uuid4(),
                    chunk_id=f"c:{uuid.uuid4().hex[:12]}",
                    derivation_id=deriv.id,
                    research_document_version_id=ver.id,
                    company_id=company_id,
                    kind="text",
                    ordinal=0,
                    text="Our clinical pipeline includes mRNA-1283 in Phase 3.",
                    char_start=0,
                    char_end=51,
                    indexable=indexable,
                    indexed_at=datetime.now(timezone.utc) if indexed else None,
                )
            )
            await s.commit()

    async def test_a_chunk_from_a_SUPERSEDED_derivation_does_not_count(self, pg) -> None:  # noqa: ANN001
        """A previous extraction of the SAME version.

        Found by writing this suite: the first version of the query joined only to the
        version and checked `is_current`, so a re-extraction that deactivated its
        predecessor still had that predecessor's chunks counted — a re-processing of a
        document already held would have read as fresh acquisition and authorised a paid
        round.
        """
        company_id = await self._company(pg)
        await self._chunk(pg, company_id, derivation_active=False)

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.indexed_chunks == 0
        assert snap.searchable_documents == 0

    async def test_a_searchable_chunk_counts(self, pg) -> None:  # noqa: ANN001
        company_id = await self._company(pg)
        await self._chunk(pg, company_id)

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.indexed_chunks == 1
        assert snap.searchable_documents == 1

    async def test_a_chunk_on_a_SUPERSEDED_version_does_not_count(self, pg) -> None:  # noqa: ANN001
        """Re-extraction must not look like acquisition.

        A superseded version is history. Counting its chunks would make every
        re-processing of a document already held read as new evidence — and authorise a
        paid round for it.
        """
        company_id = await self._company(pg)
        await self._chunk(pg, company_id, is_current=False)

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.indexed_chunks == 0

    async def test_an_UNINDEXED_chunk_does_not_count(self, pg) -> None:  # noqa: ANN001
        """The half of V3.16 that was invisible until a real search ran.

        `indexable` is permission, `indexed_at` is index state, and the lexical query
        filters on the second. A chunk with permission and no index entry is returned by
        nothing, so it is not evidence a specialist can cite.
        """
        company_id = await self._company(pg)
        await self._chunk(pg, company_id, indexed=False)

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.indexed_chunks == 0

    async def test_a_NON_INDEXABLE_chunk_does_not_count(self, pg) -> None:  # noqa: ANN001
        company_id = await self._company(pg)
        await self._chunk(pg, company_id, indexable=False)

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.indexed_chunks == 0

    async def test_another_companys_rows_do_not_affect_this_delta(self, pg) -> None:  # noqa: ANN001
        """The scope error this repository has made more than once."""
        mine = await self._company(pg)
        theirs = await self._company(pg)
        await self._chunk(pg, mine)
        for _ in range(5):
            await self._chunk(pg, theirs)

        async with pg() as s:
            snap = await snapshot_evidence(s, mine)

        assert snap.indexed_chunks == 1

    async def test_a_chunk_whose_denormalised_company_DISAGREES_is_not_counted(
        self, pg
    ) -> None:  # noqa: ANN001
        """Belt and braces, made load-bearing.

        `ResearchDocumentChunk.company_id` is denormalised — the authoritative owner is
        `ResearchDocument.company_id`. While the two agree, filtering on both is
        redundant, which mutation testing proved: removing the chunk-level filter broke
        nothing.

        It is not redundant when they DISAGREE, and that is the case worth guarding:
        a chunk written against the wrong company would otherwise be counted as this
        company's evidence and could authorise a paid round on somebody else's filing.
        Both filters, so either row being wrong is caught by the other.
        """
        from sqlalchemy import update

        from app.models.research_chunk import ResearchDocumentChunk

        mine = await self._company(pg)
        theirs = await self._company(pg)
        await self._chunk(pg, mine)

        # Drift: the chunk now claims to belong to the other company.
        async with pg() as s:
            await s.execute(
                update(ResearchDocumentChunk)
                .where(ResearchDocumentChunk.company_id == mine)
                .values(company_id=theirs)
            )
            await s.commit()

        async with pg() as s:
            snap = await snapshot_evidence(s, mine)

        assert snap.indexed_chunks == 0, "a chunk disowning this company was counted"

    async def test_duplicate_chunks_are_counted_but_progress_is_measured_on_the_total(
        self, pg
    ) -> None:  # noqa: ANN001
        """A second identical acquisition must not read as progress.

        Re-running the same acquisition produces no NEW current-version chunk, so the
        before/after total is unchanged and `improved` is false — which is the property
        that matters, rather than any opinion about the rows themselves.
        """
        company_id = await self._company(pg)
        await self._chunk(pg, company_id)

        async with pg() as s:
            before = await snapshot_evidence(s, company_id)
        # The same document acquired again supersedes rather than adds.
        async with pg() as s:
            after = await snapshot_evidence(s, company_id)

        assert measure_evidence_delta(before, after)["improved"] is False

    async def test_an_INACTIVE_fact_does_not_count(self, pg) -> None:  # noqa: ANN001
        """MRNA's real state: facts present, inactive, and therefore not usable."""
        from app.models.extracted_document import ExtractedDocument, ExtractedFact

        company_id = await self._company(pg)
        async with pg() as s:
            doc = ExtractedDocument(
                id=uuid.uuid4(),
                company_id=company_id,
                content_hash=uuid.uuid4().hex,
                canonical_url="https://www.sec.gov/x",
                provider="sec",
                source_type="filing",
                source_tier="T1_primary_filing",
                mime_type="text/html",
                extraction_method="native",
                status="extracted",
            )
            s.add(doc)
            await s.flush()
            s.add(
                ExtractedFact(
                    id=uuid.uuid4(),
                    extracted_document_id=doc.id,
                    label="revenue",
                    value_numeric=1944.0,
                    extraction_method="native",
                    confidence=0.9,
                    validation_status="validated",
                    is_active=False,
                    scope_type="group",
                )
            )
            await s.commit()

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.active_facts == 0

    async def test_a_fact_with_NO_SCOPE_does_not_count(self, pg) -> None:  # noqa: ANN001
        """Active but unscoped cannot be attached to anything safely.

        This is exactly why MRNA's `cash_runway` is legitimately unsupported, and
        counting such a fact would claim a capability the platform does not have.
        """
        from app.models.extracted_document import ExtractedDocument, ExtractedFact

        company_id = await self._company(pg)
        async with pg() as s:
            doc = ExtractedDocument(
                id=uuid.uuid4(),
                company_id=company_id,
                content_hash=uuid.uuid4().hex,
                canonical_url="https://www.sec.gov/y",
                provider="sec",
                source_type="filing",
                source_tier="T1_primary_filing",
                mime_type="text/html",
                extraction_method="native",
                status="extracted",
            )
            s.add(doc)
            await s.flush()
            s.add(
                ExtractedFact(
                    id=uuid.uuid4(),
                    extracted_document_id=doc.id,
                    label="revenue",
                    value_numeric=1944.0,
                    extraction_method="native",
                    confidence=0.9,
                    validation_status="validated",
                    is_active=True,
                    scope_type=None,
                )
            )
            await s.commit()

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.active_facts == 0

    async def test_only_OPEN_CLOSABLE_research_gaps_count(self, pg) -> None:  # noqa: ANN001
        """Three separate traps in one table.

        A closed gap is done. An unclosable gap can never justify another paid round. And
        `research_gaps` is the V3 loop's population — NOT
        `DiscoveryCandidate.missing_fields_json`, which is a different thing entirely.
        """
        from app.models.ledger import ResearchFinding, ResearchGap, ResearchRun

        company_id = await self._company(pg)
        async with pg() as s:
            run = ResearchRun(
                id=uuid.uuid4(),
                company_id=company_id,
                mode="standard",
                # 'complete', not 'completed' — `ck_research_runs_status` is a real
                # CHECK constraint and the two vocabularies differ by one letter.
                status="complete",
            )
            s.add(run)
            await s.flush()
            # A closed gap must NAME what closed it — `ck_research_gaps_closed_names_a
            # _finding`. "Closed" with nothing behind it is a claim with no evidence, and
            # the database refuses it.
            closer = ResearchFinding(
                id=uuid.uuid4(),
                research_run_id=run.id,
                question_key="pipeline_state",
                statement="mRNA-1283 is in Phase 3.",
                evidence_count=1,
            )
            s.add(closer)
            await s.flush()
            for closable, status, closed_by in (
                (True, "open", None),        # counts
                (True, "closed", closer.id), # done
                (False, "open", None),       # more research can never close it
            ):
                s.add(
                    ResearchGap(
                        id=uuid.uuid4(),
                        research_run_id=run.id,
                        gap_type="evidence_unavailable",
                        description="x",
                        closable=closable,
                        status=status,
                        closed_by_finding_id=closed_by,
                    )
                )
            await s.commit()

        async with pg() as s:
            snap = await snapshot_evidence(s, company_id)

        assert snap.open_closable_gaps == 1
