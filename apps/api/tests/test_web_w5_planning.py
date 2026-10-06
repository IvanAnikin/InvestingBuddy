"""Open-web W5 — the query plan, result selection and the bounded crawl (offline, pure).

No network, no database. The planner and the selector are pure functions; the crawl runs
over an in-memory site graph through the same ``fetch`` seam ``open_web_fetch`` fills in
production, so every rule in spec §11.1 is asserted on real link extraction and scoring.
"""

from __future__ import annotations

import json
import random
from dataclasses import replace
from datetime import date, datetime, timezone
from typing import Any

import pytest

from app.integrations.deepseek.transport import FakeDeepSeekTransport
from app.services.providers.contracts import QueryFamily, SearchResultItem
from app.services.web_research import crawl as crawl_mod
from app.services.web_research import planner as pl
from app.services.web_research import selection as sel
from app.services.web_research.budget import WebBudgetLimits, WebResearchBudget
from app.services.web_research.fetch import (
    STATUS_FETCHED,
    STATUS_NOT_RETRIEVABLE,
    OpenWebFetchResult,
    WebFetchContext,
)

TODAY = date(2026, 10, 4)


class _Company:
    name = "Voltgrid Corp"
    ticker = "VGRD"
    exchange = "NASDAQ"
    id = None


def _facts(**kw: Any) -> pl.CompanyFacts:
    return pl.facts_from_company(
        _Company(),
        industry=kw.pop("industry", "Electrical equipment"),
        themes=kw.pop("themes", ("copper",)),
        **kw,
    )


def _plan(facts: pl.CompanyFacts | None = None, **kw: Any) -> pl.QueryPlan:
    kw.setdefault("today", TODAY)
    kw.setdefault("max_queries", 16)
    return pl.build_plan(facts or _facts(), **kw)


def _view(plan: pl.QueryPlan) -> list[tuple[Any, ...]]:
    return [
        (q.family.value, q.request.query, q.request.template_version, q.request.date_from,
         q.request.language, q.request.origin)
        for q in plan.queries
    ]


# --------------------------------------------------------------------------- #
# The plan
# --------------------------------------------------------------------------- #


class TestThePlanIsDeterministicAndVersioned:
    def test_same_facts_same_plan(self) -> None:
        assert _view(_plan()) == _view(_plan())

    def test_every_query_carries_the_template_version(self) -> None:
        plan = _plan(_facts(development_stage=True), mode="deep", max_queries=40)
        assert plan.queries
        for q in plan.queries:
            assert q.request.template_version
            assert q.request.template_version.startswith(pl.QUERY_TEMPLATE_VERSION)
            assert len(q.request.template_version) <= 40, "the column is String(40)"
        assert plan.template_version == pl.QUERY_TEMPLATE_VERSION

    def test_a_fixture_company_gets_the_expected_first_pass(self) -> None:
        plan = _plan(max_queries=6)
        assert [(q.family.value, q.request.query) for q in plan.queries] == [
            ("company_docs", "Voltgrid investor presentation 2026"),
            ("catalyst", "Voltgrid contract order"),
            ("competitive", "Voltgrid competitors"),
            ("industry", "Electrical equipment value chain"),
            ("risk", "Voltgrid delay"),
            ("company_docs", "Voltgrid annual report 2025"),
        ]

    def test_annual_reports_are_asked_by_explicit_year_latest_and_previous(self) -> None:
        queries = [q.request.query for q in _plan(max_queries=40).queries]
        assert "Voltgrid annual report 2025" in queries
        assert "Voltgrid annual report 2024" in queries

    def test_every_family_is_planned_even_under_a_tiny_budget(self) -> None:
        """RISK always runs: the round-robin takes one query per family first."""
        plan = _plan(max_queries=5)
        families = {q.family for q in plan.queries}
        assert families == set(pl.WAVE_FAMILIES)
        assert QueryFamily.RISK in families

    def test_the_budget_is_a_hard_ceiling(self) -> None:
        for cap in (0, 1, 3, 7, 16):
            assert len(_plan(max_queries=cap).queries) <= cap

    def test_waves_split_depth_from_challenge(self) -> None:
        plan = _plan(max_queries=16)
        assert {q.family for q in plan.by_wave(3)} == {QueryFamily.RISK}
        assert QueryFamily.RISK not in {q.family for q in plan.by_wave(2)}

    def test_hostile_facts_never_become_urls_or_operators(self) -> None:
        facts = replace(
            _facts(), short_name="Acme site:evil.example https://evil.example/x ok"
        )
        for q in _plan(facts, max_queries=16).queries:
            assert "http" not in q.request.query
            assert "site:" not in q.request.query

    def test_a_private_token_refuses_the_query_and_it_is_recorded(self) -> None:
        plan = _plan(private_tokens={"voltgrid"}, max_queries=16)
        assert all("voltgrid" not in q.request.query.lower() for q in plan.queries)
        assert plan.refused, "refused queries are recorded, never silently dropped"


