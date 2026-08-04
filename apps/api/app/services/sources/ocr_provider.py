"""
Bounded, honest OCR-provider abstraction — Phase 32A Slice 5 (foundation).

Parallel in shape to the Phase 30A ``TranslationProvider`` seam: a small,
provider-pluggable interface for turning ONE scanned (image-only) primary
document — the case where native PDF text extraction returns nothing — into
bounded, citeable text excerpts. This is FOUNDATION ONLY: the only provider
shipped in this slice is the honest no-op default; a real Azure Document
Intelligence adapter is a documented follow-up. Nothing here is wired into the
connector / council / evidence pack, and OCR is OFF by default.

Hard product invariants enforced here:
  * **Never fabricates text.** The DEFAULT provider returns an HONEST empty
    result with status ``ocr_unavailable`` and confidence 0 — never invented
    text that could be mistaken for a real reading of a filing.
  * **Gated OFF.** ``get_ocr_provider`` returns the no-op provider in this slice
    regardless of config; when ``primary_document_ocr_enabled`` is False callers
    must not invoke OCR at all.
  * **Bounded + bomb-safe.** ``guard_image_pixels`` pins
    ``PIL.Image.MAX_IMAGE_PIXELS`` to the configured cap and rejects an oversized
    image before any raster is decoded (decompression-bomb guard).
  * **Secret-free.** Nothing here logs image bytes or extracted text; on error
    only ``type(exc).__name__`` is recorded.
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.primary_document_extractor import (
    METHOD_OCR,
    PrimaryDocumentExcerpt,
)

# OCR outcome vocabulary (neutral/factual — never a rating vocabulary).
OCR_STATUS_EXTRACTED = "ocr_extracted"
OCR_STATUS_UNAVAILABLE = "ocr_unavailable"
OCR_STATUS_DISABLED = "ocr_disabled"
OCR_STATUS_FAILED = "ocr_failed"


class OcrResult(BaseModel):
    """The bounded result of an OCR pass over ONE scanned document/image."""

    status: str = OCR_STATUS_UNAVAILABLE
    extraction_method: str = METHOD_OCR
    excerpts: list[PrimaryDocumentExcerpt] = Field(default_factory=list)
    page_count: int | None = None
    provider_name: str = "noop"
    warnings: list[str] = Field(default_factory=list)
    source_gaps: list[str] = Field(default_factory=list)
    # ONLY ``type(exc).__name__`` ever lands here — never image bytes/text.
    error_type: str | None = None

    @property
    def has_content(self) -> bool:
        return bool(self.excerpts)


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
    ) -> OcrResult:
        """OCR ONE scanned document/image into bounded excerpts.

        Bounded by ``primary_document_max_ocr_pages`` + the per-excerpt caps; must
        never fabricate text and never raise (a failure degrades to an honest
        empty result)."""


class NoOpOcrProvider(OcrProvider):
    """Honest, offline default provider (parallel to ``FakeTranslationProvider``).

    Performs no OCR and returns an empty result marked ``ocr_unavailable`` with
    confidence 0 — it NEVER fabricates text. This is the only provider shipped in
    this slice; it needs no credentials and makes no network call.
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

    Decompression-bomb guard for the (future) OCR raster path: pins
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


def get_ocr_provider(settings: Settings | None = None) -> OcrProvider:
    """Resolve an OCR provider.

    In this slice ALWAYS returns :class:`NoOpOcrProvider` — a real Azure Document
    Intelligence adapter is a documented follow-up. Callers must still check
    ``primary_document_ocr_enabled`` and not invoke OCR at all when it is False.
    """
    _ = settings or default_settings
    return NoOpOcrProvider()


__all__ = [
    "OCR_STATUS_EXTRACTED",
    "OCR_STATUS_UNAVAILABLE",
    "OCR_STATUS_DISABLED",
    "OCR_STATUS_FAILED",
    "OcrResult",
    "OcrProvider",
    "NoOpOcrProvider",
    "guard_image_pixels",
    "get_ocr_provider",
]
