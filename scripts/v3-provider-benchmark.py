#!/usr/bin/env python
"""Run the V3 provider benchmark — V3.4 Slice 4.5.

MANUAL, OPT-IN, BUDGET-CAPPED, AND NEVER IN CI
==============================================
A real benchmark spends money at a vendor and consumes a shared rate limit. This script
is the deliberate, budgeted, manually triggered run the acceptance strategy §5.6 requires.
It refuses to make a live call unless asked **twice**: `--live` and a provider that is
actually configured.

WITHOUT `--live` IT IS STILL USEFUL
===================================
The default run uses the fakes and exercises the whole harness — task list, verification
gate, scoring, the report — so the mechanism is checkable without a credential. What it
cannot tell you is anything about a vendor, and it says so.

WHAT IT WILL NOT DO
===================
Estimate a provider it did not run. Exa, Perplexity, Gemini and Anthropic have no
credentials **by decision** (ADR-048/050); they appear in the report as `not_approved`
and no number is produced for them. A benchmark that filled the gap from a published
price list would be inventing its most important column.

Usage:
    python scripts/v3-provider-benchmark.py                 # fakes, no network
    python scripts/v3-provider-benchmark.py --live          # real DeepSeek, if configured
    python scripts/v3-provider-benchmark.py --json out.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(ROOT))

from app.core.config import settings  # noqa: E402
from app.services.consumption import PriceBook  # noqa: E402
from app.services.providers.benchmark import (  # noqa: E402
    UNAVAILABLE_NOT_APPROVED,
    UNAVAILABLE_NOT_CONFIGURED,
    BenchmarkTask,
    render_report,
    run_benchmark,
)
from app.services.providers.contracts import (  # noqa: E402
    LEAD_UNVERIFIABLE,
    ResearchLead,
)

#: The real-issuer regression set, as benchmark tasks. Each one is a question the
#: platform actually has to answer, about an issuer that has already exposed a defect
#: live — which is what makes a good score here mean something.
TASKS: tuple[BenchmarkTask, ...] = (
    BenchmarkTask(
        task_id="pndora-cash-flow",
        question=(
            "What was Pandora A/S's cash flow from operating activities in its most "
            "recent published full financial year, and in which document is it stated?"
        ),
        company_ticker="PNDORA",
        expected_source_hosts=("pandoragroup.com",),
        notes="European issuer; documents sit on an off-domain CDN with extensionless URLs.",
    ),
    BenchmarkTask(
        task_id="cfr-segment-scope",
        question=(
            "What were Compagnie Financiere Richemont's Specialist Watchmakers segment "
            "sales in its most recent full financial year? Name the segment explicitly "
            "and do not report a Group figure."
        ),
        company_ticker="CFR",
        expected_source_hosts=("richemont.com",),
        notes="Segment figures must never become Group. The primary V2 failure mode.",
    ),
    BenchmarkTask(
        task_id="mrna-cash-runway",
        question=(
            "What were Moderna's cash, cash equivalents and investments at its most "
            "recent reported quarter end, per its SEC filings?"
        ),
        company_ticker="MRNA",
        expected_source_hosts=("sec.gov", "modernatx.com"),
        notes="US SEC path; MRNA has 8 genuine numeric conflicts.",
    ),
    BenchmarkTask(
        task_id="asml-bookings",
        question=(
            "What did ASML report as net bookings in its most recent quarterly results, "
            "and on what date were those results published?"
        ),
        company_ticker="ASML",
        expected_source_hosts=("asml.com",),
        notes="European reporting; semiconductor playbook.",
    ),
)


async def _refusing_verifier(lead: ResearchLead) -> str:
    """Without a live verification path, every lead is UNDECIDED — never verified.

    A benchmark that verified against nothing would report a perfect survival rate for
    whichever provider produced the most text.
    """
    if not lead.is_verifiable:
        return LEAD_UNVERIFIABLE
    return "pending"


def _providers(live: bool) -> tuple[dict, dict[str, str]]:
    unavailable: dict[str, str] = {
        "exa": UNAVAILABLE_NOT_APPROVED,
        "perplexity": UNAVAILABLE_NOT_APPROVED,
        "gemini": UNAVAILABLE_NOT_APPROVED,
        "anthropic": UNAVAILABLE_NOT_APPROVED,
    }
    if not live:
        from app.services.providers.fakes import FakeResearchProvider

        return {"fake_research": FakeResearchProvider()}, unavailable

    providers: dict = {}
    if settings.deepseek_api_key:
        from app.integrations.deepseek.providers import DeepSeekResearchProvider
        from app.integrations.deepseek.transport import HttpDeepSeekTransport

        providers["deepseek"] = DeepSeekResearchProvider(
            transport=HttpDeepSeekTransport(cfg=settings)
        )
    else:
        unavailable["deepseek"] = UNAVAILABLE_NOT_CONFIGURED
    if not providers:
        print(
            "No live provider is configured. Nothing was run, and no number is "
            "reported for a provider that did not run.",
            file=sys.stderr,
        )
    return providers, unavailable


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--live",
        action="store_true",
        help="call real providers. Costs money. Requires a configured credential.",
    )
    parser.add_argument("--json", type=Path, help="write the full report as JSON")
    parser.add_argument(
        "--max-seconds", type=int, default=180, help="per-task provider budget"
    )
    args = parser.parse_args()

    providers, unavailable = _providers(args.live)
    report = await run_benchmark(
        providers,
        TASKS,
        verifier=_refusing_verifier,
        # Empty ON PURPOSE: no price is recorded for any provider, so every cost is
        # unknown. Reporting an unpriced provider as free would make it the cheapest.
        prices=PriceBook(),
        unavailable=unavailable,
        max_seconds=args.max_seconds,
    )
    print(render_report(report))
    if args.json:
        args.json.write_text(json.dumps(report.to_dict(), indent=2))
        print(f"\nwrote {args.json}")
    if not args.live:
        print(
            "\nThis was a FAKE run. It exercises the harness and says nothing about "
            "any vendor."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
