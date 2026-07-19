"""
Phase 27 — Market Segment Discovery / Thesis-to-Universe tests.

All tests run OFFLINE: the per-ticker signal extractor and analysis workflow are
injected, so no provider, SEC, price, catalyst, or network call is ever made.
The thesis parser and universe builder are fully deterministic.

Coverage:
  - Thesis parser (theme/region extraction, vague -> needs_narrowing)
  - Universe builder (bounded, source-tagged, no fabrication, exclusions)
  - Thesis relevance + combined internal score
  - Thesis run creation (mode=thesis) + processing through the Phase 25 pipeline
  - Candidate thesis fields + internal-only interest labels
  - Run Full Analysis from a thesis candidate
  - Safety (no BUY/SELL/HOLD/WATCH/target/fair value/upside/downside/recommend;
    human_review_required True; is_public False; no publish route)
"""

from __future__ import annotations

import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.discovery import DiscoveryCandidate
from app.schemas.market_discovery import ThesisDiscoveryRunCreate
from app.services import market_discovery_service as mds
from app.services.discovery_signal_extractor import ExtractedSignal
from app.services.discovery_thesis_scoring import (
    INTERNAL_INTEREST_LABELS,
    compute_combined_internal_score,
    score_thesis_relevance,
)
from app.services.market_thesis_parser import parse_thesis
from app.services.market_universe_builder import (
    HARD_MAX_UNIVERSE_SIZE,
    build_universe,
)

# ---------------------------------------------------------------------------
# Forbidden investment-action terms (word-boundary, like the service scan)
# ---------------------------------------------------------------------------

_FORBIDDEN_RE = [
    re.compile(p, re.IGNORECASE)
    for p in [
        r"\bbuy\b",
        r"\bsell\b",
        r"\bhold\b",
        r"\bwatch\b",
        r"price target",
        r"target price",
        r"fair value",
        r"intrinsic value",
        r"\bupside\b",
        r"\bdownside\b",
        r"\bundervalued\b",
        r"\bovervalued\b",
        r"recommendation",
    ]
]


def _has_forbidden(text: str) -> list[str]:
    return [m.group(0) for r in _FORBIDDEN_RE if (m := r.search(text or ""))]


# ---------------------------------------------------------------------------
# Signal / extractor / session helpers
# ---------------------------------------------------------------------------


def _signal(ticker: str, *, strong: bool = True) -> dict:
    if strong:
        return {
            "ticker": ticker,
            "exchange": "US",
            "provider_name": "free_real",
            "is_mock": False,
            "provider_failed": False,
            "error": None,
            "identity": {
                "company_name": f"{ticker} Corp.",
                "sector": "Industrials",
                "industry": "Aerospace & Defense",
                "country": "United States",
            },
            "trend": {
                "momentum_label": "positive_momentum_candidate",
                "return_1m": 5.0,
                "return_3m": 12.0,
                "return_6m": 20.0,
                "pct_above_ma50": 6.0,
                "pct_above_ma200": 10.0,
                "has_price_history": True,
            },
            "fundamentals": {
                "available": True,
                "stale": False,
                "revenue_mln": 50000.0,
                "net_income_mln": 5000.0,
                "free_cash_flow_mln": 4000.0,
                "total_debt_mln": 10000.0,
                "cash_mln": 3000.0,
            },
            "market": {
                "latest_close": 400.0,
                "market_cap_mln": 90000.0,
                "enterprise_value_mln": 95000.0,
                "pe_ratio": 20.0,
            },
            "catalyst": {
                "coverage_status": "strong",
                "total_events": 5,
                "positive_count": 3,
                "high_strength_count": 2,
                "primary_or_regulator_event_count": 3,
                "aggregator_only_count": 0,
                "press_release_event_count": 2,
                "news_event_count": 1,
                "filing_event_count": 2,
                "latest_event_date": "2026-07-10",
                "warnings": [],
                "missing_sources": [],
            },
            "source_quality": {
                "overall": "strong",
                "strong_sources_count": 3,
                "weak_sources_count": 0,
                "aggregator_only_count": 0,
                "source_tiers": {"T2_regulator_or_gov": 3},
            },
            "completeness": {
                "missing_fields": ["fundamentals.ebitda_mln"],
                "missing_info_count": 1,
                "blocking_gap_count": 0,
            },
            "warnings": [],
        }
    return {
        "ticker": ticker,
        "exchange": "XETRA",
        "provider_name": "free_real",
        "is_mock": False,
        "provider_failed": True,
        "error": "no SEC fundamentals for non-US issuer",
        "identity": {},
        "trend": {"has_price_history": False},
        "fundamentals": {"available": False},
        "market": {},
        "catalyst": {"total_events": 0},
        "source_quality": {"overall": "insufficient"},
        "completeness": {"missing_fields": [], "missing_info_count": 0, "blocking_gap_count": 0},
        "warnings": ["thin data"],
    }


