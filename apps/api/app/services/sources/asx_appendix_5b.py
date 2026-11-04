"""Statement facts from an ASX quarterly cash flow report (Appendix 5B / 4C) — ADR-087.

THE DEFECT (QA campaign 2026-11, LIN-2 / LIN2-1)
================================================
An acquired Appendix 5B (the quarterly cash flow report a mining or oil and gas
exploration entity lodges with the ASX) was held as 26 corpus chunks and still yielded
``facts_read=0``: the statement-fact reader knows annual-report tables, and a 5B is a
fixed form instead — ``$A'000``, two value columns ("Current quarter" / "Year to date
(N months)"), and numbered items (1.9 net cash from operating activities, 2.6 investing,
3.10 financing, 4.6 cash at the end of the period, 8.7 the issuer's own estimated
quarters of funding available). No cash at quarter end, no runway.

WHAT THIS READS
===============
The form's own layout, from the PDF's row-ordered page text, and nothing else:

* **Form.** "Appendix 5B" (or "Appendix 4C", the same item layout for other entities)
  AND "quarterly cash flow report". Both forms named → refused.
* **Period** from the report's own "Quarter ended (“current quarter”) <date>" header —
  never from prose elsewhere. The date must be a month end. The fiscal position comes
  from the same header: "Year to date (N months)" says which quarter of the issuer's
  fiscal year this is, so the quarter is keyed by its fiscal year (ADR-065): a quarter
  ended 30 September 2026 with a 3-month year to date is ``2027-Q1``.
* **Currency and scale** from the column header's ``$A'000`` / ``$US'000`` (``AUD`` /
  ``USD``, scale ``thousand``). Two different currency headers → refused.
* **Columns.** A two-column item is read only under a "Current quarter … Year to date"
  header; the first value is the quarter, the second the year to date. A line with any
  other number of values is not read. "-" is the form's nil and reads as 0.
* **Items** by section AND label, then checked against the form's numbering: operating
  (1.9), investing (2.6), financing (3.10), cash at the end of the period (4.6) and the
  estimated quarters of funding available (8.7 in a 5B, 8.5 in a 4C). The issuer's label
  travels on the fact's locator and on the issuer-stated runway record.
* **The form checks itself.** Every line that restates another item ("(item 1.9)",
  "(item 4.6)") must equal it, column by column; the cash at the end of the period must
  be the same in both columns; with a 3-month year to date both columns must agree; and
  the issuer's quarters of funding must be its own stated ratio (8.6 / 8.3 in a 5B, 8.4 /
  8.1 in a 4C) when both are printed. A failed check refuses the whole reading (or the
  runway alone, for the ratio): misaligned columns must never become a fact.

Missing is fine; wrong is not. Anything not identified is left out, and a reading that
cannot be trusted produces no fact at all.

PDF-TEXT INTERLEAVING TOLERATED
===============================
* a label wrapped over two lines with its values on either line;
* a value printed in the middle of a wrapped label (item 8.7: "… divided by 4.50 /
  item 8.3)");
* a column header split over lines ("Current quarter Year to date" / "$A'000 (3
  months)" / "$A'000"), and the "Quarter ended" header sharing lines with the ABN;
* rotated watermark fragments as one-to-four-letter lines; footer lines;
* a footnote marker glued to a value ("107,9361") — not a well-formed amount, so that
  line is not read.

Every pattern here is linear (anchored, no nested or adjacent unbounded quantifiers over
overlapping classes); input is bounded (``MAX_TEXT_CHARS``, ``MAX_PAGES``). No issuer is
named in this module.
"""

from __future__ import annotations

import calendar
import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

FORM_APPENDIX_5B = "appendix_5b"
FORM_APPENDIX_4C = "appendix_4c"
FORM_LABELS = {FORM_APPENDIX_5B: "Appendix 5B", FORM_APPENDIX_4C: "Appendix 4C"}

