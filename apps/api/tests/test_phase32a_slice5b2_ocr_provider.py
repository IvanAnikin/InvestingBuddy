"""
Phase 32A Slice 5B.2 — real Azure Document Intelligence OCR provider (unit tests).

Fully offline and deterministic: NO real Azure calls, NO network. A FAKE Azure
SDK client double (duck-typed to the ``begin_analyze_document`` /
``AsyncLROPoller.result()`` surface) is injected into
``AzureDocumentIntelligenceOcrProvider`` so its mapping/timeout/error-handling
logic is exercised without the ``azure-ai-documentintelligence`` package ever
making a network call.

Covers:
  1. ``get_ocr_provider`` resolution: disabled -> NoOp; enabled but no endpoint
     -> NoOp (safe-before-provisioning); enabled + endpoint (no key) -> real
     provider, managed-identity credential; enabled + endpoint + key -> real
     provider, API-key credential.
  2. ``select_ocr_pages``: outline/bookmark heading match; bounded fallback to
     the first N pages; unreadable bytes -> no pages (never raises).
  3. ``OcrBudget``: cross-document cap.
  4. ``AzureDocumentIntelligenceOcrProvider.extract``: successful mapping of
     tables + paragraphs; timeout; 429 (throttled); 5xx (provider error);
     malformed/unexpected result shape (never raises past extract()).
  5. Secret-free by construction: OcrResult never carries an endpoint/key
     field; a raw exception message never reaches error_type/failure_code.
"""

from __future__ import annotations

import asyncio
from typing import Any

from app.core.config import Settings
from app.services.sources.ingestion_status import (
    FAILURE_OCR_MALFORMED_RESULT,
    FAILURE_OCR_PROVIDER_ERROR,
    FAILURE_OCR_PROVIDER_THROTTLED,
    FAILURE_OCR_TIMEOUT,
)
from app.services.sources.ocr_provider import (
    OCR_STATUS_EXTRACTED,
    OCR_STATUS_FAILED,
    AzureDocumentIntelligenceOcrProvider,
    NoOpOcrProvider,
    OcrBudget,
    OcrResult,
    get_ocr_provider,
    select_ocr_pages,
)
from tests.helpers.pdf_fixtures import make_pdf, make_pdf_with_image


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = dict(primary_document_ingestion_enabled=True)
    base.update(over)
    return Settings(**base)


# =========================================================================== #
# 1. get_ocr_provider resolution
# =========================================================================== #


def test_get_ocr_provider_disabled_returns_noop():
    prov = get_ocr_provider(_cfg(primary_document_ocr_enabled=False))
    assert isinstance(prov, NoOpOcrProvider)


def test_get_ocr_provider_enabled_but_unconfigured_returns_noop():
    prov = get_ocr_provider(
        _cfg(primary_document_ocr_enabled=True, azure_document_intelligence_endpoint="")
    )
    assert isinstance(prov, NoOpOcrProvider)


def test_get_ocr_provider_enabled_and_configured_prefers_managed_identity(monkeypatch):
    calls: list[str] = []

    class _FakeDefaultAzureCredential:
        def __init__(self) -> None:
            calls.append("managed_identity")

    monkeypatch.setattr(
        "azure.identity.aio.DefaultAzureCredential", _FakeDefaultAzureCredential
    )
    prov = get_ocr_provider(
        _cfg(
            primary_document_ocr_enabled=True,
            azure_document_intelligence_endpoint="https://example.cognitiveservices.azure.com",
            azure_document_intelligence_api_key="",
        )
    )
    assert isinstance(prov, AzureDocumentIntelligenceOcrProvider)
    assert prov.provider_name == "azure_document_intelligence"
    assert calls == ["managed_identity"]


def test_get_ocr_provider_enabled_with_key_uses_key_credential(monkeypatch):
    calls: list[str] = []

    class _FakeAzureKeyCredential:
        def __init__(self, key: str) -> None:
            calls.append(key)

    monkeypatch.setattr("azure.core.credentials.AzureKeyCredential", _FakeAzureKeyCredential)
    prov = get_ocr_provider(
        _cfg(
            primary_document_ocr_enabled=True,
            azure_document_intelligence_endpoint="https://example.cognitiveservices.azure.com",
            azure_document_intelligence_api_key="super-secret-key",
        )
    )
    assert isinstance(prov, AzureDocumentIntelligenceOcrProvider)
    # The credential was constructed with the key; the key is never exposed on
    # the provider/result itself (see the secret-free tests below).
    assert calls == ["super-secret-key"]


