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
deleted and no figure is changed; a label is only ever a true date.

A text unit is labelled when it states such a claim WITHOUT a date of its own, and its
as-of is known to be older than either
* the newest period the issuer's own HELD documents give for the same metric (the run's
  core filings, official disclosures and statement documents — never a cited web page),
  or
* a year before the report (``STALE_AFTER_DAYS``), which no current balance sheet is.

A "most recent fiscal year" claim is labelled when its source predates the issuer's held
annual report by more than the longest annual reporting lag, even if it prints a year.

BOUNDS, NOT GUESSES
===================
Every comparison is between an UPPER bound of the old claim's as-of and a LOWER bound of
the newer one, so a label is only applied when it is certainly true:
* the claim's as-of is at most: the latest date its text prints; else the end of its
  period key; else its source's publication date. A synthesis sentence inherits the
  latest as-of of the findings it cites for the same metric, and only when every one of
  them has one.
* a held document's period ends no earlier than its publication date minus the longest
  reporting lag for its kind (an annual report 183 days, an interim or quarterly report
  92 days); an Appendix 5B fact knows its quarter end exactly.

SEGMENTS
========
A segment finding without a period key takes the one period its own text names
(``period_key_source: "statement_text"``). Where the run's issuer findings name the
segments of a NEWER period (``scope_key`` ``segment:<name>``), an older finding about a
segment absent from that perimeter is labelled historical. An undated synthesis sentence
about segments that cites only older segment findings is labelled with their period.

Every pattern is linear and every input bounded (``MAX_TEXT_CHARS``). No issuer is named.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from datetime import date, timedelta
from typing import Any

from app.services.ledger.canonical_state import as_date, document_inventory, period_bounds

SUPERSESSION_VERSION = 1

#: A claim whose as-of is older than this before the report is not "current".
STALE_AFTER_DAYS = 365
#: The longest lag between a period's end and the publication of its report.
ANNUAL_LAG_DAYS = 183
INTERIM_LAG_DAYS = 92
#: Texts longer than this are not examined.
MAX_TEXT_CHARS = 2000

FAMILY_CASH = "cash"
FAMILY_DEBT = "debt"
FAMILY_EQUITY = "equity"
FAMILY_SEGMENTS = "segments"
FAMILY_ANNUAL = "fiscal_year"
BALANCE_FAMILIES = (FAMILY_CASH, FAMILY_DEBT, FAMILY_EQUITY)

RULE_LATEST_FISCAL_YEAR = "most_recent_fiscal_year_superseded"
RULE_BALANCE_AS_OF = "balance_sheet_figure_dated"
RULE_SEGMENT_AS_OF = "segment_structure_dated"
RULE_SEGMENT_HISTORICAL = "segment_absent_from_newer_perimeter"

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
_LATEST_FY_RE = re.compile(
    r"\b(?:most\s{1,3}recent|latest|last|current)\s{1,3}(?:full\s{1,3})?"
    r"(?:fiscal|financial|reporting)\s{1,3}year\b", re.I)
_SEGMENT_RE = re.compile(r"\bsegments?\b(?!\s{1,3}(?:its|their|the)\b)", re.I)
#: A money amount: a currency sign or code beside a number, or a number with a scale.
_AMOUNT_RE = re.compile(
    r"(?:[$€£¥]|\b(?:USD|AUD|A\$|US\$|EUR|GBP|CHF|CAD|JPY|NZD|HKD|SEK|DKK|NOK)\b)\s?\d"
    r"|\d(?:[\d,.]{0,20})\s?(?:m|bn|b|k|million|billion|thousand)\b", re.I)
_TOKEN_RE = re.compile(r"[a-z]+(?:'[a-z]?)?|\d")
#: How every label this module writes begins (so a text is never labelled twice).
_OWN_LABEL_STARTS = ("[as of ", "[figures ", "[segment structure ", "[historical segment")
#: A finding id (or its 8-character prefix) as a chair cites it; at least one letter, so
#: an 8-digit number is never one.
_HEX_ID_RE = re.compile(
    r"\b(?=[0-9]{0,7}[a-f])([0-9a-f]{8})(?:-[0-9a-f]{4}){0,3}(?:-[0-9a-f]{12})?\b")
_SENTENCE_RE = re.compile(r"(?<=[.!?])[ \t]+(?=[A-Z\"'(\[])")

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


