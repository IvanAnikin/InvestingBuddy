"""Statement consistency — V3.18.1.

WHY THIS EXISTS
===============
A live report presented, as one company's FY2025 accounts, a gross profit of 2,914.8
beside an operating income of 7,001.7. Any analyst sees that pair and stops reading: a
gross profit cannot normally sit below the operating income struck after it. The
platform did not stop, because **nothing in it had ever compared one statement line with
another.** Every guard it had checked a figure against its *source*; none checked a
figure against its *neighbours*.

The root cause of that pair was a period defect, closed by the own-period rule in
``sec_fundamentals_normalizer``. This module is the control that would have caught it
anyway — and that will catch the next contamination, whose cause nobody has thought of
yet, by its symptom.

TWO STRENGTHS, KEPT APART
=========================
* ``contradiction`` — the relationship cannot hold for figures that really are one
  scope and one period: gross profit above revenue, cash above total assets, two values
  for the same metric/period/scope. Something upstream is wrong. Ratios built on the
  pair are withheld.
* ``implausible`` — the relationship *usually* holds and has known, legitimate
  exceptions: operating income above gross profit happens when other operating income
  (a disposal gain, say) is large; assets differ from liabilities plus the PARENT's
  equity by the non-controlling interest. NOTHING is withheld on it, because a
  universal rule with no exceptions would suppress correct figures — this codebase
  once withheld thirty-two correct segment sentences by being clever with a threshold.
  It is **surfaced**, so a report cannot present the pair with unqualified confidence.

Neither strength picks a winner. Which of two conflicting figures is right is a question
for a human or for a better source, never for a tie-break.

WHAT THIS IS NOT
================
It is not a period check and does not replace one: figures reach a relationship check
only if they share a period end and a scope, and a pair that does not share them is
reported as ``not_comparable`` rather than compared. It is pure — no database, no model,
no network.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: How far apart two period ends may be and still be one reporting period. Within one
#: filing every statement concept shares its period end exactly; the allowance only
#: absorbs a filer's date irregularity and is far below the ~90 days between two
#: adjacent reporting periods.
SAME_PERIOD_TOLERANCE_DAYS = 10

SEVERITY_CONTRADICTION = "contradiction"
SEVERITY_IMPLAUSIBLE = "implausible"

SEVERITIES: frozenset[str] = frozenset({SEVERITY_CONTRADICTION, SEVERITY_IMPLAUSIBLE})

#: Relative slack on an inequality, so rounding in a filing (figures printed to one
#: decimal of a million) never manufactures a finding.
_RELATIVE_SLACK = 0.005

#: Two readings of one metric/period/scope closer than this are the same reading.
_DUPLICATE_TOLERANCE = 0.01

#: How far assets may sit from liabilities + equity before the balance sheet is flagged.
#: Wide on purpose: the equity line commonly excludes non-controlling and mezzanine
#: interests, so an honest bundle is often a few percent apart. A gap this size means a
#: period, scope or unit mix, not a presentation choice.
_BALANCE_IDENTITY_TOLERANCE = 0.30


def _parse_date(value: str | None) -> datetime | None:
    try:
        return datetime.strptime(str(value or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def same_reporting_period(a: str | None, b: str | None) -> bool:
    """Do two period-end dates name one reporting period?

    Unreadable on either side answers ``True``: unknown is not a mismatch, and a check
    that refused on an unreadable date would withhold correct figures from an irregular
    payload. The callers that must fail closed on *known* mismatches get exactly that.
    """
    first, second = _parse_date(a), _parse_date(b)
    if first is None or second is None:
        return True
    return abs((first - second).days) <= SAME_PERIOD_TOLERANCE_DAYS


@dataclass(frozen=True)
class StatementFigure:
    """One statement line, with everything needed to decide what it may be compared to."""

    metric: str
    value: float
    period_end: str | None = None
    scope: str | None = "group"
    currency: str | None = None
    source: str | None = None


@dataclass(frozen=True)
class Inconsistency:
    """One relationship that does not hold, and what follows from it."""

    code: str
    severity: str
    metrics: tuple[str, ...]
    message: str
    #: Derived metrics that must not be computed from this pair.
    withhold_derived: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.severity not in SEVERITIES:
            raise ValueError(f"{self.severity!r} is not a consistency severity.")

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "severity": self.severity,
            "metrics": list(self.metrics),
            "message": self.message,
            "withhold_derived": list(self.withhold_derived),
        }


@dataclass
class ConsistencyReport:
    inconsistencies: list[Inconsistency] = field(default_factory=list)
    #: Pairs that were NOT compared because they do not share a period or scope. Kept so
    #: "nothing found" can be told apart from "nothing could be checked".
    not_comparable: list[str] = field(default_factory=list)
    checks_run: int = 0

    @property
    def has_contradiction(self) -> bool:
        return any(i.severity == SEVERITY_CONTRADICTION for i in self.inconsistencies)

    @property
    def is_clean(self) -> bool:
        return not self.inconsistencies

    @property
    def withheld_derived(self) -> frozenset[str]:
        return frozenset(
            name for i in self.inconsistencies for name in i.withhold_derived
        )

    @property
    def flagged_metrics(self) -> frozenset[str]:
        return frozenset(m for i in self.inconsistencies for m in i.metrics)

    def to_dict(self) -> dict[str, Any]:
        return {
            "checks_run": self.checks_run,
            "is_clean": self.is_clean,
            "has_contradiction": self.has_contradiction,
            "inconsistencies": [i.to_dict() for i in self.inconsistencies],
            "not_comparable": list(self.not_comparable),
        }


def _comparable(a: StatementFigure, b: StatementFigure) -> str | None:
    """``None`` when the two may be compared, else the reason they may not."""
    if (a.scope or "group") != (b.scope or "group"):
        return f"{a.metric} is {a.scope} and {b.metric} is {b.scope}"
    if not same_reporting_period(a.period_end, b.period_end):
        return f"{a.metric} is for {a.period_end} and {b.metric} is for {b.period_end}"
    if a.currency and b.currency and a.currency != b.currency:
        return f"{a.metric} is in {a.currency} and {b.metric} is in {b.currency}"
    return None


def _exceeds(larger: float, smaller: float) -> bool:
    """Is ``larger`` really above ``smaller``, beyond printing precision?"""
    return larger > smaller + max(abs(smaller), abs(larger)) * _RELATIVE_SLACK


def find_conflicting_duplicates(figures: Iterable[StatementFigure]) -> list[Inconsistency]:
    """Two values for one metric, period and scope.

    The commonest way this arises is two CHANNELS — a regulator's structured data and the
    issuer's own document — both supplying the line. Agreement is corroboration;
    disagreement is a fact about the evidence that the report owes its reader.
    """
    seen: dict[tuple[str, str, str], StatementFigure] = {}
    found: list[Inconsistency] = []
    for figure in figures:
        end = _parse_date(figure.period_end)
        key = (
            figure.metric,
            end.strftime("%Y-%m") if end else str(figure.period_end),
            figure.scope or "group",
        )
        first = seen.setdefault(key, figure)
        if first is figure:
            continue
        if first.currency and figure.currency and first.currency != figure.currency:
            continue
        base = max(abs(first.value), abs(figure.value))
        if base and abs(first.value - figure.value) / base > _DUPLICATE_TOLERANCE:
            found.append(
                Inconsistency(
                    code="conflicting_duplicate",
                    severity=SEVERITY_CONTRADICTION,
                    metrics=(figure.metric,),
                    message=(
                        f"{figure.metric} for the period ending {figure.period_end} is "
                        f"{first.value:,.1f} from {first.source or 'one source'} and "
                        f"{figure.value:,.1f} from {figure.source or 'another'}. Both are "
                        "kept; neither is chosen."
                    ),
                )
            )
    return found


def check_statement_consistency(figures: Sequence[StatementFigure]) -> ConsistencyReport:
    """Every relationship the supplied figures can be tested against."""
    report = ConsistencyReport()
    by_metric: dict[str, StatementFigure] = {}
    for figure in figures:
        by_metric.setdefault(figure.metric, figure)

    def pair(first: str, second: str) -> tuple[StatementFigure, StatementFigure] | None:
        a, b = by_metric.get(first), by_metric.get(second)
        if a is None or b is None:
            return None
        reason = _comparable(a, b)
        if reason is not None:
            report.not_comparable.append(reason)
            return None
        report.checks_run += 1
        return a, b

    if (found := pair("gross_profit", "revenue")) is not None:
        gross, revenue = found
        if revenue.value > 0 and _exceeds(gross.value, revenue.value):
            report.inconsistencies.append(
                Inconsistency(
                    code="gross_profit_exceeds_revenue",
                    severity=SEVERITY_CONTRADICTION,
                    metrics=("gross_profit", "revenue"),
                    message=(
                        f"Gross profit ({gross.value:,.1f}) is above revenue "
                        f"({revenue.value:,.1f}) for one scope and period. Cost of sales "
                        "cannot be negative, so these two figures are not one "
                        "statement's."
                    ),
                    withhold_derived=("gross_margin",),
                )
            )

    if (found := pair("operating_income", "gross_profit")) is not None:
        operating, gross = found
        if gross.value > 0 and _exceeds(operating.value, gross.value):
            report.inconsistencies.append(
                Inconsistency(
                    code="operating_income_exceeds_gross_profit",
                    severity=SEVERITY_IMPLAUSIBLE,
                    metrics=("operating_income", "gross_profit"),
                    message=(
                        f"Operating income ({operating.value:,.1f}) is above gross profit "
                        f"({gross.value:,.1f}). That is possible only when other "
                        "operating income exceeds operating expenses; without evidence "
                        "of that, the two figures are more likely from different "
                        "periods, scopes or definitions."
                    ),
                    # NOT withheld. `implausible` means "usually true, with legitimate
                    # exceptions", and withholding on it would make it a contradiction in
                    # everything but name. The pair is surfaced; the margin stands beside
                    # the warning rather than silently.
                )
            )

    if (found := pair("operating_income", "revenue")) is not None:
        operating, revenue = found
        if revenue.value > 0 and _exceeds(operating.value, revenue.value):
            report.inconsistencies.append(
                Inconsistency(
                    code="operating_income_exceeds_revenue",
                    severity=SEVERITY_IMPLAUSIBLE,
                    metrics=("operating_income", "revenue"),
                    message=(
                        f"Operating income ({operating.value:,.1f}) is above revenue "
                        f"({revenue.value:,.1f}); an operating margin above 100% needs "
                        "an explanation the figures alone do not give."
                    ),
                )
            )

    if (found := pair("cash_and_equivalents", "total_assets")) is not None:
        cash, assets = found
        if assets.value > 0 and _exceeds(cash.value, assets.value):
            report.inconsistencies.append(
                Inconsistency(
                    code="cash_exceeds_total_assets",
                    severity=SEVERITY_CONTRADICTION,
                    metrics=("cash_and_equivalents", "total_assets"),
                    message=(
                        f"Cash ({cash.value:,.1f}) is above total assets "
                        f"({assets.value:,.1f}), of which it is a part."
                    ),
                )
            )

    assets_f = by_metric.get("total_assets")
    liabilities_f = by_metric.get("total_liabilities")
    equity_f = by_metric.get("shareholders_equity")
    if assets_f and liabilities_f and equity_f:
        reasons = [
            r
            for r in (_comparable(assets_f, liabilities_f), _comparable(assets_f, equity_f))
            if r
        ]
        if reasons:
            report.not_comparable.extend(reasons)
        elif assets_f.value > 0:
            report.checks_run += 1
            gap = abs(assets_f.value - (liabilities_f.value + equity_f.value))
            if gap / assets_f.value > _BALANCE_IDENTITY_TOLERANCE:
                report.inconsistencies.append(
                    Inconsistency(
                        code="balance_sheet_does_not_balance",
                        # IMPLAUSIBLE, not a contradiction. Found by review: the equity
                        # line this platform reads is the PARENT's, and for an Up-C or
                        # majority-NCI filer the non-controlling share alone exceeds any
                        # tolerance — assets 20,000 / liabilities 6,000 / parent equity
                        # 4,000 is a balanced sheet.
                        severity=SEVERITY_IMPLAUSIBLE,
                        metrics=("total_assets", "total_liabilities", "shareholders_equity"),
                        message=(
                            f"Total assets ({assets_f.value:,.1f}) differ from liabilities "
                            f"plus equity ({liabilities_f.value + equity_f.value:,.1f}) by "
                            f"{gap / assets_f.value:.0%}. Non-controlling or mezzanine "
                            "interests may explain it; if they do not, these figures are "
                            "from different periods, scopes or units."
                        ),
                    )
                )

    report.inconsistencies.extend(find_conflicting_duplicates(figures))
    return report


__all__ = [
    "SAME_PERIOD_TOLERANCE_DAYS",
    "SEVERITY_CONTRADICTION",
    "SEVERITY_IMPLAUSIBLE",
    "ConsistencyReport",
    "Inconsistency",
    "StatementFigure",
    "check_statement_consistency",
    "find_conflicting_duplicates",
    "same_reporting_period",
]
