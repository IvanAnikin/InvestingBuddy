"""
Phase D1a — issuer-scoped document hosts + extension-less document discovery.

SECURITY IS THE ACCEPTANCE CRITERION HERE. This slice widens a trust boundary:
an issuer may now delegate document hosting to a curated content host. Every
test below exists to prove that the widening is narrow, issuer-scoped, and
cannot be talked into a new host by anything discovered at runtime.

The motivating case is generic, not Pandora-specific: an issuer publishes its
annual report to a content CDN at an extension-less path containing spaces, so
suffix-based discovery finds nothing and the domain allowlist (correctly)
refuses the host.
"""

from __future__ import annotations

import pytest

from app.services.sources.document_discovery import (
    _encode_url_spaces,
    _is_document_url,
    discover_documents,
)
from app.services.sources.document_fetcher import classify_content_type
from app.services.sources.verified_issuer_sources import (
    VerifiedIssuerSource,
    get_verified_issuer_source,
    validate_registry,
)

ISSUER = "issuer.example"
CDN = "docs.issuer-cdn.example"
OTHER_CDN = "cdn-a.example"


def _issuer(**over) -> VerifiedIssuerSource:
    base = dict(
        ticker="TSTC",
        exchange="XX",
        company_name="Testco A/S",
        country="Testland",
        official_website_domain=ISSUER,
        allowed_domains=(ISSUER,),
        document_domains=(CDN,),
        investor_relations_url=f"https://{ISSUER}/investor",
        annual_reports_url=f"https://{ISSUER}/investor/reports",
    )
    base.update(over)
    return VerifiedIssuerSource(**base)


def _page(*hrefs: str) -> str:
    return "".join(f'<a href="{h}">Annual Report 2025</a>' for h in hrefs)


def _discover(html: str, issuer: VerifiedIssuerSource):
    return discover_documents(
        html,
        base_url=f"https://{ISSUER}/investor/reports",
        allowed_domains=issuer.fetch_allowed_domains(),
        document_domains=issuer.document_domains,
    )


# ===========================================================================
# A. Approved CDN — the case this slice exists for
# ===========================================================================
def test_extensionless_document_on_approved_cdn_is_a_document() -> None:
    docs = _discover(_page(f"https://{CDN}/v1/static/Annual Report 2025"), _issuer())
    assert len(docs) == 1
    doc = docs[0]
    assert doc.is_document is True
    assert doc.url == f"https://{CDN}/v1/static/Annual%20Report%202025"
    assert doc.doc_kind == "annual_report"


# ===========================================================================
# B / C / D / E. Trust boundary
# ===========================================================================
def test_unapproved_domain_is_never_a_document_candidate() -> None:
    docs = _discover(_page("https://evil-files.example/report"), _issuer())
    # Rejected outright: not in the issuer's fetch set at all.
    assert [d for d in docs if "evil-files" in d.url] == []


def test_document_trust_is_issuer_scoped() -> None:
    """Issuer A's curated CDN must not be usable by issuer B."""
    issuer_b = _issuer(ticker="OTHR", document_domains=())
    docs = _discover(_page(f"https://{OTHER_CDN}/report"), issuer_b)
    assert [d for d in docs if OTHER_CDN in d.url] == []

    # And a host curated for A is not a document host for B even if B may reach
    # it as an ordinary allowed domain.
    b_with_plain_allow = _issuer(
        ticker="OTHR", allowed_domains=(ISSUER, OTHER_CDN), document_domains=()
    )
    assert (
        _is_document_url(f"https://{OTHER_CDN}/report", b_with_plain_allow.document_domains)
        is False
    )


def test_curated_host_cannot_be_widened_by_page_content() -> None:
    """Trust comes from the registry, never from a link found on a page."""
    issuer = _issuer()
    html = _page(
        f"https://{CDN}/v1/static/Annual Report 2025",
        "https://attacker.example/v1/static/Annual Report 2025",
    )
    urls = [d.url for d in _discover(html, issuer)]
    assert any(CDN in u for u in urls)
    assert not any("attacker.example" in u for u in urls)


def test_lookalike_hosts_are_not_matched() -> None:
    for host in (f"{CDN}.attacker.example", f"evil-{CDN}", f"x{CDN}"):
        assert _is_document_url(f"https://{host}/report", (CDN,)) is False
    # A genuine sub-domain of the curated host IS allowed (registrable match).
    assert _is_document_url(f"https://sub.{CDN}/report", (CDN,)) is True


