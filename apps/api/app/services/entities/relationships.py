"""Entity relationships — V3.2 Slice 2.4.

ONE CANONICAL DIRECTION, NEVER TWO ROWS THAT DISAGREE
=====================================================
``parent_of`` and ``subsidiary_of`` are the same fact stated twice. A schema that
stores both can hold two rows that contradict each other and has no rule for which
wins, so the vocabulary declares one canonical direction per pair and this module
normalises to it: asserting ``subsidiary_of(A, B)`` writes ``parent_of(B, A)``.

The inverse is therefore a **query**, and ``relationships_for`` returns both
directions with a label for each, so a caller never has to know which way a fact
happened to be stored.

WHY THIS MATTERS BEYOND TIDINESS
================================
Consolidated versus subsidiary reporting is currently unrepresentable, so the
platform cannot reason about whether a filing covers the group or a part of it. That
is a correctness question about every number in a subsidiary's accounts, not a
cataloguing preference.

WHAT IT REFUSES
===============
A self-relationship, because an entity is not its own parent and a schema that can
hold that can hold a cycle nothing detects. A second live relationship for the same
canonical triple, refused by a partial unique index rather than by this module's
memory. And an unsourced relationship, for the same reason an unsourced identifier
is refused: it is a guess, and a guess about corporate structure attributes one
company's accounts to another.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING, Any

from sqlalchemy import or_, select

from app.models.legal_entity import EntityRelationship, LegalEntity
from app.services.entities.vocabulary import (
    REL_SIGNIFICANT_HOLDER_OF,
    REL_SUPPLIER_OF,
    REL_ULTIMATE_PARENT_OF,
    RELATIONSHIP_INVERSE_LABEL,
    counterparty_key,
    is_symmetric_relationship,
    require_extraction_method,
    require_relationship_type,
    require_share_basis,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings


@dataclass(frozen=True)
class RelatedEntity:
    """One relationship as seen FROM a particular entity.

    ``label`` is the relationship read in the direction the caller asked about, so a
    subsidiary looking at its stored ``parent_of`` row sees ``subsidiary_of``. The
    caller never has to know which way the fact was stored.

    V3.20: ``entity`` is ``None`` when the other end is unresolved; ``counterparty_name``
    then names it ("Customer A", a GLEIF parent not yet in the entity master).
    """

    entity: LegalEntity | None
    label: str
    canonical_type: str
    is_subject: bool
    source: str
    confidence: float
    effective_from: date | None
    effective_to: date | None
    counterparty_name: str | None = None
    relationship_id: uuid.UUID | None = None
    source_url: str | None = None
    evidence_ref: str | None = None
    source_kind: str | None = None
    extraction_method: str | None = None
    evidence_excerpt: str | None = None
    as_of: date | None = None
    period: str = ""
    disclosed_share_pct: float | None = None
    share_basis: str | None = None

    @property
    def is_current(self) -> bool:
        return self.effective_to is None

    @property
    def display_name(self) -> str:
        if self.entity is not None:
            return self.entity.legal_name
        return self.counterparty_name or "unnamed counterparty"


#: V3.20 relationship types: an edge of these types is never stored uncited.
CITED_RELATIONSHIP_TYPES: frozenset[str] = frozenset(
    {REL_ULTIMATE_PARENT_OF, REL_SIGNIFICANT_HOLDER_OF, REL_SUPPLIER_OF}
)


def _enabled(cfg: "Settings") -> bool:
    return bool(getattr(cfg, "v3_entity_master_enabled", False))


async def record_relationship(
    session: Any,
    *,
    subject_entity_id: uuid.UUID | None,
    object_entity_id: uuid.UUID | None,
    relationship_type: str,
    source: str,
    cfg: "Settings",
    source_url: str | None = None,
    confidence: float = 1.0,
    effective_from: date | None = None,
    note: str | None = None,
    counterparty_name: str | None = None,
    evidence_ref: str | None = None,
    source_kind: str | None = None,
    extraction_method: str | None = None,
    evidence_excerpt: str | None = None,
    as_of: date | None = None,
    period: str | None = None,
    disclosed_share_pct: float | None = None,
    share_basis: str | None = None,
) -> EntityRelationship | None:
    """Record one relationship in canonical form. Flush-only; the caller commits.

    Idempotent: asserting the same relationship again — in **either** direction —
    returns the existing row rather than creating a mirror of it.

    V3.20 (migration 047): ONE end may be ``None`` (unresolved), and then
    ``counterparty_name`` must name it. Identity of an edge includes its ``period``, so
    a FY2024 and a FY2025 concentration figure for one customer are two rows. An edge
    with neither ``source_url`` nor ``evidence_ref`` is refused: no uncited edges.
    """
    if not _enabled(cfg):
        return None
    canonical, swap = require_relationship_type(relationship_type)
    subject, obj = (
        (object_entity_id, subject_entity_id) if swap
        else (subject_entity_id, object_entity_id)
    )
    if subject is None and obj is None:
        raise ValueError("At least one end of a relationship must be a resolved entity.")
    name_key = counterparty_key(counterparty_name)
    if (subject is None or obj is None) and name_key is None:
        raise ValueError(
            "An unresolved end needs a counterparty name; an edge to nobody is not a fact."
        )
    if subject is not None and subject == obj:
        raise ValueError(
            "An entity cannot be related to itself. A schema that can hold that can "
            "hold a cycle nothing detects."
        )
    if not (source or "").strip():
        raise ValueError(
            "A relationship needs a source. A guess about corporate structure "
            "attributes one company's accounts to another."
        )
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be within 0.0-1.0, got {confidence}.")
    # V3.20 edges (and any edge to an unresolved counterparty) carry their citation.
    # V3.2 identity edges keep the 028 contract: a named source is enough.
    if (canonical in CITED_RELATIONSHIP_TYPES or subject is None or obj is None) and not (
        source_url or evidence_ref
    ):
        raise ValueError(
            "A relationship needs a citation (source_url or evidence_ref): every edge "
            "carries the evidence it rests on."
        )
    if disclosed_share_pct is not None and not (0.0 <= disclosed_share_pct <= 100.0):
        raise ValueError(f"disclosed_share_pct must be within 0-100, got {disclosed_share_pct}.")
    if disclosed_share_pct is not None and share_basis is None:
        raise ValueError("A disclosed share needs its basis (revenue, voting_rights, …).")
    share_basis = require_share_basis(share_basis)
    extraction_method = require_extraction_method(extraction_method)
    period_key = (period or "").strip()[:40]

    # A symmetric relationship has no direction, so the pair is ordered
    # deterministically — otherwise "A joint_venture_with B" and "B joint_venture_with
    # A" are two live rows for one fact and the unique index cannot see it.
    if is_symmetric_relationship(canonical) and subject is not None and obj is not None:
        subject, obj = (subject, obj) if str(subject) <= str(obj) else (obj, subject)

    stmt = select(EntityRelationship).where(
        EntityRelationship.relationship_type == canonical,
        EntityRelationship.effective_to.is_(None),
        EntityRelationship.period == period_key,
    )
    stmt = stmt.where(
        EntityRelationship.subject_entity_id.is_(None) if subject is None
        else EntityRelationship.subject_entity_id == subject
    )
    stmt = stmt.where(
        EntityRelationship.object_entity_id.is_(None) if obj is None
        else EntityRelationship.object_entity_id == obj
    )
    if subject is None or obj is None:
        stmt = stmt.where(EntityRelationship.counterparty_name_key == name_key)
    existing = (await session.execute(stmt)).scalar_one_or_none()
    if existing is not None:
        existing.confidence = max(existing.confidence, confidence)
        existing.source_url = existing.source_url or source_url
        existing.effective_from = existing.effective_from or effective_from
        existing.note = existing.note or note
        existing.evidence_ref = existing.evidence_ref or evidence_ref
        existing.source_kind = existing.source_kind or source_kind
        existing.extraction_method = existing.extraction_method or extraction_method
        existing.evidence_excerpt = existing.evidence_excerpt or (
            evidence_excerpt[:1000] if evidence_excerpt else None
        )
        if as_of is not None and (existing.as_of is None or as_of > existing.as_of):
            existing.as_of = as_of
            # A later disclosure of the same period's share supersedes an earlier one —
            # and its citation moves with it: a share and the document it came from must
            # never disagree (V3.20 slice 2.3: a 13G/A replacing the 13G).
            if disclosed_share_pct is not None:
                existing.disclosed_share_pct = disclosed_share_pct
                existing.share_basis = share_basis
            existing.source_url = source_url or existing.source_url
            if evidence_ref:
                existing.evidence_ref = evidence_ref[:200]
            if evidence_excerpt:
                existing.evidence_excerpt = evidence_excerpt[:1000]
            if source_kind:
                existing.source_kind = source_kind[:40]
            existing.note = note or existing.note
        elif existing.disclosed_share_pct is None and disclosed_share_pct is not None:
            existing.disclosed_share_pct = disclosed_share_pct
            existing.share_basis = share_basis
        await session.flush()
        return existing

    record = EntityRelationship(
        id=uuid.uuid4(),
        subject_entity_id=subject,
        object_entity_id=obj,
        relationship_type=canonical,
        source=source.strip()[:100],
        source_url=source_url,
        confidence=confidence,
        effective_from=effective_from,
        note=note,
        evidence_ref=(evidence_ref or None) and evidence_ref[:200],
        source_kind=(source_kind or None) and source_kind[:40],
        extraction_method=extraction_method,
        evidence_excerpt=evidence_excerpt[:1000] if evidence_excerpt else None,
        as_of=as_of,
        period=period_key,
        disclosed_share_pct=disclosed_share_pct,
        share_basis=share_basis,
        counterparty_name_raw=(counterparty_name or "").strip()[:300] or None,
        counterparty_name_key=name_key,
    )
    session.add(record)
    await session.flush()
    return record


async def close_relationship(
    session: Any,
    *,
    relationship: EntityRelationship,
    effective_to: date,
    cfg: "Settings",
) -> EntityRelationship | None:
    """Close a relationship's window — a divestment, a restructuring.

    Closing rather than deleting: a subsidiary sold in 2024 was still a subsidiary in
    2023, and every figure consolidated then depends on that having been true.
    """
    if not _enabled(cfg):
        return None
    if relationship.effective_to is None:
        relationship.effective_to = effective_to
        await session.flush()
    return relationship


async def relationships_for(
    session: Any,
    *,
    legal_entity_id: uuid.UUID,
    cfg: "Settings",
    include_closed: bool = False,
) -> list[RelatedEntity]:
    """Every relationship this entity is in, read from ITS point of view.

    Both stored directions are returned, each labelled as the caller's entity would
    read it — a subsidiary sees ``subsidiary_of`` even though the stored row says
    ``parent_of``.
    """
    if not _enabled(cfg):
        return []
    stmt = select(EntityRelationship).where(
        or_(
            EntityRelationship.subject_entity_id == legal_entity_id,
            EntityRelationship.object_entity_id == legal_entity_id,
        )
    )
    if not include_closed:
        stmt = stmt.where(EntityRelationship.effective_to.is_(None))
    rows = (await session.execute(stmt)).scalars().all()

    other_ids = {
        other
        for row in rows
        if (other := (row.object_entity_id if row.subject_entity_id == legal_entity_id
                      else row.subject_entity_id)) is not None
    }
    others: dict[uuid.UUID, LegalEntity] = {}
    if other_ids:
        found = (
            (
                await session.execute(
                    select(LegalEntity).where(LegalEntity.id.in_(other_ids))
                )
            )
            .scalars()
            .all()
        )
        others = {entity.id: entity for entity in found}

    out: list[RelatedEntity] = []
    for row in rows:
        is_subject = row.subject_entity_id == legal_entity_id
        other_id = row.object_entity_id if is_subject else row.subject_entity_id
        other = others.get(other_id) if other_id is not None else None
        if other_id is not None and other is None:  # pragma: no cover - CASCADE
            continue
        label = (
            row.relationship_type
            if is_subject
            else RELATIONSHIP_INVERSE_LABEL.get(
                row.relationship_type, row.relationship_type
            )
        )
        out.append(
            RelatedEntity(
                entity=other,
                label=label,
                canonical_type=row.relationship_type,
                is_subject=is_subject,
                source=row.source,
                confidence=row.confidence,
                effective_from=row.effective_from,
                effective_to=row.effective_to,
                counterparty_name=row.counterparty_name_raw if other is None else None,
                relationship_id=row.id,
                source_url=row.source_url,
                evidence_ref=row.evidence_ref,
                source_kind=row.source_kind,
                extraction_method=row.extraction_method,
                evidence_excerpt=row.evidence_excerpt,
                as_of=row.as_of,
                period=row.period or "",
                disclosed_share_pct=row.disclosed_share_pct,
                share_basis=row.share_basis,
            )
        )
    return sorted(out, key=lambda r: (r.label, r.display_name, r.period))


async def parent_of(
    session: Any, *, legal_entity_id: uuid.UUID, cfg: "Settings"
) -> LegalEntity | None:
    """This entity's current parent, or ``None``.

    ``None`` when there is no parent **and** when there is more than one: two live
    parents is a contradiction, and returning either would be a coin flip presented
    as a corporate structure.
    """
    related = await relationships_for(
        session, legal_entity_id=legal_entity_id, cfg=cfg
    )
    parents = [r for r in related if r.label == "subsidiary_of" and r.entity is not None]
    return parents[0].entity if len(parents) == 1 else None


async def subsidiaries_of(
    session: Any, *, legal_entity_id: uuid.UUID, cfg: "Settings"
) -> list[LegalEntity]:
    related = await relationships_for(
        session, legal_entity_id=legal_entity_id, cfg=cfg
    )
    return [r.entity for r in related if r.label == "parent_of" and r.entity is not None]


__all__ = [
    "RelatedEntity",
    "close_relationship",
    "parent_of",
    "record_relationship",
    "relationships_for",
    "subsidiaries_of",
]
