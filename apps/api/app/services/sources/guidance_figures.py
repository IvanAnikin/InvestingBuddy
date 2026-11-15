"""Guided figures in a guidance finding's statement — V3.20 slice 4.3 (spec §3.4).

Pure and fail-closed. A ledger finding with ``statement_kind = "guidance"`` carries its
period (``period_key``) and its citation, but its figures only as text: "FY2026 revenue
$25,900m to $26,100m; EPS $20." This module reads, per clause:

* the metric — revenue (revenue, net sales, sales, turnover) or diluted EPS (EPS,
  earnings per share). Only metrics an actual exists for; anything else (EBITDA, margin,
  capex, …) is left alone;
* a range ("$1.15 to $1.20 billion", "between $1.15bn and $1.20bn", "$25,900m–$26,100m")
  or a point ("approximately $1.2 billion", "EPS $20");
* currency and scale. A per-share figure takes no scale.

A clause naming two metrics, or a figure whose metric cannot be told, yields NOTHING
for that clause — a guessed attribution is how a margin becomes a revenue figure.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

METRIC_REVENUE = "revenue"
METRIC_EPS = "eps"

_METRIC_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (METRIC_EPS, re.compile(r"\b(?:diluted\s+)?(?:eps|earnings per (?:diluted )?share)\b",
                            re.I)),
    (METRIC_REVENUE, re.compile(r"\b(?:revenues?|net sales|sales|turnover)\b", re.I)),
)
#: Metrics whose presence in a clause makes a revenue / EPS attribution unsafe.
_OTHER_METRIC_RE = re.compile(
    r"\b(?:ebitda|ebit|operating (?:income|profit|margin)|gross (?:profit|margin)|margin|"
    r"net income|free cash flow|capex|capital expenditure|backlog|orders?|bookings)\b",
    re.I)

_CURRENCY = r"(?P<{n}>US\$|A\$|C\$|\$|€|£|USD|EUR|GBP|AUD|CAD)?"
_NUMBER = r"(?P<{n}>\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?|\d+(?:\.\d+)?)"
_SCALE = (r"(?:\s?(?P<{n}>billion|bn|b|million|mn|mm|m|thousand|k)\b)?")


def _amount(tag: str) -> str:
    return (_CURRENCY.format(n=f"cur{tag}") + r"\s?" + _NUMBER.format(n=f"num{tag}")
            + _SCALE.format(n=f"sc{tag}"))


_RANGE_RE = re.compile(
    r"(?:between\s+)?" + _amount("1") + r"\s*(?:to|-|–|—|and)\s*" + _amount("2"), re.I)
_POINT_RE = re.compile(_amount("1"), re.I)

_CURRENCY_CODES = {"US$": "USD", "$": "USD", "A$": "AUD", "C$": "CAD", "€": "EUR",
                   "£": "GBP", "USD": "USD", "EUR": "EUR", "GBP": "GBP", "AUD": "AUD",
                   "CAD": "CAD"}
_SCALES = {"billion": Decimal("1e9"), "bn": Decimal("1e9"), "b": Decimal("1e9"),
           "million": Decimal("1e6"), "mn": Decimal("1e6"), "mm": Decimal("1e6"),
           "m": Decimal("1e6"), "thousand": Decimal("1e3"), "k": Decimal("1e3")}
_CLAUSE_SPLIT = re.compile(r";|\.\s+(?=[A-Z])|\n")
#: Within a sentence: ", and adjusted EBITDA …", " and EPS …", ", revenue …" — a
#: conjunction or comma followed by a WORD. "between $1 and $2" is untouched: a number
#: follows its "and".
_PART_SPLIT = re.compile(r"(?:,\s+|\s+)and\s+(?=[A-Za-z])|,\s+(?=[A-Za-z])")


@dataclass(frozen=True)
class GuidedFigure:
    metric: str
    low: Decimal | None
    high: Decimal | None
    point: Decimal | None
    currency: str | None
    text: str

    @property
    def mid(self) -> Decimal | None:
        if self.point is not None:
            return self.point
        if self.low is not None and self.high is not None:
            return (self.low + self.high) / 2
        return None


def _num(raw: str | None) -> Decimal | None:
    try:
        return Decimal((raw or "").replace(",", "")) if raw else None
    except InvalidOperation:
        return None


def _value(match: re.Match[str], tag: str, *, metric: str,
           fallback_scale: str | None) -> Decimal | None:
    number = _num(match.group(f"num{tag}"))
    if number is None:
        return None
    if metric == METRIC_EPS:
        return number
    scale = (match.group(f"sc{tag}") or fallback_scale or "").lower()
    return number * _SCALES[scale] if scale in _SCALES else number


def _metric_of(clause: str) -> str | None:
    found = {metric for metric, pattern in _METRIC_RES if pattern.search(clause)}
    if len(found) != 1:
        return None
    # "sales" inside "earnings per share"? No — but "operating margin on sales" names
    # another metric: refuse.
    if _OTHER_METRIC_RE.search(clause):
        return None
    return found.pop()


def guided_figures(statement: str) -> list[GuidedFigure]:
    """Every unambiguous revenue / EPS guidance figure in ``statement``."""
    out: list[GuidedFigure] = []
    parts = [p for c in _CLAUSE_SPLIT.split(statement or "") for p in _PART_SPLIT.split(c)]
    for clause in parts:
        clause = clause.strip()
        if not clause:
            continue
        metric = _metric_of(clause)
        if metric is None:
            continue
        figure = _range(clause, metric) or _point(clause, metric)
        if figure is not None:
            out.append(figure)
    return out


def _currency(match: re.Match[str], tags: tuple[str, ...]) -> str | None:
    codes = {_CURRENCY_CODES.get((match.group(f"cur{t}") or "").upper()
                                 if match.group(f"cur{t}") not in ("€", "£")
                                 else match.group(f"cur{t}"))
             for t in tags if match.group(f"cur{t}")}
    codes.discard(None)
    return codes.pop() if len(codes) == 1 else None


def _range(clause: str, metric: str) -> GuidedFigure | None:
    matches = [m for m in _RANGE_RE.finditer(clause)
               if m.group("num1") and m.group("num2")
               and (m.group("cur1") or m.group("cur2") or metric == METRIC_EPS
                    or m.group("sc1") or m.group("sc2"))]
    if len(matches) != 1:
        return None
    m = matches[0]
    # "$1.15 to $1.20 billion": the scale written once applies to both ends.
    low = _value(m, "1", metric=metric, fallback_scale=m.group("sc2"))
    high = _value(m, "2", metric=metric, fallback_scale=m.group("sc1"))
    if low is None or high is None or low > high:
        return None
    return GuidedFigure(metric=metric, low=low, high=high, point=None,
                        currency=_currency(m, ("1", "2")), text=m.group(0).strip())


def _point(clause: str, metric: str) -> GuidedFigure | None:
    # A point needs a currency sign (a bare number may be a year, a count, a percent).
    matches = [m for m in _POINT_RE.finditer(clause)
               if m.group("cur1") and m.group("num1")
               and not clause[m.end():m.end() + 2].lstrip().startswith("%")]
    if len(matches) != 1:
        return None
    m = matches[0]
    value = _value(m, "1", metric=metric, fallback_scale=None)
    if value is None:
        return None
    return GuidedFigure(metric=metric, low=None, high=None, point=value,
                        currency=_currency(m, ("1",)), text=m.group(0).strip())


__all__ = ["METRIC_EPS", "METRIC_REVENUE", "GuidedFigure", "guided_figures"]
