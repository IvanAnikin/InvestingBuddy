"""
Phase 19.2.1 — Staging deploy hardening + provider observability.

All tests run OFFLINE — no network calls, no API keys, no database.

Coverage:
  A. build_info metadata resolution (bundled file / env fallback / unknown)
  B. summarize_price_provider_warning() lifts fallback reasons out of meta.note
  C. compose_free_real_snapshot surfaces the Stooq→EODHD fallback into warnings
  D. both-price-providers-fail surfaces "no usable price history" into warnings
  E. provider warnings carry no secrets and no forbidden recommendation terms
  F. scoring_engine handles sector=None without crashing (normal sector still works)
"""

from __future__ import annotations

import asyncio
import json
import pathlib
from datetime import date, datetime, timedelta, timezone

# Forbidden public-recommendation vocabulary — must never appear in internal warnings.
_FORBIDDEN_TERMS = {
    "buy", "sell", "hold", "watch",
    "price target", "fair value", "upside", "downside",
    "shortlist", "reject",
}

# Secret *value* shapes that must never leak into surfaced warnings.
# NOTE: naming a config variable (e.g. "Configure EODHD_API_KEY") is a helpful
# hint, not a secret — we only flag actual credential/connection-string values.
# (We deliberately do NOT embed any real/example key literal in this file.)
_SECRET_SIGNATURES = (
    "postgresql+psycopg://",
    "postgresql://",
    "://investingbuddy:",   # db url with embedded credentials
    "-----begin",           # private key / publish profile block
    "publishurl=",          # publish profile field
    "userpwd=",             # publish profile credential
    "authorization: basic",  # basic-auth header value
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _price_with_note(num_points: int, note: str | None):
    """Build a PriceHistoryData with n points and a given meta.note (is_mock=False)."""
    from app.integrations.financial_data_provider import (
        DataQuality,
        PriceHistoryData,
        PricePoint,
        ProviderResponseMetadata,
        ProviderStatus,
        SourceTier,
    )

    points = []
    base = date(2023, 7, 1)
    price = 150.0
    for i in range(num_points):
        price *= 1.001
        points.append(PricePoint(date=(base + timedelta(days=i)).isoformat(), close=price))
    status = ProviderStatus.ok if num_points else ProviderStatus.error
    meta = ProviderResponseMetadata(
        provider_name="eodhd_price_only" if num_points else "free_real_price_fallback",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=status,
        note=note,
    )
    return PriceHistoryData(
        ticker="AAPL", exchange="NASDAQ", currency="USD",
        price_points=points, source_url=None,
        meta=meta,
        data_quality=DataQuality.B_single_credible if num_points else DataQuality.D_weak_or_stale,
    )


def _sec_fundamentals():
    """Real (is_mock=False) SEC EDGAR fundamentals from the AAPL fixture."""
    from app.integrations.financial_data_provider import (
        FundamentalsData,
        ProviderResponseMetadata,
        ProviderStatus,
        SourceTier,
    )
    from app.integrations.providers.sec_edgar_fundamentals import parse_company_facts

    fixture = pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_aapl.json"
    with open(fixture) as f:
        facts = json.load(f)
    dps, _ = parse_company_facts(facts, "AAPL", "320193")
    meta = ProviderResponseMetadata(
        provider_name="sec_edgar_fundamentals",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    return FundamentalsData(ticker="AAPL", exchange="NASDAQ", datapoints=dps, meta=meta)


def _assert_no_secrets_or_forbidden(texts: list[str]) -> None:
    for t in texts:
        low = t.lower()
        for sig in _SECRET_SIGNATURES:
            assert sig not in low, f"secret signature '{sig}' leaked into warning: {t}"
        for term in _FORBIDDEN_TERMS:
            # word-ish membership — the fallback strings never use these words
            assert term not in low, f"forbidden term '{term}' present in warning: {t}"


# ============================================================================
# A. build_info metadata resolution
# ============================================================================


class TestBuildInfo:
    def test_unknown_without_file_or_env(self, monkeypatch):
        from app.core import build_info

        for var in ("GIT_SHA", "BUILD_SHA", "BUILD_ID", "BUILD_TIME"):
            monkeypatch.delenv(var, raising=False)
        info = build_info._load_build_info()
        assert info["commit_sha"] == "unknown"
        assert info["build_id"] == "unknown"

    def test_env_fallback(self, monkeypatch):
        from app.core import build_info

        monkeypatch.setenv("GIT_SHA", "abc123def4567890")
        monkeypatch.setenv("BUILD_ID", "run-789")
        info = build_info._load_build_info()
        assert info["commit_sha"] == "abc123def4567890"
        assert info["build_id"] == "run-789"

    def test_reads_bundled_build_info_json(self, monkeypatch):
        """A build_info.json bundled next to the app is read at startup."""
        from app.core import build_info

        api_root = pathlib.Path(build_info.__file__).resolve().parents[2]  # apps/api
        marker = api_root / "build_info.json"
        pre_existing = marker.exists()
        try:
            marker.write_text(json.dumps({
                "commit_sha": "deadbeefcafe1234",
                "build_id": "42",
                "build_time": "2026-07-12T00:00:00Z",
            }))
            info = build_info._load_build_info()
            assert info["commit_sha"] == "deadbeefcafe1234"
            assert info["build_id"] == "42"
            assert info["build_time"] == "2026-07-12T00:00:00Z"
        finally:
            if not pre_existing and marker.exists():
                marker.unlink()

    def test_load_never_raises_on_bad_json(self, monkeypatch):
        from app.core import build_info

        api_root = pathlib.Path(build_info.__file__).resolve().parents[2]
        marker = api_root / "build_info.json"
        pre_existing = marker.exists()
        try:
            marker.write_text("{ this is not json")
            for var in ("GIT_SHA", "BUILD_SHA", "BUILD_ID", "BUILD_TIME"):
                monkeypatch.delenv(var, raising=False)
            info = build_info._load_build_info()
            # Falls through to env → unknown; must not raise.
            assert info["commit_sha"] == "unknown"
        finally:
            if not pre_existing and marker.exists():
                marker.unlink()


# ============================================================================
# B. summarize_price_provider_warning()
# ============================================================================


class TestSummarizePriceProviderWarning:
    def test_none_price_returns_none(self):
        from app.integrations.free_real_snapshot import summarize_price_provider_warning

        assert summarize_price_provider_warning(None) is None

    def test_stooq_success_no_note_returns_none(self):
        from app.integrations.free_real_snapshot import summarize_price_provider_warning

        price = _price_with_note(30, note=None)
        assert summarize_price_provider_warning(price) is None

    def test_benign_note_returns_none(self):
        from app.integrations.free_real_snapshot import summarize_price_provider_warning

        price = _price_with_note(30, note="EODHD free plan price-only mode; fundamentals unavailable.")
        assert summarize_price_provider_warning(price) is None

    def test_eodhd_fallback_success_surfaced(self):
        from app.integrations.free_real_snapshot import summarize_price_provider_warning

        note = (
            "Stooq price provider unavailable; falling back to EODHD price-only provider. "
            "(Stooq error: TimeoutError: timeout)"
        )
        price = _price_with_note(30, note=note)
        warning = summarize_price_provider_warning(price)
        assert warning == "Stooq price provider unavailable; used EODHD price-only fallback."

    def test_stooq_empty_fallback_surfaced(self):
        from app.integrations.free_real_snapshot import summarize_price_provider_warning

        note = "Stooq returned 0 price points for AAPL; falling back to EODHD price-only provider."
        price = _price_with_note(30, note=note)
        warning = summarize_price_provider_warning(price)
        assert warning == "Stooq price provider unavailable; used EODHD price-only fallback."

    def test_both_providers_fail_surfaced(self):
        from app.integrations.free_real_snapshot import summarize_price_provider_warning

        note = (
            "Stooq price provider unavailable; falling back to EODHD price-only provider. "
            "(Stooq error: TimeoutError); EODHD_API_KEY not configured; EODHD price-only "
            "fallback skipped; No usable price history available; trend signals unavailable."
        )
        price = _price_with_note(0, note=note)
        warning = summarize_price_provider_warning(price)
        assert warning == "No usable price history available; trend signals unavailable."

    def test_no_secrets_or_forbidden_terms(self):
        from app.integrations.free_real_snapshot import summarize_price_provider_warning

        note = "Stooq price provider unavailable; falling back to EODHD price-only provider."
        both = "No usable price history available; trend signals unavailable."
        outs = [
            summarize_price_provider_warning(_price_with_note(5, note=note)),
            summarize_price_provider_warning(_price_with_note(0, note=both)),
        ]
        _assert_no_secrets_or_forbidden([o for o in outs if o])


# ============================================================================
# C. compose_free_real_snapshot surfaces fallback warning into warnings
# ============================================================================


class TestComposerSurfacesFallback:
    def test_eodhd_fallback_reaches_snapshot_warnings(self):
        from app.integrations.free_real_snapshot import (
            CompanyIdentity,
            compose_free_real_snapshot,
        )

        identity = CompanyIdentity(
            ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ", country_domicile="US"
        )
        note = "Stooq price provider unavailable; falling back to EODHD price-only provider."
        price = _price_with_note(40, note=note)

        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, provider_stack="free_real")
        )
        assert snap.warnings, "provider warnings must not be empty when a fallback occurred"
        assert any("EODHD price-only fallback" in w for w in snap.warnings)
        # And it reaches the report-facing dict.
        assert any("EODHD price-only fallback" in w for w in snap.to_dict()["warnings"])

    def test_no_fallback_no_spurious_warning(self):
        from app.integrations.free_real_snapshot import (
            CompanyIdentity,
            compose_free_real_snapshot,
        )

        identity = CompanyIdentity(
            ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ", country_domicile="US"
        )
        price = _price_with_note(40, note=None)  # Stooq succeeded directly
        snap = asyncio.run(
            compose_free_real_snapshot(identity, price_data=price, provider_stack="free_real")
        )
        assert not any("fallback" in w.lower() for w in snap.warnings)

    def test_warnings_deduplicated(self):
        """extra_warnings + meta.note surfacing must not double-list the same reason."""
        from app.integrations.free_real_snapshot import (
            CompanyIdentity,
            compose_free_real_snapshot,
            summarize_price_provider_warning,
        )

        identity = CompanyIdentity(ticker="AAPL", legal_name="Apple Inc.", country_domicile="US")
        note = "Stooq price provider unavailable; falling back to EODHD price-only provider."
        price = _price_with_note(40, note=note)
        surfaced = summarize_price_provider_warning(price)

        snap = asyncio.run(
            compose_free_real_snapshot(
                identity, price_data=price, extra_warnings=[surfaced], provider_stack="free_real"
            )
        )
        matches = [w for w in snap.warnings if w == surfaced]
        assert len(matches) == 1, f"fallback warning duplicated: {snap.warnings}"


