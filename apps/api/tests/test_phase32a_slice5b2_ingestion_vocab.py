"""
Phase 32A Slice 5B.2 — new OCR failure-code vocabulary (pure, no DB/network).

Confirms the 8 new ``FAILURE_OCR_*`` codes are proper closed-vocabulary
members (never coerced to ``unknown``), map onto an EXISTING durable
``ATTEMPT_*`` status (no new status introduced), and stay within the
persistence layer's column-length guard. Complements
``test_phase32a_slice5b1_attempt_mapping.py`` (which proves the generic
artifact -> DB-row mapping works for any closed-vocabulary code) by pinning
down the SPECIFIC new codes this slice adds.
"""

from __future__ import annotations

from app.services.sources.ingestion_status import (
    ALL_FAILURE_CODES,
    ATTEMPT_EXTRACTION_FAILED,
    ATTEMPT_METADATA_ONLY,
    ATTEMPT_TIMEOUT,
    FAILURE_OCR_BUDGET_EXHAUSTED,
    FAILURE_OCR_DOCUMENT_TOO_LARGE,
    FAILURE_OCR_LOW_CONFIDENCE,
    FAILURE_OCR_MALFORMED_RESULT,
    FAILURE_OCR_PAGE_LIMIT_EXCEEDED,
    FAILURE_OCR_PROVIDER_ERROR,
    FAILURE_OCR_PROVIDER_THROTTLED,
    FAILURE_OCR_TIMEOUT,
    attempt_status_for,
    sanitize_failure_code,
)

_STATUS_MAX = 50
_FAILURE_CODE_MAX = 50

_EXPECTED_MAPPING = {
    FAILURE_OCR_DOCUMENT_TOO_LARGE: ATTEMPT_METADATA_ONLY,
    FAILURE_OCR_PAGE_LIMIT_EXCEEDED: ATTEMPT_METADATA_ONLY,
    FAILURE_OCR_TIMEOUT: ATTEMPT_TIMEOUT,
    FAILURE_OCR_PROVIDER_THROTTLED: ATTEMPT_TIMEOUT,
    FAILURE_OCR_PROVIDER_ERROR: ATTEMPT_EXTRACTION_FAILED,
    FAILURE_OCR_MALFORMED_RESULT: ATTEMPT_EXTRACTION_FAILED,
    FAILURE_OCR_LOW_CONFIDENCE: ATTEMPT_METADATA_ONLY,
    FAILURE_OCR_BUDGET_EXHAUSTED: ATTEMPT_METADATA_ONLY,
}


def test_all_ocr_failure_codes_are_closed_vocabulary_members():
    for code in _EXPECTED_MAPPING:
        assert code in ALL_FAILURE_CODES
        assert sanitize_failure_code(code) == code  # never coerced to "unknown"


def test_ocr_failure_codes_map_onto_existing_attempt_statuses_only():
    # No new ATTEMPT_* status was introduced for OCR — every code resolves
    # into one of the three pre-existing buckets.
    for code, expected_status in _EXPECTED_MAPPING.items():
        assert attempt_status_for(None, code) == expected_status


def test_ocr_failure_codes_fit_column_length_guards():
    for code in _EXPECTED_MAPPING:
        assert len(code) <= _FAILURE_CODE_MAX
    for status in _EXPECTED_MAPPING.values():
        assert len(status) <= _STATUS_MAX


def test_unrecognized_ocr_looking_code_is_coerced_to_unknown():
    # Sanity check the closed-vocabulary guarantee itself: a plausible-looking
    # but non-member code is never passed through.
    assert sanitize_failure_code("ocr_something_made_up") == "unknown"
