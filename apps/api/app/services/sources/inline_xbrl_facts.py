"""Statement facts from a held SEC filing's inline XBRL — ADR-085.

``build_financial_statements_state`` read ``facts_read=0`` for every held 10-K, 10-Q,
20-F and results 6-K: the table reader found little in an EDGAR HTML body, and nothing
read the statements the issuer had TAGGED in the same bytes. This module turns those
tags (``inline_xbrl.parse_inline_xbrl``) into ``ValidatedFact`` rows for the same fact
store the statements view reads (``ExtractedFact``), so they travel through the same
persistence, supersession and Group-scope rules as every other statement fact.

THE RULES (all fail-closed)
===========================
* **Period from the context, never from prose.** The document's frame is
  ``dei:DocumentPeriodEndDate`` (its context end), ``dei:DocumentFiscalYearFocus`` and
  ``dei:DocumentFiscalPeriodFocus``. Without all three, no fact is produced. Each fact's
  own context is then placed on that fiscal calendar: a ~3-month span is a quarter, a
  ~6-month span ending at a half-year end is H1/H2, a ~12-month span ending at a fiscal
  year end is the fiscal year, and an instant is the balance at that point. A span that
  is none of these (nine months year-to-date, a trailing twelve months) is skipped. So the
  prior-year comparative in a filing gets ITS OWN period.
* **Scope from the context.** No dimension → the Group. Exactly one explicit member → a
  segment named by that member, never the Group. Two or more members (an intersection)
  or a typed member → skipped.
* **Currency from the unit.** ``iso4217:XXX`` → ``XXX``. No conversion. A money concept
  in a non-currency unit is skipped.
* **The issuer's term travels.** The locator ``ixbrl:<concept>@<context>`` names the
  tagged concept; ``xbrl_statement_concepts.issuer_label`` gives its words.
* **One concept per field.** When two mapped concepts fill one field for one period and
  scope (``NetIncomeLoss`` and ``ProfitLoss``), the reviewed priority decides. Two
  readings of one concept that disagree beyond their stated precision fill nothing.
* **Spend lines are positive** (``primary_fact_parser`` sign convention); a negative
  tagged spend is skipped rather than flipped.
* **No derivation.** Nothing here adds, subtracts or annualises.

Only an SEC Archives document is read (``is_sec_archive_url``): EDGAR is the one
transport whose iXBRL the platform has reviewed.
"""

from __future__ import annotations

import calendar
import re
from datetime import date, timedelta
from decimal import Decimal
from typing import Any, Iterable
from urllib.parse import urlsplit

from app.integrations.xbrl_statement_concepts import (
    KIND_FLOW,
    KIND_INSTANT,
    STATEMENT_CONCEPT_BY_QNAME,
    StatementConcept,
)
from app.services.sources.inline_xbrl import (
    InlineXbrlDocument,
    InlineXbrlFact,
    looks_like_inline_xbrl,
    parse_inline_xbrl,
)

EXTRACTION_METHOD_INLINE_XBRL = "inline_xbrl"
LOCATOR_PREFIX = "ixbrl:"
#: Tagged by the issuer in a regulator filing: above every text reading.
INLINE_XBRL_CONFIDENCE = 0.97
#: Facts kept per document (Group first). A 10-K tags hundreds of disaggregations.
MAX_FACTS_PER_DOCUMENT = 600
#: Calendar drift allowed between a context date and the fiscal calendar (52/53 weeks).
_TOLERANCE_DAYS = 10
_MAX_QUARTERS_BACK = 20

_MONTHS_TO_FISCAL_YEAR_END = {"FY": 0, "Q4": 0, "H2": 0, "Q3": 3, "Q2": 6, "H1": 6, "Q1": 9}
_HALF_FRAME = frozenset({"H1", "H2", "Q2"})
_SCALE_WORDS = {3: "thousand", 6: "million", 9: "billion"}
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_SEC_HOSTS = frozenset({"www.sec.gov", "sec.gov"})


def is_sec_archive_url(url: str | None) -> bool:
    """True for a document under ``https://www.sec.gov/Archives/``."""
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return False
    return (
        parts.scheme == "https"
        and (parts.hostname or "").lower() in _SEC_HOSTS
        and parts.path.startswith("/Archives/")
    )


# ── The fiscal calendar ───────────────────────────────────────────────────── #


def _add_months(day: date, months: int) -> date:
    month_index = day.year * 12 + (day.month - 1) + months
    year, month = divmod(month_index, 12)
    month += 1
    last = calendar.monthrange(year, month)[1]
    at_month_end = day.day == calendar.monthrange(day.year, day.month)[1]
    return date(year, month, last if at_month_end else min(day.day, last))


