"""Chart data for a report's company (V3.21 investor PDF export).

Two read-only blocks, fetched concurrently and cached in-process for 12 hours per
(ticker, exchange) so repeated PDF downloads do not re-hit the sources:

* ``price`` — EODHD end-of-day closes (the free plan may return about a year only;
  whatever comes back is used). The API key is a query parameter the provider adds
  itself; the URL returned here is built without it and stripped of any credential.
* ``financials`` — the issuer's own SEC XBRL 10-K facts (US filers only): revenue,
  net income and operating income per fiscal year, as filed, in USD units.

Never invents a number: a missing block is ``unavailable`` with a reason, a missing
line item is ``null``. Logs one short line per request (ticker, states, elapsed) and
never the key or a payload.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from typing import Any

from app.integrations.financial_data_provider import ProviderStatus
from app.integrations.providers.eodhd_price_only_provider import EodhdPriceOnlyProvider
from app.integrations.providers.eodhd_provider import EodhdNotFoundError
from app.integrations.providers.sec_edgar_fundamentals import (
    SecEdgarFundamentalsProvider,
    SecExchangeNotSupportedError,
)
from app.schemas.report_market_data import (
    FinancialRow,
    FinancialsBlock,
    MarketDataSubject,
    PriceBlock,
    PricePointOut,
)
from app.services.discovery.directories import (
    _REVENUE_CONCEPTS,
    SEC_DATA_HOST,
    annual_usd_facts_by_end,
)
from app.services.exchange_registry import (
    normalize_exchange,
    price_quote_currency_for_exchange,
)
from app.services.sources.document_fetcher import safe_fetch_document
from app.services.sources.redaction import strip_url_secrets

logger = logging.getLogger(__name__)

CACHE_TTL_SECONDS = 12 * 3600
_CACHE_MAX_ENTRIES = 256
MAX_PRICE_POINTS = 260
MAX_FISCAL_YEARS = 5
PRICE_LOOKBACK_DAYS = 5 * 365
#: Per-call bounds; the whole request stays far below the 300 s gunicorn timeout.
PRICE_TIMEOUT_SECONDS = 35.0
CIK_TIMEOUT_SECONDS = 20.0
SEC_FETCH_DEADLINE_SECONDS = 20.0
FINANCIALS_TIMEOUT_SECONDS = 75.0

_NET_INCOME_CONCEPT = "NetIncomeLoss"
_OPERATING_INCOME_CONCEPT = "OperatingIncomeLoss"

#: (TICKER, EXCHANGE) -> (monotonic stored-at, payload)
_CACHE: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}


def reset_cache() -> None:
    _CACHE.clear()


def _text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def resolve_subject(report: Any, company: Any = None) -> MarketDataSubject:
    """The report's subject: the professional-research subject, then the company row."""
    summary = getattr(report, "source_summary_json", None)
    subject: dict[str, Any] = {}
    if isinstance(summary, dict):
        v3 = summary.get("v3_research")
        pr = v3.get("professional_research") if isinstance(v3, dict) else None
        raw = pr.get("subject") if isinstance(pr, dict) else None
        if isinstance(raw, dict):
            subject = raw
    return MarketDataSubject(
        name=_text(subject.get("name")) or _text(getattr(company, "name", None)),
        ticker=_text(subject.get("ticker")) or _text(getattr(company, "ticker", None)),
        exchange=_text(subject.get("exchange")) or _text(getattr(company, "exchange", None)),
    )


def downsample(
    points: list[tuple[str, float]], limit: int = MAX_PRICE_POINTS
) -> list[tuple[str, float]]:
    """At most ``limit`` evenly spaced points; the first and the last are kept exactly."""
    n = len(points)
    if n <= limit:
        return points
    picks = sorted({round(i * (n - 1) / (limit - 1)) for i in range(limit)})
    return [points[i] for i in picks]


def _price_unavailable(reason: str, currency: str | None) -> PriceBlock:
    return PriceBlock(state="unavailable", reason=reason, currency=currency)  # type: ignore[arg-type]