class TestFreshnessWindows:
    @staticmethod
    def _days(plan: pl.QueryPlan, key_part: str) -> int:
        q = next(q for q in plan.queries if q.key.startswith(key_part))
        assert q.request.date_to == TODAY
        assert q.request.date_from is not None
        return (TODAY - q.request.date_from).days

    def test_catalyst_is_ninety_days_and_deep_extends_it_to_a_year(self) -> None:
        assert self._days(_plan(mode="standard"), "contract") == 90
        assert self._days(_plan(mode="deep", max_queries=36), "contract") == 365

    def test_catalyst_uses_the_news_topic(self) -> None:
        q = next(q for q in _plan().queries if q.key == "contract")
        assert q.request.topic == "news"

    def test_company_docs_windows(self) -> None:
        plan = _plan(max_queries=40)
        assert self._days(plan, "presentation") == pl.DAYS_18M
        assert self._days(plan, "annual_latest") == pl.DAYS_3Y

    def test_risk_is_a_year_and_litigation_eighteen_months(self) -> None:
        plan = _plan(max_queries=40)
        assert self._days(plan, "delay") == 365
        assert self._days(plan, "litigation") == pl.DAYS_18M

    def test_industry_and_competitive_windows(self) -> None:
        assert self._days(_plan(mode="standard", max_queries=40), "value_chain") == pl.DAYS_5Y
        assert self._days(_plan(mode="quick", max_queries=40), "value_chain") == pl.DAYS_3Y
        assert self._days(_plan(max_queries=40), "competitors") == pl.DAYS_3Y


class TestLanguageVariants:
    def test_a_german_venue_adds_german_queries_beside_english(self) -> None:
        facts = pl.facts_from_company(
            type("C", (), {"name": "Siemens Energy AG", "ticker": "ENR", "exchange": "XETRA",
                           "id": None})(),
            industry="Electrical equipment",
        )
        plan = pl.build_plan(facts, mode="standard", max_queries=16, today=TODAY)
        local = [q for q in plan.queries if q.locale == "de"]
        assert local, "the issuer's venue locale is planned"
        for q in local:
            assert q.request.language == "de" and q.request.country == "DE"
            assert "Siemens Energy AG" in q.request.query, "the legal local name"
        english = [q for q in plan.queries if not q.locale]
        assert english and all(q.request.language is None for q in english)
        assert all("Siemens Energy AG" not in q.request.query for q in english), (
            "English queries use the short English name"
        )

    def test_the_locale_table_matches_the_spec_examples(self) -> None:
        for venue, lang in (("XETRA", "de"), ("PA", "fr"), ("MI", "it"), ("MC", "es"),
                            ("CO", "da"), ("ST", "sv"), ("OL", "no"), ("HE", "fi"),
                            ("WA", "pl"), ("TSE", "ja"), ("HK", "zh"), ("SHG", "zh")):
            assert pl.locale_for_venue(venue)[0] == lang  # type: ignore[index]

    def test_an_english_venue_plans_english_only(self) -> None:
        plan = _plan(max_queries=40)
        assert not any(q.locale for q in plan.queries)

    def test_quick_mode_plans_no_local_variants(self) -> None:
        facts = replace(_facts(), venue="PA")
        assert not any(q.locale for q in _plan(facts, mode="quick", max_queries=6).queries)

    def test_every_glossary_concept_covers_every_locale(self) -> None:
        languages = {lang for lang, _country in pl.VENUE_LOCALES.values()}
        for concept, table in pl.GLOSSARY.items():
            assert languages <= set(table), (concept, languages - set(table))


class TestDevelopmentStageIssuers:
    def test_project_milestone_queries_only_for_a_development_stage_issuer(self) -> None:
        ordinary = {q.key for q in _plan(max_queries=40).queries}
        staged = {q.key for q in _plan(_facts(development_stage=True), max_queries=40).queries}
        project = {k for k in staged if k.startswith("project_")}
        assert project == {
            "project_permits", "project_offtake", "project_financing",
            "project_construction", "project_capex", "project_government_support",
        }
        assert not {k for k in ordinary if k.startswith("project_")}

    def test_project_templates_are_generic_wording(self) -> None:
        for template in pl.TEMPLATES:
            if template.development_only:
                assert template.pattern.startswith("{company} ")
                assert template.family is QueryFamily.CATALYST

    def test_a_small_budget_still_reaches_a_project_query(self) -> None:
        plan = _plan(_facts(development_stage=True), mode="quick", max_queries=6)
        assert any(q.key.startswith("project_") for q in plan.queries)


# --------------------------------------------------------------------------- #
# Bounded model expansion
# --------------------------------------------------------------------------- #


def _transport(queries: list[Any] | str) -> FakeDeepSeekTransport:
    text = queries if isinstance(queries, str) else json.dumps({"queries": queries})
    return FakeDeepSeekTransport(completion_text=text)


