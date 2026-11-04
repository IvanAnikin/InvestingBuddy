"""A later period supersedes — ADR-087.

WHAT WAS WRONG (QA campaign 2026-11)
====================================
* LIN2-1: the Financial capacity section showed a third-party page's "most recent fiscal
  year: cash AUD 3.49m" (dated 2026-02-20) while the issuer's FY2026 annual report
  (2026-09-24) and its 30-Sep-2026 Appendix 5B were held.
* ANTO-3: the synthesis wrote Dec-2024 net debt as the current position.
* ADBE-1: the executive synthesis described the FY2025 segment structure in the present
  tense ("revenue is primarily generated from its Digital Media and Digital Experience
  segments") beside a held, newer 10-Q; the segment findings were undated.

THE ONE RULE
============
A point-in-time balance-sheet figure (cash, net debt / net cash, total debt / borrowings,
equity), a "most recent / latest / last fiscal year" summary, or a segment structure is
never presented as current when it is not: it is LABELLED WITH ITS OWN DATE. Nothing is
deleted and no figure is changed; a label is only ever a true date. When the platform
cannot be sure of the date, nothing is labelled: a missing label is a gap, a wrong one
is wrong data.

A text unit is labelled when it states such a claim WITHOUT a date of its own, and its
as-of is known to be older than either
* the newest period the issuer's own HELD documents give for the same metric (the run's
  core filings, official disclosures and statement documents — never a cited web page),
  or
* a year before the report (``STALE_AFTER_DAYS``), which no current balance sheet is.

A "most recent / latest / last fiscal year" claim is labelled when its source predates
the issuer's held annual report by more than the longest annual reporting lag, even if
it prints a year. ("Current fiscal year" is the year in progress, not a past one: never.)

THE DATE OF A CLAIM (review of PR #326)
=======================================
A finding's as-of is, in this order, and never a day after its source's publication
date (or, undated, the report date):
1. the end of its ``period_key``;
2. the latest balance-date phrase its text prints ("at / as of / as at <date>");
3. "as of a source dated <publication date>".
Any other date in the text — a forecast ("into 2028"), a maturity ("mature 15 March
2030"), a filing date ("10-K filed 2026-02-03") — is never the figures' date. Every date
is read from the finding's ORIGINAL statement: the labels this module adds are not text.

A synthesis sentence is labelled only when
* every subject finding it cites that carries a figure has a known as-of, and the
  latest of them is older than the newer held issuer period (the label prints that
  latest as-of);
* every amount the sentence prints is one those findings print, rounded to the precision
  the sentence prints ("$4.3bn" is 4,316.3m; "A$45.1m" is not A$44.0m). A new figure
  ("rose to A$31.3m") or a peer's figure has no date the platform knows;
* findings about another entity (a peer, a competitor, "(not the company)") are not the
  subject's figures and never date the sentence.
A label goes only at a sentence start the platform is sure of. Across an abbreviation the
run of text is one unit: when the part before the last abbreviation cites nothing and
states no figure or metric ("Revenue grew 8% in the U.S. Cash of $4.2bn …"), the label
goes before the capitalised word after it; when that part states the metric ("Net debt
of $1.6bn is below that of U.S. Steel"), at the unit's start; when it cites something of
its own, nowhere.

A held document is NAMED in a label only when it is an annual report, an interim or
quarterly report (10-K, 10-Q, 20-F, 40-F, half-year report), or an Appendix 5B / 4C;
anything else (a 6-K, an activities report, a results release) is "a newer issuer
disclosure".

BOUNDS, NOT GUESSES
===================
Every comparison is between an UPPER bound of the old claim's as-of and a LOWER bound of
the newer one: a held document's period ends no earlier than its publication date minus
the longest reporting lag for its kind (an annual report 183 days, an interim or
quarterly report 92 days); an Appendix 5B fact knows its quarter end exactly.

SEGMENTS
========
A segment finding without a period key takes the one period its own text names
(``period_key_source: "statement_text"``), only when that period had ended by its
source's publication date (ADR-080: a forward period is never a period tag). An undated
synthesis sentence about segments that cites only older segment findings is labelled
with their period. No segment is ever called historical: the run's segment scope keys
are not a complete, normalised perimeter (ADR-087 known limit).

Every pattern is linear and every input bounded (``MAX_TEXT_CHARS``). No issuer is named.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from app.services.ledger.canonical_state import as_date, document_inventory, period_bounds

#: 2: dates from the original statements and from period keys / balance-date phrases only;
#: no "historical segment" rule; peers ignored; sure sentence starts (review of #326).
SUPERSESSION_VERSION = 2

#: A claim whose as-of is older than this before the report is not "current".
STALE_AFTER_DAYS = 365
#: The longest lag between a period's end and the publication of its report.
ANNUAL_LAG_DAYS = 183
INTERIM_LAG_DAYS = 92
#: Texts longer than this are not examined.
MAX_TEXT_CHARS = 2000
#: A sentence's amount is a cited figure when it is that figure ROUNDED TO THE PRECISION
#: THE SENTENCE PRINTS: within half a unit of its last printed digit, after scaling
#: ("$4.3bn" is 4,316.3m: within $0.05bn; "A$45.1m" is not A$44.0m).

FAMILY_CASH = "cash"
FAMILY_DEBT = "debt"
FAMILY_EQUITY = "equity"
FAMILY_SEGMENTS = "segments"
FAMILY_ANNUAL = "fiscal_year"
BALANCE_FAMILIES = (FAMILY_CASH, FAMILY_DEBT, FAMILY_EQUITY)

RULE_LATEST_FISCAL_YEAR = "most_recent_fiscal_year_superseded"
RULE_BALANCE_AS_OF = "balance_sheet_figure_dated"
RULE_SEGMENT_AS_OF = "segment_structure_dated"

#: Statement fields per balance-sheet family (``financial_statements_state.slots``).
_SLOT_FIELDS: dict[str, tuple[str, ...]] = {
    FAMILY_CASH: ("cash_and_equivalents",),
    FAMILY_DEBT: ("net_debt", "net_cash", "total_debt", "borrowings"),
    FAMILY_EQUITY: ("total_equity",),
}

#: Domains whose segment statements describe the company's own perimeter.
_PERIMETER_DOMAINS = frozenset({
    "business_model", "financial_capacity", "operations_assets", "products_services",
    "customers_end_markets", "growth_pipeline",
})

# ── Patterns (linear) ─────────────────────────────────────────────────────── #

_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_MONTHS = {m: i for i, m in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
    start=1)}
_DMY_RE = re.compile(r"\b(\d{1,2})\s{1,3}" + _MONTH + r"\.?,?\s{1,3}((?:19|20)\d{2})\b", re.I)
_MDY_RE = re.compile(r"\b" + _MONTH + r"\.?\s{1,3}(\d{1,2}),?\s{1,3}((?:19|20)\d{2})\b", re.I)
_ISO_RE = re.compile(r"\b((?:19|20)\d{2})-(\d{2})-(\d{2})\b")
_MONTH_YEAR_RE = re.compile(r"\b" + _MONTH + r"[-\s]{1,3}((?:19|20)\d{2})\b", re.I)
_FY_RE = re.compile(r"\b(?:FY|fiscal(?:\s{1,3}year)?)[\s-]{0,2}((?:19|20)\d{2}|\d{2})\b", re.I)
_QUARTER_RE = re.compile(
    r"\b(?:Q([1-4])[\s-]{0,2}(?:FY|fiscal)?[\s-]{0,2}((?:19|20)\d{2})"
    r"|((?:19|20)\d{2})-Q([1-4]))\b", re.I)
_HALF_RE = re.compile(
    r"\b(?:(?:H|HY)([12])?[\s-]{0,2}(?:FY)?[\s-]{0,2}((?:19|20)\d{2})"
    r"|((?:19|20)\d{2})-H([12]))\b", re.I)
_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
#: What makes a printed date the date OF a balance ("at 31 December 2024", "as of
#: June 30, 2025", "as at the end of March 2026"), read just before the date.
_BALANCE_DATE_LEAD_RE = re.compile(
    r"\b(?:as\s{1,3}(?:of|at)|at)(?:\s{1,3}the\s{1,3}end\s{1,3}of)?\s{1,3}$", re.I)
#: A past full year; "current fiscal year" is the year in progress, never superseded.
_LATEST_FY_RE = re.compile(
    r"\b(?:most\s{1,3}recent|latest|last)\s{1,3}(?:full\s{1,3})?"
    r"(?:fiscal|financial|reporting)\s{1,3}year\b", re.I)
_SEGMENT_RE = re.compile(r"\bsegments?\b(?!\s{1,3}(?:its|their|the)\b)", re.I)
#: A money amount: a currency sign or code beside a number, or a number with a scale.
#: (``_amounts`` keeps only matches with one of the two.)
_MONEY_RE = re.compile(
    r"(?:(?P<cur>[$€£¥]|\b(?:USD|AUD|A\$|US\$|EUR|GBP|CHF|CAD|JPY|NZD|HKD|SEK|DKK|NOK)\b)"
    r"\s?)?(?P<num>\d{1,3}(?:,\d{3}){1,6}|\d{1,15})(?P<dec>\.\d{1,6})?"
    r"(?:\s?(?P<scale>billion|bn|b|million|mn|m|thousand|k)\b)?", re.I)
_SCALES = {"billion": 1e9, "bn": 1e9, "b": 1e9, "million": 1e6, "mn": 1e6, "m": 1e6,
           "thousand": 1e3, "k": 1e3}
#: A statement about another entity: a peer, competitor or rival, or the writer's own
#: "(not the company)". ("peer-to-peer" is a payments product, not a peer.)
_OTHER_ENTITY_RE = re.compile(
    r"\b(?:(?<!-to-)peers?(?!-to-)|competitors?|rivals?)\b|\bnot\s{1,3}the\s{1,3}company\b",
    re.I)
_TOKEN_RE = re.compile(r"[a-z]+(?:'[a-z]?)?|\d")
#: Every label this module writes: "[as of …", "[for …", "[at …", "[figures …" or
#: "[segment structure …", ending in "is held]" or "over a year before this report]".
_OWN_LABEL_RE = re.compile(
    r"\[(?:as of |for |at |figures |segment structure )[^\[\]]{0,400}"
    r"(?:is held|over a year before this report)\]\s?")
#: A finding id (or its 8-character prefix) as a chair cites it; at least one letter, so
#: an 8-digit number is never one.
_HEX_ID_RE = re.compile(
    r"\b(?=[0-9]{0,7}[a-f])([0-9a-f]{8})(?:-[0-9a-f]{4}){0,3}(?:-[0-9a-f]{12})?\b")
_SENTENCE_RE = re.compile(r"(?<=[.!?])[ \t]+(?=[A-Z\"'(\[])")
#: A dotted abbreviation ("U.S", "e.g") before a full stop: not a sentence end.
_DOTTED_RE = re.compile(r"[A-Za-z]\.[A-Za-z]")
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "st", "inc", "co", "corp", "ltd", "plc", "llc", "no",
    "nos", "vs", "etc", "approx", "est", "fig", "jr", "sr", "bros", "dept", "mt", "ft",
    "ave", "gen", "gov", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
    "oct", "nov", "dec",
})
#: A held document named in a label: an Appendix 5B / 4C …
_NAMED_TITLE_RE = re.compile(r"\bappendix\s{0,3}(?:5b|4c)\b", re.I)
#: … or one of these filings.
_NAMED_FORMS = ("10-K", "10-Q", "20-F", "40-F")

#: Tokens after "cash" that make it a flow or a cost, not a balance.
_CASH_NOT_BALANCE_NEXT = frozenset({
    "flow", "flows", "cost", "costs", "burn", "generation", "generated", "conversion",
    "margin", "margins", "runway", "outflow", "outflows", "inflow", "inflows",
    "consideration", "payment", "payments", "dividend", "dividends", "settled", "used",
    "from", "returns", "return", "spend", "spending", "taxes", "interest", "earnings",
    "profit", "component", "basis", "rich", "compensation", "bonus", "awards", "paid",
    "receipts", "proceeds", "operating",
})
_CASH_NOT_BALANCE_PREV = frozenset({"free", "operating", "in", "for", "non", "net"})
_DEBT_NOT_BALANCE_NEXT = frozenset({"free", "securities", "facility", "facilities",
                                    "financing", "instruments", "market", "markets",
                                    "issuance", "offering", "raise"})
_EQUITY_PREV = frozenset({"total", "shareholders", "shareholders'", "stockholders",
                          "stockholders'", "book", "group"})
_NET_FLOW_NEXT = frozenset({"from", "used", "inflow", "outflow", "outflows", "inflows",
                            "generated", "provided", "flow", "flows", "proceeds"})


# ── Dates and periods in text ─────────────────────────────────────────────── #


def _day(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _month_end(year: int, month: int) -> date:
    first_next = date(year + (month == 12), month % 12 + 1, 1)
    return first_next - timedelta(days=1)


def _dated_spans(text: str) -> list[tuple[int, date]]:
    """``(start, date)`` of every date a text prints. A month-year ("June 2026" → its
    last day) that is part of a day-precise date ("15 March 2025") is that date only."""
    out: list[tuple[int, date]] = []
    covered = bytearray(len(text))
    for regex, order in ((_DMY_RE, (3, 2, 1)), (_MDY_RE, (3, 1, 2)), (_ISO_RE, (1, 2, 3))):
        for m in regex.finditer(text):
            y, mo, d = (m.group(i) for i in order)
            month = int(mo) if mo.isdigit() else _MONTHS[mo[:3].lower()]
            day = _day(int(y), month, int(d))
            if day:
                out.append((m.start(), day))
                covered[m.start(): m.end()] = b"\x01" * (m.end() - m.start())
    for m in _MONTH_YEAR_RE.finditer(text):
        if any(covered[m.start(): m.end()]):
            continue
        out.append((m.start(), _month_end(int(m.group(2)), _MONTHS[m.group(1)[:3].lower()])))
    return out


def text_dates(text: str) -> list[date]:
    """Day-precise dates a text prints (``31 Dec 2024``, ``Dec 31, 2024``,
    ``2024-12-31``) and month-year dates (``June 2026`` → its last day)."""
    return [day for _, day in _dated_spans(text)]


def balance_dates(text: str) -> list[date]:
    """The dates a text gives as the date OF a figure: "at / as of / as at <date>"."""
    return [day for start, day in _dated_spans(text)
            if _BALANCE_DATE_LEAD_RE.search(text[max(0, start - 30): start])]


def _year(raw: str) -> int:
    value = int(raw)
    return 2000 + value if value < 100 else value


def text_period_keys(text: str) -> list[str]:
    """Period keys a text names, in the vocabulary ``period_bounds`` reads."""
    keys: list[str] = []
    for m in _QUARTER_RE.finditer(text):
        if m.group(1):
            keys.append(f"{m.group(2)}-Q{m.group(1)}")
        else:
            keys.append(f"{m.group(3)}-Q{m.group(4)}")
    for m in _HALF_RE.finditer(text):
        if m.group(3):
            keys.append(f"{m.group(3)}-H{m.group(4)}")
        elif m.group(1):
            keys.append(f"{m.group(2)}-H{m.group(1)}")
    for m in _FY_RE.finditer(text):
        keys.append(f"FY{_year(m.group(1))}")
    return keys


def has_own_date(text: str) -> bool:
    """True when the text prints any date, period or year of its own."""
    return bool(text_dates(text) or text_period_keys(text) or _YEAR_RE.search(text))


# ── Amounts ───────────────────────────────────────────────────────────────── #


def _printed_amounts(text: str) -> list[tuple[float, float]]:
    """``(value, half a unit of its last printed digit)`` of every money amount a text
    prints, in units ("$4.3bn" → (4.3e9, 0.05e9); "A$1,629m" → (1.629e9, 0.5e6))."""
    out: list[tuple[float, float]] = []
    for m in _MONEY_RE.finditer(text):
        scale = (m.group("scale") or "").lower()
        if not (m.group("cur") or scale):
            continue
        decimals = m.group("dec") or ""
        factor = _SCALES.get(scale, 1.0)
        value = float(m.group("num").replace(",", "") + decimals)
        half = 0.5 * 10.0 ** -max(len(decimals) - 1, 0) * factor
        out.append((value * factor, half))
    return out


def _amounts(text: str) -> list[float]:
    """The money amounts a text prints, in units."""
    return [value for value, _ in _printed_amounts(text)]


def _states_amount(text: str) -> bool:
    return bool(_printed_amounts(text))


def _same_amount(stated: tuple[float, float], figure: float) -> bool:
    """The sentence's ``stated`` amount is ``figure`` rounded to its printed precision."""
    value, half = stated
    return abs(value - figure) <= half * (1 + 1e-9)


