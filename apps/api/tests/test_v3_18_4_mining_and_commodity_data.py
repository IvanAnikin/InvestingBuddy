"""V3.18.4/5 — a mining playbook, and the commodity numbers it needs.

A live report on the world's lowest-cost copper producer said nothing quantitative about
copper: Materials matched no playbook, and the platform's commodity sources were
reference-only links that fetched no number. These tests pin:

* commodities identified from the company's OWN documents (never a ticker table);
* per-commodity questions instantiated from them, replacing the generic base versions;
* the USGS and IMF numbers parsed EXACTLY from the real publications — including the
  superscript footnote markers a text extractor fuses into the digits;
* `get_industry_series` returning those numbers with citable ids and deterministic,
  input-cited price changes.

The PDFs and CSV under tests/fixtures are the real publications, fetched 2026-09-21.
"""

from __future__ import annotations

import asyncio
import pathlib
import uuid
from types import SimpleNamespace

import pytest
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.services.agent_tools import industry
from app.services.director.planner import plan_research
from app.services.macro import commodity_sources as sources
from app.services.macro.commodities import BY_SLUG, identify_commodities
from app.services.pipeline.v3_pipeline import _PlaybookAdapter
from app.services.playbooks import select

FIXTURES = pathlib.Path(__file__).parent / "fixtures"
_CFG = SimpleNamespace(
    v3_agent_tools_enabled=True,
    v3_deepseek_search_enabled=True,
    v3_filings_tool_enabled=True,
    v3_corpus_enabled=True,
    v3_commodity_sources_enabled=True,
)


@compiles(JSONB, "sqlite")
def _compile_jsonb_as_json_on_sqlite(element, compiler, **kw):  # noqa: ANN001
    return "JSON"


def _usgs(name: str, slug: str) -> sources.ParsedRelease:
    text = sources.pdf_text_without_superscripts((FIXTURES / name).read_bytes())
    return sources.parse_usgs_mcs(text, commodity=BY_SLUG[slug], year=2026, source_ref="u")


def _value(release: sources.ParsedRelease, key: str, period: str) -> float | None:
    return next(
        o.value
        for o in release.observations
        if o.series.series_key == key and o.period_key == period
    )


class TestTheSurveyIsReadExactly:
    def test_copper_world_and_country_figures(self) -> None:
        r = _usgs("usgs_mcs2026_copper.pdf", "copper")
        assert r.unit_note.startswith("thousand metric tons, copper content")
        assert _value(r, "copper.mine_production.world", "2025") == 23000.0
        assert _value(r, "copper.mine_production.peru", "2024") == 2740.0
        assert _value(r, "copper.mine_production.mexico", "2025") == 690.0
        assert _value(r, "copper.refinery_production.world", "2025") == 29000.0
        assert _value(r, "copper.reserves.world", "2026") == 980000.0
        assert not r.skipped

    def test_a_superscript_footnote_is_never_read_as_a_digit(self) -> None:
        """Extracted naively, Australia's copper reserves read "7100,000" (footnote 7)
        and Chile's lithium output "548,900" (footnote 5). Removed by font size."""
        copper = _usgs("usgs_mcs2026_copper.pdf", "copper")
        assert _value(copper, "copper.reserves.australia", "2026") == 100000.0
        lithium = _usgs("usgs_mcs2026_lithium.pdf", "lithium")
        assert _value(lithium, "lithium.mine_production.chile", "2024") == 48900.0
        assert _value(lithium, "lithium.mine_production.world", "2025") == 290000.0

    def test_withheld_is_not_zero_and_zero_is_zero(self) -> None:
        lithium = _usgs("usgs_mcs2026_lithium.pdf", "lithium")
        keys = {(o.series.series_key, o.period_key) for o in lithium.observations}
        assert ("lithium.mine_production.united_states", "2025") not in keys, (
            "'W' (withheld to avoid disclosing company data) must not be stored"
        )
        copper = _usgs("usgs_mcs2026_copper.pdf", "copper")
        assert _value(copper, "copper.mine_production.germany", "2025") == 0.0

    def test_the_surveys_price_statistics_carry_their_unit(self) -> None:
        copper = _usgs("usgs_mcs2026_copper.pdf", "copper")
        lme = [o for o in copper.observations
               if o.series.series_key == "copper.price.london_metal_exchange_grade_a_cash"]
        assert {o.period_key: o.value for o in lme}["2024"] == 414.7
        assert lme[0].series.unit == "cents per pound"
        assert [o.estimated for o in lme if o.period_key == "2025"] == [True]

    def test_the_reprint_without_the_filter_would_have_been_wrong(self) -> None:
        """Mutation: the plain text extractor fuses the markers into the numbers."""
        import pdfplumber

        with pdfplumber.open(str(FIXTURES / "usgs_mcs2026_copper.pdf")) as pdf:
            raw = "\n".join(page.extract_text() or "" for page in pdf.pages)
        assert "7100,000" in raw
        parsed = sources.parse_usgs_mcs(raw, commodity=BY_SLUG["copper"], year=2026,
                                        source_ref="u")
        australia = [o for o in parsed.observations
                     if o.series.series_key == "copper.reserves.australia"]
        assert not australia or australia[0].value != 100000.0


