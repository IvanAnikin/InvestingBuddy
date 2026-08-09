"""
Phase 29B.2 — Annual-report document extraction + primary-fact parsing.

Covers the whole new evidence path, all offline:

  * config defaults (extraction OFF; evidence-budget knobs present),
  * the SSRF-safe bounded document fetcher (guards, content-type, redirects,
    max-bytes, timeout, never-raises) — exercised with a fake httpx client,
  * bounded PDF / HTML / text extraction (hand-built PDF fixtures; scanned/empty
    honest gap; excerpts bounded + id'd; no full-document field),
  * the conservative primary-fact parser (high-confidence only; refuses
    ambiguity; never infers/converts; full provenance),
  * connector + evidence-pack integration (T1 excerpts/facts, no tokenized URLs,
    metadata-only preserved when off, failure never breaks a report),
  * the deterministic evidence budgeter (tier preference, char/item bounds,
    stable E ids, omitted count, gap compression),
  * the evidence-preview API (identity-only, no arbitrary URL, honest gaps),
  * a council run over document evidence (cited, safe, human-review-required),
  * regressions (US SEC path, BA.LSE ≠ Boeing, registry intact, sources health).

No real network call is ever made — the live fetch path is exercised via a fake
httpx client and injected fakes.
"""

from __future__ import annotations

import asyncio
import io
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.core.config import settings as app_settings
from app.main import app
from app.services.llm.council import run_council
from app.services.llm.evidence_budget import apply_evidence_budget
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.fake_client import FakeLLMClient
from app.services.llm.schemas import EvidenceItem as CouncilEvidenceItem
from app.services.llm.schemas import EvidencePack
from app.services.sources.company_evidence import collect_company_source_evidence
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import (
    PrimaryDocumentBundle,
)
from app.services.sources.document_fetcher import (
    classify_content_type,
    safe_fetch_document,
)
from app.services.sources.document_text_extractor import (
    extract_document_text,
)
from app.services.sources.primary_fact_parser import parse_primary_facts
from app.services.sources.safe_web_fetcher import SafeFetchResult, SafeLink
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
    T5_API_AGGREGATOR,
    T6_MODEL_ESTIMATE,
)
from app.services.sources.verified_issuer_sources import get_verified_issuer_source

client = TestClient(app)

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


def _has_forbidden(text: str) -> bool:
    low = (text or "").lower()
    return any(term in low for term in _FORBIDDEN)


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = dict(
        source_connector_enabled=True,
        source_document_extraction_enabled=True,
        source_connector_max_items_per_source=8,
    )
    base.update(over)
    return Settings(**base)


def _q() -> QueryContext:
    return QueryContext(max_items=8)


# --------------------------------------------------------------------------- #
# PDF fixture builders (hand-built bytes — no PDF-writer dependency)
# --------------------------------------------------------------------------- #


def make_pdf(pages_text: list[str]) -> bytes:
    """Build a minimal, text-extractable multi-page PDF (Tj operators)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages_text)))
    objs.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages_text)} >>".encode()
    )
    font_obj_num = 3 + len(pages_text) * 2
    for i, text in enumerate(pages_text):
        content_num = 4 + i * 2
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_num} 0 R /Resources << /Font "
                f"<< /F1 {font_obj_num} 0 R >> >> >>"
            ).encode()
        )
        lines = text.split("\n")
        content = "BT /F1 12 Tf 72 720 Td 14 TL "
        for j, ln in enumerate(lines):
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content += (f"({esc}) Tj " if j == 0 else f"T* ({esc}) Tj ")
        content += "ET"
        cb = content.encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")

    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
        f"{xref_pos}\n%%EOF".encode()
    )
    return out.getvalue()


def make_pdf_no_text() -> bytes:
    """A valid PDF whose only content is a filled rectangle — no extractable text."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << >> >>",
    ]
    content = b"0 0 1 rg 100 100 200 200 re f"
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offs: list[int] = []
    for i, body in enumerate(objs, start=1):
        offs.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xp = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for o in offs:
        out.write(f"{o:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
        f"{xp}\n%%EOF".encode()
    )
    return out.getvalue()


