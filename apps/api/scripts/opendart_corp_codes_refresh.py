"""Hold OpenDART's corp-code list (V3.20 slice 11.3).

OpenDART identifies a filer by its eight-digit corp code, and the only map from a KRX
stock code to it is ``corpCode.xml``: a 3.6 MB zip, measured on 2026-11-17 to download
at about 7 KB/s (499 s). This OPERATOR job reads it once and replaces the held listed
rows in ``opendart_corp_codes`` (migration 060). Research runs then read the table and
download the list themselves only when nothing fresh is held
(``V3_OPENDART_CORP_CODES_MAX_AGE_DAYS``).

Needs ``OPENDART_API_KEY`` (a query parameter of the request only; never printed) and
the database.

    python scripts/opendart_corp_codes_refresh.py --dry-run
    python scripts/opendart_corp_codes_refresh.py

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
    parser.add_argument("--timeout", type=float, default=900.0,
                        help="seconds allowed for the download (default 900)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what is held; no request")
    args = parser.parse_args(argv)

    from datetime import datetime, timezone

    import sqlalchemy as sa

    from app.models.opendart_corp_code import OpendartCorpCode
    from app.services.sources.disclosures.opendart import (
        api_key,
        download_corp_codes,
        store_corp_codes,
    )

    async with async_session_factory() as session:
        held, refreshed = (await session.execute(sa.select(
            sa.func.count(), sa.func.max(OpendartCorpCode.refreshed_at)))).one()
        if args.dry_run:
            print(json.dumps({"held": held, "refreshed_at": refreshed and refreshed.isoformat(),
                              "key_set": bool(api_key(settings))}))
            return 0
        if not api_key(settings):
            print("refused: OPENDART_API_KEY is not set", file=sys.stderr)
            return 2
        rows = await download_corp_codes(settings, timeout_seconds=args.timeout)
        if not rows:
            print(json.dumps({"stored": 0, "held_before": held,
                              "error": "the corp-code list could not be read"}))
            return 1
        stored = await store_corp_codes(session, rows, now=datetime.now(timezone.utc))
        await session.commit()
    print(json.dumps({"stored": stored, "held_before": held}))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
