#!/usr/bin/env python3
"""Push ONE real financial document through the V3 corpus, locally — V3.1.

WHY THIS EXISTS
===============
V3.0 is `IMPLEMENTED` and not `VALIDATED` for one reason: nothing has run a real,
long financial document through the durable path, because V3 is not deployed and
its migrations must not reach the live environment. Fixtures cannot substitute.
Every issuer in the regression set exposed a defect that only live data found,
and this slice was no exception — running a real 169-page annual report through
the parsed-representation builder is what revealed that raw headings were being
labelled business segments.

So: an opt-in, self-contained acceptance run that touches nothing.

WHAT IT DOES NOT DO
===================
* It does not deploy anything.
* It does not touch the dev database. It creates its own SQLite file (or uses a
  scratch PostgreSQL URL you pass explicitly) and its own artifact directory
  under a temporary path.
* It is not part of CI, it is not imported by the application, and
  ``scripts/v3-gates.sh`` does not run it.
* It makes NO network call unless you pass ``--url`` together with
  ``--allow-network``, and even then the fetch goes through the repository's own
  guarded, allowlisted, DNS-pinned document fetcher — never a bare request.

USAGE
=====
    # from a document you already have
    python scripts/v3-corpus-acceptance.py --pdf ~/Downloads/annual-report-2025.pdf

    # fetching a real issuer document through the guarded fetcher
    python scripts/v3-corpus-acceptance.py --allow-network \\
        --url "https://pandora.a.bigcontent.io/v1/static/Annual%20Report%202025" \\
        --allowed-domain pandora.a.bigcontent.io \\
        --title "Annual Report 2025"

WHAT IT PROVES
==============
1. raw bytes are retained, content-addressed and deduplicated;
2. a long document parses to pages, sections and tables — not 20 excerpts;
3. offsets are exact: the full text reconstructs and every page slices out of it;
4. a table survives as a GRID with its column→period map;
5. the bytes are retrievable again by content hash alone, and re-parsing them
   reproduces the same structure — which is the property that makes Slice 1.7's
   reprocessing possible without a re-fetch;
6. every honest bound is reported: how many of the document's pages were read,
   and whether the parse was complete or partial.
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import sys
import tempfile
import time
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "apps" / "api"))


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--pdf", type=Path, help="A local PDF/HTML document to ingest.")
    source.add_argument("--url", help="A document URL to fetch (needs --allow-network).")
    parser.add_argument(
        "--allow-network",
        action="store_true",
        help="Required with --url. Without it this script makes no network call.",
    )
    parser.add_argument(
        "--allowed-domain",
        action="append",
        default=[],
        help="Host the fetcher may talk to. Repeatable. Required with --url.",
    )
    parser.add_argument("--title", default=None, help="The document's own title.")
    parser.add_argument(
        "--database-url",
        default=None,
        help="A SCRATCH database. Defaults to a throwaway SQLite file. "
        "Never point this at the dev or a deployed database.",
    )
    parser.add_argument("--keep", action="store_true", help="Keep the scratch directory.")
    return parser.parse_args()


async def _load_document(args: argparse.Namespace, cfg: object) -> tuple[bytes, str, str]:
    """Return ``(raw, media_type, canonical_url)``. Network only when asked."""
    if args.pdf:
        raw = args.pdf.read_bytes()
        media = "application/pdf" if raw[:5] == b"%PDF-" else "text/html"
        return raw, media, args.pdf.resolve().as_uri()

    if not args.allow_network:
        raise SystemExit("--url requires --allow-network. Nothing was fetched.")
    if not args.allowed_domain:
        raise SystemExit("--url requires at least one --allowed-domain.")

    from app.services.sources.document_fetcher import safe_fetch_document

    started = time.perf_counter()
    fetched = await safe_fetch_document(
        args.url,
        allowed_domains=tuple(args.allowed_domain),
        cfg=cfg,  # type: ignore[arg-type]
        resolve_ip=True,
    )
    elapsed = time.perf_counter() - started
    print(
        f"  fetch      {elapsed:6.1f}s  ok={fetched.ok} status_class={fetched.status_class} "
        f"type={fetched.document_type} bytes={len(fetched.content or b''):,} "
        f"pinned={fetched.pinned} truncated={fetched.truncated}"
    )
    if not fetched.ok or not fetched.content:
        raise SystemExit(f"fetch failed: {fetched.error or fetched.failure_code}")
    media = "application/pdf" if fetched.document_type == "pdf" else "text/html"
    return fetched.content, media, fetched.final_url or args.url


async def main() -> int:
    args = _parse_args()
    scratch = Path(tempfile.mkdtemp(prefix="ib-v3-corpus-"))
    print(f"scratch: {scratch}")

    from sqlalchemy.dialects.postgresql import JSONB
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from sqlalchemy.ext.compiler import compiles
    from sqlalchemy.pool import StaticPool

    @compiles(JSONB, "sqlite")
    def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
        return "JSON"

    # ``app.models.__init__`` is not exhaustive — several model modules
    # (screening, review_event, backtest, financial_snapshot,
    # research_run_consumption) are imported only by the services that use them.
    # ``create_all`` needs every table in ``Base.metadata`` or an unresolved
    # foreign key aborts the whole schema, so import the package directory.
    import importlib
    import pkgutil

    import app.models as _models_pkg

    for _module in pkgutil.iter_modules(_models_pkg.__path__):
        importlib.import_module(f"app.models.{_module.name}")

    from app.core.config import Settings
    from app.db.base import Base
    from app.services.corpus.artifacts.backends.local_fs import (
        LocalFilesystemArtifactStore,
    )
    from app.services.corpus.artifacts.service import (
        load_artifact_bytes,
        record_artifact,
        store_raw_artifact,
    )
    from app.services.corpus.documents import DocumentVersionInput, upsert_document_version
    from app.services.corpus.parsed import (
        DerivationResult,
        build_parsed_document,
        load_full_text,
        persist_parsed_document,
    )
    from app.services.corpus.policy import ACCESS_PUBLIC_ISSUER
    from app.services.sources.document_period import document_period_of
    from app.services.sources.primary_document_extractor import extract_primary_document
    from app.services.sources.taxonomy import T1_PRIMARY_FILING

    cfg = Settings(
        v3_corpus_enabled=True,
        v3_artifact_store_backend="local",
        v3_artifact_store_local_root=str(scratch / "artifacts"),
    )
    store = LocalFilesystemArtifactStore(scratch / "artifacts")

    db_url = args.database_url or f"sqlite+aiosqlite:///{scratch / 'corpus.db'}"
    engine = create_async_engine(
        db_url,
        future=True,
        **({"poolclass": StaticPool} if db_url.startswith("sqlite") else {}),
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)

    try:
        print("\n=== 1. acquire ===")
        raw, media_type, canonical_url = await _load_document(args, cfg)
        print(f"  document   {len(raw):,} bytes  {media_type}")

        print("\n=== 2. retain the raw artifact ===")
        stored = await store_raw_artifact(
            raw,
            media_type=media_type,
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=cfg,
            store=store,
        )
        assert stored is not None
        print(f"  hash       {stored.content_hash}")
        print(f"  key        {stored.storage_key}")
        print(f"  created={stored.created} deduplicated={stored.deduplicated}")
        again = await store_raw_artifact(
            raw,
            media_type=media_type,
            access_class=ACCESS_PUBLIC_ISSUER,
            cfg=cfg,
            store=store,
        )
        assert again is not None
        print(f"  re-store   created={again.created} deduplicated={again.deduplicated}  <- dedup")

        print("\n=== 3. parse ===")
        started = time.perf_counter()
        extraction = extract_primary_document(
            raw,
            document_type="pdf" if media_type == "application/pdf" else "html",
            cfg=cfg,
            capture_blocks=True,
        )
        print(
            f"  extract    {time.perf_counter() - started:6.1f}s  status={extraction.status} "
            f"document_pages={extraction.page_count} blocks={len(extraction.blocks)} "
            f"tables={len(extraction.tables)} excerpts={len(extraction.excerpts)}"
        )
        excerpt_chars = sum(len(e.text) for e in extraction.excerpts)

        print("\n=== 4. persist the corpus ===")
        async with maker() as session:
            artifact_row = await record_artifact(session, stored, cfg=cfg)
            period = document_period_of(
                title=args.title, url=canonical_url, extraction=extraction
            )
            version = await upsert_document_version(
                session,
                DocumentVersionInput(
                    content_hash=stored.content_hash,
                    canonical_url=canonical_url,
                    transport="company_ir",
                    source_tier=T1_PRIMARY_FILING,
                    company_id=uuid.uuid4(),
                    document_type="annual_report",
                    title=args.title,
                    media_type=media_type,
                    byte_size=len(raw),
                    language=extraction.language,
                    period=period,
                    extraction_status=extraction.status,
                    research_artifact_id=artifact_row.id if artifact_row else None,
                ),
                cfg=cfg,
            )
            assert version is not None
            parsed = build_parsed_document(extraction)
            counts = DerivationResult()
            derivation = await persist_parsed_document(
                session,
                version_id=version.id,
                parsed=parsed,
                cfg=cfg,
                result=counts,
            )
            assert derivation is not None and parsed is not None
            await session.commit()

            print(f"  document   key={version.canonical_url[:60]}")
            print(f"  period     {version.period_key} ({version.period_type}) basis={version.period_basis}")
            print(
                f"  derivation pipeline_version={derivation.pipeline_version} "
                f"status={derivation.status} active={derivation.is_active}"
            )
            print(
                f"  pages      {derivation.pages_persisted} persisted of "
                f"{derivation.page_count} in the document  paginated={derivation.paginated}"
            )
            print(
                f"  content    {derivation.char_count:,} chars, "
                f"{derivation.section_count} sections, {derivation.table_count} tables"
            )
            print(
                f"  vs V2      {excerpt_chars:,} chars would have survived as "
                f"{len(extraction.excerpts)} bounded excerpts "
                f"({derivation.char_count / max(1, excerpt_chars):.1f}x more retained)"
            )

            print("\n=== 5. provenance ===")
            full = await load_full_text(session, derivation_id=derivation.id)
            exact = all(
                full[p.char_start : p.char_end] == p.text for p in parsed.pages
            )
            print(f"  full text reconstructs: {len(full):,} chars")
            print(f"  every page offset exact: {exact}")
            scoped = [s for s in parsed.sections if s.scope_type]
            print(
                f"  sections with a resolved scope: {len(scoped)} of "
                f"{len(parsed.sections)} (the rest are honestly unknown)"
            )
            for section in scoped[:5]:
                print(
                    f"    p{section.page_start}-{section.page_end} "
                    f"{section.scope_type}/{section.scope_name!r} :: {section.heading_path!r}"
                )
            grids = [t for t in parsed.tables if t.column_periods]
            print(f"  tables with a column->period map: {len(grids)} of {len(parsed.tables)}")
            for table in grids[:3]:
                print(f"    {table.table_location} periods={table.column_periods}")
                for row in table.rows[:2]:
                    print(f"      {row}")

            print("\n=== 6. re-retrieve and re-parse from the retained bytes ===")
            recovered = await load_artifact_bytes(
                session, content_hash=stored.content_hash, cfg=cfg, store=store
            )
            print(f"  bytes recovered by hash alone: {recovered == raw}")
            assert recovered is not None
            reparsed = build_parsed_document(
                extract_primary_document(
                    recovered,
                    document_type="pdf" if media_type == "application/pdf" else "html",
                    cfg=cfg,
                    capture_blocks=True,
                )
            )
            assert reparsed is not None
            print(
                f"  re-parse deterministic: pages={len(reparsed.pages) == len(parsed.pages)} "
                f"sections={len(reparsed.sections) == len(parsed.sections)} "
                f"text={reparsed.full_text() == parsed.full_text()}"
            )
            print("  (this is the property that makes reprocessing possible without a re-fetch)")

        print("\nDone. Nothing was deployed and no existing database was touched.")
        return 0
    finally:
        await engine.dispose()
        if args.keep:
            print(f"scratch kept at {scratch}")
        else:
            shutil.rmtree(scratch, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
