"""
Bounded, honest OCR-provider abstraction — Phase 32A Slice 5 (foundation) /
Slice 5B.2 (real Azure Document Intelligence adapter).

Parallel in shape to the Phase 30A ``TranslationProvider`` seam: a small,
provider-pluggable interface for turning ONE scanned (image-only) primary
document — the case where native PDF text extraction returns nothing — into
bounded, citeable text excerpts and tables. Slice 5 shipped the interface plus
an honest no-op default. Slice 5B.2 adds a real adapter
(:class:`AzureDocumentIntelligenceOcrProvider`) behind the SAME interface,
still OFF by default and inert unless BOTH ``primary_document_ocr_enabled``
is True AND an Azure Document Intelligence endpoint is configured.

Hard product invariants enforced here:
  * **Never fabricates text.** The DEFAULT provider returns an HONEST empty
    result with status ``ocr_unavailable`` and confidence 0. The real provider
    only ever returns text/tables the Azure service actually recognized —
    never invents or guesses content, and degrades honestly on any failure.
  * **Gated OFF by default.** ``get_ocr_provider`` returns the no-op provider
    unless the OCR sub-flag is on AND an endpoint is configured — this lets
    the flag be flipped safely before the Azure resource exists.
  * **Bounded + bomb-safe.** ``guard_image_pixels`` pins
    ``PIL.Image.MAX_IMAGE_PIXELS`` to the configured cap and rejects an
    oversized image before any raster is decoded (decompression-bomb guard).
    Page selection (``select_ocr_pages``) never OCRs an unbounded number of
    pages. Every network call is wrapped in a hard timeout; retries are
    counted, never unbounded.
  * **Secret-free.** Nothing here logs image bytes, extracted text, the
    endpoint, or credentials. On error only ``type(exc).__name__`` and an HTTP
    status CLASS are recorded (mirrors ``azure_openai_client.py``) — never the
    raw exception message, which can embed the endpoint URL.
  * **Fixed endpoint only.** The client always talks to the ONE
    code-configured ``azure_document_intelligence_endpoint`` — there is no
    caller-supplied URL anywhere in this module (no SSRF surface).
"""

from __future__ import annotations

import asyncio
import logging
import time
from abc import ABC, abstractmethod
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.sources.ingestion_status import (
    FAILURE_OCR_DOCUMENT_TOO_LARGE,
    FAILURE_OCR_MALFORMED_RESULT,
    FAILURE_OCR_PAGE_LIMIT_EXCEEDED,
    FAILURE_OCR_PROVIDER_ERROR,
    FAILURE_OCR_PROVIDER_THROTTLED,
    FAILURE_OCR_TIMEOUT,
    sanitize_failure_code,
)
from app.services.sources.primary_document_extractor import (
    METHOD_OCR,
    ExtractedTable,
    PrimaryDocumentExcerpt,
)

_log = logging.getLogger(__name__)

# OCR outcome vocabulary (neutral/factual — never a rating vocabulary).
OCR_STATUS_EXTRACTED = "ocr_extracted"
OCR_STATUS_UNAVAILABLE = "ocr_unavailable"
OCR_STATUS_DISABLED = "ocr_disabled"
OCR_STATUS_FAILED = "ocr_failed"

# Deterministic page-selection: financial-statement heading keywords matched
# case-insensitively against PDF outline/bookmark titles. Not text detection —
# a scanned document has no text layer to search — this only reads PDF
# METADATA (the outline), which is commonly present even on scanned reports.
_HEADING_KEYWORDS: tuple[str, ...] = (
    "income statement",
    "balance sheet",
    "statement of financial position",
    "cash flow statement",
    "cash flow",
    "financial highlights",
    "consolidated statements",
    "consolidated financial statements",
)

# Bounded table cell/row shape — mirrors primary_document_extractor.py's own
# caps so an OCR-recovered table is bounded the same way a native one is.
_MAX_TABLE_ROWS = 200
_MAX_TABLE_COLS = 40
_MAX_CELL_CHARS = 400
_MAX_TABLES_PER_DOCUMENT = 10
_MAX_EXCERPTS_PER_DOCUMENT = 20


