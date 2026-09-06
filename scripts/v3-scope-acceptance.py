#!/usr/bin/env python3
"""Measure real-document scope resolution against a persisted corpus. V3.11 slice 11.2.

V3.10 measured 170 of 173 chunks on the real Richemont annual report with no scope at
all, and the three that were labelled included ``segment:proposed dividend`` — which is
not a business segment. This script is how that number stops being an anecdote.

It rebuilds the parsed document from what the corpus already persisted — pages, sections
and tables — re-runs chunking with the current resolver, and reports coverage,
attribution method, and the metric that actually governs release safety:

    **the false-positive Group rate.**

The goal is NOT to minimise unknown. A wrong Group label is far worse than an absent one,
so a run that raises coverage while promoting one segment figure to Group is a REGRESSION,
and this script exits non-zero on exactly that.

Usage
-----
    python scripts/v3-scope-acceptance.py --database-url postgresql+psycopg://... \\
        [--expect "Specialist Watchmakers=segment:specialist watchmakers"] [--json out.json]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "apps" / "api"))

from sqlalchemy import text as sql  # noqa: E402
from sqlalchemy.ext.asyncio import create_async_engine  # noqa: E402

from app.models.research_derivation import PROFILE_LIVE  # noqa: E402
from app.services.corpus.chunking import build_chunks  # noqa: E402
from app.services.corpus.parsed import (  # noqa: E402
    ParsedDocument,
    ParsedPage,
    ParsedSection,
    ParsedTable,
)
from app.services.corpus.scope_resolution import (  # noqa: E402
    learn_segment_vocabulary,
)

BAR = "=" * 78


async def _load(engine, derivation_id: str | None) -> tuple[ParsedDocument, str, str]:
    """Rebuild the ParsedDocument the corpus persisted."""
    async with engine.connect() as conn:
        if derivation_id is None:
            row = (
                await conn.execute(
                    sql(
                        "SELECT d.id::text, v.research_document_version_id::text "
                        "FROM research_document_derivations d "
                        "JOIN research_document_chunks v "
                        "  ON v.derivation_id = d.id "
                        "GROUP BY d.id, v.research_document_version_id "
                        "ORDER BY count(*) DESC LIMIT 1"
                    )
                )
            ).first()
            if row is None:
                raise SystemExit("no derivation with chunks in this database")
            derivation_id, version_id = row[0], row[1]
        else:
            version_id = (
                await conn.execute(
                    sql(
                        "SELECT research_document_version_id::text FROM research_document_chunks "
                        "WHERE derivation_id = :d LIMIT 1"
                    ),
                    {"d": derivation_id},
                )
            ).scalar_one()

        pages = [
            ParsedPage(page_number=r[0], text=r[1], char_start=r[2], char_end=r[3])
            for r in await conn.execute(
                sql(
                    "SELECT page_number, text, char_start, char_end FROM research_document_pages "
                    "WHERE derivation_id = :d ORDER BY page_number"
                ),
                {"d": derivation_id},
            )
        ]
        sections = [
            ParsedSection(
                section_index=r[0],
                heading=r[1],
                heading_path=r[2],
                page_start=r[3],
                page_end=r[4],
                char_start=r[5],
                char_end=r[6],
                scope_type=r[7],
                scope_name=r[8],
                scope_key=r[9],
            )
            for r in await conn.execute(
                sql(
                    "SELECT section_index, heading, heading_path, page_start, page_end, "
                    "char_start, char_end, scope_type, scope_name, scope_key "
                    "FROM research_document_sections WHERE derivation_id = :d "
                    "ORDER BY section_index"
                ),
                {"d": derivation_id},
            )
        ]
        tables = [
            ParsedTable(
                table_index=r[0],
                table_location=r[1],
                page_number=r[2],
                rows=r[3] or [],
                row_count=r[4],
                col_count=r[5],
                reconstructed=r[6],
                column_periods=r[7] or [],
                scope_type=r[8],
                scope_name=r[9],
                scope_key=r[10],
                extraction_method=r[11],
                confidence=r[12],
            )
            for r in await conn.execute(
                sql(
                    "SELECT table_index, table_location, page_number, rows_json, row_count, "
                    "col_count, reconstructed, column_periods, scope_type, scope_name, "
                    "scope_key, extraction_method, confidence "
                    "FROM research_document_tables WHERE derivation_id = :d ORDER BY table_index"
                ),
                {"d": derivation_id},
            )
        ]

    parsed = ParsedDocument(
        pipeline_version=1,
        extraction_method="rebuilt-from-corpus",
        paginated=True,
        extraction_profile=PROFILE_LIVE,
        pages=pages,
        sections=sections,
        tables=tables,
        page_count=len(pages),
        char_count=sum(len(p.text) for p in pages),
    )
    return parsed, derivation_id, version_id


async def _baseline(engine, derivation_id: str) -> dict[str, int]:
    """What the CURRENTLY PERSISTED chunks say, for an honest before/after."""
    async with engine.connect() as conn:
        rows = list(
            await conn.execute(
                sql(
                    "SELECT scope_type, count(*) FROM research_document_chunks "
                    "WHERE derivation_id = :d GROUP BY scope_type"
                ),
                {"d": derivation_id},
            )
        )
    counts = {"group": 0, "segment": 0, "unknown": 0}
    for scope_type, n in rows:
        counts["unknown" if scope_type is None else scope_type] += n
    return counts


def _report(parsed, chunks, baseline, expectations, forbid_group_terms) -> dict:
    import uuid as _uuid

    full = parsed.full_text()
    vocab = learn_segment_vocabulary(
        section_texts=[
            (s.heading_path, full[s.char_start : s.char_end])
            for s in parsed.sections
            if full[s.char_start : s.char_end].strip()
        ],
        table_rows=[(t.table_location, t.rows) for t in parsed.tables],
    )

    methods = Counter(c.scope_method or "unknown" for c in chunks)
    ambiguity = Counter(c.scope_ambiguity for c in chunks if c.scope_ambiguity)
    now = {"group": 0, "segment": 0, "unknown": 0}
    for c in chunks:
        now["unknown" if c.scope_type is None else c.scope_type] += 1

    print(BAR)
    print("SCOPE ACCEPTANCE")
    print(BAR)
    print(f"chunks            {len(chunks)}")
    print(f"segments learned  {len(vocab.names)}: {', '.join(vocab.names) or '(none)'}")
    for name, src in vocab.sources.items():
        print(f"                  {name!r} <- {src}")

    print("\n--- coverage ---")
    total = max(len(chunks), 1)
    print(f"{'':18}{'before':>10}{'after':>10}   delta")
    for key in ("group", "segment", "unknown"):
        d = now[key] - baseline.get(key, 0)
        print(f"{key:18}{baseline.get(key, 0):>10}{now[key]:>10}   {d:+d}")
    known = now["group"] + now["segment"]
    was = baseline.get("group", 0) + baseline.get("segment", 0)
    print(
        f"{'scope coverage':18}{was / total:>9.1%}{known / total:>10.1%}"
        f"   {(known - was) / total:+.1%}"
    )

    print("\n--- attribution method ---")
    for method, n in methods.most_common():
        print(f"  {method:<18} {n}")
    if ambiguity:
        print("\n--- why a chunk stayed unknown ---")
        for reason, n in ambiguity.most_common():
            print(f"  {reason:<34} {n}")

    # ── the safety metric ─────────────────────────────────────────────────── #
    print("\n--- SAFETY: false-positive Group ---")
    false_group = []
    for c in chunks:
        if c.scope_type != "group":
            continue
        named = vocab.matches_in(c.text)
        if named:
            false_group.append((c.chunk_id, named, (c.text or "")[:90]))
    rate = len(false_group) / max(now["group"], 1) if now["group"] else 0.0
    print(f"  group-labelled chunks           {now['group']}")
    print(f"  ...that name a reporting segment {len(false_group)}")
    print(f"  false-positive Group rate        {rate:.1%}")
    for cid, named, excerpt in false_group:
        print(f"    ✗ {cid[:12]} names {named} :: {excerpt}")

    # ── forbidden promotions ──────────────────────────────────────────────── #
    violations = list(false_group)
    for term in forbid_group_terms:
        for c in chunks:
            if c.scope_type == "group" and term.casefold() in (c.text or "").casefold():
                violations.append((c.chunk_id, [term], (c.text or "")[:90]))

    # ── expectations ──────────────────────────────────────────────────────── #
    results = []
    if expectations:
        print("\n--- regression expectations ---")
    for probe, want in expectations:
        hits = [c for c in chunks if probe.casefold() in (c.text or "").casefold()]
        if not hits:
            print(f"  ?  {probe!r}: not present in this corpus — cannot assert")
            results.append({"probe": probe, "status": "absent"})
            continue
        for c in hits:
            got = c.scope_key
            if want == "!group":
                ok = got != "group"
                label = "must never be group"
            elif want.endswith("?"):
                # "resolve correctly OR stay explicitly unknown, never group" — the
                # standard the acceptance brief sets for evidence whose chunk may
                # legitimately carry more than one scope.
                exact = want[:-1]
                ok = got in (exact, None)
                label = f"must be {exact} or unknown"
            else:
                ok = got == want
                label = f"must be {want}"
            mark = "✓" if ok else "✗"
            print(f"  {mark}  {probe[:40]!r:<44} {label:<34} got={got!r} ({c.scope_method})")
            results.append(
                {
                    "probe": probe,
                    "chunk": c.chunk_id,
                    "want": want,
                    "got": got,
                    "method": c.scope_method,
                    "ok": ok,
                }
            )
            if not ok:
                violations.append((c.chunk_id, [probe], f"expected {want}, got {got}"))

    ok = not violations
    print("\n" + BAR)
    print("PASS — coverage improved with no unsafe Group promotion" if ok else "FAIL")
    print(BAR)
    _ = _uuid
    return {
        "chunks": len(chunks),
        "segments_learned": list(vocab.names),
        "before": baseline,
        "after": now,
        "methods": dict(methods),
        "ambiguity": dict(ambiguity),
        "false_positive_group": len(false_group),
        "false_positive_group_rate": rate,
        "expectations": results,
        "ok": ok,
    }


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--database-url", required=True)
    ap.add_argument("--derivation-id", default=None)
    ap.add_argument(
        "--expect",
        action="append",
        default=[],
        metavar="TEXT=SCOPE",
        help="assert every chunk containing TEXT resolves to SCOPE: 'segment:x' or "
        "'group' (exactly), 'segment:x?' (that scope or unknown, never anything else), "
        "or '!group' (anything but group)",
    )
    ap.add_argument(
        "--forbid-group",
        action="append",
        default=[],
        metavar="TERM",
        help="fail if any chunk containing TERM is labelled group",
    )
    ap.add_argument("--json", default=None)
    args = ap.parse_args()

    engine = create_async_engine(args.database_url, future=True)
    try:
        parsed, derivation_id, version_id = await _load(engine, args.derivation_id)
        baseline = await _baseline(engine, derivation_id)
    finally:
        await engine.dispose()

    import uuid

    chunks = build_chunks(parsed, research_document_version_id=uuid.UUID(version_id))
    expectations = [tuple(e.split("=", 1)) for e in args.expect if "=" in e]
    result = _report(parsed, chunks, baseline, expectations, args.forbid_group)
    if args.json:
        Path(args.json).write_text(json.dumps(result, indent=2))
        print(f"\nwrote {args.json}")
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
