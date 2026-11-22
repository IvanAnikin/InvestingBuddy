"""Refresh the research coverage index from Edison Group's sitemap (V3.21 slice 21.7).

The weekly periodic task ``research_coverage_refresh`` does this on the durable worker;
this script is the operator's manual run (first fill after migration 064, or a check).

    python scripts/refresh_research_coverage.py             # robots → sitemap → upsert
    python scripts/refresh_research_coverage.py --dry-run   # fetch + parse, write nothing

One robots.txt GET and one sitemap GET on www.edisongroup.com (host pinned). Fails closed
on a robots refusal, a truncated file or a layout with no company page. Prints counts
only; never a URL list. Redeye, Research Tree and mwb research are never fetched here:
they are reached only by a host-restricted web search from a report.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.db.session import async_session_factory  # noqa: E402
from app.services.research_coverage.refresh_job import TASK_KEY, run_refresh  # noqa: E402
from app.services.research_coverage.sitemap import refresh_edison_index  # noqa: E402


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch and parse, print counts, write nothing")
    args = parser.parse_args(argv)
    if args.dry_run:
        async with async_session_factory() as session:
            result = await refresh_edison_index(session, dry_run=True)
            await session.rollback()
    else:
        # Writes the counts to the periodic task's source_poll_state.detail_json too.
        result = await run_refresh(task_key=TASK_KEY)
    print(json.dumps(result.to_dict(), indent=2))
    return 0 if result.ok else 1


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
