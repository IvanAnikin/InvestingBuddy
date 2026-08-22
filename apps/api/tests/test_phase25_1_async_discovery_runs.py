"""
Phase 25.1 — Async Discovery Run Execution tests.

Covers the split of discovery run creation (fast, committed immediately) from
background processing (own DB session, incremental progress, non-blocking
per-ticker failures) plus the API/polling surface and unchanged safety rules.

All tests run OFFLINE — the signal extractor is injected/patched, and no
background task ever opens a real DB session (the app session factory is either
patched away or replaced with an in-memory fake).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.schemas.market_discovery import DiscoveryRunCreate
from app.services import market_discovery_service as mds

_NOW = datetime.now(timezone.utc)

_FORBIDDEN = [
    "buy",
    "sell",
    "hold",
    "watch",
    "price target",
    "target price",
    "fair value",
    "intrinsic value",
    "upside",
    "downside",
    "undervalued",
    "overvalued",
    "recommendation",
]


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------


def _strong_signal(ticker: str = "AAPL") -> dict:
    return {
        "ticker": ticker,
        "exchange": "US",
        "provider_name": "free_real",
        "is_mock": False,
        "provider_failed": False,
        "error": None,
        "identity": {
            "legal_name": f"{ticker} Inc.",
            "company_name": f"{ticker} Inc.",
            "sector": "Technology",
        },
        "trend": {
            "momentum_label": "positive_momentum_candidate",
            "return_3m": 12.0,
            "has_price_history": True,
        },
        "fundamentals": {"available": True, "revenue_mln": 100.0},
        "market": {"latest_close": 190.0, "market_cap_mln": 3_000_000.0},
        "catalyst": {
            "coverage_status": "strong",
            "total_events": 4,
            "press_release_event_count": 2,
            "filing_event_count": 2,
        },
        "source_quality": {"overall": "strong", "source_tiers": {"T2_regulator_or_gov": 2}},
        "completeness": {"missing_fields": [], "missing_info_count": 0, "blocking_gap_count": 0},
        "warnings": [],
    }


def _warn_signal(ticker: str = "ZZZ") -> dict:
    sig = _strong_signal(ticker)
    sig["warnings"] = ["price provider fallback used"]
    sig["catalyst"] = {"coverage_status": "filings_only", "total_events": 0}
    return sig


def _extracted(signal: dict, *, status: str = "ok") -> mds.ExtractedSignal:
    return mds.ExtractedSignal(
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


def _fake_extractor(mapping: dict[str, dict], *, fail: set[str] | None = None, raise_on: set[str] | None = None):
    fail = fail or set()
    raise_on = raise_on or set()

    async def _extract(db, *, ticker, exchange, provider_name, lookback_days):
        if ticker in raise_on:
            raise RuntimeError(f"boom for {ticker}")
        if ticker in fail:
            sig = _warn_signal(ticker)
            sig["provider_failed"] = True
            sig["error"] = "provider unavailable"
            return mds.ExtractedSignal(
                ticker=ticker,
                exchange=exchange,
                provider_name=provider_name,
                signal=sig,
                status="failed",
                error="provider unavailable",
            )
        sig = dict(mapping.get(ticker, _strong_signal(ticker)))
        sig["ticker"] = ticker
        return _extracted(sig)

    return _extract


def _mock_session_with_capture() -> tuple[AsyncMock, list]:
    added: list = []
    db = AsyncMock()
    db.add = MagicMock(side_effect=lambda o: added.append(o))
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db, added


def _pending_run(
    tickers: list[str],
    *,
    provider: str = "free_real",
    status: str = "pending",
    exchange: str = "US",
    lookback: int = 90,
    **over,
) -> DiscoveryRun:
    run = DiscoveryRun(
        id=over.pop("id", uuid.uuid4()),
        status=status,
        provider_name=provider,
        universe_source="manual_tickers",
        universe_count=len(tickers),
        requested_tickers=list(tickers),
        processed_count=0,
        candidate_count=0,
        error_count=0,
        lookback_days=lookback,
        warnings=[],
        config_json={"provider_name": provider, "exchange": exchange, "lookback_days": lookback},
        safety_notes={"internal_only": True},
        human_review_required=True,
        started_at=over.pop("started_at", None),
    )
    for k, v in over.items():
        setattr(run, k, v)
    return run


def _full_run(**over) -> DiscoveryRun:
    """A run with timestamps so DiscoveryRunRead can serialise it."""
    run = _pending_run(over.pop("tickers", ["AAPL", "MSFT", "NVDA"]), **{
        k: v for k, v in over.items() if k in {"provider", "status", "exchange", "lookback"}
    })
    run.processed_count = over.get("processed_count", 0)
    run.candidate_count = over.get("candidate_count", 0)
    run.error_count = over.get("error_count", 0)
    run.universe_count = over.get("universe_count", run.universe_count)
    run.status = over.get("status", run.status)
    run.started_at = over.get("started_at", _NOW)
    run.completed_at = over.get("completed_at", None)
    run.created_at = _NOW
    run.updated_at = _NOW
    return run


def _payload(**over) -> DiscoveryRunCreate:
    base = {"universe_source": "manual_tickers", "tickers": ["AAPL", "MSFT"], "exchange": "US"}
    base.update(over)
    return DiscoveryRunCreate(**base)


class _FakeFactory:
    """Minimal async_sessionmaker stand-in returning a captured session."""

    def __init__(self, session):
        self.session = session
        self.entered = 0

    def __call__(self):
        return self

    async def __aenter__(self):
        self.entered += 1
        return self.session

    async def __aexit__(self, *exc):
        return False


# ===========================================================================
# create_pending_run — fast, commit-first, no processing
# ===========================================================================


@pytest.mark.asyncio
async def test_04_create_pending_run_commits_before_processing() -> None:
    db, added = _mock_session_with_capture()
    run = await mds.create_pending_run(db, _payload(tickers=["AAPL", "MSFT"]))
    assert run.status == "pending"
    assert run.processed_count == 0
    assert run.candidate_count == 0
    db.commit.assert_awaited()  # run row committed
    assert run in added
    # No candidates were created — processing has not happened yet.
    assert not [o for o in added if isinstance(o, DiscoveryCandidate)]


@pytest.mark.asyncio
async def test_16_create_pending_run_never_invokes_extractor() -> None:
    db, _ = _mock_session_with_capture()
    spy = AsyncMock(side_effect=AssertionError("extractor must not run inline"))
    with patch.object(mds, "extract_signal", spy):
        await mds.create_pending_run(db, _payload(tickers=["AAPL", "MSFT"]))
    spy.assert_not_called()  # POST path does no per-ticker work


@pytest.mark.asyncio
async def test_18_oversized_universe_rejected_before_scheduling() -> None:
    db, _ = _mock_session_with_capture()
    big = [f"T{i}" for i in range(50)]
    with pytest.raises(ValueError, match="exceeds the configured maximum"):
        await mds.create_pending_run(db, _payload(tickers=big))
    db.add.assert_not_called()  # nothing persisted, nothing to schedule


@pytest.mark.asyncio
async def test_19_empty_universe_rejected_before_scheduling() -> None:
    db, _ = _mock_session_with_capture()
    with pytest.raises(ValueError, match="empty"):
        await mds.create_pending_run(db, _payload(tickers=[]))
    db.add.assert_not_called()


# ===========================================================================
# process_run — background processing on a loaded run
# ===========================================================================


@pytest.mark.asyncio
async def test_06_processing_sets_status_running() -> None:
    db, _ = _mock_session_with_capture()
    run = _pending_run(["AAPL"])
    seen: list[str] = []

    async def _extract(db_, *, ticker, exchange, provider_name, lookback_days):
        seen.append(run.status)  # status observed mid-processing
        return _extracted(_strong_signal(ticker))

    await mds.process_run(db, run, extractor=_extract)
    assert seen == ["running"]
    assert run.started_at is not None


@pytest.mark.asyncio
async def test_07_processing_updates_processed_count_per_ticker() -> None:
    db, _ = _mock_session_with_capture()
    run = _pending_run(["AAPL", "MSFT", "NVDA"])
    progression: list[int] = []

    async def _extract(db_, *, ticker, exchange, provider_name, lookback_days):
        progression.append(run.processed_count)  # count before this ticker
        return _extracted(_strong_signal(ticker))

    await mds.process_run(db, run, extractor=_extract)
    assert progression == [0, 1, 2]
    assert run.processed_count == 3
    # A commit happens per ticker (plus the running + finalize commits).
    assert db.commit.await_count >= 4


@pytest.mark.asyncio
async def test_08_processing_creates_candidates() -> None:
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL", "MSFT"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "MSFT": _strong_signal("MSFT")})
    await mds.process_run(db, run, extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert len(cands) == 2
    assert run.candidate_count == 2
    assert {c.rank for c in cands} == {1, 2}  # ranks assigned


@pytest.mark.asyncio
async def test_09_final_status_completed_when_all_ok() -> None:
    db, _ = _mock_session_with_capture()
    run = _pending_run(["AAPL", "MSFT"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "MSFT": _strong_signal("MSFT")})
    await mds.process_run(db, run, extractor=fake)
    assert run.status == "completed"
    assert run.error_count == 0
    assert run.completed_at is not None


@pytest.mark.asyncio
async def test_10_final_status_completed_with_warnings() -> None:
    db, _ = _mock_session_with_capture()
    run = _pending_run(["AAPL", "MSFT"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")}, fail={"MSFT"})
    await mds.process_run(db, run, extractor=fake)
    assert run.status == "completed_with_warnings"
    assert run.error_count == 1
    assert any("MSFT" in w for w in run.warnings)


@pytest.mark.asyncio
async def test_11_final_status_failed_when_all_fatal() -> None:
    db, _ = _mock_session_with_capture()
    run = _pending_run(["AAPL", "MSFT"])
    fake = _fake_extractor({}, fail={"AAPL", "MSFT"})
    await mds.process_run(db, run, extractor=fake)
    assert run.status == "failed"


@pytest.mark.asyncio
async def test_17_per_ticker_exception_is_non_blocking() -> None:
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL", "MSFT"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")}, raise_on={"MSFT"})
    await mds.process_run(db, run, extractor=fake)
    # AAPL still produced a candidate; MSFT's exception was captured as a warning.
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert len(cands) == 1
    assert run.processed_count == 2
    assert run.error_count == 1
    assert any("MSFT" in w and "error" in w.lower() for w in run.warnings)
    assert run.status == "completed_with_warnings"


@pytest.mark.asyncio
async def test_12_does_not_rerun_terminal_run() -> None:
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL"], status="completed")
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    result = await mds.process_run(db, run, extractor=fake)
    assert result.status == "completed"
    assert not [o for o in added if isinstance(o, DiscoveryCandidate)]
    db.commit.assert_not_awaited()  # nothing changed, nothing committed


@pytest.mark.asyncio
async def test_12b_does_not_start_second_worker_when_running() -> None:
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL"], status="running", started_at=datetime.now(timezone.utc))
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    await mds.process_run(db, run, extractor=fake)
    assert not [o for o in added if isinstance(o, DiscoveryCandidate)]


@pytest.mark.asyncio
async def test_12c_stale_running_run_is_restarted() -> None:
    db, added = _mock_session_with_capture()
    stale = datetime.now(timezone.utc) - timedelta(minutes=45)
    run = _pending_run(["AAPL"], status="running", started_at=stale)
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    await mds.process_run(db, run, extractor=fake)
    assert [o for o in added if isinstance(o, DiscoveryCandidate)]  # restarted
    assert run.status == "completed"


@pytest.mark.asyncio
async def test_13_no_duplicate_candidates_on_reprocess() -> None:
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL", "MSFT"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "MSFT": _strong_signal("MSFT")})
    await mds.process_run(db, run, extractor=fake)
    first = len([o for o in added if isinstance(o, DiscoveryCandidate)])
    # Second call: run is now terminal → guard returns early, no new candidates.
    await mds.process_run(db, run, extractor=fake)
    second = len([o for o in added if isinstance(o, DiscoveryCandidate)])
    assert first == 2
    assert second == 2


# ===========================================================================
# process_discovery_run_by_id — fresh, independent DB session
# ===========================================================================


@pytest.mark.asyncio
async def test_05_worker_uses_independent_session() -> None:
    worker_session, added = _mock_session_with_capture()
    factory = _FakeFactory(worker_session)
    run = _pending_run(["AAPL"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})

    async def _get_run(session, run_id):
        assert session is worker_session  # the fresh session, not a request one
        return run

    with patch.object(mds, "get_run", AsyncMock(side_effect=_get_run)):
        await mds.process_discovery_run_by_id(
            run.id, session_factory=factory, extractor=fake
        )
    assert factory.entered == 1
    assert run.status == "completed"
    assert [o for o in added if isinstance(o, DiscoveryCandidate)]


@pytest.mark.asyncio
async def test_05b_worker_missing_run_is_noop() -> None:
    worker_session, _ = _mock_session_with_capture()
    factory = _FakeFactory(worker_session)
    with patch.object(mds, "get_run", AsyncMock(return_value=None)):
        # Must not raise even though the run does not exist.
        await mds.process_discovery_run_by_id(uuid.uuid4(), session_factory=factory)
    assert factory.entered == 1


@pytest.mark.asyncio
async def test_11b_worker_marks_run_failed_on_fatal_error() -> None:
    worker_session, _ = _mock_session_with_capture()
    factory = _FakeFactory(worker_session)
    run = _pending_run(["AAPL"])

    # process_run raises a fatal error; the worker must mark the run failed.
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "process_run", AsyncMock(side_effect=RuntimeError("db exploded"))
    ):
        await mds.process_discovery_run_by_id(run.id, session_factory=factory)
    assert run.status == "failed"
    assert any("Fatal background processing error" in w for w in run.warnings)


@pytest.mark.asyncio
async def test_03_background_task_scheduled_with_run_id(client) -> None:
    run = _full_run(status="pending", tickers=["AAPL", "MSFT", "NVDA"])
    with patch.object(
        mds, "create_pending_run", AsyncMock(return_value=run)
    ), patch.object(mds, "process_discovery_run_task", AsyncMock()) as task:
        res = await client.post(
            "/api/v1/market-discovery/runs",
            json={"universe_source": "curated_seed"},
        )
    assert res.status_code == 201
    # Background task ran after the response with only the primitive run_id.
    task.assert_awaited_once_with(str(run.id))


# ===========================================================================
# API surface — fast POST, progress, polling while running
# ===========================================================================


@pytest.mark.asyncio
async def test_01_post_returns_before_processing(client) -> None:
    run = _full_run(status="pending", processed_count=0, candidate_count=0)
    with patch.object(
        mds, "create_pending_run", AsyncMock(return_value=run)
    ), patch.object(mds, "process_discovery_run_task", AsyncMock()):
        res = await client.post(
            "/api/v1/market-discovery/runs",
            json={"universe_source": "curated_seed"},
        )
    body = res.json()
    assert res.status_code == 201
    assert body["status"] == "pending"
    assert body["candidate_count"] == 0  # candidates need not exist yet


@pytest.mark.asyncio
async def test_02_post_response_has_progress_fields(client) -> None:
    run = _full_run(status="pending", processed_count=0, candidate_count=0)
    with patch.object(
        mds, "create_pending_run", AsyncMock(return_value=run)
    ), patch.object(mds, "process_discovery_run_task", AsyncMock()):
        res = await client.post(
            "/api/v1/market-discovery/runs",
            json={"universe_source": "curated_seed"},
        )
    body = res.json()
    assert body["processed_count"] == 0
    assert body["progress_pct"] == 0.0
    assert body["is_async"] is True
    assert body.get("message")


@pytest.mark.asyncio
async def test_14_get_run_exposes_progress_pct(client) -> None:
    run = _full_run(status="running", processed_count=1, universe_count=3)
    with patch.object(mds, "get_run", AsyncMock(return_value=run)):
        res = await client.get(f"/api/v1/market-discovery/runs/{run.id}")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "running"
    assert body["progress_pct"] == 33.3


@pytest.mark.asyncio
async def test_15_get_candidates_while_running(client) -> None:
    run = _full_run(status="running", processed_count=1, candidate_count=1, universe_count=3)
    cand = MagicMock(spec=DiscoveryCandidate)
    # Provide the fields DiscoveryCandidateRead needs via a real object.
    from tests.test_phase25_market_candidate_discovery import _candidate_obj

    cand = _candidate_obj(discovery_run_id=run.id)
    with patch.object(mds, "get_run", AsyncMock(return_value=run)), patch.object(
        mds, "list_candidates", AsyncMock(return_value=([cand], 1))
    ):
        res = await client.get(
            f"/api/v1/market-discovery/runs/{run.id}/candidates"
        )
    assert res.status_code == 200
    body = res.json()
    assert body["total"] == 1
    assert body["candidates"][0]["ticker"] == "AAPL"


@pytest.mark.asyncio
async def test_22_run_analysis_from_candidate_still_works(client) -> None:
    """An ALREADY-completed analysis job is returned as-is (no second council
    run) and still carries the candidate's own final-report id."""
    from tests.test_phase25_market_candidate_discovery import _candidate_obj

    cand = _candidate_obj()
    report_id = uuid.uuid4()
    envelope = {
        "status": "completed",
        "analysis_report_id": str(report_id),
        "agent_run_id": str(uuid.uuid4()),
        "provider_name": "free_real",
    }
    with patch.object(
        mds, "get_candidate", AsyncMock(return_value=cand)
    ), patch.object(
        mds, "start_candidate_analysis", AsyncMock(return_value=(envelope, False))
    ):
        res = await client.post(
            f"/api/v1/market-discovery/candidates/{cand.id}/run-analysis"
        )
    assert res.status_code == 202
    body = res.json()
    assert body["analysis_report_id"] == str(report_id)
    assert body["human_review_required"] is True


