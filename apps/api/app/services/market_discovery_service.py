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
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.core.structured_logging import log_event
from app.db.session import async_session_factory
from app.models.discovery import (
    ALLOWED_CANDIDATE_LABELS,
    DiscoveryCandidate,
    DiscoveryRun,
)
from app.models.report import Report
from app.schemas.market_discovery import (
    DiscoveryRunCreate,
    DiscoveryRunSummary,
    ReportLinkSummary,
    ThesisDiscoveryRunCreate,
)
from app.services import safety_terms
from app.services.discovery_filters import (
    canonical_country,
    canonical_region,
    is_supported_sector,
)
from app.services.discovery_filters import (
    get_supported_filters as _get_supported_filters,
)
from app.services.discovery_scoring_service import score_signal
from app.services.discovery_signal_extractor import (
    ExtractedSignal,
    ensure_company,
    extract_signal,
    is_placeholder_company_name,
)
from app.services.discovery_thesis_scoring import (
    INTERNAL_INTEREST_LABELS,
    compute_combined_internal_score,
)
from app.services.exchange_registry import region_for_country
from app.services.llm.discovery_council import maybe_run_discovery_council
from app.services.market_thesis_parser import (
    get_supported_themes as parser_supported_themes,
)
from app.services.market_thesis_parser import parse_thesis
from app.services.market_universe_builder import (
    THEME_COMPANY_REGISTRY,
    build_universe,
)
from app.services.sector_taxonomy import get_supported_sector_aliases
from app.workflows.company_analysis import run_company_analysis

logger = logging.getLogger(__name__)

# Injectable signal extractor type (tests supply canned signals, no network).
SignalExtractor = Callable[..., Awaitable[ExtractedSignal]]
AnalysisRunner = Callable[..., Awaitable[dict[str, Any]]]
# Phase 28A.1 — injectable final-report generator (tests supply a fake; the
# default routes to the Phase 28A FinalReportGeneratorService). Returns a
# FinalReportResponse.
FinalReportRunner = Callable[..., Awaitable[Any]]

_ALLOWED_PROVIDERS = {"free_real", "eodhd_free_real", "mock"}


# ---------------------------------------------------------------------------
# Safety scan — forbidden investment-action language
# ---------------------------------------------------------------------------

# Matching is delegated to the shared three-tier scanner in
# app.services.safety_terms — the single source of truth for every gate.
#
# This gate previously used case-insensitive word boundaries, which is stricter
# on ordinary English than the shared scanner: it rejected the legitimate
# phrases "watch industry" and "insiders hold 12%", and the bare word
# "recommendation" that compliant disclaimers require. Those are exactly the
# false positives Phase 27.1 exists to remove.