class TestPrices:
    def test_the_imf_csv_parses_to_monthly_observations(self) -> None:
        r = sources.parse_fred_csv(
            (FIXTURES / "fred_pcoppusdm.csv").read_text(),
            commodity=BY_SLUG["copper"],
            source_ref=sources.fred_url("PCOPPUSDM"),
        )
        assert len(r.observations) == 48
        assert r.observations[0].series.unit == "USD per metric ton"
        assert r.vintage_at is not None and r.vintage_at.year == 2026
        assert all(len(o.period_key) == 7 for o in r.observations)

    def test_a_blank_month_is_null_not_dropped(self) -> None:
        r = sources.parse_fred_csv(
            "observation_date,PCOPPUSDM\n2026-06-01,.\n2026-07-01,100\n",
            commodity=BY_SLUG["copper"],
            source_ref="f",
        )
        assert [(o.period_key, o.value) for o in r.observations] == [
            ("2026-06", None), ("2026-07", 100.0),
        ]


class TestCommoditiesComeFromTheCompanysOwnDocuments:
    def test_a_copper_producers_filing_names_copper_first(self) -> None:
        texts = ["Net sales of copper, molybdenum, silver and zinc. Copper cathodes."] * 10
        found = identify_commodities(texts)
        assert [m.commodity.slug for m in found][:2] == ["copper", "molybdenum"]

    def test_a_by_product_mentioned_once_is_not_researched(self) -> None:
        texts = ["Copper production rose. Copper cathodes."] * 20 + ["small gold credits"]
        assert "gold" not in {m.commodity.slug for m in identify_commodities(texts)}

    def test_words_that_merely_contain_a_commodity_do_not_count(self) -> None:
        assert identify_commodities(["Goldman Sachs led the offering; tinted glass"] * 20) == []


class TestTheMiningPlaybook:
    async def _plan(self, commodities):  # noqa: ANN001, ANN202
        playbooks = [_PlaybookAdapter(p) for p in select(sector=None, industry="Metals & Mining").playbooks]
        return await plan_research(
            subject="ANY:US", mode="deep", playbooks=playbooks, cfg=_CFG,
            commodities=[BY_SLUG[c] for c in commodities],
        )

    def test_it_applies_to_metals_and_mining(self) -> None:
        assert "mining_critical_materials" in select(sector=None, industry="Metals & Mining").versions

    async def test_market_questions_are_instantiated_per_commodity(self) -> None:
        plan = await self._plan(["copper", "molybdenum"])
        keys = {q.key for q in plan.questions}
        assert {"commodity_market__copper", "commodity_market__molybdenum",
                "demand_drivers__copper"} <= keys
        copper = next(q for q in plan.questions if q.key == "commodity_market__copper")
        assert copper.commodity == "copper" and "copper" in copper.text
        assert "{commodity}" not in copper.text

    async def test_the_same_playbook_researches_what_a_different_company_sells(self) -> None:
        """Generalisation: nothing in the playbook assumes copper."""
        plan = await self._plan(["rare_earths"])
        keys = {q.key for q in plan.questions}
        assert "commodity_market__rare_earths" in keys and "commodity_market__copper" not in keys

    async def test_sector_questions_replace_their_generic_base_versions(self) -> None:
        plan = await self._plan(["copper"])
        keys = {q.key for q in plan.questions}
        assert "industry_economics" not in keys and "operations_and_assets" not in keys
        assert "competitive_position" not in keys and "growth_pipeline" not in keys
        assert {"assets_and_production", "peer_comparison", "growth_projects"} <= keys

    async def test_with_no_commodity_identified_it_still_asks_once(self) -> None:
        plan = await self._plan([])
        market = [q for q in plan.questions if q.key.startswith("commodity_market")]
        assert [q.key for q in market] == ["commodity_market"]
        assert "principal products" in market[0].text

    async def test_a_dependency_points_at_the_same_commoditys_instance(self) -> None:
        plan = await self._plan(["copper"])
        order = [q.key for q in plan.questions]
        assert order.index("commodity_market__copper") < order.index("demand_drivers__copper")

    async def test_the_industry_question_is_owned_by_the_industry_analyst(self) -> None:
        plan = await self._plan(["copper"])
        owner = {k: t.role_id for t in plan.tasks for k in t.question_keys}
        assert owner["commodity_market__copper"] == "industry_analyst"
        assert owner["peer_comparison"] == "competitive_analyst"


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
        connect_args={"check_same_thread": False},
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


