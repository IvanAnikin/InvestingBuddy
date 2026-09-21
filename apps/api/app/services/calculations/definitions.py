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


# ── What a value MEANS ───────────────────────────────────────────────────── #
#
# V3.18.1. A definition used to say how a metric is computed and nothing about how it
# is read, so reading it was left to whoever saw the number. A live report divided net
# income by operating cash flow — a ratio this platform computes nowhere — watched it
# fall from 113% to 88%, and called that "weakening cash conversion". On the
# conventional definition (operating cash flow / net income) the same two periods show
# conversion IMPROVING. The arithmetic was right and the sentence was backwards,
# because the direction of "good" was never written down.
#
# So it is written down, per metric, and it travels with every value.

#: A higher value is the favourable reading.
HIGHER_IS_BETTER = "higher_is_better"
#: A lower value is the favourable reading.
LOWER_IS_BETTER = "lower_is_better"
#: Neither direction is favourable in general; the interpretation rule says what the
#: value indicates and a reader must not attach "improving"/"deteriorating" to it.
CONTEXTUAL = "contextual"

DIRECTIONALITIES: frozenset[str] = frozenset(
    {HIGHER_IS_BETTER, LOWER_IS_BETTER, CONTEXTUAL}
)


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
    #: Input role on top of the ratio, and the one beneath it. ``None`` for a metric
    #: that is not a quotient (a difference, a growth rate).
    numerator: str | None = None
    denominator: str | None = None
    #: Which way is favourable. REQUIRED — see the block above ``InputSpec``.
    directionality: str = ""
    #: How the value is to be read, in one or two sentences a model is handed verbatim.
    #: REQUIRED. "No model may invent the meaning of a ratio after seeing it."
    interpretation: str = ""

    def __post_init__(self) -> None:
        if self.directionality not in DIRECTIONALITIES:
            raise ValueError(
                f"{self.key}: directionality must be one of {sorted(DIRECTIONALITIES)}. "
                "A metric whose favourable direction is not declared will have one "
                "invented for it by whoever reads the number."
            )
        if len(self.interpretation.strip()) < 20:
            raise ValueError(
                f"{self.key}: an interpretation rule is required. It is the sentence a "
                "model receives with the value, in place of its own guess."
            )
        roles = {spec.role for spec in self.inputs}
        for name, role in (("numerator", self.numerator), ("denominator", self.denominator)):
            if role is not None and role not in roles:
                raise ValueError(f"{self.key}: {name} {role!r} is not one of its input roles.")

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
            "numerator": self.numerator,
            "denominator": self.denominator,
            "directionality": self.directionality,
            "interpretation": self.interpretation,
        }

    def semantics(self) -> dict[str, object]:
        """What a reader — human or model — must be told alongside a value."""
        return {
            "metric_id": self.key,
            "label": self.label,
            "definition": self.formula,
            "numerator": self.numerator,
            "denominator": self.denominator,
            "unit": self.result_unit,
            "period_rule": self.period_rule,
            "scope_rule": self.scope_rule,
            "directionality": self.directionality,
            "interpretation": self.interpretation,
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
    numerator="gross_profit",
    denominator="revenue",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Share of revenue left after cost of sales. Higher means more revenue is "
        "retained before operating costs; compare only with the same issuer's other "
        "periods or with peers that define cost of sales the same way."
    ),
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
    numerator="operating_profit",
    denominator="revenue",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Share of revenue left after operating costs. Higher is more profitable per "
        "unit of revenue; for a commodity producer it moves with the realised price, so "
        "a change is not by itself evidence of cost control."
    ),
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
    numerator="net_income",
    denominator="revenue",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Share of revenue left as net income. Higher is more profitable; it includes "
        "tax, interest and one-off items, so a change may not reflect operations."
    ),
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
    directionality=LOWER_IS_BETTER,
    interpretation=(
        "Debt not covered by cash. Lower means less financial obligation; a negative "
        "value is a net cash position, which is a real answer and not an error."
    ),
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
    numerator="net_debt",
    denominator="ebitda",
    directionality=LOWER_IS_BETTER,
    interpretation=(
        "Years of EBITDA needed to repay net debt. Lower means less leverage; a "
        "negative value means net cash."
    ),
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
    numerator="free_cash_flow",
    denominator="net_income",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Free cash flow generated per unit of accounting profit. Higher means more of "
        "reported profit arrives as cash after capital spending; a FALL means "
        "conversion weakened, a RISE means it strengthened."
    ),
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
    numerator="capital_expenditure",
    denominator="revenue",
    directionality=CONTEXTUAL,
    interpretation=(
        "Capital spending per unit of revenue. It measures capital intensity, not "
        "quality: a rise may be growth investment or rising maintenance cost, and the "
        "figure alone does not say which."
    ),
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
    numerator="net_income",
    denominator="shareholders_equity",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Net income per unit of book equity. Higher is a better return on equity "
        "capital, but it rises mechanically with leverage and with buybacks or "
        "dividends that shrink equity."
    ),
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
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Compound annual revenue growth between two periods of one type. Higher is "
        "faster growth; for a commodity producer it reflects price as well as volume."
    ),
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
    numerator="segment_revenue",
    denominator="group_revenue",
    directionality=CONTEXTUAL,
    interpretation=(
        "A segment's share of Group revenue. It describes exposure and concentration; "
        "neither a higher nor a lower share is favourable in itself."
    ),
)

