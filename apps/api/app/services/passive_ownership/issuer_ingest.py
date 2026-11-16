"""Ingest issuer (ETF sponsor) holdings files — V3.20 slice 3.5 (owner decision D8).

For each curated fund: fetch its file (or read it from disk), parse it with the
sponsor's adapter (which fails closed on any layout it was not written against), keep
only holdings of the entity master's securities, and write one
``security_passive_ownership`` row per matched security with ``source_kind =
issuer_file`` and ``period`` = the newest file date. ``top_funds_json`` lists each fund
with its own file date and domicile (US / UCITS), so UCITS funds — which N-PORT never
sees — are visible. Every file gets a ``fund_holdings_ingests`` row
(``source_kind = issuer_file:<sponsor>:<ticker>``, ``period`` = its date, or the run
date on failure) with the failure reason when it was refused.

Matching: CUSIP (SPDR) or ISIN (UCITS files) against the N-PORT target set; a holding
with neither matches by ticker only when the file names a US exchange and that ticker
belongs to exactly one open US listing of a researched security.

Private use, own risk: switch off or license before any public launch.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any, Awaitable, Callable

from app.services.sources.issuer_holdings import ADAPTERS, CURATED_FUNDS_VERSION
from app.services.sources.issuer_holdings.base import (
    DOMICILE_UCITS,
    FundSpec,
    HoldingsFile,
    IssuerHolding,
    LayoutError,
)
from app.services.sources.nport_dataset import Targets

Fetch = Callable[[str, str], Awaitable[tuple[int, bytes | None]]]
MAX_FILE_BYTES = 20_000_000
_US_MICS = {"XNAS", "XNYS", "XASE", "ARCX", "BATS", "XNGS", "XNMS", "XNCM"}
_US_EXCHANGE_WORDS = ("NASDAQ", "NEW YORK STOCK EXCHANGE", "NYSE", "CBOE", "BATS")


def _us_exchange(text: str | None) -> bool:
    upper = (text or "").upper()
    return any(word in upper for word in _US_EXCHANGE_WORDS)


async def ticker_targets(session: Any, targets: Targets) -> dict[str, str]:
    """US ticker → ``security:<id>`` for open US listings of a security with a target."""
    from sqlalchemy import select

    from app.integrations.exchange_source_registry import normalize_exchange
    from app.models.legal_entity import SecurityListing

    rows = (await session.execute(select(
        SecurityListing.ticker, SecurityListing.security_id, SecurityListing.mic,
        SecurityListing.exchange_code).where(SecurityListing.effective_to.is_(None)))).all()
    seen: dict[str, set[str]] = {}
    for ticker, security_id, mic, code in rows:
        us = (mic or "").upper() in _US_MICS or normalize_exchange(code) in {
            "NASDAQ", "NYSE", "AMEX"}
        key = f"security:{security_id}"
        if us and ticker and key in targets.primary:
            seen.setdefault(ticker.strip().upper(), set()).add(key)
    return {t: next(iter(keys)) for t, keys in seen.items() if len(keys) == 1}


def match(h: IssuerHolding, targets: Targets, tickers: dict[str, str]) -> tuple[str, str] | None:
    """``(target key, basis)`` or None."""
    if h.cusip and h.cusip in targets.by["cusip"]:
        return targets.by["cusip"][h.cusip], "cusip"
    if h.isin and h.isin in targets.by["isin"]:
        return targets.by["isin"][h.isin], "isin"
    if not h.cusip and not h.isin and h.ticker and _us_exchange(h.exchange):
        key = tickers.get(h.ticker.strip().upper())
        if key:
            return key, "ticker_us_exchange"
    return None


@dataclass
class _Agg:
    kind: str
    value: str
    name: str | None = None
    funds: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class IssuerIngestResult:
    period: str | None = None
    files_ok: int = 0
    files_failed: dict[str, str] = field(default_factory=dict)
    holdings_read: int = 0
    holdings_matched: int = 0
    securities_written: int = 0
    by_basis: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {"period": self.period, "files_ok": self.files_ok,
                "files_failed": dict(self.files_failed), "holdings_read": self.holdings_read,
                "holdings_matched": self.holdings_matched,
                "securities_written": self.securities_written, "by_basis": dict(self.by_basis),
                "curated_funds_version": CURATED_FUNDS_VERSION}


def parse_file(fund: FundSpec, body: bytes | None) -> HoldingsFile:
    adapter = ADAPTERS.get(fund.sponsor)
    if adapter is None:
        raise LayoutError("no_adapter", fund.sponsor)
    if not body:
        raise LayoutError("empty_response")
    if len(body) > MAX_FILE_BYTES:
        raise LayoutError("file_too_large")
    return adapter.parse(body, fund)


async def _log(session: Any, *, fund: FundSpec, period: str, url: str, status: str,
               read: int = 0, matched: int = 0, error: str | None = None,
               layout: str | None = None) -> None:
    from sqlalchemy import select

    from app.models.passive_ownership import FundHoldingsIngest

    kind = f"issuer_file:{fund.sponsor}:{fund.ticker}"[:30]
    row = (await session.execute(select(FundHoldingsIngest).where(
        FundHoldingsIngest.source_kind == kind,
        FundHoldingsIngest.period == period))).scalar_one_or_none()
    now = datetime.now(timezone.utc)
    if row is None:
        row = FundHoldingsIngest(id=uuid.uuid4(), source_kind=kind, period=period,
                                 status=status, started_at=now)
        session.add(row)
    row.status, row.source_url, row.finished_at = status, url, now
    row.holdings_read, row.error = read, error
    row.detail_json = {"sponsor": fund.sponsor, "fund": fund.ticker, "domicile": fund.domicile,
                       "layout": layout, "matched": matched,
                       "curated_funds_version": CURATED_FUNDS_VERSION,
                       "use": "private_use_own_risk"}


async def ingest_files(session: Any, files: list[tuple[FundSpec, str, bytes | None]], *,
                       targets: Targets, tickers: dict[str, str],
                       run_date: date | None = None) -> IssuerIngestResult:
    """Parse and store a batch of files ``(fund, url, body)``. Never raises on a file."""
    from sqlalchemy import select

    from app.models.passive_ownership import (
        INGEST_COMPLETE,
        INGEST_FAILED,
        SOURCE_ISSUER_FILE,
        SecurityPassiveOwnership,
    )

    result = IssuerIngestResult()
    today = (run_date or datetime.now(timezone.utc).date()).isoformat()
    aggs: dict[str, _Agg] = {}
    dates: list[date] = []
    for fund, url, body in files:
        try:
            parsed = parse_file(fund, body)
        except LayoutError as exc:
            result.files_failed[fund.ticker] = exc.reason
            await _log(session, fund=fund, period=today, url=url, status=INGEST_FAILED,
                       error=str(exc)[:500])
            continue
        result.files_ok += 1
        dates.append(parsed.as_of)
        matched = 0
        per_target: dict[str, dict[str, Any]] = {}
        for h in parsed.holdings:
            result.holdings_read += 1
            hit = match(h, targets, tickers)
            if hit is None:
                continue
            key, basis = hit
            matched += 1
            result.by_basis[basis] = result.by_basis.get(basis, 0) + 1
            agg = aggs.get(key)
            if agg is None:
                agg = aggs[key] = _Agg(*targets.primary[key])
            agg.name = agg.name or h.name
            entry = per_target.get(key)
            if entry is None:  # one fund = one holder, lots summed
                entry = per_target[key] = {
                    "fund_ticker": fund.ticker, "name": parsed.fund_name or fund.name,
                    "sponsor": fund.sponsor, "domicile": fund.domicile, "is_etf": True,
                    "as_of": parsed.as_of.isoformat(), "shares": Decimal(0),
                    "weight_pct": Decimal(0), "match_basis": basis, "source_url": url}
                agg.funds.append(entry)
            entry["shares"] += h.shares or Decimal(0)
            entry["weight_pct"] += h.weight_pct or Decimal(0)
        result.holdings_matched += matched
        await _log(session, fund=fund, period=parsed.as_of.isoformat(), url=url,
                   status=INGEST_COMPLETE, read=len(parsed.holdings), matched=matched,
                   layout=parsed.layout)
    if not aggs:
        await session.flush()
        return result
    period = max(dates).isoformat()
    result.period = period
    for agg in aggs.values():
        row = (await session.execute(select(SecurityPassiveOwnership).where(
            SecurityPassiveOwnership.identifier_kind == agg.kind,
            SecurityPassiveOwnership.identifier == agg.value,
            SecurityPassiveOwnership.period == period,
            SecurityPassiveOwnership.source_kind == SOURCE_ISSUER_FILE))).scalar_one_or_none()
        if row is None:
            row = SecurityPassiveOwnership(id=uuid.uuid4(), identifier_kind=agg.kind,
                                           identifier=agg.value, period=period,
                                           source_kind=SOURCE_ISSUER_FILE)
            session.add(row)
        funds = sorted(agg.funds, key=lambda f: f["shares"], reverse=True)
        file_dates = [date.fromisoformat(f["as_of"]) for f in funds]
        row.issuer_name = agg.name
        if agg.kind == "isin":
            row.isin = agg.value
        if agg.kind == "cusip":
            row.cusip = agg.value
        row.fund_count = row.etf_count = len(funds)
        row.etf_unknown_count = row.index_fund_count = 0
        row.shares_held = row.etf_shares_held = sum((f["shares"] for f in funds), Decimal(0))
        row.non_share_holdings = 0
        row.report_date_min, row.report_date_max = min(file_dates), max(file_dates)
        row.top_funds_json = [{**f, "shares": float(f["shares"]),
                               "weight_pct": float(f["weight_pct"])} for f in funds]
        row.source_url = funds[0]["source_url"]
        result.securities_written += 1
    await session.flush()
    return result


async def fetch_curated(
    funds: list[FundSpec], fetch: Fetch
) -> list[tuple[FundSpec, str, bytes | None]]:
    """Download each fund's file; a failed download is recorded as an empty body."""
    out: list[tuple[FundSpec, str, bytes | None]] = []
    for fund in funds:
        url = ADAPTERS[fund.sponsor].url_for(fund)
        try:
            status, body = await fetch(url, "*/*")
        except Exception:  # noqa: BLE001 - recorded as empty_response
            status, body = 0, None
        out.append((fund, url, body if status == 200 else None))
    return out


__all__ = [
    "DOMICILE_UCITS",
    "IssuerIngestResult",
    "fetch_curated",
    "ingest_files",
    "match",
    "parse_file",
    "ticker_targets",
]
