"""V3.17.3 — a decision must cause a job to exist.

THE TEST THIS FILE EXISTS FOR
=============================
The defect V3.17 closes is that the discovery council's ``research_next`` has been written
for several phases and read by nobody. The design is explicit about which test would have
caught it, and which would not:

    "2. **Consumer** — *does the next component actually read it?* This is the one that
    would have caught today's defect. A test asserting ``candidates_to_research_next`` is
    populated passes today, while nothing consumes it. The consumer test must assert that
    a decision **causes a `research_jobs` row to exist**."

    "Explicitly forbidden: a test that asserts the decision JSON has a key."

So the load-bearing test here is :meth:`TestTheConsumer.test_a_decision_causes_a_job_row`.
Everything else guards the ways that link can be broken while still looking connected.

THE FAIL-CLOSED CASE IS NOT A FORMALITY
=======================================
``research_jobs`` rows are executed only by ``worker.run_worker``, gated by
``V3_DURABLE_JOBS_ENABLED`` — **absent in production**. If the controller created
decisions anyway, each one would enter an OPEN state, ``ux_research_decisions_one_open``
would then forbid any further decision for that company, and with no worker to advance it
the company would be locked out **permanently** by a feature that is not switched on.

That is a worse outcome than doing nothing, caused by doing something. Hence
:class:`TestItRefusesRatherThanStrandingACompany`.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.research_decision import (
    OPEN_STATUSES,
    STATUS_QUEUED,
    STATUS_RESEARCH_REQUIRED,
    ResearchDecision,
)
from app.services.escalation import store
from app.services.escalation.controller import (
    REFUSED_DURABLE_JOBS_DISABLED,
    REFUSED_ESCALATION_DISABLED,
    REFUSED_NO_COMPANY,
    CandidateFacts,
    escalate_discovery_run,
)
from app.services.escalation.predicates import (
    REFUSED_OPEN_DECISION,
    REFUSED_RUN_FAN_OUT,
)


@dataclass
class _Flags:
    v3_research_escalation_enabled: bool = True
    v3_durable_jobs_enabled: bool = True
    v3_escalation_max_rounds: int = 2
    v3_escalation_max_decisions_per_run: int = 5
    v3_escalation_cost_cap_usd: float = 0.0


@pytest.fixture
def flags(monkeypatch: pytest.MonkeyPatch):  # noqa: ANN201
    """Both switches ON by default here, so a test that needs one OFF says so."""
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
    """Stands in for `research_jobs`, and records what was actually enqueued.

    Injected rather than mocked at import, so the assertion is on the call the controller
    really makes — a patched module-level name would pass even if the controller had
    stopped calling it.
    """

    def __init__(self) -> None:
        self.jobs: list[dict[str, Any]] = []

    async def __call__(self, session, decision):  # noqa: ANN001
        job_id = uuid.uuid4()
        self.jobs.append(
            {
                "job_id": job_id,
                "idempotency_key": f"escalation:{decision.id}",
                "company_id": decision.company_id,
                "decision_id": decision.id,
            }
        )
        return job_id


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def session():  # noqa: ANN201
    """An in-memory database with the real schema.

    NOTE the partial unique index is NOT the guarantee under test here — SQLite runs this
    suite with foreign keys off, and V3.17.1 already proved that index on real PostgreSQL
    by rejected insert. What these tests check is the CONTROLLER's behaviour: that it
    reads the open decision, refuses with a reason, and enqueues when it should.
    """
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
        name="Escalation Test Co",
        status="new",
    )
    session.add(company)
    await session.flush()
    return company.id


async def _company_with_ticker(session):  # noqa: ANN001, ANN201
    ticker = f"T{uuid.uuid4().hex[:6].upper()}"
    return await _company(session, ticker), ticker


async def _baseline(session, company_id):  # noqa: ANN001, ANN201
    """The real pre-round snapshot, taken the way the controller takes it.

    V3.17.8 made `evidence_before` a required argument of `create_decision`, so a test
    that builds a decision by hand must supply one. Measuring it rather than writing a
    literal keeps these fixtures honest if a dimension is ever added.
    """
    from app.services.escalation.evidence import snapshot_evidence

    return (await snapshot_evidence(session, company_id)).to_dict()


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


# --------------------------------------------------------------------------- #
# 1. THE CONSUMER TEST — the one the design says would have caught the defect
# --------------------------------------------------------------------------- #


class TestTheConsumer:
    async def test_a_decision_causes_a_job_row(self, session, flags) -> None:  # noqa: ANN001
        """The whole slice, in one assertion.

        Not "a decision was written" — that passes against code connected to nothing,
        which is exactly the state V3.17 exists to leave. **The decision must cause work
        to exist.**
        """
        company_id = await _company(session)
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        assert len(outcome.created) == 1
        assert len(queue.jobs) == 1, "a decision was created and no job was enqueued"
        assert queue.jobs[0]["company_id"] == company_id

    async def test_the_job_is_keyed_to_the_decision_that_ordered_it(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """Idempotency key = decision id, so the link is a fact not a convention.

        It makes two things true at once: the same decision can never produce two jobs,
        and any job can be traced back to the decision that ordered it.
        """
        company_id = await _company(session)
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        decision_id = outcome.created[0]
        assert queue.jobs[0]["idempotency_key"] == f"escalation:{decision_id}"
        assert queue.jobs[0]["decision_id"] == decision_id

    async def test_the_decision_ends_queued_and_points_at_its_job(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """A decision left in `research_required` after enqueueing would be a lie."""
        company_id = await _company(session)
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        row = await session.get(ResearchDecision, outcome.created[0])
        assert row.status == STATUS_QUEUED
        assert row.last_job_id == queue.jobs[0]["job_id"]

    async def test_a_refused_candidate_creates_neither_decision_nor_job(
        self, session, flags
    ) -> None:  # noqa: ANN001
        from sqlalchemy import func, select

        company_id = await _company(session)
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id, coerced_action="reject_for_now")],
            enqueue=queue,
        )

        assert outcome.created == []
        assert queue.jobs == []
        count = (await session.execute(select(func.count()).select_from(ResearchDecision))).scalar_one()
        assert count == 0


# --------------------------------------------------------------------------- #
# 2. FAIL CLOSED — never strand a company
# --------------------------------------------------------------------------- #


class TestItRefusesRatherThanStrandingACompany:
    async def test_escalation_off_creates_nothing(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        _set(monkeypatch, v3_research_escalation_enabled=False)
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(await _company(session))],
            enqueue=queue,
        )

        assert outcome.created == []
        assert queue.jobs == []
        assert outcome.blocked_reason == REFUSED_ESCALATION_DISABLED

    async def test_no_worker_means_no_decision_at_all(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """THE STRANDING CASE, and the reason the controller checks a flag it does not own.

        With `V3_DURABLE_JOBS_ENABLED` off, nothing executes a queued job. A decision
        created here would enter an OPEN state, `ux_research_decisions_one_open` would
        forbid any further decision for that company, and nothing could ever advance it —
        a permanent lockout caused by a feature that is not switched on.

        Doing nothing is strictly better than doing that.
        """
        from sqlalchemy import func, select

        _set(monkeypatch, v3_durable_jobs_enabled=False)
        company_id = await _company(session)
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        assert outcome.blocked_reason == REFUSED_DURABLE_JOBS_DISABLED
        assert outcome.created == []
        assert queue.jobs == []
        # The critical part: no OPEN decision was left behind to block this company.
        count = (
            await session.execute(
                select(func.count())
                .select_from(ResearchDecision)
                .where(ResearchDecision.status.in_(OPEN_STATUSES))
            )
        ).scalar_one()
        assert count == 0, "an open decision was stranded with no worker to advance it"

    async def test_the_refusal_names_the_flag_a_human_must_set(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """"Nothing happened" with no reason is the failure mode this platform avoids."""
        _set(monkeypatch, v3_durable_jobs_enabled=False)

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(await _company(session))],
            enqueue=_RecordingQueue(),
        )

        assert "V3_DURABLE_JOBS_ENABLED" in (outcome.blocked_detail or "")

    async def test_the_flags_are_read_at_call_time_not_import_time(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """A captured module-level boolean is how a flag quietly becomes permanent."""
        _set(monkeypatch, v3_research_escalation_enabled=False)
        blocked = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(await _company(session))],
            enqueue=_RecordingQueue(),
        )
        assert blocked.created == []

        _set(monkeypatch, v3_research_escalation_enabled=True)
        allowed = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(await _company(session))],
            enqueue=_RecordingQueue(),
        )
        assert len(allowed.created) == 1


# --------------------------------------------------------------------------- #
# 3. The bounds hold against real rows
# --------------------------------------------------------------------------- #


class TestBoundsAgainstRealRows:
    async def test_an_existing_open_decision_refuses_a_second(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """Read through the store, not assumed: the predicate is told what the DB holds."""
        company_id = await _company(session)
        await store.create_decision(
            session,
            company_id=company_id,
            discovery_run_id=None,
            discovery_candidate_id=None,
            source="discovery_council",
            decision="research_next",
            reason="already open",
            max_rounds=2,
            evidence_before=await _baseline(session, company_id),
        )
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        assert outcome.created == []
        assert queue.jobs == []
        assert outcome.refusals[0]["clause"] == REFUSED_OPEN_DECISION

    async def test_fan_out_is_capped_within_a_single_call(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """A thesis run returns dozens of candidates, all individually qualifying.

        The cap has to count decisions created *during this call*, not only those already
        committed — otherwise one call queues every candidate and the cap only applies to
        the next one.
        """
        _set(monkeypatch, v3_escalation_max_decisions_per_run=2)
        candidates = [_candidate(await _company(session)) for _ in range(5)]
        queue = _RecordingQueue()

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=candidates,
            enqueue=queue,
        )

        assert len(outcome.created) == 2
        assert len(queue.jobs) == 2
        assert any(r["clause"] == REFUSED_RUN_FAN_OUT for r in outcome.refusals)

    async def test_the_cap_counts_decisions_already_made_for_this_run(
        self, session, flags, monkeypatch
    ) -> None:  # noqa: ANN001
        """A second call for the same run must not restart the budget."""
        _set(monkeypatch, v3_escalation_max_decisions_per_run=2)
        run_id = uuid.uuid4()
        first = [_candidate(await _company(session)) for _ in range(2)]
        await escalate_discovery_run(
            session, discovery_run_id=run_id, candidates=first, enqueue=_RecordingQueue()
        )

        queue = _RecordingQueue()
        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=run_id,
            candidates=[_candidate(await _company(session))],
            enqueue=queue,
        )

        assert outcome.created == []
        assert queue.jobs == []

    async def test_a_candidate_with_no_company_is_refused_not_crashed(
        self, session, flags
    ) -> None:  # noqa: ANN001
        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(None)],
            enqueue=_RecordingQueue(),
        )

        assert outcome.created == []
        assert outcome.refusals[0]["clause"] == REFUSED_NO_COMPANY


# --------------------------------------------------------------------------- #
# 4. Ordering — the decision exists before the work does
# --------------------------------------------------------------------------- #


class TestTheDecisionIsWrittenBeforeTheWork:
    async def test_a_queue_failure_leaves_an_explainable_decision_not_silent_work(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """Decision first, job second, and the order is the point.

        Job-first would mean a worker could claim paid work before the record of *why* it
        was ordered exists. Decision-first means the worst case is a decision still in
        `research_required` — visible, queryable and fixable.
        """
        from sqlalchemy import select

        company_id = await _company(session)

        async def _explodes(session, decision):  # noqa: ANN001
            raise RuntimeError("the queue is unreachable")

        with pytest.raises(RuntimeError):
            await escalate_discovery_run(
                session,
                discovery_run_id=uuid.uuid4(),
                candidates=[_candidate(company_id)],
                enqueue=_explodes,
            )

        rows = (await session.execute(select(ResearchDecision))).scalars().all()
        assert len(rows) == 1
        assert rows[0].status == STATUS_RESEARCH_REQUIRED
        assert rows[0].last_job_id is None
        assert rows[0].reason, "a decision with no reason cannot be explained"


# --------------------------------------------------------------------------- #
# 5. Reading the bucket that nothing has ever read
# --------------------------------------------------------------------------- #


class TestTheCouncilBucketIsFinallyConsumed:
    """The other half of the defect.

    The controller can enqueue, but it only matters if something actually reads
    ``candidates_to_research_next``. These tests are the reason the field stops being
    decoration.
    """

    async def _run_with_bucket(self, session, entries):  # noqa: ANN001, ANN202
        from app.models.discovery import DiscoveryRun

        run = DiscoveryRun(
            id=uuid.uuid4(),
            config_json={
                "discovery_council": {"review": {"candidates_to_research_next": entries}}
            },
        )
        session.add(run)
        await session.flush()
        return run

    async def _candidate_row(self, session, run_id, ticker, **over):  # noqa: ANN001, ANN003
        """A DiscoveryCandidate carries a ticker, NOT a company_id.

        The link to a Company is (ticker, exchange), and it exists only once the
        candidate has been promoted — which is why the adapter resolves rather than reads.
        """
        from app.models.discovery import DiscoveryCandidate

        row = DiscoveryCandidate(
            id=uuid.uuid4(),
            discovery_run_id=run_id,
            ticker=ticker,
            exchange="US",
            **over,
        )
        session.add(row)
        await session.flush()
        return row

    async def test_the_research_next_bucket_becomes_candidate_facts(
        self, session
    ) -> None:  # noqa: ANN001
        from app.services.escalation.from_council import (
            candidates_proposed_for_research,
        )

        company_id, ticker = await _company_with_ticker(session)
        run = await self._run_with_bucket(session, [])
        row = await self._candidate_row(
            session, run.id, ticker, blocking_gap_count=4
        )
        run.config_json = {
            "discovery_council": {
                "review": {
                    "candidates_to_research_next": [
                        {"candidate_id": str(row.id), "confidence": 0.3}
                    ]
                }
            }
        }
        await session.flush()

        facts = await candidates_proposed_for_research(session, run)

        assert len(facts) == 1
        assert facts[0].company_id == company_id
        assert facts[0].coerced_action == "research_next"
        assert facts[0].blocking_gap_count == 4
        assert facts[0].confidence == pytest.approx(0.3)

    async def test_no_council_review_yields_no_work_and_no_error(
        self, session
    ) -> None:  # noqa: ANN001
        """"The council never ran" and "it proposed nothing" both mean no work."""
        from app.models.discovery import DiscoveryRun
        from app.services.escalation.from_council import (
            candidates_proposed_for_research,
        )

        run = DiscoveryRun(id=uuid.uuid4(), config_json={})
        session.add(run)
        await session.flush()

        assert await candidates_proposed_for_research(session, run) == []

    async def test_a_bucket_naming_a_vanished_candidate_is_skipped(
        self, session
    ) -> None:  # noqa: ANN001
        """Escalating "some candidate that was here once" is worse than escalating none."""
        from app.services.escalation.from_council import (
            candidates_proposed_for_research,
        )

        run = await self._run_with_bucket(
            session, [{"candidate_id": str(uuid.uuid4()), "confidence": 0.2}]
        )

        assert await candidates_proposed_for_research(session, run) == []

    async def test_an_absent_confidence_stays_none_and_is_not_defaulted_to_zero(
        self, session
    ) -> None:  # noqa: ANN001
        """0.0 would make every unscored candidate look uncertain enough to pay for."""
        from app.services.escalation.from_council import (
            candidates_proposed_for_research,
        )

        company_id, ticker = await _company_with_ticker(session)
        run = await self._run_with_bucket(session, [])
        row = await self._candidate_row(session, run.id, ticker)
        run.config_json = {
            "discovery_council": {
                "review": {
                    "candidates_to_research_next": [{"candidate_id": str(row.id)}]
                }
            }
        }
        await session.flush()

        facts = await candidates_proposed_for_research(session, run)

        assert facts[0].confidence is None

    async def test_bucket_to_job_end_to_end(self, session, flags) -> None:  # noqa: ANN001
        """The full path the defect broke: council bucket → decision → job row."""
        from app.services.escalation.from_council import (
            candidates_proposed_for_research,
        )

        company_id, ticker = await _company_with_ticker(session)
        run = await self._run_with_bucket(session, [])
        row = await self._candidate_row(
            session, run.id, ticker, blocking_gap_count=2
        )
        run.config_json = {
            "discovery_council": {
                "review": {
                    "candidates_to_research_next": [
                        {"candidate_id": str(row.id), "confidence": 0.25}
                    ]
                }
            }
        }
        await session.flush()

        queue = _RecordingQueue()
        facts = await candidates_proposed_for_research(session, run)
        outcome = await escalate_discovery_run(
            session, discovery_run_id=run.id, candidates=facts, enqueue=queue
        )

        assert len(outcome.created) == 1
        assert len(queue.jobs) == 1
        assert queue.jobs[0]["company_id"] == company_id

    async def test_an_unpromoted_candidate_has_no_company_and_is_refused(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """A DiscoveryCandidate has no company_id; only promotion creates a Company.

        So escalation can only research a promoted candidate. The refusal is explicit —
        an unpromoted candidate silently skipped would look identical to a council that
        proposed nothing.
        """
        from app.services.escalation.from_council import (
            candidates_proposed_for_research,
        )

        run = await self._run_with_bucket(session, [])
        row = await self._candidate_row(session, run.id, "NEVERPROMOTED")
        run.config_json = {
            "discovery_council": {
                "review": {
                    "candidates_to_research_next": [{"candidate_id": str(row.id)}]
                }
            }
        }
        await session.flush()

        facts = await candidates_proposed_for_research(session, run)
        assert facts[0].company_id is None

        outcome = await escalate_discovery_run(
            session,
            discovery_run_id=run.id,
            candidates=facts,
            enqueue=_RecordingQueue(),
        )
        assert outcome.created == []
        assert outcome.refusals[0]["clause"] == REFUSED_NO_COMPANY

    async def test_the_same_ticker_on_another_exchange_is_not_the_same_company(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """The trap the (ticker, exchange) key exists to avoid.

        Matching on ticker alone would queue paid research against the wrong issuer, and
        the resulting report would read as entirely plausible — a mistake with no visible
        symptom afterwards.
        """
        from app.models.company import Company
        from app.services.escalation.from_council import (
            candidates_proposed_for_research,
        )

        # A company with the same ticker, listed somewhere else.
        session.add(
            Company(
                id=uuid.uuid4(),
                ticker="SHARED",
                exchange="LSE",
                name="Not The One",
                status="new",
            )
        )
        await session.flush()

        run = await self._run_with_bucket(session, [])
        row = await self._candidate_row(session, run.id, "SHARED")  # exchange="US"
        run.config_json = {
            "discovery_council": {
                "review": {
                    "candidates_to_research_next": [{"candidate_id": str(row.id)}]
                }
            }
        }
        await session.flush()

        facts = await candidates_proposed_for_research(session, run)

        assert facts[0].company_id is None, "matched a company on a different exchange"


# --------------------------------------------------------------------------- #
# 6. V3.17.4 — what happens after a round, and the crash that must not strand
# --------------------------------------------------------------------------- #


class TestRoundCompletion:
    async def _decision(self, session, company_id, **over):  # noqa: ANN001, ANN003
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
                "evidence_before", await _baseline(session, company_id)
            ),
            **over,
        )

    async def test_a_round_that_acquired_nothing_terminates_as_exhausted(
        self, session, flags
    ) -> None:  # noqa: ANN001
        from app.services.escalation.controller import complete_round

        company_id = await _company(session)
        decision = await self._decision(session, company_id)
        queue = _RecordingQueue()

        verdict = await complete_round(
            session, decision, job_completed=True, enqueue=queue
        )

        assert verdict.status == "exhausted"
        assert decision.terminal_reason == "exhausted_no_improvement"
        assert queue.jobs == [], "a terminal decision enqueued more work"

    async def test_a_job_that_did_not_complete_never_reads_as_exhausted(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """The distinction, enforced end to end rather than only in the pure function."""
        from app.services.escalation.controller import complete_round

        company_id = await _company(session)
        decision = await self._decision(session, company_id)

        verdict = await complete_round(
            session, decision, job_completed=False, enqueue=_RecordingQueue()
        )

        assert decision.terminal_reason == "research_did_not_complete"
        assert verdict.status == "abandoned"

    async def test_the_measured_evidence_is_persisted_for_a_reader(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """evidence_after_json and improvement_json must survive, not be recomputed."""
        from app.services.escalation.controller import complete_round

        company_id = await _company(session)
        decision = await self._decision(session, company_id)

        await complete_round(session, decision, job_completed=True, enqueue=_RecordingQueue())

        assert decision.evidence_after_json is not None
        assert "indexed_chunks" in decision.evidence_after_json
        assert decision.improvement_json["next_status"] == "exhausted"
        assert decision.improvement_json["reasons"]

    async def test_each_round_gets_its_own_deterministic_job_key(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """One decision, several rounds, one job each — and recomputable from the row."""
        from app.services.escalation.controller import round_idempotency_key

        decision_id = uuid.uuid4()
        keys = {round_idempotency_key(decision_id, n) for n in range(3)}

        assert len(keys) == 3
        assert round_idempotency_key(decision_id, 1) == round_idempotency_key(
            decision_id, 1
        )


class TestACrashMustNotStrandACompany:
    """The transaction boundary that could cause a permanent outage.

    A decision row and a job row are written in DIFFERENT transactions — `JobStore` owns
    its own session by design. Whichever order they go in, a process that dies between
    them leaves one without the other, and an open decision with no executable job means
    `ux_research_decisions_one_open` locks that company out of every future decision.
    """

    async def _stranded(self, session, company_id):  # noqa: ANN001
        """A decision that is open and has no job — the state a crash can leave."""
        return await store.create_decision(
            session,
            company_id=company_id,
            discovery_run_id=uuid.uuid4(),
            discovery_candidate_id=None,
            source="discovery_council",
            decision="research_next",
            reason="crashed before the job landed",
            max_rounds=2,
            evidence_before=await _baseline(session, company_id),
        )

    async def test_an_open_decision_with_no_job_is_recovered(
        self, session, flags
    ) -> None:  # noqa: ANN001
        from app.services.escalation.controller import recover_stranded_decisions

        company_id = await _company(session)
        decision = await self._stranded(session, company_id)
        assert decision.last_job_id is None

        queue = _RecordingQueue()
        recovered = await recover_stranded_decisions(session, enqueue=queue)

        assert decision.id in recovered
        assert len(queue.jobs) == 1
        assert decision.last_job_id == queue.jobs[0]["job_id"]
        assert decision.status == "queued"

    async def test_recovery_is_safe_to_run_twice(self, session, flags) -> None:  # noqa: ANN001
        """It will run on a schedule; a second pass must not duplicate paid work."""
        from app.services.escalation.controller import recover_stranded_decisions

        company_id = await _company(session)
        await self._stranded(session, company_id)

        queue = _RecordingQueue()
        first = await recover_stranded_decisions(session, enqueue=queue)
        second = await recover_stranded_decisions(session, enqueue=queue)

        assert len(first) == 1
        assert second == [], "a recovered decision was recovered again"
        assert len(queue.jobs) == 1

    async def test_a_healthy_decision_is_left_alone(self, session, flags) -> None:  # noqa: ANN001
        from app.services.escalation.controller import recover_stranded_decisions

        company_id = await _company(session)
        queue = _RecordingQueue()
        await escalate_discovery_run(
            session,
            discovery_run_id=uuid.uuid4(),
            candidates=[_candidate(company_id)],
            enqueue=queue,
        )

        recovered = await recover_stranded_decisions(session, enqueue=queue)

        assert recovered == []
        assert len(queue.jobs) == 1

    async def test_a_decision_with_no_company_is_closed_not_left_open(
        self, session, flags
    ) -> None:  # noqa: ANN001
        """Nothing to research, so it must not keep occupying an open slot."""
        from app.services.escalation.controller import recover_stranded_decisions

        decision = await store.create_decision(
            session,
            company_id=None,
            discovery_run_id=uuid.uuid4(),
            discovery_candidate_id=None,
            source="discovery_council",
            decision="research_next",
            reason="orphaned",
            max_rounds=2,
            # No company means nothing to snapshot — the one exemption, and it can
            # never execute research.
            evidence_before=None,
        )

        await recover_stranded_decisions(session, enqueue=_RecordingQueue())

        assert decision.status == "abandoned"
        assert decision.terminal_reason == "research_did_not_complete"
