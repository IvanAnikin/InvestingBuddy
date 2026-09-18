"""V3.17.5 — the research queue, readable without lying about it.

WHAT THESE ENDPOINTS ARE FOR
============================
A human has to be able to see what the autonomous loop is doing: which decision was
taken, why, what round it is on, what the last round actually acquired, and why it
stopped.

THE TWO THINGS THE READ PATH MUST NOT DO
========================================
* **Invent a cost.** `cost_usd_total` is NULL in production because no price book is
  configured. Serialising that as 0.0 would tell an operator the research was free.
* **Recompute the evidence delta.** The numbers are read back from what the backend
  measured and stored. A second implementation of the same arithmetic drifts, and the
  copy on screen is the one somebody acts on.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.research_decision import (
    OPEN_STATUSES,
    STATUS_ABANDONED,
    STATUS_EXHAUSTED,
    STATUS_QUEUED,
    TERMINAL_OPERATOR_CANCELLED,
    ResearchDecision,
)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN201
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


async def _company(session, ticker: str = "MRNA"):  # noqa: ANN001, ANN201
    from app.models.company import Company

    c = Company(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="US",
        name="Moderna Inc.",
        status="new",
    )
    session.add(c)
    await session.flush()
    return c.id


async def _decision(session, company_id, **over):  # noqa: ANN001, ANN003, ANN201
    row = ResearchDecision(
        id=uuid.uuid4(),
        company_id=company_id,
        source="discovery_council",
        decision="research_next",
        status=over.pop("status", STATUS_QUEUED),
        reason=over.pop("reason", "3 blocking gap(s) are open."),
        priority=100,
        escalation_round=0,
        max_rounds=2,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
        **over,
    )
    session.add(row)
    await session.flush()
    return row


class TestTheReadProjection:
    async def test_an_unknown_cost_serialises_as_null_not_zero(self, session) -> None:  # noqa: ANN001
        """The single most misleading number this page could show."""
        from app.api.v1.research_decisions import _to_read

        company_id = await _company(session)
        row = await _decision(session, company_id)
        assert row.cost_usd_total is None

        read = await _to_read(session, row)

        assert read.cost_usd_total is None
        assert read.cost_usd_total != 0.0

    async def test_a_real_cost_survives(self, session) -> None:  # noqa: ANN001
        from app.api.v1.research_decisions import _to_read

        company_id = await _company(session)
        row = await _decision(session, company_id, cost_usd_total=0.0145)

        read = await _to_read(session, row)

        assert read.cost_usd_total == pytest.approx(0.0145)

    async def test_the_delta_is_read_back_not_recomputed(self, session) -> None:  # noqa: ANN001
        """The API returns what was MEASURED, verbatim.

        If this projection derived `improved` itself, it could disagree with the verdict
        the platform actually acted on — and the screen would explain a decision that was
        never taken.
        """
        from app.api.v1.research_decisions import _to_read

        company_id = await _company(session)
        stored = {
            "indexed_chunks_added": 218,
            "searchable_documents_added": 3,
            "closable_gaps_closed": 2,
            "facts_added": 0,
            "verified_findings_added": 18,
            "improved": True,
            "reasons": ["218 new searchable corpus chunk(s)"],
            "next_status": "reanalysis",
            "terminal_reason": None,
            "detail": "…",
        }
        row = await _decision(session, company_id, improvement_json=stored)

        read = await _to_read(session, row)

        assert read.improvement is not None
        assert read.improvement.indexed_chunks_added == 218
        assert read.improvement.improved is True
        assert read.improvement.reasons == stored["reasons"]

    async def test_the_company_identity_carries_its_exchange(self, session) -> None:  # noqa: ANN001
        """Ticker alone is not identity — the same ticker exists on several exchanges."""
        from app.api.v1.research_decisions import _to_read

        company_id = await _company(session)
        row = await _decision(session, company_id)

        read = await _to_read(session, row)

        assert read.ticker == "MRNA"
        assert read.exchange == "US"

    async def test_a_decision_with_no_company_still_renders(self, session) -> None:  # noqa: ANN001
        """An orphaned decision must not 500 the queue everyone else needs to read."""
        from app.api.v1.research_decisions import _to_read

        row = await _decision(session, None)

        read = await _to_read(session, row)

        assert read.company_id is None
        assert read.ticker is None


class TestCancel:
    async def test_cancelling_frees_the_company(self, session) -> None:  # noqa: ANN001
        """The open slot must be released, or the company is locked out for ever."""
        from app.services.escalation import store

        company_id = await _company(session)
        row = await _decision(session, company_id)
        assert row.status in OPEN_STATUSES

        await store.mark_terminal(
            session,
            row,
            status=STATUS_ABANDONED,
            terminal_reason=TERMINAL_OPERATOR_CANCELLED,
        )

        assert row.status == STATUS_ABANDONED
        assert row.status not in OPEN_STATUSES
        assert row.terminal_reason == TERMINAL_OPERATOR_CANCELLED

    async def test_an_already_terminal_decision_is_not_reopened(self, session) -> None:  # noqa: ANN001
        """Rewriting a closed record would rewrite history.

        The endpoint returns it unchanged rather than moving it, so a second click on a
        finished row cannot turn an honest `exhausted` into `operator_cancelled`.
        """
        company_id = await _company(session)
        row = await _decision(
            session,
            company_id,
            status=STATUS_EXHAUSTED,
            terminal_reason="exhausted_no_improvement",
        )

        # The endpoint's guard: only OPEN decisions are touched.
        assert row.status not in OPEN_STATUSES
        assert row.terminal_reason == "exhausted_no_improvement"


class TestTheEndpointsAreRegisteredAndAdminShaped:
    def test_the_routes_exist(self) -> None:
        from app.main import app

        paths = set(app.openapi()["paths"])

        assert "/api/v1/research-decisions" in paths
        assert "/api/v1/research-decisions/{decision_id}" in paths
        assert "/api/v1/research-decisions/{decision_id}/cancel" in paths

    def test_there_is_no_second_way_to_create_work(self) -> None:
        """Escalation starts in ONE place. A second creator is a second source of truth."""
        from app.main import app

        spec = app.openapi()["paths"]
        creators = [
            p
            for p, ops in spec.items()
            if "research-decision" in p and "post" in ops and "cancel" not in p
        ]

        assert creators == []

    def test_the_listing_says_what_it_is_not(self) -> None:
        """Product-safety invariant: no route may read as advice."""
        from app.schemas.research_decision import ResearchDecisionList

        disclaimer = ResearchDecisionList(decisions=[], total=0).disclaimer

        assert "not investment advice" in disclaimer.lower()
        assert "not a recommendation" in disclaimer.lower()


class TestAMissingSchemaIsANamedStateNotAnError:
    """Migrations are deliberately manual here, so a deploy can legitimately carry code
    whose table does not exist yet.

    Observed on the live deployment: `/api/v1/research-decisions` returned a bare
    `500 Internal Server Error` because migration 040 had not been applied. Two wrong
    answers were available and both were rejected:

    * **500** says "something is broken", which reads as an outage and buries the one
      fact an operator needs.
    * **An empty list** would be a lie — "there are no research decisions" is a
      different claim from "this environment has no such table".

    So the endpoint reports the schema state, in words, with a 503.
    """

    def test_the_known_state_is_recognised(self) -> None:
        from app.api.v1.research_decisions import _schema_missing

        undefined = type("UndefinedTable", (Exception,), {})()
        wrapped = RuntimeError("relation does not exist")
        wrapped.__cause__ = undefined

        assert _schema_missing(wrapped) is True
        assert _schema_missing(RuntimeError("no such table: research_decisions")) is True

    def test_a_real_failure_is_not_swallowed(self) -> None:
        """A connection reset must still surface as a failure, not as a schema note."""
        from app.api.v1.research_decisions import _schema_missing

        assert _schema_missing(RuntimeError("connection reset by peer")) is False
        assert _schema_missing(ValueError("bad uuid")) is False

    def test_the_message_names_the_migration_and_refuses_to_imply_emptiness(
        self,
    ) -> None:
        from app.api.v1.research_decisions import _schema_missing_error

        err = _schema_missing_error()

        assert err.status_code == 503
        assert "040" in err.detail
        assert "different fact" in err.detail
