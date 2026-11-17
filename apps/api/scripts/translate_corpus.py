"""Translate held non-English documents into a parallel English corpus (V3.20 10.1).

An OPERATOR job: nothing schedules it, because every chunk is one Azure OpenAI call.
It needs ``V3_TRANSLATION_ENABLED=true`` and the Azure OpenAI settings; without them it
refuses before anything is read. The originals are never changed: translations are
stored in ``research_chunk_translations`` beside them, and the evidence stays the
original text.

    # what would be translated (no LLM call, nothing stored):
    python scripts/translate_corpus.py --company-id <uuid> --dry-run
    python scripts/translate_corpus.py --company-id <uuid>
    python scripts/translate_corpus.py --version-id <uuid> --max-chunks 100

The summary printed per document carries counts and token usage, never document text.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import uuid
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.core.config import settings  # noqa: E402
from app.db.session import async_session_factory  # noqa: E402


async def _plan(session: Any, version_ids: list[uuid.UUID]) -> list[dict[str, Any]]:
    """The dry run: each version's detected language and chunk count. No LLM call."""
    from sqlalchemy import select

    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.services.corpus.translation import _document_language

    out = []
    for vid in version_ids:
        chunks = list((await session.execute(
            select(C).where(C.research_document_version_id == vid)
            .order_by(C.ordinal))).scalars().all())
        out.append({"version_id": str(vid), "chunks": len(chunks),
                    "language": _document_language(chunks) if chunks else None})
    return out


async def _versions_of(session: Any, company_id: uuid.UUID, limit: int) -> list[uuid.UUID]:
    from sqlalchemy import func, select

    from app.models.research_chunk import ResearchDocumentChunk as C

    rows = (await session.execute(
        select(C.research_document_version_id)
        .where(C.company_id == company_id)
        .group_by(C.research_document_version_id)
        .order_by(func.max(C.published_at).desc().nulls_last())
        .limit(limit))).all()
    return [r[0] for r in rows]


async def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--company-id", type=uuid.UUID)
    target.add_argument("--version-id", type=uuid.UUID)
    parser.add_argument("--max-documents", type=int, default=20)
    parser.add_argument("--max-chunks", type=int, default=None,
                        help="per-document cap (default: the configured cap)")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    from app.services.agents.routing import translation_client_for
    from app.services.corpus.translation import translate_version

    cfg = settings
    if args.max_chunks is not None:
        cfg = settings.model_copy(
            update={"v3_translation_max_chunks_per_document": max(1, args.max_chunks)})
    async with async_session_factory() as session:
        versions = ([args.version_id] if args.version_id
                    else await _versions_of(session, args.company_id, args.max_documents))
        if args.dry_run:
            print(json.dumps(await _plan(session, versions), indent=2))
            return 0
        client = translation_client_for(cfg)
        if client is None:
            print("refused: V3_TRANSLATION_ENABLED is off or Azure OpenAI is not "
                  "configured", file=sys.stderr)
            return 2
        summaries = []
        for vid in versions:
            summaries.append(await translate_version(session, vid, cfg=cfg, client=client))
            await session.commit()  # a document at a time: an interruption loses one
        print(json.dumps(summaries, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(asyncio.run(main()))
