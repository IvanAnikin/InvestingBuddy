"""The reporting period a DOCUMENT itself covers — current-period acceptance.

``financial_period`` models the period of one extracted VALUE. This module
answers the prior question: *what period is this document about?*

It exists because of a confirmed, dangerous live failure. Richemont's quarterly
sales release for the quarter ended 30 June 2026 states its headline figure as
plain prose — "Group sales at € 6.3 billion" — with no year in the sentence at
all. ``extracted_fact_validator`` then fell back to the document's DOMINANT
explicit year, which in that release is the bare token ``2026``, and stamped the
quarter's sales as **annual 2026** revenue. Beside the FY2026 annual Group
revenue of € 22.4 billion, that is the ``INTERIM_AS_ANNUAL`` contradiction in
its worst form: a three-month figure competing for the full-year slot.

The fallback was never wrong to exist — a real report states its period once and
then omits it from later sentences. It was wrong to be period-TYPE blind. A
document that says, in its own title and its own headings, that it covers one
quarter cannot supply an ANNUAL period to anything.

Deliberately a PURE VALUE module: it reads text somebody else already fetched,
and it decides nothing about facts, selection or promotion.

Fail-closed throughout. Every rule reads the document's OWN words — its title,
its URL as the issuer published it, its leading headings. Nothing is derived
from a fiscal calendar, a publication date or a company registry, because a
document that does not state its period simply has none here (``UNKNOWN``), and
an unknown document period changes no existing behaviour.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.sources.financial_period import (
    PERIOD_TYPE_HALF,
    PERIOD_TYPE_QUARTER,
    UNKNOWN_PERIOD,
    ReportingPeriod,
)

# ── How a period was determined (closed vocabulary, for the audit trail) ──── #

BASIS_UNKNOWN = "unknown"
#: "FY27 Q1" / "Q1 FY2027" — the issuer's own combined fiscal-year label.
BASIS_FISCAL_LABEL = "fiscal_period_label"
#: "Q2 2026" / "H1 2026" — an unambiguous four-digit period label.
BASIS_PERIOD_LABEL = "period_label"
#: "for its first quarter ended 30 June 2026" — a period-end sentence.
BASIS_PERIOD_END_PHRASE = "period_end_phrase"

#: Hard bound on how much document text is scanned. The period is stated in the
#: title and the first headings; scanning a whole annual report would only add
#: chances to read a comparative year as the document's own.
_MAX_SCAN_CHARS = 4_000
_MAX_HEADINGS = 12

_MIN_YEAR = 1990
_MAX_YEAR = 2100

_WS_RE = re.compile(r"\s+")
_SEP_RE = re.compile(r"[^a-z0-9]+")

# ── Rule 1 — the issuer's own combined fiscal-period label ────────────────── #
#
# "fy27 q1", "q1 fy27", "fy2027 q1", "h1 fy26". A two-digit fiscal year is
# accepted ONLY when it sits immediately beside a quarter/half token: on its own
# it is the guess ``financial_period.parse_period`` rightly refuses, but "FY27
# Q1" in an issuer's own filename is that issuer stating which fiscal year its
# first quarter belongs to, and no other reading of the pair exists.
_FISCAL_FIRST_RE = re.compile(
    r"\bfy\s*(?P<year>\d{2}|\d{4})\s*(?P<kind>[qh])\s*(?P<ordinal>[1-4])\b"
)
_PERIOD_FIRST_RE = re.compile(
    r"\b(?P<kind>[qh])\s*(?P<ordinal>[1-4])\s*fy\s*(?P<year>\d{2}|\d{4})\b"
)

# ── Rule 2 — an unambiguous four-digit period label ───────────────────────── #
_LABEL_RE = re.compile(
    r"\b(?:(?P<kind1>[qh])(?P<ord1>[1-4])\s*(?:fy\s*)?(?P<y1>(?:19|20)\d{2})"
    r"|(?P<y2>(?:19|20)\d{2})\s*(?P<kind2>[qh])(?P<ord2>[1-4]))\b"
)

# ── Rule 3 — a period-end sentence ────────────────────────────────────────── #
#
# The ordinal words a real release uses. "nine months" is deliberately ABSENT:
# a nine-month cumulative period is neither a quarter nor a half, and this
# module has no representation for it — inventing one would be exactly the kind
# of quiet mismapping it exists to prevent.
_ORDINAL_WORDS: tuple[tuple[str, str, int], ...] = (
    ("first quarter", PERIOD_TYPE_QUARTER, 1),
    ("1st quarter", PERIOD_TYPE_QUARTER, 1),
    ("second quarter", PERIOD_TYPE_QUARTER, 2),
    ("2nd quarter", PERIOD_TYPE_QUARTER, 2),
    ("third quarter", PERIOD_TYPE_QUARTER, 3),
    ("3rd quarter", PERIOD_TYPE_QUARTER, 3),
    ("fourth quarter", PERIOD_TYPE_QUARTER, 4),
    ("4th quarter", PERIOD_TYPE_QUARTER, 4),
    ("first half", PERIOD_TYPE_HALF, 1),
    ("first six months", PERIOD_TYPE_HALF, 1),
    ("six months", PERIOD_TYPE_HALF, 1),
    ("six month", PERIOD_TYPE_HALF, 1),
    ("half year", PERIOD_TYPE_HALF, 1),
    ("half-year", PERIOD_TYPE_HALF, 1),
    ("halfyear", PERIOD_TYPE_HALF, 1),
    ("second half", PERIOD_TYPE_HALF, 2),
)
#: The year must belong to the SAME sentence-sized window as the period phrase,
#: so an unrelated year elsewhere on the page can never date the document.
_PERIOD_END_WINDOW = 120
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")


@dataclass(frozen=True)
class DocumentPeriod:
    """The period a document covers, with how that was decided."""

    period: ReportingPeriod = UNKNOWN_PERIOD
    basis: str = BASIS_UNKNOWN
    #: The document's own phrase, kept verbatim so a reader can check the call.
    evidence: str | None = None

    @property
    def is_known(self) -> bool:
        return not self.period.is_unknown

    @property
    def is_interim(self) -> bool:
        """True when the document covers PART of a year.

        This is the property the whole module exists for: it is what forbids an
        annual period being inferred for a value the document never dated.
        """
        return self.period.is_interim

    def label(self) -> str:
        return self.period.label()


UNKNOWN_DOCUMENT_PERIOD = DocumentPeriod()


def _expand_year(raw: str) -> int | None:
    year = int(raw)
    if len(raw) == 2:
        year += 2000
    return year if _MIN_YEAR <= year <= _MAX_YEAR else None


def _period_of(kind: str, ordinal: int, year: int) -> ReportingPeriod | None:
    if kind == "q":
        return ReportingPeriod(PERIOD_TYPE_QUARTER, year, ordinal, f"Q{ordinal} {year}")
    if kind == "h" and ordinal in (1, 2):
        return ReportingPeriod(PERIOD_TYPE_HALF, year, ordinal, f"H{ordinal} {year}")
    return None


def _normalise(text: str) -> str:
    """Lower-case, separator-flattened text — the form every rule reads.

    A URL slug (``…-fy27-q1-sales-en.pdf``) and a printed heading ("FY27 Q1
    Sales") reduce to the same words, so one keyword table covers both.
    """
    lowered = _SEP_RE.sub(" ", (text or "").lower())
    return _WS_RE.sub(" ", lowered).strip()


def _match_fiscal_label(text: str) -> DocumentPeriod | None:
    for regex in (_FISCAL_FIRST_RE, _PERIOD_FIRST_RE):
        match = regex.search(text)
        if match is None:
            continue
        year = _expand_year(match.group("year"))
        if year is None:
            continue
        period = _period_of(match.group("kind"), int(match.group("ordinal")), year)
        if period is not None:
            return DocumentPeriod(period, BASIS_FISCAL_LABEL, match.group(0))
    return None


def _match_period_label(text: str) -> DocumentPeriod | None:
    match = _LABEL_RE.search(text)
    if match is None:
        return None
    kind = match.group("kind1") or match.group("kind2")
    ordinal = match.group("ord1") or match.group("ord2")
    year_text = match.group("y1") or match.group("y2")
    year = _expand_year(year_text)
    if year is None:
        return None
    period = _period_of(kind, int(ordinal), year)
    if period is None:
        return None
    return DocumentPeriod(period, BASIS_PERIOD_LABEL, match.group(0))


def _match_period_end_phrase(text: str) -> DocumentPeriod | None:
    """"…for its first quarter ended 30 June 2026" → Q1 of calendar 2026.

    The year is the period's END year as printed. When the issuer also states a
    fiscal-year label (rule 1) that label wins, because it is the issuer's own
    answer to which fiscal year the quarter belongs to; this rule is the
    fallback for a document that never states one.
    """
    for phrase, period_type, ordinal in _ORDINAL_WORDS:
        index = text.find(phrase)
        if index < 0:
            continue
        window = text[index : index + _PERIOD_END_WINDOW]
        year_match = _YEAR_RE.search(window)
        if year_match is None:
            continue
        year = _expand_year(year_match.group(1))
        if year is None:
            continue
        kind = "q" if period_type == PERIOD_TYPE_QUARTER else "h"
        period = _period_of(kind, ordinal, year)
        if period is not None:
            return DocumentPeriod(period, BASIS_PERIOD_END_PHRASE, window.strip()[:120])
    return None


def detect_document_period(
    *,
    title: str | None = None,
    url: str | None = None,
    headings: "list[str] | tuple[str, ...] | None" = None,
    text: str | None = None,
) -> DocumentPeriod:
    """Resolve the period a document covers from its OWN words. Never raises.

    Sources are read in the order the issuer's own intent is clearest:
    ``title``, then ``url`` (the issuer published that slug), then the leading
    ``headings``, then a bounded slice of ``text``. Within each source the rules
    run strongest-first: a combined fiscal label, then an unambiguous
    four-digit period label, then a period-end sentence.

    Returns ``UNKNOWN_DOCUMENT_PERIOD`` when the document states no period —
    which is the common case for an annual report, and which leaves every
    existing behaviour exactly as it was.
    """
    try:
        sources = [
            _normalise(title or ""),
            _normalise(url or ""),
            _normalise(" ".join((headings or [])[:_MAX_HEADINGS])),
            _normalise((text or "")[:_MAX_SCAN_CHARS]),
        ]
        for rule in (_match_fiscal_label, _match_period_label, _match_period_end_phrase):
            for source in sources:
                if not source:
                    continue
                found = rule(source)
                if found is not None:
                    return found
    except (AttributeError, TypeError, ValueError):  # pragma: no cover - defensive
        return UNKNOWN_DOCUMENT_PERIOD
    return UNKNOWN_DOCUMENT_PERIOD


def document_period_of(
    *,
    title: str | None,
    url: str | None,
    extraction: object | None = None,
) -> DocumentPeriod:
    """``detect_document_period`` for an already-extracted document.

    Reads the extraction's LEADING headings and excerpt text (lowest page
    numbers first) so a document whose title and URL say nothing — an official
    PDF filed under a numeric identifier, as regulated storage venues publish
    them — can still state its own period on its own front page.

    Duck-typed on purpose: this module stays free of extractor imports so it
    remains a pure value layer. Never raises.
    """
    headings: list[str] = []
    texts: list[str] = []
    try:
        excerpts = list(getattr(extraction, "excerpts", None) or [])
        excerpts.sort(key=lambda e: (getattr(e, "page_number", None) or 0))
        for excerpt in excerpts[:_MAX_HEADINGS]:
            for attr in ("heading", "ancestor_heading", "section"):
                value = getattr(excerpt, attr, None)
                if isinstance(value, str) and value.strip():
                    headings.append(value.strip())
            text = getattr(excerpt, "text", None)
            if isinstance(text, str) and text.strip():
                texts.append(text.strip())
    except (AttributeError, TypeError):  # pragma: no cover - defensive
        headings, texts = [], []
    return detect_document_period(
        title=title, url=url, headings=headings, text=" ".join(texts)
    )


__all__ = [
    "BASIS_FISCAL_LABEL",
    "BASIS_PERIOD_END_PHRASE",
    "BASIS_PERIOD_LABEL",
    "BASIS_UNKNOWN",
    "UNKNOWN_DOCUMENT_PERIOD",
    "DocumentPeriod",
    "detect_document_period",
    "document_period_of",
]
