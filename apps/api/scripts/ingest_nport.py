"""Ingest one quarter of SEC Form N-PORT holdings into the passive-ownership tables.

V3.20 slice 3.2 (spec §3.3). An OFFLINE operator job, never a research step.

    # files already on disk (nothing is downloaded):
    python scripts/ingest_nport.py --period 2026Q3 \
        --nport-zip /data/2026q3_nport.zip --ncen-zip /data/2026q3_ncen.zip ...

    # download from the SEC (≈350–700 MB for N-PORT, ≈6 MB per N-CEN quarter):
    python scripts/ingest_nport.py --period 2026Q3 --download --data-dir /data/nport

Without ``--download`` nothing is fetched; a missing file is an error. ``--dry-run``
aggregates and prints the counts, then rolls the transaction back. A period already
ingested is skipped unless ``--force``. The target set is the entity master (security
ISINs, derived CUSIPs, linked issuer LEIs): holdings of anything else are not kept.

N-CEN (which funds are ETFs / index funds) is annual per fund, so the four N-CEN
quarters ending at ``--period`` are read by default (``--ncen-quarters``).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db.session import async_session_factory  # noqa: E402
from app.services.passive_ownership.ingest import (  # noqa: E402
    dataset_url,
    download,
    ingest_period,
    previous_periods,
    targets_from_db,
)
from app.services.sources.nport_dataset import dataset_period  # noqa: E402


def _args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--period", required=True, help="data-set filing quarter, e.g. 2026Q3")
    p.add_argument("--nport-zip", type=pathlib.Path)
    p.add_argument("--ncen-zip", type=pathlib.Path, action="append", default=[])
    p.add_argument("--ncen-quarters", type=int, default=4)
    p.add_argument("--download", action="store_true",
                   help="fetch missing data sets from sec.gov into --data-dir")
    p.add_argument("--data-dir", type=pathlib.Path, default=pathlib.Path("data/nport"))
    p.add_argument("--force", action="store_true", help="re-ingest a complete period")
    p.add_argument("--dry-run", action="store_true", help="aggregate, report, roll back")
    return p.parse_args(argv)


async def _ensure(kind: str, period: str, given: pathlib.Path | None,
                  args: argparse.Namespace) -> pathlib.Path:
    path = given or args.data_dir / f"{period.lower()}_{kind}.zip"
    if path.exists():
        return path
    if not args.download:
        raise SystemExit(f"{path} is missing; pass --download to fetch "
                         f"{dataset_url(kind, period)}")
    size = await download(dataset_url(kind, period), path)
    print(f"downloaded {dataset_url(kind, period)} -> {path} ({size:,} bytes)")
    return path


async def main(argv: list[str] | None = None) -> int:
    args = _args(argv)
    period = dataset_period(args.period)
    if period is None:
        raise SystemExit(f"--period {args.period!r} is not a quarter like 2026Q3")
    nport = await _ensure("nport", period, args.nport_zip, args)
    ncen = list(args.ncen_zip)
    if not ncen:
        # Oldest first: a newer N-CEN filing overrides an older one.
        for quarter in reversed(previous_periods(period, args.ncen_quarters)):
            ncen.append(await _ensure("ncen", quarter, None, args))
    async with async_session_factory() as session:
        targets = await targets_from_db(session)
        result = await ingest_period(session, period=period, nport_path=nport,
                                     ncen_paths=ncen, targets=targets, force=args.force)
        if args.dry_run:
            await session.rollback()
        else:
            await session.commit()
    print(json.dumps(result.to_dict() | {"dry_run": args.dry_run}, indent=2, default=str))
    return 0 if result.state in ("complete", "already_ingested") else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
