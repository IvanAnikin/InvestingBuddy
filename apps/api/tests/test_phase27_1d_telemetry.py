"""
Phase 27.1D — Safe staging telemetry / logging.

Covers the redaction helpers, the structured-event formatter, the request
logging middleware, discovery run/candidate/failure events, report validation
events, and the invariant that NO publish route exists.

Every test asserts two things where relevant:
  1. the intended fields ARE logged (so staging validation is evidence-based), and
  2. secrets / full report bodies are NEVER logged.

All tests run OFFLINE — no network, no real DB, no LLM.
"""

from __future__ import annotations

import json
import logging
import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.core.log_redaction import (
    REDACTED,
    is_sensitive_key,
    redact_headers,
    redact_mapping,
    redact_url,
    redact_value,
)
from app.core.request_logging import route_family
from app.core.structured_logging import format_event, log_event
from app.models.discovery import DiscoveryRun
from app.services import market_discovery_service as mds
from app.services.final_report_generator import FinalReportGeneratorService

# ===========================================================================
# Redaction helpers
# ===========================================================================


@pytest.mark.parametrize(
    "name,expected",
    [
        ("Authorization", True),
        ("authorization", True),
        ("Cookie", True),
        ("set-cookie", True),
        ("x-api-key", True),
        ("api_key", True),
        ("eodhd_api_key", True),
        ("AUTH_GITHUB_SECRET", True),
        ("password", True),
        ("access_token", True),
        ("DATABASE_URL", True),
        ("staging_basic_auth", True),
        ("session", True),
        ("ticker", False),
        ("status", False),
        ("company_name", False),
        ("", False),
    ],
)
def test_is_sensitive_key(name: str, expected: bool) -> None:
    assert is_sensitive_key(name) is expected


def test_redact_headers_hides_credentials_keeps_names() -> None:
    headers = {
        "Authorization": "Bearer super-secret-token",
        "Cookie": "session=abc123",
        "X-API-Key": "sk-live-1234",
        "Content-Type": "application/json",
        "User-Agent": "smoke-check",
    }
    out = redact_headers(headers)
    assert out["Authorization"] == REDACTED
    assert out["Cookie"] == REDACTED
    assert out["X-API-Key"] == REDACTED
    # Non-sensitive headers pass through unchanged (names + values preserved).
    assert out["Content-Type"] == "application/json"
    assert out["User-Agent"] == "smoke-check"
    # No secret value survives anywhere in the output.
    joined = json.dumps(out)
    assert "super-secret-token" not in joined
    assert "abc123" not in joined
    assert "sk-live-1234" not in joined


def test_redact_headers_accepts_pairs_iterable() -> None:
    out = redact_headers([("authorization", "Basic Zm9v"), ("accept", "*/*")])
    assert out["authorization"] == REDACTED
    assert out["accept"] == "*/*"


def test_redact_mapping_recurses_and_redacts_known_secret_keys() -> None:
    data = {
        "password": "hunter2",
        "database_url": "postgresql+psycopg://u:p@host/db",
        "plain": "keep-me",
        "nested": {"api_key": "sk-abc", "count": 3, "authorization": "Bearer x"},
    }
    out = redact_mapping(data)
    assert out["password"] == REDACTED
    assert out["database_url"] == REDACTED
    assert out["plain"] == "keep-me"
    assert out["nested"]["api_key"] == REDACTED
    assert out["nested"]["authorization"] == REDACTED
    assert out["nested"]["count"] == 3
    joined = json.dumps(out)
    for secret in ("hunter2", "sk-abc", "u:p@host", "Bearer x"):
        assert secret not in joined


def test_redact_value() -> None:
    assert redact_value("api_key", "sk-123") == REDACTED
    assert redact_value("ticker", "AAPL") == "AAPL"


def test_redact_url_strips_token_query_values_only() -> None:
    url = "https://eodhd.com/api/eod/AAPL.US?api_token=SECRETTOKEN&period=d&order=a"
    out = redact_url(url)
    assert "SECRETTOKEN" not in out
    # The param NAME survives (so the log is still readable) but the value is gone.
    assert "api_token" in out
    # Path + non-secret params preserved.
    assert "/api/eod/AAPL.US" in out
    assert "period=d" in out
    assert "order=a" in out


def test_redact_url_no_query_unchanged() -> None:
    url = "https://ib-stg-api.azurewebsites.net/api/v1/market-discovery/runs"
    assert redact_url(url) == url


# ===========================================================================
# Structured event formatter
# ===========================================================================


def test_format_event_drops_none_and_quotes_spaces() -> None:
    msg = format_event(
        "discovery_run_started",
        {"run_id": "r1", "mode": "thesis", "country": None, "note": "two words"},
    )
    assert msg.startswith("discovery_run_started ")
    assert "run_id=r1" in msg
    assert "mode=thesis" in msg
    assert "country=" not in msg  # None dropped
    assert 'note="two words"' in msg  # spaces quoted


