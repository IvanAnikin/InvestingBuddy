"""Periods a claim or a finding STATES, and whether two of them can be the same — ADR-086.

Three readers of one sentence need the same answer to "which period is this about":

* gap reconciliation (a finding's period, read from its words when no structured period
  was stored — "Quarter ended 30 Sep 2026: cash and cash equivalents $A45,087k");
* the council / chair claim check (a claim's period against the period of the finding or
  evidence item it cites — "H1" stated as "Q2", a FY2025 balance dated 2026-09-30);
* finding creation (a web finding whose text states exactly one reporting period).

FAIL CLOSED, BOTH WAYS. A mention this module cannot read is not a period, and two
periods it cannot place on the calendar are COMPATIBLE: a claim is withheld only when
both sides are known and certainly differ. Missing data is acceptable; a withheld true
sentence is a cost, a kept wrong one is the defect.

Every pattern here is linear: alternations of literal tokens separated by bounded or
token-anchored whitespace, never two adjacent unbounded quantifiers over one class.
"""

from __future__ import annotations

import calendar
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import date

from app.services.sources.date_semantics import (
    FORWARD_ROLES,
    HISTORICAL_REFERENCE_DATE,
    ROLE_BODY,
    read_date_mention,
)

#: Spans in months. ``0`` is a balance AT a date.
SPAN_INSTANT = 0
SPAN_QUARTER = 3
SPAN_HALF = 6
SPAN_NINE = 9
SPAN_YEAR = 12

#: Two period ends this close are one period end (a 52/53-week year ends a few days off
#: the month end).
END_SLACK_DAYS = 7
#: Text longer than this is not read for periods (a claim or a finding is short).
MAX_TEXT_CHARS = 2000

_MONTH = (
    r"(?P<mon>jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|"
    r"aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)\.?"
)
_DATE = (
    r"(?:(?P<d1>\d{1,2})[ \t]{1,3}" + _MONTH.replace("mon>", "m1>") + r",?[ \t]{1,3}"
    r"(?P<y1>(?:19|20)\d{2})"
    r"|" + _MONTH.replace("mon>", "m2>") + r"[ \t]{1,3}(?P<d2>\d{1,2}),?[ \t]{1,3}"
    r"(?P<y2>(?:19|20)\d{2})"
    r"|(?P<y3>(?:19|20)\d{2})-(?P<m3>\d{2})-(?P<d3>\d{2}))(?![\w-])"
)
_MONTHS = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov",
           "dec")

