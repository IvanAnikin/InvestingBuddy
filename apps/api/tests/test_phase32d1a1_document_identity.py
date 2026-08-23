"""
Phase D1a.1 — ONE canonical document identity.

Live defect: Pandora's annual report was reachable as both
``.../Annual Report 2025`` and ``.../Annual%20Report%202025``. Those produced
two identities, so the pipeline discovered, attempted and cached the SAME
document twice — one alias timed out while the other extracted 169 pages.

The fix normalises percent-encoding in ``canonicalize_source_url`` (the shared
canonical path used by identity, ingestion attempts and the extraction cache),
so the collapse holds everywhere rather than only inside discovery.

Encoding-only, never decoding: ``a%2Fb`` and ``a/b`` are genuinely different
resources and must stay distinct.
"""

from __future__ import annotations

from app.services.sources.document_discovery import (
    discover_documents,
    document_identity,
    normalize_url_path_encoding,
)
from app.services.sources.redaction import canonicalize_source_url

RAW = "https://docs.issuer-cdn.example/v1/static/Annual Report 2025"
ENC = "https://docs.issuer-cdn.example/v1/static/Annual%20Report%202025"


# ===========================================================================
# The collapse
# ===========================================================================
def test_space_and_percent20_are_one_identity() -> None:
    assert document_identity(RAW) == document_identity(ENC)


def test_collapse_holds_in_the_shared_canonical_path() -> None:
    """Attempts and the extraction cache key on this, not on discovery."""
    assert canonicalize_source_url(RAW) == canonicalize_source_url(ENC)
    assert canonicalize_source_url(RAW).endswith("Annual%20Report%202025")


def test_escape_case_is_normalised() -> None:
    assert document_identity("https://h.example/a%2fb") == document_identity(
        "https://h.example/a%2Fb"
    )


# ===========================================================================
# What must NOT collapse
# ===========================================================================
def test_encoded_slash_is_not_conflated_with_a_real_slash() -> None:
    """Decoding %2F would merge genuinely different resources."""
    assert document_identity("https://h.example/a%2Fb") != document_identity(
        "https://h.example/a/b"
    )
    assert canonicalize_source_url("https://h.example/a%2Fb") != canonicalize_source_url(
        "https://h.example/a/b"
    )


def test_distinct_documents_stay_distinct() -> None:
    assert document_identity("https://h.example/r1.pdf") != document_identity(
        "https://h.example/r2.pdf"
    )
    assert document_identity("https://a.example/r.pdf") != document_identity(
        "https://b.example/r.pdf"
    )


def test_case_sensitive_path_is_preserved() -> None:
    """Only the identity KEY lower-cases; the canonical URL must not."""
    assert canonicalize_source_url(
        "https://h.example/Reports/AnnualReport.PDF"
    ).endswith("/Reports/AnnualReport.PDF")


def test_query_semantics_are_not_rewritten() -> None:
    """Encoding normalisation touches the PATH only.

    Credential-bearing params (``sig``, ``token``…) are still stripped by the
    pre-existing secret redaction — that behaviour is unchanged here.
    """
    out = canonicalize_source_url("https://h.example/a?year=2025&lang=EN")
    assert "year=2025" in out and "lang=EN" in out
    # And the existing secret-stripping is untouched.
    assert "sig=" not in canonicalize_source_url("https://h.example/a?sig=AbC&x=1")


# ===========================================================================
# Dedup happens BEFORE any probe / attempt budget is spent
# ===========================================================================
def test_both_url_forms_on_one_page_yield_ONE_candidate() -> None:
    html = (
        f'<a href="{RAW}">Annual Report 2025</a>'
        f'<a href="{ENC}">Annual Report 2025</a>'
    )
    docs = discover_documents(
        html,
        base_url="https://issuer.example/reports",
        allowed_domains=("issuer.example", "docs.issuer-cdn.example"),
        document_domains=("docs.issuer-cdn.example",),
    )
    assert len(docs) == 1, "the same document must not consume two fetch attempts"
    assert docs[0].is_document is True


def test_relative_and_absolute_forms_collapse() -> None:
    html = (
        '<a href="/reports/Annual Report 2025.pdf">Annual Report</a>'
        '<a href="https://issuer.example/reports/Annual%20Report%202025.pdf">Annual Report</a>'
    )
    docs = discover_documents(
        html,
        base_url="https://issuer.example/reports",
        allowed_domains=("issuer.example",),
    )
    assert len(docs) == 1


# ===========================================================================
# The primitive
# ===========================================================================
def test_normalize_url_path_encoding_is_encode_only() -> None:
    assert normalize_url_path_encoding("/a b") == "/a%20b"
    assert normalize_url_path_encoding("/a%2fb") == "/a%2Fb"
    # Already-encoded input is unchanged (no double-encoding).
    assert normalize_url_path_encoding("/a%20b") == "/a%20b"
    assert normalize_url_path_encoding("") == ""


def test_normalization_is_idempotent() -> None:
    once = canonicalize_source_url(RAW)
    assert canonicalize_source_url(once) == once
    assert document_identity(document_identity(RAW)) == document_identity(RAW)
