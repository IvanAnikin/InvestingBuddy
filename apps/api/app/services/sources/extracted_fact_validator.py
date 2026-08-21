"""
Stricter validation of table/OCR-derived primary facts — Phase 32A Slice 5.

Turns the bounded ``PrimaryDocumentExtraction`` produced by
``primary_document_extractor`` (Task 2) into candidate structured facts, applying
a DELIBERATELY STRICTER bar than the prose ``primary_fact_parser``: a
table/OCR-derived number is only promoted to a validated fact when its row-header
label is unambiguous, its column maps to a known period, its unit (currency +
scale for money, ``people`` for a headcount) is known, and its source location is
preserved. Everything short of that bar is retained as an ``excerpt_only``
candidate (never a fabricated figure); only a clear contradiction is ``rejected``.

Why stricter than prose? A table cell has no surrounding sentence to anchor its
meaning, so a mis-aligned row/column mapping can silently invent a value. This
validator mirrors the prose parser's ambiguity-refusal spirit at the grid level:
if a labelled row has more than one candidate magnitude and the columns cannot be
mapped to distinct periods, no fact is emitted. It also adds three checks the
prose path cannot do:

  * **Column alignment.** A value must sit in a numeric column aligned with a
    row-header label; an ambiguous mapping downgrades to ``excerpt_only``.
  * **Cross-field arithmetic (best-effort).** When a labelled subtotal and its
    components are present for the same period, their sum is checked within a
    tolerance; a mismatch downgrades the subtotal to ``excerpt_only`` (the
    excerpt is kept — the components are untouched).
  * **Cross-method agreement.** When the same ``(label, period)`` is produced by
    more than one extraction method (e.g. native PDF + OCR), matching values
    raise confidence; a conflict is a clear contradiction and is ``rejected``.

Hard guarantees (mirroring the extractor + prose parser):
  * Never fabricates a value. Absence ⇒ no fact (or an ``excerpt_only`` record).
  * No FX conversion; the reporting currency is recorded as-found.
  * No valuation metric, price target, fair value, or upside/downside is ever
    computed — this only reads primary statements of fact.
  * OCR-derived facts are confidence-downgraded, NEVER auto-``high``, and must
    still clear ``primary_document_min_extraction_confidence`` to validate; their
    OCR provenance is disclosed on the fact.
  * Every produced fact is ``needs_human_review=True`` and carries its method,
    confidence, unit, currency, scale, period, page number and table location.
  * Secret-free: nothing here logs document text or values.

This module is a pure, synchronous, network-free function library with its own
unit tests. It is NOT wired into the connector / council / persistence in this
task; a later slice performs the wiring (gated by
``primary_document_ingestion_enabled``) and persists a ``ValidatedFact`` onto the
``ExtractedFact`` ORM row it maps cleanly onto.
"""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.document_text_extractor import DocumentExcerpt
from app.services.sources.primary_document_extractor import (
    METHOD_HTML,
    METHOD_NATIVE_PDF,
    METHOD_OCR,
    ExtractedTable,
    PrimaryDocumentExtraction,
    _confidence_bucket,
)
from app.services.sources.primary_fact_parser import (
    FIELD_CASH,
    FIELD_EMPLOYEES,
    FIELD_FREE_CASH_FLOW,
    FIELD_NET_CASH,
    FIELD_NET_DEBT,
    FIELD_NET_INCOME,
    FIELD_OPERATING_CASH_FLOW,
    FIELD_OPERATING_FREE_CASH_FLOW,
    FIELD_OPERATING_MARGIN,
    FIELD_OPERATING_PROFIT,
    FIELD_RECURRING_OPERATING_MARGIN,
    FIELD_RECURRING_OPERATING_PROFIT,
    FIELD_REVENUE,
    FIELD_TOTAL_ASSETS,
    FIELD_TOTAL_DEBT,
    FIELD_TOTAL_EQUITY,
    PrimaryFact,
    _find_currency,
    _norm_number,
    _parse_excerpt,
    _scale_word,
)

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

# Validation outcome (matches ExtractedFact.validation_status).
VALIDATION_VALIDATED = "validated"
VALIDATION_EXCERPT_ONLY = "excerpt_only"
VALIDATION_REJECTED = "rejected"

# Units (neutral, factual — never a rating vocabulary).
UNIT_CURRENCY_AMOUNT = "currency_amount"
UNIT_PEOPLE = "people"
UNIT_PERCENT = "percent"

# Extra component labels needed for the cross-field arithmetic (subtotal) check.
FIELD_SHORT_TERM_DEBT = "short_term_debt"
FIELD_LONG_TERM_DEBT = "long_term_debt"
FIELD_CURRENT_ASSETS = "total_current_assets"
FIELD_NON_CURRENT_ASSETS = "total_non_current_assets"
# Balance-sheet identity check (Phase 32A Slice 5B.2): assets == liabilities +
# equity. Local to this file, same pattern as the debt/asset subtotal labels
# above — a cross-check-only label, not surfaced as its own report field.
FIELD_TOTAL_LIABILITIES = "total_liabilities"

