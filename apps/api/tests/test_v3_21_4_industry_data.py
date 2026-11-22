"""V3.21.4 (ADR-111) — live industry data: Ember, IRENA and EIA into the macro store.

The fixtures under tests/fixtures/industry_data are REAL responses fetched once on
2026-11-22 and trimmed (a few countries and years). No test touches the network: every
fetch is a fake that serves a fixture.

Pinned here:

* each parser reads the real shape exactly (values, units, periods), and refuses what it
  cannot read rather than storing a guess — a NULL the publisher printed stays NULL;
* the store write carries provenance (licence on the dataset, source ref, vintage,
  retrieval time) and is append-only: a re-run writes nothing, a revision is a new row;
* the EIA key is never in a stored ref, a note or a log line, and without a key the
  source makes no request at all;
* the report block selects series by country and electricity layer, says "absent" with a
  reason instead of a zero, and is ``None`` (report unchanged) with every source off;
* the weekly periodic task is registered with its own flag.
"""

from __future__ import annotations

import logging
import pathlib
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.pool import StaticPool

import app.models  # noqa: F401
from app.db.base import Base
from app.models.macro import MacroDataset, MacroObservation, MacroSeries
from app.services.industry_data import context as ctx
from app.services.industry_data import eia, ember, irena
from app.services.industry_data.common import (
    IndustryRelease,
    configured_countries,
    iso3_for,
    store_industry_release,
)
from app.services.macro.store import find_series, revision_history

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "industry_data"
LAST_MODIFIED = "Tue, 17 Nov 2026 18:34:40 GMT"
SECRET = "eia-test-key-0123456789"


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


def _text(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _cfg(**over: Any) -> SimpleNamespace:
    base: dict[str, Any] = {
        "v3_industry_data_ember_enabled": True,
        "v3_industry_data_irena_enabled": True,
        "v3_industry_data_eia_enabled": True,
        "v3_industry_data_refresh_enabled": True,
        "v3_industry_data_countries": "USA,DEU,JPN",
        "eia_api_key": "",
    }
    base.update(over)
    return SimpleNamespace(**base)


def _ok(body: bytes, **extra: Any) -> SimpleNamespace:
    return SimpleNamespace(ok=True, content=body, error=None, blocked=False, truncated=False,
                           status_code=200, **extra)


def _obs(release: IndustryRelease, key: str, period: str) -> Any:
    return next(o for o in release.observations
                if o.series.series_key == key and o.period_key == period)


@pytest.fixture
async def session():  # noqa: ANN201
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool,
                                 connect_args={"check_same_thread": False})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as s:
        yield s
    await engine.dispose()


# ── Ember ──────────────────────────────────────────────────────────────────── #


