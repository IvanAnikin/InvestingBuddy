"""Verified text counterparties → the relationship graph — V3.20 slice 2.5.

Takes what ``app.services.sources.counterparty_text.verify_claims`` ACCEPTED (never a
raw model claim) and writes one edge per counterparty, role, basis and period:

* customer / offtaker / framework partner → ``supplier_of(subject → counterparty)``;
* supplier → ``supplier_of(counterparty → subject)``.

The counterparty stays UNRESOLVED (named by the document's own words): nothing here
turns a name into an entity. ``extraction_method="text_llm_verified"``, ``source_kind``
``document_text``, ``evidence_ref`` ``chunk:<chunk_id>`` (the citable corpus span), the
quote as the excerpt. When an XBRL edge already holds the same counterparty and period,
the existing row wins (``record_relationship`` is idempotent on that identity).

No network client may be imported here (the package boundary test).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

from app.services.entities.relationships import record_relationship
from app.services.entities.vocabulary import REL_SUPPLIER_OF

if TYPE_CHECKING:
    from app.core.config import Settings

TEXT_SOURCE = "document_text"
TEXT_METHOD = "text_llm_verified"
#: A model proposed it and every deterministic gate passed; an XBRL tag or a registry
#: record is still the stronger evidence.
TEXT_CONFIDENCE = 0.8

_ROLE_LABEL = {
    "customer": "customer",
    "supplier": "supplier",
    "offtaker": "offtaker",
    "framework_partner": "framework-agreement customer",
}


def _as_date(value: Any) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    return value if isinstance(value, date) else None


async def record_text_counterparties(
    session: Any, *, entity_id: uuid.UUID, accepted: list[Any], cfg: "Settings",
) -> list[dict[str, Any]]:
    """One edge per accepted counterparty. Flush-only; the caller owns the transaction."""
    from app.services.sources.counterparty_text import BUYER_ROLES

    written: list[dict[str, Any]] = []
    for item in accepted:
        passage = item.passage
        buyer = item.role in BUYER_ROLES
        where = f" ({item.scope_name})" if item.scope_name else ""
        note = (f"{item.kind} {_ROLE_LABEL.get(item.role, item.role)}{where} named in the "
                f"company's own document; period from the {item.period_source or 'document'}")
        edge = await record_relationship(
            session,
            subject_entity_id=entity_id if buyer else None,
            object_entity_id=None if buyer else entity_id,
            counterparty_name=item.name,
            relationship_type=REL_SUPPLIER_OF,
            source=TEXT_SOURCE,
            cfg=cfg,
            source_url=passage.source_url,
            confidence=TEXT_CONFIDENCE,
            evidence_ref=f"chunk:{passage.chunk_id}"[:200],
            source_kind=TEXT_SOURCE,
            extraction_method=TEXT_METHOD,
            evidence_excerpt=item.quote,
            as_of=_as_date(passage.published_at),
            period=item.period,
            note=note[:500],
            disclosed_share_pct=item.share_pct,
            share_basis=item.share_basis,
        )
        written.append({
            "counterparty": item.name, "kind": item.kind, "role": item.role,
            "share_pct": item.share_pct, "share_basis": item.share_basis,
            "period": item.period, "period_source": item.period_source,
            "chunk_id": passage.chunk_id,
            "relationship_id": str(edge.id) if edge is not None else None,
        })
    await session.flush()
    return written


__all__ = ["TEXT_CONFIDENCE", "TEXT_METHOD", "TEXT_SOURCE", "record_text_counterparties"]
