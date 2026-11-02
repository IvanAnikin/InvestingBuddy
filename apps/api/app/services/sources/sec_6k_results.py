"""Which Form 6-K is a foreign private issuer's latest interim RESULTS (ADR-077).

A 20-F / 40-F filer reports its quarter or half year on a Form 6-K, but most of its 6-Ks
are something else: share-buyback notices, AGM convocations and vote results, voting-right
totals, clinical-data and partnership press releases, prospectus supplements. EDGAR
records no item codes for a 6-K (ADR-068 decides an 8-K on Item 2.02), so a 6-K is judged
on what its own documents say, in this order:

1. **iXBRL financial statements.** A 6-K or 6-K/A whose 6-K / EX-99 document is Inline
   XBRL carries financial statements: a 6-K has no iXBRL cover-page requirement, so
   iXBRL in a 6-K is financial data.
2. **A notice by name.** A 6-K whose document file names say AGM, vote, meeting,
   prospectus, buyback, own shares or share capital is a notice, even when the name also
   says "results" (``6-ka_agm_vote_results.htm``).
3. **Results by description or headline.** The index page's document description, or
   the headline of the press release itself (the first characters of its text), names
   financial results, an interim / half-year / quarterly report, or a period next to
   "results" ("Financial Results for the Second Quarter 2026"). A headline that names a
   notice and no results is a notice.
4. **Results by file name.** ``clls_6-k_q2_2026.htm``, ``caq22026.htm``,
   ``form6-kquarterlyfilings.htm``.

Anything else is UNDECIDED, and an undecided 6-K is never taken: the wrong 6-K is worse
than none (a clinical press release read as "the latest interim report").

BOUNDED. At most :data:`MAX_INDEX_FETCHES` ``-index.htm`` pages and
:data:`MAX_HEADLINE_FETCHES` press-release heads per selection, through the existing
guarded, throttled SEC Archives leg (``fetch_filing_index_page`` /
``_fetch_sec_archive_bytes``): official host only, URLs from code constants and the
regulator's accession, the same User-Agent and rate limiter. A 6-K whose feed file name
already names a notice costs nothing. 6-Ks with no results signal in the regulator's feed
are examined only up to :data:`MAX_UNSIGNALLED_VISITS`, so a weekly-buyback filer
(Novo Nordisk) still reaches its interim report further down the list.

Never raises.
"""

from __future__ import annotations

import html
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.sources.sec_filing_documents import (
    INDEX_PAGE_MAX_BYTES,
    SecRateLimiter,
    _fetch_sec_archive_bytes,
    base_form,
    build_document_url,
    fetch_filing_index_page,
    normalize_accession,
    normalize_cik,
    select_results_exhibit,
)

#: The forms this selector reads. Only a foreign private issuer files a 6-K.
SIXK_FORMS: frozenset[str] = frozenset({"6-K", "6-K/A"})
#: ``-index.htm`` pages read per selection, at most.
MAX_INDEX_FETCHES = 6
#: Press-release heads read per selection, at most.
MAX_HEADLINE_FETCHES = 4
#: 6-Ks with no results signal in the feed that are examined before only signalled
#: ones are.
MAX_UNSIGNALLED_VISITS = 4
#: Characters of a document's text, after its tags are stripped, that count as its
#: headline. Long enough for a headline and its sub-heads, short enough that a body
#: sentence ("interim analysis expected in Q4") is not read as the title.
HEADLINE_WINDOW_CHARS = 400
#: Characters of a 6-K COVER page read when its press release is too large to fetch:
#: the exhibit list naming the release sits after ~2,000 characters of boilerplate.
COVER_WINDOW_CHARS = 6000
#: An EX-99 exhibit smaller than this is a stub (a cover note, a link list), not the
#: results document, when a larger EX-99 exhibit sits beside it.
MIN_RESULTS_EXHIBIT_BYTES = 10_000
#: A line longer than this is a paragraph, not a title (SAP's cover: a 600-character
#: forward-looking-statements paragraph mentions "financial results").
TITLE_MAX_CHARS = 200

BASIS_IXBRL = "ixbrl_financial_statements"
BASIS_DESCRIPTION = "document_description_names_results"
BASIS_HEADLINE = "headline_names_results"
BASIS_FILE_NAME = "file_name_names_results"

