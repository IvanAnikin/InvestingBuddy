"""
Phase 25: Real Market Candidate Discovery — orchestration service.

Creates and executes bounded, internal-only market discovery runs, persists
ranked internal research candidates, and (on demand) promotes a candidate to the
full company-analysis workflow.

SAFETY (enforced here + in the model/schema/API layers):
  * INTERNAL ADMIN ONLY. No public output. No publishing.
  * Never launches an uncontrolled full-market scan — every run is validated
    against ``DISCOVERY_MAX_UNIVERSE_SIZE`` before any work begins.
  * Candidate scores are an internal prioritization signal only — never a
    recommendation, price target, fair value, or BUY/SELL/HOLD/WATCH label.
  * Every candidate is ``human_review_required=True`` and ``is_public=False``.
  * A per-ticker failure never fails the whole run.
  * CI-safe: the per-ticker signal extractor is injectable so tests never touch
    the network or run the real workflow.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import date, datetime, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.discovery import (
    ALLOWED_CANDIDATE_LABELS,
    DiscoveryCandidate,
    DiscoveryRun,
)
from app.schemas.company import CompanyCreate
from app.schemas.market_discovery import DiscoveryRunCreate, DiscoveryRunSummary
from app.services import company_service
from app.services.discovery_scoring_service import score_signal
from app.services.discovery_signal_extractor import ExtractedSignal, extract_signal
from app.workflows.company_analysis import run_company_analysis

logger = logging.getLogger(__name__)

# Injectable signal extractor type (tests supply canned signals, no network).
SignalExtractor = Callable[..., Awaitable[ExtractedSignal]]
AnalysisRunner = Callable[..., Awaitable[dict[str, Any]]]

_ALLOWED_PROVIDERS = {"free_real", "eodhd_free_real", "mock"}


# ---------------------------------------------------------------------------
# Safety scan — forbidden investment-action language
# ---------------------------------------------------------------------------

# Word-boundary patterns so we never false-positive on legitimate substrings
# (e.g. "household" for "hold"). Applied to the controlled candidate fields
# (labels + score explanation) which we author to be clean.
_FORBIDDEN_PATTERNS = [
    r"\bbuy\b",
    r"\bsell\b",
    r"\bhold\b",
    r"\bwatch\b",
    r"\bstrong buy\b",
    r"\boutperform\b",
    r"\bunderperform\b",
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
_FORBIDDEN_RE = [re.compile(p, re.IGNORECASE) for p in _FORBIDDEN_PATTERNS]


def scan_forbidden_terms(text: str) -> list[str]:
    """Return the list of forbidden investment-action terms found in ``text``."""
    if not text:
        return []
    found: list[str] = []
    for pattern in _FORBIDDEN_RE:
        m = pattern.search(text)
        if m:
            found.append(m.group(0).lower())
    return found


def scan_candidate_safety(candidate_payload: dict[str, Any]) -> list[str]:
    """
    Scan a candidate's user-facing text fields for forbidden terms.

    Returns a list of violations (empty when safe). Only the controlled fields
    (labels, score explanation, safe identity strings) are scanned.
    """
    parts: list[str] = []
    for label in candidate_payload.get("labels") or []:
        parts.append(str(label))
    if candidate_payload.get("score_explanation"):
        parts.append(str(candidate_payload["score_explanation"]))
    violations = scan_forbidden_terms(" ".join(parts))
    # Any label outside the allowed vocabulary is also a violation.
    for label in candidate_payload.get("labels") or []:
        if label not in ALLOWED_CANDIDATE_LABELS:
            violations.append(f"disallowed_label:{label}")
    return violations


# ---------------------------------------------------------------------------
# Universe resolution
# ---------------------------------------------------------------------------


def _normalize_tickers(raw: list[str], default_exchange: str) -> list[dict[str, str]]:
    seen: set[tuple[str, str]] = set()
    out: list[dict[str, str]] = []
    for item in raw:
        if not item:
            continue
        ticker = str(item).strip().upper()
        if not ticker:
            continue
        key = (ticker, default_exchange)
        if key in seen:
            continue
        seen.add(key)
        out.append({"ticker": ticker, "exchange": default_exchange})
    return out


def resolve_universe(payload: DiscoveryRunCreate) -> list[dict[str, str]]:
    """
    Resolve the (bounded) universe for a run.

    Raises ValueError on an empty universe or one exceeding the configured max
    size — this is the guardrail against an accidental full-market scan.
    """
    exchange = (payload.exchange or "US").strip().upper() or "US"
    source = payload.universe_source or "curated_seed"

    if source == "manual_tickers":
        raw = payload.tickers or []
        universe = _normalize_tickers(list(raw), exchange)
    else:  # curated_seed (default)
        seed = [t for t in settings.discovery_seed_universe.split(",")]
        universe = _normalize_tickers(seed, exchange)

    if not universe:
        raise ValueError(
            "Discovery universe is empty. Provide at least one ticker "
            "(manual_tickers) or configure DISCOVERY_SEED_UNIVERSE."
        )

    max_size = settings.discovery_max_universe_size
    if len(universe) > max_size:
        raise ValueError(
            f"Discovery universe size {len(universe)} exceeds the configured "
            f"maximum of {max_size}. Reduce the ticker list or raise "
            "DISCOVERY_MAX_UNIVERSE_SIZE (kept small on purpose to avoid an "
            "uncontrolled full-market scan)."
        )
    return universe


# ---------------------------------------------------------------------------
# Candidate persistence
# ---------------------------------------------------------------------------


def _parse_event_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:10]).date()
    except (TypeError, ValueError):
        return None


def _build_candidate(
    run_id: uuid.UUID,
    extracted: ExtractedSignal,
    score: dict[str, Any],
) -> DiscoveryCandidate:
    signal = extracted.signal
    identity = signal.get("identity") or {}
    trend = signal.get("trend") or {}
    fundamentals = signal.get("fundamentals") or {}
    market = signal.get("market") or {}
    catalyst = signal.get("catalyst") or {}
    source_quality = signal.get("source_quality") or {}
    completeness = signal.get("completeness") or {}

    candidate_payload = {
        "labels": score.get("labels") or [],
        "score_explanation": score.get("explanation"),
    }
    violations = scan_candidate_safety(candidate_payload)
    safety_valid = extracted.safety_valid if extracted.safety_valid is not None else True
    if violations:
        safety_valid = False

    return DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run_id,
        ticker=signal.get("ticker") or extracted.ticker,
        exchange=signal.get("exchange") or extracted.exchange,
        company_name=identity.get("company_name"),
        legal_name=identity.get("legal_name"),
        sector=identity.get("sector"),
        industry=identity.get("industry"),
        country=identity.get("country"),
        lei=identity.get("lei"),
        website=identity.get("website"),
        # scores
        candidate_score=score.get("candidate_score"),
        candidate_score_grade=score.get("candidate_score_grade"),
        momentum_score=score.get("momentum_score"),
        fundamentals_score=score.get("fundamentals_score"),
        catalyst_score=score.get("catalyst_score"),
        source_quality_score=score.get("source_quality_score"),
        data_completeness_score=score.get("data_completeness_score"),
        risk_penalty_score=score.get("risk_penalty_score"),
        labels_json=score.get("labels"),
        score_explanation=score.get("explanation"),
        # trend
        momentum_label=trend.get("momentum_label"),
        return_1m=trend.get("return_1m"),
        return_3m=trend.get("return_3m"),
        return_6m=trend.get("return_6m"),
        pct_above_ma50=trend.get("pct_above_ma50"),
        pct_above_ma200=trend.get("pct_above_ma200"),
        # catalysts
        catalyst_coverage_status=catalyst.get("coverage_status"),
        latest_catalyst_date=_parse_event_date(catalyst.get("latest_event_date")),
        positive_catalyst_count=int(catalyst.get("positive_count") or 0),
        high_strength_catalyst_count=int(catalyst.get("high_strength_count") or 0),
        press_release_event_count=int(catalyst.get("press_release_event_count") or 0),
        news_event_count=int(catalyst.get("news_event_count") or 0),
        filing_event_count=int(catalyst.get("filing_event_count") or 0),
        primary_or_regulator_event_count=int(
            catalyst.get("primary_or_regulator_event_count") or 0
        ),
        aggregator_only_event_count=int(catalyst.get("aggregator_only_count") or 0),
        # financials / market
        latest_close=market.get("latest_close"),
        market_cap_mln=market.get("market_cap_mln"),
        enterprise_value_mln=market.get("enterprise_value_mln"),
        pe_ratio=market.get("pe_ratio"),
        revenue_mln=fundamentals.get("revenue_mln"),
        revenue_growth_yoy_pct=fundamentals.get("revenue_growth_yoy_pct"),
        net_income_mln=fundamentals.get("net_income_mln"),
        free_cash_flow_mln=fundamentals.get("free_cash_flow_mln"),
        total_debt_mln=fundamentals.get("total_debt_mln"),
        cash_mln=fundamentals.get("cash_mln"),
        latest_annual_fy=fundamentals.get("latest_annual_fy"),
        # completeness / source
        source_quality=source_quality.get("overall"),
        missing_info_count=completeness.get("missing_info_count"),
        blocking_gap_count=completeness.get("blocking_gap_count"),
        source_tiers_json=source_quality.get("source_tiers"),
        warnings_json=signal.get("warnings"),
        missing_sources_json=catalyst.get("missing_sources"),
        missing_fields_json=completeness.get("missing_fields"),
        raw_signal_json=signal,
        snapshot_json=None,  # full snapshot kept out of the candidate row by default
        # workflow linkage (from the reused workflow run)
        analysis_report_id=(
            uuid.UUID(extracted.analysis_report_id)
            if extracted.analysis_report_id
            else None
        ),
        agent_run_id=(
            uuid.UUID(extracted.agent_run_id) if extracted.agent_run_id else None
        ),
        # safety
        human_review_required=True,
        is_public=False,
        safety_valid=safety_valid,
        schema_valid=extracted.schema_valid,
        safety_notes={"violations": violations} if violations else None,
    )


# ---------------------------------------------------------------------------
# Run creation + execution (synchronous MVP for a small bounded universe)
# ---------------------------------------------------------------------------


async def create_discovery_run(
    db: AsyncSession,
    payload: DiscoveryRunCreate,
    *,
    extractor: SignalExtractor | None = None,
) -> DiscoveryRun:
    """
    Create and synchronously execute a bounded discovery run.

    ``extractor`` is injectable for tests (defaults to the real signal
    extractor which reuses the company-analysis workflow).
    """
    provider = (payload.provider_name or settings.discovery_default_provider).strip()
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError(
            f"Provider '{provider}' is not permitted for discovery. "
            f"Allowed: {sorted(_ALLOWED_PROVIDERS)}."
        )

    universe = resolve_universe(payload)  # raises ValueError on invalid size
    lookback_days = payload.lookback_days or settings.discovery_lookback_days
    extract = extractor or extract_signal

    run = DiscoveryRun(
        id=uuid.uuid4(),
        status="running",
        provider_name=provider,
        universe_source=payload.universe_source or "curated_seed",
        universe_count=len(universe),
        requested_tickers=[u["ticker"] for u in universe],
        processed_count=0,
        candidate_count=0,
        error_count=0,
        lookback_days=lookback_days,
        warnings=[],
        config_json={
            "provider_name": provider,
            "universe_source": payload.universe_source or "curated_seed",
            "exchange": (payload.exchange or "US").upper(),
            "lookback_days": lookback_days,
            "max_universe_size": settings.discovery_max_universe_size,
            "max_concurrent_requests": settings.discovery_max_concurrent_requests,
            "notes": payload.notes,
        },
        safety_notes={
            "internal_only": True,
            "not_investment_advice": True,
            "no_public_publishing": True,
        },
        created_by=payload.created_by,
        human_review_required=True,
        started_at=datetime.now(timezone.utc),
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    # ── Process the universe (bounded, sequential; failures are non-blocking) ──
    warnings: list[str] = []
    error_count = 0
    processed = 0
    results: list[tuple[ExtractedSignal, dict[str, Any]]] = []

    for entry in universe:
        ticker = entry["ticker"]
        exchange = entry["exchange"]
        try:
            extracted = await extract(
                db,
                ticker=ticker,
                exchange=exchange,
                provider_name=provider,
                lookback_days=lookback_days,
            )
        except Exception as exc:  # defensive — never let one ticker fail the run
            logger.warning("Discovery extraction failed for %s: %s", ticker, exc)
            warnings.append(f"{ticker}: extraction error — {exc}")
            error_count += 1
            processed += 1
            continue

        processed += 1
        if extracted.status == "failed":
            error_count += 1
            if extracted.error:
                warnings.append(f"{ticker}: {extracted.error}")
        for w in extracted.signal.get("warnings") or []:
            warnings.append(f"{ticker}: {w}")

        score = score_signal(extracted.signal)
        results.append((extracted, score))

    # ── Rank by internal prioritization score (desc) and persist ──────────
    results.sort(key=lambda r: (r[1].get("candidate_score") or 0.0), reverse=True)
    candidate_count = 0
    for rank, (extracted, score) in enumerate(results, start=1):
        candidate = _build_candidate(run.id, extracted, score)
        candidate.rank = rank
        db.add(candidate)
        candidate_count += 1

    # ── Finalize run status ───────────────────────────────────────────────
    if processed == 0:
        final_status = "failed"
    elif error_count >= processed:
        final_status = "failed"
    elif error_count > 0 or warnings:
        final_status = "completed_with_warnings"
    else:
        final_status = "completed"

    run.status = final_status
    run.processed_count = processed
    run.candidate_count = candidate_count
    run.error_count = error_count
    run.warnings = warnings[:200]  # keep the JSON blob bounded
    run.completed_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(run)
    return run


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


async def list_runs(
    db: AsyncSession, *, limit: int = 50, offset: int = 0
) -> tuple[list[DiscoveryRun], int]:
    result = await db.execute(
        select(DiscoveryRun)
        .order_by(DiscoveryRun.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(result.scalars().all())
    return items, len(items)


async def get_run(db: AsyncSession, run_id: uuid.UUID) -> DiscoveryRun | None:
    result = await db.execute(select(DiscoveryRun).where(DiscoveryRun.id == run_id))
    return result.scalar_one_or_none()


async def list_candidates(
    db: AsyncSession,
    run_id: uuid.UUID,
    *,
    limit: int = 100,
    offset: int = 0,
    sort: str = "candidate_score",
    sector: str | None = None,
    grade: str | None = None,
    momentum_label: str | None = None,
    catalyst_coverage_status: str | None = None,
    source_quality: str | None = None,
    score_min: float | None = None,
    missing_info_max: int | None = None,
    has_press_releases: bool | None = None,
    has_news: bool | None = None,
    ticker: str | None = None,
) -> tuple[list[DiscoveryCandidate], int]:
    stmt = select(DiscoveryCandidate).where(
        DiscoveryCandidate.discovery_run_id == run_id
    )
    if sector:
        stmt = stmt.where(DiscoveryCandidate.sector == sector)
    if grade:
        stmt = stmt.where(DiscoveryCandidate.candidate_score_grade == grade)
    if momentum_label:
        stmt = stmt.where(DiscoveryCandidate.momentum_label == momentum_label)
    if catalyst_coverage_status:
        stmt = stmt.where(
            DiscoveryCandidate.catalyst_coverage_status == catalyst_coverage_status
        )
    if source_quality:
        stmt = stmt.where(DiscoveryCandidate.source_quality == source_quality)
    if score_min is not None:
        stmt = stmt.where(DiscoveryCandidate.candidate_score >= score_min)
    if missing_info_max is not None:
        stmt = stmt.where(DiscoveryCandidate.missing_info_count <= missing_info_max)
    if has_press_releases:
        stmt = stmt.where(DiscoveryCandidate.press_release_event_count > 0)
    if has_news:
        stmt = stmt.where(DiscoveryCandidate.news_event_count > 0)
    if ticker:
        stmt = stmt.where(DiscoveryCandidate.ticker == ticker.strip().upper())

    sort_map = {
        "rank": DiscoveryCandidate.rank.asc(),
        "candidate_score": DiscoveryCandidate.candidate_score.desc(),
        "latest_catalyst_date": DiscoveryCandidate.latest_catalyst_date.desc(),
        "momentum_score": DiscoveryCandidate.momentum_score.desc(),
        "catalyst_score": DiscoveryCandidate.catalyst_score.desc(),
        "fundamentals_score": DiscoveryCandidate.fundamentals_score.desc(),
        "created_at": DiscoveryCandidate.created_at.desc(),
    }
    stmt = stmt.order_by(sort_map.get(sort, DiscoveryCandidate.candidate_score.desc()))
    stmt = stmt.limit(limit).offset(offset)

    result = await db.execute(stmt)
    items = list(result.scalars().all())
    return items, len(items)


async def get_candidate(
    db: AsyncSession, candidate_id: uuid.UUID
) -> DiscoveryCandidate | None:
    result = await db.execute(
        select(DiscoveryCandidate).where(DiscoveryCandidate.id == candidate_id)
    )
    return result.scalar_one_or_none()


async def summarize_run(db: AsyncSession, run: DiscoveryRun) -> DiscoveryRunSummary:
    candidates, _ = await list_candidates(db, run.id, limit=500, sort="candidate_score")
    grade_breakdown: dict[str, int] = {}
    top_score: float | None = None
    for c in candidates:
        grade = c.candidate_score_grade or "unscored"
        grade_breakdown[grade] = grade_breakdown.get(grade, 0) + 1
        if c.candidate_score is not None:
            top_score = (
                c.candidate_score
                if top_score is None
                else max(top_score, c.candidate_score)
            )
    return DiscoveryRunSummary(
        run_id=run.id,
        status=run.status,
        universe_count=run.universe_count,
        processed_count=run.processed_count,
        candidate_count=run.candidate_count,
        error_count=run.error_count,
        top_candidate_score=top_score,
        grade_breakdown=grade_breakdown,
        warnings=list(run.warnings or []),
    )


# ---------------------------------------------------------------------------
# Promote a candidate to the full company-analysis workflow
# ---------------------------------------------------------------------------


async def run_candidate_analysis(
    db: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    run_analysis: AnalysisRunner | None = None,
) -> dict[str, Any]:
    """
    Run the full company-analysis workflow for a candidate and link the report.

    Reuses the existing workflow (injectable for tests). Returns a summary dict
    including the produced ``analysis_report_id`` and ``agent_run_id``.
    """
    candidate = await get_candidate(db, candidate_id)
    if candidate is None:
        raise ValueError(f"Discovery candidate {candidate_id} not found")

    runner = run_analysis or run_company_analysis
    provider = candidate.raw_signal_json.get("provider_name") if candidate.raw_signal_json else None
    provider = provider or settings.discovery_default_provider

    # Ensure the company exists so the workflow can resolve it.
    company = await company_service.get_company_by_ticker(
        db, candidate.ticker, candidate.exchange
    )
    if company is None:
        company = await company_service.create_company(
            db,
            CompanyCreate(
                ticker=candidate.ticker,
                exchange=candidate.exchange,
                name=candidate.company_name or candidate.ticker,
            ),
        )

    final_state = await runner(
        db,
        company_id=str(company.id),
        provider_name=provider,
    )

    report_id = final_state.get("draft_report_id")
    agent_run_id = final_state.get("agent_run_id")
    status = final_state.get("status", "completed")

    if report_id:
        candidate.analysis_report_id = uuid.UUID(report_id)
    if agent_run_id:
        candidate.agent_run_id = uuid.UUID(agent_run_id)
    candidate.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(candidate)

    return {
        "candidate_id": candidate.id,
        "ticker": candidate.ticker,
        "status": status,
        "analysis_report_id": uuid.UUID(report_id) if report_id else None,
        "agent_run_id": uuid.UUID(agent_run_id) if agent_run_id else None,
        "provider_name": provider,
    }
