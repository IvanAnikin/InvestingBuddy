import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.source import SourceCreate, SourceList, SourceRead
from app.schemas.source_evidence_preview import (
    MAX_PREVIEW_ITEMS,
    EvidencePreviewRequest,
    EvidencePreviewResponse,
)
from app.schemas.source_registry import (
    SourceHealthResponse,
    SourceRegistryResponse,
    TierInfo,
)
from app.services import source_service
from app.services.sources.company_evidence import collect_company_source_evidence
from app.services.sources.connector_base import CompanyContext
from app.services.sources.live_fetchers import (
    live_ir_page_fetcher,
    live_ir_press_fetcher,
    live_sec_filings_fetcher,
)
from app.services.sources.registry import build_registry, tier_legend

router = APIRouter(prefix="/sources", tags=["sources"])


# ── Source registry + connector framework (Phase 29A) ───────────────────────
# Read-only, secret-free views of the unified source registry. Declared BEFORE
# the parameterised ``/{source_id}`` route so the literal paths win the match.


@router.get("/registry", response_model=SourceRegistryResponse)
async def get_source_registry() -> SourceRegistryResponse:
    """Return the source registry: enabled + planned sources, tiers, and gaps.

    No secrets are ever exposed — a registry entry describes a source's identity
    and policy (tier, jurisdiction, cost, rate-limit), never a credential.
    """
    registry = build_registry()
    return SourceRegistryResponse(
        generated_at=datetime.now(timezone.utc),
        summary=registry.summary(),
        tiers=[
            TierInfo(
                code=t["code"],
                rank=t["rank"],
                label=t["label"],
                description=t["description"],
            )
            for t in tier_legend()
        ],
        sources=registry.all_sources(),
        gaps=registry.source_gaps(),
    )


@router.get("/health", response_model=SourceHealthResponse)
async def get_source_health() -> SourceHealthResponse:
    """Return safe, network-free connector health for every known connector."""
    registry = build_registry()
    return SourceHealthResponse(
        generated_at=datetime.now(timezone.utc),
        connectors=registry.health(),
    )


@router.post("/evidence-preview", response_model=EvidencePreviewResponse)
async def preview_source_evidence(
    payload: EvidencePreviewRequest,
) -> EvidencePreviewResponse:
    """Run the source-registry connectors for one issuer and return their
    bounded, tiered evidence + honest gaps (Phase 29B).

    Internal admin / validation aid — protected the same way as every other
    ``/api/v1`` route (staging Basic Auth + the web admin proxy). It is NOT a URL
    fetcher: the request carries only issuer identity, and connectors reach only
    fixed, known hosts (SEC EDGAR; the curated verified-issuer feed allowlist).
    A live fetch is performed only when ``source_connector_enabled`` is set;
    otherwise the connectors run offline and return honest coverage gaps.
    """
    registry = build_registry()
    known_ids = {s.source_id for s in registry.all_sources()}
    if payload.source_ids:
        unknown = [sid for sid in payload.source_ids if sid not in known_ids]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Unknown source_id(s): {', '.join(sorted(unknown))}",
            )

    company = CompanyContext(
        ticker=payload.ticker,
        exchange=payload.exchange,
        company_name=payload.company_name,
        country=payload.country,
    )

    live = bool(settings.source_connector_enabled)
    collected = await collect_company_source_evidence(
        company=company,
        source_ids=payload.source_ids,
        registry=registry,
        filings_fetcher=live_sec_filings_fetcher if live else None,
        press_fetcher=live_ir_press_fetcher if live else None,
        ir_page_fetcher=live_ir_page_fetcher if live else None,
    )

    return EvidencePreviewResponse(
        generated_at=datetime.now(timezone.utc),
        ticker=payload.ticker,
        exchange=payload.exchange,
        connector_layer_enabled=live,
        live_fetch_performed=live,
        evidence_items=collected.evidence_items[:MAX_PREVIEW_ITEMS],
        source_gaps=collected.source_gaps,
        warnings=collected.warnings,
    )


@router.post("", response_model=SourceRead, status_code=status.HTTP_201_CREATED)
async def create_source(
    payload: SourceCreate, db: AsyncSession = Depends(get_db)
) -> SourceRead:
    source, _ = await source_service.get_or_create_source(db, payload)
    return SourceRead.model_validate(source)


@router.get("", response_model=SourceList)
async def list_sources(
    limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)
) -> SourceList:
    sources = await source_service.list_sources(db, limit=limit, offset=offset)
    total = await source_service.count_sources(db)
    return SourceList(
        items=[SourceRead.model_validate(s) for s in sources],
        total=total,
    )


@router.get("/{source_id}", response_model=SourceRead)
async def get_source(
    source_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> SourceRead:
    source = await source_service.get_source(db, source_id)
    if not source:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Source {source_id} not found",
        )
    return SourceRead.model_validate(source)