# ===========================================================================
# F / G / H. Type determination stays with the RESPONSE
# ===========================================================================
def test_extensionless_url_is_only_a_candidate_not_a_declared_pdf() -> None:
    """The curated host marks a candidate; the response decides the type."""
    # An HTML response on the curated host is HTML, not PDF.
    assert classify_content_type("text/html", f"https://{CDN}/v1/static/Annual Report") == "html"
    assert classify_content_type("application/pdf", f"https://{CDN}/v1/static/x") == "pdf"


def test_misleading_pdf_extension_served_as_html_is_html() -> None:
    assert classify_content_type("text/html; charset=utf-8", "https://x.example/a.pdf") == "html"


def test_octet_stream_with_pdf_extension_stays_pdf() -> None:
    """Pinned existing behaviour: extension rescues only PDFs."""
    assert classify_content_type("application/octet-stream", "https://x.example/a.pdf") == "pdf"
    # …and does NOT invent a type for an extension-less octet-stream.
    assert classify_content_type("application/octet-stream", f"https://{CDN}/v1/x") is None


# ===========================================================================
# I. URL canonicalization
# ===========================================================================
def test_spaces_are_encoded_only_for_url_shaped_values() -> None:
    assert _encode_url_spaces("https://h.example/a b") == "https://h.example/a%20b"
    assert _encode_url_spaces("/reports/Annual Report") == "/reports/Annual%20Report"
    # Free prose is left alone (and still rejected by the shape test).
    assert _encode_url_spaces("Annual Report 2025") == "Annual Report 2025"
    # Query semantics are not rewritten.
    assert _encode_url_spaces("https://h.example/a b?q=x y") == "https://h.example/a%20b?q=x y"


def test_encoded_url_is_a_stable_cache_identity() -> None:
    a = _discover(_page(f"https://{CDN}/v1/static/Annual Report 2025"), _issuer())[0]
    b = _discover(_page(f"https://{CDN}/v1/static/Annual%20Report%202025"), _issuer())[0]
    assert a.url == b.url
    assert a.identity == b.identity


# ===========================================================================
# J. Bounds — a CDN's asset soup must not become document candidates
# ===========================================================================
def test_cdn_assets_are_not_treated_as_documents() -> None:
    issuer = _issuer()
    for asset in ("app.js", "site.css", "logo.png", "data.json", "f.woff2"):
        assert _is_document_url(f"https://{CDN}/{asset}", issuer.document_domains) is False


def test_discovery_stays_bounded_on_a_link_heavy_page() -> None:
    html = "".join(
        f'<a href="https://{CDN}/v1/static/Report {i}">Annual Report {i}</a>'
        for i in range(300)
    )
    docs = _discover(html, _issuer())
    assert len(docs) <= 25, "discovery must stay bounded"


# ===========================================================================
# K / L. No regression for issuers WITHOUT curated document hosts
# ===========================================================================
def test_same_domain_pdf_behaviour_is_unchanged() -> None:
    plain = _issuer(document_domains=())
    docs = _discover(_page(f"https://{ISSUER}/reports/annual-report-2025.pdf"), plain)
    assert docs and docs[0].is_document is True


def test_extensionless_url_is_not_a_document_without_curated_hosts() -> None:
    plain = _issuer(document_domains=())
    docs = _discover(_page(f"https://{ISSUER}/reports/Annual Report 2025"), plain)
    # It may still be a keyword-matched LINK, but never a downloadable document.
    assert all(d.is_document is False for d in docs)


def test_context_var_does_not_leak_between_runs() -> None:
    """A curated run must not make a later plain run permissive."""
    _discover(_page(f"https://{CDN}/v1/static/Annual Report 2025"), _issuer())
    assert _is_document_url(f"https://{CDN}/v1/static/Another Report") is False


# ===========================================================================
# Registry invariants
# ===========================================================================
def test_registry_rejects_unsafe_document_domains() -> None:
    for bad in (("*.evil.example",), ("UPPER.example",), ("nodot",), ("h.example/path",)):
        with pytest.raises(AssertionError):
            validate_registry((_issuer(document_domains=bad),))


def test_registry_rejects_document_domain_duplicating_allowed_domain() -> None:
    with pytest.raises(AssertionError):
        validate_registry((_issuer(document_domains=(ISSUER,)),))


def test_shipped_registry_is_valid_and_scoped() -> None:
    validate_registry()
    pandora = get_verified_issuer_source("PNDORA", "CO")
    assert pandora is not None
    assert pandora.document_domains == ("pandora.a.bigcontent.io",)
    assert pandora.fetch_allowed_domains() == (
        "pandoragroup.com",
        "pandora.a.bigcontent.io",
    )
    # An issuer without curated hosts is completely unaffected.
    richemont = get_verified_issuer_source("CFR", "SW")
    assert richemont is not None
    assert richemont.document_domains == ()
    assert richemont.fetch_allowed_domains() == richemont.allowed_domains
