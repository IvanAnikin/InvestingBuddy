"""Liquidity and its components — ADR-096 (Research Integrity Closure H6, spec §11).

WHAT WAS WRONG (QA campaign 2026-11, round 3)
=============================================
A biotech's derived cash runway read "5.9 quarter-equivalents" from cash and cash
equivalents alone (USD 61.5m), while the same 20-F's balance sheet held USD 144.8m of
fixed-term deposits not classified as cash equivalents, and the issuer's own liquidity
was "cash, cash equivalents, fixed-term deposits and restricted cash". The runway was
silently cash-only: nothing on the page said a component was left out.

THE MODEL
=========
Liquidity is a set of COMPONENTS, each its own issuer statement line, never a single
number the platform invents:

* ``cash_and_equivalents`` — the anchor; its date is the liquidity's date;
* ``short_term_deposits`` — deposits not classified as cash equivalents;
* ``short_term_investments`` — current marketable securities / short-term investments;
* ``restricted_cash`` — current restricted cash: EXCLUDED from available liquidity (it is
  not available to fund operations unless the issuer says otherwise, which the platform
  cannot read), but shown;
* ``current_financial_assets`` — an IFRS AGGREGATE that can hold deposits, restricted
  cash and derivatives. It is never summed; when it is reported and no finer component
  explains it, a non-cash component exists whose value the platform does not know.

Available liquidity = cash + every AVAILABLE component (deposits, investments) the issuer
reports for the SAME date, in the SAME currency, at Group scope. Every included component
is cited by its fact id, value, date and document.

FAIL CLOSED
===========
When a component is known to exist — a held statement fact of that line for ANY date, or
an unexplained current-financial-assets aggregate — but has no value for the cash's date,
currency and scope, available liquidity is NOT computed: the state is
``cash_only_incomplete`` and ``excludes`` names what is missing. A runway over it is
labelled "cash only, excludes …" (``issuer_financials``); it is never silently cash-only.

Pure: no I/O, no model. No issuer is named.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation
from typing import Any

from app.services.sources.primary_fact_parser import (
    FIELD_CASH,
    FIELD_CURRENT_FINANCIAL_ASSETS,
    FIELD_RESTRICTED_CASH,
    FIELD_SHORT_TERM_DEPOSITS,
    FIELD_SHORT_TERM_INVESTMENTS,
)

LIQUIDITY_VERSION = 1

#: Components added to cash when stated for the same date, currency and scope.
AVAILABLE_COMPONENTS: tuple[str, ...] = (FIELD_SHORT_TERM_DEPOSITS, FIELD_SHORT_TERM_INVESTMENTS)
#: Components shown and never added.
EXCLUDED_COMPONENTS: tuple[str, ...] = (FIELD_RESTRICTED_CASH,)
#: Aggregates: they show that a non-cash component exists; never summed.
AGGREGATE_COMPONENTS: tuple[str, ...] = (FIELD_CURRENT_FINANCIAL_ASSETS,)
#: Every liquidity field, cash first.
LIQUIDITY_FIELDS: tuple[str, ...] = (
    FIELD_CASH, *AVAILABLE_COMPONENTS, *EXCLUDED_COMPONENTS, *AGGREGATE_COMPONENTS,
)

ROLE_ANCHOR = "anchor"
ROLE_INCLUDED = "included"
ROLE_EXCLUDED = "excluded_restricted"
ROLE_AGGREGATE = "reported_aggregate"
ROLE_MISSING = "missing_for_date"

#: Cash plus every component the issuer reports for that date.
STATE_COMPLETE = "complete"
#: A component is known to exist but has no value for the cash's date: no total.
STATE_CASH_ONLY_INCOMPLETE = "cash_only_incomplete"
#: No cash figure: nothing to anchor a date.
STATE_NO_CASH = "no_cash"

COMPONENT_LABELS: dict[str, str] = {
    FIELD_CASH: "cash and cash equivalents",
    FIELD_SHORT_TERM_DEPOSITS: "short-term deposits",
    FIELD_SHORT_TERM_INVESTMENTS: "short-term investments",
    FIELD_RESTRICTED_CASH: "restricted cash",
    FIELD_CURRENT_FINANCIAL_ASSETS: "current financial assets",
}

_SCALES: dict[str | None, Decimal] = {
    None: Decimal(1), "": Decimal(1), "units": Decimal(1), "unit": Decimal(1),
    "thousand": Decimal(1000), "million": Decimal(1_000_000),
    "billion": Decimal(1_000_000_000),
}


def _period_group(fact: dict[str, Any]) -> str:
    """The canonical period of a fact ("2025" and "FY2025" are one), else its raw text."""
    from app.services.sources.financial_period import parse_period

    raw = str(fact.get("period") or "")
    period = parse_period(raw or None)
    if period.is_unknown:
        return raw
    return f"{period.period_type}:{period.label()}"


def _is_group(fact: dict[str, Any]) -> bool:
    from app.services.sources.fact_scope import parse_scope

    scope = parse_scope(fact.get("scope"))
    return not scope.is_segment and scope.scope_key == "group"


def _number(fact: dict[str, Any]) -> Decimal | None:
    try:
        return Decimal(str(fact.get("numeric_value")))
    except (InvalidOperation, TypeError, ValueError):
        return None


def _factor(fact: dict[str, Any]) -> Decimal | None:
    scale = fact.get("scale")
    return _SCALES.get(str(scale).strip().lower() if scale is not None else None)


def _view(field: str, fact: dict[str, Any], role: str) -> dict[str, Any]:
    from app.services.pipeline.issuer_financials import slot_number

    locator = str(fact.get("excerpt_id") or "")
    concept = (
        locator.removeprefix("ixbrl:").split("@", 1)[0] if locator.startswith("ixbrl:")
        else None
    )
    return {
        "component": field,
        "label": COMPONENT_LABELS.get(field, field.replace("_", " ")),
        "role": role,
        "value": slot_number(fact),
        "numeric_value": fact.get("numeric_value"),
        "currency": fact.get("currency"),
        "scale": fact.get("scale"),
        "period": fact.get("period"),
        "scope": fact.get("scope"),
        "fact_id": fact.get("fact_id"),
        "document_title": fact.get("document_title"),
        "document_date": fact.get("document_date"),
        "source_url": fact.get("source_url"),
        "xbrl_concept": concept or None,
    }


def _same_basis(fact: dict[str, Any], cash: dict[str, Any]) -> bool:
    return (
        _period_group(fact) == _period_group(cash)
        and (fact.get("currency") or None) == (cash.get("currency") or None)
        and _is_group(fact)
        and _number(fact) is not None
        and _factor(fact) is not None
    )


def build_liquidity(
    slots: dict[str, dict[str, Any]], *, held_fields: frozenset[str] | set[str] = frozenset()
) -> dict[str, Any]:
    """The liquidity of one slot set (annual or current period).

    ``slots`` maps a statement field to the fact chosen for it; ``held_fields`` is every
    field the issuer's eligible statement facts hold for ANY period (a component known
    to exist). Never raises on a partial slot set."""
    cash = slots.get(FIELD_CASH)
    if not isinstance(cash, dict) or _number(cash) is None or _factor(cash) is None:
        return {
            "version": LIQUIDITY_VERSION,
            "state": STATE_NO_CASH,
            "components": [],
            "available_liquidity": None,
            "excludes": [],
        }
    components = [_view(FIELD_CASH, cash, ROLE_ANCHOR)]
    included: list[dict[str, Any]] = [cash]
    missing: list[str] = []
    for field in AVAILABLE_COMPONENTS:
        fact = slots.get(field)
        if isinstance(fact, dict) and _same_basis(fact, cash):
            components.append(_view(field, fact, ROLE_INCLUDED))
            included.append(fact)
        elif isinstance(fact, dict) or field in held_fields:
            missing.append(field)
            entry = {
                "component": field,
                "label": COMPONENT_LABELS[field],
                "role": ROLE_MISSING,
                "value": None,
                "held_period": fact.get("period") if isinstance(fact, dict) else None,
            }
            components.append(entry)
    for field in EXCLUDED_COMPONENTS:
        fact = slots.get(field)
        if isinstance(fact, dict) and _same_basis(fact, cash):
            components.append(_view(field, fact, ROLE_EXCLUDED))
    for field in AGGREGATE_COMPONENTS:
        fact = slots.get(field)
        if isinstance(fact, dict) and _same_basis(fact, cash):
            components.append(_view(field, fact, ROLE_AGGREGATE))
            if len(included) == 1 and field not in missing:
                # Reported, and no finer component explains it: something besides cash
                # is in it, and the platform does not know what or how much.
                missing.append(field)
    excludes = [COMPONENT_LABELS[f] for f in missing]
    base = {
        "version": LIQUIDITY_VERSION,
        "period": cash.get("period"),
        "currency": cash.get("currency"),
        "scope": "group" if _is_group(cash) else cash.get("scope"),
        "components": components,
        "excludes": excludes,
    }
    if missing:
        return {
            **base,
            "state": STATE_CASH_ONLY_INCOMPLETE,
            "available_liquidity": None,
            "note": (
                "Available liquidity is not computed: the issuer reports "
                + ", ".join(excludes)
                + ", with no value for the same date, currency and scope as cash."
            ),
        }
    cash_factor = _factor(cash) or Decimal(1)
    total_base = sum(
        ((_number(f) or Decimal(0)) * (_factor(f) or Decimal(1)) for f in included),
        Decimal(0),
    )
    total = total_base / cash_factor
    return {
        **base,
        "state": STATE_COMPLETE,
        "available_liquidity": {
            "numeric_value": float(total),
            "scale": cash.get("scale"),
            "currency": cash.get("currency"),
            "period": cash.get("period"),
            "includes": [COMPONENT_LABELS[str(c["component"])] for c in components
                         if c["role"] in (ROLE_ANCHOR, ROLE_INCLUDED)],
            "input_fact_ids": [f.get("fact_id") for f in included],
        },
        "note": (
            "Available liquidity = "
            + " + ".join(COMPONENT_LABELS[str(c["component"])] for c in components
                         if c["role"] in (ROLE_ANCHOR, ROLE_INCLUDED))
            + ", each the issuer's own statement line for the same date, currency and "
            "scope. Restricted cash is never included."
        ),
    }


def basis_label(liquidity: dict[str, Any]) -> str:
    """How a runway reads its cash: "cash and cash equivalents + short-term deposits",
    or "cash only, excludes short-term deposits"."""
    if liquidity.get("state") == STATE_CASH_ONLY_INCOMPLETE:
        return "cash only, excludes " + ", ".join(liquidity.get("excludes") or ())
    available = liquidity.get("available_liquidity") or {}
    parts = list(available.get("includes") or ())
    return " + ".join(parts) if parts else COMPONENT_LABELS[FIELD_CASH]


__all__ = [
    "AGGREGATE_COMPONENTS",
    "AVAILABLE_COMPONENTS",
    "COMPONENT_LABELS",
    "EXCLUDED_COMPONENTS",
    "LIQUIDITY_FIELDS",
    "LIQUIDITY_VERSION",
    "STATE_CASH_ONLY_INCOMPLETE",
    "STATE_COMPLETE",
    "STATE_NO_CASH",
    "basis_label",
    "build_liquidity",
]
