"""
Phase 28B — run-level LLM discovery council.

All tests run with the deterministic FAKE discovery client only — no network,
no credentials. They cover the run evidence pack builder, the council
orchestrator (citations + safety + internal-action/run-quality labels), the
gating flags, the service storage under the run's existing config_json (no
migration), the API endpoints, regressions, and safe logging.
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import Settings
from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.services import market_discovery_service as mds
from app.services import safety_terms
from app.services.llm.discovery_citation_checker import check_and_sanitize
from app.services.llm.discovery_council import (
    get_discovery_llm_client,
    maybe_run_discovery_council,
)
from app.services.llm.discovery_evidence_pack import build_discovery_evidence_pack
from app.services.llm.discovery_schemas import (
    ALLOWED_INTERNAL_ACTIONS,
    ALLOWED_RUN_QUALITY,
    DISCOVERY_COUNCIL_AGENT_ORDER,
    DiscoveryCouncilAgentOutput,
)
from app.services.llm.fake_discovery_client import FakeDiscoveryLLMClient

FORBIDDEN_SUBSTRINGS = (
    "BUY",
    "SELL",
    "HOLD",
    "WATCH",
    "price target",
    "target price",
    "fair value",
    "intrinsic value",
    "upside of",
    "downside of",
    "undervalued",
    "overvalued",
)


# ---------------------------------------------------------------------------
# Factories (plain dicts — the pack/council take no ORM objects)
# ---------------------------------------------------------------------------


def _cfg(enabled: bool = True, max_candidates: int = 25) -> Settings:
    return Settings(
        llm_council_enabled=enabled,
        llm_discovery_council_enabled=enabled,
        llm_provider_council="fake",
        llm_discovery_council_max_candidates=max_candidates,
    )


def _cand(ticker: str, **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "candidate_id": str(uuid.uuid4()),
        "ticker": ticker,
        "exchange": "US",
        "company_name": f"{ticker} Corp",
        "country": "United States",
        "sector": "Technology",
        "industry": "Semiconductors",
        "thesis_relevance_score": 70.0,
        "combined_internal_score": 30.0,
        "candidate_score": 50.0,
        "candidate_score_grade": "medium_internal_interest",
        "momentum_score": 40,
        "catalyst_score": 10,
        "fundamentals_score": 20,
        "source_quality_score": 30,
        "data_completeness_score": 25,
        "risk_penalty_score": 5,
        "data_coverage": {
            "profile_source": "curated",
            "fundamentals_source": "sec",
            "sec_eligible": True,
            "reason": "US issuer",
            "requires_human_research": False,
        },
        "source_quality": "adequate",
        "missing_info_count": 1,
        "blocking_gap_count": 0,
        "catalyst_coverage_status": "limited",
        "momentum_label": "neutral",
        "positive_catalyst_count": 1,
        "high_strength_catalyst_count": 0,
        "filing_event_count": 1,
        "news_event_count": 0,
        "press_release_event_count": 0,
        "safety_valid": True,
        "human_review_required": True,
        "is_public": False,
        "warnings": [],
    }
    base.update(over)
    return base


def _swiss_cand(ticker: str, name: str, **over: Any) -> dict[str, Any]:
    return _cand(
        ticker,
        exchange="SW",
        company_name=name,
        country="Switzerland",
        sector="Consumer Discretionary",
        industry="Luxury Goods",
        data_coverage={
            "profile_source": "curated",
            "fundamentals_source": "not_sourced",
            "sec_eligible": False,
            "reason": "non-US issuer",
            "requires_human_research": True,
        },
        source_quality="limited",
        missing_info_count=4,
        **over,
    )


def _run(mode: str = "thesis", **over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": str(uuid.uuid4()),
        "mode": mode,
        "status": "completed",
        "thesis_text": "European luxury watch producers",
        "parsed_thesis": {"theme": "luxury_goods", "region": "Europe"},
        "config": {"region": "Europe", "country": None, "sector": "Consumer Discretionary"},
        "provider": "free_real",
        "lookback_days": 90,
        "universe_count": 5,
        "candidate_count": 3,
        "error_count": 0,
        "warnings": ["sparse data for one issuer"],
    }
    base.update(over)
    return base


def _europe_luxury() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    cands = [
        _swiss_cand("UHR", "Swatch Group"),
        _swiss_cand("CFR", "Richemont"),
        _cand(
            "MC",
            exchange="PA",
            company_name="LVMH",
            country="France",
            sector="Consumer Discretionary",
        ),
    ]
    return _run(candidate_count=len(cands)), cands


def _us_semis() -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tickers = ["AMAT", "LRCX", "KLAC", "TER"]
    cands = [_cand(t) for t in tickers]
    return _run(mode="ticker", thesis_text=None, parsed_thesis=None, candidate_count=4), cands


async def _run_fake(
    run: dict[str, Any],
    cands: list[dict[str, Any]],
    *,
    client: FakeDiscoveryLLMClient | None = None,
    max_candidates: int = 25,
):
    return await maybe_run_discovery_council(
        run=run,
        candidates=cands,
        run_id=run["run_id"],
        cfg=_cfg(max_candidates=max_candidates),
        client=client or FakeDiscoveryLLMClient(),
    )


# ===========================================================================
# Evidence pack
# ===========================================================================


def test_01_evidence_pack_europe_luxury() -> None:
    run, cands = _europe_luxury()
    pack = build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=25)
    assert pack.run.region == "Europe"
    assert pack.run.parsed_theme == "luxury_goods"
    assert pack.candidate_count == 3
    assert pack.run_facts  # thesis + universe + provider facts
    assert pack.do_not_infer and any("recommendation" in g for g in pack.do_not_infer)


def test_02_evidence_pack_swiss_strict_only_swiss() -> None:
    run = _run(country="Switzerland", config={"region": "Europe", "country": "Switzerland"})
    cands = [_swiss_cand("UHR", "Swatch Group"), _swiss_cand("CFR", "Richemont")]
    pack = build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=25)
    countries = {c.country for c in pack.candidates}
    assert countries == {"Switzerland"}
    tickers = {c.ticker for c in pack.candidates}
    assert "BA" not in tickers and "BOEING" not in {t.upper() for t in tickers}


def test_03_evidence_pack_us_semis() -> None:
    run, cands = _us_semis()
    pack = build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=25)
    tickers = {c.ticker for c in pack.candidates}
    assert {"AMAT", "LRCX", "KLAC", "TER"} <= tickers


def test_04_candidate_ids_stable_and_unique() -> None:
    run, cands = _us_semis()
    pack = build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=25)
    ids = [c.id for c in pack.candidates]
    assert ids == ["C1", "C2", "C3", "C4"]
    assert len(set(ids)) == len(ids)
    # Deterministic across rebuilds.
    pack2 = build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=25)
    assert [c.id for c in pack2.candidates] == ids


def test_05_pack_bounded_by_max_candidates() -> None:
    run = _run(candidate_count=30)
    cands = [_cand(f"T{i}") for i in range(30)]
    pack = build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=5)
    assert pack.candidate_count == 5
    assert any("Only the top 5 of 30" in g for g in pack.known_gaps)


def test_06_pack_includes_source_and_data_coverage_fields() -> None:
    run, cands = _europe_luxury()
    pack = build_discovery_evidence_pack(run=run, candidates=cands, max_candidates=25)
    swiss = next(c for c in pack.candidates if c.ticker == "UHR")
    assert swiss.data_coverage.get("profile_source") == "curated"
    assert swiss.data_coverage.get("fundamentals_source") == "not_sourced"
    assert swiss.data_coverage.get("sec_eligible") is False
    assert swiss.score_breakdown  # component scores present


# ===========================================================================
# Council orchestrator
# ===========================================================================


@pytest.mark.asyncio
async def test_07_fake_council_runs_all_agents() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(run, cands)
    assert result.llm_used is True
    assert result.agents_completed == len(DISCOVERY_COUNCIL_AGENT_ORDER)
    assert result.agents_failed == 0
    assert {a.agent_name for a in result.agents} == set(DISCOVERY_COUNCIL_AGENT_ORDER)


@pytest.mark.asyncio
async def test_08_disabled_council_returns_disabled_result() -> None:
    run, cands = _europe_luxury()
    result = await maybe_run_discovery_council(
        run=run, candidates=cands, run_id=run["run_id"], cfg=_cfg(enabled=False)
    )
    assert result.llm_used is False
    assert result.to_storage_dict()["llm_used"] is False


@pytest.mark.asyncio
async def test_09_gating_requires_both_flags() -> None:
    assert get_discovery_llm_client(Settings()) is None
    assert get_discovery_llm_client(Settings(llm_council_enabled=True)) is None
    assert (
        get_discovery_llm_client(Settings(llm_discovery_council_enabled=True)) is None
    )
    client = get_discovery_llm_client(
        Settings(
            llm_council_enabled=True,
            llm_discovery_council_enabled=True,
            llm_provider_council="fake",
        )
    )
    assert isinstance(client, FakeDiscoveryLLMClient)


@pytest.mark.asyncio
async def test_10_single_agent_failure_isolated() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(run, cands, client=FakeDiscoveryLLMClient(mode="timeout"))
    # Every agent failed gracefully, but the council still returned (no crash).
    assert result.llm_used is True
    assert result.agents_failed == len(DISCOVERY_COUNCIL_AGENT_ORDER)
    assert result.agents_completed == 0


@pytest.mark.asyncio
async def test_11_invalid_citation_ids_flagged() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(
        run, cands, client=FakeDiscoveryLLMClient(bad_citation_agents={"run_coordinator"})
    )
    assert any("not present in the evidence pack" in w for w in result.warnings)
    # No dropped id survived into the stored output.
    coord = next(a for a in result.agents if a.agent_name == "run_coordinator")
    for rn in coord.run_notes:
        assert "R999" not in rn.citation_ids


@pytest.mark.asyncio
async def test_12_uncited_material_claim_flagged() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(
        run,
        cands,
        client=FakeDiscoveryLLMClient(uncited_agents={"diversity_anti_convergence"}),
    )
    agent = next(a for a in result.agents if a.agent_name == "diversity_anti_convergence")
    assert agent.unsupported_claims  # the un-cited claim was moved here


@pytest.mark.asyncio
async def test_13_forbidden_terms_quarantine_unsafe_output() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(
        run,
        cands,
        client=FakeDiscoveryLLMClient(forbidden_agents={"candidate_prioritization"}),
    )
    agent = next(
        a for a in result.agents if a.agent_name == "candidate_prioritization"
    )
    assert agent.status == "failed"
    # The forbidden term never survives — not even in the quarantine note.
    dumped = json.dumps(agent.model_dump())
    assert "BUY" not in dumped
    assert result.safety_valid is False


@pytest.mark.asyncio
async def test_14_chair_uses_only_allowed_labels() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(run, cands)
    assert result.run_quality in ALLOWED_RUN_QUALITY
    for agent in result.agents:
        for note in agent.candidate_notes:
            assert note.internal_action in ALLOWED_INTERNAL_ACTIONS


@pytest.mark.asyncio
async def test_15_16_no_forbidden_recommendation_or_valuation_language() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(run, cands)
    blob = json.dumps(result.to_storage_dict())
    for term in FORBIDDEN_SUBSTRINGS:
        assert term not in blob, f"forbidden term {term!r} leaked into stored review"
    # Safety scanner agrees (exempt the do_not_infer-style descriptors).
    hits = safety_terms.scan_value(
        result.to_storage_dict(), exempt_keys=frozenset({"do_not_infer"})
    )
    assert hits == []


@pytest.mark.asyncio
async def test_17_safety_valid_true_for_safe_output() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(run, cands)
    assert result.safety_valid is True


@pytest.mark.asyncio
async def test_18_safety_valid_false_for_unsafe_output() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(
        run, cands, client=FakeDiscoveryLLMClient(forbidden_agents={"run_red_team"})
    )
    assert result.safety_valid is False


def test_citation_checker_coerces_bad_internal_action() -> None:
    out = DiscoveryCouncilAgentOutput(
        agent_name="candidate_prioritization",
        candidate_notes=[
            {
                "candidate_ref": "C1",
                "internal_action": "STRONG_BUY_NOW",
                "rationale": "some sufficiently long rationale text",
                "citation_ids": ["C1"],
            }
        ],
    )
    sanitized, issues = check_and_sanitize(out, {"C1", "R1"}, {"C1"})
    assert sanitized.candidate_notes[0].internal_action in ALLOWED_INTERNAL_ACTIONS
    assert any("internal_action" in i for i in issues)


# ===========================================================================
# Storage / API
# ===========================================================================


def _orm_run(**over: Any) -> DiscoveryRun:
    run = DiscoveryRun(
        id=over.pop("id", uuid.uuid4()),
        status=over.pop("status", "completed"),
        provider_name="free_real",
        mode=over.pop("mode", "thesis"),
        universe_source="thesis_generated",
        universe_count=5,
        candidate_count=over.pop("candidate_count", 2),
        error_count=0,
        lookback_days=90,
        warnings=["sparse data"],
        config_json=over.pop("config_json", {"region": "Europe", "exchange": "US"}),
        thesis_text="European luxury watch producers",
        parsed_thesis_json={"theme": "luxury_goods", "region": "Europe"},
        human_review_required=True,
    )
    for k, v in over.items():
        setattr(run, k, v)
    return run


def _orm_candidate(run_id: uuid.UUID, ticker: str, **over: Any) -> DiscoveryCandidate:
    c = DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run_id,
        ticker=ticker,
        exchange=over.pop("exchange", "SW"),
        company_name=over.pop("company_name", f"{ticker} AG"),
        country=over.pop("country", "Switzerland"),
        sector="Consumer Discretionary",
        candidate_score=30.0,
        combined_internal_score=28.0,
        thesis_relevance_score=80.0,
        source_quality="limited",
        missing_info_count=3,
        raw_signal_json={"data_coverage": {"sec_eligible": False, "fundamentals_source": "not_sourced"}},
        safety_valid=True,
        human_review_required=True,
        is_public=False,
        warnings_json=[],
    )
    for k, v in over.items():
        setattr(c, k, v)
    return c


@pytest.mark.asyncio
async def test_19_post_council_review_stores_under_config_json() -> None:
    run = _orm_run()
    cands = [
        _orm_candidate(run.id, "UHR", company_name="Swatch Group"),
        _orm_candidate(run.id, "CFR", company_name="Richemont"),
    ]
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    with patch.object(
        mds, "list_candidates", AsyncMock(return_value=(cands, len(cands)))
    ):
        stored = await mds.run_discovery_council_review(
            db, run, cfg=_cfg(), client=FakeDiscoveryLLMClient()
        )
    assert stored["llm_used"] is True
    # Persisted under the existing config_json blob — no migration.
    assert run.config_json[mds.COUNCIL_STORAGE_KEY] == stored
    assert run.config_json["region"] == "Europe"  # existing config preserved
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_20_get_stored_council_review_roundtrip() -> None:
    review = {"llm_used": True, "run_quality": "adequate"}
    run = _orm_run(config_json={"region": "Europe", mds.COUNCIL_STORAGE_KEY: review})
    assert mds.get_stored_council_review(run) == review
    # Absent -> None.
    assert mds.get_stored_council_review(_orm_run(config_json={"region": "Europe"})) is None


@pytest.mark.asyncio
async def test_21_stored_review_has_no_secrets_or_prompts() -> None:
    run = _orm_run()
    cands = [_orm_candidate(run.id, "UHR")]
    db = AsyncMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    with patch.object(mds, "list_candidates", AsyncMock(return_value=(cands, 1))):
        stored = await mds.run_discovery_council_review(
            db, run, cfg=_cfg(), client=FakeDiscoveryLLMClient()
        )
    blob = json.dumps(stored).lower()
    for banned in (
        "api_key",
        "apikey",
        "authorization",
        "cookie",
        "basic ",
        "hard rules",  # system-prompt marker
        "you are the",  # system-prompt marker
        "evidence pack (untrusted",  # user-prompt marker
    ):
        assert banned not in blob, f"stored review leaked {banned!r}"


@pytest.mark.asyncio
async def test_22_disabled_service_raises_disabled_error() -> None:
    run = _orm_run()
    db = AsyncMock()
    with pytest.raises(mds.DiscoveryCouncilDisabledError):
        await mds.run_discovery_council_review(db, run, cfg=_cfg(enabled=False))


def test_23_no_publish_route_added() -> None:
    from app.main import app

    schema = app.openapi()
    md_paths = [p for p in schema["paths"] if "market-discovery" in p]
    assert any("council-review" in p for p in md_paths)
    assert not any("publish" in p for p in md_paths)


# --- API-level (client fixture) -------------------------------------------


@pytest.mark.asyncio
async def test_api_post_council_review_success(client, mock_db) -> None:
    run = _orm_run()
    stored = {
        "llm_used": True,
        "council_version": "v1",
        "provider": "fake",
        "run_quality": "adequate",
        "candidates_to_research_next": [{"ticker": "UHR", "exchange": "SW"}],
        "safety_valid": True,
        "human_review_required": True,
        "publication_ready": False,
    }
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "run_discovery_council_review", AsyncMock(return_value=stored)
    ):
        resp = await client.post(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["llm_used"] is True
    assert body["run_quality"] == "adequate"
    assert body["publication_ready"] is False
    assert body["human_review_required"] is True


@pytest.mark.asyncio
async def test_api_post_council_review_disabled_returns_409(client, mock_db) -> None:
    run = _orm_run()
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds,
        "run_discovery_council_review",
        AsyncMock(side_effect=mds.DiscoveryCouncilDisabledError("Discovery council is disabled.")),
    ):
        resp = await client.post(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 409
    assert "disabled" in resp.json()["detail"].lower()


@pytest.mark.asyncio
async def test_api_post_council_review_missing_run_404(client, mock_db) -> None:
    with patch.object(mds, "get_run", AsyncMock(return_value=None)):
        resp = await client.post(
            f"/api/v1/market-discovery/runs/{uuid.uuid4()}/council-review"
        )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_api_get_council_review_success(client, mock_db) -> None:
    review = {
        "llm_used": True,
        "council_version": "v1",
        "run_quality": "thin",
        "safety_valid": True,
    }
    run = _orm_run(config_json={"region": "Europe", mds.COUNCIL_STORAGE_KEY: review})
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        resp = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 200
    assert resp.json()["run_quality"] == "thin"


@pytest.mark.asyncio
async def test_api_get_council_review_absent_404(client, mock_db) -> None:
    run = _orm_run(config_json={"region": "Europe"})
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        resp = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/council-review"
        )
    assert resp.status_code == 404


# ===========================================================================
# Regressions — the council faithfully reflects the candidate set only
# ===========================================================================


@pytest.mark.asyncio
async def test_24_european_watch_producers_council_ok() -> None:
    run, cands = _europe_luxury()
    result = await _run_fake(run, cands)
    assert result.llm_used is True
    seen = _bucket_tickers(result)
    assert "UHR" in seen and "CFR" in seen


@pytest.mark.asyncio
async def test_25_swiss_strict_council_only_swiss_names() -> None:
    run = _run(country="Switzerland", config={"region": "Europe", "country": "Switzerland"})
    cands = [_swiss_cand("UHR", "Swatch Group"), _swiss_cand("CFR", "Richemont")]
    result = await _run_fake(run, cands)
    seen = _bucket_tickers(result)
    # Only the Swiss candidates provided are ever referenced.
    assert seen <= {"UHR", "CFR"}
    blob = json.dumps(result.to_storage_dict())
    assert "Boeing" not in blob


@pytest.mark.asyncio
async def test_26_europe_defense_ba_lse_never_boeing() -> None:
    run = _run(
        thesis_text="European defense",
        parsed_thesis={"theme": "defense", "region": "Europe"},
        config={"region": "Europe"},
    )
    cands = [
        _cand(
            "BA",
            exchange="LSE",
            company_name="BAE Systems",
            country="United Kingdom",
            sector="Industrials",
        )
    ]
    result = await _run_fake(run, cands)
    blob = json.dumps(result.to_storage_dict())
    assert "Boeing" not in blob and "BOEING" not in blob
    seen = _bucket_tickers(result)
    assert seen <= {"BA"}


@pytest.mark.asyncio
async def test_27_us_semiconductor_council_reflects_all() -> None:
    run, cands = _us_semis()
    result = await _run_fake(run, cands)
    seen = _bucket_tickers(result)
    assert {"AMAT", "LRCX", "KLAC"} & seen  # top candidates surface


@pytest.mark.asyncio
async def test_28_manual_ticker_run_reflects_names() -> None:
    run = _run(mode="ticker", thesis_text=None, parsed_thesis=None, candidate_count=3)
    cands = [_cand("AAPL"), _cand("MSFT"), _cand("NVDA")]
    result = await _run_fake(run, cands)
    seen = _bucket_tickers(result)
    assert seen <= {"AAPL", "MSFT", "NVDA"}
    assert seen  # at least one placed


def _bucket_tickers(result) -> set[str]:
    seen: set[str] = set()
    for field in (
        "candidates_to_research_next",
        "candidates_to_monitor",
        "candidates_to_reject",
        "candidates_insufficient_data",
    ):
        for entry in getattr(result, field):
            if entry.get("ticker"):
                seen.add(entry["ticker"])
    return seen


# ===========================================================================
# Logging
# ===========================================================================


@pytest.mark.asyncio
async def test_29_council_logs_safe_metadata(caplog) -> None:
    run, cands = _europe_luxury()
    with caplog.at_level(logging.INFO, logger="app.services.llm.discovery_council"):
        await _run_fake(run, cands)
    text = caplog.text
    assert "discovery_council_started" in text
    assert "discovery_council_evidence_built" in text
    assert "discovery_council_agent_completed" in text
    assert "discovery_council_completed" in text
    assert "provider=fake" in text


@pytest.mark.asyncio
async def test_30_logs_have_no_prompts_completions_or_secrets(caplog) -> None:
    run, cands = _europe_luxury()
    with caplog.at_level(logging.INFO, logger="app.services.llm.discovery_council"):
        await _run_fake(run, cands)
    text = caplog.text.lower()
    for banned in (
        "hard rules",
        "you are the",
        "evidence pack (untrusted",
        "deterministic fake summary",
        "api_key",
        "authorization",
        "basic ",
    ):
        assert banned not in text, f"log leaked {banned!r}"
    # No forbidden rating token in the logs either.
    assert " buy" not in text.replace("buy signal", "")
