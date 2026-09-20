"""V3.17.8 — a round is measured against what was there before it, or not at all.

THE DEFECT THIS FILE EXISTS FOR
===============================
``evidence_before_json`` was written at exactly one place — ``complete_round``, for the
*next* round. ``create_decision`` built the row without it. So every **round 0** in
production was measured against ``EvidenceSnapshot.from_dict(None)``: all zeros. A
company holding a corpus the platform had spent weeks acquiring recorded that entire
corpus as this round's acquisition, and the arithmetic tell reached the database:

    closable_gaps_closed: -14, -16, -28, -32        improved: true

A negative count of gaps *closed* is not a small error in a number; it is a number that
does not mean anything. It is ``0 − 14``, the signature of a baseline that was never
taken. All four decisions stopped at ``cost_unknown`` regardless, which is the only
reason this cost nothing — **the moment a price book exists, a round that acquired
nothing buys another one.**

WHAT IS ACTUALLY BEING PROVEN HERE
==================================
Not "a column is populated". A test asserting ``evidence_before_json is not None`` would
pass against a baseline captured at the wrong instant, which is the same bug with better
paperwork. The claims are:

1. the baseline is on the row **before anything exists that could execute the round**;
2. evidence the platform already held is **never** counted as this round's acquisition;
3. a round that acquired nothing reports ``improved: false``;
4. an unmeasurable round reports **no numbers at all**, rather than zeros that a reader
   cannot distinguish from a measurement;
5. no repair path — recovery, reconciliation, restart — may invent a baseline for work
   that has already run, or overwrite one that exists.

THE COUNTING CLAIMS RUN ON REAL POSTGRESQL
==========================================
Claims 2 and 3 are join semantics over documents, versions, derivations, chunks, facts
and gaps, and this repository's most repeated defect is a scope error in exactly those
joins. A test that never runs the join proves nothing about them.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.research_decision import (
    STATUS_ABANDONED,
    STATUS_EXHAUSTED,
    STATUS_QUEUED,
    STATUS_REANALYSIS,
    STATUS_RESEARCH_REQUIRED,
    TERMINAL_EVIDENCE_BASELINE_MISSING,
    TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
    TERMINAL_MAX_ROUNDS,
    TERMINAL_REASONS,
    ResearchDecision,
)
from app.services.escalation import store
from app.services.escalation.controller import (
    CandidateFacts,
    complete_round,
    escalate_discovery_run,
    recover_stranded_decisions,
    round_idempotency_key,
)
from app.services.escalation.evidence import (
    EvidenceSnapshot,
    measure_evidence_delta,
    snapshot_evidence,
    unmeasurable_delta,
)
from app.services.escalation.rounds import RoundInputs, decide_next_state

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 040"
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@dataclass
class _Flags:
    v3_research_escalation_enabled: bool = True
    v3_durable_jobs_enabled: bool = True
    v3_escalation_max_rounds: int = 2
    v3_escalation_max_decisions_per_run: int = 5
    #: 0.0 means "no cap configured", which is the production state. The spend-safety
    #: class below sets a real one.
    v3_escalation_cost_cap_usd: float = 0.0


@pytest.fixture
def flags(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    import app.core.config as config

    current = _Flags()
    for name in (
        "v3_research_escalation_enabled",
        "v3_durable_jobs_enabled",
        "v3_escalation_max_rounds",
        "v3_escalation_max_decisions_per_run",
        "v3_escalation_cost_cap_usd",
    ):
        monkeypatch.setattr(config.settings, name, getattr(current, name), raising=False)
    return current


def _set(monkeypatch: pytest.MonkeyPatch, **over) -> None:  # noqa: ANN003
    import app.core.config as config

    for k, v in over.items():
        monkeypatch.setattr(config.settings, k, v, raising=False)


class _RecordingQueue:
    """Stands in for `research_jobs`, and records the BASELINE AS IT WAS AT ENQUEUE TIME.

    That last part is the point of this class in this file. Snapshotting a copy of
    ``evidence_before_json`` at the instant the enqueue happens is what makes "the
    baseline was captured before the job existed" an assertion rather than a hope: a
    mutation that moves the capture below the enqueue leaves this list holding ``None``,
    however correct the row looks once the function returns.
    """

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    async def __call__(self, session, decision):  # noqa: ANN001
        job_id = uuid.uuid4()
        raw = decision.evidence_before_json
        self.jobs.append(
            {
                "job_id": job_id,
                "company_id": decision.company_id,
                "decision_id": decision.id,
                "baseline_at_enqueue": dict(raw) if raw else raw,
            }
        )
        return job_id


class _RealJobQueue(_RecordingQueue):
    """Records the same things, and writes a real ``research_jobs`` row.

    Necessary on PostgreSQL and useful everywhere: ``research_decisions.last_job_id`` is
    a FOREIGN KEY, and a fabricated id is accepted only because the SQLite suite runs
    with foreign keys off. The key is the deterministic one the production path uses, so
    a second enqueue of the same round joins rather than duplicating — the property
    recovery depends on.
    """

    async def __call__(self, session, decision):  # noqa: ANN001
        from app.models.research_job import ResearchJob

        key = round_idempotency_key(decision.id, int(decision.escalation_round or 0))
        existing = (
            await session.execute(
                select(ResearchJob).where(ResearchJob.idempotency_key == key)
            )
        ).scalars().first()
        if existing is None:
            existing = ResearchJob(
                id=uuid.uuid4(),
                job_type="company_research",
                idempotency_key=key,
                company_id=decision.company_id,
                status="pending",
                payload_json={"escalation": {"decision_id": str(decision.id)}},
            )
            session.add(existing)
            await session.flush()
        raw = decision.evidence_before_json
        self.jobs.append(
            {
                "job_id": existing.id,
                "company_id": decision.company_id,
                "decision_id": decision.id,
                "baseline_at_enqueue": dict(raw) if raw else raw,
            }
        )
        return existing.id


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


async def _company(session, ticker: str | None = None):  # noqa: ANN001, ANN201
    from app.models.company import Company

    company = Company(
        id=uuid.uuid4(),
        ticker=ticker or f"T{uuid.uuid4().hex[:6].upper()}",
        exchange="US",
        name="Baseline Test Co",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company.id


def _candidate(company_id, **over) -> CandidateFacts:  # noqa: ANN001, ANN003
    base = {
        "candidate_id": uuid.uuid4(),
        "company_id": company_id,
        "coerced_action": "research_next",
        "blocking_gap_count": 3,
        "confidence": 0.4,
    }
    base.update(over)
    return CandidateFacts(**base)


async def _decision(session, company_id, **over):  # noqa: ANN001, ANN003
    """A decision built the supported way, carrying a real baseline."""
    return await store.create_decision(
        session,
        company_id=company_id,
        discovery_run_id=over.pop("run_id", uuid.uuid4()),
        discovery_candidate_id=None,
        source="discovery_council",
        decision="research_next",
        reason="test",
        max_rounds=over.pop("max_rounds", 2),
        evidence_before=over.pop(
            "evidence_before", (await snapshot_evidence(session, company_id)).to_dict()
        ),
        **over,
    )


async def _legacy_decision(session, company_id, **over):  # noqa: ANN001, ANN003
    """A decision as V3.17.1–7 wrote it: open, and with NO baseline.

    Built by inserting the row directly, because ``store.create_decision`` now refuses
    to produce one — which is itself the guarantee under test, and the reason this
    helper has to reach past it. Every research decision currently in production has
    this shape, so the repair paths will meet it.
    """
    row = ResearchDecision(
        id=uuid.uuid4(),
        company_id=company_id,
        discovery_run_id=over.pop("run_id", uuid.uuid4()),
        discovery_candidate_id=None,
        source="discovery_council",
        decision="research_next",
        status=over.pop("status", STATUS_RESEARCH_REQUIRED),
        reason="created before the baseline was captured",
        priority=100,
        escalation_round=over.pop("escalation_round", 0),
        max_rounds=over.pop("max_rounds", 2),
        evidence_before_json=None,
        **over,
    )
    session.add(row)
    await session.flush()
    return row


# --------------------------------------------------------------------------- #
# 1. The shape of an answer — pure, so every branch is visible
# --------------------------------------------------------------------------- #


class TestAMissingBaselineIsNotABaselineOfZero:
    """The substitution that produced `-14`, stated as a property.

    ``from_dict`` is tolerant by design and keeps its tolerance — a snapshot missing a
    key that did not exist when it was written is still a usable snapshot. What this
    class fixes is that a missing *snapshot* now has its own answer, and the code that
    decides whether to spend money asks for that answer specifically.
    """

    def test_no_snapshot_reads_as_no_snapshot(self) -> None:
        assert EvidenceSnapshot.persisted(None) is None

    def test_an_empty_object_is_not_a_measurement_either(self) -> None:
        """`{}` is the other way a baseline goes missing, and it is falsy in the same way.

        A row updated by hand, a payload that lost its keys, a default that was never
        filled: the record shows an object, and reading it as five zeros would be the
        original defect with a JSON wrapper.
        """
        assert EvidenceSnapshot.persisted({}) is None

    def test_a_MEASURED_zero_is_a_real_baseline_and_is_kept(self) -> None:
        """The distinction that stops this from being a blunt "is it truthy" check.

        A company the platform has never researched genuinely has no evidence. That is a
        measurement, it is correct, and a round against it may legitimately report every
        chunk it acquires as new. Rejecting it would make a first-ever research round
        unmeasurable and stop the loop before it started.
        """
        snap = EvidenceSnapshot.persisted(
            {
                "indexed_chunks": 0,
                "searchable_documents": 0,
                "open_closable_gaps": 0,
                "active_facts": 0,
                "verified_findings": 0,
            }
        )

        assert snap == EvidenceSnapshot()

    def test_the_tolerant_reader_still_exists_for_the_case_it_was_written_for(
        self,
    ) -> None:
        snap = EvidenceSnapshot.from_dict({"indexed_chunks": 7})

        assert snap.indexed_chunks == 7
        assert snap.active_facts == 0


class TestGapsOpenedAreNotNegativeGapsClosed:
    """Requirement 5, at the arithmetic level."""

    def test_a_round_that_discovers_gaps_reports_them_as_discovered(self) -> None:
        d = measure_evidence_delta(
            EvidenceSnapshot(open_closable_gaps=4),
            EvidenceSnapshot(open_closable_gaps=18),
        )

        assert d["closable_gaps_closed"] == 0
        assert d["closable_gaps_opened"] == 14

    def test_no_dimension_may_go_negative_in_the_gap_direction(self) -> None:
        """The production numbers, as a property rather than four examples."""
        for before, after in ((4, 18), (0, 32), (7, 7), (9, 2)):
            d = measure_evidence_delta(
                EvidenceSnapshot(open_closable_gaps=before),
                EvidenceSnapshot(open_closable_gaps=after),
            )

            assert d["closable_gaps_closed"] >= 0, (before, after)
            assert d["closable_gaps_opened"] >= 0, (before, after)

    def test_closing_gaps_still_reads_as_closing_gaps(self) -> None:
        d = measure_evidence_delta(
            EvidenceSnapshot(open_closable_gaps=9),
            EvidenceSnapshot(open_closable_gaps=2),
        )

        assert d["closable_gaps_closed"] == 7
        assert d["closable_gaps_opened"] == 0
        assert d["improved"] is True

    def test_a_discovered_gap_is_explained_rather_than_left_as_a_bare_number(
        self,
    ) -> None:
        d = measure_evidence_delta(
            EvidenceSnapshot(indexed_chunks=0, open_closable_gaps=1),
            EvidenceSnapshot(indexed_chunks=40, open_closable_gaps=6),
        )

        assert any("not counted against improvement" in r for r in d["reasons"])


class TestAnUnmeasurableRoundReportsNoNumbers:
    def test_it_carries_no_numeric_dimension_at_all(self) -> None:
        """Zeros would be indistinguishable from a round that acquired nothing.

        Both would render "0 searchable chunk(s)" to an operator. One of those is a
        measurement and the other is the absence of one, and they lead to different
        actions — retry the research, versus fix the platform.
        """
        d = unmeasurable_delta("no baseline")

        for key in (
            "indexed_chunks_added",
            "searchable_documents_added",
            "closable_gaps_closed",
            "closable_gaps_opened",
            "facts_added",
        ):
            assert key not in d, f"{key} was reported without a baseline to measure it"

    def test_it_says_so_in_words(self) -> None:
        d = unmeasurable_delta("because the baseline is absent")

        assert d["measurable"] is False
        assert d["improved"] is False
        assert d["reasons"] == ["because the baseline is absent"]

    def test_a_measured_delta_declares_itself_measured(self) -> None:
        d = measure_evidence_delta(EvidenceSnapshot(), EvidenceSnapshot())

        assert d["measurable"] is True


class TestTheStateMachineRefusesToGuess:
    def test_a_round_with_no_baseline_is_never_called_exhausted(self) -> None:
        """`exhausted_no_improvement` is a claim about the WORLD.

        Writing it here would say "research ran and there was nothing new" on the
        strength of a comparison that never happened — a finished-looking investigation
        nobody goes back to.
        """
        v = decide_next_state(
            RoundInputs(job_completed=True, improved=False, evidence_baseline_known=False)
        )

        assert v.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING
        assert v.terminal_reason != TERMINAL_EXHAUSTED_NO_IMPROVEMENT
        assert v.status == STATUS_ABANDONED

    def test_a_round_with_no_baseline_never_authorises_another(self) -> None:
        """The spend-safety claim, in the pure function.

        Everything else about this round says "keep going": it completed, it looks
        improved, gaps remain, a round is left, cost is known and well under the cap. The
        baseline is the only thing missing, and it is enough to stop.
        """
        v = decide_next_state(
            RoundInputs(
                job_completed=True,
                improved=True,
                evidence_baseline_known=False,
                open_closable_gaps_remain=True,
                escalation_round=0,
                max_rounds=3,
                cost_usd_so_far=0.10,
                cost_cap_usd=5.00,
            )
        )

        assert v.status != STATUS_REANALYSIS
        assert v.is_terminal is True
        assert v.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING

    def test_the_platform_question_is_still_asked_first(self) -> None:
        """A job that never ran is reported as a job that never ran.

        Both facts are true of this round — it did not complete AND it has no baseline —
        and the ordering decides which a human is told. "We did not manage to look" is
        the more actionable, and it is not weakened by also being unmeasurable.
        """
        v = decide_next_state(
            RoundInputs(job_completed=False, evidence_baseline_known=False)
        )

        assert v.terminal_reason == "research_did_not_complete"

    def test_the_reason_is_in_the_closed_vocabulary(self) -> None:
        assert TERMINAL_EVIDENCE_BASELINE_MISSING in TERMINAL_REASONS

    def test_a_measured_round_is_unaffected(self) -> None:
        """The default must not quietly change the behaviour of every existing branch."""
        v = decide_next_state(
            RoundInputs(
                job_completed=True,
                improved=True,
                open_closable_gaps_remain=True,
                escalation_round=0,
                max_rounds=2,
                cost_usd_so_far=0.0,
            )
        )

        assert v.status == STATUS_REANALYSIS


# --------------------------------------------------------------------------- #
# 2. Round 0 — the baseline exists before anything can execute the round
# --------------------------------------------------------------------------- #


class TestTheBaselineIsOnTheRowBeforeTheJobExists:
    """Requirement 1, and the mutation tests that keep it true."""

    async def test_the_enqueue_sees_a_baseline_already_on_the_decision(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """THE LOAD-BEARING TEST OF THIS SLICE.

        Not "the column is populated when the call returns" — that passes against a
        capture placed *after* the enqueue, which is the same defect one line later: the
        job is claimable the instant ``JobStore`` commits it, in its own transaction, so
        a round can begin before a capture that happens afterwards.
        """
        company_id = await _company(session)
        queue = _RecordingQueue()

        await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        assert len(queue.jobs) == 1
        assert queue.jobs[0]["baseline_at_enqueue"] is not None, (
            "the round-0 job was made executable before its evidence baseline existed"
        )

    async def test_the_baseline_is_the_real_snapshot_not_a_placeholder(
        self, session, flags
    ) -> None:  # noqa: ANN001
        company_id = await _company(session)
        expected = (await snapshot_evidence(session, company_id)).to_dict()
        queue = _RecordingQueue()

        await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        assert queue.jobs[0]["baseline_at_enqueue"] == expected

    async def test_the_baseline_is_flushed_to_the_row_not_only_held_in_memory(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """A worker reads the database, not this session's identity map."""
        company_id = await _company(session)

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=_RecordingQueue(),
        )

        row = (
            await session.execute(
                select(ResearchDecision.evidence_before_json).where(
                    ResearchDecision.id == outcome.created[0]
                )
            )
        ).scalar_one()
        assert row is not None
        assert "indexed_chunks" in row

    async def test_a_decision_cannot_be_created_without_one(self, session) -> None:  # noqa: ANN001
        """MUTATION: delete the capture.

        Removing the snapshot from `escalate_discovery_run` cannot leave working code,
        because the argument is required and a company decision without it is refused.
        """
        company_id = await _company(session)

        with pytest.raises(store.MissingEvidenceBaseline):
            await store.create_decision(
                session,
                company_id=company_id,
                discovery_run_id=uuid.uuid4(),
                discovery_candidate_id=None,
                source="discovery_council",
                decision="research_next",
                reason="no baseline",
                max_rounds=2,
                evidence_before=None,
            )

    async def test_an_empty_baseline_is_refused_as_firmly_as_a_missing_one(
        self, session
    ) -> None:  # noqa: ANN001
        """MUTATION: substitute `{}` for the genuine snapshot."""
        company_id = await _company(session)

        with pytest.raises(store.MissingEvidenceBaseline):
            await store.create_decision(
                session,
                company_id=company_id,
                discovery_run_id=uuid.uuid4(),
                discovery_candidate_id=None,
                source="discovery_council",
                decision="research_next",
                reason="empty baseline",
                max_rounds=2,
                evidence_before={},
            )

    async def test_no_path_may_enqueue_a_decision_that_lost_its_baseline(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """MUTATION: capture the baseline AFTER the enqueue.

        Simulated by clearing it on a decision that has one and then asking the enqueue
        path to run. Every producer funnels through `_enqueue`, so this is the single
        choke point that makes the invariant hold for paths that do not exist yet.
        """
        from app.services.escalation.controller import _enqueue

        company_id = await _company(session)
        decision = await _decision(session, company_id)
        decision.evidence_before_json = None

        with pytest.raises(store.MissingEvidenceBaseline):
            await _enqueue(session, decision, enqueue=_RecordingQueue())

    async def test_a_decision_with_no_company_is_still_allowed_to_exist(
        self, session
    ) -> None:  # noqa: ANN001
        """The one exemption, and it is narrow: nothing to snapshot, nothing to research.

        Such a decision is closed by `recover_stranded_decisions` rather than queued, so
        exempting it cannot make an unmeasurable round executable.
        """
        decision = await store.create_decision(
            session,
            company_id=None,
            discovery_run_id=uuid.uuid4(),
            discovery_candidate_id=None,
            source="discovery_council",
            decision="research_next",
            reason="never promoted to a company",
            max_rounds=2,
            evidence_before=None,
        )

        assert decision.evidence_before_json is None


# --------------------------------------------------------------------------- #
# 3. Recovery, reconciliation and restart
# --------------------------------------------------------------------------- #


class TestRecoveryMayNotInventABaseline:
    """Requirements 6 and 7.

    The repair paths are the ones that can reintroduce the defect most quietly: they run
    unattended, on a schedule, against rows nobody is looking at, and "snapshot the
    evidence now and call it before" is the obvious-looking fix.
    """

    async def _stranded(self, session, company_id, **over):  # noqa: ANN001, ANN003
        """Open, no job — the state a crash between the two writes leaves."""
        return await _decision(session, company_id, **over)

    async def test_a_decision_that_never_started_gets_a_baseline_then_a_job(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """The legacy row that CAN be repaired truthfully.

        `research_required`, round 0, no job row under its round-0 key: nothing has
        executed, so the evidence as it stands now IS the pre-round evidence. The
        ordering is still baseline-then-job.
        """
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id)
        assert decision.evidence_before_json is None
        queue = _RecordingQueue()

        recovered = await recover_stranded_decisions(session, enqueue=queue)

        assert decision.id in recovered
        assert queue.jobs[0]["baseline_at_enqueue"] is not None
        assert decision.status == STATUS_QUEUED

    async def test_recovery_never_enqueues_a_round_with_a_null_baseline(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """MUTATION: let recovery enqueue whatever it finds.

        Here the decision has already been marked queued once, so the platform cannot
        prove nothing has run. Recovery must not enqueue, and must not snapshot.
        """
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id, status=STATUS_QUEUED)
        queue = _RecordingQueue()

        recovered = await recover_stranded_decisions(session, enqueue=queue)

        assert queue.jobs == [], "recovery queued a paid round with nothing to measure it"
        assert recovered == []
        assert decision.evidence_before_json is None, "recovery invented a baseline"

    async def test_such_a_decision_is_closed_rather_than_left_open_for_ever(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """Failing closed must not become a different permanent outage.

        `ux_research_decisions_one_open` blocks every future decision for a company while
        one is open, so refusing to repair and then walking away would lock the company
        out exactly as the crash would have.
        """
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id, status=STATUS_QUEUED)

        await recover_stranded_decisions(session, enqueue=_RecordingQueue())

        assert decision.status == STATUS_ABANDONED
        assert decision.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING

    async def test_a_later_round_is_never_treated_as_never_started(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """Round 2 exists only because round 1 ran, so "now" cannot be "before"."""
        company_id = await _company(session)
        decision = await _legacy_decision(
            session, company_id, escalation_round=1, max_rounds=3
        )
        queue = _RecordingQueue()

        await recover_stranded_decisions(session, enqueue=queue)

        assert queue.jobs == []
        assert decision.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING

    async def test_an_existing_job_row_proves_the_round_may_already_have_run(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """The case that is not obvious, and the reason the check hits `research_jobs`.

        `JobStore` commits in its own session. A job can therefore exist — claimable, and
        possibly already executed — while the decision that ordered it was rolled back to
        `last_job_id IS NULL`. Snapshotting now would produce an AFTER snapshot wearing a
        "before" label: the exact substitution this slice removes, reintroduced by the
        repair path.
        """
        from app.models.research_job import ResearchJob

        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id)
        session.add(
            ResearchJob(
                id=uuid.uuid4(),
                job_type="company_research",
                idempotency_key=round_idempotency_key(decision.id, 0),
                company_id=company_id,
                status="running",
                payload_json={},
            )
        )
        await session.flush()
        queue = _RecordingQueue()

        await recover_stranded_decisions(session, enqueue=queue)

        assert queue.jobs == []
        assert decision.evidence_before_json is None
        assert decision.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING

    async def test_recovery_does_not_overwrite_a_baseline_that_exists(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """Requirement 7. A repair pass must be inert on a healthy row.

        Re-snapshotting a decision whose job crashed after doing real work would move the
        baseline forward past the evidence that work acquired, and the round would then
        report having acquired none of it.
        """
        company_id = await _company(session)
        decision = await self._stranded(session, company_id)
        decision.evidence_before_json = {
            "indexed_chunks": 203,
            "searchable_documents": 11,
            "open_closable_gaps": 32,
            "active_facts": 85,
            "verified_findings": 18,
        }
        await session.flush()
        original = dict(decision.evidence_before_json)

        await recover_stranded_decisions(session, enqueue=_RecordingQueue())

        assert decision.evidence_before_json == original

    async def test_repeated_sweeps_change_nothing(self, session, flags) -> None:  # noqa: ANN001
        """It runs at startup and on a cadence; thirteen hours of it must be inert."""
        company_id = await _company(session)
        decision = await self._stranded(session, company_id)
        queue = _RecordingQueue()

        first = await recover_stranded_decisions(session, enqueue=queue)
        baseline_after_first = dict(decision.evidence_before_json)
        second = await recover_stranded_decisions(session, enqueue=queue)
        third = await recover_stranded_decisions(session, enqueue=queue)

        assert len(first) == 1
        assert second == [] and third == []
        assert len(queue.jobs) == 1
        assert decision.evidence_before_json == baseline_after_first


class TestReconciliationOfALegacyDecision:
    """A decision created before this slice, whose round has already finished.

    This is the shape of every row already in production. The sweep will meet them.
    """

    async def test_it_is_closed_as_unmeasurable_not_as_exhausted(
        self, session, flags
    ) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id)

        verdict = await complete_round(
            session, decision, job_completed=True, enqueue=_RecordingQueue()
        )

        assert verdict.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING
        assert decision.status == STATUS_ABANDONED

    async def test_it_authorises_no_further_paid_round(self, session, flags) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id)
        queue = _RecordingQueue()

        await complete_round(session, decision, job_completed=True, enqueue=queue)

        assert queue.jobs == []

    async def test_its_after_snapshot_is_still_recorded(self, session, flags) -> None:  # noqa: ANN001
        """The measurement that CAN be taken is taken. Only the delta is withheld."""
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id)

        await complete_round(session, decision, job_completed=True, enqueue=_RecordingQueue())

        assert decision.evidence_after_json is not None
        assert "indexed_chunks" in decision.evidence_after_json

    async def test_it_reports_no_delta_rather_than_a_delta_of_zero(
        self, session, flags
    ) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id)

        await complete_round(session, decision, job_completed=True, enqueue=_RecordingQueue())

        improvement = decision.improvement_json
        assert improvement["measurable"] is False
        assert improvement["improved"] is False
        assert "closable_gaps_closed" not in improvement

    async def test_the_baseline_is_not_backfilled_from_the_after_snapshot(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """MUTATION: repair the row on the way past.

        Tempting, and wrong: the round has finished, so today's evidence is *after*.
        Writing it into `evidence_before_json` would make the historical record assert a
        measurement that was never taken.
        """
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id)

        await complete_round(session, decision, job_completed=True, enqueue=_RecordingQueue())

        assert decision.evidence_before_json is None


