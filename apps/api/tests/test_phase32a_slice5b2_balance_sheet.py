"""
Phase 32A Slice 5B.2 — balance-sheet identity check (assets = liabilities + equity).

Fully offline and deterministic; mirrors the existing subtotal-check test
pattern in ``test_phase32a_slice5_validation.py``. No network, no LLM, no DB.

Covers:
  * All three labels present + identity reconciles -> all three stay validated.
  * All three present + identity does NOT reconcile -> all three downgraded to
    excerpt_only (no way to single out which figure is wrong).
  * Any label missing -> check does not run at all (existing behaviour for the
    other two untouched).
"""

from __future__ import annotations

from app.core.config import Settings
from app.services.sources.extracted_fact_validator import (
    VALIDATION_EXCERPT_ONLY,
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import (
    METHOD_NATIVE_PDF,
    STATUS_EXTRACTED,
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.primary_fact_parser import FIELD_TOTAL_ASSETS

ISSUER = IssuerContext(
    company_name="Compagnie Financiere Richemont SA",
    ticker="CFR",
    reporting_currency="EUR",
    default_period="2024",
)
SCALE_CUE = "All figures are stated in millions of euros (EUR)."


def _table(rows, *, page=12, index=1):
    return ExtractedTable(
        table_location=f"p{page}:t{index}",
        table_index=index,
        page_number=page,
        rows=rows,
        row_count=len(rows),
        col_count=max((len(r) for r in rows), default=0),
        extraction_method=METHOD_NATIVE_PDF,
        confidence=0.7,
    )


def _extraction(tables, *, page=12):
    excerpts = [
        PrimaryDocumentExcerpt(
            excerpt_id="X1",
            text=SCALE_CUE,
            page_number=page,
            extraction_method=METHOD_NATIVE_PDF,
            confidence=0.6,
            char_count=len(SCALE_CUE),
        )
    ]
    return PrimaryDocumentExtraction(
        content_hash="0" * 64,
        mime_type="application/pdf",
        extraction_method=METHOD_NATIVE_PDF,
        status=STATUS_EXTRACTED,
        page_count=20,
        excerpts=excerpts,
        tables=list(tables),
    )


def _by_label(facts: list[ValidatedFact], label: str) -> ValidatedFact | None:
    return next((f for f in facts if f.label == label), None)


def test_balance_sheet_identity_reconciles_stays_validated():
    table = _table(
        [
            ["", "2024"],
            ["Total liabilities", "600"],
            ["Total equity", "400"],
            ["Total assets", "1,000"],
        ]
    )
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=Settings())
    assets = _by_label(facts, FIELD_TOTAL_ASSETS)
    liabilities = _by_label(facts, "total_liabilities")
    equity = _by_label(facts, "total_equity")
    assert assets is not None and assets.validation_status == VALIDATION_VALIDATED
    assert liabilities is not None and liabilities.validation_status == VALIDATION_VALIDATED
    assert equity is not None and equity.validation_status == VALIDATION_VALIDATED


def test_balance_sheet_identity_mismatch_downgrades_all_three():
    # 600 + 400 = 1,000 != stated total assets (1,200) -> genuine mismatch.
    table = _table(
        [
            ["", "2024"],
            ["Total liabilities", "600"],
            ["Total equity", "400"],
            ["Total assets", "1,200"],
        ]
    )
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=Settings())
    assets = _by_label(facts, FIELD_TOTAL_ASSETS)
    liabilities = _by_label(facts, "total_liabilities")
    equity = _by_label(facts, "total_equity")
    assert assets is not None and assets.validation_status == VALIDATION_EXCERPT_ONLY
    assert liabilities is not None and liabilities.validation_status == VALIDATION_EXCERPT_ONLY
    assert equity is not None and equity.validation_status == VALIDATION_EXCERPT_ONLY
    assert any("balance-sheet identity" in n.lower() for n in assets.validation_notes)


def test_balance_sheet_identity_check_skipped_when_equity_missing():
    # Only assets + liabilities present — the identity cannot be checked, so
    # neither is touched by this check (existing subtotal/label behaviour
    # applies independently).
    table = _table(
        [
            ["", "2024"],
            ["Total liabilities", "600"],
            ["Total assets", "1,000"],
        ]
    )
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=Settings())
    assets = _by_label(facts, FIELD_TOTAL_ASSETS)
    liabilities = _by_label(facts, "total_liabilities")
    assert assets is not None and assets.validation_status == VALIDATION_VALIDATED
    assert liabilities is not None and liabilities.validation_status == VALIDATION_VALIDATED
    assert not any("balance-sheet identity" in n.lower() for n in assets.validation_notes)
