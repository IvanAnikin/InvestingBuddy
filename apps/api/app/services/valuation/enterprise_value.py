"""Enterprise value and multiples — V3.20 slice 8.1 (spec §3.8).

Deterministic arithmetic over cited, dated inputs; no model call, no estimate:

* **Market cap** — the run's own dated observation (the exchange's figure re-read at
  analysis time, slice 1.3), in its NATIVE currency.
* **Net debt** — ``total_debt − cash_and_equivalents`` from the issuer's group-scope
  facts for ONE period (the newest annual period that has both), each fact cited.
* **EV** = market cap + net debt, only when both are in the same currency (no FX
  conversion is ever performed here) and the balance sheet was filed no more than
  ``MAX_BALANCE_SHEET_AGE_DAYS`` before the market-cap date.
* **EV / EBIT** — over the newest annual ``operating_profit`` fact (same currency);
  ``not_meaningful`` when EBIT is zero or negative, never a negative multiple.
* **EV / capacity unit** — per disclosed natural-unit capacity reading (slice 5.4), one
  multiple per unit family; never summed across units.
* **EV / backlog** — needs a disclosed order backlog figure; the platform holds none as a
  fact yet, so it is ``not_available`` with that reason.
* **Own-history multiple range** — needs a dated price series; the platform holds only
  the current market cap, so it is ``not_available`` with that reason (never a range
  built from one point).

A multiple says how the market prices the company today relative to one disclosed
figure. It is not a judgement of value.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

BLOCK_VERSION = "2026-11-16.1"
MAX_BALANCE_SHEET_AGE_DAYS = 400

COMPUTED = "computed"
REFUSED = "refused"
NOT_MEANINGFUL = "not_meaningful"
NOT_AVAILABLE = "not_available"

_SCALE = {None: Decimal(1), "": Decimal(1), "unit": Decimal(1), "units": Decimal(1),
          "thousand": Decimal(1000), "million": Decimal(1_000_000),
          "billion": Decimal(1_000_000_000)}


def absolute(value: Any, scale: str | None) -> Decimal | None:
    """A fact value in absolute currency units; ``None`` for an unknown scale."""
    key = (scale or "").strip().lower() or None
    if key not in _SCALE or value is None:
        return None
    try:
        return Decimal(str(value)) * _SCALE[key]
    except (InvalidOperation, ValueError):
        return None


def _day(value: Any) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _money(value: Decimal | None) -> float | None:
    return None if value is None else float(round(value, 0))


def _refused(reason: str, detail: str) -> dict[str, Any]:
    return {"state": REFUSED, "value": None, "reason": reason, "detail": detail}


def enterprise_value_block(
    *,
    market_cap: Mapping[str, Any] | None,
    balance: Mapping[str, Any] | None,
    ebit: Mapping[str, Any] | None = None,
    capacity: Sequence[Mapping[str, Any]] = (),
) -> dict[str, Any]:
    """The block (pure). Inputs are already absolute amounts with their provenance.

    ``market_cap``: ``{amount, currency, as_of, source_url, method, lower_bound}``.
    ``balance``: ``{period, total_debt, cash, currency, filed, sources[]}``.
    ``ebit``: ``{period, value, currency, filed, source_url}``.
    """
    mc_amount = market_cap.get("amount") if market_cap else None
    mc_ccy = (market_cap or {}).get("currency")
    mc_day = _day((market_cap or {}).get("as_of"))
    net_debt: Decimal | None = None
    nd_state: dict[str, Any]
    if not balance or balance.get("total_debt") is None or balance.get("cash") is None:
        nd_state = {"state": NOT_AVAILABLE, "value": None,
                    "reason": "no period with both total debt and cash held as facts"}
    else:
        net_debt = Decimal(str(balance["total_debt"])) - Decimal(str(balance["cash"]))
        nd_state = {"state": COMPUTED, "value": _money(net_debt),
                    "currency": balance.get("currency"), "period": balance.get("period"),
                    "filed": balance.get("filed"),
                    "total_debt": _money(Decimal(str(balance["total_debt"]))),
                    "cash_and_equivalents": _money(Decimal(str(balance["cash"]))),
                    "sources": list(balance.get("sources") or []),
                    "formula": "total_debt - cash_and_equivalents"}

    ev: Decimal | None = None
    if mc_amount is None:
        ev_state = _refused("no_market_cap", "no dated market capitalisation for this run")
    elif net_debt is None:
        ev_state = _refused("no_net_debt", nd_state["reason"])
    elif not mc_ccy or mc_ccy != balance.get("currency"):  # type: ignore[union-attr]
        ev_state = _refused(
            "currency_mismatch",
            f"market cap in {mc_ccy or 'unknown'}, balance sheet in "
            f"{balance.get('currency') or 'unknown'}; no conversion is made")  # type: ignore[union-attr]
    else:
        filed = _day(balance.get("filed"))  # type: ignore[union-attr]
        age = (mc_day - filed).days if (mc_day and filed) else None
        if age is None:
            ev_state = _refused("undated", "the market cap or the balance sheet has no date")
        elif age > MAX_BALANCE_SHEET_AGE_DAYS or age < -31:
            ev_state = _refused(
                "stale_balance_sheet",
                f"balance sheet filed {filed}, market cap dated {mc_day} "
                f"({age} days apart; at most {MAX_BALANCE_SHEET_AGE_DAYS})")
        else:
            ev = Decimal(str(mc_amount)) + net_debt
            ev_state = {"state": COMPUTED, "value": _money(ev), "currency": mc_ccy,
                        "as_of": mc_day.isoformat() if mc_day else None,
                        "formula": "market_cap + net_debt",
                        "lower_bound": bool((market_cap or {}).get("lower_bound")),
                        "balance_sheet_age_days": age}

    multiples: list[dict[str, Any]] = []
    # EV / EBIT
    if ev is None:
        multiples.append({"key": "ev_to_ebit", **_refused("no_ev", "EV was not computed")})
    elif not ebit or ebit.get("value") is None:
        multiples.append({"key": "ev_to_ebit", "state": NOT_AVAILABLE, "value": None,
                          "reason": "no annual operating profit held as a fact"})
    elif ebit.get("currency") != mc_ccy:
        multiples.append({"key": "ev_to_ebit", **_refused(
            "currency_mismatch", f"EBIT in {ebit.get('currency')}, EV in {mc_ccy}")})
    else:
        e = Decimal(str(ebit["value"]))
        if e <= 0:
            multiples.append({"key": "ev_to_ebit", "state": NOT_MEANINGFUL, "value": None,
                              "reason": "operating profit is zero or negative",
                              "ebit": _money(e), "period": ebit.get("period")})
        else:
            multiples.append({"key": "ev_to_ebit", "state": COMPUTED,
                              "value": float(round(ev / e, 2)), "unit": "x",
                              "ebit": _money(e), "period": ebit.get("period"),
                              "filed": ebit.get("filed"),
                              "source_url": ebit.get("source_url"),
                              "formula": "enterprise_value / operating_profit"})
    # EV / capacity unit — one per unit, never summed across units.
    seen_units: set[str] = set()
    for reading in capacity:
        unit = str(reading.get("unit") or "").strip()
        value = reading.get("value")
        if not unit or unit in seen_units or value in (None, 0):
            continue
        seen_units.add(unit)
        if ev is None:
            continue
        try:
            per = ev / Decimal(str(value))
        except (InvalidOperation, ZeroDivisionError):
            continue
        multiples.append({"key": "ev_per_capacity_unit", "state": COMPUTED,
                          "value": _money(per), "unit": f"{mc_ccy} per {unit}",
                          "capacity": value, "capacity_unit": unit,
                          "source_url": reading.get("source_url"),
                          "as_of": reading.get("as_of"),
                          "formula": "enterprise_value / disclosed capacity"})
    multiples.append({"key": "ev_to_backlog", "state": NOT_AVAILABLE, "value": None,
                      "reason": "no disclosed order backlog figure is held as a fact"})

    return {
        "version": BLOCK_VERSION,
        "market_cap": {
            "value": _money(Decimal(str(mc_amount))) if mc_amount is not None else None,
            "currency": mc_ccy,
            "as_of": mc_day.isoformat() if mc_day else None,
            "source_url": (market_cap or {}).get("source_url"),
            "method": (market_cap or {}).get("method"),
            "lower_bound": bool((market_cap or {}).get("lower_bound")),
        },
        "net_debt": nd_state,
        "enterprise_value": ev_state,
        "multiples": multiples,
        "own_history": {
            "state": NOT_AVAILABLE,
            "reason": "a multiple range over the company's own history needs a dated price "
                      "series; only today's market capitalisation is held",
        },
        "note": (
            "Arithmetic over the dated market capitalisation and the issuer's own filed "
            "figures, each cited; no currency conversion and no estimate. A multiple shows "
            "how the market prices the company relative to one disclosed figure; it is not "
            "a judgement of value."
        ),
    }


__all__ = [
    "BLOCK_VERSION",
    "MAX_BALANCE_SHEET_AGE_DAYS",
    "absolute",
    "enterprise_value_block",
]
