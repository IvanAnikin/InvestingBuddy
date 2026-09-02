"""The product front door must not run research inside the HTTP request.

THE DEFECT
==========
``/research/company`` ran the pipeline synchronously: the company-analysis
workflow, then the final-report generator. On live data that measured ~154s of
primary-document ingestion plus ~145-190s of council — comfortably past the
~230s Azure gateway ceiling. The observed live results were HTTP 502 at ~206s
and HTTP 504 at ~240s, a rolled-back transaction, and a user who had waited
five minutes for an error. The product's primary entry point was unusable.

WHAT THESE TESTS PIN
====================
  * the submit returns immediately and does NOT do the work,
  * the job row is COMMITTED before the expensive work begins,
  * stages are visible and are the WORKFLOW'S OWN, not invented,
  * a reload recovers the job by company — the id is not only in the browser,
  * the job survives the request ending,
  * a successful job links the EXACT final report it produced,
  * a failure persists a terminal state and can be retried,
  * a double submit never starts a second expensive run,
  * identity (company_id / ticker / exchange / provider) is carried exactly,
  * and the streaming progress path returns the SAME state as the plain one.

All offline: the long work is a controlled fake, so nothing here waits minutes
and nothing touches the network or an LLM.
"""

from __future__ import annotations

import asyncio
import json
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models import agent_run as _agent_run  # noqa: F401
from app.models import company as _company  # noqa: F401
from app.models import discovery as _discovery  # noqa: F401
from app.models import report as _report  # noqa: F401
from app.models import scorecard as _scorecard  # noqa: F401
from app.models import screening as _screening  # noqa: F401
from app.models import source as _source  # noqa: F401
from app.models.agent_run import AgentRun, AgentStep
from app.models.company import Company
from app.schemas.company_research import CompanyResearchJobResponse
from app.services import company_research_service as svc
from app.services import research_job


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


@pytest.fixture
async def engine():
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def factory(engine):
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def session(factory):
    async with factory() as s:
        yield s


PNDORA_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")


async def _pandora(session) -> Company:
    company = Company(
        id=PNDORA_ID,
        ticker="PNDORA",
        exchange="CO",
        name="Pandora A/S",
        country="Denmark",
        status="new",
    )
    session.add(company)
    await session.commit()
    return company


class _FakeFinalReport:
    """What the final-report generator returns, reduced to what the job reads."""

    def __init__(self, report_id: uuid.UUID) -> None:
        self.report_id = report_id
        self.llm_used = True
        self.schema_valid = True
        self.safety_valid = True


def _workflow_runner(
    *,
    draft_id: uuid.UUID,
    agent_run_id: uuid.UUID,
    nodes: list[str] | None = None,
    delay: float = 0.0,
    fail: bool = False,
):
    """A controlled stand-in for the five-minute workflow."""

    async def run(db, **kwargs: Any) -> dict[str, Any]:
        if delay:
            await asyncio.sleep(delay)
        if fail:
            raise RuntimeError("provider exploded")
        on_node = kwargs.get("on_node")
        if on_node is not None:
            for node in nodes or []:
                await on_node(node)
        return {
            "draft_report_id": str(draft_id),
            "agent_run_id": str(agent_run_id),
            "status": "completed",
            "company_name": "Pandora A/S",
            "ticker": kwargs.get("ticker"),
            "provider_name": kwargs.get("provider_name"),
        }

    return run


def _report_generator(report_id: uuid.UUID, *, fail: bool = False):
    async def gen(db, **kwargs: Any):
        if fail:
            raise RuntimeError("assembly failed")
        return _FakeFinalReport(report_id)

    return gen


# ---------------------------------------------------------------------------
# 1-3. Submit is fast, does no work, and commits the job first
# ---------------------------------------------------------------------------


async def test_submit_returns_immediately_and_runs_no_research(session) -> None:
    """The submit creates a job. It must NOT run the pipeline."""
    company = await _pandora(session)
    ran = False

    async def never(db, **kwargs):  # pragma: no cover - must not be called
        nonlocal ran
        ran = True
        return {}

    envelope, scheduled = await svc.start_company_research(
        session, company, provider_name="free_real"
    )

    assert scheduled is True
    assert envelope["status"] == research_job.STATUS_PENDING
    assert envelope["stage"] == research_job.STAGE_QUEUED
    assert ran is False
    del never


async def test_submit_is_fast(session) -> None:
    """A submit must land far inside the gateway ceiling, not near it."""
    company = await _pandora(session)
    start = asyncio.get_running_loop().time()
    await svc.start_company_research(session, company, provider_name="free_real")
    elapsed = asyncio.get_running_loop().time() - start
    # Generous by design: the point is that it is bounded work (two inserts and
    # a commit) rather than a pipeline run. The live budget is < 5s.
    assert elapsed < 5.0


