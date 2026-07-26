"""
Conservative primary-fact parser — Phase 29B.2.

Parses only *high-confidence* primary facts out of already-extracted, bounded
document excerpts (``document_text_extractor.DocumentTextExtraction``). It is the
opposite of an aggressive scraper: when a value is at all ambiguous it is *not*
parsed — an honest excerpt with no structured fact is always preferable to a
wrong number.

Hard rules (enforced here + by tests):
  * Every parsed fact carries its provenance: ``source_url`` + ``excerpt_id`` +
    ``page_number`` + ``confidence`` + ``needs_human_review=True``.
  * No inference of a missing financial. If it is not explicitly stated, it is
    not produced.
  * No currency conversion. The reporting currency is recorded as-found.
  * No valuation metric, price target, fair value, or upside/downside is ever
    computed — this parser only reads primary statements of fact.
  * Ambiguous lines (a labelled metric with two candidate magnitudes, or a
    number with no unit) are skipped with no fact emitted.

Facts remain ``needs_human_review=True`` until an admin verifies them — they are
never treated as verified truth downstream.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.services.sources.document_text_extractor import (
    DocumentExcerpt,
    DocumentTextExtraction,
)

# Canonical fact field names (neutral, factual — never a rating vocabulary).
FIELD_LEGAL_NAME = "company_legal_name"
FIELD_REPORTING_CURRENCY = "reporting_currency"
FIELD_FISCAL_YEAR = "fiscal_year"
FIELD_REVENUE = "revenue"
FIELD_OPERATING_PROFIT = "operating_profit"
FIELD_NET_INCOME = "net_income"
FIELD_FREE_CASH_FLOW = "free_cash_flow"
FIELD_TOTAL_ASSETS = "total_assets"
FIELD_TOTAL_DEBT = "total_debt"
FIELD_CASH = "cash_and_equivalents"
FIELD_EMPLOYEES = "employees"


class PrimaryFact(BaseModel):
    """One high-confidence primary fact parsed from a document excerpt."""

    field: str
    value: str
    numeric_value: float | None = None
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None  # million | billion | thousand | None
    period: str | None = None
    source_url: str | None = None
    excerpt_id: str | None = None
    page_number: int | None = None
    confidence: str = "medium"  # low | medium | high
    parser_warning: str | None = None
    needs_human_review: bool = True


# --------------------------------------------------------------------------- #
# Currency + number helpers
# --------------------------------------------------------------------------- #

_CURRENCY_WORDS: dict[str, str] = {
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "€": "EUR",
    "swiss franc": "CHF",
    "swiss francs": "CHF",
    "chf": "CHF",
    "sterling": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
    "£": "GBP",
    "danish krone": "DKK",
    "kroner": "DKK",
    "dkk": "DKK",
    "us dollar": "USD",
    "us dollars": "USD",
    "usd": "USD",
    "dollars": "USD",
    "$": "USD",
}
_CURRENCY_SYMBOLS = "€£$"

# A number like 20,616 or 20.6 or 1 234 (thin-space grouping), optionally scaled.
_NUM = r"(\d[\d.,  ]*\d|\d)"
_SCALE = r"(million|billion|thousand|bn|mn|m)\b"


def _norm_number(raw: str) -> float | None:
    """Parse a grouped number string to float, else None. No unit inference."""
    s = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    # Decide decimal separator: if both , and . present, the last one is decimal.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Ambiguous: 20,616 (grouping) vs 20,6 (decimal). Treat a single comma
        # with exactly 3 trailing digits as grouping; else as decimal.
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _scale_word(w: str | None) -> str | None:
    if not w:
        return None
    w = w.lower()
    if w in ("billion", "bn"):
        return "billion"
    if w in ("million", "mn", "m"):
        return "million"
    if w == "thousand":
        return "thousand"
    return None


def _find_currency(text: str) -> str | None:
    low = text.lower()
    # Prefer explicit "in millions of euros" / "reporting currency" phrasing.
    for word, code in _CURRENCY_WORDS.items():
        if word in low:
            return code
    for sym in _CURRENCY_SYMBOLS:
        if sym in text:
            return _CURRENCY_WORDS[sym]
    return None


# --------------------------------------------------------------------------- #
# Field patterns — each requires an explicit label near a single number.
# --------------------------------------------------------------------------- #

# label -> compiled regex capturing (number, optional scale)
def _money_pattern(label_alts: str) -> re.Pattern[str]:
    return re.compile(
        rf"(?:{label_alts})"
        rf"(?:\s+(?:of|was|were|amounted to|reached|totalled|totaled|:))?"
        rf"[^\d\n]{{0,25}}?"
        rf"[€£$]?\s*{_NUM}\s*(?:{_SCALE})?",
        re.IGNORECASE,
    )


_MONEY_FIELDS: list[tuple[str, re.Pattern[str]]] = [
    (FIELD_REVENUE, _money_pattern(r"revenue|net sales|total sales|sales")),
    (
        FIELD_OPERATING_PROFIT,
        _money_pattern(
            r"operating profit|operating income|ebit\b|recurring operating (?:profit|income)"
        ),
    ),
    (
        FIELD_NET_INCOME,
        _money_pattern(r"net income|net profit|profit attributable|net result|profit for the year"),
    ),
    (FIELD_FREE_CASH_FLOW, _money_pattern(r"free cash flow")),
    (FIELD_TOTAL_ASSETS, _money_pattern(r"total assets")),
    (FIELD_TOTAL_DEBT, _money_pattern(r"total debt|gross debt|total borrowings|borrowings")),
    (FIELD_CASH, _money_pattern(r"cash and cash equivalents")),
]

_FISCAL_YEAR_RE = re.compile(
    r"(?:fiscal year|financial year|year ended|for the year|as at 31|as of 31|"
    r"annual report)[^\n]{0,40}?((?:19|20)\d{2})",
    re.IGNORECASE,
)
_EMPLOYEES_RE = re.compile(
    r"(\d[\d.,  ]*\d)\s+(?:employees|people employed|staff|full-time equivalents)",
    re.IGNORECASE,
)
_EMPLOYEES_RE2 = re.compile(
    r"(?:employ(?:s|ed)?|workforce of|headcount of)\s+(?:approximately\s+)?"
    r"(\d[\d.,  ]*\d)",
    re.IGNORECASE,
)


def _ambiguous_multiple(pattern: re.Pattern[str], text: str) -> bool:
    """True when a labelled metric matches with two *different* magnitudes."""
    vals = {
        _norm_number(m.group(1))
        for m in pattern.finditer(text)
        if _norm_number(m.group(1)) is not None
    }
    return len(vals) > 1


def _parse_excerpt(excerpt: DocumentExcerpt, source_url: str | None) -> list[PrimaryFact]:
    text = excerpt.text
    facts: list[PrimaryFact] = []
    seen_fields: set[str] = set()

    def add(fact: PrimaryFact) -> None:
        if fact.field in seen_fields:
            return
        seen_fields.add(fact.field)
        facts.append(fact)

    # -- reporting currency (only if a currency is explicitly present) --------
    _currency_cues = ("in millions", "reporting currency", "in thousands", "in eur", "in chf")
    if any(kw in text.lower() for kw in _currency_cues):
        cur = _find_currency(text)
        if cur:
            add(
                PrimaryFact(
                    field=FIELD_REPORTING_CURRENCY,
                    value=cur,
                    currency=cur,
                    source_url=source_url,
                    excerpt_id=excerpt.excerpt_id,
                    page_number=excerpt.page_number,
                    confidence="high",
                )
            )

    # -- fiscal year ----------------------------------------------------------
    fy = _FISCAL_YEAR_RE.search(text)
    if fy:
        year = fy.group(1)
        add(
            PrimaryFact(
                field=FIELD_FISCAL_YEAR,
                value=year,
                numeric_value=float(year),
                period=year,
                source_url=source_url,
                excerpt_id=excerpt.excerpt_id,
                page_number=excerpt.page_number,
                confidence="high",
            )
        )

    # -- money fields ---------------------------------------------------------
    for field, pattern in _MONEY_FIELDS:
        m = pattern.search(text)
        if not m:
            continue
        num = _norm_number(m.group(1))
        if num is None:
            continue
        scale = _scale_word(m.group(2))
        currency = _find_currency(m.group(0)) or _find_currency(text)
        # Refuse ambiguity: the same label with two different magnitudes, or a
        # bare number with neither a scale nor a currency (too weak to trust).
        if _ambiguous_multiple(pattern, text):
            continue
        if scale is None and currency is None:
            continue
        conf = "high" if (scale and currency) else "medium"
        warning = (
            None
            if (scale and currency)
            else "Scale or currency inferred from surrounding text; verify."
        )
        add(
            PrimaryFact(
                field=field,
                value=m.group(0).strip(),
                numeric_value=num,
                unit="currency_amount",
                currency=currency,
                scale=scale,
                period=str(_year_hint(excerpt)),
                source_url=source_url,
                excerpt_id=excerpt.excerpt_id,
                page_number=excerpt.page_number,
                confidence=conf,
                parser_warning=warning,
            )
        )

    # -- employees ------------------------------------------------------------
    for rx in (_EMPLOYEES_RE, _EMPLOYEES_RE2):
        em = rx.search(text)
        if em:
            num = _norm_number(em.group(1))
            if num is not None and num >= 10:
                add(
                    PrimaryFact(
                        field=FIELD_EMPLOYEES,
                        value=em.group(1).strip(),
                        numeric_value=num,
                        unit="people",
                        source_url=source_url,
                        excerpt_id=excerpt.excerpt_id,
                        page_number=excerpt.page_number,
                        confidence="medium",
                    )
                )
            break

    return facts


def _year_hint(excerpt: DocumentExcerpt) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", excerpt.text)
    return int(m.group(0)) if m else None


def parse_primary_facts(
    extraction: DocumentTextExtraction,
) -> list[PrimaryFact]:
    """Parse conservative, high-confidence primary facts from an extraction.

    Returns an empty list when parsing is weak — the caller then keeps the raw
    excerpt evidence but produces no structured facts (honest under-reporting).
    Every returned fact carries full provenance and ``needs_human_review=True``.
    """
    source_url = extraction.source_url
    out: list[PrimaryFact] = []
    seen: set[str] = set()
    for excerpt in extraction.excerpts:
        for fact in _parse_excerpt(excerpt, source_url):
            # De-dup on (field) across excerpts — first (highest-ranked) wins.
            if fact.field in seen:
                continue
            seen.add(fact.field)
            out.append(fact)
    return out


__all__ = [
    "PrimaryFact",
    "parse_primary_facts",
    "FIELD_LEGAL_NAME",
    "FIELD_REPORTING_CURRENCY",
    "FIELD_FISCAL_YEAR",
    "FIELD_REVENUE",
    "FIELD_OPERATING_PROFIT",
    "FIELD_NET_INCOME",
    "FIELD_FREE_CASH_FLOW",
    "FIELD_TOTAL_ASSETS",
    "FIELD_TOTAL_DEBT",
    "FIELD_CASH",
    "FIELD_EMPLOYEES",
]
