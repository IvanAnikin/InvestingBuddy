"""Take today's Alpha Vantage consensus snapshot for researched US-listed companies.

V3.20 slice 4.2 (ADR-097). An OPERATOR job: research runs take a snapshot too, but an
expectation only counts when it was read BEFORE the results (``is_pre_report``), so a
company that is not re-researched in the weeks before its report needs this job.

    python scripts/snapshot_consensus.py                 # every company with earnings_results
    python scripts/snapshot_consensus.py --company-id <uuid> --company-id <uuid>
    python scripts/snapshot_consensus.py --dry-run       # list who would be called

Each company costs two calls; the shared budget (``vendor_api_calls``, 25 a day on the
free tier) stops the job when it is used up. Companies already snapshotted today are
skipped without a call. Needs ``ALPHA_VANTAGE_API_KEY``; without it nothing is called.
Companies are ordered by the oldest snapshot first, so a daily run rotates through them.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import uuid

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402
from app.services.pipeline.consensus_step import (  # noqa: E402
    consensus_snapshot_step,
    us_listed_symbol,
)


async def _companies(session, ids: list[uuid.UUID]) -> list:  # noqa: ANN001
    from sqlalchemy import func, select

    from app.models.company import Company
    from app.models.consensus import ConsensusSnapshot
    from app.models.earnings import EarningsResult

    if ids:
        rows = (await session.execute(select(Company).where(Company.id.in_(ids)))).scalars()
        return [c for c in rows if us_listed_symbol(c)]
    last = (select(ConsensusSnapshot.company_id, func.max(ConsensusSnapshot.as_of).label("last"))
            .group_by(ConsensusSnapshot.company_id).subquery())
    stmt = (select(Company).where(Company.id.in_(select(EarningsResult.company_id)))
            .outerjoin(last, last.c.company_id == Company.id)
            .order_by(last.c.last.asc().nulls_first(), Company.ticker))
    return [c for c in (await session.execute(stmt)).scalars() if us_listed_symbol(c)]


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--company-id", action="append", type=uuid.UUID, default=[])
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)
    if not settings.alpha_vantage_api_key:
        print("ALPHA_VANTAGE_API_KEY is not set; nothing is called.")
        return 2
    async with async_session_factory() as session:
        companies = await _companies(session, args.company_id)
        if args.dry_run:
            for c in companies:
                print(c.id, us_listed_symbol(c))
            return 0
        for company in companies:
            out = await consensus_snapshot_step(session, company, cfg=settings)
            await session.commit()
            print(json.dumps({"company_id": str(company.id), **out}, default=str))
            if out.get("state") == "budget_exhausted" or out.get("reason") == "vendor_rate_limited":
                break
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