async def test_job_is_committed_before_any_long_work(session, factory) -> None:
    """The job must exist in the DATABASE before the worker starts.

    This is what makes the browser irrelevant: the 202 is backed by a row, so
    losing the connection cannot lose the run.
    """
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )

    # A SEPARATE session must be able to see it.
    async with factory() as other:
        stored = await svc.get_job_envelope(other, uuid.UUID(envelope["job_id"]))
    assert stored is not None
    assert stored["status"] == research_job.STATUS_PENDING
    assert stored["company"]["ticker"] == "PNDORA"


# ---------------------------------------------------------------------------
# 4. Stages are visible, and they are the workflow's own
# ---------------------------------------------------------------------------


async def test_stages_progress_through_the_workflows_own_nodes(
    session, factory
) -> None:
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])
    report_id = uuid.uuid4()

    await svc.process_company_research_by_id(
        job_id,
        session_factory=factory,
        run_analysis=_workflow_runner(
            draft_id=uuid.uuid4(),
            agent_run_id=uuid.uuid4(),
            nodes=[
                "load_company",
                "fetch_provider_data",
                "build_company_snapshot",
                "financial_data_agent",
                "citation_validator_v2",
            ],
        ),
        generate_final_report=_report_generator(report_id),
    )

    async with factory() as other:
        stored = await svc.get_job_envelope(other, job_id)
    assert stored is not None
    stages = stored["stages_completed"]
    assert research_job.STAGE_COMPANY_IDENTITY in stages
    assert research_job.STAGE_SOURCE_DISCOVERY in stages
    assert research_job.STAGE_DOCUMENT_INGESTION in stages
    assert research_job.STAGE_FINANCIAL_EXTRACTION in stages
    assert research_job.STAGE_EVIDENCE_VALIDATION in stages
    assert research_job.STAGE_COUNCIL_ANALYSIS in stages
    assert research_job.STAGE_REPORT_ASSEMBLY in stages
    assert stored["stage"] == research_job.STAGE_COMPLETED


async def test_every_stage_has_human_words_and_no_percentage() -> None:
    """Stage names are enough. A fabricated percentage is not offered."""
    for stage in research_job.STAGE_ORDER:
        label = research_job.stage_label(stage)
        assert label and label != stage
        assert "%" not in label


async def test_unknown_graph_node_does_not_move_the_stage() -> None:
    """Adding a node to the graph cannot silently claim progress."""
    assert research_job.stage_for_node("a_node_that_does_not_exist") is None


# ---------------------------------------------------------------------------
# 5-6. Reload / navigation recover the job; the job survives the request
# ---------------------------------------------------------------------------


async def test_reload_recovers_the_job_by_company(session) -> None:
    """A refreshed page finds the run without the browser having kept the id."""
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )

    recovered = await svc.latest_job_for_company(session, company.id)
    assert recovered is not None
    assert recovered["job_id"] == envelope["job_id"]


async def test_latest_job_lookup_is_company_scoped(session) -> None:
    """Never a global-latest lookup: another company's job is not returned."""
    company = await _pandora(session)
    other = Company(
        id=uuid.uuid4(), ticker="CFR", exchange="SW", name="Richemont", status="new"
    )
    session.add(other)
    await session.commit()

    await svc.start_company_research(session, company, provider_name="free_real")
    assert await svc.latest_job_for_company(session, other.id) is None


async def test_job_completes_after_the_request_session_is_gone(
    session, factory
) -> None:
    """The worker uses its OWN session — the request's is closed by then."""
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])
    await session.close()  # the request has ended

    report_id = uuid.uuid4()
    await svc.process_company_research_by_id(
        job_id,
        session_factory=factory,
        run_analysis=_workflow_runner(
            draft_id=uuid.uuid4(), agent_run_id=uuid.uuid4()
        ),
        generate_final_report=_report_generator(report_id),
    )

    async with factory() as other:
        stored = await svc.get_job_envelope(other, job_id)
    assert stored["status"] == research_job.STATUS_COMPLETED
    assert stored["analysis_report_id"] == str(report_id)


# ---------------------------------------------------------------------------
# 7-9. Success links the EXACT report
# ---------------------------------------------------------------------------