# ── Metric families ───────────────────────────────────────────────────────── #


def _tokens(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower().replace("’", "'"))


def families(text: str) -> set[str]:
    """The point-in-time balance-sheet families a text states."""
    out: set[str] = set()
    tokens = _tokens(text)
    for i, token in enumerate(tokens):
        prev = tokens[i - 1] if i else ""
        nxt = tokens[i + 1] if i + 1 < len(tokens) else ""
        if token == "cash":
            if prev == "net" and nxt not in _NET_FLOW_NEXT:
                out.add(FAMILY_DEBT)  # a net cash POSITION
            elif nxt not in _CASH_NOT_BALANCE_NEXT and prev not in _CASH_NOT_BALANCE_PREV:
                out.add(FAMILY_CASH)
        elif token == "liquidity" or (token == "liquid" and nxt == "investments"):
            out.add(FAMILY_CASH)
        elif token in ("debt", "borrowings"):
            if nxt not in _DEBT_NOT_BALANCE_NEXT:
                out.add(FAMILY_DEBT)
        elif token == "equity":
            if prev in _EQUITY_PREV or prev.isdigit() or prev in ("m", "bn", "b", "k"):
                out.add(FAMILY_EQUITY)
        elif token == "assets" and prev == "net":
            out.add(FAMILY_EQUITY)
    return out


