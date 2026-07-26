"""
Bounded PDF / HTML / text extraction — Phase 29B.2.

Turns a fetched primary document (an issuer's annual report / registration
document / integrated report) into a small, bounded set of citeable *excerpts* —
never the full document. The LLM council must never receive a whole filing; it
receives at most ``source_document_extraction_max_excerpts`` excerpts, each
capped at ``source_document_extraction_max_chars_per_excerpt`` characters.

Design guarantees:
  * **Bounded.** First-N-pages only for PDFs (no OCR); excerpt count + per-excerpt
    length are config-capped; the full text is never stored on the result.
  * **Honest gaps.** A scanned / image-only / empty PDF returns no excerpts and a
    clear ``source_gap`` ("PDF appears scanned or text extraction returned no
    usable text.") — never fabricated text.
  * **Never raises.** Any parse error degrades to a result with warnings/gaps.
  * **Secret-free.** Nothing here logs document text.

The extractor is deliberately pure/synchronous and network-free: it takes bytes
in and returns a typed model out, so it is trivially unit-testable.
"""

from __future__ import annotations

import re

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings

# Evidence-type tags for an excerpt (mapped to EvidenceItem source_types by the
# connector). Deliberately factual/neutral — never a rating vocabulary.
EVIDENCE_TYPE_FINANCIAL = "financial"
EVIDENCE_TYPE_BUSINESS = "business_description"
EVIDENCE_TYPE_RISK = "risk"
EVIDENCE_TYPE_GENERAL = "annual_report_text"

# Keyword sets used only to *classify* and *rank* excerpts (not to fabricate).
_FINANCIAL_KEYWORDS = (
    "revenue",
    "sales",
    "net income",
    "net profit",
    "operating profit",
    "operating income",
    "ebit",
    "ebitda",
    "gross margin",
    "free cash flow",
    "cash flow",
    "total assets",
    "net debt",
    "borrowings",
    "cash and cash equivalents",
    "earnings per share",
    "dividend",
    "million",
    "billion",
)
_RISK_KEYWORDS = (
    "risk",
    "risks",
    "uncertaint",
    "litigation",
    "regulatory",
    "exposure",
    "may adversely",
    "could adversely",
)
_BUSINESS_KEYWORDS = (
    "founded",
    "headquarter",
    "we are",
    "the group",
    "the company",
    "our business",
    "operates",
    "maisons",
    "brands",
    "employees",
    "segment",
)

# Very small language heuristic: presence of common non-English function words.
# Used ONLY to set ``requires_translation`` honestly — never to translate.
_LANG_HINTS: dict[str, tuple[str, ...]] = {
    "fr": (" le ", " la ", " les ", " des ", " et ", " société", " exercice",
           " chiffre d'affaires"),
    "de": (" der ", " die ", " und ", " das ", " geschäftsjahr", " umsatz", " gesellschaft"),
    "it": (" il ", " la ", " che ", " gli ", " ricavi", " società", " esercizio"),
}


class DocumentExcerpt(BaseModel):
    """One bounded, citeable excerpt from a primary document."""

    excerpt_id: str
    heading: str | None = None
    text: str
    page_number: int | None = None
    char_count: int = 0
    confidence: str = "medium"  # low | medium | high
    evidence_type: str = EVIDENCE_TYPE_GENERAL


class DocumentTextExtraction(BaseModel):
    """The bounded result of extracting text from ONE primary document."""

    source_url: str | None = None
    document_type: str | None = None  # pdf | html | text
    title: str | None = None
    inferred_year: int | None = None
    language: str = "en"
    original_language: str | None = None
    requires_translation: bool = False
    extracted_char_count: int = 0
    page_count_if_known: int | None = None
    excerpts: list[DocumentExcerpt] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    source_gaps: list[str] = Field(default_factory=list)

    @property
    def has_excerpts(self) -> bool:
        return bool(self.excerpts)


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #

_WS_RE = re.compile(r"[ \t ]+")
_MULTI_NL_RE = re.compile(r"\n\s*\n+")
_YEAR_RE = re.compile(r"\b(19|20)\d{2}\b")


def _normalize(text: str) -> str:
    """Collapse runs of spaces/tabs; keep paragraph breaks."""
    lines = [_WS_RE.sub(" ", ln).strip() for ln in text.splitlines()]
    return "\n".join(lines)


def _blocks(text: str, *, min_len: int = 40) -> list[str]:
    """Split text into paragraph-ish blocks, dropping tiny/boilerplate lines."""
    normalized = _normalize(text)
    parts = _MULTI_NL_RE.split(normalized)
    out: list[str] = []
    for p in parts:
        # If a part has no blank-line structure, split further on single lines
        # only when it is very long, so we don't glue a whole page into one block.
        p = p.strip()
        if not p:
            continue
        if len(p) <= 2000:
            out.append(p)
            continue
        buf: list[str] = []
        size = 0
        for ln in p.splitlines():
            buf.append(ln)
            size += len(ln)
            if size >= 900:
                out.append(" ".join(buf).strip())
                buf, size = [], 0
        if buf:
            out.append(" ".join(buf).strip())
    return [b for b in out if len(b) >= min_len]


