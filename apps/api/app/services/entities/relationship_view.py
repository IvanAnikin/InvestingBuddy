"""The company relationship graph, grouped for a reader — V3.20 slice 2.1."""

from __future__ import annotations

import uuid
from typing import Any

from app.schemas.relationships import CompanyRelationshipsRead, RelationshipEdgeRead
from app.services.entities.relationships import RelatedEntity, relationships_for

#: The label a company READS an edge as → the group it belongs to.
LABEL_GROUP: dict[str, str] = {
    "subsidiary_of": "parents",
    "ultimately_controlled_by": "ultimate_parents",
    "parent_of": "subsidiaries",
    # We supply them → they are our customers; we are their customer → our supplier.
    "supplier_of": "customers",
    "customer_of": "suppliers",
    "significantly_held_by": "significant_holders",
    "significant_holder_of": "holdings",
}

GROUPS: tuple[str, ...] = (
    "parents", "ultimate_parents", "subsidiaries", "customers", "suppliers",
    "significant_holders", "holdings", "other",
)


def edge_read(r: RelatedEntity) -> RelationshipEdgeRead:
    return RelationshipEdgeRead(
        relationship_id=r.relationship_id,
        label=r.label,
        canonical_type=r.canonical_type,
        counterparty_entity_id=r.entity.id if r.entity is not None else None,
        counterparty_name=r.display_name,
        counterparty_resolved=r.entity is not None,
        disclosed_share_pct=r.disclosed_share_pct,
        share_basis=r.share_basis,
        period=r.period or None,
        as_of=r.as_of,
        source=r.source,
        source_kind=r.source_kind,
        source_url=r.source_url,
        evidence_ref=r.evidence_ref,
        evidence_excerpt=r.evidence_excerpt,
        extraction_method=r.extraction_method,
        confidence=r.confidence,
        effective_from=r.effective_from,
    )


def group_edges(related: list[RelatedEntity]) -> dict[str, list[RelationshipEdgeRead]]:
    groups: dict[str, list[RelationshipEdgeRead]] = {g: [] for g in GROUPS}
    for r in related:
        groups[LABEL_GROUP.get(r.label, "other")].append(edge_read(r))
    # Largest disclosed share first, then most recent; undisclosed shares after.
    for edges in groups.values():
        edges.sort(key=lambda e: (
            -(e.disclosed_share_pct if e.disclosed_share_pct is not None else -1.0),
            e.counterparty_name,
            e.period or "",
        ))
    return groups


async def company_relationships(
    session: Any, *, company: Any, cfg: Any
) -> CompanyRelationshipsRead:
    company_id: uuid.UUID = company.id
    entity_id = getattr(company, "legal_entity_id", None)
    empty: dict[str, list[RelationshipEdgeRead]] = {g: [] for g in GROUPS}
    if not getattr(cfg, "v3_entity_master_enabled", False):
        return CompanyRelationshipsRead(company_id=company_id, legal_entity_id=entity_id,
                                        state="disabled", groups=empty, total=0)
    if entity_id is None:
        return CompanyRelationshipsRead(company_id=company_id, legal_entity_id=None,
                                        state="entity_unresolved", groups=empty, total=0)
    related = await relationships_for(session, legal_entity_id=entity_id, cfg=cfg)
    return CompanyRelationshipsRead(
        company_id=company_id, legal_entity_id=entity_id, state="ok",
        groups=group_edges(related), total=len(related),
    )


__all__ = ["GROUPS", "LABEL_GROUP", "company_relationships", "group_edges"]