# ============================================================================
# D. both providers fail — SEC-only partial result still surfaces the reason
# ============================================================================


class TestBothFailPartialResult:
    def test_no_usable_price_warning_reaches_snapshot(self):
        from app.integrations.free_real_snapshot import (
            CompanyIdentity,
            compose_free_real_snapshot,
            summarize_price_provider_warning,
        )

        identity = CompanyIdentity(
            ticker="AAPL", legal_name="Apple Inc.", exchange="NASDAQ", country_domicile="US"
        )
        fund = _sec_fundamentals()
        empty = _price_with_note(
            0, note="No usable price history available; trend signals unavailable."
        )
        # Workflow surfaces the reason from the raw (empty) price object and passes
        # it as an extra warning while passing price_data=None to the composer.
        surfaced = summarize_price_provider_warning(empty)
        snap = asyncio.run(
            compose_free_real_snapshot(
                identity,
                price_data=None,
                fundamentals_data=fund,
                extra_warnings=[surfaced],
                provider_stack="free_real",
            )
        )
        # SEC data present → still a real (non-mock) partial snapshot, no crash.
        assert snap.is_mock is False
        assert any("No usable price history" in w for w in snap.warnings)
        _assert_no_secrets_or_forbidden(snap.warnings)


# ============================================================================
# F. scoring_engine sector=None robustness
# ============================================================================


