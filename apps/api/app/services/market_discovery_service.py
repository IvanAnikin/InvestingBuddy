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
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Any, Awaitable, Callable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import settings
from app.db.session import async_session_factory
from app.models.discovery import (
    ALLOWED_CANDIDATE_LABELS,
    DiscoveryCandidate,
    DiscoveryRun,
)
from app.schemas.market_discovery import (
    DiscoveryRunCreate,
    DiscoveryRunSummary,
    ThesisDiscoveryRunCreate,
)
from app.services import safety_terms
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


async def create_pending_thesis_run(
    db: AsyncSession, payload: ThesisDiscoveryRunCreate
) -> DiscoveryRun:
    """
    Phase 27 — parse a market thesis, build a bounded real-company universe, and
    persist a ``pending`` thesis discovery run WITHOUT processing it.

    Returns quickly so the API can hand back a ``run_id`` immediately (the scan
    runs in the background). Raises ``ValueError`` when:
      * the provider is not permitted,
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

    parsed = parse_thesis(
        payload.thesis_text,
        region=payload.region,
        country=payload.country,
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
        warnings=list(universe.warnings),
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

    # Ensure the company exists so the workflow can resolve it. Routed through
    # the shared helper so a company row still stuck on its bare-ticker stub
    # name is upgraded to the candidate's resolved name before the full
    # analysis (and its report title) is generated.
    company = await ensure_company(
        db,
        candidate.ticker,
        candidate.exchange,
        company_name=candidate.company_name,
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