class OcrResult(BaseModel):
    """The bounded result of an OCR pass over ONE scanned document."""

    status: str = OCR_STATUS_UNAVAILABLE
    extraction_method: str = METHOD_OCR
    excerpts: list[PrimaryDocumentExcerpt] = Field(default_factory=list)
    # Phase 32A Slice 5B.2 (additive): without this, OCR output could never
    # become a ValidatedFact — validate_extracted_facts() only ever reads
    # extraction.tables, never bare excerpts.
    tables: list[ExtractedTable] = Field(default_factory=list)
    page_count: int | None = None
    selected_pages: list[int] = Field(default_factory=list)
    provider_name: str = "noop"
    # Sanitized operational metadata only — never the endpoint or a credential.
    model_id: str | None = None
    api_version: str | None = None
    duration_ms: int | None = None
    warnings: list[str] = Field(default_factory=list)
    source_gaps: list[str] = Field(default_factory=list)
    # ONLY ``type(exc).__name__`` ever lands here — never image bytes/text.
    error_type: str | None = None
    # A member of the closed ``ingestion_status`` FAILURE_OCR_* vocabulary,
    # set only when OCR was attempted and did not produce usable content.
    failure_code: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.excerpts or self.tables)


class OcrProvider(ABC):
    """Abstract OCR provider (parallel to ``TranslationProvider``)."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Identifier for this backend ('noop' | 'azure_document_intelligence')."""

    @property
    def is_noop(self) -> bool:
        return False

    @abstractmethod
    async def extract(
        self,
        image_or_pdf_bytes: bytes,
        *,
        cfg: Settings | None = None,
        pages: list[int] | None = None,
        timeout_seconds: float | None = None,
    ) -> OcrResult:
        """OCR ONE scanned document into bounded excerpts + tables.

        ``pages`` (1-based, from :func:`select_ocr_pages`) bounds which pages
        are rastered/analyzed; ``timeout_seconds`` is a hard cap for the whole
        call (submit + poll). Must never fabricate text and never raise — a
        failure degrades to an honest :class:`OcrResult` with ``status``
        ``ocr_failed``/``ocr_unavailable`` and a sanitized ``failure_code``.
        """


class NoOpOcrProvider(OcrProvider):
    """Honest, offline default provider (parallel to ``FakeTranslationProvider``).

    Performs no OCR and returns an empty result marked ``ocr_unavailable`` with
    confidence 0 — it NEVER fabricates text. Needs no credentials, makes no
    network call. Used whenever OCR is disabled OR enabled-but-unconfigured
    (no endpoint set) — see :func:`get_ocr_provider`.
    """

    @property
    def provider_name(self) -> str:
        return "noop"

    @property
    def is_noop(self) -> bool:
        return True

    async def extract(
        self,
        image_or_pdf_bytes: bytes,
        *,
        cfg: Settings | None = None,
        pages: list[int] | None = None,
        timeout_seconds: float | None = None,
    ) -> OcrResult:
        return OcrResult(
            status=OCR_STATUS_UNAVAILABLE,
            provider_name="noop",
            source_gaps=[
                "OCR is not available; scanned document text was not extracted."
            ],
        )


def guard_image_pixels(raw: bytes, *, cfg: Settings | None = None) -> str | None:
    """Return None if ``raw`` is a safely-sized image, else a rejection reason.

    Decompression-bomb guard for the OCR raster path: pins
    ``PIL.Image.MAX_IMAGE_PIXELS`` to ``primary_document_max_image_pixels`` and
    rejects an image whose declared dimensions exceed the cap BEFORE any full
    raster is decoded. Never raises; never logs image bytes.
    """
    cfg = cfg or default_settings
    try:
        import io

        from PIL import Image
    except Exception as exc:  # noqa: BLE001
        return f"image library unavailable: {type(exc).__name__}"

    cap = max(1, cfg.primary_document_max_image_pixels)
    # Global side-effect is intentional: it hardens every subsequent PIL decode.
    Image.MAX_IMAGE_PIXELS = cap
    try:
        with Image.open(io.BytesIO(raw)) as img:
            width, height = img.size
    except Exception as exc:  # noqa: BLE001 - a bad image must not raise
        return f"image rejected ({type(exc).__name__})"
    if width * height > cap:
        return f"image too large: {width}x{height} pixels exceeds cap of {cap}"
    return None