# Money labels require a KNOWN currency AND scale (the stricter bar). Percent
# labels (margins) require only an explicit period. Count labels require only
# a plausible integer count.
_MONEY_LABELS: frozenset[str] = frozenset(
    {
        FIELD_REVENUE,
        FIELD_OPERATING_PROFIT,
        FIELD_RECURRING_OPERATING_PROFIT,
        FIELD_NET_INCOME,
        FIELD_FREE_CASH_FLOW,
        FIELD_OPERATING_FREE_CASH_FLOW,
        FIELD_TOTAL_ASSETS,
        FIELD_TOTAL_DEBT,
        FIELD_NET_DEBT,
        FIELD_NET_CASH,
        FIELD_CASH,
        FIELD_SHORT_TERM_DEBT,
        FIELD_LONG_TERM_DEBT,
        FIELD_CURRENT_ASSETS,
        FIELD_NON_CURRENT_ASSETS,
        FIELD_TOTAL_LIABILITIES,
        FIELD_TOTAL_EQUITY,
        FIELD_OPERATING_CASH_FLOW,
    }
)
# Phase 32A corrective (Problem A/B): a table row like "Operating margin | 20.0%"
# carries no currency/scale — it needs its own bar (an explicit period is
# enough), never the money bar. See ``_numeric_cells``/``_make_candidate``.
_PERCENT_LABELS: frozenset[str] = frozenset(
    {FIELD_OPERATING_MARGIN, FIELD_RECURRING_OPERATING_MARGIN}
)
_COUNT_LABELS: frozenset[str] = frozenset({FIELD_EMPLOYEES})

# Row-header label patterns → normalized label. Ordered most-specific first so a
# component ("short-term debt", "total current assets") is never swallowed by a
# broader subtotal pattern ("total debt", "total assets"), and a "recurring"
# variant is never swallowed by its plain counterpart.
_LABEL_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"short[- ]term (?:debt|borrowings)", re.I), FIELD_SHORT_TERM_DEBT),
    (re.compile(r"long[- ]term (?:debt|borrowings)", re.I), FIELD_LONG_TERM_DEBT),
    (re.compile(r"total current assets|current assets", re.I), FIELD_CURRENT_ASSETS),
    (
        re.compile(r"total non[- ]current assets|non[- ]current assets", re.I),
        FIELD_NON_CURRENT_ASSETS,
    ),
    (re.compile(r"net (?:financial )?debt", re.I), FIELD_NET_DEBT),
    (re.compile(r"total (?:debt|borrowings)|gross debt", re.I), FIELD_TOTAL_DEBT),
    (re.compile(r"total assets", re.I), FIELD_TOTAL_ASSETS),
    (re.compile(r"total liabilities", re.I), FIELD_TOTAL_LIABILITIES),
    (
        re.compile(
            r"total (?:shareholders|stockholders)[’']?\s*equity"
            r"|shareholders[’']?\s*equity|total equity"
            # A table ROW-HEADER cell whose ENTIRE content is just "Equity"
            # (e.g. LVMH's "Financial highlights" table: a bare "Equity" row
            # alongside "Revenue", "Net financial debt", ...) is a safe,
            # generic match here — this pattern is only ever run against one
            # isolated row-label cell (``_match_label``), never free-flowing
            # prose, so it can never collide with an unrelated phrase like
            # "private equity" or "brand equity". A bare "equity" WITHIN a
            # longer label (e.g. a "Return on equity" ratio row) is
            # intentionally still excluded by the exact whole-cell anchors.
            r"|^\s*equity\s*$",
            re.I,
        ),
        FIELD_TOTAL_EQUITY,
    ),
    (re.compile(r"operating free cash flow", re.I), FIELD_OPERATING_FREE_CASH_FLOW),
    (re.compile(r"(?<!operating )free cash flow", re.I), FIELD_FREE_CASH_FLOW),
    (
        re.compile(
            r"net cash (?:generated |provided )?from operating activities"
            r"|cash flow from operations|operating cash flow"
            r"|net cash flows? from operating activities",
            re.I,
        ),
        FIELD_OPERATING_CASH_FLOW,
    ),
    (re.compile(r"cash and cash equivalents", re.I), FIELD_CASH),
    (
        re.compile(
            r"net cash position|net cash(?!\s*(?:flow|inflow|outflow|generated|"
            r"provided|from|and))",
            re.I,
        ),
        FIELD_NET_CASH,
    ),
    (
        re.compile(
            r"net income|net profit|profit for the year|profit attributable", re.I
        ),
        FIELD_NET_INCOME,
    ),
    (
        re.compile(r"recurring operating margin", re.I),
        FIELD_RECURRING_OPERATING_MARGIN,
    ),
    (
        re.compile(r"(?<!recurring )operating margin", re.I),
        FIELD_OPERATING_MARGIN,
    ),
    (
        re.compile(
            r"recurring operating (?:profit|income|result)"
            r"|profit from recurring operations",
            re.I,
        ),
        FIELD_RECURRING_OPERATING_PROFIT,
    ),
    (
        re.compile(
            r"(?<!recurring )(?:operating profit|operating income|operating result|ebit\b)",
            re.I,
        ),
        FIELD_OPERATING_PROFIT,
    ),
    (re.compile(r"revenue|net sales|total sales|turnover", re.I), FIELD_REVENUE),
    (
        re.compile(r"employees|headcount|full[- ]time equivalents", re.I),
        FIELD_EMPLOYEES,
    ),
]

# subtotal_label -> component labels that should sum to it (same period, table).
_SUBTOTAL_RULES: list[tuple[str, tuple[str, ...]]] = [
    (FIELD_TOTAL_DEBT, (FIELD_SHORT_TERM_DEBT, FIELD_LONG_TERM_DEBT)),
    (FIELD_TOTAL_ASSETS, (FIELD_CURRENT_ASSETS, FIELD_NON_CURRENT_ASSETS)),
]

_YEAR_RE = re.compile(r"(?:19|20)\d{2}")
# Accept singular OR plural scale words ("million"/"millions") + the abbreviations.
_SCALE_RE = re.compile(
    r"(?:€|£|\$)?\s*(millions?|billions?|thousands?|bn|mn|m)\b", re.IGNORECASE
)

# Confidence model. ``high`` is a bucket at >= 0.75 (see extractor
# ``_confidence_bucket``); OCR facts are held strictly below it.
_HIGH_CONFIDENCE = 0.75
_FULLY_QUALIFIED_CONFIDENCE = 0.8
_CROSS_METHOD_BOOST = 0.1
_MAX_CONFIDENCE = 0.95
_OCR_CONFIDENCE_FACTOR = 0.85
_OCR_CONFIDENCE_CEILING = 0.72  # deliberately below _HIGH_CONFIDENCE