class TestEmberParser:
    def _release(self) -> IndustryRelease:
        return ember.parse_ember_csv(_text("ember_yearly_trimmed.csv"),
                                     countries=["DEU", "USA", "JPN"],
                                     last_modified=LAST_MODIFIED)

    def test_values_units_and_periods_are_read_exactly(self) -> None:
        r = self._release()
        solar = _obs(r, "DEU.generation_twh.solar", "2024")
        assert solar.value == 74.13 and solar.series.unit == "TWh"
        assert solar.series.geography == "DEU" and solar.series.frequency == "annual"
        assert solar.series.indicator_code == "Generation (TWh)|Solar"
        assert _obs(r, "DEU.share_pct.solar", "2024").value == 15.073
        assert _obs(r, "DEU.capacity_gw.solar", "2024").value == 91.2
        assert _obs(r, "DEU.capacity_gw.solar", "2024").series.unit == "GW"
        # A demand figure is the Generation cell of the Demand row — and it is demand.
        demand = _obs(r, "DEU.demand_twh", "2024")
        assert demand.value == 518.089 and "demand" in demand.series.display_name.lower()
        assert _obs(r, "USA.demand_twh", "2025").value is not None

    def test_only_wanted_countries_and_the_last_six_years_are_kept(self) -> None:
        r = self._release()
        isos = {o.series.geography for o in r.observations}
        assert isos == {"DEU", "USA", "JPN"}  # ASEAN (a region) and FRA (not asked) dropped
        deu_years = {o.period_key for o in r.observations if o.series.geography == "DEU"}
        assert deu_years == {"2020", "2021", "2022", "2023", "2024", "2025"}
        assert not any(".net_imports" in o.series.series_key for o in r.observations)
        assert not any(".clean" in o.series.series_key for o in r.observations)

    def test_a_blank_cell_is_null_never_zero_and_blank_capacity_is_not_a_series(self) -> None:
        r = self._release()
        # Germany's nuclear share in 2024 is a published 0.0 — a real zero, kept.
        assert _obs(r, "DEU.share_pct.nuclear", "2024").value == 0.0
        # Net imports and Demand have no capacity: no capacity series exists for them.
        assert not any(o.series.series_key.endswith("capacity_gw.demand")
                       for o in r.observations)

    def test_the_vintage_is_the_files_last_modified(self) -> None:
        r = self._release()
        assert r.vintage_at == datetime(2026, 11, 17, 18, 34, 40, tzinfo=timezone.utc)
        assert r.vintage_basis == "publisher_last_modified"
        assert r.dataset.spec.licence == "CC BY 4.0" and "Ember" in r.dataset.attribution

    def test_without_last_modified_the_vintage_is_period_derived_and_said(self) -> None:
        r = ember.parse_ember_csv(_text("ember_yearly_trimmed.csv"), countries=["DEU"],
                                  now=datetime(2026, 11, 22, tzinfo=timezone.utc))
        assert r.vintage_at == datetime(2026, 1, 1, tzinfo=timezone.utc)
        assert r.vintage_basis == "period_derived"

    def test_a_changed_header_stores_nothing_and_says_why(self) -> None:
        body = _text("ember_yearly_trimmed.csv").replace("Capacity (GW)", "Capacity (MW)", 1)
        r = ember.parse_ember_csv(body, countries=["DEU"])
        assert r.observations == [] and "Capacity (GW)" in r.skipped[0]

    def test_a_country_not_in_the_file_is_named(self) -> None:
        r = ember.parse_ember_csv(_text("ember_yearly_trimmed.csv"), countries=["DEU", "ZZZ"])
        assert "ZZZ: not in the Ember file" in r.skipped


class TestEmberFetch:
    async def test_bounded_pinned_and_refuses_a_truncated_file(self) -> None:
        calls: list[dict[str, Any]] = []

        async def fetch(url: str, **kw: Any) -> Any:
            calls.append({"url": url, **kw})
            return SimpleNamespace(ok=True, content=b"Area,ISO", truncated=True, error=None)

        r = await ember.fetch_ember(cfg=_cfg(), countries=["DEU"], fetcher=fetch)
        assert r.observations == [] and "refused" in r.skipped[0]
        assert calls[0]["allowed_domains"] == ("files.ember-energy.org",)
        assert calls[0]["max_bytes"] == ember.MAX_BYTES and calls[0]["resolve_ip"] is True

    async def test_it_parses_what_the_fetcher_returns_with_the_header_vintage(self) -> None:
        async def fetch(url: str, **kw: Any) -> Any:
            return _ok((FIXTURES / "ember_yearly_trimmed.csv").read_bytes(),
                       last_modified=LAST_MODIFIED)

        r = await ember.fetch_ember(cfg=_cfg(), countries=["DEU"], fetcher=fetch)
        assert r.observations and r.vintage_basis == "publisher_last_modified"
        assert r.fetch_attempts == 1

    async def test_flag_off_is_inert_and_makes_no_request(self) -> None:
        async def fetch(url: str, **kw: Any) -> Any:  # pragma: no cover - must not run
            raise AssertionError("fetched with the flag off")

        r = await ember.fetch_ember(cfg=_cfg(v3_industry_data_ember_enabled=False),
                                    countries=["DEU"], fetcher=fetch)
        assert r.inert_reason and r.fetch_attempts == 0


# ── IRENA ──────────────────────────────────────────────────────────────────── #