def _is_segment_claim(text: str) -> bool:
    return bool(_SEGMENT_RE.search(text))


# ── What the issuer's held documents give ─────────────────────────────────── #


def _families_of_document(kind: str, form: str | None) -> tuple[set[str], int] | None:
    """Families a held document reports, and the longest lag to its publication."""
    from app.services.sources.disclosures.relevance import is_full_year_results

    form_text = (form or "").upper()
    all_families = {*BALANCE_FAMILIES, FAMILY_SEGMENTS}
    if kind == "annual_report" or form_text.startswith(("10-K", "20-F", "40-F")):
        return {*all_families, FAMILY_ANNUAL}, ANNUAL_LAG_DAYS
    if kind == "interim_report" or form_text.startswith(("10-Q", "6-K")):
        return all_families, INTERIM_LAG_DAYS
    if kind == "results_release":
        if is_full_year_results(form):
            return {*all_families, FAMILY_ANNUAL}, ANNUAL_LAG_DAYS
        return {FAMILY_CASH}, INTERIM_LAG_DAYS
    return None


def _nameable(kind: str, form: str | None) -> bool:
    """A held document a label may name: an annual, interim or quarterly report, or an
    Appendix 5B / 4C. A 6-K, an activities report or a release is not named."""
    form_text = (form or "").strip().upper()
    if form_text.startswith("6-K"):
        return False
    if _NAMED_TITLE_RE.search(form or "") or form_text.startswith(_NAMED_FORMS):
        return True
    return kind in ("annual_report", "interim_report")


