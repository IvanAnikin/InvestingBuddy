"""Ingest ETF sponsors' daily holdings files for the curated fund list (V3.20 slice 3.5).

PRIVATE USE, OWN RISK (owner decision D8): switch off or license before a public launch.

    # files already on disk (nothing is downloaded), one --file per fund:
    python scripts/ingest_issuer_holdings.py --file XLU=/data/xlu.xlsx --file XLI=/data/xli.xlsx

    # download every curated fund's file (or only --fund XLU --fund ICLN):
    python scripts/ingest_issuer_holdings.py --download [--fund XLU]

    python scripts/ingest_issuer_holdings.py --list        # the curated list and its version

    # the daily refresh (schedule once a day): only funds without a successful file today
    python scripts/ingest_issuer_holdings.py --download --due

Without ``--download`` nothing is fetched. ``--dry-run`` parses and prints, then rolls
back. Each adapter fails closed on a layout it was not written against; the failure is
recorded in ``fund_holdings_ingests`` and the other files still load.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db.session import async_session_factory  # noqa: E402
from app.services.passive_ownership.ingest import targets_from_db  # noqa: E402
from app.services.passive_ownership.issuer_ingest import (  # noqa: E402
    done_today,
    fetch_curated,
    ingest_files,
    ticker_targets,
)
from app.services.sources.issuer_holdings import (  # noqa: E402
    ADAPTERS,
    CURATED_FUNDS,
    CURATED_FUNDS_VERSION,
    fund,
)


async def _fetch(url: str, accept: str) -> tuple[int, bytes | None]:  # pragma: no cover
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent

    # No redirects: a sponsor URL that moves is a recorded failure, never another host.
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=False) as client:
        resp = await client.get(url, headers={USER_AGENT_HEADER: research_user_agent(),
                                              "Accept": accept})
    return resp.status_code, resp.content if resp.status_code == 200 else None


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--file", action="append", default=[], metavar="TICKER=PATH")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--fund", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--due", action="store_true",
                        help="skip funds whose file was ingested successfully today (UTC)")
    args = parser.parse_args(argv)
    if args.list:
        print(f"curated funds version {CURATED_FUNDS_VERSION}")
        for f in CURATED_FUNDS:
            print(f"{f.ticker:6} {f.sponsor:8} {f.domicile:6} verified={f.format_verified} "
                  f"{ADAPTERS[f.sponsor].url_for(f)}")
        return 0
    files = []
    for spec in args.file:
        ticker, _, path = spec.partition("=")
        f = fund(ticker)
        if f is None or not path:
            print(f"unknown fund or missing path: {spec}")
            return 2
        files.append((f, f"file://{pathlib.Path(path).name}", pathlib.Path(path).read_bytes()))
    if args.download:
        chosen = [f for f in CURATED_FUNDS if not args.fund or f.ticker in
                  {t.upper() for t in args.fund}]
        if args.due:
            async with async_session_factory() as session:
                done = await done_today(session)
            chosen = [f for f in chosen if f"issuer_file:{f.sponsor}:{f.ticker}" not in done]
        files.extend(await fetch_curated(chosen, _fetch))
    if not files:
        print("nothing to ingest: pass --file TICKER=PATH or --download")
        return 2
    async with async_session_factory() as session:
        targets = await targets_from_db(session)
        tickers = await ticker_targets(session, targets)
        result = await ingest_files(session, files, targets=targets, tickers=tickers)
        print(json.dumps(result.to_dict(), default=str))
        if args.dry_run:
            await session.rollback()
        else:
            await session.commit()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
