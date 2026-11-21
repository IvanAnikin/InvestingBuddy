"""Hold TWSE's material announcements (V3.20 slice 11.4).

TWSE's open-data feed ``t187ap04_L`` (上市公司每日重大訊息) states ONE announcement day:
the next day's read replaces it. This OPERATOR job reads it once and holds every row
not yet held in ``twse_material_announcements`` (migration 061); research runs read the
held rows. Run it once a day (after 08:00 Asia/Taipei) for history to accumulate — a
day it does not run, and no research run reads the feed, is a hole.

Open Government Data License v1.0: attribution required (``twse_material.ATTRIBUTION``).
No key. Needs the database.

    python scripts/twse_material_refresh.py --dry-run
    python scripts/twse_material_refresh.py

Prints counts only.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is held; no request")
    args = parser.parse_args(argv)

    import sqlalchemy as sa

    from app.models.twse_material_announcement import TwseMaterialAnnouncement
    from app.services.sources.disclosures.twse_material import is_enabled, refresh_feed

    async with async_session_factory() as session:
        held, latest = (await session.execute(sa.select(
            sa.func.count(), sa.func.max(TwseMaterialAnnouncement.announced_at)))).one()
        if args.dry_run:
            print(json.dumps({"held": held, "latest": latest and latest.isoformat(),
                              "enabled": is_enabled(settings)}))
            return 0
        if not is_enabled(settings):
            print("refused: V3_TWSE_MATERIAL_INFO_ENABLED or V3_DIRECTORY_TWSE_ENABLED is off",
                  file=sys.stderr)
            return 2
        outcome = await refresh_feed(session, cfg=settings, force=True)
        await session.commit()
    print(json.dumps({**outcome, "held_before": held}))
    return 0 if outcome["read"] and not outcome["error"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