VERDICT_RESULTS = "results"
VERDICT_NOTICE = "notice"
VERDICT_UNDECIDED = "undecided"

# ── Text rules (bounded quantifiers only; inputs are short windows) ──────────── #

_PERIOD = (
    r"(?:q[1-4]|h[12]|(?:first|second|third|fourth|1st|2nd|3rd|4th)[- ]quarter|"
    r"quarter(?:ly)?|half[- ]year(?:ly)?|first half|second half|"
    r"(?:three|six|nine|twelve)[- ]months?|9m)"
)
#: Names financial results outright.
_FINANCIAL_TEXT_RE = re.compile(
    r"(?<!annual )\bfinancial (?:results|report)\b"
    r"|\b(?:interim|half[- ]year(?:ly)?|quarterly|semi[- ]annual)(?: financial)?"
    r" (?:report|statements?)\b"
    r"|\bearnings (?:release|report|results)\b"
)
#: A period next to "results" — results only when the text is not about a trial:
#: "topline results expected in the first quarter" is a clinical calendar.
_PERIOD_RESULTS_RE = re.compile(
    rf"\b{_PERIOD}\b[^.!?|]{{0,60}}\b(?:results|earnings)\b"
    rf"|\b(?:results|earnings)\b[^.!?|]{{0,60}}\b{_PERIOD}\b"
)
_CLINICAL_RE = re.compile(
    r"\b(?:top[- ]?line|trial|phase [1-3i]+|study|clinical|patients?|data|dosing)\b"
)
_MONTHS = (
    r"(?:january|february|march|april|may|june|july|august|september|october|november"
    r"|december|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)"
)
#: An ANNOUNCEMENT about results, not the results — vetoes a line even when it names a
#: results phrase (review of PR #313): "Cellectis to Report Third Quarter 2026
#: Financial Results … on November 5", "Invitation to conference call on Q3 2026
#: results", "Half-Year Report on the Liquidity Contract", "Availability of the
#: Half-Year Financial Report", "Notice of Q3 2026 earnings release date".
_ANNOUNCEMENT_TEXT_RE = re.compile(
    r"\b(?:to|will) (?:report|announce|release|publish)\b"
    r"|\binvitation\b|\bliquidity contract\b|\bavailability of\b"
    r"|\b(?:release|results|reporting) date\b"
    r"|\breport on (?:the )?(?:share buy[- ]?back|share repurchase|own shares|liquidity)"
)
#: A future date ("on November 5") is an announcement only in a line that does not
#: itself report: "Acme Reports Q3 Results; Call on November 5" still reports.
_FUTURE_DATE_RE = re.compile(rf"\bon {_MONTHS}\.? \d{{1,2}}\b")
_REPORTING_VERB_RE = re.compile(
    r"\b(?:reports|reported|announces|announced|posts|delivers|publishes)\b"
)
#: A notice that is ONLY a notice: a line naming a meeting, a vote, a buyback, voting
#: rights or a prospectus and NO results phrase. "Half-year results including share
#: buyback update" stays results; a conference call or webcast vetoes nothing.
_NOTICE_TEXT_RE = re.compile(
    r"\b(?:agm|egm|annual general meeting|general meeting|shareholders'? meeting"
    r"|voting rights|vote|voting|share capital|total number of (?:shares|voting)"
    r"|own shares|share (?:re)?purchase|share buy[- ]?back|buy[- ]?back|prospectus"
    r"|prospectus supplement|notice of)\b"
)
#: File names concatenate words ("form6-kagmdisclosure"), so these are substrings.
_NOTICE_NAME_TOKENS: tuple[str, ...] = (
    "agm", "vote", "voting", "meeting", "prospectus", "buyback", "repurchase",
    "ownshares", "own_shares", "sharecapital", "share_capital", "424b",
    "invitation", "liquidity",
)
#: A file name about a trial is never results evidence: BioNTech's
#: ``form6-ktpamtoplineresultsb.htm`` is a Phase 3 topline-results release. Substrings,
#: since names concatenate words — but "industrial…" is not a trial, and "data" is
#: not clinical ("q3-2026-supplementarydata.htm").
_CLINICAL_NAME_RE = re.compile(
    r"top[-_]?line|clinical|phase[-_]?(?:[1-3]|iii|ii|i(?![a-z]))|(?<!indus)trial|study"
)
#: A file name names results only through a PERIOD: a quarter or half, an interim or
#: quarterly report, or a fiscal / full year next to "results". A bare "results" or
#: "earnings" is not enough (review of PR #313, BioNTech).
_RESULTS_NAME_RE = re.compile(
    r"q[1-4](?![a-z])|(?<![a-z])h[12](?![a-z0-9])|half[-_ ]?year|first[-_ ]?half"
    r"|interim|quarter"
    r"|(?:fy|full[-_ ]?year)[-_ ]?\d{0,4}[-_ ]?(?:results|earnings)"
)
_WS_RE = re.compile(r"\s+")


