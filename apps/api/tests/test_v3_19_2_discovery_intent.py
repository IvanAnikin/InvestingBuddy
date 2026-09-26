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
