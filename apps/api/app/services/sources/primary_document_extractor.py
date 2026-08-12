"""
Bounded, network-free primary-document extraction — Phase 32A Slice 5.

Turns the RAW bytes of an issuer's OWN primary document (annual report /
registration document, as native PDF or HTML) into a small, bounded, citeable
set of text *excerpts* + structured *tables* — never the whole document. It is a
richer, structure-aware sibling of ``document_text_extractor`` (Phase 29B.2): it
adds pdfplumber table extraction, HTML DOM-aware boilerplate removal, per-item
extraction provenance (method / page / table location / confidence) and a raw
``content_hash`` so a later slice can persist + dedup ``ExtractedDocument`` rows.

This module is FOUNDATION ONLY: it is a pure, synchronous, network-free function
library with its own unit tests. It is NOT wired into the connector, council,
evidence pack, or persistence in this slice — the existing extraction paths keep
their exact default behaviour. A later slice performs the wiring.

Design guarantees (mirroring the Phase 29B.2 extractor + the safe fetcher):
  * **Bounded.** Page count, per-excerpt length, excerpt count, table rows/cols,
    total extracted characters and a wall-clock budget are all capped — a whole
    filing is never carried around and a decompression bomb cannot exhaust memory
    or hang the process.
  * **Honest.** A wrong magic byte / malformed / encrypted PDF degrades to
    ``extraction_failed``; a valid-but-scanned (image-only) PDF or an empty HTML
    body degrades to ``metadata_only`` — text is never fabricated.
  * **Never raises.** Every parse error is caught and recorded as a status +
    honest gap; on error only ``type(exc).__name__`` is stored, never bytes/text.
  * **Injection-inert.** Extracted text is treated as UNTRUSTED DATA: it is never
    executed/interpreted and prompt-injection markers are NOT stripped — they
    must survive verbatim as inert data for a downstream prompt-boundary guard.
  * **Secret-free.** Nothing here logs document bytes or extracted text.
"""

from __future__ import annotations

import hashlib
import time
from collections.abc import Iterator, Sequence
from html.parser import HTMLParser

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.document_text_extractor import (
    EVIDENCE_TYPE_BUSINESS,
    EVIDENCE_TYPE_GENERAL,
    DocumentExcerpt,
    DocumentTextExtraction,
    _blocks,
    _classify,
    _relevance,
)
from app.services.sources.ingestion_status import (
    FAILURE_CLIENT_UNAVAILABLE,
    FAILURE_EMPTY_EXTRACTION,
    FAILURE_ENCRYPTED_PDF,
    FAILURE_EXTRACTION_TIMEOUT,
    FAILURE_MALFORMED_PDF,
    FAILURE_NOT_A_PDF,
    FAILURE_PASSWORD_PROTECTED_PDF,
    FAILURE_SCANNED_NO_TEXT,
    FAILURE_UNSUPPORTED_CONTENT_TYPE,
)
from app.services.sources.language import detect_language
from app.services.sources.safe_web_fetcher import looks_like_pdf

# --------------------------------------------------------------------------- #
# Vocabulary (neutral, factual — never a rating vocabulary)
# --------------------------------------------------------------------------- #

# How the text was obtained (matches ExtractedDocument.extraction_method).
METHOD_NATIVE_PDF = "native_pdf"
METHOD_HTML = "html"
METHOD_OCR = "ocr"

# Ingestion outcome (matches ExtractedDocument.status).
STATUS_EXTRACTED = "extracted"
STATUS_METADATA_ONLY = "metadata_only"
STATUS_EXTRACTION_FAILED = "extraction_failed"

# Honest, human-readable gap text per PDF failure code (Phase 32A Slice 5B.1).
# Each states plainly what happened and what it does NOT mean — none of them
# claims a successful extraction, and none suggests circumventing protection.
_PDF_FAILURE_GAP: dict[str, str] = {
    FAILURE_PASSWORD_PROTECTED_PDF: (
        "PDF requires a user password and is not accessible; it was not opened "
        "and no text is extracted."
    ),
    FAILURE_ENCRYPTED_PDF: (
        "PDF is encrypted and could not be read without a password; no text is "
        "extracted."
    ),
    FAILURE_MALFORMED_PDF: (
        "PDF structure is malformed and could not be parsed; no text is extracted."
    ),
    FAILURE_EXTRACTION_TIMEOUT: (
        "PDF extraction exceeded its time budget; no text is extracted."
    ),
}

# Fallback wording for any failure code without bespoke text (e.g. one added to
# the closed vocabulary later). Honest and generic — it never claims a successful
# extraction and never guesses a cause.
_PDF_FAILURE_GAP_DEFAULT = (
    "PDF could not be read; no text is extracted."
)

# Decompression-bomb ceiling: stop accumulating once total extracted text passes
# this, no matter how many pages remain (defensive; the tight bound is the
# per-excerpt/page-count cap). Not a per-request budget — a hard safety ceiling.
_MAX_TOTAL_EXTRACTED_CHARS = 4_000_000
# Table bounds (object-abuse guard): a single page/table cannot explode memory.
_MAX_TABLES_PER_PAGE = 10
_MAX_TABLE_ROWS = 200
_MAX_TABLE_COLS = 40
_MAX_CELL_CHARS = 400