# Method quality order (best first) — the primary method on a merged fact.
#
# Phase 32A corrective: HTML now ranks ABOVE native PDF (was the reverse).
# A live CFR staging failure traced HTML losing this tie-break even when a
# clean issuer results HTML page and a multi-column annual-report PDF both
# carried the same headline figure: pdfplumber's ``extract_text()`` has no
# general column-aware reading order, so a native-PDF candidate for a
# multi-column page can be corrupted (mislabelled value / spliced sentence)
# in ways a DOM-structured HTML table/paragraph is not. This does not
# override genuine cross-method conflict handling below (a real value
# disagreement between HTML and PDF is still surfaced as an explicit
# rejection, never silently resolved by rank alone) — it only decides which
# candidate is the REPRESENTATIVE one when values agree, and which method a
# lone candidate is reported under.
_METHOD_QUALITY = {METHOD_HTML: 0, METHOD_NATIVE_PDF: 1, METHOD_OCR: 2}


# --------------------------------------------------------------------------- #
# Result models
# --------------------------------------------------------------------------- #


class IssuerContext(BaseModel):
    """Minimal, known issuer/filing identity a structured fact must be tied to.

    A fact can only be *validated* when the issuer is known (at least one of
    company/legal name or ticker). ``reporting_currency`` / ``default_period`` are
    optional hints used when a table cell/header does not itself state them.
    """

    company_name: str | None = None
    legal_name: str | None = None
    ticker: str | None = None
    reporting_currency: str | None = None
    default_period: str | None = None  # e.g. "2024" fiscal year

    def is_known(self) -> bool:
        return bool(
            (self.company_name and self.company_name.strip())
            or (self.legal_name and self.legal_name.strip())
            or (self.ticker and self.ticker.strip())
        )


class ValidatedFact(BaseModel):
    """One candidate structured fact with its validation verdict + provenance.

    The first block of fields maps 1:1 onto the ``ExtractedFact`` ORM columns so a
    later persistence task can store it directly. The trailing fields
    (``methods`` / ``ocr_derived`` / ``validation_notes``) are informational and
    are not ORM columns.
    """

    # ── ExtractedFact-mapped columns ──────────────────────────────────────
    label: str
    value_numeric: float | None = None
    value_text: str | None = None  # raw as-found value (never normalized away)
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None
    period: str | None = None
    page_number: int | None = None
    table_location: str | None = None
    extraction_method: str
    confidence: float
    validation_status: str
    needs_human_review: bool = True
    # Best-effort entity/segment scope (``"group"``, a segment/business-unit
    # label, or ``None`` when unknown — never guessed). Phase 32A corrective
    # (Problem A/B): without this a Group-scoped and a segment-scoped fact for
    # the same (label, period) collided as one "conflicting" group.
    scope: str | None = None
    # ── Informational (not persisted as ORM columns) ──────────────────────
    methods: list[str] = Field(default_factory=list)
    ocr_derived: bool = False
    validation_notes: list[str] = Field(default_factory=list)

    @property
    def is_validated(self) -> bool:
        return self.validation_status == VALIDATION_VALIDATED


# --------------------------------------------------------------------------- #
# Internal candidate (mutable working record before cross-method resolution)
# --------------------------------------------------------------------------- #


class _Candidate:
    """A single table-cell candidate before subtotal / cross-method resolution."""

    __slots__ = (
        "label",
        "period",
        "value_numeric",
        "value_text",
        "unit",
        "currency",
        "scale",
        "page_number",
        "table_location",
        "method",
        "base_confidence",
        "fully_qualified",
        "status",
        "notes",
        "scope",
    )

    def __init__(
        self,
        *,
        label: str,
        period: str | None,
        value_numeric: float | None,
        value_text: str,
        unit: str | None,
        currency: str | None,
        scale: str | None,
        page_number: int | None,
        table_location: str | None,
        method: str,
        base_confidence: float,
        fully_qualified: bool,
        status: str,
        scope: str | None = None,
    ) -> None:
        self.label = label
        self.period = period
        self.value_numeric = value_numeric
        self.value_text = value_text
        self.unit = unit
        self.currency = currency
        self.scale = scale
        self.page_number = page_number
        self.table_location = table_location
        self.method = method
        self.base_confidence = base_confidence
        self.fully_qualified = fully_qualified
        self.status = status
        self.notes: list[str] = []
        self.scope = scope


# --------------------------------------------------------------------------- #
# Small pure helpers
# --------------------------------------------------------------------------- #


def _match_label(text: str) -> str | None:
    """Return the single normalized label for a row-header cell, else None.

    None when the cell matches no known label OR matches more than one distinct
    label (ambiguous → not a structured fact, mirror the prose refusal).
    """
    if not text:
        return None
    matched = {label for pat, label in _LABEL_PATTERNS if pat.search(text)}
    if len(matched) == 1:
        return next(iter(matched))
    return None


def _find_scale(text: str) -> str | None:
    """Return million/billion/thousand if a scale token is present, else None."""
    m = _SCALE_RE.search(text or "")
    # rstrip("s") normalizes a plural ("millions" → "million") for _scale_word.
    return _scale_word(m.group(1).rstrip("s")) if m else None


def _column_periods(table: ExtractedTable) -> dict[int, str]:
    """Map column index → period (a 4-digit year) from the first row that has
    year tokens (the header). Later columns without a year are left unmapped."""
    for row in table.rows:
        found: dict[int, str] = {}
        for col, cell in enumerate(row):
            m = _YEAR_RE.search(cell or "")
            if m:
                found[col] = m.group(0)
        if found:
            return found
    return {}