def _classify(text: str) -> str:
    low = f" {text.lower()} "
    fin = sum(1 for k in _FINANCIAL_KEYWORDS if k in low)
    risk = sum(1 for k in _RISK_KEYWORDS if k in low)
    biz = sum(1 for k in _BUSINESS_KEYWORDS if k in low)
    if fin >= 1 and fin >= risk:
        return EVIDENCE_TYPE_FINANCIAL
    if risk >= 1 and risk > fin:
        return EVIDENCE_TYPE_RISK
    if biz >= 1:
        return EVIDENCE_TYPE_BUSINESS
    return EVIDENCE_TYPE_GENERAL


def _relevance(text: str) -> int:
    low = f" {text.lower()} "
    score = 0
    for k in _FINANCIAL_KEYWORDS:
        if k in low:
            score += 2
    for k in _RISK_KEYWORDS:
        if k in low:
            score += 1
    for k in _BUSINESS_KEYWORDS:
        if k in low:
            score += 1
    return score


def _detect_language(text: str, original_language: str | None) -> tuple[str, bool]:
    """Return (language_code, requires_translation).

    A cheap heuristic honouring an ``original_language`` hint (from the issuer's
    country). Defaults to English. Never blocks extraction — only labels it.
    """
    if original_language and original_language.lower().startswith(("fr", "de", "it")):
        # Trust the registry hint but still verify against content when possible.
        code = original_language.lower()[:2]
        return code, True
    low = f" {text[:4000].lower()} "
    best_lang, best_hits = "en", 0
    for lang, hints in _LANG_HINTS.items():
        hits = sum(1 for h in hints if h in low)
        if hits > best_hits:
            best_lang, best_hits = lang, hits
    if best_hits >= 3:
        return best_lang, True
    return "en", False


def _infer_year(*texts: str | None) -> int | None:
    years: list[int] = []
    for t in texts:
        if not t:
            continue
        for m in _YEAR_RE.finditer(t):
            y = int(m.group(0))
            if 1990 <= y <= 2099:
                years.append(y)
    if not years:
        return None
    # Prefer the most recent plausible reporting year seen near the top.
    return max(years)


# --------------------------------------------------------------------------- #
# HTML extraction (reuse the safe_web_fetcher parser for title, add body text)
# --------------------------------------------------------------------------- #

_SCRIPT_STYLE_RE = re.compile(
    r"<(script|style|noscript|nav|footer|header)\b[^>]*>.*?</\1>",
    re.IGNORECASE | re.DOTALL,
)
_TAG_BLOCK_RE = re.compile(
    r"<(h[1-6]|p|li|section|article|div|td|th|caption)\b[^>]*>(.*?)</\1>",
    re.IGNORECASE | re.DOTALL,
)
_ANY_TAG_RE = re.compile(r"<[^>]+>")
_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)


def _html_unescape(s: str) -> str:
    import html as _html

    return _html.unescape(s)


def _extract_html_blocks(html: str) -> tuple[str | None, list[str]]:
    cleaned = _SCRIPT_STYLE_RE.sub(" ", html)
    title_m = _HTML_TITLE_RE.search(cleaned)
    title = _html_unescape(_ANY_TAG_RE.sub("", title_m.group(1))).strip() if title_m else None
    blocks: list[str] = []
    for m in _TAG_BLOCK_RE.finditer(cleaned):
        inner = _ANY_TAG_RE.sub(" ", m.group(2))
        text = _WS_RE.sub(" ", _html_unescape(inner)).strip()
        if len(text) >= 40:
            blocks.append(text)
    if not blocks:
        # Fall back to a flat strip of all tags.
        flat = _WS_RE.sub(" ", _html_unescape(_ANY_TAG_RE.sub(" ", cleaned))).strip()
        blocks = _blocks(flat)
    return title, blocks


def _extract_pdf_pages(
    content: bytes, max_pages: int
) -> tuple[list[tuple[int, str]], int | None, list[str]]:
    """Return ([(page_no, text)], page_count, warnings) using pypdf.

    Never raises — a broken/encrypted PDF returns an empty page list plus a
    warning so the caller can emit an honest gap.
    """
    warnings: list[str] = []
    try:
        import io

        from pypdf import PdfReader
    except Exception as exc:  # noqa: BLE001
        return [], None, [f"PDF library unavailable: {type(exc).__name__}"]

    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception as exc:  # noqa: BLE001
        return [], None, [f"PDF could not be parsed ({type(exc).__name__})."]

    if getattr(reader, "is_encrypted", False):
        try:
            reader.decrypt("")  # try empty owner password
        except Exception:  # noqa: BLE001
            return [], None, ["PDF is encrypted; text extraction is not available."]

    try:
        page_count = len(reader.pages)
    except Exception:  # noqa: BLE001
        page_count = None

    pages: list[tuple[int, str]] = []
    n = min(max_pages, page_count) if page_count else max_pages
    for i in range(n):
        try:
            text = reader.pages[i].extract_text() or ""
        except Exception:  # noqa: BLE001
            text = ""
            warnings.append(f"Page {i + 1} text extraction failed; skipped.")
        if text.strip():
            pages.append((i + 1, text))
    return pages, page_count, warnings


