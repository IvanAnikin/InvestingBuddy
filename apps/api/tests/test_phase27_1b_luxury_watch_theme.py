"""
Phase 27.1B — Luxury/watch theme, sector taxonomy, supported themes, and the
company-name backfill fix.

All tests run OFFLINE: the parser, taxonomy and universe builder are pure, and
the signal extractor is injected so no provider/SEC/price/network call is made.

Coverage:
  - Parser: "European watch producers" and friends map to luxury_goods
  - Safety: "Watches & Jewelry" / "Swatch Group AG" must NOT trip the scanner
  - Taxonomy: "Luxury Goods" resolves to Consumer Discretionary
  - Universe: bounded, exchange-aware, region-filtered, never fabricated
  - No SEC ticker collision (MC.PA is LVMH, never Moelis; UHR.SW not SEC-eligible)
  - Company name: curated name wins over a bare-ticker stub, honestly attributed
  - Supported-themes endpoint shape + example coverage
  - Regressions: defense (BA.LSE != Boeing) and US semiconductors still work
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.discovery import DiscoveryCandidate
from app.services import market_discovery_service as mds
from app.services import safety_terms
from app.services.discovery_signal_extractor import (
    ExtractedSignal,
    ensure_company,
    is_placeholder_company_name,
)
from app.services.exchange_registry import get_exchange, is_sec_eligible
from app.services.market_thesis_parser import get_supported_themes, parse_thesis
from app.services.market_universe_builder import (
    HARD_MAX_UNIVERSE_SIZE,
    THEME_COMPANY_REGISTRY,
    build_universe,
)
from app.services.sector_taxonomy import (
    get_supported_sector_aliases,
    normalize_industry,
    normalize_sector,
    sector_matches,
)


def _universe(thesis: str, **kwargs):
    """Parse + build in one step; ``max_universe_size`` passes through."""
    cap = kwargs.pop("max_universe_size", 25)
    parsed = parse_thesis(thesis, **kwargs)
    return parsed, build_universe(parsed.to_dict(), max_universe_size=cap)


# ===========================================================================
# 1. Thesis parser — luxury / watch
# ===========================================================================


def test_01_european_watch_producers_maps_to_luxury_and_europe() -> None:
    parsed = parse_thesis("European watch producers")
    assert "luxury_goods" in parsed.themes
    assert "Europe" in parsed.regions
    assert "Consumer Discretionary" in parsed.sectors
    assert any("watch" in k for k in parsed.keywords)
    assert parsed.confidence > 0
    assert parsed.needs_narrowing is False


def test_02_swiss_watch_companies_maps_to_luxury_switzerland_europe() -> None:
    parsed = parse_thesis("Swiss watch companies")
    assert "luxury_goods" in parsed.themes
    assert "Switzerland" in parsed.countries
    assert "Europe" in parsed.regions
    assert parsed.needs_narrowing is False


def test_03_european_luxury_goods_companies_maps_to_luxury() -> None:
    parsed = parse_thesis("European luxury goods companies")
    assert "luxury_goods" in parsed.themes
    assert "Europe" in parsed.regions
    assert parsed.needs_narrowing is False


@pytest.mark.parametrize(
    "thesis",
    [
        "European watchmakers",
        "European watches and jewelry companies",
        "public luxury brands in Europe",
        "Swiss timepiece manufacturers",
    ],
)
def test_04_luxury_thesis_variants_all_parse(thesis: str) -> None:
    parsed = parse_thesis(thesis)
    assert "luxury_goods" in parsed.themes, thesis
    assert parsed.needs_narrowing is False, thesis


def test_04b_swatch_alone_does_not_match_via_watch() -> None:
    """Phrase matching is whole-word: "Swatch" must not trigger "watch"."""
    parsed = parse_thesis("Swatch")
    assert "luxury_goods" not in parsed.themes
    assert parsed.needs_narrowing is True


def test_04c_theme_phrase_sets_are_pairwise_disjoint() -> None:
    """
    Overlapping phrases would silently union two themes' sectors.

    ``parse_thesis`` iterates every theme, so a phrase shared by two themes
    makes both fire and merges their sector/industry hints — a search that
    quietly widens beyond what the admin asked for.
    """
    from app.services.market_thesis_parser import _THEME_TABLE

    seen: dict[str, str] = {}
    for theme, spec in _THEME_TABLE.items():
        for phrase in spec["phrases"]:
            assert phrase not in seen, (
                f"phrase {phrase!r} is shared by {seen[phrase]!r} and {theme!r}"
            )
            seen[phrase] = theme


def test_05_parser_emits_no_recommendation_language() -> None:
    """The parse is a search structure — it must contain no action language."""
    parsed = parse_thesis("European watch producers")
    blob = " ".join(
        parsed.themes
        + parsed.sectors
        + parsed.industries
        + parsed.keywords
        + parsed.warnings
        + [parsed.normalized_text]
    )
    assert safety_terms.scan_text(blob) == []


# ===========================================================================
# 2. Safety scanner must not false-positive on real luxury vocabulary
# ===========================================================================


@pytest.mark.parametrize(
    "text",
    [
        "Watches & Jewelry",
        "Watches and Jewellery",
        "Swatch Group AG",
        "UHR trades on the SIX Swiss Exchange",
        "Compagnie Financiere Richemont SA",
        "The watch industry is cyclical",
        "Luxury Goods",
    ],
)
def test_06_luxury_vocabulary_passes_the_safety_scanner(text: str) -> None:
    assert safety_terms.scan_text(text) == [], text


@pytest.mark.parametrize(
    "text",
    [
        "Rating: BUY",
        "Rating: Hold",
        "price target of 120",
        "fair value estimate",
        "SELL",
    ],
)
def test_07_real_recommendation_language_still_blocked(text: str) -> None:
    """The luxury fix must not have widened the gate for actual violations."""
    assert safety_terms.scan_text(text) != [], text


# ===========================================================================
# 3. Sector taxonomy
# ===========================================================================


@pytest.mark.parametrize(
    "value",
    [
        "Luxury Goods",
        "luxury",
        "Watches & Jewelry",
        "jewellery",
        "personal goods",
        "premium brands",
        "apparel",
    ],
)
def test_08_luxury_aliases_normalize_to_consumer_discretionary(value: str) -> None:
    assert normalize_sector(value) == "Consumer Discretionary"


def test_09_existing_sectors_still_normalize() -> None:
    for sector in (
        "Industrials",
        "Technology",
        "Energy",
        "Financials",
        "Healthcare",
        "Materials",
        "Utilities",
        "Consumer Discretionary",
    ):
        assert normalize_sector(sector) == sector


def test_10_unknown_sector_is_not_guessed() -> None:
    """An unrecognized sector must return None, never a plausible guess."""
    assert normalize_sector("Interdimensional Widgets") is None
    assert normalize_industry("Interdimensional Widgets") is None


def test_11_normalize_industry_keeps_granularity() -> None:
    assert normalize_industry("watches and jewelry") == "Watches & Jewelry"
    assert normalize_industry("jewellery") == "Jewelry"
    assert normalize_industry("handbags") == "Leather Goods"
    # Must NOT collapse an industry up into its sector.
    assert normalize_industry("Luxury Goods") == "Luxury Goods"


def test_12_sector_matches_bridges_industry_to_sector() -> None:
    assert sector_matches("Luxury Goods", "Consumer Discretionary") is True
    assert (
        sector_matches("Watches & Jewelry", "Consumer Discretionary", ["Luxury Goods"])
        is True
    )
    assert sector_matches("Industrials", "Industrials") is True
    # A genuine mismatch stays a mismatch.
    assert sector_matches("Luxury Goods", "Technology", ["Semiconductors"]) is False
    # Empty filter matches everything.
    assert sector_matches(None, "Technology") is True


def test_13_supported_sector_aliases_shape() -> None:
    aliases = get_supported_sector_aliases()
    by_sector = {a["sector"]: a for a in aliases}
    assert "Consumer Discretionary" in by_sector
    cd = by_sector["Consumer Discretionary"]
    assert "luxury goods" in cd["aliases"]
    assert "Watches & Jewelry" in cd["industries"]
    assert "Industrials" in by_sector


# ===========================================================================
# 4. Universe builder — luxury registry
# ===========================================================================


def test_14_european_watch_producers_builds_non_empty_universe() -> None:
    _, universe = _universe("European watch producers")
    assert universe.needs_narrowing is False
    assert len(universe.items) > 0


@pytest.mark.parametrize(
    "ticker,exchange,name_fragment",
    [
        ("UHR", "SW", "Swatch"),
        ("CFR", "SW", "Richemont"),
        ("MC", "PA", "LVMH"),
    ],
)
def test_15_universe_includes_curated_european_issuers(
    ticker: str, exchange: str, name_fragment: str
) -> None:
    _, universe = _universe("European watch producers")
    match = next(
        (
            i
            for i in universe.items
            if i["ticker"] == ticker and i["exchange"] == exchange
        ),
        None,
    )
    assert match is not None, f"{ticker}.{exchange} missing from universe"
    assert name_fragment.lower() in (match["company_name"] or "").lower()


def test_16_europe_filter_excludes_us_only_luxury_names() -> None:
    _, universe = _universe("European watch producers")
    tickers = {i["ticker"] for i in universe.items}
    assert "CPRI" not in tickers
    assert "TPR" not in tickers
    # Excluded, never silently dropped.
    excluded = {e["ticker"] for e in universe.excluded}
    assert {"CPRI", "TPR"} <= excluded


def test_17_global_luxury_thesis_keeps_non_european_names() -> None:
    """Without a region filter the US/HK luxury entries stay in the universe."""
    _, universe = _universe("luxury goods companies")
    tickers = {i["ticker"] for i in universe.items}
    assert {"CPRI", "TPR", "1913"} <= tickers


def test_18_universe_cap_is_respected() -> None:
    _, universe = _universe("luxury goods companies", max_universe_size=3)
    assert len(universe.items) == 3
    assert any("truncated" in w for w in universe.warnings)
    _, hard = _universe("luxury goods companies", max_universe_size=9999)
    assert len(hard.items) <= HARD_MAX_UNIVERSE_SIZE


def test_19_every_luxury_universe_item_is_fully_source_tagged() -> None:
    _, universe = _universe("European watch producers")
    for item in universe.items:
        assert item["exchange"], item
        assert item["country"], item
        assert item["region"], item
        assert item["universe_source"] == "curated_theme_registry"
        assert item["source_tier"] == "T3_curated_reference_list"
        assert item["relevance_reason"], item
        assert item["company_name"], item


def test_20_luxury_registry_exchanges_are_all_known_venues() -> None:
    """An unknown venue would silently become SEC-ineligible — catch it here."""
    for entry in THEME_COMPANY_REGISTRY["luxury_goods"]:
        info = get_exchange(entry["exchange"])
        assert info is not None, entry
        assert info.country == entry["country"] or entry["country"] in info.country


def test_21_mc_pa_is_lvmh_and_never_sec_eligible() -> None:
    """MC + PA must be LVMH here; the SEC ticker index would return Moelis."""
    entry = next(
        e for e in THEME_COMPANY_REGISTRY["luxury_goods"] if e["ticker"] == "MC"
    )
    assert entry["exchange"] == "PA"
    assert "LVMH" in entry["company_name"]
    assert is_sec_eligible("PA") is False


def test_22_uhr_sw_is_not_sec_eligible() -> None:
    """Swatch trades on SIX; a ticker-only SEC lookup must never be attempted."""
    assert is_sec_eligible("SW") is False


def test_23_no_luxury_entry_claims_a_sec_eligible_non_us_venue() -> None:
    for entry in THEME_COMPANY_REGISTRY["luxury_goods"]:
        info = get_exchange(entry["exchange"])
        assert info is not None
        if not info.is_us:
            assert info.sec_eligible is False, entry


def test_24_sector_filter_luxury_goods_selects_consumer_discretionary() -> None:
    """A structured sector filter alone (no theme phrase) still builds."""
    parsed = parse_thesis("companies in Europe", region="Europe", sector="Luxury Goods")
    universe = build_universe(parsed.to_dict(), max_universe_size=25)
    tickers = {i["ticker"] for i in universe.items}
    assert "UHR" in tickers


# ===========================================================================
# 5. Company-name backfill
# ===========================================================================


def test_25_is_placeholder_company_name() -> None:
    assert is_placeholder_company_name(None, "UHR") is True
    assert is_placeholder_company_name("", "UHR") is True
    assert is_placeholder_company_name("UHR", "UHR") is True
    assert is_placeholder_company_name("uhr", "UHR") is True
    assert is_placeholder_company_name("UHR.SW", "UHR") is True
    assert is_placeholder_company_name("Swatch Group AG", "UHR") is False


def _thesis_item(ticker: str, name: str) -> dict:
    return {
        "ticker": ticker,
        "company_name": name,
        "exchange": "SW",
        "country": "Switzerland",
        "region": "Europe",
        "sector": "Consumer Discretionary",
        "industry": "Watches & Jewelry",
        "theme": "luxury_goods",
        "matched_keywords": ["watch"],
        "relevance_reason": "matches theme 'luxury_goods'",
        "universe_source": "curated_theme_registry",
        "source_tier": "T3_curated_reference_list",
        "relevance_score_pre_scan": 80.0,
        "metadata_not_sourced": False,
    }


def _extracted_for(ticker: str, *, identity: dict) -> ExtractedSignal:
    signal = {
        "ticker": ticker,
        "exchange": "SW",
        "provider_name": "free_real",
        "identity": identity,
        "trend": {},
        "fundamentals": {"available": False},
        "market": {},
        "catalyst": {"total_events": 0},
        "source_quality": {"overall": "insufficient"},
        "completeness": {"missing_info_count": 0, "blocking_gap_count": 0},
        "warnings": [],
    }
    return ExtractedSignal(
        ticker=ticker,
        exchange="SW",
        provider_name="free_real",
        signal=signal,
        status="ok",
        safety_valid=True,
        schema_valid=False,
    )


def test_26_curated_name_replaces_bare_ticker_stub() -> None:
    """The stub name "UHR" must not shadow the curated "Swatch Group AG"."""
    extracted = _extracted_for("UHR", identity={"company_name": "UHR"})
    candidate = mds._build_candidate(
        uuid.uuid4(),
        extracted,
        {"candidate_score": 40.0},
        thesis_item=_thesis_item("UHR", "Swatch Group AG"),
    )
    assert candidate.company_name == "Swatch Group AG"


def test_27_raw_signal_identity_preserves_curated_name_and_provenance() -> None:
    extracted = _extracted_for("UHR", identity={"company_name": "UHR"})
    candidate = mds._build_candidate(
        uuid.uuid4(),
        extracted,
        {"candidate_score": 40.0},
        thesis_item=_thesis_item("UHR", "Swatch Group AG"),
    )
    identity = candidate.raw_signal_json["identity"]
    assert identity["company_name"] == "Swatch Group AG"
    assert identity["company_name_source"] == "curated_theme_registry"
    assert identity["company_name_source_tier"] == "T3_curated_reference_list"


def test_28_curated_name_is_never_attributed_to_sec_or_provider() -> None:
    """A curated display name must not masquerade as sourced fundamentals data."""
    extracted = _extracted_for(
        "UHR", identity={"company_name": "UHR", "legal_name": None}
    )
    candidate = mds._build_candidate(
        uuid.uuid4(),
        extracted,
        {"candidate_score": 40.0},
        thesis_item=_thesis_item("UHR", "Swatch Group AG"),
    )
    # legal_name stays exactly as the scan produced it (not sourced).
    assert candidate.legal_name is None
    identity = candidate.raw_signal_json["identity"]
    assert identity["company_name_source"] != "sec"
    assert identity["company_name_source_tier"].startswith("T3")


def test_29_real_provider_name_wins_over_curated_name() -> None:
    """A genuinely sourced name must not be overwritten by the registry."""
    extracted = _extracted_for(
        "UHR", identity={"company_name": "The Swatch Group Ltd (provider)"}
    )
    candidate = mds._build_candidate(
        uuid.uuid4(),
        extracted,
        {"candidate_score": 40.0},
        thesis_item=_thesis_item("UHR", "Swatch Group AG"),
    )
    assert candidate.company_name == "The Swatch Group Ltd (provider)"
    assert candidate.raw_signal_json["identity"]["company_name_source"] == (
        "provider_profile"
    )


def test_30_ticker_run_candidate_has_no_curated_name_metadata() -> None:
    """Ticker runs carry no thesis item — nothing to attribute, nothing added."""
    extracted = _extracted_for("AAPL", identity={"company_name": "Apple Inc."})
    candidate = mds._build_candidate(
        uuid.uuid4(), extracted, {"candidate_score": 40.0}, thesis_item=None
    )
    assert candidate.company_name == "Apple Inc."
    assert candidate.thesis_match_json is None


@pytest.mark.asyncio
async def test_31_ensure_company_uses_curated_name_for_a_new_stub() -> None:
    created: dict = {}

    async def fake_create(db, payload):
        created["name"] = payload.name
        return MagicMock(id=uuid.uuid4(), name=payload.name)

    import app.services.company_service as cs

    orig_get, orig_create = cs.get_company_by_ticker, cs.create_company
    cs.get_company_by_ticker = AsyncMock(return_value=None)
    cs.create_company = fake_create
    try:
        await ensure_company(
            AsyncMock(), "UHR", "SW", company_name="Swatch Group AG"
        )
    finally:
        cs.get_company_by_ticker, cs.create_company = orig_get, orig_create

    assert created["name"] == "Swatch Group AG"


@pytest.mark.asyncio
async def test_32_ensure_company_upgrades_an_existing_bare_ticker_stub() -> None:
    existing = MagicMock(id=uuid.uuid4())
    existing.name = "UHR"
    db = AsyncMock()

    import app.services.company_service as cs

    orig_get = cs.get_company_by_ticker
    cs.get_company_by_ticker = AsyncMock(return_value=existing)
    try:
        await ensure_company(db, "UHR", "SW", company_name="Swatch Group AG")
    finally:
        cs.get_company_by_ticker = orig_get

    assert existing.name == "Swatch Group AG"


@pytest.mark.asyncio
async def test_33_ensure_company_never_overwrites_a_real_name() -> None:
    existing = MagicMock(id=uuid.uuid4())
    existing.name = "The Swatch Group Ltd"
    db = AsyncMock()

    import app.services.company_service as cs

    orig_get = cs.get_company_by_ticker
    cs.get_company_by_ticker = AsyncMock(return_value=existing)
    try:
        await ensure_company(db, "UHR", "SW", company_name="Swatch Group AG")
    finally:
        cs.get_company_by_ticker = orig_get

    assert existing.name == "The Swatch Group Ltd"


# ===========================================================================
# 6. Supported themes — service + endpoint
# ===========================================================================


def test_34_parser_supported_themes_include_luxury_and_the_originals() -> None:
    ids = {t["id"] for t in get_supported_themes()}
    assert "luxury_goods" in ids
    assert {
        "defense",
        "semiconductors",
        "nuclear_energy",
        "grid_electrification",
        "robotics_automation",
        "biotech_pharma",
        "banks_fintech",
        "mining_materials",
        "ai_infrastructure",
    } <= ids


def test_35_supported_themes_service_joins_registry_coverage() -> None:
    payload = mds.get_supported_themes()
    lux = next(t for t in payload["themes"] if t["id"] == "luxury_goods")
    assert lux["universe_company_count"] == len(
        THEME_COMPANY_REGISTRY["luxury_goods"]
    )
    assert "Europe" in lux["regions"]
    assert "Switzerland" in lux["countries"]
    assert "Consumer Discretionary" in lux["sectors"]


def test_36_every_advertised_theme_actually_builds_a_universe() -> None:
    """The UI must never offer an example that returns an empty universe."""
    for theme in mds.get_supported_themes()["themes"]:
        for example in theme["examples"]:
            _, universe = _universe(example)
            assert universe.items, f"{theme['id']}: {example!r} built nothing"


def test_37_supported_theme_examples_include_european_watch_producers() -> None:
    payload = mds.get_supported_themes()
    assert "European watch producers" in payload["examples"]


def test_38_coverage_note_states_the_bootstrap_limitation() -> None:
    note = mds.get_supported_themes()["coverage_note"].lower()
    assert "bounded" in note and "curated" in note
    assert "not investment advice" in note


def test_39_supported_themes_payload_is_recommendation_free() -> None:
    payload = mds.get_supported_themes()
    hits = safety_terms.scan_value(payload)
    assert hits == [], safety_terms.hits_to_strings(hits)


@pytest.mark.asyncio
async def test_40_supported_themes_endpoint_returns_luxury(client) -> None:
    res = await client.get("/api/v1/market-discovery/supported-themes")
    assert res.status_code == 200
    body = res.json()
    ids = {t["id"] for t in body["themes"]}
    assert "luxury_goods" in ids
    assert "defense" in ids
    assert "European watch producers" in body["examples"]
    assert body["coverage_note"]
    assert any(s["sector"] == "Consumer Discretionary" for s in body["sectors"])


@pytest.mark.asyncio
async def test_41_supported_themes_endpoint_exposes_no_action_fields(client) -> None:
    res = await client.get("/api/v1/market-discovery/supported-themes")
    keys = " ".join(res.json().keys()).lower()
    for term in ("recommendation", "rating", "target", "fair_value", "upside"):
        assert term not in keys


# ===========================================================================
# 7. Thesis run end-to-end (offline) for the luxury theme
# ===========================================================================


def _lux_signal(ticker: str) -> dict:
    """A non-US issuer: fundamentals honestly not sourced (Phase 27.1A)."""
    return {
        "ticker": ticker,
        "exchange": "SW",
        "provider_name": "free_real",
        "is_mock": False,
        "provider_failed": False,
        "error": None,
        # The scan only knows the stub name — the curated one must win.
        "identity": {"company_name": ticker},
        "trend": {
            "momentum_label": "insufficient_price_history",
            "has_price_history": False,
        },
        "fundamentals": {"available": False},
        "market": {},
        "catalyst": {"total_events": 0, "coverage_status": "filings_only"},
        "source_quality": {"overall": "insufficient"},
        "completeness": {
            "missing_fields": ["fundamentals_not_sourced_non_us_exchange"],
            "missing_info_count": 1,
            "blocking_gap_count": 1,
        },
        "warnings": ["fundamentals not sourced for a non-SEC venue"],
    }


def _lux_extractor():
    async def _extract(
        db, *, ticker, exchange, provider_name, lookback_days, company_name=None
    ):
        signal = _lux_signal(ticker)
        signal["exchange"] = exchange
        return ExtractedSignal(
            ticker=ticker,
            exchange=exchange,
            provider_name=provider_name,
            signal=signal,
            status="ok",
            safety_valid=True,
            schema_valid=False,
        )

    return _extract


def _mock_session():
    """A fake session that captures only the CANDIDATES added (not the run)."""
    added: list = []

    def _add(obj):
        if isinstance(obj, DiscoveryCandidate):
            added.append(obj)

    db = AsyncMock()
    db.add = MagicMock(side_effect=_add)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db, added


def _lux_payload(**over):
    from app.schemas.market_discovery import ThesisDiscoveryRunCreate

    base = {
        "thesis_text": "European watch producers",
        "region": "Europe",
        "sector": "Luxury Goods",
        "max_universe_size": 25,
        "max_candidates": 10,
        "provider_name": "free_real",
        "lookback_days": 90,
    }
    base.update(over)
    return ThesisDiscoveryRunCreate(**base)


@pytest.mark.asyncio
async def test_42_luxury_thesis_creates_a_thesis_mode_run() -> None:
    db, _ = _mock_session()
    run = await mds.create_pending_thesis_run(db, _lux_payload())
    assert run.mode == "thesis"
    assert run.status == "pending"
    assert run.universe_count > 0
    assert "UHR" in (run.requested_tickers or [])
    assert run.human_review_required is True


@pytest.mark.asyncio
async def test_43_luxury_thesis_run_completes_without_crashing() -> None:
    db, added = _mock_session()
    run = await mds.create_pending_thesis_run(db, _lux_payload())
    processed = await mds.process_run(db, run, extractor=_lux_extractor())
    assert processed.status in {"completed", "completed_with_warnings"}
    assert processed.error_count == 0
    assert processed.processed_count == run.universe_count
    assert len(added) > 0


@pytest.mark.asyncio
async def test_44_luxury_candidates_show_curated_names_not_tickers() -> None:
    db, added = _mock_session()
    run = await mds.create_pending_thesis_run(db, _lux_payload())
    await mds.process_run(db, run, extractor=_lux_extractor())
    by_ticker = {c.ticker: c for c in added}
    assert "Swatch" in (by_ticker["UHR"].company_name or "")
    assert "LVMH" in (by_ticker["MC"].company_name or "")
    for candidate in added:
        assert candidate.company_name != candidate.ticker


@pytest.mark.asyncio
async def test_45_luxury_candidates_are_internal_only_and_human_reviewed() -> None:
    db, added = _mock_session()
    run = await mds.create_pending_thesis_run(db, _lux_payload())
    await mds.process_run(db, run, extractor=_lux_extractor())
    for candidate in added:
        assert candidate.human_review_required is True
        assert candidate.is_public is False
        assert candidate.safety_valid is True
        assert candidate.thesis_match_json is not None
        assert candidate.thesis_match_json["relevance_reason"]


@pytest.mark.asyncio
async def test_46_non_us_fundamentals_degrade_honestly() -> None:
    db, added = _mock_session()
    run = await mds.create_pending_thesis_run(db, _lux_payload())
    await mds.process_run(db, run, extractor=_lux_extractor())
    for candidate in added:
        # Never a fabricated financial number for a venue SEC cannot cover.
        assert candidate.revenue_mln is None
        assert candidate.net_income_mln is None
        assert candidate.market_cap_mln is None
        assert "fundamentals_not_sourced_non_us_exchange" in (
            candidate.missing_fields_json or []
        )


@pytest.mark.asyncio
async def test_47_luxury_candidate_text_is_recommendation_free() -> None:
    db, added = _mock_session()
    run = await mds.create_pending_thesis_run(db, _lux_payload())
    await mds.process_run(db, run, extractor=_lux_extractor())
    for candidate in added:
        blob = " ".join(
            [
                candidate.company_name or "",
                candidate.industry or "",
                candidate.score_explanation or "",
                " ".join(candidate.labels_json or []),
                str((candidate.thesis_match_json or {}).get("explanation") or ""),
            ]
        )
        hits = safety_terms.scan_text(blob)
        assert hits == [], safety_terms.hits_to_strings(hits)


@pytest.mark.asyncio
async def test_48_vague_thesis_still_refused_with_supported_theme_guidance() -> None:
    db, _ = _mock_session()
    with pytest.raises(ValueError) as exc:
        await mds.create_pending_thesis_run(
            db, _lux_payload(thesis_text="best stocks to make money", sector=None)
        )
    assert "narrow" in str(exc.value).lower()


@pytest.mark.asyncio
async def test_49_unknown_thesis_error_points_at_supported_themes(client) -> None:
    res = await client.post(
        "/api/v1/market-discovery/thesis-runs",
        json={"thesis_text": "companies whose logo is a duck", "provider_name": "free_real"},
    )
    assert res.status_code == 422
    assert "supported-themes" in res.json()["detail"]


# ===========================================================================
# 8. Regressions — Phase 27 / 27.1A must still hold
# ===========================================================================


def test_50_european_defense_thesis_still_builds() -> None:
    _, universe = _universe(
        "European defense suppliers benefiting from NATO spending", region="Europe"
    )
    tickers = {i["ticker"] for i in universe.items}
    assert "RHM" in tickers
    assert "BA" in tickers


def test_51_ba_lse_is_bae_not_boeing_and_not_sec_eligible() -> None:
    _, universe = _universe(
        "European defense suppliers benefiting from NATO spending", region="Europe"
    )
    ba = next(i for i in universe.items if i["ticker"] == "BA")
    assert ba["exchange"] == "LSE"
    assert "BAE" in (ba["company_name"] or "")
    assert "Boeing" not in (ba["company_name"] or "")
    assert is_sec_eligible("LSE") is False


def test_52_us_semiconductor_thesis_still_builds() -> None:
    _, universe = _universe(
        "US semiconductor equipment companies with recent positive catalysts"
    )
    tickers = {i["ticker"] for i in universe.items}
    assert "AMAT" in tickers
    for item in universe.items:
        assert item["region"] == "North America"


def test_53_luxury_theme_did_not_leak_into_other_themes() -> None:
    """Adding "watch"/"luxury" must not have widened the existing themes."""
    for thesis in (
        "European defense suppliers benefiting from NATO spending",
        "US semiconductor equipment companies with recent positive catalysts",
        "Japanese industrial robotics companies",
    ):
        parsed = parse_thesis(thesis)
        assert "luxury_goods" not in parsed.themes, thesis


def test_54_manual_ticker_universe_still_resolves() -> None:
    from app.schemas.market_discovery import DiscoveryRunCreate

    universe = mds.resolve_universe(
        DiscoveryRunCreate(
            universe_source="manual_tickers",
            tickers=["AAPL", "MSFT", "NVDA"],
            exchange="US",
        )
    )
    assert [u["ticker"] for u in universe] == ["AAPL", "MSFT", "NVDA"]
    assert all(u["exchange"] == "US" for u in universe)


def test_55_no_publish_route_was_added() -> None:
    """Phase 27.1B must not introduce any public publishing surface."""
    from app.api.v1 import market_discovery as md_router

    paths = [r.path for r in md_router.router.routes]
    assert not any("publish" in p for p in paths)
    # The new read-only themes route is present alongside the existing ones.
    assert any(p.endswith("/supported-themes") for p in paths)
