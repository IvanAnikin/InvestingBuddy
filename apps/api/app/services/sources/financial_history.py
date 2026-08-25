"""Bounded, comparable HISTORICAL FINANCIAL SERIES from already-extracted facts.

Private-use production readiness, PR-B.

Phase 32D taught the extractor to rebuild borderless multi-year tables, and a
real Pandora annual report now yields ~52 period-scoped facts covering
FY2021-FY2025. Almost none of that reached a human: every downstream consumer
(`_high_confidence_facts_for`, the canonical snapshot, the council evidence
pack) takes ONE representative value per field and drops the rest. The council
could still say "no historical revenue trend information" while a complete
five-year revenue series sat in the database.

This module turns those facts into series. It is deliberately a PURE FUNCTION
over facts already produced, validated and scoped elsewhere — it fetches
nothing, promotes nothing, and invents nothing.

Two rules do most of the work:

**Grouping is by full identity, not by metric name.** A series is keyed by
(metric, scope, period type, currency, unit, scale). Group revenue and
Specialist Watchmakers revenue are two series. DKK and EUR revenue are two
series. FY and H1 revenue are two series. Nothing is merged because the labels
happened to match.

**Comparability is fail-closed.** A series that cannot support a like-for-like
comparison is still RETURNED — with its values, its provenance and an explicit
reason it is not comparable — rather than being silently dropped or silently
compared. Only deterministic descriptive arithmetic is ever performed (absolute
change, percentage change, margin change in percentage points). There is no
forecasting, no projection, no annualisation and no expected return.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.sources.fact_scope import FactScope, parse_scope
from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    ReportingPeriod,
    parse_period,
)

# ── Bounds ───────────────────────────────────────────────────────────────── #

#: Default and hard ceiling on the number of periods kept per series. The
#: newest periods win; older ones are dropped, never averaged away.
DEFAULT_MAX_PERIODS = 5
MAX_PERIODS_CEILING = 10
#: A single observation is not a trend. A series below this is still returned
#: (its value is real evidence) but is marked not-comparable.
MIN_PERIODS_FOR_TREND = 2
#: Hard cap on how many distinct series are built, so a pathological document
#: cannot grow the evidence pack without bound.
MAX_SERIES = 40

# ── Metric vocabulary ────────────────────────────────────────────────────── #

#: The metrics worth a series, in presentation order. A metric absent from a
#: document simply has no series — nothing is forced or interpolated.
HISTORY_METRICS: tuple[str, ...] = (
    "revenue",
    "operating_profit",
    "recurring_operating_profit",
    "operating_margin",
    "recurring_operating_margin",
    "net_income",
    "operating_cash_flow",
    "free_cash_flow",
    "total_assets",
    "total_equity",
    "net_debt",
    "net_cash",
    "employees",
)
_METRIC_ORDER = {m: i for i, m in enumerate(HISTORY_METRICS)}

#: Metrics already expressed as a percentage. Their period-over-period change
#: is reported in PERCENTAGE POINTS, never as a percent change of a percent —
#: "the margin rose 4.9%" when it moved 23.9% -> 20.6% is simply false.
_PERCENT_METRICS: frozenset[str] = frozenset(
    {"operating_margin", "recurring_operating_margin"}
)
#: Metrics that carry no currency at all. Requiring one would silently drop
#: every headcount series.
_UNITLESS_METRICS: frozenset[str] = frozenset({"employees"})

# ── Comparability verdicts ───────────────────────────────────────────────── #

COMPARABLE = "comparable"
NOT_COMPARABLE = "not_comparable"

REASON_SINGLE_PERIOD = "single_period"
REASON_CURRENCY_MISMATCH = "currency_mismatch"
REASON_UNIT_MISMATCH = "unit_mismatch"
REASON_SCOPE_UNKNOWN = "scope_unknown"
REASON_PERIOD_UNKNOWN = "period_unknown"
REASON_RESTATED_PERIOD = "restated_period"

COMPLETENESS_COMPLETE = "complete"
COMPLETENESS_PARTIAL = "partial"

# ── Derived-calculation vocabulary ───────────────────────────────────────── #

CALC_ABSOLUTE_CHANGE = "absolute_change"
CALC_PERCENT_CHANGE = "percent_change"
CALC_PERCENTAGE_POINT_CHANGE = "percentage_point_change"


@dataclass(frozen=True)
class HistoryPoint:
    """One observation, with the provenance that makes it citeable."""

    period: ReportingPeriod
    value: float
    #: The raw as-found text, never normalised away.
    value_text: str | None = None
    source_url: str | None = None
    page_number: int | None = None
    table_location: str | None = None
    confidence: str | None = None
    #: True when a later fact restated this same period and this one lost.
    superseded: bool = False

    @property
    def period_label(self) -> str:
        return self.period.label()


@dataclass(frozen=True)
class DerivedChange:
    """A deterministic descriptive calculation over exactly two observations.

    Carries its own inputs and formula so a reader can check it without
    re-deriving anything. Never a forecast, never a projection.
    """

    calculation: str
    from_period: str
    to_period: str
    from_value: float
    to_value: float
    value: float
    unit: str
    formula: str
    provenance: str = "derived"


@dataclass
class FinancialHistorySeries:
    """One metric, one scope, one period type, one unit — ordered oldest first."""

    metric: str
    scope: FactScope
    period_type: str
    currency: str | None = None
    unit: str | None = None
    scale: str | None = None
    points: list[HistoryPoint] = field(default_factory=list)
    comparability: str = NOT_COMPARABLE
    comparability_reasons: list[str] = field(default_factory=list)
    completeness: str = COMPLETENESS_PARTIAL
    #: Periods missing between the first and last observed period.
    missing_periods: list[str] = field(default_factory=list)
    changes: list[DerivedChange] = field(default_factory=list)

    @property
    def period_count(self) -> int:
        return len(self.points)

    @property
    def scope_label(self) -> str:
        return self.scope.human_label()

    @property
    def is_comparable(self) -> bool:
        return self.comparability == COMPARABLE

    def unit_label(self) -> str:
        """Compact unit for display, e.g. ``DKK million`` / ``%`` / ``count``."""
        if self.metric in _PERCENT_METRICS:
            return "%"
        if self.metric in _UNITLESS_METRICS:
            return "count"
        bits = [b for b in (self.currency, self.scale) if b]
        return " ".join(bits) if bits else (self.unit or "")

    def _format(self, value: float) -> str:
        """Percent metrics always keep one decimal — a margin printed as "25"
        where the document said "25.0%" reads as lost precision."""
        if self.metric in _PERCENT_METRICS:
            return f"{value:,.1f}"
        return _fmt(value)

    def compact_line(self) -> str:
        """One dense line: ``FY2021 x · FY2022 y · …`` — the council-facing form.

        Deliberately compact: the whole point of PR-B is to give the council a
        real trend WITHOUT dropping 52 raw facts into a token budget.
        """
        values = " · ".join(
            f"{p.period_label} {self._format(p.value)}"
            for p in self.points
            if not p.superseded
        )
        unit = self.unit_label()
        head = f"{self.metric} ({self.scope_label})"
        return f"{head}{f' [{unit}]' if unit else ''}: {values}"


@dataclass
class FinancialHistory:
    """Every series built for one company, bounded and ordered."""

    series: list[FinancialHistorySeries] = field(default_factory=list)
    #: Series that were built but could not support a comparison, kept so the
    #: absence of a trend is auditable rather than invisible.
    skipped_reasons: dict[str, int] = field(default_factory=dict)

    @property
    def available(self) -> bool:
        return bool(self.series)

    @property
    def comparable_series(self) -> list[FinancialHistorySeries]:
        return [s for s in self.series if s.is_comparable]

    def for_metric(self, metric: str) -> list[FinancialHistorySeries]:
        return [s for s in self.series if s.metric == metric]

    def group_series(self) -> list[FinancialHistorySeries]:
        return [s for s in self.series if s.scope.is_group]


def _fmt(value: float) -> str:
    """Render a value the way the document would, without inventing precision."""
    if value == int(value) and abs(value) >= 1:
        return f"{int(value):,}"
    return f"{value:,.1f}"


def _fact_get(fact: Any, key: str) -> Any:
    if isinstance(fact, dict):
        return fact.get(key)
    return getattr(fact, key, None)


def _metric_of(fact: Any) -> str | None:
    """The canonical metric name, from either fact shape.

    ``PrimaryFact``/report dicts call it ``field``; ``ValidatedFact``/ORM rows
    call it ``label``. Both reach this module, so both are accepted rather than
    forcing one caller to translate.
    """
    metric = _fact_get(fact, "field") or _fact_get(fact, "label")
    if not isinstance(metric, str):
        return None
    metric = metric.strip().lower()
    return metric if metric in _METRIC_ORDER else None


def _numeric_of(fact: Any) -> float | None:
    raw = _fact_get(fact, "numeric_value")
    if raw is None:
        raw = _fact_get(fact, "value_numeric")
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def _confidence_rank(fact: Any) -> tuple[int, float]:
    """Ordering for choosing a representative when a period is restated.

    Deterministic and source-strength based: a higher stated confidence wins,
    then a higher numeric confidence. Never "the biggest number" and never
    "whichever we saw first".
    """
    conf = _fact_get(fact, "confidence")
    if isinstance(conf, str):
        return ({"high": 2, "medium": 1, "low": 0}.get(conf.lower(), 0), 0.0)
    try:
        return (1, float(conf))
    except (TypeError, ValueError):
        return (0, 0.0)


@dataclass(frozen=True)
class _SeriesKey:
    metric: str
    scope_key: str
    period_type: str
    currency: str | None
    unit: str | None
    scale: str | None


def build_financial_history(
    facts: "list[Any] | None",
    *,
    max_periods: int = DEFAULT_MAX_PERIODS,
    period_type: str = PERIOD_TYPE_ANNUAL,
) -> FinancialHistory:
    """Build every bounded, comparable series present in ``facts``.

    ``facts`` may be report-shaped dicts or ``ValidatedFact``-shaped objects.
    Facts whose metric, value, scope or period cannot be resolved are counted
    in ``skipped_reasons`` rather than silently discarded — a missing trend
    must be explainable.

    ``period_type`` selects which KIND of series to build (annual by default).
    Interim series are built by asking for them explicitly; they are never
    mixed into an annual series, which is the ``INTERIM_AS_ANNUAL``
    contradiction this campaign forbids.
    """
    history = FinancialHistory()
    cap = max(MIN_PERIODS_FOR_TREND, min(int(max_periods or 0), MAX_PERIODS_CEILING))

    grouped: dict[_SeriesKey, list[tuple[ReportingPeriod, Any, float]]] = {}
    # The as-found scope label per key. Reconstructing it from the casefolded
    # ``scope_key`` would show a human "specialist watchmakers" where the
    # document said "Specialist Watchmakers".
    scopes: dict[str, FactScope] = {}
    for fact in facts or []:
        metric = _metric_of(fact)
        if metric is None:
            continue
        value = _numeric_of(fact)
        if value is None:
            _bump(history, "no_numeric_value")
            continue
        period = parse_period(_fact_get(fact, "period"))
        if period.is_unknown:
            _bump(history, REASON_PERIOD_UNKNOWN)
            continue
        if period.period_type != period_type:
            continue
        scope = parse_scope(_fact_get(fact, "scope"))
        if scope.scope_key is None:
            # An unscoped fact cannot be placed in a series: it may be the
            # Group figure or a segment one, and guessing is how a segment
            # trend gets presented as the Group's.
            _bump(history, REASON_SCOPE_UNKNOWN)
            continue
        key = _SeriesKey(
            metric=metric,
            scope_key=scope.scope_key,
            period_type=period.period_type or period_type,
            currency=_norm(_fact_get(fact, "currency")),
            unit=_norm(_fact_get(fact, "unit")),
            scale=_norm(_fact_get(fact, "scale")),
        )
        scopes.setdefault(scope.scope_key, scope)
        grouped.setdefault(key, []).append((period, fact, value))

    for key in sorted(
        grouped,
        key=lambda k: (_METRIC_ORDER.get(k.metric, 99), k.scope_key, k.currency or ""),
    )[:MAX_SERIES]:
        series = _build_one(
            key, grouped[key], cap=cap, scope=scopes[key.scope_key]
        )
        if series is not None:
            history.series.append(series)
    return history


def _norm(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _bump(history: FinancialHistory, reason: str) -> None:
    history.skipped_reasons[reason] = history.skipped_reasons.get(reason, 0) + 1


def _build_one(
    key: _SeriesKey,
    entries: "list[tuple[ReportingPeriod, Any, float]]",
    *,
    cap: int,
    scope: FactScope,
) -> FinancialHistorySeries | None:
    series = FinancialHistorySeries(
        metric=key.metric,
        scope=scope,
        period_type=key.period_type,
        currency=key.currency,
        unit=key.unit,
        scale=key.scale,
    )

    # One representative per period. A restatement (two facts, same period,
    # different values) resolves deterministically by source strength, and the
    # loser is KEPT as a superseded point so the restatement stays auditable.
    by_period: dict[str, list[tuple[ReportingPeriod, Any, float]]] = {}
    for period, fact, value in entries:
        pk = period.key
        if pk is None:
            continue
        by_period.setdefault(pk, []).append((period, fact, value))

    restated = False
    points: list[HistoryPoint] = []
    for pk, candidates in by_period.items():
        distinct = {round(v, 6) for _, _, v in candidates}
        ordered = sorted(candidates, key=lambda c: _confidence_rank(c[1]), reverse=True)
        if len(distinct) > 1:
            restated = True
        for index, (period, fact, value) in enumerate(ordered):
            if index > 0 and round(value, 6) in {round(ordered[0][2], 6)}:
                # An exact duplicate of the representative (e.g. the same
                # figure stated in a table AND in the narrative) is not a
                # restatement and must not raise a false conflict.
                continue
            points.append(
                HistoryPoint(
                    period=period,
                    value=value,
                    value_text=_fact_get(fact, "value_text") or _fact_get(fact, "value"),
                    source_url=_fact_get(fact, "source_url"),
                    page_number=_fact_get(fact, "page_number"),
                    table_location=_fact_get(fact, "table_location"),
                    confidence=(
                        _fact_get(fact, "confidence")
                        if isinstance(_fact_get(fact, "confidence"), str)
                        else None
                    ),
                    superseded=index > 0,
                )
            )

    active = sorted(
        (p for p in points if not p.superseded), key=lambda p: p.period.sort_key
    )
    if not active:
        return None
    # Newest N periods win; older ones are dropped, never averaged away.
    active = active[-cap:]
    kept_keys = {p.period.key for p in active}
    series.points = sorted(
        [p for p in points if p.period.key in kept_keys],
        key=lambda p: (p.period.sort_key, p.superseded),
    )

    _assess_completeness(series, active)
    _assess_comparability(series, active, restated=restated)
    if series.is_comparable:
        series.changes = _derive_changes(series, active)
    return series


def _assess_completeness(
    series: FinancialHistorySeries, active: "list[HistoryPoint]"
) -> None:
    """A gap between the first and last period makes the series PARTIAL.

    The gap is named rather than filled. Interpolating a missing year would be
    fabrication, and quietly presenting FY2021 next to FY2023 as if they were
    consecutive is how a reader misreads a trend.
    """
    if series.period_type != PERIOD_TYPE_ANNUAL or len(active) < 2:
        series.completeness = (
            COMPLETENESS_COMPLETE if len(active) >= 1 else COMPLETENESS_PARTIAL
        )
        return
    years = [p.period.year for p in active if p.period.year is not None]
    if not years:
        series.completeness = COMPLETENESS_PARTIAL
        return
    expected = set(range(min(years), max(years) + 1))
    missing = sorted(expected - set(years))
    series.missing_periods = [f"FY{y}" for y in missing]
    series.completeness = COMPLETENESS_COMPLETE if not missing else COMPLETENESS_PARTIAL


def _assess_comparability(
    series: FinancialHistorySeries,
    active: "list[HistoryPoint]",
    *,
    restated: bool,
) -> None:
    """Decide whether a like-for-like comparison is legitimate.

    Currency/unit/scale mismatch cannot actually occur within a series (they
    are part of the grouping key), so a mismatch shows up as TWO series for the
    same metric — which is the honest outcome and is detected here so the
    caller can say "no comparable trend" instead of comparing across them.
    """
    reasons: list[str] = []
    if len(active) < MIN_PERIODS_FOR_TREND:
        reasons.append(REASON_SINGLE_PERIOD)
    if series.scope.is_unknown:
        reasons.append(REASON_SCOPE_UNKNOWN)
    if restated:
        # A restatement does not make a series unusable, but it must be
        # declared: the reader is comparing against a figure the issuer itself
        # published more than one version of.
        reasons.append(REASON_RESTATED_PERIOD)
    blocking = [r for r in reasons if r != REASON_RESTATED_PERIOD]
    series.comparability_reasons = reasons
    series.comparability = COMPARABLE if not blocking else NOT_COMPARABLE


def _derive_changes(
    series: FinancialHistorySeries, active: "list[HistoryPoint]"
) -> list[DerivedChange]:
    """Deterministic descriptive change between the FIRST and LAST period, and
    the most recent period-over-period step. Nothing else, and never a forecast.
    """
    if len(active) < 2:
        return []
    out: list[DerivedChange] = []
    pairs = [(active[0], active[-1])]
    if len(active) > 2:
        pairs.append((active[-2], active[-1]))

    seen: set[tuple[str, str]] = set()
    for start, end in pairs:
        pk = (start.period.key or "", end.period.key or "")
        if pk in seen:
            continue
        seen.add(pk)
        if series.metric in _PERCENT_METRICS:
            out.append(
                DerivedChange(
                    calculation=CALC_PERCENTAGE_POINT_CHANGE,
                    from_period=start.period_label,
                    to_period=end.period_label,
                    from_value=start.value,
                    to_value=end.value,
                    value=round(end.value - start.value, 4),
                    unit="pp",
                    formula="to_value - from_value (percentage points)",
                )
            )
            continue
        out.append(
            DerivedChange(
                calculation=CALC_ABSOLUTE_CHANGE,
                from_period=start.period_label,
                to_period=end.period_label,
                from_value=start.value,
                to_value=end.value,
                value=round(end.value - start.value, 4),
                unit=series.unit_label(),
                formula="to_value - from_value",
            )
        )
        # A percentage change needs a non-zero denominator; a zero base is not
        # "infinite growth", it is an undefined ratio and is simply not emitted.
        if start.value != 0:
            out.append(
                DerivedChange(
                    calculation=CALC_PERCENT_CHANGE,
                    from_period=start.period_label,
                    to_period=end.period_label,
                    from_value=start.value,
                    to_value=end.value,
                    value=round((end.value - start.value) / abs(start.value) * 100.0, 2),
                    unit="%",
                    formula="(to_value - from_value) / abs(from_value) * 100",
                )
            )
    return out


def history_evidence_lines(
    history: FinancialHistory, *, max_lines: int = 12
) -> list[str]:
    """The compact, token-bounded trend slice the LLM council reads.

    Group series first (a Group trend is what a reader asks for), then segment
    series. Each line states its own scope and unit, so a segment trend can
    never be read as the Group's. A not-comparable series is included with its
    reason rather than omitted — "we have the numbers but they are not
    comparable" is a different, and more useful, statement than silence.
    """
    lines: list[str] = []
    ordered = sorted(
        history.series,
        key=lambda s: (
            0 if s.scope.is_group else 1,
            _METRIC_ORDER.get(s.metric, 99),
            s.scope_label,
        ),
    )
    for series in ordered:
        if len(lines) >= max_lines:
            break
        line = series.compact_line()
        if not series.is_comparable:
            reasons = ", ".join(series.comparability_reasons) or NOT_COMPARABLE
            line += f" (not comparable: {reasons})"
        elif series.completeness == COMPLETENESS_PARTIAL and series.missing_periods:
            line += f" (missing: {', '.join(series.missing_periods)})"
        lines.append(line)
    return lines


__all__ = [
    "CALC_ABSOLUTE_CHANGE",
    "CALC_PERCENTAGE_POINT_CHANGE",
    "CALC_PERCENT_CHANGE",
    "COMPARABLE",
    "COMPLETENESS_COMPLETE",
    "COMPLETENESS_PARTIAL",
    "DEFAULT_MAX_PERIODS",
    "HISTORY_METRICS",
    "MIN_PERIODS_FOR_TREND",
    "NOT_COMPARABLE",
    "REASON_CURRENCY_MISMATCH",
    "REASON_PERIOD_UNKNOWN",
    "REASON_RESTATED_PERIOD",
    "REASON_SCOPE_UNKNOWN",
    "REASON_SINGLE_PERIOD",
    "REASON_UNIT_MISMATCH",
    "DerivedChange",
    "FinancialHistory",
    "FinancialHistorySeries",
    "HistoryPoint",
    "build_financial_history",
    "history_evidence_lines",
]
