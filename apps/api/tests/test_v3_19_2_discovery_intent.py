"""V3.19.2 — the Discovery Intent: what the user ASKED FOR, never what a company IS.

Pins the structure of the two acceptance queries, the deterministic hard/soft rules, the
union geography semantics, and the closed vocabularies — every filter value must come from
a table in this repository.
"""

from __future__ import annotations

import pytest

from app.services.discovery.intent import (
    HARD,
    SCHEMA,
    SOFT,
    build_intent,
    hardness_for,
    intent_from_dict,
)
from app.services.exchange_registry import COUNTRY_TO_REGION
from app.services.macro.commodities import BY_SLUG

LUXURY = "small cap growing european luxury companies"
CRITICAL = (
    "Find smaller listed companies in Europe, North America and Australia developing "
    "gallium, germanium, antimony, tungsten, rare-earth or other strategic materials used "
    "in semiconductors, AI hardware, EVs or advanced manufacturing, preferably with "
    "permitting, government-funding, offtake or production catalysts within 2–5 years."
)


def _constraint(intent, key):
    return next((c for c in intent.constraints() if c.key == key), None)


def test_luxury_query_structure():
    intent = build_intent(LUXURY)
    assert intent.themes == ("luxury_goods",)
    assert intent.regions == ("Europe",)
    size = _constraint(intent, "size")
    assert size.requested == ("small_cap",) and size.hardness == HARD
    growth = _constraint(intent, "growth")
    assert growth.requested == ("growing",) and growth.hardness == HARD
    assert all(c.verification_required for c in intent.constraints())
    assert not intent.needs_narrowing


def test_critical_materials_query_structure():
    intent = build_intent(CRITICAL)
    assert "critical_materials" in intent.themes
    assert set(intent.materials) >= {"gallium", "germanium", "antimony", "tungsten", "rare_earths"}
    assert set(intent.regions) == {"Europe", "North America"}
    assert intent.countries == ("Australia",)
    # Semiconductors are where the materials GO — not the kind of company wanted.
    assert "semiconductors" not in intent.themes
    assert {"semiconductors", "ai_data_centres", "electric_vehicles"} <= set(intent.end_markets)
    assert set(intent.catalysts) == {"permitting", "government_funding", "offtake", "production"}
    assert intent.horizon_years == (2, 5)
    size = _constraint(intent, "size")
    assert size.hardness == SOFT
    assert set(size.requested) == {"micro_cap", "small_cap", "mid_cap"}


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("only small-cap luxury companies", HARD),
        ("strictly small cap miners", HARD),
        ("luxury companies, preferably small-cap", SOFT),
        ("ideally small cap luxury companies", SOFT),
        ("small cap luxury companies", HARD),
        ("smaller luxury companies", SOFT),
    ],
)
def test_size_hardness_rules(text, expected):
    assert _constraint(build_intent(text), "size").hardness == expected


def test_soft_cue_after_comma_does_not_leak_into_the_previous_clause():
    intent = build_intent("only small-cap Swiss watch companies, ideally growing")
    assert _constraint(intent, "size").hardness == HARD
    assert _constraint(intent, "growth").hardness == SOFT


def test_hardness_basis_is_recorded():
    hardness, basis = hardness_for("prefer small cap names", 7)
    assert hardness == SOFT and "prefer" in basis


def test_a_country_is_not_widened_to_its_region():
    intent = build_intent("Swiss watch companies")
    assert intent.countries == ("Switzerland",)
    assert intent.regions == ()


def test_geography_is_a_union_in_the_universe_filter():
    intent = build_intent(CRITICAL)
    assert set(intent.universe_filter()["regions"]) == {"Europe", "North America", "Oceania"}


def test_industry_named_as_company_kind_is_not_an_end_market():
    intent = build_intent("US semiconductor equipment companies")
    assert intent.themes == ("semiconductors",)
    assert intent.end_markets == ()


def test_growth_in_a_catalyst_phrase_is_not_a_growth_constraint():
    intent = build_intent("defence suppliers with growth catalysts")
    assert _constraint(intent, "growth") is None


def test_high_growth():
    intent = build_intent("high-growth European semiconductor companies")
    assert _constraint(intent, "growth").requested == ("high_growth",)


def test_every_value_comes_from_a_closed_vocabulary():
    for text in (LUXURY, CRITICAL, "only mid cap profitable German defence companies"):
        intent = build_intent(text)
        assert all(m in BY_SLUG for m in intent.materials)
        assert all(c in COUNTRY_TO_REGION for c in intent.countries)
        assert all(r in set(COUNTRY_TO_REGION.values()) for r in intent.regions)


def test_unmatched_terms_are_shown_not_dropped():
    intent = build_intent("European luxury companies with zanzibar exposure")
    assert "zanzibar" in intent.unmatched_terms


def test_roundtrip():
    intent = build_intent(CRITICAL)
    data = intent.to_dict()
    assert data["schema"] == SCHEMA
    again = intent_from_dict(data)
    assert again is not None
    assert again.to_dict() == data
    assert intent_from_dict({"themes": []}) is None  # a legacy parse is not an intent


def test_selector_bucket_is_a_hard_size_constraint():
    intent = build_intent("European luxury companies", market_cap_bucket="small_cap")
    size = _constraint(intent, "size")
    assert size.requested == ("small_cap",) and size.hardness == HARD


# ── wiring: the intent is persisted on a run and returned by the preview ──── #

from unittest.mock import AsyncMock, MagicMock  # noqa: E402

from app.schemas.market_discovery import ThesisDiscoveryRunCreate  # noqa: E402
from app.services import market_discovery_service as mds  # noqa: E402


def _mock_session():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


