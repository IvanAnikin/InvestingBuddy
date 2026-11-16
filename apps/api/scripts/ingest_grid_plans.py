"""Ingest a published grid plan into ``grid_capex_plans`` (V3.20 Phase 7).

An OPERATOR job: nothing schedules it. Each source is gated by its own flag, set from
its licence (see ``app/services/grid_plans/base.py``); a source whose flag is off is
refused before anything is read or stored.

    python scripts/ingest_grid_plans.py --list
    # a file already on disk (nothing is downloaded):
    python scripts/ingest_grid_plans.py --source entsoe_tyndp --file /data/tyndp.xlsx
    # the source's pinned download URL (no redirects; research User-Agent):
    python scripts/ingest_grid_plans.py --source entsoe_tyndp --download
    # --edition 2026-02-03 overrides the edition read from the file name;
    # --dry-run parses and prints, then rolls back.
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
from app.services.grid_plans.base import SOURCES  # noqa: E402
from app.services.grid_plans.store import SourceDisabled, ingest_file, source  # noqa: E402

MAX_BYTES = 60_000_000


async def _download(url: str) -> bytes:  # pragma: no cover - network
    import httpx

    from app.core.outbound_identity import USER_AGENT_HEADER, research_user_agent

    async with httpx.AsyncClient(timeout=120.0, follow_redirects=False) as client:
        resp = await client.get(url, headers={USER_AGENT_HEADER: research_user_agent()})
    if resp.status_code != 200:
        raise RuntimeError(f"HTTP {resp.status_code} from the pinned URL")
    if len(resp.content) > MAX_BYTES:
        raise RuntimeError("file larger than the bound")
    return resp.content


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--source", choices=sorted(SOURCES))
    parser.add_argument("--file")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--edition")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        for src in SOURCES.values():
            on = getattr(settings, src.flag, False)
            print(f"{src.key:16} {'ON ' if on else 'off'} {src.use_constraint:24} {src.name}\n"
                  f"{'':16} {src.licence}")
        return 0
    if not args.source or bool(args.file) == bool(args.download):
        print("pass --source and exactly one of --file PATH / --download")
        return 2
    try:
        src = source(args.source, settings)
    except SourceDisabled as exc:
        print(f"refused: {exc}")
        return 3
    if args.file:
        path = pathlib.Path(args.file)
        body, url = path.read_bytes(), src.download_url
    else:
        body, url = await _download(src.download_url), src.download_url
    async with async_session_factory() as session:
        ingest = await ingest_file(session, key=args.source, body=body, cfg=settings,
                                   source_url=url, edition=args.edition)
        print(json.dumps({"ingest_id": str(ingest.id), "status": ingest.status,
                          "edition": ingest.plan_edition, "rows_read": ingest.rows_read,
                          "rows_written": ingest.rows_written, "error": ingest.error,
                          "detail": ingest.detail_json}, default=str))
        if args.dry_run:
            await session.rollback()
        else:
            await session.commit()
    return 0 if ingest.status == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
