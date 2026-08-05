"""Phase 32A Slice 5B.1 hotfix — SEC filing-body evidence in the report summary.

Staging validation of PR #79's CIK/preflight fix proved the end-to-end path
genuinely works: a real AAPL 10-Q body was fetched, extracted, persisted, and a
validated ``cash_and_equivalents`` fact was cited by multiple council agents (147
citations total, 42 SEC-typed). But the SAME report's
``source_summary_json.llm_council.primary_documents`` was empty and
``source_reference_counts.extracted_primary_document_count`` was 0 — because
``_DOCUMENT_SOURCE_TYPES`` in ``council.py`` was written before Slice 5B.1 added
the SEC filing-body evidence types and was never extended for them. The
underlying evidence, citations and persistence were always correct; only the
report-level DISPLAY summary silently ignored the SEC evidence class.

These tests pin the fix: SEC filing-body EvidenceItems (excerpt AND fact) are now
included in both ``_primary_document_summary`` and
``_source_reference_summary``'s ``extracted_primary_document_count`` — exactly
like the pre-existing company-IR document evidence.
"""

from __future__ import annotations

from typing import Any

from app.services.llm.council import (
    _DOCUMENT_SOURCE_TYPES,
    _primary_document_summary,
    _source_reference_summary,
)
from app.services.sources.company_evidence import (
    SEC_DOCUMENT_EXCERPT_TYPE,
    SEC_DOCUMENT_FACT_TYPE,
)
from app.services.sources.evidence import build_evidence_item
from app.services.sources.taxonomy import T1_PRIMARY_FILING, T2_REGULATOR_OR_GOV

SEC_URL = "https://www.sec.gov/Archives/edgar/data/320193/000032019324000123/aapl-10q.htm"


def _sec_excerpt_item() -> Any:
    return build_evidence_item(
        id="SECDOC1X1",
        source_id="sec_edgar",
        source_name="SEC EDGAR",
        provider_transport="SEC EDGAR (data.sec.gov)",
        provider_transport_tier=T2_REGULATOR_OR_GOV,
        content_source="Apple Inc. 10-Q filing",
        content_source_tier=T1_PRIMARY_FILING,
        source_type=SEC_DOCUMENT_EXCERPT_TYPE,
        title="10-Q filing — excerpt",
        url=SEC_URL,
        excerpt="The Company reported total net sales of $94.0 billion.",
        data_quality="B",
        confidence="high",
    )


def _sec_fact_item(*, label: str = "cash_and_equivalents") -> Any:
    return build_evidence_item(
        id="SECFACT1_1",
        source_id="sec_edgar",
        source_name="SEC EDGAR",
        provider_transport="SEC EDGAR (data.sec.gov)",
        provider_transport_tier=T2_REGULATOR_OR_GOV,
        content_source="Apple Inc. 10-Q filing",
        content_source_tier=T1_PRIMARY_FILING,
        source_type=SEC_DOCUMENT_FACT_TYPE,
        title=f"10-Q filing: {label}",
        url=SEC_URL,
        excerpt=f"{label} = 3610.0 (million, USD) [2025]",
        data_quality="B",
        confidence="high",
    )


def test_sec_evidence_types_are_in_the_document_source_type_set():
    assert SEC_DOCUMENT_EXCERPT_TYPE in _DOCUMENT_SOURCE_TYPES
    assert SEC_DOCUMENT_FACT_TYPE in _DOCUMENT_SOURCE_TYPES


def test_a_real_sec_extraction_now_appears_in_primary_documents():
    """The exact staging scenario: one filing, one extracted fact, no excerpt."""
    summary = _primary_document_summary([_sec_fact_item()])
    assert len(summary) == 1
    doc = summary[0]
    assert doc["fact_count"] == 1
    assert doc["excerpt_count"] == 0
    assert doc["domain"] == "sec.gov"


def test_sec_excerpt_and_fact_group_under_the_same_document():
    summary = _primary_document_summary([_sec_excerpt_item(), _sec_fact_item()])
    assert len(summary) == 1
    doc = summary[0]
    assert doc["excerpt_count"] == 1
    assert doc["fact_count"] == 1


def test_sec_evidence_counts_toward_extracted_primary_document_count():
    summary = _source_reference_summary([_sec_fact_item()])
    assert summary["counts"]["extracted_primary_document_count"] == 1
    # A real extraction is not a bare "reference" and not "metadata only".
    assert summary["counts"]["metadata_only_source_count"] == 0
    assert summary["counts"]["primary_source_reference_count"] == 0


def test_two_different_sec_filings_produce_two_document_groups():
    other_url = (
        "https://www.sec.gov/Archives/edgar/data/320193/000032019326000018/aapl-8k.htm"
    )
    fact_a = _sec_fact_item()
    fact_b = build_evidence_item(
        id="SECFACT2_1",
        source_id="sec_edgar",
        source_name="SEC EDGAR",
        provider_transport="SEC EDGAR (data.sec.gov)",
        provider_transport_tier=T2_REGULATOR_OR_GOV,
        content_source="Apple Inc. 8-K filing",
        content_source_tier=T1_PRIMARY_FILING,
        source_type=SEC_DOCUMENT_FACT_TYPE,
        title="8-K filing: revenue",
        url=other_url,
        excerpt="revenue = 94000.0 (million, USD) [2025]",
        data_quality="B",
        confidence="high",
    )
    summary = _primary_document_summary([fact_a, fact_b])
    assert len(summary) == 2
    counts = _source_reference_summary([fact_a, fact_b])["counts"]
    assert counts["extracted_primary_document_count"] == 2


def test_company_ir_evidence_is_unaffected_by_the_sec_addition():
    """Regression guard: the pre-existing company-IR path is untouched."""
    ir_item = build_evidence_item(
        id="IRAR1X1",
        source_id="company_ir",
        source_name="Test Issuer IR",
        content_source_tier=T1_PRIMARY_FILING,
        source_type="company_ir_annual_report_excerpt",
        title="Annual report — excerpt",
        url="https://www.testissuer.com/annual-report.pdf",
        excerpt="Some excerpt text.",
        data_quality="B",
        confidence="high",
    )
    summary = _primary_document_summary([ir_item])
    assert len(summary) == 1
    assert summary[0]["excerpt_count"] == 1
    assert summary[0]["fact_count"] == 0


def test_non_document_evidence_types_are_still_excluded():
    """A press-release / catalyst item must never be counted as a document."""
    press_item = build_evidence_item(
        id="PRESS1",
        source_id="company_ir",
        source_name="Test Issuer IR",
        content_source_tier=T1_PRIMARY_FILING,
        source_type="company_ir_press_release",
        title="Press release",
        url="https://www.testissuer.com/news/1",
        excerpt="Some press text.",
        data_quality="B",
        confidence="high",
    )
    assert _primary_document_summary([press_item]) == []
    assert _source_reference_summary([press_item])["counts"][
        "extracted_primary_document_count"
    ] == 0