class TestTheNextRoundsBaseline:
    """Requirement: later rounds use the previous round's AFTER snapshot. Preserved."""

    async def test_the_next_round_is_measured_from_where_this_one_ended(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _decision(session, company_id, max_rounds=3)
        decision.cost_usd_total = 0.25
        _set(monkeypatch, v3_escalation_cost_cap_usd=5.0)
        # The round runs: it acquires corpus and leaves a gap still open.
        await _corpus(session, company_id, chunks=6)
        await _gap_on_sqlite(session, company_id)
        queue = _RecordingQueue()

        verdict = await complete_round(
            session, decision, job_completed=True, enqueue=queue
        )

        assert verdict.status == STATUS_REANALYSIS
        assert decision.evidence_before_json == decision.evidence_after_json

    async def test_the_next_rounds_job_is_enqueued_after_its_baseline_is_written(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """The same ordering rule as round 0, for round n+1.

        Before V3.17.8 the enqueue came first and the baseline was assigned afterwards.
        The row ended up correct, but between the two the next round's job was already
        claimable while the decision still carried the PREVIOUS round's baseline.
        """
        company_id = await _company(session)
        decision = await _decision(session, company_id, max_rounds=3)
        decision.cost_usd_total = 0.25
        _set(monkeypatch, v3_escalation_cost_cap_usd=5.0)
        await _corpus(session, company_id, chunks=6)
        await _gap_on_sqlite(session, company_id)
        queue = _RecordingQueue()

        await complete_round(session, decision, job_completed=True, enqueue=queue)

        assert len(queue.jobs) == 1
        assert queue.jobs[0]["baseline_at_enqueue"] == decision.evidence_after_json, (
            "round n+1 was queued while the decision still carried round n's baseline"
        )


async def _corpus(session, company_id, *, chunks: int) -> None:  # noqa: ANN001
    """One document's worth of searchable corpus: document → version → derivation → chunk.

    The whole chain, because every link in it can independently make a chunk unreachable
    and `snapshot_evidence` walks all four. Seeding a bare chunk row would count evidence
    a search could never return, which is the false success V3.16 was opened to fix.
    """
    from app.models.research_chunk import ResearchDocumentChunk
    from app.models.research_derivation import ResearchDocumentDerivation
    from app.models.research_document import ResearchDocument, ResearchDocumentVersion

    doc = ResearchDocument(
        id=uuid.uuid4(),
        company_id=company_id,
        document_key=f"k{uuid.uuid4().hex[:10]}",
        document_type="filing",
    )
    session.add(doc)
    await session.flush()
    ver = ResearchDocumentVersion(
        id=uuid.uuid4(),
        research_document_id=doc.id,
        content_hash=uuid.uuid4().hex,
        canonical_url=f"https://www.sec.gov/{uuid.uuid4().hex[:8]}",
        transport="https",
        source_tier="T1_primary_filing",
        access_class="public",
        extraction_status="extracted",
        is_current=True,
    )
    session.add(ver)
    await session.flush()
    deriv = ResearchDocumentDerivation(
        id=uuid.uuid4(),
        research_document_version_id=ver.id,
        pipeline_version=1,
        extraction_method="native",
        status="completed",
        is_active=True,
    )
    session.add(deriv)
    await session.flush()
    for ordinal in range(chunks):
        session.add(
            ResearchDocumentChunk(
                id=uuid.uuid4(),
                chunk_id=f"c:{uuid.uuid4().hex[:12]}",
                derivation_id=deriv.id,
                research_document_version_id=ver.id,
                company_id=company_id,
                kind="text",
                ordinal=ordinal,
                text="Our clinical pipeline includes mRNA-1283 in Phase 3.",
                char_start=0,
                char_end=51,
                indexable=True,
                indexed_at=datetime.now(timezone.utc),
            )
        )
    await session.flush()


async def _gap_on_sqlite(session, company_id, *, closable: bool = True) -> None:  # noqa: ANN001
    """One open, closable research gap, scoped through a run to this company.

    Enough to make `open_closable_gaps_remain` true without a PostgreSQL fixture. The
    COUNTING semantics are proven on real PostgreSQL below; what this supports is the
    state machine.
    """
    from app.models.ledger import ResearchGap, ResearchRun

    run = ResearchRun(
        id=uuid.uuid4(), company_id=company_id, mode="standard", status="complete"
    )
    session.add(run)
    await session.flush()
    session.add(
        ResearchGap(
            id=uuid.uuid4(),
            research_run_id=run.id,
            gap_type="missing_disclosure",
            description="cash runway is not stated in any retrieved filing",
            closable=closable,
            status="open",
        )
    )
    await session.flush()


# --------------------------------------------------------------------------- #
# 4. SPEND SAFETY with a KNOWN cost below the cap — requirement 8
# --------------------------------------------------------------------------- #


class TestSpendSafetyOnceCostIsKnown:
    """What the production masking hides, made visible.

    Every live decision currently stops at `cost_unknown`, so the defect costs nothing
    today. These tests remove that mask deliberately: cost is known, well under a
    configured cap, and rounds remain. The ONLY thing standing between a round that
    acquired nothing and a second paid round is the evidence delta.
    """

    @pytest.fixture(autouse=True)
    def _cap(self, flags, monkeypatch) -> None:  # noqa: ANN001
        _set(monkeypatch, v3_escalation_cost_cap_usd=5.00, v3_escalation_max_rounds=3)

    async def test_a_round_that_acquired_nothing_buys_no_second_round(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """THE SPEND-SAFETY CLAIM.

        Before this slice the same row reported `improved: true`, because the baseline
        was absent and the company's pre-existing corpus counted as acquisition.
        """
        company_id = await _company(session)
        # Evidence the platform acquired WEEKS AGO, before this decision existed. The
        # production rows looked exactly like this: a company with a real corpus and
        # real open gaps.
        await _corpus(session, company_id, chunks=421)
        await _gap_on_sqlite(session, company_id)

        decision = await _decision(session, company_id, max_rounds=3)
        decision.cost_usd_total = 0.40
        # The round runs and acquires nothing. Nothing is seeded between here and
        # `complete_round`.
        queue = _RecordingQueue()

        verdict = await complete_round(
            session, decision, job_completed=True, enqueue=queue
        )

        assert queue.jobs == [], "an unimproved round authorised paid work"
        assert verdict.status == STATUS_EXHAUSTED
        assert decision.terminal_reason == TERMINAL_EXHAUSTED_NO_IMPROVEMENT
        assert decision.improvement_json["improved"] is False

    async def test_real_improvement_buys_exactly_one_next_round(
        self, session, flags
    ) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _decision(session, company_id, max_rounds=2)
        decision.cost_usd_total = 0.40
        # Round 0 genuinely acquires evidence, and leaves a gap open.
        await _corpus(session, company_id, chunks=40)
        await _gap_on_sqlite(session, company_id)
        queue = _RecordingQueue()

        first = await complete_round(session, decision, job_completed=True, enqueue=queue)
        # The authorised round runs and acquires nothing further, measured from where
        # round 0 ended rather than from zero.
        second = await complete_round(session, decision, job_completed=True, enqueue=queue)

        assert first.status == STATUS_REANALYSIS
        assert len(queue.jobs) == 1, "more than one further round was authorised"
        assert second.is_terminal is True
        assert second.terminal_reason in {
            TERMINAL_MAX_ROUNDS,
            TERMINAL_EXHAUSTED_NO_IMPROVEMENT,
        }

    async def test_an_unmeasurable_round_buys_nothing_even_with_budget_left(
        self, session, flags
    ) -> None:  # noqa: ANN001
        company_id = await _company(session)
        decision = await _legacy_decision(session, company_id, max_rounds=3)
        decision.cost_usd_total = 0.40
        await _corpus(session, company_id, chunks=40)
        await _gap_on_sqlite(session, company_id)
        queue = _RecordingQueue()

        verdict = await complete_round(
            session, decision, job_completed=True, enqueue=queue
        )

        assert queue.jobs == []
        assert verdict.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING


# --------------------------------------------------------------------------- #
# 5. REAL POSTGRESQL — the counting, and the locking
#
# Claims 2, 3 and 4 are join semantics over documents, versions, derivations, chunks,
# facts and gaps. SQLite runs this suite with foreign keys off and without row locking,
# so it can demonstrate the control flow and nothing about either.
# --------------------------------------------------------------------------- #


@requires_postgres
class TestOnRealRows:
    @pytest.fixture
    async def pg(self):  # noqa: ANN201
        engine = create_async_engine(POSTGRES_URL, future=True)
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    async def _company_pg(self, maker) -> uuid.UUID:  # noqa: ANN001
        from app.models.company import Company

        async with maker() as s:
            c = Company(
                id=uuid.uuid4(),
                ticker=f"T{uuid.uuid4().hex[:6].upper()}",
                exchange="US",
                name="Round Zero Co",
                status="new",
            )
            s.add(c)
            await s.commit()
            return c.id

    async def _discovery_run_pg(self, maker) -> uuid.UUID:  # noqa: ANN001
        """A real ``discovery_runs`` row.

        PostgreSQL enforces `fk_research_decisions_discovery_run_id_discovery_runs`;
        SQLite runs this suite with foreign keys off and would have accepted a fabricated
        id. That difference is the whole reason these tests are here.
        """
        from app.models.discovery import DiscoveryRun

        async with maker() as s:
            run = DiscoveryRun(id=uuid.uuid4(), status="completed")
            s.add(run)
            await s.commit()
            return run.id

    async def _corpus_pg(self, maker, company_id, *, chunks: int) -> None:  # noqa: ANN001
        async with maker() as s:
            await _corpus(s, company_id, chunks=chunks)
            await s.commit()

    async def _gaps_pg(self, maker, company_id, *, count: int) -> None:  # noqa: ANN001
        async with maker() as s:
            for _ in range(count):
                await _gap_on_sqlite(s, company_id)
            await s.commit()

    async def _escalate(self, maker, company_id) -> uuid.UUID:  # noqa: ANN001
        """The real entry path, with the job injected so no worker is needed.

        The discovery run and candidate are real rows because ``research_decisions``
        carries FOREIGN KEYs to both and PostgreSQL enforces them.
        """
        from app.models.company import Company
        from app.models.discovery import DiscoveryCandidate

        run_id = await self._discovery_run_pg(maker)
        async with maker() as s:
            company = await s.get(Company, company_id)
            candidate = DiscoveryCandidate(
                id=uuid.uuid4(),
                discovery_run_id=run_id,
                ticker=company.ticker,
                exchange=company.exchange,
            )
            s.add(candidate)
            await s.commit()
            candidate_id = candidate.id

        queue = _RealJobQueue()
        async with maker() as s:
            outcome = await escalate_discovery_run(
                s,
                discovery_run_id=run_id,
                candidates=[_candidate(company_id, candidate_id=candidate_id)],
                enqueue=queue,
            )
            await s.commit()
        assert outcome.created, outcome.to_dict()
        return outcome.created[0]

    # --- 5.1 the baseline is real, and it is the real numbers ----------------

    async def test_the_committed_row_carries_the_real_pre_round_snapshot(
        self, pg, flags
    ) -> None:  # noqa: ANN001
        """Requirement 1, against rows a worker in another process would read.

        The numbers matter as much as the presence. A baseline of the right *shape* and
        the wrong *values* is the defect with better paperwork: production had no
        baseline at all, and a placeholder would have been indistinguishable from it once
        the delta was computed.
        """
        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=17)
        await self._gaps_pg(pg, company_id, count=4)

        async with pg() as s:
            expected = (await snapshot_evidence(s, company_id)).to_dict()
        decision_id = await self._escalate(pg, company_id)

        async with pg() as s:
            stored = (
                await s.execute(
                    select(ResearchDecision.evidence_before_json).where(
                        ResearchDecision.id == decision_id
                    )
                )
            ).scalar_one()

        assert stored == expected
        assert stored["indexed_chunks"] == 17
        assert stored["open_closable_gaps"] == 4

    # --- 5.2 pre-existing evidence is not this round's acquisition -----------

    async def test_a_corpus_acquired_weeks_ago_is_not_this_rounds_acquisition(
        self, pg, flags
    ) -> None:  # noqa: ANN001
        """Requirements 2 and 3, and the production regression in one test.

        This is the exact shape of all four live decisions: a company with a substantial
        corpus and many open gaps, researched again, acquiring nothing. Before this slice
        it reported ``improved: true`` and ``closable_gaps_closed: -32``.
        """
        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=383)
        await self._gaps_pg(pg, company_id, count=32)

        decision_id = await self._escalate(pg, company_id)
        queue = _RealJobQueue()
        async with pg() as s:
            decision = await s.get(ResearchDecision, decision_id)
            verdict = await complete_round(
                s, decision, job_completed=True, enqueue=queue
            )
            improvement = dict(decision.improvement_json)
            await s.commit()

        assert improvement["indexed_chunks_added"] == 0, "383 held chunks read as new"
        assert improvement["facts_added"] == 0
        assert improvement["closable_gaps_closed"] == 0
        assert improvement["closable_gaps_opened"] == 0
        assert improvement["improved"] is False
        assert verdict.status == STATUS_EXHAUSTED
        assert queue.jobs == [], "a round that acquired nothing authorised paid work"

    async def test_no_delta_dimension_can_come_back_negative(self, pg, flags) -> None:  # noqa: ANN001
        """The tell itself, as an assertion. `-14` was not a small error; it was a lie."""
        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=12)
        await self._gaps_pg(pg, company_id, count=14)

        decision_id = await self._escalate(pg, company_id)
        # The round runs and discovers MORE gaps than it closes — legitimate, and the
        # direction that used to produce the negative number.
        await self._gaps_pg(pg, company_id, count=9)

        async with pg() as s:
            decision = await s.get(ResearchDecision, decision_id)
            await complete_round(
                s, decision, job_completed=True, enqueue=_RealJobQueue()
            )
            improvement = dict(decision.improvement_json)
            await s.commit()

        assert improvement["closable_gaps_closed"] == 0
        assert improvement["closable_gaps_opened"] == 9
        for key, value in improvement.items():
            if key.endswith(("_added", "_closed", "_opened")):
                assert value >= 0, f"{key} came back negative: {value}"

    # --- 5.3 a genuine delta is exactly the delta ----------------------------

    async def test_only_what_the_round_acquired_is_counted(self, pg, flags) -> None:  # noqa: ANN001
        """Requirement 4. Three chunks held, two acquired, and the answer is two."""
        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=3)

        decision_id = await self._escalate(pg, company_id)
        await self._corpus_pg(pg, company_id, chunks=2)

        async with pg() as s:
            decision = await s.get(ResearchDecision, decision_id)
            await complete_round(
                s, decision, job_completed=True, enqueue=_RealJobQueue()
            )
            improvement = dict(decision.improvement_json)
            before = dict(decision.evidence_before_json)
            after = dict(decision.evidence_after_json)
            await s.commit()

        assert before["indexed_chunks"] == 3
        assert after["indexed_chunks"] == 5
        assert improvement["indexed_chunks_added"] == 2, "the delta was not the delta"
        assert improvement["improved"] is True

    # --- 5.4 locking: a sweep must not rewrite what another sweep captured ---

    async def test_two_concurrent_sweeps_do_not_each_capture_a_baseline(
        self, pg, flags
    ) -> None:  # noqa: ANN001
        """Requirement 7, where the transaction behaviour is real.

        Two workers sweep at once — the startup sweep of a container that has just
        recycled, and the cadence sweep of one that has not. Without row locking both
        would snapshot the same stranded decision, and the second snapshot would be taken
        after the first had already made the round executable: an "after" measurement
        written into ``evidence_before_json``.
        """
        import asyncio

        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=5)
        run_id = await self._discovery_run_pg(pg)
        async with pg() as s:
            decision = await _legacy_decision(s, company_id, run_id=run_id)
            decision_id = decision.id
            await s.commit()

        async def sweep():  # noqa: ANN202
            queue = _RealJobQueue()
            async with pg() as s:
                ids = await recover_stranded_decisions(s, enqueue=queue)
                await s.commit()
            return ids, queue.jobs

        first, second = await asyncio.gather(sweep(), sweep())

        # Scoped to THIS decision: the sweep is a table scan and other tests share the
        # database, so counting every row it touched would measure the suite, not the
        # lock.
        recovered = [d for d in first[0] + second[0] if d == decision_id]
        jobs = [j for j in first[1] + second[1] if j["decision_id"] == decision_id]

        assert recovered == [decision_id], "the decision was recovered twice"
        assert len(jobs) == 1
        assert jobs[0]["baseline_at_enqueue"]["indexed_chunks"] == 5

    async def test_a_sweep_leaves_an_already_captured_baseline_alone(
        self, pg, flags
    ) -> None:  # noqa: ANN001
        """A healthy decision is inert under repair, however often it runs."""
        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=9)
        decision_id = await self._escalate(pg, company_id)

        async with pg() as s:
            original = dict(
                (await s.get(ResearchDecision, decision_id)).evidence_before_json
            )
        # More evidence arrives — a later, unrelated research run for the same company.
        await self._corpus_pg(pg, company_id, chunks=40)

        async with pg() as s:
            await recover_stranded_decisions(s, enqueue=_RealJobQueue())
            await s.commit()

        async with pg() as s:
            after_sweep = dict(
                (await s.get(ResearchDecision, decision_id)).evidence_before_json
            )

        assert after_sweep == original, "a sweep moved the baseline forward past evidence"
        assert after_sweep["indexed_chunks"] == 9

    async def test_a_legacy_decision_whose_round_finished_is_closed_not_guessed(
        self, pg, flags
    ) -> None:  # noqa: ANN001
        """What the first post-deploy sweep will do to the rows already in production.

        It must not backfill: today's evidence is AFTER this decision's round, and
        writing it into ``evidence_before_json`` would put a measurement nobody took into
        a permanent record.
        """
        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=421)
        run_id = await self._discovery_run_pg(pg)
        async with pg() as s:
            decision = await _legacy_decision(
                s, company_id, status=STATUS_QUEUED, run_id=run_id
            )
            decision_id = decision.id
            await s.commit()

        queue = _RealJobQueue()
        async with pg() as s:
            await recover_stranded_decisions(s, enqueue=queue)
            await s.commit()

        async with pg() as s:
            row = await s.get(ResearchDecision, decision_id)
            assert row.status == STATUS_ABANDONED
            assert row.terminal_reason == TERMINAL_EVIDENCE_BASELINE_MISSING
            assert row.evidence_before_json is None
        assert queue.jobs == []

    async def test_closing_it_frees_the_company_for_a_correct_decision(
        self, pg, flags
    ) -> None:  # noqa: ANN001
        """`ux_research_decisions_one_open` is a real partial unique index here.

        Failing closed is only acceptable if it ends the lockout rather than moving it:
        the company must be able to receive a fresh decision, which will carry a real
        baseline.
        """
        company_id = await self._company_pg(pg)
        await self._corpus_pg(pg, company_id, chunks=6)
        run_id = await self._discovery_run_pg(pg)
        async with pg() as s:
            await _legacy_decision(s, company_id, status=STATUS_QUEUED, run_id=run_id)
            await s.commit()

        async with pg() as s:
            await recover_stranded_decisions(s, enqueue=_RealJobQueue())
            await s.commit()

        new_id = await self._escalate(pg, company_id)

        async with pg() as s:
            stored = (await s.get(ResearchDecision, new_id)).evidence_before_json
        assert stored["indexed_chunks"] == 6
