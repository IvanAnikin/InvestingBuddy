"""
Tests for source_service: create, list, get, dedup logic.

Uses MagicMock for SQLAlchemy models and AsyncMock for the db session.
No real database is required.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from app.models.source import Source
from app.schemas.source import SourceCreate
from app.services import source_service


def _make_source(source_id: uuid.UUID | None = None) -> MagicMock:
    now = datetime.now(timezone.utc)
    s = MagicMock(spec=Source)
    s.id = source_id or uuid.uuid4()
    s.source_type = "news_article"
    s.title = "Test Source"
    s.url = "https://example.com/article"
    s.publisher = "Example Publisher"
    s.published_at = None
    s.retrieved_at = now
    s.credibility_score = 0.8
    s.content_hash = None
    s.blob_path = None
    s.created_at = now
    return s


_FIND_BY_URL = "app.services.source_service.find_by_url"
_FIND_BY_HASH = "app.services.source_service.find_by_content_hash"
_CREATE = "app.services.source_service.create_source"


# ---------------------------------------------------------------------------
# create_source
# ---------------------------------------------------------------------------


async def test_create_source_adds_and_commits(mock_db: AsyncMock) -> None:
    mock_db.refresh = AsyncMock()

    await source_service.create_source(
        mock_db,
        SourceCreate(source_type="news_article", title="Test", url="https://x.com"),
    )

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_db.refresh.assert_called_once()

    added = mock_db.add.call_args[0][0]
    assert isinstance(added, Source)
    assert added.source_type == "news_article"
    assert added.title == "Test"
    assert added.url == "https://x.com"


async def test_create_source_sets_retrieved_at_when_not_provided(mock_db: AsyncMock) -> None:
    mock_db.refresh = AsyncMock()

    await source_service.create_source(
        mock_db,
        SourceCreate(source_type="placeholder", title="No URL", url=None),
    )

    added = mock_db.add.call_args[0][0]
    assert added.retrieved_at is not None


# ---------------------------------------------------------------------------
# get_or_create_source — deduplication
# ---------------------------------------------------------------------------


async def test_get_or_create_returns_existing_by_hash(mock_db: AsyncMock) -> None:
    existing = _make_source()
    existing.content_hash = "abc123"

    with (
        patch(_FIND_BY_HASH, new_callable=AsyncMock, return_value=existing),
    ):
        source, created = await source_service.get_or_create_source(
            mock_db,
            SourceCreate(
                source_type="news_article",
                title="Duplicate",
                content_hash="abc123",
            ),
        )

    assert created is False
    assert source.id == existing.id


async def test_get_or_create_returns_existing_by_url(mock_db: AsyncMock) -> None:
    existing = _make_source()

    with (
        patch(_FIND_BY_HASH, new_callable=AsyncMock, return_value=None),
        patch(_FIND_BY_URL, new_callable=AsyncMock, return_value=existing),
    ):
        source, created = await source_service.get_or_create_source(
            mock_db,
            SourceCreate(
                source_type="news_article",
                title="Duplicate URL",
                url="https://example.com/article",
            ),
        )

    assert created is False
    assert source.id == existing.id


async def test_get_or_create_creates_new_when_no_match(mock_db: AsyncMock) -> None:
    new_source = _make_source()

    with (
        patch(_FIND_BY_HASH, new_callable=AsyncMock, return_value=None),
        patch(_FIND_BY_URL, new_callable=AsyncMock, return_value=None),
        patch(_CREATE, new_callable=AsyncMock, return_value=new_source),
    ):
        source, created = await source_service.get_or_create_source(
            mock_db,
            SourceCreate(
                source_type="annual_report",
                title="New Source",
                url="https://new.example.com",
            ),
        )

    assert created is True
    assert source.id == new_source.id


async def test_get_or_create_skips_url_check_when_no_url(mock_db: AsyncMock) -> None:
    new_source = _make_source()

    with (
        patch(_FIND_BY_HASH, new_callable=AsyncMock, return_value=None),
        patch(_FIND_BY_URL, new_callable=AsyncMock) as mock_url,
        patch(_CREATE, new_callable=AsyncMock, return_value=new_source),
    ):
        await source_service.get_or_create_source(
            mock_db,
            SourceCreate(source_type="placeholder", title="No URL", url=None),
        )

    mock_url.assert_not_called()


# ---------------------------------------------------------------------------
# list_sources / count_sources
# ---------------------------------------------------------------------------


async def test_list_sources_returns_list(mock_db: AsyncMock) -> None:
    sources = [_make_source(), _make_source()]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = sources
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await source_service.list_sources(mock_db, limit=10, offset=0)

    assert len(result) == 2
    mock_db.execute.assert_called_once()


async def test_count_sources_returns_int(mock_db: AsyncMock) -> None:
    sources = [_make_source(), _make_source(), _make_source()]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = sources
    mock_db.execute = AsyncMock(return_value=mock_result)

    count = await source_service.count_sources(mock_db)

    assert count == 3


# ---------------------------------------------------------------------------
# get_source
# ---------------------------------------------------------------------------


async def test_get_source_returns_source(mock_db: AsyncMock) -> None:
    source = _make_source()
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = source
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await source_service.get_source(mock_db, source.id)

    assert result is source


async def test_get_source_returns_none_when_missing(mock_db: AsyncMock) -> None:
    mock_result = MagicMock()
    mock_result.scalar_one_or_none.return_value = None
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await source_service.get_source(mock_db, uuid.uuid4())

    assert result is None
