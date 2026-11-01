"""
Official SEC filing-body retrieval — Phase 32A Slice 5B.1.

Until this module, the SEC path only ever read *structured* JSON from
``data.sec.gov`` (companyfacts / submissions): filing **metadata** was sourced but
the filing **body** was never fetched, which is exactly why every SEC result also
carried a ``primary_filing_unavailable`` gap and why US issuers produced zero
primary-document candidates. This module resolves an accession number to the
canonical filing-body document on ``www.sec.gov/Archives`` and hands that URL to
the existing bounded, SSRF-safe document fetcher.

SUPPLEMENT ONLY: this module does NOT touch, replace, re-derive or second-guess
SEC/XBRL structured facts. Structured financial facts continue to come from the
companyfacts pipeline; filing bodies are additional narrative evidence layered on
top of them.

Safety properties:
  * **Official SEC hosts only.** Every URL is built from code-defined constants
    against ``sec.gov``; no third-party mirror, no caller-supplied URL, no
    arbitrary-URL fetch surface. The body fetch re-checks the host against
    ``SEC_ALLOWED_DOMAINS`` inside ``safe_fetch_document``.
  * **Path-traversal proof.** A document filename that contains ``..``, a path
    separator (raw or percent-encoded) or a scheme can never become a URL.
  * **Declared identity + real throttle.** Every request carries the SEC
    fair-access User-Agent and passes through :class:`SecRateLimiter`, which
    enforces a minimum client-side interval between SEC requests.
  * **Bounded.** Index fetches use a config timeout, a byte cap,
    ``follow_redirects=False`` and accept only a 2xx. Body fetches inherit the
    timeout / max-bytes / redirect / content-type guards of
    ``safe_fetch_document`` (with ``resolve_ip=True``, closing DNS rebinding).
  * **Never the full-submission dump.** ``*.txt`` complete-submission files are
    excluded from selection outright — they are unbounded concatenations.
  * **Deterministic.** Document selection is a pure, total function with a stable
    tie-break, so the same filing index always yields the same document.
  * **Never raises.** Every failure (bad CIK, missing accession, 404, malformed
    JSON, transport error) degrades to an empty list / ``None`` / an honest
    ``DocumentFetchResult``. Nothing is ever fabricated.
  * **Secret-free.** No credentials are sent, no tokenized URLs exist on SEC
    Archives, and only counts / form types / accession numbers are logged.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.sources.document_discovery import (
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_OTHER,
    DOC_KIND_RESULTS_RELEASE,
)
from app.services.sources.document_fetcher import (
    DocumentFetchResult,
    safe_fetch_document,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.ingestion_status import (
    FAILURE_CONFLICTING_CIK,
    FAILURE_INVALID_SEC_URL,
    FAILURE_MALFORMED_ACCESSION,
    FAILURE_MISSING_CIK,
    FAILURE_NO_PRIMARY_FILING_DOCUMENT,
    FAILURE_PREFLIGHT_BUDGET_EXHAUSTED,
    failure_code_for_block,
    http_status_class,
)
from app.services.sources.safe_web_fetcher import (
    Resolver,
    async_check_fetch_url,
    pinned_transport_for,
)
from app.services.sources.verified_issuer_sources import (
    host_of,
    registrable_host_allowed,
)

_logger = logging.getLogger("app.services.sources.sec_filing_documents")

# --------------------------------------------------------------------------- #
# Constants
# --------------------------------------------------------------------------- #

SEC_ALLOWED_DOMAINS: tuple[str, ...] = ("sec.gov",)
SEC_ARCHIVES_BASE = "https://www.sec.gov/Archives/edgar/data"
SEC_USER_AGENT = (
    "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"
)

# Preference order: the richest primary-disclosure form first.
SUPPORTED_FORMS: tuple[str, ...] = ("10-K", "20-F", "10-Q", "6-K", "8-K")

# How the URL of a SEC filing body was discovered — the accession number in the
# issuer's own EDGAR submission history, not a page scan. Part of the same
# ``discovery_strategy`` vocabulary as the ``document_discovery`` strategies.
STRATEGY_SEC_ACCESSION = "sec_accession"

# Form → document kind, using the SAME closed ``doc_kind`` vocabulary the IR
# discovery layer uses so an operator can compare like with like.
_FORM_DOC_KIND: dict[str, str] = {
    "10-K": DOC_KIND_ANNUAL_REPORT,
    "20-F": DOC_KIND_ANNUAL_REPORT,
    "10-Q": DOC_KIND_INTERIM_REPORT,
    "6-K": DOC_KIND_INTERIM_REPORT,
    "8-K": DOC_KIND_RESULTS_RELEASE,
}

# Connector key used on any gap this module emits (SEC evidence lineage).
_CONNECTOR_KEY = "sec_edgar"
_SOURCE_ID = "sec_edgar"

# Defaults used when the corresponding setting is absent (read via ``getattr``
# so this module never depends on a config change landing first).
DEFAULT_REQUEST_MIN_INTERVAL_MS = 120
DEFAULT_INDEX_TIMEOUT_SECONDS = 15
DEFAULT_INDEX_MAX_BYTES = 2_000_000
#: Byte ceiling for a filing's ``-index.htm`` page. A real page is ~12 KB (JPM's
#: Q3 2026 results 8-K: 11.7 KB); anything past this is discarded, never parsed.
INDEX_PAGE_MAX_BYTES = 262_144

# Index fetches allowed per document we are trying to resolve. Headroom for
# filings whose index carries no usable body document, WITHOUT letting a long
# filings list turn resolution into an unbounded sequence of network round-trips.
_INDEX_ATTEMPTS_PER_DOCUMENT = 3

# Sleep signature for the injectable throttle.
SleepFn = Callable[[float], Awaitable[None]]
ClockFn = Callable[[], float]

_HTML_SUFFIXES = (".htm", ".html")

# Real EDGAR exhibits are typed ``EX-…`` and conventionally named ``ex99…`` /
# ``ex-10_1…``. Both shapes are treated as exhibits (a superset of the "starts
# with EX-" rule) so an exhibit never masquerades as the primary document.
#: An exhibit, by the name the issuer gave the file. Two conventions, because relying on
#: the index's ``type`` field alone let MP Materials' 500-page Exhibit 96.1 technical
#: report summary — ``mpmcexhibit961123125.htm``, typed only in some index listings —
#: past the exhibit filter and win step 3, which prefers the LARGEST body document. The
#: 10-K itself was never ingested. A filing body is never called "exhibit<number>".
_EXHIBIT_NAME_RE = re.compile(r"^ex[-_]?\d|exhibit[-_]?\d", re.IGNORECASE)
# XBRL viewer fragments: R1.htm, R42.htm — rendered slices, never the filing.
_XBRL_FRAGMENT_RE = re.compile(r"^r\d+\.html?$", re.IGNORECASE)

#: EDGAR 8-K Item 2.02, "Results of Operations and Financial Condition" — the item an
#: issuer files its earnings release under. The only thing that makes an 8-K a results
#: release (ADR-068).
RESULTS_ITEM_CODE = "2.02"
_ITEM_PREFIX_RE = re.compile(r"^\s*item\s*", re.IGNORECASE)


# --------------------------------------------------------------------------- #
# Pure helpers (no network — safe to unit-test exhaustively)
# --------------------------------------------------------------------------- #


def normalize_cik(cik: str | int | None) -> str | None:
    """Return the 10-digit zero-padded CIK, or None when unusable.

    Accepts an int, a bare number, a zero-padded string or a ``CIK``-prefixed
    string. Anything containing non-digits (after the optional ``CIK`` prefix),
    an empty value, or a zero/over-long CIK returns None.
    """
    if cik is None or isinstance(cik, bool):
        return None
    raw = str(cik).strip()
    if not raw:
        return None
    if raw.lower().startswith("cik"):
        raw = raw[3:].strip()
    if raw.startswith("-"):
        return None
    if not raw.isdigit():
        return None
    value = int(raw)
    if value <= 0 or value > 9_999_999_999:
        return None
    return str(value).zfill(10)


def normalize_accession(accession: str | None) -> str | None:
    """Return the 18-digit dash-free accession number, or None when unusable."""
    if accession is None:
        return None
    raw = str(accession).strip().replace("-", "").replace(" ", "")
    if len(raw) != 18 or not raw.isdigit():
        return None
    return raw


def format_accession(accession: str) -> str:
    """Return the canonical dashed accession form ``0000320193-24-000123``.

    Best-effort: an accession that cannot be normalized is returned stripped and
    unchanged rather than raising (callers already guard with
    :func:`normalize_accession`).
    """
    normalized = normalize_accession(accession)
    if normalized is None:
        return str(accession or "").strip()
    return f"{normalized[:10]}-{normalized[10:12]}-{normalized[12:]}"


def _is_unsafe_filename(filename: str | None) -> bool:
    """True when ``filename`` must never be interpolated into an Archives URL."""
    if not filename:
        return True
    name = str(filename).strip()
    if not name:
        return True
    lowered = name.lower()
    # Control characters (incl. CR/LF/NUL/DEL) could split a request line or be
    # normalized away by an intermediary — never interpolate one into a URL.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        return True
    if ".." in name or "/" in name or "\\" in name:
        return True
    # Percent-encoded separators would be decoded by the server.
    if "%2f" in lowered or "%5c" in lowered or "%2e%2e" in lowered:
        return True
    # A scheme (or a protocol-relative URL) would leave sec.gov entirely.
    if "://" in lowered or lowered.startswith("//") or ":" in lowered:
        return True
    if "?" in name or "#" in name:
        return True
    return False


def build_filing_index_url(cik: str | int | None, accession: str | None) -> str | None:
    """Return the ``index.json`` URL for one filing, or None when unusable.

    NOTE on CIK forms: ``data.sec.gov`` (submissions / companyfacts) requires the
    10-digit **zero-padded** CIK, but ``www.sec.gov/Archives/edgar/data`` serves
    the **unpadded integer** CIK. We keep the padded form on the record (and on
    :class:`SecFilingDocument`) and use the unpadded integer only in Archives
    paths.
    """
    padded = normalize_cik(cik)
    normalized = normalize_accession(accession)
    if padded is None or normalized is None:
        return None
    return f"{SEC_ARCHIVES_BASE}/{int(padded)}/{normalized}/index.json"


def build_filing_index_page_url(cik: str | int | None, accession: str | None) -> str | None:
    """Return the ``<accession>-index.htm`` page URL for one filing, or None.

    ``index.json`` lists the files in an accession folder but types every one by its
    icon (``text.gif``); only this page carries each document's EDGAR type
    (``EX-99.1``), which is how a results 8-K's press release is found (ADR-068).
    Same host, same unpadded-CIK Archives path as :func:`build_filing_index_url`.
    """
    padded = normalize_cik(cik)
    normalized = normalize_accession(accession)
    if padded is None or normalized is None:
        return None
    dashed = format_accession(normalized)
    return f"{SEC_ARCHIVES_BASE}/{int(padded)}/{normalized}/{dashed}-index.htm"


def build_document_url(
    cik: str | int | None, accession: str | None, filename: str | None
) -> str | None:
    """Return the Archives URL for one filing document, or None when unsafe.

    A filename containing ``..``, a path separator or a scheme is rejected
    outright — the filename comes from a parsed remote index, so it is treated as
    untrusted input.
    """
    padded = normalize_cik(cik)
    normalized = normalize_accession(accession)
    if padded is None or normalized is None:
        return None
    if _is_unsafe_filename(filename):
        return None
    name = str(filename).strip()
    return f"{SEC_ARCHIVES_BASE}/{int(padded)}/{normalized}/{name}"


def base_form(form: str | None) -> str:
    """Return the upper-case base form (``10-K/A`` → ``10-K``); '' when absent.

    Case- and whitespace-tolerant; an amendment suffix after ``/`` is dropped.
    """
    if not form:
        return ""
    return str(form).strip().upper().split("/")[0].strip()


def is_supported_form(form: str | None) -> bool:
    """True when ``form`` (or its base form) is one this module retrieves."""
    return base_form(form) in SUPPORTED_FORMS


def parse_item_codes(value: Any) -> tuple[str, ...]:
    """EDGAR 8-K item codes from the submissions feed's ``items`` value. Pure + total.

    EDGAR sends one comma-separated string per filing (``"2.02,9.01"``, sometimes
    ``"Item 2.02, Item 9.01"``); the catalyst layer already keeps them as a list. Both
    shapes are accepted. Anything else — ``None``, a number, an empty string — is
    ``()``: no item code is ever guessed.
    """
    if isinstance(value, str):
        parts: list[Any] = value.split(",")
    elif isinstance(value, (list, tuple)):
        parts = list(value)
    else:
        return ()
    codes: list[str] = []
    for part in parts:
        if not isinstance(part, str):
            continue
        code = _ITEM_PREFIX_RE.sub("", part).strip()
        if code and code not in codes:
            codes.append(code)
    return tuple(codes)


def filing_item_codes(filing: dict[str, Any] | None) -> tuple[str, ...]:
    """The EDGAR item codes a connector filing dict carries; ``()`` when it carries none.

    ``()`` means UNKNOWN, not "no items": an 8-K always reports at least one item, so
    an empty value only ever says the caller did not pass them on (a 6-K has no items
    field at all).
    """
    if not isinstance(filing, dict):
        return ()
    for key in ("item_numbers", "items"):
        codes = parse_item_codes(filing.get(key))
        if codes:
            return codes
    return ()


def is_results_release_8k(form: str | None, item_codes: Any) -> bool:
    """True only for an 8-K whose EDGAR items include 2.02, Results of Operations.

    The item code is the issuer's own declaration of what the filing is. A note
    offering, a director change or a Regulation FD slide deck is an 8-K too, and none
    of them is a results release however recent it is (ADR-068).
    """
    if base_form(form) != "8-K":
        return False
    codes = item_codes if isinstance(item_codes, tuple) else parse_item_codes(item_codes)
    return RESULTS_ITEM_CODE in codes


def doc_kind_for_form(form: str | None, item_codes: Any = None) -> str:
    """Map a SEC form onto the shared ``doc_kind`` vocabulary. Pure + total.

    An unrecognised form is ``other`` — never guessed into ``annual_report``.

    An 8-K is a ``results_release`` only when its item codes are unknown (the legacy
    label, kept for callers that pass none) or include 2.02. An 8-K whose items are
    known and do NOT include 2.02 is ``other``: it is an 8-K, not a results release
    (ADR-068).
    """
    base = base_form(form)
    if base == "8-K":
        codes = item_codes if isinstance(item_codes, tuple) else parse_item_codes(item_codes)
        if codes and RESULTS_ITEM_CODE not in codes:
            return DOC_KIND_OTHER
    return _FORM_DOC_KIND.get(base, DOC_KIND_OTHER)


def _form_rank(form: str | None) -> int:
    """Preference rank for a form (lower is preferred); unsupported ranks last."""
    base = base_form(form)
    try:
        return SUPPORTED_FORMS.index(base)
    except ValueError:
        return len(SUPPORTED_FORMS)


def _filing_rank(filing: dict[str, Any]) -> tuple[int, int]:
    """Form rank, then — within the 8-Ks — an Item 2.02 results release first.

    Without the second key the NEWEST 8-K always won, so a note-offering legal
    opinion filed a week after the earnings 8-K displaced the quarter's results
    (JPM, 2026-10-20 vs 2026-10-13). Every other form is unaffected.
    """
    form = filing.get("form_type")
    results_first = 0 if is_results_release_8k(form, filing_item_codes(filing)) else 1
    return (_form_rank(form), results_first)


def parse_filing_index(payload: Any) -> list[dict[str, Any]]:
    """Extract the ``directory.item`` entries from an ``index.json`` payload.

    Tolerant by design: any shape mismatch (missing keys, wrong types, renamed
    fields) returns ``[]``. Never raises.
    """
    if not isinstance(payload, dict):
        return []
    directory = payload.get("directory")
    if not isinstance(directory, dict):
        return []
    items = directory.get("item")
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]


def _entry_name(entry: dict[str, Any]) -> str:
    value = entry.get("name")
    return str(value).strip() if isinstance(value, (str, int)) else ""


def _entry_type(entry: dict[str, Any]) -> str:
    value = entry.get("type")
    return str(value).strip() if isinstance(value, (str, int)) else ""


def _entry_size(entry: dict[str, Any]) -> int:
    """Parse the ``size`` field (SEC sends it as a string) — 0 when unusable."""
    value = entry.get("size")
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    if isinstance(value, float):
        return max(0, int(value))
    if isinstance(value, str):
        digits = value.strip().replace(",", "")
        if digits.isdigit():
            return int(digits)
    return 0


def _is_html_entry(entry: dict[str, Any]) -> bool:
    return _entry_name(entry).lower().endswith(_HTML_SUFFIXES)


def is_exhibit_name(name: str | None) -> bool:
    """Is this file name an exhibit's? The one rule, so callers cannot disagree."""
    return bool(_EXHIBIT_NAME_RE.search((name or "").lower()))