class TestExpansionValidation:
    def test_good_proposals_pass(self) -> None:
        ok, refused = pl.validate_proposals(
            ["grain oriented electrical steel supply", "transformer core lamination"],
            template_queries=[], limit=3,
        )
        assert len(ok) == 2 and refused == {}

    @pytest.mark.parametrize(
        ("proposal", "code"),
        [
            ("see https://evil.example/page for more", "url_or_operator"),
            ("transformer site:evil.example", "url_or_operator"),
            ("filetype:pdf transformer capacity", "url_or_operator"),
            (" ".join(["word"] * 13), "too_many_words"),
            ('"quoted phrase"', "bad_characters"),
            ("", "empty"),
        ],
    )
    def test_bad_proposals_are_refused_never_repaired(self, proposal: str, code: str) -> None:
        ok, refused = pl.validate_proposals([proposal], template_queries=[], limit=3)
        assert ok == [] and refused.get(code) == 1

    def test_the_cap_the_duplicates_and_private_tokens(self) -> None:
        ok, refused = pl.validate_proposals(
            ["alpha one", "beta two", "gamma three", "delta four", "Voltgrid competitors",
             "VGRD secret"],
            template_queries=["voltgrid competitors"], limit=2, private_tokens={"vgrd"},
        )
        assert ok == ["alpha one", "beta two"]
        assert refused.get("over_cap", 0) >= 1
        assert "G1_private_token" in refused or refused.get("over_cap", 0) >= 3

    def test_non_text_proposals_are_refused(self) -> None:
        ok, refused = pl.validate_proposals([None, 7, {"q": "x"}], template_queries=[], limit=3)
        assert ok == [] and refused == {"not_text": 3}


class TestExpansionIsBoundedCachedAndRecorded:
    @pytest.fixture(autouse=True)
    def _clean(self) -> None:
        pl.clear_expansion_cache()

    async def test_the_prompt_carries_the_intent_and_the_template_queries_only(self) -> None:
        facts, plan = _facts(), _plan(max_queries=6)
        transport = _transport(["grain oriented electrical steel supply"])
        result = await pl.propose_expansion(
            transport, facts, plan, limit=3, max_tokens=500
        )
        assert result.queries == ("grain oriented electrical steel supply",)
        (system, user), = transport.completions
        assert set(json.loads(user)) == {
            "company", "industry", "themes", "already_planned", "max_queries",
        }
        assert transport.json_mode_calls == [True]
        assert transport.thinking_calls == [False], "thinking is OFF for expansion"
        assert "Voltgrid contract order" in user

    async def test_the_same_intent_is_served_from_the_cache(self) -> None:
        facts, plan = _facts(), _plan(max_queries=6)
        transport = _transport(["transformer core lamination"])
        first = await pl.propose_expansion(transport, facts, plan, limit=3, max_tokens=500)
        second = await pl.propose_expansion(transport, facts, plan, limit=3, max_tokens=500)
        assert first.queries == second.queries
        assert second.from_cache is True
        assert len(transport.completions) == 1, "one model call for one intent"

    async def test_a_different_prompt_version_or_model_is_a_new_key(self) -> None:
        facts, plan = _facts(), _plan(max_queries=6)
        a, b = _transport(["one thing"]), _transport(["another thing"])
        b.model = "other-model"  # type: ignore[misc]
        await pl.propose_expansion(a, facts, plan, limit=3, max_tokens=500)
        out = await pl.propose_expansion(b, facts, plan, limit=3, max_tokens=500)
        assert out.from_cache is False and out.queries == ("another thing",)

    async def test_a_model_failure_costs_the_expansion_only(self) -> None:
        boom = FakeDeepSeekTransport(raises=RuntimeError("down"))
        result = await pl.propose_expansion(
            boom, _facts(), _plan(max_queries=6), limit=3, max_tokens=500
        )
        assert result.queries == () and result.error == "RuntimeError"

    async def test_an_unparseable_reply_is_recorded(self) -> None:
        result = await pl.propose_expansion(
            _transport("not json at all"), _facts(), _plan(max_queries=6), limit=3,
            max_tokens=500,
        )
        assert result.queries == () and result.error == "unparseable_reply"

    async def test_no_budget_means_no_call(self) -> None:
        transport = _transport(["x y"])
        for limit, tokens in ((0, 500), (3, 0)):
            await pl.propose_expansion(
                transport, _facts(), _plan(max_queries=6), limit=limit, max_tokens=tokens
            )
        assert transport.completions == []

    async def test_the_tokens_are_reported_as_units(self) -> None:
        result = await pl.propose_expansion(
            _transport(["one two"]), _facts(), _plan(max_queries=6), limit=3, max_tokens=500
        )
        assert result.units.model_calls == 1
        assert result.units.model_input_tokens > 0
        assert "model_calls" in result.units.instrumented
        assert result.units.by_vendor, "priced per vendor, never as an anonymous sum"

    def test_proposals_join_the_plan_recorded_as_llm_expansion_within_the_budget(self) -> None:
        plan = _plan(max_queries=16, expansion=("transformer core lamination",
                                                "electrical steel allocation"))
        assert len(plan.queries) <= 16
        expanded = [q for q in plan.queries if q.origin == pl.ORIGIN_LLM_EXPANSION]
        assert len(expanded) == 2
        for q in expanded:
            assert q.family in pl.EXPANSION_FAMILIES
            assert pl.EXPANSION_PROMPT_VERSION in (q.request.template_version or "")
        # A full template set neither crowds them out nor is crowded out beyond its tail.
        assert len(plan.queries) == 16