def _extracted(signal: dict, *, status: str = "ok") -> ExtractedSignal:
    return ExtractedSignal(
        ticker=signal["ticker"],
        exchange=signal["exchange"],
        provider_name=signal["provider_name"],
        signal=signal,
        status=status,
        error=signal.get("error"),
        analysis_report_id=str(uuid.uuid4()),
        agent_run_id=str(uuid.uuid4()),
        schema_valid=False,
        safety_valid=True,
    )


def _fake_extractor(strong: set[str] | None = None):
    strong = strong or set()

    async def _extract(db, *, ticker, exchange, provider_name, lookback_days):
        sig = _signal(ticker, strong=(not strong or ticker in strong))
        sig["ticker"] = ticker
        sig["exchange"] = exchange
        status = "failed" if sig.get("provider_failed") else "ok"
        return _extracted(sig, status=status)

    return _extract


def _mock_session():
    added: list = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda o: added.append(o))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db, added


def _thesis_payload(**over) -> ThesisDiscoveryRunCreate:
    base = {
        "thesis_text": "European defense suppliers benefiting from NATO spending",
        "region": "Europe",
        "max_universe_size": 25,
        "max_candidates": 10,
        "provider_name": "free_real",
        "lookback_days": 90,
    }
    base.update(over)
    return ThesisDiscoveryRunCreate(**base)


# ===========================================================================
# 1. Thesis parser
# ===========================================================================


def test_01_parser_extracts_defense_europe_nato() -> None:
    parsed = parse_thesis(
        "European defense suppliers benefiting from NATO spending", region="Europe"
    )
    assert "defense" in parsed.themes
    assert "Europe" in parsed.regions
    assert any(k in parsed.keywords for k in ("defense", "nato"))
    assert "Industrials" in parsed.sectors
    assert parsed.needs_narrowing is False
    assert parsed.confidence > 0.0


def test_02_vague_thesis_needs_narrowing() -> None:
    for txt in ["best stocks to buy", "top stocks", "make money", "good stocks"]:
        parsed = parse_thesis(txt)
        assert parsed.needs_narrowing is True, txt
        assert parsed.warnings


def test_02b_unmatched_thesis_needs_narrowing() -> None:
    parsed = parse_thesis("some unrelated words with no theme")
    assert parsed.needs_narrowing is True


def test_03_parser_extracts_semiconductor_equipment_and_us() -> None:
    parsed = parse_thesis(
        "US semiconductor equipment companies with recent positive catalysts"
    )
    assert "semiconductors" in parsed.themes
    assert "North America" in parsed.regions
    assert parsed.catalyst_hints  # "catalyst"/"positive" intent captured


def test_04_parser_extracts_japanese_robotics() -> None:
    parsed = parse_thesis("Japanese robotics and automation companies")
    assert "robotics_automation" in parsed.themes
    assert "Japan" in parsed.regions