def _is_exhibit_entry(entry: dict[str, Any]) -> bool:
    name = _entry_name(entry).lower()
    etype = _entry_type(entry).lower()
    if etype.startswith("ex-") or name.startswith("ex-"):
        return True
    return is_exhibit_name(name)


def _is_noise_entry(entry: dict[str, Any]) -> bool:
    """True for filing-index pages, XBRL viewer fragments and filing summaries."""
    name = _entry_name(entry).lower()
    if not name:
        return True
    if name.endswith("-index.htm") or name.endswith("-index.html"):
        return True
    if name.endswith("-index-headers.html"):
        return True
    if _XBRL_FRAGMENT_RE.match(name):
        return True
    if name.startswith("filingsummary"):
        return True
    return False


def select_primary_document(
    entries: list[dict[str, Any]],
    *,
    form_type: str | None,
    primary_document_hint: str | None = None,
    allow_exhibits: bool = False,
) -> dict[str, Any] | None:
    """Pick the filing-body document from ``index.json`` entries. Pure + total.

    ``entries`` are ``directory.item`` dicts (``name`` / ``type`` / ``size``;
    ``size`` may be a string). Selection order:

      1. exact case-insensitive match on ``primary_document_hint`` (the
         submissions feed's ``primaryDocument``);
      2. an entry whose ``type`` is the filing's form and whose name is
         ``.htm`` / ``.html``;
      3. the LARGEST non-exhibit ``.htm`` / ``.html`` entry that is not a filing
         index page, an XBRL viewer fragment (``R2.htm``) or a filing summary;
      4. the first (name-ascending) non-exhibit ``.htm`` / ``.html`` entry;
      5. only when ``allow_exhibits`` is True, the largest exhibit
         ``.htm`` / ``.html`` entry.

    ``.txt`` complete-submission dumps are excluded at every step — they are
    unbounded concatenations of the whole filing. Ties break on name ascending,
    so the selection is stable across runs.
    """
    usable = [
        e
        for e in entries
        if isinstance(e, dict) and _entry_name(e) and _is_html_entry(e)
    ]
    if not usable:
        return None

    # 1. Explicit hint from the submissions feed wins outright.
    hint = (primary_document_hint or "").strip().lower()
    if hint:
        hinted = sorted(
            (e for e in usable if _entry_name(e).lower() == hint),
            key=lambda e: _entry_name(e),
        )
        if hinted:
            return hinted[0]

    non_exhibits = [e for e in usable if not _is_exhibit_entry(e)]

    # 2. An entry explicitly typed as the filing's form.
    wanted = base_form(form_type)
    if wanted:
        typed = sorted(
            (e for e in non_exhibits if base_form(_entry_type(e)) == wanted),
            key=lambda e: _entry_name(e),
        )
        if typed:
            return typed[0]

    # 3. Largest genuine body document.
    bodies = [e for e in non_exhibits if not _is_noise_entry(e)]
    if bodies:
        return sorted(bodies, key=lambda e: (-_entry_size(e), _entry_name(e)))[0]

    # 4. Any non-exhibit HTML entry (index pages / fragments as a last resort).
    if non_exhibits:
        return sorted(non_exhibits, key=lambda e: _entry_name(e))[0]

    # 5. Exhibits, only when explicitly allowed, ranked after everything above.
    if allow_exhibits:
        exhibits = [e for e in usable if _is_exhibit_entry(e)]
        if exhibits:
            return sorted(exhibits, key=lambda e: (-_entry_size(e), _entry_name(e)))[0]

    return None