ANNUAL_TEXT = (
    "Compagnie Financiere Richemont SA\n"
    "Annual Report and Accounts for the fiscal year ended 31 March 2024.\n"
    "The Group is a leading luxury goods company operating a portfolio of Maisons. "
    "Richemont employs 35,987 people worldwide.\n"
    "All figures are stated in millions of euros (EUR).\n"
    "Revenue: 20,616 million. Operating profit was 4,794 million.\n"
    "Net profit for the year reached 2,357 million.\n"
    "Cash and cash equivalents totalled 7,151 million.\n"
    "Principal risks: the Group is exposed to foreign currency risk and "
    "regulatory uncertainty."
)


# --------------------------------------------------------------------------- #
# Fake httpx client for the document fetcher (no real network)
# --------------------------------------------------------------------------- #


class _FakeStream:
    def __init__(self, *, status_code=200, headers=None, body=b"", is_redirect=False, raise_exc=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self._body = body
        self._raise = raise_exc

    async def __aenter__(self):
        if self._raise is not None:
            raise self._raise
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for i in range(0, max(1, len(self._body)), 1024):
            yield self._body[i : i + 1024]


class _FakeClient:
    def __init__(self, script, **kw):
        self._script = list(script)
        self._i = 0
        self.kw = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        item = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return item


def _patch_httpx(monkeypatch, script):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(script, **kw))


# =========================================================================== #
# 1–2  Config
# =========================================================================== #


def test_1_extraction_defaults_off():
    s = Settings()
    assert s.source_document_extraction_enabled is False
    assert s.source_connector_enabled is False


def test_2_config_knobs_loaded():
    s = Settings()
    # Phase 32A Slice 5B.2 hotfix: raised from 5_000_000 — the old cap
    # silently truncated real large annual-report PDFs mid-download,
    # corrupting their trailer/xref table and misclassifying the result as
    # "scanned, no text" rather than "download was cut off" (staging
    # validation finding). 35 MB comfortably covers real annual-report sizes
    # while staying explicitly bounded.
    assert s.source_document_extraction_max_bytes == 35_000_000
    assert s.source_document_extraction_timeout_seconds == 15
    assert s.source_document_extraction_max_pages == 20
    assert s.source_document_extraction_max_excerpts == 8
    assert s.source_document_extraction_max_chars_per_excerpt == 1200
    assert "application/pdf" in s.source_document_extraction_allowed_content_types
    assert s.llm_council_evidence_max_items == 20
    assert s.llm_council_evidence_max_chars == 24000
    assert s.llm_council_evidence_max_chars_per_item == 1200


# =========================================================================== #
# 3–9  Safe document fetcher
# =========================================================================== #


def test_3_rejects_http():
    r = asyncio.run(
        safe_fetch_document("http://www.richemont.com/x.pdf", allowed_domains=("richemont.com",))
    )
    assert r.blocked and not r.ok
    assert r.source_gaps  # honest gap, no raise


def test_4_rejects_localhost_private_internal_ip():
    for url in (
        "https://localhost/x.pdf",
        "https://127.0.0.1/x.pdf",
        "https://10.0.0.5/x.pdf",
        "https://169.254.169.254/x.pdf",
        "https://metadata.google.internal/x.pdf",
    ):
        r = asyncio.run(safe_fetch_document(url, allowed_domains=("richemont.com",)))
        assert r.blocked, url


def test_5_rejects_off_allowlist_redirect(monkeypatch):
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=302, headers={"location": "https://evil.com/x.pdf"}, is_redirect=True)],
    )
    r = asyncio.run(
        safe_fetch_document(
            "https://www.richemont.com/reports/ar.pdf", allowed_domains=("richemont.com",)
        )
    )
    assert r.blocked and "redirect" in (r.error or "")
    assert any("off the verified issuer domain" in g.message for g in r.source_gaps)