# HTML elements whose entire contents are dropped as boilerplate/non-content.
_HTML_SKIP_TAGS = frozenset(
    {"script", "style", "noscript", "nav", "footer", "header", "aside", "form"}
)
# Void elements never carry a body — they must not be pushed on the tag stack.
_HTML_VOID_TAGS = frozenset(
    {
        "br", "img", "hr", "meta", "link", "input", "source", "area", "base",
        "col", "embed", "param", "track", "wbr", "keygen",
    }
)
# class / id substrings (case-insensitive) that mark a boilerplate container.
_HTML_BOILERPLATE_MARKERS = (
    "cookie",
    "consent",
    "banner",
    "cookiebar",
    "gdpr",
    "newsletter",
    "subscribe",
    "advert",
    "sidebar",
)
_HTML_HEADING_TAGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
_HTML_BLOCK_TAGS = frozenset({"p", "li", "caption", "blockquote", "dd", "dt"})


# --------------------------------------------------------------------------- #
# Result models
# --------------------------------------------------------------------------- #


class ExtractedTable(BaseModel):
    """One bounded structured table recovered from a document."""

    table_location: str  # e.g. "p12:t2" (PDF) or "t2" (HTML)
    table_index: int
    page_number: int | None = None
    rows: list[list[str]] = Field(default_factory=list)
    row_count: int = 0
    col_count: int = 0
    extraction_method: str = METHOD_NATIVE_PDF
    confidence: float = 0.7
    # Best-effort entity/segment scope this table was found under (same
    # vocabulary as ``PrimaryFact.scope`` / ``_infer_scope``): ``"group"``,
    # a segment/business-unit heading label, or ``None`` when no scope signal
    # was found. Never guessed — see ``_infer_scope`` for how it is derived.
    scope: str | None = None


class PrimaryDocumentExcerpt(BaseModel):
    """One bounded, citeable text excerpt with full extraction provenance."""

    excerpt_id: str
    text: str
    page_number: int | None = None
    section: str | None = None  # nearest heading / section context
    heading: str | None = None
    table_location: str | None = None
    extraction_method: str = METHOD_NATIVE_PDF
    confidence: float = 0.5  # 0..1 (aligns with primary_document_min_extraction_confidence)
    char_count: int = 0
    evidence_type: str = EVIDENCE_TYPE_GENERAL


class PrimaryDocumentExtraction(BaseModel):
    """The bounded result of extracting ONE primary document."""

    content_hash: str  # sha256 hex of the RAW bytes (dedup identity)
    mime_type: str
    extraction_method: str
    status: str  # extracted | metadata_only | extraction_failed
    page_count: int | None = None
    language: str = "en"
    requires_translation: bool = False
    extracted_char_count: int = 0
    truncated: bool = False
    excerpts: list[PrimaryDocumentExcerpt] = Field(default_factory=list)
    tables: list[ExtractedTable] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_gaps: list[str] = Field(default_factory=list)
    # ONLY ``type(exc).__name__`` ever lands here — never bytes/text.
    error_type: str | None = None
    # Phase 32A Slice 5B.1 — a member of the closed ``ingestion_status``
    # vocabulary saying WHY this document did not extract. Unlike ``error_type``
    # this is safe to persist and to render in the admin UI, and it is what lets
    # an operator tell "scanned, OCR would help" from "encrypted, it would not".
    failure_code: str | None = None
    # True when the PDF carries an encryption dictionary. Note this can be True
    # on a SUCCESSFUL extraction: an owner-password-only document restricts
    # printing/copying yet opens with no user password.
    encrypted: bool = False

    @property
    def has_content(self) -> bool:
        return bool(self.excerpts or self.tables)

    def to_text_extraction(
        self,
        *,
        source_url: str | None = None,
        title: str | None = None,
        original_language: str | None = None,
        include_tables: bool = True,
    ) -> DocumentTextExtraction:
        """Bridge to the Phase 29B.2 ``DocumentTextExtraction`` shape.

        Lets a LATER wiring task feed these excerpts (and, optionally, flattened
        tables) straight into ``parse_primary_facts`` WITHOUT modifying that
        parser. The numeric ``confidence`` is mapped to the parser's
        low/medium/high bucket; page numbers and excerpt ids are preserved.
        """
        doc_type = "pdf" if self.extraction_method == METHOD_NATIVE_PDF else "html"
        out = DocumentTextExtraction(
            source_url=source_url,
            document_type=doc_type,
            title=title,
            original_language=original_language,
            language=self.language,
            requires_translation=self.requires_translation,
            extracted_char_count=self.extracted_char_count,
            page_count_if_known=self.page_count,
            warnings=list(self.warnings),
            source_gaps=list(self.source_gaps),
        )
        for ex in self.excerpts:
            out.excerpts.append(
                DocumentExcerpt(
                    excerpt_id=ex.excerpt_id,
                    heading=ex.heading,
                    text=ex.text,
                    page_number=ex.page_number,
                    char_count=ex.char_count,
                    confidence=_confidence_bucket(ex.confidence),
                    evidence_type=ex.evidence_type,
                )
            )
        if include_tables:
            for tbl in self.tables:
                flat = "\n".join(" | ".join(row) for row in tbl.rows).strip()
                if not flat:
                    continue
                out.excerpts.append(
                    DocumentExcerpt(
                        excerpt_id=f"T{tbl.table_index}",
                        heading=None,
                        text=flat,
                        page_number=tbl.page_number,
                        char_count=len(flat),
                        confidence=_confidence_bucket(tbl.confidence),
                        evidence_type=_classify(flat),
                    )
                )
        return out


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #


def content_hash_of(raw: bytes) -> str:
    """SHA-256 hex digest of the RAW bytes — the document dedup identity."""
    return hashlib.sha256(raw).hexdigest()


