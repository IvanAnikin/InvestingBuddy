#!/usr/bin/env python3
"""Run ONE issuer through the whole V3 pipeline, locally — V3.10.

WHAT THIS IS FOR
================
The RC report's largest gap: *"nothing has run against a live issuer end to end."* This
closes it as far as is safely possible without a deployment.

It creates its **own scratch database**, migrates it to head, seeds one company, and runs
`run_v3_research` — the same function the real front door calls behind
`V3_PIPELINE_ENABLED`. Then it prints what a reviewer needs in order to find a
**correctness failure**, not a pretty report.

WHAT IT DOES NOT DO
===================
* It does not deploy anything and does not touch the dev or any deployed database.
* It does not enable a flag anywhere but in its own process.
* It makes no model call unless a provider is configured, and no network call at all
  unless `--allow-network` is passed. Every fetch goes through the repository's own
  guarded, allowlisted, DNS-pinned fetcher.

WHAT TO LOOK FOR
================
Not "did it produce findings". Look for:

* an entity resolved to the WRONG issuer;
* a segment figure reported as Group;
* an interim figure reported as annual;
* a citation that resolves to nothing;
* a finding with no evidence behind it;
* a chair label outside the five;
* a fabricated citation that was NOT discarded.

Every one of those is printed explicitly, and the run exits non-zero if any invariant
check fails.

USAGE
=====
    python scripts/v3-issuer-acceptance.py --ticker MRNA --exchange NASDAQ \
        --sector "Health Care" --industry Biotechnology --mode quick
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1] / "apps" / "api"
sys.path.insert(0, str(ROOT))


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ticker", required=True)
    parser.add_argument("--exchange", required=True)
    parser.add_argument("--name", default=None)
    parser.add_argument("--sector", default=None)
    parser.add_argument("--industry", default=None)
    parser.add_argument("--mode", default="standard")
    parser.add_argument(
        "--database-url",
        default=None,
        help="A SCRATCH database. Never the dev or a deployed one.",
    )
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Permit the guarded fetcher and live macro sources. Off by default.",
    )
    parser.add_argument("--runs", type=int, default=1, help="Run N times, for a delta.")
    parser.add_argument(
        "--seed-corpus-url",
        action="append",
        default=[],
        help=(
            "Ingest a real issuer document into the scratch corpus before running, "
            "through the repository's own guarded fetcher. Repeatable: a blocking "
            "question is often answered by a different document from the one carrying "
            "the financials. Requires --allow-network."
        ),
    )
    parser.add_argument(
        "--seed-corpus-domain",
        action="append",
        default=[],
        help="Host the fetcher may talk to. Repeatable. Required with a corpus URL.",
    )
    parser.add_argument(
        "--seed-sec",
        action="store_true",
        help=(
            "Seed the scratch database with REAL SEC company facts for this ticker "
            "before running. Public data, no credential. Requires --allow-network."
        ),
    )
    parser.add_argument(
        "--price-in",
        type=float,
        default=None,
        metavar="USD_PER_M",
        help="Price per million INPUT tokens. Unset means unpriced, which reports a "
        "cost of None rather than zero.",
    )
    parser.add_argument(
        "--price-out",
        type=float,
        default=None,
        metavar="USD_PER_M",
        help="Price per million OUTPUT tokens.",
    )
    parser.add_argument(
        "--price-source",
        default="",
        help="Where the prices came from. Recorded beside the estimate so it is "
        "auditable rather than merely plausible.",
    )
    parser.add_argument(
        "--external-research",
        action="store_true",
        help=(
            "V3.12. Enable the EXTERNAL research path for this run: the Investigator "
            "may call search_web and fetch_public_source, so a real provider searches "
            "the web and every claim it returns is re-fetched and verified by "
            "InvestingBuddy before anything may cite it. Requires --allow-network and a "
            "configured provider credential. Costs real money."
        ),
    )
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    scratch = args.database_url or (
        "postgresql+psycopg://investingbuddy:investingbuddy@localhost:5432/"
        f"ib_v3_acc_{args.ticker.lower()}"
    )
    os.environ["DATABASE_URL"] = scratch

    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.core.config import Settings
    from app.models.company import Company

    cfg = Settings(
        v3_pipeline_enabled=True,
        v3_agent_tools_enabled=True,
        v3_corpus_enabled=True,
        v3_entity_master_enabled=True,
        v3_search_backend="postgres",
        v3_macro_sources_enabled=bool(args.allow_network),
        v3_issuer_traversal_enabled=bool(args.allow_network),
        v3_filings_tool_enabled=bool(args.allow_network),
        v3_research_mode_default=args.mode,
        # The two gates a real company-research run has on. Without them the fact
        # writer returns an empty result WITHOUT querying — which is why V3.10's
        # acceptance saw an empty ``extracted_facts`` and concluded the luxury
        # playbook's blocking question was unanswerable.
        primary_document_ingestion_enabled=True,
        report_citation_persistence_enabled=True,
        # V3.12. Off unless asked for: the external path spends at a vendor, and a
        # credential in the environment is not a decision to spend it.
        v3_deepseek_search_enabled=bool(args.external_research),
        v3_price_usd_per_million_input_tokens=args.price_in,
        v3_price_usd_per_million_output_tokens=args.price_out,
        v3_price_source=args.price_source,
    )

    engine = create_async_engine(scratch)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    from app.services.corpus.search.factory import get_search_backend
    from app.services.pipeline.v3_pipeline import run_v3_research

    print(f"=== V3 issuer acceptance: {args.ticker}:{args.exchange} ===")
    print(f"scratch database: …/{scratch.rsplit('/', 1)[-1]}")
    print(f"mode: {args.mode}   network: {'ALLOWED' if args.allow_network else 'off'}")
    if args.external_research:
        from app.services.agent_tools.builtin import register_builtins
        from app.services.agent_tools.registry import ToolRegistry
        from app.services.agents.routing import research_provider_for

        provider = research_provider_for(cfg)
        tools = sorted(
            set(register_builtins(ToolRegistry(), cfg=cfg).names())
            & {"search_web", "fetch_public_source"}
        )
        print(f"external research: ON  provider={type(provider).__name__ if provider else None}")
        print(f"external tools registered: {tools}")
        if provider is None:
            print(
                "  !! no provider resolved — the flag is on and no credential is "
                "configured, so the external path will degrade to nothing."
            )

    from app.services.agents.routing import resolve_routing

    routing = resolve_routing(cfg)
    print(f"routing: {json.dumps(routing.to_dict()['slots'], sort_keys=True)}")
    print(
        "red team shares the chair's vendor: "
        f"{routing.shares_vendor_with_chair}"
    )

    outcomes = []
    async with maker() as session:
        company = Company(
            id=uuid.uuid4(),
            ticker=args.ticker,
            exchange=args.exchange,
            name=args.name or args.ticker,
            status="new",
            sector=args.sector,
            industry=args.industry,
        )
        session.add(company)
        await session.flush()
        backend = get_search_backend(cfg, session=session)

        if args.seed_corpus_url:
            if not args.allow_network:
                print("\n--seed-corpus-url needs --allow-network. Nothing was fetched.")
            else:
                chunks = 0
                for seed_url in args.seed_corpus_url:
                    chunks += await _seed_corpus(session, company, cfg, args, seed_url)
                print(f"\nseeded {chunks} corpus chunk(s) from a REAL issuer document")
                await session.commit()

        if args.seed_sec:
            if not args.allow_network:
                print("\n--seed-sec needs --allow-network. Nothing was fetched.")
            else:
                seeded = await _seed_sec_facts(session, company, cfg)
                print(f"\nseeded {seeded} REAL SEC fact(s) into the scratch database")
                await session.commit()

        for index in range(max(1, args.runs)):
            started = time.perf_counter()
            outcome = await run_v3_research(
                session, company, cfg=cfg, search_backend=backend
            )
            await session.commit()
            elapsed = time.perf_counter() - started
            outcomes.append(outcome)
            _report(index, outcome, elapsed)

        failures = await _invariants(session, outcomes[-1], company)

        external = None
        if args.external_research:
            external = await _external_provenance(session)
            print("\n=== EXTERNAL RESEARCH PROVENANCE (V3.12) ===")
            print(json.dumps(external, indent=2, default=str))
            # The brief's gate, stated as a check rather than as a paragraph: calling
            # search proves nothing, and neither does a verified lead nobody used.
            if external["leads_discovered"] == 0:
                failures.append(
                    "external research produced NO leads at all — the path ran and "
                    "discovered nothing to verify"
                )
            if external["evidence_promoted"] == 0:
                failures.append(
                    "no external lead survived InvestingBuddy's own retrieval and "
                    "verification, so no external evidence was promoted"
                )
            if not external["findings_citing_external_evidence"]:
                failures.append(
                    "external evidence was promoted but NO downstream finding cites "
                    "it — proving search ran is not proving the path is staffed"
                )

    await engine.dispose()

    if args.json:
        args.json.write_text(
            json.dumps(
                {
                    "runs": [o.to_dict() for o in outcomes],
                    "external_research": external,
                },
                indent=2,
                default=str,
            )
        )
        print(f"\nwrote {args.json}")

    print("\n=== INVARIANT CHECKS ===")
    if failures:
        for failure in failures:
            print(f"  FAIL  {failure}")
        print(f"\n{len(failures)} invariant failure(s).")
        return 1
    print("  all invariant checks passed")
    return 0


async def _seed_corpus(session, company, cfg, args, seed_url) -> int:  # noqa: ANN001
    """Ingest one real issuer document into the scratch corpus.

    The same path the corpus acceptance script uses and the same path a live run would:
    the repository's own guarded, allowlisted, DNS-pinned fetcher, then the real
    extractor, then document / version / derivation / chunk persistence, then the index.

    This is what makes `search_company_corpus` return REAL evidence ids — and therefore
    what makes the model's findings citable, the Council convenable and the Red Team and
    Chair reachable on real data.
    """
    from app.services.corpus.documents import (
        DocumentVersionInput,
        upsert_document_version,
    )
    from app.services.corpus.indexing import index_version, persist_chunks
    from app.services.corpus.parsed import (
        DerivationResult,
        build_parsed_document,
        persist_parsed_document,
    )
    from app.services.corpus.search.factory import get_search_backend
    from app.services.sources.document_fetcher import safe_fetch_document
    from app.services.sources.primary_document_extractor import (
        extract_primary_document,
    )
    from app.services.sources.taxonomy import T1_PRIMARY_FILING

    domains = tuple(args.seed_corpus_domain) or (seed_url.split("/")[2],)
    print(f"  fetching {seed_url[:90]}")
    fetched = await safe_fetch_document(
        seed_url, allowed_domains=domains, cfg=cfg, resolve_ip=True
    )
    if not fetched.ok or not fetched.content:
        print(f"  fetch failed: blocked={fetched.blocked} error={fetched.error}")
        return 0
    raw = fetched.content
    print(f"  fetched {len(raw):,} bytes  type={fetched.document_type}")

    extraction = extract_primary_document(
        raw, document_type=fetched.document_type or "pdf", cfg=cfg, capture_blocks=True
    )
    print(
        f"  extracted status={extraction.status} pages={extraction.page_count} "
        f"blocks={len(extraction.blocks)} tables={len(extraction.tables)}"
    )
    if not extraction.blocks and not extraction.tables:
        print("  nothing extractable; the corpus was not seeded")
        return 0

    # No artifact store here: retaining raw bytes is the corpus acceptance script's
    # concern and this run is about the RESEARCH path. The lineage still resolves —
    # `research_artifact_id` NULL is the documented shape of "lineage kept, bytes not
    # retained", not a missing field.
    version = await upsert_document_version(
        session,
        DocumentVersionInput(
            content_hash=extraction.content_hash,
            canonical_url=fetched.final_url or seed_url,
            transport="company_ir",
            source_tier=T1_PRIMARY_FILING,
            company_id=company.id,
            document_type="annual_report",
            title=f"{company.name} annual report",
            media_type=fetched.content_type,
            byte_size=len(raw),
            language=extraction.language,
            extraction_status=extraction.status,
        ),
        cfg=cfg,
    )
    if version is None:
        print("  the corpus is disabled; nothing was persisted")
        return 0
    parsed = build_parsed_document(extraction)
    derivation = await persist_parsed_document(
        session,
        version_id=version.id,
        parsed=parsed,
        cfg=cfg,
        result=DerivationResult(),
    )
    if derivation is None or parsed is None:
        print("  nothing was parsed; the corpus was not seeded")
        return 0
    chunk_rows = await persist_chunks(
        session, version=version, derivation=derivation, parsed=parsed, cfg=cfg
    )
    await session.flush()
    backend = get_search_backend(cfg, session=session)
    indexed = await index_version(
        session,
        research_document_version_id=version.id,
        backend=backend,
        cfg=cfg,
    )
    print(
        f"  persisted  pages={derivation.pages_persisted} chunks={len(chunk_rows)} "
        f"indexed={indexed.indexed}"
    )
    await _seed_issuer_facts(session, company, cfg, extraction, fetched, seed_url)
    return len(chunk_rows)


async def _seed_issuer_facts(session, company, cfg, extraction, fetched, seed_url) -> int:  # noqa: ANN001
    """Validate the SAME extraction into structured facts, as production does.

    V3.10's acceptance seeded the corpus but never ran fact validation, so
    ``extracted_facts`` was EMPTY and ``get_segment_facts`` had nothing to return —
    which made the luxury playbook's blocking question unanswerable and the Council
    unconvenable. That was a gap in the MEASUREMENT, not in the product: a real
    company-research run persists these facts through
    ``persist_primary_document_artifacts`` before V3 ever starts.

    The bytes are already fetched, so this re-uses that extraction rather than pulling a
    9 MB annual report twice.
    """
    from app.services.extracted_document_service import (
        persist_primary_document_artifacts,
    )
    from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
    from app.services.sources.document_period import detect_document_period
    from app.services.sources.extracted_fact_validator import (
        IssuerContext,
        validate_extracted_facts,
    )

    issuer = IssuerContext(company_name=company.name, ticker=company.ticker)
    headings = [b.section for b in extraction.blocks if b.section][:200]
    period = detect_document_period(
        title=f"{company.name} annual report",
        url=seed_url,
        headings=headings,
        text=" ".join(b.text for b in extraction.blocks[:80]),
    )
    validated = await asyncio.to_thread(
        validate_extracted_facts,
        extraction,
        issuer_context=issuer,
        cfg=cfg,
        document_period=period,
    )
    scoped = sum(1 for f in validated if getattr(f, "scope", None))
    print(f"  validated  facts={len(validated)} with-scope={scoped}")
    if not validated:
        return 0

    artifact = PrimaryDocumentArtifact(
        source_url=fetched.final_url or seed_url,
        document_type=fetched.document_type,
        title=f"{company.name} annual report",
        retrieved_at=datetime.now(timezone.utc),
        status="extracted",
    )
    artifact.extraction = extraction
    artifact.validated_facts = validated
    result = await persist_primary_document_artifacts(
        session,
        artifacts=[artifact],
        company_id=company.id,
        agent_run_id=None,
        cfg=cfg,
    )
    await session.flush()
    print(
        f"  persisted  facts={result.facts_created} deduped={result.facts_deduped} "
        f"documents={result.documents_created}"
    )
    return result.facts_created


async def _seed_sec_facts(session, company, cfg) -> int:  # noqa: ANN001
    """Pull real, public SEC company facts and persist them as extracted facts.

    Public information, no credential, through the provider the platform already uses.

    **Each datapoint keeps its OWN period**, taken from its own ``as_of``. That is not
    fussiness: MRNA's live companyfacts response returns ``revenue`` as of 2022-12-31
    beside a ``net_income`` as of 2025-12-31, because the two concepts were last filed
    under different taxonomies. Stamping one fiscal year across all of them would
    manufacture a period-mislabelled fact — the exact failure the scope and period
    invariants exist to prevent — and it would do it while looking tidy.

    Scope is ``group``: an SEC company-level XBRL concept IS the consolidated group.
    """
    import uuid as _uuid
    from datetime import datetime, timezone
    from decimal import Decimal, InvalidOperation

    from app.integrations.providers.sec_edgar_fundamentals import (
        SecEdgarFundamentalsProvider,
    )
    from app.models.extracted_document import ExtractedDocument, ExtractedFact

    provider = SecEdgarFundamentalsProvider()
    try:
        fundamentals = await provider.get_fundamentals(company.ticker, company.exchange)
    except Exception as exc:  # noqa: BLE001
        print(f"  SEC fetch failed: {type(exc).__name__}: {exc}")
        return 0

    payload = (
        fundamentals.model_dump()
        if hasattr(fundamentals, "model_dump")
        else dict(fundamentals)
    )
    datapoints = payload.get("datapoints") or []
    if not datapoints:
        print("  SEC returned no datapoints")
        return 0
    meta = payload.get("meta") or {}
    source_url = next(
        (d.get("source_url") for d in datapoints if d.get("source_url")), None
    )

    document = ExtractedDocument(
        id=_uuid.uuid4(),
        company_id=company.id,
        content_hash=_uuid.uuid4().hex + _uuid.uuid4().hex,
        canonical_url=source_url or "https://data.sec.gov/api/xbrl/companyfacts/",
        provider="sec_edgar",
        source_type="regulatory_filing",
        source_tier="T2_regulator_or_gov",
        mime_type="application/json",
        status="extracted",
        extraction_method="xbrl",
        retrieved_at=datetime.now(timezone.utc),
    )
    session.add(document)
    await session.flush()

    periods: dict[str, int] = {}
    count = 0
    for point in datapoints:
        if not isinstance(point, dict):
            continue
        raw_value = point.get("value")
        if raw_value is None or isinstance(raw_value, (bool, dict, list)):
            continue
        try:
            numeric = Decimal(str(raw_value))
        except (InvalidOperation, ValueError):
            continue  # a string datapoint (form_type, accession_number) is metadata
        label = str(point.get("field_name") or "").split(".", 1)[-1]
        if not label:
            continue
        # `as_of` is the PERIOD END for a directly-reported concept and the FILING DATE
        # for a derived one — the live MRNA response carries `as_of 2026-02-20` on
        # FY2025 derivations. Taking the year from it blindly stamped those facts
        # "2026", the model faithfully reported them as FY2026 "projected" figures, and
        # the whole run carried a period error nothing could catch. The provider states
        # the real period in its own note ("annual data, FY2025 FY"), so that is read
        # first and `as_of` is only the fallback.
        note = str(point.get("note") or "")
        period = ""
        match = re.search(r"\bFY(\d{4})\b", note)
        if match:
            period = match.group(1)
        else:
            as_of = str(point.get("as_of") or "")
            if len(as_of) >= 4 and as_of[:4].isdigit():
                period = as_of[:4]
        periods[period or "unstated"] = periods.get(period or "unstated", 0) + 1
        unit = point.get("unit")
        session.add(
            ExtractedFact(
                id=_uuid.uuid4(),
                extracted_document_id=document.id,
                label=label,
                value_numeric=numeric,
                value_text=str(raw_value),
                unit=str(unit) if unit else None,
                currency=point.get("currency"),
                # `period` is the fact's OWN period, not a run-level fiscal year. The
                # period TYPE is not a column on this model — it is derived from the
                # label by `financial_period`, which is the one place that decides it.
                period=period or None,
                scope_type="group",
                scope_key="group",
                extraction_method="xbrl",
                confidence=0.9,
                validation_status="validated",
                needs_human_review=False,
                is_active=True,
            )
        )
        count += 1
    await session.flush()
    note = str(meta.get("note") or "")
    if note:
        print(f"  SEC note: {note[:200]}")
    print(f"  periods present in the seeded facts: {dict(sorted(periods.items()))}")
    if len(periods) > 1:
        print(
            "  NOTE: the live SEC response mixes periods across concepts. Each fact "
            "keeps its own; a calculation that crosses them must REFUSE."
        )
    return count


def _report(index: int, outcome, elapsed: float) -> None:  # noqa: ANN001
    print(f"\n--- run {index + 1} ---")
    print(f"  research_run_id   {outcome.research_run_id}")
    print(f"  playbooks         {outcome.playbook_versions or 'NONE'}")
    print(f"  elapsed           {elapsed:.1f}s")
    loop = outcome.loop or {}
    print(
        f"  loop              stopped_by={loop.get('stopped_by')} "
        f"rounds={loop.get('rounds')} tasks={loop.get('tasks_run')} "
        f"tool_calls={loop.get('tool_calls')}"
    )
    print(
        f"                    complete_analysis={loop.get('is_complete_analysis')} "
        f"council_may_convene={loop.get('council_may_convene')}"
    )
    print(
        f"  fabricated cites  {loop.get('fabricated_citations_discarded', 0)} discarded"
    )
    council = outcome.council or {}
    print(
        f"  council           convened={council.get('convened')} "
        f"refusal={council.get('refusal_reason')} "
        f"findings={council.get('finding_count')} "
        f"verified={council.get('verified_finding_count')} "
        f"gaps={council.get('gap_count')} "
        f"unresolved_disagreements={council.get('unresolved_disagreement_count')}"
    )
    challenges = outcome.challenges or {}
    print(
        f"  red team          challenges={challenges.get('challenges')} "
        f"resolved={challenges.get('resolved')} "
        f"unresolved={challenges.get('unresolved')} "
        f"withdrawn={challenges.get('withdrawn_findings')} "
        f"unknown_targets={challenges.get('discarded_unknown_targets', 0)}"
    )
    chair = outcome.chair or {}
    print(
        f"  chair             label={chair.get('label')} "
        f"setup={chair.get('fundamental_setup')} "
        f"fallback={chair.get('deterministic_fallback')} "
        f"key_points={len(chair.get('key_points') or [])} "
        f"discarded_cites={len(chair.get('discarded_citations') or [])}"
    )
    if outcome.delta:
        counts = outcome.delta.get("counts", {})
        print(
            f"  delta             unchanged_thesis="
            f"{outcome.delta.get('unchanged_core_thesis')} {counts}"
        )
    c = outcome.consumption or {}
    model = c.get("model") or {}
    print(
        f"  consumption       tool_calls={c.get('tool_calls')} "
        f"model_calls={model.get('model_calls')} "
        f"in={model.get('model_input_tokens')} out={model.get('model_output_tokens')}"
    )
    print(
        f"  useful findings   {c.get('verified_useful_findings')} of "
        f"{c.get('findings_total')} "
        f"({c.get('findings_withdrawn_by_red_team')} withdrawn by the red team)"
    )
    per_finding = c.get("cost_per_verified_useful_finding")
    print(
        f"  cost              run_usd={c.get('cost_per_company_research_run')} "
        f"per_useful_finding="
        + (f"{per_finding:.6f}" if isinstance(per_finding, float) else str(per_finding))
    )
    if c.get("price_source"):
        print(f"                    priced from: {c['price_source']}")
    if c.get("cost_is_unknown_because"):
        print(f"                    {c['cost_is_unknown_because']}")
    if c.get("model_by_vendor"):
        print(f"  by vendor         {c['model_by_vendor']}")
    if outcome.degraded:
        print("  DEGRADED:")
        for reason in outcome.degraded:
            print(f"    - {reason}")
    if outcome.error:
        print(f"  ERROR: {outcome.error}")
    for finding in (outcome.findings or [])[:5]:
        print(
            f"    finding  [{finding.get('scope_key')}|{finding.get('period_key')}] "
            f"{str(finding.get('statement'))[:110]}"
        )
        print(f"             cites {finding.get('evidence_ids')}")
    for gap in (outcome.gaps or [])[:5]:
        print(f"    gap      [{gap.get('gap_type')}] {str(gap.get('description'))[:110]}")


async def _external_provenance(session) -> dict:  # noqa: ANN001
    """What the EXTERNAL path actually did, at the granularity the V3.12 brief asks for.

    Deliberately not "was search_web called". The brief's point is that calling a search
    proves nothing: what has to be shown is a lead **discovered**, a source **fetched by
    InvestingBuddy**, a verification **passed or failed**, evidence **promoted**, and
    that promoted evidence **used downstream**. Each of those is a different row in a
    different table, and this reads all of them.
    """
    from sqlalchemy import select

    from app.models.ledger import ResearchFinding
    from app.models.research_lead import ResearchLeadRecord
    from app.models.research_tool_call import ResearchToolCall
    from app.services.agent_tools.external import EXTERNAL_EVIDENCE_PREFIX

    leads = (await session.execute(select(ResearchLeadRecord))).scalars().all()
    calls = (
        (
            await session.execute(
                select(ResearchToolCall).where(
                    ResearchToolCall.tool_name.in_(
                        ["search_web", "fetch_public_source"]
                    )
                )
            )
        )
        .scalars()
        .all()
    )
    findings = (await session.execute(select(ResearchFinding))).scalars().all()

    promoted = sorted(
        {
            lead.fetched_content_hash
            for lead in leads
            if lead.status == "verified" and lead.fetched_content_hash
        }
    )
    external_cited = [
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
        "tool_calls": {
            name: sum(1 for c in calls if c.tool_name == name)
            for name in ("search_web", "fetch_public_source")
        },
        "tool_outcomes": {
            outcome: sum(1 for c in calls if c.outcome == outcome)
            for outcome in sorted({c.outcome for c in calls})
        },
        "leads_discovered": len(leads),
        "leads_by_status": by_status,
        "leads_rejected_by_reason": by_reason,
        # `fetch_attempted` lives on the OUTCOME, not on the row. A fetch happened iff
        # we hold the bytes' hash, or the refusal names something only a fetch can find
        # out. A policy refusal decided before the network is not a fetch.
        "sources_fetched": sum(
            1
            for lead in leads
            if lead.fetched_content_hash
            or lead.rejection_reason
            in (
                "url_unreachable",
                "claim_not_in_source",
                "value_mismatch",
                "period_mismatch",
                "scope_mismatch",
            )
        ),
        "verifications_passed": by_status.get("verified", 0),
        "verifications_failed": by_status.get("rejected", 0),
        "evidence_promoted": len(promoted),
        "findings_citing_external_evidence": [
            {
                "finding_id": str(f.id),
                "statement": (f.statement or "")[:160],
                "evidence_ids": list(f.evidence_ids_json or []),
                "period_key": f.period_key,
                "scope_key": f.scope_key,
                "verification_status": f.verification_status,
            }
            for f in external_cited
        ],
    }


async def _unresolved_citations(session, findings) -> list[str]:  # noqa: ANN001
    """Citation ids that point at no row.

    Two id spaces, both minted by the platform: corpus evidence ids are ``ev:`` followed
    by a chunk id (which itself begins ``c:``), and everything else is an extracted-fact
    or calculation row id.
    """
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedFact
    from app.models.research_chunk import ResearchDocumentChunk

    cited: set[str] = set()
    for finding in findings:
        cited.update(finding.evidence_ids_json or [])
    if not cited:
        return []

    # `ev:x:` is EXTERNAL evidence (V3.12) — a URL InvestingBuddy fetched and verified,
    # deliberately not a corpus chunk. Looking one up in `research_document_chunks` finds
    # nothing and reports a fabricated citation, so this harness failed on exactly the
    # outcome V3.12 exists to produce. It resolves against the lead record instead.
    external_ids = {c for c in cited if c.startswith(EXTERNAL_EVIDENCE_PREFIX)}
    chunk_ids = {
        c[3:]
        for c in cited
        if c.startswith("ev:") and not c.startswith(EXTERNAL_EVIDENCE_PREFIX)
    }
    other = {c for c in cited if not c.startswith("ev:")}

    unresolved_external: set[str] = set()
    if external_ids:
        from app.models.research_lead import ResearchLeadRecord
        from app.services.agent_tools.external import external_evidence_id

        rows = (
            (
                await session.execute(
                    select(
                        ResearchLeadRecord.fetched_content_hash,
                        ResearchLeadRecord.fetched_url,
                        ResearchLeadRecord.claimed_source_url,
                    ).where(ResearchLeadRecord.status == "verified")
                )
            )
            .all()
        )
        mintable = {
            external_evidence_id(h, url or claimed)
            for h, url, claimed in rows
            if h
        }
        unresolved_external = external_ids - mintable

    found_chunks: set[str] = set()
    if chunk_ids:
        found_chunks = set(
            (
                await session.execute(
                    select(ResearchDocumentChunk.chunk_id).where(
                        ResearchDocumentChunk.chunk_id.in_(chunk_ids)
                    )
                )
            )
            .scalars()
            .all()
        )

    found_facts: set[str] = set()
    if other:
        import uuid as _uuid

        as_uuid = []
        for value in other:
            try:
                as_uuid.append(_uuid.UUID(value))
            except (ValueError, AttributeError):
                continue
        if as_uuid:
            found_facts = {
                str(row)
                for row in (
                    await session.execute(
                        select(ExtractedFact.id).where(ExtractedFact.id.in_(as_uuid))
                    )
                )
                .scalars()
                .all()
            }

    failures = []
    for missing in sorted(chunk_ids - found_chunks):
        failures.append(f"citation 'ev:{missing}' resolves to no corpus chunk")
    for missing in sorted(other - found_facts):
        failures.append(f"citation {missing!r} resolves to no fact or calculation")
    for missing in sorted(unresolved_external):
        failures.append(
            f"citation {missing!r} resolves to no verified lead — an external evidence "
            "id must be derivable from the hash of bytes InvestingBuddy fetched"
        )
    return failures


async def _invariants(session, outcome, company) -> list[str]:  # noqa: ANN001
    """The checks a correctness failure would trip. Not "is the report good"."""
    from sqlalchemy import select

    from app.models.ledger import ResearchFinding
    from app.services.llm.schemas import ALLOWED_COMMITTEE_LABELS

    failures: list[str] = []

    label = (outcome.chair or {}).get("label")
    if label and label not in ALLOWED_COMMITTEE_LABELS:
        failures.append(f"chair label {label!r} is outside the allowed five")

    forbidden = ("buy", "sell", "hold ", "price target", "fair value")
    synthesis = str((outcome.chair or {}).get("synthesis") or "").lower()
    for term in forbidden:
        if term in synthesis:
            failures.append(f"chair synthesis contains forbidden term {term!r}")

    rows = (
        (
            await session.execute(
                select(ResearchFinding).where(
                    ResearchFinding.research_run_id == outcome.research_run_id
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        if row.evidence_count == 0 and row.calculation_count == 0:
            failures.append(f"finding {row.id} has no evidence and no calculation")
        if row.scope_key and row.scope_key.startswith("segment:"):
            statement = (row.statement or "").lower()
            if "group" in statement and "segment" not in statement:
                failures.append(
                    f"finding {row.id} is segment-scoped and its statement says Group"
                )

    for finding in outcome.findings or []:
        if not finding.get("evidence_ids") and not finding.get("calculation_ids"):
            failures.append("a council finding carries no citation")

    # Every citation must RESOLVE. A finding carrying an id that points at nothing is
    # indistinguishable, to a reader, from one that is properly sourced — and this was
    # unchecked until V3.11.5, so "citations resolve" was an assumption rather than a
    # measurement.
    failures.extend(await _unresolved_citations(session, rows))

    if outcome.error:
        failures.append(f"the pipeline recorded an error: {outcome.error}")

    return failures


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