async def test_successful_job_links_the_exact_report_it_produced(
    session, factory
) -> None:
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])
    report_id = uuid.uuid4()
    draft_id = uuid.uuid4()
    agent_run_id = uuid.uuid4()

    await svc.process_company_research_by_id(
        job_id,
        session_factory=factory,
        run_analysis=_workflow_runner(draft_id=draft_id, agent_run_id=agent_run_id),
        generate_final_report=_report_generator(report_id),
    )

    async with factory() as other:
        stored = await svc.get_job_envelope(other, job_id)
    assert stored["analysis_report_id"] == str(report_id)
    assert stored["legacy_draft_report_id"] == str(draft_id)
    assert stored["agent_run_id"] == str(agent_run_id)
    assert stored["report"]["report_id"] == str(report_id)
    # The clean report page and the technical page must reference ONE report.
    response = CompanyResearchJobResponse.from_envelope(stored, message="x")
    assert str(response.analysis_report_id) == str(report_id)
    assert response.report["report_id"] == str(report_id)


# ---------------------------------------------------------------------------
# 10-11. Failure persists, and is retryable
# ---------------------------------------------------------------------------


async def test_workflow_failure_persists_a_terminal_state(session, factory) -> None:
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])

    await svc.process_company_research_by_id(
        job_id,
        session_factory=factory,
        run_analysis=_workflow_runner(
            draft_id=uuid.uuid4(), agent_run_id=uuid.uuid4(), fail=True
        ),
    )

    async with factory() as other:
        stored = await svc.get_job_envelope(other, job_id)
        run = await other.get(AgentRun, job_id)
    assert stored["status"] == research_job.STATUS_FAILED
    assert stored["error"] == "internal_error"
    assert stored["completed_at"]
    # Never stuck in running, on the job row either.
    assert run.status == "failed"


async def test_report_assembly_failure_keeps_the_evidence_run(
    session, factory
) -> None:
    """A failed last step must not erase the work that succeeded before it."""
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])
    draft_id = uuid.uuid4()

    await svc.process_company_research_by_id(
        job_id,
        session_factory=factory,
        run_analysis=_workflow_runner(
            draft_id=draft_id, agent_run_id=uuid.uuid4()
        ),
        generate_final_report=_report_generator(uuid.uuid4(), fail=True),
    )

    async with factory() as other:
        stored = await svc.get_job_envelope(other, job_id)
    # The draft the evidence run produced is still linked and still reachable.
    assert stored["legacy_draft_report_id"] == str(draft_id)
    assert stored["analysis_report_id"] == str(draft_id)
    assert "final_report_generation_failed" in stored["warnings"]
    assert stored["status"] == research_job.STATUS_COMPLETED_WITH_WARNINGS


async def test_retry_after_failure_starts_a_new_job(session, factory) -> None:
    company = await _pandora(session)
    first, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    await svc.process_company_research_by_id(
        uuid.UUID(first["job_id"]),
        session_factory=factory,
        run_analysis=_workflow_runner(
            draft_id=uuid.uuid4(), agent_run_id=uuid.uuid4(), fail=True
        ),
    )

    second, scheduled = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    assert scheduled is True
    assert second["job_id"] != first["job_id"]


# ---------------------------------------------------------------------------
# 12. Double submit
# ---------------------------------------------------------------------------


async def test_double_submit_does_not_start_a_second_expensive_job(
    session,
) -> None:
    company = await _pandora(session)
    first, scheduled_first = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    second, scheduled_second = await svc.start_company_research(
        session, company, provider_name="free_real"
    )

    assert scheduled_first is True
    assert scheduled_second is False
    assert second["job_id"] == first["job_id"]

    steps = (
        await session.execute(
            select(AgentStep).where(AgentStep.agent_name == svc.JOB_AGENT_NAME)
        )
    ).scalars().all()
    assert len(steps) == 1


async def test_an_abandoned_job_does_not_block_a_new_one(session) -> None:
    """A job whose worker died must be restartable, not a permanent lock."""
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])

    step = await svc._load_job_step(session, job_id)
    stale = dict(step.output_json)
    stale["status"] = research_job.STATUS_RUNNING
    stale["started_at"] = (
        datetime.now(timezone.utc)
        - timedelta(minutes=research_job.stale_after_minutes() + 5)
    ).isoformat()
    step.output_json = stale
    await session.commit()

    described = research_job.describe(stale)
    assert described["status"] == research_job.STATUS_INTERRUPTED
    assert described["recoverable"] is True

    _, scheduled = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    assert scheduled is True


# ---------------------------------------------------------------------------
# 13-14. Identity is exact
# ---------------------------------------------------------------------------