EXTRACTION_METHOD = "asx_quarterly_cash_flow_report"
#: The issuer's own fixed form, read by item and self-checked: above a prose reading.
CONFIDENCE = 0.9
#: Characters of page text read (a 5B is ~12k characters; a quarterly activities report
#: with a 5B appended is read only from the pages that carry the form).
MAX_TEXT_CHARS = 60_000
#: Pages read from a PDF, from the first page that carries the form.
MAX_PAGES = 8
#: Lines a wrapped item label may continue over.
_MAX_CONTINUATION_LINES = 3

FIELD_OPERATING = "operating_cash_flow"
FIELD_INVESTING = "investing_cash_flow"
FIELD_FINANCING = "financing_cash_flow"
FIELD_CASH = "cash_and_equivalents"
FIELD_RUNWAY = "issuer_estimated_quarters_of_funding"

#: Item numbering of the 17/07/20 forms. An item identified by its label must carry
#: this number, or it is not read.
_ITEM_NUMBERS: dict[str, dict[str, str]] = {
    FORM_APPENDIX_5B: {FIELD_OPERATING: "1.9", FIELD_INVESTING: "2.6",
                       FIELD_FINANCING: "3.10", FIELD_CASH: "4.6", FIELD_RUNWAY: "8.7"},
    FORM_APPENDIX_4C: {FIELD_OPERATING: "1.9", FIELD_INVESTING: "2.6",
                       FIELD_FINANCING: "3.10", FIELD_CASH: "4.6", FIELD_RUNWAY: "8.5"},
}

#: What the issuer's runway formula counts — stated beside the figure.
RUNWAY_BASIS: dict[str, str] = {
    FORM_APPENDIX_5B: (
        "The issuer's formula (Appendix 5B item 8.7): total available funding (cash at "
        "quarter end plus unused finance facilities, item 8.6) divided by total relevant "
        "outgoings (net operating cash flow plus exploration and evaluation payments "
        "classified as investing, item 8.3). Investing payments for property, plant and "
        "equipment, development and acquisitions are not in it."
    ),
    FORM_APPENDIX_4C: (
        "The issuer's formula (Appendix 4C item 8.5): total available funding (cash at "
        "quarter end plus unused finance facilities, item 8.4) divided by net operating "
        "cash flow (item 8.1). Investing payments are not in it."
    ),
}

_CURRENCIES = {"A": "AUD", "US": "USD", "NZ": "NZD"}

REFUSE_NOT_A_REPORT = "not_a_quarterly_cash_flow_report"
REFUSE_AMBIGUOUS_FORM = "both_forms_named"
REFUSE_NO_PERIOD = "quarter_end_not_found"
REFUSE_PERIOD_CONFLICT = "quarter_end_conflict"
REFUSE_NO_COLUMNS = "column_headers_not_identified"
REFUSE_CURRENCY = "currency_header_not_identified"
REFUSE_SELF_CHECK = "form_self_check_failed"
REFUSE_NO_ITEMS = "no_item_identified"

# ── Patterns (all linear) ─────────────────────────────────────────────────── #

_FORM_5B_RE = re.compile(r"\bappendix\s{1,3}5b\b", re.I)
_FORM_4C_RE = re.compile(r"\bappendix\s{1,3}4c\b", re.I)
_REPORT_RE = re.compile(r"\bquarterly\s{1,3}cash\s{1,3}flow\s{1,3}report\b", re.I)
_QUARTER_ENDED_RE = re.compile(r"\bquarter\s{1,3}ended\b", re.I)
_MONTH = (
    r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
    r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
)
_DMY_RE = re.compile(
    r"\b(\d{1,2})(?:st|nd|rd|th)?\s{1,3}" + _MONTH + r"\.?,?\s{1,3}((?:19|20)\d{2})\b", re.I
)
_MONTH_NUMBER = {name: i for i, name in enumerate(
    ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"),
    start=1)}
