"""The calculation engine — V3.3 Slice 3.3.

Deterministic arithmetic in deterministic code. No model is ever asked to divide two
numbers, because a model that divides two numbers cannot be asked *which* two.

WHAT THE ENGINE IS FOR
======================
It is not for computing. Computing is one line per metric. The engine exists to
**refuse**, and the acceptance strategy states the demonstration in those terms: *a
calculation with incompatible periods or scopes is refused, not computed.*

A refusal is a **result**, recorded with a reason from a closed vocabulary — not an
exception a caller can swallow. That is the same shape ``ClaimOutcome`` and
``ToolCallResult`` use, and for the same reason: an aggregate of refusals per metric
tells a reader which figures the platform *cannot yet* produce and why, which is a
research gap rather than a bug report.

THE CHECKS, IN THE ORDER THEY MATTER
====================================
1. **Every required input present.** A metric computed from a subset of its inputs is a
   different metric.
2. **Period.** ``ReportingPeriod.comparable_with`` already refuses cross-type
   comparison and refuses an unknown period against anything, including another
   unknown. It is called, not re-implemented.
3. **Scope.** Same scope key, and an unknown scope is refused — unknown is not Group.
   The one exception is declared per definition, not inferred.
4. **Currency.** Refused on a mismatch, never converted: an FX rate this platform did
   not source is an invented number (CLAUDE.md rule 6).
5. **Scale.** See ``quantities.scales_are_combinable`` — one known and one unknown is
   the pair with no safe reading.
6. **Domain.** A zero denominator and a non-positive growth base are refused rather
   than clamped, because a clamped input produces a number that looks like an answer.

Only after all six does anything multiply.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal, DivisionByZero, InvalidOperation
from typing import Any

from app.services.calculations.definitions import (
    CURRENCY_SAME,
    PERIOD_SAME,
    PERIOD_TWO_OF_ONE_TYPE,
    SCOPE_SAME,
    SCOPE_SEGMENT_OVER_GROUP,
    CalculationDefinition,
)
from app.services.calculations.quantities import (
    SCALED_UNITS,
    Quantity,
    scales_are_combinable,
)
from app.services.sources.fact_scope import SCOPE_TYPE_GROUP, SCOPE_TYPE_SEGMENT

# ── Refusal reasons ──────────────────────────────────────────────────────── #

REFUSED_MISSING_INPUT = "missing_input"
REFUSED_VALUE_MISSING = "value_missing"
REFUSED_UNIT_MISMATCH = "unit_mismatch"
REFUSED_PERIOD_UNKNOWN = "period_unknown"
REFUSED_PERIOD_MISMATCH = "period_mismatch"
REFUSED_PERIOD_TYPE_MISMATCH = "period_type_mismatch"
REFUSED_PERIOD_SPAN_INVALID = "period_span_invalid"
REFUSED_SCOPE_UNKNOWN = "scope_unknown"
REFUSED_SCOPE_MISMATCH = "scope_mismatch"
REFUSED_SCOPE_NOT_SEGMENT = "scope_not_segment"
REFUSED_SCOPE_NOT_GROUP = "scope_not_group"
REFUSED_CURRENCY_MISMATCH = "currency_mismatch"
REFUSED_SCALE_INCOMBINABLE = "scale_incombinable"
REFUSED_DIVIDE_BY_ZERO = "divide_by_zero"
REFUSED_NON_POSITIVE_BASE = "non_positive_base"
REFUSED_ARITHMETIC_ERROR = "arithmetic_error"

REFUSAL_REASONS: frozenset[str] = frozenset(
    {
        REFUSED_MISSING_INPUT,
        REFUSED_VALUE_MISSING,
        REFUSED_UNIT_MISMATCH,
        REFUSED_PERIOD_UNKNOWN,
        REFUSED_PERIOD_MISMATCH,
        REFUSED_PERIOD_TYPE_MISMATCH,
        REFUSED_PERIOD_SPAN_INVALID,
        REFUSED_SCOPE_UNKNOWN,
        REFUSED_SCOPE_MISMATCH,
        REFUSED_SCOPE_NOT_SEGMENT,
        REFUSED_SCOPE_NOT_GROUP,
        REFUSED_CURRENCY_MISMATCH,
        REFUSED_SCALE_INCOMBINABLE,
        REFUSED_DIVIDE_BY_ZERO,
        REFUSED_NON_POSITIVE_BASE,
        REFUSED_ARITHMETIC_ERROR,
    }
)

STATUS_COMPUTED = "computed"
STATUS_REFUSED = "refused"


@dataclass
class CalculationOutcome:
    """A computed value, or a refusal with its reason. Never an exception.

    Everything needed to reproduce or to attribute: the definition and its version, the
    inputs by fact id with their own periods, scopes, units and scales, and the
    resulting unit. A result whose inputs cannot be named is a number nobody can check.
    """

    definition_key: str
    definition_version: int
    status: str
    value: Decimal | None = None
    result_unit: str | None = None
    result_currency: str | None = None
    #: For a money result, the scale the value is expressed in.
    result_scale: str | None = None
    period_key: str | None = None
    period_type: str | None = None
    scope_type: str | None = None
    scope_key: str | None = None
    refusal_reason: str | None = None
    detail: str = ""
    inputs: dict[str, dict[str, Any]] = field(default_factory=dict)
    input_fact_ids: list[str] = field(default_factory=list)
    formula: str = ""
    computed_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )
    calculation_id: uuid.UUID | None = None

    @property
    def computed(self) -> bool:
        return self.status == STATUS_COMPUTED

    @property
    def refused(self) -> bool:
        return self.status == STATUS_REFUSED

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition_key": self.definition_key,
            "definition_version": self.definition_version,
            "status": self.status,
            "value": float(self.value) if self.value is not None else None,
            "result_unit": self.result_unit,
            "result_currency": self.result_currency,
            "result_scale": self.result_scale,
            "period_key": self.period_key,
            "period_type": self.period_type,
            "scope_type": self.scope_type,
            "scope_key": self.scope_key,
            "refusal_reason": self.refusal_reason,
            "detail": self.detail,
            "inputs": dict(self.inputs),
            "input_fact_ids": list(self.input_fact_ids),
            "formula": self.formula,
            "calculation_id": (
                str(self.calculation_id) if self.calculation_id else None
            ),
        }


def _refuse(
    definition: CalculationDefinition,
    reason: str,
    detail: str,
    inputs: dict[str, Quantity],
) -> CalculationOutcome:
    return CalculationOutcome(
        definition_key=definition.key,
        definition_version=definition.version,
        status=STATUS_REFUSED,
        refusal_reason=reason,
        detail=detail,
        inputs={role: q.to_dict() for role, q in inputs.items()},
        input_fact_ids=[q.fact_id for q in inputs.values() if q.fact_id],
        formula=definition.formula,
    )


def calculate(
    definition: CalculationDefinition, inputs: dict[str, Quantity]
) -> CalculationOutcome:
    """Compute one metric, or refuse it with a reason. Never raises for bad inputs.

    Bad inputs are the normal case here — the platform is assembling arithmetic out of
    figures parsed from PDFs — so a refusal is a first-class result and an exception is
    reserved for a programming error.
    """
    supplied = {role: q for role, q in inputs.items() if role in definition.input_roles}

    missing = [role for role in definition.input_roles if role not in supplied]
    if missing:
        return _refuse(
            definition,
            REFUSED_MISSING_INPUT,
            f"missing input(s): {', '.join(missing)}. A metric computed from a subset "
            "of its inputs is a different metric.",
            supplied,
        )

    for spec in definition.inputs:
        quantity = supplied[spec.role]
        if quantity.value is None:  # pragma: no cover - Quantity requires a value
            return _refuse(
                definition, REFUSED_VALUE_MISSING, f"{spec.role} has no value", supplied
            )
        if quantity.unit != spec.unit:
            return _refuse(
                definition,
                REFUSED_UNIT_MISMATCH,
                f"{spec.role} is {quantity.unit!r}; {definition.key} needs "
                f"{spec.unit!r}",
                supplied,
            )

    period_refusal = _check_periods(definition, supplied)
    if period_refusal is not None:
        return period_refusal

    scope_refusal = _check_scopes(definition, supplied)
    if scope_refusal is not None:
        return scope_refusal

    currency_refusal = _check_currency(definition, supplied)
    if currency_refusal is not None:
        return currency_refusal

    scale_refusal = _check_scales(definition, supplied)
    if scale_refusal is not None:
        return scale_refusal

    domain_refusal = _check_domain(definition, supplied)
    if domain_refusal is not None:
        return domain_refusal

    try:
        value = definition.compute(supplied)
    except (DivisionByZero, ZeroDivisionError):
        return _refuse(
            definition, REFUSED_DIVIDE_BY_ZERO, "the formula divided by zero", supplied
        )
    except (InvalidOperation, ArithmeticError, ValueError) as exc:
        # Recorded rather than raised: an arithmetic surprise on real parsed figures is
        # information about the figures, not a crash to propagate.
        return _refuse(
            definition,
            REFUSED_ARITHMETIC_ERROR,
            f"{type(exc).__name__}: {exc}",
            supplied,
        )

    return _computed(definition, supplied, value)


def _computed(
    definition: CalculationDefinition,
    inputs: dict[str, Quantity],
    value: Decimal,
) -> CalculationOutcome:
    reference = _reference_input(definition, inputs)
    money = definition.result_unit in SCALED_UNITS
    return CalculationOutcome(
        definition_key=definition.key,
        definition_version=definition.version,
        status=STATUS_COMPUTED,
        value=value,
        result_unit=definition.result_unit,
        # A ratio or a percentage has no currency, and stamping one on it would invite a
        # reader to think it had been converted from something.
        result_currency=reference.currency if money else None,
        # A money result carries the inputs' SHARED scale when they had one, because
        # that is the magnitude the documents printed and the one a reader recognises.
        # When the scales differed, the value is in base units and the scale is NULL,
        # which means base rather than unknown.
        result_scale=(_shared_scale(inputs) if money else None),
        period_key=(
            inputs["end"].period.key
            if definition.period_rule == PERIOD_TWO_OF_ONE_TYPE and inputs["end"].period
            else (reference.period.key if reference.period else None)
        ),
        period_type=reference.period.period_type if reference.period else None,
        scope_type=_result_scope(definition, inputs).scope_type
        if _result_scope(definition, inputs)
        else None,
        scope_key=_result_scope(definition, inputs).scope_key
        if _result_scope(definition, inputs)
        else None,
        inputs={role: q.to_dict() for role, q in inputs.items()},
        input_fact_ids=[q.fact_id for q in inputs.values() if q.fact_id],
        formula=definition.formula,
    )


def _shared_scale(inputs: dict[str, Quantity]) -> str | None:
    """The scale every money input shared, or ``None`` when they did not share one.

    ``None`` here means the value is in base units, which is a different statement from
    a fact's ``scale`` of NULL meaning "the document never said". The distinction only
    matters for a money result, and the column comment says which one it is.
    """
    scales = {q.scale for q in inputs.values() if q.is_money}
    if len(scales) == 1:
        return next(iter(scales))
    return None


def _reference_input(
    definition: CalculationDefinition, inputs: dict[str, Quantity]
) -> Quantity:
    """The input whose period/currency the result inherits — the first declared one."""
    return inputs[definition.input_roles[0]]


def _result_scope(definition: CalculationDefinition, inputs: dict[str, Quantity]):  # noqa: ANN202
    """Which scope a result belongs to.

    For a segment-over-Group mix it is the **segment**: the answer is a property of the
    segment, not of the Group, and attributing it to the Group would be the very
    confusion this definition exists to make safe.
    """
    if definition.scope_rule == SCOPE_SEGMENT_OVER_GROUP:
        return inputs["segment_revenue"].scope
    return _reference_input(definition, inputs).scope


# ── The six checks ───────────────────────────────────────────────────────── #


def _check_periods(
    definition: CalculationDefinition, inputs: dict[str, Quantity]
) -> CalculationOutcome | None:
    periods = {role: q.period for role, q in inputs.items()}
    for role, period in periods.items():
        if period is None or period.is_unknown:
            return _refuse(
                definition,
                REFUSED_PERIOD_UNKNOWN,
                f"{role} has no established period. An unknown period is comparable "
                "with nothing, not even another unknown.",
                inputs,
            )

    if definition.period_rule == PERIOD_SAME:
        keys = {p.key for p in periods.values() if p}
        if len(keys) > 1:
            return _refuse(
                definition,
                REFUSED_PERIOD_MISMATCH,
                f"inputs span {sorted(k for k in keys if k)}; this metric needs one "
                "period",
                inputs,
            )
        return None

    if definition.period_rule == PERIOD_TWO_OF_ONE_TYPE:
        start, end = inputs["start"].period, inputs["end"].period
        if start is None or end is None:  # pragma: no cover - checked above
            return _refuse(
                definition, REFUSED_PERIOD_UNKNOWN, "start or end has no period", inputs
            )
        if not start.comparable_with(end):
            # Delegated, not re-implemented: `comparable_with` is the module that
            # already knows FY2025 and H1 2026 are not the same kind of span.
            return _refuse(
                definition,
                REFUSED_PERIOD_TYPE_MISMATCH,
                f"{start.label()} and {end.label()} do not measure the same kind of "
                "span, so there is no growth rate between them",
                inputs,
            )
        span = (end.year or 0) - (start.year or 0)
        if span <= 0:
            return _refuse(
                definition,
                REFUSED_PERIOD_SPAN_INVALID,
                f"end period {end.label()} is not later than start {start.label()}",
                inputs,
            )
        return None

    return None  # pragma: no cover - the rule vocabulary is closed


def _check_scopes(
    definition: CalculationDefinition, inputs: dict[str, Quantity]
) -> CalculationOutcome | None:
    if definition.scope_rule == SCOPE_SAME:
        for role, quantity in inputs.items():
            scope = quantity.scope
            if scope is None or scope.is_unknown:
                # Unknown is NOT Group. The report layer's implicit convention is right
                # for a rendered report and wrong for arithmetic, where it would produce
                # a Group margin out of a figure nobody attributed.
                return _refuse(
                    definition,
                    REFUSED_SCOPE_UNKNOWN,
                    f"{role} has no established scope, and unknown is not Group",
                    inputs,
                )
        keys = {q.scope.scope_key for q in inputs.values() if q.scope}
        if len(keys) > 1:
            return _refuse(
                definition,
                REFUSED_SCOPE_MISMATCH,
                f"inputs span scopes {sorted(k for k in keys if k)}; a ratio across "
                "scopes is not a ratio of anything",
                inputs,
            )
        return None

    if definition.scope_rule == SCOPE_SEGMENT_OVER_GROUP:
        segment = inputs["segment_revenue"].scope
        group = inputs["group_revenue"].scope
        if segment is None or segment.scope_type != SCOPE_TYPE_SEGMENT:
            return _refuse(
                definition,
                REFUSED_SCOPE_NOT_SEGMENT,
                "the numerator must be a named segment figure",
                inputs,
            )
        if group is None or group.scope_type != SCOPE_TYPE_GROUP:
            return _refuse(
                definition,
                REFUSED_SCOPE_NOT_GROUP,
                "the denominator must be the consolidated Group figure",
                inputs,
            )
        return None

    return None  # pragma: no cover - the rule vocabulary is closed


def _check_currency(
    definition: CalculationDefinition, inputs: dict[str, Quantity]
) -> CalculationOutcome | None:
    if definition.currency_rule != CURRENCY_SAME:
        return None
    currencies = {q.currency for q in inputs.values() if q.is_money}
    if len(currencies) > 1:
        return _refuse(
            definition,
            REFUSED_CURRENCY_MISMATCH,
            f"inputs are in {sorted(str(c) for c in currencies)}; converting would "
            "require an exchange rate this platform did not source",
            inputs,
        )
    return None


def _check_scales(
    definition: CalculationDefinition, inputs: dict[str, Quantity]
) -> CalculationOutcome | None:
    money = [q for q in inputs.values() if q.is_money]
    for left in money:
        for right in money:
            if left is right:
                continue
            if not scales_are_combinable(left, right):
                return _refuse(
                    definition,
                    REFUSED_SCALE_INCOMBINABLE,
                    f"one input is scaled {left.scale!r} and another {right.scale!r}; "
                    "an undetected 1000x does not look broken, it looks like a "
                    "plausible percentage",
                    inputs,
                )
    return None


def _check_domain(
    definition: CalculationDefinition, inputs: dict[str, Quantity]
) -> CalculationOutcome | None:
    for role in definition.non_zero_roles:
        if inputs[role].value == 0:
            return _refuse(
                definition,
                REFUSED_DIVIDE_BY_ZERO,
                f"{role} is zero, so the formula has no value",
                inputs,
            )
    for role in definition.positive_roles:
        if inputs[role].value <= 0:
            return _refuse(
                definition,
                REFUSED_NON_POSITIVE_BASE,
                f"{role} is {inputs[role].value}, and a non-positive base has no "
                "compound growth rate — clamping it would produce a number that looks "
                "like an answer",
                inputs,
            )
    return None


__all__ = [
    "REFUSAL_REASONS",
    "REFUSED_ARITHMETIC_ERROR",
    "REFUSED_CURRENCY_MISMATCH",
    "REFUSED_DIVIDE_BY_ZERO",
    "REFUSED_MISSING_INPUT",
    "REFUSED_NON_POSITIVE_BASE",
    "REFUSED_PERIOD_MISMATCH",
    "REFUSED_PERIOD_SPAN_INVALID",
    "REFUSED_PERIOD_TYPE_MISMATCH",
    "REFUSED_PERIOD_UNKNOWN",
    "REFUSED_SCALE_INCOMBINABLE",
    "REFUSED_SCOPE_MISMATCH",
    "REFUSED_SCOPE_NOT_GROUP",
    "REFUSED_SCOPE_NOT_SEGMENT",
    "REFUSED_SCOPE_UNKNOWN",
    "REFUSED_UNIT_MISMATCH",
    "REFUSED_VALUE_MISSING",
    "STATUS_COMPUTED",
    "STATUS_REFUSED",
    "CalculationOutcome",
    "calculate",
]