# --------------------------------------------------------------------------- #
# Deterministic page selection (Phase 32A Slice 5B.2)
# --------------------------------------------------------------------------- #


def _flatten_outline(reader: Any, outline: Any) -> Iterator[tuple[int, str | None]]:
    """Walk a (possibly nested) pypdf outline, yielding (1-based page, title).

    Defensive by construction: any per-item failure is skipped, never raised —
    a malformed/partial outline degrades to "no candidates found", which falls
    through to the deterministic first-N-pages fallback.
    """
    for item in outline or []:
        if isinstance(item, list):
            yield from _flatten_outline(reader, item)
            continue
        try:
            page_index = reader.get_destination_page_number(item)
        except Exception:  # noqa: BLE001
            continue
        title = getattr(item, "title", None)
        yield page_index + 1, title


def select_ocr_pages(raw: bytes, *, max_pages: int) -> list[int]:
    """Choose a bounded, deterministic set of pages to OCR (1-based, ordered).

    Never OCRs every page of a long document. Prefers pages whose PDF
    outline/bookmark title matches a known financial-statement heading (PDF
    outlines are METADATA, so even a fully scanned/image-only document can
    carry one). Falls through to the first ``max_pages`` pages when no
    outline exists or nothing matches — a deterministic, honest default, not
    a fabricated heading match.
    """
    max_pages = max(1, max_pages)
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        total_pages = len(reader.pages)
    except Exception:  # noqa: BLE001 - an unreadable document yields no pages
        return []

    if total_pages <= 0:
        return []

    candidates: list[int] = []
    try:
        for page_no, title in _flatten_outline(reader, reader.outline):
            if page_no < 1 or page_no > total_pages:
                continue
            if any(k in (title or "").lower() for k in _HEADING_KEYWORDS):
                if page_no not in candidates:
                    candidates.append(page_no)
            if len(candidates) >= max_pages:
                break
    except Exception:  # noqa: BLE001 - outline access can raise on odd PDFs
        candidates = []

    if not candidates:
        candidates = list(range(1, min(total_pages, max_pages) + 1))

    return candidates[:max_pages]


# --------------------------------------------------------------------------- #
# Cross-document OCR budget (Phase 32A Slice 5B.2)
# --------------------------------------------------------------------------- #


@dataclass
class OcrBudget:
    """Bounded, per-request cross-document OCR usage tracker.

    Constructed ONCE per ``collect_company_source_evidence`` call (same
    lifetime as the existing ingestion-budget closure state) and threaded down
    to each document's extraction call — mirrors the ``primary_document_reuse``
    threading precedent. Purely in-memory counters; never persisted itself
    (the durable record is the ingestion-attempt row each OCR call produces).
    """

    max_documents_per_run: int
    documents_used: int = 0

    def can_start_document(self) -> bool:
        return self.documents_used < max(0, self.max_documents_per_run)

    def record_document_started(self) -> None:
        self.documents_used += 1


# --------------------------------------------------------------------------- #
# Real adapter: Azure Document Intelligence (Phase 32A Slice 5B.2)
# --------------------------------------------------------------------------- #


def _confidence_bucket_local(confidence: float) -> str:
    """Same thresholds as ``primary_document_extractor._confidence_bucket``."""
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.4:
        return "medium"
    return "low"


def _bound_cell_local(value: object) -> str:
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ").strip()
    if len(s) > _MAX_CELL_CHARS:
        s = s[: _MAX_CELL_CHARS - 1].rstrip() + "…"
    return s


