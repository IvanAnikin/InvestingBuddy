"""External research must survive into the SECOND run of a company, and the third.

WHAT WENT WRONG IN PRODUCTION
=============================
The first live MRNA run searched the web — five searches, six sources retrieved, one
promoted to evidence. **Every run after it silently did not.** Zero searches, zero
leads, the whole V3 stage finishing in under four seconds, reporting only a gap.

A two-step interaction that cannot appear on a first run:

1. Run one leaves ``recent_external_developments`` unanswered, so it becomes a gap.
2. Run two turns that gap into a question with **empty** ``required_tools`` — a gap
   record does not carry them — and ``questions.setdefault`` cannot then supply the
   canonical definition, because the key is already present.

With no external tool required, the V3.12 eligibility rule (which exists so that turning
a flag on cannot re-route corpus work to a paid vendor) disqualifies every role holding
an external tool. **The external role is excluded from its own question.**

Nothing reported it. The run succeeded and the answer was a gap.

WHY THIS FILE USES REAL POSTGRESQL
==================================
The defect is persisted-state dependent: it needs run one's gaps to exist when run two
plans. SQLite proves nothing about it, and this campaign has twice been bitten by
believing otherwise.

Skipped without ``V3_TEST_POSTGRES_URL``; CI sets it against a ``postgres:16`` service.
"""

from __future__ import annotations

import os
import uuid
from typing import Any

import pytest

from app.core.config import Settings
from app.services.director.planner import plan_research
from app.services.research_mode import parse_mode

pytestmark = pytest.mark.anyio

EXTERNAL = "recent_external_developments"
EXTERNAL_TOOLS = {"search_web", "fetch_public_source"}

POSTGRES_URL = os.environ.get("V3_TEST_POSTGRES_URL", "")
requires_postgres = pytest.mark.skipif(
    not POSTGRES_URL, reason="set V3_TEST_POSTGRES_URL to a PostgreSQL at head 038"
)


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        "v3_deepseek_search_enabled": True,
        "deepseek_api_key": "planning-only-not-a-real-key",
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


async def _plan(gaps: tuple, **over: Any):  # noqa: ANN202
    return await plan_research(
        subject="MRNA:US",
        mode=parse_mode("standard"),
        playbooks=[],
        prior_open_gaps=gaps,
        cfg=_cfg(**over),
    )


def _owner(plan: Any, key: str) -> str | None:
    for task in plan.tasks:
        if key in task.question_keys:
            return task.role_id
    return None


class TestThreeConsecutiveRuns:
    """The criterion: when an unresolved externally-researchable task survives into the
    next run, its tool requirements and role eligibility must survive with it."""

    async def test_the_external_task_survives_all_three_runs(self) -> None:
        carried: tuple = ()
        for run in (1, 2, 3):
            plan = await _plan(carried)
            question = next((q for q in plan.questions if q.key == EXTERNAL), None)
            assert question is not None, f"run {run}: the external question vanished"
            assert set(question.required_tools) == EXTERNAL_TOOLS, (
                f"run {run}: required_tools became {sorted(question.required_tools)}"
            )
            assert _owner(plan, EXTERNAL) == "external_research_analyst", (
                f"run {run}: assigned to {_owner(plan, EXTERNAL)}, which cannot search"
            )
            assert not any(EXTERNAL in d for d in plan.degraded), (
                f"run {run}: reported as degraded despite correct assignment"
            )
            # THE ASSERTION THIS FILE ORIGINALLY LACKED.
            #
            # A gap's description is "No citable evidence was retrieved for 'x'.", and
            # `RecalledGap.as_question()` hands that to the planner as the question
            # TEXT — which the investigator then sends to a paid provider as
            # `f"{subject}: {question.text}"`. Restoring `required_tools` alone seats
            # the external role and makes it search, every repeat run for ever, for a
            # sentence about this platform's own bookkeeping. The first version of this
            # test asserted the tools and the role and passed while the query was
            # nonsense. Found by review.
            assert "No citable evidence" not in question.text, (
                f"run {run}: the vendor would be asked {question.text!r}"
            )
            assert "headline figures" in question.text, (
                f"run {run}: the canonical question text was not restored"
            )
            if run > 1:
                assert question.origin == "prior_gap", (
                    "re-asking must stay visible as a re-ask"
                )
            # What the next run inherits: the question went unanswered, so it is a gap.
            carried = (
                (EXTERNAL, f"No citable evidence was retrieved for '{EXTERNAL}'."),
                ("revenue_trajectory", "No citable evidence was retrieved."),
            )

    async def test_it_is_not_satisfied_by_searching_unconditionally(self) -> None:
        """With the capability switched OFF, no run acquires it. The fix restores
        canonical metadata; it does not force every run to reach the open web."""
        carried: tuple = ()
        for _run in (1, 2, 3):
            plan = await _plan(carried, v3_deepseek_search_enabled=False)
            question = next((q for q in plan.questions if q.key == EXTERNAL), None)
            if question is not None:
                assert not (set(question.required_tools) & EXTERNAL_TOOLS)
            assert _owner(plan, EXTERNAL) != "external_research_analyst"
            carried = ((EXTERNAL, "No citable evidence was retrieved."),)