def test_get_ocr_provider_credential_failure_degrades_to_noop(monkeypatch):
    def _boom() -> None:
        raise RuntimeError("no identity available")

    monkeypatch.setattr("azure.identity.aio.DefaultAzureCredential", _boom)
    prov = get_ocr_provider(
        _cfg(
            primary_document_ocr_enabled=True,
            azure_document_intelligence_endpoint="https://example.cognitiveservices.azure.com",
        )
    )
    assert isinstance(prov, NoOpOcrProvider)


# =========================================================================== #
# 2. select_ocr_pages
# =========================================================================== #


def test_select_ocr_pages_falls_back_to_first_n_when_no_outline():
    raw = make_pdf_with_image("scanned page", width=200, height=100)
    pages = select_ocr_pages(raw, max_pages=3)
    assert pages == [1]  # single-page fixture; bounded, never expands


def test_select_ocr_pages_bounded_by_max_pages():
    # A multi-page native PDF has no outline either — still bounded fallback.
    raw = make_pdf(["p1", "p2", "p3", "p4", "p5"])
    pages = select_ocr_pages(raw, max_pages=2)
    assert pages == [1, 2]
    assert len(pages) <= 2


def test_select_ocr_pages_unreadable_bytes_returns_empty_never_raises():
    assert select_ocr_pages(b"not a pdf at all", max_pages=5) == []


def test_select_ocr_pages_empty_bytes_returns_empty():
    assert select_ocr_pages(b"", max_pages=5) == []


# =========================================================================== #
# 3. OcrBudget
# =========================================================================== #


def test_ocr_budget_caps_cross_document_usage():
    budget = OcrBudget(max_documents_per_run=2)
    assert budget.can_start_document() is True
    budget.record_document_started()
    assert budget.can_start_document() is True
    budget.record_document_started()
    assert budget.can_start_document() is False


def test_ocr_budget_zero_max_never_starts():
    budget = OcrBudget(max_documents_per_run=0)
    assert budget.can_start_document() is False


# =========================================================================== #
# 4. AzureDocumentIntelligenceOcrProvider.extract — fake SDK client
# =========================================================================== #


class _FakeRegion:
    def __init__(self, page_number: int) -> None:
        self.page_number = page_number


class _FakeCell:
    def __init__(self, row_index: int, column_index: int, content: str, page: int) -> None:
        self.row_index = row_index
        self.column_index = column_index
        self.content = content
        self.bounding_regions = [_FakeRegion(page)]


class _FakeTable:
    def __init__(self, row_count: int, column_count: int, cells: list[_FakeCell]) -> None:
        self.row_count = row_count
        self.column_count = column_count
        self.cells = cells


class _FakeParagraph:
    def __init__(self, content: str, page: int, confidence: float) -> None:
        self.content = content
        self.bounding_regions = [_FakeRegion(page)]
        self.confidence = confidence


class _FakeAnalyzeResult:
    def __init__(
        self,
        *,
        tables: list[_FakeTable] | None = None,
        paragraphs: list[_FakeParagraph] | None = None,
        pages: list[Any] | None = None,
    ) -> None:
        self.tables = tables or []
        self.paragraphs = paragraphs or []
        self.pages = pages or [object()]


class _FakePoller:
    def __init__(self, result: Any = None, exc: BaseException | None = None) -> None:
        self._result = result
        self._exc = exc

    async def result(self) -> Any:
        if self._exc is not None:
            raise self._exc
        return self._result


class _FakeAzureError(Exception):
    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class _FakeClient:
    """Duck-typed to DocumentIntelligenceClient.begin_analyze_document()."""

    def __init__(self, poller: _FakePoller | None = None, submit_exc: BaseException | None = None) -> None:
        self._poller = poller
        self._submit_exc = submit_exc
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    async def begin_analyze_document(self, model_id: str, *, body: bytes, pages: str | None) -> Any:
        self.calls.append({"model_id": model_id, "body_len": len(body), "pages": pages})
        if self._submit_exc is not None:
            raise self._submit_exc
        return self._poller

    async def close(self) -> None:
        self.closed = True


def _provider(client: _FakeClient) -> AzureDocumentIntelligenceOcrProvider:
    return AzureDocumentIntelligenceOcrProvider(
        endpoint="https://example.cognitiveservices.azure.com",
        credential=object(),
        client=client,
    )