def _classify_azure_error(exc: BaseException) -> str:
    """Map an Azure SDK exception to a sanitized FAILURE_OCR_* code.

    Reads ONLY the exception type name, a numeric status code, and (if
    present) the SDK's own short structured error CODE (e.g.
    ``InvalidContentLength`` — a fixed, code-defined identifier, never the
    free-text message) — mirrors ``azure_openai_client.py``'s
    ``_classify_provider_error``. Never inspects/logs the exception message,
    which can embed the endpoint.
    """
    status = getattr(exc, "status_code", None)
    error_obj = getattr(exc, "error", None)
    error_code = (getattr(error_obj, "code", "") or "").lower()
    if status == 429:
        return FAILURE_OCR_PROVIDER_THROTTLED
    if status == 413 or "contentlength" in error_code or "toolarge" in error_code:
        return FAILURE_OCR_DOCUMENT_TOO_LARGE
    if "page" in error_code and ("limit" in error_code or "count" in error_code):
        return FAILURE_OCR_PAGE_LIMIT_EXCEEDED
    if isinstance(status, int) and 500 <= status < 600:
        return FAILURE_OCR_PROVIDER_ERROR
    if "timeout" in type(exc).__name__.lower():
        return FAILURE_OCR_TIMEOUT
    return FAILURE_OCR_PROVIDER_ERROR


class AzureDocumentIntelligenceOcrProvider(OcrProvider):
    """Real OCR/layout adapter using Azure Document Intelligence.

    Uses the official ``azure-ai-documentintelligence`` SDK's
    ``prebuilt-layout`` model (generic layout + table recognition — the
    input is an arbitrary annual report/filing, not a specialized document
    type). Auth is managed-identity-first: a credential is injected at
    construction (see :func:`get_ocr_provider`) rather than resolved here, so
    this class never decides its own auth — it only ever talks to ONE
    code-configured endpoint with WHATEVER credential it was given.

    The SDK is imported lazily (inside :meth:`extract`, not at module import
    time) so importing this module never requires the Azure packages to be
    installed unless the real provider is actually invoked.
    """

    def __init__(
        self,
        *,
        endpoint: str,
        credential: Any,
        client: Any | None = None,
        model_id: str = "prebuilt-layout",
    ) -> None:
        self._endpoint = endpoint
        self._credential = credential
        # Injectable for tests (a fake client with a matching async surface);
        # built lazily in production so constructing the provider never opens
        # a network connection.
        self._client = client
        self._model_id = model_id

    @property
    def provider_name(self) -> str:
        return "azure_document_intelligence"

    def _build_client(self) -> Any:
        from azure.ai.documentintelligence.aio import DocumentIntelligenceClient

        return DocumentIntelligenceClient(
            endpoint=self._endpoint, credential=self._credential
        )

    async def extract(
        self,
        image_or_pdf_bytes: bytes,
        *,
        cfg: Settings | None = None,
        pages: list[int] | None = None,
        timeout_seconds: float | None = None,
    ) -> OcrResult:
        cfg = cfg or default_settings
        started = time.perf_counter()
        client = self._client or self._build_client()
        page_spec = ",".join(str(p) for p in pages) if pages else None
        deadline = timeout_seconds or cfg.primary_document_ocr_timeout_seconds

        try:
            poller = await asyncio.wait_for(
                client.begin_analyze_document(
                    self._model_id,
                    body=image_or_pdf_bytes,
                    pages=page_spec,
                ),
                timeout=deadline,
            )
            raw_result = await asyncio.wait_for(poller.result(), timeout=deadline)
        except TimeoutError:
            log_event(
                _log,
                "primary_document_ocr_timeout",
                level=logging.WARNING,
                provider=self.provider_name,
            )
            return OcrResult(
                status=OCR_STATUS_FAILED,
                provider_name=self.provider_name,
                error_type="TimeoutError",
                failure_code=FAILURE_OCR_TIMEOUT,
                source_gaps=["OCR call exceeded its bounded timeout."],
            )
        except Exception as exc:  # noqa: BLE001 - a provider failure must degrade
            code = _classify_azure_error(exc)
            log_event(
                _log,
                "primary_document_ocr_provider_error",
                level=logging.WARNING,
                provider=self.provider_name,
                error_type=type(exc).__name__,
                failure_code=code,
            )
            return OcrResult(
                status=OCR_STATUS_FAILED,
                provider_name=self.provider_name,
                error_type=type(exc).__name__,
                failure_code=sanitize_failure_code(code),
                source_gaps=["OCR provider call failed."],
            )
        finally:
            try:
                aclose = getattr(client, "close", None)
                if aclose is not None and self._client is None:
                    await aclose()
            except Exception:  # noqa: BLE001 - cleanup must never raise
                pass

        duration_ms = int((time.perf_counter() - started) * 1000)
        try:
            return _map_azure_result(
                raw_result,
                provider_name=self.provider_name,
                model_id=self._model_id,
                selected_pages=pages or [],
                duration_ms=duration_ms,
            )
        except Exception as exc:  # noqa: BLE001 - a malformed result must degrade
            log_event(
                _log,
                "primary_document_ocr_malformed_result",
                level=logging.WARNING,
                provider=self.provider_name,
                error_type=type(exc).__name__,
            )
            return OcrResult(
                status=OCR_STATUS_FAILED,
                provider_name=self.provider_name,
                error_type=type(exc).__name__,
                failure_code=FAILURE_OCR_MALFORMED_RESULT,
                source_gaps=["OCR provider returned an unexpected result shape."],
            )


