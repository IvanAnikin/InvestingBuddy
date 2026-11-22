"""Poll the electricity trade-press feeds once (V3.21.6, ADR-113).

An OPERATOR run of what the 6-hourly ``trade_press_poll`` periodic task does: robots.txt
checked, one conditional, host-pinned, bounded GET per feed, guarded parse, excerpt-only
theme documents (title + 600-character feed summary + link), mention links.

    python scripts/poll_trade_press.py --list
    python scripts/poll_trade_press.py --dry-run            # fetch + parse, write nothing
    python scripts/poll_trade_press.py --feed utility_dive  # one feed
    python scripts/poll_trade_press.py                      # every feed, stored

Refused unless ``V3_TRADE_PRESS_FEEDS_ENABLED``. Prints counts only, never item text.
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
from app.services.trade_press.feeds import FEEDS, feed  # noqa: E402
from app.services.trade_press.poller import poll_all  # noqa: E402


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--list", action="store_true", help="list the feeds and exit")
    parser.add_argument("--feed", choices=sorted(f.key for f in FEEDS),
                        help="poll one feed only")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and parse, write nothing (no validators sent)")
    args = parser.parse_args(argv)
    if args.list:
        for f in FEEDS:
            print(f"{f.key:24} {f.language}  {','.join(f.layers):60} {f.url}")
        return 0
    if not settings.v3_trade_press_feeds_enabled:
        print("V3_TRADE_PRESS_FEEDS_ENABLED is off; nothing polled.", file=sys.stderr)
        return 2
    feeds = (feed(args.feed),) if args.feed else FEEDS
    async with async_session_factory() as session:
        summary = await poll_all(session, cfg=settings,
                                 feeds=tuple(f for f in feeds if f is not None),
                                 dry_run=args.dry_run)
    print(json.dumps(summary.to_dict(), indent=2))
    return 1 if summary.failed else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
