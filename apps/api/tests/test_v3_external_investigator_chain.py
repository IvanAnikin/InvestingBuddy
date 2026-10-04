"""V3.12 — the Investigator's external chain, through the REAL tool session.

WHY THIS FILE IS SEPARATE FROM THE UNIT TESTS
=============================================
``test_v3_external_research_integration.py`` calls the tool handlers directly. That
proves each tool, and proves nothing about the wiring — whether the Director seats the
role, whether the session permits the tools, whether the Investigator chains search into
verification, and whether a verified external claim becomes a citable id a finding can
carry.

So this file runs the **real** ``ToolSession`` against the **real** registry, with a
scripted search provider and a scripted fetcher, and asserts on the audit rows the
session wrote. Nothing here is a mock of the platform's own machinery: the permission
check, the budget, the tool-call ledger and the evidence harvest are the production ones.

OPEN-WEB W5 CHANGED THE CHAIN
=============================
``search_web`` now returns CANDIDATE URLs from the configured web search provider, not
claims. There is therefore nothing for the Investigator to chain into verification: the
search mints nothing and the Investigator offers the model nothing from it. Evidence is
still minted by exactly one function — ``fetch_public_source`` — and only for a claim
that InvestingBuddy's own fetch confirmed against bytes it holds. Both properties are
asserted below, through the real session.

WHAT IS SCRIPTED AND WHY
========================
The search provider (so no vendor is called) and the document fetcher (so no network is
touched). Everything between them is real.
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

from app.core.config import Settings
from app.db.base import Base
from app.services.agent_tools.contracts import (
    TOOL_FETCH_PUBLIC_SOURCE,
    TOOL_SEARCH_WEB,
    ToolBudget,
)
from app.services.agent_tools.external import EXTERNAL_EVIDENCE_PREFIX
from app.services.agent_tools.policy import policy_for
from app.services.agent_tools.registry import ToolRegistry
from app.services.agent_tools.session import ToolSession
from app.services.agents.investigator import LLMInvestigator
from app.services.director.planner import PlannedQuestion
from app.services.ledger import store as ledger

pytestmark = pytest.mark.anyio


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


CLAIM = "Total revenue was $145 million"
REAL_URL = "https://www.sec.gov/Archives/edgar/data/1682852/x/exhibit991.htm"
#: Deliberately phrased the way a real 10-Q is — "quarterly period ended", "three
#: months ended" — and NOT "Second Quarter 2026". Review caught the first version using
#: the one phrasing that makes `periods_in` emit a quarter key, which dodged the very
#: false-rejection the period gate had to be fixed for.
DOC = (
    "Quarterly period ended June 30, 2026. For the three months ended June 30, 2026, "
    "total revenue was $145 million compared with $142 million."
)


SEARCH_FIXTURE: dict[str, Any] = {
    "results": [
        {
            "title": "Moderna reports second quarter 2026 financial results",
            "url": REAL_URL,
            "content": "Synthetic snippet: total revenue was $145 million.",
            "score": 0.9,
            "published_date": "Thu, 06 Aug 2026 12:00:00 GMT",
        }
    ],
    "request_id": "synthetic-chain-1",
    "usage": {"credits": 1},
}


class _AnyQuery(dict):
    def get(self, key: Any, default: Any = None) -> Any:  # noqa: ANN401
        return SEARCH_FIXTURE


def _provider(**kw: Any) -> Any:
    from app.integrations.search.fake import FakeWebSearchProvider

    return FakeWebSearchProvider(fixtures=_AnyQuery(), **kw)


@dataclass
class _Fetch:
    """A document fetch result shaped like the safe fetcher's."""

    content: bytes
    status_code: int = 200
    blocked: bool = False
    error: str | None = None
    final_url: str | None = REAL_URL
    document_type: str = "html"
    status_class: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and not self.blocked and self.status_code < 400


WEB_FLAGS: dict[str, Any] = {
    "v3_company_web_research_enabled": True,
    "v3_web_search_enabled": True,
    "v3_web_search_provider": "fake",
    # The tool surface itself must be on, or every call is refused as `disabled`.
    "v3_agent_tools_enabled": True,
}


def _tool_session(session: Any, monkeypatch: Any, provider: Any, fetch: _Fetch, *, tools: Any) -> Any:
    import app.services.agents.routing as routing
    import app.services.providers.leads as leads_mod
    from app.services.agent_tools.external import register_external_tools

    monkeypatch.setattr(routing, "web_search_provider_for", lambda _cfg: provider)

    async def _fetcher(url, *, allowed_domains, cfg, resolve_ip=True):  # noqa: ANN001
        return fetch

    monkeypatch.setattr(leads_mod, "_default_fetcher", _fetcher)
    cfg = Settings(**WEB_FLAGS)
    return ToolSession(
        registry=register_external_tools(ToolRegistry(), cfg=cfg),
        policy=policy_for(
            "external_research_analyst",
            tools=tools,
            budget=ToolBudget(max_calls=10),
            access_classes={"public_web"},
        ),
        cfg=cfg,
        db=session,
        company_id=uuid.uuid4(),
    )


async def _run(session: Any, monkeypatch: Any, *, provider: Any, fetch: _Fetch) -> Any:
    """One real question through the real session and the real Investigator."""
    tool_session = _tool_session(
        session, monkeypatch, provider, fetch,
        tools={TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE},
    )
    investigator = LLMInvestigator(
        session=tool_session, company_id=tool_session.company_id, ticker="MRNA",
        exchange="NASDAQ",
    )
    return await investigator.investigate(
        role_id="external_research_analyst",
        questions=[
            PlannedQuestion(
                key="external_q",
                text="What was the most recent reported quarterly revenue?",
                origin=ledger.ORIGIN_DIRECTOR,
                required_tools=frozenset({TOOL_SEARCH_WEB}),
                priority=1,
            )
        ],
        round_index=0,
        remaining_tool_calls=10,
    )