def _table_currency_scale(
    table: ExtractedTable,
    excerpts_by_page: dict[int | None, list[str]],
    issuer: IssuerContext,
) -> tuple[str | None, str | None]:
    """Best-effort currency + scale for a whole table.

    Scanned from (in priority order) the table's own cells, then any excerpt on
    the same page, then the issuer's reporting-currency hint. No fabrication: a
    missing currency/scale simply stays None (and blocks money-fact validation).
    """
    flat = " ".join(cell for row in table.rows for cell in row)
    currency = _find_currency(flat)
    scale = _find_scale(flat)

    if currency is None or scale is None:
        for text in excerpts_by_page.get(table.page_number, []):
            currency = currency or _find_currency(text)
            scale = scale or _find_scale(text)
            if currency and scale:
                break

    if currency is None and issuer.reporting_currency:
        currency = issuer.reporting_currency.strip().upper() or None
    return currency, scale


def _numeric_cells(row: list[str]) -> list[tuple[int, str, float]]:
    """Return ``(col, raw_text, numeric)`` for every parseable numeric cell in a
    row, skipping the leading label column (col 0)."""
    out: list[tuple[int, str, float]] = []
    for col, cell in enumerate(row):
        if col == 0:
            continue
        num = _norm_number(cell)
        if num is not None:
            out.append((col, cell.strip(), num))
    return out


def _numeric_cells_percent(row: list[str]) -> list[tuple[int, str, float]]:
    """Same as :func:`_numeric_cells` but for a row whose value cells carry an
    explicit ``%`` sign (a margin row) — only a cell that itself states ``%``
    is accepted, so a plain currency amount in the same table is never
    mistaken for a percentage."""
    out: list[tuple[int, str, float]] = []
    for col, cell in enumerate(row):
        if col == 0:
            continue
        stripped = cell.strip()
        if not stripped.endswith("%"):
            continue
        num = _norm_number(stripped[:-1])
        if num is not None:
            out.append((col, stripped, num))
    return out


def _derive_confidence(
    method: str, base: float, *, fully_qualified: bool
) -> float:
    """Confidence for a single-method fact: OCR is downgraded + capped below high;
    a fully-qualified native/HTML fact is lifted (still earned, never auto-high
    for OCR)."""
    if method == METHOD_OCR:
        return round(min(base * _OCR_CONFIDENCE_FACTOR, _OCR_CONFIDENCE_CEILING), 4)
    conf = base
    if fully_qualified:
        conf = max(conf, _FULLY_QUALIFIED_CONFIDENCE)
    return round(min(conf, _MAX_CONFIDENCE), 4)


def _values_agree(a: float, b: float) -> bool:
    """True when two magnitudes match within a small relative/absolute tolerance."""
    tol = max(abs(a), abs(b)) * 0.01
    return abs(a - b) <= max(tol, 0.5)


# Multiplier to a common base unit, used ONLY to compare two money candidates
# for agreement/conflict — never to rewrite a candidate's own stored value
# (``ValidatedFact.value_numeric``/``scale`` are always kept exactly as
# reported). Phase 32A corrective (cross-excerpt reconciliation): before this,
# a rounded "EUR22.4 billion" mention and a precise "EUR22,420 million"
# mention of the SAME Group figure compared their raw digits directly
# (22.4 vs 22420) and were treated as a hard conflict.
_SCALE_MULTIPLIER: dict[str, float] = {"thousand": 1e3, "million": 1e6, "billion": 1e9}
# Coarser-to-finer precision rank, used only as a low-priority representative
# tie-break (prefer the more precise scale when two candidates already agree
# and are otherwise equally good).
_SCALE_PRECISION_RANK: dict[str | None, int] = {
    "thousand": 0,
    "million": 1,
    "billion": 2,
    None: 3,
}


def _scaled_magnitude(candidate: "_Candidate") -> tuple[float | None, bool]:
    """``(magnitude, was_scaled)`` — ``magnitude`` converted to a common base
    unit ONLY when ``candidate`` itself carries a known scale; otherwise the
    raw ``value_numeric`` digits are returned unchanged (``was_scaled=False``)."""
    if candidate.value_numeric is None:
        return None, False
    if candidate.unit == UNIT_CURRENCY_AMOUNT and candidate.scale:
        return candidate.value_numeric * _SCALE_MULTIPLIER.get(candidate.scale, 1.0), True
    return candidate.value_numeric, False


def _candidates_agree(a: "_Candidate", b: "_Candidate") -> bool:
    """True when two candidates' values agree.

    Phase 32A corrective (cross-excerpt reconciliation) — two money
    candidates that BOTH carry a known scale are compared on a common base
    unit (a rounded "EUR22.4 billion" mention must agree with a precise
    "EUR22,420 million" mention of the SAME figure, not be treated as a hard
    conflict merely because their raw digits differ). When EITHER candidate
    lacks a scale (or for a non-money unit, which never has one), the RAW
    digits are compared exactly as before this fix — e.g. a table cell
    "20,616" (scale known from the table header) and an unscaled prose
    mention of the SAME literal digits "20,616" (no local scale marker) must
    still agree; multiplying only the scaled side would compare 20,616 to
    20,616,000,000 and manufacture a false conflict between two mentions of
    the exact same written number.
    """
    va, a_scaled = _scaled_magnitude(a)
    vb, b_scaled = _scaled_magnitude(b)
    if va is None or vb is None:
        return True  # nothing to compare; never the source of a conflict
    if a_scaled and b_scaled:
        return _values_agree(va, vb)
    return _values_agree(a.value_numeric, b.value_numeric)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# Per-table candidate extraction
# --------------------------------------------------------------------------- #


