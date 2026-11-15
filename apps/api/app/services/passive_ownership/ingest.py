"""The N-PORT passive-ownership ingest — V3.20 slice 3.2 (spec §3.3).

An OFFLINE job (``scripts/ingest_nport.py``), never a research step: one quarter's data
set is ~440 MB. It:

1. builds the target set from the entity master (``targets_from_db``): every security's
   ISIN (a US / CA ISIN also yields its CUSIP — characters 3–11) and every issuer LEI of
   an entity a company is linked to;
2. downloads the N-PORT data set and the N-CEN data sets (``download``; the research
   User-Agent, streamed to disk, size-capped) — or reads files already on disk;
3. aggregates (``sources.nport_dataset``) off the event loop;
4. upserts ``funds`` and ``security_passive_ownership`` and records the ingest in
   ``fund_holdings_ingests``. A period already ``complete`` is skipped unless forced:
   the job is idempotent per (source, period).

The raw ZIP is not kept by this module: its size is beyond the corpus artifact store's
per-object budget, and the DERA URL is stable and cited on every row.
"""

from __future__ import annotations

import asyncio
import uuid
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.services.sources.nport_dataset import (
    FundType,
    Target,
    Targets,
    aggregate_nport,
    fund_types,
)

NPORT_URL = "https://www.sec.gov/files/dera/data/form-n-port-data-sets/{period}_nport.zip"
NCEN_URL = "https://www.sec.gov/files/dera/data/form-n-cen-data-sets/{period}_ncen.zip"
MAX_ZIP_BYTES = 1_200_000_000
_CHUNK = 1 << 20


def dataset_url(kind: str, period: str) -> str:
    """``("nport", "2026Q3")`` → the DERA URL (lower-case ``2026q3``)."""
    template = {"nport": NPORT_URL, "ncen": NCEN_URL}[kind]
    return template.format(period=period.lower())


def previous_periods(period: str, count: int) -> list[str]:
    """``2026Q3``, 4 → ``["2026Q3", "2026Q2", "2026Q1", "2025Q4"]``."""
    year, quarter = int(period[:4]), int(period[-1])
    out = []
    for _ in range(count):
        out.append(f"{year}Q{quarter}")
        quarter -= 1
        if quarter == 0:
            year, quarter = year - 1, 4
    return out


async def download(url: str, dest: Path) -> int:  # pragma: no cover - network
    """Stream ``url`` to ``dest``; the byte count. Raises on HTTP errors or the cap."""
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent

    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_suffix(dest.suffix + ".part")
    written = 0
    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, read=300.0)) as client:
        async with client.stream("GET", url, headers={
                USER_AGENT_HEADER: research_user_agent(), "Accept": "application/zip"}) as r:
            r.raise_for_status()
            with partial.open("wb") as out:
                async for chunk in r.aiter_bytes(_CHUNK):
                    written += len(chunk)
                    if written > MAX_ZIP_BYTES:
                        raise ValueError(f"data set exceeds {MAX_ZIP_BYTES} bytes")
                    out.write(chunk)
    partial.replace(dest)
    return written


def _cusip_of_isin(isin: str) -> str | None:
    return isin[2:11] if isin[:2] in ("US", "CA") and len(isin) == 12 else None


async def targets_from_db(session: Any) -> Targets:
    """Every security ISIN, and the LEI of every entity a company is linked to."""
    from sqlalchemy import select

    from app.models.company import Company
    from app.models.legal_entity import EntityIdentifier

    items: list[Target] = []
    rows = await session.execute(
        select(EntityIdentifier.security_id, EntityIdentifier.value_normalized).where(
            EntityIdentifier.scheme == "isin", EntityIdentifier.security_id.is_not(None)))
    for security_id, isin in rows.all():
        items.append(Target(key=f"security:{security_id}", isin=isin,
                            cusip=_cusip_of_isin(isin or "")))
    linked = select(Company.legal_entity_id).where(Company.legal_entity_id.is_not(None))
    rows = await session.execute(
        select(EntityIdentifier.legal_entity_id, EntityIdentifier.value_normalized).where(
            EntityIdentifier.scheme == "lei", EntityIdentifier.legal_entity_id.in_(linked)))
    for entity_id, lei in rows.all():
        items.append(Target(key=f"entity:{entity_id}", lei=lei))
    return Targets(items)


@dataclass
class IngestResult:
    state: str
    period: str
    funds_written: int = 0
    securities_written: int = 0
    holdings_read: int = 0
    holdings_matched: int = 0
    filings_kept: int = 0
    filings_superseded: int = 0
    targets: int = 0
    fund_types_known: int = 0
    error: str | None = None
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _read(nport_path: Path, ncen_paths: list[Path], targets: Targets, period: str) -> Any:
    types: dict[str, FundType] = {}
    for path in ncen_paths:  # oldest first: a newer N-CEN filing overrides
        with zipfile.ZipFile(path) as zf:
            types.update(fund_types(zf))
    with zipfile.ZipFile(nport_path) as zf:
        return aggregate_nport(zf, targets, period=period, types=types), types


