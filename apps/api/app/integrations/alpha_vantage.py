"""Alpha Vantage earnings + estimates adapter — V3.20 slice 4.2 (ADR-097, owner decision D2).

Two functions of ``https://www.alphavantage.co/query``:

* ``EARNINGS`` — per reported quarter: ``fiscalDateEnding``, ``reportedDate``,
  ``reportedEPS``, ``estimatedEPS``, ``surprise``, ``surprisePercentage``,
  ``reportTime``. These EPS figures are on the PROVIDER'S basis (usually adjusted), not
  the filed diluted EPS; they are kept apart and never subtracted from it (ADR-097 §6).
  ``annualEarnings`` is ignored: its newest row is the partial current year (seen on the
  IBM demo response of 2026-11-16: fiscal year "2026-09-30" = 7.79, three quarters).
* ``EARNINGS_ESTIMATES`` — per fiscal quarter / year (``horizon`` = ``fiscal quarter`` |
  ``fiscal year``, ``date`` = period end): EPS and revenue average / high / low / analyst
  count, the EPS average 7/30/60/90 days ago and revision counts. Past quarters are
  listed too; read on a date after the report, they are post-report observations and
  never count as an expectation (ADR-097 §2).

Formats were pinned from the public ``apikey=demo`` responses (IBM) on 2026-11-16:
numbers are strings ("24.0000"), missing values are JSON ``null`` or the string
``"None"``. A refused call answers HTTP 200 with a single ``Information`` / ``Note`` /
``Error Message`` key instead of data.

The key is sent as the ``apikey`` query parameter and NEVER stored: every URL this
module returns for a record (``public_url``) omits it. Private use only (registry
``alpha_vantage``): the free tier's terms do not cover a public launch.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any, Awaitable, Callable
from urllib.parse import urlencode

BASE_URL = "https://www.alphavantage.co/query"
VENDOR = "alpha_vantage"
FUNCTION_EARNINGS = "EARNINGS"
FUNCTION_ESTIMATES = "EARNINGS_ESTIMATES"
#: Free-tier requests per day (Alpha Vantage support page, 2026).
DAILY_CALL_BUDGET = 25

HORIZON_QUARTER = "fiscal quarter"
HORIZON_YEAR = "fiscal year"

_SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9.\-]{0,9}$")
_ERROR_KEYS = ("Error Message", "Information", "Note")

#: ``(url, accept) -> (status, body)`` — the same shape as the SEC fetchers.
Fetch = Callable[[str, str], Awaitable[tuple[int, bytes | None]]]


class AlphaVantageError(RuntimeError):
    """A refused or unreadable response. ``reason`` is stable and key-free."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason if not detail else f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


def valid_symbol(symbol: str | None) -> bool:
    return bool(symbol) and bool(_SYMBOL_RE.match(symbol or ""))


def public_url(function: str, symbol: str) -> str:
    """The request URL WITHOUT the key — the only form ever stored or logged."""
    return f"{BASE_URL}?{urlencode({'function': function, 'symbol': symbol})}"


def request_url(function: str, symbol: str, api_key: str) -> str:
    return f"{BASE_URL}?{urlencode({'function': function, 'symbol': symbol, 'apikey': api_key})}"


def refusal_reason(payload: Any) -> str | None:
    """Why a payload is a refusal rather than data, or None for data.

    ``Error Message`` = the symbol / function was rejected; ``Information`` / ``Note`` =
    a rate limit, a premium-only function or a key problem. The vendor's message text is
    never returned (it can echo the request).
    """
    if not isinstance(payload, dict):
        return "unreadable_response"
    if "Error Message" in payload:
        return "vendor_rejected_request"
    for key in ("Information", "Note"):
        if key in payload:
            text = str(payload.get(key) or "").lower()
            if "rate limit" in text or "requests per day" in text or "per minute" in text:
                return "vendor_rate_limited"
            if "premium" in text:
                return "vendor_premium_only"
            if "api key" in text or "apikey" in text:
                return "vendor_key_refused"
            return "vendor_information_response"
    return None


def _dec(value: Any) -> Decimal | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text in {"None", "-", "null", "NaN"}:
        return None
    try:
        out = Decimal(text)
    except InvalidOperation:
        return None
    return out if out.is_finite() else None


def _count(value: Any) -> int | None:
    d = _dec(value)
    if d is None or d < 0 or d != d.to_integral_value():
        return None
    return int(d)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class ReportedQuarter:
    """The provider's own record of one reported quarter (provider EPS basis)."""

    period_end: date
    reported_date: date | None
    reported_eps: Decimal | None
    estimated_eps: Decimal | None
    surprise: Decimal | None
    surprise_pct: Decimal | None
    report_time: str | None


@dataclass(frozen=True)
class Estimate:
    """One estimate row as the provider gave it on the day it was read."""

    period_end: date
    horizon: str  # HORIZON_QUARTER | HORIZON_YEAR
    eps_avg: Decimal | None
    eps_high: Decimal | None
    eps_low: Decimal | None
    eps_analyst_count: int | None
    revenue_avg: Decimal | None
    revenue_high: Decimal | None
    revenue_low: Decimal | None
    revenue_analyst_count: int | None
    eps_avg_7d_ago: Decimal | None
    eps_avg_30d_ago: Decimal | None
    eps_avg_60d_ago: Decimal | None
    eps_avg_90d_ago: Decimal | None
    eps_revisions_up_30d: int | None
    eps_revisions_down_30d: int | None