# Heading substrings (case-insensitive) that mark a SEGMENT/business-unit-level
# breakdown — generic financial-reporting vocabulary, never a company's actual
# segment/brand names. Ordered as a tuple for deterministic matching.
_SEGMENT_HEADING_MARKERS = (
    "segment",
    "by business",
    "by division",
    "by region",
    "reportable segment",
    "operating segment",
    "business area",
    "business group",
    "business unit",
)
# Heading substrings that mark an entity-level (whole-company) figure.
_GROUP_HEADING_MARKERS = ("consolidated", "group")
_SCOPE_LABEL_MAX = 80


def _infer_scope(heading: str | None, ancestor: str | None = None) -> str | None:
    """Best-effort entity/segment scope label inferred from a document heading.

    Generic, structure-only heuristic — it never references a specific company's
    segment/brand names. Returns:
      * ``None`` when ``heading`` is falsy or gives no scope signal (unknown —
        never guessed).
      * the (trimmed, bounded) heading text itself when it looks like a
        segment/business-unit breakdown (e.g. "Segment information",
        "Revenue by region") — this IS the segment-scope label.
      * the literal ``"group"`` when the heading looks like a consolidated /
        whole-company figure.
      * the (trimmed, bounded) heading text when ``heading`` itself gives no
        signal but ``ancestor`` (the immediately-preceding, higher-level
        heading) does look like a segment/business-unit breakdown — this is
        the common real-report shape of a named subsection (e.g. a specific
        business-unit heading) nested under a generic "Segment information" /
        "operating segments" section title. The specific (leaf) heading text
        is still the more useful label than the generic ancestor text, so it
        is what gets returned — only the SIGNAL (that this is segment-scoped
        at all) is inherited from the ancestor. Never inherits company-name
        vocabulary — purely structural nesting.
    """
    if not heading:
        return None
    low = heading.lower()
    if any(marker in low for marker in _SEGMENT_HEADING_MARKERS):
        label = heading.strip()
        if len(label) > _SCOPE_LABEL_MAX:
            label = label[: _SCOPE_LABEL_MAX - 1].rstrip() + "…"
        return label
    if any(marker in low for marker in _GROUP_HEADING_MARKERS):
        return "group"
    if ancestor and any(marker in ancestor.lower() for marker in _SEGMENT_HEADING_MARKERS):
        label = heading.strip()
        if len(label) > _SCOPE_LABEL_MAX:
            label = label[: _SCOPE_LABEL_MAX - 1].rstrip() + "…"
        return label
    return None


# --------------------------------------------------------------------------- #
# Statement-type classification (Phase 32A Problem C)
#
# A generic, structure-only heading classifier — mirrors (but does not import,
# to avoid a circular import: ``ocr_provider`` already imports FROM this module)
# the financial-statement heading keywords ``ocr_provider._HEADING_KEYWORDS``
# uses to pick OCR page targets. This is a SECOND, independent consumer of the
# same vocabulary: it lets evidence-selection recognise that a prose excerpt or
# a demoted table row came from a balance sheet / cash-flow statement / segment
# note, so genuine statement content is not crowded out by generic narrative
# within the same evidence-budget category (see ``evidence_budget.py``'s
# ``CATEGORY_STATEMENT_TABLE``). Never a company-specific vocabulary; never used
# to fabricate a fact — purely a ranking/priority signal.
# --------------------------------------------------------------------------- #

STATEMENT_TYPE_INCOME = "income_statement"
STATEMENT_TYPE_BALANCE_SHEET = "balance_sheet"
STATEMENT_TYPE_CASH_FLOW = "cash_flow_statement"
STATEMENT_TYPE_SEGMENT = "segment_reporting"

# Checked in this order so a heading matching more than one table is classified
# by its most specific/likely signal first (segment before the generic group
# balance-sheet/cash-flow/income vocabulary below).
_STATEMENT_HEADING_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        STATEMENT_TYPE_SEGMENT,
        (
            "segment information",
            "segment reporting",
            "operating segments",
            "reportable segments",
            "segment note",
        ),
    ),
    (
        STATEMENT_TYPE_BALANCE_SHEET,
        ("balance sheet", "statement of financial position"),
    ),
    (
        STATEMENT_TYPE_CASH_FLOW,
        ("cash flow statement", "statement of cash flows", "cash flow"),
    ),
    (
        STATEMENT_TYPE_INCOME,
        (
            "income statement",
            "statement of income",
            "profit and loss",
            "statement of comprehensive income",
            "statement of profit or loss",
        ),
    ),
)


def classify_statement_type(heading: str | None) -> str | None:
    """Classify a document heading/section as a known financial-statement type.

    Returns one of ``income_statement`` / ``balance_sheet`` /
    ``cash_flow_statement`` / ``segment_reporting``, or ``None`` when the
    heading gives no such signal — never guessed. Pure, deterministic, never
    raises; generic vocabulary only (no company-specific terms).
    """
    if not heading:
        return None
    try:
        low = heading.lower()
    except AttributeError:
        return None
    for statement_type, keywords in _STATEMENT_HEADING_KEYWORDS:
        if any(kw in low for kw in keywords):
            return statement_type
    return None


# --------------------------------------------------------------------------- #
# Bounded, targeted long-PDF page selection (Phase 32A corrective, Problem C)
# --------------------------------------------------------------------------- #


