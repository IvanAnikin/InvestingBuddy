"""Re-check a company's size at ANALYSIS time — V3.20 slice 1.3 (spec §3.1, point 4).

WHY
===
Discovery verifies a market cap on the day of the run. Deep research may run days or weeks
later, and a market cap moves: a USD 1.9bn small cap can be a USD 2.3bn company by the time
its report is written. The report's ``size_fit`` used the discovery snapshot as if it were
current, and never said how old it was.

WHAT
====
The same official sources discovery uses, read again for the one company being analysed:
the exchange's own directory (ASX market cap, Euronext admitted shares × closing price, …)
and, for a US filer, the 10-K public float (a lower bound). The amount is converted with an
official FX rate (H.10, else ECB). Every figure carries its as-of date, method and source.

Nothing here guesses: no directory, no official figure, or no FX rate is a ``reason``, and
the report falls back to the discovery snapshot WITH its age — never to a silent pass.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
from types import SimpleNamespace
from typing import Any

from app.services.discovery import constraints as cons
from app.services.discovery.fx import FxRate, to_usd, usd_rate

#: A discovery snapshot older than this is flagged as stale in the report's size check.
SNAPSHOT_STALE_AFTER_DAYS = 30


@dataclass(frozen=True)
class SizeObservation:
    """A market cap in USD, with where and when it came from."""

    market_cap_usd: float
    as_of: str | None
    method: str
    source_url: str | None
    #: ``analysis_time`` (re-read today) | ``discovery_snapshot`` | ``company_record``.
    origin: str
    native_amount: float | None = None
    native_currency: str | None = None
    fx: dict[str, Any] | None = None

    @property
    def is_lower_bound(self) -> bool:
        return self.method == cons.FLOAT_FLOOR_METHOD

    def to_dict(self) -> dict[str, Any]:
        return {
            "market_cap_usd": round(self.market_cap_usd, 0),
            "as_of": self.as_of,
            "method": self.method,
            "source_url": self.source_url,
            "origin": self.origin,
            "native_amount": self.native_amount,
            "native_currency": self.native_currency,
            "fx": self.fx,
            "lower_bound": self.is_lower_bound,
        }


def age_days(as_of: str | None, *, today: date | None = None) -> int | None:
    if not as_of:
        return None
    try:
        day = date.fromisoformat(str(as_of)[:10])
    except ValueError:
        return None
    return ((today or datetime.now(timezone.utc).date()) - day).days


async def recheck_market_cap(
    *,
    name: str | None,
    ticker: str | None,
    venue: str | None,
    isin: str | None = None,
    cfg: Any = None,
    fetcher: Any = None,
) -> tuple[SizeObservation | None, str | None]:
    """``(observation, None)`` from the exchange's own figure today, or ``(None, reason)``.

    Never raises: a source that cannot be read is a reason, not an error.
    """
    from app.services.discovery.directories import find_listing
    from app.services.discovery.pipeline import _official_cap

    if not ticker or not venue:
        return None, "no ticker and venue to look the company up by"
    try:
        listing, reason = await find_listing(
            name=name, ticker=ticker, venue=venue, isin=isin, cfg=cfg, fetcher=fetcher
        )
    except Exception as exc:  # noqa: BLE001 - an unreadable directory is a reason
        return None, f"the exchange directory could not be read ({type(exc).__name__})"
    if listing is None:
        return None, reason or f"no official directory covers venue {venue}"
    observation = await _official_cap(
        SimpleNamespace(directory_listing=listing.to_dict()), cfg=cfg, fetcher=fetcher
    )
    if observation is None or not observation.verified:
        return None, "the exchange publishes no market capitalisation for this listing"
    return await to_size_observation(observation, origin="analysis_time", cfg=cfg,
                                     fetcher=fetcher)


async def to_size_observation(
    observation: cons.MarketCapObservation,
    *,
    origin: str,
    cfg: Any = None,
    fetcher: Any = None,
) -> tuple[SizeObservation | None, str | None]:
    """Convert a verified native-currency observation to USD at an official rate."""
    as_of = None
    if observation.as_of_basis == "stated" and observation.as_of:
        try:
            as_of = date.fromisoformat(str(observation.as_of)[:10])
        except ValueError:
            as_of = None
    rate, reason = await usd_rate(observation.currency, as_of, cfg=cfg, fetcher=fetcher)
    if rate is None:
        return None, reason or "no official FX rate"
    return _observation(observation, rate, origin), None


def _observation(
    observation: cons.MarketCapObservation, rate: FxRate, origin: str
) -> SizeObservation:
    return SizeObservation(
        market_cap_usd=to_usd(observation.amount, observation.currency, rate),
        as_of=str(observation.as_of)[:10] if observation.as_of else None,
        method=observation.method or "unknown",
        source_url=observation.source_url,
        origin=origin,
        native_amount=observation.amount,
        native_currency=observation.currency,
        fx=None if rate.currency == "USD" else rate.to_dict(),
    )


__all__ = [
    "SNAPSHOT_STALE_AFTER_DAYS",
    "SizeObservation",
    "age_days",
    "recheck_market_cap",
    "to_size_observation",
]