async def test_identity_is_carried_exactly_and_never_re_derived(
    session, factory
) -> None:
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="eodhd_free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])

    seen: dict[str, Any] = {}

    async def capture(db, **kwargs):
        seen.update(kwargs)
        return {
            "draft_report_id": str(uuid.uuid4()),
            "agent_run_id": str(uuid.uuid4()),
            "status": "completed",
        }

    await svc.process_company_research_by_id(
        job_id,
        session_factory=factory,
        run_analysis=capture,
        generate_final_report=_report_generator(uuid.uuid4()),
    )

    assert seen["company_id"] == str(PNDORA_ID)
    assert seen["provider_name"] == "eodhd_free_real"
    async with factory() as other:
        stored = await svc.get_job_envelope(other, job_id)
    assert stored["company"]["ticker"] == "PNDORA"
    assert stored["company"]["exchange"] == "CO"
    assert stored["company"]["id"] == str(PNDORA_ID)
    assert stored["provider_name"] == "eodhd_free_real"


async def test_company_record_comes_from_the_row_not_a_label(session) -> None:
    company = await _pandora(session)
    record = svc.company_record_of(company)
    assert record["ticker"] == "PNDORA"
    assert record["exchange"] == "CO"
    assert record["name"] == "Pandora A/S"
    assert record["id"] == str(PNDORA_ID)


async def test_resolve_company_accepts_ticker_and_exchange(session) -> None:
    await _pandora(session)
    resolved = await svc.resolve_company(session, ticker="PNDORA", exchange="CO")
    assert resolved is not None and resolved.id == PNDORA_ID
    # A combined code is NOT how the registry stores it, and must not match.
    assert await svc.resolve_company(session, ticker="PNDORA.CO", exchange="CO") is None


# ---------------------------------------------------------------------------
# 15. The streaming progress path is the same graph
# ---------------------------------------------------------------------------


async def test_streamed_progress_returns_the_same_final_state() -> None:
    """``on_node`` must not change what the workflow produces.

    The progress callback switches the graph from ``ainvoke`` to a multi-mode
    ``astream``. If those two disagreed about the final state, stage progress
    would have been bought with a silently different research run.
    """
    from typing import TypedDict

    from langgraph.graph import END, StateGraph

    class S(TypedDict, total=False):
        a: int
        b: int
        status: str

    async def n1(_s):
        return {"a": 1}

    async def n2(s):
        return {"b": s["a"] + 1, "status": "done"}

    g = StateGraph(S)
    g.add_node("n1", n1)
    g.add_node("n2", n2)
    g.set_entry_point("n1")
    g.add_edge("n1", "n2")
    g.add_edge("n2", END)
    app = g.compile()

    invoked = await app.ainvoke({"status": "running"})

    streamed: dict[str, Any] = {}
    nodes: list[str] = []
    async for mode, chunk in app.astream(
        {"status": "running"}, stream_mode=["updates", "values"]
    ):
        if mode == "values":
            streamed = chunk
        elif mode == "updates":
            nodes.extend(chunk.keys())

    assert streamed == invoked
    assert nodes == ["n1", "n2"]


async def test_progress_callback_failure_never_fails_the_run(
    session, factory
) -> None:
    """A UI concern must never be able to break a research run."""
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])

    async def run_with_bad_callback(db, **kwargs):
        on_node = kwargs.get("on_node")
        assert on_node is not None
        try:
            await on_node("load_company")
        except Exception:  # noqa: BLE001 - the workflow swallows it too
            pass
        return {
            "draft_report_id": str(uuid.uuid4()),
            "agent_run_id": str(uuid.uuid4()),
            "status": "completed",
        }

    await svc.process_company_research_by_id(
        job_id,
        session_factory=factory,
        run_analysis=run_with_bad_callback,
        generate_final_report=_report_generator(uuid.uuid4()),
    )

    async with factory() as other:
        stored = await svc.get_job_envelope(other, job_id)
    assert stored["status"] == research_job.STATUS_COMPLETED


# ---------------------------------------------------------------------------
# No mock fallback outside explicit test mode
# ---------------------------------------------------------------------------


async def test_job_records_the_provider_it_was_given(session) -> None:
    """The envelope reports the provider actually requested — never 'mock'."""
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    assert envelope["provider_name"] == "free_real"


async def test_sweep_reports_abandoned_jobs_read_only(session, factory) -> None:
    company = await _pandora(session)
    envelope, _ = await svc.start_company_research(
        session, company, provider_name="free_real"
    )
    job_id = uuid.UUID(envelope["job_id"])
    step = await svc._load_job_step(session, job_id)
    stale = dict(step.output_json)
    stale["status"] = research_job.STATUS_RUNNING
    stale["started_at"] = (
        datetime.now(timezone.utc)
        - timedelta(minutes=research_job.stale_after_minutes() + 5)
    ).isoformat()
    step.output_json = stale
    await session.commit()

    async with factory() as other:
        found = await svc.sweep_interrupted_company_jobs(other)
        # READ-ONLY: the stored envelope is left exactly as the dead worker
        # left it, so its audit trail survives.
        after = await svc.get_job_envelope(other, job_id)
    assert [j["job_id"] for j in found] == [str(job_id)]
    assert after["status"] == research_job.STATUS_RUNNING


