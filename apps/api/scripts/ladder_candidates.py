"""Walk the supply chain upstream from a company and print the value-chain query plan.

V3.20 slice 6.1. Read-only: no network call, nothing written.

    python scripts/ladder_candidates.py --ticker POWL [--hops 3]
    python scripts/ladder_candidates.py --driver data_centre_load [--bottleneck goes]
    python scripts/ladder_candidates.py --list
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services.discovery.laddering import MAX_HOPS, ladder, query_plan  # noqa: E402
from app.services.electricity.drivers import (  # noqa: E402
    BOTTLENECKS,
    CATALOGUE_VERSION,
    DRIVERS,
)


async def _ladder(ticker: str, hops: int) -> dict[str, object]:  # pragma: no cover
    from sqlalchemy import select

    from app.db.session import async_session_factory
    from app.models.company import Company
    from app.models.legal_entity import LegalEntity

    async with async_session_factory() as session:
        company = (await session.execute(
            select(Company).where(Company.ticker == ticker.upper()).limit(1))).scalar()
        if company is None or company.legal_entity_id is None:
            return {"error": f"{ticker}: no company with a legal entity"}
        out = await ladder(session, company.legal_entity_id, max_hops=hops)
        ids = [c["entity_id"] for c in out["candidates"]]  # type: ignore[index]
        if ids:
            names = dict((await session.execute(
                select(LegalEntity.id, LegalEntity.legal_name).where(
                    LegalEntity.id.in_(ids)))).all())
            for c in out["candidates"]:  # type: ignore[union-attr]
                c["legal_name"] = next((v for k, v in names.items()
                                        if str(k) == c["entity_id"]), None)
        return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--ticker")
    parser.add_argument("--hops", type=int, default=MAX_HOPS)
    parser.add_argument("--driver")
    parser.add_argument("--bottleneck", action="append")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        print(f"catalogue {CATALOGUE_VERSION}")
        for d in DRIVERS:
            print(f"driver {d.key:28} -> {', '.join(d.bottlenecks)}")
        for b in BOTTLENECKS:
            print(f"bottleneck {b.key:24} {b.layer}/{b.sub_segment}")
        return 0
    if args.driver:
        plan = query_plan(args.driver, bottleneck_keys=args.bottleneck)
        if plan is None:
            print(f"unknown driver {args.driver!r}")
            return 2
        print(json.dumps(plan, indent=2))
        return 0
    if args.ticker:
        print(json.dumps(asyncio.run(_ladder(args.ticker, args.hops)), indent=2, default=str))
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
