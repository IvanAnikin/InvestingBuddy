#!/usr/bin/env python3
"""What the classification fix WOULD do to every company, without doing any of it.

READ-ONLY. It issues one ``SELECT`` against the database and never writes: no backfill,
no migration, no mutation. Everything it reports is computed in memory from the rows it
read plus the SEC's public submissions endpoint.

WHY RUN THIS BEFORE SHIPPING
============================
The fix classifies lazily — a company is classified on its next research run — so there
is no backfill to review after the fact. This is the review: 71 rows, what each one
becomes, which playbook that selects, and, most importantly, **which companies stay
unknown**. A change that silently classified everything would be the worrying outcome,
not the reassuring one.

Usage::

    DATABASE_URL_RO=postgresql+psycopg://... python scripts/v3-classification-dry-run.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from sqlalchemy import create_engine, text  # noqa: E402

import app.main  # noqa: E402, F401 - registers every model
import app.services.playbooks.industries  # noqa: E402, F401 - registers the playbooks
from app.services.classification.resolver import resolve_classification  # noqa: E402
from app.services.classification.service import (  # noqa: E402
    _fetch_sec_classification,
)
from app.services.playbooks import select as select_playbooks  # noqa: E402


async def _classify(rows: list[tuple]) -> list[dict]:
    out: list[dict] = []
    for ticker, exchange, sector, industry in rows:
        sic, desc, reason = await _fetch_sec_classification(ticker, exchange)
        resolved = resolve_classification(
            sic_code=sic,
            sic_description=desc,
            stored_sector=sector,
            stored_industry=industry,
        )
        # `matching_sector`, not `sector` — the production call site. A sector that is
        # only an estimate, or that sits beside a known industry no playbook declares,
        # is reported but does not select a methodology.
        selection = select_playbooks(
            sector=resolved.matching_sector, industry=resolved.industry
        )
        out.append(
            {
                "ticker": ticker,
                "exchange": exchange,
                "was": (sector, industry),
                "sic": resolved.sic_code,
                "sector": resolved.sector,
                "industry": resolved.industry,
                "raw": resolved.industry_raw,
                "tier": resolved.tier,
                "source": resolved.source,
                "playbooks": [p.playbook_id for p in selection.playbooks],
                "reason": reason,
            }
        )
        await asyncio.sleep(0.15)  # SEC asks for <= 10 req/s; stay far under it.
    return out


def main() -> None:
    url = os.environ.get("DATABASE_URL_RO")
    if not url:
        raise SystemExit("set DATABASE_URL_RO (read-only use; never printed)")
    engine = create_engine(url)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                "select ticker, exchange, sector, industry from companies "
                "order by exchange, ticker"
            )
        ).all()
    results = asyncio.run(_classify([tuple(r) for r in rows]))

    classified = [r for r in results if r["sector"] or r["industry"]]
    with_playbook = [r for r in results if r["playbooks"]]

    print(f"{len(results)} companies read (SELECT only; nothing written)\n")
    print(f"{'TICKER':<10}{'EXCH':<8}{'SIC':<6}{'SECTOR':<24}{'INDUSTRY':<26}{'TIER':<22}PLAYBOOKS")
    print("-" * 120)
    for r in results:
        print(
            f"{r['ticker']:<10}{r['exchange']:<8}{str(r['sic'] or '-'):<6}"
            f"{str(r['sector'] or '-'):<24}{str(r['industry'] or '-'):<26}"
            f"{str(r['tier'] or '-'):<22}{','.join(r['playbooks']) or '-'}"
        )

    print("\n" + "=" * 60)
    print(f"classified      : {len(classified)}/{len(results)}")
    print(f"gets a playbook : {len(with_playbook)}/{len(results)}")
    print(f"stays unknown   : {len(results) - len(classified)}/{len(results)}")
    print("\nby playbook:")
    for pb, n in Counter(
        pb for r in results for pb in (r["playbooks"] or ["(none)"])
    ).most_common():
        print(f"  {pb:<22}{n}")
    print("\nby provenance tier:")
    for tier, n in Counter(str(r["tier"] or "unclassified") for r in results).most_common():
        print(f"  {tier:<24}{n}")
    inferred = [r for r in results if r["tier"] == "T6_model_estimate"]
    if inferred:
        print(f"\nINFERRED (T6 — an estimate, flagged on every run that uses it): {len(inferred)}")
        for r in inferred:
            print(f"  {r['ticker']:<10}{str(r['sector'] or '-'):<24}{r['raw']}")


if __name__ == "__main__":
    main()