def test_6_enforces_max_bytes(monkeypatch):
    body = b"%PDF-1.4 " + b"x" * 5000
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=200, headers={"content-type": "application/pdf"}, body=body)],
    )
    cfg = _cfg(source_document_extraction_max_bytes=200)
    r = asyncio.run(
        safe_fetch_document(
            "https://www.richemont.com/reports/ar.pdf",
            allowed_domains=("richemont.com",),
            cfg=cfg,
        )
    )
    assert r.truncated is True
    assert r.content is not None and len(r.content) <= 200


def test_6b_real_size_large_annual_report_not_truncated_at_default_cap(monkeypatch):
    # Phase 32A Slice 5B.2 hotfix regression: a genuine large annual-report
    # PDF (here ~8 MB — bigger than the OLD 5 MB default, comfortably under
    # the NEW 35 MB default) must download completely, not get silently
    # truncated mid-stream (which corrupts the trailer/xref table at the end
    # of the file and misclassifies the document as "scanned, no text layer"
    # rather than "download was cut off" — the exact staging failure this
    # fixes).
    body = b"%PDF-1.4 " + b"x" * 8_000_000
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=200, headers={"content-type": "application/pdf"}, body=body)],
    )
    r = asyncio.run(
        safe_fetch_document(
            "https://www.richemont.com/reports/ar.pdf",
            allowed_domains=("richemont.com",),
            cfg=_cfg(),  # default max_bytes (35_000_000)
        )
    )
    assert r.truncated is False
    assert r.content is not None and len(r.content) == len(body)


def test_7_enforces_timeout_never_raises(monkeypatch):
    import httpx

    _patch_httpx(
        monkeypatch,
        [_FakeStream(raise_exc=httpx.TimeoutException("timed out"))],
    )
    r = asyncio.run(
        safe_fetch_document(
            "https://www.richemont.com/reports/ar.pdf", allowed_domains=("richemont.com",)
        )
    )
    assert not r.ok and r.error and "fetch failed" in r.error
    assert r.source_gaps


def test_8_rejects_unsupported_content_type(monkeypatch):
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=200, headers={"content-type": "image/png"}, body=b"\x89PNG")],
    )
    r = asyncio.run(
        safe_fetch_document(
            "https://www.richemont.com/reports/img", allowed_domains=("richemont.com",)
        )
    )
    assert r.blocked and "unsupported content-type" in (r.error or "")


def test_9_classify_content_type_and_no_secret_url(monkeypatch):
    assert classify_content_type("application/pdf") == "pdf"
    assert classify_content_type("text/html; charset=utf-8") == "html"
    assert classify_content_type("text/plain") == "text"
    assert classify_content_type("image/png") is None
    # A .pdf served as octet-stream is still a pdf (by extension).
    assert classify_content_type("application/octet-stream", "https://x.com/a.pdf") == "pdf"
    # A tokenized requested URL is stripped on the result.
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=200, headers={"content-type": "application/pdf"}, body=make_pdf(["Hi"]))],
    )
    r = asyncio.run(
        safe_fetch_document(
            "https://www.richemont.com/reports/ar.pdf?api_token=SECRET",
            allowed_domains=("richemont.com",),
        )
    )
    assert "api_token" not in (r.requested_url or "")
    assert "api_token" not in (r.final_url or "")


# =========================================================================== #
# 10–14  Text extraction
# =========================================================================== #


def test_10_extracts_bounded_text_from_pdf():
    ext = extract_document_text(
        make_pdf([ANNUAL_TEXT, "Segment: Jewellery Maisons."]),
        document_type="pdf",
        source_url="https://www.richemont.com/reports/ar2024.pdf",
    )
    assert ext.excerpts
    assert ext.page_count_if_known == 2
    assert ext.inferred_year == 2024
    assert not ext.source_gaps


def test_11_scanned_pdf_returns_honest_gap():
    ext = extract_document_text(make_pdf_no_text(), document_type="pdf", source_url="https://x/a.pdf")
    assert ext.excerpts == []
    assert any("scanned" in g.lower() for g in ext.source_gaps)