# ===========================================================================
# Safety — unchanged across the async refactor
# ===========================================================================


@pytest.mark.asyncio
async def test_20_safety_fields_on_every_candidate() -> None:
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL", "ZZZ"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "ZZZ": _warn_signal("ZZZ")})
    await mds.process_run(db, run, extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    assert cands
    assert all(c.human_review_required is True for c in cands)
    assert all(c.is_public is False for c in cands)


@pytest.mark.asyncio
async def test_21_no_forbidden_terms_in_candidate_output() -> None:
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL", "ZZZ"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL"), "ZZZ": _warn_signal("ZZZ")})
    await mds.process_run(db, run, extractor=fake)
    cands = [o for o in added if isinstance(o, DiscoveryCandidate)]
    for c in cands:
        blob = " ".join(str(x) for x in (c.labels_json or [])).lower()
        blob += " " + (c.score_explanation or "").lower()
        assert mds.scan_forbidden_terms(blob) == []
        for label in c.labels_json or []:
            tokens = label.lower().replace("_", " ").split()
            for term in _FORBIDDEN:
                assert term not in tokens


@pytest.mark.asyncio
async def test_23_validation_metadata_persists_on_candidates() -> None:
    # Final validation metadata (safety_valid / schema_valid) still flows through
    # onto every candidate after the async refactor (regression vs Phase 24.1.3).
    db, added = _mock_session_with_capture()
    run = _pending_run(["AAPL"])
    fake = _fake_extractor({"AAPL": _strong_signal("AAPL")})
    await mds.process_run(db, run, extractor=fake)
    cand = next(o for o in added if isinstance(o, DiscoveryCandidate))
    assert cand.safety_valid is True
    assert cand.schema_valid is False  # expected at this phase, not blocking
    assert cand.human_review_required is True
