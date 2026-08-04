"""
Phase 32A Slice 5, part 3a — stricter validation of table/OCR-derived facts +
gated evidence-pack primary_document floor/cap.

Fully OFFLINE and deterministic: every ``PrimaryDocumentExtraction`` and every
``EvidencePack`` is built in-code; no network, no LLM, no DB.

Covers:
  A. ``extracted_fact_validator``:
     * clean labelled/period/currency+scale table cell → validated fact;
     * ambiguous multi-column table (no period header) → excerpt_only, no fact;
     * OCR-method fact → confidence downgraded, never ``high``; a below-min-
       confidence OCR fact → excerpt_only;
     * subtotal that does not reconcile with its components → excerpt_only
       (components stay validated);
     * same (label, period) agreeing across methods → boosted; conflicting across
       methods → rejected.
  B. Evidence-pack budgeter (``primary_document`` floor + cap):
     * flag ON: floor guarantees a primary-document slot and the cap bounds a
       flood WITHOUT reducing the 3 financial-fact floor or the 8 news cap;
     * flag OFF: byte-identical to the current category path (primary-document
       uncapped; a non-primary-document pack is untouched).
"""

from __future__ import annotations

from collections import Counter

from app.core.config import Settings
from app.services.llm.evidence_budget import (
    CATEGORY_FINANCIAL_FACT,
    CATEGORY_MATERIAL_NEWS,
    CATEGORY_PRIMARY_DOCUMENT,
    apply_evidence_budget,
    evidence_category,
)
from app.services.llm.schemas import (
    TIER_T1_PRIMARY_FILING,
    TIER_T4_QUALITY_MEDIA,
    EvidenceItem,
    EvidencePack,
)
from app.services.sources.extracted_fact_validator import (
    VALIDATION_EXCERPT_ONLY,
    VALIDATION_REJECTED,
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import (
    METHOD_NATIVE_PDF,
    METHOD_OCR,
    STATUS_EXTRACTED,
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.primary_fact_parser import (
    FIELD_NET_INCOME,
    FIELD_REVENUE,
    FIELD_TOTAL_DEBT,
)

# --------------------------------------------------------------------------- #
# Fixtures / helpers                                                          #
# --------------------------------------------------------------------------- #

ISSUER = IssuerContext(
    company_name="Compagnie Financiere Richemont SA",
    ticker="CFR",
    reporting_currency="EUR",
    default_period="2024",
)

# A currency/scale cue lives in an excerpt on the same page as the table, since a
# bounded ExtractedTable carries no caption.
SCALE_CUE = "All figures are stated in millions of euros (EUR)."


def _table(rows, *, method=METHOD_NATIVE_PDF, page=12, index=1, confidence=0.7):
    return ExtractedTable(
        table_location=f"p{page}:t{index}",
        table_index=index,
        page_number=page,
        rows=rows,
        row_count=len(rows),
        col_count=max((len(r) for r in rows), default=0),
        extraction_method=method,
        confidence=confidence,
    )


def _extraction(tables, *, page=12, method=METHOD_NATIVE_PDF, cue=SCALE_CUE):
    excerpts = []
    if cue:
        excerpts.append(
            PrimaryDocumentExcerpt(
                excerpt_id="X1",
                text=cue,
                page_number=page,
                extraction_method=method,
                confidence=0.6,
                char_count=len(cue),
            )
        )
    return PrimaryDocumentExtraction(
        content_hash="0" * 64,
        mime_type="application/pdf",
        extraction_method=method,
        status=STATUS_EXTRACTED,
        page_count=20,
        excerpts=excerpts,
        tables=list(tables),
    )


def _validated(facts: list[ValidatedFact]) -> list[ValidatedFact]:
    return [f for f in facts if f.validation_status == VALIDATION_VALIDATED]


def _by_label(facts: list[ValidatedFact], label: str) -> ValidatedFact | None:
    return next((f for f in facts if f.label == label), None)


def _cfg(**overrides) -> Settings:
    return Settings(**overrides)


# --------------------------------------------------------------------------- #
# A1. Clean table cell → validated fact                                       #
# --------------------------------------------------------------------------- #


def test_clean_table_cell_is_validated():
    table = _table(
        [
            ["", "2024", "2023"],
            ["Revenue", "20,616", "19,182"],
        ]
    )
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=_cfg())
    rev_2024 = next(
        f for f in facts if f.label == FIELD_REVENUE and f.period == "2024"
    )
    assert rev_2024.validation_status == VALIDATION_VALIDATED
    assert rev_2024.value_numeric == 20616.0
    assert rev_2024.value_text == "20,616"
    assert rev_2024.currency == "EUR"
    assert rev_2024.scale == "million"
    assert rev_2024.unit == "currency_amount"
    assert rev_2024.page_number == 12
    assert rev_2024.table_location == "p12:t1"
    assert rev_2024.extraction_method == METHOD_NATIVE_PDF
    assert rev_2024.needs_human_review is True
    # Both period columns are recovered as distinct facts (2024 + 2023).
    assert {f.period for f in facts if f.label == FIELD_REVENUE} == {"2024", "2023"}


def test_maps_cleanly_onto_extracted_fact_columns():
    table = _table([["", "2024"], ["Net income", "93,736"]])
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=_cfg())
    fact = _by_label(facts, FIELD_NET_INCOME)
    assert fact is not None
    # Field names line up 1:1 with the ExtractedFact ORM columns.
    for col in (
        "label",
        "value_numeric",
        "value_text",
        "unit",
        "currency",
        "scale",
        "period",
        "page_number",
        "table_location",
        "extraction_method",
        "confidence",
        "validation_status",
        "needs_human_review",
    ):
        assert hasattr(fact, col)