def _select_statement_pages(
    raw: bytes,
    *,
    total_pages: int,
    exclude: set[int],
    max_pages: int,
) -> list[int]:
    """Bounded, deterministic supplemental-page targeting beyond the leading
    -page extraction window.

    Reads ONLY the PDF's outline/bookmark metadata (never a second full-text
    pass) to find pages whose bookmark title matches a known financial-
    statement heading — the SAME generic vocabulary ``classify_statement_type``
    uses, never a company-specific term and never a hardcoded page number. This
    is deliberately a SECOND, independent implementation of the outline-walk
    ``ocr_provider.select_ocr_pages`` already uses (duplicated, not imported,
    to avoid a circular import: ``ocr_provider`` already imports FROM this
    module — the same reasoning already documented on
    ``_STATEMENT_HEADING_KEYWORDS`` above).

    Never raises and never reads sequentially past the leading window: any
    failure (no pypdf, no outline, a malformed outline) simply yields no
    candidates, which the caller treats as honest "nothing found beyond the
    window" — never a fabricated or guessed page number.
    """
    if max_pages <= 0 or total_pages <= 0:
        return []
    try:
        import io

        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(raw))
        outline = reader.outline
    except Exception:  # noqa: BLE001
        return []

    keywords = tuple(kw for _stype, kws in _STATEMENT_HEADING_KEYWORDS for kw in kws)

    def _walk(items: object) -> Iterator[tuple[int, str | None]]:
        for item in items or []:  # type: ignore[attr-defined]
            if isinstance(item, list):
                yield from _walk(item)
                continue
            try:
                page_index = reader.get_destination_page_number(item)
            except Exception:  # noqa: BLE001
                continue
            if page_index is None:
                continue
            yield page_index + 1, getattr(item, "title", None)

    candidates: list[int] = []
    try:
        for page_no, title in _walk(outline):
            if page_no < 1 or page_no > total_pages or page_no in exclude:
                continue
            if page_no in candidates:
                continue
            if any(kw in (title or "").lower() for kw in keywords):
                candidates.append(page_no)
            if len(candidates) >= max_pages:
                break
    except Exception:  # noqa: BLE001 - outline access can raise on odd PDFs
        return []
    return candidates[:max_pages]


def _confidence_from_relevance(relevance: int) -> float:
    """Map the keyword relevance score to a bounded [0..1] confidence."""
    if relevance >= 4:
        return 0.9
    if relevance >= 1:
        return 0.6
    return 0.3


def _confidence_bucket(confidence: float) -> str:
    """Map a numeric [0..1] confidence to the parser's low/medium/high bucket."""
    if confidence >= 0.75:
        return "high"
    if confidence >= 0.4:
        return "medium"
    return "low"


def _bound_cell(value: object) -> str:
    """Coerce one table cell to a bounded string (None → empty)."""
    s = "" if value is None else str(value)
    s = s.replace("\r", " ").replace("\n", " ").strip()
    if len(s) > _MAX_CELL_CHARS:
        s = s[: _MAX_CELL_CHARS - 1].rstrip() + "…"
    return s


def _bound_table(raw_rows: Sequence[Sequence[object]]) -> list[list[str]]:
    """Bound a raw pdfplumber/HTML table to a rectangular, capped grid."""
    rows: list[list[str]] = []
    for raw_row in raw_rows[:_MAX_TABLE_ROWS]:
        cells = [_bound_cell(c) for c in list(raw_row or [])[:_MAX_TABLE_COLS]]
        if any(cells):  # drop fully-empty rows
            rows.append(cells)
    return rows


def _rank_and_build_excerpts(
    blocks: list[tuple[int | None, str | None, str]],
    *,
    method: str,
    max_excerpts: int,
    per_excerpt: int,
) -> list[PrimaryDocumentExcerpt]:
    """Relevance-rank ``(page, section, text)`` blocks into bounded excerpts.

    Mirrors ``document_text_extractor.extract_document_text``: always keep the
    leading (overview) block, then the most financially-relevant blocks; stable
    within equal scores; de-dup on a text-prefix key.
    """
    indexed = list(enumerate(blocks))
    lead = indexed[:1]
    rest = sorted(indexed[1:], key=lambda t: (-_relevance(t[1][2]), t[0]))
    chosen = lead + rest

    excerpts: list[PrimaryDocumentExcerpt] = []
    seen: set[str] = set()
    for orig_idx, (page, section, blk) in chosen:
        if len(excerpts) >= max_excerpts:
            break
        text = blk.strip()
        if not text:
            continue
        if len(text) > per_excerpt:
            text = text[: per_excerpt - 1].rstrip() + "…"
        key = text[:160].lower()
        if key in seen:
            continue
        seen.add(key)
        rel = _relevance(blk)
        etype = _classify(blk)
        if orig_idx == 0 and etype == EVIDENCE_TYPE_GENERAL:
            etype = EVIDENCE_TYPE_BUSINESS
        heading = section or (text.split(".")[0][:90] if text else None)
        excerpts.append(
            PrimaryDocumentExcerpt(
                excerpt_id=f"X{len(excerpts) + 1}",
                text=text,
                page_number=page,
                section=section,
                heading=heading,
                extraction_method=method,
                confidence=_confidence_from_relevance(rel),
                char_count=len(text),
                evidence_type=etype,
            )
        )
    return excerpts


def _finalize_language(
    result: PrimaryDocumentExtraction,
    blocks: list[tuple[int | None, str | None, str]],
    original_language: str | None,
) -> None:
    """Detect language + inferred year over the leading content (labels only).

    Phase 32A Problem F: delegates to ``detect_language_with_confidence``, whose
    CONTENT-based detection runs first — ``original_language`` (typically a
    domicile guess passed in by the caller) is only a WEAK fallback used when
    content detection is genuinely inconclusive. A confidently-English document
    is never re-labelled by a non-English domicile guess, so
    ``requires_translation`` never invents a translation requirement.
    """
    joined_head = " ".join(b[2] for b in blocks[:12])
    lang = detect_language(joined_head, hint=original_language)
    result.language = lang
    result.requires_translation = lang != "en"