async def _price_block(ticker: str, exchange: str | None, today: date) -> PriceBlock:
    currency = price_quote_currency_for_exchange(exchange)
    provider = EodhdPriceOnlyProvider()
    if provider.get_provider_status() != ProviderStatus.ok:
        return _price_unavailable("no_api_key", currency)
    try:
        history = await asyncio.wait_for(
            provider.get_price_history(
                ticker, exchange,
                start_date=(today - timedelta(days=PRICE_LOOKBACK_DAYS)).isoformat(),
                end_date=today.isoformat(),
            ),
            timeout=PRICE_TIMEOUT_SECONDS,
        )
    except EodhdNotFoundError:
        return _price_unavailable("unknown_symbol", currency)
    except Exception:  # noqa: BLE001 - an unreadable price history is an absent one
        return _price_unavailable("fetch_failed", currency)
    # The chart uses the split- and dividend-adjusted close: an unadjusted series shows
    # a stock split as a crash (POWL, March 2026). The last close stays the traded one.
    by_date: dict[str, float] = {}
    raw_by_date: dict[str, float] = {}
    adjusted = True
    for point in history.price_points:
        close = point.close
        if not (isinstance(close, (int, float)) and close > 0):
            continue
        d = str(point.date)[:10]
        raw_by_date[d] = float(close)
        adj = point.adjusted_close
        if isinstance(adj, (int, float)) and adj > 0:
            by_date[d] = float(adj)
        else:
            adjusted = False
            by_date[d] = float(close)
    if not adjusted:
        # A partly adjusted series would mix two bases; fall back to raw closes.
        by_date = dict(raw_by_date)
    series = sorted(by_date.items())
    if not series:
        return _price_unavailable("no_prices", currency)
    series = downsample(series)
    first, last = series[0][1], series[-1][1]
    return PriceBlock(
        state="ok",
        currency=history.currency or currency,
        # The provider's URL is key-free; strip any credential parameter regardless.
        source_url=strip_url_secrets(str(history.source_url).split("?", 1)[0]),
        source_tier=provider.source_tier.value,
        points=[PricePointOut(d=d, c=c) for d, c in series],
        first_date=series[0][0],
        last_date=series[-1][0],
        last_close=raw_by_date.get(series[-1][0], last),
        change_pct=round((last / first - 1) * 100, 2),
        adjusted=adjusted,
    )


def _concept_url(cik: str, concept: str) -> str:
    return (f"https://{SEC_DATA_HOST}/api/xbrl/companyconcept/CIK{int(cik):010d}"
            f"/us-gaap/{concept}.json")


async def _read_concept(cik: str, concept: str) -> tuple[bool, dict[str, Any] | None]:
    """(fetched ok, payload) for one companyconcept JSON. Never raises."""
    try:
        result = await safe_fetch_document(
            _concept_url(cik, concept), allowed_domains=(SEC_DATA_HOST,), resolve_ip=True,
            extra_text_content_types=("application/json",),
            total_deadline_seconds=SEC_FETCH_DEADLINE_SECONDS,
        )
    except Exception:  # noqa: BLE001 - an unreadable concept is an absent figure
        return False, None
    if getattr(result, "status_code", None) == 404:
        return True, None  # the issuer never tagged this concept
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        return False, None
    try:
        payload = json.loads(content)
    except (TypeError, ValueError):
        return False, None
    return True, payload if isinstance(payload, dict) else None


async def _revenue_facts(cik: str) -> tuple[bool, str | None, dict[str, dict]]:
    """(any read ok, concept URL used, facts by end) for the first revenue concept with facts."""
    any_ok = False
    for concept in _REVENUE_CONCEPTS:
        ok, payload = await _read_concept(cik, concept)
        any_ok = any_ok or ok
        facts = annual_usd_facts_by_end(payload) if payload else {}
        if facts:
            return True, _concept_url(cik, concept), facts
    return any_ok, None, {}


async def _signed_facts(cik: str, concept: str) -> dict[str, dict]:
    _, payload = await _read_concept(cik, concept)
    return annual_usd_facts_by_end(payload, positive_only=False) if payload else {}


async def _resolve_cik(ticker: str, exchange: str | None) -> str:
    return await SecEdgarFundamentalsProvider().resolve_cik(ticker, exchange)


