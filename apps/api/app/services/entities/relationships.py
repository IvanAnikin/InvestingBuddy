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
    RELATIONSHIP_INVERSE_LABEL,
    is_symmetric_relationship,
    require_relationship_type,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings


@dataclass(frozen=True)
class RelatedEntity:
    """One relationship as seen FROM a particular entity.

    ``label`` is the relationship read in the direction the caller asked about, so a
    subsidiary looking at its stored ``parent_of`` row sees ``subsidiary_of``. The
    caller never has to know which way the fact was stored.
    """

    entity: LegalEntity
    label: str
    canonical_type: str
    is_subject: bool
    source: str
    confidence: float
    effective_from: date | None
    effective_to: date | None

    @property
    def is_current(self) -> bool:
        return self.effective_to is None


def _enabled(cfg: "Settings") -> bool:
    return bool(getattr(cfg, "v3_entity_master_enabled", False))


async def record_relationship(
    session: Any,
    *,
    subject_entity_id: uuid.UUID,
    object_entity_id: uuid.UUID,
    relationship_type: str,
    source: str,
    cfg: "Settings",
    source_url: str | None = None,
    confidence: float = 1.0,
    effective_from: date | None = None,
    note: str | None = None,
) -> EntityRelationship | None:
    """Record one relationship in canonical form. Flush-only; the caller commits.

    Idempotent: asserting the same relationship again — in **either** direction —
    returns the existing row rather than creating a mirror of it.
    """
    if not _enabled(cfg):
        return None
    canonical, swap = require_relationship_type(relationship_type)
    subject, obj = (
        (object_entity_id, subject_entity_id) if swap
        else (subject_entity_id, object_entity_id)
    )
    if subject == obj:
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

    # A symmetric relationship has no direction, so the pair is ordered
    # deterministically — otherwise "A joint_venture_with B" and "B joint_venture_with
    # A" are two live rows for one fact and the unique index cannot see it.
    if is_symmetric_relationship(canonical):
        subject, obj = (subject, obj) if str(subject) <= str(obj) else (obj, subject)

    existing = (
        await session.execute(
            select(EntityRelationship).where(
                EntityRelationship.subject_entity_id == subject,
                EntityRelationship.object_entity_id == obj,
                EntityRelationship.relationship_type == canonical,
                EntityRelationship.effective_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.confidence = max(existing.confidence, confidence)
        existing.source_url = existing.source_url or source_url
        existing.effective_from = existing.effective_from or effective_from
        existing.note = existing.note or note
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
        (row.object_entity_id if row.subject_entity_id == legal_entity_id
         else row.subject_entity_id)
        for row in rows
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
        other = others.get(other_id)
        if other is None:  # pragma: no cover - CASCADE makes this unreachable
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
            )
        )
    return sorted(out, key=lambda r: (r.label, r.entity.legal_name))


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
    parents = [r for r in related if r.label == "subsidiary_of"]
    return parents[0].entity if len(parents) == 1 else None


async def subsidiaries_of(
    session: Any, *, legal_entity_id: uuid.UUID, cfg: "Settings"
) -> list[LegalEntity]:
    related = await relationships_for(
        session, legal_entity_id=legal_entity_id, cfg=cfg
    )
    return [r.entity for r in related if r.label == "parent_of"]


__all__ = [
    "RelatedEntity",
    "close_relationship",
    "parent_of",
    "record_relationship",
    "relationships_for",
    "subsidiaries_of",
]
