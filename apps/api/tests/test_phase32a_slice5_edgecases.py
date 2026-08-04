"""
Phase 32A Slice 5 — audit-driven edge cases (MISSING-COVERAGE tests only).

Every scenario here has a real code path but had no test. All tests are fully
OFFLINE and deterministic: PDFs are built in-code (``tests/helpers/pdf_fixtures``),
the network is a hand-built fake ``httpx.AsyncClient`` and DNS is an injected fake
``resolver`` — NO real network, NO respx/responses, NO LLM, NO DB. No production
code is modified.

Covers:
  1. Encrypted PDF → honest ``extraction_failed`` (never raises, no fabricated
     fact), pure path AND the deep ``live_primary_document_extractor`` path.
  2. OCR gating at the SEAM (OCR is a NoOp seam with NO call site this slice, so
     end-to-end activation gating cannot be tested — see the module note below):
     the provider stays NoOp even with the flag ON; a fake ``OcrProvider`` double
     honours the page cap + low confidence; and neither the native nor the deep
     extraction flow invokes any OCR provider (even on a scanned PDF).
  3. Transport error mid-download on the deep path → honest gap, no raise.
  4. Redirect at the fetch layer on the deep path: followed within the hop cap
     with per-hop host + resolved-IP re-validation; a redirect to a disallowed
     host or one that resolves to a private IP (rebinding) is blocked, no body
     fetch.
  5. No-document company (IR page yields zero document links) → honest gap, no
     artifacts, no crash.
  6. PDF resource-abuse (very large declared page count) → bounded by the page
     cap, honest truncation, no hang.

OCR-WIRING NOTE: ``get_ocr_provider`` / ``OcrProvider.extract`` / the NoOp
provider have NO call site anywhere in the extraction flow this slice — OCR is a
pure seam. So "native prevents unnecessary OCR" and "disabled ⇒ never call OCR"
are asserted here as they are OBSERVABLE today (the flow never touches OCR at
all); genuine end-to-end OCR-activation gating cannot be exercised until a later
slice wires a call site.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

import httpx

from app.core.config import Settings
from app.services.sources import ocr_provider as ocr_mod
from app.services.sources.company_evidence import collect_company_source_evidence
from app.services.sources.connector_base import CompanyContext
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.live_fetchers import live_primary_document_extractor
from app.services.sources.ocr_provider import (
    OCR_STATUS_EXTRACTED,
    NoOpOcrProvider,
    OcrProvider,
    OcrResult,
    get_ocr_provider,
)
from app.services.sources.primary_document_extractor import (
    METHOD_OCR,
    STATUS_EXTRACTED,
    STATUS_EXTRACTION_FAILED,
    STATUS_METADATA_ONLY,
    PrimaryDocumentExcerpt,
    extract_pdf,
    extract_primary_document,
)
from app.services.sources.safe_web_fetcher import SafeFetchResult, SafeLink
from tests.helpers.pdf_fixtures import (
    make_encrypted_pdf,
    make_pdf,
    make_pdf_no_text,
    make_pdf_with_table,
)

_FORBIDDEN = (
    "buy", "sell", "hold", "watch", "price target", "fair value",
    "intrinsic value", "upside", "downside",
)


def _has_forbidden(text: str) -> bool:
    low = (text or "").lower()
    return any(term in low for term in _FORBIDDEN)


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = dict(
        source_connector_enabled=True,
        primary_document_ingestion_enabled=True,
        source_connector_max_items_per_source=20,
    )
    base.update(over)
    return Settings(**base)


CFR_TABLE_ROWS = [
    ["EUR million", "2024", "2023"],
    ["Revenue", "20,616", "19,182"],
    ["Net income", "2,357", "2,101"],
]


# --------------------------------------------------------------------------- #
# Fake httpx client / DNS resolver (no real network) — Phase 29B.2 style.
# --------------------------------------------------------------------------- #


class _FakeStream:
    def __init__(
        self,
        *,
        status_code: int = 200,
        headers: dict[str, str] | None = None,
        body: bytes = b"",
        is_redirect: bool = False,
        raise_exc: Exception | None = None,
        raise_mid: Exception | None = None,
    ) -> None:
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self._body = body
        self._raise = raise_exc
        self._raise_mid = raise_mid

    async def __aenter__(self):
        if self._raise is not None:
            raise self._raise
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        # Yield a leading chunk, then optionally fail mid-download.
        yield self._body[:8]
        if self._raise_mid is not None:
            raise self._raise_mid
        yield self._body[8:]


class _ExplodingStream:
    """A stream whose body must never be entered — proves 'no body fetch'."""

    def __init__(self, **_: Any) -> None:
        pass

    async def __aenter__(self):
        raise AssertionError("document body must not be fetched after a block")

    async def __aexit__(self, *a):
        return False


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
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(script, **kw))


def _resolver_returning(ip: str):
    def _resolve(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    return _resolve


def _recording_resolver(mapping: dict[str, str], *, default: str = "93.184.216.34"):
    """A resolver that records each host it is asked to resolve (per-hop proof)."""
    seen: list[str] = []

    def _resolve(host, *a, **k):
        seen.append(host)
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (mapping.get(host, default), 443))]

    return _resolve, seen


def _run_deep(script, resolver, monkeypatch, *, url="https://www.richemont.com/reports/ar2024.pdf", cfg=None):
    _patch_httpx(monkeypatch, script)
    return asyncio.run(
        live_primary_document_extractor(
            url,
            allowed_domains=("richemont.com",),
            cfg=cfg or _cfg(),
            resolver=resolver,
        )
    )


# =========================================================================== #
# 1. Encrypted PDF → honest degradation, never a fabricated fact.
# =========================================================================== #


def test_encrypted_pdf_pure_path_extraction_failed_no_fabrication():
    enc = make_encrypted_pdf(["Revenue: 20,616 million euros (EUR) in 2024."])
    assert enc[:5] == b"%PDF-"  # magic stays valid; only the body is encrypted
    r = extract_pdf(enc)
    assert r.status == STATUS_EXTRACTION_FAILED
    assert r.excerpts == [] and r.tables == []  # nothing fabricated
    assert r.error_type is not None  # sanitized to type(exc).__name__ only
    assert r.source_gaps  # honest gap
    # The dispatcher degrades identically and never raises.
    assert extract_primary_document(enc, document_type="pdf").status == STATUS_EXTRACTION_FAILED


def test_encrypted_pdf_deep_path_degrades_honestly(monkeypatch):
    enc = make_encrypted_pdf(["Encrypted filing body."])
    artifact = _run_deep(
        [_FakeStream(status_code=200, headers={"content-type": "application/pdf"}, body=enc)],
        _resolver_returning("93.184.216.34"),
        monkeypatch,
    )
    assert artifact.status != STATUS_EXTRACTED
    assert artifact.validated_facts == []  # never a fabricated fact
    # An extraction object exists but carries no content, or the fetch degraded.
    assert artifact.extraction is None or not artifact.extraction.has_content


# =========================================================================== #
# 2. OCR gating at the seam (OCR is a NoOp seam with NO call site this slice).
# =========================================================================== #


def test_get_ocr_provider_is_noop_even_when_flag_enabled():
    # Provider selection is inert this slice: enabling the flag does NOT swap in a
    # real backend — the honest NoOp is returned regardless (gating not wired).
    prov = get_ocr_provider(_cfg(primary_document_ocr_enabled=True))
    assert isinstance(prov, NoOpOcrProvider)
    assert prov.is_noop is True


class _RecordingOcrProvider(OcrProvider):
    """A fake OCR provider double: records calls, returns canned LOW-confidence text.

    Demonstrates the contract a real provider must satisfy at the seam — it honours
    ``primary_document_max_ocr_pages`` and never emits a high-confidence reading.
    """

    def __init__(self, canned_pages: int = 5) -> None:
        self.calls: list[tuple[int, int]] = []  # (byte_len, page_cap)
        self._canned_pages = canned_pages

    @property
    def provider_name(self) -> str:
        return "recording_fake"

    @property
    def is_noop(self) -> bool:
        return False

    async def extract(self, image_or_pdf_bytes: bytes, *, cfg: Settings | None = None) -> OcrResult:
        cfg = cfg or Settings()
        page_cap = max(1, cfg.primary_document_max_ocr_pages)
        self.calls.append((len(image_or_pdf_bytes), page_cap))
        excerpts = [
            PrimaryDocumentExcerpt(
                excerpt_id=f"O{i + 1}",
                text=f"canned ocr page {i + 1}",
                page_number=i + 1,
                extraction_method=METHOD_OCR,
                confidence=0.3,  # deliberately low — never a confident reading
                char_count=18,
            )
            for i in range(min(self._canned_pages, page_cap))
        ]
        return OcrResult(
            status=OCR_STATUS_EXTRACTED,
            provider_name=self.provider_name,
            excerpts=excerpts,
        )


def test_fake_ocr_provider_double_honors_page_cap_and_low_confidence():
    fake = _RecordingOcrProvider(canned_pages=5)
    cfg = _cfg(primary_document_max_ocr_pages=2, primary_document_min_extraction_confidence=0.6)
    result = asyncio.run(fake.extract(b"scanned-image-bytes", cfg=cfg))
    # Call recorded with the page cap passed through.
    assert fake.calls == [(len(b"scanned-image-bytes"), 2)]
    # Page cap honoured: 5 canned pages bounded to 2.
    assert len(result.excerpts) == 2
    assert all(e.page_number and e.page_number <= 2 for e in result.excerpts)
    assert all(e.extraction_method == METHOD_OCR for e in result.excerpts)
    # Never a confident reading — below the min-extraction-confidence threshold.
    assert all(e.confidence < cfg.primary_document_min_extraction_confidence for e in result.excerpts)
    assert isinstance(fake, OcrProvider) and fake.is_noop is False


def _ocr_spy(monkeypatch) -> list[int]:
    """Spy on NoOpOcrProvider.extract; records every OCR invocation (should be 0)."""
    calls: list[int] = []
    orig = NoOpOcrProvider.extract

    async def _spy(self, image_or_pdf_bytes, *, cfg=None):
        calls.append(len(image_or_pdf_bytes))
        return await orig(self, image_or_pdf_bytes, cfg=cfg)

    monkeypatch.setattr(ocr_mod.NoOpOcrProvider, "extract", _spy)
    return calls


def test_native_success_never_invokes_ocr(monkeypatch):
    calls = _ocr_spy(monkeypatch)
    r = extract_pdf(make_pdf(["Revenue: 20,616 million euros (EUR) in 2024."]))
    assert r.status == STATUS_EXTRACTED
    assert calls == []  # native text succeeded → OCR is not needed and not called


def test_scanned_pdf_never_invokes_ocr_even_when_enabled(monkeypatch):
    # OCR is unwired: even an empty (scanned) PDF WITH the flag ON does not invoke
    # any OCR provider — it degrades to metadata_only. (See module OCR-WIRING NOTE.)
    calls = _ocr_spy(monkeypatch)
    r = extract_pdf(make_pdf_no_text(), cfg=_cfg(primary_document_ocr_enabled=True))
    assert r.status == STATUS_METADATA_ONLY
    assert r.excerpts == []
    assert calls == []


def test_deep_path_scanned_pdf_never_invokes_ocr(monkeypatch):
    calls = _ocr_spy(monkeypatch)
    artifact = _run_deep(
        [_FakeStream(status_code=200, headers={"content-type": "application/pdf"}, body=make_pdf_no_text())],
        _resolver_returning("93.184.216.34"),
        monkeypatch,
        cfg=_cfg(primary_document_ocr_enabled=True),
    )
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.validated_facts == []
    assert calls == []  # deep flow never reaches OCR this slice


# =========================================================================== #
# 3. Transport error mid-download / on connect on the deep path.
# =========================================================================== #


def test_deep_path_transport_error_on_connect_degrades_honestly(monkeypatch):
    artifact = _run_deep(
        [_FakeStream(raise_exc=httpx.ConnectError("connection refused"))],
        _resolver_returning("93.184.216.34"),
        monkeypatch,
    )
    assert artifact.status != STATUS_EXTRACTED
    assert artifact.extraction is None
    assert artifact.validated_facts == []
    assert artifact.source_gaps  # honest gap, no fabricated evidence


def test_deep_path_transport_error_mid_download_degrades_honestly(monkeypatch):
    artifact = _run_deep(
        [
            _FakeStream(
                status_code=200,
                headers={"content-type": "application/pdf"},
                body=b"%PDF-1.4 " + b"x" * 64,
                raise_mid=httpx.ReadError("connection dropped mid-download"),
            )
        ],
        _resolver_returning("93.184.216.34"),
        monkeypatch,
    )
    assert artifact.status != STATUS_EXTRACTED
    assert artifact.extraction is None
    assert artifact.validated_facts == []
    assert artifact.source_gaps


# =========================================================================== #
# 4. Redirect handling at the fetch layer on the deep path.
# =========================================================================== #


def test_deep_path_follows_allowlisted_redirect_with_per_hop_revalidation(monkeypatch):
    resolver, seen = _recording_resolver({})  # every host resolves public
    artifact = _run_deep(
        [
            _FakeStream(
                status_code=302,
                headers={"location": "https://reports.richemont.com/final.pdf"},
                is_redirect=True,
            ),
            _FakeStream(
                status_code=200,
                headers={"content-type": "application/pdf"},
                body=make_pdf_with_table(CFR_TABLE_ROWS),
            ),
        ],
        resolver,
        monkeypatch,
    )
    # Redirect followed within the hop cap and the body extracted.
    assert artifact.status == STATUS_EXTRACTED
    assert artifact.source_url == "https://reports.richemont.com/final.pdf"
    # Per-hop host re-validation: BOTH the initial and the redirect host resolved.
    assert "www.richemont.com" in seen and "reports.richemont.com" in seen


def test_deep_path_blocks_redirect_to_off_allowlist_host(monkeypatch):
    artifact = _run_deep(
        [
            _FakeStream(
                status_code=302,
                headers={"location": "https://evil.example.com/x.pdf"},
                is_redirect=True,
            ),
            _ExplodingStream(),  # proves the body is never fetched after the block
        ],
        _resolver_returning("93.184.216.34"),
        monkeypatch,
    )
    assert artifact.status != STATUS_EXTRACTED
    assert artifact.extraction is None
    assert artifact.validated_facts == []
    assert any("redirected off the verified" in g.message for g in artifact.source_gaps)


def test_deep_path_blocks_redirect_to_rebinding_private_ip(monkeypatch):
    # Redirect target is ON the allowlist (registrable domain matches) but resolves
    # to a PRIVATE IP → per-hop resolved-IP re-validation blocks it (DNS rebinding).
    resolver, _seen = _recording_resolver(
        {"reports.richemont.com": "10.0.0.5"}, default="93.184.216.34"
    )
    artifact = _run_deep(
        [
            _FakeStream(
                status_code=302,
                headers={"location": "https://reports.richemont.com/x.pdf"},
                is_redirect=True,
            ),
            _ExplodingStream(),  # body must never be fetched
        ],
        resolver,
        monkeypatch,
    )
    assert artifact.status != STATUS_EXTRACTED
    assert artifact.extraction is None
    assert artifact.validated_facts == []
    joined = " ".join(g.message for g in artifact.source_gaps)
    assert "resolved ip" in joined  # blocked on the resolved-IP re-check, not the host


# =========================================================================== #
# 5. No-document company: IR page yields zero document links.
# =========================================================================== #


def _page_fetcher(links: list[SafeLink]):
    async def _fetch(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(requested_url=url, status_code=200, links=list(links))

    return _fetch


def _recording_deep_extractor(calls: list[str]):
    async def _extract(url, *, allowed_domains, title_hint=None, original_language=None, issuer_context=None):
        calls.append(url)
        return PrimaryDocumentArtifact(source_url=url, document_type="pdf", status=STATUS_EXTRACTION_FAILED)

    return _extract


def test_no_document_links_yields_honest_gap_no_artifacts():
    calls: list[str] = []
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_page_fetcher([]),  # zero document links discovered
            primary_document_extractor=_recording_deep_extractor(calls),
            cfg=_cfg(),
        )
    )
    assert calls == []  # extractor never invoked when there is nothing to fetch
    types = {i.source_type for i in collected.evidence_items}
    assert "company_ir_annual_report_excerpt" not in types
    assert "company_ir_financial_fact" not in types
    # The metadata index survives as a reference; an honest gap is recorded.
    assert "company_ir_annual_reports_index" in types
    assert any("annual report link not identified" in g.message for g in collected.source_gaps)
    assert collected.primary_document_artifacts == []
    import json

    assert not _has_forbidden(
        json.dumps([i.model_dump(mode="json") for i in collected.evidence_items])
    )


# =========================================================================== #
# 6. PDF resource-abuse: a very large page count is bounded by the page cap.
# =========================================================================== #


def test_large_page_count_pdf_bounded_by_page_cap():
    big = make_pdf([f"Page {i}: group business and revenue discussion." for i in range(80)])
    r = extract_pdf(big, cfg=_cfg(primary_document_max_pdf_pages=3))
    assert r.status == STATUS_EXTRACTED
    assert r.page_count == 80  # the true count is reported honestly
    assert r.truncated is True
    assert max((e.page_number or 0) for e in r.excerpts) <= 3  # only capped pages read
    assert any("only the first" in w.lower() for w in r.warnings)