def _snapshot_with_sector(sector):
    return {
        "company_identity": {"ticker": "AAPL", "legal_name": "Apple Inc."},
        "provider_metadata": {"source_tier": "T2_regulator_or_gov", "provider_name": "free_real"},
        "profile": {"sector": sector},
        "is_mock": False,
    }


class TestScoringSectorNone:
    def test_engine_sector_none_does_not_crash(self):
        from app.services.scoring_engine import ScoringEngine

        engine = ScoringEngine()
        # bull points are non-empty so theme alignment hits the sector join (line ~1019).
        bull = {"positive_thesis_points": ["clean energy growth", "strong balance sheet"]}
        result = engine.score_company_analysis(
            company_snapshot=_snapshot_with_sector(None), bull_case_summary=bull
        )
        assert 0 <= result.overall_score <= 100

    def test_engine_profile_none_does_not_crash(self):
        from app.services.scoring_engine import ScoringEngine

        engine = ScoringEngine()
        snap = _snapshot_with_sector(None)
        snap["profile"] = None  # provider omitted the profile entirely
        bull = {"positive_thesis_points": ["renewable expansion"]}
        result = engine.score_company_analysis(company_snapshot=snap, bull_case_summary=bull)
        assert 0 <= result.overall_score <= 100

    def test_engine_normal_sector_still_works(self):
        from app.services.scoring_engine import ScoringEngine

        engine = ScoringEngine()
        bull = {"positive_thesis_points": ["technology leadership", "cloud growth"]}
        result = engine.score_company_analysis(
            company_snapshot=_snapshot_with_sector("Technology"), bull_case_summary=bull
        )
        assert 0 <= result.overall_score <= 100

    def test_wrapper_sector_none_returns_real_scorecard(self):
        """The workflow wrapper produces a real scorecard (not the error fallback)."""
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        bull = {"positive_thesis_points": ["clean energy growth", "margin expansion"]}
        out = run_score_research_attractiveness(
            company_snapshot=_snapshot_with_sector(None), bull_case_summary=bull
        )
        # Error fallback returns an empty scores dict; a real run populates it.
        assert out["scores"], "sector=None must not fall back to the error scorecard"
        assert "not investment advice" in out["disclaimer"].lower()

    def test_scoring_output_has_no_forbidden_labels(self):
        from app.agents.analysis_council.score_research_attractiveness import (
            run_score_research_attractiveness,
        )

        bull = {"positive_thesis_points": ["clean energy growth"]}
        out = run_score_research_attractiveness(
            company_snapshot=_snapshot_with_sector(None), bull_case_summary=bull
        )
        assert out["internal_status"] not in {"BUY", "SELL", "HOLD", "WATCH"}