def _candidates_from_table(
    table: ExtractedTable,
    excerpts_by_page: dict[int | None, list[str]],
    issuer: IssuerContext,
) -> list[_Candidate]:
    """Turn one bounded table into per-cell candidates + run the subtotal check."""
    col_period = _column_periods(table)
    currency, scale = _table_currency_scale(table, excerpts_by_page, issuer)
    candidates: list[_Candidate] = []

    for row in table.rows:
        if not row:
            continue
        label = _match_label(row[0])
        if label is None:
            continue  # unknown/ambiguous label → stays an excerpt, not a fact
        is_money = label in _MONEY_LABELS
        is_percent = label in _PERCENT_LABELS
        nums = _numeric_cells_percent(row) if is_percent else _numeric_cells(row)
        if not nums:
            continue

        # Resolve (period, value) pairs for this row.
        pairs: list[tuple[str | None, str, float, int]] = []  # period, text, num, col
        mapped = [(col, t, n) for (col, t, n) in nums if col in col_period]
        if mapped:
            for col, t, n in mapped:
                pairs.append((col_period[col], t, n, col))
        else:
            distinct = {round(n, 6) for _c, _t, n in nums}
            if len(distinct) > 1:
                # Multiple different magnitudes and no period mapping → ambiguous.
                col, t, n = nums[0]
                candidates.append(
                    _make_candidate(
                        label,
                        issuer.default_period,
                        t,
                        n,
                        table,
                        currency if is_money else None,
                        scale if is_money else None,
                        is_money,
                        VALIDATION_EXCERPT_ONLY,
                        is_percent=is_percent,
                        note="Ambiguous row/column mapping (multiple unlabelled "
                        "magnitudes); retained as excerpt.",
                    )
                )
                continue
            col, t, n = nums[0]
            pairs.append((issuer.default_period, t, n, col))

        for period, text, num, _col in pairs:
            status = VALIDATION_VALIDATED
            note = None
            if period is None:
                status = VALIDATION_EXCERPT_ONLY
                note = "Period could not be resolved; retained as excerpt."
            elif is_money and not (currency and scale):
                status = VALIDATION_EXCERPT_ONLY
                note = (
                    "Currency and/or scale not stated for the table; retained as "
                    "excerpt."
                )
            candidates.append(
                _make_candidate(
                    label,
                    period,
                    text,
                    num,
                    table,
                    currency if is_money else None,
                    scale if is_money else None,
                    is_money,
                    status,
                    is_percent=is_percent,
                    note=note,
                )
            )

    _apply_subtotal_check(candidates)
    _apply_balance_sheet_check(candidates)
    return candidates


def _make_candidate(
    label: str,
    period: str | None,
    value_text: str,
    value_numeric: float,
    table: ExtractedTable,
    currency: str | None,
    scale: str | None,
    is_money: bool,
    status: str,
    *,
    is_percent: bool = False,
    note: str | None = None,
) -> _Candidate:
    if is_percent:
        unit = UNIT_PERCENT
        fully_qualified = bool(period)
    elif is_money:
        unit = UNIT_CURRENCY_AMOUNT
        fully_qualified = bool(currency and scale and period)
    else:
        unit = UNIT_PEOPLE
        fully_qualified = bool(period and value_numeric >= 1)
    cand = _Candidate(
        label=label,
        period=period,
        value_numeric=value_numeric,
        value_text=value_text,
        unit=unit,
        currency=currency,
        scale=scale,
        page_number=table.page_number,
        table_location=table.table_location,
        method=table.extraction_method,
        base_confidence=table.confidence,
        fully_qualified=fully_qualified,
        status=status,
        scope=table.scope,
    )
    if note:
        cand.notes.append(note)
    return cand


# --------------------------------------------------------------------------- #
# Per-excerpt (prose) candidate extraction — Phase 32A corrective, Problem A
# --------------------------------------------------------------------------- #