# --------------------------------------------------------------------------- #
# PDF accessibility classification (Phase 32A Slice 5B.1)
#
# Slice 5A collapsed four genuinely different outcomes into one opaque
# ``extraction_failed``: Richemont's documents were reported the same way a
# corrupt byte-stream would be, so staging could not tell "needs OCR" from
# "needs nothing — it is broken". These helpers separate them.
#
# NOTE ON PASSWORDS: ``pdfplumber.open`` supplies the EMPTY password, which is
# the standard "this document has no user password" case (an owner-password-only
# PDF restricts printing/copying but is openly readable). That is the only
# password ever tried. No password is guessed, derived, brute-forced or stripped,
# and a document that genuinely requires a user password is recorded as
# inaccessible — never opened.
# --------------------------------------------------------------------------- #


def pdf_has_encryption_marker(raw: bytes) -> bool:
    """True when the PDF carries an ``/Encrypt`` dictionary.

    A cheap, dependency-free signal used only for CLASSIFICATION — it decides how
    an outcome is *labelled*, never whether decryption is attempted. Scanning the
    trailer region rather than the whole file keeps this bounded and avoids
    matching the literal inside a content stream.
    """
    if not raw:
        return False
    tail = raw[-4096:] if len(raw) > 4096 else raw
    if b"/Encrypt" in tail:
        return True
    # Linearized PDFs put the trailer dictionary near the front instead.
    head = raw[:4096]
    return b"/Encrypt" in head


def classify_pdf_failure(raw: bytes, exc: BaseException | None) -> str:
    """Return the sanitized failure code for a PDF that could not be parsed.

    Distinguishes password-protected (a user password is required — inaccessible,
    and we do not try to get past it) from encrypted-but-broken, from plain
    malformed. Uses the exception TYPE and the encryption marker only; the
    exception message is never inspected or retained.
    """
    name = type(exc).__name__.lower() if exc is not None else ""
    if "password" in name:
        return FAILURE_PASSWORD_PROTECTED_PDF
    if "timeout" in name:
        return FAILURE_EXTRACTION_TIMEOUT
    if pdf_has_encryption_marker(raw):
        return FAILURE_ENCRYPTED_PDF
    return FAILURE_MALFORMED_PDF


# --------------------------------------------------------------------------- #
# PDF extraction (pdfplumber, native text + tables)
# --------------------------------------------------------------------------- #


