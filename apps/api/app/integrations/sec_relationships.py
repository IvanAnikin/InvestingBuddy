"""SEC relationship documents — Exhibit 21 and Schedules 13D/13G — V3.20 slice 2.3.

Two regulated disclosures say who a US registrant owns and who owns it:

* **Exhibit 21** to the 10-K ("Subsidiaries of the registrant") — names, jurisdiction and
  sometimes the % held; a 20-F carries the same list as **Exhibit 8**. Nearly always an
  HTML table; a few are ``Name (Jurisdiction)`` lines. Children stay UNRESOLVED by
  default — a name in a list is not an identity.
* **Schedules 13D / 13G** — a holder above 5 % of a class files one. Since 2024-12-19
  they are structured XML (``SCHEDULE 13D`` / ``SCHEDULE 13G`` forms, ``primary_doc.xml``)
  and they appear in the ISSUER's own EDGAR submissions. 13D = an active holder, 13G = a
  passive one (index funds, banks, advisers). The older ``SC 13D`` / ``SC 13G`` HTML
  filings are counted and skipped: free text is not parsed here.

Every URL is built from code constants plus an accession and a file name that EDGAR's
own index returned and ``build_document_url`` re-validated. The fetcher is injected so
the unit suite runs on canned documents; the default throttles through the SEC
fair-access limiter. Pure parsers, no regex over unbounded input in a hot loop.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime
from html.parser import HTMLParser
from typing import Any

SEC_DATA_HOST = "data.sec.gov"
SUBSIDIARY_FORMS: dict[str, str] = {"10-K": "EX-21", "20-F": "EX-8"}
STRUCTURED_HOLDER_FORMS: frozenset[str] = frozenset(
    {"SCHEDULE 13D", "SCHEDULE 13D/A", "SCHEDULE 13G", "SCHEDULE 13G/A"}
)
LEGACY_HOLDER_FORMS: frozenset[str] = frozenset(
    {"SC 13D", "SC 13D/A", "SC 13G", "SC 13G/A"}
)
#: The newest structured holder filings read per run. EDGAR lists every amendment, so a
#: dozen covers the current holders of a small cap; the rest are older states of them.
MAX_HOLDER_FILINGS = 12
#: An Exhibit 21 of a large group lists thousands of entities. The first N are kept and
#: the truncation is stated.
MAX_SUBSIDIARIES = 500
MAX_DOCUMENT_BYTES = 3_000_000
MAX_XML_BYTES = 1_000_000
#: A 10-K's iXBRL body is typically 2–15 MB (``inline_xbrl.MAX_INLINE_XBRL_BYTES`` = 40 MB).
MAX_ANNUAL_BODY_BYTES = 40_000_000

#: ``(url, accept)`` → ``(status, body or None)``. Raises on a transport failure.
SecFetch = Callable[[str, str], Awaitable[tuple[int, bytes | None]]]


# ── Results ─────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class SubsidiaryRow:
    name: str
    jurisdiction: str | None = None
    #: The % the registrant holds, when the exhibit states it.
    ownership_pct: float | None = None
    #: The section heading the row sat under ("Joint Ventures"), when there was one.
    section: str | None = None
    excerpt: str = ""


@dataclass
class SubsidiaryList:
    form: str
    accession: str
    filing_date: date | None
    exhibit_type: str
    source_url: str
    #: ``parsed`` | ``empty`` (the exhibit was read and no row was recognised).
    state: str = "parsed"
    rows: list[SubsidiaryRow] = field(default_factory=list)
    truncated: bool = False


@dataclass(frozen=True)
class ReportingPerson:
    name: str
    cik: str | None
    percent_of_class: float | None
    person_type: str | None = None


@dataclass
class HolderFiling:
    form: str
    accession: str
    filing_date: date | None
    source_url: str
    filer_cik: str | None
    event_date: date | None
    class_title: str | None
    persons: list[ReportingPerson] = field(default_factory=list)

    @property
    def schedule(self) -> str:
        return "13D" if "13D" in self.form else "13G"

    @property
    def lead(self) -> ReportingPerson | None:
        """The reporting person with the largest stake — joint filers report ONE holding."""
        ranked = [p for p in self.persons if p.percent_of_class is not None]
        if not ranked:
            return None
        return max(ranked, key=lambda p: p.percent_of_class or 0.0)

    @property
    def holder_key(self) -> str:
        lead = self.lead
        return (self.filer_cik or (lead.cik if lead else None)
                or (lead.name.casefold() if lead else self.accession))


@dataclass
class SecRelationshipReport:
    cik: str
    #: The newest 10-K / 20-F: ``{form, accession, filing_date, primary_document}``.
    annual: dict[str, Any] | None = None
    subsidiaries: SubsidiaryList | None = None
    holders: list[HolderFiling] = field(default_factory=list)
    legacy_holder_filings_skipped: int = 0
    notes: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        def clean(value: Any) -> Any:
            if isinstance(value, (date, datetime)):
                return value.isoformat()
            if isinstance(value, dict):
                return {k: clean(v) for k, v in value.items()}
            if isinstance(value, list):
                return [clean(v) for v in value]
            return value

        return clean(asdict(self))


# ── Submissions ─────────────────────────────────────────────────────────── #


def submissions_url(cik: str) -> str:
    return f"https://{SEC_DATA_HOST}/submissions/CIK{int(cik):010d}.json"


def _iso(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def recent_filings(submissions: Any) -> list[dict[str, Any]]:
    """EDGAR's ``filings.recent`` columns → rows, newest first. Pure + total."""
    recent = (((submissions or {}).get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    rows: list[dict[str, Any]] = []
    for i, form in enumerate(forms):
        def col(name: str, i: int = i) -> Any:
            values = recent.get(name) or []
            return values[i] if i < len(values) else None

        rows.append({
            "form": str(form or "").strip().upper(),
            "accession": str(col("accessionNumber") or ""),
            "filing_date": _iso(col("filingDate")),
            "primary_document": col("primaryDocument"),
        })
    rows.sort(key=lambda r: r["filing_date"] or date.min, reverse=True)
    return rows


# ── Exhibit 21 ──────────────────────────────────────────────────────────── #

_HEADER_WORDS = (
    "name of", "subsidiar", "jurisdiction", "incorporat", "organization", "organisation",
    "percentage", "ownership", "state or", "country", "domicile", "entity name", "% owned",
)
_PCT_RE = re.compile(r"^\s*(\d{1,3}(?:\.\d+)?)\s*%?\s*$")
_PAREN_LINE_RE = re.compile(r"^(?P<name>[^()]{3,200}?)\s*\((?P<juris>[^()]{2,80})\)\s*$")
_WS = re.compile(r"\s+")


class _TableReader(HTMLParser):
    """Rows of every table as cell texts, plus the text lines outside tables."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[str]] = []
        self.lines: list[str] = []
        self._depth = 0
        self._row: list[str] | None = None
        self._cell: list[str] | None = None
        self._line: list[str] = []

    def handle_starttag(self, tag: str, attrs: Any) -> None:
        if tag == "table":
            self._depth += 1
        elif tag == "tr" and self._depth:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("p", "div", "br", "li") and not self._depth:
            self._flush_line()

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self._row is not None and self._cell is not None:
            self._row.append(_WS.sub(" ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            if any(c for c in self._row):
                self.rows.append([c for c in self._row])
            self._row = None
        elif tag == "table" and self._depth:
            self._depth -= 1
        elif tag in ("p", "div", "li") and not self._depth:
            self._flush_line()

    def handle_data(self, data: str) -> None:
        if self._cell is not None:
            self._cell.append(data)
        elif not self._depth:
            self._line.append(data)

    def _flush_line(self) -> None:
        text = _WS.sub(" ", "".join(self._line)).strip()
        if text:
            self.lines.append(text)
        self._line = []

    def close(self) -> None:
        super().close()
        self._flush_line()


def _is_header(cells: list[str]) -> bool:
    joined = " ".join(cells).casefold()
    return any(w in joined for w in _HEADER_WORDS) and not any(
        _PCT_RE.match(c) for c in cells
    )


def _pct(text: str) -> float | None:
    m = _PCT_RE.match(text or "")
    if not m:
        return None
    value = float(m.group(1))
    return value if 0.0 <= value <= 100.0 else None


def parse_subsidiary_exhibit(
    body: bytes | str | None, *, registrant_name: str | None = None
) -> tuple[list[SubsidiaryRow], bool]:
    """Exhibit 21 (or 20-F Exhibit 8) → ``(rows, truncated)``. Pure + total.

    Table rows: first non-empty cell = name, next = jurisdiction, a cell that is only a
    percentage = the ownership. A one-cell row is a section heading ("Joint Ventures").
    Header rows and the registrant itself are skipped. With no table rows, falls back to
    ``Name (Jurisdiction)`` lines.
    """
    if body is None:
        return [], False
    if isinstance(body, bytes):
        if len(body) > MAX_DOCUMENT_BYTES:
            body = body[:MAX_DOCUMENT_BYTES]
        body = body.decode("utf-8", errors="replace")
    reader = _TableReader()
    try:
        reader.feed(body)
        reader.close()
    except Exception:  # noqa: BLE001 - a malformed page yields what was read
        pass
    own = _WS.sub(" ", (registrant_name or "").casefold()).strip(" .,")
    rows: list[SubsidiaryRow] = []
    seen: set[str] = set()
    section: str | None = None
    for cells in reader.rows:
        texts = [c for c in cells if c]
        if not texts:
            continue
        if len(texts) == 1:
            if not _is_header(texts) and len(texts[0]) <= 80:
                section = texts[0]
            continue
        if _is_header(texts):
            continue
        name = texts[0]
        rest = texts[1:]
        pct = next((p for p in (_pct(c) for c in rest) if p is not None), None)
        juris = next((c for c in rest if _pct(c) is None), None)
        _add(rows, seen, own, name, juris, pct, section, " | ".join(texts))
    if not rows:
        for line in reader.lines:
            m = _PAREN_LINE_RE.match(line)
            if m:
                _add(rows, seen, own, m.group("name"), m.group("juris"), None, None, line)
    truncated = len(rows) > MAX_SUBSIDIARIES
    return rows[:MAX_SUBSIDIARIES], truncated


def _add(rows: list[SubsidiaryRow], seen: set[str], own: str, name: str,
         juris: str | None, pct: float | None, section: str | None, excerpt: str) -> None:
    clean = _WS.sub(" ", name).strip(" ,;:*")
    if len(clean) < 2 or len(clean) > 300:
        return
    folded = clean.casefold().strip(" .,")
    if own and folded == own:
        return  # the registrant listing itself
    key = f"{folded}|{(juris or '').casefold()}"
    if key in seen:
        return
    seen.add(key)
    rows.append(SubsidiaryRow(
        name=clean, jurisdiction=(juris or None) and juris.strip()[:120],
        ownership_pct=pct, section=section, excerpt=excerpt[:500],
    ))


# ── Schedules 13D / 13G ─────────────────────────────────────────────────── #


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _first(root: ET.Element, name: str) -> str | None:
    for el in root.iter():
        if _local(el.tag) == name and (el.text or "").strip():
            return (el.text or "").strip()
    return None


def _float(text: str | None) -> float | None:
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 100.0 else None


def _us_date(text: str | None) -> date | None:
    raw = (text or "").strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(raw, fmt).date()
        except ValueError:
            continue
    return None


def parse_schedule13(body: bytes | None) -> dict[str, Any] | None:
    """A ``primary_doc.xml`` of SCHEDULE 13D / 13G → its holding. None when unreadable.

    Refuses a document that declares a DTD or an entity (no expansion attacks) or is over
    ``MAX_XML_BYTES``. 13G: ``coverPageHeaderReportingPersonDetails`` / ``classPercent`` /
    ``eventDateRequiresFilingThisStatement``. 13D: ``reportingPersonInfo`` /
    ``percentOfClass`` / ``dateOfEvent``.
    """
    if not body or len(body) > MAX_XML_BYTES:
        return None
    head = body[:4096].upper()
    if b"<!DOCTYPE" in head or b"<!ENTITY" in body.upper():
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    persons: list[ReportingPerson] = []
    for el in root.iter():
        if _local(el.tag) not in ("coverPageHeaderReportingPersonDetails", "reportingPersonInfo"):
            continue
        fields = {_local(c.tag): (c.text or "").strip() for c in el}
        name = fields.get("reportingPersonName")
        if not name:
            continue
        pct = _float(fields.get("classPercent") or fields.get("percentOfClass"))
        persons.append(ReportingPerson(
            name=_WS.sub(" ", name)[:300],
            cik=(fields.get("reportingPersonCIK") or None),
            percent_of_class=pct,
            person_type=(fields.get("typeOfReportingPerson") or None),
        ))
    if not persons:
        return None
    return {
        "submission_type": _first(root, "submissionType"),
        "filer_cik": _first(root, "cik"),
        "issuer_cik": _first(root, "issuerCik") or _first(root, "issuerCIK"),
        "class_title": _first(root, "securitiesClassTitle"),
        "event_date": _us_date(
            _first(root, "eventDateRequiresFilingThisStatement") or _first(root, "dateOfEvent")
        ),
        "persons": persons,
    }


# ── The lookup ──────────────────────────────────────────────────────────── #


async def _default_fetch(url: str, accept: str) -> tuple[int, bytes | None]:  # pragma: no cover
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent
    from app.services.sources.sec_filing_documents import SecRateLimiter

    await SecRateLimiter().acquire()
    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        resp = await client.get(
            url, headers={USER_AGENT_HEADER: research_user_agent(), "Accept": accept}
        )
    if resp.status_code != 200:
        return resp.status_code, None
    return 200, resp.content[:MAX_DOCUMENT_BYTES]


async def _default_large_fetch(  # pragma: no cover - network
    url: str, accept: str
) -> tuple[int, bytes | None]:
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent
    from app.services.sources.sec_filing_documents import SecRateLimiter

    await SecRateLimiter().acquire()
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=False) as client:
        async with client.stream(
            "GET", url, headers={USER_AGENT_HEADER: research_user_agent(), "Accept": accept}
        ) as resp:
            if resp.status_code != 200:
                return resp.status_code, None
            chunks: list[bytes] = []
            size = 0
            async for chunk in resp.aiter_bytes():
                size += len(chunk)
                if size > MAX_ANNUAL_BODY_BYTES:
                    return 413, None  # refused whole, never parsed half
                chunks.append(chunk)
    return 200, b"".join(chunks)


@dataclass
class AnnualInlineXbrl:
    url: str | None
    document: Any = None
    error: str | None = None


async def fetch_annual_inline_xbrl(
    cik: str, annual: dict[str, Any], *, fetch: SecFetch | None = None
) -> AnnualInlineXbrl:
    """The newest annual report's primary document, parsed as inline XBRL. Never raises.

    The URL is built by ``build_document_url`` from EDGAR's own ``primaryDocument``.
    Parsing runs in a worker thread (a 10-K body is megabytes).
    """
    import asyncio

    from app.services.sources.inline_xbrl import parse_inline_xbrl
    from app.services.sources.sec_filing_documents import build_document_url

    url = build_document_url(cik, annual.get("accession"), annual.get("primary_document"))
    if url is None:
        return AnnualInlineXbrl(
            url=None, error="primary document name is not a safe Archives name")
    try:
        status, body = await (fetch or _default_large_fetch)(url, "text/html")
    except Exception as exc:  # noqa: BLE001
        return AnnualInlineXbrl(url=url, error=f"annual report unread ({type(exc).__name__})")
    if status != 200 or not body:
        return AnnualInlineXbrl(url=url, error=f"annual report returned HTTP {status}")
    try:
        document = await asyncio.to_thread(parse_inline_xbrl, body)
    except Exception as exc:  # noqa: BLE001
        return AnnualInlineXbrl(url=url, error=f"inline XBRL unreadable ({type(exc).__name__})")
    if getattr(document, "refused", None):
        return AnnualInlineXbrl(url=url, error=f"inline XBRL refused ({document.refused})")
    return AnnualInlineXbrl(url=url, document=document)


async def fetch_sec_relationships(
    cik: str,
    *,
    registrant_name: str | None = None,
    fetch: SecFetch | None = None,
    max_holder_filings: int = MAX_HOLDER_FILINGS,
) -> SecRelationshipReport:
    """Read the registrant's subsidiary exhibit and its structured 13D/13G. Never raises."""
    import json

    from app.services.sources.sec_filing_documents import (
        build_document_url,
        build_filing_index_page_url,
        normalize_cik,
        parse_filing_index_page,
    )

    get = fetch or _default_fetch
    padded = normalize_cik(cik)
    report = SecRelationshipReport(cik=padded or str(cik))
    if padded is None:
        report.errors.append("no usable CIK")
        return report
    try:
        status, body = await get(submissions_url(padded), "application/json")
        if status != 200 or not body:
            report.errors.append(f"EDGAR submissions returned HTTP {status}")
            return report
        filings = recent_filings(json.loads(body))
    except Exception as exc:  # noqa: BLE001
        report.errors.append(f"EDGAR submissions unreadable ({type(exc).__name__})")
        return report

    # Subsidiaries — the newest annual report's exhibit.
    annual = next((f for f in filings if f["form"] in SUBSIDIARY_FORMS), None)
    if annual is not None:
        report.annual = dict(annual)
    if annual is None:
        report.notes.append("no 10-K or 20-F in the registrant's recent filings")
    else:
        try:
            await _read_subsidiaries(report, padded, annual, registrant_name, get,
                                     build_filing_index_page_url, parse_filing_index_page,
                                     build_document_url)
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"subsidiary exhibit unread ({type(exc).__name__})")

    # Holders — the newest structured filing per holder.
    report.legacy_holder_filings_skipped = sum(
        1 for f in filings if f["form"] in LEGACY_HOLDER_FORMS
    )
    latest: dict[str, HolderFiling] = {}
    for filing in [f for f in filings if f["form"] in STRUCTURED_HOLDER_FORMS][
        :max_holder_filings
    ]:
        url = build_document_url(padded, filing["accession"], "primary_doc.xml")
        if url is None:
            continue
        try:
            status, body = await get(url, "application/xml")
        except Exception as exc:  # noqa: BLE001
            report.errors.append(f"{filing['form']} {filing['accession']} unread "
                                 f"({type(exc).__name__})")
            continue
        parsed = parse_schedule13(body) if status == 200 else None
        if parsed is None:
            report.errors.append(f"{filing['form']} {filing['accession']} not readable XML")
            continue
        issuer = parsed.get("issuer_cik")
        if issuer and normalize_cik(issuer) != padded:
            report.notes.append(f"{filing['accession']} is about another issuer; skipped")
            continue
        holding = HolderFiling(
            form=filing["form"], accession=filing["accession"],
            filing_date=filing["filing_date"], source_url=url,
            filer_cik=normalize_cik(parsed.get("filer_cik")),
            event_date=parsed.get("event_date"), class_title=parsed.get("class_title"),
            persons=list(parsed["persons"]),
        )
        # Newest first, so the first filing seen per holder is its current state.
        latest.setdefault(holding.holder_key, holding)
    report.holders = list(latest.values())
    if report.legacy_holder_filings_skipped:
        report.notes.append(
            f"{report.legacy_holder_filings_skipped} pre-2025 SC 13D/13G (free-text) "
            "filings were not read"
        )
    return report


async def _read_subsidiaries(report: SecRelationshipReport, cik: str, annual: dict[str, Any],
                             registrant_name: str | None, get: SecFetch,
                             index_url: Any, parse_index: Any, doc_url: Any) -> None:
    form = annual["form"]
    wanted = SUBSIDIARY_FORMS[form]
    url = index_url(cik, annual["accession"])
    if url is None:
        report.errors.append("annual report index URL could not be built")
        return
    status, body = await get(url, "text/html")
    if status != 200 or not body:
        report.errors.append(f"{form} index returned HTTP {status}")
        return
    entries = parse_index(body)
    exhibit = next(
        (e for e in entries
         if str(e.get("type", "")).upper().replace(" ", "").startswith(wanted)), None
    )
    if exhibit is None:
        report.notes.append(f"the {form} filed {annual['filing_date']} has no {wanted} exhibit")
        return
    url = doc_url(cik, annual["accession"], exhibit.get("name"))
    if url is None:
        report.errors.append(f"{wanted} file name is not a safe Archives name")
        return
    status, body = await get(url, "text/html")
    if status != 200 or not body:
        report.errors.append(f"{wanted} returned HTTP {status}")
        return
    rows, truncated = parse_subsidiary_exhibit(body, registrant_name=registrant_name)
    report.subsidiaries = SubsidiaryList(
        form=form, accession=annual["accession"], filing_date=annual["filing_date"],
        exhibit_type=str(exhibit.get("type") or wanted), source_url=url,
        state="parsed" if rows else "empty", rows=rows, truncated=truncated,
    )
    if truncated:
        report.notes.append(f"{wanted} lists more than {MAX_SUBSIDIARIES} entities; "
                            f"the first {MAX_SUBSIDIARIES} were kept")


__all__ = [
    "AnnualInlineXbrl",
    "LEGACY_HOLDER_FORMS",
    "fetch_annual_inline_xbrl",
    "MAX_HOLDER_FILINGS",
    "MAX_SUBSIDIARIES",
    "STRUCTURED_HOLDER_FORMS",
    "SUBSIDIARY_FORMS",
    "HolderFiling",
    "ReportingPerson",
    "SecFetch",
    "SecRelationshipReport",
    "SubsidiaryList",
    "SubsidiaryRow",
    "fetch_sec_relationships",
    "parse_schedule13",
    "parse_subsidiary_exhibit",
    "recent_filings",
    "submissions_url",
]
