"""
Phase 27.1C — prompt-derived autofill + controlled selector values.

All tests run OFFLINE and deterministically: the parser, universe builder and
filter tables are pure (no LLM, no network, no DB writes). Endpoint tests use the
in-process ASGI client with a mocked DB session.

Coverage:
  - Parser detects canonical single-value Region / Country / Sector / Industry
    from the prompt text ("European watch producers", "Swiss watch companies",
    "Danish jewelry companies", "US semiconductor equipment companies").
  - Explicit form values override the parsed prompt values (precedence).
  - A conflict warning is emitted when an explicit value contradicts the prompt.
  - Invalid Region / Country / Sector values are rejected by service validation.
  - GET /supported-filters returns canonical region/country/sector options.
  - Strict country filtering still holds after autofill (no region broadening).
  - POST /parse-thesis previews detections and does NOT create a run.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from app.schemas.market_discovery import ThesisDiscoveryRunCreate
from app.services import market_discovery_service as mds
from app.services.discovery_filters import (
    canonical_country,
    canonical_region,
    get_supported_filters,
    is_supported_sector,
)
from app.services.market_thesis_parser import parse_thesis
from app.services.market_universe_builder import build_universe


def _mock_session():
    added: list = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda o: added.append(o))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db, added


def _payload(**over) -> ThesisDiscoveryRunCreate:
    base = {
        "thesis_text": "European watch producers",
        "max_universe_size": 25,
        "max_candidates": 10,
        "provider_name": "free_real",
        "lookback_days": 90,
    }
    base.update(over)
    return ThesisDiscoveryRunCreate(**base)


# ===========================================================================
# 1. Parser — canonical single-value detections
# ===========================================================================


def test_01_detects_europe_from_european_watch_producers() -> None:
    parsed = parse_thesis("European watch producers")
    assert parsed.region == "Europe"
    assert parsed.sector == "Consumer Discretionary"
    assert parsed.country is None
    assert "luxury_goods" in parsed.themes
    assert parsed.extraction_source == "prompt_text"


def test_02_detects_switzerland_from_swiss_watch_companies() -> None:
    parsed = parse_thesis("Swiss watch companies")
    assert parsed.country == "Switzerland"
    assert parsed.region == "Europe"
    assert parsed.sector == "Consumer Discretionary"


def test_03_detects_denmark_from_danish_jewelry_companies() -> None:
    parsed = parse_thesis("Danish jewelry companies")
    assert parsed.country == "Denmark"
    assert parsed.region == "Europe"
    assert parsed.sector == "Consumer Discretionary"


def test_04_detects_united_states_from_us_semiconductor_equipment() -> None:
    parsed = parse_thesis("US semiconductor equipment companies")
    assert parsed.country == "United States"
    assert parsed.region == "North America"
    assert parsed.sector == "Technology"


# ===========================================================================
# 2. Precedence + conflict warnings
# ===========================================================================


def test_05_explicit_country_overrides_parsed_country() -> None:
    # Prompt says Switzerland; explicit form country is Denmark -> Denmark wins.
    parsed = parse_thesis("Swiss watch companies", country="Denmark")
    assert parsed.country == "Denmark"


def test_06_conflict_warning_emitted_on_country_mismatch() -> None:
    parsed = parse_thesis("Swiss watch companies", country="Denmark")
    assert any(
        "Switzerland" in w and "Denmark" in w for w in parsed.warnings
    ), parsed.warnings


def test_07_no_conflict_warning_when_explicit_matches_prompt() -> None:
    parsed = parse_thesis("Swiss watch companies", country="Switzerland")
    assert parsed.country == "Switzerland"
    assert not any("but explicit" in w for w in parsed.warnings)


def test_08_explicit_country_only_derives_region() -> None:
    # No geography in the prompt; explicit country drives the region.
    parsed = parse_thesis("watch companies", country="Denmark")
    assert parsed.country == "Denmark"
    assert parsed.region == "Europe"


# ===========================================================================
# 3. Controlled-selector validation (service level)
# ===========================================================================


@pytest.mark.asyncio
async def test_09_invalid_country_rejected() -> None:
    db, _ = _mock_session()
    with pytest.raises(ValueError, match="Country must be one of the supported"):
        await mds.create_pending_thesis_run(db, _payload(country="Atlantis"))


@pytest.mark.asyncio
async def test_10_invalid_region_rejected() -> None:
    db, _ = _mock_session()
    with pytest.raises(ValueError, match="Region must be one of the supported"):
        await mds.create_pending_thesis_run(db, _payload(region="Middle Earth"))


@pytest.mark.asyncio
async def test_11_invalid_sector_rejected() -> None:
    db, _ = _mock_session()
    with pytest.raises(ValueError, match="Sector must be one of the supported"):
        await mds.create_pending_thesis_run(db, _payload(sector="Cryptozoology"))


@pytest.mark.asyncio
async def test_12_supported_country_case_insensitive_accepted() -> None:
    # A lower-cased supported value is accepted and canonicalized downstream.
    db, _ = _mock_session()
    run = await mds.create_pending_thesis_run(
        db, _payload(thesis_text="Swiss watch companies", country="switzerland")
    )
    assert run.mode == "thesis"
    assert run.parsed_thesis_json["country"] == "Switzerland"


# ===========================================================================
# 4. Filter option builders
# ===========================================================================


def test_13_supported_filters_expose_canonical_options() -> None:
    filters = get_supported_filters()
    region_vals = {r["value"] for r in filters["regions"]}
    country_vals = {c["value"] for c in filters["countries"]}
    sector_vals = {s["value"] for s in filters["sectors"]}
    assert {"Europe", "North America", "Asia"} <= region_vals
    assert {"Switzerland", "Denmark", "United States"} <= country_vals
    assert {"Consumer Discretionary", "Technology", "Industrials"} <= sector_vals
    # Countries are region-tagged so the UI can filter by region.
    switzerland = next(c for c in filters["countries"] if c["value"] == "Switzerland")
    assert switzerland["region"] == "Europe"
    # Aliases never leak into the canonical sector options.
    assert "luxury goods" not in {s["value"] for s in filters["sectors"]}


def test_14_canonicalizers_reject_unknown_and_accept_known() -> None:
    assert canonical_region("europe") == "Europe"
    assert canonical_region("atlantis") is None
    assert canonical_country("switzerland") == "Switzerland"
    assert canonical_country("atlantis") is None
    assert is_supported_sector("luxury goods") is True  # alias resolves
    assert is_supported_sector("Cryptozoology") is False
    assert is_supported_sector(None) is True  # empty = not specified


# ===========================================================================
# 5. Strict country filtering survives autofill
# ===========================================================================


def test_15_swiss_watch_universe_is_swiss_only() -> None:
    parsed = parse_thesis("Swiss watch companies")
    universe = build_universe(parsed.to_dict())
    countries = {it["country"] for it in universe.items}
    assert countries == {"Switzerland"}
    assert {it["ticker"] for it in universe.items} == {"UHR", "CFR"}


def test_16_danish_jewelry_universe_is_danish_only() -> None:
    parsed = parse_thesis("Danish jewelry companies")
    universe = build_universe(parsed.to_dict())
    assert {it["ticker"] for it in universe.items} == {"PNDORA"}


def test_17_explicit_country_narrows_universe_over_prompt() -> None:
    # Prompt implies Swiss; explicit Denmark wins and narrows to Danish issuers.
    parsed = parse_thesis("Swiss watch companies", country="Denmark")
    universe = build_universe(parsed.to_dict())
    assert {it["country"] for it in universe.items} == {"Denmark"}


# ===========================================================================
# 6. Endpoints
# ===========================================================================


@pytest.mark.asyncio
async def test_18_parse_thesis_endpoint_previews_without_a_run(client) -> None:
    resp = await client.post(
        "/api/v1/market-discovery/parse-thesis",
        json={"thesis": "Swiss watch companies"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["country"] == "Switzerland"
    assert body["region"] == "Europe"
    assert body["sector"] == "Consumer Discretionary"
    assert "luxury_goods" in body["themes"]
    assert body["extraction_source"] == "prompt_text"


@pytest.mark.asyncio
async def test_19_parse_thesis_endpoint_detects_us_semiconductors(client) -> None:
    resp = await client.post(
        "/api/v1/market-discovery/parse-thesis",
        json={"thesis": "US semiconductor equipment companies"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["country"] == "United States"
    assert body["sector"] == "Technology"


@pytest.mark.asyncio
async def test_20_supported_filters_endpoint(client) -> None:
    resp = await client.get("/api/v1/market-discovery/supported-filters")
    assert resp.status_code == 200
    body = resp.json()
    region_vals = {r["value"] for r in body["regions"]}
    country_vals = {c["value"] for c in body["countries"]}
    sector_vals = {s["value"] for s in body["sectors"]}
    assert "Europe" in region_vals
    assert "Switzerland" in country_vals
    assert "Consumer Discretionary" in sector_vals


@pytest.mark.asyncio
async def test_21_parse_thesis_endpoint_does_not_create_run(client) -> None:
    # A vague thesis still returns 200 (preview) with needs_narrowing, never a run.
    resp = await client.post(
        "/api/v1/market-discovery/parse-thesis",
        json={"thesis": "best stocks to buy"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_narrowing"] is True
    # No BUY/SELL/HOLD/WATCH label ever leaks into the preview payload values.
    blob = " ".join(str(v) for v in body.values()).upper()
    for bad in (" BUY ", " SELL ", " HOLD ", " WATCH "):
        assert bad not in f" {blob} "