def _norm(text: str | None) -> str:
    return _WS_RE.sub(" ", str(text or "").replace("\xa0", " ")).strip().lower()


def _names_results_phrase(norm: str) -> bool:
    if _FINANCIAL_TEXT_RE.search(norm):
        return True
    return bool(_PERIOD_RESULTS_RE.search(norm)) and not _CLINICAL_RE.search(norm)


def _is_announcement(norm: str) -> bool:
    if _ANNOUNCEMENT_TEXT_RE.search(norm):
        return True
    return bool(_FUTURE_DATE_RE.search(norm)) and not _REPORTING_VERB_RE.search(norm)


def text_names_results(text: str | None) -> bool:
    """Does a short text (a description, a headline line) name financial results?

    It must name a results phrase and must not be an ANNOUNCEMENT about results ("to
    report …", "invitation", "availability of", a future date in a line that reports
    nothing). A meeting, buyback or call mentioned beside a results phrase does not
    veto it. Pure."""
    norm = _norm(text)
    return _names_results_phrase(norm) and not _is_announcement(norm)


def text_names_notice(text: str | None) -> bool:
    """Is a short text a notice: an announcement about results, or a meeting / vote /
    buyback / voting-rights / prospectus notice that names no results? Pure."""
    norm = _norm(text)
    if _is_announcement(norm):
        return True
    return bool(_NOTICE_TEXT_RE.search(norm)) and not _names_results_phrase(norm)


def name_names_notice(name: str | None) -> bool:
    lowered = str(name or "").lower()
    return any(token in lowered for token in _NOTICE_NAME_TOKENS)


def name_names_results(name: str | None) -> bool:
    lowered = str(name or "").lower()
    if _CLINICAL_NAME_RE.search(lowered):
        return False
    return bool(_RESULTS_NAME_RE.search(lowered))


_BLOCK_TAGS: frozenset[str] = frozenset({
    "p", "div", "br", "tr", "td", "th", "li", "table", "h1", "h2", "h3", "h4", "h5", "h6",
})


def headline_lines(
    body: bytes | str | None, *, window: int = HEADLINE_WINDOW_CHARS
) -> list[str]:
    """The first lines of a document's visible text, up to ``window`` characters. A
    line is the text between block tags (``<p>``, ``<div>``, ``<br>`` …), so a headline
    stays apart from its sub-heads. Linear ``str.find`` walk, no regex over the page;
    text before ``<body`` (title, styles) is skipped; ``[]`` on anything unreadable."""
    if isinstance(body, bytes):
        body = body[:INDEX_PAGE_MAX_BYTES].decode("utf-8", errors="replace")
    if not isinstance(body, str) or not body:
        return []
    page = body[:INDEX_PAGE_MAX_BYTES]
    start = page.lower().find("<body")
    i, n = (start if start >= 0 else 0), len(page)
    lines: list[str] = []
    current: list[str] = []
    total = 0

    def _flush() -> None:
        nonlocal total
        text = _WS_RE.sub(" ", html.unescape("".join(current)).replace("\xa0", " ")).strip()
        current.clear()
        if text:
            lines.append(text)
            total += len(text)

    while i < n and total < window:
        lt = page.find("<", i)
        if lt < 0:
            current.append(page[i:])
            break
        current.append(page[i:lt])
        gt = page.find(">", lt + 1)
        if gt < 0:
            break
        name = page[lt + 1 : min(gt, lt + 12)].lstrip("/").split(" ", 1)[0]
        if name.split("/", 1)[0].strip().lower() in _BLOCK_TAGS:
            _flush()
        else:
            current.append(" ")
        i = gt + 1
    _flush()
    out: list[str] = []
    used = 0
    for line in lines:
        if used >= window:
            break
        out.append(line[: window - used])
        used += len(out[-1])
    return out