def _quarters_before(fiscal_year_end: date, point: date) -> int | None:
    """How many fiscal quarters ``point`` lies before the fiscal year end, or ``None``
    when it is not (within tolerance) a fiscal quarter end."""
    if point > fiscal_year_end + timedelta(days=_TOLERANCE_DAYS):
        return None
    q = round((fiscal_year_end - point).days / 91.31)
    if q < 0 or q > _MAX_QUARTERS_BACK:
        return None
    if abs((_add_months(fiscal_year_end, -3 * q) - point).days) > _TOLERANCE_DAYS:
        return None
    return q


def period_label(
    fact: InlineXbrlFact, *, fiscal_year: int, fiscal_year_end: date, half_frame: bool
) -> str | None:
    """The fact's own period as an ``ExtractedFact.period`` key, or ``None``."""
    end = fact.period_end
    if end is None:
        return None
    q = _quarters_before(fiscal_year_end, end)
    if q is None:
        return None
    year = fiscal_year - q // 4
    position = q % 4  # 0 = year end, 1 = Q3 end, 2 = Q2/H1 end, 3 = Q1 end
    if fact.is_instant:
        if position == 0:
            return str(year)
        if position == 2 and half_frame:
            return f"{year}-H1"
        return f"{year}-Q{4 - position}"
    days = fact.duration_days or 0
    if 80 <= days <= 100:
        return f"{year}-Q{4 - position}"
    if 170 <= days <= 195:
        if position == 0:
            return f"{year}-H2"
        if position == 2:
            return f"{year}-H1"
        return None
    if 350 <= days <= 380 and position == 0:
        return str(year)
    return None


# ── Scope ─────────────────────────────────────────────────────────────────── #


def _words(qname: str, suffix: str) -> str:
    local = qname.rpartition(":")[2]
    if local.endswith(suffix):
        local = local[: -len(suffix)]
    return _CAMEL_RE.sub(" ", local).strip()


def scope_label(dimensions: tuple[tuple[str, str], ...]) -> str | None:
    """``"Group"`` for no dimension; the member's name for one; ``None`` otherwise."""
    from app.services.sources.fact_scope import is_group_label, is_period_label, parse_scope

    if not dimensions:
        return "Group"
    if len(dimensions) != 1:
        return None
    axis, member = dimensions[0]
    if not member or member == "typed":
        return None
    label = _words(member, "Member")
    if not label or is_group_label(label) or is_period_label(label):
        label = f"{label or member} ({_words(axis, 'Axis') or axis})"
    if not parse_scope(label).is_segment:  # pragma: no cover - defence in depth
        label = f"Segment: {label}"
    return label


# ── Facts ─────────────────────────────────────────────────────────────────── #


def _precision(decimals: str | None) -> Decimal:
    """Half a unit in the last stated place (``INF`` → exact)."""
    if not decimals or decimals.upper() == "INF":
        return Decimal(0)
    try:
        return Decimal(5).scaleb(-int(decimals) - 1)
    except ValueError:
        return Decimal(0)


def _value_fields(
    fact: InlineXbrlFact, mapped: StatementConcept
) -> dict[str, Any] | None:
    """numeric_value / unit / currency / scale for one fact, or ``None`` to skip it."""
    unit = fact.unit or ""
    if mapped.ratio:
        if unit.lower() != "pure":
            return None
        percent = fact.displayed if fact.scale == -2 else fact.value * 100
        return {"value_numeric": float(percent), "unit": "percent",
                "currency": None, "scale": None}
    if not _CURRENCY_RE.match(unit):
        return None
    if mapped.spend and fact.displayed < 0:
        return None
    scale_word = _SCALE_WORDS.get(fact.scale)
    number = fact.displayed if scale_word else fact.value
    return {"value_numeric": float(number), "unit": "currency_amount",
            "currency": unit, "scale": scale_word}