# --------------------------------------------------------------------------- #
# Selection
# --------------------------------------------------------------------------- #


def _item(url: str, rank: int = 1, *, title: str = "", snippet: str = "",
          published: datetime | None = None) -> SearchResultItem:
    from urllib.parse import urlsplit

    host = (urlsplit(url).hostname or "").removeprefix("www.")
    return SearchResultItem(
        rank=rank, url=url, canonical_url=url, domain=host, title=title or None,
        snippet=snippet or None, published_hint=published,
    )


def _cand(url: str, rank: int = 1, family: QueryFamily = QueryFamily.CATALYST,
          **kw: Any) -> sel.SearchCandidate:
    return sel.SearchCandidate(family=family, item=_item(url, rank, **kw), query_key="k")


def _ctx(**kw: Any) -> sel.SelectionContext:
    base: dict[str, Any] = {
        "today": TODAY, "identity_terms": ("Voltgrid",), "total": 10,
    }
    base.update(kw)
    return sel.SelectionContext(**base)


class TestSelectionIsDeterministic:
    def _candidates(self) -> list[sel.SearchCandidate]:
        out: list[sel.SearchCandidate] = []
        for i in range(12):
            family = pl.WAVE_FAMILIES[i % 5]
            out.append(
                _cand(f"https://site{i % 7}.example/doc/{i}", rank=1 + i % 4, family=family,
                      title=f"Voltgrid result {i}")
            )
        return out

    def test_input_order_does_not_change_the_choice(self) -> None:
        cands = self._candidates()
        ref = sel.select_results(cands, _ctx(total=6))
        for seed in range(5):
            shuffled = list(cands)
            random.Random(seed).shuffle(shuffled)
            again = sel.select_results(shuffled, _ctx(total=6))
            assert [s.candidate.url for s in again.selected] == [
                s.candidate.url for s in ref.selected
            ]

    def test_ties_break_on_provider_rank_then_url(self) -> None:
        same = [_cand("https://b.example/x", rank=2), _cand("https://a.example/x", rank=2),
                _cand("https://c.example/x", rank=1)]
        got = sel.select_results(same, _ctx(total=3))
        assert [s.candidate.url for s in got.selected][0] == "https://c.example/x"
        assert [s.candidate.url for s in got.selected][1:] == [
            "https://a.example/x", "https://b.example/x",
        ]

    def test_a_regulator_outranks_an_unknown_host_at_equal_relevance(self) -> None:
        got = sel.select_results(
            [_cand("https://blog.example/a", rank=1), _cand("https://www.ferc.gov/a", rank=2)],
            _ctx(total=1),
        )
        assert got.selected[0].candidate.url == "https://www.ferc.gov/a"
        assert got.selected[0].source_class == "regulator_publication"

    def test_title_and_snippet_terms_raise_relevance(self) -> None:
        plain = sel.score_candidate(_cand("https://x.example/a"), _ctx())
        rich = sel.score_candidate(
            _cand("https://x.example/b", title="Voltgrid wins contract order",
                  snippet="awarded"),
            _ctx(),
        )
        assert rich.components["relevance"] > plain.components["relevance"]
        assert rich.components["identity"] > plain.components["identity"]

    def test_freshness_fit_follows_the_family_window(self) -> None:
        inside = _item("https://x.example/a", published=datetime(2026, 9, 20, tzinfo=timezone.utc))
        outside = _item("https://x.example/b", published=datetime(2025, 1, 1, tzinfo=timezone.utc))
        undated = _item("https://x.example/c")
        f = lambda i: sel.freshness_fit(i, family=QueryFamily.CATALYST, mode="standard",  # noqa: E731
                                        today=TODAY)
        assert f(inside) == 1.0 and f(outside) == 0.0
        assert f(undated) == sel.UNDATED_FRESHNESS

    def test_the_issuer_domain_gets_the_identity_hint(self) -> None:
        ctx = _ctx(issuer_domains=("voltgrid.example",))
        scored = sel.score_candidate(_cand("https://ir.voltgrid.example/p"), ctx)
        assert scored.components["identity"] == sel.W_IDENTITY_ISSUER_DOMAIN
        assert scored.source_class in ("company_web_page", "investor_presentation")