_CURRENT_QUARTER_RE = re.compile(r"\bcurrent\s{1,3}quarter\b", re.I)
_YEAR_TO_DATE_RE = re.compile(r"\byear\s{1,3}to\s{1,3}date\b", re.I)
_PREVIOUS_QUARTER_RE = re.compile(r"\bprevious\s{1,3}quarter\b", re.I)
_MONTHS_RE = re.compile(r"\(\s{0,2}(\d{1,2})\s{1,2}months?\s{0,2}\)", re.I)
_CURRENCY_RE = re.compile(r"\$\s?(A|US|NZ)\s?['’‘`]\s?000\b", re.I)
_ANY_CURRENCY_RE = re.compile(r"\$\s?([A-Z]{1,3})\s?['’‘`]\s?000\b", re.I)
_ITEM_RE = re.compile(r"^(\d{1,2})\.(\d{1,2})(?=\s|$)")
_SECTION_RE = re.compile(r"^\d{1,2}\.\s")
_SUB_ITEM_RE = re.compile(r"^\([a-z]{1,2}\)", re.I)
_NOTE_RE = re.compile(r"^(?:note|answer)\b", re.I)
_CONTINUATION_RE = re.compile(r"^[a-z(]")
_AMOUNT_RE = re.compile(r"^(\()?(-)?(\d{1,3}(?:,\d{3})*|\d+)(\.\d{1,4})?(\))?$")
_NIL_TOKENS = frozenset({"-", "–", "—"})
_NA_TOKENS = frozenset({"n/a", "na", "n.a."})
_STRAY_RE = re.compile(r"^[A-Za-z]{1,4}$")
_ITEM_REF_RE = re.compile(r"\(item\s{1,3}(\d{1,2}\.\d{1,2})(?:\s{1,3}above)?\s{0,2}\)", re.I)
_RATIO_REF_RE = re.compile(
    r"\bitem\s{1,3}(\d{1,2}\.\d{1,2})\s{1,3}divided\s{1,3}by\s{1,3}item\s{1,3}(\d{1,2}\.\d{1,2})",
    re.I,
)
_FOOTER_RE = re.compile(r"^(?:\+\s?see\s|asx\s{1,3}listing\s{1,3}rules\b|page\s{1,3}\d)", re.I)
_WS_RE = re.compile(r"\s+")

#: Label keywords per field (normalised lower-case text), and the section it sits in.
_FIELD_RULES: dict[str, tuple[str, tuple[str, ...]]] = {
    FIELD_OPERATING: ("1", ("net cash from", "operating activities")),
    FIELD_INVESTING: ("2", ("net cash from", "investing activities")),
    FIELD_FINANCING: ("3", ("net cash from", "financing activities")),
    FIELD_CASH: ("4", ("cash and cash equivalents at end of",)),
    FIELD_RUNWAY: ("8", ("estimated quarters of funding available",)),
}


# ── Model ─────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class ItemLine:
    """One numbered item as printed: its number, label, values and page."""

    item: str
    label: str
    values: tuple[Decimal | None, ...]
    printed: tuple[str, ...]
    page: int | None
    #: The column header the item sat under: ``ytd`` / ``previous`` / ``single`` / None.
    header: str | None

    @property
    def section(self) -> str:
        return self.item.split(".", 1)[0]


@dataclass
class QuarterlyCashFlowReading:
    """The result of reading one report: facts-to-be, or a refusal with its reason."""

    form: str | None = None
    quarter_end: date | None = None
    ytd_months: int | None = None
    currency: str | None = None
    items: dict[str, ItemLine] = field(default_factory=dict)
    fields: dict[str, ItemLine] = field(default_factory=dict)
    refusal: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.refusal is None and bool(self.fields)

    def refuse(self, reason: str, note: str | None = None) -> "QuarterlyCashFlowReading":
        self.refusal = reason
        self.fields = {}
        if note:
            self.notes.append(note)
        return self


# ── Small helpers ─────────────────────────────────────────────────────────── #


