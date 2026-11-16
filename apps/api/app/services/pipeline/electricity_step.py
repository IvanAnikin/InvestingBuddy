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
    from app.services.sources.inline_xbrl_facts import EXTRACTION_METHOD_INLINE_XBRL

    texts: list[CitedText] = []
    description = (getattr(company, "description", None) or "").strip()
    if description:
        updated = getattr(company, "updated_at", None)
        texts.append(CitedText(
            text=description, source_kind="company_profile",
            source_url=getattr(company, "website", None) or None,
            as_of=updated.date().isoformat() if updated is not None else None,
            label="Stored company description"))

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
            case((ExtractedFact.extraction_method == EXTRACTION_METHOD_INLINE_XBRL, 0),
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


__all__ = ["electricity_layers_section", "load_inputs"]