def text_dates(text: str) -> list[date]:
    """Day-precise dates a text prints (``31 Dec 2024``, ``Dec 31, 2024``,
    ``2024-12-31``) and month-year dates (``June 2026`` → its last day)."""
    out: list[date] = []
    for m in _DMY_RE.finditer(text):
        d = _day(int(m.group(3)), _MONTHS[m.group(2)[:3].lower()], int(m.group(1)))
        if d:
            out.append(d)
    for m in _MDY_RE.finditer(text):
        d = _day(int(m.group(3)), _MONTHS[m.group(1)[:3].lower()], int(m.group(2)))
        if d:
            out.append(d)
    for m in _ISO_RE.finditer(text):
        d = _day(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        if d:
            out.append(d)
    for m in _MONTH_YEAR_RE.finditer(text):
        out.append(_month_end(int(m.group(2)), _MONTHS[m.group(1)[:3].lower()]))
    return out


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


def _upper_bound_of_text(text: str) -> date | None:
    """The latest point any date or period in the text reaches, else ``None``."""
    ends = list(text_dates(text))
    for key in text_period_keys(text):
        bounds = period_bounds(key)
        if bounds:
            ends.append(bounds[1])
    if not ends:
        for m in _YEAR_RE.finditer(text):
            ends.append(date(int(m.group(1)), 12, 31))
    return max(ends) if ends else None


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


def newest_issuer_periods(v3: Mapping[str, Any]) -> dict[str, dict[str, Any]]:
    """Per family, the LOWER bound of the newest period the issuer's held documents give,
    and which document gives it."""
    from app.services.sources.asx_appendix_5b import parse_locator

    best: dict[str, dict[str, Any]] = {}

    def offer(family: str, bound: date, what: str, published: date | None) -> None:
        current = best.get(family)
        if current is None or bound > current["lower_bound"]:
            best[family] = {"lower_bound": bound, "document": what,
                            "published": published.isoformat() if published else None}

    for doc in document_inventory(v3):
        if doc.origin == "cited_finding":
            continue
        reported = _families_of_document(doc.kind, doc.form)
        if reported is None:
            continue
        fams, lag = reported
        what = doc.form or doc.kind.replace("_", " ")
        for family in fams:
            offer(family, doc.published - timedelta(days=lag), what, doc.published)

    statements = v3.get("financial_statements_state")
    slots = statements.get("slots") if isinstance(statements, Mapping) else None
    for key, dp in (slots or {}).items():
        if not isinstance(dp, Mapping):
            continue
        for family, fields in _SLOT_FIELDS.items():
            if not any(str(key).startswith(f + "_") for f in fields):
                continue
            exact = None
            for bit in dp.get("fact_provenance") or ():
                if str(bit).startswith("excerpt="):
                    parsed = parse_locator(str(bit)[len("excerpt="):])
                    exact = parsed[1] if parsed else None
            published = as_date(dp.get("document_date"))
            if exact is not None:
                offer(family, exact, str(dp.get("document_title") or "issuer statement"),
                      published)
    return best


# ── As-of of a claim ──────────────────────────────────────────────────────── #


def _finding_as_of(finding: Mapping[str, Any]) -> tuple[date, str] | None:
    """The upper bound of a finding's as-of and how it reads in a label."""
    text = str(finding.get("statement") or "")[:MAX_TEXT_CHARS]
    published = as_date(finding.get("source_published_at"))
    bound = _upper_bound_of_text(text)
    if bound is not None:
        dates = text_dates(text)
        if dates and max(dates) == bound:
            return _clamp(bound, published), f"at {_fmt(bound)}"
        keys = text_period_keys(text)
        label = _key_label(max(keys, key=lambda k: (period_bounds(k) or (date.min,) * 2)[1])
                           ) if keys else str(bound.year)
        return _clamp(bound, published), f"for {label}"
    key = finding.get("period_key")
    bounds = period_bounds(key) if key else None
    if bounds is not None:
        return _clamp(bounds[1], published), f"for {_key_label(str(key))}"
    if published is not None:
        return published, f"as of a source dated {published.isoformat()}"
    return None


def _clamp(bound: date, published: date | None) -> date:
    return min(bound, published) if published else bound


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
        self.by_id: dict[str, Mapping[str, Any]] = {}
        self.by_label: dict[str, Mapping[str, Any]] = {}
        self.perimeters: dict[date, set[str]] = {}

    # -- inputs --
    def index(self, v3: Mapping[str, Any]) -> None:
        report = v3.get("professional_research")
        labels = {}
        if isinstance(report, Mapping):
            raw = report.get("finding_labels")
            labels = dict(raw) if isinstance(raw, Mapping) else {}
        for finding in _findings(v3):
            fid = str(finding.get("finding_id") or "")
            if fid:
                self.by_id.setdefault(fid, finding)
            label = finding.get("label") or labels.get(fid)
            if label:
                self.by_label.setdefault(str(label), finding)

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
        return f"the issuer's newer {newer['document']}{when} is held"

    # -- findings --
    def finding_label(self, finding: Mapping[str, Any]) -> tuple[str, str] | None:
        text = str(finding.get("statement") or "")
        if not text or len(text) > MAX_TEXT_CHARS or _body(text).startswith(_OWN_LABEL_STARTS):
            return None
        as_of = _finding_as_of(finding)
        if _LATEST_FY_RE.search(text):
            published = as_date(finding.get("source_published_at"))
            newer = self.newest.get(FAMILY_ANNUAL)
            if published is not None and newer is not None and (
                    published < newer["lower_bound"]):
                return RULE_LATEST_FISCAL_YEAR, (
                    f"[as of a source dated {published.isoformat()}; not the latest fiscal "
                    f"year: {self._held_phrase(newer)}]")
        if has_own_date(text) or as_of is None:
            return None
        if not _AMOUNT_RE.search(_strip_ids(text)):
            return None
        for family in sorted(families(text)):
            newer = self._stale(as_of[0], family)
            if newer is not None:
                return RULE_BALANCE_AS_OF, f"[{as_of[1]}; {self._held_phrase(newer)}]"
        return None

    def segment_period(self, finding: dict[str, Any]) -> None:
        """Fill an undated segment finding's period from the one its text names."""
        if finding.get("period_key") or str(finding.get("domain")) not in _PERIMETER_DOMAINS:
            return
        text = str(finding.get("statement") or "")[:MAX_TEXT_CHARS]
        scope = str(finding.get("scope_key") or "")
        if not (_is_segment_claim(text) or scope.startswith("segment:")):
            return
        keys = {k for k in text_period_keys(text) if period_bounds(k)}
        if len(keys) != 1 or text_dates(text):
            # One named period and nothing else: a text that also prints dates (an event
            # on a day) is not stating the period its figures are for.
            return
        key = next(iter(keys))
        finding["period_key"] = key
        finding["period_key_source"] = "statement_text"
        entry = {"finding_id": finding.get("finding_id"), "period_key": key}
        if entry not in self.periods_filled:
            self.periods_filled.append(entry)

    def build_perimeters(self) -> None:
        for finding in self.by_id.values():
            scope = str(finding.get("scope_key") or "")
            kinds = set(finding.get("source_kinds") or ())
            if not scope.startswith("segment:") or not kinds & {"issuer_filing", "issuer_ir"}:
                continue
            bounds = period_bounds(finding.get("period_key"))
            if bounds is None:
                continue
            self.perimeters.setdefault(bounds[1], set()).add(scope)

    def segment_label(self, finding: Mapping[str, Any]) -> tuple[str, str] | None:
        scope = str(finding.get("scope_key") or "")
        text = str(finding.get("statement") or "")
        if (not scope.startswith("segment:") or not text or len(text) > MAX_TEXT_CHARS
                or _body(text).startswith(_OWN_LABEL_STARTS)
                or str(finding.get("domain")) not in _PERIMETER_DOMAINS):
            return None
        bounds = period_bounds(finding.get("period_key"))
        if bounds is None:
            return None
        newer = [end for end in self.perimeters if end > bounds[1]]
        if not newer:
            return None
        latest = max(newer)
        if scope in self.perimeters[latest]:
            return None
        return RULE_SEGMENT_HISTORICAL, (
            f"[historical segment, {_key_label(str(finding.get('period_key')))}: not among "
            f"the segments the issuer reports for a later period]")

    # -- sentences --
    def sentence_label(
        self, sentence: str, cited: list[Mapping[str, Any]]
    ) -> tuple[str, str] | None:
        text = _strip_ids(sentence).strip()
        if (not text or len(text) > MAX_TEXT_CHARS or not cited or has_own_date(text)
                or _body(text).startswith(_OWN_LABEL_STARTS)):
            return None
        stated = bool(_AMOUNT_RE.search(text))
        for family in sorted(families(text)):
            backing = [f for f in cited if family in families(str(f.get("statement") or ""))]
            as_ofs = [_finding_as_of(f) for f in backing]
            if not backing or any(a is None for a in as_ofs):
                continue
            # A sentence that prints no figure still states one when every finding it
            # cites for the metric does ("maintains a cash position sufficient …").
            if stated or all(_AMOUNT_RE.search(_strip_ids(str(f.get("statement") or "")))
                             for f in backing):
                upper, label = max((a for a in as_ofs if a is not None), key=lambda a: a[0])
                newer = self._stale(upper, family)
                if newer is not None:
                    return RULE_BALANCE_AS_OF, (
                        f"[figures {label}; {self._held_phrase(newer)}]")
        if _is_segment_claim(text):
            backing = [f for f in cited
                       if _is_segment_claim(str(f.get("statement") or ""))
                       and str(f.get("domain")) in _PERIMETER_DOMAINS]
            as_ofs = [_finding_as_of(f) for f in backing]
            if backing and all(a is not None for a in as_ofs):
                upper, label = max((a for a in as_ofs if a is not None), key=lambda a: a[0])
                newer = self.newest.get(FAMILY_SEGMENTS)
                if newer is not None and upper < newer["lower_bound"]:
                    return RULE_SEGMENT_AS_OF, (
                        f"[segment structure as reported {label}; "
                        f"{self._held_phrase(newer)}]")
        return None

    def label_text(self, text: Any, cited_for: Any, path: str) -> Any:
        """Label each sentence of a text that needs it; ``cited_for(sentence)`` gives the
        findings a sentence cites."""
        if not isinstance(text, str) or not text.strip() or len(text) > 20_000:
            return text
        pieces: list[tuple[str, str]] = []
        start = 0
        for m in _SENTENCE_RE.finditer(text):
            pieces.append((text[start: m.start()], m.group(0)))
            start = m.end()
        pieces.append((text[start:], ""))
        changed = False
        out: list[str] = []
        for sentence, separator in pieces:
            hit = self.sentence_label(sentence, cited_for(sentence))
            if hit is not None:
                rule, label = hit[0], _safe(hit[1])
                lead = sentence[: len(sentence) - len(sentence.lstrip())]
                sentence = f"{lead}{label} {sentence.lstrip()}"
                self.actions.append({"rule": rule, "path": path, "label": label})
                changed = True
            out.append(sentence + separator)
        return "".join(out) if changed else text


def _strip_ids(text: str) -> str:
    """The text without the finding ids a chair cites ("(finding_ids: 13aac435, …)")."""
    return _HEX_ID_RE.sub(" ", text)


def _body(text: str) -> str:
    """The text after the platform's own leading bracket labels ("[single source] …")."""
    body = text.lstrip()
    for _ in range(4):
        if not body.startswith("[") or body.startswith(_OWN_LABEL_STARTS):
            break
        end = body.find("]")
        if end == -1:
            break
        body = body[end + 1:].lstrip()
    return body


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
    labeller.index(out)
    labeller.build_perimeters()

    finding_labels: dict[str, tuple[str, str]] = {}
    for fid, indexed in labeller.by_id.items():
        hit = labeller.finding_label(indexed) or labeller.segment_label(indexed)
        if hit is not None:
            finding_labels[fid] = (hit[0], _safe(hit[1]))
    for finding in _findings(out):
        fid = str(finding.get("finding_id") or "")
        if fid in finding_labels:
            rule, label = finding_labels[fid]
            finding["statement"] = _with_label(str(finding.get("statement") or ""), label)
            finding["superseded_period_label"] = label
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

    record = {
        "version": SUPERSESSION_VERSION,
        "as_of": today.isoformat(),
        "newest_issuer_periods": {
            family: {**entry, "lower_bound": entry["lower_bound"].isoformat()}
            for family, entry in labeller.newest.items()
        },
        "labelled": labeller.actions,
        "periods_filled": labeller.periods_filled,
        "note": (
            "A balance-sheet figure, a 'most recent fiscal year' summary or a segment "
            "structure older than what the issuer's own held documents give (or more "
            "than a year old) is labelled with its own date; nothing is deleted or "
            "changed."
        ),
    }
    return out, record


__all__ = [
    "SUPERSESSION_VERSION",
    "STALE_AFTER_DAYS",
    "apply_period_supersession",
    "families",
    "has_own_date",
    "newest_issuer_periods",
    "text_dates",
    "text_period_keys",
]
