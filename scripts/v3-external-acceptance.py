#!/usr/bin/env python3
"""V3.12 — the EXTERNAL research path, end to end, on a scratch database.

WHAT THIS PROVES, AND WHAT IT DOES NOT
======================================
The V3.11 release candidate closed with one caveat: the ``ResearchLead`` -> Evidence path
had never run with a real external provider. This script runs the whole chain and prints
the provenance needed to check it — not "search was called", but:

    lead discovered -> source fetched BY INVESTINGBUDDY -> verification passed or failed
    -> evidence promoted -> a downstream finding citing that evidence

It runs the real Director, the real Investigator, the real tool session and the real
Council against a scratch database, with the external tools registered. The provider and
the fetches are REAL when ``--live`` is given.

It does **not** deploy, migrate a real environment, or touch the dev database. With
``--sqlite`` it builds the schema with ``create_all`` on an in-memory database, which is
what the V3 test suite does — that is a weaker statement than the migration chain and is
labelled as such in the output.

USAGE
=====
    python scripts/v3-external-acceptance.py --sqlite --live --ticker MRNA
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(ROOT))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", default="MRNA")
    parser.add_argument("--exchange", default="NASDAQ")
    parser.add_argument("--name", default="Moderna, Inc.")
    parser.add_argument("--sector", default="Health Care")
    parser.add_argument("--industry", default="Biotechnology")
    # `standard` rather than `quick`: quick's task budget is small enough that the
    # external question competes with the baseline for a slot, and an acceptance run
    # that sometimes does not ask its own question is not an acceptance run.
    parser.add_argument("--mode", default="standard")
    parser.add_argument(
        "--sqlite",
        action="store_true",
        help="Build the schema with create_all on an in-memory database instead of "
        "requiring PostgreSQL. Weaker than the migration chain; labelled in the output.",
    )
    parser.add_argument("--database-url", default=None)
    parser.add_argument(
        "--live",
        action="store_true",
        help="Use the REAL external provider and REAL fetches. Costs money.",
    )
    parser.add_argument(
        "--price-in", type=float, default=None, metavar="USD_PER_M",
        help="Price per MILLION input tokens. Unset means UNPRICED, which reports a "
        "cost of None — never zero.",
    )
    parser.add_argument("--price-out", type=float, default=None, metavar="USD_PER_M")
    parser.add_argument(
        "--price-source", default="",
        help="Where the prices came from, recorded beside the estimate so it is "
        "auditable rather than merely plausible.",
    )
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.pool import StaticPool

    @compiles(JSONB, "sqlite")
    def _jsonb(element, compiler, **kw):  # noqa: ANN001, ANN202
        return "JSON"

    from app.core.config import Settings
    from app.db.base import Base

    # `models/__init__.py` is not exhaustive — a documented gotcha of this repository —
    # and `create_all` needs every mapped table present or a foreign key resolves to a
    # table nobody imported. Importing the package's modules is the cheapest way to be
    # sure, and a missing one shows up here rather than mid-run.
    import importlib
    import pkgutil

    import app.models as _models_pkg

    for _module in pkgutil.iter_modules(_models_pkg.__path__):
        importlib.import_module(f"app.models.{_module.name}")

    from app.models.company import Company
    from app.services.agents.routing import research_provider_for, resolve_routing
    from app.services.corpus.search.factory import get_search_backend
    from app.services.pipeline.v3_pipeline import run_v3_research

    cfg = Settings(
        v3_pipeline_enabled=True,
        v3_agent_tools_enabled=True,
        v3_corpus_enabled=True,
        v3_entity_master_enabled=True,
        v3_research_mode_default=args.mode,
        # THE decision this script exists to exercise. Off everywhere else.
        v3_deepseek_search_enabled=bool(args.live),
        v3_price_usd_per_million_input_tokens=args.price_in,
        v3_price_usd_per_million_output_tokens=args.price_out,
        v3_price_source=args.price_source,
    )

    print("=== V3.12 EXTERNAL RESEARCH ACCEPTANCE ===")
    print(f"issuer: {args.ticker}:{args.exchange}   mode: {args.mode}")
    print(f"external research: {'LIVE' if args.live else 'OFF'}")
    if args.sqlite:
        print(
            "database: in-memory SQLite via create_all — NOT the migration chain, and "
            "not a substitute for it"
        )
    provider = research_provider_for(cfg)
    print(f"provider: {type(provider).__name__ if provider else None}")
    routing = resolve_routing(cfg)
    print(f"routing: {json.dumps(routing.to_dict()['slots'], sort_keys=True)}")

    if args.sqlite:
        engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            future=True,
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
    else:
        url = args.database_url or (
            "postgresql+psycopg://investingbuddy:investingbuddy@localhost:5432/"
            f"ib_v3_ext_{args.ticker.lower()}"
        )
        engine = create_async_engine(url)

    maker = async_sessionmaker(engine, expire_on_commit=False)
    started = time.perf_counter()
    async with maker() as session:
        company = Company(
            id=uuid.uuid4(),
            ticker=args.ticker,
            exchange=args.exchange,
            name=args.name,
            status="new",
            sector=args.sector,
            industry=args.industry,
        )
        session.add(company)
        await session.flush()
        backend = get_search_backend(cfg, session=session)
        outcome = await run_v3_research(
            session, company, cfg=cfg, search_backend=backend
        )
        await session.commit()
        elapsed = time.perf_counter() - started

        print(f"\nrun: ran={outcome.ran} degraded={outcome.degraded} "
              f"error={outcome.error} elapsed={elapsed:.1f}s")
        print("\n=== RUN OUTCOME ===")
        print(json.dumps(outcome.to_dict(), indent=2, default=str)[:4000])

        report = await _provenance(session)
        report["cost"] = _cost(report, args)
        print("\n=== EXTERNAL PROVENANCE ===")
        print(json.dumps(report, indent=2, default=str))

        failures = _gate(report, live=bool(args.live))

    await engine.dispose()

    if args.json:
        args.json.write_text(
            json.dumps(
                {"elapsed_seconds": elapsed, "provenance": report, "gate": failures},
                indent=2,
                default=str,
            )
        )
        print(f"\nwrote {args.json}")

    print("\n=== GATE ===")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        return 1
    print("  every external-path gate passed")
    return 0


async def _provenance(session) -> dict:  # noqa: ANN001
    """Each step of the chain, read from the table that records it."""
    from sqlalchemy import select

    from app.models.ledger import ResearchFinding
    from app.models.research_lead import ResearchLeadRecord
    from app.models.research_tool_call import ResearchToolCall
    from app.services.agent_tools.external import EXTERNAL_EVIDENCE_PREFIX

    leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
    calls = (await session.execute(select(ResearchToolCall))).scalars().all()
    findings = (await session.execute(select(ResearchFinding))).scalars().all()

    external_calls = [
        c for c in calls if c.tool_name in ("search_web", "fetch_public_source")
    ]
    cited = [
        f
        for f in findings
        if any(
            str(e).startswith(EXTERNAL_EVIDENCE_PREFIX)
            for e in (f.evidence_ids_json or [])
        )
    ]
    by_status: dict[str, int] = {}
    by_reason: dict[str, int] = {}
    for lead in leads:
        by_status[lead.status] = by_status.get(lead.status, 0) + 1
        if lead.rejection_reason:
            by_reason[lead.rejection_reason] = by_reason.get(lead.rejection_reason, 0) + 1

    return {
        "external_tool_calls": {
            name: sum(1 for c in external_calls if c.tool_name == name)
            for name in ("search_web", "fetch_public_source")
        },
        "external_tool_refusals": [
            {
                "tool": c.tool_name,
                "role": c.role,
                "outcome": c.outcome,
                "refusal_reason": c.refusal_reason,
                "error_type": c.error_type,
                "summary": (c.summary or "")[:200],
                "arguments": c.arguments_json,
                "item_count": c.item_count,
                "latency_ms": c.latency_ms,
            }
            for c in external_calls
        ],
        "external_tool_outcomes": {
            o: sum(1 for c in external_calls if c.outcome == o)
            for o in sorted({c.outcome for c in external_calls})
        },
        "leads_discovered": len(leads),
        "lead_details": [
            {
                "url": (lead.claimed_source_url or "")[:120],
                "value": lead.claimed_value,
                "period": lead.claimed_period,
                "status": lead.status,
                "reason": lead.rejection_reason,
                "hash": (lead.fetched_content_hash or "")[:16],
                "period_verified": lead.period_verified,
            }
            for lead in leads
        ],
        "leads_by_status": by_status,
        "leads_rejected_by_reason": by_reason,
        # A fetch happened iff we hold the bytes' hash OR the outcome names a fetch
        # failure. A policy refusal decided before the network is NOT a fetch.
        "sources_fetched_by_investingbuddy": sum(
            1
            for lead in leads
            if lead.fetched_content_hash
            or lead.rejection_reason
            in ("url_unreachable", "claim_not_in_source", "value_mismatch",
                "period_mismatch", "scope_mismatch")
        ),
        "verifications_passed": by_status.get("verified", 0),
        "verifications_failed": by_status.get("rejected", 0),
        "evidence_promoted": sorted(
            {
                lead.fetched_content_hash[:16]
                for lead in leads
                if lead.status == "verified" and lead.fetched_content_hash
            }
        ),
        "total_findings": len(findings),
        "findings_citing_external_evidence": [
            {
                "finding_id": str(f.id),
                "statement": (f.statement or "")[:200],
                "evidence_ids": list(f.evidence_ids_json or []),
                "period_key": f.period_key,
                "scope_key": f.scope_key,
            }
            for f in cited
        ],
        # Consumption lives in a JSON column, so it is summed by unit name rather than
        # by attribute — a unit absent from a row is UNMEASURED, and adding it as zero
        # is the fabrication the consumption module exists to prevent.
        "consumption": _sum_consumption(external_calls),
        "latency_ms": sum(c.latency_ms or 0 for c in external_calls),
        "estimated_cost_usd": (
            sum(c.estimated_cost_usd for c in external_calls if c.estimated_cost_usd)
            or None
        ),
    }


def _sum_consumption(calls) -> dict:  # noqa: ANN001
    """Totals per unit, over the units the rows actually measured."""
    totals: dict[str, float] = {}
    for call in calls:
        for unit, value in (call.consumption_json or {}).items():
            if isinstance(value, int | float):
                totals[unit] = totals.get(unit, 0) + value
    return {k: totals[k] for k in sorted(totals) if totals[k]}


def _cost(report: dict, args) -> dict:  # noqa: ANN001
    """What the external path cost, or an honest refusal to say.

    ``None`` means UNPRICED, never free. A run whose price this platform has not been
    told would otherwise report zero, and a zero makes an unpriced provider look like
    the cheapest one — which is how a benchmark chooses the wrong vendor.
    """
    units = report.get("consumption") or {}
    tokens_in = units.get("model_input_tokens", 0)
    tokens_out = units.get("model_output_tokens", 0)
    verified = report.get("verifications_passed", 0)
    useful = len(report.get("findings_citing_external_evidence") or [])
    priced = args.price_in is not None and args.price_out is not None
    total = (
        (tokens_in / 1_000_000) * args.price_in
        + (tokens_out / 1_000_000) * args.price_out
        if priced
        else None
    )
    return {
        "input_tokens": tokens_in,
        "output_tokens": tokens_out,
        "cached_tokens": units.get("cached_tokens", 0),
        "web_search_calls": units.get("web_search_calls", 0),
        "url_fetch_calls": units.get("url_fetch_calls", 0),
        "elapsed_ms": report.get("latency_ms"),
        "leads_discovered": report.get("leads_discovered", 0),
        "verified_leads": verified,
        "rejected_leads": report.get("verifications_failed", 0),
        "verified_useful_findings": useful,
        "price_source": args.price_source or None,
        "estimated_cost_usd": total,
        "cost_per_verified_useful_finding_usd": (
            (total / useful) if (total is not None and useful) else None
        ),
        "note": (
            "A single bounded sample. Not a statistically robust benchmark, and not "
            "presented as one."
            if priced
            else "UNPRICED: no price was supplied, so the cost is unknown, not zero."
        ),
    }


def _gate(report: dict, *, live: bool) -> list[str]:
    """The brief's own conditions, as checks rather than as prose."""
    failures: list[str] = []
    if not live:
        if report["external_tool_calls"]["search_web"]:
            failures.append(
                "the external path ran with --live off, which means a flag leaked"
            )
        return failures
    if not report["external_tool_calls"]["search_web"]:
        failures.append("search_web was never called: the path is not wired in")
    if report["leads_discovered"] == 0:
        failures.append("no leads were discovered, so nothing could be verified")
    if report["sources_fetched_by_investingbuddy"] == 0:
        failures.append(
            "no lead reached InvestingBuddy's own fetch — proving search ran is not "
            "proving the path is staffed"
        )
    if not report["evidence_promoted"]:
        failures.append(
            "no lead survived verification, so no external evidence was promoted"
        )
    if not report["findings_citing_external_evidence"]:
        failures.append(
            "external evidence was promoted and NO downstream finding cites it"
        )
    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