class TestIrena:
    def test_the_newest_country_table_is_found_with_its_publication_time(self) -> None:
        table_id, updated, notes = irena.parse_table_listing(_text("irena_table_listing.json"))
        assert table_id == "Country_ELECSTAT_2026_H2_PX.px" and notes == []
        assert updated == datetime(2026, 9, 28, 17, 56, 28, tzinfo=timezone.utc)

    def test_a_table_id_from_the_response_cannot_choose_another_path(self) -> None:
        assert irena.table_url("../../admin.px") is None
        assert irena.table_url("Country_ELECSTAT_2026_H2_PX.px?x=1") is None
        assert irena.table_url("Country_ELECSTAT_2026_H2_PX.px").startswith(
            "https://pxweb.irena.org/api/v1/en/IRENASTAT/")

    def test_the_query_is_built_from_the_metadata_codes_and_is_bounded(self) -> None:
        meta = irena.parse_metadata(_text("irena_table_metadata.json"))
        query, notes = irena.build_query(meta, countries=["DEU", "JPN", "USA", "ZZZ"])
        assert query is not None and "ZZZ: not in the IRENA table" in notes
        sel = {q["code"]: q["selection"]["values"] for q in query["query"]}
        assert sel["Country/area"] == ["DEU", "JPN", "USA"]
        assert set(sel["Technology"]) == {"1", "4", "5", "21", "3", "13"}
        assert sel["Data Type"] == ["0", "1"] and sel["Grid connection"] == ["0"]
        assert sel["Year"] == ["20", "21", "22", "23", "24", "25"]  # 2020-2025
        assert query["response"] == {"format": "json-stat2"}

    def test_json_stat_values_are_read_exactly_and_dots_are_null(self) -> None:
        obs, notes = irena.parse_jsonstat(_text("irena_query_response.json"), source_ref="r")
        assert notes == []
        by = {(o.series.series_key, o.period_key): o for o in obs}
        assert by[("DEU.generation_gwh.total_renewable", "2022")].value == 252267
        assert by[("DEU.generation_gwh.total_renewable", "2025")].value is None  # ".."
        cap = by[("DEU.capacity_mw.total_renewable", "2025")]
        assert cap.value == 200901.5 and cap.series.unit == "MW"
        assert by[("USA.capacity_mw.pumped_hydro_storage", "2024")].series.geography == "USA"
        assert by[("JPN.capacity_mw.solar_pv", "2025")].value is not None

    def test_a_value_count_that_does_not_match_is_refused(self) -> None:
        body = _text("irena_query_response.json").replace('"value":[252267,', '"value":[', 1)
        obs, notes = irena.parse_jsonstat(body, source_ref="r")
        assert obs == [] and "does not match" in notes[0]

    async def test_listing_metadata_and_one_post_then_a_dated_release(self) -> None:
        gets: list[str] = []
        posts: list[tuple[str, dict[str, Any]]] = []

        async def get(url: str, **kw: Any) -> Any:
            gets.append(url)
            assert kw["allowed_domains"] == ("pxweb.irena.org",)
            name = ("irena_table_listing.json" if url == irena.FOLDER_URL
                    else "irena_table_metadata.json")
            return _ok((FIXTURES / name).read_bytes())

        async def post(url: str, payload: dict[str, Any], **kw: Any) -> Any:
            posts.append((url, payload))
            assert kw["allowed_domains"] == ("pxweb.irena.org",)
            return _ok((FIXTURES / "irena_query_response.json").read_bytes())

        r = await irena.fetch_irena(cfg=_cfg(), countries=["DEU", "JPN", "USA"],
                                    fetcher=get, poster=post)
        assert len(gets) == 2 and len(posts) == 1 and r.fetch_attempts == 3
        assert r.vintage_basis == "publisher_updated" and r.observations
        assert r.vintage_at == datetime(2026, 9, 28, 17, 56, 28, tzinfo=timezone.utc)


# ── EIA ────────────────────────────────────────────────────────────────────── #