def _fetcher_for(pdf: bytes, csv: bytes):  # noqa: ANN202
    calls: list[str] = []

    async def fetch(url, *, allowed_domains, cfg=None, resolve_ip=False, **kw):  # noqa: ANN001, ANN202
        calls.append(url)
        body = pdf if url.endswith(".pdf") else csv
        return SimpleNamespace(ok=True, content=body, error=None, blocked=False)

    return fetch, calls


class TestTheTool:
    async def test_it_returns_cited_statistics_and_deterministic_changes(
        self, session, monkeypatch
    ) -> None:
        pdf = (FIXTURES / "usgs_mcs2026_copper.pdf").read_bytes()
        csv = (FIXTURES / "fred_pcoppusdm.csv").read_bytes()
        fetch, calls = _fetcher_for(pdf, csv)
        original_fred, original_usgs = sources.fetch_fred_prices, sources.fetch_usgs_summary
        monkeypatch.setattr(sources, "fetch_fred_prices",
                            lambda c, cfg: original_fred(c, cfg=cfg, fetcher=fetch))
        monkeypatch.setattr(sources, "fetch_usgs_summary",
                            lambda c, cfg, year: original_usgs(c, cfg=cfg, year=2026, fetcher=fetch))
        context = SimpleNamespace(session=session, cfg=_CFG, company_id=uuid.uuid4())
        payload = await industry._get_industry_series(
            context, industry.validate_get_industry_series({"commodity": "copper"})
        )
        items = payload["items"]
        assert payload["consumption"].url_fetch_calls == 2
        ids = [i["id"] for i in items]
        assert all(i.startswith(("mo:", "calc:")) for i in ids), "every figure is citable"
        latest = next(i for i in items if i.get("role") == "latest_price")
        change = next(i for i in items if i["id"].startswith("calc:series_change"))
        twelve = change["changes"]["12m"]
        assert twelve["to_value"] == latest["value"]
        expected = round((twelve["to_value"] / twelve["from_value"] - 1) * 100, 1)
        assert twelve["change_pct"] == expected
        assert set(twelve["input_ids"]) <= set(ids), "a change cites the readings it used"
        world = next(i for i in items if i.get("role") == "mine_production_world")
        assert world["value"] == 23000.0 and world["source_tier"] == "T3_industry_specialist"
        peru = next(i for i in items if i.get("country") == "peru"
                    and i["role"] == "mine_production_country")
        assert peru["share_of_world_pct"] == pytest.approx(11.7, abs=0.1)

        # A second call reads the store and fetches nothing.
        again = await industry._get_industry_series(
            context, industry.validate_get_industry_series({"commodity": "copper"})
        )
        assert again["consumption"].url_fetch_calls == 0

    async def test_a_publisher_failure_is_a_gap_never_a_zero(self, session, monkeypatch) -> None:
        async def failed(*_a, **_kw):  # noqa: ANN002, ANN003, ANN202
            return sources.ParsedRelease(dataset=sources.USGS_MCS_DATASET,
                                         skipped=["USGS 2026 edition unreachable"])

        monkeypatch.setattr(sources, "fetch_fred_prices", failed)
        monkeypatch.setattr(sources, "fetch_usgs_summary", failed)
        context = SimpleNamespace(session=session, cfg=_CFG, company_id=uuid.uuid4())
        payload = await industry._get_industry_series(
            context, industry.validate_get_industry_series({"commodity": "copper"})
        )
        assert payload["items"] == []
        assert any("nothing stored" in g for g in payload["gaps"])

    def test_it_is_registered_only_when_enabled(self) -> None:
        from app.services.agent_tools.builtin import register_builtins
        from app.services.agent_tools.registry import ToolRegistry

        off = SimpleNamespace(**{**vars(_CFG), "v3_commodity_sources_enabled": False})
        assert "get_industry_series" not in register_builtins(ToolRegistry(), cfg=off).names()
        assert "get_industry_series" in register_builtins(ToolRegistry(), cfg=_CFG).names()

    def test_an_unknown_commodity_is_refused(self) -> None:
        with pytest.raises(ValueError, match="commodity must be one of"):
            industry.validate_get_industry_series({"commodity": "unobtainium"})


def test_parsing_never_runs_on_the_event_loop() -> None:
    """A synchronous PDF parse on the loop once had gunicorn killing workers."""
    source = pathlib.Path(sources.__file__).read_text()
    assert "asyncio.to_thread(pdf_text_without_superscripts" in source
    assert asyncio is not None