class TestSelectionSkipsAreRecorded:
    def test_each_skip_has_its_reason(self) -> None:
        held = "https://held.example/doc"
        cands = [
            _cand(held, rank=1),
            _cand("https://dup.example/a", rank=1),
            _cand("https://dup.example/a", rank=2, family=QueryFamily.RISK),
            _cand("http://insecure.example/a", rank=3),
            _cand("https://web.archive.org/web/2026/https://x.example", rank=4),
            _cand("https://fine.example/a", rank=5),
        ]
        got = sel.select_results(cands, _ctx(held_urls=frozenset({held}), total=10))
        reasons = got.skipped_by_reason()
        assert reasons[sel.SKIP_ALREADY_HELD] == 1
        assert reasons[sel.SKIP_DUPLICATE_URL] == 1
        assert reasons[sel.SKIP_NOT_HTTPS] == 1
        assert reasons[sel.SKIP_DENYLISTED] == 1
        urls = {s.candidate.url for s in got.selected}
        assert urls == {"https://dup.example/a", "https://fine.example/a"}

    def test_over_budget_is_recorded(self) -> None:
        cands = [_cand(f"https://s{i}.example/a", rank=i + 1) for i in range(6)]
        got = sel.select_results(cands, _ctx(total=2))
        assert len(got.selected) == 2
        assert sum(got.skipped_by_reason().values()) == 4

    def test_a_family_quota_bounds_one_family(self) -> None:
        cands = [_cand(f"https://s{i}.example/a", rank=i + 1) for i in range(8)]
        got = sel.select_results(
            cands, _ctx(total=8, quotas={QueryFamily.CATALYST: 3})
        )
        # The unused quota flows to the best of the rest, still under the total cap.
        assert len(got.selected) == 8

    def test_quotas_sum_to_the_total_deterministically(self) -> None:
        for total in (0, 1, 7, 20, 31):
            q = sel.family_quotas(total)
            assert sum(q.values()) == total
            assert q == sel.family_quotas(total)

    def test_one_domain_cannot_fill_a_family(self) -> None:
        cands = [_cand(f"https://same.example/a{i}", rank=i + 1) for i in range(3)] + [
            _cand("https://other.example/a", rank=9)
        ]
        got = sel.select_results(cands, _ctx(total=2))
        assert {s.candidate.url for s in got.selected} >= {"https://other.example/a"}


# --------------------------------------------------------------------------- #
# The bounded crawl
# --------------------------------------------------------------------------- #


def _html(*links: tuple[str, str], pad: int = 0) -> bytes:
    anchors = "".join(f'<a href="{href}">{text}</a>\n' for href, text in links)
    return f"<html><body><main>{anchors}<p>{'x ' * pad}</p></main></body></html>".encode()


class Site:
    """An in-memory web: url → (content_class, bytes). Counts and records every fetch."""

    def __init__(self, pages: dict[str, tuple[str, bytes]], *, robots: set[str] | None = None):
        self.pages = pages
        self.robots = robots or set()
        self.fetched: list[str] = []

    async def fetch(self, session: Any, url: str, *, context: Any, budget: Any, origin: str,
                    **_kw: Any) -> OpenWebFetchResult:
        assert origin == "crawl", "the crawl fetches through open_web_fetch(origin='crawl')"
        if url in self.robots:
            return OpenWebFetchResult(status=STATUS_NOT_RETRIEVABLE, origin=origin,
                                      requested_url=url, failure_code="robots_disallowed")
        if url not in self.pages:
            return OpenWebFetchResult(status="failed", origin=origin, requested_url=url,
                                      failure_code="http_404")
        klass, body = self.pages[url]
        self.fetched.append(url)
        budget.record_fetch(byte_count=len(body), is_pdf=klass == "pdf")
        return OpenWebFetchResult(
            status=STATUS_FETCHED, origin=origin, requested_url=url, final_url=url,
            canonical_url=url, content=body, content_class=klass, charset="utf-8",
            bytes=len(body), tdm_decision="tdm_not_reserved",
        )


def _budget(**kw: Any) -> WebResearchBudget:
    limits = WebBudgetLimits(
        kw.pop("queries", 16), 100, kw.pop("fetches", 30), kw.pop("pdfs", 10),
        60 * 1024 * 1024, 600.0, kw.pop("per_domain", 8),
    )
    return WebResearchBudget(limits=limits, daily_cap=300)


def _cctx(**kw: Any) -> crawl_mod.CrawlContext:
    base: dict[str, Any] = {
        "today": TODAY, "issuer_domains": ("voltgrid.example",),
        "identity_terms": ("Voltgrid",), "query_terms": ("annual", "report"),
        "window_years": frozenset({2026, 2025, 2024}),
    }
    base.update(kw)
    return crawl_mod.CrawlContext(**base)