def test_12_extracts_bounded_text_from_html():
    html = (
        b"<html><head><title>Annual Report 2024</title></head><body>"
        b"<script>var x=1;</script>"
        b"<h1>Overview</h1><p>Revenue was 20,616 million euros in 2024.</p>"
        b"<p>The Group operates luxury Maisons across the world.</p></body></html>"
    )
    ext = extract_document_text(html, document_type="html", source_url="https://x/ar")
    assert ext.excerpts
    assert ext.title == "Annual Report 2024"
    # Script content is stripped.
    assert all("var x" not in e.text for e in ext.excerpts)


def test_13_excerpts_bounded_and_have_ids():
    long_para = "Revenue grew. " * 400  # ~5600 chars
    ext = extract_document_text(
        make_pdf([long_para]),
        document_type="pdf",
        source_url="https://x/a.pdf",
        cfg=_cfg(source_document_extraction_max_chars_per_excerpt=300, source_document_extraction_max_excerpts=3),
    )
    assert ext.excerpts
    assert len(ext.excerpts) <= 3
    for e in ext.excerpts:
        assert e.excerpt_id.startswith("X")
        assert e.char_count <= 300
        assert len(e.text) <= 300


def test_14_no_full_document_field_stored():
    ext = extract_document_text(make_pdf([ANNUAL_TEXT]), document_type="pdf", source_url="https://x/a.pdf")
    from app.services.sources.document_text_extractor import DocumentTextExtraction

    fields = set(DocumentTextExtraction.model_fields.keys())
    # No field carries the whole document text.
    assert "full_text" not in fields and "raw_text" not in fields and "content" not in fields
    joined = " ".join(e.text for e in ext.excerpts)
    assert len(joined) <= 8 * 1200 + 100  # bounded by excerpt count * per-excerpt cap


# =========================================================================== #
# 15–20  Primary fact parser
# =========================================================================== #


def test_15_parses_high_confidence_facts():
    ext = extract_document_text(make_pdf([ANNUAL_TEXT]), document_type="pdf", source_url="https://x/a.pdf")
    facts = {f.field: f for f in parse_primary_facts(ext)}
    assert facts["reporting_currency"].value == "EUR"
    assert facts["fiscal_year"].value == "2024"
    assert facts["revenue"].numeric_value == 20616.0
    assert facts["revenue"].currency == "EUR" and facts["revenue"].scale == "million"


def test_16_refuses_ambiguous_values():
    text = "Revenue: 20,616 million in 2024 and Revenue: 18,300 million in 2023. All figures in millions of euros."
    ext = extract_document_text(make_pdf([text]), document_type="pdf", source_url="https://x/a.pdf")
    fields = {f.field for f in parse_primary_facts(ext)}
    assert "revenue" not in fields  # two different magnitudes → refused


def test_17_does_not_infer_missing_facts():
    text = "The Group operates a portfolio of luxury Maisons across many regions."
    ext = extract_document_text(make_pdf([text]), document_type="pdf", source_url="https://x/a.pdf")
    facts = parse_primary_facts(ext)
    # No revenue/currency/year invented from prose with no numbers.
    assert all(f.field not in ("revenue", "net_income", "operating_profit") for f in facts)


def test_18_every_fact_has_provenance():
    ext = extract_document_text(make_pdf([ANNUAL_TEXT]), document_type="pdf", source_url="https://x/a.pdf")
    for f in parse_primary_facts(ext):
        assert f.source_url == "https://x/a.pdf"
        assert f.excerpt_id and f.excerpt_id.startswith("X")
        assert f.needs_human_review is True


def test_19_no_currency_conversion():
    ext = extract_document_text(make_pdf([ANNUAL_TEXT]), document_type="pdf", source_url="https://x/a.pdf")
    rev = next(f for f in parse_primary_facts(ext) if f.field == "revenue")
    # Value preserved as-found in EUR; no USD/other conversion produced.
    assert rev.currency == "EUR"
    assert rev.numeric_value == 20616.0


