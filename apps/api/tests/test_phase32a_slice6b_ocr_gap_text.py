"""
Phase 32A Slice 6B — Fix C8: stale "no OCR in this phase" text.

Root cause: two literal, unconditional occurrences of "no OCR in this phase"
in ``final_report_generator.py`` (inside the function building
``primary_evidence_summary``) predate Slice 5B.2's real Azure Document
Intelligence OCR adapter (now deployed and enabled) and were never updated.

These tests pin the fix: the text is now conditioned on the REAL state via
``_ocr_status_note`` — ``settings.primary_document_ocr_enabled`` and each
discovered document's real ``failure_code`` (the closed ``ingestion_status``
vocabulary) — never the old unconditional literal.

All tests run OFFLINE — pure function calls, no network, no DB, no LLM.
"""

from __future__ import annotations

from app.services.final_report_generator import _build_research_memo, _ocr_status_note
from app.services.llm.schemas import CouncilResult
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from tests.test_phase31_research_memo import _thin_report_content

_STALE_TEXT = "no OCR in this phase"


# ---------------------------------------------------------------------------
# 1. OCR disabled
# ---------------------------------------------------------------------------


def test_ocr_disabled_note(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "primary_document_ocr_enabled", False)

    memo = _build_research_memo(
        _thin_report_content(), CouncilResult.disabled(), source_tier="T5_api_aggregator"
    )
    note = memo["primary_evidence_summary"]["note"]["value"]
    assert "(OCR disabled)" in note
    assert _STALE_TEXT not in note


# ---------------------------------------------------------------------------
# 2. OCR enabled, no document candidate ever discovered (BRBY's actual case)
# ---------------------------------------------------------------------------


def test_ocr_enabled_no_candidate_discovered_note(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "primary_document_ocr_enabled", True)

    memo = _build_research_memo(
        _thin_report_content(), CouncilResult.disabled(), source_tier="T5_api_aggregator"
    )
    note = memo["primary_evidence_summary"]["note"]["value"]
    assert "(no document candidate discovered — OCR was not reached)" in note
    assert _STALE_TEXT not in note


# ---------------------------------------------------------------------------
# 3. OCR enabled, OCR attempted and failed with a specific failure code
# ---------------------------------------------------------------------------


def test_ocr_enabled_attempted_and_timed_out_note(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "primary_document_ocr_enabled", True)

    artifact = PrimaryDocumentArtifact(
        source_url="https://example-issuer.com/annual-report-2025.pdf",
        status="extraction_failed",
        failure_code="ocr_timeout",
    )
    council = CouncilResult(llm_used=False, primary_document_artifacts=[artifact])

    memo = _build_research_memo(
        _thin_report_content(), council, source_tier="T5_api_aggregator"
    )
    note = memo["primary_evidence_summary"]["note"]["value"]
    assert "(OCR attempted and failed — OCR timed out)" in note
    assert _STALE_TEXT not in note


# ---------------------------------------------------------------------------
# 4. OCR enabled, document discovered but no OCR-specific failure — honest,
#    distinct wording (never claims "no candidate discovered" when one WAS).
# ---------------------------------------------------------------------------


def test_ocr_enabled_candidate_discovered_but_ocr_not_attempted_note(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "primary_document_ocr_enabled", True)

    artifact = PrimaryDocumentArtifact(
        source_url="https://example-issuer.com/annual-report-2025.pdf",
        status="extraction_failed",
        failure_code="blocked_host",
    )
    council = CouncilResult(llm_used=False, primary_document_artifacts=[artifact])

    memo = _build_research_memo(
        _thin_report_content(), council, source_tier="T5_api_aggregator"
    )
    note = memo["primary_evidence_summary"]["note"]["value"]
    assert "document candidate discovered but OCR was not attempted for it" in note
    assert _STALE_TEXT not in note
    assert "no document candidate discovered" not in note


# ---------------------------------------------------------------------------
# 5. Direct unit tests of _ocr_status_note
# ---------------------------------------------------------------------------


def test_ocr_status_note_native_extraction_succeeded(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "primary_document_ocr_enabled", True)
    note = _ocr_status_note(CouncilResult.disabled(), doc_rows=[{"title": "AR 2025"}])
    assert note == "(OCR not eligible — native extraction succeeded)"
