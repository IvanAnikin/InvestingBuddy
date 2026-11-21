"""Link every ``companies`` row to the entity master (V3.2 slice 2.2, run by an operator).

``backfill_entities_from_companies`` is deliberately called by nothing in ``app/``: a
backfill that starts itself after a deploy is how a migration becomes an outage. This
script is its operator entry point. Until it runs, ``companies.legal_entity_id`` is NULL
everywhere, so every step that needs a legal entity reads ``entity_unresolved``:
relationships, GLEIF parents, Exhibit 21 / 13D-G, customer concentration, named
counterparties, earnings actuals and passive ownership (V3.20).

Found 2026-11-21 on the deployed environment: ``V3_ENTITY_MASTER_ENABLED`` on, 121
companies, 0 legal entities.

    python scripts/backfill_entity_master.py --dry-run   # counts only, rolled back
    python scripts/backfill_entity_master.py             # link, one commit per batch

Resumable (only unlinked rows are selected), idempotent (a second run links nothing) and
bounded (``--batch`` rows a pass, at most ``--max-batches`` passes). A row whose derived
key belongs to an entity with a different name is left UNLINKED and listed, never
merged. The entity master flag must be on; with it off the backfill writes nothing.
Prints counts and the ambiguous rows' public ticker / exchange / name only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))


async def run(factory: Any, cfg: Any, *, dry_run: bool, batch: int,
              max_batches: int) -> dict[str, Any]:
    """Run bounded passes until a pass does not fill its limit. Returns the totals."""
    import sqlalchemy as sa

    from app.models.company import Company
    from app.services.entities.backfill import backfill_entities_from_companies

    totals: dict[str, Any] = {"dry_run": dry_run, "passes": 0, "examined": 0, "linked": 0,
                              "reused": 0, "ambiguous": 0, "skipped": 0,
                              "ambiguous_rows": []}
    async with factory() as session:
        unlinked = sa.select(sa.func.count()).select_from(Company).where(
            Company.legal_entity_id.is_(None))
        totals["unlinked_before"] = (await session.execute(unlinked)).scalar_one()
        for _ in range(max(1, max_batches)):
            result = await backfill_entities_from_companies(session, cfg=cfg, limit=batch)
            totals["passes"] += 1
            for key in ("examined", "linked", "reused", "ambiguous", "skipped"):
                totals[key] += getattr(result, key)
            totals["ambiguous_rows"].extend(list(row) for row in result.ambiguous_rows)
            if not dry_run:
                await session.commit()
            # In a dry run nothing is committed, so the same rows would be selected
            # again: one pass is the whole answer. An all-ambiguous pass cannot shrink
            # the unlinked set either.
            if dry_run or not result.remaining_possible or result.linked == 0:
                break
        totals["unlinked_after"] = (await session.execute(unlinked)).scalar_one()
        if dry_run:
            await session.rollback()
    return totals


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="run one pass and roll it back; print what it would link")
    parser.add_argument("--batch", type=int, default=200, help="rows per pass (default 200)")
    parser.add_argument("--max-batches", type=int, default=20,
                        help="at most this many passes (default 20)")
    args = parser.parse_args(argv)

    from app.core.config import settings
    from app.db.session import async_session_factory

    if not getattr(settings, "v3_entity_master_enabled", False):
        print("refused: V3_ENTITY_MASTER_ENABLED is off", file=sys.stderr)
        return 2
    totals = await run(async_session_factory, settings, dry_run=args.dry_run,
                       batch=max(1, args.batch), max_batches=args.max_batches)
    print(json.dumps(totals, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