def test_20_no_valuation_metrics_or_forbidden_vocab():
    ext = extract_document_text(make_pdf([ANNUAL_TEXT]), document_type="pdf", source_url="https://x/a.pdf")
    facts = parse_primary_facts(ext)
    valuation = {"pe_ratio", "fair_value", "price_target", "intrinsic_value", "upside", "downside"}
    for f in facts:
        assert f.field not in valuation
        assert not _has_forbidden(f"{f.field} {f.value} {f.parser_warning or ''}")


# =========================================================================== #
# 21–26  Connector + evidence integration
# =========================================================================== #


async def _fake_page(url, *, allowed_domains, keywords, fallback_keywords=()):
    return SafeFetchResult(
        requested_url=url,
        status_code=200,
        links=[
            SafeLink(
                url="https://www.richemont.com/reports/ar2024.pdf",
                text="Annual Report 2024",
                is_document=True,
            )
        ],
    )


def _real_bundle_extractor():
    async def _extract(url, *, allowed_domains, title_hint=None, original_language=None):
        ext = extract_document_text(
            make_pdf([ANNUAL_TEXT]),
            document_type="pdf",
            source_url=url,
            title_hint=title_hint,
            original_language=original_language,
        )
        facts = parse_primary_facts(ext)
        return PrimaryDocumentBundle(source_url=url, document_type="pdf", extraction=ext, facts=facts)

    return _extract


def test_21_cfr_returns_annual_report_text_evidence():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=_real_bundle_extractor(),
            cfg=_cfg(),
        )
    )
    types = {i.source_type for i in collected.evidence_items}
    assert "company_ir_annual_report_excerpt" in types
    assert "company_ir_financial_fact" in types


def test_22_extraction_disabled_keeps_metadata_only():
    # No document_extractor injected → Phase 29B.1 behaviour (metadata + gaps).
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            cfg=_cfg(),
        )
    )
    types = {i.source_type for i in collected.evidence_items}
    assert "company_ir_annual_report_excerpt" not in types
    assert "company_ir_financial_fact" not in types
    assert any(i.data_quality == "metadata_only" for i in collected.evidence_items)


def test_23_blocked_document_returns_honest_gap():
    async def blocked_extractor(url, *, allowed_domains, title_hint=None, original_language=None):
        from app.services.sources.gaps import GapSeverity, GapType, SourceGap

        return PrimaryDocumentBundle(
            source_url=url,
            document_type=None,
            extraction=None,
            source_gaps=[
                SourceGap(
                    connector_key="company_ir",
                    source_id="company_ir",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message="Annual-report document could not be safely fetched (blocked).",
                )
            ],
        )

    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="KER", exchange="PA", country="France"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=blocked_extractor,
            cfg=_cfg(),
        )
    )
    # No fabricated excerpt; index metadata survives; honest gap present.
    assert all(i.source_type != "company_ir_annual_report_excerpt" for i in collected.evidence_items)
    assert any("could not be safely fetched" in g.message for g in collected.source_gaps)


def test_24_excerpt_items_are_t1_primary_filing():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=_real_bundle_extractor(),
            cfg=_cfg(),
        )
    )
    doc_items = [
        i
        for i in collected.evidence_items
        if i.source_type in ("company_ir_annual_report_excerpt", "company_ir_financial_fact")
    ]
    assert doc_items
    for it in doc_items:
        assert it.content_source_tier == T1_PRIMARY_FILING
        assert it.provider_transport_tier == T1_PRIMARY_COMPANY_SOURCE


def test_25_evidence_items_have_no_tokenized_urls():
    async def tokenized_page(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(
            requested_url=url,
            status_code=200,
            links=[
                SafeLink(
                    url="https://www.richemont.com/reports/ar2024.pdf",
                    text="Annual Report 2024",
                    is_document=True,
                )
            ],
        )

    async def tokenized_extractor(url, *, allowed_domains, title_hint=None, original_language=None):
        ext = extract_document_text(
            make_pdf([ANNUAL_TEXT]),
            document_type="pdf",
            source_url="https://www.richemont.com/reports/ar2024.pdf?api_token=SECRET",
        )
        return PrimaryDocumentBundle(
            source_url="https://www.richemont.com/reports/ar2024.pdf?api_token=SECRET",
            document_type="pdf",
            extraction=ext,
            facts=parse_primary_facts(ext),
        )

    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=tokenized_page,
            document_extractor=tokenized_extractor,
            cfg=_cfg(),
        )
    )
    for it in collected.evidence_items:
        assert "api_token" not in (it.url or "")
        for w in it.warnings:
            assert "api_token" not in w