def headline_of(body: bytes | str | None, *, window: int = HEADLINE_WINDOW_CHARS) -> str:
    """:func:`headline_lines` joined with ``" | "`` — the text the rules read."""
    return " | ".join(headline_lines(body, window=window))


def title_lines(headline: str | None) -> list[str]:
    """The lines of a head that can be TITLES: at most :data:`TITLE_MAX_CHARS` long.
    A paragraph — forward-looking-statement boilerplate that warns about "financial
    results", a body sentence — is never a title. Pure."""
    return [
        line.strip()
        for line in str(headline or "").split(" | ")
        if line.strip() and len(line.strip()) <= TITLE_MAX_CHARS
    ]


def results_title(headline: str | None) -> str | None:
    """The one title line that names results — the document's own title for its
    period — or ``None``. Pure."""
    return next((line for line in title_lines(headline) if text_names_results(line)), None)


def _is_filing_document(entry: dict[str, Any]) -> bool:
    """The 6-K itself or one of its EX-99 exhibits — not a graphic, not the .txt dump."""
    etype = str(entry.get("type") or "").upper().replace(" ", "")
    name = str(entry.get("name") or "").lower()
    if not name.endswith((".htm", ".html")):
        return False
    return base_form(etype) == "6-K" or etype.startswith("EX-99")


def _size(entry: dict[str, Any]) -> int:
    raw = str(entry.get("size") or "").replace(",", "").strip()
    return int(raw) if raw.isdigit() else 0


def classify_6k(
    entries: list[dict[str, Any]],
    *,
    feed_document: str | None = None,
    headline: str | None = None,
) -> tuple[str, str | None]:
    """``(verdict, basis)`` for one 6-K from its own documents. Pure + total.

    ``entries`` are ``parse_filing_index_page`` rows; ``feed_document`` is the
    regulator feed's primary-document name; ``headline`` the press release's head when
    it was read. See the module docstring for the order of the rules.
    """
    docs = [e for e in entries if isinstance(e, dict) and _is_filing_document(e)]
    if any(e.get("ixbrl") for e in docs):
        return VERDICT_RESULTS, BASIS_IXBRL
    names = [str(e.get("name") or "") for e in docs]
    if feed_document:
        names.append(feed_document)
    descriptions = [str(e.get("description") or "") for e in docs]
    if any(text_names_results(d) for d in descriptions):
        return VERDICT_RESULTS, BASIS_DESCRIPTION
    # Line by line: a headline and its sub-heads are judged apart, so a clinical
    # sub-head cannot veto a "Financial Results" title, nor a "Q4" in one line pair
    # with "results" in another.
    lines = title_lines(headline)
    if any(text_names_results(line) for line in lines):
        return VERDICT_RESULTS, BASIS_HEADLINE
    if any(text_names_notice(d) for d in descriptions) or any(
        text_names_notice(line) for line in lines
    ):
        return VERDICT_NOTICE, None
    # Per document: one exhibit's notice-shaped name ("…earningscall…") never vetoes
    # another document that names results, and a name that is itself a notice
    # ("6-ka_agm_vote_results.htm", "h1-liquidity.htm") is no results evidence.
    if any(name_names_results(n) and not name_names_notice(n) for n in names):
        return VERDICT_RESULTS, BASIS_FILE_NAME
    if any(name_names_notice(n) for n in names):
        return VERDICT_NOTICE, None
    return VERDICT_UNDECIDED, None


