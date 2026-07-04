import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.source import SourceCreate, SourceList, SourceRead
from app.services import source_service

router = APIRouter(prefix="/sources", tags=["sources"])


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