def test_unknown_issuer_context_blocks_validation():
    table = _table([["", "2024"], ["Revenue", "20,616"]])
    facts = validate_extracted_facts(
        _extraction([table]), issuer_context=IssuerContext(), cfg=_cfg()
    )
    assert _validated(facts) == []
    rev = _by_label(facts, FIELD_REVENUE)
    assert rev is not None and rev.validation_status == VALIDATION_EXCERPT_ONLY


# --------------------------------------------------------------------------- #
# A2. Ambiguous multi-column table → excerpt_only, no fact                    #
# --------------------------------------------------------------------------- #


def test_ambiguous_multi_column_table_is_excerpt_only():
    # No period header row, and the labelled row has two DIFFERENT magnitudes →
    # the column/period mapping is ambiguous, so no structured fact is emitted.
    table = _table(
        [
            ["Segment", "Watches", "Jewellery"],
            ["Revenue", "8,200", "12,416"],
        ]
    )
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=_cfg())
    assert _validated(facts) == []
    rev = _by_label(facts, FIELD_REVENUE)
    assert rev is not None and rev.validation_status == VALIDATION_EXCERPT_ONLY


def test_money_without_currency_or_scale_is_excerpt_only():
    table = _table([["", "2024"], ["Revenue", "20,616"]], page=5)
    # No scale/currency cue anywhere (issuer has no reporting_currency either).
    issuer = IssuerContext(company_name="Acme", ticker="ACM", default_period="2024")
    facts = validate_extracted_facts(
        _extraction([table], page=5, cue=None), issuer_context=issuer, cfg=_cfg()
    )
    rev = _by_label(facts, FIELD_REVENUE)
    assert rev is not None and rev.validation_status == VALIDATION_EXCERPT_ONLY


# --------------------------------------------------------------------------- #
# A3. OCR-derived → downgraded, never high; below-min → excerpt_only          #
# --------------------------------------------------------------------------- #


def test_ocr_fact_confidence_downgraded_never_high():
    # A high-quality OCR table (0.8) that clears the min but must never be 'high'.
    table = _table(
        [["", "2024"], ["Revenue", "20,616"]], method=METHOD_OCR, confidence=0.8
    )
    facts = validate_extracted_facts(
        _extraction([table], method=METHOD_OCR), issuer_context=ISSUER, cfg=_cfg()
    )
    rev = _by_label(facts, FIELD_REVENUE)
    assert rev is not None
    assert rev.validation_status == VALIDATION_VALIDATED
    assert rev.ocr_derived is True
    assert rev.extraction_method == METHOD_OCR
    assert rev.confidence < 0.75  # never auto-high
    assert rev.confidence >= _cfg().primary_document_min_extraction_confidence