def results_document(entries: list[dict[str, Any]], basis: str | None) -> dict[str, Any] | None:
    """The document of a results 6-K to read: its iXBRL statements when that was the
    evidence, else its press-release exhibit (``EX-99.1``, else the first ``EX-99*``,
    as ADR-068) — unless that exhibit is a stub under :data:`MIN_RESULTS_EXHIBIT_BYTES`
    and a larger ``EX-99*`` exists (Shell's Q3 2026 6-K: a 3 KB EX-99.1 beside the
    1.8 MB quarterly report filed as EX-99.2) — else the 6-K document itself. Pure."""
    docs = [e for e in entries if isinstance(e, dict) and _is_filing_document(e)]
    if basis == BASIS_IXBRL:
        ixbrl = [e for e in docs if e.get("ixbrl")]
        exhibits = [
            e for e in ixbrl
            if str(e.get("type") or "").upper().replace(" ", "").startswith("EX-99")
        ]
        if exhibits or ixbrl:
            return (exhibits or ixbrl)[0]
    exhibit = select_results_exhibit(docs)
    if exhibit is not None:
        if _size(exhibit) >= MIN_RESULTS_EXHIBIT_BYTES:
            return exhibit
        larger = [
            e for e in docs
            if str(e.get("type") or "").upper().replace(" ", "").startswith("EX-99")
            and _size(e) > _size(exhibit)
            and select_results_exhibit([e]) is not None
        ]
        return max(larger, key=_size) if larger else exhibit
    primary = [e for e in docs if base_form(str(e.get("type") or "")) == "6-K"]
    return primary[0] if primary else None


def _headline_document(entries: list[dict[str, Any]]) -> tuple[dict[str, Any], int] | None:
    """The document worth reading, and how many characters of it count as its head.

    The press-release exhibit's opening lines when it is small enough to read; else the
    6-K document itself, further in — a cover page whose exhibit list names the release
    ("Quarterly Statement dated October 21, 2026"), or, on a newswire 6-K, the
    announcement below the cover header. A document over ``INDEX_PAGE_MAX_BYTES`` is
    never fetched."""
    docs = [e for e in entries if isinstance(e, dict) and _is_filing_document(e)]
    exhibit = select_results_exhibit(docs)
    if exhibit is not None and 0 < _size(exhibit) <= INDEX_PAGE_MAX_BYTES:
        return exhibit, HEADLINE_WINDOW_CHARS
    cover = next((e for e in docs if base_form(str(e.get("type") or "")) == "6-K"), None)
    if cover is not None and 0 < _size(cover) <= INDEX_PAGE_MAX_BYTES:
        return cover, COVER_WINDOW_CHARS
    return None


def _feed_document_name(event: Any) -> str | None:
    url = str(getattr(event, "related_document_url", "") or "")
    tail = url.split("?")[0].rstrip("/").rsplit("/", 1)[-1]
    return tail or None


def _parse_day(raw: Any) -> date | None:
    try:
        return date.fromisoformat(str(raw or "")[:10])
    except ValueError:
        return None


def _filed_after(event: Any, day: date | None) -> bool:
    if day is None:
        return True
    filed = _parse_day(getattr(event, "filing_date", None))
    return filed is not None and filed > day


def period_end_of(event: Any) -> str | None:
    """The regulator's "period of report" when it reads as a reporting-period end — a
    date in the last week of a month, 10–200 days before the filing — else ``None``.

    For a newswire 6-K the period of report is the filing date and says nothing; for
    a financial-statements 6-K it is the balance-sheet date (ASML's 52-week quarters
    end on 06-28 / 09-27 / 03-29, hence "last week"). Never a guessed period: the date
    the issuer declared, or nothing.
    """
    report = _parse_day(getattr(event, "report_date", None))
    filed = _parse_day(getattr(event, "filing_date", None))
    if report is None or filed is None or report.day < 24:
        return None
    lag = (filed - report).days
    return report.isoformat() if 10 <= lag <= 200 else None


def feed_signals_results(event: Any) -> bool:
    """A results hint the regulator's feed gives for free: the primary document's file
    name, or a period-of-report that is a period end. Only orders the visits."""
    name = _feed_document_name(event)
    return bool(
        (name and name_names_results(name) and not name_names_notice(name))
        or period_end_of(event)
    )


PageFetcher = Callable[..., Awaitable[list[dict[str, Any]]]]
HeadFetcher = Callable[..., Awaitable[bytes | None]]


