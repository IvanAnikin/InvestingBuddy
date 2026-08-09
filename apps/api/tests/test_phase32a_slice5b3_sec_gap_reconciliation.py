"""Phase 32A Slice 5B.3 — reconcile the stale SEC "full filing text not
retrieved" gap with what deep primary-document ingestion actually did.

``SecEdgarConnector.fetch_filings()`` (sec_edgar.py) always attaches a gap
saying the full filing text was not retrieved whenever it returns SEC filing
METADATA — even when the SEPARATE, later deep body-fetch (Slice 5B.1's
``sec_filing_documents.py``, wired into ``council.py``'s primary-document
ingestion) has already extracted that exact filing's body for this same run.
The connector itself has no visibility into that later outcome, so the fix
lives at the report-assembly point in ``final_report_generator.py`` where
both facts — the gap message and the extraction outcome (already surfaced via
``primary_documents``, the same data used for
``primary_document_extracted_count`` in ``source_summary_json.llm_council``)
— are available together.
"""

from __future__ import annotations

from app.services.final_report_generator import (
    _reconcile_stale_sec_gaps,
    _sec_primary_document_extracted,
)

_STALE_GAP = (
    "SEC filing metadata is sourced (transport T2 / content T1), but full "
    "filing text is not retrieved in this phase; the full-text fetcher is "
    "pending. (planned: Phase 29B.x)"
)
_OTHER_SEC_GAP = (
    "SEC filing body was not fetched (blocked_ip_pin_mismatch); filing text "
    "is not extracted."
)
_UNRELATED_GAP = (
    "SEC EDGAR covers US issuers only; ACME on exchange 'LSE' is not "
    "SEC-eligible."
)


def _sec_doc(*, excerpt_count: int = 0, fact_count: int = 0, domain: str = "sec.gov") -> dict:
    return {
        "title": "10-Q",
        "domain": domain,
        "tier": "T1_primary_filing",
        "excerpt_count": excerpt_count,
        "fact_count": fact_count,
        "requires_translation": False,
        "warnings": [],
    }


def test_no_sec_document_extracted_keeps_gap_unchanged() -> None:
    """Pre-5B.1 / OFF-flag case: metadata present, no deep SEC extraction —
    the honest gap must survive untouched."""
    gaps = [_STALE_GAP, _OTHER_SEC_GAP, _UNRELATED_GAP]
    assert _sec_primary_document_extracted([]) is False
    assert _reconcile_stale_sec_gaps(gaps, []) == gaps


def test_metadata_only_sec_document_does_not_suppress_gap() -> None:
    """A SEC document that was grouped but never actually extracted (0
    excerpts, 0 facts) must not be treated as a successful extraction."""
    primary_documents = [_sec_doc(excerpt_count=0, fact_count=0)]
    gaps = [_STALE_GAP, _UNRELATED_GAP]
    assert _sec_primary_document_extracted(primary_documents) is False
    assert _reconcile_stale_sec_gaps(gaps, primary_documents) == gaps


def test_sec_document_extracted_suppresses_only_the_stale_gap() -> None:
    """When a SEC-sourced primary document WAS extracted this run, the stale
    gap is dropped — OTHER gaps (including SEC's own still-honest
    blocked-fetch gap, and unrelated connector gaps) remain untouched."""
    primary_documents = [_sec_doc(excerpt_count=2, fact_count=1)]
    gaps = [_STALE_GAP, _OTHER_SEC_GAP, _UNRELATED_GAP]

    assert _sec_primary_document_extracted(primary_documents) is True
    result = _reconcile_stale_sec_gaps(gaps, primary_documents)

    assert _STALE_GAP not in result
    assert _OTHER_SEC_GAP in result
    assert _UNRELATED_GAP in result
    assert len(result) == 2


def test_sec_document_extracted_via_fact_only_also_suppresses_gap() -> None:
    """A fact-only SEC extraction (no prose excerpt — the real AAPL 10-Q/8-K
    shape) counts as a successful extraction too."""
    primary_documents = [_sec_doc(excerpt_count=0, fact_count=1)]
    gaps = [_STALE_GAP]
    assert _reconcile_stale_sec_gaps(gaps, primary_documents) == []


def test_non_sec_domain_extraction_does_not_suppress_sec_gap() -> None:
    """A successfully-extracted company-IR document (different domain) must
    NOT suppress the SEC-specific stale gap."""
    primary_documents = [
        _sec_doc(excerpt_count=3, fact_count=0, domain="example-issuer.com")
    ]
    gaps = [_STALE_GAP]
    assert _sec_primary_document_extracted(primary_documents) is False
    assert _reconcile_stale_sec_gaps(gaps, primary_documents) == gaps


def test_www_prefixed_sec_domain_still_matches() -> None:
    """council.py strips a leading 'www.' from the grouped domain, but a
    subdomain like data.sec.gov should still be recognised."""
    primary_documents = [_sec_doc(excerpt_count=1, domain="data.sec.gov")]
    assert _sec_primary_document_extracted(primary_documents) is True


def test_malformed_primary_document_entries_are_ignored_not_crashed() -> None:
    """Non-dict entries in primary_documents (defensive) never raise."""
    assert _sec_primary_document_extracted(["not-a-dict", None, 42]) is False
    gaps = [_STALE_GAP]
    assert _reconcile_stale_sec_gaps(gaps, ["not-a-dict", None]) == gaps