def newest_issuer_periods(v3: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per family, the LOWER bound of the newest period the issuer's held documents give,
    which document gives it, and whether a label may name it."""
    from app.services.sources.asx_appendix_5b import FORM_LABELS, parse_locator

    best: dict[str, dict[str, Any]] = {}

    def offer(family: str, bound: date, what: str, published: date | None,
              named: bool) -> None:
        current = best.get(family)
        if (current is None or bound > current["lower_bound"]
                or (bound == current["lower_bound"] and named and not current["named"])):
            best[family] = {"lower_bound": bound, "document": what, "named": named,
                            "published": published.isoformat() if published else None}

    for doc in document_inventory(v3):
        if doc.origin == "cited_finding":
            continue
        reported = _families_of_document(doc.kind, doc.form)
        if reported is None:
            continue
        fams, lag = reported
        what = doc.form or doc.kind.replace("_", " ")
        named = _nameable(doc.kind, doc.form)
        for family in fams:
            offer(family, doc.published - timedelta(days=lag), what, doc.published, named)

    statements = v3.get("financial_statements_state")
    slots = statements.get("slots") if isinstance(statements, Mapping) else None
    for key, dp in (slots or {}).items() if isinstance(slots, Mapping) else ():
        if not isinstance(dp, Mapping):
            continue
        for family, fields in _SLOT_FIELDS.items():
            if not any(str(key).startswith(f + "_") for f in fields):
                continue
            parsed = None
            for bit in dp.get("fact_provenance") or ():
                if str(bit).startswith("excerpt="):
                    parsed = parse_locator(str(bit)[len("excerpt="):])
            if parsed is None:
                continue
            title = str(dp.get("document_title") or FORM_LABELS.get(parsed[0], parsed[0]))
            # The fact came from the form itself: it is an Appendix 5B / 4C, whatever the
            # announcement's headline says.
            offer(family, parsed[1], title, as_date(dp.get("document_date")), True)
    return best


# ── As-of of a claim ──────────────────────────────────────────────────────── #


def _statement(finding: Mapping[str, Any]) -> str:
    """A finding's statement as written: without any label this module added."""
    return _OWN_LABEL_RE.sub("", str(finding.get("statement") or "")[:MAX_TEXT_CHARS])


def _about_other_entity(finding: Mapping[str, Any]) -> bool:
    return bool(_OTHER_ENTITY_RE.search(_statement(finding)))


def _finding_as_of(
    finding: Mapping[str, Any], report_date: date | None = None
) -> tuple[date, str] | None:
    """The upper bound of a finding's as-of and how it reads in a label: its period key,
    else its latest balance-date phrase, else its source's date — never a day after its
    source's publication (or, undated, the report date)."""
    text = _statement(finding)
    published = as_date(finding.get("source_published_at"))
    ceiling = published or report_date or date.today()
    key = finding.get("period_key")
    bounds = period_bounds(key) if key else None
    if bounds is not None and bounds[1] <= ceiling:
        return bounds[1], f"for {_key_label(str(key))}"
    dated = [day for day in balance_dates(text) if day <= ceiling]
    if dated:
        latest = max(dated)
        return latest, f"at {_fmt(latest)}"
    if published is not None:
        return published, f"as of a source dated {published.isoformat()}"
    return None


def _combined(
    every: list[tuple[date, str] | None], shown: list[tuple[date, str] | None]
) -> tuple[date, str] | None:
    """``(latest as-of of EVERY figure, how the SHOWN ones read)``: one as-of reads as
    itself ("for FY2025"); different ones as "up to" the latest ("up to 2026-Q2"), never
    one period for figures of several. ``None`` when any as-of is unknown."""
    if not every or not shown or any(a is None for a in (*every, *shown)):
        return None
    upper = max(a[0] for a in every if a is not None)
    dated = [a for a in shown if a is not None]
    latest = max(dated, key=lambda a: a[0])
    if len({a[0] for a in dated}) == 1 and latest[0] == upper:
        return upper, latest[1]
    return upper, f"up to {_short(latest)}"


def _short(as_of: tuple[date, str]) -> str:
    """An as-of without its preposition: "FY2025", "31 Dec 2025", "20 Feb 2026"."""
    label = as_of[1]
    if label.startswith("for "):
        return label[len("for "):]
    return _fmt(as_of[0])


def _fmt(day: date) -> str:
    return f"{day.day} {day.strftime('%b')} {day.year}"


def _key_label(key: str) -> str:
    text = key.strip()
    return f"FY{text}" if text.isdigit() else text


# ── The rule ──────────────────────────────────────────────────────────────── #


class _Labeller:
    def __init__(self, v3: Mapping[str, Any], as_of: date) -> None:
        self.as_of = as_of
        self.newest = newest_issuer_periods(v3)
        self.actions: list[dict[str, Any]] = []
        self.periods_filled: list[dict[str, Any]] = []
        #: SNAPSHOTS of the findings before any label is added (labels are not text).
        self.by_id: dict[str, Mapping[str, Any]] = {}
        self.by_label: dict[str, Mapping[str, Any]] = {}

    # -- inputs --
    def index(self, v3: Mapping[str, Any]) -> None:
        report = v3.get("professional_research")
        labels = {}
        if isinstance(report, Mapping):
            raw = report.get("finding_labels")
            labels = dict(raw) if isinstance(raw, Mapping) else {}
        for finding in _findings(v3):
            snapshot = dict(finding)
            fid = str(finding.get("finding_id") or "")
            if fid:
                self.by_id.setdefault(fid, snapshot)
            label = finding.get("label") or labels.get(fid)
            if label:
                self.by_label.setdefault(str(label), self.by_id.get(fid, snapshot))

    def as_of_of(self, finding: Mapping[str, Any]) -> tuple[date, str] | None:
        return _finding_as_of(finding, self.as_of)

    def _stale(self, upper: date, family: str) -> dict[str, Any] | None:
        newer = self.newest.get(family)
        if newer is not None and upper < newer["lower_bound"]:
            return newer
        if family in BALANCE_FAMILIES and upper < self.as_of - timedelta(
                days=STALE_AFTER_DAYS):
            return {"lower_bound": None, "document": None, "published": None}
        return None

    @staticmethod
    def _held_phrase(newer: Mapping[str, Any]) -> str:
        if not newer.get("document"):
            return "over a year before this report"
        when = f" of {newer['published']}" if newer.get("published") else ""
        if newer.get("named"):
            return f"the issuer's newer {newer['document']}{when} is held"
        return f"a newer issuer disclosure{when} is held"

    # -- findings --
    def finding_label(self, finding: Mapping[str, Any]) -> tuple[str, str] | None:
        raw = str(finding.get("statement") or "")
        if not raw or len(raw) > MAX_TEXT_CHARS or _is_labelled(raw):
            return None
        text = _statement(finding)
        if _about_other_entity(finding):
            return None  # "the issuer's newer report" is not that entity's report
        if _LATEST_FY_RE.search(text):
            published = as_date(finding.get("source_published_at"))
            newer = self.newest.get(FAMILY_ANNUAL)
            if published is not None and newer is not None and (
                    published < newer["lower_bound"]):
                return RULE_LATEST_FISCAL_YEAR, (
                    f"[as of a source dated {published.isoformat()}; not the latest fiscal "
                    f"year: {self._held_phrase(newer)}]")
        as_of = self.as_of_of(finding)
        if has_own_date(text) or as_of is None:
            return None
        if not _states_amount(_strip_ids(text)):
            return None
        for family in sorted(families(text)):
            newer = self._stale(as_of[0], family)
            if newer is not None:
                return RULE_BALANCE_AS_OF, f"[{as_of[1]}; {self._held_phrase(newer)}]"
        return None

    def segment_period(self, finding: dict[str, Any]) -> None:
        """Fill an undated segment finding's period from the one its text names, when
        that period had ended by its source's publication (or the report) date."""
        if finding.get("period_key") or str(finding.get("domain")) not in _PERIMETER_DOMAINS:
            return
        text = _statement(finding)
        scope = str(finding.get("scope_key") or "")
        if not (_is_segment_claim(text) or scope.startswith("segment:")):
            return
        keys = {k for k in text_period_keys(text) if period_bounds(k)}
        if len(keys) != 1 or text_dates(text):
            # One named period and nothing else: a text that also prints dates (an event
            # on a day) is not stating the period its figures are for.
            return
        key = next(iter(keys))
        bounds = period_bounds(key)
        ceiling = as_date(finding.get("source_published_at")) or self.as_of
        if bounds is None or bounds[1] > ceiling:
            return  # a target or a forecast ("output of 50kt in FY2028"): not a period
        finding["period_key"] = key
        finding["period_key_source"] = "statement_text"
        entry = {"finding_id": finding.get("finding_id"), "period_key": key}
        if entry not in self.periods_filled:
            self.periods_filled.append(entry)

    # -- sentences --
    def sentence_label(
        self, sentence: str, cited: list[Mapping[str, Any]]
    ) -> tuple[str, str] | None:
        text = _strip_ids(sentence).strip()
        if (not text or len(text) > MAX_TEXT_CHARS or not cited or has_own_date(text)
                or _is_labelled(text)):
            return None
        subject = [f for f in cited if not _about_other_entity(f)]
        with_figures = [f for f in subject if _states_amount(_strip_ids(_statement(f)))]
        figure_dates = [self.as_of_of(f) for f in with_figures]
        if any(a is None for a in figure_dates):
            return None  # a cited figure the platform cannot date: no label is certain
        stated = _printed_amounts(text)
        if not all(any(_same_amount(a, b) for f in with_figures
                       for b in _amounts(_strip_ids(_statement(f)))) for a in stated):
            return None  # a figure no subject finding gives (new, derived, or a peer's)
        # The figures the sentence states are those of the findings that print them; a
        # sentence that prints none rests on every figure it cites.
        stating = [f for f in with_figures
                   if any(_same_amount(a, b) for a in stated
                          for b in _amounts(_strip_ids(_statement(f))))] if stated else []
        for family in sorted(families(text)):
            backing = [f for f in subject if family in families(_statement(f))]
            # A sentence that prints no figure still states one when every finding it
            # cites for the metric does ("maintains a cash position sufficient …").
            if not backing or not (stated or all(f in with_figures for f in backing)):
                continue
            every = [*figure_dates, *(self.as_of_of(f) for f in backing)]
            shown = [self.as_of_of(f) for f in stating] if stated else every
            label = _combined(every, shown)
            if label is None:
                continue
            newer = self._stale(label[0], family)
            if newer is not None:
                return RULE_BALANCE_AS_OF, (
                    f"[figures {label[1]}; {self._held_phrase(newer)}]")
        if _is_segment_claim(text):
            backing = [f for f in subject
                       if _is_segment_claim(_statement(f))
                       and str(f.get("domain")) in _PERIMETER_DOMAINS]
            every = [*figure_dates, *(self.as_of_of(f) for f in backing)]
            label = _combined(every, every) if backing else None
            newer = self.newest.get(FAMILY_SEGMENTS)
            if label is not None and newer is not None and label[0] < newer["lower_bound"]:
                return RULE_SEGMENT_AS_OF, (
                    f"[segment structure {label[1]}; {self._held_phrase(newer)}]")
        return None

    def label_text(self, text: Any, cited_for: Any, path: str) -> Any:
        """Label each unit of a text that needs it; ``cited_for(unit)`` gives the findings
        a unit cites. A unit begins at a sentence start the platform is sure of."""
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
            return text
        pieces: list[tuple[str, str, list[int]]] = []
        start = 0
        joins: list[int] = []
        for m in _SENTENCE_RE.finditer(text):
            if not _sure_break(text, m.start()):
                # "U.S. Steel" or "in the U.S. Cash …": one unit, remembering where a
                # capitalised word follows the abbreviation.
                joins.append(m.end() - start)
                continue
            pieces.append((text[start: m.start()], m.group(0), joins))
            start, joins = m.end(), []
        pieces.append((text[start:], "", joins))
        changed = False
        out: list[str] = []
        for sentence, separator, unit_joins in pieces:
            placed = self._place(sentence, unit_joins, cited_for)
            if placed is not None:
                offset, (rule, raw) = placed
                label = _safe(raw)
                if offset == 0:
                    lead = sentence[: len(sentence) - len(sentence.lstrip())]
                    sentence = f"{lead}{label} {sentence.lstrip()}"
                else:
                    sentence = f"{sentence[:offset]}{label} {sentence[offset:]}"
                self.actions.append({"rule": rule, "path": path, "label": label})
                changed = True
            out.append(sentence + separator)
        return "".join(out) if changed else text

    def _place(
        self, unit: str, joins: list[int], cited_for: Any
    ) -> tuple[int, tuple[str, str]] | None:
        """Where a unit's label goes, and the label; ``None`` for no label.

        A unit joined across an abbreviation ("… in the U.S. Cash of $4.2bn …") may be
        two sentences. Its label never covers text it does not date:
        * the part before the last join cites nothing and states no figure, metric or
          segment ("Revenue grew 8% in the U.S.") → judged and labelled from the
          capitalised word after the join;
        * that part states the metric itself ("Net debt of $1.6bn is below that of
          U.S. Steel") → the unit is one claim, labelled at its start;
        * that part carries a citation of its own → unplaceable: no label.
        """
        if not joins:
            hit = self.sentence_label(unit, cited_for(unit))
            return (0, hit) if hit is not None else None
        cut = joins[-1]
        head, tail = unit[:cut], unit[cut:]
        if _HEX_ID_RE.search(head):
            return None
        if _states_amount(head) or families(head) or _is_segment_claim(head):
            hit = self.sentence_label(unit, cited_for(unit))
            return (0, hit) if hit is not None else None
        hit = self.sentence_label(tail, cited_for(unit))
        return (cut, hit) if hit is not None else None


def _sure_break(text: str, end: int) -> bool:
    """True when the full stop / mark just before ``end`` certainly ends a sentence: not
    after a single letter, a dotted abbreviation ("U.S") or a known one ("Inc", "No")."""
    if text[end - 1] in "!?":
        return True
    i = end - 1
    floor = max(0, i - 40)
    while i > floor and not text[i - 1].isspace():
        i -= 1
    token = text[i: end - 1].strip("\"'()[]")
    if not token or (len(token) == 1 and token.isalpha()):
        return False
    return not (_DOTTED_RE.search(token) or token.lower() in _ABBREVIATIONS)


def _strip_ids(text: str) -> str:
    """The text without the finding ids a chair cites ("(finding_ids: 13aac435, …)")."""
    return _HEX_ID_RE.sub(" ", text)


def _body(text: str) -> str:
    """The text after the platform's own leading bracket labels ("[single source] …")."""
    body = text.lstrip()
    for _ in range(4):
        if not body.startswith("[") or _OWN_LABEL_RE.match(body):
            break
        end = body.find("]")
        if end == -1:
            break
        body = body[end + 1:].lstrip()
    return body


def _is_labelled(text: str) -> bool:
    """True when this module already labelled the text (so it is never labelled twice)."""
    return bool(_OWN_LABEL_RE.match(_body(text)))


def _findings(v3: Mapping[str, Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in v3.get("findings") or ():
        if isinstance(item, dict):
            out.append(item)
    report = v3.get("professional_research")
    if isinstance(report, Mapping):
        for section in report.get("sections") or ():
            if isinstance(section, Mapping):
                for item in section.get("findings") or ():
                    if isinstance(item, dict):
                        out.append(item)
    return out


def _safe(label: str) -> str:
    """A label quotes a document headline (issuer text); it passes the neutralisation V2
    applies to external headlines, as ADR-082's reasons do."""
    from app.schemas.catalyst import neutralize_forbidden_terms

    return neutralize_forbidden_terms(label) or label


def _with_label(statement: str, label: str) -> str:
    """Put the date label after the platform's own source labels, before the claim."""
    body = _body(statement)
    head = statement[: len(statement) - len(body)]
    return f"{head}{label} {body}"


def apply_period_supersession(
    v3: Mapping[str, Any], *, as_of: Any = None
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Label every superseded claim in a V3 payload. Works on a copy; never raises on a
    partial payload. Returns ``(payload, record)``."""
    out = copy.deepcopy(dict(v3))
    today = as_date(as_of) or date.today()
    labeller = _Labeller(out, today)

    for finding in _findings(out):
        labeller.segment_period(finding)
    # Every label below is computed from these snapshots, taken before any is added.
    labeller.index(out)

    finding_labels: dict[str, tuple[str, str]] = {}
    for fid, indexed in labeller.by_id.items():
        hit = labeller.finding_label(indexed)
        if hit is not None:
            finding_labels[fid] = (hit[0], _safe(hit[1]))
    for fid, (rule, label) in finding_labels.items():
        labeller.actions.append({"rule": rule, "path": f"finding:{fid}", "label": label})

    def by_labels(labels: Any) -> Any:
        found = [labeller.by_label[str(lab)] for lab in labels or () if str(lab) in
                 labeller.by_label]
        return lambda _sentence: found

    def by_ids(sentence: str) -> list[Mapping[str, Any]]:
        found: list[Mapping[str, Any]] = []
        for m in _HEX_ID_RE.finditer(sentence):
            prefix = m.group(1)
            for fid, finding in labeller.by_id.items():
                if fid.startswith(prefix):
                    found.append(finding)
        return found

    report = out.get("professional_research")
    if isinstance(report, dict):
        for i, section in enumerate(report.get("sections") or ()):
            if not isinstance(section, dict):
                continue
            lead = section.get("lead")
            if isinstance(lead, dict):
                lead["text"] = labeller.label_text(
                    lead.get("text"), by_labels(lead.get("labels")),
                    f"professional_research.sections[{i}].lead")
            for j, sentence in enumerate(section.get("sentences") or ()):
                if isinstance(sentence, dict):
                    sentence["text"] = labeller.label_text(
                        sentence.get("text"), by_labels(sentence.get("labels")),
                        f"professional_research.sections[{i}].sentences[{j}]")
    chair = out.get("chair")
    if isinstance(chair, dict):
        chair["synthesis"] = labeller.label_text(chair.get("synthesis"), by_ids,
                                                 "chair.synthesis")
        for i, point in enumerate(chair.get("key_points") or ()):
            if isinstance(point, dict):
                point["text"] = labeller.label_text(point.get("text"), by_ids,
                                                    f"chair.key_points[{i}]")

    # Only now are the findings' own statements labelled.
    for finding in _findings(out):
        fid = str(finding.get("finding_id") or "")
        if fid in finding_labels:
            label = finding_labels[fid][1]
            finding["statement"] = _with_label(str(finding.get("statement") or ""), label)
            finding["superseded_period_label"] = label

    record = {
        "version": SUPERSESSION_VERSION,
        "as_of": today.isoformat(),
        "newest_issuer_periods": {
            family: {**entry, "lower_bound": entry["lower_bound"].isoformat(),
                     "document": _safe(str(entry["document"]))}
            for family, entry in labeller.newest.items()
        },
        "labelled": labeller.actions,
        "periods_filled": labeller.periods_filled,
        "note": (
            "A balance-sheet figure, a 'most recent fiscal year' summary or a segment "
            "structure older than what the issuer's own held documents give (or more "
            "than a year old) is labelled with its own date; nothing is deleted or "
            "changed. A label is only applied when its date is certain."
        ),
    }
    return out, record


__all__ = [
    "SUPERSESSION_VERSION",
    "STALE_AFTER_DAYS",
    "apply_period_supersession",
    "balance_dates",
    "families",
    "has_own_date",
    "newest_issuer_periods",
    "text_dates",
    "text_period_keys",
]