async def _crawl(site: Site, seeds: list[crawl_mod.CrawlSeed], *, budget: Any = None,
                 bounds: crawl_mod.CrawlBounds | None = None, on_fetched: Any = None,
                 ctx: crawl_mod.CrawlContext | None = None) -> crawl_mod.CrawlResult:
    return await crawl_mod.crawl(
        seeds, session=None, context=WebFetchContext(), budget=budget or _budget(),
        ctx=ctx or _cctx(), on_fetched=on_fetched, bounds=bounds, fetch=site.fetch,
    )


IR = "https://www.voltgrid.example/investors"
PDF_BODY = b"%PDF-1.4 fake"


def _ir_site() -> Site:
    return Site(
        {
            IR: ("html", _html(("/investors/reports", "Reports archive"),
                               ("/investors/presentations", "Investor presentations"))),
            "https://www.voltgrid.example/investors/reports": (
                "html", _html(("/files/annual-report-2025.pdf", "Annual report 2025"),
                              ("/investors/reports/old", "Older reports"))),
            "https://www.voltgrid.example/investors/presentations": (
                "html", _html(("/files/results-presentation-2026.pdf", "Results presentation"))),
            "https://www.voltgrid.example/files/annual-report-2025.pdf": ("pdf", PDF_BODY),
            "https://www.voltgrid.example/files/results-presentation-2026.pdf": ("pdf", PDF_BODY),
            "https://www.voltgrid.example/investors/reports/old": (
                "html", _html(("/files/annual-report-2019.pdf", "Annual report 2019"))),
            "https://www.voltgrid.example/files/annual-report-2019.pdf": ("pdf", PDF_BODY),
        }
    )


class TestCrawlChains:
    async def test_ir_landing_to_reports_to_the_latest_annual_pdf(self) -> None:
        site = _ir_site()
        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)])
        assert "https://www.voltgrid.example/files/annual-report-2025.pdf" in site.fetched
        assert got.documents_fetched >= 2
        assert got.max_depth_reached <= 2

    async def test_a_seed_that_is_already_fetched_is_not_fetched_again(self) -> None:
        site = _ir_site()
        page = await site.fetch(None, IR, context=None, budget=_budget(), origin="crawl")
        site.fetched.clear()
        await _crawl(site, [crawl_mod.CrawlSeed(IR, fetched=page)])
        assert IR not in site.fetched

    async def test_no_seeds_no_fetches(self) -> None:
        site = _ir_site()
        got = await _crawl(site, [])
        assert site.fetched == [] and got.fetches == []