class TestEia:
    def test_the_three_routes_parse_with_the_publishers_units(self) -> None:
        cap, _ = eia.parse_eia_response(_text("eia_cap.json"), route=eia.ROUTES[2],
                                        source_ref="r")
        bat = next(o for o in cap if o.series.series_key == "USA.capacity_mw.bat"
                   and o.period_key == "2025")
        assert bat.value == 43333.3 and bat.series.unit == "megawatts"
        sales, _ = eia.parse_eia_response(_text("eia_sales.json"), route=eia.ROUTES[0],
                                          source_ref="r")
        by = {(o.series.series_key, o.period_key): o for o in sales}
        assert by[("USA.retail_sales.all_sectors", "2025")].value == 4079909.35378
        assert by[("USA.retail_sales.all_sectors", "2025")].series.unit == (
            "million kilowatt hours")
        assert by[("USA.retail_price.all_sectors", "2025")].value == 13.56
        gen, _ = eia.parse_eia_response(_text("eia_gen.json"), route=eia.ROUTES[1],
                                        source_ref="r")
        assert {o.series.series_key for o in gen} >= {"USA.net_generation.all",
                                                       "USA.net_generation.sun"}

    async def test_without_a_key_it_is_inert_and_makes_no_request(self) -> None:
        async def fetch(url: str, **kw: Any) -> Any:  # pragma: no cover - must not run
            raise AssertionError("EIA fetched without a key")

        r = await eia.fetch_eia(cfg=_cfg(eia_api_key=""), fetcher=fetch)
        assert r.fetch_attempts == 0 and r.observations == []
        assert "EIA_API_KEY" in (r.inert_reason or "")

    def test_no_demo_key_is_a_default(self) -> None:
        from app.core.config import CREDENTIAL_SETTING_FIELDS, Settings

        assert Settings.model_fields["eia_api_key"].default == ""
        assert Settings.model_fields["eia_api_key"].repr is False
        assert "eia_api_key" in CREDENTIAL_SETTING_FIELDS
        assert "DEMO_KEY" not in pathlib.Path(eia.__file__).read_text()

    async def test_the_key_is_in_the_request_only(self, caplog: pytest.LogCaptureFixture
                                                  ) -> None:
        requested: list[str] = []
        bodies = {"retail-sales": "eia_sales.json",
                  "electric-power-operational-data": "eia_gen.json",
                  "capability": "eia_cap.json"}

        async def fetch(url: str, **kw: Any) -> Any:
            requested.append(url)
            assert kw["allowed_domains"] == ("api.eia.gov",)
            if "capability" in url:
                # An exception whose message carries the URL — it must not be repeated.
                raise RuntimeError(f"boom while fetching {url}")
            name = next(v for k, v in bodies.items() if k in url)
            return _ok((FIXTURES / name).read_bytes())

        caplog.set_level(logging.DEBUG)
        r = await eia.fetch_eia(cfg=_cfg(eia_api_key=SECRET), fetcher=fetch,
                                now=datetime(2026, 11, 22, 9, tzinfo=timezone.utc))
        assert all(SECRET in u for u in requested) and len(requested) == 3
        assert r.observations
        assert all(SECRET not in o.source_ref for o in r.observations)
        assert all("api_key" not in o.source_ref for o in r.observations)
        assert all(SECRET not in s for s in r.skipped)
        assert any("request failed (RuntimeError)" in s for s in r.skipped)
        assert SECRET not in caplog.text
        assert r.vintage_basis == "first_seen"
        assert r.vintage_at == datetime(2026, 11, 22, tzinfo=timezone.utc)

    async def test_the_refresh_log_carries_no_key(self, session: Any,
                                                  caplog: pytest.LogCaptureFixture) -> None:
        from app.services.industry_data.refresh import refresh_industry_data

        async def fetch(url: str, **kw: Any) -> Any:
            return SimpleNamespace(ok=False, content=None, error=f"http 403 for {url}",
                                   status_code=403, truncated=False)

        caplog.set_level(logging.DEBUG)
        outcomes = await refresh_industry_data(
            lambda: _NoCommit(session), cfg=_cfg(eia_api_key=SECRET), sources=["eia_api_v2"],
            fetchers={"eia_api_v2": {"fetcher": fetch}})
        assert SECRET not in caplog.text
        assert SECRET not in repr([o.to_dict() for o in outcomes])
        assert any("http 403" in s for s in outcomes[0].skipped)


class _NoCommit:
    """A session-factory result that is the test's own session, as a context manager."""

    def __init__(self, session: Any) -> None:
        self.session = session

    async def __aenter__(self) -> Any:
        return self.session

    async def __aexit__(self, *exc: Any) -> None:
        return None


# ── the store ──────────────────────────────────────────────────────────────── #


async def _store_ember(session: Any, *, last_modified: str = LAST_MODIFIED,
                       body: str | None = None, countries: tuple[str, ...] = ("DEU",)) -> int:
    release = ember.parse_ember_csv(body or _text("ember_yearly_trimmed.csv"),
                                    countries=list(countries), last_modified=last_modified)
    return await store_industry_release(session, release)