#: "three months ended June 30, 2026", "six-month period ended 30 June 2026".
_MONTHS_ENDED_RE = re.compile(
    r"\b(?P<len>three|six|nine|twelve|3|6|9|12)[- ]months?(?:[ \t]{1,3}period)?"
    r"[ \t]{1,3}(?:ended|ending|to)[ \t]{1,3}" + _DATE,
    re.IGNORECASE,
)
#: "quarter ended 30 Sep 2026", "half-year ended 31 December 2025", "year ended …".
_UNIT_ENDED_RE = re.compile(
    r"\b(?P<unit>quarter|half[- ]year|half|interim\s+period|fiscal\s+year|financial\s+year"
    r"|year)[ \t]{1,3}(?:ended|ending|to)[ \t]{1,3}" + _DATE,
    re.IGNORECASE,
)
#: A balance at a date: "at 30 June 2026", "as of September 30, 2026", "on 2026-06-30".
_AT_DATE_RE = re.compile(
    r"\b(?:as[ \t]{1,3}at|as[ \t]{1,3}of|at|on|end[ \t]{1,3}of)[ \t]{1,3}" + _DATE,
    re.IGNORECASE,
)
#: "FY26 Q1", "FY2026 H1" — the issuer's fiscal label, quarter or half after the year.
_FY_FIRST_RE = re.compile(
    r"\bfy[ \t]?'?(?P<y>(?:19|20)\d{2}|\d{2})[ \t-]{0,2}(?P<k>[qh])(?P<o>[1-4])\b",
    re.IGNORECASE,
)
#: "Q2 2026", "Q3 FY26", "H1 2026", "2026-Q2", "2026 H1".
_LABEL_RE = re.compile(
    r"\b(?P<k>[qh])(?P<o>[1-4])[ \t]{0,2}(?P<fy>fy[ \t]?'?)?(?P<y>(?:19|20)\d{2}|\d{2})\b"
    r"|\b(?P<y2>(?:19|20)\d{2})[- ](?P<k2>[qh])(?P<o2>[1-4])\b",
    re.IGNORECASE,
)
#: "second quarter of 2026", "first half of fiscal 2026", "third-quarter 2026".
_ORDINAL_RE = re.compile(
    r"\b(?P<ord>first|second|third|fourth|1st|2nd|3rd|4th)[- ](?P<k>quarter|half)"
    r"(?:[ \t]{1,3}of)?(?:[ \t]{1,3}(?P<fy>fiscal(?:[ \t]{1,3}year)?|fy))?[ \t]{1,3}"
    r"(?P<y>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
#: "FY2025", "FY25", "fiscal 2025", "fiscal year 2025", "full-year 2025", "full year 2025".
_FY_RE = re.compile(
    r"\b(?:fy[ \t]?'?|fiscal(?:[ \t]{1,3}year)?[ \t]{1,3}|financial[ \t]{1,3}year[ \t]{1,3}"
    r"|full[- ]year[ \t]{1,3})(?P<y>(?:19|20)\d{2}|\d{2})(?![\w-]|-[qh]\d)",
    re.IGNORECASE,
)
_CY_RE = re.compile(
    r"\b(?:cy[ \t]?|calendar(?:[ \t]{1,3}year)?[ \t]{1,3})(?P<y>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_ORDINALS = {"first": 1, "1st": 1, "second": 2, "2nd": 2, "third": 3, "3rd": 3,
             "fourth": 4, "4th": 4}
_LENGTHS = {"three": 3, "3": 3, "six": 6, "6": 6, "nine": 9, "9": 9, "twelve": 12,
            "12": 12}

#: Stored period keys (``rf.normalise_period`` vocabulary and ``ReportingPeriod.key``).
_KEY_LABEL_RE = re.compile(r"^(?P<fy>FY)?(?P<y>\d{4})-(?P<k>[QH])(?P<o>[1-4])$")
_KEY_YEAR_RE = re.compile(r"^(?P<cy>CY|FY)?(?P<y>\d{4})$")
_KEY_SPLIT_RE = re.compile(r"^(?P<y>\d{4})/(?P<y2>\d{2})$")
_KEY_DATE_RE = re.compile(r"^(?P<p>FYE|HYE|QE)?(?P<d>\d{4}-\d{2}-\d{2})$")


@dataclass(frozen=True)
class Period:
    """One period, placed on the calendar as far as its words allow."""

    #: A short canonical key ("2026-Q2", "FY2025", "2026-09-30", "HYE2025-12-31").
    key: str
    #: Months it spans; ``SPAN_INSTANT`` for a balance at a date; ``None`` unknown.
    span: int | None
    #: The earliest and latest day it can END on (a fiscal label is not a calendar).
    end_lo: date | None = None
    end_hi: date | None = None
    #: ``("q"|"h"|"y", year, ordinal, fiscal)`` for a label, so two labels of the same
    #: kind are compared as labels; ``None`` for a dated form.
    label: tuple[str, int, int, bool] | None = None
    start: int = 0
    end: int = 0
    #: ``date_semantics`` role of the mention in its sentence (``None`` when not read).
    role: str | None = None

    @property
    def is_forward(self) -> bool:
        return self.role in FORWARD_ROLES

    @property
    def is_historical(self) -> bool:
        return self.role == HISTORICAL_REFERENCE_DATE

    def rf_key(self) -> str | None:
        """The key in ``research_fields.statement_period`` vocabulary, or ``None``."""
        if self.label is not None:
            kind, year, ordinal, fiscal = self.label
            if kind == "y":
                return f"FY{year}" if fiscal else f"CY{year}"
            prefix = "FY" if fiscal else ""
            return f"{prefix}{year}-{kind.upper()}{ordinal}"
        if self.end_lo is None or self.end_lo != self.end_hi:
            return None
        iso = self.end_lo.isoformat()
        if self.span == SPAN_HALF:
            return f"HYE{iso}"
        if self.span == SPAN_YEAR:
            return f"FYE{iso}"
        if self.span in (SPAN_INSTANT, SPAN_QUARTER):
            # A balance at a date, or a quarter ended on it: both END on that day.
            return iso
        return None


# ── Calendar helpers ──────────────────────────────────────────────────────── #


def _month_end(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def _shift_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    return _month_end(index // 12, index % 12 + 1)


def _year4(raw: str) -> int:
    return int(raw) if len(raw) == 4 else 2000 + int(raw)


def _date_of(match: re.Match[str]) -> date | None:
    try:
        if match.group("y3"):
            return date(int(match.group("y3")), int(match.group("m3")), int(match.group("d3")))
        if match.group("y1"):
            mon, day, year = match.group("m1"), match.group("d1"), match.group("y1")
        else:
            mon, day, year = match.group("m2"), match.group("d2"), match.group("y2")
        return date(int(year), _MONTHS.index(mon[:3].lower()) + 1, int(day))
    except (TypeError, ValueError):
        return None


def _label_period(kind: str, year: int, ordinal: int, fiscal: bool, *, key: str,
                  start: int = 0, end: int = 0) -> Period | None:
    if kind == "q" and 1 <= ordinal <= 4:
        span, month = SPAN_QUARTER, 3 * ordinal
    elif kind == "h" and ordinal in (1, 2):
        span, month = SPAN_HALF, 6 * ordinal
    elif kind == "y":
        span, month = SPAN_YEAR, 12
    else:
        return None
    if not 1900 <= year <= 2100:
        return None
    calendar_end = _month_end(year, month)
    # A label may name a FISCAL period (ADR-065: keyed by the year the fiscal year ends),
    # which can end up to a year before its calendar reading.
    lo = _month_end(year, 1) if kind == "y" else _shift_months(calendar_end, 12)
    return Period(key=key, span=span, end_lo=lo, end_hi=calendar_end,
                  label=(kind, year, ordinal, fiscal), start=start, end=end)


def period_of_key(key: object) -> Period | None:
    """A STORED period key (a finding's ``period_key``, an evidence item's ``period``)."""
    text = str(key or "").strip().upper()
    if not text:
        return None
    m = _KEY_LABEL_RE.match(text)
    if m:
        return _label_period(m.group("k").lower(), int(m.group("y")), int(m.group("o")),
                             bool(m.group("fy")), key=text)
    m = _KEY_YEAR_RE.match(text)
    if m:
        year = int(m.group("y"))
        if m.group("cy") == "CY":
            day = date(year, 12, 31)
            return Period(key=text, span=SPAN_YEAR, end_lo=day, end_hi=day,
                          label=("y", year, 0, False))
        return _label_period("y", year, 0, True, key=f"FY{year}")
    m = _KEY_SPLIT_RE.match(text)
    if m:
        year = int(m.group("y")) + 1
        return Period(key=text, span=SPAN_YEAR, end_lo=_month_end(year, 1),
                      end_hi=_month_end(year, 12), label=("y", year, 0, True))
    m = _KEY_DATE_RE.match(text)
    if m:
        try:
            day = date.fromisoformat(m.group("d"))
        except ValueError:
            return None
        span = {"FYE": SPAN_YEAR, "HYE": SPAN_HALF, "QE": SPAN_QUARTER}.get(
            m.group("p") or "", SPAN_INSTANT)
        return Period(key=text, span=span, end_lo=day, end_hi=day)
    # A label written as words ("Q2 2026", "FY2025") reads like a mention.
    found = [p for p in mentions(text, classify=False)]
    return found[0] if len(found) == 1 else None


# ── Reading mentions ──────────────────────────────────────────────────────── #


def _read(text: str) -> list[Period]:
    """Every period mention, strongest rule first; a later rule never re-reads a span
    an earlier one took."""
    found: list[Period] = []
    taken: list[tuple[int, int]] = []

    def free(a: int, b: int) -> bool:
        return all(b <= s or a >= e for s, e in taken)

    def add(period: Period | None) -> None:
        if period is not None and free(period.start, period.end):
            taken.append((period.start, period.end))
            found.append(period)

    for m in _MONTHS_ENDED_RE.finditer(text):
        day = _date_of(m)
        length = _LENGTHS[m.group("len").lower()]
        if day is not None:
            key = {3: "QE", 6: "HYE", 12: "FYE"}.get(length, f"M{length}E")
            add(Period(key=f"{key}{day.isoformat()}", span=length, end_lo=day, end_hi=day,
                       start=m.start(), end=m.end()))
    for m in _UNIT_ENDED_RE.finditer(text):
        day = _date_of(m)
        unit = re.sub(r"\s+", " ", m.group("unit").lower())
        span = (SPAN_QUARTER if unit == "quarter"
                else SPAN_HALF if unit in ("half", "half-year", "half year",
                                           "interim period")
                else SPAN_YEAR)
        if day is not None:
            key = {SPAN_QUARTER: "QE", SPAN_HALF: "HYE"}.get(span, "FYE")
            add(Period(key=f"{key}{day.isoformat()}", span=span, end_lo=day, end_hi=day,
                       start=m.start(), end=m.end()))
    for m in _AT_DATE_RE.finditer(text):
        day = _date_of(m)
        if day is not None:
            add(Period(key=day.isoformat(), span=SPAN_INSTANT, end_lo=day, end_hi=day,
                       start=m.start(), end=m.end()))
    for m in _FY_FIRST_RE.finditer(text):
        year = _year4(m.group("y"))
        kind, ordinal = m.group("k").lower(), int(m.group("o"))
        add(_label_period(kind, year, ordinal, True, key=f"FY{year}-{kind.upper()}{ordinal}",
                          start=m.start(), end=m.end()))
    for m in _LABEL_RE.finditer(text):
        if m.group("y2"):
            year, kind, ordinal, fiscal = (int(m.group("y2")), m.group("k2").lower(),
                                           int(m.group("o2")), False)
        else:
            raw, fiscal = m.group("y"), bool(m.group("fy"))
            if len(raw) == 2 and not fiscal:
                continue  # "Q3 26" names no year with certainty
            year, kind, ordinal = _year4(raw), m.group("k").lower(), int(m.group("o"))
        prefix = "FY" if fiscal else ""
        add(_label_period(kind, year, ordinal, fiscal,
                          key=f"{prefix}{year}-{kind.upper()}{ordinal}",
                          start=m.start(), end=m.end()))
    for m in _ORDINAL_RE.finditer(text):
        kind = "q" if m.group("k").lower() == "quarter" else "h"
        ordinal = _ORDINALS[m.group("ord").lower()]
        fiscal, year = bool(m.group("fy")), int(m.group("y"))
        prefix = "FY" if fiscal else ""
        add(_label_period(kind, year, ordinal, fiscal,
                          key=f"{prefix}{year}-{kind.upper()}{ordinal}",
                          start=m.start(), end=m.end()))
    for m in _FY_RE.finditer(text):
        year = _year4(m.group("y"))
        add(_label_period("y", year, 0, True, key=f"FY{year}", start=m.start(), end=m.end()))
    for m in _CY_RE.finditer(text):
        year = int(m.group("y"))
        day = date(year, 12, 31)
        if free(m.start(), m.end()):
            taken.append((m.start(), m.end()))
            found.append(Period(key=f"CY{year}", span=SPAN_YEAR, end_lo=day, end_hi=day,
                                label=("y", year, 0, False), start=m.start(), end=m.end()))
    found.sort(key=lambda p: p.start)
    return found


_NORM_SEP_RE = re.compile(r"[^a-z0-9]+")


def _normalised(text: str) -> str:
    return _NORM_SEP_RE.sub(" ", text.lower()).strip()


def _role(text: str, period: Period) -> str:
    """``date_semantics`` role of one mention, read in its own sentence-sized window."""
    before = _normalised(text[max(0, period.start - 120):period.start])[-60:]
    mention = _normalised(text[period.start:period.end])
    after = _normalised(text[period.end:period.end + 80])[:40]
    window = f"{before} {mention} {after}"
    start = len(before) + 1
    return read_date_mention(window, start, start + len(mention), role=ROLE_BODY).role


def mentions(text: str | None, *, classify: bool = True) -> list[Period]:
    """Every period ``text`` mentions, in order, each with its ``date_semantics`` role
    when ``classify``. Never raises."""
    source = str(text or "")[:MAX_TEXT_CHARS]
    try:
        found = _read(source)
    except (TypeError, ValueError):  # pragma: no cover - defensive
        return []
    if not classify:
        return found
    return [
        Period(key=p.key, span=p.span, end_lo=p.end_lo, end_hi=p.end_hi, label=p.label,
               start=p.start, end=p.end, role=_role(source, p))
        for p in found
    ]


def single_period(text: str | None) -> Period | None:
    """The one period ``text`` is about: exactly one distinct mention that is not
    forward-looking (a forecast, a target, a deadline) and not a comparison. Otherwise
    ``None`` — two periods are not one."""
    keys: dict[str, Period] = {}
    for period in mentions(text):
        if period.is_forward or period.is_historical:
            continue
        keys.setdefault(period.key, period)
    return next(iter(keys.values())) if len(keys) == 1 else None


# ── Comparing two periods ─────────────────────────────────────────────────── #


def compatible(a: Period, b: Period) -> bool:
    """Can ``a`` and ``b`` be the same period? ``False`` only when they certainly differ.

    * Two labels of one kind (two quarters, two halves, two years) are the same only
      when they are the same label; a fiscal label is compared with a calendar one only
      through its possible end dates.
    * Different spans (three months against six, a year against a quarter) differ — a
      balance at a date is compared by its date alone.
    * Otherwise the possible ends must overlap, within ``END_SLACK_DAYS``.
    """
    if a.key == b.key:
        return True
    if a.label is not None and b.label is not None:
        if a.label[0] == b.label[0] and a.label[3] == b.label[3]:
            return a.label[1:3] == b.label[1:3]
    if (
        a.span not in (None, SPAN_INSTANT) and b.span not in (None, SPAN_INSTANT)
        and a.span != b.span
    ):
        return False
    if None in (a.end_lo, a.end_hi, b.end_lo, b.end_hi):
        return True
    assert a.end_lo and a.end_hi and b.end_lo and b.end_hi
    slack = END_SLACK_DAYS
    return (a.end_lo - b.end_hi).days <= slack and (b.end_lo - a.end_hi).days <= slack


def any_compatible(periods: Iterable[Period], others: Sequence[Period]) -> bool:
    return any(compatible(p, q) for p in periods for q in others)


__all__ = [
    "END_SLACK_DAYS",
    "SPAN_HALF",
    "SPAN_INSTANT",
    "SPAN_QUARTER",
    "SPAN_YEAR",
    "Period",
    "any_compatible",
    "compatible",
    "mentions",
    "period_of_key",
    "single_period",
]