def test_format_event_redacts_sensitive_field_names() -> None:
    msg = format_event("evt", {"api_key": "sk-should-not-appear", "ticker": "AAPL"})
    assert "sk-should-not-appear" not in msg
    assert f"api_key={REDACTED}" in msg
    assert "ticker=AAPL" in msg


def test_format_event_collapses_newlines_to_single_line() -> None:
    msg = format_event("evt", {"error": "line1\nline2\rline3"})
    assert "\n" not in msg
    assert "\r" not in msg


def test_log_event_emits_single_line(caplog: pytest.LogCaptureFixture) -> None:
    logger = logging.getLogger("app.test.telemetry")
    with caplog.at_level(logging.INFO, logger="app.test.telemetry"):
        log_event(logger, "unit_event", foo="bar", secret="nope-token")
    records = [r for r in caplog.records if r.name == "app.test.telemetry"]
    assert records
    msg = records[-1].getMessage()
    assert msg.startswith("unit_event ")
    assert "foo=bar" in msg
    assert "nope-token" not in msg  # key contains "secret" → value redacted


# ===========================================================================
# route_family
# ===========================================================================


@pytest.mark.parametrize(
    "path,expected",
    [
        ("/", "root"),
        ("/health", "health"),
        ("/api/v1/market-discovery/runs", "market-discovery"),
        ("/api/v1/market-discovery/runs/abc-123", "market-discovery"),
        ("/api/version", "version"),
        ("/api/v1/final-reports/xyz/validate", "final-reports"),
    ],
)
def test_route_family(path: str, expected: str) -> None:
    assert route_family(path) == expected


# ===========================================================================
# Request logging middleware (uses the real app via the `client` fixture)
# ===========================================================================


async def test_request_logging_emits_method_path_status_duration(client, caplog) -> None:
    caplog.set_level(logging.INFO)
    resp = await client.get("/health")
    assert resp.status_code == 200
    records = [r for r in caplog.records if r.name == "app.request"]
    assert records, "no app.request log line emitted"
    msg = records[-1].getMessage()
    assert msg.startswith("http_request ")
    assert "method=GET" in msg
    assert "path=/health" in msg
    assert "status=200" in msg
    assert "duration_ms=" in msg
    assert "request_id=" in msg
    assert "route_family=health" in msg


async def test_request_logging_never_logs_authorization_or_cookie(client, caplog) -> None:
    caplog.set_level(logging.INFO)
    secret_token = "super-secret-bearer-value-9f8e7d"
    secret_cookie = "ib_session=deadbeefcafe"
    resp = await client.get(
        "/health",
        headers={
            "Authorization": f"Bearer {secret_token}",
            "Cookie": secret_cookie,
            "X-Request-ID": "trace-abc-123",
        },
    )
    assert resp.status_code == 200
    # Correlation id is honoured and echoed back.
    assert resp.headers.get("X-Request-ID") == "trace-abc-123"
    joined = "\n".join(r.getMessage() for r in caplog.records)
    assert secret_token not in joined
    assert secret_cookie not in joined
    # The honoured request id IS present (it is not a secret).
    assert "request_id=trace-abc-123" in joined


# ===========================================================================
# Discovery run telemetry
# ===========================================================================


def _signal(ticker: str = "AAPL", exchange: str = "US") -> dict:
    return {
        "ticker": ticker,
        "exchange": exchange,
        "provider_name": "free_real",
        "is_mock": False,
        "provider_failed": False,
        "error": None,
        "identity": {
            "legal_name": f"{ticker} Inc.",
            "company_name": f"{ticker} Inc.",
            "sector": "Technology",
        },
        "trend": {"momentum_label": "positive_momentum_candidate", "return_3m": 12.0},
        "fundamentals": {"available": True, "revenue_mln": 100.0},
        "market": {"latest_close": 190.0, "market_cap_mln": 3_000_000.0},
        "catalyst": {"coverage_status": "strong", "total_events": 4},
        "source_quality": {"overall": "strong", "source_tiers": {"T2_regulator_or_gov": 2}},
        "completeness": {"missing_fields": [], "missing_info_count": 0, "blocking_gap_count": 0},
        "data_coverage": {
            "sec_eligible": True,
            "profile_source": "sec_edgar",
            "fundamentals_source": "sec_edgar_xbrl",
            "reason": "sec_covered",
        },
        "warnings": [],
    }


def _extractor(*, raise_on: set[str] | None = None):
    raise_on = raise_on or set()

    async def _extract(db, *, ticker, exchange, provider_name, lookback_days, **kw):
        if ticker in raise_on:
            raise RuntimeError("boom")
        sig = _signal(ticker, exchange)
        return mds.ExtractedSignal(
            ticker=ticker,
            exchange=exchange,
            provider_name=provider_name,
            signal=sig,
            status="ok",
            schema_valid=False,
            safety_valid=True,
        )

    return _extract