def test_26_connector_failure_does_not_break_collection():
    async def boom(url, *, allowed_domains, title_hint=None, original_language=None):
        raise RuntimeError("extractor exploded")

    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=boom,
            cfg=_cfg(),
        )
    )
    # call_safe swallows the exception → metadata items still present, no raise.
    assert any(i.source_id == "company_ir" for i in collected.evidence_items)


# =========================================================================== #
# 27–32  Evidence budget
# =========================================================================== #


def _item(id, tier, *, excerpt="x", url=None, title="t", data_quality=None):
    return CouncilEvidenceItem(
        id=id,
        source_tier=tier,
        source_type="x",
        content_tier=tier,
        transport_tier=tier,
        title=title,
        url=url,
        excerpt=excerpt,
        data_quality=data_quality,
    )


def _pack(items, gaps=None):
    return EvidencePack(evidence_items=items, known_gaps=gaps or [])


def test_27_keeps_t1_t2_before_t5_t6():
    items = [
        _item("E1", T6_MODEL_ESTIMATE, title="model"),
        _item("E2", T5_API_AGGREGATOR, title="agg"),
        _item("E3", T1_PRIMARY_FILING, title="filing"),
        _item("E4", "T2_regulator_or_gov", title="reg"),
    ]
    out = apply_evidence_budget(_pack(items), max_items=2)
    kept_titles = {i.title for i in out.evidence_items}
    assert kept_titles == {"filing", "reg"}


def test_28_bounds_items_and_chars():
    items = [_item(f"E{i}", T1_PRIMARY_FILING, excerpt="z" * 500, title=f"t{i}") for i in range(30)]
    out = apply_evidence_budget(_pack(items), max_items=5, max_chars=1000, max_chars_per_item=100)
    assert len(out.evidence_items) <= 5
    for it in out.evidence_items:
        assert len(it.excerpt or "") <= 100


def test_29_preserves_stable_e_ids():
    items = [_item(f"X{i}", T1_PRIMARY_FILING, title=f"t{i}") for i in range(5)]
    out = apply_evidence_budget(_pack(items), max_items=5)
    assert [i.id for i in out.evidence_items] == ["E1", "E2", "E3", "E4", "E5"]


def test_30_reports_omitted_count():
    items = [_item(f"E{i}", T1_PRIMARY_FILING, title=f"t{i}") for i in range(10)]
    out = apply_evidence_budget(_pack(items), max_items=3)
    assert out.omitted_evidence_count == 7
    assert out.omitted_reason and "compressed out" in out.omitted_reason


def test_31_compresses_duplicate_gaps_but_never_drops_all():
    gaps = ["Gap A", "gap a", "GAP A", "Gap B"]
    out = apply_evidence_budget(_pack([_item("E1", T1_PRIMARY_FILING)], gaps), max_items=5)
    assert out.known_gaps  # never emptied
    assert len(out.known_gaps) == 2  # A + B, case-insensitive de-dup


def test_32_large_aapl_like_pack_stays_within_budget():
    items = [_item(f"E{i}", T5_API_AGGREGATOR, excerpt="q" * 2000, title=f"t{i}") for i in range(60)]
    out = apply_evidence_budget(_pack(items), max_items=20, max_chars=24000, max_chars_per_item=1200)
    assert len(out.evidence_items) <= 20
    total = sum(len(i.excerpt or "") + len(i.title or "") for i in out.evidence_items)
    assert total <= 24000 + 1300  # within budget (+ one-item slack)


