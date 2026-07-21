"""
Phase 25: Real Market Candidate Discovery — signal extractor.

Gathers the per-ticker signals the discovery scorer needs by REUSING the
existing, tested company-analysis workflow (``run_company_analysis``). For a
small bounded universe this is the sanctioned path (see the Phase 25 plan):
it avoids duplicating provider/snapshot/catalyst logic at the cost of running
the full workflow (which also persists a draft report the candidate links to).

Design:
  * ``extract_signal`` accepts an injectable ``run_analysis`` callable so tests
    can supply canned final-state dicts and never touch the network or a real
    workflow. CI runs entirely offline.
  * ``map_state_to_signal`` is a PURE function (no DB, no network) that maps a
    workflow ``final_state`` into the flat signal dict consumed by
    ``discovery_scoring_service.score_signal`` and persisted on the candidate.

SAFETY: nothing here produces a recommendation, price target, fair value, or
investment-action label. Trend/momentum labels are T6 model-derived only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy.ext.asyncio import AsyncSession

from app.schemas.company import CompanyCreate
from app.services import company_service
from app.workflows.company_analysis import run_company_analysis

# Signature of the injectable analysis runner (defaults to run_company_analysis).
AnalysisRunner = Callable[..., Awaitable[dict[str, Any]]]


@dataclass
class ExtractedSignal:
    """Result of gathering signals for a single ticker."""

    ticker: str
    exchange: str
    provider_name: str
    signal: dict[str, Any]
    status: str = "ok"  # ok | failed
    error: str | None = None
    analysis_report_id: str | None = None
    agent_run_id: str | None = None
    snapshot: dict[str, Any] = field(default_factory=dict)
    safety_valid: bool | None = None
    schema_valid: bool | None = None


# ---------------------------------------------------------------------------
# Pure mapping: workflow final_state -> flat signal dict
# ---------------------------------------------------------------------------


def _num(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return None


def _fundamentals_stale(fiscal_year: Any) -> bool:
    """Latest annual fiscal year older than 1 full year is treated as stale."""
    try:
        fy = int(fiscal_year)
    except (TypeError, ValueError):
        return False
    return (datetime.now(timezone.utc).year - fy) > 1


def map_state_to_signal(
    final_state: dict[str, Any],
    *,
    ticker: str,
    exchange: str,
    provider_name: str,
) -> dict[str, Any]:
    """Map a company-analysis ``final_state`` into a flat discovery signal dict."""
    snapshot = final_state.get("company_snapshot") or {}
    identity = snapshot.get("company_identity") or {}
    profile = snapshot.get("profile") or {}
    fundamentals_summary = snapshot.get("fundamentals_summary") or {}
    market_metrics = snapshot.get("market_metrics_summary") or {}
    trend = snapshot.get("trend_signal_summary") or final_state.get(
        "trend_signal_summary"
    ) or {}
    missing_fields = list(snapshot.get("missing_fields") or [])

    provider_failed = final_state.get("status") == "failed"
    is_mock = bool(final_state.get("is_mock"))

    # ── Fundamentals ──────────────────────────────────────────────────────
    # Phase 27.1A: a venue SEC cannot cover is reported as unavailable and
    # flagged in missing_fields, so completeness/source-quality scores degrade
    # honestly instead of the candidate looking complete.
    coverage = final_state.get("data_coverage") or {}
    coverage_not_sourced = bool(coverage.get("requires_human_research"))
    if coverage_not_sourced:
        missing_fields.append("fundamentals_not_sourced_non_us_exchange")

    fundamentals_available = (not coverage_not_sourced) and (
        bool(final_state.get("fundamentals_available"))
        or (_num(fundamentals_summary.get("revenue_usd_m")) is not None)
    )
    fiscal_year = fundamentals_summary.get("fiscal_year")
    fundamentals = {
        "available": fundamentals_available,
        "stale": _fundamentals_stale(fiscal_year),
        "latest_annual_fy": (f"FY{fiscal_year}" if fiscal_year else None),
        "revenue_mln": _num(fundamentals_summary.get("revenue_usd_m")),
        "revenue_growth_yoy_pct": _num(
            fundamentals_summary.get("revenue_yoy_growth_pct")
        ),
        "net_income_mln": _num(fundamentals_summary.get("net_income_usd_m")),
        "free_cash_flow_mln": _num(fundamentals_summary.get("free_cash_flow_usd_m")),
        "total_debt_mln": _num(fundamentals_summary.get("total_debt_usd_m")),
        "cash_mln": _num(fundamentals_summary.get("cash_and_equivalents_usd_m")),
    }

    # ── Market metrics (derived, T6) ──────────────────────────────────────
    market = {
        "latest_close": _num(market_metrics.get("latest_close"))
        or _num(fundamentals_summary.get("latest_close")),
        "week52_high": _num(market_metrics.get("week52_high")),
        "week52_low": _num(market_metrics.get("week52_low")),
        "market_cap_mln": _num(market_metrics.get("market_cap_mln"))
        or _num(fundamentals_summary.get("market_cap_usd_m")),
        "enterprise_value_mln": _num(market_metrics.get("enterprise_value_mln"))
        or _num(fundamentals_summary.get("enterprise_value_usd_m")),
        "pe_ratio": _num(market_metrics.get("pe_ratio"))
        or _num(fundamentals_summary.get("pe_ratio")),
    }

    # ── Trend / momentum (T6) ─────────────────────────────────────────────
    trend_signal = {
        "momentum_label": trend.get("momentum_label"),
        "return_1m": _num(trend.get("return_1m")),
        "return_3m": _num(trend.get("return_3m")),
        "return_6m": _num(trend.get("return_6m")),
        "pct_above_ma50": _num(trend.get("pct_above_ma50")),
        "pct_above_ma200": _num(trend.get("pct_above_ma200")),
        "has_price_history": bool(trend.get("momentum_label"))
        and trend.get("momentum_label") != "insufficient_price_history",
    }

    # ── Catalysts ─────────────────────────────────────────────────────────
    catalyst_discovery = final_state.get("catalyst_discovery") or {}
    catalyst_summary = catalyst_discovery.get("summary") or {}
    catalyst = {
        "coverage_status": catalyst_discovery.get("coverage_quality")
        or final_state.get("catalyst_coverage_status"),
        "total_events": int(catalyst_summary.get("total_events") or 0),
        "positive_count": int(catalyst_summary.get("positive_count") or 0),
        "high_strength_count": int(catalyst_summary.get("high_strength_count") or 0),
        "primary_or_regulator_event_count": int(
            catalyst_summary.get("primary_or_regulator_event_count") or 0
        ),
        "aggregator_only_count": int(
            catalyst_summary.get("aggregator_only_count") or 0
        ),
        "press_release_event_count": int(
            catalyst_summary.get("press_release_event_count") or 0
        ),
        "news_event_count": int(catalyst_summary.get("news_event_count") or 0),
        "filing_event_count": int(catalyst_summary.get("filing_event_count") or 0),
        "latest_event_date": catalyst_summary.get("latest_event_date"),
        "warnings": list(catalyst_discovery.get("warnings") or []),
        "missing_sources": list(catalyst_discovery.get("missing_sources") or []),
    }

    # ── Source quality ────────────────────────────────────────────────────
    sq = final_state.get("source_quality_summary") or {}
    source_tiers: dict[str, int] = {}
    if fundamentals_available:
        source_tiers["T2_regulator_or_gov"] = source_tiers.get(
            "T2_regulator_or_gov", 0
        ) + 1
    if trend_signal["has_price_history"]:
        source_tiers["T5_api_aggregator"] = source_tiers.get(
            "T5_api_aggregator", 0
        ) + 1
        source_tiers["T6_model_estimate"] = source_tiers.get(
            "T6_model_estimate", 0
        ) + 1
    if catalyst["press_release_event_count"]:
        source_tiers["T1_primary_filing"] = source_tiers.get(
            "T1_primary_filing", 0
        ) + catalyst["press_release_event_count"]
    if catalyst["filing_event_count"]:
        source_tiers["T2_regulator_or_gov"] = source_tiers.get(
            "T2_regulator_or_gov", 0
        ) + catalyst["filing_event_count"]

    source_quality = {
        "overall": sq.get("overall_source_quality") or "insufficient",
        "strong_sources_count": len(sq.get("strong_sources") or []),
        "weak_sources_count": len(sq.get("weak_sources") or []),
        "aggregator_only_count": len(sq.get("aggregator_only_claims") or []),
        "source_tiers": source_tiers,
    }

    # ── Completeness ──────────────────────────────────────────────────────
    rc = final_state.get("research_completeness_summary") or {}
    blocking_gaps = list(rc.get("blocking_gaps") or [])
    missing_info_count = len(missing_fields) + len(rc.get("missing_required_fields") or [])
    completeness = {
        "missing_fields": missing_fields,
        "missing_info_count": missing_info_count,
        "blocking_gap_count": len(blocking_gaps),
    }

    # ── Aggregate warnings ────────────────────────────────────────────────
    warnings: list[str] = []
    warnings.extend(final_state.get("provider_warnings") or [])
    warnings.extend(catalyst["warnings"])
    warnings.extend(final_state.get("research_team_warnings") or [])

    return {
        "ticker": ticker.upper(),
        "exchange": exchange,
        "provider_name": provider_name,
        "is_mock": is_mock,
        "provider_failed": provider_failed,
        "error": final_state.get("error"),
        "identity": {
            "legal_name": identity.get("legal_name"),
            "company_name": final_state.get("company_name") or identity.get("legal_name"),
            "sector": profile.get("sector"),
            "industry": profile.get("industry"),
            "country": identity.get("country_domicile"),
            "lei": identity.get("lei"),
            "website": profile.get("website"),
        },
        "trend": trend_signal,
        "fundamentals": fundamentals,
        "market": market,
        "catalyst": catalyst,
        "source_quality": source_quality,
        "completeness": completeness,
        "data_coverage": final_state.get("data_coverage"),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Company resolution
# ---------------------------------------------------------------------------


async def _ensure_company(db: AsyncSession, ticker: str, exchange: str):
    """Return an existing company for (ticker, exchange) or create a stub one."""
    company = await company_service.get_company_by_ticker(db, ticker, exchange)
    if company is not None:
        return company
    return await company_service.create_company(
        db,
        CompanyCreate(ticker=ticker, exchange=exchange, name=ticker),
    )


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


async def extract_signal(
    db: AsyncSession,
    *,
    ticker: str,
    exchange: str,
    provider_name: str,
    lookback_days: int = 90,
    run_analysis: AnalysisRunner | None = None,
) -> ExtractedSignal:
    """
    Gather discovery signals for a single ticker.

    Reuses the company-analysis workflow (injectable via ``run_analysis`` for
    tests). Never raises — a per-ticker failure is captured on the result so the
    surrounding run can continue with the other tickers.
    """
    ticker_u = ticker.upper()
    runner = run_analysis or run_company_analysis

    try:
        company = await _ensure_company(db, ticker_u, exchange)
        final_state = await runner(
            db,
            company_id=str(company.id),
            provider_name=provider_name,
        )
    except Exception as exc:  # defensive — a bad ticker must not fail the run
        failed_signal = {
            "ticker": ticker_u,
            "exchange": exchange,
            "provider_name": provider_name,
            "is_mock": provider_name == "mock",
            "provider_failed": True,
            "error": str(exc),
            "identity": {},
            "trend": {"has_price_history": False},
            "fundamentals": {"available": False},
            "market": {},
            "catalyst": {"total_events": 0},
            "source_quality": {"overall": "insufficient"},
            "completeness": {"missing_info_count": 0, "blocking_gap_count": 0},
            "warnings": [f"Signal extraction failed: {exc}"],
        }
        return ExtractedSignal(
            ticker=ticker_u,
            exchange=exchange,
            provider_name=provider_name,
            signal=failed_signal,
            status="failed",
            error=str(exc),
        )

    signal = map_state_to_signal(
        final_state,
        ticker=ticker_u,
        exchange=exchange,
        provider_name=provider_name,
    )
    status = "failed" if final_state.get("status") == "failed" else "ok"

    safety_json = final_state.get("safety_validation_json") or {}

    return ExtractedSignal(
        ticker=ticker_u,
        exchange=exchange,
        provider_name=provider_name,
        signal=signal,
        status=status,
        error=final_state.get("error"),
        analysis_report_id=final_state.get("draft_report_id"),
        agent_run_id=final_state.get("agent_run_id"),
        snapshot=final_state.get("company_snapshot") or {},
        safety_valid=safety_json.get("passed") if safety_json else None,
        schema_valid=final_state.get("schema_valid"),
    )