def test_05_parser_exclusion_keywords() -> None:
    parsed = parse_thesis("semiconductor companies excluding china")
    assert "semiconductors" in parsed.themes
    assert "china" in parsed.exclusion_keywords


# ===========================================================================
# 2. Universe builder
# ===========================================================================


def test_06_universe_bounded_by_max_size() -> None:
    parsed = parse_thesis("nuclear energy and uranium companies")
    result = build_universe(parsed.to_dict(), max_universe_size=3)
    assert len(result.items) <= 3
    assert result.needs_narrowing is False


def test_07_universe_hard_cap_enforced() -> None:
    parsed = parse_thesis("nuclear energy uranium")
    # Request an absurd size — the hard cap must still bound it.
    result = build_universe(parsed.to_dict(), max_universe_size=9999)
    assert len(result.items) <= HARD_MAX_UNIVERSE_SIZE


def test_08_universe_items_include_source_and_reason() -> None:
    parsed = parse_thesis("European defense suppliers", region="Europe")
    result = build_universe(parsed.to_dict(), max_universe_size=25)
    assert result.items
    for item in result.items:
        assert item["universe_source"] == "curated_theme_registry"
        assert item["source_tier"].startswith("T3")
        assert item["relevance_reason"]
        assert item["relevance_score_pre_scan"] >= 0.0
        # Real, non-fabricated company: ticker + name present.
        assert item["ticker"]
        assert item["company_name"]


def test_09_region_filter_excludes_with_reason() -> None:
    parsed = parse_thesis("defense suppliers", region="Europe")
    result = build_universe(parsed.to_dict(), max_universe_size=25)
    # US defense names are excluded (region mismatch), recorded with a reason.
    assert result.excluded
    assert all("reason" in e for e in result.excluded)
    assert all(it["country"] != "United States" for it in result.items)


def test_10_vague_thesis_universe_empty_and_needs_narrowing() -> None:
    parsed = parse_thesis("best stocks")
    result = build_universe(parsed.to_dict())
    assert result.items == []
    assert result.needs_narrowing is True


def test_11_no_fabrication_when_no_match() -> None:
    parsed = parse_thesis("underwater basket weaving companies in atlantis")
    result = build_universe(parsed.to_dict())
    # Either needs narrowing or simply no items — never invented companies.
    assert result.items == []


# ===========================================================================
# 3. Thesis scoring
# ===========================================================================


def test_12_thesis_relevance_rewards_theme_and_region() -> None:
    parsed = parse_thesis("European defense suppliers", region="Europe").to_dict()
    item = {
        "ticker": "RHM",
        "company_name": "Rheinmetall AG",
        "sector": "Industrials",
        "industry": "Aerospace & Defense",
        "country": "Germany",
        "region": "Europe",
        "theme": "defense",
        "metadata_not_sourced": False,
    }
    rel = score_thesis_relevance(item, parsed)
    assert rel["thesis_relevance_score"] >= 70.0
    assert "matches theme 'defense'" in rel["relevance_reason"]


def test_13_metadata_not_sourced_penalized() -> None:
    parsed = parse_thesis("defense suppliers").to_dict()
    base = {
        "ticker": "X",
        "company_name": "X",
        "sector": "Industrials",
        "industry": "Aerospace & Defense",
        "country": "United States",
        "region": "North America",
        "theme": "defense",
    }
    good = score_thesis_relevance({**base, "metadata_not_sourced": False}, parsed)
    bad = score_thesis_relevance({**base, "metadata_not_sourced": True}, parsed)
    assert bad["thesis_relevance_score"] < good["thesis_relevance_score"]