async def _financials_block(
    ticker: str, exchange: str | None
) -> tuple[str | None, FinancialsBlock]:
    try:
        cik = await asyncio.wait_for(_resolve_cik(ticker, exchange), timeout=CIK_TIMEOUT_SECONDS)
    except SecExchangeNotSupportedError:
        return None, FinancialsBlock(state="unavailable", reason="not_a_us_filer")
    except ValueError:
        return None, FinancialsBlock(state="unavailable", reason="no_cik")
    except Exception:  # noqa: BLE001 - the ticker index could not be read
        return None, FinancialsBlock(state="unavailable", reason="fetch_failed")
    if not cik or not str(cik).isdigit():
        return None, FinancialsBlock(state="unavailable", reason="no_cik")
    cik = str(int(cik))

    (any_ok, revenue_url, revenue), net_income, operating = await asyncio.gather(
        _revenue_facts(cik),
        _signed_facts(cik, _NET_INCOME_CONCEPT),
        _signed_facts(cik, _OPERATING_INCOME_CONCEPT),
    )
    if not revenue:
        return cik, FinancialsBlock(
            state="unavailable", reason="no_annual_facts" if any_ok else "fetch_failed")
    ni_url = _concept_url(cik, _NET_INCOME_CONCEPT)
    oi_url = _concept_url(cik, _OPERATING_INCOME_CONCEPT)
    rows: list[FinancialRow] = []
    for end in sorted(revenue)[-MAX_FISCAL_YEARS:]:
        fact = revenue[end]
        ni, oi = net_income.get(end), operating.get(end)
        urls = [revenue_url] if revenue_url else []
        urls += [ni_url] if ni else []
        urls += [oi_url] if oi else []
        rows.append(FinancialRow(
            fiscal_year_end=end,
            revenue=fact["_val"],
            net_income=ni["_val"] if ni else None,
            operating_income=oi["_val"] if oi else None,
            form=_text(fact.get("form")),
            filed=_text(fact.get("filed")),
            source_urls=urls,
        ))
    return cik, FinancialsBlock(state="ok", rows=rows)


async def _bounded_financials(
    ticker: str, exchange: str | None
) -> tuple[str | None, FinancialsBlock]:
    try:
        return await asyncio.wait_for(
            _financials_block(ticker, exchange), timeout=FINANCIALS_TIMEOUT_SECONDS)
    except Exception:  # noqa: BLE001 - a timed-out read is an absent figure
        return None, FinancialsBlock(state="unavailable", reason="fetch_failed")


def _cache_key(ticker: str, exchange: str | None) -> tuple[str, str]:
    return ticker.strip().upper(), normalize_exchange(exchange)


async def market_data_for(
    ticker: str | None, exchange: str | None, *, today: date | None = None
) -> dict[str, Any]:
    """``{"retrieved_at", "cik", "price", "financials"}`` for one listing (cached 12 h)."""
    if not ticker:
        return {
            "retrieved_at": datetime.now(timezone.utc), "cik": None,
            "price": _price_unavailable("no_ticker", price_quote_currency_for_exchange(exchange)),
            "financials": FinancialsBlock(state="unavailable", reason="no_ticker"),
        }
    key = _cache_key(ticker, exchange)
    started = time.monotonic()
    hit = _CACHE.get(key)
    if hit is not None and started - hit[0] < CACHE_TTL_SECONDS:
        payload = hit[1]
        logger.info("report_market_data ticker=%s price=%s financials=%s cached=true",
                    key[0], payload["price"].state, payload["financials"].state)
        return payload

    now = today or datetime.now(timezone.utc).date()
    price, (cik, financials) = await asyncio.gather(
        _price_block(ticker, exchange, now), _bounded_financials(ticker, exchange))
    payload = {"retrieved_at": datetime.now(timezone.utc), "cik": cik,
               "price": price, "financials": financials}
    # A transient failure is not cached: the next download retries it.
    if "fetch_failed" not in (price.reason, financials.reason):
        if len(_CACHE) >= _CACHE_MAX_ENTRIES:
            _CACHE.pop(min(_CACHE, key=lambda k: _CACHE[k][0]))
        _CACHE[key] = (time.monotonic(), payload)
    logger.info("report_market_data ticker=%s price=%s financials=%s cached=false elapsed_ms=%d",
                key[0], price.state, financials.state, (time.monotonic() - started) * 1000)
    return payload
