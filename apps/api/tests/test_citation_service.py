"""
Tests for citation_service: create, list, and validate_citations_for_draft.

No real database needed — uses AsyncMock for db session.
"""

import uuid
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

from app.models.source import Citation
from app.schemas.source import CitationCreate
from app.services import citation_service


def _make_citation(
    citation_id: uuid.UUID | None = None,
    report_id: uuid.UUID | None = None,
    claim_text: str | None = "thesis",
) -> MagicMock:
    now = datetime.now(timezone.utc)
    c = MagicMock(spec=Citation)
    c.id = citation_id or uuid.uuid4()
    c.source_id = uuid.uuid4()
    c.report_id = report_id or uuid.uuid4()
    c.agent_run_id = None
    c.claim_text = claim_text
    c.source_quote = "Quoted text."
    c.url = None
    c.retrieved_at = None
    c.created_at = now
    return c


# ---------------------------------------------------------------------------
# create_citation
# ---------------------------------------------------------------------------


async def test_create_citation_adds_and_commits(mock_db: AsyncMock) -> None:
    mock_db.refresh = AsyncMock()
    source_id = uuid.uuid4()
    report_id = uuid.uuid4()

    await citation_service.create_citation(
        mock_db,
        CitationCreate(
            source_id=source_id,
            report_id=report_id,
            claim_text="thesis",
        ),
    )

    mock_db.add.assert_called_once()
    mock_db.commit.assert_called_once()
    mock_db.refresh.assert_called_once()

    added = mock_db.add.call_args[0][0]
    assert isinstance(added, Citation)
    assert added.source_id == source_id
    assert added.report_id == report_id
    assert added.claim_text == "thesis"


async def test_create_citation_without_optional_fields(mock_db: AsyncMock) -> None:
    mock_db.refresh = AsyncMock()

    await citation_service.create_citation(
        mock_db,
        CitationCreate(source_id=uuid.uuid4()),
    )

    added = mock_db.add.call_args[0][0]
    assert added.report_id is None
    assert added.claim_text is None


# ---------------------------------------------------------------------------
# list_citations_for_report
# ---------------------------------------------------------------------------


async def test_list_citations_for_report(mock_db: AsyncMock) -> None:
    rid = uuid.uuid4()
    citations = [_make_citation(report_id=rid), _make_citation(report_id=rid)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = citations
    mock_db.execute = AsyncMock(return_value=mock_result)

    result = await citation_service.list_citations_for_report(mock_db, rid)

    assert len(result) == 2
    mock_db.execute.assert_called_once()


async def test_count_citations_for_report(mock_db: AsyncMock) -> None:
    rid = uuid.uuid4()
    citations = [_make_citation(report_id=rid)]
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = citations
    mock_db.execute = AsyncMock(return_value=mock_result)

    count = await citation_service.count_citations_for_report(mock_db, rid)

    assert count == 1


# ---------------------------------------------------------------------------
# validate_citations_for_draft
# ---------------------------------------------------------------------------


def _placeholder_analysis() -> dict:
    return {
        "ticker": "VOW3",
        "company_name": "Volkswagen AG",
        "rating": "WATCH",
        "confidence_score": 0.50,
        "thesis": "Placeholder thesis.",
        "financial_metrics": {},
        "is_placeholder": True,
    }


def test_validate_placeholder_analysis_returns_warnings() -> None:
    analysis = _placeholder_analysis()
    citations = [_make_citation(claim_text="thesis")]

    result = citation_service.validate_citations_for_draft(analysis, citations)

    assert result.status == "warnings"
    assert any("is_placeholder" in w for w in result.warnings)


def test_validate_missing_thesis_citation_adds_warning() -> None:
    analysis = _placeholder_analysis()
    analysis["is_placeholder"] = False
    citations: list = []  # no citations at all

    result = citation_service.validate_citations_for_draft(analysis, citations)

    assert result.status == "failed"
    missing_sections = [m.section for m in result.missing_citations]
    assert "thesis" in missing_sections
    assert "rating" in missing_sections


def test_validate_cited_thesis_approves_it() -> None:
    analysis = _placeholder_analysis()
    analysis["is_placeholder"] = False
    analysis["financial_metrics"] = {}
    citations = [_make_citation(claim_text="thesis")]

    result = citation_service.validate_citations_for_draft(analysis, citations)

    assert "thesis" in result.approved_claims


def test_validate_financial_metrics_with_no_data_adds_warning() -> None:
    analysis = _placeholder_analysis()
    analysis["is_placeholder"] = False
    analysis["financial_metrics"] = {}
    citations = [_make_citation(claim_text="thesis")]

    result = citation_service.validate_citations_for_draft(analysis, citations)

    assert any("financial_metrics" in w for w in result.warnings)


def test_validate_empty_thesis_adds_warning() -> None:
    analysis = _placeholder_analysis()
    analysis["thesis"] = ""
    analysis["is_placeholder"] = False
    citations: list = []

    result = citation_service.validate_citations_for_draft(analysis, citations)

    assert any("thesis field is empty" in w for w in result.warnings)