class TestTheStore:
    async def test_provenance_is_stored_with_every_reading(self, session: Any) -> None:
        assert await _store_ember(session) > 0
        dataset = (await session.execute(select(MacroDataset).where(
            MacroDataset.dataset_key == "ember:yearly_electricity"))).scalar_one()
        assert dataset.licence == "CC BY 4.0" and dataset.source_id == "ember_electricity"
        series = await find_series(session, dataset_key="ember:yearly_electricity",
                                   series_key="DEU.demand_twh")
        assert series is not None and series.unit == "TWh" and series.geography == "DEU"
        row = (await session.execute(select(MacroObservation).where(
            MacroObservation.series_id == series.id,
            MacroObservation.period_key == "2024"))).scalar_one()
        assert float(row.value) == 518.089 and row.source_ref == ember.EMBER_URL
        assert row.vintage_at is not None and row.retrieved_at is not None

    async def test_a_rerun_writes_nothing_and_a_republish_with_same_values_neither(
        self, session: Any
    ) -> None:
        first = await _store_ember(session)
        assert await _store_ember(session) == 0
        assert await _store_ember(session, last_modified="Mon, 30 Nov 2026 10:00:00 GMT") == 0
        total = len((await session.execute(select(MacroObservation))).scalars().all())
        assert total == first

    async def test_a_revision_is_a_new_row_and_the_old_one_is_kept(self, session: Any
                                                                   ) -> None:
        await _store_ember(session)
        revised = _text("ember_yearly_trimmed.csv").replace(
            "Germany,DEU,2024,Country or economy,Demand,True,518.089",
            "Germany,DEU,2024,Country or economy,Demand,True,519.5")
        assert await _store_ember(session, body=revised,
                                  last_modified="Mon, 30 Nov 2026 10:00:00 GMT") == 1
        series = await find_series(session, dataset_key="ember:yearly_electricity",
                                   series_key="DEU.demand_twh")
        history = await revision_history(session, series, period_key="2024")
        assert [h.value for h in history] == [519.5, 518.089]
        assert [h.is_current for h in history] == [True, False]

    async def test_nothing_is_written_without_a_vintage(self, session: Any) -> None:
        release = ember.parse_ember_csv(_text("ember_yearly_trimmed.csv"), countries=["DEU"])
        release.vintage_at = None
        assert await store_industry_release(session, release) == 0


# ── the report block ───────────────────────────────────────────────────────── #


def _company(exchange: str = "XETRA", country: str | None = "Germany") -> Any:
    return SimpleNamespace(exchange=exchange, country=country, ticker="X")


def _layers(layer: str | None) -> dict[str, Any] | None:
    if layer is None:
        return None
    return {"status": "classified", "primary_layer": layer}


async def _seed_all(session: Any) -> None:
    await _store_ember(session, countries=("DEU", "USA"))
    obs, _ = irena.parse_jsonstat(_text("irena_query_response.json"), source_ref="irena-ref")
    await store_industry_release(session, IndustryRelease(
        source_id="irena_irenastat", dataset=irena.IRENA_DATASET, observations=obs,
        vintage_at=datetime(2026, 9, 28, tzinfo=timezone.utc)))
    cap, _ = eia.parse_eia_response(_text("eia_cap.json"), route=eia.ROUTES[2],
                                    source_ref="eia-ref")
    sales, _ = eia.parse_eia_response(_text("eia_sales.json"), route=eia.ROUTES[0],
                                      source_ref="eia-ref")
    await store_industry_release(session, IndustryRelease(
        source_id="eia_api_v2", dataset=eia.EIA_DATASET, observations=cap + sales,
        vintage_at=datetime(2026, 11, 22, tzinfo=timezone.utc)))
    await session.flush()


