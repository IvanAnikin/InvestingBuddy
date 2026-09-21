"""Deterministic metrics over one period's statement lines — V3.18.1.

WHY THIS EXISTS
===============
The calculation engine (V3.3.3) computes from ``ExtractedFact`` rows, inside the V3 tool
layer. The council that writes the report's analysis reads an *evidence pack* built from
the regulator's structured data, and the engine never saw that path. So on the path that
actually produced prose, the only "derived metrics" were whatever a model chose to divide.
A live report divided net income by operating cash flow, a ratio defined nowhere, and read
its fall as weakening cash conversion — backwards.

This module runs the SAME engine, with the SAME definitions and the SAME period, scope,
currency and domain refusals, over one period's statement lines. It adds no arithmetic of
its own: a second implementation of "operating cash flow over net income" would be a
second place for the definition to drift.

WHAT A READING CARRIES
======================
The value, **and its semantics**: the formula, numerator, denominator, unit, period,
scope, which direction is favourable, and the interpretation rule. A model handed the
number is handed its meaning in the same line, so it has nothing to invent.

A refusal is returned as a refusal with its reason. Missing input ⇒ no reading — never
zero, never a partial.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.calculations.definitions import DEFINITIONS, CalculationDefinition
from app.services.calculations.engine import calculate
from app.services.calculations.quantities import (
    UNIT_CURRENCY_AMOUNT,
    UNIT_PERCENT,
    UNIT_RATIO,
    Quantity,
)
from app.services.sources.fact_scope import FactScope
from app.services.sources.financial_period import parse_period

#: The metrics worth computing from a statement bundle, in the order a reader wants
#: them. Growth and segment metrics are absent on purpose: they need two periods or two
#: scopes, which one period's Group statement cannot supply.
STATEMENT_METRIC_KEYS: tuple[str, ...] = (
    "operating_margin",
    "net_margin",
    "gross_margin",
    "cash_conversion",
    "fcf_conversion",
    "fcf_margin",
    "capex_to_ocf",
    "capex_intensity",
    "net_debt",
    "return_on_equity",
    "dividend_cover_by_fcf",
)


@dataclass(frozen=True)
class MetricReading:
    """One computed metric with the meaning it must be read with."""

    key: str
    value: float
    unit: str
    period_label: str
    scope: str
    currency: str | None
    semantics: dict[str, Any]
    inputs: dict[str, float]

    @property
    def display(self) -> str:
        if self.unit == UNIT_PERCENT:
            return f"{self.value:.1f}%"
        if self.unit == UNIT_RATIO:
            return f"{self.value:.2f}x"
        return f"{self.value:,.1f}"

    def as_evidence_line(self) -> str:
        """The single line a model receives. Value and meaning never travel apart."""
        sem = self.semantics
        return (
            f"{sem['label']} [{self.key}] = {self.display} for {self.period_label} "
            f"({self.scope}); defined as {sem['definition']}; "
            f"{sem['directionality'].replace('_', ' ')}. {sem['interpretation']}"
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric_id": self.key,
            "value": self.value,
            "display": self.display,
            "unit": self.unit,
            "period": self.period_label,
            "scope": self.scope,
            "currency": self.currency,
            "inputs": dict(self.inputs),
            **{k: v for k, v in self.semantics.items() if k not in {"metric_id", "unit"}},
        }


@dataclass(frozen=True)
class MetricRefusal:
    key: str
    reason: str
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return {"metric_id": self.key, "refusal_reason": self.reason, "detail": self.detail}


def _quantities(
    definition: CalculationDefinition,
    values: Mapping[str, float | None],
    *,
    period_label: str,
    currency: str | None,
    scale: str | None,
) -> dict[str, Quantity]:
    period = parse_period(period_label)
    scope = FactScope(scope_type="group")
    supplied: dict[str, Quantity] = {}
    for spec in definition.inputs:
        raw = next(
            (values[label] for label in spec.labels if values.get(label) is not None), None
        )
        if raw is None:
            continue
        is_money = spec.unit == UNIT_CURRENCY_AMOUNT
        supplied[spec.role] = Quantity(
            value=Decimal(str(raw)),
            unit=spec.unit,
            currency=currency if is_money else None,
            scale=scale if is_money else None,
            period=period,
            scope=scope,
            label=spec.role,
        )
    return supplied


def compute_statement_metrics(
    values: Mapping[str, float | None],
    *,
    period_label: str,
    currency: str | None = "USD",
    scale: str | None = "million",
    keys: Sequence[str] = STATEMENT_METRIC_KEYS,
) -> tuple[list[MetricReading], list[MetricRefusal]]:
    """Every requested metric these statement lines can support, and why the rest cannot.

    ``values`` is keyed by the calculation vocabulary's input labels (``revenue``,
    ``operating_profit``, ``net_income``, ``operating_cash_flow``,
    ``capital_expenditure``, ``free_cash_flow``, ``total_debt``,
    ``cash_and_equivalents``, ``shareholders_equity``, ``dividends_paid`` …). **All of
    them must already be for ``period_label``** — this function is handed one period's
    lines and the engine refuses anything else, but it cannot know a caller mislabelled
    a figure; the own-period rule upstream is what guarantees that.
    """
    readings: list[MetricReading] = []
    refusals: list[MetricRefusal] = []
    for key in keys:
        definition = DEFINITIONS.get(key)
        if definition is None:
            continue
        supplied = _quantities(
            definition, values, period_label=period_label, currency=currency, scale=scale
        )
        outcome = calculate(definition, supplied)
        if not outcome.computed or outcome.value is None:
            refusals.append(
                MetricRefusal(
                    key=key,
                    reason=outcome.refusal_reason or "refused",
                    detail=outcome.detail,
                )
            )
            continue
        readings.append(
            MetricReading(
                key=key,
                value=round(float(outcome.value), 2),
                unit=definition.result_unit,
                period_label=period_label,
                scope="group",
                currency=currency if definition.result_unit == UNIT_CURRENCY_AMOUNT else None,
                semantics=definition.semantics(),
                inputs={role: float(q.value) for role, q in supplied.items()},
            )
        )
    return readings, refusals


#: `fundamentals_summary` key → calculation input label.
SEC_SUMMARY_TO_INPUT: dict[str, str] = {
    "revenue_usd_m": "revenue",
    "gross_profit_usd_m": "gross_profit",
    "operating_income_usd_m": "operating_profit",
    "net_income_usd_m": "net_income",
    "operating_cash_flow_usd_m": "operating_cash_flow",
    "capital_expenditures_usd_m": "capital_expenditure",
    "free_cash_flow_usd_m": "free_cash_flow",
    "dividends_paid_usd_m": "dividends_paid",
    "total_debt_usd_m": "total_debt",
    "cash_and_equivalents_usd_m": "cash_and_equivalents",
    "shareholders_equity_usd_m": "shareholders_equity",
}


def metrics_from_sec_summary(
    fs: Mapping[str, Any],
) -> tuple[list[MetricReading], list[MetricRefusal]]:
    """Readings from a snapshot's ``fundamentals_summary``. Empty when it has no year."""
    fiscal_year = fs.get("fiscal_year")
    if not fiscal_year or (fs.get("period_basis") or "annual") != "annual":
        # An interim bundle has no annual label to compute under, and stamping one on
        # it would be the silent annualisation the period invariants forbid.
        return [], []
    withheld = set((fs.get("statement_consistency") or {}).get("withheld") or ())
    for finding in (fs.get("statement_consistency") or {}).get("inconsistencies") or []:
        withheld.update(finding.get("withhold_derived") or ())
    values = {
        label: fs.get(summary_key) for summary_key, label in SEC_SUMMARY_TO_INPUT.items()
    }
    # A debt leg the filer has not tagged for this period makes `total_debt` a PARTIAL
    # sum. It may well be zero — and "may well be" is not a figure. Net debt built on a
    # partial total would be handed to the council as a defined metric, with authority.
    debt_legs = {"short_term_debt", "long_term_debt"}
    if any(
        isinstance(item, dict) and item.get("field") in debt_legs
        for item in fs.get("withheld_fields") or ()
    ):
        withheld.add("net_debt")
    keys = [k for k in STATEMENT_METRIC_KEYS if k not in withheld]
    return compute_statement_metrics(values, period_label=f"FY{fiscal_year}", keys=keys)


__all__ = [
    "SEC_SUMMARY_TO_INPUT",
    "STATEMENT_METRIC_KEYS",
    "MetricReading",
    "MetricRefusal",
    "compute_statement_metrics",
    "metrics_from_sec_summary",
]
