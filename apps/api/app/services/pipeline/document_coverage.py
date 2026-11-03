"""ADR-084 — what the platform HOLDS, and what it read: the primary-document coverage audit.

Two things, both bounded and never raising:

* ``held_primary_documents`` — the subject's held PRIMARY documents (issuer and regulatory
  filings, results releases, venue disclosures, the issuer's own releases on its verified
  domain — the ``corpus.search.types.is_disclosure`` rule), each with how much of it is
  indexed. Computed ONCE per run, before the questions are asked, and handed to the
  investigator so its re-read knows what it is re-reading and its gaps can name it.
* ``build_coverage`` — the audit the report payload carries under
  ``v3_research.primary_document_coverage`` (an admin / technical surface): documents
  acquired; pages, sections and chunks parsed; tables inspected; candidate fact
  sections; metric-bearing passages read; extraction failures; and the questions left
  unresolved although a relevant primary document was held.

"Facts extracted" here counts metric-bearing PASSAGES the re-read put in front of the
investigator. Statement-table extraction into structured facts is a separate, later
change; this audit does not pretend it happened.
"""

from __future__ import annotations

import logging
import uuid
from collections.abc import Iterable, Mapping, Sequence
from typing import Any

from app.services.agents.document_reread import (
    DOCUMENT_ACQUIRED_NOT_PARSED,
    DOCUMENT_PARSE_FAILED,
    DOCUMENT_PARSED_FACT_FOUND,
    EXTRACTION_STATUSES,
    document_name,
)

logger = logging.getLogger(__name__)

#: The acquisition-log rung the investigator's re-read writes.
REREAD_RUNG = "primary_document_reread"
#: Bounds on what the payload carries.
MAX_INVENTORY = 60
MAX_STATUS_ROWS = 200
MAX_UNRESOLVED = 40
#: Slot states that mean the document is held and searchable.
_READY_SLOT_STATES = frozenset({"ready", "acquired", "reused"})


async def held_primary_documents(
    session: Any, company_id: uuid.UUID | None, *, limit: int = MAX_INVENTORY
) -> list[dict[str, Any]] | None:
    """The subject's held primary documents (current versions), newest first.

    ``None`` when the lookup fails — "unknown" is not "nothing held". Read in a
    SAVEPOINT so a failure cannot abort the run's transaction.
    """
    if session is None or company_id is None:
        return None
    from sqlalchemy import case, func, or_, select

    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocument as D
    from app.models.research_document import ResearchDocumentVersion as V
    from app.services.corpus.search.types import is_disclosure_fields

    owned_versions = select(C.research_document_version_id).where(C.company_id == company_id)
    stmt = (
        select(
            V.id, V.title, D.title, D.document_type, V.canonical_url, V.published_at,
            V.source_tier, V.source_class, V.access_class, V.extraction_status,
            func.count(C.id), func.count(C.indexed_at),
            func.sum(case((C.kind == "table", 1), else_=0)),
            func.max(C.page_end), func.count(func.distinct(C.section_path)),
        )
        .join(D, D.id == V.research_document_id)
        .outerjoin(C, C.research_document_version_id == V.id)
        .where(V.is_current.is_(True), or_(D.company_id == company_id, V.id.in_(owned_versions)))
        .group_by(
            V.id, V.title, D.title, D.document_type, V.canonical_url, V.published_at,
            V.source_tier, V.source_class, V.access_class, V.extraction_status,
        )
        .order_by(V.published_at.desc().nulls_last())
        .limit(max(limit * 3, limit))
    )
    try:
        async with session.begin_nested():
            rows = (await session.execute(stmt)).all()
    except Exception:  # noqa: BLE001 - an unknown inventory is reported as unknown
        logger.warning("primary-document inventory lookup failed")
        return None
    out: list[dict[str, Any]] = []
    for row in rows:
        (version_id, v_title, d_title, doc_type, url, published, tier, source_class,
         access_class, extraction, chunks, indexed, tables, pages, sections) = row
        if not is_disclosure_fields(
            source_tier=tier, source_class=source_class, access_class=access_class
        ):
            continue
        out.append(
            {
                "version_id": str(version_id),
                "title": (v_title or d_title or None),
                "document_type": doc_type,
                "canonical_url": url,
                "published_at": published.isoformat() if published else None,
                "source_tier": tier,
                "extraction_status": extraction,
                "chunks": int(chunks or 0),
                "indexed_chunks": int(indexed or 0),
                "table_chunks": int(tables or 0),
                "pages": int(pages or 0),
                "sections": int(sections or 0),
            }
        )
        if len(out) >= limit:
            break
    return out