def test_32b_dedup_by_url_title_excerpt():
    items = [
        _item("E1", T1_PRIMARY_FILING, url="https://x/a", title="A", excerpt="same"),
        _item("E2", T1_PRIMARY_FILING, url="https://x/a", title="A", excerpt="same"),
        _item("E3", T1_PRIMARY_FILING, url="https://x/a", title="A", excerpt="different"),
    ]
    out = apply_evidence_budget(_pack(items), max_items=10)
    assert len(out.evidence_items) == 2  # the exact duplicate is dropped


# =========================================================================== #
# 33–37  Evidence preview API
# =========================================================================== #


def test_33_cfr_preview_document_text_offline_safe(monkeypatch):
    # Connector layer + extraction on; live fetchers replaced with offline fakes.
    monkeypatch.setattr(app_settings, "source_connector_enabled", True, raising=False)
    import app.api.v1.sources as sources_api

    monkeypatch.setattr(sources_api, "live_ir_page_fetcher", _fake_page)
    monkeypatch.setattr(sources_api, "live_document_extractor", _real_bundle_extractor())

    async def no_sec(company, query):
        return []

    async def no_press(company, query):
        return []

    monkeypatch.setattr(sources_api, "live_sec_filings_fetcher", no_sec)
    monkeypatch.setattr(sources_api, "live_ir_press_fetcher", no_press)

    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={
            "ticker": "CFR",
            "exchange": "SW",
            "source_ids": ["company_ir"],
            "include_document_text": True,
        },
    )
    assert r.status_code == 200
    body = r.json()
    assert body["document_extraction_performed"] is True
    types = {i["source_type"] for i in body["evidence_items"]}
    assert "company_ir_annual_report_excerpt" in types


def test_34_uhr_preview_offline_returns_gaps_when_layer_off():
    # Default settings: connector layer off → no crash, honest offline result.
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "UHR", "exchange": "SW", "source_ids": ["company_ir"], "include_document_text": True},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["document_extraction_performed"] is False


def test_35_ker_blocked_document_returns_honest_gap(monkeypatch):
    monkeypatch.setattr(app_settings, "source_connector_enabled", True, raising=False)
    import app.api.v1.sources as sources_api

    async def blocked_extractor(url, *, allowed_domains, title_hint=None, original_language=None):
        from app.services.sources.gaps import GapSeverity, GapType, SourceGap

        return PrimaryDocumentBundle(
            source_url=url,
            document_type=None,
            extraction=None,
            source_gaps=[
                SourceGap(
                    connector_key="company_ir",
                    source_id="company_ir",
                    gap_type=GapType.primary_filing_unavailable,
                    severity=GapSeverity.info,
                    message="Annual-report document could not be safely fetched (blocked).",
                )
            ],
        )

    monkeypatch.setattr(sources_api, "live_ir_page_fetcher", _fake_page)
    monkeypatch.setattr(sources_api, "live_document_extractor", blocked_extractor)

    async def empty(company, query):
        return []

    monkeypatch.setattr(sources_api, "live_sec_filings_fetcher", empty)
    monkeypatch.setattr(sources_api, "live_ir_press_fetcher", empty)

    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "KER", "exchange": "PA", "source_ids": ["company_ir"], "include_document_text": True},
    )
    assert r.status_code == 200
    body = r.json()
    joined = " ".join(g["message"] for g in body["source_gaps"])
    assert "could not be safely fetched" in joined


def test_36_unknown_source_id_returns_400():
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "CFR", "exchange": "SW", "source_ids": ["made_up_source"], "include_document_text": True},
    )
    assert r.status_code == 400


def test_37_no_arbitrary_url_fetch_possible():
    from app.schemas.source_evidence_preview import EvidencePreviewRequest

    assert "url" not in EvidencePreviewRequest.model_fields
    req = EvidencePreviewRequest.model_validate(
        {"ticker": "CFR", "exchange": "SW", "url": "https://evil.com/x.pdf", "include_document_text": True}
    )
    assert not hasattr(req, "url")


# =========================================================================== #
# 38–41  Full analysis / council over document evidence
# =========================================================================== #


def _document_pack():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=_real_bundle_extractor(),
            cfg=_cfg(),
        )
    )
    return build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "CFR"}}},
        connector_evidence=collected.evidence_items,
        connector_gap_messages=collected.gap_messages(),
        apply_budget=True,
        budget_cfg=_cfg(),
    )


