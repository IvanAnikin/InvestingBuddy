"""
Phase 31 hotfix — surfacing metadata-only PRIMARY-SOURCE references in the memo.

The full-analysis path emits ``data_quality="metadata_only"`` T1 EvidenceItems
(issuer IR profile / annual-report index / press index) with NO network call.
Before this hotfix those verified references were invisible in the report because
``_primary_document_summary`` counts only extracted excerpts and ``_primary_facts``
counts only high-confidence parsed facts.

These tests pin the new behaviour:
  * ``_source_reference_summary`` classifies metadata-only references vs extracted
    documents vs parsed facts (a reference is NEITHER a document text nor a fact).
  * ``_build_research_memo`` gains a THIRD honest branch — references present but
    no extraction — while the two existing branches (extracted evidence / fully
    empty) stay back-compatible.
  * Metadata-only references never flip the T1/T2 checklist and never introduce a
    forbidden rating / valuation term; a real BAE (BA.LSE) reference stays "BAE",
    never "Boeing".
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.services import safety_terms
from app.services.final_report_generator import (
    _build_research_memo,
    _has_t1_t2_evidence,
    run_safety_gate,
)
from app.services.llm.council import _primary_facts, _source_reference_summary
from app.services.llm.schemas import CouncilResult
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import CompanyIrConnector
from app.services.sources.evidence import PrimaryFactRef, build_evidence_item
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
)
from app.services.sources.verified_issuer_sources import get_verified_issuer_source

# Reuse the thin-content fixture from the Phase 31 memo tests so the memo has the
# same already-assembled report sections it reads (source_citation_appendix etc.).
from tests.test_phase31_research_memo import _thin_report_content

_FORBIDDEN = (
    "buy",
    "sell",
    "hold",
    "watch",
    "price target",
    "fair value",
    "intrinsic value",
    "upside",
    "downside",
)


def _ref_item(
    *,
    id: str,
    source_type: str,
    title: str,
    url: str,
    tier: str = T1_PRIMARY_COMPANY_SOURCE,
    data_quality: str = "metadata_only",
    requires_translation: bool = False,
) -> Any:
    """A metadata-only PRIMARY-source reference EvidenceItem (no extracted text)."""
    return build_evidence_item(
        id=id,
        source_id="company_ir",
        source_name="Test Issuer IR",
        content_source_tier=tier,
        source_type=source_type,
        title=title,
        url=url,
        data_quality=data_quality,
        requires_translation=requires_translation,
        warnings=["Metadata only — page content / document text is not extracted."],
    )


def _metadata_only_reference_set() -> list[Any]:
    return [
        _ref_item(
            id="IRPROFILE",
            source_type="company_ir_profile",
            title="Test Issuer — Investor Relations",
            url="https://www.testissuer.com/investors",
        ),
        _ref_item(
            id="ARINDEX",
            source_type="company_ir_annual_reports_index",
            title="Test Issuer — Annual reports",
            url="https://www.testissuer.com/investors/annual-reports",
        ),
        _ref_item(
            id="PRINDEX",
            source_type="company_ir_press_release_index",
            title="Test Issuer — Newsroom",
            url="https://www.testissuer.com/media",
        ),
    ]


# ---------------------------------------------------------------------------
# 1) _source_reference_summary — counts + reference rows
# ---------------------------------------------------------------------------


def test_source_reference_summary_counts_metadata_only_references() -> None:
    summary = _source_reference_summary(_metadata_only_reference_set())
    counts = summary["counts"]
    assert counts["primary_source_reference_count"] == 3
    assert counts["metadata_only_source_count"] == 3
    assert counts["extracted_primary_document_count"] == 0
    # Only the annual-report INDEX is a document reference (profile/press are not).
    assert counts["primary_document_reference_count"] == 1

    refs = summary["references"]
    assert len(refs) == 3
    for row in refs:
        assert row["title"]
        assert row["tier"] == T1_PRIMARY_COMPANY_SOURCE
        assert row["reference_type"] in {"ir_profile", "filing_index", "press_index"}
        assert "domain" in row
    # The reference rows carry no fabricated document text and no forbidden terms.
    joined = " ".join(str(r).lower() for r in refs)
    assert not any(term in joined for term in _FORBIDDEN)


# ---------------------------------------------------------------------------
# 2) A parsed high-confidence fact is a FACT, never a reference
# ---------------------------------------------------------------------------


def test_financial_fact_is_a_fact_not_a_reference() -> None:
    fact_item = build_evidence_item(
        id="F1",
        source_id="company_ir",
        source_name="Test Issuer IR",
        content_source_tier=T1_PRIMARY_FILING,
        source_type="company_ir_financial_fact",
        title="Test Issuer — Annual report 2024",
        url="https://www.testissuer.com/ar-2024.pdf",
        data_quality="B",
        primary_fact=PrimaryFactRef(
            field="revenue",
            value="20,616 million",
            numeric_value=20616.0,
            unit="million",
            currency="EUR",
            period="FY2024",
            confidence="high",
            source_url="https://www.testissuer.com/ar-2024.pdf",
        ),
    )
    items = _metadata_only_reference_set() + [fact_item]

    summary = _source_reference_summary(items)
    # The fact item is neither an extracted document nor a metadata-only reference.
    assert summary["counts"]["extracted_primary_document_count"] == 0
    assert summary["counts"]["metadata_only_source_count"] == 3
    assert summary["counts"]["primary_source_reference_count"] == 3
    fact_titles = {r["source_type"] for r in summary["references"]}
    assert "company_ir_financial_fact" not in fact_titles

    # But it IS surfaced as a high-confidence primary fact.
    facts = _primary_facts(items)
    assert len(facts) == 1
    assert facts[0]["field"] == "revenue"
    assert facts[0]["confidence"] == "high"


# ---------------------------------------------------------------------------
# 3) _build_research_memo — references present, no extraction (Branch B)
# ---------------------------------------------------------------------------


def _reference_council() -> CouncilResult:
    summary = _source_reference_summary(_metadata_only_reference_set())
    counts = dict(summary["counts"])
    counts["source_gap_count"] = 1
    return CouncilResult(
        llm_used=False,
        primary_source_references=summary["references"],
        source_reference_counts=counts,
        source_gaps=["Annual-report links not identified without live extraction."],
    )


def test_memo_surfaces_metadata_only_references_when_no_extraction() -> None:
    memo = _build_research_memo(
        _thin_report_content(),
        _reference_council(),
        source_tier="T1_primary_company_source",
    )
    pes = memo["primary_evidence_summary"]
    assert pes["primary_source_reference_count"] > 0
    assert pes["report_primary_fact_count"] == 0
    assert pes["primary_document_count"] == 0
    assert pes["extracted_document_text_available"] is False
    assert pes["primary_facts_available"] is False

    note = pes["note"].lower()
    assert "reference" in note
    assert "not extracted" in note or "no primary financial facts" in note

    assert pes["primary_source_references"]["value"]
    for row in pes["primary_source_references"]["value"]:
        assert row["title"]
        assert row["reference_type"]

    # The honest connector gap is carried through.
    assert pes["source_gaps"]["value"]

    # The source appendix no longer implies zero source references.
    assert memo["source_appendix"]["primary_source_reference_count"] > 0
    assert memo["source_appendix"]["metadata_only_source_count"] == 3


# ---------------------------------------------------------------------------
# 4) Regression — no references / docs / facts keeps the honest-empty branch
# ---------------------------------------------------------------------------


def test_memo_empty_evidence_keeps_honest_empty_branch() -> None:
    memo = _build_research_memo(
        _thin_report_content(),
        CouncilResult.disabled(),
        source_tier="T5_api_aggregator",
    )
    pes = memo["primary_evidence_summary"]
    assert pes["primary_document_count"] == 0
    assert pes["report_primary_fact_count"] == 0
    assert pes["primary_source_reference_count"] == 0
    assert "0 primary facts" in pes["note"]["value"]
    assert pes["note"]["provenance"] == "missing_data"


# ---------------------------------------------------------------------------
# 5) Regression — an extracted document keeps Branch A (primary_document_count==1)
# ---------------------------------------------------------------------------


def _extracted_doc_council() -> CouncilResult:
    return CouncilResult(
        llm_used=True,
        provider="fake",
        model="fake-council",
        primary_documents=[
            {
                "title": "Annual report 2024",
                "domain": "testissuer.com",
                "tier": T1_PRIMARY_FILING,
                "excerpt_count": 8,
                "fact_count": 0,
                "requires_translation": False,
                "warnings": [],
            }
        ],
    )


def test_memo_extracted_document_keeps_branch_a() -> None:
    memo = _build_research_memo(
        _thin_report_content(),
        _extracted_doc_council(),
        source_tier="T1_primary_filing",
    )
    pes = memo["primary_evidence_summary"]
    assert pes["primary_document_count"] == 1
    assert pes["extracted_document_text_available"] is True


# ---------------------------------------------------------------------------
# 6) Metadata-only references do NOT complete the T1/T2 checklist
# ---------------------------------------------------------------------------


def test_metadata_only_references_do_not_complete_t1_t2_checklist() -> None:
    assert (
        _has_t1_t2_evidence(
            "T6_model_estimate",
            [],
            [],
        )
        is False
    )


# ---------------------------------------------------------------------------
# 7) The reference-carrying memo passes the report safety gate
# ---------------------------------------------------------------------------


def test_reference_memo_passes_safety_gate() -> None:
    memo = _build_research_memo(
        _thin_report_content(),
        _reference_council(),
        source_tier="T1_primary_company_source",
    )
    report_content = {"research_memo": memo}
    assert run_safety_gate(report_content).passed is True

    # Every memo field except the exempt disallowed_outputs notice is clean.
    for key, value in memo.items():
        if key == "disallowed_outputs":
            continue
        assert safety_terms.scan_value(value) == [], f"forbidden term in memo.{key}"


# ---------------------------------------------------------------------------
# 8) A real BAE (BA.LSE) reference stays "BAE" — never "Boeing"
# ---------------------------------------------------------------------------


def test_ba_lse_reference_is_bae_not_boeing() -> None:
    src = get_verified_issuer_source("BA", "LSE")
    assert src is not None and "BAE" in (src.company_name or "")
    conn = CompanyIrConnector(verified_source=src)
    res = asyncio.run(
        conn.search_company(
            CompanyContext(ticker="BA", exchange="LSE"),
            QueryContext(max_items=5),
        )
    )
    summary = _source_reference_summary(res.evidence_items)
    assert summary["references"], "expected a metadata-only IR reference for BAE"
    joined = " ".join(r["title"] for r in summary["references"])
    assert "BAE" in joined
    assert "Boeing" not in joined