def extract_pdf(
    raw: bytes,
    *,
    cfg: Settings | None = None,
    original_language: str | None = None,
) -> PrimaryDocumentExtraction:
    """Extract bounded text excerpts + tables from a native-text PDF.

    Never raises. A wrong magic byte / oversize / malformed / encrypted / scanned
    document degrades to an honest status; extraction is bounded by page count,
    per-excerpt length, total characters and a wall-clock budget.
    """
    cfg = cfg or default_settings
    result = PrimaryDocumentExtraction(
        content_hash=content_hash_of(raw),
        mime_type="application/pdf",
        extraction_method=METHOD_NATIVE_PDF,
        status=STATUS_EXTRACTION_FAILED,
    )

    # 1) Magic bytes — a non-PDF blob is never fed to the parser.
    if not looks_like_pdf(raw):
        result.failure_code = FAILURE_NOT_A_PDF
        result.source_gaps.append(
            "Document is not a PDF (missing %PDF- signature); not extracted."
        )
        return result

    # Recorded whether or not parsing succeeds: an owner-password-only PDF is
    # readable but still encrypted, and an operator needs to see that.
    result.encrypted = pdf_has_encryption_marker(raw)

    # 2) Byte cap — record honest truncation (the hard memory bound lives in the
    #    fetch layer; this is a defensive, honest flag on the pure path).
    max_bytes = max(1, cfg.primary_document_max_download_bytes)
    if len(raw) > max_bytes:
        result.truncated = True
        result.warnings.append(
            "Document exceeds the maximum download size; extraction is bounded."
        )

    try:
        import io

        import pdfplumber
    except Exception as exc:  # noqa: BLE001
        result.error_type = type(exc).__name__
        result.failure_code = FAILURE_CLIENT_UNAVAILABLE
        result.source_gaps.append(
            f"PDF library unavailable ({type(exc).__name__}); not extracted."
        )
        return result

    max_pages = max(1, cfg.primary_document_max_pdf_pages)
    max_supplemental_pages = max(0, cfg.primary_document_max_supplemental_pdf_pages)
    max_excerpts = max(1, cfg.primary_document_max_excerpts_per_document)
    per_excerpt = max(120, cfg.primary_document_max_excerpt_chars)
    deadline = time.monotonic() + max(1, cfg.primary_document_extraction_timeout_seconds)

    page_blocks: list[tuple[int | None, str | None, str]] = []
    total_chars = 0

    def _extract_one_page(page: object, page_no: int) -> None:
        """Extract text blocks + tables from ONE page; mutates the closures."""
        nonlocal total_chars
        # -- text --
        try:
            text = page.extract_text() or ""  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            text = ""
            result.warnings.append(f"Page {page_no} text extraction failed; skipped.")
        page_start = len(page_blocks)
        for blk in _blocks(text):
            if total_chars >= _MAX_TOTAL_EXTRACTED_CHARS:
                result.truncated = True
                break
            page_blocks.append((page_no, None, blk))
            total_chars += len(blk)
        # Best-effort per-page scope signal for this page's tables: raw PDF
        # text carries no heading/paragraph distinction, so this scans the
        # page's own extracted blocks for a segment/group marker (same
        # vocabulary as ``_infer_scope``); stays None — never guessed — when
        # no signal is found (Phase 32A corrective, Problem B).
        page_scope: str | None = None
        for _pg, _sec, blk_text in page_blocks[page_start:]:
            page_scope = _infer_scope(blk_text[:120])
            if page_scope is not None:
                break
        # -- tables --
        try:
            raw_tables = page.extract_tables() or []  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            raw_tables = []
        for t_idx, raw_rows in enumerate(raw_tables[:_MAX_TABLES_PER_PAGE]):
            rows = _bound_table(raw_rows)
            if not rows:
                continue
            result.tables.append(
                ExtractedTable(
                    table_location=f"p{page_no}:t{t_idx}",
                    table_index=t_idx,
                    page_number=page_no,
                    rows=rows,
                    row_count=len(rows),
                    col_count=max(len(r) for r in rows),
                    extraction_method=METHOD_NATIVE_PDF,
                    scope=page_scope,
                )
            )

    try:
        # pdfplumber.open defaults to an empty password → this is the single
        # empty-password attempt; a non-empty-password PDF raises → handled below.
        with pdfplumber.open(io.BytesIO(raw)) as pdf:
            page_count = len(pdf.pages)
            result.page_count = page_count
            n = min(max_pages, page_count)
            if page_count > n:
                result.truncated = True
                result.warnings.append(
                    f"Document has {page_count} pages; only the first {n} "
                    "were extracted."
                )
            for i in range(n):
                if time.monotonic() > deadline:
                    result.truncated = True
                    result.warnings.append(
                        "Extraction time budget exceeded; partial extraction only."
                    )
                    break
                _extract_one_page(pdf.pages[i], i + 1)

            # Phase 32A corrective (Problem C): a SMALL, bounded, TARGETED
            # look-beyond pass — never a larger sequential prefix. Only pages
            # whose PDF bookmark/outline title matches a known financial-
            # statement heading are read, and only when pages remain beyond
            # the leading window and extraction budget remains.
            if (
                page_count > n
                and max_supplemental_pages > 0
                and time.monotonic() <= deadline
                and total_chars < _MAX_TOTAL_EXTRACTED_CHARS
            ):
                supplemental = _select_statement_pages(
                    raw,
                    total_pages=page_count,
                    exclude=set(range(1, n + 1)),
                    max_pages=max_supplemental_pages,
                )
                if supplemental:
                    result.warnings.append(
                        f"Targeted {len(supplemental)} additional page(s) beyond "
                        f"the first {n} whose bookmark/outline title matched a "
                        "financial-statement heading."
                    )
                for page_no in supplemental:
                    if time.monotonic() > deadline:
                        result.truncated = True
                        result.warnings.append(
                            "Extraction time budget exceeded during the "
                            "supplemental page pass; partial extraction only."
                        )
                        break
                    if total_chars >= _MAX_TOTAL_EXTRACTED_CHARS:
                        break
                    _extract_one_page(pdf.pages[page_no - 1], page_no)
    except Exception as exc:  # noqa: BLE001 - malformed/encrypted must not raise
        result.error_type = type(exc).__name__
        result.failure_code = classify_pdf_failure(raw, exc)
        # ``.get`` (not ``[...]``): a failure code with no bespoke wording must
        # still degrade honestly. Indexing here would turn a never-raises function
        # into a raiser the moment a new code is added to the vocabulary.
        result.source_gaps.append(
            _PDF_FAILURE_GAP.get(result.failure_code, _PDF_FAILURE_GAP_DEFAULT)
        )
        return result

    result.excerpts = _rank_and_build_excerpts(
        page_blocks,
        method=METHOD_NATIVE_PDF,
        max_excerpts=max_excerpts,
        per_excerpt=per_excerpt,
    )
    result.extracted_char_count = total_chars
    _finalize_language(result, page_blocks, original_language)

    if not result.has_content:
        # Valid PDF but no usable text/tables → scanned / image-only. This is the
        # one failure mode OCR can actually rescue (Slice 5B.2), so it is labelled
        # distinctly from encrypted / malformed, which OCR cannot help with.
        result.status = STATUS_METADATA_ONLY
        result.failure_code = FAILURE_SCANNED_NO_TEXT
        result.source_gaps.append(
            "PDF appears scanned or text extraction returned no usable text."
        )
    else:
        result.status = STATUS_EXTRACTED
        if result.encrypted:
            result.warnings.append(
                "PDF carries an encryption dictionary but is readable without a "
                "user password; extracted normally."
            )
    return result


# --------------------------------------------------------------------------- #
# HTML extraction (stdlib HTMLParser, boilerplate-stripped, tables preserved)
# --------------------------------------------------------------------------- #


