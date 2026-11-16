"""Peers by layer from the comparable-set store — V3.20 slice 8.2 (spec §3.8).

The subject's electricity layer (slice 5.2) selects the newest research record of every
other company classified into the SAME layer (``research_records.comparable_set``,
slice 9.1). Each peer is shown with what its own record holds — market cap and date,
EV/EBIT and currency, obscurity score — and its size relative to the subject.

ADR-089 still applies: same layer is the function test; size is the second test. A peer
whose market cap is outside ``SIZE_BAND`` × the subject's is listed but marked
``comparable_size: false`` and left out of the median. The median EV/EBIT is given only
over at least ``MIN_PEERS_FOR_MEDIAN`` size-comparable peers in ONE currency; otherwise
it is ``null`` with the reason. Records are as of their own dates, which are shown.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from decimal import Decimal
from statistics import median
from typing import Any

SIZE_BAND = (0.2, 5.0)
MIN_PEERS_FOR_MEDIAN = 3
MAX_PEERS = 15


def _f(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(Decimal(str(value)))
    except (ArithmeticError, ValueError):
        return None


def layer_peers_block(
    *,
    layer: str,
    subject_market_cap_usd: float | None,
    subject_ev_to_ebit: float | None,
    records: Sequence[Any],
) -> dict[str, Any] | None:
    """The block (pure), or ``None`` when no other company holds a record in the layer."""
    if not records:
        return None
    peers: list[dict[str, Any]] = []
    for r in list(records)[:MAX_PEERS]:
        cap = _f(getattr(r, "market_cap_usd", None))
        ratio = (round(cap / subject_market_cap_usd, 2)
                 if cap and subject_market_cap_usd else None)
        peers.append({
            "ticker": getattr(r, "ticker", None),
            "exchange": getattr(r, "exchange", None),
            "as_of": r.as_of.isoformat() if getattr(r, "as_of", None) else None,
            "market_cap_usd": cap,
            "market_cap_as_of": (r.market_cap_as_of.isoformat()
                                 if getattr(r, "market_cap_as_of", None) else None),
            "ev_to_ebit": _f(getattr(r, "ev_to_ebit", None)),
            "ev_currency": getattr(r, "ev_currency", None),
            "obscurity_score": getattr(r, "obscurity_score", None),
            "size_ratio": ratio,
            "comparable_size": (None if ratio is None
                                else SIZE_BAND[0] <= ratio <= SIZE_BAND[1]),
            "research_record_id": str(getattr(r, "id", "") or "") or None,
        })
    usable = [p for p in peers if p["ev_to_ebit"] is not None and p["comparable_size"]]
    currencies = {p["ev_currency"] for p in usable}
    if subject_market_cap_usd is None:
        med, reason = None, "the subject has no dated market cap, so size cannot be compared"
    elif len(usable) < MIN_PEERS_FOR_MEDIAN:
        med, reason = None, (f"{len(usable)} size-comparable peer(s) with EV/EBIT; at least "
                             f"{MIN_PEERS_FOR_MEDIAN} are needed")
    elif len(currencies) != 1:
        med, reason = None, "peers report EV in more than one currency"
    else:
        med, reason = round(median(p["ev_to_ebit"] for p in usable), 2), None
    return {
        "layer": layer,
        "size_band": list(SIZE_BAND),
        "peers": peers,
        "peer_count": len(peers),
        "subject_ev_to_ebit": subject_ev_to_ebit,
        "median_ev_to_ebit": med,
        "median_basis": (f"{len(usable)} size-comparable peers, one currency"
                         if med is not None else None),
        "median_reason": reason,
        "note": (
            "Companies the platform has researched in the same electricity layer, each as "
            "of its own record's date. Same layer is the function test; a peer outside the "
            "size band is listed but not in the median. A comparison, not a judgement of "
            "value."
        ),
    }


def subject_inputs(
    ev_block: Mapping[str, Any] | None, size_fit: Mapping[str, Any] | None,
) -> tuple[float | None, float | None]:
    """(subject USD market cap, subject EV/EBIT) from this run's own blocks."""
    cap = _f((size_fit or {}).get("market_cap_usd"))
    ev_to_ebit = next((m.get("value") for m in (ev_block or {}).get("multiples") or []
                       if m.get("key") == "ev_to_ebit" and m.get("state") == "computed"),
                      None)
    return cap, _f(ev_to_ebit)


__all__ = ["MIN_PEERS_FOR_MEDIAN", "SIZE_BAND", "layer_peers_block", "subject_inputs"]