async def fetch_document_head(
    cik: str, accession: str, name: str, *, cfg: Any = None,
    limiter: SecRateLimiter | None = None, client_factory: Any = None,
) -> bytes | None:
    """One small filing document, over the guarded SEC Archives leg; ``None`` on any
    failure or when it is larger than ``INDEX_PAGE_MAX_BYTES`` (never half-read)."""
    url = build_document_url(cik, accession, name)
    if url is None:
        return None
    return await _fetch_sec_archive_bytes(
        url,
        accession,
        accept="text/html",
        cfg=cfg,
        client_factory=client_factory,
        limiter=limiter,
        resolver=None,
        max_bytes=INDEX_PAGE_MAX_BYTES,
        event_prefix="sec_6k_headline",
    )


@dataclass
class SixKSelection:
    """What the selector chose, and how much it looked at."""

    event: Any | None = None
    document_name: str | None = None
    basis: str | None = None
    title: str | None = None
    period_end: str | None = None
    listed: int = 0
    examined: int = 0
    index_fetches: int = 0
    headline_fetches: int = 0
    skipped_by_name: int = 0
    #: Index pages / heads that could not be read (an SEC outage, a refusal). A real
    #: ``-index.htm`` page always lists at least the complete-submission file, so an
    #: empty page is a failed read, never "a 6-K with no documents".
    index_unreadable: int = 0
    heads_unreadable: int = 0
    #: 6-Ks filed on or before the annual report's filing date, never considered.
    before_annual: int = 0
    since: str | None = None
    reason: str | None = None
    examined_log: list[dict[str, Any]] = field(default_factory=list)

    def selection_dict(self) -> dict[str, Any]:
        return {
            "listed_6k": self.listed,
            "examined": self.examined,
            "index_fetches": self.index_fetches,
            "headline_fetches": self.headline_fetches,
            "skipped_by_name": self.skipped_by_name,
            "index_unreadable": self.index_unreadable,
            "heads_unreadable": self.heads_unreadable,
            "before_annual": self.before_annual,
            "since": self.since,
        }

    def _no_pick_reason(self) -> str:
        base = (
            f"no results 6-K since the annual report of {self.since}"
            if self.since else "no results 6-K"
        )
        if self.listed == 0:
            return f"{base}: no 6-K filed after it" if self.since else "no 6-K filings listed"
        notes = [f"{self.skipped_by_name} skipped as notices by file name"]
        if self.index_unreadable:
            notes.append(f"{self.index_unreadable} index page(s) unreadable")
        if self.heads_unreadable:
            notes.append(f"{self.heads_unreadable} headline read(s) failed")
        return (
            f"{base} among the {self.examined} examined of {self.listed} listed "
            f"({'; '.join(notes)})"
        )


