"""Open-web W5 — the company web stage, offline (SQLite, fake provider, fake network).

The stage runs for real: the real planner, the real ``run_searches`` over
``FakeWebSearchProvider`` (recorded-shape fixtures under ``tests/fixtures/web``), the real
selector and crawl, and the real W3 ingest (process-pool extraction, classification,
corpus write). Only the network is replaced: ``deps.fetch`` stands in for
``open_web_fetch`` and serves the fixture pages, accounting against the SAME budget the
real fetcher uses so every ceiling is exercised.

``test_web_w5_postgres.py`` repeats the write path on PostgreSQL, where the foreign keys
and the independent provenance session are real.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401 - registers every table on Base.metadata
from app.core.config import Settings
from app.db.base import Base
from app.integrations.deepseek.transport import FakeDeepSeekTransport
from app.integrations.search.fake import MODE_OUTAGE, FakeWebSearchProvider
from app.models.company import Company
from app.models.research_chunk import ResearchDocumentChunk
from app.models.research_document import ResearchDocument, ResearchDocumentVersion
from app.models.web_research import WebSearchQuery, WebSearchResult
from app.services.consumption import BudgetExceeded, ResearchBudget
from app.services.corpus.artifacts.backends.memory import InMemoryArtifactStore
from app.services.web_research import planner as pl
from app.services.web_research import stage as st
from app.services.web_research.budget import PROFILES, WebBudgetLimits
from app.services.web_research.fetch import (
    STATUS_FETCHED,
    STATUS_NOT_RETRIEVABLE,
    STATUS_REFUSED,
    OpenWebFetchResult,
)
from app.services.web_research.pool import ExtractionPool

FIXTURES = Path(__file__).parent / "fixtures" / "web"
TODAY = date(2026, 10, 4)
NOW = datetime(2026, 10, 4, 12, 0, tzinfo=timezone.utc)

#: url → fixture page. Every URL the fixtures' search results point at.
PAGES: dict[str, str] = {
    "https://www.voltgrid.example/investors/presentation-2026": "w5_issuer_presentation.html",
    "https://www.tdworld.com/grid/voltgrid-order": "w5_catalyst_news.html",
    "https://www.power-technology.com/features/transformer-makers": "w5_competitor_feature.html",
    "https://www.nema.org/reports/transformer-supply-outlook": "w5_industry_assoc.html",
    "https://www.utilitydive.com/news/voltgrid-texas-delay": "w5_risk_news.html",
}


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


@pytest.fixture(scope="module")
def pool() -> Any:
    p = ExtractionPool(1)
    yield p
    p.shutdown()


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


def _cfg(**over: Any) -> Settings:
    values: dict[str, Any] = {
        "app_env": "test",
        "v3_company_web_research_enabled": True,
        "v3_web_search_enabled": True,
        "v3_web_search_provider": "fake",
        "v3_web_search_max_queries_per_day": 300,
        "v3_run_max_web_searches": 0,
        "v3_web_fetch_enabled": True,
        "v3_corpus_enabled": True,
        # Off by default: extraction runs in a real process pool and is slow on a loaded
        # machine, so only the tests that assert on stored documents turn it on (INGEST).
        "v3_web_corpus_ingest_enabled": False,
        "v3_artifact_store_backend": "none",
        "v3_run_max_model_calls": 0,
        "deepseek_api_key": "",
    }
    values.update(over)
    return Settings(**values)


INGEST: dict[str, Any] = {"v3_web_corpus_ingest_enabled": True}


async def _company(session: Any) -> Company:
    company = Company(
        id=uuid.uuid4(), ticker="VGRD", exchange="NASDAQ", name="Voltgrid Corp", status="new"
    )
    session.add(company)
    await session.flush()
    return company


class FakeNet:
    """The ``open_web_fetch`` stand-in: serves fixture pages, accounts like the real one."""

    def __init__(self, *, extra: dict[str, bytes] | None = None,
                 not_retrievable: dict[str, str] | None = None,
                 advance: Any = None, boom: bool = False) -> None:
        self.extra = extra or {}
        self.not_retrievable = not_retrievable or {}
        self.advance = advance
        self.boom = boom
        self.requested: list[tuple[str, str]] = []

    def body_for(self, url: str) -> bytes | None:
        if url in self.extra:
            return self.extra[url]
        name = PAGES.get(url)
        return (FIXTURES / name).read_bytes() if name else None

    async def __call__(self, session: Any, url: str, *, context: Any, budget: Any,
                       origin: str, search_result_id: Any = None, **_kw: Any
                       ) -> OpenWebFetchResult:
        self.requested.append((origin, url))
        if self.boom:
            raise RuntimeError("network exploded")
        refusal = budget.fetch_refusal()
        if refusal:
            return OpenWebFetchResult(status=STATUS_REFUSED, origin=origin, requested_url=url,
                                      failure_code=refusal)
        if url in self.not_retrievable:
            return OpenWebFetchResult(
                status=STATUS_NOT_RETRIEVABLE, origin=origin, requested_url=url,
                failure_code=self.not_retrievable[url],
            )
        body = self.body_for(url)
        if body is None:
            return OpenWebFetchResult(status="failed", origin=origin, requested_url=url,
                                      failure_code="http_404")
        is_pdf = body.startswith(b"%PDF")
        budget.record_fetch(byte_count=len(body), is_pdf=is_pdf)
        if self.advance:
            self.advance()
        return OpenWebFetchResult(
            status=STATUS_FETCHED, origin=origin, requested_url=url, final_url=url,
            canonical_url=url, content=body, content_class="pdf" if is_pdf else "html",
            charset="utf-8", content_hash=hashlib.sha256(body).hexdigest(), bytes=len(body),
            tdm_decision="tdm_not_reserved",
        )


class Harness:
    def __init__(self, session: Any, pool: Any) -> None:
        self.session = session
        self.pool = pool
        self.provider = FakeWebSearchProvider.from_fixture_dir(FIXTURES)
        self.net = FakeNet()
        self.store = InMemoryArtifactStore()
        self.transport: Any = None
        self.clock: Any = None

    def deps(self, **over: Any) -> st.StageDeps:
        values: dict[str, Any] = {
            "provider": self.provider,
            "fetch": self.net,
            "pool": self.pool,
            "store": self.store,
            "llm_transport": self.transport,
            "persist_session_factory": None,
            "today": TODAY,
            "now": NOW,
        }
        if self.clock is not None:
            values["clock"] = self.clock
        values.update(over)
        return st.StageDeps(**values)

    async def run(self, company: Any, *, cfg: Any = None, mode: str = "quick",
                  deps: st.StageDeps | None = None, **ctx: Any) -> st.WebStageResult:
        ctx.setdefault("industry", "Electrical equipment")
        return await st.ensure_web_context(
            self.session, company, st.WebRunContext(mode=mode, **ctx),
            cfg=cfg or _cfg(), deps=deps or self.deps(),
        )


@pytest.fixture
def h(session: Any, pool: Any) -> Harness:
    return Harness(session, pool)


async def _count(session: Any, model: Any) -> int:
    return int((await session.execute(select(func.count()).select_from(model))).scalar_one())


# --------------------------------------------------------------------------- #
# The flag and the states
# --------------------------------------------------------------------------- #


class TestTheFlag:
    def test_it_defaults_off(self) -> None:
        assert Settings.model_fields["v3_company_web_research_enabled"].default is False

    async def test_off_means_no_query_no_fetch_no_row_no_summary(self, h: Harness,
                                                                 session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(v3_company_web_research_enabled=False))
        assert result.ran is False and result.summary == {}
        assert h.provider.requests == [] and h.net.requested == []
        assert await _count(session, WebSearchQuery) == 0
        assert await _count(session, ResearchDocumentVersion) == 0

    async def test_search_disabled_is_a_labelled_state_not_an_error(self, h: Harness,
                                                                     session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(v3_web_search_enabled=False))
        assert result.state == "web_search_disabled"
        assert h.provider.requests == [] and h.net.requested == []
        assert result.summary.get("label") is None, "official-source research as today"

    async def test_no_provider_selected_is_search_disabled(self, h: Harness,
                                                            session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(v3_web_search_provider="none"),
                             deps=h.deps(provider=None))
        assert result.state == "web_search_disabled"


class TestAnOutageIsNotMaskedByRecall:
    async def test_every_query_failed_is_web_search_unavailable(self, h: Harness,
                                                                  session: Any) -> None:
        h.provider.mode = MODE_OUTAGE
        company = await _company(session)
        result = await h.run(company)
        assert result.state == "web_search_unavailable"
        assert "Live web search unavailable" in str(result.summary["label"])
        assert h.net.requested == [], "no fetch without a search"
        assert await _count(session, ResearchDocumentVersion) == 0

    async def test_nothing_is_recorded_as_an_executed_search(self, h: Harness,
                                                              session: Any) -> None:
        """ACC-88: provenance is a network fact. No row says a search ran that did not."""
        h.provider.mode = MODE_OUTAGE
        company = await _company(session)
        await h.run(company)
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert rows and all(r.executed is False for r in rows)
        assert all(r.error_code for r in rows)
        assert not any(r.origin == "model_recall" for r in rows)

    async def test_the_calls_that_failed_still_count_against_the_run(self, h: Harness,
                                                                      session: Any) -> None:
        h.provider.mode = MODE_OUTAGE
        company = await _company(session)
        result = await h.run(company)
        assert result.units.web_search_calls == len(h.provider.requests) > 0
        assert result.summary["budget"]["queries_reserved"] == len(h.provider.requests)
        assert result.summary["queries"]["failed"] == len(h.provider.requests)

    async def test_a_partial_outage_is_degraded_with_the_coverage_recorded(
        self, h: Harness, session: Any
    ) -> None:
        h.provider.mode_by_query = {
            "voltgrid delay": MODE_OUTAGE, "voltgrid competitors": MODE_OUTAGE,
        }
        company = await _company(session)
        result = await h.run(company)
        assert result.state == "web_search_degraded"
        assert result.summary["label"] == "Web search incomplete (4 of 6 searches ran)"
        assert result.summary["queries"]["executed"] == 4
        assert result.summary["fetch"]["fetched"] >= 1, "what executed is still used"


# --------------------------------------------------------------------------- #
# The happy path, recorded
# --------------------------------------------------------------------------- #


class TestTheStageEndToEnd:
    async def test_documents_reach_the_corpus_with_their_labels(self, h: Harness,
                                                                  session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        s = result.summary
        assert result.state == "ok"
        assert s["queries"]["planned"] == 6 and s["queries"]["executed"] == 6
        assert s["results"]["seen"] >= 5
        assert s["ingest"]["ingested"] == 5
        assert s["source_classes"]["trade_publication"] == 3
        assert s["source_classes"]["industry_association"] == 1
        assert s["template_version"] == pl.QUERY_TEMPLATE_VERSION
        versions = (await session.execute(select(ResearchDocumentVersion))).scalars().all()
        assert len(versions) == 5
        assert all(v.web_extractor_version is not None and v.source_class for v in versions)
        assert await _count(session, ResearchDocumentChunk) >= 5, "chunks are the searchable unit"

    async def test_every_search_result_row_ends_in_a_disposition(self, h: Harness,
                                                                   session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        rows = (await session.execute(select(WebSearchResult))).scalars().all()
        assert rows
        dispositions = {r.disposition for r in rows}
        assert "candidate" not in dispositions, "no row is left undecided"
        assert {"ingested", "skipped"} <= dispositions
        skipped = [r for r in rows if r.disposition == "skipped"]
        assert any(r.disposition_reason == "duplicate_url" for r in skipped)
        assert result.summary["dispositions"]["ingested"] == 5

    async def test_a_fetch_attempt_is_tied_to_its_search_result(self, h: Harness,
                                                                   session: Any) -> None:
        company = await _company(session)
        seen: list[Any] = []
        original = h.net.__call__

        async def spy(*a: Any, **kw: Any) -> Any:
            seen.append(kw.get("search_result_id"))
            return await original(*a, **kw)

        h.net = spy  # type: ignore[assignment]
        await h.run(company)
        assert seen and all(i is not None for i in seen[:5])

    async def test_units_count_calls_fetches_and_bytes(self, h: Harness, session: Any) -> None:
        company = await _company(session)
        result = await h.run(company)
        u = result.units
        assert u.web_search_calls == 6
        assert u.url_fetch_calls == result.summary["fetch"]["fetched"] > 0
        assert u.bytes_downloaded == result.summary["fetch"]["bytes"] > 0
        assert {"web_search_calls", "url_fetch_calls", "bytes_downloaded"} <= u.instrumented
        assert u.elapsed_seconds == 0.0, "wall time is measured once, for the whole run"
        # The fake bills nothing and says so: no credit figure is invented.
        assert u.tavily_credits == 0.0 and "tavily_credits" not in u.instrumented

    async def test_the_summary_carries_no_snippet_url_or_query_text(self, h: Harness,
                                                                      session: Any) -> None:
        company = await _company(session)
        result = await h.run(company)
        blob = json.dumps(result.summary, default=str)
        assert "Synthetic snippet" not in blob
        assert "Voltgrid contract order" not in blob, "query text lives in provenance rows"
        assert "template_keys" in blob

    async def test_catalyst_documents_are_surfaced_for_the_v2_section(self, h: Harness,
                                                                        session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        rows = result.summary["catalyst_evidence"]
        assert len(rows) == 1
        row = rows[0]
        assert row["domain"] == "tdworld.com" and row["source_class"] == "trade_publication"
        assert row["published_at"] == "2026-09-12"
        assert row["title"].startswith("Voltgrid wins")

    async def test_provenance_rows_record_the_plan(self, h: Harness, session: Any) -> None:
        company = await _company(session)
        await h.run(company)
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert len(rows) == 6
        assert all((r.template_version or "").startswith(pl.QUERY_TEMPLATE_VERSION) for r in rows)
        assert {r.family for r in rows} == {"company_docs", "catalyst", "competitive",
                                            "industry", "risk"}
        assert all(r.stage == "company_web" for r in rows)

    async def test_a_second_run_does_not_refetch_what_the_corpus_holds(self, h: Harness,
                                                                         session: Any) -> None:
        company = await _company(session)
        await h.run(company, cfg=_cfg(**INGEST))
        first = len(h.net.requested)
        again = await h.run(company, cfg=_cfg(**INGEST))
        assert len(h.net.requested) == first, "already_held is skipped before any fetch"
        assert again.summary["results"]["skipped_by_reason"].get("already_held", 0) >= 4


# --------------------------------------------------------------------------- #
# The budget stops at each ceiling
# --------------------------------------------------------------------------- #


def _profile(**over: Any) -> WebBudgetLimits:
    base = PROFILES["company_quick"]
    values = {f: getattr(base, f) for f in base.__dataclass_fields__}
    values.update(over)
    return WebBudgetLimits(**values)


class TestTheBudgetStopsAtEachCeiling:
    async def test_the_query_ceiling(self, h: Harness, session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(v3_run_max_web_searches=2))
        assert len(h.provider.requests) == 2
        assert result.summary["queries"]["planned"] == 2

    async def test_the_platform_daily_cap_counts_failed_calls(self, h: Harness,
                                                                session: Any) -> None:
        h.provider.mode = MODE_OUTAGE
        company = await _company(session)
        await h.run(company, cfg=_cfg(v3_web_search_max_queries_per_day=3))
        assert len(h.provider.requests) == 3, "three failed calls spent the day's cap"
        again = await h.run(company, cfg=_cfg(v3_web_search_max_queries_per_day=3))
        assert len(h.provider.requests) == 3, "the next run is refused before any call"
        assert again.state == "web_search_unavailable"

    async def test_the_daily_cap_of_zero_means_no_calls(self, h: Harness, session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(v3_web_search_max_queries_per_day=0))
        assert h.provider.requests == [] and result.state == "web_search_unavailable"

    async def test_the_fetch_ceiling(self, h: Harness, session: Any,
                                      monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(PROFILES, "company_quick", _profile(max_fetches=3))
        company = await _company(session)
        result = await h.run(company)
        assert result.summary["fetch"]["fetched"] <= 3
        assert result.summary["budget"]["fetches"] <= 3

    async def test_the_byte_ceiling(self, h: Harness, session: Any,
                                     monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setitem(PROFILES, "company_quick", _profile(max_bytes=500))
        company = await _company(session)
        result = await h.run(company)
        assert result.summary["fetch"]["fetched"] == 1, "the first page exhausted the bytes"
        assert any("max_bytes" in n for n in result.summary["notes"])

    async def test_the_wall_time_ceiling(self, h: Harness, session: Any,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
        now = [1000.0]

        def clock() -> float:
            return now[0]

        h.clock = clock
        h.net = FakeNet(advance=lambda: now.__setitem__(0, now[0] + 40.0))
        monkeypatch.setitem(PROFILES, "company_quick", _profile(max_wall_seconds=100.0))
        company = await _company(session)
        result = await h.run(company)
        assert result.summary["fetch"]["fetched"] == 3, "40 s per page against a 100 s ceiling"
        assert any("max_wall_seconds" in n for n in result.summary["notes"])

    async def test_the_pdf_ceiling_stops_pdf_downloads(self, h: Harness, session: Any,
                                                         monkeypatch: pytest.MonkeyPatch) -> None:
        from tests.helpers.pdf_fixtures import make_pdf

        pdf_url = "https://www.voltgrid.example/files/annual-report-2025.pdf"
        h.net = FakeNet(extra={pdf_url: make_pdf(["Voltgrid Corp annual report 2025. " * 20])})
        monkeypatch.setitem(PROFILES, "company_quick", _profile(max_pdfs=0))
        company = await _company(session)
        result = await h.run(company)
        assert result.summary["budget"]["pdfs"] == 0


class TestResearchBudgetCheckHasItsFirstCaller:
    def test_a_wave_is_trimmed_to_the_searches_that_still_fit(self) -> None:
        tally = st._Tally()
        trimmed = st._fit_to_run_budget(
            list(range(5)), ResearchBudget(max_web_searches=3),
            st.ConsumptionUnits(web_search_calls=1), started_at=0.0, clock=lambda: 1.0,
            tally=tally,
        )
        assert trimmed == [0, 1] and tally.run_budget_limit_hit == "max_web_searches"

    def test_an_exhausted_wall_clock_issues_nothing(self) -> None:
        tally = st._Tally()
        out = st._fit_to_run_budget(
            [1, 2], ResearchBudget(max_wall_seconds=10.0), st.ConsumptionUnits(),
            started_at=0.0, clock=lambda: 50.0, tally=tally,
        )
        assert out == [] and tally.run_budget_limit_hit == "max_wall_seconds"

    def test_an_unbounded_budget_changes_nothing(self) -> None:
        tally = st._Tally()
        out = st._fit_to_run_budget(
            [1, 2, 3], ResearchBudget(), st.ConsumptionUnits(), started_at=0.0,
            clock=lambda: 1.0, tally=tally,
        )
        assert out == [1, 2, 3] and tally.run_budget_limit_hit is None

    def test_the_budget_really_raises(self) -> None:
        with pytest.raises(BudgetExceeded):
            ResearchBudget(max_web_searches=1).check(
                st.ConsumptionUnits(), about_to_spend=st.ConsumptionUnits(web_search_calls=2)
            )

    async def test_the_stage_stops_at_the_run_budget_and_says_degraded(
        self, h: Harness, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(st, "run_budget_for", lambda mode, cfg: ResearchBudget(
            max_web_searches=1))
        company = await _company(session)
        result = await h.run(company)
        assert len(h.provider.requests) == 1
        assert result.summary["run_budget_limit_hit"] == "max_web_searches"


# --------------------------------------------------------------------------- #
# Isolation
# --------------------------------------------------------------------------- #


class TestTheStageNeverFailsTheJob:
    async def test_an_exception_inside_the_stage_is_recorded_not_raised(
        self, h: Harness, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("planner exploded")

        monkeypatch.setattr(st, "build_plan", boom)
        company = await _company(session)
        result = await h.run(company)
        assert result.state == "web_stage_failed"
        assert result.summary["error"] == "RuntimeError"
        assert "official sources" in str(result.summary["label"])
        # The session is still usable: the SAVEPOINT released the stage's writes only.
        assert (await session.execute(select(func.count()).select_from(Company))).scalar_one() == 1

    async def test_a_network_that_explodes_costs_the_urls_not_the_stage(self, h: Harness,
                                                                          session: Any) -> None:
        h.net = FakeNet(boom=True)
        company = await _company(session)
        result = await h.run(company)
        assert result.state == "ok"
        assert result.summary["fetch"]["failed"] >= 1
        assert result.summary["ingest"]["ingested"] == 0

    async def test_a_document_that_fails_to_store_costs_only_itself(
        self, h: Harness, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.web_research import ingest as ingest_mod

        real = ingest_mod.store_web_document
        calls = {"n": 0}

        async def flaky(*a: Any, **k: Any) -> Any:
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("write failed")
            return await real(*a, **k)

        monkeypatch.setattr(ingest_mod, "store_web_document", flaky)
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        assert result.state == "ok"
        assert result.summary["ingest"]["ingested"] == 4
        assert result.summary["ingest"]["not_ingested"] == {"RuntimeError": 1}

    async def test_the_units_of_a_failed_stage_still_report_the_paid_searches(
        self, h: Harness, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def boom(*a: Any, **k: Any) -> Any:
            raise RuntimeError("selection exploded")

        monkeypatch.setattr(st, "select_results", lambda *a, **k: (_ for _ in ()).throw(
            RuntimeError("selection exploded")))
        company = await _company(session)
        result = await h.run(company)
        assert result.state == "web_stage_failed"
        assert result.units.web_search_calls == 6, "paid calls are reported even on failure"


# --------------------------------------------------------------------------- #
# What is fetched, and what is not accessible
# --------------------------------------------------------------------------- #


class TestSourcesFoundButNotAccessible:
    async def test_a_blocked_source_is_listed_with_its_reason(self, h: Harness,
                                                                session: Any) -> None:
        h.net = FakeNet(not_retrievable={
            "https://www.utilitydive.com/news/voltgrid-texas-delay": "paywalled",
            "https://www.nema.org/reports/transformer-supply-outlook": "robots_disallowed",
        })
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        listed = {(r["domain"], r["reason"]) for r in result.summary["not_retrievable"]}
        assert listed == {("utilitydive.com", "paywalled"), ("nema.org", "robots_disallowed")}
        rows = (await session.execute(select(WebSearchResult).where(
            WebSearchResult.disposition == "not_retrievable"))).scalars().all()
        assert {r.disposition_reason for r in rows} == {"paywalled", "robots_disallowed"}
        assert result.summary["fetch"]["not_retrievable"] == 2
        assert result.summary["ingest"]["ingested"] == 3


class TestOtherFlags:
    async def test_fetch_disabled_records_the_reason_and_fetches_nothing(self, h: Harness,
                                                                          session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(v3_web_fetch_enabled=False))
        assert h.net.requested == []
        assert result.summary["fetch"]["state"] == "web_fetch_disabled"
        rows = (await session.execute(select(WebSearchResult.disposition_reason).where(
            WebSearchResult.disposition == "not_ingested"))).scalars().all()
        assert rows and set(rows) == {"web_fetch_disabled"}

    async def test_ingest_disabled_fetches_but_stores_nothing(self, h: Harness,
                                                                session: Any) -> None:
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(v3_web_corpus_ingest_enabled=False))
        assert result.summary["fetch"]["fetched"] >= 1
        assert result.summary["ingest"]["ingested"] == 0
        assert result.summary["ingest"]["not_ingested"] == {"web_ingest_disabled": 5}
        assert await _count(session, ResearchDocumentVersion) == 0


# --------------------------------------------------------------------------- #
# PI-07 and the expansion
# --------------------------------------------------------------------------- #

PAGE_TOKEN = "ZQXPAGETOKEN7"


class TestNoPageTextBecomesAQuery:
    async def test_a_page_token_never_reaches_a_query_or_the_expansion_prompt(
        self, h: Harness, session: Any
    ) -> None:
        poisoned = (FIXTURES / "w5_catalyst_news.html").read_text().replace(
            "framework order", f"framework order {PAGE_TOKEN} acquisition of Rival Corp"
        )
        h.net = FakeNet(extra={"https://www.tdworld.com/grid/voltgrid-order": poisoned.encode()})
        h.transport = FakeDeepSeekTransport(
            completion_text=json.dumps({"queries": ["transformer core lamination"]})
        )
        company = await _company(session)
        result = await h.run(company, mode="standard", cfg=_cfg(**INGEST))
        sent = " ".join(r.query for r in h.provider.requests)
        assert PAGE_TOKEN not in sent and "Rival Corp" not in sent
        prompts = " ".join(f"{s} {u}" for s, u in h.transport.completions)
        assert PAGE_TOKEN not in prompts and "Rival Corp" not in prompts
        assert result.summary["ingest"]["ingested"] >= 1, "the page itself was still used"

    def test_the_planner_has_no_parameter_that_can_carry_page_text(self) -> None:
        import inspect

        params = set(inspect.signature(pl.build_plan).parameters)
        assert params == {
            "facts", "mode", "max_queries", "max_locale_queries", "today", "private_tokens",
            "expansion", "expansion_origin",
        }
        assert set(inspect.signature(pl.propose_expansion).parameters) == {
            "transport", "facts", "plan", "limit", "max_tokens", "private_tokens", "timeout",
        }


class TestTheExpansionIsRecordedAndCounted:
    @pytest.fixture(autouse=True)
    def _clean(self) -> None:
        pl.clear_expansion_cache()

    async def test_proposals_are_issued_as_llm_expansion_and_their_tokens_counted(
        self, h: Harness, session: Any
    ) -> None:
        h.transport = FakeDeepSeekTransport(completion_text=json.dumps(
            {"queries": ["transformer core lamination", "electrical steel allocation"]}
        ))
        company = await _company(session)
        result = await h.run(company, mode="standard")
        rows = (await session.execute(
            select(WebSearchQuery).where(WebSearchQuery.origin == "llm_expansion")
        )).scalars().all()
        assert len(rows) == 2
        assert all(pl.EXPANSION_PROMPT_VERSION in (r.template_version or "") for r in rows)
        assert result.units.model_calls == 1 and result.units.by_vendor
        assert result.summary["queries"]["expansion"]["queries"] == 2
        assert result.summary["queries"]["planned"] <= 16

    async def test_quick_mode_buys_no_expansion(self, h: Harness, session: Any) -> None:
        h.transport = FakeDeepSeekTransport(completion_text='{"queries": ["a b"]}')
        company = await _company(session)
        result = await h.run(company, mode="quick")
        assert h.transport.completions == []
        assert result.summary["queries"]["expansion"] is None

    async def test_a_second_run_reuses_the_cached_expansion(self, h: Harness,
                                                              session: Any) -> None:
        h.transport = FakeDeepSeekTransport(completion_text=json.dumps(
            {"queries": ["transformer core lamination"]}))
        company = await _company(session)
        await h.run(company, mode="standard")
        await h.run(company, mode="standard")
        assert len(h.transport.completions) == 1


# --------------------------------------------------------------------------- #
# The crawl, through the stage
# --------------------------------------------------------------------------- #


class TestTheCrawlFromTheVerifiedIssuer:
    async def test_the_issuer_ir_page_is_a_seed_and_its_report_is_ingested(
        self, h: Harness, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from tests.helpers.pdf_fixtures import make_pdf

        ir = "https://www.voltgrid.example/investors"
        pdf_url = "https://www.voltgrid.example/files/annual-report-2025.pdf"
        ir_html = (
            b"<html><body><main>"
            b'<a href="/files/annual-report-2025.pdf">Annual report 2025</a>'
            b"</main></body></html>"
        )
        h.net = FakeNet(extra={
            ir: ir_html,
            pdf_url: make_pdf(["Voltgrid Corp annual report 2025. Revenue and orders. " * 30]),
        })
        verified = SimpleNamespace(
            official_website_domain="voltgrid.example",
            allowed_domains=("voltgrid.example",), document_domains=(),
            investor_relations_url=ir, annual_reports_url=None, press_releases_url=None,
        )
        monkeypatch.setattr(st, "_verified_issuer", lambda company: verified)
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        assert ("crawl", ir) in h.net.requested
        assert ("crawl", pdf_url) in h.net.requested
        crawl = result.summary["crawl"]
        assert crawl["documents_fetched"] == 1 and crawl["max_depth_reached"] <= 2
        classes = {v.source_class for v in (
            await session.execute(select(ResearchDocumentVersion))).scalars().all()}
        assert "company_web_page" in classes or "investor_presentation" in classes
        assert result.summary["ingest"]["ingested"] >= 6

    async def test_a_crawl_never_fetches_through_anything_but_the_fetcher_seam(
        self, h: Harness, session: Any
    ) -> None:
        company = await _company(session)
        await h.run(company)
        assert {origin for origin, _url in h.net.requested} <= {"search", "crawl"}


class TestIdentityAndScope:
    async def test_documents_are_company_documents_with_the_company_subject(
        self, h: Harness, session: Any
    ) -> None:
        company = await _company(session)
        await h.run(company, cfg=_cfg(**INGEST))
        docs = (await session.execute(select(ResearchDocument))).scalars().all()
        assert docs and all(d.company_id == company.id for d in docs)
        assert all(d.subject_scope == "company" for d in docs)