def test_14_combined_score_formula_and_label() -> None:
    res = compute_combined_internal_score(
        thesis_relevance_score=90.0,
        discovery_score=60.0,
        catalyst_score=70.0,
        source_quality_score=80.0,
        missing_info_count=0,
        discovery_grade="high_internal_interest",
    )
    # 0.45*90 + 0.35*60 + 0.10*70 + 0.10*80 = 40.5+21+7+8 = 76.5
    assert res["combined_internal_score"] == pytest.approx(76.5, abs=0.1)
    assert res["internal_interest_label"] == "high_internal_research_interest"


def test_15_combined_label_insufficient_when_discovery_data_insufficient() -> None:
    res = compute_combined_internal_score(
        thesis_relevance_score=95.0,
        discovery_score=0.0,
        catalyst_score=0.0,
        source_quality_score=0.0,
        missing_info_count=0,
        discovery_grade="data_insufficient",
    )
    assert res["internal_interest_label"] == "insufficient_data"


def test_16_interest_labels_are_internal_only() -> None:
    for combined in (0, 25, 50, 80):
        res = compute_combined_internal_score(
            thesis_relevance_score=combined,
            discovery_score=combined,
            catalyst_score=0,
            source_quality_score=0,
            missing_info_count=0,
        )
        assert res["internal_interest_label"] in INTERNAL_INTEREST_LABELS


# ===========================================================================
# 4. Thesis run creation + processing
# ===========================================================================


@pytest.mark.asyncio
async def test_17_create_pending_thesis_run_sets_mode() -> None:
    db, _ = _mock_session()
    run = await mds.create_pending_thesis_run(db, _thesis_payload())
    assert run.mode == "thesis"
    assert run.universe_source == "thesis_generated"
    assert run.thesis_text
    assert run.parsed_thesis_json["themes"] == ["defense"]
    assert run.universe_json["items"]
    assert run.universe_count == len(run.requested_tickers)
    assert run.human_review_required is True


@pytest.mark.asyncio
async def test_18_vague_thesis_run_rejected() -> None:
    db, _ = _mock_session()
    with pytest.raises(ValueError, match="narrow"):
        await mds.create_pending_thesis_run(db, _thesis_payload(thesis_text="best stocks"))


@pytest.mark.asyncio
async def test_19_no_match_thesis_run_rejected() -> None:
    db, _ = _mock_session()
    with pytest.raises(ValueError):
        await mds.create_pending_thesis_run(
            db, _thesis_payload(thesis_text="grid electrification", region="China")
        )


@pytest.mark.asyncio
async def test_20_thesis_run_processes_candidates() -> None:
    db, added = _mock_session()
    # US semiconductor thesis -> US names -> strong signals for all.
    run = await mds.create_thesis_discovery_run(
        db,
        _thesis_payload(
            thesis_text="US semiconductor equipment companies", region=None
        ),
        extractor=_fake_extractor(),
    )
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert run.status in ("completed", "completed_with_warnings")
    assert cands
    assert run.candidate_count == len(cands)


@pytest.mark.asyncio
async def test_21_thesis_candidate_has_thesis_and_combined_scores() -> None:
    db, added = _mock_session()
    await mds.create_thesis_discovery_run(
        db,
        _thesis_payload(thesis_text="US semiconductor equipment", region=None),
        extractor=_fake_extractor(),
    )
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert cands
    for c in cands:
        assert c.thesis_relevance_score is not None
        assert c.combined_internal_score is not None
        assert c.thesis_match_json is not None
        assert c.thesis_match_json["internal_interest_label"] in INTERNAL_INTEREST_LABELS


@pytest.mark.asyncio
async def test_22_thesis_candidates_ranked_by_combined_score() -> None:
    db, added = _mock_session()
    await mds.create_thesis_discovery_run(
        db,
        _thesis_payload(thesis_text="US semiconductor equipment", region=None),
        extractor=_fake_extractor(),
    )
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    ranked = sorted(cands, key=lambda c: c.rank)
    scores = [c.combined_internal_score for c in ranked]
    assert scores == sorted(scores, reverse=True)