def _candidates_from_excerpts(extraction: PrimaryDocumentExtraction) -> list[_Candidate]:
    """Turn each bounded PROSE excerpt into fact candidates.

    Before this fix, ``validate_extracted_facts`` looked ONLY at
    ``extraction.tables`` — a figure explicitly stated in ordinary prose (e.g.
    an HTML press release with no ``<table>`` at all, or a lead paragraph
    summarizing the headline numbers) never became a structured fact even
    when clearly stated, because the deep pipeline never ran the conservative
    prose parser at all (that parser was wired only into the superseded
    shallow/legacy path). Reuses ``primary_fact_parser._parse_excerpt`` — the
    SAME conservative, fail-closed matcher — so prose and table facts share
    one vocabulary and one ambiguity-refusal policy. Each candidate then goes
    through the SAME cross-method reconciliation as table candidates: a figure
    corroborated by both a table and a press-release paragraph is boosted, a
    genuine conflict is a rejection, never a silent pick.

    Phase 32A corrective (cross-excerpt reconciliation) — a real report
    commonly restates the fiscal year ONCE (e.g. in a "Sales" lead sentence)
    and then omits it from later sentences ("Operating profit for the year
    grew to ...") that a per-excerpt-only period search cannot see. A prose
    fact whose OWN local sentence states no year is given the DOCUMENT's own
    dominant reporting period — the most common explicit year found
    elsewhere among this SAME document's own parsed facts, never an
    external/company hint — but ONLY when the fact already carries POSITIVE
    scope evidence (an explicit Group or named-segment signal; see
    ``primary_fact_parser._infer_prose_scope``). An unscoped fact is left
    exactly as before, still requiring its own local period: filling in a
    period for an unscoped candidate would let it silently collide with a
    DIFFERENT, also-unscoped fact under the same (label, period, scope=None)
    key, and the existing fail-closed conflict handling would then mark BOTH
    ambiguous — quietly breaking an already-working unscoped fact. Requiring
    scope FIRST means this can only ever add a cleanly-separated fact or
    surface a genuine same-scope conflict, never manufacture a new collision
    between two previously-safe unscoped facts.

    A second, independent safety gate: the fallback is also SKIPPED for a
    given ``(label, scope)`` when this SAME document already has an
    EXPLICIT-period (never inferred) candidate for that exact ``(label,
    scope, dominant_period)`` whose value does not agree with this one — a
    real live example: an unrelated "€134 million" figure sitting near an
    incidental "... of Group sales" phrase (a ratio-base mention, not a
    sales figure of its own) must never be allowed to newly collide with
    the genuine, explicitly-dated "Group sales reached €22.4 billion"
    figure just because both happen to lack their own stated year — without
    this gate the inferred candidate would drag the ALREADY-correct
    explicit one down to ``excerpt_only`` too. Two candidates that BOTH lack
    an explicit period may still end up conflicting with each other after
    inference — that is a genuine same-scope ambiguity, correctly surfaced,
    not suppressed.
    """
    parsed: list[tuple[PrimaryFact, Any]] = []
    for exc in extraction.excerpts:
        wrapper = DocumentExcerpt(
            excerpt_id=exc.excerpt_id,
            heading=exc.heading,
            ancestor_heading=exc.ancestor_heading,
            text=exc.text,
            page_number=exc.page_number,
            char_count=exc.char_count,
            confidence=_confidence_bucket(exc.confidence),
            evidence_type=exc.evidence_type,
        )
        for fact in _parse_excerpt(wrapper, None):
            parsed.append((fact, exc))

    dominant_period: str | None = None
    periods_seen = [fact.period for fact, _exc in parsed if fact.period]
    if periods_seen:
        dominant_period = Counter(periods_seen).most_common(1)[0][0]

    # Explicit-period (never inferred) anchor magnitudes per (label, scope,
    # period) — consulted below so the period-inference fallback never drags
    # an already-correct, explicitly-dated fact into a new conflict. Each
    # anchor keeps its raw ``(value, scale)`` — NOT pre-multiplied — so the
    # comparison below can apply the same "only compare on a common base
    # unit when BOTH sides carry a known scale" rule as ``_candidates_agree``
    # (an unscaled anchor and a scaled candidate must fall back to a raw
    # -digit comparison, never a false conflict from one-sided scaling).
    anchors: dict[tuple[str, str | None, str | None], list[tuple[float, str | None]]] = {}
    if dominant_period is not None:
        for fact, _exc in parsed:
            if fact.period != dominant_period or fact.numeric_value is None:
                continue
            scale = fact.scale if fact.unit == UNIT_CURRENCY_AMOUNT else None
            anchors.setdefault((fact.field, fact.scope, dominant_period), []).append(
                (fact.numeric_value, scale)
            )

    def _magnitudes_agree(
        value_a: float, scale_a: str | None, value_b: float, scale_b: str | None
    ) -> bool:
        if scale_a and scale_b:
            return _values_agree(
                value_a * _SCALE_MULTIPLIER.get(scale_a, 1.0),
                value_b * _SCALE_MULTIPLIER.get(scale_b, 1.0),
            )
        return _values_agree(value_a, value_b)

    candidates: list[_Candidate] = []
    for fact, exc in parsed:
        period = fact.period
        inferred_period = False
        if period is None and fact.scope is not None and dominant_period is not None:
            candidate_scale = fact.scale if fact.unit == UNIT_CURRENCY_AMOUNT else None
            anchor_values = anchors.get((fact.field, fact.scope, dominant_period), [])
            conflicts_with_anchor = fact.numeric_value is not None and any(
                not _magnitudes_agree(
                    fact.numeric_value, candidate_scale, anchor_value, anchor_scale
                )
                for anchor_value, anchor_scale in anchor_values
            )
            if not conflicts_with_anchor:
                period = dominant_period
                inferred_period = True
        is_money = fact.unit == UNIT_CURRENCY_AMOUNT
        period_known = bool(period)
        if is_money:
            status = (
                VALIDATION_VALIDATED
                if period_known and fact.currency and fact.scale
                else VALIDATION_EXCERPT_ONLY
            )
        else:
            status = VALIDATION_VALIDATED if period_known else VALIDATION_EXCERPT_ONLY
        fully_qualified = (
            bool(fact.currency and fact.scale and period)
            if is_money
            else bool(period)
        )
        cand = _Candidate(
            label=fact.field,
            period=period,
            value_numeric=fact.numeric_value,
            value_text=fact.value,
            unit=fact.unit,
            currency=fact.currency,
            scale=fact.scale,
            page_number=fact.page_number,
            # Reuses the ``table_location`` slot for the excerpt id — the
            # same field ``ValidatedFact.table_location`` already maps
            # onto and that ``company_ir.py`` already renders as
            # provenance for BOTH tables and (now) prose.
            table_location=fact.excerpt_id,
            method=exc.extraction_method,
            base_confidence=exc.confidence,
            fully_qualified=fully_qualified,
            status=status,
            scope=fact.scope,
        )
        if fact.parser_warning:
            cand.notes.append(fact.parser_warning)
        if inferred_period:
            cand.notes.append(
                "Period inferred from the document's own dominant reporting "
                "period; this candidate's own local text stated no year."
            )
        candidates.append(cand)
    return candidates


def _apply_subtotal_check(candidates: list[_Candidate]) -> None:
    """Downgrade a labelled subtotal to ``excerpt_only`` when it does not
    reconcile with its components for the same period (components are untouched)."""
    by_period: dict[str | None, dict[str, _Candidate]] = {}
    for c in candidates:
        by_period.setdefault(c.period, {}).setdefault(c.label, c)

    for period, by_label in by_period.items():
        if period is None:
            continue
        for subtotal_label, components in _SUBTOTAL_RULES:
            sub = by_label.get(subtotal_label)
            if sub is None or sub.value_numeric is None:
                continue
            comp_facts = [by_label.get(c) for c in components]
            comp_values = [
                c.value_numeric
                for c in comp_facts
                if c is not None and c.value_numeric is not None
            ]
            if len(comp_values) != len(components):
                continue
            expected = sum(comp_values)
            tol = max(abs(expected) * 0.01, 0.5)
            if abs(sub.value_numeric - expected) > tol:
                sub.status = VALIDATION_EXCERPT_ONLY
                sub.notes.append(
                    "Subtotal did not reconcile with its components; retained as "
                    "excerpt."
                )