class _DocumentHtmlParser(HTMLParser):
    """DOM-aware body-text + table extractor (mirrors ``_PageParser``).

    Drops whole boilerplate subtrees (script/style/nav/footer/header/aside/form
    and cookie/consent/banner/newsletter/advert/sidebar containers), keeps
    headings (as section context), paragraphs / list items, and tables.
    """

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title: str | None = None
        # (page=None, section, text)
        self.blocks: list[tuple[int | None, str | None, str]] = []
        self.tables: list[list[list[str]]] = []
        # Parallel to ``self.tables`` (same index) — the inferred scope for
        # each table, derived from the heading in effect when the table opened
        # (Phase 32A corrective, Problem B/C: tables previously carried no
        # section/scope context at all).
        self.table_scopes: list[str | None] = []
        # element stack of (tag, is_skip_region)
        self._stack: list[tuple[str, bool]] = []
        self._current_section: str | None = None
        # The heading immediately preceding ``_current_section`` — lets a
        # table nested under a specific subsection (e.g. a named business
        # unit) inherit a segment signal from a higher-level ancestor heading
        # (e.g. "Segment information") that the leaf heading text alone would
        # not carry. See ``_infer_scope``.
        self._prev_section: str | None = None
        self._in_title = False
        self._title_parts: list[str] = []
        self._cur_block_tag: str | None = None
        self._cur_block_is_heading = False
        self._cur_text: list[str] = []
        # table capture stacks
        self._table_stack: list[list[list[str]]] = []
        self._table_scope_stack: list[str | None] = []
        self._row_stack: list[list[str]] = []
        self._cur_cell: list[str] | None = None
        self._cur_cell_colspan = 1

    # -- skip-region bookkeeping ------------------------------------------- #
    @staticmethod
    def _is_boilerplate(attrs: dict[str, str]) -> bool:
        hay = f"{attrs.get('class', '')} {attrs.get('id', '')}".lower()
        return any(m in hay for m in _HTML_BOILERPLATE_MARKERS)

    def _skipping(self) -> bool:
        return any(is_skip for _tag, is_skip in self._stack)

    # -- start / end tags -------------------------------------------------- #
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        tag = tag.lower()
        if tag in _HTML_VOID_TAGS:
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        is_skip = tag in _HTML_SKIP_TAGS or self._is_boilerplate(a)
        self._stack.append((tag, is_skip))
        if self._skipping():
            return
        if tag == "title":
            self._in_title = True
        elif tag in _HTML_HEADING_TAGS or tag in _HTML_BLOCK_TAGS:
            self._flush_block()
            self._cur_block_tag = tag
            self._cur_block_is_heading = tag in _HTML_HEADING_TAGS
            self._cur_text = []
        elif tag == "table":
            self._table_stack.append([])
            self._table_scope_stack.append(
                _infer_scope(self._current_section, self._prev_section)
            )
        elif tag == "tr" and self._table_stack:
            self._row_stack.append([])
        elif tag in ("td", "th") and self._row_stack:
            self._cur_cell = []
            colspan_raw = a.get("colspan", "1") or "1"
            try:
                self._cur_cell_colspan = max(1, min(int(colspan_raw), _MAX_TABLE_COLS))
            except ValueError:
                self._cur_cell_colspan = 1

    def handle_startendtag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        # Self-closing element carries no body — ignore for our purposes.
        return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag in _HTML_VOID_TAGS:
            return
        skipping = self._skipping()
        if not skipping:
            if tag == "title":
                self._in_title = False
                if not self.title:
                    self.title = " ".join(self._title_parts).strip() or None
            elif tag in ("td", "th") and self._cur_cell is not None:
                if self._row_stack:
                    # Collapse ALL internal whitespace (including a literal
                    # source-formatting newline inside one data() chunk, not
                    # just between chunks) — real issuer HTML routinely wraps
                    # cell text across source lines, and HTML itself treats
                    # any whitespace run as a single space when rendered.
                    text = " ".join(" ".join(self._cur_cell).split())
                    # A merged header/value cell (colspan > 1) is repeated
                    # across the columns it visually spans — without this a
                    # multi-period header row (e.g. one cell spanning two
                    # value columns) undercounts columns relative to the data
                    # rows below it, misaligning row-header -> value/period
                    # mapping downstream (Phase 32A corrective, Problem B).
                    for _ in range(self._cur_cell_colspan):
                        self._row_stack[-1].append(text)
                self._cur_cell = None
                self._cur_cell_colspan = 1
            elif tag == "tr" and self._row_stack:
                row = self._row_stack.pop()
                if self._table_stack:
                    self._table_stack[-1].append(row)
            elif tag == "table" and self._table_stack:
                rows = self._table_stack.pop()
                scope = (
                    self._table_scope_stack.pop() if self._table_scope_stack else None
                )
                if rows:
                    self.tables.append(rows)
                    self.table_scopes.append(scope)
            elif tag == self._cur_block_tag:
                self._flush_block()
        self._pop_tag(tag)

    def _pop_tag(self, tag: str) -> None:
        # Pop back to the matching open tag (tolerant of unclosed elements).
        for idx in range(len(self._stack) - 1, -1, -1):
            if self._stack[idx][0] == tag:
                del self._stack[idx:]
                return

    def _flush_block(self) -> None:
        if self._cur_block_tag is None:
            return
        # Collapse ALL internal whitespace, not just between data() chunks —
        # real issuer HTML routinely source-wraps a paragraph's text across
        # multiple lines, and a literal newline inside the resulting excerpt
        # text breaks the ``[^\d\n]``-bounded label→value gap every money/
        # percent regex in ``primary_fact_parser`` relies on (Phase 32A
        # corrective, Problem A) — HTML itself treats any whitespace run,
        # including a newline, as a single space when rendered.
        text = " ".join(" ".join(self._cur_text).split())
        if self._cur_block_is_heading:
            if text:
                self._prev_section = self._current_section
                self._current_section = text[:120]
                self.blocks.append((None, text[:120], text))
        elif len(text) >= 40:
            self.blocks.append((None, self._current_section, text))
        self._cur_block_tag = None
        self._cur_block_is_heading = False
        self._cur_text = []

    def handle_data(self, data: str) -> None:
        if self._skipping():
            return
        if self._in_title:
            self._title_parts.append(data)
        elif self._cur_cell is not None:
            self._cur_cell.append(data)
        elif self._cur_block_tag is not None:
            self._cur_text.append(data)