def _slot_failures(
    core_filings: Mapping[str, Any] | None, core_disclosures: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Core documents the pre-run acquisition held but could not make searchable."""
    out: list[dict[str, Any]] = []
    for slot in ("annual", "quarterly"):
        item = (core_filings or {}).get(slot)
        if not isinstance(item, Mapping) or not item.get("form"):
            continue
        state = str(item.get("state") or "")
        if state in _READY_SLOT_STATES or state in {"", "not_listed", "absent"}:
            continue
        out.append({
            "document": document_name({"form": item.get("form"),
                                       "published_at": item.get("filing_date")}),
            "slot": slot, "state": state,
            "status": (DOCUMENT_ACQUIRED_NOT_PARSED if state == "historical_without_chunks"
                       else DOCUMENT_PARSE_FAILED),
            "reason": str(item.get("reason") or "")[:200] or None,
        })
    for item in (core_disclosures or {}).get("documents") or []:
        if not isinstance(item, Mapping) or item.get("state") in _READY_SLOT_STATES:
            continue
        reason = str(item.get("reason") or "")
        if "extract" not in reason and "index" not in reason:
            continue  # not fetched at all: not held, so not an extraction failure
        out.append({
            "document": document_name({"title": item.get("title"),
                                       "published_at": item.get("filing_date"),
                                       "document_type": item.get("document_kind")}),
            "slot": "disclosure", "state": str(item.get("state") or ""),
            "status": (DOCUMENT_PARSE_FAILED if "extract" in reason
                       else DOCUMENT_ACQUIRED_NOT_PARSED),
            "reason": reason[:200] or None,
        })
    return out


def reread_steps(log: Iterable[Mapping[str, Any]] | None) -> list[Mapping[str, Any]]:
    return [s for s in (log or []) if isinstance(s, Mapping) and s.get("rung") == REREAD_RUNG]


def build_coverage(
    *,
    inventory: Sequence[Mapping[str, Any]] | None,
    questions: Sequence[Mapping[str, Any]],
    core_filings: Mapping[str, Any] | None = None,
    core_disclosures: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """The audit. ``questions``: ``{question_key, contract_status, acquisition_log}``."""
    docs = list(inventory or [])
    status_rows: list[dict[str, Any]] = []
    tables_inspected = 0
    candidate_sections = 0
    metric_passages = 0
    queries = 0
    unresolved: list[dict[str, Any]] = []
    for question in questions:
        key = str(question.get("question_key") or "")
        steps = reread_steps(question.get("acquisition_log"))
        for step in steps:
            queries += len(step.get("queries") or [])
            tables_inspected += int(step.get("table_hits") or 0)
            candidate_sections += int(step.get("candidate_sections") or 0)
            metric_passages += int(step.get("metric_passages") or 0)
            for row in step.get("extraction_status") or []:
                if isinstance(row, Mapping) and row.get("status") in EXTRACTION_STATUSES:
                    status_rows.append({"question_key": key, "document": row.get("document"),
                                        "status": row.get("status")})
        held = max((int(s.get("held_primary_documents") or 0) for s in steps), default=0)
        if question.get("contract_status") != "satisfied" and held:
            named = next(
                (list(s.get("documents") or []) for s in reversed(steps) if s.get("documents")),
                [],
            )
            unresolved.append({
                "question_key": key,
                "held_primary_documents": held,
                "documents": named[:4],
                "found_in": sorted({
                    str(r.get("document")) for s in steps
                    for r in (s.get("extraction_status") or [])
                    if isinstance(r, Mapping) and r.get("status") == DOCUMENT_PARSED_FACT_FOUND
                })[:4],
            })
    by_status: dict[str, int] = {}
    for row in status_rows:
        by_status[row["status"]] = by_status.get(row["status"], 0) + 1
    failures = [
        {"document": document_name(d), "status": (
            DOCUMENT_PARSE_FAILED if d.get("extraction_status") == "extraction_failed"
            else DOCUMENT_ACQUIRED_NOT_PARSED)}
        for d in docs
        if d.get("extraction_status") == "extraction_failed"
        or not int(d.get("indexed_chunks") or 0)
    ] + _slot_failures(core_filings, core_disclosures)
    return {
        "version": 1,
        "inventory_known": inventory is not None,
        "documents_acquired": len(docs),
        "documents": [
            {
                "document": document_name(d),
                "document_type": d.get("document_type"),
                "published_at": d.get("published_at"),
                "chunks": int(d.get("chunks") or 0),
                "indexed_chunks": int(d.get("indexed_chunks") or 0),
                "table_chunks": int(d.get("table_chunks") or 0),
                "pages": int(d.get("pages") or 0),
                "sections": int(d.get("sections") or 0),
                "extraction_status": d.get("extraction_status"),
            }
            for d in docs
        ],
        "pages_parsed": sum(int(d.get("pages") or 0) for d in docs),
        "sections_parsed": sum(int(d.get("sections") or 0) for d in docs),
        "chunks_parsed": sum(int(d.get("indexed_chunks") or 0) for d in docs),
        "tables_held": sum(int(d.get("table_chunks") or 0) for d in docs),
        "tables_inspected": tables_inspected,
        "reread_queries": queries,
        "candidate_fact_sections": candidate_sections,
        "facts_extracted": metric_passages,
        "facts_extracted_note": (
            "metric-bearing passages the re-read put in front of the investigator; "
            "statement-table extraction into structured facts is not part of this audit"
        ),
        "extraction_failures": failures[:MAX_INVENTORY],
        "extraction_status_counts": dict(sorted(by_status.items())),
        "extraction_status": status_rows[:MAX_STATUS_ROWS],
        "unresolved_despite_documents": unresolved[:MAX_UNRESOLVED],
        "note": (
            "Admin / technical audit. A question listed under unresolved_despite_documents "
            "was left open while a primary document the platform holds was relevant to it: "
            "the gap is in what was read, not evidence that the information does not exist."
        ),
    }


async def coverage_for_run(
    session: Any,
    run: Any,
    *,
    inventory: Sequence[Mapping[str, Any]] | None,
    core_filings: Mapping[str, Any] | None = None,
    core_disclosures: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """``build_coverage`` over the run's question rows (their FULL acquisition logs)."""
    from sqlalchemy import select

    from app.models.ledger import ResearchQuestion

    try:
        async with session.begin_nested():
            rows = (
                await session.execute(
                    select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
                )
            ).scalars().all()
        questions = [
            {"question_key": r.question_key, "contract_status": r.contract_status,
             "acquisition_log": list(r.acquisition_log_json or [])}
            for r in rows
        ]
    except Exception:  # noqa: BLE001 - the audit must not cost the report
        logger.warning("primary-document coverage: question rows unreadable")
        questions = []
    return build_coverage(
        inventory=inventory, questions=questions,
        core_filings=core_filings, core_disclosures=core_disclosures,
    )


__all__ = [
    "REREAD_RUNG",
    "build_coverage",
    "coverage_for_run",
    "held_primary_documents",
    "reread_steps",
]