def test_ocr_fact_below_min_confidence_is_excerpt_only():
    # A low-quality OCR table (0.6): 0.6 * 0.85 = 0.51 < the 0.6 minimum.
    table = _table(
        [["", "2024"], ["Revenue", "20,616"]], method=METHOD_OCR, confidence=0.6
    )
    facts = validate_extracted_facts(
        _extraction([table], method=METHOD_OCR), issuer_context=ISSUER, cfg=_cfg()
    )
    rev = _by_label(facts, FIELD_REVENUE)
    assert rev is not None and rev.validation_status == VALIDATION_EXCERPT_ONLY


# --------------------------------------------------------------------------- #
# A4. Subtotal mismatch → excerpt_only (components untouched)                 #
# --------------------------------------------------------------------------- #


def test_subtotal_mismatch_downgrades_subtotal_only():
    # short-term (30) + long-term (80) = 110 != stated total debt (100).
    table = _table(
        [
            ["", "2024"],
            ["Short-term debt", "30"],
            ["Long-term debt", "80"],
            ["Total debt", "100"],
        ]
    )
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=_cfg())
    total = _by_label(facts, FIELD_TOTAL_DEBT)
    assert total is not None and total.validation_status == VALIDATION_EXCERPT_ONLY
    # Components remain validated — only the subtotal is downgraded.
    short = _by_label(facts, "short_term_debt")
    long = _by_label(facts, "long_term_debt")
    assert short is not None and short.validation_status == VALIDATION_VALIDATED
    assert long is not None and long.validation_status == VALIDATION_VALIDATED


def test_subtotal_reconciles_stays_validated():
    table = _table(
        [
            ["", "2024"],
            ["Short-term debt", "30"],
            ["Long-term debt", "70"],
            ["Total debt", "100"],
        ]
    )
    facts = validate_extracted_facts(_extraction([table]), issuer_context=ISSUER, cfg=_cfg())
    total = _by_label(facts, FIELD_TOTAL_DEBT)
    assert total is not None and total.validation_status == VALIDATION_VALIDATED


# --------------------------------------------------------------------------- #
# A5. Cross-method agreement → boost; conflict → rejected                     #
# --------------------------------------------------------------------------- #


def test_cross_method_agreement_boosts_confidence():
    native = _table([["", "2024"], ["Revenue", "20,616"]], method=METHOD_NATIVE_PDF, index=1)
    ocr = _table(
        [["", "2024"], ["Revenue", "20,616"]], method=METHOD_OCR, index=2, confidence=0.8
    )
    extraction = _extraction([native, ocr])
    baseline = validate_extracted_facts(
        _extraction([native]), issuer_context=ISSUER, cfg=_cfg()
    )
    base_rev = next(f for f in baseline if f.label == FIELD_REVENUE and f.period == "2024")

    facts = validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=_cfg())
    rev = next(f for f in facts if f.label == FIELD_REVENUE and f.period == "2024")
    assert rev.validation_status == VALIDATION_VALIDATED
    assert set(rev.methods) == {METHOD_NATIVE_PDF, METHOD_OCR}
    assert rev.confidence > base_rev.confidence  # corroboration raises confidence
    # A single reconciled fact, not one per method.
    assert len([f for f in facts if f.label == FIELD_REVENUE and f.period == "2024"]) == 1


def test_cross_method_conflict_is_rejected():
    native = _table([["", "2024"], ["Revenue", "20,616"]], method=METHOD_NATIVE_PDF, index=1)
    ocr = _table(
        [["", "2024"], ["Revenue", "99,999"]], method=METHOD_OCR, index=2, confidence=0.8
    )
    facts = validate_extracted_facts(
        _extraction([native, ocr]), issuer_context=ISSUER, cfg=_cfg()
    )
    rev = next(f for f in facts if f.label == FIELD_REVENUE and f.period == "2024")
    assert rev.validation_status == VALIDATION_REJECTED
    # A rejected contradiction never asserts a value.
    assert rev.value_numeric is None


def test_no_tables_returns_empty():
    facts = validate_extracted_facts(
        _extraction([], cue=None), issuer_context=ISSUER, cfg=_cfg()
    )
    assert facts == []


# --------------------------------------------------------------------------- #
# B. Evidence-pack primary_document floor + cap                               #
# --------------------------------------------------------------------------- #


def _fin(i: int) -> EvidenceItem:
    return EvidenceItem(
        id=f"F{i}",
        source_tier=TIER_T1_PRIMARY_FILING,
        content_tier=TIER_T1_PRIMARY_FILING,
        transport_tier="T2_regulator_or_gov",
        source_type="sec_financial_statement",
        title=f"AAPL FY2024 statement {i}",
        excerpt=f"revenue line {i}",
        data_quality="B_single_credible",
        fields_supported=["revenue_usd_m"],
    )