@pytest.mark.asyncio
async def test_thesis_run_persists_the_intent_beside_the_legacy_parse():
    run = await mds.create_pending_thesis_run(
        _mock_session(),
        ThesisDiscoveryRunCreate(thesis_text=LUXURY, provider_name="free_real"),
    )
    stored = run.parsed_thesis_json["discovery_intent"]
    assert stored["schema"] == SCHEMA
    keys = {c["key"]: c for c in stored["constraints"]}
    assert keys["size"]["requested"] == ["small_cap"]
    # Legacy readers still find their keys.
    assert run.parsed_thesis_json["themes"] == ["luxury_goods"]


@pytest.mark.asyncio
async def test_materials_thesis_builds_a_materials_universe_not_a_semiconductor_one():
    run = await mds.create_pending_thesis_run(
        _mock_session(),
        ThesisDiscoveryRunCreate(thesis_text=CRITICAL, provider_name="free_real"),
    )
    themes = {item["theme"] for item in run.universe_json["items"]}
    assert themes == {"mining_materials"}


@pytest.mark.asyncio
async def test_parse_thesis_endpoint_returns_the_intent(client):
    resp = await client.post(
        "/api/v1/market-discovery/parse-thesis", json={"thesis": LUXURY}
    )
    assert resp.status_code == 200
    intent = resp.json()["discovery_intent"]
    assert intent["schema"] == SCHEMA
    size = next(c for c in intent["constraints"] if c["key"] == "size")
    assert size["hardness"] == "hard" and size["hardness_basis"]


# ── review follow-ups: negation, selectors, pronouns, idioms, cue-based hardness ── #

from app.services.market_thesis_parser import parse_thesis  # noqa: E402
from app.services.market_universe_builder import build_universe  # noqa: E402


def _universe(text, **kw):
    intent = build_intent(text, **kw)
    parsed = parse_thesis(text, region=kw.get("region"), country=kw.get("country"))
    return intent, sorted(i["ticker"] for i in build_universe(
        {**parsed.to_dict(), **intent.universe_filter()}).items)


@pytest.mark.parametrize(
    ("text", "excluded"),
    [
        ("non-US defence companies", ("United States",)),
        ("defense companies outside the US", ("United States",)),
        ("luxury stocks, no US", ("United States",)),
        ("Europe excluding UK defence companies", ("United Kingdom",)),
        ("European defense companies other than British ones", ("United Kingdom",)),
        ("Asia ex-Japan semiconductor companies", ("Japan",)),
    ],
)
def test_negated_geography_is_an_exclusion_never_a_request(text, excluded):
    intent = build_intent(text)
    geo = _constraint(intent, "geography")
    assert geo.excluded == excluded
    assert not set(excluded) & set(geo.requested)


def test_non_us_defence_universe_drops_the_us_names():
    _, tickers = _universe("non-US defence companies")
    assert tickers and not {"LMT", "RTX", "NOC", "GD", "LHX"} & set(tickers)


@pytest.mark.parametrize(
    ("text", "requested", "excluded"),
    [
        ("not large cap european luxury companies", ("micro_cap", "small_cap", "mid_cap"),
         ("large_cap", "mega_cap")),
        ("luxury companies, no large caps", ("micro_cap", "small_cap", "mid_cap"),
         ("large_cap", "mega_cap")),
        ("excluding mega caps luxury", ("micro_cap", "small_cap", "mid_cap", "large_cap"),
         ("mega_cap",)),
    ],
)
def test_negated_size_excludes_bands(text, requested, excluded):
    size = _constraint(build_intent(text), "size")
    assert size.requested == requested and size.excluded == excluded


@pytest.mark.parametrize(
    ("text", "key"),
    [
        ("luxury companies not just small caps", "size"),
        ("luxury companies that are not growing", "growth"),
        ("not necessarily profitable luxury companies", "profitability"),
    ],
)
def test_negated_or_not_required_terms_make_no_constraint(text, key):
    intent = build_intent(text)
    assert _constraint(intent, key) is None
    assert intent.warnings  # and the reader is told why


def test_explicit_country_selector_is_never_widened():
    intent, tickers = _universe("European luxury watch companies", country="Switzerland")
    assert _constraint(intent, "geography").requested == ("Switzerland",)
    assert tickers == ["CFR", "UHR"]


def test_pronoun_us_is_not_the_united_states():
    assert _constraint(build_intent("help us find luxury companies"), "geography") is None
    assert _constraint(build_intent("US semiconductor companies"), "geography").requested == (
        "United States",
    )


def test_latin_american_is_not_the_united_states():
    geo = _constraint(build_intent("Latin American copper miners"), "geography")
    assert geo.requested == ("South America",)


def test_a_commodity_idiom_is_not_a_material():
    intent = build_intent("silver lining stocks")
    assert intent.materials == () and intent.needs_narrowing


def test_soft_cue_softens_geography():
    assert _constraint(build_intent("semiconductor stocks, preferably US"),
                       "geography").hardness == SOFT
    assert _constraint(build_intent("luxury companies, ideally European"),
                       "geography").hardness == SOFT


def test_material_as_input_does_not_widen_to_miners():
    intent, tickers = _universe("semiconductor companies using gallium processing")
    assert intent.materials_role == "input"
    assert "mining_materials" not in intent.themes
    assert not {"FCX", "SCCO", "MP"} & set(tickers)


def test_copper_for_semiconductors_is_a_materials_thesis():
    intent = build_intent("copper for semiconductors")
    assert intent.materials == ("copper",) and intent.materials_role == "product"
    assert "semiconductors" not in intent.themes


def test_growing_fast_is_high_growth():
    assert _constraint(build_intent("luxury companies growing fast"), "growth").requested == (
        "high_growth",
    )