class TestCrawlBounds:
    async def test_depth_is_at_most_two(self) -> None:
        site = Site(
            {
                IR: ("html", _html(("/investors/l1", "Investor reports"))),
                "https://www.voltgrid.example/investors/l1": (
                    "html", _html(("/investors/l2", "Investor reports archive"))),
                "https://www.voltgrid.example/investors/l2": (
                    "html", _html(("/investors/l3", "Investor reports more"))),
                "https://www.voltgrid.example/investors/l3": ("html", _html()),
            }
        )
        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)])
        assert "https://www.voltgrid.example/investors/l3" not in site.fetched
        assert got.max_depth_reached <= 2

    async def test_pages_per_domain_are_capped(self) -> None:
        links = tuple((f"/investors/reports/y{y}", f"Investor reports {y}") for y in range(30))
        pages: dict[str, tuple[str, bytes]] = {IR: ("html", _html(*links))}
        for y in range(30):
            pages[f"https://www.voltgrid.example/investors/reports/y{y}"] = (
                "html", _html())
        site = Site(pages)
        got = await _crawl(
            site, [crawl_mod.CrawlSeed(IR)],
            bounds=crawl_mod.CrawlBounds(max_pages_per_domain=4, novelty_window=50),
        )
        assert len(site.fetched) <= 4
        assert got.skipped.get(crawl_mod.SKIP_DOMAIN_CAP, 0) >= 1

    async def test_a_cross_domain_link_reaches_an_official_host_only(self) -> None:
        site = Site(
            {
                IR: ("html", _html(
                    ("https://www.ferc.gov/files/transformer-report-2026.pdf",
                     "Transformer study 2026"),
                    ("https://random-blog.example/annual-report-2026.pdf", "Annual report 2026"),
                    ("https://www.nema.org/reports/study-2026.pdf", "Industry study 2026"),
                )),
                "https://www.ferc.gov/files/transformer-report-2026.pdf": ("pdf", PDF_BODY),
                "https://random-blog.example/annual-report-2026.pdf": ("pdf", PDF_BODY),
                "https://www.nema.org/reports/study-2026.pdf": ("pdf", PDF_BODY),
            }
        )
        await _crawl(site, [crawl_mod.CrawlSeed(IR)])
        assert "https://www.ferc.gov/files/transformer-report-2026.pdf" in site.fetched
        assert "https://www.nema.org/reports/study-2026.pdf" in site.fetched
        assert "https://random-blog.example/annual-report-2026.pdf" not in site.fetched

    async def test_a_cross_domain_navigation_link_is_never_followed(self) -> None:
        site = Site(
            {
                IR: ("html", _html(("https://www.ferc.gov/news", "Investor news"))),
                "https://www.ferc.gov/news": ("html", _html()),
            }
        )
        await _crawl(site, [crawl_mod.CrawlSeed(IR)])
        assert site.fetched == [IR]

    async def test_robots_disallow_is_skipped_and_counted_not_fetched(self) -> None:
        site = _ir_site()
        site.robots = {"https://www.voltgrid.example/investors/reports"}
        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)])
        assert "https://www.voltgrid.example/investors/reports" not in site.fetched
        assert got.skipped.get("robots_disallowed") == 1
        assert "https://www.voltgrid.example/files/annual-report-2025.pdf" not in site.fetched

    async def test_three_pages_without_novelty_stop_the_walk(self) -> None:
        pages: dict[str, tuple[str, bytes]] = {}
        docs = tuple((f"/files/annual-report-{2026 - i}.pdf", f"Annual report {2026 - i}")
                     for i in range(8))
        pages[IR] = ("html", _html(*docs))
        for i in range(8):
            pages[f"https://www.voltgrid.example/files/annual-report-{2026 - i}.pdf"] = (
                "pdf", PDF_BODY)
        site = Site(pages)

        async def never_novel(page: Any, link: Any) -> bool:  # noqa: ARG001
            return False

        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)], on_fetched=never_novel,
                           bounds=crawl_mod.CrawlBounds(max_documents=20))
        assert got.stopped_by == crawl_mod.STOPPED_NOVELTY
        assert len(got.fetches) == 3

    async def test_a_navigation_page_neither_adds_to_nor_spends_the_novelty_streak(self) -> None:
        site = _ir_site()

        async def nav_aware(page: Any, link: Any) -> bool | None:
            return None if page.content_class == "html" else True

        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)], on_fetched=nav_aware)
        assert got.stopped_by != crawl_mod.STOPPED_NOVELTY
        assert got.novel_pages >= 2

    async def test_a_calendar_trap_is_cut_off(self) -> None:
        links = tuple((f"/investors/calendar/2026/{m:02d}", f"Investor calendar {m}")
                      for m in range(1, 13))
        pages: dict[str, tuple[str, bytes]] = {IR: ("html", _html(*links))}
        for m in range(1, 13):
            pages[f"https://www.voltgrid.example/investors/calendar/2026/{m:02d}"] = (
                "html", _html())
        site = Site(pages)
        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)],
                           bounds=crawl_mod.CrawlBounds(novelty_window=50))
        calendar = [u for u in site.fetched if "/calendar/" in u]
        assert len(calendar) <= crawl_mod.MAX_REPEATED_PATTERN
        assert got.skipped.get(crawl_mod.SKIP_REPEATED_PATTERN, 0) >= 1

    async def test_the_document_cap_bounds_pdfs(self) -> None:
        docs = tuple((f"/files/annual-report-{2026 - i}.pdf", f"Annual report {2026 - i}")
                     for i in range(8))
        pages: dict[str, tuple[str, bytes]] = {IR: ("html", _html(*docs))}
        for i in range(8):
            pages[f"https://www.voltgrid.example/files/annual-report-{2026 - i}.pdf"] = (
                "pdf", PDF_BODY)
        site = Site(pages)
        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)],
                           bounds=crawl_mod.CrawlBounds(max_documents=2, novelty_window=50))
        assert got.documents_fetched == 2
        assert got.skipped.get(crawl_mod.SKIP_DOCUMENT_CAP, 0) >= 1

    async def test_the_budget_stops_the_crawl(self) -> None:
        site = _ir_site()
        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)], budget=_budget(fetches=2))
        assert len(site.fetched) == 2
        assert got.stopped_by == crawl_mod.STOPPED_BUDGET
        assert got.skipped.get("budget:max_fetches") == 1

    async def test_the_pdf_budget_is_a_ceiling_on_documents(self) -> None:
        site = _ir_site()
        got = await _crawl(site, [crawl_mod.CrawlSeed(IR)], budget=_budget(pdfs=1))
        assert got.documents_fetched <= 1