def _news(i: int) -> EvidenceItem:
    return EvidenceItem(
        id=f"N{i}",
        source_tier=TIER_T4_QUALITY_MEDIA,
        content_tier=TIER_T4_QUALITY_MEDIA,
        source_type="news",
        title=f"Distinct market story {i}",
        url=f"https://news.example.com/{i}",
        excerpt=f"body {i}",
        fields_supported=["catalyst"],
        relevance_level="medium",
    )


def _pdoc(i: int) -> EvidenceItem:
    return EvidenceItem(
        id=f"P{i}",
        source_tier=TIER_T1_PRIMARY_FILING,
        content_tier=TIER_T1_PRIMARY_FILING,
        source_type="company_ir_annual_report_excerpt",
        title=f"Annual report excerpt {i}",
        excerpt=f"excerpt body {i}",
        data_quality="extracted",
        fields_supported=["business_description"],
    )


def _cfg_pd_on(**overrides) -> Settings:
    base = dict(
        llm_council_evidence_budgets_enabled=True,
        primary_document_ingestion_enabled=True,
        llm_council_evidence_max_items=20,
        llm_council_evidence_financial_floor=3,
        llm_council_evidence_news_cap=8,
        primary_document_evidence_floor=1,
        primary_document_evidence_cap=6,
    )
    base.update(overrides)
    return Settings(**base)


def _cfg_pd_off(**overrides) -> Settings:
    base = dict(
        llm_council_evidence_budgets_enabled=True,
        primary_document_ingestion_enabled=False,
        llm_council_evidence_max_items=20,
        llm_council_evidence_financial_floor=3,
        llm_council_evidence_news_cap=8,
    )
    base.update(overrides)
    return Settings(**base)


def _categories(pack: EvidencePack) -> Counter:
    return Counter(evidence_category(i) for i in pack.evidence_items)


def test_pd_flood_capped_without_touching_financial_or_news():
    # 3 financial facts + 12 news + 10 primary-document excerpts.
    items = [_fin(i) for i in range(3)] + [_news(i) for i in range(12)] + [
        _pdoc(i) for i in range(10)
    ]
    out = apply_evidence_budget(EvidencePack(evidence_items=items), cfg=_cfg_pd_on())
    counts = _categories(out)
    assert counts[CATEGORY_FINANCIAL_FACT] == 3  # floor preserved
    assert counts[CATEGORY_MATERIAL_NEWS] <= 8  # news cap preserved
    assert counts[CATEGORY_PRIMARY_DOCUMENT] == 6  # capped at the primary-doc cap


def test_pd_floor_guarantees_a_slot_under_news_flood():
    # A single primary-document excerpt must survive a flood of 20 news items.
    items = [_fin(i) for i in range(3)] + [_news(i) for i in range(20)] + [_pdoc(0)]
    out = apply_evidence_budget(EvidencePack(evidence_items=items), cfg=_cfg_pd_on())
    counts = _categories(out)
    assert counts[CATEGORY_PRIMARY_DOCUMENT] >= 1  # floor guaranteed
    assert counts[CATEGORY_FINANCIAL_FACT] == 3
    assert counts[CATEGORY_MATERIAL_NEWS] <= 8


def test_flag_off_primary_document_is_not_capped():
    # With ingestion OFF the primary-document category is uncapped (current
    # behavior): a flood of 10 survives up to max_items.
    items = [_pdoc(i) for i in range(10)]
    out = apply_evidence_budget(EvidencePack(evidence_items=items), cfg=_cfg_pd_off())
    assert _categories(out)[CATEGORY_PRIMARY_DOCUMENT] == 10


def test_flag_off_matches_current_category_path_byte_for_byte():
    # For a pack with NO primary-document items, the gating must be a no-op: the
    # output with the primary-document flag on vs off is byte-identical.
    items = [_fin(i) for i in range(3)] + [_news(i) for i in range(12)]
    pack = EvidencePack(evidence_items=items)
    on = apply_evidence_budget(pack, cfg=_cfg_pd_on())
    off = apply_evidence_budget(pack, cfg=_cfg_pd_off())
    assert on.model_dump(mode="json") == off.model_dump(mode="json")
