"""Company research step: named counterparties from HELD document text — V3.20 slice 2.5.

ONE held primary document per run, ONE model call:

1. pick the company's own primary documents in the corpus (``source_tier``
   ``T1_primary_filing``, chunks carrying the company's id) that this step has not read
   yet — annual reports and filings first, then the newest;
2. offer the document's chunks that carry counterparty words (``candidate_score``), at
   most ``MAX_PASSAGES``;
3. ask the run's investigator-slot model for claims; verify every claim through
   ``counterparty_text.verify_claims`` (verbatim quote, name, role, figure, ADR-083
   attribution, framing, period);
4. write only what was accepted; record the document as read (a gated ingestion
   attempt), so the next run reads the next document instead of paying again.

Never raises; a model or database failure costs this step, said in the outcome.
``_model_reply`` is the network seam the tests replace.
"""

from __future__ import annotations

import secrets
from types import SimpleNamespace
from typing import Any

COUNTERPARTY_STRATEGY = "counterparty_text_v1"
PRIMARY_TIER = "T1_primary_filing"
_CANDIDATE_ROWS = 400
_ANNUAL_TYPES = ("annual_report", "sec_filing")
_LIKE_WORDS = ("%customer%", "%client%", "%supplier%", "%vendor%", "%offtake%",
               "%off-take%", "%framework agreement%", "%sole source%", "%single source%",
               "%sole-source%", "%single-source%", "%power purchase agreement%")
MODEL_MAX_TOKENS = 2_000
MODEL_TIMEOUT = 90


async def _model_reply(client: Any, system: str, user: str) -> Any:
    """The model's JSON reply, from either client shape the router can hand over.

    Live finding 2026-11-21 (POWL1): the investigator slot resolves to a V3 provider
    client, which has ``complete(..., json_mode=)`` returning a ``ModelResponse`` and no
    ``complete_json``. Calling ``complete_json`` unconditionally raised AttributeError on
    every live run, so no named counterparty was ever read. Same two shapes as the
    Investigator and the report editor handle.
    """
    if client is None:
        raise RuntimeError("no model client resolved for the investigator slot")
    if hasattr(client, "complete_json"):
        return await client.complete_json(system, user, max_tokens=MODEL_MAX_TOKENS,
                                          temperature=0.0, timeout=MODEL_TIMEOUT)
    from app.services.agents.investigator import _supports_json_mode

    extra: dict[str, Any] = {"json_mode": True} if _supports_json_mode(client) else {}
    response = await client.complete(system=system, user=user,
                                     max_tokens=MODEL_MAX_TOKENS, timeout=MODEL_TIMEOUT,
                                     **extra)
    payload = getattr(response, "payload", None)
    return payload if isinstance(payload, dict) else {}


async def _candidate_rows(session: Any, company_id: Any) -> list[Any]:
    from sqlalchemy import or_, select

    from app.models.research_chunk import CHUNK_KIND_PROSE, ResearchDocumentChunk
    from app.models.research_document import ResearchDocumentVersion

    text = ResearchDocumentChunk.text
    stmt = (
        select(
            ResearchDocumentChunk.chunk_id, ResearchDocumentChunk.text,
            ResearchDocumentChunk.section_path, ResearchDocumentChunk.period_key,
            ResearchDocumentChunk.document_type, ResearchDocumentChunk.published_at,
            ResearchDocumentChunk.ordinal,
            ResearchDocumentChunk.research_document_version_id.label("version_id"),
            ResearchDocumentVersion.canonical_url, ResearchDocumentVersion.title,
        )
        .join(ResearchDocumentVersion,
              ResearchDocumentVersion.id == ResearchDocumentChunk.research_document_version_id)
        .where(
            ResearchDocumentChunk.company_id == company_id,
            ResearchDocumentChunk.source_tier == PRIMARY_TIER,
            ResearchDocumentChunk.kind == CHUNK_KIND_PROSE,
            or_(*[text.ilike(word) for word in _LIKE_WORDS]),
        )
        .order_by(ResearchDocumentChunk.published_at.desc().nulls_last(),
                  ResearchDocumentChunk.ordinal)
        .limit(_CANDIDATE_ROWS)
    )
    return list((await session.execute(stmt)).all())


async def _read_urls(session: Any, company_id: Any) -> set[str]:
    from sqlalchemy import select

    from app.models.document_ingestion_attempt import DocumentIngestionAttempt

    rows = await session.execute(
        select(DocumentIngestionAttempt.url_hash).where(
            DocumentIngestionAttempt.company_id == company_id,
            DocumentIngestionAttempt.discovery_strategy == COUNTERPARTY_STRATEGY,
        )
    )
    return {r[0] for r in rows.all()}


