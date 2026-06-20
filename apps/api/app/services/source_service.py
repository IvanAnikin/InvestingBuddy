import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import Source
from app.schemas.source import SourceCreate


async def create_source(db: AsyncSession, data: SourceCreate) -> Source:
    now = datetime.now(timezone.utc)
    source = Source(
        source_type=data.source_type,
        title=data.title,
        url=data.url,
        publisher=data.publisher,
        published_at=data.published_at,
        retrieved_at=data.retrieved_at or now,
        credibility_score=data.credibility_score,
        content_hash=data.content_hash,
        blob_path=data.blob_path,
    )
    db.add(source)
    await db.commit()
    await db.refresh(source)
    return source


async def get_source(db: AsyncSession, source_id: uuid.UUID) -> Source | None:
    result = await db.execute(select(Source).where(Source.id == source_id))
    return result.scalar_one_or_none()


async def list_sources(
    db: AsyncSession, limit: int = 50, offset: int = 0
) -> list[Source]:
    result = await db.execute(
        select(Source).order_by(Source.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def count_sources(db: AsyncSession) -> int:
    result = await db.execute(select(Source))
    return len(result.scalars().all())


async def find_by_url(db: AsyncSession, url: str) -> Source | None:
    """Return the most recent source with this URL, or None."""
    result = await db.execute(
        select(Source)
        .where(Source.url == url)
        .order_by(Source.retrieved_at.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


async def find_by_content_hash(db: AsyncSession, content_hash: str) -> Source | None:
    """Return an existing source with the same content hash, or None."""
    result = await db.execute(
        select(Source).where(Source.content_hash == content_hash).limit(1)
    )
    return result.scalar_one_or_none()


async def get_or_create_source(db: AsyncSession, data: SourceCreate) -> tuple[Source, bool]:
    """
    Return (source, created).
    Deduplicates by content_hash first, then by URL.
    created=False means an existing record was returned.
    """
    if data.content_hash:
        existing = await find_by_content_hash(db, data.content_hash)
        if existing:
            return existing, False

    if data.url:
        existing = await find_by_url(db, data.url)
        if existing:
            return existing, False

    source = await create_source(db, data)
    return source, True