def statement_facts_from_inline_xbrl(document: InlineXbrlDocument) -> list[Any]:
    """Validated statement facts from a parsed document. Pure; never raises."""
    from app.services.sources.extracted_fact_validator import (
        VALIDATION_VALIDATED,
        ValidatedFact,
    )

    year = document.fiscal_year_focus
    focus = document.fiscal_period_focus
    period_end = document.period_end
    if year is None or focus is None or period_end is None:
        return []
    fiscal_year_end = _add_months(period_end, _MONTHS_TO_FISCAL_YEAR_END[focus])
    half_frame = focus in _HALF_FRAME

    # (field, period, scope) -> list of (priority, fact, mapped, value fields)
    candidates: dict[tuple[str, str, str], list[tuple[int, InlineXbrlFact,
                                                       StatementConcept, dict]]] = {}
    for fact in document.facts:
        mapped = STATEMENT_CONCEPT_BY_QNAME.get(fact.concept)
        if mapped is None or mapped.statement_field is None:
            continue
        if (mapped.kind == KIND_INSTANT) != fact.is_instant:
            continue
        if mapped.kind == KIND_FLOW and fact.is_instant:  # pragma: no cover - above
            continue
        scope = scope_label(fact.dimensions)
        if scope is None:
            continue
        period = period_label(
            fact, fiscal_year=year, fiscal_year_end=fiscal_year_end, half_frame=half_frame
        )
        if period is None:
            continue
        values = _value_fields(fact, mapped)
        if values is None:
            continue
        key = (mapped.statement_field, period, scope)
        candidates.setdefault(key, []).append((mapped.priority, fact, mapped, values))

    out: list[Any] = []
    for (field_name, period, scope), group in candidates.items():
        best_priority = min(p for p, *_ in group)
        best = [entry for entry in group if entry[0] == best_priority]
        chosen = _consistent_reading(best)
        if chosen is None:
            continue
        _priority, fact, mapped, values = chosen
        out.append(
            ValidatedFact(
                label=field_name,
                value_text=("-" if fact.displayed < 0 and not fact.text.startswith("-")
                            else "") + fact.text,
                period=period,
                page_number=None,
                table_location=f"{LOCATOR_PREFIX}{fact.concept}@{fact.context_id}"[:200],
                extraction_method=EXTRACTION_METHOD_INLINE_XBRL,
                confidence=INLINE_XBRL_CONFIDENCE,
                validation_status=VALIDATION_VALIDATED,
                needs_human_review=True,
                scope=scope,
                methods=[EXTRACTION_METHOD_INLINE_XBRL],
                validation_notes=[
                    f"issuer term: {mapped.label}",
                    f"tagged {fact.concept} in context {fact.context_id}",
                ],
                **values,
            )
        )
    # Group first, so a cap never drops a consolidated figure for a disaggregation.
    out.sort(key=lambda f: (f.scope != "Group", f.label, f.period or ""))
    return out[:MAX_FACTS_PER_DOCUMENT]


def _consistent_reading(
    entries: list[tuple[int, InlineXbrlFact, StatementConcept, dict]],
) -> tuple[int, InlineXbrlFact, StatementConcept, dict] | None:
    """The most precise of several readings of one concept, when they agree."""
    if len({entry[1].concept for entry in entries}) > 1:
        return None
    ordered = sorted(entries, key=lambda e: _precision(e[1].decimals))
    first = ordered[0]
    for other in ordered[1:]:
        allowance = max(_precision(first[1].decimals), _precision(other[1].decimals))
        if abs(first[1].value - other[1].value) > allowance:
            return None
    return first


def merge_with_text_facts(text_facts: Iterable[Any], tagged: list[Any]) -> list[Any]:
    """The tagged facts plus every text-read fact they do not cover.

    A text reading of a figure the same document tags for the same field, period and
    scope (or with an unknown scope beside a tagged Group figure) is the lesser reading
    of one statement, and is dropped rather than left to "disagree" with the tag.
    """
    from app.services.sources.fact_scope import parse_scope

    covered: set[tuple[str, str | None, str | None]] = set()
    group_covered: set[tuple[str, str | None]] = set()
    for fact in tagged:
        scope = parse_scope(fact.scope)
        covered.add((fact.label, fact.period, scope.scope_key))
        if not scope.is_segment:
            group_covered.add((fact.label, fact.period))
    kept: list[Any] = list(tagged)
    for fact in text_facts:
        scope = parse_scope(getattr(fact, "scope", None))
        key = (getattr(fact, "label", None), getattr(fact, "period", None))
        if (key[0], key[1], scope.scope_key) in covered:
            continue
        if not scope.is_segment and key in group_covered:
            continue
        kept.append(fact)
    return kept


def with_inline_xbrl_facts(
    validated_facts: Iterable[Any] | None,
    content: bytes | None,
    *,
    source_url: str | None,
) -> list[Any]:
    """``validated_facts`` with the document's tagged statement facts added.

    The hook the SEC filing-body path calls with the bytes it already holds. Anything
    that is not an SEC Archives iXBRL document is returned unchanged. Never raises.
    """
    facts = list(validated_facts or [])
    try:
        if not is_sec_archive_url(source_url) or not looks_like_inline_xbrl(content):
            return facts
        tagged = statement_facts_from_inline_xbrl(parse_inline_xbrl(content))
    except Exception:  # noqa: BLE001 - a tag reader must never cost the text reading
        return facts
    if not tagged:
        return facts
    return merge_with_text_facts(facts, tagged)


__all__ = [
    "EXTRACTION_METHOD_INLINE_XBRL",
    "INLINE_XBRL_CONFIDENCE",
    "LOCATOR_PREFIX",
    "MAX_FACTS_PER_DOCUMENT",
    "is_sec_archive_url",
    "merge_with_text_facts",
    "period_label",
    "scope_label",
    "statement_facts_from_inline_xbrl",
    "with_inline_xbrl_facts",
]
