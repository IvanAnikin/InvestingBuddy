"""V3.20 slice 5.2 — load what the platform holds and classify the company's layers.

Inputs, both already held (no external call):

* the company's stored business description (``companies.description``);
* active segment-scoped revenue facts from the company's own extracted documents
  (``company_documents_clause``), inline XBRL first, then by confidence — so when the same
  segment was read twice, the better reading is the one that counts.

Additive: returns ``None`` when the flag is off or anything fails, and the report is then
unchanged.
"""

from __future__ import annotations

import logging
from typing import Any

from app.services.electricity.classifier import CitedText, SegmentRevenue, classify

logger = logging.getLogger(__name__)

#: Segment revenue facts read per company.
MAX_SEGMENT_FACTS = 400
#: The annual report's business section is read in at most this many prose chunks.
MAX_BUSINESS_CHUNKS = 12
_ANNUAL_TYPES = ("annual_report", "sec_filing")
_PRIMARY_TIER = "T1_primary_filing"


async def _business_section_texts(session: Any, company_id: Any) -> list[CitedText]:
    """The company's latest held annual report, business section first.

    V3.20 live wave A (2026-11-21): the classifier read only ``company.description``
    and segment-revenue facts. Neither exists for most companies (Powell Industries and
    Preformed Line Products both had an empty description), so the electricity layer was
    never assigned. The annual report the run already holds says what the company does,
    in its own words: chunks whose section heading names the business, else the first
    chunks of the document. Cited by the document's URL and date.
    """
    from sqlalchemy import select

    from app.models.research_chunk import CHUNK_KIND_PROSE, ResearchDocumentChunk
    from app.models.research_document import ResearchDocumentVersion

    chunk = ResearchDocumentChunk
    base = (
        select(chunk.text, chunk.section_path, chunk.ordinal, chunk.published_at,
               chunk.research_document_version_id,
               ResearchDocumentVersion.canonical_url, ResearchDocumentVersion.title)
        .join(ResearchDocumentVersion,
              ResearchDocumentVersion.id == chunk.research_document_version_id)
        .where(chunk.company_id == company_id, chunk.source_tier == _PRIMARY_TIER,
               chunk.kind == CHUNK_KIND_PROSE, chunk.document_type.in_(_ANNUAL_TYPES))
    )
    async with session.begin_nested():
        latest = (await session.execute(
            base.with_only_columns(chunk.research_document_version_id)
            .order_by(chunk.published_at.desc().nulls_last()).limit(1)
        )).scalar_one_or_none()
        if latest is None:
            return []
        in_doc = base.where(chunk.research_document_version_id == latest)
        rows = (await session.execute(
            in_doc.where(chunk.section_path.ilike("%business%"))
            .order_by(chunk.ordinal).limit(MAX_BUSINESS_CHUNKS))).all()
        if not rows:
            rows = (await session.execute(
                in_doc.order_by(chunk.ordinal).limit(MAX_BUSINESS_CHUNKS))).all()
    return [
        CitedText(
            text=str(text), source_kind="annual_report_business_section",
            source_url=url or None,
            as_of=published.isoformat()[:10] if published is not None else None,
            label=str(title or "Annual report") + (f" — {section}" if section else ""))
        for text, section, _ordinal, published, _version, url, title in rows if text
    ]

NOTE = (
    "Layers of the electricity stack assigned from the company's own description and "
    "reported segment revenue. A layer needs a segment whose name contains one of the "
    "layer's anchors, or at least two distinct anchors in the description. Revenue shares "
    "come from one period, one currency and one segment axis."
)


async def load_inputs(session: Any, company: Any) -> tuple[list[CitedText], list[SegmentRevenue]]:
    from sqlalchemy import case, select

    from app.models.extracted_document import ExtractedDocument, ExtractedFact
    from app.services.sources.company_documents import company_documents_clause
    from app.services.sources.inline_xbrl_facts import TAGGED_EXTRACTION_METHODS

    texts: list[CitedText] = []
    description = (getattr(company, "description", None) or "").strip()
    if description:
        updated = getattr(company, "updated_at", None)
        texts.append(CitedText(
            text=description, source_kind="company_profile",
            source_url=getattr(company, "website", None) or None,
            as_of=updated.date().isoformat() if updated is not None else None,
            label="Stored company description"))

    try:
        texts.extend(await _business_section_texts(session, company.id))
    except Exception:  # noqa: BLE001 - the description and segments still classify
        logger.warning("electricity business-section read failed")

    stmt = (
        select(ExtractedFact.id, ExtractedFact.scope_name, ExtractedFact.value_numeric,
               ExtractedFact.currency, ExtractedFact.scale, ExtractedFact.period,
               ExtractedDocument.doc_date, ExtractedDocument.canonical_url)
        .join(ExtractedDocument, ExtractedFact.extracted_document_id == ExtractedDocument.id)
        .where(
            company_documents_clause(company.id),
            ExtractedFact.is_active.is_(True),
            ExtractedFact.scope_type == "segment",
            ExtractedFact.label == "revenue",
            ExtractedFact.value_numeric.is_not(None),
            ExtractedFact.scope_name.is_not(None),
        )
        .order_by(
            case((ExtractedFact.extraction_method.in_(TAGGED_EXTRACTION_METHODS), 0),
                 else_=1),
            ExtractedFact.confidence.desc(),
        )
        .limit(MAX_SEGMENT_FACTS)
    )
    async with session.begin_nested():
        rows = (await session.execute(stmt)).all()
    segments = [
        SegmentRevenue(
            name=str(name), value=value, currency=currency, period=str(period or ""),
            scale=scale, doc_date=doc_date.isoformat() if doc_date else None,
            source_url=url, fact_id=str(fid))
        for fid, name, value, currency, scale, period, doc_date, url in rows
    ]
    return texts, segments


async def electricity_layers_section(
    session: Any, company: Any, *, cfg: Any,
) -> dict[str, Any] | None:
    """The report's "Electricity layers" block, or ``None`` (off / failed)."""
    if not getattr(cfg, "v3_electricity_classifier_enabled", False):
        return None
    try:
        texts, segments = await load_inputs(session, company)
        from app.schemas.catalyst import neutralize_forbidden_terms

        block = classify(texts, segments)
        # Quotes are the issuer's own prose: pass them through the same neutraliser
        # as every other externally sourced string, so a quote can never withhold
        # the report at the final safety scan.
        for lay in block["layers"] + block["candidate_layers"]:
            for ev in lay["evidence"]:
                ev["quote"] = neutralize_forbidden_terms(ev["quote"])
        block["note"] = NOTE
        return block
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("electricity layer classification failed")
        return None


#: The playbook-selection signal a classified company gets (slice 5.3).
SELECTION_SIGNAL = "electricity_value_chain"


async def electricity_selection_signals(
    session: Any, company: Any, *, cfg: Any,
) -> tuple[str, ...]:
    """``(SELECTION_SIGNAL,)`` when the company is classified into a layer, else ``()``.

    Only ``classified`` counts: an ``excluded`` company (a gold miner that also sells
    wire) does not get the electricity methodology. Never raises.
    """
    if not getattr(cfg, "v3_electricity_classifier_enabled", False):
        return ()
    try:
        texts, segments = await load_inputs(session, company)
        if classify(texts, segments)["status"] == "classified":
            return (SELECTION_SIGNAL,)
    except Exception:  # noqa: BLE001 - selection proceeds on the other signals
        logger.warning("electricity selection signal failed")
    return ()


__all__ = [
    "SELECTION_SIGNAL",
    "electricity_layers_section",
    "electricity_selection_signals",
    "load_inputs",
]