CASH_CONVERSION = CalculationDefinition(
    key="cash_conversion",
    label="Cash conversion (operating cash flow / net income)",
    version=1,
    formula="operating_cash_flow / net_income * 100",
    inputs=(
        InputSpec("operating_cash_flow", ("operating_cash_flow",), MONEY),
        InputSpec("net_income", ("net_income",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "operating_cash_flow", "net_income"),
    non_zero_roles=("net_income",),
    positive_roles=("net_income",),
    numerator="operating_cash_flow",
    denominator="net_income",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Operating cash generated per unit of net income. Above 100% means cash flow "
        "exceeded accounting profit. A RISE is stronger conversion and a FALL is weaker. "
        "The inverse ratio (net income / operating cash flow) moves the OPPOSITE way and "
        "must never be described as cash conversion."
    ),
    notes=(
        "Refused when net income is not positive: a ratio over a loss has no "
        "conversion reading, and printing one invites exactly the inverted sentence "
        "this definition exists to prevent."
    ),
    tags=("cash",),
)

CAPEX_TO_OCF = CalculationDefinition(
    key="capex_to_ocf",
    label="Capital expenditure / operating cash flow",
    version=1,
    formula="capital_expenditure / operating_cash_flow * 100",
    inputs=(
        InputSpec("capital_expenditure", ("capital_expenditure", "capex"), MONEY),
        InputSpec("operating_cash_flow", ("operating_cash_flow",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "capital_expenditure", "operating_cash_flow"),
    non_zero_roles=("operating_cash_flow",),
    positive_roles=("operating_cash_flow",),
    numerator="capital_expenditure",
    denominator="operating_cash_flow",
    directionality=LOWER_IS_BETTER,
    interpretation=(
        "Share of operating cash flow absorbed by capital spending. Lower leaves more "
        "cash for debt service and distributions; above 100% means capital spending "
        "was not funded from operations in the period."
    ),
    tags=("cash", "capital"),
)

FCF_MARGIN = CalculationDefinition(
    key="fcf_margin",
    label="Free cash flow margin",
    version=1,
    formula="free_cash_flow / revenue * 100",
    inputs=(
        InputSpec("free_cash_flow", ("free_cash_flow",), MONEY),
        InputSpec("revenue", ("revenue",), MONEY),
    ),
    result_unit=UNIT_PERCENT,
    compute=lambda i: _percent(i, "free_cash_flow", "revenue"),
    non_zero_roles=("revenue",),
    numerator="free_cash_flow",
    denominator="revenue",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Free cash flow per unit of revenue. Higher means more revenue arrives as cash "
        "after capital spending."
    ),
    tags=("cash", "margin"),
)

DIVIDEND_COVER = CalculationDefinition(
    key="dividend_cover_by_fcf",
    label="Dividend cover by free cash flow",
    version=1,
    formula="free_cash_flow / dividends_paid",
    inputs=(
        InputSpec("free_cash_flow", ("free_cash_flow",), MONEY),
        InputSpec("dividends_paid", ("dividends_paid",), MONEY),
    ),
    result_unit=UNIT_RATIO,
    compute=lambda i: _ratio(i, "free_cash_flow", "dividends_paid"),
    non_zero_roles=("dividends_paid",),
    positive_roles=("dividends_paid",),
    numerator="free_cash_flow",
    denominator="dividends_paid",
    directionality=HIGHER_IS_BETTER,
    interpretation=(
        "Times the cash dividend was covered by free cash flow. Below 1.0x means the "
        "dividend exceeded free cash flow and was funded from cash or borrowing."
    ),
    tags=("cash", "capital"),
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
        CASH_CONVERSION,
        CAPEX_TO_OCF,
        FCF_MARGIN,
        DIVIDEND_COVER,
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
    "CONTEXTUAL",
    "DIRECTIONALITIES",
    "HIGHER_IS_BETTER",
    "LOWER_IS_BETTER",
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