def _apply_balance_sheet_check(candidates: list[_Candidate]) -> None:
    """Balance-sheet identity: total assets == total liabilities + total equity.

    Phase 32A Slice 5B.2. Runs ONLY when all three labels are present for the
    SAME period in the same table (the identity is meaningless otherwise). On a
    mismatch outside tolerance, all three candidates are downgraded to
    ``excerpt_only`` — unlike a two-component subtotal, a 3-way identity gives
    no way to single out which figure is wrong, so the honest response is to
    keep all three as evidence without promoting any of them to a fact.
    """
    by_period: dict[str | None, dict[str, _Candidate]] = {}
    for c in candidates:
        by_period.setdefault(c.period, {}).setdefault(c.label, c)

    for period, by_label in by_period.items():
        if period is None:
            continue
        assets = by_label.get(FIELD_TOTAL_ASSETS)
        liabilities = by_label.get(FIELD_TOTAL_LIABILITIES)
        equity = by_label.get(FIELD_TOTAL_EQUITY)
        if assets is None or liabilities is None or equity is None:
            continue
        assets_value = assets.value_numeric
        liabilities_value = liabilities.value_numeric
        equity_value = equity.value_numeric
        if assets_value is None or liabilities_value is None or equity_value is None:
            continue
        expected = liabilities_value + equity_value
        tol = max(abs(assets_value) * 0.01, 0.5)
        if abs(assets_value - expected) > tol:
            for candidate in (assets, liabilities, equity):
                candidate.status = VALIDATION_EXCERPT_ONLY
                candidate.notes.append(
                    "Balance-sheet identity (assets = liabilities + equity) did "
                    "not reconcile; retained as excerpt."
                )


# --------------------------------------------------------------------------- #
# Cross-method resolution
# --------------------------------------------------------------------------- #


def _resolve_group(
    key: tuple[str, str | None, str | None], group: list[_Candidate], cfg: Settings
) -> ValidatedFact:
    """Collapse all candidates for one ``(label, period, scope)`` into a single
    verdict.

    * Values agree across >1 method → boosted confidence.
    * Values conflict across >1 method → clear contradiction → rejected.
    * Values conflict within a single method → ambiguous → excerpt_only.

    ``scope`` is part of the group key (Phase 32A corrective, Problem A/B): a
    Group-scoped and a segment-scoped candidate for the same label/period are
    two genuinely different figures, never conflicting values of one fact.
    """
    label, period, scope = key
    methods = sorted({c.method for c in group}, key=lambda m: _METHOD_QUALITY.get(m, 9))
    ocr_derived = METHOD_OCR in methods
    # Phase 32A corrective (cross-excerpt reconciliation) — compare candidates
    # pairwise on a common base unit ONLY when BOTH sides carry a known scale
    # (see ``_candidates_agree``), not raw digits: a rounded "EUR22.4bn" and a
    # precise "EUR22,420m" mention of the SAME Group figure must agree, not
    # be treated as a hard conflict.
    valued = [c for c in group if c.value_numeric is not None]

    conflict = False
    for i in range(len(valued)):
        for j in range(i + 1, len(valued)):
            if not _candidates_agree(valued[i], valued[j]):
                conflict = True
                break
        if conflict:
            break

    # Representative candidate: prefer the highest-quality method.
    rep = min(group, key=lambda c: _METHOD_QUALITY.get(c.method, 9))
    primary_method = methods[0] if methods else rep.method

    if conflict:
        if len(methods) > 1:
            return ValidatedFact(
                label=label,
                value_numeric=None,
                value_text=None,
                unit=rep.unit,
                currency=rep.currency,
                scale=rep.scale,
                period=period,
                page_number=rep.page_number,
                table_location=rep.table_location,
                extraction_method=primary_method,
                confidence=round(min(c.base_confidence for c in group), 4),
                validation_status=VALIDATION_REJECTED,
                methods=methods,
                ocr_derived=ocr_derived,
                validation_notes=[
                    "Cross-method value conflict for the same label/period; "
                    "rejected as a clear contradiction."
                ],
                scope=scope,
            )
        # Same-method disagreement → ambiguous, not a contradiction.
        return _excerpt_only_fact(
            rep,
            methods,
            ocr_derived,
            note="Conflicting magnitudes from the same method; retained as excerpt.",
        )

    # Values agree (or a single value): the strongest status wins.
    best = _best_candidate(group)
    fully_qualified = any(c.fully_qualified for c in group)
    conf = _derive_confidence(
        primary_method, best.base_confidence, fully_qualified=fully_qualified
    )
    boosted = len(methods) > 1
    if boosted:
        conf = round(min(conf + _CROSS_METHOD_BOOST, _MAX_CONFIDENCE), 4)
    # OCR-only facts are never auto-high, even after a boost.
    if methods and all(m == METHOD_OCR for m in methods):
        conf = round(min(conf, _OCR_CONFIDENCE_CEILING), 4)

    status = best.status
    notes = list(best.notes)
    # Confidence floor: a validated fact must clear the minimum.
    min_conf = float(cfg.primary_document_min_extraction_confidence)
    if status == VALIDATION_VALIDATED and conf < min_conf:
        status = VALIDATION_EXCERPT_ONLY
        notes.append(
            "Extraction confidence below the minimum; retained as excerpt."
        )
    if ocr_derived and status == VALIDATION_VALIDATED:
        notes.append("OCR-derived value; confidence downgraded, human review required.")
    if boosted and status == VALIDATION_VALIDATED:
        notes.append("Corroborated across extraction methods; confidence raised.")

    return ValidatedFact(
        label=label,
        value_numeric=best.value_numeric,
        value_text=best.value_text,
        unit=best.unit,
        currency=best.currency,
        scale=best.scale,
        period=period,
        page_number=best.page_number,
        table_location=best.table_location,
        extraction_method=primary_method,
        confidence=conf,
        validation_status=status,
        methods=methods,
        ocr_derived=ocr_derived,
        validation_notes=notes,
        scope=scope,
    )