def test_extract_success_maps_tables_and_paragraphs():
    table = _FakeTable(
        row_count=2,
        column_count=2,
        cells=[
            _FakeCell(0, 0, "Total assets", 1),
            _FakeCell(0, 1, "1,234", 1),
            _FakeCell(1, 0, "Total liabilities", 1),
            _FakeCell(1, 1, "800", 1),
        ],
    )
    para = _FakeParagraph("Consolidated Balance Sheet", page=1, confidence=0.9)
    client = _FakeClient(poller=_FakePoller(result=_FakeAnalyzeResult(tables=[table], paragraphs=[para])))
    prov = _provider(client)

    result = asyncio.run(prov.extract(b"pdf-bytes", cfg=Settings(), pages=[1], timeout_seconds=5))

    assert result.status == OCR_STATUS_EXTRACTED
    assert result.has_content is True
    assert len(result.tables) == 1
    assert result.tables[0].extraction_method == "ocr"
    assert result.tables[0].row_count == 2
    assert len(result.excerpts) == 1
    assert result.excerpts[0].confidence == 0.9
    assert result.provider_name == "azure_document_intelligence"
    assert result.duration_ms is not None
    # Submitted with the bounded page spec, not "every page".
    assert client.calls == [{"model_id": "prebuilt-layout", "body_len": len(b"pdf-bytes"), "pages": "1"}]


def test_extract_no_content_returns_unavailable_not_malformed():
    client = _FakeClient(poller=_FakePoller(result=_FakeAnalyzeResult(tables=[], paragraphs=[])))
    prov = _provider(client)
    result = asyncio.run(prov.extract(b"pdf-bytes", cfg=Settings(), pages=[1]))
    assert result.has_content is False
    assert result.status != OCR_STATUS_EXTRACTED


def test_extract_timeout_degrades_honestly():
    class _NeverPoller:
        async def result(self) -> Any:
            await asyncio.sleep(10)

    client = _FakeClient(poller=_NeverPoller())
    prov = _provider(client)
    result = asyncio.run(
        prov.extract(b"pdf-bytes", cfg=Settings(), pages=[1], timeout_seconds=0.01)
    )
    assert result.status == OCR_STATUS_FAILED
    assert result.failure_code == FAILURE_OCR_TIMEOUT
    assert result.error_type == "TimeoutError"
    assert result.excerpts == [] and result.tables == []


def test_extract_429_maps_to_throttled():
    client = _FakeClient(submit_exc=_FakeAzureError("rate limited", status_code=429))
    prov = _provider(client)
    result = asyncio.run(prov.extract(b"pdf-bytes", cfg=Settings(), pages=[1]))
    assert result.status == OCR_STATUS_FAILED
    assert result.failure_code == FAILURE_OCR_PROVIDER_THROTTLED
    assert result.error_type == "_FakeAzureError"


def test_extract_5xx_maps_to_provider_error():
    client = _FakeClient(submit_exc=_FakeAzureError("server error", status_code=503))
    prov = _provider(client)
    result = asyncio.run(prov.extract(b"pdf-bytes", cfg=Settings(), pages=[1]))
    assert result.status == OCR_STATUS_FAILED
    assert result.failure_code == FAILURE_OCR_PROVIDER_ERROR


def test_extract_malformed_result_never_raises():
    class _Malformed:
        # No .tables / .paragraphs / .pages at all AND a broken __getattr__-ish
        # object would still be handled defensively via getattr(default=[]); to
        # genuinely force the outer except, make `.tables` a property that raises.
        @property
        def tables(self) -> Any:
            raise RuntimeError("boom")

    client = _FakeClient(poller=_FakePoller(result=_Malformed()))
    prov = _provider(client)
    result = asyncio.run(prov.extract(b"pdf-bytes", cfg=Settings(), pages=[1]))
    assert result.status == OCR_STATUS_FAILED
    assert result.failure_code == FAILURE_OCR_MALFORMED_RESULT
    assert result.error_type == "RuntimeError"


def test_extract_bounds_pages_argument_to_selected_pages():
    client = _FakeClient(poller=_FakePoller(result=_FakeAnalyzeResult()))
    prov = _provider(client)
    asyncio.run(prov.extract(b"x", cfg=Settings(), pages=[2, 5, 7]))
    assert client.calls[0]["pages"] == "2,5,7"


# =========================================================================== #
# 5. Secret-free by construction
# =========================================================================== #


def test_ocr_result_schema_never_carries_endpoint_or_key():
    fields = set(OcrResult.model_fields.keys())
    assert "endpoint" not in fields
    assert "api_key" not in fields
    assert "credential" not in fields


def test_ocr_result_error_type_is_never_a_message(monkeypatch):
    client = _FakeClient(
        submit_exc=RuntimeError("failed calling https://real-endpoint.example/secret?key=abc")
    )
    prov = _provider(client)
    result = asyncio.run(prov.extract(b"x", cfg=Settings(), pages=[1]))
    assert result.error_type == "RuntimeError"
    assert "real-endpoint" not in (result.error_type or "")
    assert "secret" not in (result.error_type or "")
    for gap in result.source_gaps:
        assert "real-endpoint" not in gap