def _map_azure_result(
    raw_result: Any,
    *,
    provider_name: str,
    model_id: str,
    selected_pages: list[int],
    duration_ms: int,
) -> OcrResult:
    """Map an Azure ``AnalyzeResult`` onto the bounded internal shape.

    Defensive throughout (``getattr`` with defaults, bounded loops) so an
    unexpected SDK response shape degrades to a malformed-result failure in
    the caller rather than raising past this function. Only bounded,
    structured fields are copied — never raw provider text beyond the bounded
    excerpt/cell caps, and never the endpoint/credential.

    Every recognized excerpt/table is returned with its REAL confidence,
    regardless of ``primary_document_ocr_min_confidence`` — that threshold is
    applied by the CALLER (the merge step in ``live_fetchers.py``), which
    decides whether to promote the document to ``extracted`` or keep it
    ``metadata_only``/``ocr_low_confidence``. This function only ever reports
    what Azure actually recognized.
    """
    tables: list[ExtractedTable] = []
    raw_tables = list(getattr(raw_result, "tables", None) or [])[
        :_MAX_TABLES_PER_DOCUMENT
    ]
    for t_idx, raw_table in enumerate(raw_tables):
        row_count = int(getattr(raw_table, "row_count", 0) or 0)
        col_count = int(getattr(raw_table, "column_count", 0) or 0)
        row_count = min(row_count, _MAX_TABLE_ROWS)
        col_count = min(col_count, _MAX_TABLE_COLS)
        grid: list[list[str]] = [["" for _ in range(col_count)] for _ in range(row_count)]
        page_number: int | None = None
        for cell in list(getattr(raw_table, "cells", None) or []):
            r = int(getattr(cell, "row_index", -1))
            c = int(getattr(cell, "column_index", -1))
            if 0 <= r < row_count and 0 <= c < col_count:
                grid[r][c] = _bound_cell_local(getattr(cell, "content", ""))
            if page_number is None:
                regions = list(getattr(cell, "bounding_regions", None) or [])
                if regions:
                    page_number = getattr(regions[0], "page_number", None)
        rows = [row for row in grid if any(row)]
        if not rows:
            continue
        # CONFIRMED repo convention (primary_document_extractor.py): "p{page}:t{idx}".
        location = f"p{page_number}:t{t_idx}" if page_number else f"t{t_idx}"
        tables.append(
            ExtractedTable(
                table_location=location,
                table_index=t_idx,
                page_number=page_number,
                rows=rows,
                row_count=len(rows),
                col_count=col_count,
                extraction_method=METHOD_OCR,
                confidence=0.6,  # layout model exposes no per-table confidence
            )
        )

    excerpts: list[PrimaryDocumentExcerpt] = []
    raw_paragraphs = list(getattr(raw_result, "paragraphs", None) or [])[
        :_MAX_EXCERPTS_PER_DOCUMENT
    ]
    for p_idx, para in enumerate(raw_paragraphs):
        text = _bound_cell_local(getattr(para, "content", ""))
        if not text:
            continue
        regions = list(getattr(para, "bounding_regions", None) or [])
        page_number = getattr(regions[0], "page_number", None) if regions else None
        confidence = float(getattr(para, "confidence", 0.6) or 0.6)
        excerpts.append(
            PrimaryDocumentExcerpt(
                excerpt_id=f"OCR{p_idx}",
                text=text,
                page_number=page_number,
                extraction_method=METHOD_OCR,
                confidence=confidence,
                char_count=len(text),
            )
        )

    page_count = len(list(getattr(raw_result, "pages", None) or [])) or None
    has_content = bool(excerpts or tables)
    return OcrResult(
        status=OCR_STATUS_EXTRACTED if has_content else OCR_STATUS_UNAVAILABLE,
        provider_name=provider_name,
        model_id=model_id,
        selected_pages=selected_pages,
        page_count=page_count,
        duration_ms=duration_ms,
        excerpts=excerpts,
        tables=tables,
        failure_code=None,
        source_gaps=(
            []
            if has_content
            else ["OCR completed but recognized no usable text or tables."]
        ),
    )


