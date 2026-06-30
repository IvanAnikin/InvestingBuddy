"""
Phase 14: CompanyDiscoveryService — company discovery and candidate management.

This service manages the screening funnel:
  1. create_universe        — define a universe of companies to screen
  2. run_screening          — execute a screen against a universe
  3. get_screening_run      — retrieve a screening run by ID
  4. list_screening_runs    — list all screening runs
  5. list_candidates        — list candidates for a run
  6. promote_candidate      — create/identify a Company record from a candidate

Constraints (enforced throughout):
  - No BUY/SELL/HOLD/WATCH/price_target/fair_value/upside produced.
  - No public publishing of candidates.
  - Source tier stays T5 for EODHD data; T6 for mock data.
  - Promotion creates a Company DB record for deeper analysis — it does NOT
    trigger analysis, does NOT approve anything, does NOT publish anything.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.models.screening import (
    CANDIDATE_STATUS_VALUES,
    ScreeningCandidate,
    ScreeningRun,
    ScreeningUniverse,
)
from app.schemas.discovery import (
    PromoteCandidateResponse,
    ScreeningRunCreate,
    ScreeningUniverseCreate,
)
from app.services.screener import CandidateInput, CompanyScreener

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Universe management
# ---------------------------------------------------------------------------


async def create_universe(
    db: AsyncSession,
    data: ScreeningUniverseCreate,
) -> ScreeningUniverse:
    """
    Create a new screening universe definition.

    A universe defines the filter parameters for future screening runs.
    """
    universe = ScreeningUniverse(
        name=data.name,
        description=data.description,
        region=data.region,
        exchange=data.exchange,
        sector_filter=data.sector_filter,
        theme=data.theme,
        provider_name=data.provider_name,
    )
    db.add(universe)
    await db.commit()
    await db.refresh(universe)
    logger.info("Created screening universe '%s' (id=%s)", universe.name, universe.id)
    return universe


async def get_universe(
    db: AsyncSession,
    universe_id: uuid.UUID,
) -> ScreeningUniverse | None:
    result = await db.execute(
        select(ScreeningUniverse).where(ScreeningUniverse.id == universe_id)
    )
    return result.scalar_one_or_none()


async def list_universes(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[ScreeningUniverse], int]:
    rows = await db.execute(
        select(ScreeningUniverse)
        .order_by(ScreeningUniverse.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    items = list(rows.scalars().all())
    count_row = await db.execute(
        select(func.count()).select_from(ScreeningUniverse)
    )
    total = count_row.scalar_one()
    return items, total


# ---------------------------------------------------------------------------
# Screening runs
# ---------------------------------------------------------------------------


async def run_screening(
    db: AsyncSession,
    data: ScreeningRunCreate,
    eodhd_search_results: list[dict] | None = None,
) -> ScreeningRun:
    """
    Execute a screening run against the named universe.

    Steps:
      1. Load universe definition.
      2. Create ScreeningRun record (status=running).
      3. Run deterministic screener to produce CandidateInput list.
      4. Persist each CandidateInput as a ScreeningCandidate.
      5. Build run summary.
      6. Update ScreeningRun record (status=completed or failed).

    `eodhd_search_results` is used for offline/fixture-based testing and
    for live EODHD screening — when None, the mock universe is used.

    Raises ValueError if the universe is not found.
    """
    universe = await get_universe(db, data.universe_id)
    if universe is None:
        raise ValueError(f"Screening universe {data.universe_id} not found")

    parameters = {
        "universe_id": str(data.universe_id),
        "max_candidates": data.max_candidates,
        "market_cap_min": data.market_cap_min,
        "market_cap_max": data.market_cap_max,
        "keyword_search": data.keyword_search,
    }

    run = ScreeningRun(
        universe_id=data.universe_id,
        status="running",
        provider_name=universe.provider_name,
        started_at=_utcnow(),
        parameters_json=parameters,
    )
    db.add(run)
    await db.commit()
    await db.refresh(run)

    logger.info(
        "Starting screening run %s for universe '%s'", run.id, universe.name
    )

    try:
        screener = CompanyScreener()
        candidate_inputs = screener.screen(
            region=universe.region,
            exchange=universe.exchange,
            sector=universe.sector_filter,
            theme=universe.theme,
            max_candidates=data.max_candidates,
            provider_name=universe.provider_name,
            market_cap_min=data.market_cap_min,
            market_cap_max=data.market_cap_max,
            keyword_search=data.keyword_search,
            eodhd_search_results=eodhd_search_results,
        )

        candidates = await _persist_candidates(db, run.id, candidate_inputs)

        # ── Build summary ────────────────────────────────────────────────────
        status_counts: dict[str, int] = {}
        for c in candidates:
            status_counts[c.candidate_status] = (
                status_counts.get(c.candidate_status, 0) + 1
            )

        summary = {
            "total_candidates": len(candidates),
            "status_counts": status_counts,
            "provider_name": universe.provider_name,
            "theme": universe.theme,
            "region": universe.region,
            "exchange": universe.exchange,
            "sector_filter": universe.sector_filter,
            "note": (
                "Internal research funnel only. "
                "No investment recommendation produced. "
                "No price target or fair value computed."
            ),
        }

        run.status = "completed"
        run.completed_at = _utcnow()
        run.summary_json = summary
        await db.commit()
        await db.refresh(run)

        logger.info(
            "Screening run %s completed: %d candidates found", run.id, len(candidates)
        )
        return run

    except Exception as exc:
        logger.exception("Screening run %s failed: %s", run.id, exc)
        run.status = "failed"
        run.completed_at = _utcnow()
        run.error_message = str(exc)
        await db.commit()
        await db.refresh(run)
        return run


async def _persist_candidates(
    db: AsyncSession,
    run_id: uuid.UUID,
    inputs: list[CandidateInput],
) -> list[ScreeningCandidate]:
    """
    Persist a list of CandidateInput objects as ScreeningCandidate rows.

    Does not assign company_id — that happens on promotion.
    """
    records: list[ScreeningCandidate] = []
    for inp in inputs:
        status = inp.candidate_status
        if status not in CANDIDATE_STATUS_VALUES:
            logger.warning(
                "Invalid candidate_status '%s' — setting to 'error'", status
            )
            status = "error"

        candidate = ScreeningCandidate(
            screening_run_id=run_id,
            company_id=None,
            ticker=inp.ticker.upper() if inp.ticker else "",
            exchange=inp.exchange.upper() if inp.exchange else None,
            name=inp.name,
            country=inp.country,
            sector=inp.sector,
            provider_symbol=inp.provider_symbol,
            market_cap=inp.market_cap,
            currency=inp.currency,
            candidate_status=status,
            discovery_reasons_json=inp.discovery_reasons,
            available_data_json=inp.available_data,
            missing_data_json=inp.missing_data,
            source_tier=inp.source_tier,
            data_quality=inp.data_quality,
            warnings_json=inp.warnings,
        )
        db.add(candidate)
        records.append(candidate)

    await db.commit()
    for c in records:
        await db.refresh(c)

    return records


async def get_screening_run(
    db: AsyncSession,
    run_id: uuid.UUID,
) -> ScreeningRun | None:
    result = await db.execute(
        select(ScreeningRun).where(ScreeningRun.id == run_id)
    )
    return result.scalar_one_or_none()


async def list_screening_runs(
    db: AsyncSession,
    limit: int = 50,
    offset: int = 0,
    universe_id: uuid.UUID | None = None,
) -> tuple[list[ScreeningRun], int]:
    q = select(ScreeningRun).order_by(ScreeningRun.created_at.desc())
    count_q = select(func.count()).select_from(ScreeningRun)
    if universe_id is not None:
        q = q.where(ScreeningRun.universe_id == universe_id)
        count_q = count_q.where(ScreeningRun.universe_id == universe_id)

    rows = await db.execute(q.limit(limit).offset(offset))
    items = list(rows.scalars().all())
    count_row = await db.execute(count_q)
    total = count_row.scalar_one()
    return items, total


# ---------------------------------------------------------------------------
# Candidates
# ---------------------------------------------------------------------------


async def list_candidates(
    db: AsyncSession,
    run_id: uuid.UUID,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[ScreeningCandidate], int]:
    q = (
        select(ScreeningCandidate)
        .where(ScreeningCandidate.screening_run_id == run_id)
        .order_by(ScreeningCandidate.created_at.asc())
    )
    count_q = (
        select(func.count())
        .select_from(ScreeningCandidate)
        .where(ScreeningCandidate.screening_run_id == run_id)
    )
    rows = await db.execute(q.limit(limit).offset(offset))
    items = list(rows.scalars().all())
    count_row = await db.execute(count_q)
    total = count_row.scalar_one()
    return items, total


async def get_candidate(
    db: AsyncSession,
    candidate_id: uuid.UUID,
) -> ScreeningCandidate | None:
    result = await db.execute(
        select(ScreeningCandidate).where(ScreeningCandidate.id == candidate_id)
    )
    return result.scalar_one_or_none()


# ---------------------------------------------------------------------------
# Promotion
# ---------------------------------------------------------------------------


async def promote_candidate_to_analysis(
    db: AsyncSession,
    candidate_id: uuid.UUID,
) -> PromoteCandidateResponse:
    """
    Promote a screening candidate to the company analysis funnel.

    Promotion:
      - Creates or identifies an existing Company record for the candidate.
      - Updates candidate_status to 'ready_for_deeper_analysis'.
      - Sets candidate.company_id to the resolved company.

    This does NOT:
      - Trigger analysis workflow automatically.
      - Create any investment recommendation.
      - Publish anything.
      - Produce a price target or fair value.

    The admin must separately trigger the company-analysis workflow.

    Raises ValueError if the candidate is not found or is in error state.
    """
    candidate = await get_candidate(db, candidate_id)
    if candidate is None:
        raise ValueError(f"Screening candidate {candidate_id} not found")

    if candidate.candidate_status == "error":
        raise ValueError(
            f"Candidate {candidate_id} is in error state and cannot be promoted. "
            "Resolve the underlying error before promoting."
        )

    if candidate.candidate_status == "rejected_by_screen":
        raise ValueError(
            f"Candidate {candidate_id} was rejected by the screen and cannot be "
            "promoted. Create a new screening run with adjusted parameters if needed."
        )

    if not candidate.ticker:
        raise ValueError(
            f"Candidate {candidate_id} has no ticker — cannot create Company record."
        )

    # ── Find or create Company ────────────────────────────────────────────────
    company_created = False
    company: Company | None = None

    if candidate.company_id is not None:
        company = await db.get(Company, candidate.company_id)

    if company is None and candidate.ticker and candidate.exchange:
        result = await db.execute(
            select(Company).where(
                Company.ticker == candidate.ticker.upper(),
                Company.exchange == (candidate.exchange or "").upper(),
            )
        )
        company = result.scalar_one_or_none()

    if company is None:
        company = Company(
            ticker=candidate.ticker.upper(),
            exchange=(candidate.exchange or "UNKNOWN").upper(),
            name=candidate.name or candidate.ticker,
            country=candidate.country,
            sector=candidate.sector,
            status="new",
        )
        db.add(company)
        await db.commit()
        await db.refresh(company)
        company_created = True
        logger.info(
            "Created Company record %s for candidate %s (%s.%s)",
            company.id,
            candidate_id,
            candidate.ticker,
            candidate.exchange,
        )
    else:
        logger.info(
            "Using existing Company record %s for candidate %s",
            company.id,
            candidate_id,
        )

    # ── Update candidate ──────────────────────────────────────────────────────
    candidate.company_id = company.id
    candidate.candidate_status = "ready_for_deeper_analysis"
    await db.commit()
    await db.refresh(candidate)

    return PromoteCandidateResponse(
        candidate_id=candidate.id,
        company_id=company.id,
        ticker=company.ticker,
        exchange=company.exchange,
        name=company.name,
        promoted=True,
        company_created=company_created,
        new_candidate_status="ready_for_deeper_analysis",
        message=(
            f"Candidate promoted. Company record {'created' if company_created else 'identified'} "
            f"({company.ticker}.{company.exchange}). "
            "Run the company-analysis workflow separately to begin deeper research. "
            "No recommendation produced. No publishing performed."
        ),
    )
