"""GLEIF Level-2 parents → the relationship graph — V3.20 slice 2.2 (spec §3.2).

Takes a parsed ``ParentReport`` (``app.integrations.gleif_level2``) and records it:

* a reported parent  → the parent as a legal entity (keyed by its LEI, the LEI recorded
  as its identifier) and a ``parent_of`` / ``ultimate_parent_of`` edge, cited to the
  GLEIF URL, ``extraction_method="gleif_api"``, its start date as ``effective_from``;
* a reporting exception → an ``entity_relationship_exceptions`` row with GLEIF's reason.
  Never an edge and never "independent";
* neither              → nothing. Unknown stays unknown.

CHANGES ARE CLOSED, NEVER DELETED
=================================
A parent that GLEIF now reports differently closes the previous GLEIF edge of that type
(``effective_to`` = today), and a parent that is now reported closes the exception that
said there was none — and the reverse. Only GLEIF-sourced rows are closed: a parent read
from an Exhibit 21 is that filing's claim and a GLEIF lookup does not retract it. A
lookup that FAILED, or that GLEIF answered with nothing, changes nothing.

NO NETWORK HERE
===============
This package may not import a network client (a test enforces it). The report is
duck-typed, and ``refresh_company_parents`` receives its ``lookup`` (and the optional
LEI ``promote`` step) from the caller — the research pipeline passes the real ones.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.legal_entity import (
    EntityIdentifier,
    EntityRelationship,
    EntityRelationshipException,
)
from app.services.entities.identifiers import SCHEME_LEI
from app.services.entities.master import (
    EntityInput,
    entity_key_for,
    find_entity_by_identifier,
    record_identifier,
    upsert_legal_entity,
)
from app.services.entities.relationships import close_relationship, record_relationship
from app.services.entities.vocabulary import (
    ENTITY_ACTIVE,
    ENTITY_INACTIVE,
    REL_PARENT_OF,
    REL_ULTIMATE_PARENT_OF,
)

if TYPE_CHECKING:
    from app.core.config import Settings

GLEIF_SOURCE = "gleif"
RELATIONSHIP_FOR_KIND: dict[str, str] = {
    "direct": REL_PARENT_OF,
    "ultimate": REL_ULTIMATE_PARENT_OF,
}

#: What GLEIF's reporting-exception reasons mean, in a reader's words. The raw reason is
#: always kept and shown beside this; an unknown reason is shown raw.
EXCEPTION_REASON_LABELS: dict[str, str] = {
    "NATURAL_PERSONS": "controlled by one or more natural persons (no parent company)",
    "NON_CONSOLIDATING": "the controlling entity does not consolidate it",
    "NO_KNOWN_PERSON": "no known person controls it",
    "NON_PUBLIC": "the parent is not disclosed (non-public)",
    "NO_LEI": "the parent has no LEI and did not consent to be named",
    "BINDING_LEGAL_COMMITMENTS": "the parent is not disclosed (binding legal commitments)",
    "LEGAL_OBSTACLES": "the parent is not disclosed (legal obstacles)",
    "DISCLOSURE_DETRIMENTAL": "the parent is not disclosed (disclosure detrimental)",
    "DETRIMENT_NOT_EXCLUDED": "the parent is not disclosed (detriment not excluded)",
    "CONSENT_NOT_OBTAINED": "the parent is not disclosed (consent not obtained)",
}

_GLEIF_STATUS = {"ACTIVE": ENTITY_ACTIVE, "INACTIVE": ENTITY_INACTIVE}

Lookup = Callable[[str], Awaitable[Any]]
Promote = Callable[[Any, uuid.UUID], Awaitable[Any]]


def exception_label(reason: str | None) -> str | None:
    if not reason:
        return None
    return EXCEPTION_REASON_LABELS.get(reason.upper(), reason)


@dataclass
class ParentWriteResult:
    """What one refresh did. Serialised onto the research outcome (``to_dict``)."""

    #: ``written`` | ``disabled`` | ``entity_unresolved`` | ``no_lei`` | ``error``.
    state: str
    lei: str | None = None
    edges: list[dict[str, Any]] = field(default_factory=list)
    exceptions: list[dict[str, Any]] = field(default_factory=list)
    closed: list[str] = field(default_factory=list)
    not_reported: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _enabled(cfg: "Settings") -> bool:
    return bool(getattr(cfg, "v3_entity_master_enabled", False))


async def lei_for_entity(session: Any, entity_id: uuid.UUID) -> str | None:
    """The entity's current LEI identifier, if the master holds one."""
    row = (
        await session.execute(
            select(EntityIdentifier.value_normalized).where(
                EntityIdentifier.legal_entity_id == entity_id,
                EntityIdentifier.scheme == SCHEME_LEI,
                EntityIdentifier.effective_to.is_(None),
            )
        )
    ).scalars().first()
    return str(row) if row else None


