"""Quantities, and the rules for putting two of them in one expression — V3.3 Slice 3.3.

WHY A QUANTITY AND NOT A FLOAT
==============================
A float has no period, no scope, no currency and no scale, so nothing about a float
can be refused. Every arithmetic defect this platform has had was a float that was
*valid* — a real number, correctly parsed — combined with another valid float that
measured something else: a segment figure over a Group figure, an interim number
against an annual one, a figure in thousands divided by a figure in millions.

So the unit of computation is a ``Quantity``, and combining two of them asks a
question that can be answered "no".

SCALE IS THE ONE THAT LOOKS SAFE AND IS NOT
===========================================
``extracted_fact_validator`` tolerates an unknown scale on purpose: it compares two
money candidates for *agreement*, and comparing raw digits is the honest fallback when
neither says whether it means thousands. A **ratio** cannot do that. An undetected
1000x does not produce an obviously broken number, it produces a plausible percentage,
and a plausible wrong percentage is worse than a refusal.

Two rules follow, and they are different:

* **Both scales known** → convert to a common base and compute.
* **Both scale strings identical, even if unknown** → a *ratio* is scale-free, so two
  figures at the same unstated scale divide correctly. A difference does too.
* **One known, one unknown** → refuse. There is no reading of that pair that is safe.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from app.services.sources.extracted_fact_validator import (
    UNIT_CURRENCY_AMOUNT,
    UNIT_PEOPLE,
    UNIT_PERCENT,
)
from app.services.sources.fact_scope import FactScope
from app.services.sources.financial_period import ReportingPeriod

#: Derived units this engine can produce that no fact ever carries.
UNIT_RATIO = "ratio"
UNIT_YEARS = "years"

#: Every unit an input or a result may have.
UNITS: frozenset[str] = frozenset(
    {UNIT_CURRENCY_AMOUNT, UNIT_PERCENT, UNIT_PEOPLE, UNIT_RATIO, UNIT_YEARS}
)

#: Units whose magnitude depends on a scale word, so a scale is load-bearing.
SCALED_UNITS: frozenset[str] = frozenset({UNIT_CURRENCY_AMOUNT})

#: The multiplier table, reused from the validator rather than re-declared, so the two
#: cannot disagree about what "million" means.
SCALE_MULTIPLIER: dict[str, Decimal] = {
    "thousand": Decimal("1000"),
    "million": Decimal("1000000"),
    "billion": Decimal("1000000000"),
}


@dataclass(frozen=True)
class Quantity:
    """One number with everything needed to refuse combining it with another."""

    value: Decimal
    unit: str
    currency: str | None = None
    scale: str | None = None
    period: ReportingPeriod | None = None
    scope: FactScope | None = None
    #: The fact this came from, so a result can name its inputs by id.
    fact_id: str | None = None
    label: str | None = None

    @property
    def is_money(self) -> bool:
        return self.unit == UNIT_CURRENCY_AMOUNT

    @property
    def scale_is_known(self) -> bool:
        return self.scale in SCALE_MULTIPLIER

    @property
    def base_value(self) -> Decimal | None:
        """The value in a common base unit, or ``None`` when it cannot be known.

        ``None`` is not zero and not the raw digits: it is the honest answer for a
        money figure whose scale was never stated, and every caller has to handle it.
        """
        if self.unit not in SCALED_UNITS:
            return self.value
        if self.scale is None:
            return None
        multiplier = SCALE_MULTIPLIER.get(self.scale)
        if multiplier is None:
            return None
        return self.value * multiplier

    def to_dict(self) -> dict[str, object]:
        return {
            "value": float(self.value),
            "unit": self.unit,
            "currency": self.currency,
            "scale": self.scale,
            "period_key": self.period.key if self.period else None,
            "period_type": self.period.period_type if self.period else None,
            "scope_type": self.scope.scope_type if self.scope else None,
            "scope_key": self.scope.scope_key if self.scope else None,
            "fact_id": self.fact_id,
            "label": self.label,
        }


def scales_are_combinable(a: Quantity, b: Quantity) -> bool:
    """Whether ``a`` and ``b`` may appear in one ratio or difference.

    True when both scales are known, or when both scale strings are *identical* —
    including both being ``None``, because a ratio or a difference between two figures
    at the same unstated scale is scale-free. False when one is known and the other is
    not: there is no reading of that pair that is safe.
    """
    if not (a.unit in SCALED_UNITS or b.unit in SCALED_UNITS):
        return True
    if a.scale_is_known and b.scale_is_known:
        return True
    return a.scale == b.scale


def common_magnitudes(a: Quantity, b: Quantity) -> tuple[Decimal, Decimal] | None:
    """Two magnitudes comparable with each other, or ``None`` if they are not.

    **Identical scales are left alone**, whether or not the scale is known. Two figures
    both printed in millions are already comparable, and converting them to base units
    would make a difference come back as ``-4100000000`` where the documents said
    ``-4100`` — the same number, in a form no reader recognises, and with the shared
    scale thrown away for nothing.

    Conversion happens only when the scales *differ* and both are known, which is the
    one case where the raw digits are not comparable.
    """
    if not scales_are_combinable(a, b):
        return None
    if a.scale == b.scale:
        return a.value, b.value
    left, right = a.base_value, b.base_value
    if left is None or right is None:  # pragma: no cover - guarded above
        return None
    return left, right


__all__ = [
    "SCALED_UNITS",
    "SCALE_MULTIPLIER",
    "UNITS",
    "UNIT_CURRENCY_AMOUNT",
    "UNIT_PEOPLE",
    "UNIT_PERCENT",
    "UNIT_RATIO",
    "UNIT_YEARS",
    "Quantity",
    "common_magnitudes",
    "scales_are_combinable",
]
