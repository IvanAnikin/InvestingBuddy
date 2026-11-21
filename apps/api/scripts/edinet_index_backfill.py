"""Fill the EDINET filing index for the disclosure window (V3.20 slice 11.2).

EDINET lists filings by day only, so an issuer's annual or half-year report is found by
reading the days of the lookback window into ``edinet_filings``. A research run reads
at most ``V3_EDINET_INDEX_DAYS_PER_RUN`` missing days; this OPERATOR job reads the whole
window once (one request per day, newest first, paced), after which a run reads only the
days since.

Needs ``EDINET_API_KEY`` (sent as a request header, never printed) and the database.

    python scripts/edinet_index_backfill.py --dry-run
    python scripts/edinet_index_backfill.py --max-days 200

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
    parser.add_argument("--max-days", type=int, default=600,
                        help="days read in this invocation (default: the whole window)")
    parser.add_argument("--lookback-days", type=int, default=None,
                        help="window length (default: V3_DISCLOSURE_LOOKBACK_DAYS)")
    parser.add_argument("--pause", type=float, default=1.0,
                        help="seconds between day requests")
    parser.add_argument("--dry-run", action="store_true",
                        help="report the window and the days still unread; no request")
    args = parser.parse_args(argv)

    from datetime import datetime, timezone

    from app.services.sources.disclosures.edinet import (
        _days_held,
        api_key,
        refresh_index,
        window_days,
    )

    lookback = args.lookback_days or int(settings.v3_disclosure_lookback_days or 560)
    async with async_session_factory() as session:
        days = window_days(datetime.now(timezone.utc), lookback)
        held = await _days_held(session, days)
        unread = sum(1 for d in days if not (d in held and held[d].final))
        if args.dry_run:
            print(json.dumps({"window_days": len(days), "held_final": len(days) - unread,
                              "to_read": unread, "key_set": bool(api_key(settings))}))
            return 0
        if not api_key(settings):
            print("refused: EDINET_API_KEY is not set", file=sys.stderr)
            return 2
        result = await refresh_index(session, cfg=settings, max_days=args.max_days,
                                     lookback_days=lookback, pause=args.pause)
        await session.commit()
    print(json.dumps({"days_read": result.days_read, "days_failed": result.days_failed,
                      "filings_seen": result.filings_seen,
                      "errors": sorted(set(result.errors))[:5]}))
    return 0 if not result.days_failed else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