def _norm(text: str) -> str:
    return _WS_RE.sub(" ", text.replace("’", "'").replace("‘", "'")).strip().lower()


def _amount(token: str) -> tuple[bool, Decimal | None]:
    """``(is_value, value)`` for one token: an amount, the form's nil "-" (0), or "N/A"
    (a value position with no number). Anything else is not a value."""
    if token in _NIL_TOKENS:
        return True, Decimal(0)
    if token.lower() in _NA_TOKENS:
        return True, None
    m = _AMOUNT_RE.match(token)
    if not m:
        return False, None
    opened, minus, digits, decimals, closed = m.groups()
    if bool(opened) != bool(closed) or (opened and minus):
        return False, None
    try:
        value = Decimal(digits.replace(",", "") + (decimals or ""))
    except InvalidOperation:
        return False, None
    return True, -value if (opened or minus) else value


def _split_trailing_values(text: str) -> tuple[str, list[str], list[Decimal | None]]:
    """The label part of a line and the value tokens printed at its end."""
    tokens = text.split()
    printed: list[str] = []
    values: list[Decimal | None] = []
    while tokens:
        is_value, value = _amount(tokens[-1])
        if not is_value:
            break
        printed.insert(0, tokens.pop())
        values.insert(0, value)
    return " ".join(tokens), printed, values


def _month_end(day: date) -> bool:
    return day.day == calendar.monthrange(day.year, day.month)[1]