def _mock_db_capture() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _make_run(tickers: list[str], *, mode: str = "ticker", exchange: str = "US") -> DiscoveryRun:
    return DiscoveryRun(
        id=uuid.uuid4(),
        status="pending",
        provider_name="free_real",
        mode=mode,
        universe_source="manual_tickers",
        universe_count=len(tickers),
        requested_tickers=list(tickers),
        processed_count=0,
        candidate_count=0,
        error_count=0,
        lookback_days=90,
        warnings=[],
        config_json={"exchange": exchange, "lookback_days": 90},
        parsed_thesis_json=None,
        universe_json=None,
        human_review_required=True,
        started_at=None,
    )


async def test_discovery_run_logs_started_candidate_and_terminal_status(caplog) -> None:
    caplog.set_level(logging.INFO)
    run = _make_run(["AAPL", "MSFT"])
    rid = run.id
    db = _mock_db_capture()

    await mds.process_run(db, run, extractor=_extractor())

    msgs = "\n".join(
        r.getMessage() for r in caplog.records
        if r.name == "app.services.market_discovery_service"
    )
    assert f"discovery_run_started run_id={rid}" in msgs
    assert "mode=ticker" in msgs
    assert "provider=free_real" in msgs
    assert "universe_size=2" in msgs
    # Per-candidate provenance line (no raw payloads).
    assert "discovery_candidate" in msgs
    assert "ticker=AAPL" in msgs
    assert "profile_source=sec_edgar" in msgs
    assert "sec_eligible=True" in msgs
    assert "human_review_required=True" in msgs
    # Terminal completion event carries the run_id + status + counts.
    assert f"discovery_run_completed run_id={rid}" in msgs
    assert "status=completed" in msgs
    assert "processed_count=2" in msgs
    assert "candidate_count=2" in msgs
    assert "error_count=0" in msgs
    assert "duration_ms=" in msgs


class _FakeFactory:
    def __init__(self, session: AsyncMock) -> None:
        self.session = session

    def __call__(self) -> "_FakeFactory":
        return self

    async def __aenter__(self) -> AsyncMock:
        return self.session

    async def __aexit__(self, *exc: object) -> bool:
        return False


async def test_discovery_run_failure_logs_run_id_and_safe_exception(caplog) -> None:
    caplog.set_level(logging.INFO)
    run = _make_run(["AAPL"])
    rid = run.id
    factory = _FakeFactory(_mock_db_capture())

    with (
        patch.object(mds, "get_run", AsyncMock(return_value=run)),
        patch.object(
            mds, "process_run", AsyncMock(side_effect=RuntimeError("safe-message"))
        ),
    ):
        await mds.process_discovery_run_by_id(rid, session_factory=factory)

    msgs = "\n".join(
        r.getMessage() for r in caplog.records
        if r.name == "app.services.market_discovery_service"
    )
    assert f"discovery_run_failed run_id={rid}" in msgs
    assert "exception_type=RuntimeError" in msgs
    assert "safe-message" in msgs
    # The run is still marked failed (safety unchanged).
    assert run.status == "failed"


# ===========================================================================
# Report validation telemetry
# ===========================================================================


def _report_with_content(content: dict) -> MagicMock:
    report = MagicMock()
    report.id = uuid.uuid4()
    report.content_markdown = "```json\n" + json.dumps(content) + "\n```"
    report.created_at = None
    report.safety_validation_json = None
    report.schema_validation_json = None
    return report


def _db_returning(report: MagicMock) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = report
    db.execute.return_value = result
    return db


async def test_report_validation_logs_booleans_not_full_text(caplog) -> None:
    caplog.set_level(logging.INFO)
    svc = FinalReportGeneratorService()
    body_sentinel = "REPORT-BODY-SENTINEL-must-not-be-logged"
    content = {
        "executive_summary": {
            "value": f"Internal research draft. Human review required. {body_sentinel}"
        }
    }
    report = _report_with_content(content)
    db = _db_returning(report)

    resp = await svc.validate_final_report(db, report.id)

    validation_lines = "\n".join(
        r.getMessage() for r in caplog.records if "report_validation" in r.getMessage()
    )
    assert validation_lines, "no report_validation event logged"
    assert f"report_id={report.id}" in validation_lines
    assert "schema_valid=" in validation_lines
    assert "safety_valid=" in validation_lines
    assert "research_complete=" in validation_lines
    # Safety invariants must be visible AND unchanged.
    assert "publication_ready=False" in validation_lines
    assert "human_review_required=True" in validation_lines
    assert "forbidden_terms_count=" in validation_lines
    assert "missing_required_sections_count=" in validation_lines

    # The report body text is NEVER logged (booleans/counts only).
    all_msgs = "\n".join(r.getMessage() for r in caplog.records)
    assert body_sentinel not in all_msgs

    # Response semantics unchanged.
    assert resp.human_review_required is True
    assert resp.publication_ready is False


# ===========================================================================
# Invariant: no publish route exists (Phase 27.1D adds none)
# ===========================================================================


def test_no_publish_route_added() -> None:
    from app.main import app

    publish_paths = [
        getattr(r, "path", "")
        for r in app.routes
        if "publish" in getattr(r, "path", "").lower()
    ]
    assert publish_paths == [], f"unexpected publish route(s): {publish_paths}"