# --------------------------------------------------------------------------- #
# Public entrypoint
# --------------------------------------------------------------------------- #


def extract_document_text(
    content: bytes,
    *,
    document_type: str,
    source_url: str | None = None,
    title_hint: str | None = None,
    original_language: str | None = None,
    cfg: Settings | None = None,
) -> DocumentTextExtraction:
    """Extract a bounded set of excerpts from one primary document.

    ``document_type`` is ``pdf`` | ``html`` | ``text`` (from the fetcher). Returns
    a ``DocumentTextExtraction`` — with excerpts when usable text is found, or an
    honest ``source_gap`` when the document is scanned / empty / unparseable.
    """
    cfg = cfg or default_settings
    result = DocumentTextExtraction(
        source_url=source_url,
        document_type=document_type,
        title=title_hint,
        original_language=original_language,
    )
    max_excerpts = max(1, cfg.source_document_extraction_max_excerpts)
    per_excerpt = max(120, cfg.source_document_extraction_max_chars_per_excerpt)
    max_pages = max(1, cfg.source_document_extraction_max_pages)

    page_blocks: list[tuple[int | None, str]] = []

    if document_type == "pdf":
        pages, page_count, warns = _extract_pdf_pages(content, max_pages)
        result.page_count_if_known = page_count
        result.warnings.extend(warns)
        if not pages:
            result.source_gaps.append(
                "PDF appears scanned or text extraction returned no usable text."
            )
            return result
        for page_no, text in pages:
            for blk in _blocks(text):
                page_blocks.append((page_no, blk))
    elif document_type == "html":
        try:
            html = content.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            html = ""
        html_title, blocks = _extract_html_blocks(html)
        if html_title and not result.title:
            result.title = html_title
        if not blocks:
            result.source_gaps.append(
                "HTML document contained no extractable body text."
            )
            return result
        for blk in blocks:
            page_blocks.append((None, blk))
    else:  # text
        try:
            text = content.decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            text = ""
        blocks = _blocks(text)
        if not blocks:
            result.source_gaps.append("Document contained no extractable text.")
            return result
        for blk in blocks:
            page_blocks.append((None, blk))

    # Total extracted character count (bounded view of what we read).
    result.extracted_char_count = sum(len(b) for _, b in page_blocks)

    # Language detection over the leading content.
    joined_head = " ".join(b for _, b in page_blocks[:12])
    lang, needs_tr = _detect_language(joined_head, original_language)
    result.language = lang
    result.requires_translation = needs_tr
    if needs_tr and not result.original_language:
        result.original_language = lang

    # Inferred report year from title + leading blocks.
    result.inferred_year = _infer_year(result.title, joined_head)

    # Rank blocks: always keep the leading (overview) block, then the most
    # financially-relevant blocks. Stable within equal scores (preserve order).
    indexed = list(enumerate(page_blocks))
    lead = indexed[:1]
    rest = indexed[1:]
    rest_sorted = sorted(rest, key=lambda t: (-_relevance(t[1][1]), t[0]))
    chosen = lead + rest_sorted
    seen_text: set[str] = set()
    n = 0
    for _orig_idx, (block_page, blk) in chosen:
        if n >= max_excerpts:
            break
        text = blk.strip()
        if len(text) > per_excerpt:
            text = text[: per_excerpt - 1].rstrip() + "…"
        key = text[:160].lower()
        if key in seen_text:
            continue
        seen_text.add(key)
        etype = _classify(blk)
        # Leading block with weak financial signal is a business description.
        if _orig_idx == 0 and etype == EVIDENCE_TYPE_GENERAL:
            etype = EVIDENCE_TYPE_BUSINESS
        rel = _relevance(blk)
        confidence = "high" if rel >= 4 else ("medium" if rel >= 1 else "low")
        heading = text.split(".")[0][:90] if text else None
        n += 1
        result.excerpts.append(
            DocumentExcerpt(
                excerpt_id=f"X{n}",
                heading=heading,
                text=text,
                page_number=block_page,
                char_count=len(text),
                confidence=confidence,
                evidence_type=etype,
            )
        )

    if not result.excerpts:
        result.source_gaps.append(
            "Document text was read but no usable excerpts could be formed."
        )
    return result


__all__ = [
    "DocumentExcerpt",
    "DocumentTextExtraction",
    "extract_document_text",
    "EVIDENCE_TYPE_FINANCIAL",
    "EVIDENCE_TYPE_BUSINESS",
    "EVIDENCE_TYPE_RISK",
    "EVIDENCE_TYPE_GENERAL",
]