#: The ``-index.htm`` page is parsed with ``str.find`` / ``str.split`` only: every
#: step is linear in the page, so an adversarial page (thousands of unclosed tags)
#: cannot stall the event loop the way a backtracking ``<tag[^>]*…>`` regex can.
_DOC_TABLE_MARKER = 'summary="document format files"'
#: ASCII-only lower-casing. ``str.lower`` can change a string's LENGTH ("İ" lowers to
#: two code points), which would misalign every index found in the lowered copy.
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")
_PRESS_RELEASE_EXHIBIT_RE = re.compile(r"^EX-99\.0*1$")


def _strip_tags(raw: str) -> str:
    """Text with every ``<…>`` removed; an unclosed ``<`` drops the rest. Linear."""
    out: list[str] = []
    i, n = 0, len(raw)
    while i < n:
        lt = raw.find("<", i)
        if lt < 0:
            out.append(raw[i:])
            break
        out.append(raw[i:lt])
        out.append(" ")
        gt = raw.find(">", lt + 1)
        if gt < 0:
            break
        i = gt + 1
    return "".join(out)


def _cell_text(raw: str) -> str:
    return html.unescape(_strip_tags(raw)).replace("\xa0", " ").strip()


def _split_ci(text: str, lowered: str, separator: str) -> list[tuple[str, str]]:
    """Split ``text`` on a lower-case ``separator`` found in ``lowered``. Linear."""
    parts: list[tuple[str, str]] = []
    start = 0
    while True:
        at = lowered.find(separator, start)
        if at < 0:
            parts.append((text[start:], lowered[start:]))
            return parts
        parts.append((text[start:at], lowered[start:at]))
        start = at + len(separator)


def _after_open_tag(text: str, lowered: str, tag: str) -> tuple[str, str] | None:
    """The content after the LAST ``<tag …>`` opener in a segment, or None. Linear."""
    at = lowered.rfind(tag)
    if at < 0:
        return None
    gt = lowered.find(">", at)
    if gt < 0:
        return None
    return text[gt + 1 :], lowered[gt + 1 :]