class TestTheNegativeControl:
    async def test_a_non_external_task_gains_no_search_capability(self) -> None:
        """Restoring canonical metadata must not hand search to a question that never
        asked for it — that is the defect the eligibility rule was written to prevent."""
        carried = (
            ("recent_disclosure", "No citable evidence was retrieved."),
            ("profitability", "No citable evidence was retrieved."),
        )
        plan = await _plan(carried)
        for key in ("recent_disclosure", "profitability"):
            question = next((q for q in plan.questions if q.key == key), None)
            assert question is not None
            assert not (set(question.required_tools) & EXTERNAL_TOOLS), (
                f"{key} acquired {sorted(set(question.required_tools) & EXTERNAL_TOOLS)}"
            )
            assert _owner(plan, key) != "external_research_analyst"

    async def test_an_unknown_gap_key_gains_nothing(self) -> None:
        plan = await _plan((("a_gap_with_no_definition", "Unresolved."),))
        question = next(
            (q for q in plan.questions if q.key == "a_gap_with_no_definition"), None
        )
        assert question is not None
        assert not question.required_tools


class TestSilentDegradationIsReported:
    async def test_an_external_question_on_a_blind_role_is_declared(self) -> None:
        """The observability requirement. A question needing the open web assigned to a
        role that cannot reach it is allowed — the role can still answer from internal
        evidence — but the run must say so, or a four-second zero-search run looks
        exactly like a thorough one."""
        # Force the shape: the capability is off, so the canonical tools are not
        # restored and no external role is seated, yet the question still exists.
        plan = await _plan(
            ((EXTERNAL, "No citable evidence was retrieved."),),
            v3_deepseek_search_enabled=False,
        )
        owner = _owner(plan, EXTERNAL)
        if owner is not None and owner != "external_research_analyst":
            assert any(EXTERNAL in d for d in plan.degraded), (
                f"assigned to {owner} with no degraded reason; plan.degraded={plan.degraded}"
            )


@requires_postgres
class TestAcrossRealRunsOnPostgres:
    """The same guarantee driven through real persisted state rather than a tuple of
    gaps handed to the planner — because the defect lives in what run N leaves behind."""

    @pytest.fixture
    async def pg(self):  # noqa: ANN201
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

        engine = create_async_engine(POSTGRES_URL, future=True)
        yield async_sessionmaker(engine, expire_on_commit=False)
        await engine.dispose()

    async def test_three_runs_against_persisted_memory(self, pg) -> None:  # noqa: ANN001
        from sqlalchemy import text

        from app.models.company import Company
        from app.services.memory import store as memory

        company_id = uuid.uuid4()
        async with pg() as session:
            session.add(
                Company(
                    id=company_id,
                    ticker=f"RP{uuid.uuid4().hex[:5].upper()}",
                    exchange="NASDAQ",
                    name="Repeat run acceptance",
                    status="new",
                )
            )
            await session.commit()

        seen: list[str | None] = []
        for _run in (1, 2, 3):
            async with pg() as session:
                prior = await memory.recall(session, company_id=company_id)
                carried = prior.carry_forward_questions if prior.exists else ()
                plan = await _plan(tuple(carried))
                seen.append(_owner(plan, EXTERNAL))
                # Persist the gap this run would leave, so the NEXT run inherits it.
                run_id = uuid.uuid4()
                await session.execute(
                    text(
                        # `stopped_by` is required by a CHECK constraint: a stopped run
                        # must name the limit that stopped it. The database enforcing
                        # that is exactly why this test runs on PostgreSQL.
                        "INSERT INTO research_runs (id, company_id, mode, status, "
                        "stopped_by, started_at, rounds_completed) VALUES (:i, :c, "
                        "'standard', 'stopped', 'max_rounds', now(), 1)"
                    ),
                    {"i": run_id, "c": company_id},
                )
                await session.execute(
                    text(
                        "INSERT INTO research_gaps (id, research_run_id, gap_type, "
                        "description, status, created_at) VALUES (:i, :r, "
                        "'evidence_unavailable', :d, 'open', now())"
                    ),
                    {
                        "i": uuid.uuid4(),
                        "r": run_id,
                        "d": f"No citable evidence was retrieved for '{EXTERNAL}'.",
                    },
                )
                await session.commit()

        assert seen == ["external_research_analyst"] * 3, (
            f"the external role was lost across repeat runs: {seen}"
        )
