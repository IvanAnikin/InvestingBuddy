"""Open-web W1 — search contract, provenance, Tavily adapter (dark).

No live network anywhere in this file: the Tavily adapter runs over
``httpx.MockTransport`` and everything else over ``FakeWebSearchProvider``. Fixtures under
``tests/fixtures/web`` are SYNTHETIC (no live key exists yet) and say so in
``_fixture_note``.

Configuration is passed as a ``SimpleNamespace``, never a bare ``Settings()``: a bare
``Settings()`` reads the developer's ``.env``.

The planted key below is not a real credential. Even so, no assertion here takes the key
as an operand: every check is reduced to a boolean first, so a failure cannot print it.
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.integrations.search import web_search_provider_from_settings
from app.integrations.search.base import UnavailableSearchProvider, apply_client_filters
from app.integrations.search.fake import (
    MODE_AUTH,
    MODE_EMPTY,
    MODE_HTTP_429,
    MODE_OUTAGE,
    MODE_PARSE,
    MODE_TIMEOUT,
    FakeWebSearchProvider,
    fixture_key,
)
from app.integrations.search.tavily import (
    TavilySearchProvider,
    base_url_allowed,
    parse_response,
)
from app.models.web_research import WebSearchQuery, WebSearchResult
from app.services.consumption import (
    UNIT_NAMES,
    ConsumptionUnits,
    derive_cost,
    price_book_from_settings,
)
from app.services.providers.contracts import (
    RESULT_STORAGE_URL_ONLY,
    CandidateSearchProvider,
    QueryFamily,
    SearchCapabilities,
    SearchExecution,
    SearchProvider,
    SearchRequest,
)
from app.services.providers.governance import ProviderGovernance, default_governance
from app.services.web_research import queries as q
from app.services.web_research.budget import (
    LIMIT_DAILY,
    LIMIT_QUERIES,
    PROFILES,
    WebBudgetLimits,
    WebResearchBudget,
    limits_for,
    network_calls_today,
)
from app.services.web_research.search import (
    STATE_DEGRADED,
    STATE_DISABLED,
    STATE_OK,
    STATE_UNAVAILABLE,
    SearchContext,
    run_searches,
)

FIXTURES = Path(__file__).parent / "fixtures" / "web"
PLANTED_KEY = "tvly-planted-value-not-a-real-credential"
Q_TRANSFORMERS = "power transformer manufacturer listed company Europe"
Q_NEWS = "transformer maker new factory"
Q_EMPTY = "obscure niche component supplier with no web presence"
Q_NO_USAGE = "grid equipment supplier investor presentation"


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


def _cfg(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "app_env": "test",
        "v3_web_search_enabled": True,
        "v3_web_search_provider": "fake",
        "v3_web_search_max_queries_per_day": 300,
        "v3_run_max_web_searches": 0,
        "tavily_api_key": "",
        "tavily_base_url": "https://api.tavily.com",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _req(query: str = Q_TRANSFORMERS, **over: Any) -> SearchRequest:
    return SearchRequest(query=query, family=over.pop("family", QueryFamily.ENTITY), **over)


def _fixture(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text())


def _fake(**kwargs: Any) -> FakeWebSearchProvider:
    return FakeWebSearchProvider.from_fixture_dir(FIXTURES, **kwargs)


class _Tavily:
    """A MockTransport-backed Tavily adapter that records what it was sent."""

    def __init__(self, handler: Any = None, *, key: str = PLANTED_KEY, **kwargs: Any) -> None:
        self.calls: list[httpx.Request] = []
        self._handler = handler or (lambda request: httpx.Response(200, json=_fixture(
            "tavily_transformer_manufacturers.json")))

        def record(request: httpx.Request) -> httpx.Response:
            self.calls.append(request)
            return self._handler(request)

        self.provider = TavilySearchProvider(
            api_key=key, transport=httpx.MockTransport(record), **kwargs
        )

    def body(self, index: int = 0) -> dict[str, Any]:
        return json.loads(self.calls[index].content)


# --------------------------------------------------------------------------- #
# 1. The query sanitiser — threat model QI-01…QI-06
# --------------------------------------------------------------------------- #


class TestTheSanitiser:
    def test_qi01_operators_in_free_text_are_stripped(self) -> None:
        out = q.sanitise_query(
            "transformers site:intranet inurl:admin filetype:env cache:x related:y makers"
        )
        assert out.ok
        assert out.text == "transformers makers"
        assert "site:intranet" in out.stripped_operators

    def test_qi01_only_planner_operators_are_appended(self) -> None:
        out = q.sanitise_query(
            "transformer market report", site_domains=["iea.example"], filetype_pdf=True
        )
        assert out.text == "transformer market report site:iea.example filetype:pdf"
        assert q.validate_outgoing_query(out.text) is None

    def test_qi01_a_planner_site_must_be_a_plain_domain(self) -> None:
        assert q.sanitise_query("x", site_domains=["10.0.0.5/admin"]).refusal == (
            q.REFUSAL_BAD_SITE
        )
        assert q.validate_outgoing_query("x site:localhost") == q.REFUSAL_BAD_SITE

    def test_qi01_the_gate_refuses_an_operator_it_did_not_emit(self) -> None:
        assert q.validate_outgoing_query("transformers inurl:admin") == q.REFUSAL_OPERATOR
        assert q.validate_outgoing_query("transformers filetype:env") == q.REFUSAL_OPERATOR

    def test_prose_with_colons_is_not_an_operator(self) -> None:
        out = q.sanitise_query("Q1:2026 guidance update: margins")
        assert out.text == "Q1:2026 guidance update: margins"

    def test_qi02_urls_are_extracted_not_sent(self) -> None:
        out = q.sanitise_query("research https://10.0.0.5/secret and www.example.com/page now")
        assert out.text == "research and now"
        assert out.extracted_urls == ("https://10.0.0.5/secret", "www.example.com/page")
        assert q.validate_outgoing_query("see https://evil.example") == q.REFUSAL_URL_IN_QUERY

    def test_qi03_a_private_token_refuses_the_query(self) -> None:
        private = {"Acme Holdings", "ACME"}
        out = q.sanitise_query("acme holdings competitors", private_tokens=private)
        assert out.refusal == q.REFUSAL_PRIVATE_TOKEN
        assert out.text == "", "a refused query is not returned for sending"
        # Whole words only: "acmeville" is not the private token "acme".
        assert q.sanitise_query("acmeville ports", private_tokens=private).ok

    def test_qi04_the_adapter_only_ever_calls_its_fixed_host(self) -> None:
        assert base_url_allowed("https://api.tavily.com")
        for bad in (
            "http://api.tavily.com",
            "https://10.0.0.5",
            "https://localhost",
            "https://api.tavily.com.evil.example",
            "https://user:pw@api.tavily.com",
            "https://api.tavily.com:8443",
            "https://api.tavily.com/internal",
        ):
            assert not base_url_allowed(bad), bad

    async def test_qi04_a_non_allowlisted_base_url_makes_no_call(self) -> None:
        t = _Tavily(base_url="https://internal.search.local")
        execution, items = await t.provider.search(_req("search our database for customers"))
        assert execution.executed is False
        assert execution.error_code == "host_not_allowed"
        assert t.calls == [] and items == []

    def test_qi05_length_is_capped(self) -> None:
        assert q.sanitise_query("a " * 250).refusal == q.REFUSAL_TOO_LONG
        assert q.validate_outgoing_query("b" * (q.MAX_QUERY_CHARS + 1)) == q.REFUSAL_TOO_LONG
        assert q.validate_outgoing_query("b" * q.MAX_QUERY_CHARS) is None

    def test_qi06_the_blocklist_refuses(self) -> None:
        assert q.sanitise_query("how to make a bomb").refusal == q.REFUSAL_BLOCKLIST
        assert q.sanitise_query("bombardier transport listed").ok

    def test_empty_after_cleaning_is_refused(self) -> None:
        assert q.sanitise_query("site:x.example https://a.example").refusal == q.REFUSAL_EMPTY


# --------------------------------------------------------------------------- #
# 2. Normalisation from fixtures
# --------------------------------------------------------------------------- #


class TestNormalisation:
    def test_every_fixture_is_marked_synthetic(self) -> None:
        for path in FIXTURES.glob("tavily_*.json"):
            data = json.loads(path.read_text())
            assert "synthetic" in data["_fixture_note"], path.name

    def test_a_tavily_body_normalises(self) -> None:
        parsed = parse_response(_fixture("tavily_transformer_manufacturers.json"))
        assert parsed is not None
        request_id, items, cost, malformed = parsed
        assert request_id.startswith("synthetic-")
        assert cost == {"tavily_credits": 1.0}
        assert malformed == 2, "the url-less and the ftp:// entries are dropped"
        assert [i.rank for i in items] == [1, 2, 3, 4]
        first = items[0]
        assert first.domain == "transformer-maker.example"
        assert first.canonical_url == "https://www.transformer-maker.example/about?utm_source=search"
        assert first.published_hint == datetime(2026, 6, 2, tzinfo=timezone.utc)
        assert first.provider_score == pytest.approx(0.91)
        assert first.contains_untrusted_content
        # PSL, not "last two labels".
        assert items[1].domain == "grid-equipment.co.uk"

    def test_news_dates_in_rfc_2822_parse(self) -> None:
        parsed = parse_response(_fixture("tavily_news_catalyst.json"))
        assert parsed is not None
        assert parsed[1][0].published_hint == datetime(2026, 9, 15, 10, tzinfo=timezone.utc)

    def test_a_missing_usage_block_reports_no_cost_not_zero(self) -> None:
        parsed = parse_response(_fixture("tavily_no_usage.json"))
        assert parsed is not None
        assert parsed[2] == {}

    @pytest.mark.parametrize(
        "body",
        [b"not json", b"[]", b'{"results": []}', b'{"request_id": "r"}', b'{"results": {}, "request_id": "r"}'],
    )
    def test_unusable_bodies_do_not_parse(self, body: bytes) -> None:
        assert parse_response(body) is None

    async def test_the_fake_serves_fixtures_through_the_same_parser(self) -> None:
        fake = _fake()
        execution, items = await fake.search(_req("  POWER transformer manufacturer listed company europe "))
        assert execution.executed is True
        assert len(items) == 4
        assert execution.cost_units == {}, "the fake bills nothing and says so by absence"

    async def test_the_fake_with_no_fixture_is_an_executed_empty_answer(self) -> None:
        execution, items = await _fake().search(_req("nothing recorded for this"))
        assert execution.executed is True and items == [] and execution.result_count == 0


# --------------------------------------------------------------------------- #
# 3. Client-side filters and filters_enforced_by
# --------------------------------------------------------------------------- #


class TestFilters:
    async def test_tavily_request_body(self) -> None:
        t = _Tavily()
        request = _req(
            max_results=50,
            date_from=date(2025, 1, 1),
            date_to=date(2026, 9, 30),
            include_domains=("Transformer-Maker.example",),
            exclude_domains=("aggregator.example",),
            country="de",
            language="de",
        )
        await t.provider.search(request)
        body = t.body()
        assert body["max_results"] == 20
        assert body["search_depth"] == "basic"
        assert body["include_answer"] is False
        assert body["include_raw_content"] is False
        assert body["include_usage"] is True
        assert body["start_date"] == "2025-01-01" and body["end_date"] == "2026-09-30"
        assert body["include_domains"] == ["transformer-maker.example"]
        assert body["exclude_domains"] == ["aggregator.example"]
        assert body["country"] == "germany"
        assert "language" not in body
        assert "api_key" not in json.dumps(body)
        sent = t.calls[0]
        assert sent.url == httpx.URL("https://api.tavily.com/search")
        assert sent.method == "POST"
        auth_ok = sent.headers.get("authorization") == "Bearer " + PLANTED_KEY
        assert auth_ok is True

    async def test_checkable_filters_are_enforced_client_side_and_recorded(self) -> None:
        t = _Tavily()
        execution, items = await t.provider.search(
            _req(
                exclude_domains=("aggregator.example",),
                date_from=date(2025, 1, 1),
                country="DE",
                language="de",
                topic="general",
            )
        )
        assert execution.executed
        # Undated results were kept, so the window rests on the vendor (review C4), and
        # Tavily's country is a ranking boost, not a filter (review C6).
        assert execution.filters_enforced_by == {
            "exclude_domains": "client",
            "date_range": "provider",
            "country": "provider_boost",
            "language": "unsupported",
        }
        assert execution.date_unchecked_count == 2
        domains = [i.domain for i in items]
        assert "aggregator.example" not in domains
        assert execution.client_filtered_count == 1
        assert [i.rank for i in items] == list(range(1, len(items) + 1))

    async def test_a_vendor_that_ignores_include_domains_cannot_leak(self) -> None:
        # ADR-055: the vendor returns off-domain results anyway; the client removes them.
        t = _Tavily()
        execution, items = await t.provider.search(
            _req(include_domains=("grid-equipment.co.uk",))
        )
        assert [i.domain for i in items] == ["grid-equipment.co.uk"]
        assert execution.filters_enforced_by["include_domains"] == "client"
        assert execution.client_filtered_count == 3

    def test_undated_results_survive_a_date_window(self) -> None:
        parsed = parse_response(_fixture("tavily_transformer_manufacturers.json"))
        assert parsed is not None
        kept, removed, unchecked = apply_client_filters(_req(date_from=date(2025, 1, 1)), parsed[1])
        assert removed == 1, "only the dated 2019 page falls outside"
        assert len(kept) == 3
        assert unchecked == 2

    async def test_an_unmapped_country_is_unsupported_not_guessed(self) -> None:
        t = _Tavily()
        execution, _ = await t.provider.search(_req(country="ZZ"))
        assert "country" not in t.body()
        assert execution.filters_enforced_by["country"] == "unsupported"

    async def test_news_topic_is_provider_enforced(self) -> None:
        t = _Tavily()
        execution, _ = await t.provider.search(_req(topic="news", country="DE"))
        assert t.body()["topic"] == "news"
        assert "country" not in t.body(), "Tavily's country applies to general only"
        assert execution.filters_enforced_by == {"country": "unsupported", "topic": "provider"}


# --------------------------------------------------------------------------- #
# 4. Fail closed
# --------------------------------------------------------------------------- #


class TestFailClosed:
    async def test_no_key_is_unavailable_with_zero_network_calls(self, session) -> None:  # noqa: ANN001
        cfg = _cfg(v3_web_search_provider="tavily", tavily_api_key="")
        provider = web_search_provider_from_settings(cfg)
        assert isinstance(provider, UnavailableSearchProvider)
        result = await run_searches(session, [_req(), _req(Q_NEWS)], SearchContext(), cfg=cfg)
        assert result.state == STATE_UNAVAILABLE
        assert result.network_call_count == 0
        assert {o.execution.error_code for o in result.outcomes} == {"no_key"}
        stored = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert len(stored) == 2 and all(r.network_call_count == 0 for r in stored)

    async def test_an_adapter_without_a_key_makes_no_call(self) -> None:
        t = _Tavily(key="")
        execution, _ = await t.provider.search(_req())
        assert (execution.executed, execution.error_code) == (False, "no_key")
        assert t.calls == []

    @pytest.mark.parametrize(
        ("status", "code"),
        [(401, "auth"), (403, "auth"), (429, "http_429"), (500, "http_5xx"), (503, "http_5xx"),
         (400, "http_4xx"), (432, "quota"), (433, "quota"), (302, "http_3xx")],
    )
    async def test_http_errors_are_not_executed(self, status: int, code: str) -> None:
        t = _Tavily(lambda r: httpx.Response(status, json={"detail": "x"}, headers={"location": "https://elsewhere.example"}))
        execution, items = await t.provider.search(_req())
        assert (execution.executed, execution.error_code) == (False, code)
        assert execution.http_status == status
        assert execution.network_call_count == 1
        assert items == []
        assert len(t.calls) == 1, "redirects are never followed; no retries in W1"

    async def test_a_timeout_is_not_executed(self) -> None:
        def slow(request: httpx.Request) -> httpx.Response:
            raise httpx.ReadTimeout("slow", request=request)

        execution, _ = await _Tavily(slow).provider.search(_req())
        assert (execution.executed, execution.error_code) == (False, "timeout")
        assert execution.network_call_count == 1

    async def test_a_transport_error_is_not_executed(self) -> None:
        def down(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("refused", request=request)

        execution, _ = await _Tavily(down).provider.search(_req())
        assert (execution.executed, execution.error_code) == (False, "transport")

    @pytest.mark.parametrize(
        "content",
        [b"<html>not json</html>", b'{"results": []}', b'{"request_id": "r", "results": "x"}'],
    )
    async def test_an_unparseable_200_is_not_executed(self, content: bytes) -> None:
        t = _Tavily(lambda r: httpx.Response(200, content=content))
        execution, _ = await t.provider.search(_req())
        assert (execution.executed, execution.error_code, execution.http_status) == (
            False,
            "parse",
            200,
        )

    async def test_an_oversize_body_is_not_executed(self) -> None:
        t = _Tavily(lambda r: httpx.Response(200, content=b"x" * 5000), max_response_bytes=1000)
        execution, _ = await t.provider.search(_req())
        assert (execution.executed, execution.error_code) == (False, "response_too_large")

    @pytest.mark.parametrize(
        ("mode", "code"),
        [(MODE_OUTAGE, "http_5xx"), (MODE_HTTP_429, "http_429"), (MODE_AUTH, "auth"),
         (MODE_TIMEOUT, "timeout"), (MODE_PARSE, "parse")],
    )
    async def test_every_failed_query_means_unavailable(self, session, mode: str, code: str) -> None:  # noqa: ANN001
        result = await run_searches(
            session, [_req(), _req(Q_NEWS)], SearchContext(), provider=_fake(mode=mode), cfg=_cfg()
        )
        assert result.state == STATE_UNAVAILABLE
        assert {o.execution.error_code for o in result.outcomes} == {code}

    async def test_some_failed_means_degraded(self, session) -> None:  # noqa: ANN001
        fake = _fake(mode_by_query={fixture_key(Q_NEWS): MODE_OUTAGE})
        result = await run_searches(session, [_req(), _req(Q_NEWS)], SearchContext(), provider=fake, cfg=_cfg())
        assert result.state == STATE_DEGRADED
        assert result.summary()["errors"] == {"http_5xx": 1}

    async def test_an_empty_answer_is_executed_not_a_failure(self, session) -> None:  # noqa: ANN001
        result = await run_searches(
            session, [_req(Q_EMPTY)], SearchContext(), provider=_fake(mode=MODE_EMPTY), cfg=_cfg()
        )
        assert result.state == STATE_OK and result.executed == 1

    async def test_flag_off_is_disabled_and_writes_nothing(self, session) -> None:  # noqa: ANN001
        fake = _fake()
        result = await run_searches(
            session, [_req()], SearchContext(), provider=fake, cfg=_cfg(v3_web_search_enabled=False)
        )
        assert result.state == STATE_DISABLED
        assert fake.requests == []
        assert await session.scalar(select(func.count()).select_from(WebSearchQuery)) == 0

    async def test_provider_none_is_disabled(self, session) -> None:  # noqa: ANN001
        result = await run_searches(
            session, [_req()], SearchContext(), cfg=_cfg(v3_web_search_provider="none")
        )
        assert result.state == STATE_DISABLED

    async def test_an_adapter_that_raises_is_recorded_not_propagated(self, session) -> None:  # noqa: ANN001
        class Broken:
            name = "fake_web_search"
            capabilities = SearchCapabilities(result_storage="full")
            is_configured = True

            async def search(self, request):  # noqa: ANN001, ANN201
                raise RuntimeError("boom")

        result = await run_searches(session, [_req()], SearchContext(), provider=Broken(), cfg=_cfg())
        assert result.state == STATE_UNAVAILABLE
        assert result.outcomes[0].execution.error_code == "adapter_error"


class TestRecallCannotMasquerade:
    def test_an_executed_record_needs_a_network_call(self) -> None:
        with pytest.raises(ValueError, match="network call"):
            SearchExecution(
                provider="deepseek", executed=True, provider_request_id="r",
                http_status=200, latency_ms=0, result_count=3, network_call_count=0,
            )

    def test_an_executed_record_needs_a_2xx_and_a_request_id(self) -> None:
        with pytest.raises(ValueError, match="2xx"):
            SearchExecution(
                provider="x", executed=True, provider_request_id="r",
                http_status=None, latency_ms=0, result_count=0, network_call_count=1,
            )
        with pytest.raises(ValueError, match="request id"):
            SearchExecution(
                provider="x", executed=True, provider_request_id=None,
                http_status=200, latency_ms=0, result_count=0, network_call_count=1,
            )

    def test_a_failure_must_say_why(self) -> None:
        with pytest.raises(ValueError, match="error_code"):
            SearchExecution(
                provider="x", executed=False, provider_request_id=None,
                http_status=None, latency_ms=0, result_count=0,
            )

    def test_deepseek_is_not_a_web_search_provider(self) -> None:
        from app.integrations.deepseek.providers import DeepSeekSearchProvider
        from tests.test_v3_deepseek_providers import FakeDeepSeekTransport

        legacy = DeepSeekSearchProvider(transport=FakeDeepSeekTransport(), enabled=True)
        assert isinstance(legacy, CandidateSearchProvider)
        assert not isinstance(legacy, SearchProvider)

    def test_deepseek_cannot_be_selected(self) -> None:
        provider = web_search_provider_from_settings(_cfg(v3_web_search_provider="deepseek"))
        assert isinstance(provider, UnavailableSearchProvider)
        assert provider.error_code == "unknown_provider"

    def test_the_real_adapters_satisfy_the_contract(self) -> None:
        assert isinstance(_fake(), SearchProvider)
        assert isinstance(TavilySearchProvider(api_key=PLANTED_KEY), SearchProvider)
        assert isinstance(UnavailableSearchProvider("tavily", "no_key"), SearchProvider)

    async def test_an_outage_produces_no_executed_row(self, session) -> None:  # noqa: ANN001
        # ACC-88 precursor: with the provider down nothing may look like a search.
        await run_searches(session, [_req(), _req(Q_NEWS)], SearchContext(), provider=_fake(mode=MODE_OUTAGE), cfg=_cfg())
        executed = await session.scalar(
            select(func.count()).select_from(WebSearchQuery).where(WebSearchQuery.executed.is_(True))
        )
        assert executed == 0
        assert await session.scalar(select(func.count()).select_from(WebSearchResult)) == 0


# --------------------------------------------------------------------------- #
# 5. Budget and the daily cap
# --------------------------------------------------------------------------- #


def _budget(max_queries: int = 10, daily_cap: int = 300, daily_used: int = 0, **kw: Any) -> WebResearchBudget:
    limits = WebBudgetLimits(max_queries, kw.pop("max_results", 100), 10, 2, 10_000, kw.pop("wall", 600))
    return WebResearchBudget(limits=limits, daily_cap=daily_cap, daily_used=daily_used, **kw)


class TestBudget:
    def test_profiles_match_the_spec(self) -> None:
        assert PROFILES["discovery_standard"].max_queries == 24
        assert PROFILES["company_quick"].max_queries == 6
        assert PROFILES["company_standard"].max_queries == 16
        assert PROFILES["followup"].max_fetches == 12

    def test_the_operator_cap_narrows_and_never_widens(self) -> None:
        assert limits_for("company_standard", _cfg(v3_run_max_web_searches=5)).max_queries == 5
        assert limits_for("company_standard", _cfg(v3_run_max_web_searches=500)).max_queries == 16
        with pytest.raises(KeyError):
            limits_for("unbounded_please", _cfg())

    def test_reservation_refuses_past_each_limit(self) -> None:
        b = _budget(max_queries=2)
        assert b.reserve_query() is None and b.reserve_query() is None
        assert b.reserve_query() == "budget:" + LIMIT_QUERIES
        d = _budget(daily_cap=3, daily_used=3)
        assert d.reserve_query() == "budget:" + LIMIT_DAILY
        assert _budget(daily_cap=0).reserve_query() == "budget:" + LIMIT_DAILY, "0 means none"

    def test_wall_time_is_a_limit(self) -> None:
        ticks = iter([0.0, 1000.0])
        b = _budget(wall=10, clock=lambda: next(ticks))
        assert b.reserve_query() == "budget:max_wall_seconds"

    async def test_failed_calls_count_against_the_run(self, session) -> None:  # noqa: ANN001
        fake = _fake(mode=MODE_OUTAGE)
        ctx = SearchContext(budget=_budget(max_queries=2))
        result = await run_searches(
            session, [_req(), _req(Q_NEWS), _req(Q_EMPTY)], ctx, provider=fake, cfg=_cfg()
        )
        codes = [o.execution.error_code for o in result.outcomes]
        assert codes == ["http_5xx", "http_5xx", "budget:max_queries"]
        assert len(fake.requests) == 2

    async def test_the_daily_cap_counts_todays_network_calls_including_failures(self, session) -> None:  # noqa: ANN001
        now = datetime.now(timezone.utc)
        for created, calls, executed in (
            (now, 1, True),
            (now, 1, False),  # a failed call still counts
            (now, 0, False),  # a refusal made no call
            (now - timedelta(days=2), 1, True),  # not today
        ):
            session.add(WebSearchQuery(
                id=uuid.uuid4(), family="entity", origin="template", query_text="x",
                request_hash=uuid.uuid4().hex, provider="fake_web_search", executed=executed,
                provider_request_id="r" if executed else None, network_call_count=calls,
                result_count=0, error_code=None if executed else "http_5xx", created_at=created,
            ))
        await session.flush()
        assert await network_calls_today(session, now=now) == 2

        fake = _fake()
        result = await run_searches(
            session, [_req(), _req(Q_NEWS)], SearchContext(), provider=fake,
            cfg=_cfg(v3_web_search_max_queries_per_day=3), now=now,
        )
        assert [o.execution.error_code for o in result.outcomes] == [None, "budget:daily_platform_cap"]
        assert result.state == STATE_DEGRADED

    async def test_the_result_budget_marks_the_overflow_skipped(self, session) -> None:  # noqa: ANN001
        ctx = SearchContext(budget=_budget(max_results=2))
        result = await run_searches(session, [_req()], ctx, provider=_fake(), cfg=_cfg())
        assert len(result.outcomes[0].results) == 2
        rows = (await session.execute(select(WebSearchResult).order_by(WebSearchResult.rank))).scalars().all()
        assert [r.disposition for r in rows] == ["candidate", "candidate", "skipped", "skipped"]
        assert rows[-1].disposition_reason == "budget:max_results"


# --------------------------------------------------------------------------- #
# 6. Governance — the first runtime call site (rule G4)
# --------------------------------------------------------------------------- #


class TestGovernance:
    def test_tavily_is_public_only(self) -> None:
        policy = default_governance().policy_for("tavily")
        assert policy.permits("public_web")
        assert not policy.permits("user_private")
        assert not policy.permits("licensed_private")

    async def test_a_credential_looking_payload_is_refused_and_not_stored(self, session) -> None:  # noqa: ANN001
        fake = _fake()
        result = await run_searches(
            session, [_req("transformer maker api_key leak")], SearchContext(), provider=fake, cfg=_cfg()
        )
        assert result.outcomes[0].execution.error_code == "credential_in_payload"
        assert fake.requests == []
        row = (await session.execute(select(WebSearchQuery))).scalar_one()
        assert row.query_text == "[withheld: credential_in_payload]"
        assert row.filters_json is None

    async def test_an_unregistered_provider_is_refused(self, session) -> None:  # noqa: ANN001
        fake = _fake(governance=ProviderGovernance())
        result = await run_searches(
            session, [_req()], SearchContext(), provider=fake, cfg=_cfg(), governance=ProviderGovernance()
        )
        assert result.outcomes[0].execution.error_code == "governance_refused"
        assert fake.requests == []

    async def test_the_adapter_checks_its_own_wire_payload(self) -> None:
        t = _Tavily(governance=ProviderGovernance())
        execution, _ = await t.provider.search(_req())
        assert execution.error_code == "governance_refused"
        assert t.calls == []
        t2 = _Tavily()
        execution, _ = await t2.provider.search(_req("client_secret rotation"))
        assert execution.error_code == "credential_in_payload"
        assert t2.calls == []

    async def test_a_g1_refusal_never_stores_the_private_text(self, session) -> None:  # noqa: ANN001
        ctx = SearchContext(private_tokens=frozenset({"my secret holding"}))
        fake = _fake()
        result = await run_searches(
            session, [_req("my secret holding competitors")], ctx, provider=fake, cfg=_cfg()
        )
        assert result.outcomes[0].execution.error_code == "G1_private_token"
        assert fake.requests == []
        row = (await session.execute(select(WebSearchQuery))).scalar_one()
        leaked = "secret holding" in row.query_text
        assert leaked is False
        assert row.request_hash == "withheld:G1_private_token", "no hash of the private text"
        assert row.filters_json is None


# --------------------------------------------------------------------------- #
# 7. The key never escapes
# --------------------------------------------------------------------------- #


class TestTheKeyNeverEscapes:
    def test_repr_hides_the_key(self) -> None:
        provider = TavilySearchProvider(api_key=PLANTED_KEY)
        leaked = PLANTED_KEY in repr(provider) or PLANTED_KEY in str(provider)
        assert leaked is False
        assert provider.is_configured is True

    def test_the_setting_is_a_listed_non_printing_credential(self) -> None:
        from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings

        assert "tavily_api_key" in CREDENTIAL_SETTING_FIELDS
        assert Settings.model_fields["tavily_api_key"].repr is False

    async def test_no_log_line_or_result_carries_the_key(self, session, caplog) -> None:  # noqa: ANN001
        caplog.set_level(logging.DEBUG)
        responses = iter([
            httpx.Response(401, json={"detail": {"error": "Unauthorized: missing or invalid API key."}}),
            httpx.Response(200, json=_fixture("tavily_transformer_manufacturers.json")),
        ])

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path.endswith("/search") and "news" in request.content.decode():
                raise httpx.ConnectError("refused", request=request)
            return next(responses)

        t = _Tavily(handler)
        result = await run_searches(
            session, [_req(), _req(Q_TRANSFORMERS + " 2026"), _req("news on transformers")],
            SearchContext(), provider=t.provider, cfg=_cfg(),
        )
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        haystack = caplog.text + repr(result) + " ".join(
            json.dumps({c.name: str(getattr(r, c.name)) for c in WebSearchQuery.__table__.columns})
            for r in rows
        )
        leaked = PLANTED_KEY in haystack
        assert leaked is False
        assert "web_search_query" in caplog.text


# --------------------------------------------------------------------------- #
# 8. Provenance rows and the cache
# --------------------------------------------------------------------------- #


class TestProvenanceAndCache:
    async def test_a_scripted_run_writes_complete_provenance(self, session) -> None:  # noqa: ANN001
        job_id, company_id = uuid.uuid4(), uuid.uuid4()
        ctx = SearchContext(research_job_id=job_id, company_id=company_id, stage="entity")
        result = await run_searches(
            session,
            [_req(), _req(Q_NEWS, family=QueryFamily.CATALYST, topic="news", origin="gap",
                          template_version="t1")],
            ctx, provider=_fake(), cfg=_cfg(),
        )
        assert result.state == STATE_OK
        rows = (await session.execute(select(WebSearchQuery).order_by(WebSearchQuery.family))).scalars().all()
        assert [r.family for r in rows] == ["catalyst", "entity"]
        news = rows[0]
        assert news.research_job_id == job_id and news.company_id == company_id
        assert news.stage == "entity" and news.origin == "gap" and news.template_version == "t1"
        assert news.executed is True and news.network_call_count == 1
        assert news.provider == "fake_web_search"
        assert news.provider_request_id and news.http_status == 200
        assert news.filters_json["requested"]["topic"] == "news"
        assert news.filters_json["enforced_by"] == {"topic": "unsupported"}
        assert len(news.request_hash) == 64
        results = (await session.execute(
            select(WebSearchResult).where(WebSearchResult.query_id == news.id).order_by(WebSearchResult.rank)
        )).scalars().all()
        assert [r.rank for r in results] == [1, 2]
        assert results[0].domain == "wire.example" and results[0].title and results[0].snippet
        assert results[0].disposition == "candidate"
        assert result.consumption.web_search_calls == 2
        assert "tavily_credits" not in result.consumption.instrumented

    async def test_a_cache_hit_serves_rows_without_a_network_call(self, session) -> None:  # noqa: ANN001
        fake = _fake()
        first = await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        second = await run_searches(
            session, [_req("  Power transformer manufacturer LISTED company Europe")],
            SearchContext(), provider=fake, cfg=_cfg(),
        )
        assert len(fake.requests) == 1
        hit = second.outcomes[0]
        assert hit.from_cache and hit.execution.executed and hit.execution.network_call_count == 0
        assert hit.execution.cached_from == str(first.outcomes[0].query_id)
        assert [i.url for i in hit.results] == [i.url for i in first.outcomes[0].results]
        assert second.network_call_count == 0 and second.state == STATE_OK
        row = await session.get(WebSearchQuery, hit.query_id)
        assert str(row.served_from_query_id) == hit.execution.cached_from
        assert row.provider_request_id is None, "the request id lives on the original row"
        assert row.network_call_count == 0 and row.executed is True

    async def test_a_different_filter_is_a_different_question(self, session) -> None:  # noqa: ANN001
        fake = _fake()
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        await run_searches(session, [_req(exclude_domains=("x.example",))], SearchContext(), provider=fake, cfg=_cfg())
        assert len(fake.requests) == 2

    async def test_yesterdays_answer_is_not_served(self, session) -> None:  # noqa: ANN001
        fake = _fake()
        yesterday = datetime.now(timezone.utc) - timedelta(days=1, hours=1)
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        for row in (await session.execute(select(WebSearchQuery))).scalars():
            row.created_at = yesterday
        await session.flush()
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        assert len(fake.requests) == 2

    async def test_url_only_storage_keeps_no_title_or_snippet(self, session) -> None:  # noqa: ANN001
        fake = _fake(capabilities=SearchCapabilities(result_storage=RESULT_STORAGE_URL_ONLY))
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        rows = (await session.execute(select(WebSearchResult))).scalars().all()
        assert rows and all(r.title is None and r.snippet is None for r in rows)
        assert all(r.url for r in rows)

    async def test_tavily_credits_flow_into_consumption(self, session) -> None:  # noqa: ANN001
        t = _Tavily()
        result = await run_searches(session, [_req(), _req(Q_NEWS)], SearchContext(), provider=t.provider, cfg=_cfg())
        assert result.consumption.web_search_calls == 2
        assert result.consumption.tavily_credits == pytest.approx(2.0)
        assert "tavily_credits" in result.consumption.instrumented
        assert result.consumption.unreported == frozenset()

    async def test_one_unreported_credit_makes_the_credit_total_unknown(self, session) -> None:  # noqa: ANN001
        bodies = iter([_fixture("tavily_transformer_manufacturers.json"), _fixture("tavily_no_usage.json")])
        t = _Tavily(lambda r: httpx.Response(200, json=next(bodies)))
        result = await run_searches(session, [_req(), _req(Q_NO_USAGE)], SearchContext(), provider=t.provider, cfg=_cfg(), concurrency=1)
        # Instrumented (so the flat per-search price cannot stand in) AND unreported
        # (so the cost is unknown) — review C3.
        assert "tavily_credits" in result.consumption.instrumented
        assert result.consumption.unreported == frozenset({"tavily_credits"})
        book = _book('{"tavily": {"usd_per_credit": 0.008}}')
        assert derive_cost(result.consumption, book).estimated_usd is None


# --------------------------------------------------------------------------- #
# 9. Cost: an unpriced unit keeps cost unknown
# --------------------------------------------------------------------------- #


def _book(raw: str) -> Any:
    return price_book_from_settings(SimpleNamespace(v3_price_vendor_rates=raw))


class TestCost:
    def test_the_unit_exists(self) -> None:
        assert "tavily_credits" in UNIT_NAMES
        assert "web_search_calls" in UNIT_NAMES

    def test_credits_are_priced_from_the_vendor_rates(self) -> None:
        book = _book('{"tavily": {"usd_per_credit": 0.008}}')
        assert book.credit_rate("tavily") == pytest.approx(0.008)
        units = ConsumptionUnits(
            web_search_calls=3, tavily_credits=3,
            instrumented=frozenset({"web_search_calls", "tavily_credits"}),
        )
        cost = derive_cost(units, book)
        assert cost.estimated_usd == pytest.approx(0.024), "calls are not billed twice"

    def test_unpriced_credits_make_cost_unknown_never_zero(self) -> None:
        book = _book('{"deepseek": {"input_per_million": 0.3, "cached_input_per_million": 0.006, "output_per_million": 1.2}}')
        units = ConsumptionUnits(
            web_search_calls=1, tavily_credits=1,
            instrumented=frozenset({"web_search_calls", "tavily_credits"}),
        )
        cost = derive_cost(units, book)
        assert cost.estimated_usd is None
        assert "tavily_credits" in cost.unpriced_units

    @pytest.mark.parametrize("raw", ['{"tavily": {"usd_per_credit": -1}}', '{"tavily": {"usd_per_credit": "x"}}', "{bad"])
    def test_a_bad_credit_rate_is_no_rate(self, raw: str) -> None:
        assert _book(raw).credit_rate("tavily") is None

    def test_a_record_without_credits_keeps_the_legacy_search_price(self) -> None:
        book = price_book_from_settings(
            SimpleNamespace(v3_price_vendor_rates="", v3_price_per_thousand_web_searches=5.0)
        )
        units = ConsumptionUnits(web_search_calls=2, instrumented=frozenset({"web_search_calls"}))
        assert derive_cost(units, book).estimated_usd == pytest.approx(0.01)


# --------------------------------------------------------------------------- #
# 10. The admin audit endpoint
# --------------------------------------------------------------------------- #


class TestAdminEndpoint:
    def test_it_is_mounted_under_the_admin_prefix(self) -> None:
        from app.main import app

        paths = set(app.openapi()["paths"])
        assert "/api/v1/admin/web-research/jobs/{research_job_id}" in paths
        assert "/api/v1/admin/web-research/discovery-runs/{discovery_run_id}" in paths

    async def test_basic_auth_guards_it_like_every_admin_route(self) -> None:
        from fastapi import FastAPI

        from app.api.v1.web_research_admin import router
        from app.core.staging_auth import install_staging_basic_auth

        guarded = FastAPI()
        install_staging_basic_auth(guarded, "admin:planted-password")
        guarded.include_router(router, prefix="/api/v1")
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=guarded), base_url="http://t") as c:
            r = await c.get(f"/api/v1/admin/web-research/jobs/{uuid.uuid4()}")
        assert r.status_code == 401

    async def test_shape_and_404(self, session) -> None:  # noqa: ANN001
        from fastapi import FastAPI

        from app.api.v1.web_research_admin import router
        from app.db.session import get_db
        from app.models.discovery import DiscoveryRun
        from app.models.research_job import ResearchJob

        job = ResearchJob(id=uuid.uuid4(), job_type="company_research", idempotency_key="k", status="running")
        run = DiscoveryRun(id=uuid.uuid4(), status="running", provider_name="free_real",
                           universe_source="curated_seed", universe_count=0, processed_count=0,
                           candidate_count=0)
        session.add_all([job, run])
        await session.flush()
        fake = _fake(mode_by_query={fixture_key(Q_NEWS): MODE_HTTP_429})
        await run_searches(
            session, [_req(), _req(Q_NEWS)], SearchContext(research_job_id=job.id),
            provider=fake, cfg=_cfg(),
        )
        await session.commit()

        api = FastAPI()
        api.include_router(router, prefix="/api/v1")
        api.dependency_overrides[get_db] = lambda: session
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
            ok = await c.get(f"/api/v1/admin/web-research/jobs/{job.id}")
            empty = await c.get(f"/api/v1/admin/web-research/discovery-runs/{run.id}")
            missing = await c.get(f"/api/v1/admin/web-research/jobs/{uuid.uuid4()}")
            missing_run = await c.get(f"/api/v1/admin/web-research/discovery-runs/{uuid.uuid4()}")
        assert (missing.status_code, missing_run.status_code) == (404, 404)
        assert ok.status_code == 200
        body = ok.json()
        assert body["scope"] == "research_job"
        assert body["totals"]["queries"] == 2
        assert body["totals"]["executed"] == 1
        assert body["totals"]["network_call_count"] == 2
        assert body["totals"]["errors_by_code"] == {"http_429": 1}
        assert body["totals"]["results"] == 4
        assert body["totals"]["fetch_attempts"] == 0
        executed = next(qr for qr in body["queries"] if qr["executed"])
        assert executed["provider_request_id"] and executed["results"][0]["disposition"] == "candidate"
        assert "INTERNAL ADMIN ONLY" in body["notice"]
        assert empty.status_code == 200 and empty.json()["totals"]["queries"] == 0

    async def test_a_missing_table_is_a_503_not_an_empty_audit(self) -> None:
        from fastapi import FastAPI

        from app.api.v1.web_research_admin import router
        from app.db.session import get_db

        engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                     connect_args={"check_same_thread": False})
        from app.models.research_job import ResearchJob

        async with engine.begin() as conn:
            await conn.run_sync(lambda sync: ResearchJob.__table__.create(sync))
        maker = async_sessionmaker(engine, expire_on_commit=False)
        async with maker() as s:
            job = ResearchJob(id=uuid.uuid4(), job_type="company_research", idempotency_key="k", status="done")
            s.add(job)
            await s.commit()
            api = FastAPI()
            api.include_router(router, prefix="/api/v1")
            api.dependency_overrides[get_db] = lambda: s
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api), base_url="http://t") as c:
                r = await c.get(f"/api/v1/admin/web-research/jobs/{job.id}")
        await engine.dispose()
        assert r.status_code == 503
        assert "042" in r.json()["detail"]


# --------------------------------------------------------------------------- #
# 11. Review round 1 — security S1–S9, code C1–C11, and the surviving mutants
# --------------------------------------------------------------------------- #


class _Clock:
    """Returns the scripted values in order, then repeats the last one."""

    def __init__(self, *values: float) -> None:
        self.values = list(values)

    def __call__(self) -> float:
        return self.values.pop(0) if len(self.values) > 1 else self.values[0]


class TestReviewSecurity:
    async def test_s1_secret_check_runs_even_when_the_gate_refuses(self, session) -> None:  # noqa: ANN001
        # Refused as url_in_query too — but the credential check comes first.
        await run_searches(
            session,
            [_req("see https://api.example/x?api_token=abc123secretvalue")],
            SearchContext(), provider=_fake(), cfg=_cfg(),
        )
        row = (await session.execute(select(WebSearchQuery))).scalar_one()
        assert row.error_code == "credential_in_payload"
        leaked = "abc123secretvalue" in (row.query_text + row.request_hash)
        assert leaked is False

    async def test_s1_private_token_is_withheld_even_when_the_gate_refuses(self, session) -> None:  # noqa: ANN001
        ctx = SearchContext(private_tokens=frozenset({"Pensana"}))
        await run_searches(
            session, [_req("pensana inurl:admin " + "x " * 250)], ctx, provider=_fake(), cfg=_cfg()
        )
        row = (await session.execute(select(WebSearchQuery))).scalar_one()
        assert row.error_code == "G1_private_token"
        assert row.query_text == "[withheld: G1_private_token]"

    async def test_s1_a_refused_query_is_stored_bounded_and_url_stripped(self, session) -> None:  # noqa: ANN001
        long_query = "transformers " * 60
        await run_searches(
            session,
            [_req(long_query), _req("look at https://files.example/a?signature=abc123secretvalue")],
            SearchContext(), provider=_fake(), cfg=_cfg(),
        )
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        by_code = {r.error_code: r for r in rows}
        assert len(by_code["query_too_long"].query_text) <= q.MAX_QUERY_CHARS
        stored = by_code["url_in_query"].query_text
        leaked = "abc123secretvalue" in stored
        assert leaked is False
        assert "files.example" in stored

    @pytest.mark.parametrize(
        "payload",
        ["?api_token=x", "session token=abc", "X-API-Key: y", "&sig=zzz", "tvly-abcdef"],
    )
    def test_s2_more_credential_markers(self, payload: str) -> None:
        from app.services.providers.governance import (
            CredentialInPayloadError,
            assert_no_credentials,
        )

        with pytest.raises(CredentialInPayloadError):
            assert_no_credentials(payload)

    async def test_s3_c1_a_withheld_refusal_never_logs_its_hash(self, session, caplog) -> None:  # noqa: ANN001
        caplog.set_level(logging.INFO)
        request = _req("my secret holding competitors")
        await run_searches(
            session, [request], SearchContext(private_tokens=frozenset({"my secret holding"})),
            provider=_fake(), cfg=_cfg(),
        )
        assert "request_hash=withheld" in caplog.text
        leaked = request.request_hash()[:12] in caplog.text
        assert leaked is False

    @pytest.mark.parametrize(
        "text",
        [
            "ＡＡＰＬ outlook",  # full-width
            "AAPL_Q3 results",  # underscore is not a word character here
            "Аcme corp",  # Cyrillic А
            "Pen​sana results",  # zero-width space
            "Pen­sana results",  # soft hyphen
        ],
    )
    def test_s4_g1_cannot_be_evaded(self, text: str) -> None:
        tokens = {"AAPL", "Acme", "Pensana"}
        assert q.sanitise_query(text, private_tokens=tokens).refusal in (
            q.REFUSAL_PRIVATE_TOKEN,
            q.REFUSAL_INVISIBLE_CHAR,
        )
        assert q.validate_outgoing_query(text, tokens) in (
            q.REFUSAL_PRIVATE_TOKEN,
            q.REFUSAL_INVISIBLE_CHAR,
        )
        assert q.find_private_token(text, tokens) is True

    def test_s4_legitimate_text_still_passes(self) -> None:
        tokens = {"AAPL", "Acme"}
        assert q.sanitise_query("acmeville ports and aaplus", private_tokens=tokens).ok
        assert q.validate_outgoing_query("Transformatorenhersteller börsennotiert", tokens) is None

    async def test_s5_a_private_token_in_a_filter_is_withheld(self, session) -> None:  # noqa: ANN001
        fake = _fake()
        result = await run_searches(
            session, [_req(include_domains=("pensana.co.uk",))],
            SearchContext(private_tokens=frozenset({"Pensana"})), provider=fake, cfg=_cfg(),
        )
        assert result.outcomes[0].execution.error_code == "G1_private_token"
        assert fake.requests == []
        row = (await session.execute(select(WebSearchQuery))).scalar_one()
        assert row.filters_json is None

    @pytest.mark.parametrize(
        "over",
        [
            {"include_domains": ("https://evil.example/path",)},
            {"exclude_domains": ("10.0.0.5",)},
            {"include_domains": ("*.example.com",)},
            {"country": "Germany"},
            {"language": "english"},
        ],
    )
    async def test_s5_filters_must_be_well_formed(self, session, over: dict) -> None:  # noqa: ANN001
        fake = _fake()
        result = await run_searches(session, [_req(**over)], SearchContext(), provider=fake, cfg=_cfg())
        assert result.outcomes[0].execution.error_code == "filter_invalid"
        assert fake.requests == []
        t = _Tavily()
        execution, _ = await t.provider.search(_req(**over))
        assert execution.error_code == "filter_invalid" and t.calls == []

    @pytest.mark.parametrize("env", ["staging", "production", ""])
    async def test_s6_the_fake_is_refused_outside_dev_and_test(self, session, env: str) -> None:  # noqa: ANN001
        cfg = _cfg(app_env=env)
        provider = web_search_provider_from_settings(cfg)
        assert isinstance(provider, UnavailableSearchProvider)
        assert provider.error_code == "fake_not_allowed_in_env"
        result = await run_searches(session, [_req()], SearchContext(), cfg=cfg)
        assert result.state == STATE_UNAVAILABLE
        assert result.outcomes[0].execution.error_code == "fake_not_allowed_in_env"
        assert result.network_call_count == 0

    @pytest.mark.parametrize("env", ["development", "test"])
    def test_s6_the_fake_is_allowed_in_dev_and_test(self, env: str) -> None:
        assert isinstance(web_search_provider_from_settings(_cfg(app_env=env)), FakeWebSearchProvider)

    def test_s7_c5_cached_from_must_be_a_uuid_and_callless(self) -> None:
        common: dict[str, Any] = dict(
            provider="x", executed=True, provider_request_id="r",
            http_status=200, latency_ms=0, result_count=0,
        )
        with pytest.raises(ValueError, match="UUID"):
            SearchExecution(**common, network_call_count=0, cached_from="anything")
        with pytest.raises(ValueError, match="no network call"):
            SearchExecution(**common, network_call_count=1, cached_from=str(uuid.uuid4()))
        SearchExecution(**common, network_call_count=0, cached_from=str(uuid.uuid4()))

    async def test_s8_the_url_is_built_from_the_validated_host(self) -> None:
        t = _Tavily(base_url="https://API.TAVILY.COM/")
        await t.provider.search(_req())
        assert str(t.calls[0].url) == "https://api.tavily.com/search"

    async def test_s9_result_urls_are_stored_without_secrets(self, session) -> None:  # noqa: ANN001
        body = {
            "request_id": "synthetic-s9",
            "results": [{"url": "https://docs.example/report.pdf?token=abc123secretvalue&page=2",
                         "title": "t", "content": "c"}],
            "usage": {"credits": 1},
        }
        t = _Tavily(lambda r: httpx.Response(200, json=body))
        await run_searches(session, [_req()], SearchContext(), provider=t.provider, cfg=_cfg())
        row = (await session.execute(select(WebSearchResult))).scalar_one()
        leaked = "abc123secretvalue" in (row.url + (row.canonical_url or ""))
        assert leaked is False
        assert "page=2" in row.url


class TestReviewCode:
    async def test_c2_a_cancelled_run_still_records_its_calls(self, session) -> None:  # noqa: ANN001
        import asyncio

        started = asyncio.Event()

        class Hanging:
            name = "fake_web_search"
            capabilities = SearchCapabilities(result_storage="full")
            is_configured = True

            async def search(self, request):  # noqa: ANN001, ANN201
                started.set()
                await asyncio.Event().wait()

        task = asyncio.create_task(
            run_searches(session, [_req()], SearchContext(), provider=Hanging(), cfg=_cfg())
        )
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        row = (await session.execute(select(WebSearchQuery))).scalar_one()
        assert (row.error_code, row.network_call_count, row.executed) == ("cancelled", 1, False)
        assert await network_calls_today(session) == 1, "the daily cap sees the call"

    @pytest.mark.parametrize(
        ("handler", "unreported"),
        [
            (lambda r: httpx.Response(200, content=b"not json"), True),  # 2xx, probably billed
            (lambda r: httpx.Response(429, json={}), False),  # refused, not billed
        ],
    )
    async def test_c3_unreported_credits_are_unknown_never_flat_priced(
        self, session, handler: Any, unreported: bool  # noqa: ANN001
    ) -> None:
        t = _Tavily(handler)
        result = await run_searches(session, [_req()], SearchContext(), provider=t.provider, cfg=_cfg())
        units = result.consumption
        assert "tavily_credits" in units.instrumented
        assert ("tavily_credits" in units.unreported) is unreported
        book = price_book_from_settings(SimpleNamespace(
            v3_price_vendor_rates='{"tavily": {"usd_per_credit": 0.008}}',
            v3_price_per_thousand_web_searches=5.0,
        ))
        cost = derive_cost(units, book).estimated_usd
        if unreported:
            assert cost is None, "never the flat per-search price for a billed call"

    async def test_c4_the_date_window_is_client_only_when_every_result_was_dated(self) -> None:
        t = _Tavily(lambda r: httpx.Response(200, json=_fixture("tavily_news_catalyst.json")))
        execution, items = await t.provider.search(_req(Q_NEWS, date_from=date(2026, 9, 1)))
        assert items and all(i.published_hint for i in items)
        assert execution.filters_enforced_by["date_range"] == "client"
        assert execution.date_unchecked_count == 0

    async def test_c4_the_fake_cannot_claim_a_window_it_did_not_check(self) -> None:
        execution, _ = await _fake().search(_req(date_from=date(2025, 1, 1)))
        assert execution.filters_enforced_by["date_range"] == "unsupported"
        assert execution.date_unchecked_count == 2

    async def test_c8_an_identical_request_in_one_batch_is_sent_once(self, session) -> None:  # noqa: ANN001
        fake = _fake()
        result = await run_searches(session, [_req(), _req("  " + Q_TRANSFORMERS.upper())], SearchContext(), provider=fake, cfg=_cfg())
        assert len(fake.requests) == 1
        first, dup = result.outcomes
        assert dup.from_cache and dup.execution.cached_from == str(first.query_id)
        assert dup.execution.network_call_count == 0
        assert result.state == STATE_OK
        row = await session.get(WebSearchQuery, dup.query_id)
        assert row.served_from_query_id == first.query_id

    async def test_c8_a_duplicate_of_a_failed_call_is_not_executed(self, session) -> None:  # noqa: ANN001
        fake = _fake(mode=MODE_OUTAGE)
        result = await run_searches(session, [_req(), _req()], SearchContext(), provider=fake, cfg=_cfg())
        assert len(fake.requests) == 1
        assert [o.execution.error_code for o in result.outcomes] == ["http_5xx", "duplicate_in_batch"]

    async def test_c9_wall_time_is_checked_as_each_call_starts(self, session) -> None:  # noqa: ANN001
        budget = _budget(wall=10, clock=_Clock(0.0, 0.0, 1000.0))
        fake = _fake()
        result = await run_searches(session, [_req()], SearchContext(budget=budget), provider=fake, cfg=_cfg())
        assert result.outcomes[0].execution.error_code == "budget:max_wall_seconds"
        assert fake.requests == []

    async def test_c10_long_provenance_strings_are_clipped(self, session) -> None:  # noqa: ANN001
        await run_searches(
            session, [_req(template_version="t" * 100, origin="o" * 100)],
            SearchContext(stage="s" * 100), provider=_fake(), cfg=_cfg(),
        )
        row = (await session.execute(select(WebSearchQuery))).scalar_one()
        assert (len(row.template_version), len(row.origin), len(row.stage)) == (40, 30, 40)

    async def test_c11_a_truncated_audit_says_so(self, session, monkeypatch) -> None:  # noqa: ANN001
        from app.services.web_research import audit

        job_id = uuid.uuid4()
        await run_searches(
            session, [_req(), _req(Q_NEWS)], SearchContext(research_job_id=job_id),
            provider=_fake(), cfg=_cfg(),
        )
        monkeypatch.setattr(audit, "MAX_QUERIES", 1)
        read = await audit.web_research_audit(session, research_job_id=job_id)
        assert read.queries_truncated is True and len(read.queries) == 1
        assert read.fetch_attempts_truncated is False


class TestSurvivingMutants:
    async def test_a_cache_serve_is_never_itself_a_cache_source(self, session) -> None:  # noqa: ANN001
        # Kills: dropping `network_call_count > 0` from the cache query.
        fake = _fake()
        first = await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        third = await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        assert third.outcomes[0].execution.cached_from == str(first.outcomes[0].query_id)
        assert len(fake.requests) == 1

    async def test_a_failed_call_is_retried_later_the_same_day(self, session) -> None:  # noqa: ANN001
        # Kills: dropping `executed.is_(True)` from the cache query.
        fake = _fake(mode=MODE_HTTP_429)
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        fake.mode = "ok"
        again = await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        assert len(fake.requests) == 2
        assert again.outcomes[0].execution.executed and not again.outcomes[0].from_cache

    async def test_a_vendor_ignoring_exclude_domains_cannot_leak(self) -> None:
        # Kills: disabling the client-side exclude_domains check (no date filter to hide it).
        t = _Tavily()
        execution, items = await t.provider.search(_req(exclude_domains=("aggregator.example",)))
        assert "aggregator.example" not in [i.domain for i in items]
        assert execution.client_filtered_count == 1
        assert len(items) == 3

    async def test_the_cache_bucket_is_the_utc_day_not_a_rolling_24h(self, session) -> None:  # noqa: ANN001
        # Kills: replacing the UTC-day bucket with a rolling 24h window.
        fake = _fake()
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg())
        for row in (await session.execute(select(WebSearchQuery))).scalars():
            row.created_at = datetime(2026, 9, 29, 23, 50, tzinfo=timezone.utc)
        await session.flush()
        after_midnight = datetime(2026, 9, 30, 0, 30, tzinfo=timezone.utc)
        await run_searches(session, [_req()], SearchContext(), provider=fake, cfg=_cfg(), now=after_midnight)
        assert len(fake.requests) == 2, "40 minutes old, but yesterday's bucket"
