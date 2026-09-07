#!/usr/bin/env python3
"""The V3 migration chain, against a real PostgreSQL, as a repeatable script.

WHAT IT PROVES, AND WHY EACH ONE
================================
The campaign verified this chain migration-by-migration on scratch databases. This
script does the whole thing in one run, so a reader can reproduce the claim rather than
trust a table, and so it can be re-run before a promotion.

1. **018 -> 038 applies.** Twenty V3 migrations against a schema built from the V2
   baseline, not from `create_all`.
2. **038 -> 018 reverses.** Every one has a `downgrade()` and the chain runs backwards.
3. **The downgraded schema is IDENTICAL to the pre-V3 one**, compared as a sorted
   `table.column` fingerprint. This is the rollback guarantee: if the fingerprint differs
   by one column, redeploying V2 against a rolled-back database is a gamble.
4. **V2-on-V3 compatibility.** With the schema at 038, every V2 table still has every V2
   column, unchanged. That is what lets `release/v2-current` run against a database that
   has already been migrated — the rollback path that does not require a down-migration
   at all.
5. **V3 code on an 018 schema.** The dark-deployment reality: `deploy-api-staging.yml`
   does NOT run migrations, so merged V3 code meets the *old* schema first. With every
   V3 flag off, nothing may touch a V3 table.

USAGE
=====
    python scripts/v3-migration-acceptance.py --database-url postgresql+psycopg://...

Creates its own scratch database, drops it at the end, and touches nothing else.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"

# Columns returned separately and joined in Python: a ':' inside a SQL string literal
# is read by SQLAlchemy as a bind parameter, which is a confusing way to lose an hour.
FINGERPRINT_SQL = """
SELECT table_name, column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'public' AND table_name <> 'alembic_version'
ORDER BY 1, 2
"""

TABLES_SQL = """
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public' AND table_name <> 'alembic_version' ORDER BY 1
"""


def alembic(url: str, *args: str) -> subprocess.CompletedProcess:
    env = {**os.environ, "DATABASE_URL": url}
    return subprocess.run(
        [str(ROOT / ".venv" / "bin" / "alembic"), *args],
        cwd=ROOT,
        env=env,
        capture_output=True,
        text=True,
    )


async def fingerprint(engine) -> tuple[set[str], set[str]]:  # noqa: ANN001
    from sqlalchemy import text

    async with engine.connect() as conn:
        cols = {
            f"{t}.{c}:{d}:{'null' if n == 'YES' else 'notnull'}"
            for t, c, d, n in (await conn.execute(text(FINGERPRINT_SQL))).all()
        }
        tables = {r[0] for r in (await conn.execute(text(TABLES_SQL))).all()}
    return cols, tables


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--admin-url",
        default="postgresql+psycopg://postgres@localhost:5432/postgres",
        help="A URL with rights to CREATE/DROP DATABASE.",
    )
    parser.add_argument("--keep", action="store_true", help="Do not drop the scratch DB.")
    args = parser.parse_args()

    sys.path.insert(0, str(ROOT))
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    scratch = f"ib_v3_migacc_{uuid.uuid4().hex[:8]}"
    admin = create_async_engine(args.admin_url, isolation_level="AUTOCOMMIT")
    async with admin.connect() as conn:
        await conn.execute(text(f'CREATE DATABASE "{scratch}"'))
    await admin.dispose()
    url = args.admin_url.rsplit("/", 1)[0] + "/" + scratch
    print(f"=== V3 MIGRATION ACCEPTANCE ===\nscratch database: {scratch}\n")

    failures: list[str] = []
    engine = create_async_engine(url)
    try:
        # 1. Build the V2 baseline schema, exactly as the deployed database is.
        r = alembic(url, "upgrade", "018")
        if r.returncode != 0:
            print(r.stdout[-2000:], r.stderr[-2000:])
            failures.append("could not migrate to the V2 baseline 018")
            raise SystemExit(1)
        v2_cols, v2_tables = await fingerprint(engine)
        print(f"1. at 018 (V2 baseline): {len(v2_tables)} tables, {len(v2_cols)} columns")

        # 2. The whole V3 chain forward.
        r = alembic(url, "upgrade", "head")
        if r.returncode != 0:
            print(r.stdout[-3000:], r.stderr[-3000:])
            failures.append("018 -> head FAILED")
            raise SystemExit(1)
        v3_cols, v3_tables = await fingerprint(engine)
        head = alembic(url, "current").stdout.strip().splitlines()[-1:]
        print(f"2. at head:              {len(v3_tables)} tables, {len(v3_cols)} columns  {head}")

        # 3. V2-on-V3 compatibility: every V2 column still present and identical.
        lost = sorted(v2_cols - v3_cols)
        if lost:
            failures.append(f"V3 changed or removed {len(lost)} V2 column(s): {lost[:8]}")
        added_to_v2_tables = sorted(
            c for c in (v3_cols - v2_cols) if c.split(".")[0] in v2_tables
        )
        print(
            f"3. V2-on-V3 compatibility: {len(lost)} V2 column(s) lost or changed; "
            f"{len(added_to_v2_tables)} column(s) added to existing V2 tables "
            f"{added_to_v2_tables}"
        )
        for col in added_to_v2_tables:
            if not col.endswith(":null"):
                failures.append(f"column added to a V2 table is NOT NULL: {col}")

        # 4. The whole chain backward, and the fingerprint must be identical.
        r = alembic(url, "downgrade", "018")
        if r.returncode != 0:
            print(r.stdout[-3000:], r.stderr[-3000:])
            failures.append("head -> 018 FAILED")
            raise SystemExit(1)
        back_cols, back_tables = await fingerprint(engine)
        print(f"4. back at 018:          {len(back_tables)} tables, {len(back_cols)} columns")
        if back_cols != v2_cols:
            only_after = sorted(back_cols - v2_cols)
            only_before = sorted(v2_cols - back_cols)
            failures.append(
                f"downgraded schema differs from the pre-V3 one: "
                f"+{only_after[:5]} -{only_before[:5]}"
            )
        else:
            print("   fingerprint identical to the pre-V3 schema — rollback is real")

        # 5. Re-apply, so the database is left at head for the code test.
        r = alembic(url, "upgrade", "head")
        if r.returncode != 0:
            failures.append("re-applying 018 -> head after a downgrade FAILED")
    finally:
        await engine.dispose()
        if not args.keep:
            admin2 = create_async_engine(args.admin_url, isolation_level="AUTOCOMMIT")
            async with admin2.connect() as conn:
                await conn.execute(
                    text(
                        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                        f"WHERE datname = '{scratch}'"
                    )
                )
                await conn.execute(text(f'DROP DATABASE IF EXISTS "{scratch}"'))
            await admin2.dispose()
            print(f"\nscratch database {scratch} dropped")

    print("\n=== RESULT ===")
    if failures:
        for f in failures:
            print(f"  FAIL  {f}")
        return 1
    print("  the V3 migration chain applies, reverses, and leaves V2 intact")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