def scan_forbidden_terms(text: str) -> list[str]:
    """Return the list of forbidden investment-action terms found in ``text``."""
    if not text:
        return []
    return [
        hit.matched_text.lower() for hit in safety_terms.scan_text(text)
    ]


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
    # Phase 27 — thesis relevance explanation + interest label are also authored
    # by us and persisted, so scan them too.
    if candidate_payload.get("thesis_explanation"):
        parts.append(str(candidate_payload["thesis_explanation"]))
    thesis_label = candidate_payload.get("thesis_interest_label")
    if thesis_label:
        parts.append(str(thesis_label))
    violations = scan_forbidden_terms(" ".join(parts))
    # Any label outside the allowed vocabulary is also a violation.
    for label in candidate_payload.get("labels") or []:
        if label not in ALLOWED_CANDIDATE_LABELS:
            violations.append(f"disallowed_label:{label}")
    # The thesis interest label must be from the internal-only vocabulary.
    if thesis_label and thesis_label not in INTERNAL_INTEREST_LABELS:
        violations.append(f"disallowed_interest_label:{thesis_label}")
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
    *,
    thesis_item: dict[str, Any] | None = None,
) -> DiscoveryCandidate:
    signal = extracted.signal
    identity = signal.get("identity") or {}
    trend = signal.get("trend") or {}
    fundamentals = signal.get("fundamentals") or {}
    market = signal.get("market") or {}
    catalyst = signal.get("catalyst") or {}
    source_quality = signal.get("source_quality") or {}
    completeness = signal.get("completeness") or {}

    # ── Phase 27: thesis relevance + combined internal score (thesis runs) ─
    thesis_relevance_score: float | None = None
    combined_internal_score: float | None = None
    thesis_match_json: dict[str, Any] | None = None
    thesis_interest_label: str | None = None
    thesis_explanation: str | None = None
    if thesis_item is not None:
        thesis_relevance_score = thesis_item.get("relevance_score_pre_scan")
        combined = compute_combined_internal_score(
            thesis_relevance_score=thesis_relevance_score,
            discovery_score=score.get("candidate_score"),
            catalyst_score=score.get("catalyst_score"),
            source_quality_score=score.get("source_quality_score"),
            missing_info_count=completeness.get("missing_info_count"),
            discovery_grade=score.get("candidate_score_grade"),
        )
        combined_internal_score = combined["combined_internal_score"]
        thesis_interest_label = combined["internal_interest_label"]
        thesis_explanation = combined["explanation"]
        thesis_match_json = {
            "internal_interest_label": thesis_interest_label,
            "thesis_relevance_score": thesis_relevance_score,
            "combined_internal_score": combined_internal_score,
            "matched_keywords": thesis_item.get("matched_keywords") or [],
            "relevance_reason": thesis_item.get("relevance_reason"),
            "universe_source": thesis_item.get("universe_source"),
            "source_tier": thesis_item.get("source_tier"),
            "theme": thesis_item.get("theme"),
            "metadata_not_sourced": bool(thesis_item.get("metadata_not_sourced")),
            "explanation": thesis_explanation,
            "missing_data_penalty": combined["missing_data_penalty"],
        }

    candidate_payload = {
        "labels": score.get("labels") or [],
        "score_explanation": score.get("explanation"),
        "thesis_interest_label": thesis_interest_label,
        "thesis_explanation": thesis_explanation,
    }
    violations = scan_candidate_safety(candidate_payload)
    safety_valid = extracted.safety_valid if extracted.safety_valid is not None else True
    if violations:
        safety_valid = False

    # Prefer curated thesis-universe identity metadata when the live scan could
    # not source it (never fabricated — it comes from the curated registry).
    #
    # The name test is is_placeholder_company_name, NOT truthiness: the scan
    # creates a stub company row named after the ticker, so a truthiness check
    # sees "UHR" as a sourced name and never reaches the curated "Swatch Group
    # AG". See discovery_signal_extractor.is_placeholder_company_name.
    ticker_value = str(signal.get("ticker") or extracted.ticker or "")
    ci_name = identity.get("company_name")
    ci_sector = identity.get("sector")
    ci_industry = identity.get("industry")
    ci_country = identity.get("country")
    name_source: str | None = None
    name_source_tier: str | None = None
    curated_name = (thesis_item or {}).get("company_name")

    # Provenance must NOT be decided by "is the incoming name a bare ticker?".
    # ``ensure_company`` seeds the stub Company row with the curated name and the
    # workflow echoes that row back as ``identity.company_name``, so by the time
    # the scan returns a curated name no longer looks like a placeholder. It was
    # therefore credited to ``provider_profile`` — on staging, all eight European
    # luxury candidates reported a provider-sourced display name while their
    # provider profile was explicitly ``not_sourced``.
    #
    # Two independent signals decide this correctly:
    #   1. ``data_coverage.profile_source`` — the provider stating whether it
    #      sourced a profile at all. When it says ``not_sourced``, nothing in
    #      the identity block may be credited to the provider.
    #   2. The VALUE — a display name equal to the curated registry string came
    #      from the registry, whichever layer handed it over.
    # Only a name the provider produced *independently* (profile sourced AND
    # differing from the curated string) may be attributed to the provider.
    def _same_name(a: str | None, b: str | None) -> bool:
        return bool(a and b and a.strip().casefold() == b.strip().casefold())

    profile_source = (signal.get("data_coverage") or {}).get("profile_source")
    provider_profile_sourced = profile_source != "not_sourced"

    if (
        ci_name
        and provider_profile_sourced
        and not is_placeholder_company_name(ci_name, ticker_value)
        and not _same_name(ci_name, curated_name)
    ):
        name_source = "provider_profile"

    if thesis_item is not None:
        curated_applies = curated_name and (
            is_placeholder_company_name(ci_name, ticker_value)
            or _same_name(ci_name, curated_name)
            or not provider_profile_sourced
        )
        if curated_applies:
            ci_name = curated_name
            # Attributed to the curated registry — NOT to SEC or the provider.
            name_source = thesis_item.get("universe_source") or "curated_theme_registry"
            name_source_tier = (
                thesis_item.get("source_tier") or "T3_curated_reference_list"
            )
        ci_sector = ci_sector or thesis_item.get("sector")
        ci_industry = ci_industry or thesis_item.get("industry")
        ci_country = ci_country or thesis_item.get("country")

    # Mirror the resolved display name (and its provenance) into the persisted
    # signal so the candidate detail and the row agree. ``legal_name`` is left
    # exactly as the scan produced it — a curated display name is not evidence
    # of a legal name and must never be presented as SEC-sourced.
    if isinstance(signal.get("identity"), dict):
        signal["identity"]["company_name"] = ci_name
        signal["identity"]["company_name_source"] = name_source
        signal["identity"]["company_name_source_tier"] = name_source_tier
    if thesis_match_json is not None and thesis_item is not None:
        thesis_match_json["company_name"] = thesis_item.get("company_name")
        thesis_match_json["company_name_source"] = name_source
        thesis_match_json["company_name_source_tier"] = name_source_tier

    return DiscoveryCandidate(
        id=uuid.uuid4(),
        discovery_run_id=run_id,
        ticker=signal.get("ticker") or extracted.ticker,
        exchange=signal.get("exchange") or extracted.exchange,
        company_name=ci_name,
        legal_name=identity.get("legal_name"),
        sector=ci_sector,
        industry=ci_industry,
        country=ci_country,
        lei=identity.get("lei"),
        website=identity.get("website"),
        # scores
        candidate_score=score.get("candidate_score"),
        candidate_score_grade=score.get("candidate_score_grade"),
        # Phase 27 thesis relevance
        thesis_relevance_score=thesis_relevance_score,
        combined_internal_score=combined_internal_score,
        thesis_match_json=thesis_match_json,
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
# Run creation + async execution (Phase 25.1)
#
# A run is created and committed IMMEDIATELY (status="pending") so the POST
# endpoint can return a run_id fast — a multi-ticker free_real scan can exceed a
# gateway/proxy timeout when executed inline. The universe is then processed in
# the background by ``process_discovery_run_by_id`` using its OWN DB session,
# committing progress after every ticker so the admin UI can poll for status.
# ---------------------------------------------------------------------------

# A run in one of these states is finished — a worker must never reprocess it.
_TERMINAL_STATUSES = {"completed", "completed_with_warnings", "failed", "cancelled"}

# A run stuck in "running" longer than this is treated as abandoned (e.g. the
# process that owned it restarted — FastAPI BackgroundTasks are process-local
# and not durable) and may be restarted by a new worker.
_STALE_RUNNING_MINUTES = 30


def _aware(dt: datetime | None) -> datetime | None:
    """Return ``dt`` as a timezone-aware UTC datetime (assume UTC if naive)."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _run_universe(run: DiscoveryRun) -> list[dict[str, str]]:
    """
    Rebuild the (already-bounded) universe from a persisted run row.

    For a THESIS run the universe is read from ``universe_json`` so each item
    keeps its own exchange (a thesis universe can mix exchanges). For a ticker
    run the single run-level exchange applies to every ticker.
    """
    if run.mode == "thesis":
        items = ((run.universe_json or {}).get("items")) or []
        universe = [
            {
                "ticker": str(it.get("ticker")),
                "exchange": str(it.get("exchange") or "US"),
            }
            for it in items
            if it.get("ticker")
        ]
        if universe:
            return universe
    exchange = ((run.config_json or {}).get("exchange") or "US").upper()
    return [
        {"ticker": t, "exchange": exchange} for t in (run.requested_tickers or [])
    ]


def _thesis_context(run: DiscoveryRun) -> dict[str, dict[str, Any]]:
    """Map ticker -> its generated universe item for a thesis run (else empty)."""
    if run.mode != "thesis":
        return {}
    items = ((run.universe_json or {}).get("items")) or []
    return {str(it.get("ticker")): it for it in items if it.get("ticker")}


async def create_pending_run(
    db: AsyncSession, payload: DiscoveryRunCreate
) -> DiscoveryRun:
    """
    Validate and persist a discovery run WITHOUT processing it.

    Commits the ``discovery_runs`` row (status="pending") and returns quickly so
    the API can hand back a ``run_id`` immediately. Raises ``ValueError`` on an
    invalid provider, an empty universe, or one exceeding the configured max
    size — so an oversized/empty run is rejected BEFORE any background work is
    scheduled.
    """
    provider = (payload.provider_name or settings.discovery_default_provider).strip()
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError(
            f"Provider '{provider}' is not permitted for discovery. "
            f"Allowed: {sorted(_ALLOWED_PROVIDERS)}."
        )

    universe = resolve_universe(payload)  # raises ValueError on invalid size
    lookback_days = payload.lookback_days or settings.discovery_lookback_days

    run = DiscoveryRun(
        id=uuid.uuid4(),
        status="pending",
        mode="ticker",
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
        started_at=None,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


def get_supported_themes() -> dict[str, Any]:
    """
    Phase 27.1B — the themes/sectors an admin can actually build a universe for.

    Joins the parser's theme table (what will MATCH) with the curated registry
    (what companies actually BACK the theme), so the UI can never advertise a
    theme that parses but yields an empty universe. Pure, deterministic, no DB.
    """
    themes: list[dict[str, Any]] = []
    for theme in parser_supported_themes():
        entries = THEME_COMPANY_REGISTRY.get(str(theme["id"]), [])
        countries = sorted({e["country"] for e in entries if e.get("country")})
        regions = sorted(
            {r for r in (region_for_country(c) for c in countries) if r}
        )
        themes.append(
            {
                **theme,
                "regions": regions,
                "countries": countries,
                "universe_company_count": len(entries),
            }
        )

    examples: list[str] = []
    for theme in themes:
        for example in theme.get("examples") or []:
            if example not in examples:
                examples.append(example)

    return {
        "themes": themes,
        "sectors": get_supported_sector_aliases(),
        "examples": examples,
        "coverage_note": (
            "Thesis discovery runs against a bounded curated universe "
            "bootstrap, not a full-market scan. Only the themes listed here "
            "resolve to companies today, and each theme is backed by a small "
            "hand-curated list of real public issuers — it is not an "
            "exhaustive index of the segment. Results are internal research "
            "candidates requiring human review; they are not investment "
            "advice and carry no recommendation."
        ),
    }


def get_supported_filters() -> dict[str, Any]:
    """
    Phase 27.1C — canonical controlled-selector options for the thesis form.

    Region / Country / Sector / Industry are no longer arbitrary free text; the
    admin UI loads its allowed values from here (never hard-coded on the
    frontend), and the backend rejects anything outside them.
    """
    return _get_supported_filters()


async def create_pending_thesis_run(
    db: AsyncSession, payload: ThesisDiscoveryRunCreate
) -> DiscoveryRun:
    """
    Phase 27 — parse a market thesis, build a bounded real-company universe, and
    persist a ``pending`` thesis discovery run WITHOUT processing it.

    Returns quickly so the API can hand back a ``run_id`` immediately (the scan
    runs in the background). Raises ``ValueError`` when:
      * the provider is not permitted,
      * a Region/Country/Sector filter is not one of the supported options,
      * the thesis is too vague to bound a universe (needs narrowing), or
      * no company matched the thesis (empty universe).

    These are rejected BEFORE any background work is scheduled — never an
    accidental full-market scan.
    """
    provider = (payload.provider_name or settings.discovery_default_provider).strip()
    if provider not in _ALLOWED_PROVIDERS:
        raise ValueError(
            f"Provider '{provider}' is not permitted for discovery. "
            f"Allowed: {sorted(_ALLOWED_PROVIDERS)}."
        )

    # Phase 27.1C — controlled selectors. Reject any value outside the allowed
    # options (empty/None means "not specified" and is always allowed), and
    # canonicalize casing so "switzerland" filters against "Switzerland".
    if payload.region and not canonical_region(payload.region):
        raise ValueError("Region must be one of the supported options.")
    if payload.country and not canonical_country(payload.country):
        raise ValueError("Country must be one of the supported options.")
    if payload.sector and not is_supported_sector(payload.sector):
        raise ValueError("Sector must be one of the supported options.")
    region = canonical_region(payload.region) or payload.region
    country = canonical_country(payload.country) or payload.country

    parsed = parse_thesis(
        payload.thesis_text,
        region=region,
        country=country,
        sector=payload.sector,
        industry=payload.industry,
        industry_keywords=payload.industry_keywords,
        market_cap_bucket=payload.market_cap_bucket,
    )
    if parsed.needs_narrowing:
        raise ValueError(
            "Thesis needs narrowing before a bounded universe can be built: "
            + " ".join(parsed.warnings)
        )

    universe = build_universe(parsed.to_dict(), max_universe_size=payload.max_universe_size)
    if universe.needs_narrowing:
        raise ValueError(
            "Thesis needs narrowing: " + " ".join(universe.warnings)
        )
    if not universe.items:
        raise ValueError(
            "No companies matched this thesis in the curated registry. "
            + " ".join(universe.warnings)
        )

    lookback_days = payload.lookback_days or settings.discovery_lookback_days
    tickers = [str(it["ticker"]) for it in universe.items]

    run = DiscoveryRun(
        id=uuid.uuid4(),
        status="pending",
        mode="thesis",
        provider_name=provider,
        universe_source="thesis_generated",
        universe_count=len(tickers),
        requested_tickers=tickers,
        thesis_text=payload.thesis_text,
        parsed_thesis_json=parsed.to_dict(),
        universe_json=universe.to_dict(),
        processed_count=0,
        candidate_count=0,
        error_count=0,
        lookback_days=lookback_days,
        # Phase 27.1C — surface any explicit-vs-prompt conflict warnings from the
        # parser alongside the universe-build warnings (the explicit choice is
        # kept; the admin is told the prompt disagreed).
        warnings=list(universe.warnings) + list(parsed.warnings),
        config_json={
            "provider_name": provider,
            "universe_source": "thesis_generated",
            "mode": "thesis",
            "max_universe_size": payload.max_universe_size,
            "max_candidates": payload.max_candidates,
            "lookback_days": lookback_days,
            "region": payload.region,
            "country": payload.country,
            "sector": payload.sector,
            "industry": payload.industry,
            "market_cap_bucket": payload.market_cap_bucket,
            "notes": payload.notes,
        },
        safety_notes={
            "internal_only": True,
            "not_investment_advice": True,
            "no_public_publishing": True,
            "no_recommendation": True,
        },
        created_by=payload.created_by,
        human_review_required=True,
        started_at=None,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)
    return run


def _log_candidate(
    run_id: uuid.UUID, candidate: DiscoveryCandidate, signal: dict[str, Any]
) -> None:
    """Emit a compact per-candidate telemetry line (no raw payloads).

    Logs only provenance/eligibility booleans a staging operator needs to audit
    a run — never the full snapshot, fundamentals, or any secret.
    """
    identity = signal.get("identity") or {}
    coverage = signal.get("data_coverage") or {}
    log_event(
        logger,
        "discovery_candidate",
        run_id=run_id,
        ticker=candidate.ticker,
        exchange=candidate.exchange,
        company_name_source=identity.get("company_name_source"),
        profile_source=coverage.get("profile_source"),
        fundamentals_source=coverage.get("fundamentals_source"),
        sec_eligible=coverage.get("sec_eligible"),
        reason=coverage.get("reason"),
        safety_valid=candidate.safety_valid,
        human_review_required=candidate.human_review_required,
    )


async def process_run(
    db: AsyncSession,
    run: DiscoveryRun,
    *,
    extractor: SignalExtractor | None = None,
) -> DiscoveryRun:
    """
    Process an already-loaded discovery run to completion using ``db``.

    Idempotency / concurrency guards (see plan §8):
      * A run already in a terminal state is NOT reprocessed.
      * A run already "running" (and not stale) is left to its existing worker.

    Progress is committed after every ticker so a polling UI shows live counts.
    A per-ticker failure never fails the whole run. ``extractor`` is injectable
    for tests (defaults to the real signal extractor).
    """
    if run.status in _TERMINAL_STATUSES:
        logger.info(
            "Discovery run %s already in terminal state '%s' — skipping.",
            run.id,
            run.status,
        )
        return run
    if run.status == "running":
        started = _aware(run.started_at)
        if started is not None:
            age = datetime.now(timezone.utc) - started
            if age < timedelta(minutes=_STALE_RUNNING_MINUTES):
                logger.info(
                    "Discovery run %s already running (age %ss) — not starting a "
                    "second worker.",
                    run.id,
                    int(age.total_seconds()),
                )
                return run
            logger.warning(
                "Discovery run %s stale in 'running' for %s min — restarting.",
                run.id,
                int(age.total_seconds() // 60),
            )

    extract = extractor or extract_signal
    provider = run.provider_name
    lookback_days = run.lookback_days
    universe = _run_universe(run)
    thesis_ctx = _thesis_context(run)

    # ── Mark running and persist immediately so pollers see progress ──────
    run.status = "running"
    run.started_at = run.started_at or datetime.now(timezone.utc)
    run.updated_at = datetime.now(timezone.utc)
    await db.commit()

    parsed = run.parsed_thesis_json or {}
    log_event(
        logger,
        "discovery_run_started",
        run_id=run.id,
        mode=run.mode,
        provider=provider,
        universe_size=len(universe),
        max_universe=settings.discovery_max_universe_size,
        max_candidates=len(universe),
        lookback_days=lookback_days,
        region=parsed.get("region"),
        country=parsed.get("country"),
        sector=parsed.get("sector"),
        theme=parsed.get("theme"),
    )

    warnings: list[str] = list(run.warnings or [])
    error_count = 0
    processed = 0
    created: list[DiscoveryCandidate] = []

    for entry in universe:
        ticker = entry["ticker"]
        exchange = entry["exchange"]
        thesis_item = thesis_ctx.get(ticker)
        extract_kwargs: dict[str, Any] = {
            "ticker": ticker,
            "exchange": exchange,
            "provider_name": provider,
            "lookback_days": lookback_days,
        }
        # Only thesis runs carry a curated name. Passing it lets the extractor
        # name the stub company row properly instead of creating it as the bare
        # ticker — the stub name is what used to shadow the curated one.
        curated_name = (thesis_item or {}).get("company_name")
        if curated_name:
            extract_kwargs["company_name"] = curated_name
        try:
            extracted = await extract(db, **extract_kwargs)
        except Exception as exc:  # defensive — never let one ticker fail the run
            logger.warning("Discovery extraction failed for %s: %s", ticker, exc)
            warnings.append(f"{ticker}: extraction error — {exc}")
            error_count += 1
            processed += 1
            run.processed_count = processed
            run.error_count = error_count
            run.warnings = warnings[:200]
            run.updated_at = datetime.now(timezone.utc)
            await db.commit()
            continue

        processed += 1
        if extracted.status == "failed":
            error_count += 1
            if extracted.error:
                warnings.append(f"{ticker}: {extracted.error}")
        for w in extracted.signal.get("warnings") or []:
            warnings.append(f"{ticker}: {w}")

        score = score_signal(extracted.signal)
        candidate = _build_candidate(run.id, extracted, score, thesis_item=thesis_item)
        db.add(candidate)
        created.append(candidate)
        _log_candidate(run.id, candidate, extracted.signal)

        # ── Persist progress after each ticker (bounded warnings blob) ─────
        run.processed_count = processed
        run.candidate_count = len(created)
        run.error_count = error_count
        run.warnings = warnings[:200]
        run.updated_at = datetime.now(timezone.utc)
        await db.commit()

    # ── Rank by internal prioritization score (desc), in memory ───────────
    # Thesis runs rank by the blended combined_internal_score; ticker runs rank
    # by the Phase 25 discovery candidate_score.
    if run.mode == "thesis":
        rank_key = lambda c: (c.combined_internal_score or 0.0)  # noqa: E731
    else:
        rank_key = lambda c: (c.candidate_score or 0.0)  # noqa: E731
    for rank, candidate in enumerate(
        sorted(created, key=rank_key, reverse=True),
        start=1,
    ):
        candidate.rank = rank

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
    run.candidate_count = len(created)
    run.error_count = error_count
    run.warnings = warnings[:200]
    run.completed_at = datetime.now(timezone.utc)
    run.updated_at = datetime.now(timezone.utc)

    await db.commit()
    await db.refresh(run)

    started_at = _aware(run.started_at)
    completed_at = _aware(run.completed_at)
    duration_ms = (
        round((completed_at - started_at).total_seconds() * 1000, 2)
        if started_at and completed_at
        else None
    )
    log_event(
        logger,
        "discovery_run_completed",
        run_id=run.id,
        status=final_status,
        processed_count=processed,
        candidate_count=len(created),
        error_count=error_count,
        warning_count=len(warnings),
        duration_ms=duration_ms,
    )
    return run


async def process_discovery_run_by_id(
    run_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    extractor: SignalExtractor | None = None,
) -> None:
    """
    Background worker: load a run by id in a FRESH session and process it.

    Must NOT reuse the request-scoped session (the response has already been
    returned by the time this runs). Opens its own session from the app session
    factory (injectable for tests). On a fatal error the run is best-effort
    marked ``failed`` in a separate session so it never sticks in "running".
    """
    factory = session_factory or async_session_factory
    try:
        async with factory() as session:
            run = await get_run(session, run_id)
            if run is None:
                logger.warning(
                    "Discovery run %s not found for background processing.", run_id
                )
                return
            await process_run(session, run, extractor=extractor)
    except Exception as exc:  # noqa: BLE001 — must not crash the worker
        # Structured, secret-free failure event. The exception type + str(exc)
        # are safe to log (never headers/body/credentials); the full traceback
        # is emitted by logger.exception below for local debugging only.
        log_event(
            logger,
            "discovery_run_failed",
            level=logging.ERROR,
            run_id=run_id,
            exception_type=type(exc).__name__,
            error=str(exc),
        )
        logger.exception("Discovery run %s failed fatally: %s", run_id, exc)
        try:
            async with factory() as session:
                run = await get_run(session, run_id)
                if run is not None and run.status not in _TERMINAL_STATUSES:
                    warnings = list(run.warnings or [])
                    warnings.append(f"Fatal background processing error: {exc}")
                    run.status = "failed"
                    run.warnings = warnings[:200]
                    run.completed_at = datetime.now(timezone.utc)
                    run.updated_at = datetime.now(timezone.utc)
                    await session.commit()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to mark discovery run %s as failed.", run_id)


async def process_discovery_run_task(run_id: str) -> None:
    """
    FastAPI ``BackgroundTasks`` entry point.

    Takes only a primitive ``run_id`` (never an ORM object or the request
    session) and drives the fresh-session worker. Swallows exceptions so a
    background failure can never surface to (or crash) the request handler.
    """
    try:
        await process_discovery_run_by_id(uuid.UUID(run_id))
    except Exception:  # noqa: BLE001
        logger.exception("Background discovery task crashed for run %s", run_id)


async def create_discovery_run(
    db: AsyncSession,
    payload: DiscoveryRunCreate,
    *,
    extractor: SignalExtractor | None = None,
) -> DiscoveryRun:
    """
    Create AND synchronously process a discovery run (create_pending_run +
    process_run) in a single call.

    Retained for offline tests and any caller that wants an inline run. The API
    no longer uses this path — it calls ``create_pending_run`` and schedules
    ``process_discovery_run_task`` so the request returns immediately.
    """
    run = await create_pending_run(db, payload)
    return await process_run(db, run, extractor=extractor)


async def create_thesis_discovery_run(
    db: AsyncSession,
    payload: ThesisDiscoveryRunCreate,
    *,
    extractor: SignalExtractor | None = None,
) -> DiscoveryRun:
    """
    Phase 27 — create AND synchronously process a thesis discovery run.

    Retained for offline tests and any caller that wants an inline run. The API
    uses ``create_pending_thesis_run`` + a background task so the request
    returns immediately.
    """
    run = await create_pending_thesis_run(db, payload)
    return await process_run(db, run, extractor=extractor)


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
        "combined_internal_score": DiscoveryCandidate.combined_internal_score.desc(),
        "thesis_relevance_score": DiscoveryCandidate.thesis_relevance_score.desc(),
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


async def get_report_for_candidate(
    db: AsyncSession, report_id: uuid.UUID
) -> Report | None:
    """Load a report row by id (used to summarise a candidate's linked report)."""
    result = await db.execute(select(Report).where(Report.id == report_id))
    return result.scalar_one_or_none()


def report_link_summary_from_report(report: Report | None) -> ReportLinkSummary | None:
    """
    Build a compact :class:`ReportLinkSummary` from a persisted report row.

    Phase 28A.1 — ``report_kind`` is derived from ``final_report_version``: a
    report written by the final-report generator always carries a version, so a
    NULL version marks a legacy deterministic "Phase 9" Analysis Council draft.
    LLM / council metadata is read from ``source_summary_json.llm_council`` and
    the validation flags from the persisted validation JSON — never fabricated.
    """
    if report is None:
        return None
    source_summary = report.source_summary_json or {}
    council = source_summary.get("llm_council") if isinstance(source_summary, dict) else None
    council = council if isinstance(council, dict) else {}
    schema_json = report.schema_validation_json or {}
    safety_json = report.safety_validation_json or {}
    is_final = bool(report.final_report_version)
    return ReportLinkSummary(
        report_id=report.id,
        report_kind="final" if is_final else "legacy",
        title=report.title,
        llm_used=bool(council.get("llm_used", False)),
        llm_provider=council.get("provider"),
        llm_model=council.get("model"),
        council_version=council.get("council_version"),
        agents_completed=council.get("agents_completed"),
        agents_failed=council.get("agents_failed"),
        evidence_item_count=council.get("evidence_item_count"),
        schema_valid=(schema_json.get("is_valid") if isinstance(schema_json, dict) else None),
        safety_valid=(safety_json.get("passed") if isinstance(safety_json, dict) else None),
        final_report_version=report.final_report_version,
        generated_at=report.created_at,
    )


def _summary_from_final_response(resp: Any) -> ReportLinkSummary:
    """Build a "final" report summary from a fresh FinalReportResponse."""
    from app.services.final_report_generator import FINAL_REPORT_VERSION

    return ReportLinkSummary(
        report_id=resp.report_id,
        report_kind="final",
        llm_used=bool(resp.llm_used),
        llm_provider=resp.llm_provider,
        llm_model=resp.llm_model,
        council_version=resp.council_version,
        agents_completed=resp.council_agents_completed,
        agents_failed=resp.council_agents_failed,
        evidence_item_count=resp.evidence_item_count,
        schema_valid=resp.schema_valid,
        safety_valid=resp.safety_valid,
        final_report_version=FINAL_REPORT_VERSION,
    )


async def _default_generate_final_report(db: AsyncSession, **kwargs: Any) -> Any:
    """Default final-report runner — the Phase 28A generator from live state."""
    from app.services.final_report_generator import FinalReportGeneratorService

    return await FinalReportGeneratorService().generate_from_workflow_state(db, **kwargs)


async def _load_final_report_inputs(
    db: AsyncSession, legacy_draft_id: str | None
) -> tuple[Report | None, list[Any], list[Any]]:
    """
    Best-effort load of the intermediate workflow draft plus its citations and
    sources, used as additional evidence for the final report. Never fatal — a
    failure here just means the final report is built from the in-memory state
    alone.
    """
    if not legacy_draft_id:
        return None, [], []
    try:
        from app.services.final_report_generator import (
            _load_citations_for_report,
            _load_report_by_id,
            _load_sources_for_citations,
        )

        source_report = await _load_report_by_id(db, uuid.UUID(legacy_draft_id))
        if source_report is None:
            return None, [], []
        citations = await _load_citations_for_report(db, source_report.id)
        sources = await _load_sources_for_citations(db, citations)
        return source_report, citations, sources
    except Exception:  # noqa: BLE001 - evidence enrichment is best-effort
        return None, [], []


async def run_candidate_analysis(
    db: AsyncSession,
    candidate_id: uuid.UUID,
    *,
    run_analysis: AnalysisRunner | None = None,
    generate_final_report: FinalReportRunner | None = None,
) -> dict[str, Any]:
    """
    Run the full analysis for a candidate and link the FINAL report.

    Phase 28A.1 — the single-company "Run Full Analysis" flow now routes through
    the Phase 28A final-report generator so the candidate links to a real final
    report (LLM analysis council when ``LLM_COUNCIL_ENABLED`` and a provider
    resolve; honest ``llm_used=False`` otherwise) — NEVER a legacy "Phase 9"
    deterministic Analysis Council draft. The deterministic workflow still runs
    first to produce the raw research artefact (retained as
    ``legacy_draft_report_id``); its output state feeds the final report.

    Both the workflow runner and the final-report generator are injectable for
    tests. If final-report generation fails the candidate falls back to linking
    the deterministic draft and a warning is surfaced — the run never fails
    purely because of the routing step.
    """
    candidate = await get_candidate(db, candidate_id)
    if candidate is None:
        raise ValueError(f"Discovery candidate {candidate_id} not found")

    runner = run_analysis or run_company_analysis
    provider = candidate.raw_signal_json.get("provider_name") if candidate.raw_signal_json else None
    provider = provider or settings.discovery_default_provider

    # Ensure the company exists so the workflow can resolve it. Routed through
    # the shared helper so a company row still stuck on its bare-ticker stub
    # name is upgraded to the candidate's resolved name before the full
    # analysis (and its report title) is generated. ``legal_name`` is a real,
    # sourced identity value populated earlier in the discovery pipeline and
    # takes precedence over ``company_name`` (which is sometimes just the
    # ticker) when present.
    company = await ensure_company(
        db,
        candidate.ticker,
        candidate.exchange,
        company_name=candidate.legal_name or candidate.company_name,
    )

    final_state = await runner(
        db,
        company_id=str(company.id),
        provider_name=provider,
    )

    legacy_draft_id = final_state.get("draft_report_id")
    agent_run_id = final_state.get("agent_run_id")
    status = final_state.get("status", "completed")

    warnings: list[str] = []
    report_summary: ReportLinkSummary | None = None
    linked_report_id: uuid.UUID | None = None

    company_record = {
        "id": str(company.id),
        "name": company.name,
        "ticker": company.ticker,
        "exchange": company.exchange,
        "country": getattr(company, "country", None),
        "sector": getattr(company, "sector", None),
        "industry": getattr(company, "industry", None),
    }
    try:
        gen = generate_final_report or _default_generate_final_report
        source_report, citations, sources = await _load_final_report_inputs(
            db, legacy_draft_id
        )
        # NOTE: the final-report generator's ``candidate`` arg is a
        # ScreeningCandidate (it reads ``candidate.name`` / discovery-rationale
        # fields). This flow has a DiscoveryCandidate — a different model — so
        # passing it would AttributeError and silently degrade every run to the
        # legacy draft. Identity + research data already come from
        # ``company_record`` (built from the Company) and the workflow state's
        # ``company_snapshot``, so we pass ``candidate=None``.
        #
        # Phase 32A Slice 6B (C2) — the real discovery-run lineage is NOT lost
        # though: build an additive, plain-dict lineage block straight off this
        # (real, ORM) DiscoveryCandidate + its parent DiscoveryRun, threaded
        # through separately so "No screening candidate is linked" no longer
        # hides a real discovery-run origin. Never inferred/fabricated — only
        # fields that already exist on the candidate/run.
        discovery_lineage: dict[str, Any] | None = None
        try:
            discovery_run = await get_run(db, candidate.discovery_run_id)
            discovery_lineage = {
                "discovery_run_id": str(candidate.discovery_run_id),
                "discovery_candidate_id": str(candidate.id),
                "ticker": candidate.ticker,
                "exchange": candidate.exchange,
                "rank": candidate.rank,
                "candidate_score": candidate.candidate_score,
                "candidate_score_grade": candidate.candidate_score_grade,
                "score_explanation": candidate.score_explanation,
                "thesis_relevance_score": candidate.thesis_relevance_score,
                "thesis_match_json": candidate.thesis_match_json,
                "thesis_text": discovery_run.thesis_text if discovery_run else None,
            }
        except Exception as exc:  # noqa: BLE001 - lineage is best-effort, never fatal
            logger.warning(
                "discovery_lineage_build_failed candidate=%s error=%s",
                str(candidate_id),
                type(exc).__name__,
            )
            discovery_lineage = None

        final_resp = await gen(
            db,
            state=final_state,
            company_record=company_record,
            candidate=None,
            source_report=source_report,
            citations=citations,
            sources=sources,
            discovery_lineage=discovery_lineage,
        )
        linked_report_id = final_resp.report_id
        report_summary = _summary_from_final_response(final_resp)
    except Exception as exc:  # noqa: BLE001 - never fail the whole run on routing
        logger.warning(
            "final_report_routing_failed candidate=%s error=%s",
            str(candidate_id),
            type(exc).__name__,
        )
        warnings.append("final_report_generation_failed")
        if legacy_draft_id:
            linked_report_id = uuid.UUID(legacy_draft_id)
            report_summary = ReportLinkSummary(
                report_id=linked_report_id,
                report_kind="legacy",
                llm_used=False,
            )

    if linked_report_id:
        candidate.analysis_report_id = linked_report_id
    if agent_run_id:
        candidate.agent_run_id = uuid.UUID(agent_run_id)
    candidate.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(candidate)

    return {
        "candidate_id": candidate.id,
        "ticker": candidate.ticker,
        "status": status,
        "analysis_report_id": linked_report_id,
        "agent_run_id": uuid.UUID(agent_run_id) if agent_run_id else None,
        "provider_name": provider,
        "report": report_summary,
        "legacy_draft_report_id": (
            uuid.UUID(legacy_draft_id) if legacy_draft_id else None
        ),
        "warnings": warnings,
    }


# ---------------------------------------------------------------------------
# Product readiness — ASYNC single-candidate "Run Full Analysis" job
# ---------------------------------------------------------------------------
#
# WHY THIS EXISTS. ``run_candidate_analysis`` above runs the whole pipeline
# (workflow → primary-document ingestion → 8-agent LLM council → final-report
# assembly) inline. On staging that regularly exceeds the Azure App Service
# gateway ceiling (~230s), so the ADMIN SAW HTTP 504 while the backend kept
# working and persisted a perfectly good final report. Worse, a retry/second
# click launched a SECOND expensive council run for the same candidate.
#
# The fix mirrors the Phase 28B.2 async discovery-council pattern exactly: the
# POST writes a ``pending`` job envelope, commits, returns 202 immediately, and
# a background task drives the real work in its OWN session. The UI polls a
# normal GET. Raising the gateway timeout is deliberately NOT the fix.
#
# The envelope lives under the candidate's existing ``raw_signal_json`` blob —
# additive key, no migration, same trick the council uses on
# ``DiscoveryRun.config_json``.

ANALYSIS_JOB_STORAGE_KEY = "analysis_job"

# A job in one of these states is in flight — a second click must NEVER start a
# duplicate council run.
_ANALYSIS_IN_FLIGHT = {"pending", "running"}

# A job in one of these states produced a linked report.
_ANALYSIS_HAS_RESULT = {"completed", "completed_with_warnings"}

# Fixed allowance (seconds) for everything in a full-analysis job that is NOT
# the council or primary-document ingestion: data fetching, snapshot build,
# report assembly/persistence, commits. Part of the derived stale threshold.
_ANALYSIS_JOB_OVERHEAD_SECONDS = 300.0

# Safety margin (minutes) on top of the derived worst-case job duration before
# a ``running`` envelope may be treated as abandoned.
_ANALYSIS_STALE_MARGIN_MINUTES = 10


def analysis_job_stale_after_minutes(cfg=None) -> int:
    """Effective abandoned-job threshold (minutes), coherent BY CONSTRUCTION.

    Phase 32A TPM slice: the council wall budget was raised for the async era
    (150s -> 600s) and paced attempts can wait for TPM headroom, so a fixed
    literal here could mark a legitimately long-running job stale mid-council.
    The threshold is therefore ``max(configured base, derived worst case)``
    where the worst case = council wall budget + one full pacing wait (an
    in-flight attempt can wait past the council deadline) + primary-document
    ingestion budget + fixed orchestration overhead + margin. Raising any
    council budget automatically raises this threshold with it. A job stuck in
    ``running`` longer than this is treated as abandoned (FastAPI
    BackgroundTasks are process-local and not durable — an app restart mid-run
    leaves the envelope in ``running`` forever otherwise).
    """
    cfg = cfg or settings
    worst_case_seconds = (
        float(cfg.llm_council_total_budget_seconds)
        + float(cfg.llm_council_pacing_max_wait_seconds)
        + float(cfg.primary_document_ingestion_budget_seconds)
        + _ANALYSIS_JOB_OVERHEAD_SECONDS
    )
    derived_minutes = int(worst_case_seconds // 60) + 1 + _ANALYSIS_STALE_MARGIN_MINUTES
    return max(int(cfg.analysis_job_stale_after_minutes), derived_minutes)


def _new_analysis_job_envelope(
    *,
    status: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    analysis_report_id: str | None = None,
    agent_run_id: str | None = None,
    legacy_draft_report_id: str | None = None,
    report: dict[str, Any] | None = None,
    provider_name: str | None = None,
    workflow_status: str | None = None,
    warnings: list[str] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build one analysis-job envelope. Plain JSON-safe values only."""
    return {
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "analysis_report_id": analysis_report_id,
        "agent_run_id": agent_run_id,
        "legacy_draft_report_id": legacy_draft_report_id,
        "report": report,
        "provider_name": provider_name,
        "workflow_status": workflow_status,
        "warnings": list(warnings or []),
        "error": error,
    }


def _store_analysis_job_envelope(
    candidate: DiscoveryCandidate, envelope: dict[str, Any]
) -> None:
    """Persist ``envelope`` under the candidate's ``raw_signal_json`` (no migration).

    Reassigns the whole dict so SQLAlchemy detects the JSONB change — an in-place
    mutation of a JSONB column is not tracked by default.
    """
    new_raw = dict(candidate.raw_signal_json or {})
    new_raw[ANALYSIS_JOB_STORAGE_KEY] = envelope
    candidate.raw_signal_json = new_raw


def get_analysis_job_envelope(
    candidate: DiscoveryCandidate,
) -> dict[str, Any] | None:
    """Return the candidate's analysis-job envelope, or None if none has run.

    ONLY an explicitly-stored envelope counts as a job.

    A pre-existing ``analysis_report_id`` must NEVER be read as "a full-analysis
    job already completed". The DISCOVERY pipeline itself sets that column: the
    signal extractor runs the deterministic company-analysis workflow for every
    candidate and links the Phase-9 draft it produces. Treating that as a
    finished job made "Run Full Analysis" short-circuit on every freshly
    discovered candidate — HTTP 202 in 0.3s with ``status=completed`` and no LLM
    council run at all (caught on staging, 2026-08-22, candidate
    ``3c4c9bc8`` linked to discovery-time draft ``b64d800b``).

    Returning None here is also the honest answer for a candidate analysed
    before this async migration: no JOB has run. The UI still renders the
    existing report link — ``GET /candidates/{id}`` already exposes
    ``analysis_report_id`` and ``latest_report`` independently of the job.
    """
    raw = candidate.raw_signal_json or {}
    stored = raw.get(ANALYSIS_JOB_STORAGE_KEY)
    if isinstance(stored, dict) and "status" in stored:
        return stored
    return None


def _analysis_job_is_stale(envelope: dict[str, Any]) -> bool:
    """True when a ``running`` job has clearly been abandoned by a dead worker."""
    if envelope.get("status") != "running":
        return False
    started = envelope.get("started_at")
    if not isinstance(started, str) or not started:
        # No timestamp to reason about — treat as in flight, never as stale.
        return False
    try:
        started_dt = _aware(datetime.fromisoformat(started))
    except ValueError:
        return False
    if started_dt is None:
        return False
    age = datetime.now(timezone.utc) - started_dt
    return age > timedelta(minutes=analysis_job_stale_after_minutes())


async def start_candidate_analysis(
    db: AsyncSession,
    candidate: DiscoveryCandidate,
    *,
    force: bool = False,
) -> tuple[dict[str, Any], bool]:
    """Start (or return the current state of) an async full-analysis job.

    Returns ``(envelope, scheduled)`` — ``scheduled`` tells the API whether to
    launch the background task. Never runs the analysis itself.

    Job-lifecycle rules (NO duplicate expensive council runs):
      * A pending/running job that is not stale → return it, ``scheduled=False``.
      * A stale ``running`` job (dead worker) → restartable.
      * A completed job and not ``force`` → return it, ``scheduled=False``.
      * Otherwise write a fresh ``pending`` envelope and return ``scheduled=True``.
    """
    envelope = get_analysis_job_envelope(candidate)
    status = (envelope or {}).get("status")

    if status in _ANALYSIS_IN_FLIGHT and not _analysis_job_is_stale(envelope or {}):
        log_event(
            logger,
            "candidate_analysis_job_duplicate",
            candidate_id=candidate.id,
            status=status,
        )
        return envelope or {}, False

    if status in _ANALYSIS_HAS_RESULT and not force:
        return envelope or {}, False

    provider = (
        (candidate.raw_signal_json or {}).get("provider_name")
        or settings.discovery_default_provider
    )
    started_at = datetime.now(timezone.utc).isoformat()
    pending = _new_analysis_job_envelope(
        status="pending", started_at=started_at, provider_name=provider
    )
    _store_analysis_job_envelope(candidate, pending)
    candidate.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(candidate)
    log_event(
        logger,
        "candidate_analysis_job_queued",
        candidate_id=candidate.id,
        status="pending",
    )
    return pending, True


def _analysis_job_envelope_from_result(
    result: dict[str, Any], *, started_at: str | None
) -> dict[str, Any]:
    """Build the terminal envelope for a completed ``run_candidate_analysis``."""
    warnings = list(result.get("warnings") or [])
    report_summary = result.get("report")
    report_dict: dict[str, Any] | None = None
    if report_summary is not None:
        report_dict = report_summary.model_dump(mode="json")
    status = "completed_with_warnings" if warnings else "completed"
    if result.get("analysis_report_id") is None:
        status = "failed"
    return _new_analysis_job_envelope(
        status=status,
        started_at=started_at,
        completed_at=datetime.now(timezone.utc).isoformat(),
        analysis_report_id=(
            str(result["analysis_report_id"])
            if result.get("analysis_report_id")
            else None
        ),
        agent_run_id=(
            str(result["agent_run_id"]) if result.get("agent_run_id") else None
        ),
        legacy_draft_report_id=(
            str(result["legacy_draft_report_id"])
            if result.get("legacy_draft_report_id")
            else None
        ),
        report=report_dict,
        provider_name=result.get("provider_name"),
        workflow_status=result.get("status"),
        warnings=warnings,
        error=None if result.get("analysis_report_id") else "no_report_produced",
    )


async def _mark_analysis_job_failed(
    session: AsyncSession,
    candidate: DiscoveryCandidate,
    *,
    reason: str,
) -> None:
    """Persist a ``failed`` envelope, never clobbering a completed result."""
    existing = get_analysis_job_envelope(candidate) or {}
    if existing.get("status") in _ANALYSIS_HAS_RESULT:
        return
    failed = _new_analysis_job_envelope(
        status="failed",
        started_at=existing.get("started_at"),
        completed_at=datetime.now(timezone.utc).isoformat(),
        provider_name=existing.get("provider_name"),
        error=reason,
    )
    _store_analysis_job_envelope(candidate, failed)
    candidate.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def _mark_analysis_job_failed_fresh(
    factory: async_sessionmaker[AsyncSession],
    candidate_id: uuid.UUID,
    *,
    reason: str,
) -> None:
    """Best-effort: mark a candidate's analysis job ``failed`` in a fresh session."""
    try:
        async with factory() as session:
            candidate = await get_candidate(session, candidate_id)
            if candidate is not None:
                await _mark_analysis_job_failed(session, candidate, reason=reason)
    except Exception:  # noqa: BLE001 — must not crash the worker
        logger.exception(
            "Failed to mark candidate analysis job %s as failed.", candidate_id
        )


async def process_candidate_analysis_by_id(
    candidate_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    run_analysis: AnalysisRunner | None = None,
    generate_final_report: FinalReportRunner | None = None,
) -> None:
    """Background worker: run one candidate's full analysis in a FRESH session.

    Must NOT reuse the request-scoped session (the 202 has already been sent).
    Every failure path persists a terminal envelope so a job can never stick in
    ``running``. Only ids/statuses/durations are logged — never prompts,
    completions, evidence excerpts, or credentials.
    """
    factory = session_factory or async_session_factory
    start = time.perf_counter()
    try:
        async with factory() as session:
            candidate = await get_candidate(session, candidate_id)
            if candidate is None:
                logger.warning(
                    "Candidate analysis: candidate %s not found for background job.",
                    candidate_id,
                )
                return

            existing = get_analysis_job_envelope(candidate) or {}
            started_at = existing.get("started_at") or datetime.now(
                timezone.utc
            ).isoformat()
            _store_analysis_job_envelope(
                candidate,
                _new_analysis_job_envelope(
                    status="running",
                    started_at=started_at,
                    provider_name=existing.get("provider_name"),
                ),
            )
            candidate.updated_at = datetime.now(timezone.utc)
            await session.commit()
            log_event(
                logger,
                "candidate_analysis_job_started",
                candidate_id=candidate_id,
                status="running",
            )

            try:
                result = await run_candidate_analysis(
                    session,
                    candidate_id,
                    run_analysis=run_analysis,
                    generate_final_report=generate_final_report,
                )
            except ValueError:
                await _mark_analysis_job_failed(
                    session, candidate, reason="candidate_not_found"
                )
                log_event(
                    logger,
                    "candidate_analysis_job_failed",
                    level=logging.WARNING,
                    candidate_id=candidate_id,
                    status="failed",
                    reason="candidate_not_found",
                    duration_ms=int((time.perf_counter() - start) * 1000),
                )
                return

            # ``run_candidate_analysis`` refreshed the candidate and committed;
            # re-read the envelope target from the same live ORM object.
            envelope = _analysis_job_envelope_from_result(
                result, started_at=started_at
            )
            _store_analysis_job_envelope(candidate, envelope)
            candidate.updated_at = datetime.now(timezone.utc)
            await session.commit()
            log_event(
                logger,
                "candidate_analysis_job_completed",
                candidate_id=candidate_id,
                status=envelope["status"],
                warning_count=len(envelope["warnings"]),
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
    except Exception as exc:  # noqa: BLE001 — must not crash the worker
        log_event(
            logger,
            "candidate_analysis_job_failed",
            level=logging.ERROR,
            candidate_id=candidate_id,
            status="failed",
            reason="internal_error",
            exception_type=type(exc).__name__,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        logger.exception(
            "Candidate analysis job crashed for candidate %s: %s", candidate_id, exc
        )
        await _mark_analysis_job_failed_fresh(
            factory, candidate_id, reason="internal_error"
        )


async def process_candidate_analysis_task(candidate_id: str) -> None:
    """FastAPI ``BackgroundTasks`` entry point for an async full-analysis job.

    Takes only a primitive ``candidate_id`` (never an ORM object or the request
    session). Swallows exceptions so a background failure can never surface to
    (or crash) the request handler.
    """
    try:
        await process_candidate_analysis_by_id(uuid.UUID(candidate_id))
    except Exception:  # noqa: BLE001
        logger.exception(
            "Background candidate analysis task crashed for candidate %s",
            candidate_id,
        )


# ---------------------------------------------------------------------------
# Phase 28B — run-level LLM discovery council review
# ---------------------------------------------------------------------------

# Council output is stored under this key inside the run's existing ``config_json``
# metadata blob — no schema migration is required. The key is stripped from the
# config passed back into the evidence pack so a re-run never feeds a prior
# review to the council.
COUNCIL_STORAGE_KEY = "discovery_council"


class DiscoveryCouncilDisabledError(Exception):
    """Raised when a council review is requested but the council is disabled.

    The API layer maps this to a clear 409 — no LLM call and no fake result are
    produced in production when the feature flags are off or no provider is
    available.
    """


def discovery_council_enabled(cfg: Any | None = None) -> bool:
    """True only when BOTH the shared council flag and the discovery flag are on."""
    cfg = cfg or settings
    return bool(cfg.llm_council_enabled and cfg.llm_discovery_council_enabled)


def _run_to_evidence_dict(run: DiscoveryRun) -> dict[str, Any]:
    """Adapt a run row into the bounded, secret-free dict the pack builder reads."""
    config = {
        k: v
        for k, v in (run.config_json or {}).items()
        if k != COUNCIL_STORAGE_KEY
    }
    return {
        "run_id": str(run.id),
        "mode": run.mode,
        "status": run.status,
        "thesis_text": run.thesis_text,
        "parsed_thesis": run.parsed_thesis_json,
        "config": config,
        "provider": run.provider_name,
        "lookback_days": run.lookback_days,
        "universe_count": run.universe_count,
        "candidate_count": run.candidate_count,
        "error_count": run.error_count,
        "warnings": list(run.warnings or []),
    }


def _candidate_to_evidence_dict(c: DiscoveryCandidate) -> dict[str, Any]:
    """Adapt a candidate row into the bounded dict the pack builder reads."""
    raw = c.raw_signal_json if isinstance(c.raw_signal_json, dict) else {}
    data_coverage = raw.get("data_coverage") if isinstance(raw, dict) else {}
    return {
        "candidate_id": str(c.id),
        "ticker": c.ticker,
        "exchange": c.exchange,
        "company_name": c.company_name,
        "country": c.country,
        "sector": c.sector,
        "industry": c.industry,
        "thesis_relevance_score": c.thesis_relevance_score,
        "combined_internal_score": c.combined_internal_score,
        "candidate_score": c.candidate_score,
        "candidate_score_grade": c.candidate_score_grade,
        "momentum_score": c.momentum_score,
        "catalyst_score": c.catalyst_score,
        "fundamentals_score": c.fundamentals_score,
        "source_quality_score": c.source_quality_score,
        "data_completeness_score": c.data_completeness_score,
        "risk_penalty_score": c.risk_penalty_score,
        "data_coverage": data_coverage if isinstance(data_coverage, dict) else {},
        "source_quality": c.source_quality,
        "missing_info_count": c.missing_info_count,
        "blocking_gap_count": c.blocking_gap_count,
        "catalyst_coverage_status": c.catalyst_coverage_status,
        "momentum_label": c.momentum_label,
        "positive_catalyst_count": c.positive_catalyst_count,
        "high_strength_catalyst_count": c.high_strength_catalyst_count,
        "filing_event_count": c.filing_event_count,
        "news_event_count": c.news_event_count,
        "press_release_event_count": c.press_release_event_count,
        "safety_valid": c.safety_valid,
        "human_review_required": c.human_review_required,
        "is_public": c.is_public,
        "warnings": list(c.warnings_json or []),
    }


# ---------------------------------------------------------------------------
# Async council job state (Phase 28B.2)
#
# The value stored under ``config_json["discovery_council"]`` is an ENVELOPE that
# wraps the lifecycle of one async council job around the actual review payload:
#
#   {
#     "status": "pending|running|completed|completed_with_warnings|failed",
#     "started_at": ISO | None,
#     "completed_at": ISO | None,
#     "llm_used": bool | None,
#     "agents_completed": int,
#     "agents_failed": int,
#     "safety_valid": bool | None,
#     "error": str | None,          # short, safe reason code — never an exc str
#     "review": {...} | None,       # the Phase 28B to_storage_dict payload
#   }
#
# A run whose council job is still queued/running has ``review=None``. This is a
# JSONB-only change — no schema migration is required. Legacy Phase 28B rows that
# stored the RAW review dict directly (no envelope) are read transparently via
# ``get_council_envelope`` (normalised to a completed envelope).
# ---------------------------------------------------------------------------

# Terminal envelope statuses — the job has finished (successfully or not).
_COUNCIL_TERMINAL = {"completed", "completed_with_warnings", "failed"}
# Non-terminal envelope statuses — a job is queued or in flight.
_COUNCIL_IN_FLIGHT = {"pending", "running"}
# A completed review exists (usable) in one of these states.
_COUNCIL_HAS_REVIEW = {"completed", "completed_with_warnings"}


def _new_council_envelope(
    *,
    status: str,
    started_at: str | None = None,
    completed_at: str | None = None,
    llm_used: bool | None = None,
    agents_completed: int = 0,
    agents_failed: int = 0,
    safety_valid: bool | None = None,
    review: dict[str, Any] | None = None,
    error: str | None = None,
) -> dict[str, Any]:
    """Build a council job envelope dict for storage under ``config_json``."""
    return {
        "status": status,
        "started_at": started_at,
        "completed_at": completed_at,
        "llm_used": llm_used,
        "agents_completed": agents_completed,
        "agents_failed": agents_failed,
        "safety_valid": safety_valid,
        "error": error,
        "review": review,
    }


def _store_council_envelope(run: DiscoveryRun, envelope: dict[str, Any]) -> None:
    """Persist ``envelope`` under the run's existing config_json (no migration).

    Reassigns the whole dict so SQLAlchemy detects the JSONB change — an in-place
    mutation of a JSONB column is not tracked by default.
    """
    new_config = dict(run.config_json or {})
    new_config[COUNCIL_STORAGE_KEY] = envelope
    run.config_json = new_config


def get_council_envelope(run: DiscoveryRun) -> dict[str, Any] | None:
    """Return the run's council job envelope, or None if no job has ever run.

    A legacy Phase 28B row that stored the RAW review dict (no ``status``/
    ``review`` keys) is normalised into a completed envelope so it keeps
    rendering after the async migration.
    """
    config = run.config_json or {}
    raw = config.get(COUNCIL_STORAGE_KEY)
    if not isinstance(raw, dict):
        return None
    if "status" in raw and "review" in raw:
        return raw  # already an async envelope
    # Legacy raw review → wrap as a completed envelope.
    return _new_council_envelope(
        status="completed",
        started_at=None,
        completed_at=raw.get("created_at"),
        llm_used=raw.get("llm_used"),
        agents_completed=raw.get("agents_completed", 0) or 0,
        agents_failed=raw.get("agents_failed", 0) or 0,
        safety_valid=raw.get("safety_valid"),
        review=raw,
    )


def get_stored_council_review(run: DiscoveryRun) -> dict[str, Any] | None:
    """Return the stored discovery-council review payload, or None if absent.

    Reads the review out of the async envelope (or a legacy raw review). Returns
    None when no council job has run — this includes a queued/running job that
    has not yet produced a review.
    """
    envelope = get_council_envelope(run)
    if envelope is None:
        return None
    review = envelope.get("review")
    return review if isinstance(review, dict) else None


def _classify_council_status(
    result: Any, stored_review: dict[str, Any]
) -> str:
    """Map a completed council result to a terminal envelope status.

    - No agent completed at all → ``failed`` (thin — the review still documents
      the failures, but there is nothing usable to act on).
    - Some agents failed, or the safety re-scan flagged the output →
      ``completed_with_warnings``.
    - Otherwise → ``completed``.
    """
    if (result.agents_completed or 0) <= 0:
        return "failed"
    if (result.agents_failed or 0) > 0 or not stored_review.get("safety_valid", True):
        return "completed_with_warnings"
    return "completed"


async def _compute_council_result(
    db: AsyncSession,
    run: DiscoveryRun,
    *,
    cfg: Any | None = None,
    client: Any | None = None,
) -> Any:
    """Run the discovery council for a run and return the raw result (no store).

    Raises ``DiscoveryCouncilDisabledError`` when the council is disabled or no
    provider resolves, and ``ValueError`` when the run is not ready. The council
    agents run sequentially (one LLM call at a time inside ``run_discovery_council``)
    with per-agent failure isolation (Phase 28B.1), so a rate-limited agent never
    crashes the job — it is recorded as ``failed`` and the review still returns.
    """
    cfg = cfg or settings
    if not discovery_council_enabled(cfg):
        # Manual admin-triggered review requested while the council is off. Emit a
        # safe, secret-free telemetry event (no LLM call is made).
        log_event(
            logger,
            "discovery_council_disabled",
            run_id=run.id,
            reason="flags_off",
        )
        raise DiscoveryCouncilDisabledError("Discovery council is disabled.")

    # Require something to review: a terminal run or at least one candidate.
    if run.status not in _TERMINAL_STATUSES and (run.candidate_count or 0) <= 0:
        raise ValueError(
            "Discovery run is not ready for council review "
            "(no candidates and not in a terminal state)."
        )

    sort = "combined_internal_score" if run.mode == "thesis" else "candidate_score"
    candidates, _ = await list_candidates(
        db,
        run.id,
        limit=max(1, cfg.llm_discovery_council_max_candidates),
        offset=0,
        sort=sort,
    )
    if not candidates:
        raise ValueError("Discovery run has no candidates to review.")

    run_dict = _run_to_evidence_dict(run)
    candidate_dicts = [_candidate_to_evidence_dict(c) for c in candidates]

    result = await maybe_run_discovery_council(
        run=run_dict,
        candidates=candidate_dicts,
        run_id=str(run.id),
        cfg=cfg,
        client=client,
        logger=logger,
    )
    if not result.llm_used:
        # Flags were on but no provider was available (e.g. missing credentials).
        log_event(
            logger,
            "discovery_council_disabled",
            run_id=run.id,
            reason="provider_unavailable",
        )
        raise DiscoveryCouncilDisabledError(
            "Discovery council provider is not available."
        )
    return result


def _finalize_council_review(run: DiscoveryRun, result: Any) -> dict[str, Any]:
    """Build + persist the terminal envelope for a completed council result.

    Runs the defensive safety re-scan, classifies the terminal status, stores the
    envelope on ``run.config_json`` (caller commits) and returns it.
    """
    created_at = datetime.now(timezone.utc).isoformat()
    stored_review = result.to_storage_dict(created_at=created_at)

    # Backstop: no forbidden investment-action language may be saved. The council
    # already quarantines unsafe agent output; this is a defensive re-scan.
    hits = safety_terms.scan_value(
        stored_review, exempt_keys=frozenset({"do_not_infer"})
    )
    if hits:
        stored_review["safety_valid"] = False

    status = _classify_council_status(result, stored_review)
    existing = get_council_envelope(run) or {}
    envelope = _new_council_envelope(
        status=status,
        started_at=existing.get("started_at") or created_at,
        completed_at=created_at,
        llm_used=result.llm_used,
        agents_completed=result.agents_completed,
        agents_failed=result.agents_failed,
        safety_valid=stored_review.get("safety_valid"),
        review=stored_review,
        error="no_agents_completed" if status == "failed" else None,
    )
    _store_council_envelope(run, envelope)
    run.updated_at = datetime.now(timezone.utc)
    return envelope


async def run_discovery_council_review(
    db: AsyncSession,
    run: DiscoveryRun,
    *,
    cfg: Any | None = None,
    client: Any | None = None,
) -> dict[str, Any]:
    """Synchronous inline path: run the council and store the result envelope.

    Retained for inline callers and tests. The API no longer uses this path — it
    schedules the council with ``start_discovery_council_review`` +
    ``process_discovery_council_task`` so the request returns immediately.

    Raises ``DiscoveryCouncilDisabledError`` (→ 409) when disabled/unavailable and
    ``ValueError`` (→ 422) when the run is not ready. Returns the stored envelope.
    """
    cfg = cfg or settings
    result = await _compute_council_result(db, run, cfg=cfg, client=client)
    envelope = _finalize_council_review(run, result)
    await db.commit()
    await db.refresh(run)
    return envelope


async def start_discovery_council_review(
    db: AsyncSession,
    run: DiscoveryRun,
    *,
    force: bool = False,
    cfg: Any | None = None,
) -> tuple[dict[str, Any], bool]:
    """Start (or return the current state of) an async council job for a run.

    Returns ``(envelope, scheduled)`` where ``scheduled`` tells the API whether a
    background task must be launched. Never runs the council itself — that happens
    in ``process_discovery_council_by_id``.

    Job-lifecycle rules (no duplicate jobs):
      * A queued/running job → returns the current envelope, ``scheduled=False``
        (a ``discovery_council_job_duplicate`` event is logged).
      * A completed review and not ``force`` → returns it, ``scheduled=False``.
      * Otherwise a fresh ``pending`` envelope is written and ``scheduled=True``.

    Raises ``DiscoveryCouncilDisabledError`` (→ 409) when a (re)start is requested
    while the council is disabled and no completed review exists, and ``ValueError``
    (→ 422) when the run is not ready to review.
    """
    cfg = cfg or settings
    envelope = get_council_envelope(run)
    status = (envelope or {}).get("status")

    # A job is already queued/running — never launch a second one.
    if status in _COUNCIL_IN_FLIGHT:
        log_event(
            logger,
            "discovery_council_job_duplicate",
            run_id=run.id,
            status=status,
        )
        return envelope or {}, False

    have_completed = status in _COUNCIL_HAS_REVIEW

    # A completed review already exists and no explicit re-run was requested.
    if have_completed and not force:
        return envelope or {}, False

    # From here we intend to (re)start the job — the council must be enabled.
    if not discovery_council_enabled(cfg):
        if have_completed:
            # Cannot re-run while disabled, but the prior review is still valid.
            return envelope or {}, False
        log_event(
            logger,
            "discovery_council_disabled",
            run_id=run.id,
            reason="flags_off",
        )
        raise DiscoveryCouncilDisabledError("Discovery council is disabled.")

    # Require something to review: a terminal run or at least one candidate.
    if run.status not in _TERMINAL_STATUSES and (run.candidate_count or 0) <= 0:
        raise ValueError(
            "Discovery run is not ready for council review "
            "(no candidates and not in a terminal state)."
        )

    started_at = datetime.now(timezone.utc).isoformat()
    pending = _new_council_envelope(status="pending", started_at=started_at)
    _store_council_envelope(run, pending)
    run.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(run)
    log_event(
        logger,
        "discovery_council_job_queued",
        run_id=run.id,
        status="pending",
    )
    return pending, True


async def _mark_council_failed(
    session: AsyncSession, run: DiscoveryRun, *, reason: str
) -> None:
    """Persist a ``failed`` envelope for a run, preserving any prior fields."""
    existing = get_council_envelope(run) or {}
    if existing.get("status") in _COUNCIL_HAS_REVIEW:
        # Never clobber a good stored review with a failure.
        return
    failed = _new_council_envelope(
        status="failed",
        started_at=existing.get("started_at"),
        completed_at=datetime.now(timezone.utc).isoformat(),
        llm_used=existing.get("llm_used"),
        agents_completed=existing.get("agents_completed", 0) or 0,
        agents_failed=existing.get("agents_failed", 0) or 0,
        safety_valid=existing.get("safety_valid"),
        review=existing.get("review"),
        error=reason,
    )
    _store_council_envelope(run, failed)
    run.updated_at = datetime.now(timezone.utc)
    await session.commit()


async def _mark_council_failed_fresh(
    factory: async_sessionmaker[AsyncSession], run_id: uuid.UUID, *, reason: str
) -> None:
    """Best-effort: mark a run's council job ``failed`` in a fresh session."""
    try:
        async with factory() as session:
            run = await get_run(session, run_id)
            if run is not None:
                await _mark_council_failed(session, run, reason=reason)
    except Exception:  # noqa: BLE001 — must not crash the worker
        logger.exception(
            "Failed to mark discovery council job for run %s as failed.", run_id
        )


async def process_discovery_council_by_id(
    run_id: uuid.UUID,
    *,
    session_factory: async_sessionmaker[AsyncSession] | None = None,
    cfg: Any | None = None,
    client: Any | None = None,
) -> None:
    """Background worker: load a run in a FRESH session and run its council job.

    Must NOT reuse the request-scoped session (the response has already been
    returned). Every failure path persists a terminal envelope so a job can never
    stick in ``running``. Only ids/statuses/counts/durations are logged — never
    prompts, completions, evidence excerpts, or credentials.
    """
    factory = session_factory or async_session_factory
    cfg = cfg or settings
    start = time.perf_counter()
    try:
        async with factory() as session:
            run = await get_run(session, run_id)
            if run is None:
                logger.warning(
                    "Discovery council: run %s not found for background job.", run_id
                )
                return

            # Transition pending → running (preserve started_at).
            existing = get_council_envelope(run) or {}
            started_at = existing.get("started_at") or datetime.now(
                timezone.utc
            ).isoformat()
            _store_council_envelope(
                run, _new_council_envelope(status="running", started_at=started_at)
            )
            run.updated_at = datetime.now(timezone.utc)
            await session.commit()
            log_event(
                logger,
                "discovery_council_job_started",
                run_id=run_id,
                status="running",
            )

            # Compute (may raise disabled/ValueError). Persist a terminal envelope
            # for every outcome — success or handled failure.
            try:
                result = await _compute_council_result(
                    session, run, cfg=cfg, client=client
                )
            except DiscoveryCouncilDisabledError:
                await _mark_council_failed(session, run, reason="disabled")
                log_event(
                    logger,
                    "discovery_council_job_failed",
                    level=logging.WARNING,
                    run_id=run_id,
                    status="failed",
                    reason="disabled",
                    duration_ms=int((time.perf_counter() - start) * 1000),
                )
                return
            except ValueError:
                await _mark_council_failed(session, run, reason="not_ready")
                log_event(
                    logger,
                    "discovery_council_job_failed",
                    level=logging.WARNING,
                    run_id=run_id,
                    status="failed",
                    reason="not_ready",
                    duration_ms=int((time.perf_counter() - start) * 1000),
                )
                return

            envelope = _finalize_council_review(run, result)
            await session.commit()
            log_event(
                logger,
                "discovery_council_job_completed",
                run_id=run_id,
                status=envelope["status"],
                agents_completed=envelope["agents_completed"],
                agents_failed=envelope["agents_failed"],
                safety_valid=envelope["safety_valid"],
                duration_ms=int((time.perf_counter() - start) * 1000),
            )
    except Exception as exc:  # noqa: BLE001 — must not crash the worker
        # Structured, secret-free failure event — never the raw exception string.
        log_event(
            logger,
            "discovery_council_job_failed",
            level=logging.ERROR,
            run_id=run_id,
            status="failed",
            reason="internal_error",
            exception_type=type(exc).__name__,
            duration_ms=int((time.perf_counter() - start) * 1000),
        )
        logger.exception("Discovery council job crashed for run %s: %s", run_id, exc)
        await _mark_council_failed_fresh(factory, run_id, reason="internal_error")


async def process_discovery_council_task(run_id: str) -> None:
    """FastAPI ``BackgroundTasks`` entry point for an async council job.

    Takes only a primitive ``run_id`` (never an ORM object or the request
    session) and drives the fresh-session worker. Swallows exceptions so a
    background failure can never surface to (or crash) the request handler.
    """
    try:
        await process_discovery_council_by_id(uuid.UUID(run_id))
    except Exception:  # noqa: BLE001
        logger.exception(
            "Background discovery council task crashed for run %s", run_id
        )