async def _parent_entity(session: Any, parent: Any, source_url: str, *, cfg: "Settings") -> Any:
    """The parent as a legal entity: the holder of its LEI, else a new ``lei:`` entity."""
    existing = await find_entity_by_identifier(
        session, scheme=SCHEME_LEI, value=parent.lei, cfg=cfg
    )
    if existing is not None:
        return existing
    status = _GLEIF_STATUS.get(str(parent.status or "").upper())
    entity = await upsert_legal_entity(
        session,
        EntityInput(
            entity_key=entity_key_for(lei=parent.lei),
            legal_name=parent.legal_name,
            jurisdiction=parent.country,
            entity_status=status,
            status_source=GLEIF_SOURCE if status else None,
            source=GLEIF_SOURCE,
            source_url=source_url,
        ),
        cfg=cfg,
    )
    if entity is None:
        return None
    await record_identifier(
        session,
        scheme=SCHEME_LEI,
        value=parent.lei,
        cfg=cfg,
        legal_entity_id=entity.id,
        source=GLEIF_SOURCE,
        source_url=source_url,
        confidence=1.0,
    )
    return entity


async def _current_gleif_edges(
    session: Any, entity_id: uuid.UUID, relationship_type: str
) -> list[EntityRelationship]:
    return list(
        (
            await session.execute(
                select(EntityRelationship).where(
                    EntityRelationship.object_entity_id == entity_id,
                    EntityRelationship.relationship_type == relationship_type,
                    EntityRelationship.source == GLEIF_SOURCE,
                    EntityRelationship.effective_to.is_(None),
                )
            )
        ).scalars()
    )


async def _current_exception(
    session: Any, entity_id: uuid.UUID, relationship_type: str
) -> EntityRelationshipException | None:
    return (
        await session.execute(
            select(EntityRelationshipException).where(
                EntityRelationshipException.legal_entity_id == entity_id,
                EntityRelationshipException.relationship_type == relationship_type,
                EntityRelationshipException.effective_to.is_(None),
            )
        )
    ).scalar_one_or_none()


async def current_exceptions(
    session: Any, entity_id: uuid.UUID
) -> list[EntityRelationshipException]:
    """Every open reporting exception on the entity, oldest relationship type first."""
    return list(
        (
            await session.execute(
                select(EntityRelationshipException)
                .where(
                    EntityRelationshipException.legal_entity_id == entity_id,
                    EntityRelationshipException.effective_to.is_(None),
                )
                .order_by(EntityRelationshipException.relationship_type)
            )
        ).scalars()
    )