async def _fetch_tool(
    session: Any, monkeypatch: Any, fetch: _Fetch, **overrides: Any
) -> Any:
    tool_session = _tool_session(
        session, monkeypatch, _provider(), fetch,
        tools={TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE},
    )
    arguments = {"url": REAL_URL, "claim": CLAIM, "claimed_value": "145",
                 "claimed_period": "2026-Q2", "provider": "web_search"}
    arguments.update(overrides)
    return await tool_session.call(TOOL_FETCH_PUBLIC_SOURCE, arguments)


class TestTheChainRunsThroughTheRealSession:
    async def test_search_returns_candidates_and_the_investigator_mints_nothing(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """The search leg, asserted on the session's own audit rows."""
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord
        from app.models.research_tool_call import ResearchToolCall

        captured: dict[str, Any] = {}
        original = LLMInvestigator._write_up

        async def _capture(self, role_id, question, evidence, **kw):  # noqa: ANN001, ANN003
            captured["ids"] = [e.citation_id for e in evidence]
            captured["kinds"] = [e.kind for e in evidence]
            return await original(self, role_id, question, evidence, **kw)

        monkeypatch.setattr(LLMInvestigator, "_write_up", _capture)
        provider = _provider()
        await _run(
            session, monkeypatch, provider=provider, fetch=_Fetch(content=DOC.encode())
        )

        calls = (await session.execute(select(ResearchToolCall))).scalars().all()
        names = [c.tool_name for c in calls]
        assert TOOL_SEARCH_WEB in names, "the Investigator called the search tool"
        assert TOOL_FETCH_PUBLIC_SOURCE not in names, (
            "a candidate is not a claim: nothing is chained into verification"
        )
        assert all(c.outcome == "ok" for c in calls), [c.outcome for c in calls]
        assert len(provider.requests) == 1
        # Nothing from the search is citable and no lead was written.
        assert not any(i.startswith(EXTERNAL_EVIDENCE_PREFIX) for i in captured.get("ids", []))
        assert TOOL_SEARCH_WEB not in captured.get("kinds", [])
        assert (await session.execute(select(ResearchLeadRecord))).scalars().all() == []

    async def test_a_verified_claim_through_our_own_fetch_becomes_citable(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """``fetch_public_source`` is still the ONLY function that mints an id."""
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord

        result = await _fetch_tool(session, monkeypatch, _Fetch(content=DOC.encode()))
        assert result.ok
        items = (result.payload or {}).get("items") or []
        assert items and str(items[0].get("evidence_id", "")).startswith(
            EXTERNAL_EVIDENCE_PREFIX
        )
        leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
        assert len(leads) == 1 and leads[0].status == "verified"
        assert leads[0].fetched_content_hash, "verified means bytes WE fetched"
        assert leads[0].promoted_evidence_id.startswith(EXTERNAL_EVIDENCE_PREFIX)

    async def test_a_rejected_claim_records_no_evidence_id(
        self, session: Any, monkeypatch: Any
    ) -> None:
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord

        result = await _fetch_tool(
            session, monkeypatch, _Fetch(content=DOC.encode()), claimed_value="999999"
        )
        assert result.ok
        assert not any(
            i.get("evidence_id") for i in ((result.payload or {}).get("items") or [])
        )
        leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
        assert leads and leads[0].status == "rejected"
        assert leads[0].rejection_reason == "value_mismatch"
        assert leads[0].promoted_evidence_id is None

    async def test_a_blocked_fetch_never_becomes_evidence(
        self, session: Any, monkeypatch: Any
    ) -> None:
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord

        await _fetch_tool(
            session, monkeypatch, _Fetch(content=b"", blocked=True, error="refused by policy")
        )
        leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
        assert leads and leads[0].status == "rejected"
        assert leads[0].rejection_reason == "source_not_permitted"

    async def test_an_outage_leaves_the_question_a_gap_not_a_finding(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """No search ran: nothing citable exists, and recall does not stand in for it."""
        from app.integrations.search.fake import MODE_OUTAGE

        outcome = await _run(
            session, monkeypatch, provider=_provider(mode=MODE_OUTAGE),
            fetch=_Fetch(content=DOC.encode()),
        )
        assert outcome.findings == [], "no finding may rest on a search that did not run"
        assert outcome.gaps, "and the question is recorded as unanswered"

    async def test_the_session_refuses_the_tools_a_role_does_not_hold(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """The permission surface is the session's, not the Investigator's.

        A role without `fetch_public_source` gets a refusal ROW, not an exception, and
        the refusal is the most diagnostic record in the table: it says the Director
        seated the wrong role for the question.
        """
        from sqlalchemy import select

        from app.models.research_tool_call import ResearchToolCall

        tool_session = _tool_session(
            session, monkeypatch, _provider(), _Fetch(content=DOC.encode()),
            tools={TOOL_SEARCH_WEB},  # Search only. No fetch.
        )
        result = await tool_session.call(
            TOOL_FETCH_PUBLIC_SOURCE, {"url": REAL_URL, "claim": CLAIM}
        )
        assert result.ok is False
        rows = (await session.execute(select(ResearchToolCall))).scalars().all()
        assert rows and rows[0].outcome == "refused"
        assert rows[0].refusal_reason == "tool_not_permitted"
