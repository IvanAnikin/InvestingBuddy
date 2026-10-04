# ruff: noqa: F811 - pytest fixtures are imported and requested by argument name
"""Open-web W5 review round 1 — one mutation-sensitive test per HIGH/MEDIUM finding.

Each test fails if the fix it names is reverted (the comment says what it would see).
Everything is offline on SQLite; ``test_web_w5_postgres.py`` holds the FK-dependent ones.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select, update

from app.integrations.deepseek.transport import FakeDeepSeekTransport
from app.models.research_document import ResearchDocumentVersion
from app.models.web_research import WebSearchQuery
from app.services import safety_terms
from app.services.consumption import ConsumptionUnits, PriceBook, derive_cost
from app.services.providers.contracts import QueryFamily, SearchResultItem
from app.services.web_research import planner as pl
from app.services.web_research import queries as q
from app.services.web_research import selection as sel
from app.services.web_research import stage as st
from tests.test_web_w5_pipeline import Spies, _web_cfg
from tests.test_web_w5_pipeline import _company as pipeline_company
from tests.test_web_w5_pipeline import session as pipeline_session  # noqa: F401
from tests.test_web_w5_stage import (  # noqa: F401 - fixtures
    FIXTURES,
    INGEST,
    FakeNet,
    Harness,
    _cfg,
    _company,
    h,
    pool,
    session,
)

TODAY = date(2026, 10, 4)
CATALYST_URL = "https://www.tdworld.com/grid/voltgrid-order"

POISON = (
    "<html><head><title>Copper news</title></head><body><article><h1>Voltgrid copper</h1>"
    + "".join(
        "<p>Copper cathode and copper concentrate output rose as the JORC compliant Mineral "
        "Resource estimate and ore reserve permits, offtake and feasibility study advanced "
        f"at the project in period {i}. Voltgrid Corp said copper is its main product.</p>"
        for i in range(12)
    )
    + "</article></body></html>"
)


# --------------------------------------------------------------------------- #
# C-H1 / F1 — a stored web chunk cannot move the profile or the stage detector
# --------------------------------------------------------------------------- #


class TestWebChunksNeverFeedTheDetectors:
    async def _store_poison(self, h: Harness, session: Any) -> Any:
        h.net = FakeNet(extra={CATALYST_URL: POISON.encode()})
        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        assert result.summary["ingest"]["ingested"] >= 1
        return company

    async def test_the_subject_profile_ignores_stored_web_pages(
        self, h: Harness, session: Any
    ) -> None:
        from app.services.director.subject_profile import build_subject_profile

        company = await self._store_poison(h, session)
        profile = await build_subject_profile(session, company)
        # Reverting the join makes this read the poisoned chunks: commodities non-empty.
        assert profile.chunks_read == 0 and profile.commodities == []

    async def test_the_same_text_from_an_official_document_does_count(
        self, h: Harness, session: Any
    ) -> None:
        """Control: the filter is the discriminator, not the text."""
        from app.services.director.subject_profile import build_subject_profile

        company = await self._store_poison(h, session)
        await session.execute(update(ResearchDocumentVersion).values(web_extractor_version=None))
        await session.flush()
        profile = await build_subject_profile(session, company)
        assert profile.chunks_read > 0
        assert any(m.commodity.slug == "copper" for m in profile.commodities)

    async def test_the_stage_detector_is_given_no_web_text(
        self, h: Harness, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.classification import stage as stage_mod

        company = await self._store_poison(h, session)
        seen: list[list[str]] = []
        real = stage_mod.assess

        def spy(facts: Any, texts: Any, **kw: Any) -> Any:
            seen.append(list(texts))
            return real(facts, texts, **kw)

        monkeypatch.setattr(stage_mod, "assess", spy)
        await stage_mod.detect_stage(session, company)
        assert seen == [[]], "no stored web chunk reaches the stage assessment"
        await session.execute(update(ResearchDocumentVersion).values(web_extractor_version=None))
        await session.flush()
        seen.clear()
        await stage_mod.detect_stage(session, company)
        assert seen and seen[0], "control: an official document's chunks are read"

    async def test_the_second_run_plans_without_the_poisoned_theme(
        self, h: Harness, session: Any
    ) -> None:
        """End to end: after run 1 stored the page, run 2's themes come from official
        documents only, so no theme query is planned from the page."""
        from app.services.director.subject_profile import build_subject_profile

        company = await self._store_poison(h, session)
        profile = await build_subject_profile(session, company)
        await h.run(
            company, cfg=_cfg(**INGEST), mode="standard",
            themes=[m.commodity.name for m in profile.commodities],
        )
        sent = " ".join(r.query.lower() for r in h.provider.requests)
        assert "copper" not in sent


# --------------------------------------------------------------------------- #
# C-H2 / F2 — an unsafe URL is dropped in both places
# --------------------------------------------------------------------------- #

BAD_URL = "https://news.example/voltgrid-strong-buy-rating-order"


class TestUnsafeUrlsAreDropped:
    def test_safe_url(self) -> None:
        assert st.safe_url(BAD_URL) is None
        assert st.safe_url(CATALYST_URL) == CATALYST_URL
        assert st.safe_url(None) is None

    async def test_the_stage_summary_drops_it_but_keeps_the_domain_and_title(
        self, h: Harness, session: Any
    ) -> None:
        page = (FIXTURES / "w5_catalyst_news.html").read_bytes()
        # The page is served from a URL that contains a gate term.
        h.net = FakeNet(extra={CATALYST_URL: page})
        original = h.net.body_for

        company = await _company(session)
        result = await h.run(company, cfg=_cfg(**INGEST))
        rows = result.summary["catalyst_evidence"]
        assert rows and rows[0]["url"] == CATALYST_URL  # control: a safe URL is kept
        assert original is not None
        version = (await session.execute(select(ResearchDocumentVersion).where(
            ResearchDocumentVersion.canonical_url == CATALYST_URL))).scalar_one()
        version.canonical_url = BAD_URL
        await session.flush()
        again = await st._catalyst_evidence(session, [version.id])
        assert again[0]["url"] is None
        assert again[0]["domain"] == "news.example" and again[0]["title"]

    def test_the_v2_catalyst_section_drops_it_and_the_report_scan_is_clean(self) -> None:
        from app.services.pipeline import v3_pipeline as v3

        content = {"news_catalyst_discovery": {"available": True, "events": []}}
        report = SimpleNamespace(
            content_markdown="```json\n" + json.dumps(content) + "\n```", source_summary_json={}
        )
        outcome = v3.V3ResearchOutcome(web_context={"catalyst_evidence": [
            {"title": "Order", "url": BAD_URL, "domain": "news.example",
             "source_class": "trade_publication"}]})
        v3.attach_to_report(report, outcome)
        section = json.loads(
            report.content_markdown.split("```json")[1].split("```")[0]
        )["news_catalyst_discovery"]["web_catalyst_evidence"]
        assert section["value"][0]["url"] is None
        assert safety_terms.scan_value(section, path="x") == []
        assert "strong-buy" not in report.content_markdown


# --------------------------------------------------------------------------- #
# C-M3 — flag-off behaviour
# --------------------------------------------------------------------------- #


class TestFlagOffBehaviour:
    def test_mode_budgets_keep_their_old_ceilings_with_the_stage_off(self) -> None:
        from app.services.research_mode import ResearchMode, budget_for

        off = [budget_for(m, SimpleNamespace(v3_run_max_web_searches=0)).max_web_searches
               for m in ResearchMode]
        on = [budget_for(m, SimpleNamespace(
            v3_run_max_web_searches=0, v3_company_web_research_enabled=True)).max_web_searches
              for m in ResearchMode]
        assert off == [4, 12, 30, 60] and on == [6, 16, 36, 60]

    async def test_the_classification_block_reads_a_populated_corpus_the_same_way(
        self, h: Harness, session: Any
    ) -> None:
        """Populated-corpus order test (the old one had an empty corpus): with official
        chunks present, the profile and stage read them whichever order the pipeline
        runs its steps in, because they do not read the index."""
        from app.services.director.subject_profile import build_subject_profile

        company = await _company(session)
        h.net = FakeNet(extra={CATALYST_URL: POISON.encode()})
        await h.run(company, cfg=_cfg(**INGEST))
        await session.execute(update(ResearchDocumentVersion).values(web_extractor_version=None))
        await session.flush()
        before = await build_subject_profile(session, company)
        from app.services.corpus.indexing import ensure_company_indexed
        from app.services.corpus.search.backends.memory import InMemorySearchBackend

        await ensure_company_indexed(
            session, company_id=company.id, backend=InMemorySearchBackend(), cfg=_cfg(**INGEST)
        )
        after = await build_subject_profile(session, company)
        assert before.chunks_read > 0
        assert before.to_dict() == after.to_dict()


# --------------------------------------------------------------------------- #
# C-M4 — headroom is network calls; units survive a stage that dies mid-search
# --------------------------------------------------------------------------- #


class TestInvestigatorHeadroom:
    async def test_cache_serves_are_not_spend_and_failed_calls_are(
        self, pipeline_session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from app.services.corpus.search.backends.memory import InMemorySearchBackend
        from app.services.pipeline import v3_pipeline as v3

        Spies(monkeypatch, FakeNet())
        company = await pipeline_company(pipeline_session)
        first = await v3.run_v3_research(
            pipeline_session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        calls = first.consumption["web_stage"]["web_search_calls"]
        assert calls > 0
        assert first.loop["external_searches"]["limit"] == 16 - calls

        second = await v3.run_v3_research(
            pipeline_session, company, cfg=_web_cfg(), search_backend=InMemorySearchBackend()
        )
        assert second.web_context["queries"]["executed"] > 0
        assert second.web_context["queries"]["from_cache"] > 0
        assert second.consumption["web_stage"]["web_search_calls"] == 0
        # The old formula (queries "executed") would have said 16 - executed here.
        assert second.loop["external_searches"]["limit"] == 16

    async def test_a_stage_that_dies_inside_the_search_still_reports_the_reserved_calls(
        self, h: Harness, session: Any, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def dies_after_paying(sess: Any, requests: Any, context: Any, **kw: Any) -> Any:
            for _ in requests:
                assert context.budget.reserve_query() is None
            raise RuntimeError("connection reset after the calls were paid for")

        monkeypatch.setattr(st, "run_searches", dies_after_paying)
        company = await _company(session)
        result = await h.run(company)
        assert result.state == "web_stage_failed"
        assert result.units.web_search_calls == 5, "the wave-2 reservations"
        assert "web_search_calls" in result.units.instrumented


# --------------------------------------------------------------------------- #
# C-M5 / F6 — provenance write failure falls back instead of failing the stage
# --------------------------------------------------------------------------- #


class TestProvenanceFallback:
    async def test_a_failing_independent_session_falls_back_to_the_callers(
        self, h: Harness, session: Any
    ) -> None:
        class _Broken:
            def __call__(self) -> Any:
                return self

            async def __aenter__(self) -> Any:
                raise RuntimeError("foreign key violation on an uncommitted agent run")

            async def __aexit__(self, *a: Any) -> None:
                return None

        company = await _company(session)
        result = await h.run(company, deps=h.deps(persist_session_factory=_Broken()))
        assert result.state == "ok", "the stage does not fail because provenance could not commit"
        rows = (await session.execute(select(WebSearchQuery))).scalars().all()
        assert len(rows) == 6 and all(r.executed for r in rows)
        assert result.units.web_search_calls == 6


# --------------------------------------------------------------------------- #
# C-M6 — selection
# --------------------------------------------------------------------------- #


def _item(url: str, rank: int = 1, title: str = "", published: Any = None) -> SearchResultItem:
    from urllib.parse import urlsplit

    return SearchResultItem(
        rank=rank, url=url, canonical_url=url,
        domain=(urlsplit(url).hostname or "").removeprefix("www."), title=title or None,
        published_hint=published,
    )


def _cand(url: str, family: QueryFamily, rank: int = 1, title: str = "") -> sel.SearchCandidate:
    return sel.SearchCandidate(family=family, item=_item(url, rank, title), query_key="k")


def _ctx(**kw: Any) -> sel.SelectionContext:
    base: dict[str, Any] = {"today": TODAY, "identity_terms": ("Voltgrid",), "total": 10,
                            "issuer_domains": ("voltgrid.example",)}
    base.update(kw)
    return sel.SelectionContext(**base)


class TestSelectionRiskAndBestFamily:
    def test_a_url_found_by_two_families_is_kept_under_the_one_it_scores_best_in(self) -> None:
        url = "https://www.utilitydive.com/news/voltgrid-delay"
        title = "Voltgrid delay postponed lawsuit warning"
        got = sel.select_results(
            [_cand(url, QueryFamily.CATALYST, rank=1, title=title),
             _cand(url, QueryFamily.RISK, rank=5, title=title)],
            _ctx(),
        )
        # First-occurrence-in-family-order (the old rule) would file it under CATALYST.
        assert [s.candidate.family for s in got.selected] == [QueryFamily.RISK]
        assert got.skipped_by_reason() == {sel.SKIP_DUPLICATE_URL: 1}

    def test_an_issuer_page_gets_no_bonus_and_a_capped_prior_for_risk(self) -> None:
        issuer = "https://www.voltgrid.example/news/statement"
        risk = sel.score_candidate(_cand(issuer, QueryFamily.RISK, title="Voltgrid delay"), _ctx())
        catalyst = sel.score_candidate(
            _cand(issuer, QueryFamily.CATALYST, title="Voltgrid contract"), _ctx())
        assert risk.components["identity"] == 0.0
        assert risk.components["class_prior"] <= sel.RISK_ISSUER_PRIOR_CAP
        assert catalyst.components["identity"] == sel.W_IDENTITY_ISSUER_DOMAIN  # control

    def test_the_first_risk_pick_is_independent_of_the_issuer(self) -> None:
        issuer = _cand("https://www.voltgrid.example/news/delay-statement", QueryFamily.RISK,
                       rank=1, title="Voltgrid delay lawsuit warning postponed")
        independent = _cand("https://blog.example/voltgrid", QueryFamily.RISK, rank=9,
                            title="notes")
        got = sel.select_results(
            [issuer, independent], _ctx(quotas={QueryFamily.RISK: 1}, total=1))
        risk = [s for s in got.selected if s.candidate.family is QueryFamily.RISK]
        assert [s.candidate.url for s in risk] == [independent.url]

    def test_an_issuer_only_risk_pool_still_selects_it(self) -> None:
        issuer = _cand("https://www.voltgrid.example/news/x", QueryFamily.RISK, title="delay")
        got = sel.select_results([issuer], _ctx(quotas={QueryFamily.RISK: 1}))
        assert len(got.selected) == 1


# --------------------------------------------------------------------------- #
# C-M7 — cost is not unknown merely because the platform fetched pages
# --------------------------------------------------------------------------- #


class TestOwnFetcherIsNotAPricedVendorUnit:
    PRICES = PriceBook(usd_per_million_input_tokens=1.0, usd_per_million_output_tokens=2.0)

    def _units(self, *, own: bool) -> ConsumptionUnits:
        return ConsumptionUnits(
            model_input_tokens=1000, model_output_tokens=500, url_fetch_calls=12,
            bytes_downloaded=900_000 if own else 0,
            instrumented=frozenset({"url_fetch_calls", "model_input_tokens",
                                    "model_output_tokens"}
                                   | ({"bytes_downloaded"} if own else set())),
        )

    def test_with_the_own_fetcher_instrumented_the_cost_is_known(self) -> None:
        cost = derive_cost(self._units(own=True), self.PRICES)
        assert cost.estimated_usd is not None and not cost.unpriced_units

    def test_without_it_an_unpriced_fetch_still_makes_the_cost_unknown(self) -> None:
        cost = derive_cost(self._units(own=False), self.PRICES)
        assert cost.estimated_usd is None and "url_fetch_calls" in cost.unpriced_units

    def test_a_configured_fetch_price_is_still_applied(self) -> None:
        priced = PriceBook(usd_per_million_input_tokens=1.0, usd_per_million_output_tokens=2.0,
                           usd_per_thousand_url_fetches=5.0)
        base = derive_cost(self._units(own=True), self.PRICES).estimated_usd
        assert derive_cost(self._units(own=True), priced).estimated_usd > base  # type: ignore[operator]

    def test_an_unreported_credit_still_makes_it_unknown(self) -> None:
        units = ConsumptionUnits(
            web_search_calls=3, tavily_credits=2.0, bytes_downloaded=1,
            instrumented=frozenset({"web_search_calls", "tavily_credits", "bytes_downloaded"}),
            unreported=frozenset({"tavily_credits"}),
        )
        priced = PriceBook(credit_rates=(("tavily", 0.01),))
        assert derive_cost(units, priced).estimated_usd is None


# --------------------------------------------------------------------------- #
# F3 / F4 / F5 and the expansion cache key
# --------------------------------------------------------------------------- #


class TestSecurityLows:
    @pytest.mark.parametrize("text", ["Acme ｓｉｔｅ:evil.example ok", "Acme filetype：env x",
                                      "Acme ｉｎｕｒｌ:admin"])
    def test_compatibility_form_operators_are_stripped_by_the_sanitiser(self, text: str) -> None:
        clean = q.sanitise_query(text)
        assert clean.stripped_operators, "an NFKC-equivalent operator is an operator"
        assert ":" not in clean.text and "：" not in clean.text

    @pytest.mark.parametrize("text", ["Acme ｓｉｔｅ:evil.example", "Acme filetype：env"])
    def test_the_gate_refuses_them_even_when_the_normal_form_looks_valid(self, text: str) -> None:
        assert q.validate_outgoing_query(text) == q.REFUSAL_OPERATOR

    def test_the_gate_still_accepts_the_planners_own_operators_and_ratios(self) -> None:
        assert q.validate_outgoing_query("Acme site:ir.acme.example") is None
        assert q.validate_outgoing_query("Acme Q1：2025 results") is None

    def test_expansion_proposals_with_full_width_operators_are_refused(self) -> None:
        ok, refused = pl.validate_proposals(
            ["transformer ｓｉｔｅ:evil.example"], template_queries=[], limit=3)
        assert ok == [] and refused

    @pytest.mark.parametrize("payload", [
        "postgresql+psycopg://user:pw@host/db", "redis://:pw@cache:6379/0",
        "mongodb+srv://u:p@cluster/db", "key sk-abcdefghijklmnopqrstuvwx1234",
    ])
    def test_credential_shapes_are_refused(self, payload: str) -> None:
        from app.services.providers.governance import (
            CredentialInPayloadError,
            assert_no_credentials,
        )

        with pytest.raises(CredentialInPayloadError):
            assert_no_credentials(payload)

    def test_ordinary_text_with_sk_inside_a_word_is_not_a_credential(self) -> None:
        from app.services.providers.governance import assert_no_credentials

        assert_no_credentials("risk-adjusted task-based skills-gap analysis")

    async def test_an_unconfigured_provider_never_buys_an_expansion(
        self, h: Harness, session: Any
    ) -> None:
        pl.clear_expansion_cache()
        h.provider.is_configured = False
        h.transport = FakeDeepSeekTransport(completion_text='{"queries": ["a b"]}')
        company = await _company(session)
        await h.run(company, mode="standard")
        assert h.transport.completions == [], "no model call for queries nobody will issue"

    async def test_the_expansion_cache_key_includes_the_limit(self) -> None:
        pl.clear_expansion_cache()
        facts = pl.facts_from_company(
            SimpleNamespace(name="Voltgrid Corp", ticker="VGRD", exchange="NASDAQ", id=None),
            industry="Electrical equipment",
        )
        plan = pl.build_plan(facts, mode="standard", max_queries=6, today=TODAY)
        transport = FakeDeepSeekTransport(completion_text=json.dumps(
            {"queries": ["alpha one", "beta two", "gamma three"]}))
        small = await pl.propose_expansion(transport, facts, plan, limit=1, max_tokens=500)
        large = await pl.propose_expansion(transport, facts, plan, limit=3, max_tokens=500)
        assert small.queries == ("alpha one",)
        assert large.queries == ("alpha one", "beta two", "gamma three"), (
            "a larger request is not truncated by an earlier smaller one"
        )
        assert len(transport.completions) == 2
        again = await pl.propose_expansion(transport, facts, plan, limit=3, max_tokens=500)
        assert again.from_cache and len(transport.completions) == 2


_ = (datetime, timezone)
