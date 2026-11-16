"""SEC Form N-PORT / N-CEN quarterly data sets → per-security passive ownership —
V3.20 slice 3.2 (spec §3.3). Pure: reads an opened ZIP (``zipfile.ZipFile``), never the
network; the ingest job (``services/passive_ownership/ingest.py``) owns download and
storage. Table and column names: ``docs/v3.20-passive-ownership-spike.md``.

WHY A TARGET SET
================
One quarter is ~5 M holdings rows (390–690 MB zipped). A full aggregation of every
security held by every fund needs a holding→ISIN map of that size, which an App Service
B1 (1.75 GB) cannot hold. The job therefore aggregates the securities the platform
researches: a target set of ISINs, CUSIPs and issuer LEIs. A holding matches by its own
CUSIP, its issuer LEI, or an ISIN in the ``IDENTIFIERS`` table; one pass per table.

THE RULES
=========
* **One filing per (series, report date):** an NPORT-P/A replaces the original; the
  later filing date wins. ``NT NPORT-P`` (a notice of late filing) carries no holdings.
* **Long positions only** (``PAYOFF_PROFILE`` ``Long`` or empty); a short is not
  ownership.
* **Shares are summed only in shares** (``UNIT = 'NS'``). Any other unit (principal,
  contracts) is counted as ``non_share_holdings`` and never added.
* **The latest report per series:** a series files up to three quarter-end months per
  data set only when its fiscal quarter ends move it; when one series appears with two
  report dates, its newest holding counts (a fund is not two holders).
* **ETF:** from N-CEN ``FUND_REPORTED_INFO.IS_ETF`` / ``IS_ETMF`` by series id, the
  newest N-CEN filing winning. A series with no N-CEN row is ``None`` (unknown) — the
  N-CEN data sets omit schema-3.1 filings — and is counted as such, never as "not an ETF".
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any

TOP_FUNDS = 20
_ISIN_RE = re.compile(r"^[A-Z]{2}[A-Z0-9]{9}\d$")
_CUSIP_RE = re.compile(r"^[A-Z0-9]{8}\d$")
_LEI_RE = re.compile(r"^[A-Z0-9]{18}\d{2}$")
_PERIOD_RE = re.compile(r"(\d{4})q([1-4])", re.IGNORECASE)

csv.field_size_limit(10_000_000)


def normalise_identifier(kind: str, value: str | None) -> str | None:
    raw = re.sub(r"\s+", "", (value or "")).upper()
    pattern = {"isin": _ISIN_RE, "cusip": _CUSIP_RE, "lei": _LEI_RE}[kind]
    return raw if pattern.match(raw) else None


def dataset_period(name: str) -> str | None:
    """``2026q3_nport.zip`` → ``2026Q3``."""
    match = _PERIOD_RE.search(name or "")
    return f"{match.group(1)}Q{match.group(2)}" if match else None


@dataclass(frozen=True)
class Target:
    """One security the platform researches, by every identifier it is known by. An
    ISIN or CUSIP names the SECURITY; an LEI names the ISSUER (ADR-061), so an LEI-only
    target aggregates every security of that issuer the funds report."""

    key: str
    isin: str | None = None
    cusip: str | None = None
    lei: str | None = None


class Targets:
    """Identifier → target key, and each target's primary identifier (ISIN, else CUSIP,
    else LEI). Identifiers that fail their check digit shape are dropped."""

    def __init__(self, items: Iterable[Target]) -> None:
        self.by: dict[str, dict[str, str]] = {"isin": {}, "cusip": {}, "lei": {}}
        self.primary: dict[str, tuple[str, str]] = {}
        for item in items:
            for kind in ("isin", "cusip", "lei"):
                value = normalise_identifier(kind, getattr(item, kind))
                if value:
                    self.by[kind].setdefault(value, item.key)
                    self.primary.setdefault(item.key, (kind, value))

    @property
    def isins(self) -> dict[str, str]:
        return self.by["isin"]

    def __bool__(self) -> bool:
        return bool(self.primary)


@dataclass(frozen=True)
class FundType:
    is_etf: bool | None
    is_index: bool | None
    as_of: date | None


@dataclass
class FundInfo:
    series_id: str
    name: str | None
    series_lei: str | None
    registrant_cik: str | None
    registrant_name: str | None
    net_assets: Decimal | None
    report_date: date | None
    accession: str


@dataclass
class _Holding:
    series_id: str
    report_date: date | None
    shares: Decimal | None
    value_usd: Decimal | None
    pct_of_fund: Decimal | None
    non_share: bool


@dataclass
class SecurityAggregate:
    """What the data set says about one target security."""

    identifier_kind: str
    identifier: str
    issuer_name: str | None = None
    issuer_lei: str | None = None
    cusip: str | None = None
    isin: str | None = None
    holdings: dict[str, _Holding] = field(default_factory=dict)
    target_key: str | None = None

    def finish(self, funds: dict[str, FundInfo], types: dict[str, FundType]) -> dict[str, Any]:
        rows = list(self.holdings.values())
        dates = [h.report_date for h in rows if h.report_date]
        etf = [h for h in rows if (types.get(h.series_id) or FundType(None, None, None)).is_etf]
        unknown = [h for h in rows if (types.get(h.series_id) is None
                                       or types[h.series_id].is_etf is None)]
        index = [h for h in rows if (types.get(h.series_id) or FundType(None, None, None)).is_index]

        def total(items: list[_Holding], attr: str) -> Decimal | None:
            values = [getattr(h, attr) for h in items if getattr(h, attr) is not None]
            return sum(values, Decimal(0)) if values else None

        # Share holdings first, by shares; a principal-amount or contract balance is not
        # comparable with a share count, so those come last whatever their size.
        top = sorted(rows, key=lambda h: (not h.non_share, h.shares or Decimal(0)),
                     reverse=True)[:TOP_FUNDS]
        return {
            "target_key": self.target_key,
            "identifier_kind": self.identifier_kind, "identifier": self.identifier,
            "issuer_name": self.issuer_name, "issuer_lei": self.issuer_lei,
            "cusip": self.cusip, "isin": self.isin,
            "fund_count": len(rows), "etf_count": len(etf), "etf_unknown_count": len(unknown),
            "index_fund_count": len(index),
            "shares_held": total([h for h in rows if not h.non_share], "shares"),
            "etf_shares_held": total([h for h in etf if not h.non_share], "shares"),
            "value_usd": total(rows, "value_usd"),
            "non_share_holdings": sum(1 for h in rows if h.non_share),
            "report_date_min": min(dates) if dates else None,
            "report_date_max": max(dates) if dates else None,
            "top_funds": [
                {
                    "series_id": h.series_id,
                    "name": _fund_attr(funds, h.series_id, "name"),
                    "sponsor": _fund_attr(funds, h.series_id, "registrant_name"),
                    "unit": "shares" if not h.non_share else "other",
                    "is_etf": (types.get(h.series_id) or FundType(None, None, None)).is_etf,
                    "shares": _num(h.shares), "value_usd": _num(h.value_usd),
                    "pct_of_fund": _num(h.pct_of_fund),
                    "report_date": h.report_date.isoformat() if h.report_date else None,
                }
                for h in top
            ],
        }


def _fund_attr(funds: dict[str, FundInfo], series_id: str, attr: str) -> str | None:
    info = funds.get(series_id)
    return getattr(info, attr) if info is not None else None


def _num(value: Decimal | None) -> float | None:
    return float(value) if value is not None else None


def _dec(value: str | None) -> Decimal | None:
    try:
        return Decimal(value) if value else None
    except (InvalidOperation, ValueError):
        return None


def _date(value: str | None) -> date | None:
    text = (value or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%m/%d/%Y", "%d-%b-%y"):
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def _member(zf: Any, table: str) -> str | None:
    want = f"{table.lower()}.tsv"
    for name in zf.namelist():
        if name.rsplit("/", 1)[-1].lower() == want:
            return name
    return None


def iter_table(zf: Any, table: str) -> Iterator[dict[str, str]]:
    """Rows of one TSV member as dicts; nothing when the member is absent."""
    name = _member(zf, table)
    if name is None:
        return
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        yield from csv.DictReader(text, delimiter="\t", quoting=csv.QUOTE_NONE)


def iter_columns(zf: Any, table: str, columns: tuple[str, ...]) -> Iterator[tuple[str, ...]]:
    """Only ``columns`` of each row, as a tuple ("" when absent) — the fast path for the
    two big tables (FUND_REPORTED_HOLDING ~5.6M rows, IDENTIFIERS ~5M rows a quarter),
    where building a dict per row dominated the run time."""
    name = _member(zf, table)
    if name is None:
        return
    with zf.open(name) as raw:
        text = io.TextIOWrapper(raw, encoding="utf-8", errors="replace", newline="")
        reader = csv.reader(text, delimiter="\t", quoting=csv.QUOTE_NONE)
        header = next(reader, None)
        if header is None:
            return
        index = {col: i for i, col in enumerate(header)}
        picks = [index.get(col) for col in columns]
        for row in reader:
            width = len(row)
            yield tuple(row[i] if i is not None and i < width else "" for i in picks)


def _lookup_key(value: str | None) -> str:
    """Whitespace-free upper case: the cheap per-row key. A hit in a ``Targets`` map is
    valid by construction (targets are validated), so the regex runs only on hits."""
    return "".join(value.split()).upper() if value else ""


def fund_types(ncen_zf: Any) -> dict[str, FundType]:
    """Series id → type, from an N-CEN data set; the newest filing per series wins."""
    filed: dict[str, date | None] = {}
    for row in iter_table(ncen_zf, "SUBMISSION"):
        filed[row.get("ACCESSION_NUMBER", "")] = _date(row.get("FILING_DATE"))
    out: dict[str, FundType] = {}
    for row in iter_table(ncen_zf, "FUND_REPORTED_INFO"):
        series = (row.get("SERIES_ID") or "").strip()
        if not series:
            continue
        as_of = filed.get(row.get("ACCESSION_NUMBER", ""))
        current = out.get(series)
        if current and current.as_of and as_of and current.as_of >= as_of:
            continue
        etf = (row.get("IS_ETF") or "").upper() == "Y" or (row.get("IS_ETMF") or "").upper() == "Y"
        out[series] = FundType(is_etf=etf, is_index=(row.get("IS_INDEX") or "").upper() == "Y",
                               as_of=as_of)
    return out


@dataclass
class DatasetResult:
    period: str | None
    funds: dict[str, FundInfo]
    securities: list[dict[str, Any]]
    holdings_read: int
    holdings_matched: int
    filings_kept: int
    filings_superseded: int


def aggregate_nport(nport_zf: Any, targets: Targets, *, period: str | None,
                    types: dict[str, FundType] | None = None) -> DatasetResult:
    """One pass per table; memory bounded by the number of filings and target matches."""
    types = types or {}
    submissions: dict[str, tuple[date | None, date | None, str]] = {}
    for row in iter_table(nport_zf, "SUBMISSION"):
        sub_type = (row.get("SUB_TYPE") or "").upper()
        if sub_type.startswith("NT"):
            continue
        submissions[row["ACCESSION_NUMBER"]] = (
            _date(row.get("FILING_DATE")), _date(row.get("REPORT_DATE")), sub_type)
    registrants = {
        row["ACCESSION_NUMBER"]: (row.get("CIK"), row.get("REGISTRANT_NAME"))
        for row in iter_table(nport_zf, "REGISTRANT")
    }
    infos: dict[str, FundInfo] = {}
    newest: dict[tuple[str, date | None], tuple[date | None, str]] = {}
    for row in iter_table(nport_zf, "FUND_REPORTED_INFO"):
        accession = row.get("ACCESSION_NUMBER", "")
        series = (row.get("SERIES_ID") or "").strip()
        if accession not in submissions or not series:
            continue
        filed, report_date, _ = submissions[accession]
        cik, registrant = registrants.get(accession, (None, None))
        infos[accession] = FundInfo(
            series_id=series, name=row.get("SERIES_NAME") or None,
            series_lei=row.get("SERIES_LEI") or None, registrant_cik=cik or None,
            registrant_name=registrant or None, net_assets=_dec(row.get("NET_ASSETS")),
            report_date=report_date, accession=accession)
        key = (series, report_date)
        newer = newest.get(key)
        if newer is None or (filed or date.min, accession) > (newer[0] or date.min, newer[1]):
            newest[key] = (filed, accession)
    kept = {accession for _, accession in newest.values()}
    superseded = len(infos) - len(kept)

    isin_of: dict[str, str] = {}
    if targets.isins:
        wanted = targets.isins
        for holding_id, raw_isin in iter_columns(nport_zf, "IDENTIFIERS",
                                                 ("HOLDING_ID", "IDENTIFIER_ISIN")):
            if raw_isin:
                isin = _lookup_key(raw_isin)
                if isin in wanted:
                    isin_of[holding_id] = isin

    aggregates: dict[str, SecurityAggregate] = {}
    read = matched = 0
    by_isin, by_cusip, by_lei = targets.by["isin"], targets.by["cusip"], targets.by["lei"]
    for (accession, holding_id, raw_cusip, raw_lei, issuer_name, balance, unit_raw,
         currency, value, pct, payoff) in iter_columns(nport_zf, "FUND_REPORTED_HOLDING", (
            "ACCESSION_NUMBER", "HOLDING_ID", "ISSUER_CUSIP", "ISSUER_LEI", "ISSUER_NAME",
            "BALANCE", "UNIT", "CURRENCY_CODE", "CURRENCY_VALUE", "PERCENTAGE",
            "PAYOFF_PROFILE")):
        read += 1
        if accession not in kept:
            continue
        isin = isin_of.get(holding_id)
        cusip_key, lei_key = _lookup_key(raw_cusip), _lookup_key(raw_lei)
        target = (by_isin.get(isin or "") or by_cusip.get(cusip_key) or by_lei.get(lei_key))
        if target is None:
            continue
        if payoff.strip().lower() not in ("", "long", "n/a"):
            continue
        cusip = normalise_identifier("cusip", raw_cusip)
        lei = normalise_identifier("lei", raw_lei)
        matched += 1
        info = infos[accession]
        agg = aggregates.get(target)
        if agg is None:
            agg = aggregates[target] = SecurityAggregate(*targets.primary[target])
            agg.target_key = target
        agg.issuer_name = agg.issuer_name or issuer_name or None
        agg.issuer_lei = agg.issuer_lei or lei
        agg.cusip = agg.cusip or cusip
        agg.isin = agg.isin or isin
        unit = unit_raw.strip().upper()
        holding = _Holding(
            series_id=info.series_id, report_date=info.report_date,
            shares=_dec(balance), value_usd=_dec(value)
            if (currency or "USD").strip().upper() == "USD" else None,
            pct_of_fund=_dec(pct), non_share=unit != "NS")
        prior = agg.holdings.get(info.series_id)
        if prior is None or (holding.report_date or date.min) > (prior.report_date or date.min):
            agg.holdings[info.series_id] = holding
        elif prior.report_date == holding.report_date and not holding.non_share:
            # Two lots of one security in one report (share classes, lots): one holder.
            prior.shares = (prior.shares or Decimal(0)) + (holding.shares or Decimal(0))
            if holding.value_usd is not None:
                prior.value_usd = (prior.value_usd or Decimal(0)) + holding.value_usd

    funds = {info.series_id: info for acc, info in infos.items() if acc in kept}
    securities = [agg.finish(funds, types) for agg in aggregates.values()]
    return DatasetResult(period=period, funds=funds, securities=securities,
                         holdings_read=read, holdings_matched=matched,
                         filings_kept=len(kept), filings_superseded=superseded)


__all__ = [
    "DatasetResult",
    "FundInfo",
    "FundType",
    "SecurityAggregate",
    "TOP_FUNDS",
    "Target",
    "Targets",
    "aggregate_nport",
    "dataset_period",
    "fund_types",
    "iter_table",
    "normalise_identifier",
]