def extract_html(
    raw: bytes,
    *,
    cfg: Settings | None = None,
    original_language: str | None = None,
) -> PrimaryDocumentExtraction:
    """Extract bounded, boilerplate-stripped body excerpts + tables from HTML.

    Never raises. Text is treated as inert, untrusted data: prompt-injection
    markers are preserved verbatim (never stripped) so a downstream prompt-
    boundary guard can neutralize them.
    """
    cfg = cfg or default_settings
    result = PrimaryDocumentExtraction(
        content_hash=content_hash_of(raw),
        mime_type="text/html",
        extraction_method=METHOD_HTML,
        status=STATUS_EXTRACTION_FAILED,
    )

    if not raw:
        result.source_gaps.append("HTML document was empty; not extracted.")
        return result

    max_bytes = max(1, cfg.primary_document_max_download_bytes)
    if len(raw) > max_bytes:
        result.truncated = True
        result.warnings.append(
            "Document exceeds the maximum download size; extraction is bounded."
        )

    max_excerpts = max(1, cfg.primary_document_max_excerpts_per_document)
    per_excerpt = max(120, cfg.primary_document_max_excerpt_chars)

    try:
        html = raw.decode("utf-8", "replace")
    except Exception as exc:  # noqa: BLE001
        result.error_type = type(exc).__name__
        result.source_gaps.append("HTML could not be decoded; not extracted.")
        return result

    parser = _DocumentHtmlParser()
    try:
        parser.feed(html)
        parser.close()
    except Exception as exc:  # noqa: BLE001 - a malformed page must never raise
        result.error_type = type(exc).__name__

    # Bound + record tables.
    for t_idx, rows in enumerate(parser.tables):
        bounded = _bound_table([list(r) for r in rows])
        if not bounded:
            continue
        scope = (
            parser.table_scopes[t_idx] if t_idx < len(parser.table_scopes) else None
        )
        result.tables.append(
            ExtractedTable(
                table_location=f"t{t_idx}",
                table_index=t_idx,
                page_number=None,
                rows=bounded,
                row_count=len(bounded),
                col_count=max(len(r) for r in bounded),
                extraction_method=METHOD_HTML,
                scope=scope,
            )
        )

    # Bound total characters (decompression-bomb guard).
    page_blocks: list[tuple[int | None, str | None, str]] = []
    total_chars = 0
    for page, section, blk in parser.blocks:
        if total_chars >= _MAX_TOTAL_EXTRACTED_CHARS:
            result.truncated = True
            break
        page_blocks.append((page, section, blk))
        total_chars += len(blk)

    result.excerpts = _rank_and_build_excerpts(
        page_blocks,
        method=METHOD_HTML,
        max_excerpts=max_excerpts,
        per_excerpt=per_excerpt,
    )
    result.extracted_char_count = total_chars
    _finalize_language(result, page_blocks, original_language)

    if not result.has_content:
        result.status = STATUS_METADATA_ONLY
        result.failure_code = FAILURE_EMPTY_EXTRACTION
        result.source_gaps.append(
            "HTML document contained no extractable body text."
        )
    else:
        result.status = STATUS_EXTRACTED
    return result


# --------------------------------------------------------------------------- #
# Dispatcher
# --------------------------------------------------------------------------- #


def extract_primary_document(
    raw: bytes,
    *,
    document_type: str,
    cfg: Settings | None = None,
    original_language: str | None = None,
) -> PrimaryDocumentExtraction:
    """Dispatch to ``extract_pdf`` / ``extract_html`` by ``document_type``.

    ``document_type`` is ``pdf`` | ``html`` (from the document fetcher). An
    unknown type degrades to an honest ``extraction_failed`` result — never a
    fabricated document.
    """
    if document_type == "pdf":
        return extract_pdf(raw, cfg=cfg, original_language=original_language)
    if document_type in ("html", "text"):
        return extract_html(raw, cfg=cfg, original_language=original_language)
    return PrimaryDocumentExtraction(
        content_hash=content_hash_of(raw),
        mime_type="application/octet-stream",
        extraction_method=METHOD_NATIVE_PDF,
        status=STATUS_EXTRACTION_FAILED,
        failure_code=FAILURE_UNSUPPORTED_CONTENT_TYPE,
        source_gaps=[f"Unsupported document type '{document_type}'; not extracted."],
    )


__all__ = [
    "METHOD_NATIVE_PDF",
    "METHOD_HTML",
    "METHOD_OCR",
    "STATUS_EXTRACTED",
    "STATUS_METADATA_ONLY",
    "STATUS_EXTRACTION_FAILED",
    "STATEMENT_TYPE_INCOME",
    "STATEMENT_TYPE_BALANCE_SHEET",
    "STATEMENT_TYPE_CASH_FLOW",
    "STATEMENT_TYPE_SEGMENT",
    "ExtractedTable",
    "PrimaryDocumentExcerpt",
    "PrimaryDocumentExtraction",
    "classify_statement_type",
    "content_hash_of",
    "extract_pdf",
    "extract_html",
    "extract_primary_document",
]