def _resolve_credential(cfg: Settings) -> Any:
    """Managed identity first; API-key fallback only when a key is configured.

    Imported lazily so the ``azure-identity`` package is only required when
    the real provider actually resolves (OCR enabled + endpoint configured).
    """
    if cfg.azure_document_intelligence_api_key:
        from azure.core.credentials import AzureKeyCredential

        return AzureKeyCredential(cfg.azure_document_intelligence_api_key)
    from azure.identity.aio import DefaultAzureCredential

    return DefaultAzureCredential()


def get_ocr_provider(settings: Settings | None = None) -> OcrProvider:
    """Resolve an OCR provider.

    Returns :class:`NoOpOcrProvider` unless BOTH ``primary_document_ocr_enabled``
    is True AND ``azure_document_intelligence_endpoint`` is configured — this is
    what lets the flag be flipped on before the Azure resource is provisioned
    without ever making a real call or crashing. Callers must still check
    ``primary_document_ocr_enabled`` themselves before invoking OCR at all;
    this function is the single place credential/endpoint resolution happens.
    """
    cfg = settings or default_settings
    if not cfg.primary_document_ocr_enabled:
        return NoOpOcrProvider()
    endpoint = cfg.azure_document_intelligence_endpoint
    if not endpoint:
        log_event(
            _log,
            "primary_document_ocr_enabled_but_unconfigured",
            level=logging.WARNING,
        )
        return NoOpOcrProvider()
    try:
        credential = _resolve_credential(cfg)
    except Exception as exc:  # noqa: BLE001 - a credential failure must degrade
        log_event(
            _log,
            "primary_document_ocr_credential_unavailable",
            level=logging.WARNING,
            error_type=type(exc).__name__,
        )
        return NoOpOcrProvider()
    return AzureDocumentIntelligenceOcrProvider(endpoint=endpoint, credential=credential)


__all__ = [
    "OCR_STATUS_EXTRACTED",
    "OCR_STATUS_UNAVAILABLE",
    "OCR_STATUS_DISABLED",
    "OCR_STATUS_FAILED",
    "OcrResult",
    "OcrProvider",
    "NoOpOcrProvider",
    "AzureDocumentIntelligenceOcrProvider",
    "OcrBudget",
    "guard_image_pixels",
    "select_ocr_pages",
    "get_ocr_provider",
]