def _add_months(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(index, 12)
    return date(year, month + 1, calendar.monthrange(year, month + 1)[1])


def _quarter_end(text: str) -> tuple[date | None, str | None]:
    """The date in the report's own "Quarter ended" header (the 200 characters after
    it), or a refusal reason."""
    found: set[date] = set()
    anchors = list(_QUARTER_ENDED_RE.finditer(text))
    # The form's own header cell reads 'Quarter ended ("current quarter")'; when it is
    # printed, a "quarter ended" in a sentence elsewhere is not the header.
    headers = [m for m in anchors
               if _CURRENT_QUARTER_RE.search(text[m.end(): m.end() + 40])]
    for m in headers or anchors:
        window = text[m.end(): m.end() + 200]
        d = _DMY_RE.search(window)
        if not d:
            continue
        try:
            day = date(int(d.group(3)), _MONTH_NUMBER[d.group(2)[:3].lower()],
                       int(d.group(1)))
        except (KeyError, ValueError):
            continue
        if _month_end(day):
            found.add(day)
    if not found:
        return None, REFUSE_NO_PERIOD
    if len(found) > 1:
        return None, REFUSE_PERIOD_CONFLICT
    return next(iter(found)), None


# ── Reading ───────────────────────────────────────────────────────────────── #


def _lines(pages: list[str]) -> Iterable[tuple[int, str]]:
    for number, page in enumerate(pages, start=1):
        for raw in page.split("\n"):
            line = raw.strip()
            if line:
                yield number, line


def read_quarterly_cash_flow_report(
    text: str | list[str], *, first_page: int = 1
) -> QuarterlyCashFlowReading:
    """Read one report's page text (a string, or one string per page). Pure; never
    raises; never reads beyond ``MAX_TEXT_CHARS``."""
    reading = QuarterlyCashFlowReading()
    pages = [text] if isinstance(text, str) else list(text)
    budget = MAX_TEXT_CHARS
    bounded: list[str] = []
    for raw_page in pages:
        if budget <= 0:
            break
        chunk = str(raw_page or "")[:budget]
        bounded.append(chunk)
        budget -= len(chunk)
    joined = "\n".join(bounded)

    is_5b, is_4c = bool(_FORM_5B_RE.search(joined)), bool(_FORM_4C_RE.search(joined))
    if not _REPORT_RE.search(joined) or not (is_5b or is_4c):
        return reading.refuse(REFUSE_NOT_A_REPORT)
    if is_5b and is_4c:
        return reading.refuse(REFUSE_AMBIGUOUS_FORM)
    reading.form = FORM_APPENDIX_5B if is_5b else FORM_APPENDIX_4C

    reading.quarter_end, why = _quarter_end(joined)
    if why:
        return reading.refuse(why)

    currencies = {m.group(1).upper() for m in _ANY_CURRENCY_RE.finditer(joined)}
    if len(currencies) != 1 or next(iter(currencies)) not in _CURRENCIES:
        return reading.refuse(REFUSE_CURRENCY)
    reading.currency = _CURRENCIES[next(iter(currencies))]

    months_seen: set[int] = set()
    header: str | None = None
    pending_header = 0  # lines after a "Current quarter / Year to date" header
    current: dict[str, Any] | None = None
    items: dict[str, ItemLine] = {}
    duplicated: set[str] = set()

    def close() -> None:
        nonlocal current
        if current is None:
            return
        item = current["item"]
        line = ItemLine(
            item=item,
            label=" ".join(current["label"]).strip(),
            values=tuple(current["values"]),
            printed=tuple(current["printed"]),
            page=current["page"] + first_page - 1,
            header=current["header"],
        )
        if item in items:
            duplicated.add(item)
        items[item] = line
        current = None

    lone_quarter = False  # the previous line was a bare "Current quarter" header cell
    for page, line in _lines(bounded):
        if _FOOTER_RE.match(line) or _STRAY_RE.match(line):
            continue
        quarter = _CURRENT_QUARTER_RE.search(line)
        ytd = _YEAR_TO_DATE_RE.search(line)
        previous = _PREVIOUS_QUARTER_RE.search(line)
        if quarter or (ytd and lone_quarter):
            # A column header. "Current quarter" then "Year to date" — on one line, or
            # on two consecutive lines — is the two-column layout; anything else is not.
            if ytd and not previous and (not quarter or quarter.start() < ytd.start()):
                header, pending_header = "ytd", 3
            elif previous:
                header, pending_header = "previous", 0
            elif ytd:
                header, pending_header = None, 0  # "Year to date" printed first
            else:
                header, pending_header = "single", 0
            lone_quarter = bool(quarter and not ytd and not previous)
            m = _MONTHS_RE.search(line)
            if m and header == "ytd":
                months_seen.add(int(m.group(1)))
            if not _ITEM_RE.match(line):
                close()
                continue
        else:
            lone_quarter = False
            if pending_header:
                pending_header -= 1
                m = _MONTHS_RE.search(line)
                if m and header == "ytd":
                    months_seen.add(int(m.group(1)))
        item_match = _ITEM_RE.match(line)
        if item_match:
            close()
            item = f"{item_match.group(1)}.{item_match.group(2)}"
            label, printed, values = _split_trailing_values(line[item_match.end():].strip())
            current = {"item": item, "label": [label], "printed": printed,
                       "values": values, "page": page,
                       "header": "single" if item_match.group(1) == "8" else header,
                       "continued": 0}
            continue
        if _SECTION_RE.match(line) or _SUB_ITEM_RE.match(line) or _NOTE_RE.match(line):
            close()
            continue
        if (current is not None and current["continued"] < _MAX_CONTINUATION_LINES
                and _CONTINUATION_RE.match(line)):
            # A wrapped label goes on in lower case (or an opening parenthesis); a
            # capitalised or numbered line is something else (a footnote, a note).
            label, printed, values = _split_trailing_values(line)
            current["label"].append(label)
            current["printed"].extend(printed)
            current["values"].extend(values)
            current["continued"] += 1
            continue
        close()
    close()
    reading.items = {k: v for k, v in items.items() if k not in duplicated}

    if len(months_seen) != 1 or next(iter(months_seen)) not in (3, 6, 9, 12):
        return reading.refuse(REFUSE_NO_COLUMNS,
                              f"year-to-date months seen: {sorted(months_seen)}")
    reading.ytd_months = next(iter(months_seen))

    # Identify the fields: section + label + the form's numbering.
    numbering = _ITEM_NUMBERS[reading.form]
    for field_name, (section, keywords) in _FIELD_RULES.items():
        matches = [
            candidate for candidate in reading.items.values()
            if candidate.section == section
            and all(k in _norm(candidate.label) for k in keywords)
            and not _ITEM_REF_RE.search(candidate.label)
        ]
        if len(matches) != 1:
            continue
        found = matches[0]
        if found.item != numbering[field_name]:
            reading.notes.append(f"{field_name}: printed as item {found.item}, the form "
                                 f"numbers it {numbering[field_name]}; not read")
            continue
        if field_name == FIELD_RUNWAY:
            if len(found.values) != 1 or found.values[0] is None or found.values[0] < 0:
                continue
        else:
            if found.header != "ytd" or len(found.values) != 2 or None in found.values:
                continue
        reading.fields[field_name] = found

    why = _self_check(reading)
    if why:
        return reading.refuse(REFUSE_SELF_CHECK, why)
    if not reading.fields:
        return reading.refuse(REFUSE_NO_ITEMS)
    return reading


def _self_check(reading: QuarterlyCashFlowReading) -> str | None:
    """The form's own redundancy. Returns why it failed, or ``None``. Drops the runway
    alone when its ratio does not hold."""
    items = reading.items
    for line in items.values():
        refs = _ITEM_REF_RE.findall(line.label)
        if len(refs) != 1 or refs[0] not in items:
            continue
        target = items[refs[0]]
        if None in line.values or None in target.values:
            continue
        if len(line.values) == len(target.values):
            if line.values != target.values:
                return f"item {line.item} restates item {target.item} differently"
        elif len(line.values) == 1 and target.values:
            if line.values[0] != target.values[0]:
                return f"item {line.item} restates item {target.item} differently"
    cash = reading.fields.get(FIELD_CASH)
    if cash is not None and cash.values[0] != cash.values[1]:
        return "the cash at the end of the period differs between the two columns"
    if reading.ytd_months == 3:
        for name in (FIELD_OPERATING, FIELD_INVESTING, FIELD_FINANCING):
            flow = reading.fields.get(name)
            if flow is not None and flow.values[0] != flow.values[1]:
                return f"item {flow.item}: a 3-month year to date differs from the quarter"
    runway = reading.fields.get(FIELD_RUNWAY)
    if runway is not None:
        ratio = _RATIO_REF_RE.search(_norm(runway.label))
        top = items.get(ratio.group(1)) if ratio else None
        bottom = items.get(ratio.group(2)) if ratio else None
        if (top is not None and bottom is not None and len(top.values) == 1
                and len(bottom.values) == 1 and top.values[0] is not None
                and bottom.values[0] not in (None, Decimal(0))):
            assert bottom.values[0] is not None and top.values[0] is not None
            stated = runway.values[0]
            assert stated is not None
            exact = top.values[0] / abs(bottom.values[0])
            whole = stated == stated.to_integral_value()
            agrees = (
                abs(exact - stated) <= Decimal("0.051") + exact * Decimal("0.01")
                or (whole and int(exact) == int(stated))
            )
            if not agrees:
                reading.fields.pop(FIELD_RUNWAY)
                reading.notes.append(
                    f"item {runway.item} is not the stated ratio of items "
                    f"{top.item} and {bottom.item}; not read")
    return None


# ── Facts ─────────────────────────────────────────────────────────────────── #


def period_keys(reading: QuarterlyCashFlowReading) -> tuple[str, str | None] | None:
    """``(quarter key, year-to-date key or None)`` keyed by the fiscal year (ADR-065):
    the fiscal year ends ``12 - N`` months after the quarter end."""
    if reading.quarter_end is None or reading.ytd_months not in (3, 6, 9, 12):
        return None
    months = int(reading.ytd_months or 0)
    fiscal_year = _add_months(reading.quarter_end, 12 - months).year
    quarter = f"{fiscal_year}-Q{months // 3}"
    # Only a half has a period type of its own; 3 months is the quarter itself, and a
    # 9- or 12-month year to date is not stated (an annual figure belongs to the
    # annual report).
    ytd = f"{fiscal_year}-H1" if months == 6 else None
    return quarter, ytd


def locator(reading: QuarterlyCashFlowReading, item: str) -> str:
    """``appendix_5b@2026-09-30#4.6`` — the form, the quarter end and the item."""
    end = reading.quarter_end.isoformat() if reading.quarter_end else "unknown"
    return f"{reading.form}@{end}#{item}"


def parse_locator(value: Any) -> tuple[str, date, str] | None:
    """``(form, quarter end, item)`` from a fact locator this module wrote, else None."""
    text = str(value or "")
    form, sep, rest = text.partition("@")
    if not sep or form not in FORM_LABELS:
        return None
    day, sep, item = rest.partition("#")
    try:
        return form, date.fromisoformat(day), item
    except ValueError:
        return None


def statement_facts(reading: QuarterlyCashFlowReading) -> list[Any]:
    """Validated statement facts for a trusted reading; ``[]`` otherwise."""
    from app.services.sources.extracted_fact_validator import (
        VALIDATION_VALIDATED,
        ValidatedFact,
    )

    keys = period_keys(reading)
    if not reading.ok or keys is None:
        return []
    quarter_key, ytd_key = keys
    out: list[Any] = []

    def add(name: str, line: ItemLine, value: Decimal, printed: str, period: str) -> None:
        runway = name == FIELD_RUNWAY
        out.append(ValidatedFact(
            label=name,
            value_numeric=float(value),
            value_text=printed,
            unit="quarters" if runway else "currency_amount",
            currency=None if runway else reading.currency,
            scale=None if runway else "thousand",
            period=period,
            page_number=line.page,
            table_location=locator(reading, line.item),
            extraction_method=EXTRACTION_METHOD,
            confidence=CONFIDENCE,
            validation_status=VALIDATION_VALIDATED,
            needs_human_review=True,
            scope="Group",
            methods=[EXTRACTION_METHOD],
            validation_notes=[
                f"{FORM_LABELS.get(str(reading.form))} item {line.item}: {line.label}"[:300],
                f"quarter ended {reading.quarter_end}",
            ],
        ))

    for name, line in reading.fields.items():
        if name == FIELD_RUNWAY:
            assert line.values[0] is not None
            add(name, line, line.values[0], line.printed[0], quarter_key)
            continue
        current_value, ytd_value = line.values
        assert current_value is not None and ytd_value is not None
        add(name, line, current_value, line.printed[0], quarter_key)
        if ytd_key and name != FIELD_CASH:
            add(name, line, ytd_value, line.printed[1], ytd_key)
    return out


def merge_with_text_facts(text_facts: Iterable[Any], read: list[Any]) -> list[Any]:
    """The form's facts plus every other fact of the document they do not cover: a text
    reading of the same field and period at Group (or unknown) scope is the lesser
    reading of one statement and is dropped rather than left to "disagree"."""
    from app.services.sources.fact_scope import parse_scope

    covered = {(f.label, f.period) for f in read}
    kept: list[Any] = list(read)
    for fact in text_facts:
        key = (getattr(fact, "label", None), getattr(fact, "period", None))
        if key in covered and not parse_scope(getattr(fact, "scope", None)).is_segment:
            continue
        kept.append(fact)
    return kept


# ── The hook ──────────────────────────────────────────────────────────────── #


def _form_pages(extraction: Any, title: str | None) -> list[int]:
    """1-based pages the extracted text says carry the form ("Appendix 5B/4C")."""
    pages: set[int] = set()
    for item in list(getattr(extraction, "blocks", None) or []) + list(
        getattr(extraction, "excerpts", None) or []
    ):
        text = str(getattr(item, "text", "") or "")
        page = getattr(item, "page_number", None)
        if page and (_FORM_5B_RE.search(text) or _FORM_4C_RE.search(text)):
            pages.add(int(page))
    if not pages and title and (_FORM_5B_RE.search(title) or _FORM_4C_RE.search(title)):
        pages.add(1)
    return sorted(pages)


def _pdf_page_texts(content: bytes, first: int, count: int) -> list[str]:
    """Row-ordered text of ``count`` pages from ``first`` (1-based). Raises on a bad PDF
    (the caller catches)."""
    import io

    import pdfplumber

    out: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        total = len(pdf.pages)
        for index in range(first - 1, min(total, first - 1 + count)):
            out.append(pdf.pages[index].extract_text() or "")
    return out


def facts_from_pdf(content: bytes | None, *, extraction: Any, title: str | None) -> list[Any]:
    """The form's facts from a fetched PDF, or ``[]``. Never raises."""
    try:
        if not content or not content.lstrip()[:5].startswith(b"%PDF-"):
            return []
        pages = _form_pages(extraction, title)
        if not pages:
            return []
        first = pages[0]
        texts = _pdf_page_texts(content, first, MAX_PAGES)
        return statement_facts(read_quarterly_cash_flow_report(texts, first_page=first))
    except Exception:  # noqa: BLE001 - a form reader must never cost the text reading
        return []


def with_quarterly_cash_flow_facts(
    validated_facts: Iterable[Any] | None,
    content: bytes | None,
    *,
    extraction: Any,
    title: str | None,
) -> list[Any]:
    """``validated_facts`` with an ASX quarterly cash flow report's own facts added.

    The hook the primary-document path calls with the bytes it already holds. Anything
    that is not such a report is returned unchanged. Never raises."""
    facts = list(validated_facts or [])
    read = facts_from_pdf(content, extraction=extraction, title=title)
    return merge_with_text_facts(facts, read) if read else facts


# ── The issuer-stated runway, for the statements view ─────────────────────── #


def issuer_stated_runway(slots_by_field: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """The issuer's own "estimated quarters of funding available", as a record beside
    (never inside) the platform's derived metrics. ``slots_by_field`` maps a field to the
    fact dict that fills its current-period slot."""
    fact = slots_by_field.get(FIELD_RUNWAY)
    if not fact or fact.get("numeric_value") is None:
        return []
    parsed = parse_locator(fact.get("excerpt_id"))
    if parsed is None:
        return []
    form, quarter_end, item = parsed
    form_label = FORM_LABELS[form]
    return [{
        "definition_key": FIELD_RUNWAY,
        "label": "Estimated quarters of funding available",
        "issuer_label": f"{form_label} item {item}: Estimated quarters of funding available",
        "value": fact.get("numeric_value"),
        "value_as_printed": fact.get("value"),
        "period_key": fact.get("period"),
        "as_at": quarter_end.isoformat(),
        "basis": "current_period",
        "provenance": "issuer_stated",
        "status": "stated",
        "document_title": fact.get("document_title"),
        "document_date": fact.get("document_date"),
        "source_url": fact.get("source_url"),
        "fact_id": fact.get("fact_id"),
        "formula": RUNWAY_BASIS[form],
        "note": (
            "Stated by the issuer in its ASX quarterly cash flow report, for the quarter "
            f"ended {quarter_end.isoformat()}; not computed by InvestingBuddy and not a "
            "forecast."
        ),
    }]


__all__ = [
    "CONFIDENCE",
    "EXTRACTION_METHOD",
    "FIELD_RUNWAY",
    "FORM_APPENDIX_4C",
    "FORM_APPENDIX_5B",
    "MAX_PAGES",
    "MAX_TEXT_CHARS",
    "QuarterlyCashFlowReading",
    "facts_from_pdf",
    "issuer_stated_runway",
    "locator",
    "merge_with_text_facts",
    "parse_locator",
    "period_keys",
    "read_quarterly_cash_flow_report",
    "statement_facts",
    "with_quarterly_cash_flow_facts",
]