def _anchor_text(cell: str, lowered: str) -> str:
    """The text of the first ``<a …>…</a>`` in a cell, else the whole cell. Linear."""
    at = lowered.find("<a ")
    if at < 0:
        at = lowered.find("<a>")
    if at < 0:
        return cell
    gt = lowered.find(">", at)
    end = lowered.find("</a>", gt + 1) if gt >= 0 else -1
    if gt < 0 or end < 0:
        return cell
    return cell[gt + 1 : end]


def _exhibit_type(entry: dict[str, Any]) -> str:
    return _entry_type(entry).upper().replace(" ", "")


def parse_filing_index_page(page: str | bytes | None) -> list[dict[str, Any]]:
    """Rows of the ``Document Format Files`` table on a ``-index.htm`` page. Pure + total.

    Each row becomes ``{"name", "type", "size", "description"}``, in the filing's own
    sequence order. Only that FIRST table is read: the "Data Files" table below it
    holds XBRL files, never a press release. Any shape mismatch, or a page over
    ``INDEX_PAGE_MAX_BYTES``, returns ``[]``. A name is the anchor TEXT (never the link
    target) and is re-validated by :func:`build_document_url` before it can become a
    URL. Linear in the page: ``str.find`` / ``str.split`` only, no regex.
    """
    if isinstance(page, bytes):
        if len(page) > INDEX_PAGE_MAX_BYTES:
            return []
        page = page.decode("utf-8", errors="replace")
    if not isinstance(page, str) or len(page) > INDEX_PAGE_MAX_BYTES:
        return []
    lowered_page = page.translate(_ASCII_LOWER)
    marker = lowered_page.find(_DOC_TABLE_MARKER)
    if marker < 0:
        return []
    open_end = lowered_page.find(">", marker)
    close = lowered_page.find("</table>", open_end + 1) if open_end >= 0 else -1
    if open_end < 0 or close < 0:
        return []
    table, lowered_table = page[open_end + 1 : close], lowered_page[open_end + 1 : close]
    entries: list[dict[str, Any]] = []
    for row, lowered_row in _split_ci(table, lowered_table, "</tr>"):
        cells: list[tuple[str, str]] = []
        for piece, lowered_piece in _split_ci(row, lowered_row, "</td>"):
            content = _after_open_tag(piece, lowered_piece, "<td")
            if content is not None:
                cells.append(content)
        if len(cells) < 5:
            continue
        text = _cell_text(_anchor_text(*cells[2]))
        name = text.split()[0] if text.split() else ""
        if not name:
            continue
        entries.append(
            {
                "name": name,
                "type": _cell_text(cells[3][0]),
                "size": _cell_text(cells[4][0]),
                "description": _cell_text(cells[1][0]),
            }
        )
    return entries