def test_38_council_over_document_evidence_is_safe():
    pack = _document_pack()
    result = asyncio.run(run_council(pack, FakeLLMClient(), cfg=_cfg()))
    assert result.llm_used is True
    report = result.to_report_dict()
    assert report["human_review_required"] is True
    # No forbidden recommendation/valuation vocabulary anywhere in the output.
    import json

    assert not _has_forbidden(json.dumps(report))


def test_39_council_receives_cited_evidence_ids():
    pack = _document_pack()
    ids = pack.evidence_ids()
    assert ids
    assert all(i.startswith("E") for i in ids)  # stable, contiguous after budget


def test_40_report_stays_human_review_required():
    pack = _document_pack()
    result = asyncio.run(run_council(pack, FakeLLMClient(), cfg=_cfg()))
    meta = result.to_metadata_dict()
    assert result.to_report_dict()["human_review_required"] is True
    # primary_documents summary is compact + secret-free (counts only).
    for doc in meta.get("primary_documents", []):
        assert "excerpt_count" in doc and "fact_count" in doc


def test_41_no_fake_filings_when_document_missing():
    # Extractor returns nothing usable → no excerpt/fact evidence fabricated.
    async def empty_extractor(url, *, allowed_domains, title_hint=None, original_language=None):
        from app.services.sources.document_text_extractor import DocumentTextExtraction

        ext = DocumentTextExtraction(source_url=url, document_type="pdf", source_gaps=["scanned"])
        return PrimaryDocumentBundle(source_url=url, document_type="pdf", extraction=ext, facts=[])

    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_fake_page,
            document_extractor=empty_extractor,
            cfg=_cfg(),
        )
    )
    assert all(
        i.source_type not in ("company_ir_annual_report_excerpt", "company_ir_financial_fact")
        for i in collected.evidence_items
    )
    assert any("could not be extracted" in g.message or "scanned" in g.message.lower() for g in collected.source_gaps)


# =========================================================================== #
# 42–47  Regressions
# =========================================================================== #


def test_42_aapl_us_sec_path_still_works():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="AAPL", exchange="US"),
            source_ids=["sec_edgar", "company_ir"],
            cfg=_cfg(),
        )
    )
    # No crash; SEC not fabricated offline; US issuer is not flagged non-eligible.
    joined = " ".join(g.message for g in collected.source_gaps).lower()
    assert "not eligible" not in joined or "sec" in joined  # honest, not fabricated


def test_43_ba_lse_not_boeing():
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "BA", "exchange": "LSE", "source_ids": ["sec_edgar", "company_ir"]},
    )
    assert r.status_code == 200
    for it in r.json()["evidence_items"]:
        presented = f"{it.get('source_name')} {it.get('title')} {it.get('url')}".lower()
        assert "boeing" not in presented
        assert "sec.gov" not in presented


def test_44_verified_registry_intact_for_targets():
    # My changes must not have broken the verified-issuer registry.
    for t, x in [("CFR", "SW"), ("UHR", "SW"), ("MC", "PA"), ("RMS", "PA"),
                 ("KER", "PA"), ("BRBY", "LSE"), ("PNDORA", "CO"), ("MONC", "MI")]:
        assert get_verified_issuer_source(t, x) is not None


def test_45_sources_registry_and_health_ok():
    assert client.get("/api/v1/sources/registry").status_code == 200
    assert client.get("/api/v1/sources/health").status_code == 200


def test_46_metadata_only_pack_not_budgeted_when_connector_off():
    # Budget only applies on the connector-enabled path; default path unchanged.
    pack = build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "CFR"}}},
        apply_budget=False,
    )
    assert pack.omitted_evidence_count == 0


def test_47_council_disabled_when_flag_off():
    from app.services.llm.council import maybe_run_council

    result = asyncio.run(
        maybe_run_council(
            report_content={"company_identity": {}},
            cfg=Settings(llm_council_enabled=False),
        )
    )
    assert result.llm_used is False