async def record_gleif_parents(
    session: Any,
    *,
    entity_id: uuid.UUID,
    report: Any,
    cfg: "Settings",
    today: date | None = None,
) -> ParentWriteResult:
    """Record one ``ParentReport`` for ``entity_id``. Flush-only; the caller commits."""
    result = ParentWriteResult(state="written", lei=getattr(report, "lei", None))
    if not _enabled(cfg):
        result.state = "disabled"
        return result
    day = today or datetime.now(timezone.utc).date()
    retrieved = getattr(report, "retrieved_at", None)
    as_of = retrieved.date() if isinstance(retrieved, datetime) else day
    result.errors.extend(getattr(report, "errors", None) or [])
    result.not_reported.extend(getattr(report, "not_reported", None) or [])

    for link in getattr(report, "links", None) or []:
        rel_type = RELATIONSHIP_FOR_KIND.get(link.kind)
        if rel_type is None:
            continue
        if not link.active:
            result.notes.append(
                f"GLEIF reports the {link.kind} parent relationship as "
                f"{link.relationship_status}; not recorded as current"
            )
            continue
        parent = await _parent_entity(session, link.parent, link.source_url, cfg=cfg)
        if parent is None:
            continue
        if parent.id == entity_id:
            result.errors.append(f"the {link.kind} parent resolved to the entity itself")
            continue
        for stale in await _current_gleif_edges(session, entity_id, rel_type):
            if stale.subject_entity_id != parent.id:
                await close_relationship(session, relationship=stale, effective_to=day, cfg=cfg)
                result.closed.append(f"{rel_type}:{stale.id}")
        verb = link.relationship_type or (
            "IS_DIRECTLY_CONSOLIDATED_BY" if link.kind == "direct"
            else "IS_ULTIMATELY_CONSOLIDATED_BY"
        )
        excerpt = (
            f"GLEIF Level 2: {report.lei} {verb} {link.parent.legal_name} "
            f"({link.parent.lei}); status {link.relationship_status or 'not stated'}; "
            f"corroboration {link.corroboration or 'not stated'}"
        )
        edge = await record_relationship(
            session,
            subject_entity_id=parent.id,
            object_entity_id=entity_id,
            relationship_type=rel_type,
            source=GLEIF_SOURCE,
            cfg=cfg,
            source_url=link.source_url,
            confidence=link.confidence,
            effective_from=link.start_date,
            evidence_ref=f"gleif:{report.lei}:{link.kind}-parent",
            source_kind="registry",
            extraction_method="gleif_api",
            evidence_excerpt=excerpt,
            as_of=as_of,
        )
        open_exc = await _current_exception(session, entity_id, rel_type)
        if open_exc is not None:
            open_exc.effective_to = day
            result.closed.append(f"exception:{rel_type}:{open_exc.reason}")
        result.edges.append({
            "kind": link.kind,
            "relationship_type": rel_type,
            "parent_lei": link.parent.lei,
            "parent_name": link.parent.legal_name,
            "parent_country": link.parent.country,
            "relationship_id": str(edge.id) if edge is not None else None,
            "corroboration": link.corroboration,
            "source_url": link.source_url,
        })

    for exc in getattr(report, "exceptions", None) or []:
        rel_type = RELATIONSHIP_FOR_KIND.get(exc.kind)
        if rel_type is None:
            continue
        for stale in await _current_gleif_edges(session, entity_id, rel_type):
            await close_relationship(session, relationship=stale, effective_to=day, cfg=cfg)
            result.closed.append(f"{rel_type}:{stale.id}")
        current = await _current_exception(session, entity_id, rel_type)
        if current is not None and current.reason == exc.reason:
            current.as_of = as_of
            current.category = current.category or exc.category
        else:
            if current is not None:
                current.effective_to = day
                result.closed.append(f"exception:{rel_type}:{current.reason}")
            session.add(EntityRelationshipException(
                id=uuid.uuid4(),
                legal_entity_id=entity_id,
                relationship_type=rel_type,
                category=(exc.category or None),
                reason=exc.reason[:60],
                source=GLEIF_SOURCE,
                source_url=exc.source_url,
                as_of=as_of,
                effective_from=day,
            ))
        result.exceptions.append({
            "kind": exc.kind,
            "relationship_type": rel_type,
            "reason": exc.reason,
            "reason_label": exception_label(exc.reason),
            "source_url": exc.source_url,
        })
    await session.flush()
    return result


async def refresh_company_parents(
    session: Any,
    *,
    company: Any,
    cfg: "Settings",
    lookup: Lookup,
    promote: Promote | None = None,
    today: date | None = None,
) -> ParentWriteResult:
    """Look the company's parents up in GLEIF and record them. Flush-only.

    The LEI comes from the entity master. When it holds none and ``promote`` is given,
    that step runs first (the pipeline passes the gated GLEIF identifier promotion, which
    withholds on any ambiguous name) and the master is read again.
    """
    if not _enabled(cfg) or not getattr(cfg, "v3_gleif_parents_enabled", False):
        return ParentWriteResult(state="disabled")
    entity_id = getattr(company, "legal_entity_id", None)
    if entity_id is None:
        return ParentWriteResult(
            state="entity_unresolved",
            notes=["the company is not linked to a legal entity; no parent lookup"],
        )
    lei = await lei_for_entity(session, entity_id)
    notes: list[str] = []
    if lei is None and promote is not None:
        try:
            await promote(session, entity_id)
        except Exception as exc:  # noqa: BLE001 - promotion failing is "no LEI"
            notes.append(f"LEI promotion failed ({type(exc).__name__})")
        lei = await lei_for_entity(session, entity_id)
        if lei is not None:
            notes.append("LEI established from GLEIF by exact legal-name match")
    if lei is None:
        return ParentWriteResult(
            state="no_lei",
            notes=notes + ["no verified LEI for the company; GLEIF parents not looked up"],
        )
    report = await lookup(lei)
    result = await record_gleif_parents(
        session, entity_id=entity_id, report=report, cfg=cfg, today=today
    )
    result.notes[:0] = notes
    if result.errors and not (result.edges or result.exceptions or result.not_reported):
        result.state = "error"
    return result


__all__ = [
    "EXCEPTION_REASON_LABELS",
    "GLEIF_SOURCE",
    "RELATIONSHIP_FOR_KIND",
    "ParentWriteResult",
    "current_exceptions",
    "exception_label",
    "lei_for_entity",
    "record_gleif_parents",
    "refresh_company_parents",
]