class TestSelection:
    def test_layer_to_focus(self) -> None:
        assert ctx.focus_for(_layers("storage")) == ("storage", ctx.FOCUS_STORAGE)
        assert ctx.focus_for(_layers("generation_fuel"))[1] == ctx.FOCUS_GENERATION_MIX
        for layer in ("transmission", "distribution_grid_edge", "utilities_grid_services"):
            assert ctx.focus_for(_layers(layer))[1] == ctx.FOCUS_DEMAND_GROWTH
        assert ctx.focus_for(_layers("power_electronics"))[1] == ctx.FOCUS_DEMAND
        assert ctx.focus_for(None) == (None, ctx.FOCUS_DEMAND)
        assert ctx.focus_for({"status": "not_electricity", "primary_layer": None})[1] == (
            ctx.FOCUS_DEMAND)

    def test_countries_listing_first_then_domicile_and_unknown_is_said(self) -> None:
        countries, notes = ctx.countries_for(_company("XETRA", "US"))
        assert [c["iso3"] for c in countries] == ["DEU", "USA"]
        assert [c["basis"] for c in countries] == ["listing venue", "stated domicile"]
        countries, notes = ctx.countries_for(_company("NOWHERE", "Atlantis"))
        assert countries == [] and "Atlantis" in notes[0]
        assert iso3_for("United States") == iso3_for("US") == iso3_for("usa") == "USA"

    def test_configured_countries_drop_what_is_not_an_iso3_code(self) -> None:
        assert configured_countries(_cfg(v3_industry_data_countries="usa, DE ,JPN,JPN")) == [
            "USA", "JPN"]


class TestTheBlock:
    async def test_transmission_in_germany_shows_demand_with_growth_and_provenance(
        self, session: Any
    ) -> None:
        await _seed_all(session)
        block = await ctx.industry_context_block(session, _company(), cfg=_cfg(),
                                                 layers_block=_layers("transmission"))
        assert block is not None and block["state"] == "available"
        assert block["focus"] == ctx.FOCUS_DEMAND_GROWTH
        demand = next(f for f in block["figures"] if f["series_id"] == "DEU.demand_twh")
        assert demand["period"] == "2025" and demand["unit"] == "TWh"
        for key in ("source_id", "source_name", "series_id", "period", "value", "unit",
                    "retrieved_at", "vintage_at", "licence", "attribution", "source_ref",
                    "observation_id"):
            assert demand[key] not in (None, ""), key
        assert demand["licence"] == "CC BY 4.0" and demand["observation_id"].startswith("mo:")
        change = next(c for c in block["changes"]
                      if c["series_id"] == "DEU.demand_twh" and c["years"] == 1)
        assert change["from_period"] == "2024" and change["to_period"] == "2025"
        assert len(change["input_ids"]) == 2 and "computed by InvestingBuddy" in (
            change["definition"])

    async def test_storage_outside_the_us_says_battery_capacity_is_not_published(
        self, session: Any
    ) -> None:
        await _seed_all(session)
        block = await ctx.industry_context_block(session, _company(), cfg=_cfg(),
                                                 layers_block=_layers("storage"))
        assert block is not None and block["focus"] == ctx.FOCUS_STORAGE
        assert any(m["reason"] == ctx.NO_BATTERY_REASON for m in block["missing"])
        ids = {f["series_id"] for f in block["figures"]}
        assert "DEU.capacity_gw.wind_and_solar" in ids
        assert "USA.capacity_mw.bat" not in ids  # Germany only: no US series

    async def test_storage_in_the_us_shows_eia_batteries_only_with_a_key(self, session: Any
                                                                       ) -> None:
        await _seed_all(session)
        us = _company("US", "United States")
        keyed = await ctx.industry_context_block(session, us, cfg=_cfg(eia_api_key=SECRET),
                                                 layers_block=_layers("storage"))
        assert keyed is not None
        bat = next(f for f in keyed["figures"] if f["series_id"] == "USA.capacity_mw.bat")
        assert bat["value"] == 43333.3 and bat["unit"] == "megawatts"
        assert any(c["series_id"] == "USA.capacity_mw.bat" for c in keyed["changes"])
        unkeyed = await ctx.industry_context_block(session, us, cfg=_cfg(),
                                                   layers_block=_layers("storage"))
        assert unkeyed is not None
        assert not any(f["source_id"] == "eia_api_v2" for f in unkeyed["figures"])
        assert any("EIA_API_KEY" in m["reason"] for m in unkeyed["missing"])
        eia_src = next(s for s in unkeyed["sources"] if s["source_id"] == "eia_api_v2")
        assert eia_src["inert_reason"]

    async def test_generation_shows_the_mix_and_irena_capacity(self, session: Any) -> None:
        await _seed_all(session)
        block = await ctx.industry_context_block(session, _company(), cfg=_cfg(),
                                                 layers_block=_layers("generation_fuel"))
        assert block is not None
        ids = {f["series_id"] for f in block["figures"]}
        assert {"DEU.share_pct.solar", "DEU.share_pct.coal",
                "DEU.generation_twh.total_generation"} <= ids
        solar_pv = next(f for f in block["figures"] if f["series_id"] ==
                        "DEU.capacity_mw.solar_pv")
        assert solar_pv["source_id"] == "irena_irenastat" and "IRENA" in solar_pv["attribution"]

    async def test_no_data_is_an_explicit_state_and_never_a_zero(self, session: Any) -> None:
        block = await ctx.industry_context_block(session, _company(), cfg=_cfg(),
                                                 layers_block=_layers("transmission"))
        assert block is not None and block["state"] == "no_data"
        assert block["figures"] == [] and block["changes"] == []
        assert block["missing"] and all(m["reason"] for m in block["missing"])

    async def test_a_country_outside_the_refresh_list_says_so(self, session: Any) -> None:
        await _seed_all(session)
        # Japan is listed, not in the refresh list, and Ember holds nothing for it.
        block = await ctx.industry_context_block(
            session, _company("TSE", None), cfg=_cfg(v3_industry_data_countries="USA"),
            layers_block=None)
        assert block is not None and block["state"] == "no_data"
        assert [c["iso3"] for c in block["countries"]] == ["JPN"]
        assert any("V3_INDUSTRY_DATA_COUNTRIES" in m["reason"] for m in block["missing"])

    async def test_no_country_is_an_explicit_state(self, session: Any) -> None:
        block = await ctx.industry_context_block(session, _company("NOWHERE", None),
                                                 cfg=_cfg(), layers_block=None)
        assert block is not None and block["state"] == "no_country"

    async def test_every_source_off_means_no_block(self, session: Any) -> None:
        await _seed_all(session)
        off = _cfg(v3_industry_data_ember_enabled=False, v3_industry_data_irena_enabled=False,
                   v3_industry_data_eia_enabled=False)
        assert await ctx.industry_context_block(session, _company(), cfg=off,
                                                layers_block=None) is None

    async def test_one_source_off_is_said_not_hidden(self, session: Any) -> None:
        await _seed_all(session)
        block = await ctx.industry_context_block(
            session, _company(), cfg=_cfg(v3_industry_data_ember_enabled=False),
            layers_block=None)
        assert block is not None and block["figures"] == []
        assert any("switched off" in m["reason"] for m in block["missing"])

    async def test_the_block_passes_the_final_safety_scan(self, session: Any) -> None:
        from app.services import safety_terms

        await _seed_all(session)
        for layer in ("storage", "generation_fuel", "transmission", None):
            block = await ctx.industry_context_block(
                session, _company("US", "Germany"), cfg=_cfg(eia_api_key=SECRET),
                layers_block=_layers(layer))
            assert safety_terms.scan_value(block, path="industry_context") == []
            assert SECRET not in repr(block)