@pytest.mark.asyncio
async def test_23_thesis_candidate_uses_curated_identity_when_scan_sparse() -> None:
    # European defense -> non-US names -> sparse scan; curated registry supplies
    # the company name/sector (never fabricated).
    db, added = _mock_session()
    await mds.create_thesis_discovery_run(
        db, _thesis_payload(), extractor=_fake_extractor(strong=set())
    )
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert cands
    for c in cands:
        assert c.company_name  # filled from the curated universe item
        assert c.sector == "Industrials"


# ===========================================================================
# 5. Run Full Analysis from a thesis candidate
# ===========================================================================


@pytest.mark.asyncio
async def test_24_run_full_analysis_from_thesis_candidate() -> None:
    report_id = str(uuid.uuid4())
    agent_run_id = str(uuid.uuid4())

    async def fake_runner(db, **kwargs):
        return {
            "draft_report_id": report_id,
            "agent_run_id": agent_run_id,
            "status": "completed",
            "schema_valid": True,
            "safety_validation_json": {"passed": True},
        }

    candidate = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=uuid.uuid4(),
        ticker="AMAT",
        exchange="US",
        company_name="Applied Materials Inc.",
        raw_signal_json={"provider_name": "free_real"},
        human_review_required=True,
        is_public=False,
    )

    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    company = MagicMock()
    company.id = uuid.uuid4()

    import app.services.company_service as company_service

    orig_get = company_service.get_company_by_ticker
    orig_create = company_service.create_company
    company_service.get_company_by_ticker = AsyncMock(return_value=company)
    company_service.create_company = AsyncMock(return_value=company)
    mds.get_candidate = AsyncMock(return_value=candidate)  # type: ignore[assignment]
    try:
        result = await mds.run_candidate_analysis(
            db, candidate.id, run_analysis=fake_runner
        )
    finally:
        company_service.get_company_by_ticker = orig_get
        company_service.create_company = orig_create

    assert result["status"] == "completed"
    assert str(result["analysis_report_id"]) == report_id
    assert candidate.analysis_report_id == uuid.UUID(report_id)


# ===========================================================================
# 6. Safety
# ===========================================================================


@pytest.mark.asyncio
async def test_25_no_forbidden_terms_in_thesis_candidate_outputs() -> None:
    db, added = _mock_session()
    await mds.create_thesis_discovery_run(
        db,
        _thesis_payload(thesis_text="US semiconductor equipment", region=None),
        extractor=_fake_extractor(),
    )
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert cands
    for c in cands:
        blob = " ".join(
            [
                " ".join(c.labels_json or []),
                c.score_explanation or "",
                str((c.thesis_match_json or {}).get("explanation") or ""),
                str((c.thesis_match_json or {}).get("internal_interest_label") or ""),
                str((c.thesis_match_json or {}).get("relevance_reason") or ""),
            ]
        )
        assert _has_forbidden(blob) == [], blob


@pytest.mark.asyncio
async def test_26_thesis_candidates_internal_only_and_human_review() -> None:
    db, added = _mock_session()
    await mds.create_thesis_discovery_run(
        db,
        _thesis_payload(thesis_text="US semiconductor equipment", region=None),
        extractor=_fake_extractor(),
    )
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    for c in cands:
        assert c.human_review_required is True
        assert c.is_public is False


def test_27_no_public_publish_route_exists() -> None:
    from app.api.v1 import market_discovery as md_router

    paths = [r.path for r in md_router.router.routes]
    assert not any("publish" in p for p in paths)
    # The thesis-run creation route exists.
    assert any(p.endswith("/thesis-runs") for p in paths)


def test_28_parser_warnings_have_no_forbidden_terms() -> None:
    # Even the "needs narrowing" warnings we author must be recommendation-free.
    for txt in ["best stocks to buy", "top stocks", "random words"]:
        parsed = parse_thesis(txt)
        for w in parsed.warnings:
            assert _has_forbidden(w) == [], w