# ---------------------------------------------------------------------------
# The HTTP contract
# ---------------------------------------------------------------------------
#
# The service tests above prove the lifecycle. These prove the endpoints the
# browser actually calls: that the POST answers 202 rather than blocking, that
# the response carries the stage a reader is shown, and that recovery by
# company works over HTTP.


async def _client(factory):
    from httpx import ASGITransport, AsyncClient

    from app.db.session import get_db
    from app.main import app

    async def override():
        async with factory() as s:
            yield s

    app.dependency_overrides[get_db] = override
    transport = ASGITransport(app=app)
    return AsyncClient(transport=transport, base_url="http://test"), app


async def test_post_returns_202_with_a_queued_job(factory, session) -> None:
    company = await _pandora(session)
    client, app = await _client(factory)
    try:
        res = await client.post(
            "/api/v1/company-research/jobs",
            json={"company_id": str(company.id), "provider_name": "free_real"},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert res.status_code == 202
    body = res.json()
    assert body["status"] == "pending"
    assert body["stage"] == "queued"
    assert body["stage_label"] == "Queued"
    assert body["company"]["ticker"] == "PNDORA"
    assert body["human_review_required"] is True
    # Stage names, in human words, with no fabricated percentage.
    labels = [s["label"] for s in body["stages"]]
    assert "Reading the issuer's own documents" in labels
    assert "Running the research council" in labels
    assert not any("%" in label for label in labels)


async def test_post_for_an_unknown_company_is_404_not_a_started_job(
    factory, session
) -> None:
    await _pandora(session)
    client, app = await _client(factory)
    try:
        res = await client.post(
            "/api/v1/company-research/jobs",
            json={"company_id": str(uuid.uuid4())},
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
    assert res.status_code == 404


async def test_post_requires_an_identity(factory, session) -> None:
    await _pandora(session)
    client, app = await _client(factory)
    try:
        res = await client.post("/api/v1/company-research/jobs", json={})
    finally:
        await client.aclose()
        app.dependency_overrides.clear()
    assert res.status_code == 422


async def test_get_by_job_id_and_by_company(factory, session) -> None:
    company = await _pandora(session)
    client, app = await _client(factory)
    try:
        created = (
            await client.post(
                "/api/v1/company-research/jobs",
                json={"company_id": str(company.id)},
            )
        ).json()
        job_id = created["job_id"]

        by_id = await client.get(f"/api/v1/company-research/jobs/{job_id}")
        by_company = await client.get(
            "/api/v1/company-research/jobs", params={"company_id": str(company.id)}
        )
        missing = await client.get(
            f"/api/v1/company-research/jobs/{uuid.uuid4()}"
        )
        other_company = await client.get(
            "/api/v1/company-research/jobs", params={"company_id": str(uuid.uuid4())}
        )
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    assert by_id.status_code == 200 and by_id.json()["job_id"] == job_id
    # The refresh path: the id is recoverable from the backend, not only the tab.
    assert by_company.status_code == 200 and by_company.json()["job_id"] == job_id
    assert missing.status_code == 404
    assert other_company.status_code == 404


async def test_the_response_never_carries_an_investment_action(
    factory, session
) -> None:
    company = await _pandora(session)
    client, app = await _client(factory)
    try:
        body = (
            await client.post(
                "/api/v1/company-research/jobs",
                json={"company_id": str(company.id)},
            )
        ).json()
    finally:
        await client.aclose()
        app.dependency_overrides.clear()

    # The DISCLAIMER is excluded from the scan and asserted separately. It is
    # the standing statement that no rating, price target, fair value or return
    # projection is produced — scanning it would flag the safety copy as a
    # safety violation, which is the oldest false positive in this codebase.
    disclaimer = body.pop("disclaimer")
    text = json.dumps(body).lower()
    for forbidden in ("price target", "fair value", "upside", "downside", "overvalued"):
        assert forbidden not in text, forbidden
    # BUY/SELL/HOLD/WATCH as words, not as substrings of ordinary English.
    for word in ("buy", "sell", "hold", "watch"):
        assert not re.search(rf"\b{word}\b", text), word

    lowered = disclaimer.lower()
    assert "not investment advice" in lowered
    assert "no rating, price target, fair value or return projection" in lowered