class TestTheReport:
    async def test_the_block_lands_in_industry_and_market_and_is_absent_when_off(
        self, session: Any
    ) -> None:
        from app.models.company import Company
        from app.services.ledger import store as ledger
        from app.services.pipeline import v3_pipeline as v3

        company = Company(ticker="SIE", exchange="XETRA", name="Siemens Energy AG",
                          country="Germany")
        session.add(company)
        await session.flush()
        run = await ledger.open_run(session, mode="standard", company_id=company.id)
        args = dict(company=company, thesis=None, thesis_size=None, table_payloads={},
                    council_convened=False, editor_client=None)
        plan = SimpleNamespace(questions=[])
        loop = SimpleNamespace(evidence_by_question={})

        off_cfg = _cfg(v3_industry_data_ember_enabled=False,
                       v3_industry_data_irena_enabled=False,
                       v3_industry_data_eia_enabled=False)
        block_off = await v3._industry_context_section(session, company, cfg=off_cfg,
                                                       layers_block=None)
        assert block_off is None
        report, _ = await v3._professional_report(session, run, plan, loop, **args,
                                                  industry_context=block_off)
        market = next(s for s in report["sections"] if s["key"] == "industry_and_market")
        assert "industry_context" not in market

        await _seed_all(session)
        block = await v3._industry_context_section(session, company, cfg=_cfg(),
                                                   layers_block=_layers("transmission"))
        report, withheld = await v3._professional_report(session, run, plan, loop, **args,
                                                         industry_context=block)
        assert withheld is None and report is not None
        market = next(s for s in report["sections"] if s["key"] == "industry_and_market")
        assert market["industry_context"]["state"] == "available"


