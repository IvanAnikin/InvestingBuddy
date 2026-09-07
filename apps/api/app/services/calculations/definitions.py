"""Declarative calculation definitions — V3.3 Slice 3.3.

A definition is **data**: what it needs, what relationship its inputs must stand in,
what it produces, and a pure function. Nothing here reaches a database or a model.

WHY THE RULES ARE DECLARED RATHER THAN CODED PER METRIC
=======================================================
"Operating margin needs two figures from the same period, the same scope and the same
currency" is true of eight of the nine metrics below. Written out in each one it would
be eight opportunities to forget the scope check — and forgetting it once produces a
Specialist Watchmakers margin presented as a Group margin, which is the failure this
repository has already had to correct in the extraction layer.

So the relationship is a field, the engine enforces it, and a metric's own function
receives inputs it is already allowed to combine.

THE ONE CROSS-SCOPE DEFINITION
==============================
``segment_mix`` deliberately requires **two different scopes**: a segment numerator and
a Group denominator. It is the only legitimate cross-scope calculation, and declaring
it explicitly is what lets every other definition refuse a scope mismatch outright
rather than having a general "sometimes scopes may differ" escape.

VERSIONS ARE PART OF THE RECORD
===============================
A definition carries a ``version``. When a formula changes, the version changes and old
records stay interpretable — the same reason ``CURRENT_EXTRACTION_PIPELINE_VERSION``
exists. A silently redefined metric makes every historical value a different metric
wearing the same name.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal

from app.services.calculations.quantities import (
    UNIT_CURRENCY_AMOUNT,
    UNIT_PERCENT,
    UNIT_RATIO,
    Quantity,
)

# ── Input relationships ──────────────────────────────────────────────────── #

#: Every input must carry the same period key. The default, and the only one that is
#: safe without saying more.
PERIOD_SAME = "same_period"
#: Exactly two inputs, ``start`` and ``end``, of the same period TYPE and different
#: years. Growth over time is the only reason to want two periods.
PERIOD_TWO_OF_ONE_TYPE = "two_periods_of_one_type"

PERIOD_RULES: frozenset[str] = frozenset({PERIOD_SAME, PERIOD_TWO_OF_ONE_TYPE})

#: Every input must carry the same scope key, and an unknown scope is refused —
#: unknown is not Group.
SCOPE_SAME = "same_scope"
#: A segment numerator over a Group denominator, and nothing else.
SCOPE_SEGMENT_OVER_GROUP = "segment_over_group"

SCOPE_RULES: frozenset[str] = frozenset({SCOPE_SAME, SCOPE_SEGMENT_OVER_GROUP})

#: Money inputs must agree on currency. Refused rather than converted: an FX rate this
#: platform did not source is an invented number (CLAUDE.md rule 6).
CURRENCY_SAME = "same_currency"
CURRENCY_NOT_APPLICABLE = "not_applicable"

CURRENCY_RULES: frozenset[str] = frozenset({CURRENCY_SAME, CURRENCY_NOT_APPLICABLE})


@dataclass(frozen=True)
class InputSpec:
    """One named input a definition requires."""

    role: str
    #: The fact labels that can fill this role, in preference order. Matching is exact
    #: on the stored label — no fuzzy matching, because "revenue" and "revenues" being
    #: the same thing is a decision for the extractor's vocabulary, not for arithmetic.
    labels: tuple[str, ...]
    unit: str
    description: str = ""


@dataclass(frozen=True)
class CalculationDefinition:
    """One metric: what it needs, how its inputs must relate, and how to compute it."""

    key: str
    label: str
    version: int
    formula: str
    inputs: tuple[InputSpec, ...]
    result_unit: str
    compute: Callable[[dict[str, Quantity]], Decimal]
    period_rule: str = PERIOD_SAME
    scope_rule: str = SCOPE_SAME
    currency_rule: str = CURRENCY_SAME
    #: Roles whose value must not be zero, because the formula divides by them.
    non_zero_roles: tuple[str, ...] = ()
    #: Roles whose value must be strictly positive — a growth base, for instance.
    positive_roles: tuple[str, ...] = ()
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)

    @property
    def input_roles(self) -> tuple[str, ...]:
        return tuple(spec.role for spec in self.inputs)

    def to_dict(self) -> dict[str, object]:
        return {
            "key": self.key,
            "label": self.label,
            "version": self.version,
            "formula": self.formula,
            "result_unit": self.result_unit,
            "period_rule": self.period_rule,
            "scope_rule": self.scope_rule,
            "currency_rule": self.currency_rule,
            "inputs": [
                {
                    "role": spec.role,
                    "labels": list(spec.labels),
                    "unit": spec.unit,
                    "description": spec.description,
                }
                for spec in self.inputs
            ],
            "non_zero_roles": list(self.non_zero_roles),
            "positive_roles": list(self.positive_roles),
            "notes": self.notes,
            "tags": list(self.tags),
        }


# ── The arithmetic, each function pure and each one trivially readable ────── #


def _ratio(inputs: dict[str, Quantity], numerator: str, denominator: str) -> Decimal:
    from app.services.calculations.quantities import common_magnitudes

    pair = common_magnitudes(inputs[numerator], inputs[denominator])
    if pair is None:  # pragma: no cover - the engine refuses this before computing
        raise ValueError("incombinable scales reached compute()")
    top, bottom = pair
    return top / bottom


def _percent(inputs: dict[str, Quantity], numerator: str, denominator: str) -> Decimal:
    return _ratio(inputs, numerator, denominator) * Decimal(100)


def _difference(inputs: dict[str, Quantity], left: str, right: str) -> Decimal:
    from app.services.calculations.quantities import common_magnitudes

    pair = common_magnitudes(inputs[left], inputs[right])
    if pair is None:  # pragma: no cover - refused before computing
        raise ValueError("incombinable scales reached compute()")
    return pair[0] - pair[1]


def _cagr(inputs: dict[str, Quantity]) -> Decimal:
    """Compound annual growth rate, as a percentage.

    Years apart come from the periods' own headline years, so a two-year gap in the
    documents is a two-year gap in the arithmetic. The engine has already established
    that both periods are the same type and that the base is positive.
    """
    from app.services.calculations.quantities import common_magnitudes

    start, end = inputs["start"], inputs["end"]
    pair = common_magnitudes(start, end)
    if pair is None:  # pragma: no cover - refused before computing
        raise ValueError("incombinable scales reached compute()")
    base, final = pair
    start_year = (start.period.year if start.period else None) or 0
    end_year = (end.period.year if end.period else None) or 0
    years = end_year - start_year
    if years <= 0:  # pragma: no cover - refused before computing
        raise ValueError("non-positive span reached compute()")
    growth = (final / base) ** (Decimal(1) / Decimal(years))
    return (growth - Decimal(1)) * Decimal(100)


# ── The definitions ──────────────────────────────────────────────────────── #

MONEY = UNIT_CURRENCY_AMOUNT

GROSS_MARGIN = CalculationDefinition(
    key="gross_margin",
    label="Gross margin",
    version=1,
    formula="gross_profit / revenue * 100",
    inputs=(
        InputSpec("gross_profit", ("gross_profit",), MONEY),
        InputSpec("revenue", ("revenue",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "gross_profit", "revenue"),
    non_zero_roles=("revenue",),
    tags=("margin",),
)

OPERATING_MARGIN = CalculationDefinition(
    key="operating_margin",
    label="Operating margin",
    version=1,
    formula="operating_profit / revenue * 100",
    inputs=(
        InputSpec("operating_profit", ("operating_profit", "ebit"), MONEY),
        InputSpec("revenue", ("revenue",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "operating_profit", "revenue"),
    non_zero_roles=("revenue",),
    tags=("margin",),
)

NET_MARGIN = CalculationDefinition(
    key="net_margin",
    label="Net margin",
    version=1,
    formula="net_income / revenue * 100",
    inputs=(
        InputSpec("net_income", ("net_income",), MONEY),
        InputSpec("revenue", ("revenue",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "net_income", "revenue"),
    non_zero_roles=("revenue",),
    tags=("margin",),
)

NET_DEBT = CalculationDefinition(
    key="net_debt",
    label="Net debt",
    version=1,
    formula="total_debt - cash_and_equivalents",
    inputs=(
        InputSpec("total_debt", ("total_debt",), MONEY),
        InputSpec("cash_and_equivalents", ("cash_and_equivalents", "cash"), MONEY),
    ),
    result_unit=MONEY,
    compute=lambda i: _difference(i, "total_debt", "cash_and_equivalents"),
    notes=(
        "A negative result is a net cash position and is a real answer, not an error."
    ),
    tags=("leverage",),
)

LEVERAGE = CalculationDefinition(
    key="net_debt_to_ebitda",
    label="Net debt / EBITDA",
    version=1,
    formula="net_debt / ebitda",
    inputs=(
        InputSpec("net_debt", ("net_debt",), MONEY),
        InputSpec("ebitda", ("ebitda",), MONEY),
    ),
    result_unit=UNIT_RATIO,
    compute=lambda i: _ratio(i, "net_debt", "ebitda"),
    non_zero_roles=("ebitda",),
    notes=(
        "Takes net debt as an INPUT rather than recomputing it, so a run that already "
        "refused net_debt cannot silently produce a leverage ratio."
    ),
    tags=("leverage",),
)

FCF_CONVERSION = CalculationDefinition(
    key="fcf_conversion",
    label="Free cash flow conversion",
    version=1,
    formula="free_cash_flow / net_income * 100",
    inputs=(
        InputSpec("free_cash_flow", ("free_cash_flow",), MONEY),
        InputSpec("net_income", ("net_income",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "free_cash_flow", "net_income"),
    non_zero_roles=("net_income",),
    tags=("cash",),
)

CAPEX_INTENSITY = CalculationDefinition(
    key="capex_intensity",
    label="Capex intensity",
    version=1,
    formula="capital_expenditure / revenue * 100",
    inputs=(
        InputSpec("capital_expenditure", ("capital_expenditure", "capex"), MONEY),
        InputSpec("revenue", ("revenue",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "capital_expenditure", "revenue"),
    non_zero_roles=("revenue",),
    tags=("capital",),
)

ROE = CalculationDefinition(
    key="return_on_equity",
    label="Return on equity",
    version=1,
    formula="net_income / shareholders_equity * 100",
    inputs=(
        InputSpec("net_income", ("net_income",), MONEY),
        InputSpec("shareholders_equity", ("shareholders_equity",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "net_income", "shareholders_equity"),
    non_zero_roles=("shareholders_equity",),
    tags=("returns",),
)

REVENUE_CAGR = CalculationDefinition(
    key="revenue_cagr",
    label="Revenue CAGR",
    version=1,
    formula="((end / start) ** (1 / years)) - 1, as a percentage",
    inputs=(
        InputSpec("start", ("revenue",), MONEY, "the earlier period"),
        InputSpec("end", ("revenue",), MONEY, "the later period"),
    ),
    result_unit=UNIT_PERCENT,
    compute=_cagr,
    period_rule=PERIOD_TWO_OF_ONE_TYPE,
    positive_roles=("start",),
    notes=(
        "A non-positive base has no compound growth rate — the result would be a "
        "complex number or a division by zero — so it is refused rather than clamped."
    ),
    tags=("growth",),
)

SEGMENT_MIX = CalculationDefinition(
    key="segment_revenue_mix",
    label="Segment share of Group revenue",
    version=1,
    formula="segment_revenue / group_revenue * 100",
    inputs=(
        InputSpec("segment_revenue", ("revenue",), MONEY, "the segment figure"),
        InputSpec("group_revenue", ("revenue",), MONEY, "the consolidated figure"),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "segment_revenue", "group_revenue"),
    scope_rule=SCOPE_SEGMENT_OVER_GROUP,
    non_zero_roles=("group_revenue",),
    notes=(
        "The ONLY cross-scope definition, declared so every other one can refuse a "
        "scope mismatch outright instead of having a general escape."
    ),
    tags=("segment",),
)


DEFINITIONS: dict[str, CalculationDefinition] = {
    definition.key: definition
    for definition in (
        GROSS_MARGIN,
        OPERATING_MARGIN,
        NET_MARGIN,
        NET_DEBT,
        LEVERAGE,
        FCF_CONVERSION,
        CAPEX_INTENSITY,
        ROE,
        REVENUE_CAGR,
        SEGMENT_MIX,
    )
}


def definition_for(key: str | None) -> CalculationDefinition:
    """The definition, or raise. The set is closed for the same reason tools are."""
    found = DEFINITIONS.get((key or "").strip())
    if found is None:
        raise ValueError(
            f"{key!r} is not a known calculation. The set is closed; adding one is a "
            f"change to definitions.py. Known: {', '.join(sorted(DEFINITIONS))}."
        )
    return found


__all__ = [
    "CURRENCY_NOT_APPLICABLE",
    "CURRENCY_RULES",
    "CURRENCY_SAME",
    "DEFINITIONS",
    "PERIOD_RULES",
    "PERIOD_SAME",
    "PERIOD_TWO_OF_ONE_TYPE",
    "SCOPE_RULES",
    "SCOPE_SAME",
    "SCOPE_SEGMENT_OVER_GROUP",
    "CalculationDefinition",
    "InputSpec",
    "definition_for",
]