async def select_results_6k(
    events: list[Any],
    *,
    cik: str | int | None,
    cfg: Any = None,
    page_fetcher: PageFetcher | None = None,
    head_fetcher: HeadFetcher | None = None,
    limiter: SecRateLimiter | None = None,
    client_factory: Any = None,
    max_index_fetches: int = MAX_INDEX_FETCHES,
    max_headline_fetches: int = MAX_HEADLINE_FETCHES,
    max_unsignalled: int = MAX_UNSIGNALLED_VISITS,
    since: str | None = None,
) -> SixKSelection:
    """The newest 6-K among those examined that is a RESULTS release. Never raises.

    ``events`` are the regulator's recent filings (any forms, any order); only 6-K and
    6-K/A are read, newest first. ``since`` is the annual report's filing date: a 6-K
    filed on or before it is never the CURRENT period — Barrick's Q4 iXBRL 6-K of
    2026-02-05 predates its 40-F of 2026-02-27 and reports the same year.
    ``selection.event is None`` means nothing qualified, and ``selection.reason`` says
    why in words a reader can check.
    """
    since_day = _parse_day(since)
    all_sixks = [
        e for e in events if str(getattr(e, "form_type", "") or "").upper() in SIXK_FORMS
    ]
    sixks = sorted(
        (e for e in all_sixks if _filed_after(e, since_day)),
        key=lambda e: str(getattr(e, "filing_date", "") or ""),
        reverse=True,
    )
    out = SixKSelection(
        listed=len(sixks),
        before_annual=len(all_sixks) - len(sixks),
        since=since_day.isoformat() if since_day else None,
    )
    padded = normalize_cik(cik)
    if not sixks or padded is None:
        out.reason = out._no_pick_reason() if not sixks else "no usable CIK"
        return out
    fetch_page = page_fetcher or fetch_filing_index_page
    fetch_head = head_fetcher or fetch_document_head
    limiter = limiter or SecRateLimiter(cfg=cfg)
    unsignalled = 0
    for event in sixks:
        if out.index_fetches >= max_index_fetches:
            break
        accession = normalize_accession(getattr(event, "accession_number", None))
        if accession is None:
            continue
        feed_name = _feed_document_name(event)
        if feed_name and name_names_notice(feed_name):
            out.skipped_by_name += 1
            continue
        if not feed_signals_results(event):
            if unsignalled >= max_unsignalled:
                continue
            unsignalled += 1
        out.index_fetches += 1
        out.examined += 1
        try:
            entries = await fetch_page(
                padded, accession, cfg=cfg, limiter=limiter, client_factory=client_factory
            )
        except Exception:  # noqa: BLE001 - one unreadable index costs that 6-K only
            entries = []
        if not entries:
            # Unreadable or empty (a real page always lists the complete-submission
            # file). Never taken on its feed file name alone: which document carries
            # the results is unknown (Shell's 12 KB cover vs its 1.8 MB EX-99.2).
            out.index_unreadable += 1
            out.examined_log.append({
                "accession": getattr(event, "accession_number", None),
                "form": getattr(event, "form_type", None),
                "filing_date": getattr(event, "filing_date", None),
                "verdict": "index_unreadable",
                "basis": None,
            })
            continue
        verdict, basis = classify_6k(entries, feed_document=feed_name)
        headline: str | None = None
        # A notice by file name alone may still carry a results release in another
        # exhibit, so its headline is read too (bounded by the same cap).
        if verdict != VERDICT_RESULTS and out.headline_fetches < max_headline_fetches:
            target = _headline_document(entries)
            if target is not None:
                document, window = target
                out.headline_fetches += 1
                try:
                    body = await fetch_head(
                        padded, accession, str(document.get("name") or ""), cfg=cfg,
                        limiter=limiter, client_factory=client_factory,
                    )
                except Exception:  # noqa: BLE001 - an unreadable head decides nothing
                    body = None
                if body is None:
                    out.heads_unreadable += 1
                headline = headline_of(body, window=window) or None
                verdict, basis = classify_6k(
                    entries, feed_document=feed_name, headline=headline
                )
        out.examined_log.append({
            "accession": getattr(event, "accession_number", None),
            "form": getattr(event, "form_type", None),
            "filing_date": getattr(event, "filing_date", None),
            "verdict": verdict,
            "basis": basis,
        })
        if verdict != VERDICT_RESULTS:
            continue
        chosen = results_document(entries, basis)
        name = str((chosen or {}).get("name") or feed_name or "")
        if not name or build_document_url(padded, accession, name) is None:
            continue
        out.event = event
        out.document_name = name
        out.basis = basis
        out.title = results_title(headline)
        # The declared period end is reported only when the evidence is the financial
        # statements themselves: for those EDGAR's period of report IS the balance-sheet
        # date. A press release's "period of report" is its own date.
        out.period_end = period_end_of(event) if basis == BASIS_IXBRL else None
        return out
    out.reason = out._no_pick_reason()
    return out


__all__ = [
    "BASIS_DESCRIPTION",
    "BASIS_FILE_NAME",
    "BASIS_HEADLINE",
    "BASIS_IXBRL",
    "MAX_HEADLINE_FETCHES",
    "MAX_INDEX_FETCHES",
    "MAX_UNSIGNALLED_VISITS",
    "SIXK_FORMS",
    "SixKSelection",
    "classify_6k",
    "feed_signals_results",
    "headline_lines",
    "headline_of",
    "results_title",
    "period_end_of",
    "results_document",
    "select_results_6k",
    "text_names_notice",
    "text_names_results",
]