# ── the weekly task ────────────────────────────────────────────────────────── #


class TestTheWeeklyTask:
    def test_registered_weekly_with_its_own_flag_and_a_handler(self) -> None:
        import app.services.jobs.handlers  # noqa: F401
        from app.services.industry_data import refresh
        from app.services.jobs import worker as worker_mod
        from app.services.scheduling import periodic

        periodic.register_periodic_task(refresh.INDUSTRY_DATA_REFRESH_TASK)
        task = {t.key: t for t in periodic.registered_tasks()}["industry_data_refresh"]
        assert task.interval == periodic.WEEKLY
        assert task.job_type == "industry_data_refresh"
        assert task.flag_attr == "v3_industry_data_refresh_enabled"
        assert "industry_data_refresh" in worker_mod.registry
        cfg = SimpleNamespace(v3_periodic_tasks_enabled=True,
                              v3_industry_data_refresh_enabled=False)
        assert periodic.task_enabled(task, cfg) is False

    def test_the_handler_module_is_imported_by_the_worker(self) -> None:
        text = pathlib.Path("app/services/jobs/handlers.py").read_text()
        assert "app.services.industry_data import refresh" in text

    async def test_the_refresh_stores_each_source_and_one_failure_costs_only_it(
        self, session: Any
    ) -> None:
        from app.services.industry_data.refresh import refresh_industry_data

        async def ember_fetch(url: str, **kw: Any) -> Any:
            return _ok((FIXTURES / "ember_yearly_trimmed.csv").read_bytes(),
                       last_modified=LAST_MODIFIED)

        async def irena_get(url: str, **kw: Any) -> Any:
            raise ConnectionError("down")

        outcomes = await refresh_industry_data(
            lambda: _NoCommit(session), cfg=_cfg(), countries=["DEU"],
            fetchers={"ember_electricity": {"fetcher": ember_fetch},
                      "irena_irenastat": {"fetcher": irena_get}})
        by = {o.source_id: o for o in outcomes}
        assert by["ember_electricity"].written > 0
        assert by["irena_irenastat"].parsed == 0 and by["irena_irenastat"].skipped
        assert by["eia_api_v2"].inert_reason  # no key
        held = (await session.execute(select(MacroSeries))).scalars().all()
        assert held and all(s.geography == "DEU" for s in held)


# ── the registry ───────────────────────────────────────────────────────────── #


class TestTheRegistry:
    def test_three_live_sources_follow_their_flags(self) -> None:
        from app.core.config import Settings
        from app.services.sources.registry import build_registry
        from app.services.sources.taxonomy import SourceStatus

        on = build_registry(Settings())
        for sid in ("ember_electricity", "irena_irenastat", "eia_api_v2"):
            src = on.get(sid)
            assert src is not None and src.status == SourceStatus.enabled, sid
        off = build_registry(Settings(v3_industry_data_ember_enabled=False,
                                      v3_industry_data_irena_enabled=False,
                                      v3_industry_data_eia_enabled=False))
        for sid in ("ember_electricity", "irena_irenastat", "eia_api_v2"):
            src = off.get(sid)
            assert src is not None and src.status == SourceStatus.disabled, sid
            assert src.enabled is False


# ── the guarded fetcher keeps the publisher's Last-Modified ─────────────────── #


def test_the_document_fetcher_records_last_modified(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from app.services.sources.document_fetcher import safe_fetch_document
    from tests.test_phase29b2_document_extraction import _FakeStream, _patch_httpx

    _patch_httpx(monkeypatch, [_FakeStream(
        headers={"content-type": "text/csv;charset=utf-8", "last-modified": LAST_MODIFIED},
        body=b"Area,ISO 3 code\n")])
    result = asyncio.run(safe_fetch_document(
        ember.EMBER_URL, allowed_domains=(ember.EMBER_HOST,),
        extra_text_content_types=("text/csv",)))
    assert result.ok and result.last_modified == LAST_MODIFIED
    assert ember.vintage_from_last_modified(result.last_modified) == datetime(
        2026, 11, 17, 18, 34, 40, tzinfo=timezone.utc)