class TestPublicSuffixSafety:
    def test_a_co_uk_site_does_not_widen_to_co_uk(self) -> None:
        from app.services.traversal.issuer_site import registrable_domain_of

        assert registrable_domain_of("https://www.issuer.co.uk/ir") == "issuer.co.uk"
        assert registrable_domain_of("https://co.uk/") is None

    async def test_a_sibling_co_uk_domain_is_not_same_domain(self) -> None:
        start = "https://www.issuer.co.uk/investors"
        site = Site(
            {
                start: ("html", _html(
                    ("https://www.other-company.co.uk/annual-report-2026.pdf", "Annual report"),
                    ("https://ir.issuer.co.uk/annual-report-2026.pdf", "Annual report 2026"),
                )),
                "https://www.other-company.co.uk/annual-report-2026.pdf": ("pdf", PDF_BODY),
                "https://ir.issuer.co.uk/annual-report-2026.pdf": ("pdf", PDF_BODY),
            }
        )
        await _crawl(site, [crawl_mod.CrawlSeed(start)], ctx=_cctx(issuer_domains=()))
        assert "https://ir.issuer.co.uk/annual-report-2026.pdf" in site.fetched
        assert "https://www.other-company.co.uk/annual-report-2026.pdf" not in site.fetched


class TestDocumentScoring:
    def test_a_pdf_with_a_report_anchor_and_a_recent_year_is_a_document(self) -> None:
        score, doc = crawl_mod.score_link(
            "https://www.voltgrid.example/files/annual-report-2025.pdf", "Annual report 2025",
            _cctx(),
        )
        assert doc and score >= crawl_mod.DOCUMENT_THRESHOLD

    def test_multilingual_anchors_count(self) -> None:
        for text in ("Geschäftsbericht 2025", "Rapport annuel 2025", "年度报告", "årsrapport"):
            _score, doc = crawl_mod.score_link("https://x.example/dl?id=1", text, _cctx())
            assert doc, text

    def test_a_year_outside_the_window_scores_less(self) -> None:
        recent, _ = crawl_mod.score_link("https://x.example/ar-2025.pdf", "Annual report", _cctx())
        old, _ = crawl_mod.score_link("https://x.example/ar-2009.pdf", "Annual report", _cctx())
        assert recent > old

    def test_navigation_is_followable_but_not_a_document(self) -> None:
        score, doc = crawl_mod.score_link("https://x.example/investors", "Investors", _cctx())
        assert not doc and score >= crawl_mod.FOLLOW_THRESHOLD

    def test_scoring_is_deterministic_and_versioned(self) -> None:
        args = ("https://x.example/a.pdf", "Annual report 2025", _cctx())
        assert crawl_mod.score_link(*args) == crawl_mod.score_link(*args)
        assert crawl_mod.CRAWL_SCORING_VERSION

    def test_url_pattern_collapses_digits_and_query_values(self) -> None:
        a = crawl_mod.url_pattern("https://x.example/cal/2026/01?page=2")
        b = crawl_mod.url_pattern("https://x.example/cal/2025/12?page=9")
        assert a == b


class TestBoundsValidation:
    def test_an_unbounded_crawl_is_not_offered(self) -> None:
        for field in ("max_depth", "max_pages_per_domain", "max_documents", "novelty_window"):
            with pytest.raises(ValueError):
                crawl_mod.CrawlBounds(**{field: 0})


class TestModePresetsAndUnits:
    def test_the_mode_web_search_counts_are_raised_and_match_the_web_profiles(self) -> None:
        from app.services.research_mode import (
            MODE_LIMITS,
            WEB_PROFILE_BY_MODE,
            ResearchMode,
            web_profile_for,
        )
        from app.services.web_research.budget import PROFILES

        assert [MODE_LIMITS[m].max_web_searches for m in ResearchMode] == [6, 16, 36, 60]
        for mode, profile in WEB_PROFILE_BY_MODE.items():
            assert MODE_LIMITS[mode].max_web_searches == PROFILES[profile].max_queries
        assert web_profile_for("deep") == "company_deep"
        assert web_profile_for("nonsense") == "company_standard", "a typo never runs MAX"

    def test_the_spec_profile_table_for_company_modes(self) -> None:
        from app.services.web_research.budget import PROFILES

        quick, standard, deep = (PROFILES[k] for k in
                                 ("company_quick", "company_standard", "company_deep"))
        assert (quick.max_fetches, standard.max_fetches, deep.max_fetches) == (8, 30, 70)
        assert (quick.max_pdfs, standard.max_pdfs, deep.max_pdfs) == (2, 6, 12)
        assert (quick.max_expansion_queries, standard.max_expansion_queries,
                deep.max_expansion_queries) == (0, 3, 6)
        assert (quick.max_wall_seconds, standard.max_wall_seconds) == (120, 360)

    def test_the_bytes_unit_is_listed_only_once_a_producer_instruments_it(self) -> None:
        from app.services.consumption import ConsumptionUnits

        plain = ConsumptionUnits(model_calls=1).to_dict()
        assert "bytes_downloaded" not in plain
        assert "bytes_downloaded" not in plain["not_instrumented"]
        measured = ConsumptionUnits(
            bytes_downloaded=0, instrumented=frozenset({"bytes_downloaded"})
        ).to_dict()
        assert measured["bytes_downloaded"] == 0
        back = ConsumptionUnits.from_dict(measured)
        assert back.measured("bytes_downloaded")
