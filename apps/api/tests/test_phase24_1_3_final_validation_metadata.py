"""
Phase 24.1.3 — Final report validation metadata persistence.

Regression fix: the final-report generate + validate service methods wrote to the
report row with only ``await db.flush()`` and no ``await db.commit()``. Because
``get_db()`` yields the session and closes it (rolling back any uncommitted
transaction), the ``final_report_version`` / ``safety_validation_json`` /
``schema_validation_json`` writes were silently discarded — so the admin
report-detail page kept showing "Final Report Version / Safety / Schema: n/a"
after clicking Generate / Validate.

These tests assert the persistence paths COMMIT, that the validation JSON is
attached to the report, and that safety/schema/human-review semantics are
unchanged (schema_valid may be false while safety_valid is true; human review
stays required; no recommendation/target/fair-value/upside).
"""

from __future__ import annotations

import json
import re
import uuid
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.services.final_report_generator import (
    FinalReportGeneratorService,
    _save_final_report_draft,
    run_safety_gate,
)

_FORBIDDEN = re.compile(
    r"(?i)\b(BUY|SELL|HOLD|WATCH)\b|price target|target price|fair value|"
    r"upside|downside|under\s?valued|over\s?valued"
)


def _assert_no_forbidden(text: str) -> None:
    m = _FORBIDDEN.search(text or "")
    assert m is None, f"forbidden term leaked: {m.group(0)!r}"


def _mock_db() -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()  # SQLAlchemy Session.add is synchronous
    return db


# ===========================================================================
# _save_final_report_draft (the generate persistence path)
# ===========================================================================


@pytest.mark.asyncio
async def test_save_final_report_draft_commits() -> None:
    db = _mock_db()
    safety_result = run_safety_gate({})  # benign → passed
    schema_validation = {"is_valid": False, "errors": ["missing section"], "warnings": []}

    report = await _save_final_report_draft(
        db,
        report_content={"executive_summary": {"value": "Internal research draft."}},
        safety_result=safety_result,
        schema_validation=schema_validation,
        source_summary={},
        scorecard_id=None,
        company_name="Apple Inc.",
        ticker="AAPL",
        source_report_id=uuid.uuid4(),
    )

    # The draft must be added AND committed (not just flushed).
    db.add.assert_called_once()
    db.flush.assert_awaited()
    db.commit.assert_awaited()  # <-- the fix

    # The persisted row carries the version + validation JSON.
    assert report.final_report_version
    assert report.safety_validation_json["passed"] is True
    assert report.schema_validation_json["is_valid"] is False
    assert report.human_review_required is True


@pytest.mark.asyncio
async def test_save_final_report_draft_no_forbidden() -> None:
    db = _mock_db()
    report = await _save_final_report_draft(
        db,
        report_content={"executive_summary": {"value": "Internal research draft."}},
        safety_result=run_safety_gate({}),
        schema_validation={"is_valid": False, "errors": [], "warnings": []},
        source_summary={},
        scorecard_id=None,
        company_name="Apple Inc.",
        ticker="AAPL",
        source_report_id=uuid.uuid4(),
    )
    _assert_no_forbidden(report.content_markdown or "")
    _assert_no_forbidden(json.dumps(report.safety_validation_json))


# ===========================================================================
# validate_final_report (the validate persistence path — the reported symptom)
# ===========================================================================


def _report_with_content(content: dict) -> MagicMock:
    report = MagicMock()
    report.id = uuid.uuid4()
    report.content_markdown = "```json\n" + json.dumps(content) + "\n```"
    report.safety_validation_json = None
    report.schema_validation_json = None
    return report


def _db_returning(report: MagicMock) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = report
    db.execute.return_value = result
    return db


@pytest.mark.asyncio
async def test_validate_final_report_commits_and_persists() -> None:
    svc = FinalReportGeneratorService()
    report = _report_with_content(
        {"executive_summary": {"value": "Internal research draft. Human review required."}}
    )
    db = _db_returning(report)

    resp = await svc.validate_final_report(db, report.id)

    # The fix: validation JSON is committed, not just flushed.
    db.flush.assert_awaited()
    db.commit.assert_awaited()

    # It is attached to the report row (what the detail page reads).
    assert report.safety_validation_json is not None
    assert report.safety_validation_json["passed"] is True
    assert report.schema_validation_json is not None
    assert "is_valid" in report.schema_validation_json

    # Response semantics unchanged.
    assert resp.safety_valid is True
    assert resp.human_review_required is True


@pytest.mark.asyncio
async def test_validate_schema_false_allowed_with_safety_true() -> None:
    svc = FinalReportGeneratorService()
    # Minimal content → schema invalid (missing required sections) but safe.
    report = _report_with_content({"executive_summary": {"value": "Internal draft."}})
    db = _db_returning(report)

    resp = await svc.validate_final_report(db, report.id)

    assert resp.schema_valid is False
    assert resp.safety_valid is True
    assert resp.human_review_required is True
    db.commit.assert_awaited()


@pytest.mark.asyncio
async def test_validate_no_forbidden_in_persisted_json() -> None:
    svc = FinalReportGeneratorService()
    report = _report_with_content({"executive_summary": {"value": "Internal draft."}})
    db = _db_returning(report)

    await svc.validate_final_report(db, report.id)

    _assert_no_forbidden(json.dumps(report.safety_validation_json))
    _assert_no_forbidden(json.dumps(report.schema_validation_json))


@pytest.mark.asyncio
async def test_validate_not_found_does_not_commit() -> None:
    svc = FinalReportGeneratorService()
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute.return_value = result

    with pytest.raises(ValueError, match="not found"):
        await svc.validate_final_report(db, uuid.uuid4())
    db.commit.assert_not_awaited()
