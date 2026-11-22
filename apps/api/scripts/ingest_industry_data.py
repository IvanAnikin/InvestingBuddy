"""Refresh live industry data (Ember, IRENA, EIA) into the macro store — V3.21.4 (ADR-111).

The same refresh the WEEKLY ``industry_data_refresh`` periodic task runs, on demand. Each
source is gated by its own flag; EIA is inert without ``EIA_API_KEY``. Downloads go
through the platform's guarded fetcher, pinned to each publisher's host. Nothing printed
carries a URL with a key.

    python scripts/ingest_industry_data.py --list
    python scripts/ingest_industry_data.py                      # every enabled source
    python scripts/ingest_industry_data.py --source ember --dry-run
    python scripts/ingest_industry_data.py --source irena --countries DEU,JPN
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
from app.services.industry_data.common import (  # noqa: E402
    SOURCE_EIA,
    SOURCE_EMBER,
    SOURCE_FLAGS,
    SOURCE_IRENA,
    configured_countries,
)
from app.services.industry_data.refresh import refresh_industry_data  # noqa: E402

ALIASES = {"ember": SOURCE_EMBER, "irena": SOURCE_IRENA, "eia": SOURCE_EIA}


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n")[0])
    parser.add_argument("--source", choices=sorted(ALIASES), action="append",
                        help="repeatable; default every source")
    parser.add_argument("--countries", help="comma-separated ISO-3 codes; default "
                        "V3_INDUSTRY_DATA_COUNTRIES")
    parser.add_argument("--dry-run", action="store_true",
                        help="fetch, parse and write, then roll back")
    parser.add_argument("--list", action="store_true")
    args = parser.parse_args(argv)
    if args.list:
        for alias, source_id in sorted(ALIASES.items()):
            on = getattr(settings, SOURCE_FLAGS[source_id], False)
            keyed = "" if source_id != SOURCE_EIA else (
                " (key set)" if settings.eia_api_key else " (no EIA_API_KEY: inert)")
            print(f"{alias:6} {source_id:18} {'ON ' if on else 'off'}{keyed}")
        print("countries:", ",".join(configured_countries(settings)))
        return 0
    sources = [ALIASES[s] for s in args.source] if args.source else None
    countries = ([c.strip().upper() for c in args.countries.split(",") if c.strip()]
                 if args.countries else None)
    outcomes = await refresh_industry_data(
        async_session_factory, cfg=settings, sources=sources, countries=countries,
        dry_run=args.dry_run)
    for outcome in outcomes:
        print(json.dumps(outcome.to_dict(), default=str))
    return 1 if any(o.error for o in outcomes) else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