def _best_candidate(group: list[_Candidate]) -> _Candidate:
    """Pick the representative candidate: validated first, then the more
    precise scale, then highest base confidence, then best-quality method.

    Only ever called on a GROUP ``_resolve_group`` has already confirmed does
    NOT conflict (see the ``if conflict:`` branch above, which returns
    early) — every candidate here is already an agreeing restatement of the
    same figure, so preferring precision can never silently pick a
    contradicting value, only the more exact of two that already agree, e.g.
    a "22,420 million" candidate over an agreeing but coarser "22.4 billion"
    one.

    Phase 32A corrective (LVMH H1 2026) — precision used to be the LOWEST
    priority, ranked after ``base_confidence``. A table cell carries no
    excerpt-relevance score of its own (a flat ``0.7`` default), while a
    lead-paragraph prose restatement is typically ranked far higher purely
    for citation-display relevance (e.g. ``0.9``) — a signal about how
    worth-reading the prose is, not about which figure is more exact. That
    let a rounded prose mention ("€8.7 billion") silently outrank an
    agreeing, more precise table figure ("8,691" million) as the fact's
    representative value merely because of its unrelated excerpt-ranking
    score — exactly the "never prefer a candidate merely due to a higher
    excerpt score" failure mode this parser is designed to avoid."""
    return min(
        group,
        key=lambda c: (
            0 if c.status == VALIDATION_VALIDATED else 1,
            _SCALE_PRECISION_RANK.get(c.scale, 3),
            -c.base_confidence,
            _METHOD_QUALITY.get(c.method, 9),
        ),
    )


def _excerpt_only_fact(
    rep: _Candidate, methods: list[str], ocr_derived: bool, *, note: str
) -> ValidatedFact:
    notes = list(rep.notes)
    notes.append(note)
    return ValidatedFact(
        label=rep.label,
        value_numeric=rep.value_numeric,
        value_text=rep.value_text,
        unit=rep.unit,
        currency=rep.currency,
        scale=rep.scale,
        period=rep.period,
        page_number=rep.page_number,
        table_location=rep.table_location,
        extraction_method=methods[0] if methods else rep.method,
        confidence=round(rep.base_confidence, 4),
        validation_status=VALIDATION_EXCERPT_ONLY,
        methods=methods,
        ocr_derived=ocr_derived,
        validation_notes=notes,
        scope=rep.scope,
    )


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def validate_extracted_facts(
    extraction: PrimaryDocumentExtraction,
    *,
    issuer_context: IssuerContext,
    cfg: Settings | None = None,
) -> list[ValidatedFact]:
    """Validate an extraction's tables into candidate structured facts.

    Every returned fact is ``needs_human_review=True`` and carries its method,
    confidence, unit, currency, scale, period, page number and table location.
    Facts below the stricter bar are returned as ``excerpt_only`` (retained, never
    a fabricated figure); only a clear cross-method contradiction is ``rejected``.
    Returns an empty list when there are no tables — the caller keeps the raw
    excerpts as evidence and produces no structured facts (honest under-reporting).
    """
    cfg = cfg or default_settings
    issuer = issuer_context or IssuerContext()
    issuer_known = issuer.is_known()

    excerpts_by_page: dict[int | None, list[str]] = {}
    for ex in extraction.excerpts:
        excerpts_by_page.setdefault(ex.page_number, []).append(ex.text)

    candidates: list[_Candidate] = []
    for table in extraction.tables:
        candidates.extend(_candidates_from_table(table, excerpts_by_page, issuer))
    # Phase 32A corrective (Problem A): prose excerpts are now ALSO a candidate
    # source, not just tables — see ``_candidates_from_excerpts``.
    candidates.extend(_candidates_from_excerpts(extraction))

    # Group by (label, period, scope) so the same figure from >1 method/source
    # is reconciled, while a Group-scoped and a segment-scoped candidate for
    # the same label/period are kept as two distinct facts, never merged or
    # cross-checked against each other as "conflicting".
    groups: dict[tuple[str, str | None, str | None], list[_Candidate]] = {}
    for cand in candidates:
        groups.setdefault((cand.label, cand.period, cand.scope), []).append(cand)

    facts: list[ValidatedFact] = []
    for key, group in groups.items():
        fact = _resolve_group(key, group, cfg)
        # Without a known issuer/filing context a fact can never be validated.
        if not issuer_known and fact.validation_status == VALIDATION_VALIDATED:
            fact.validation_status = VALIDATION_EXCERPT_ONLY
            fact.validation_notes.append(
                "Issuer/filing context unknown; retained as excerpt."
            )
        facts.append(fact)

    # Deterministic order: validated first, then by label + period.
    facts.sort(
        key=lambda f: (
            0 if f.validation_status == VALIDATION_VALIDATED else 1,
            f.label,
            f.period or "",
        )
    )
    return facts


__all__ = [
    "VALIDATION_VALIDATED",
    "VALIDATION_EXCERPT_ONLY",
    "VALIDATION_REJECTED",
    "UNIT_CURRENCY_AMOUNT",
    "UNIT_PEOPLE",
    "FIELD_SHORT_TERM_DEBT",
    "FIELD_LONG_TERM_DEBT",
    "FIELD_CURRENT_ASSETS",
    "FIELD_NON_CURRENT_ASSETS",
    "FIELD_OPERATING_CASH_FLOW",
    "IssuerContext",
    "ValidatedFact",
    "validate_extracted_facts",
]
