"""The 10-K business section as exposure evidence for US filers — V3.21.1b (ADR-115).

Live finding (staging run 2026-11-22, "US small-cap cybersecurity software companies"):
six US small caps passed the size check from their own SEC cover page, yet every one
stayed ``industry: unknown`` — the only exposure evidence screening reads is the issuer's
own website (from a recall lead's URL) or web search, and with search off and no usable
issuer URL nothing said what the companies do. Every SEC registrant says it itself, in
"Item 1. Business" of its annual report.

WHAT IT DOES
============
For an issuer with an SEC CIK and no verified affirmative exposure from its own site:

1. ``data.sec.gov/submissions/CIK##########.json`` → the newest 10-K (10-K405, 10-KT) or
   20-F in ``filings.recent`` (never an amendment: a 10-K/A is usually Part III only),
   filed within :data:`MAX_FILING_AGE_DAYS`;
2. its primary document from ``www.sec.gov/Archives/edgar/data/<cik>/<acc>/<doc>``, at most
   :data:`MAX_FILING_BYTES` (Item 1 sits near the top);
3. the "Item 1. Business" section (20-F: "Item 4. Information on the Company"), the one
   with the longest span to the next item heading so the table of contents is skipped,
   bounded to :data:`MAX_SECTION_CHARS`; without a heading, the text after the cover;
4. ``exposures_from_text`` on it, tier ``T1_primary_filing``, verified: the platform read
   the registrant's own filing itself.

BOUNDS
======
* Two GETs per issuer at most (submissions + one filing), both through
  ``safe_fetch_document`` pinned to ``data.sec.gov`` / ``www.sec.gov`` with the platform's
  declared User-Agent; no other host, ever.
* One result per CIK per UTC day is cached in-process (a failed read is not cached).
* Screening calls it once per screened issuer, so a run reads at most as many filings as
  it screens.
* ``V3_DISCOVERY_SEC_BUSINESS_EXPOSURE_ENABLED`` (default on) switches it.
"""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

SEC_DATA_HOST = "data.sec.gov"
SEC_ARCHIVE_HOST = "www.sec.gov"
#: Annual reports whose business section is read. 40-F is left out: its description of
#: the business sits in an exhibit (the AIF), not in the primary document.
BUSINESS_FORMS = ("10-K", "10-K405", "10-KT", "20-F")
MAX_FILING_BYTES = 3_000_000
MAX_SUBMISSIONS_BYTES = 2_000_000
#: The overview and the product description open the section; later pages (regulation,
#: human capital) mention terms like "cybersecurity" for every company. Bounded so only
#: what the company says it DOES is read.
MAX_SECTION_CHARS = 15_000
#: Without an item heading: this much text after the cover page.
FALLBACK_CHARS = 40_000
COVER_CHARS = 3_000
#: A section shorter than this between headings is a table-of-contents line.
MIN_SECTION_CHARS = 1_000
#: An annual report older than this describes another company-year (a lapsed filer).
MAX_FILING_AGE_DAYS = 800
MAX_CACHE_ENTRIES = 512

_ACCESSION = re.compile(r"\d{10}-\d{2}-\d{6}")
_PRIMARY_DOC = re.compile(r"[A-Za-z0-9._-]+\.(?:htm|html)")
_CIK = re.compile(r"[0-9]{1,10}")
_SEP = r"\s*[.:\-–—]?\s*"
_SECTIONS: dict[str, tuple[re.Pattern[str], re.Pattern[str]]] = {
    "10-K": (re.compile(rf"\bitem\s*1{_SEP}business\b", re.IGNORECASE),
             re.compile(r"\bitem\s*(?:1a|2)\b", re.IGNORECASE)),
    "20-F": (re.compile(rf"\bitem\s*4{_SEP}information\s+on\s+the\s+company\b",
                        re.IGNORECASE),
             re.compile(r"\bitem\s*(?:4a|5)\b", re.IGNORECASE)),
}


@dataclass(frozen=True)
class BusinessSection:
    """The registrant's own description of its business, as read from its filing."""

    cik: str
    form: str
    filed: str | None
    url: str
    text: str
    basis: str  # "item_heading" | "after_cover"


#: (cik, UTC day) -> the section, or None when the registrant has no usable annual report.
_CACHE: dict[tuple[str, str], BusinessSection | None] = {}


def _today() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_discovery_sec_business_exposure_enabled", True))


def issuer_cik(issuer: Any) -> str | None:
    """The SEC CIK the verified identity carries (its SEC directory row, else the issuer
    record), canonical without leading zeros; None when it has none."""
    for record in (getattr(issuer, "directory_listing", None),
                   getattr(issuer, "issuer", None)):
        value = (record or {}).get("cik") if isinstance(record, dict) else None
        if value is not None and _CIK.fullmatch(str(value).strip()):
            return str(int(str(value).strip()))
    return None