def select_results_exhibit(entries: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The press-release exhibit of a results 8-K, or None. Pure + total.

    An ``.htm`` / ``.html`` document typed ``EX-99.1`` wins; otherwise the first
    ``EX-99*`` document in filing order. A filename that could never be a safe URL is
    skipped. None means "no press-release exhibit here", and the caller keeps the
    8-K's primary document (ADR-068).
    """
    candidates = [
        e
        for e in entries
        if isinstance(e, dict)
        and _is_html_entry(e)
        and not _is_unsafe_filename(_entry_name(e))
        and _exhibit_type(e).startswith("EX-99")
    ]
    for entry in candidates:
        if _PRESS_RELEASE_EXHIBIT_RE.match(_exhibit_type(entry)):
            return entry
    return candidates[0] if candidates else None


# --------------------------------------------------------------------------- #
# Result type
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SecFilingDocument:
    """One resolved SEC filing body, with its full provenance retained."""

    accession_number: str  # canonical dashed form
    form_type: str
    filing_date: str | None
    canonical_url: str  # the filing-body URL on www.sec.gov/Archives
    document_name: str
    cik: str  # 10-digit zero-padded
    title: str
    is_exhibit: bool = False
    #: The filing's EDGAR item codes (8-K only; ``()`` when unknown) — what decides
    #: whether an 8-K is labelled a results release.
    item_codes: tuple[str, ...] = ()


# --------------------------------------------------------------------------- #
# Client-side throttle (SEC fair access)
# --------------------------------------------------------------------------- #


class SecRateLimiter:
    """Minimum-interval throttle for SEC requests (SEC fair-access policy).

    A single sliding window: each ``acquire`` waits until at least
    ``min_interval_seconds`` has elapsed since the previous request. ``sleep`` and
    ``clock`` are injectable so tests are deterministic and never really sleep.
    """

    def __init__(
        self,
        *,
        min_interval_seconds: float | None = None,
        cfg: Settings | None = None,
        sleep: SleepFn | None = None,
        clock: ClockFn | None = None,
    ) -> None:
        if min_interval_seconds is None:
            min_interval_seconds = _min_interval_seconds(cfg)
        self.min_interval_seconds = max(0.0, float(min_interval_seconds))
        self._sleep: SleepFn = sleep or asyncio.sleep
        self._clock: ClockFn = clock or time.monotonic
        self._last: float | None = None
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        """Block until the next SEC request is allowed. Never raises."""
        if self.min_interval_seconds <= 0:
            return
        async with self._lock:
            now = self._clock()
            if self._last is not None:
                wait = self.min_interval_seconds - (now - self._last)
                if wait > 0:
                    await self._sleep(wait)
            self._last = self._clock()


def _min_interval_seconds(cfg: Settings | None) -> float:
    raw = getattr(cfg or default_settings, "sec_request_min_interval_ms", None)
    try:
        ms = int(raw) if raw is not None else DEFAULT_REQUEST_MIN_INTERVAL_MS
    except (TypeError, ValueError):
        ms = DEFAULT_REQUEST_MIN_INTERVAL_MS
    return max(0.0, ms / 1000.0)


def _index_timeout_seconds(cfg: Settings | None) -> int:
    raw = getattr(
        cfg or default_settings,
        "primary_document_fetch_timeout_seconds",
        DEFAULT_INDEX_TIMEOUT_SECONDS,
    )
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INDEX_TIMEOUT_SECONDS


def _index_max_bytes(cfg: Settings | None) -> int:
    """Byte ceiling for a filing index.json.

    Deliberately a module CONSTANT, not a ``Settings`` field: an env-var-shaped
    `getattr` lookup for a setting that does not exist would silently ignore
    anything an operator set. The index is a small directory listing, so there is
    no operational reason to tune it. ``cfg`` is accepted for signature symmetry
    with the other bound helpers.
    """
    raw = DEFAULT_INDEX_MAX_BYTES
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return DEFAULT_INDEX_MAX_BYTES


# --------------------------------------------------------------------------- #
# Network layer (bounded, throttled, guarded — never raises)
# --------------------------------------------------------------------------- #


async def _fetch_sec_archive_bytes(
    url: str,
    accession: str | None,
    *,
    accept: str,
    cfg: Settings | None,
    client_factory: Callable[..., Any] | None,
    limiter: SecRateLimiter | None,
    resolver: Resolver | None,
    max_bytes: int | None = None,
    event_prefix: str = "sec_filing_index",
) -> bytes | None:
    """GET one small SEC Archives listing; ``None`` on any failure. Never raises.

    The single network leg shared by ``index.json`` and the ``-index.htm`` page: the
    same allowlist re-check with ``resolve_ip=True``, address pinning, declared
    User-Agent, config timeout, byte cap, no redirects, 2xx only. A truncated
    (over-cap) body is discarded rather than half-parsed. ``max_bytes`` defaults to the
    ``index.json`` cap; ``event_prefix`` keeps each listing's log events distinct.
    """
    cfg = cfg or default_settings
    limiter = limiter or SecRateLimiter(cfg=cfg)
    if max_bytes is None:
        max_bytes = _index_max_bytes(cfg)

    try:
        import httpx
    except Exception as exc:  # noqa: BLE001 - degrade, never crash a run
        log_event(
            _logger,
            f"{event_prefix}_unavailable",
            reason=type(exc).__name__,
        )
        return None

    # Same guard chain as ``safe_fetch_document``: an unvalidated address is never
    # connected to, even though the host here is a code-defined constant.
    try:
        check_kwargs: dict[str, Any] = {"cfg": cfg, "resolve_ip": True}
        if resolver is not None:
            check_kwargs["resolver"] = resolver
        reason, pinned_ip = await async_check_fetch_url(
            url, SEC_ALLOWED_DOMAINS, **check_kwargs
        )
    except Exception as exc:  # noqa: BLE001 - a guard failure is a block, never a crash
        reason, pinned_ip = f"guard failed: {type(exc).__name__}", None
    if reason:
        log_event(
            _logger,
            f"{event_prefix}_blocked",
            reason=failure_code_for_block(reason),
            accession=format_accession(str(accession or "")),
        )
        return None

    transport = pinned_transport_for(cfg, host_of(url), pinned_ip)
    client_kwargs: dict[str, Any] = {
        "follow_redirects": False,
        "timeout": _index_timeout_seconds(cfg),
        "cookies": None,
        # W0 / D6: no environment proxy may reroute a guarded fetch.
        "trust_env": False,
        "headers": {
            "User-Agent": SEC_USER_AGENT,
            "Accept": accept,
        },
    }
    if transport is not None:
        client_kwargs["transport"] = transport

    factory = client_factory or httpx.AsyncClient
    try:
        await limiter.acquire()
        async with factory(**client_kwargs) as client:
            async with client.stream("GET", url) as resp:
                status = int(getattr(resp, "status_code", 0) or 0)
                if not 200 <= status < 300:
                    # Only the status CLASS is logged — never the exact code.
                    log_event(
                        _logger,
                        f"{event_prefix}_rejected",
                        status_class=http_status_class(status),
                        accession=format_accession(str(accession or "")),
                    )
                    return None
                chunks: list[bytes] = []
                total = 0
                truncated = False
                async for chunk in resp.aiter_bytes():
                    chunks.append(chunk)
                    total += len(chunk)
                    if total > max_bytes:
                        truncated = True
                        break
                if truncated:
                    log_event(
                        _logger,
                        f"{event_prefix}_over_cap",
                        max_bytes=max_bytes,
                        accession=format_accession(str(accession or "")),
                    )
                    return None
                body = b"".join(chunks)
    except Exception as exc:  # noqa: BLE001 - transport must never crash a run
        log_event(
            _logger,
            f"{event_prefix}_fetch_failed",
            reason=type(exc).__name__,
            accession=format_accession(str(accession or "")),
        )
        return None
    return body


async def fetch_filing_index(
    cik: str | int | None,
    accession: str | None,
    *,
    cfg: Settings | None = None,
    client_factory: Callable[..., Any] | None = None,
    limiter: SecRateLimiter | None = None,
    resolver: Resolver | None = None,
) -> list[dict[str, Any]]:
    """Fetch and parse one filing's ``index.json``. Returns ``[]`` on any failure.

    Routed through the SAME hardened outbound chain as the body fetch: the URL is
    re-checked with ``async_check_fetch_url`` against ``SEC_ALLOWED_DOMAINS`` with
    ``resolve_ip=True`` (https-only, safe public host, allowlist, every resolved
    address public), and the connection is PINNED to the validated address so the
    name cannot rebind between check and connect.

    Bounded: declared User-Agent, config timeout, byte cap, no redirects, 2xx
    only. A truncated (over-cap) body is discarded rather than half-parsed.
    """
    url = build_filing_index_url(cik, accession)
    if url is None:
        return []
    body = await _fetch_sec_archive_bytes(
        url,
        accession,
        accept="application/json",
        cfg=cfg,
        client_factory=client_factory,
        limiter=limiter,
        resolver=resolver,
    )
    if body is None:
        return []

    try:
        payload = json.loads(body.decode("utf-8", errors="replace"))
    except Exception:  # noqa: BLE001 - malformed JSON is an honest empty result
        log_event(
            _logger,
            "sec_filing_index_unparseable",
            accession=format_accession(str(accession or "")),
        )
        return []

    entries = parse_filing_index(payload)
    log_event(
        _logger,
        "sec_filing_index_fetched",
        accession=format_accession(str(accession or "")),
        entry_count=len(entries),
    )
    return entries


async def fetch_filing_index_page(
    cik: str | int | None,
    accession: str | None,
    *,
    cfg: Settings | None = None,
    client_factory: Callable[..., Any] | None = None,
    limiter: SecRateLimiter | None = None,
    resolver: Resolver | None = None,
) -> list[dict[str, Any]]:
    """Fetch and parse one filing's ``-index.htm`` page. ``[]`` on any failure.

    Same hardened, throttled network leg as :func:`fetch_filing_index` (one shared
    helper) and the same official host: no new host and no caller-supplied URL. Its
    own byte cap (``INDEX_PAGE_MAX_BYTES``) and its own ``sec_filing_index_page_*``
    log events; the parse runs in a worker thread.
    """
    url = build_filing_index_page_url(cik, accession)
    if url is None:
        return []
    body = await _fetch_sec_archive_bytes(
        url,
        accession,
        accept="text/html",
        cfg=cfg,
        client_factory=client_factory,
        limiter=limiter,
        resolver=resolver,
        max_bytes=INDEX_PAGE_MAX_BYTES,
        event_prefix="sec_filing_index_page",
    )
    if body is None:
        return []
    # Off the event loop: the parse is linear and the page capped, but a request
    # handler is never the place to find out (the gunicorn worker-timeout outage).
    entries = await asyncio.to_thread(parse_filing_index_page, body)
    log_event(
        _logger,
        "sec_filing_index_page_fetched",
        accession=format_accession(str(accession or "")),
        entry_count=len(entries),
    )
    return entries


IndexFetcher = Callable[..., Awaitable[list[dict[str, Any]]]]


def _sorted_filings(filings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Supported filings in preference order: form rank, then newest first.

    Two stable passes: newest ``filed_date`` first (ISO dates sort
    lexicographically; a missing date sorts last), then by form rank — where, among
    8-Ks, an Item 2.02 results release ranks ahead of every other 8-K (ADR-068).
    """
    usable = [
        f
        for f in filings
        if isinstance(f, dict) and is_supported_form(f.get("form_type"))
    ]
    # Newest first among filings that carry a date; undated ones sort last.
    dated = [f for f in usable if str(f.get("filed_date") or "")]
    undated = [f for f in usable if not str(f.get("filed_date") or "")]
    dated.sort(key=lambda f: str(f.get("filed_date") or ""), reverse=True)
    return sorted(dated + undated, key=_filing_rank)


_ARCHIVES_CIK_RE = re.compile(r"/edgar/data/(\d{1,10})(?:/|$)", re.IGNORECASE)


def cik_from_archives_url(url: str | None) -> str | None:
    """Extract the filer CIK from an official SEC Archives URL.

    ``https://www.sec.gov/Archives/edgar/data/320193/0000.../aapl-...htm`` carries
    the filer's CIK in its path. Only ``sec.gov`` URLs are trusted — a CIK is an
    identity, and taking one from an arbitrary host would let unrelated metadata
    redirect us at a different filer.
    """
    if not url:
        return None
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return None
    # HTTPS only, matching every other SEC path here — an http:// URL is not a
    # trustworthy source of an issuer identity.
    if parts.scheme != "https":
        return None
    host = (parts.hostname or "").lower()
    if not registrable_host_allowed(host, SEC_ALLOWED_DOMAINS):
        return None
    match = _ARCHIVES_CIK_RE.search(parts.path or "")
    return normalize_cik(match.group(1)) if match else None


def cik_from_accession(accession: str | None) -> str | None:
    """Derive the filer CIK from an accession number's 10-digit prefix.

    ``0000320193-26-000020`` is ``<filer CIK><year><sequence>``. This is the
    fallback when a filing carries no Archives URL.
    """
    digits = normalize_accession(accession)
    return normalize_cik(digits[:10]) if digits else None


def _one_cik_from_filing(filing: dict[str, Any]) -> str | None:
    """URL-derived CIK preferred; accession-derived CIK as the fallback."""
    return cik_from_archives_url(filing.get("url")) or cik_from_accession(
        filing.get("accession_number")
    )


def _ciks_from_urls(filings: list[dict[str, Any]]) -> set[str]:
    """CIKs from official SEC Archives URLs only — the issuer's own path segment.

    An accession's 10-digit prefix is NOT this: it identifies whoever SUBMITTED the
    filing, which for most large issuers is a filing agent. SCCO's 2026 10-K is
    ``0001104659-26-021492`` — prefix ``0001104659``, Broadridge — while the issuer is
    ``0001001838``. Treating that prefix as an identity made the two disagree and the
    acquisition of the 10-K fail closed, which is how a live deep run ended up with the
    10-Q and the 10-K's exhibit index and no annual report at all.
    """
    found: set[str] = set()
    for filing in filings:
        if not isinstance(filing, dict):
            continue
        candidate = cik_from_archives_url(filing.get("url"))
        if candidate:
            found.add(candidate)
    return found


def _ciks_from_filings(filings: list[dict[str, Any]]) -> set[str]:
    """Every DISTINCT CIK derivable from the filing list — never just the first.

    Returning the full set (not one value) is what lets a caller distinguish
    "nothing derivable" (empty set) from "the filings disagree about who filed
    them" (more than one value) — the latter must fail closed, never guess.
    """
    found: set[str] = set()
    for filing in filings:
        if not isinstance(filing, dict):
            continue
        candidate = _one_cik_from_filing(filing)
        if candidate:
            found.add(candidate)
    return found


def _cik_from_filings(filings: list[dict[str, Any]]) -> str | None:
    """First CIK derivable from the filing list — URL preferred, accession next.

    Returns None rather than guessing when the filings disagree, so a mixed list
    can never silently attribute one issuer's filing body to another.
    """
    found = _ciks_from_filings(filings)
    return found.pop() if len(found) == 1 else None


def resolve_sec_filer_cik(
    cik: str | int | None, filings: list[dict[str, Any]]
) -> tuple[str | None, str | None]:
    """Return ``(resolved_cik, failure_code)`` via a deterministic fallback chain.

    Phase 32A Slice 5B.1 hotfix. Staging proved the SEC filing-body path was a
    SILENT no-op for every issuer: ``CompanyContext.cik`` is populated from
    ``company_snapshot`` → ``company_identity``, which carries no ``cik`` field
    at all, so trusting the caller's value alone always failed with no log and no
    SourceGap. This resolves the filer identity from every source available and
    CROSS-CHECKS them rather than trusting one silently:

      1. the caller-supplied CIK, when present and valid;
      2. otherwise the CIK derivable from the filing metadata itself — an
         official SEC Archives URL already attached to the filing event, else
         the accession number's own 10-digit prefix — but ONLY when every
         filing that yields one agrees.

    If the caller's value and the filings' own derived value DISAGREE, or the
    filings disagree among themselves, resolution FAILS CLOSED — returns
    ``(None, "conflicting_cik")`` — rather than trusting one source over the
    other; a wrong CIK would silently attribute one issuer's filing to another.
    Company name and ticker are never consulted: guessing an identity from a
    display name is exactly the kind of inference this function refuses to make.
    On success ``failure_code`` is None.
    """
    caller_cik = normalize_cik(cik)
    from_urls = _ciks_from_urls(filings)

    # Two official Archives URLs naming different issuers is a mixed list: fail closed.
    if len(from_urls) > 1:
        return None, FAILURE_CONFLICTING_CIK
    url_cik = next(iter(from_urls), None)
    if caller_cik and url_cik and caller_cik != url_cik:
        # Both authoritative and disagreeing — one issuer's body would be attributed to
        # another.
        return None, FAILURE_CONFLICTING_CIK
    if caller_cik:
        return caller_cik, None
    if url_cik:
        return url_cik, None

    # Nothing authoritative. The accession prefix is the SUBMITTER's id — usually a
    # filing agent — and is used only when nothing better exists, and only when every
    # filing agrees on it.
    derived = _ciks_from_filings(filings)
    if len(derived) > 1:
        return None, FAILURE_CONFLICTING_CIK
    filings_cik = next(iter(derived), None)
    if filings_cik:
        return filings_cik, None
    return None, FAILURE_MISSING_CIK


# --------------------------------------------------------------------------- #
# Preflight attempt visibility — a real candidate that never reached a fetch.
#
# ``resolve_filing_documents`` only ever returned successfully-resolved
# documents; every other outcome (unresolvable identity, a malformed accession,
# an unsafe filename, no selectable primary document, the preflight budget
# running out) degraded SILENTLY — no log, no SourceGap, no attempt row. That
# silence is indistinguishable from "the feature never ran", which is exactly
# what staging validation could not tell apart. ``SecPreflightFailure`` is the
# bounded, honest record of one such candidate.
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class SecPreflightFailure:
    """One known filing candidate that never reached a network fetch, and why.

    ``canonical_url`` is always a safe, non-secret identity string — the real SEC
    index location when a CIK + accession are both known, else the filing's own
    validated ``sec.gov`` URL, else a synthetic ``urn:`` identifier keyed on the
    (possibly malformed) accession text. It is NEVER a fabricated, guessed or
    fetchable-looking location, and it never carries a query string or fragment.
    """

    canonical_url: str
    accession_number: str | None
    form_type: str | None
    filing_date: str | None
    failure_code: str
    item_codes: tuple[str, ...] = ()


_SAFE_TOKEN_RE = re.compile(r"[^A-Za-z0-9._-]+")


def _sec_gov_safe_url(url: Any) -> str | None:
    """A validated ``https://…sec.gov/…`` URL, or None.

    Unlike :func:`cik_from_archives_url` this accepts ANY safe ``sec.gov`` path,
    not only the ``/edgar/data/<cik>/…`` shape — used only to pick an honest
    identity for a preflight attempt record, never to derive a filer identity.
    """
    if not isinstance(url, str) or not url:
        return None
    try:
        parts = urlsplit(url)
    except (ValueError, TypeError):
        return None
    if parts.scheme != "https":
        return None
    host = (parts.hostname or "").lower()
    if not registrable_host_allowed(host, SEC_ALLOWED_DOMAINS):
        return None
    return url


def _sanitize_identity_token(raw: Any, *, max_len: int = 64) -> str | None:
    """Bound + charset-restrict a value for use inside a synthetic identity URN.

    Never a secret: an accession number or form type is a public SEC filing
    identifier, not a credential, so it is safe to retain in a sanitized form.
    """
    text = str(raw or "").strip()
    if not text:
        return None
    cleaned = _SAFE_TOKEN_RE.sub("-", text).strip("-")[:max_len].strip("-")
    return cleaned or None


def _preflight_canonical_url(
    filing: dict[str, Any] | None,
    *,
    cik: str | None,
    accession: str | None,
) -> str | None:
    """The best honest, safe identity for a candidate that was never fetched.

    Prefers the real SEC index location (bounded, deterministic, never actually
    requested by this preflight path); falls back to the filing's own validated
    ``sec.gov`` URL; falls back to a synthetic, clearly-internal ``urn:`` keyed on
    whatever raw accession text is available. Returns None only when nothing
    stable and safe can be built at all — in which case nothing is persisted,
    matching the pre-existing behaviour for a filing with no accession at all.
    """
    if cik and accession:
        built = build_filing_index_url(cik, accession)
        if built:
            return built
    if isinstance(filing, dict):
        safe = _sec_gov_safe_url(filing.get("url"))
        if safe:
            return safe
        token = _sanitize_identity_token(filing.get("accession_number"))
        if token:
            return f"urn:investingbuddy:sec-filing:{token}"
    return None


def _preflight_failure(
    filing: dict[str, Any] | None,
    *,
    cik: str | None,
    accession: str | None,
    failure_code: str,
) -> SecPreflightFailure | None:
    """Build one preflight failure record, or None when no safe identity exists."""
    url = _preflight_canonical_url(filing, cik=cik, accession=accession)
    if not url:
        return None
    form = (
        base_form(filing.get("form_type"))
        if isinstance(filing, dict) and filing.get("form_type")
        else None
    )
    filed_date = (
        str(filing.get("filed_date") or "").strip() or None
        if isinstance(filing, dict)
        else None
    )
    return SecPreflightFailure(
        canonical_url=url,
        accession_number=format_accession(accession) if accession else None,
        form_type=form or None,
        filing_date=filed_date,
        failure_code=failure_code,
        item_codes=filing_item_codes(filing),
    )


async def resolve_filing_documents(
    cik: str | int | None,
    filings: list[dict[str, Any]],
    *,
    max_documents: int,
    cfg: Settings | None = None,
    client_factory: Callable[..., Any] | None = None,
    limiter: SecRateLimiter | None = None,
    index_fetcher: IndexFetcher | None = None,
    exhibit_index_fetcher: IndexFetcher | None = None,
    allow_exhibits: bool = False,
    deadline: float | None = None,
    clock: ClockFn = time.monotonic,
    resolver: Resolver | None = None,
    preflight_sink: list[SecPreflightFailure] | None = None,
) -> list[SecFilingDocument]:
    """Resolve filing metadata into canonical filing-body documents.

    ``filings`` are ``live_fetchers._filing_dict``-shaped dicts (``form_type``,
    ``accession_number``, ``filed_date``, ``title``, ``url``). Filings are visited
    in preference order (10-K, 20-F, 10-Q, 6-K, 8-K; newest first within a form,
    an Item 2.02 8-K ahead of any other 8-K) until ``max_documents`` bodies are
    resolved. Filings without a usable accession are skipped. Never raises.

    RESOLUTION IS BUDGETED. Each filing costs one bounded index fetch, so a long
    ``filings`` list could otherwise spend minutes here — BEFORE the caller's
    fetch budget is even consulted — and blow the request's gateway timeout. Two
    independent bounds prevent that:

      * ``deadline`` — an absolute ``clock()`` (monotonic) instant. It is checked
        BEFORE every index fetch; once passed, resolution stops cleanly and
        returns whatever resolved so far (never a fabricated document).
      * ``max_documents * 3`` index attempts, enforced regardless of the
        deadline, so a filings list full of index misses still terminates.

    RESULTS 8-K -> PRESS-RELEASE EXHIBIT (ADR-068). An Item 2.02 8-K's primary
    document is a cover page that only points at the earnings release filed as
    Exhibit 99.1. For such a filing the ``-index.htm`` page is read (one more index
    fetch, counted against the same attempt cap and deadline) and the ``EX-99.1``
    document, else the first ``EX-99*``, is resolved instead. If the page cannot be
    read, names no such exhibit, or the budget is spent, the primary document is kept.
    ``exhibit_index_fetcher`` is the injectable seam for that page.

    ``preflight_sink``, when given, collects one :class:`SecPreflightFailure` for
    every KNOWN candidate that never reached a network fetch (unresolvable
    identity, a malformed accession, an unsafe filename, no selectable primary
    document, or the preflight budget running out) — the class of failure that
    previously degraded silently. ``None`` (the default) is a pure no-op: nothing
    is collected and every return value is unchanged from before this parameter
    existed.
    """
    cap = max(0, int(max_documents or 0))
    if cap == 0 or not isinstance(filings, list):
        return []

    # The caller's CompanyContext.cik comes from ``company_snapshot`` →
    # ``company_identity``, which does NOT carry a ``cik`` key today — so it is
    # None for every issuer, including AAPL. Relying on it alone made this whole
    # path a SILENT no-op: it returned here with no log and no SourceGap, which
    # is indistinguishable from "never ran". ``resolve_sec_filer_cik`` derives the
    # filer identity from every available source and cross-checks them instead.
    padded, cik_failure = resolve_sec_filer_cik(cik, filings)
    if padded is None:
        log_event(
            _logger,
            "sec_filing_cik_unresolved",
            candidate_count=len(filings),
            reason=cik_failure,
        )
        if preflight_sink is not None:
            for filing in _sorted_filings(filings):
                failure = _preflight_failure(
                    filing,
                    cik=None,
                    accession=normalize_accession(filing.get("accession_number")),
                    failure_code=cik_failure or FAILURE_MISSING_CIK,
                )
                if failure is not None:
                    preflight_sink.append(failure)
        return []

    cfg = cfg or default_settings
    limiter = limiter or SecRateLimiter(cfg=cfg)
    fetch_index = index_fetcher or fetch_filing_index
    fetch_exhibit_index = exhibit_index_fetcher or fetch_filing_index_page
    # Hard ceiling on index fetches: enough headroom for filings whose index has
    # no usable body document, but never unbounded.
    max_attempts = cap * _INDEX_ATTEMPTS_PER_DOCUMENT

    resolved: list[SecFilingDocument] = []
    seen_urls: set[str] = set()
    skipped_no_accession = 0
    attempts = 0
    stopped_reason: str | None = None

    candidates = _sorted_filings(filings)
    for position, filing in enumerate(candidates):
        if len(resolved) >= cap:
            break
        if attempts >= max_attempts or (deadline is not None and clock() >= deadline):
            stopped_reason = "attempt_cap" if attempts >= max_attempts else "deadline"
            if preflight_sink is not None:
                # Every candidate not yet visited was KNOWN but skipped purely
                # because time/attempts ran out — record each one that carries
                # enough identity to be worth recording.
                for remaining in candidates[position:]:
                    remaining_accession = normalize_accession(
                        remaining.get("accession_number")
                    )
                    if remaining_accession is None:
                        continue
                    failure = _preflight_failure(
                        remaining,
                        cik=padded,
                        accession=remaining_accession,
                        failure_code=FAILURE_PREFLIGHT_BUDGET_EXHAUSTED,
                    )
                    if failure is not None:
                        preflight_sink.append(failure)
            break
        raw_accession = (
            filing.get("accession_number") if isinstance(filing, dict) else None
        )
        accession = normalize_accession(raw_accession)
        if accession is None:
            skipped_no_accession += 1
            # Only a genuinely malformed (present but unusable) accession is
            # worth an attempt record — a filing with NO accession field at all
            # was never a real candidate, matching the pre-existing behaviour of
            # ``skipped_no_accession`` (a count, not a record).
            if preflight_sink is not None and raw_accession:
                failure = _preflight_failure(
                    filing,
                    cik=padded,
                    accession=None,
                    failure_code=FAILURE_MALFORMED_ACCESSION,
                )
                if failure is not None:
                    preflight_sink.append(failure)
            continue

        attempts += 1
        try:
            entries = await fetch_index(
                padded,
                accession,
                cfg=cfg,
                client_factory=client_factory,
                limiter=limiter,
                resolver=resolver,
            )
        except Exception as exc:  # noqa: BLE001 - one bad filing never breaks the rest
            log_event(
                _logger,
                "sec_filing_index_error",
                reason=type(exc).__name__,
                accession=format_accession(accession),
            )
            continue

        form = base_form(filing.get("form_type")) or str(
            filing.get("form_type") or ""
        ).strip()
        entry = select_primary_document(
            entries,
            form_type=form,
            primary_document_hint=_hint_from_filing(filing),
            allow_exhibits=allow_exhibits,
        )
        if entry is None:
            if preflight_sink is not None:
                failure = _preflight_failure(
                    filing,
                    cik=padded,
                    accession=accession,
                    failure_code=FAILURE_NO_PRIMARY_FILING_DOCUMENT,
                )
                if failure is not None:
                    preflight_sink.append(failure)
            continue

        item_codes = filing_item_codes(filing)
        if is_results_release_8k(form, item_codes) and not _is_exhibit_entry(entry):
            if attempts < max_attempts and (deadline is None or clock() < deadline):
                attempts += 1
                try:
                    page_entries = await fetch_exhibit_index(
                        padded,
                        accession,
                        cfg=cfg,
                        client_factory=client_factory,
                        limiter=limiter,
                        resolver=resolver,
                    )
                except Exception as exc:  # noqa: BLE001 - degrade to the primary document
                    log_event(
                        _logger,
                        "sec_filing_index_page_error",
                        reason=type(exc).__name__,
                        accession=format_accession(accession),
                    )
                    page_entries = []
                exhibit = select_results_exhibit(page_entries)
                chosen = exhibit is not None and bool(
                    build_document_url(padded, accession, _entry_name(exhibit))
                )
                if chosen and exhibit is not None:
                    entry = exhibit
                log_event(
                    _logger,
                    "sec_results_exhibit_selected" if chosen else "sec_results_exhibit_not_found",
                    accession=format_accession(accession),
                )

        name = _entry_name(entry)
        url = build_document_url(padded, accession, name)
        if url is None:
            if preflight_sink is not None:
                failure = _preflight_failure(
                    filing,
                    cik=padded,
                    accession=accession,
                    failure_code=FAILURE_INVALID_SEC_URL,
                )
                if failure is not None:
                    preflight_sink.append(failure)
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        filed_date = str(filing.get("filed_date") or "") or None
        title = str(filing.get("title") or "").strip() or (
            f"SEC {form} filing" if form else "SEC filing"
        )
        resolved.append(
            SecFilingDocument(
                accession_number=format_accession(accession),
                form_type=form or "SEC filing",
                filing_date=filed_date,
                canonical_url=url,
                document_name=name,
                cik=padded,
                title=title,
                is_exhibit=_is_exhibit_entry(entry),
                item_codes=item_codes,
            )
        )

    log_event(
        _logger,
        "sec_filing_documents_resolved",
        cik=padded,
        candidate_count=len(filings),
        resolved_count=len(resolved),
        skipped_no_accession=skipped_no_accession,
        index_attempts=attempts,
        stopped_reason=stopped_reason,
    )
    return resolved


def _hint_from_filing(filing: dict[str, Any]) -> str | None:
    """Best-effort primary-document filename hint carried on a filing dict.

    The submissions feed exposes ``primaryDocument``; ``_filing_dict`` keeps only
    a document ``url``, whose last path segment is that same filename. Both are
    accepted, and an unsafe value is discarded rather than trusted.
    """
    for key in ("primary_document", "primaryDocument"):
        value = filing.get(key)
        if isinstance(value, str) and not _is_unsafe_filename(value):
            return value.strip()
    url = filing.get("url")
    if isinstance(url, str) and "/" in url:
        tail = url.split("?")[0].split("#")[0].rstrip("/").rsplit("/", 1)[-1]
        if tail and not _is_unsafe_filename(tail):
            return tail
    return None


def _blocked_result(url: str, reason: str) -> DocumentFetchResult:
    """An honest blocked fetch result (no body, no fabrication)."""
    return DocumentFetchResult(
        requested_url=url,
        blocked=True,
        error=reason,
        source_gaps=[
            SourceGap(
                connector_key=_CONNECTOR_KEY,
                source_id=_SOURCE_ID,
                gap_type=GapType.primary_filing_unavailable,
                severity=GapSeverity.info,
                message=(
                    f"SEC filing body was not fetched ({reason}); filing text is "
                    "not extracted."
                ),
                blocks_research_complete=False,
            )
        ],
    )


async def fetch_filing_body(
    doc: SecFilingDocument,
    *,
    cfg: Settings | None = None,
    resolver: Resolver | None = None,
    limiter: SecRateLimiter | None = None,
) -> DocumentFetchResult:
    """Fetch one resolved filing body through the bounded, SSRF-safe fetcher.

    Delegates every transport guard (https-only, allowlist, redirect re-checks,
    content-type gate, byte/timeout caps) to ``safe_fetch_document`` with
    ``resolve_ip=True`` so DNS rebinding is closed. Never raises.
    """
    cfg = cfg or default_settings
    url = getattr(doc, "canonical_url", "") or ""
    if not registrable_host_allowed(host_of(url), SEC_ALLOWED_DOMAINS):
        return _blocked_result(url, "host not an official SEC host")

    limiter = limiter or SecRateLimiter(cfg=cfg)
    await limiter.acquire()

    kwargs: dict[str, Any] = {
        "allowed_domains": SEC_ALLOWED_DOMAINS,
        "cfg": cfg,
        "resolve_ip": True,
    }
    if resolver is not None:
        kwargs["resolver"] = resolver
    try:
        return await safe_fetch_document(url, **kwargs)
    except Exception as exc:  # noqa: BLE001 - the fetcher should not raise; belt-and-braces
        return _blocked_result(url, f"fetch failed: {type(exc).__name__}")


__all__ = [
    "SEC_ALLOWED_DOMAINS",
    "SEC_ARCHIVES_BASE",
    "SEC_USER_AGENT",
    "STRATEGY_SEC_ACCESSION",
    "SUPPORTED_FORMS",
    "SecFilingDocument",
    "SecPreflightFailure",
    "SecRateLimiter",
    "base_form",
    "build_document_url",
    "build_filing_index_url",
    "cik_from_accession",
    "cik_from_archives_url",
    "INDEX_PAGE_MAX_BYTES",
    "build_filing_index_page_url",
    "doc_kind_for_form",
    "fetch_filing_index_page",
    "parse_filing_index_page",
    "select_results_exhibit",
    "filing_item_codes",
    "is_results_release_8k",
    "parse_item_codes",
    "fetch_filing_body",
    "fetch_filing_index",
    "format_accession",
    "is_supported_form",
    "normalize_accession",
    "normalize_cik",
    "parse_filing_index",
    "resolve_filing_documents",
    "resolve_sec_filer_cik",
    "is_exhibit_name",
    "select_primary_document",
]
