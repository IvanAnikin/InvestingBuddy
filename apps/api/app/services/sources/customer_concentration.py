"""Customer concentration from a 10-K's inline XBRL — V3.20 slice 2.4 (spec §3.2).

A US filer that depends on a few customers tags it: ``us-gaap:ConcentrationRiskPercentage1``
on the ``srt:MajorCustomersAxis`` (the customer — a filer extension member), with the
``ConcentrationRiskByBenchmarkAxis`` (of revenue, of receivables) and usually the
``ConcentrationRiskByTypeAxis``. Seen live 2026-11-15:

* Ultra Clean — ``uctt:LamResearchCorporationMember`` 37.0 % of revenue FY2025, named;
* Babcock & Wilcox — ``bw:OneCustomerMember`` 10 % of revenue, anonymous;
* Ichor — ``ichr:ThreeMajorCustomersMember`` 76 % of revenue, an aggregate.

``companyfacts`` drops dimensions, so this reads the filing's own iXBRL (``inline_xbrl``).

THE RULES (fail closed)
=======================
* Only that concept on that axis. Any OTHER axis on the fact (a segment, a product, a
  timing axis) is an intersection and is skipped — a share of a segment is not a share of
  the company.
* A type axis, when present, must be customer or credit concentration; the benchmark must
  be revenue (→ ``revenue``) or receivables (→ ``receivables``). Anything else is skipped.
* The customer is the member's own words: ``LamResearchCorporationMember`` → "Lam Research
  Corporation". The label linkbase is not read, and nothing resolves a name to an entity.
  ``kind``: ``named`` | ``anonymous`` ("Customer A", "One customer", "Largest customer") |
  ``aggregate`` ("Three major customers", "Total customer", "A, B and C").
* The value is the tag's meaning (a fraction, 0.370) × 100. Outside 0–100 → skipped.
* The period comes from the document's checked fiscal frame (``inline_xbrl_facts``): a
  ~12-month span ending at a fiscal year end is ``FY<year>``; an instant at a year end is
  ``FY<year> year-end``. A frame that fails its check yields nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

CONCEPT_LOCAL = "ConcentrationRiskPercentage1"
CUSTOMER_AXIS_LOCAL = "MajorCustomersAxis"
BENCHMARK_AXIS_LOCAL = "ConcentrationRiskByBenchmarkAxis"
TYPE_AXIS_LOCAL = "ConcentrationRiskByTypeAxis"

_REVENUE_BENCHMARKS = frozenset({
    "RevenueFromContractWithCustomerMember", "SalesRevenueNetMember", "RevenuesMember",
    "SalesRevenueGoodsNetMember", "SalesRevenueServicesNetMember",
    "RevenueFromContractWithCustomerProductAndServiceBenchmarkMember",
    "RevenueBenchmarkMember",
})
_RECEIVABLE_BENCHMARKS = frozenset({
    "AccountsReceivableMember", "AccountsReceivableBenchmarkMember",
    "AccountsNotesAndLoansReceivableMember",
})
_TYPES = frozenset({"CustomerConcentrationRiskMember", "CreditConcentrationRiskMember"})

_NUMBER_WORDS = ("one", "two", "three", "four", "five", "six", "seven", "eight", "nine",
                 "ten", "single")
_ANONYMOUS_RE = re.compile(
    r"^(customer|client)( (number|no))? ?([a-z]|\d+|one|two|three|four|five|six|seven|eight|"
    r"nine|ten)$|^(one|a|single|largest|major|significant|top|primary|first|second|third)"
    r"( (major|significant|largest|top))? (customer|client)$|^(largest|top) (customer|client)"
    r" ?\d*$"
)
_AGGREGATE_RE = re.compile(
    r"^(total|all|combined|aggregate|other)\b|\b(customers|clients)\b|\band\b|"
    r"^(" + "|".join(_NUMBER_WORDS[1:]) + r")\b"
)
_CAMEL_RE = re.compile(r"(?<=[a-z0-9])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")


@dataclass(frozen=True)
class CustomerConcentration:
    customer: str
    #: ``named`` | ``anonymous`` | ``aggregate``.
    kind: str
    member: str
    share_pct: float
    share_basis: str
    period: str
    start: date | None
    end: date | None
    instant: date | None
    context_id: str
    printed: str


def member_words(member: str) -> str:
    """``uctt:AppliedMaterialsInc.LamResearchCorporationAndASMLMember`` → words."""
    local = member.rpartition(":")[2]
    if local.endswith("Member"):
        local = local[: -len("Member")]
    text = _CAMEL_RE.sub(" ", local.replace(".", ". ").replace("_", " "))
    return re.sub(r"\s+", " ", text).strip()


def customer_kind(words: str) -> str:
    folded = words.casefold().strip(" .")
    if _ANONYMOUS_RE.match(folded):
        return "anonymous"
    if _AGGREGATE_RE.search(folded):
        return "aggregate"
    return "named"


def _local(qname: str) -> str:
    return qname.rpartition(":")[2]


def _frame(document: Any) -> tuple[int, date, bool] | None:
    from app.services.sources.inline_xbrl_facts import (
        _HALF_FRAME,
        _MONTHS_TO_FISCAL_YEAR_END,
        _add_months,
        frame_is_consistent,
    )

    year, focus, end = (document.fiscal_year_focus, document.fiscal_period_focus,
                        document.period_end)
    if year is None or focus is None or end is None or not frame_is_consistent(document):
        return None
    return year, _add_months(end, _MONTHS_TO_FISCAL_YEAR_END[focus]), focus in _HALF_FRAME


def _period(fact: Any, frame: tuple[int, date, bool]) -> str | None:
    from app.services.sources.inline_xbrl_facts import period_label

    year, fiscal_year_end, half = frame
    label = period_label(fact, fiscal_year=year, fiscal_year_end=fiscal_year_end,
                         half_frame=half)
    if label is None:
        return None
    if fact.is_instant:
        return f"FY{label} year-end" if label.isdigit() else f"{label} end"
    return f"FY{label}" if label.isdigit() else label


def concentrations_from_inline_xbrl(document: Any) -> list[CustomerConcentration]:
    """Every customer-concentration fact the module rules accept. Pure; never raises."""
    frame = _frame(document)
    if frame is None:
        return []
    latest_end = (document.period_end or date.max) + timedelta(days=10)
    out: list[CustomerConcentration] = []
    seen: set[tuple[str, str, str]] = set()
    for fact in getattr(document, "facts", None) or []:
        if _local(fact.concept) != CONCEPT_LOCAL:
            continue
        axes = {_local(axis): member for axis, member in fact.dimensions}
        member = axes.pop(CUSTOMER_AXIS_LOCAL, None)
        if member is None or member == "typed":
            continue
        benchmark = _local(axes.pop(BENCHMARK_AXIS_LOCAL, "") or "")
        kind_member = axes.pop(TYPE_AXIS_LOCAL, None)
        if axes:
            continue  # an intersection with another axis
        if kind_member is not None and _local(kind_member) not in _TYPES:
            continue
        if benchmark in _REVENUE_BENCHMARKS:
            basis = "revenue"
        elif benchmark in _RECEIVABLE_BENCHMARKS:
            basis = "receivables"
        else:
            continue
        if fact.period_end is None or fact.period_end > latest_end:
            continue
        pct = fact.value * Decimal(100)
        if not (Decimal(0) < pct <= Decimal(100)):
            continue
        period = _period(fact, frame)
        if period is None:
            continue
        key = (member, basis, period)
        if key in seen:
            continue  # the same fact tagged twice (a table and a note)
        seen.add(key)
        words = member_words(member)
        out.append(CustomerConcentration(
            customer=words or member, kind=customer_kind(words), member=member,
            share_pct=float(round(pct, 4)), share_basis=basis, period=period,
            start=fact.start, end=fact.end, instant=fact.instant,
            context_id=fact.context_id, printed=fact.text,
        ))
    return out


__all__ = [
    "CustomerConcentration",
    "concentrations_from_inline_xbrl",
    "customer_kind",
    "member_words",
]