def parse_earnings(payload: Any) -> list[ReportedQuarter]:
    """``EARNINGS`` → reported quarters, newest first. Raises on a refusal."""
    reason = refusal_reason(payload)
    if reason:
        raise AlphaVantageError(reason)
    rows = payload.get("quarterlyEarnings")
    if not isinstance(rows, list):
        raise AlphaVantageError("unreadable_response", "no quarterlyEarnings list")
    out: list[ReportedQuarter] = []
    seen: set[date] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        end = _date(row.get("fiscalDateEnding"))
        if end is None or end in seen:
            continue
        seen.add(end)
        time = str(row.get("reportTime") or "").strip().lower() or None
        out.append(ReportedQuarter(
            period_end=end, reported_date=_date(row.get("reportedDate")),
            reported_eps=_dec(row.get("reportedEPS")),
            estimated_eps=_dec(row.get("estimatedEPS")),
            surprise=_dec(row.get("surprise")),
            surprise_pct=_dec(row.get("surprisePercentage")),
            report_time=time if time in {"pre-market", "post-market"} else None))
    return sorted(out, key=lambda r: r.period_end, reverse=True)


def parse_estimates(payload: Any) -> list[Estimate]:
    """``EARNINGS_ESTIMATES`` → quarter and year estimates. Raises on a refusal.

    Rows with another horizon, an unreadable date or no EPS and no revenue figure are
    dropped; a duplicated (horizon, date) keeps the first row.
    """
    reason = refusal_reason(payload)
    if reason:
        raise AlphaVantageError(reason)
    rows = payload.get("estimates")
    if not isinstance(rows, list):
        raise AlphaVantageError("unreadable_response", "no estimates list")
    out: list[Estimate] = []
    seen: set[tuple[str, date]] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        horizon = str(row.get("horizon") or "").strip().lower()
        end = _date(row.get("date"))
        if horizon not in {HORIZON_QUARTER, HORIZON_YEAR} or end is None:
            continue
        if (horizon, end) in seen:
            continue
        est = Estimate(
            period_end=end, horizon=horizon,
            eps_avg=_dec(row.get("eps_estimate_average")),
            eps_high=_dec(row.get("eps_estimate_high")),
            eps_low=_dec(row.get("eps_estimate_low")),
            eps_analyst_count=_count(row.get("eps_estimate_analyst_count")),
            revenue_avg=_dec(row.get("revenue_estimate_average")),
            revenue_high=_dec(row.get("revenue_estimate_high")),
            revenue_low=_dec(row.get("revenue_estimate_low")),
            revenue_analyst_count=_count(row.get("revenue_estimate_analyst_count")),
            eps_avg_7d_ago=_dec(row.get("eps_estimate_average_7_days_ago")),
            eps_avg_30d_ago=_dec(row.get("eps_estimate_average_30_days_ago")),
            eps_avg_60d_ago=_dec(row.get("eps_estimate_average_60_days_ago")),
            eps_avg_90d_ago=_dec(row.get("eps_estimate_average_90_days_ago")),
            eps_revisions_up_30d=_count(row.get("eps_estimate_revision_up_trailing_30_days")),
            eps_revisions_down_30d=_count(
                row.get("eps_estimate_revision_down_trailing_30_days")))
        if est.eps_avg is None and est.revenue_avg is None:
            continue
        seen.add((horizon, end))
        out.append(est)
    return out


async def fetch_function(function: str, symbol: str, *, api_key: str,
                         fetch: Fetch | None = None) -> Any:
    """One call. Raises ``AlphaVantageError`` (key-free reason) on any failure."""
    if not api_key:
        raise AlphaVantageError("no_api_key")
    if not valid_symbol(symbol):
        raise AlphaVantageError("invalid_symbol")
    get = fetch or _default_fetch
    try:
        status, body = await get(request_url(function, symbol, api_key), "application/json")
    except Exception as exc:  # noqa: BLE001 - the exception text may carry the URL + key
        raise AlphaVantageError("vendor_unreachable", type(exc).__name__) from None
    if status != 200 or not body:
        raise AlphaVantageError("vendor_http_error", f"HTTP {status}")
    try:
        return json.loads(body)
    except ValueError:
        raise AlphaVantageError("unreadable_response", "not JSON") from None


async def _default_fetch(url: str, accept: str) -> tuple[int, bytes | None]:  # pragma: no cover
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent

    async with httpx.AsyncClient(timeout=20.0, follow_redirects=False) as client:
        resp = await client.get(
            url, headers={USER_AGENT_HEADER: research_user_agent(), "Accept": accept})
    if resp.status_code != 200:
        return resp.status_code, None
    return 200, resp.content[:2_000_000]


__all__ = [
    "BASE_URL",
    "DAILY_CALL_BUDGET",
    "FUNCTION_EARNINGS",
    "FUNCTION_ESTIMATES",
    "HORIZON_QUARTER",
    "HORIZON_YEAR",
    "VENDOR",
    "AlphaVantageError",
    "Estimate",
    "ReportedQuarter",
    "fetch_function",
    "parse_earnings",
    "parse_estimates",
    "public_url",
    "refusal_reason",
    "request_url",
    "valid_symbol",
]
