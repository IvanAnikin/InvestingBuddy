"""V3.12 — the Investigator's external chain, through the REAL tool session.

WHY THIS FILE IS SEPARATE FROM THE UNIT TESTS
=============================================
``test_v3_external_research_integration.py`` calls the tool handlers directly. That
proves each tool, and proves nothing about the wiring — whether the Director seats the
role, whether the session permits the tools, whether the Investigator chains search into
verification, and whether a verified external claim becomes a citable id a finding can
carry.

So this file runs the **real** ``ToolSession`` against the **real** registry, with a
scripted provider and a scripted fetcher, and asserts on the audit rows the session
wrote. Nothing here is a mock of the platform's own machinery: the permission check, the
budget, the tool-call ledger and the evidence harvest are the production ones.

WHAT IS SCRIPTED AND WHY
========================
The provider (so no vendor is called) and the document fetcher (so no network is
touched). Everything between them is real. The live counterpart — a real provider, a real
SEC document, a real fetch — is the acceptance run recorded in the slice document; this
file is what keeps the wiring honest in CI, where neither is available.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
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
from app.services.providers.contracts import ResearchLead, ResearchProviderResult

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


@dataclass
class _Provider:
    """A research provider that has already retrieved. Scripted, not mocked-out."""

    leads: list[ResearchLead] = field(default_factory=list)
    opened: tuple[str, ...] = (REAL_URL,)
    asked: list[str] = field(default_factory=list)

    async def investigate(self, *, question, context=None, max_seconds=300, domains=None):  # noqa: ANN001, ANN201
        self.asked.append(question)
        return ResearchProviderResult(
            provider="deepseek",
            model="deepseek-v4-flash",
            task_id=str(uuid.uuid4()),
            status="completed",
            started_at=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ),
            research_leads=list(self.leads),
            raw_provider_metadata={
                "trace": {"opened_urls": list(self.opened), "queries": ["q"]},
                "retrieval_backed": True,
                "leads_citing_unopened_pages": 0,
            },
        )


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


def _lead(**kw: Any) -> ResearchLead:
    return ResearchLead(
        claim_text=kw.get("claim", CLAIM),
        provider="deepseek",
        model="deepseek-v4-flash",
        provider_task_id="t",
        claimed_source_url=kw.get("url", REAL_URL),
        claimed_value=kw.get("value", "145"),
        claimed_period=kw.get("period", "2026-Q2"),
    )


async def _run(
    session: Any, monkeypatch: Any, *, provider: _Provider, fetch: _Fetch
) -> Any:
    """One real question through the real session and the real Investigator."""
    import app.services.agents.routing as routing
    import app.services.providers.leads as leads_mod
    from app.services.agent_tools.external import register_external_tools

    monkeypatch.setattr(routing, "research_provider_for", lambda _cfg: provider)

    async def _fetcher(url, *, allowed_domains, cfg, resolve_ip=True):  # noqa: ANN001
        return fetch

    monkeypatch.setattr(leads_mod, "_default_fetcher", _fetcher)

    cfg = Settings(
        v3_deepseek_search_enabled=True,
        deepseek_api_key="k",
        # The tool surface itself must be on, or every call is refused as `disabled`.
        v3_agent_tools_enabled=True,
    )
    registry = register_external_tools(ToolRegistry(), cfg=cfg)
    company_id = uuid.uuid4()
    tool_session = ToolSession(
        registry=registry,
        policy=policy_for(
            "external_research_analyst",
            tools={TOOL_SEARCH_WEB, TOOL_FETCH_PUBLIC_SOURCE},
            budget=ToolBudget(max_calls=10),
            access_classes={"public_web"},
        ),
        cfg=cfg,
        db=session,
        company_id=company_id,
    )
    investigator = LLMInvestigator(
        session=tool_session, company_id=company_id, ticker="MRNA", exchange="NASDAQ"
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


class TestTheChainRunsThroughTheRealSession:
    async def test_search_then_verify_then_citable_evidence(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """The whole point of V3.12, asserted on the session's own audit rows."""
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord
        from app.models.research_tool_call import ResearchToolCall

        provider = _Provider(leads=[_lead()])
        await _run(
            session,
            monkeypatch,
            provider=provider,
            fetch=_Fetch(content=DOC.encode()),
        )

        calls = (await session.execute(select(ResearchToolCall))).scalars().all()
        names = [c.tool_name for c in calls]
        assert TOOL_SEARCH_WEB in names, "the Investigator called the search tool"
        assert TOOL_FETCH_PUBLIC_SOURCE in names, (
            "and chained into InvestingBuddy's own retrieval, which is the step that "
            "makes the search worth anything"
        )
        assert names.index(TOOL_SEARCH_WEB) < names.index(TOOL_FETCH_PUBLIC_SOURCE)
        assert all(c.outcome == "ok" for c in calls), [c.outcome for c in calls]

        leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
        assert len(leads) == 1, "the lead was persisted, verified or not"
        assert leads[0].status == "verified"
        assert leads[0].fetched_content_hash, "verified means bytes WE fetched"
        # The audit trail back from a citation. The column has existed since migration
        # 031 and nothing wrote it, so an auditor holding `ev:x:…` had no route to the
        # URL, the hash or the claim — which is most of what an audit trail is for.
        assert leads[0].promoted_evidence_id
        assert leads[0].promoted_evidence_id.startswith(EXTERNAL_EVIDENCE_PREFIX)

    async def test_a_rejected_lead_records_no_evidence_id(
        self, session: Any, monkeypatch: Any
    ) -> None:
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord

        await _run(
            session,
            monkeypatch,
            provider=_Provider(leads=[_lead(value="999999")]),
            fetch=_Fetch(content=DOC.encode()),
        )
        leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
        assert leads and leads[0].status == "rejected"
        assert leads[0].promoted_evidence_id is None

    async def test_the_evidence_offered_to_the_model_is_the_verified_one(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """A model may cite only ids the tools returned, so this is what decides whether
        an external claim can appear in a finding at all."""
        captured: dict[str, Any] = {}

        provider = _Provider(leads=[_lead()])

        from app.services.agents.investigator import LLMInvestigator

        original = LLMInvestigator._write_up

        async def _capture(self, role_id, question, evidence):  # noqa: ANN001
            captured["ids"] = [e.citation_id for e in evidence]
            captured["kinds"] = [e.kind for e in evidence]
            return await original(self, role_id, question, evidence)

        monkeypatch.setattr(LLMInvestigator, "_write_up", _capture)
        outcome = await _run(
            session,
            monkeypatch,
            provider=provider,
            fetch=_Fetch(content=DOC.encode()),
        )
        assert outcome is not None
        ids = captured.get("ids", [])
        assert any(i.startswith(EXTERNAL_EVIDENCE_PREFIX) for i in ids), (
            f"no external evidence reached the write-up; got {ids}"
        )
        assert TOOL_FETCH_PUBLIC_SOURCE in captured.get("kinds", [])
        # And nothing from the search itself is citable.
        assert TOOL_SEARCH_WEB not in captured.get("kinds", [])

    async def test_an_unverifiable_claim_yields_no_citable_evidence(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """The negative, through the same real machinery: our fetch says the document
        does not support the claim, so nothing citable exists and the question becomes a
        gap rather than a finding with a bad citation."""
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord

        provider = _Provider(leads=[_lead(value="999999")])
        outcome = await _run(
            session,
            monkeypatch,
            provider=provider,
            fetch=_Fetch(content=DOC.encode()),
        )
        leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
        assert leads and leads[0].status == "rejected"
        assert leads[0].rejection_reason == "value_mismatch"
        assert outcome.findings == [], "no finding may rest on a rejected claim"
        assert outcome.gaps, "and the question is recorded as unanswered"

    async def test_a_blocked_fetch_never_becomes_evidence(
        self, session: Any, monkeypatch: Any
    ) -> None:
        from sqlalchemy import select

        from app.models.research_lead import ResearchLeadRecord

        provider = _Provider(leads=[_lead()])
        await _run(
            session,
            monkeypatch,
            provider=provider,
            fetch=_Fetch(content=b"", blocked=True, error="refused by policy"),
        )
        leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
        assert leads and leads[0].status == "rejected"
        assert leads[0].rejection_reason == "source_not_permitted"

    async def test_the_session_refuses_the_tools_a_role_does_not_hold(
        self, session: Any, monkeypatch: Any
    ) -> None:
        """The permission surface is the session's, not the Investigator's.

        A role without `fetch_public_source` gets a refusal ROW, not an exception, and
        the refusal is the most diagnostic record in the table: it says the Director
        seated the wrong role for the question.
        """
        from sqlalchemy import select

        import app.services.agents.routing as routing
        from app.models.research_tool_call import ResearchToolCall
        from app.services.agent_tools.external import register_external_tools

        provider = _Provider(leads=[_lead()])
        monkeypatch.setattr(routing, "research_provider_for", lambda _cfg: provider)

        cfg = Settings(
        v3_deepseek_search_enabled=True,
        deepseek_api_key="k",
        # The tool surface itself must be on, or every call is refused as `disabled`.
        v3_agent_tools_enabled=True,
    )
        tool_session = ToolSession(
            registry=register_external_tools(ToolRegistry(), cfg=cfg),
            # Search only. No fetch.
            policy=policy_for(
                "external_research_analyst",
                tools={TOOL_SEARCH_WEB},
                budget=ToolBudget(max_calls=10),
                access_classes={"public_web"},
            ),
            cfg=cfg,
            db=session,
            company_id=uuid.uuid4(),
        )
        result = await tool_session.call(
            TOOL_FETCH_PUBLIC_SOURCE, {"url": REAL_URL, "claim": CLAIM}
        )
        assert result.ok is False
        rows = (await session.execute(select(ResearchToolCall))).scalars().all()
        assert rows and rows[0].outcome == "refused"
        assert rows[0].refusal_reason == "tool_not_permitted"