async def ingest_period(
    session: Any, *, period: str, nport_path: Path, ncen_paths: list[Path],
    targets: Targets, force: bool = False,
) -> IngestResult:
    """Aggregate one N-PORT data set into the tables. Commits nothing; flushes."""
    from sqlalchemy import select

    from app.models.passive_ownership import (
        INGEST_COMPLETE,
        INGEST_FAILED,
        INGEST_RUNNING,
        SOURCE_NPORT,
        Fund,
        FundHoldingsIngest,
        SecurityPassiveOwnership,
    )

    result = IngestResult(state="complete", period=period, targets=len(targets.primary))
    ingest = (await session.execute(select(FundHoldingsIngest).where(
        FundHoldingsIngest.source_kind == SOURCE_NPORT, FundHoldingsIngest.period == period,
    ))).scalar_one_or_none()
    if ingest is not None and ingest.status == INGEST_COMPLETE and not force:
        result.state = "already_ingested"
        return result
    if not targets:
        result.state = "no_targets"
        result.notes.append("no security ISIN or issuer LEI is known to the entity master")
        return result
    now = datetime.now(timezone.utc)
    if ingest is None:
        ingest = FundHoldingsIngest(id=uuid.uuid4(), source_kind=SOURCE_NPORT, period=period,
                                    status=INGEST_RUNNING)
        session.add(ingest)
    ingest.status, ingest.started_at, ingest.error = INGEST_RUNNING, now, None
    ingest.source_url = dataset_url("nport", period)
    await session.flush()
    try:
        data, types = await asyncio.to_thread(_read, nport_path, ncen_paths, targets, period)
    except Exception as exc:  # noqa: BLE001 - recorded on the ingest row
        ingest.status, ingest.finished_at = INGEST_FAILED, datetime.now(timezone.utc)
        ingest.error = f"{type(exc).__name__}: {str(exc)[:300]}"
        await session.flush()
        return IngestResult(state="failed", period=period, error=ingest.error)

    held_series = {top["series_id"] for sec in data.securities for top in sec["top_funds"]}
    for series_id in held_series:
        info = data.funds.get(series_id)
        if info is None:
            continue
        fund = (await session.execute(select(Fund).where(Fund.series_id == series_id))
                ).scalar_one_or_none()
        if fund is None:
            fund = Fund(id=uuid.uuid4(), series_id=series_id)
            session.add(fund)
        fund.name = info.name or fund.name
        fund.series_lei = info.series_lei or fund.series_lei
        fund.registrant_cik = info.registrant_cik or fund.registrant_cik
        fund.registrant_name = info.registrant_name or fund.registrant_name
        if info.report_date and (fund.net_assets_as_of is None
                                 or info.report_date >= fund.net_assets_as_of):
            fund.net_assets, fund.net_assets_as_of = info.net_assets, info.report_date
        kind = types.get(series_id)
        if kind is not None:
            fund.is_etf, fund.is_index = kind.is_etf, kind.is_index
            fund.fund_type_source, fund.fund_type_as_of = "ncen", kind.as_of
        result.funds_written += 1
    url = dataset_url("nport", period)
    for sec in data.securities:
        row = (await session.execute(select(SecurityPassiveOwnership).where(
            SecurityPassiveOwnership.identifier_kind == sec["identifier_kind"],
            SecurityPassiveOwnership.identifier == sec["identifier"],
            SecurityPassiveOwnership.period == period,
            SecurityPassiveOwnership.source_kind == SOURCE_NPORT,
        ))).scalar_one_or_none()
        if row is None:
            row = SecurityPassiveOwnership(
                id=uuid.uuid4(), identifier_kind=sec["identifier_kind"],
                identifier=sec["identifier"], period=period, source_kind=SOURCE_NPORT)
            session.add(row)
        for key in ("issuer_name", "issuer_lei", "cusip", "isin", "fund_count", "etf_count",
                    "etf_unknown_count", "index_fund_count", "shares_held",
                    "etf_shares_held", "value_usd", "non_share_holdings",
                    "report_date_min", "report_date_max"):
            setattr(row, key, sec[key])
        row.top_funds_json = sec["top_funds"]
        row.source_url = url
        result.securities_written += 1
    result.holdings_read, result.holdings_matched = data.holdings_read, data.holdings_matched
    result.filings_kept, result.filings_superseded = data.filings_kept, data.filings_superseded
    result.fund_types_known = len(types)
    ingest.status, ingest.finished_at = INGEST_COMPLETE, datetime.now(timezone.utc)
    ingest.funds_written, ingest.securities_written = (result.funds_written,
                                                       result.securities_written)
    ingest.holdings_read = result.holdings_read
    ingest.detail_json = {k: v for k, v in result.to_dict().items()
                          if k not in ("state", "period", "error")}
    await session.flush()
    return result


__all__ = [
    "IngestResult",
    "MAX_ZIP_BYTES",
    "dataset_url",
    "download",
    "ingest_period",
    "previous_periods",
    "targets_from_db",
]
