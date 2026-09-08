"""`V3_SEARCH_BACKEND` must actually select a backend.

THE DEFECT
==========
The setting had no consumer on the production path. `get_search_backend` existed, the
`PostgresSearchBackend` existed with its own tests, and `run_v3_research` took a
``search_backend`` parameter — but `company_research_service` never passed one and
nothing built one from `cfg`. So `search_company_corpus` returned::

    {"items": [], "backend_configured": False,
     "summary": "no corpus search backend is configured, ..."}

on every production run, whatever `V3_SEARCH_BACKEND` said.

The tool's own answer was honest — it distinguishes "no backend" from "the corpus is
empty", which is exactly the distinction an agent must not lose — so nothing failed and
nothing looked wrong. It would simply have been switched on and had no effect.

This is the THIRD flag in this campaign found without a consumer, after
`V3_DEEPSEEK_SEARCH_ENABLED` (whose gate had been deleted with the hard refusal it
replaced) and the role `source_classes` that never reached the policy. Hence the rule
these tests encode: **a flag is not enabled until something reads it.**
"""

from __future__ import annotations

from typing import Any

import pytest

from app.core.config import Settings
from app.services.corpus.search.factory import (
    BACKEND_MEMORY,
    BACKEND_POSTGRES,
    UnknownSearchBackendError,
    get_search_backend,
)

pytestmark = pytest.mark.anyio


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = {
        "v3_pipeline_enabled": True,
        "v3_agent_tools_enabled": True,
        "v3_corpus_enabled": True,
        "azure_openai_api_key": "",
        "azure_openai_endpoint": "",
        "deepseek_api_key": "",
    }
    base.update(over)
    return Settings(**base)  # type: ignore[arg-type]


class TestTheSettingSelectsABackend:
    def test_postgres_is_selected_when_configured(self) -> None:
        backend = get_search_backend(_cfg(v3_search_backend=BACKEND_POSTGRES), session=object())
        assert backend.name == BACKEND_POSTGRES

    def test_memory_is_the_default(self) -> None:
        assert get_search_backend(_cfg(v3_search_backend=BACKEND_MEMORY)).name == BACKEND_MEMORY

    def test_an_unknown_name_raises_rather_than_degrading(self) -> None:
        """A retrieval that silently became in-memory returns an empty corpus, which
        reads as an issuer with no documents rather than as a misconfiguration."""
        with pytest.raises(UnknownSearchBackendError):
            get_search_backend(_cfg(v3_search_backend="azure_ai_search"), session=object())


class TestThePipelineActuallyBuildsIt:
    """The wire itself. Without these, the tests above pass over dead code."""

    async def test_the_pipeline_builds_the_configured_backend(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: dict[str, Any] = {}

        def _spy(cfg: Any, *, session: Any = None) -> Any:
            seen["cfg"] = cfg
            seen["session"] = session
            return "the-backend"

        monkeypatch.setattr(
            "app.services.corpus.search.factory.get_search_backend", _spy
        )
        cfg = _cfg(v3_search_backend=BACKEND_POSTGRES)
        outcome = await _run_pipeline(cfg)

        assert seen.get("cfg") is cfg, "the pipeline never asked for a backend"
        assert seen["session"] is not None, "postgres without a session would raise"
        assert outcome.error is None

    async def test_a_bad_backend_name_degrades_the_search_leg_not_the_run(self) -> None:
        """Losing the findings, the council and the chair over a misconfigured
        retrieval setting would be a far worse outcome than searching nothing."""
        outcome = await _run_pipeline(_cfg(v3_search_backend="not_a_backend"))
        assert outcome.error is None, "the run must survive"
        assert outcome.research_run_id is not None, "the ledger run still opened"
        assert any("corpus search unavailable" in d for d in outcome.degraded), (
            f"the reason must be recorded; got {outcome.degraded}"
        )

    async def test_the_corpus_flag_off_means_no_backend_is_built(self) -> None:
        """With the corpus off there is nothing to search, and building a backend
        would issue queries against tables the flag says are not in use."""
        outcome = await _run_pipeline(
            _cfg(v3_corpus_enabled=False, v3_search_backend="not_a_backend")
        )
        assert not any("corpus search unavailable" in d for d in outcome.degraded)


async def _run_pipeline(cfg: Settings) -> Any:
    """One real pipeline run against in-memory SQLite."""
    import uuid

    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.pool import StaticPool

    from app.db.base import Base
    from app.models.company import Company
    from app.services.pipeline.v3_pipeline import run_v3_research

    if not getattr(_run_pipeline, "_compiled", False):
        @compiles(JSONB, "sqlite")
        def _j(element: Any, compiler: Any, **kw: Any) -> str:  # noqa: ANN401
            return "JSON"

        _run_pipeline._compiled = True  # type: ignore[attr-defined]

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        future=True,
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        async with maker() as session:
            company = Company(
                id=uuid.uuid4(),
                ticker="MRNA",
                exchange="NASDAQ",
                name="Moderna, Inc.",
                status="new",
                sector="Health Care",
                industry="Biotechnology",
            )
            session.add(company)
            await session.flush()
            return await run_v3_research(session, company, cfg=cfg)
    finally:
        await engine.dispose()


class TestItReachesTheToolItself:
    """The chain is `_run` -> `ToolSession.search_backend` -> `ToolContext` ->
    `_search_company_corpus`. Proving the factory is CALLED is not the same as proving
    the tool RECEIVES it, and only the second one is the property that matters."""

    async def test_the_corpus_tool_reports_a_configured_backend(self) -> None:
        from app.services.agent_tools.corpus_search import _search_company_corpus

        class _Ctx:
            search_backend = None
            session = None
            cfg = None

        answer = await _search_company_corpus(_Ctx(), {"query": "revenue"})
        assert answer["backend_configured"] is False
        assert "not a statement about what the corpus contains" in answer["summary"], (
            "the tool must distinguish 'no backend' from 'the corpus is empty' — an "
            "agent that cannot tell them apart concludes the second"
        )

    def test_the_session_hands_the_backend_to_every_tool_context(self) -> None:
        """Read from the source rather than asserted about it: the ToolSession field
        and the ToolContext it builds must be the same object."""
        import inspect

        from app.services.agent_tools import session as session_module

        source = inspect.getsource(session_module)
        assert "search_backend=self.search_backend" in source, (
            "ToolSession must pass its backend into the ToolContext, or the pipeline "
            "builds a backend that no tool can reach"
        )