def latest_annual_document(
    data: Any, cik: str, *, today: date | None = None
) -> tuple[str, str, str | None] | None:
    """``(form, primary document URL, filing date)`` of the newest annual report in a
    submissions record, or None. Untrusted input; never raises."""
    try:
        recent = data["filings"]["recent"]
        forms = recent["form"]
        accessions = recent["accessionNumber"]
        documents = recent["primaryDocument"]
        dates = recent.get("filingDate") or []
    except (KeyError, TypeError):
        return None
    if not all(isinstance(x, list) for x in (forms, accessions, documents, dates)):
        return None
    now = today or datetime.now(timezone.utc).date()
    for i, (form, acc, doc) in enumerate(zip(forms, accessions, documents, strict=False)):
        if str(form) not in BUSINESS_FORMS:
            continue
        if not _ACCESSION.fullmatch(str(acc)) or not _PRIMARY_DOC.fullmatch(str(doc)):
            continue
        filed = str(dates[i])[:10] if i < len(dates) and dates[i] else None
        if filed:
            try:
                if (now - date.fromisoformat(filed)).days > MAX_FILING_AGE_DAYS:
                    return None  # the newest annual report is stale: nothing current
            except ValueError:
                filed = None
        url = (f"https://{SEC_ARCHIVE_HOST}/Archives/edgar/data/{int(cik)}/"
               f"{str(acc).replace('-', '')}/{doc}")
        return str(form), url, filed
    return None


def business_section(text: str, form: str) -> tuple[str, str] | None:
    """``(section text, basis)`` of a filing's plain text, or None when it is empty.

    The heading with the LONGEST span to the next item heading is the section itself; the
    table of contents names the same heading with a page number before the next item.
    """
    if not text:
        return None
    start_re, end_re = _SECTIONS["20-F" if form.startswith("20-F") else "10-K"]
    best: tuple[int, int] | None = None
    for match in start_re.finditer(text):
        end = end_re.search(text, match.end())
        stop = end.start() if end else len(text)
        if best is None or stop - match.end() > best[1] - best[0]:
            best = (match.end(), stop)
    if best is not None and best[1] - best[0] >= MIN_SECTION_CHARS:
        return text[best[0] : best[1]][:MAX_SECTION_CHARS].strip(), "item_heading"
    tail = text[COVER_CHARS : COVER_CHARS + FALLBACK_CHARS].strip()
    return (tail, "after_cover") if tail else None


async def _get(url: str, host: str, *, cfg: Any, fetcher: Any, max_bytes: int,
               json_body: bool = False) -> bytes | None:
    from app.services.sources.document_fetcher import safe_fetch_document

    try:
        result = await (fetcher or safe_fetch_document)(
            url, allowed_domains=(host,), cfg=cfg, resolve_ip=True,
            extra_text_content_types=("application/json",) if json_body else (),
            max_bytes=max_bytes,
        )
    except Exception:  # noqa: BLE001 - an unreadable filing is absent evidence
        return None
    if not getattr(result, "ok", False):
        return None
    content = getattr(result, "content", None)
    return bytes(content) if isinstance(content, (bytes, bytearray)) and content else None


async def sec_business_section(
    cik: str, *, cfg: Any = None, fetcher: Any = None, today: date | None = None
) -> tuple[BusinessSection | None, int]:
    """``(section or None, fetches made)`` for one registrant. Never raises."""
    if not _CIK.fullmatch(str(cik or "")):
        return None, 0
    cik = str(int(cik))
    key = (cik, _today())
    if key in _CACHE:
        return _CACHE[key], 0
    fetches = 1
    raw = await _get(f"https://{SEC_DATA_HOST}/submissions/CIK{int(cik):010d}.json",
                     SEC_DATA_HOST, cfg=cfg, fetcher=fetcher,
                     max_bytes=MAX_SUBMISSIONS_BYTES, json_body=True)
    if raw is None:
        return None, fetches  # not cached: a transient failure is no verdict
    try:
        data = json.loads(raw)
    except ValueError:
        return None, fetches
    picked = latest_annual_document(data, cik, today=today)
    if picked is None:
        _remember(key, None)
        return None, fetches
    form, url, filed = picked
    fetches += 1
    content = await _get(url, SEC_ARCHIVE_HOST, cfg=cfg, fetcher=fetcher,
                         max_bytes=MAX_FILING_BYTES)
    if content is None:
        return None, fetches
    from app.services.discovery.official_domains import _filing_texts

    # Off the event loop: an iXBRL 10-K is megabytes of markup.
    _windows, body = await asyncio.to_thread(_filing_texts, bytes(content[:MAX_FILING_BYTES]))
    found = business_section(body, form)
    section = (BusinessSection(cik=cik, form=form, filed=filed, url=url, text=found[0],
                               basis=found[1]) if found else None)
    _remember(key, section)
    return section, fetches


def _remember(key: tuple[str, str], value: BusinessSection | None) -> None:
    if len(_CACHE) >= MAX_CACHE_ENTRIES:
        _CACHE.clear()
    _CACHE[key] = value


async def sec_business_exposures(
    issuer: Any, terms: dict[str, re.Pattern[str]], *, cfg: Any, fetcher: Any = None,
) -> tuple[list[Any], int]:
    """``(exposures, fetches)`` from the issuer's latest annual-report business section.

    Nothing when the flag is off, there are no terms, or the identity has no CIK. Never
    raises.
    """
    if not terms or not enabled(cfg):
        return [], 0
    cik = issuer_cik(issuer)
    if cik is None:
        return [], 0
    try:
        section, fetches = await sec_business_section(cik, cfg=cfg, fetcher=fetcher)
    except Exception:  # noqa: BLE001 - belt and braces: screening never fails on this
        return [], 0
    if section is None:
        return [], fetches
    from app.services.discovery.screening import _safe, exposures_from_text

    passage = _safe(section.text, len(section.text) + 1) or ""
    return exposures_from_text(passage, terms, source_url=section.url,
                               source_tier="T1_primary_filing", verified=True), fetches


__all__ = [
    "BUSINESS_FORMS",
    "BusinessSection",
    "business_section",
    "enabled",
    "issuer_cik",
    "latest_annual_document",
    "sec_business_exposures",
    "sec_business_section",
]