def choose_document(rows: list[Any], read_hashes: set[str]) -> tuple[Any, list[Any]] | None:
    """The document to read this run and its candidate chunks, best first. Pure."""
    from app.services.document_ingestion_attempt_service import url_hash_of
    from app.services.sources.counterparty_text import MAX_PASSAGES, candidate_score

    documents: dict[Any, list[tuple[int, Any]]] = {}
    for row in rows:
        if not row.canonical_url or url_hash_of(row.canonical_url) in read_hashes:
            continue
        score = candidate_score(row.text)
        if score:
            documents.setdefault(row.version_id, []).append((score, row))
    if not documents:
        return None

    def rank(item: tuple[Any, list[tuple[int, Any]]]) -> tuple[Any, ...]:
        first = item[1][0][1]
        published = first.published_at.toordinal() if first.published_at else 0
        return (first.document_type in _ANNUAL_TYPES, published,
                sum(score for score, _ in item[1]))

    version_id, scored = max(documents.items(), key=rank)
    best = sorted(scored, key=lambda pair: (-pair[0], pair[1].ordinal))[:MAX_PASSAGES]
    return version_id, sorted((row for _, row in best), key=lambda row: row.ordinal)


async def _record_read(session: Any, company_id: Any, url: str, cfg: Any) -> None:
    from app.models.document_ingestion_attempt import STATUS_EXTRACTED
    from app.services.document_ingestion_attempt_service import (
        IngestionAttemptRecord,
        record_ingestion_attempts,
    )

    await record_ingestion_attempts(
        session, company_id=company_id, agent_run_id=None, cfg=cfg,
        attempts=[IngestionAttemptRecord(
            canonical_url=url, source_type="primary_document", source_tier=PRIMARY_TIER,
            status=STATUS_EXTRACTED, discovery_strategy=COUNTERPARTY_STRATEGY,
        )],
    )


async def text_counterparties_step(
    session: Any, company: Any, *, cfg: Any, client: Any,
) -> dict[str, Any]:
    """``{}`` when off; otherwise the step's result for ``outcome.relationships``."""
    if not (getattr(cfg, "v3_text_counterparties_enabled", False)
            and getattr(cfg, "v3_entity_master_enabled", False)):
        return {}
    subject = SimpleNamespace(
        id=getattr(company, "id", None), name=getattr(company, "name", None),
        ticker=getattr(company, "ticker", None),
        legal_entity_id=getattr(company, "legal_entity_id", None),
    )
    if subject.legal_entity_id is None or subject.id is None:
        return {"state": "entity_unresolved",
                "notes": ["the company is not linked to a legal entity"]}
    if client is None or getattr(client, "is_fake", False):
        return {"state": "no_model",
                "notes": ["no research model resolved, so held documents were not read "
                          "for named customers and suppliers"]}
    from app.services.entities.counterparties import record_text_counterparties
    from app.services.entities.passage_subject import SubjectIdentity
    from app.services.sources.counterparty_text import (
        SYSTEM_PROMPT,
        HeldPassage,
        build_prompt,
        parse_reply,
        verify_claims,
    )

    try:
        rows = await _candidate_rows(session, subject.id)
        chosen = choose_document(rows, await _read_urls(session, subject.id))
    except Exception as exc:  # noqa: BLE001 - the step degrades, the run goes on
        return {"state": "error", "errors": [f"held documents unreadable ({type(exc).__name__})"]}
    if chosen is None:
        return {"state": "no_candidates" if not rows else "already_read",
                "notes": ["no unread held primary document names customers or suppliers"]}
    _, chunks = chosen
    passages = [
        HeldPassage(ref=f"P{i}", chunk_id=row.chunk_id, text=row.text,
                    heading=row.section_path, period_key=row.period_key,
                    document_type=row.document_type, published_at=row.published_at,
                    source_url=row.canonical_url, title=row.title)
        for i, row in enumerate(chunks, start=1)
    ]
    document = {"title": chunks[0].title, "source_url": chunks[0].canonical_url,
                "document_type": chunks[0].document_type,
                "period_key": chunks[0].period_key, "passages_offered": len(passages)}
    name = subject.name or subject.ticker or ""
    try:
        reply = await _model_reply(client, SYSTEM_PROMPT,
                                   build_prompt(name, passages, nonce=secrets.token_hex(6)))
    except Exception as exc:  # noqa: BLE001 - a model failure costs this step only
        return {"state": "error", "document": document,
                "errors": [f"counterparty model call failed ({type(exc).__name__})"]}
    claims = parse_reply(reply)
    report = verify_claims(claims, passages,
                           SubjectIdentity(name=subject.name, ticker=subject.ticker))
    try:
        async with session.begin_nested():
            written = await record_text_counterparties(
                session, entity_id=subject.legal_entity_id, accepted=report.accepted,
                cfg=cfg)
            await _record_read(session, subject.id, chunks[0].canonical_url, cfg)
    except Exception as exc:  # noqa: BLE001
        return {"state": "error", "document": document,
                "errors": [f"counterparty write failed ({type(exc).__name__})"]}
    return {
        "state": "written" if written else "none_verified",
        "document": document,
        "claims": len(claims),
        "accepted": written,
        "rejected": dict(sorted(report.rejected.items())),
        **({} if written else {"notes": [
            "no counterparty the model proposed passed verification; not named in this "
            "document is not the same as none"]}),
    }


__all__ = ["COUNTERPARTY_STRATEGY", "choose_document", "text_counterparties_step"]
