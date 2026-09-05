"""Promote an entity's identity from what sources say — V3.2 Slice 2.3.1.

Takes a set of ``IdentifierSource``s, asks each one, and puts every claim through
``verify_identifier_claim``. Returns a ``PromotionReport`` that distinguishes three
outcomes, because collapsing them loses the information that matters:

    accepted    an identifier was recorded, with what was actually checked
    rejected    a claim reached the gate and was refused, WITH ITS REASON
    withheld    a source found something and declined to assert it, WITH ITS REASON

PURE ORCHESTRATION
==================
No network here. The sources are handed in, so this module never imports a client
and the 2.1 test that refuses a network import inside ``app.services.entities``
keeps holding. Semantics live in this package; clients live in
``app.integrations``; the Protocol crosses the boundary in the safe direction.

WHAT PROMOTION IS NOT
=====================
It is not a merge. It never rewrites ``entity_key`` — an entity created as
``listing:XCSE:PNDORA`` that acquires a LEI keeps that key, because rewriting it
would silently merge the identity's history and break every key a caller had
recorded. It never resolves a conflict: an identifier already held by another
subject is refused, not moved. And a source is never trusted more than a caller —
every claim goes through the same gate either way.

A SOURCE THAT FAILS DOES NOT STOP THE OTHERS
============================================
An exception from one source becomes a ``WITHHELD_SOURCE_ERROR`` finding and the
remaining sources still run. A registry timing out is a normal Tuesday, and letting
it abort the promotion of every other identifier would make the whole mechanism as
reliable as its least reliable dependency.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.legal_entity import LegalEntity
from app.services.entities.claims import (
    WITHHELD_SOURCE_ERROR,
    ClaimOutcome,
    IdentifierQuery,
    IdentifierSource,
    WithheldFinding,
    verify_identifier_claim,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings


@dataclass
class PromotionReport:
    """Everything one promotion pass learned, including what it refused."""

    legal_entity_id: uuid.UUID | None = None
    entity_key: str | None = None
    accepted: list[ClaimOutcome] = field(default_factory=list)
    rejected: list[ClaimOutcome] = field(default_factory=list)
    withheld: list[WithheldFinding] = field(default_factory=list)
    #: ``source_id`` of every source that was actually asked.
    sources_consulted: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def gained_identity(self) -> bool:
        """True when this pass recorded at least one new identifier."""
        return bool(self.accepted)

    @property
    def rejection_reasons(self) -> list[str]:
        return [o.rejection_reason for o in self.rejected if o.rejection_reason]

    @property
    def withheld_reasons(self) -> list[str]:
        return [w.reason for w in self.withheld]


async def promote_entity_identity(
    session: Any,
    *,
    legal_entity_id: uuid.UUID,
    sources: Sequence[IdentifierSource],
    cfg: "Settings",
    query: IdentifierQuery | None = None,
    verified_at: datetime | None = None,
) -> PromotionReport:
    """Ask every source about one entity and record what survives the gate.

    Flush-only; the caller commits. Returns an empty report — and consults nothing —
    when the entity master is disabled or the entity does not exist.

    ``query`` defaults to the entity's own legal name and jurisdiction. A caller
    with more (a ticker, a venue, an already-known identifier) should pass it: more
    inputs is strictly better, because a source that can cross-check two of them
    withholds instead of guessing.
    """
    report = PromotionReport()
    if not getattr(cfg, "v3_entity_master_enabled", False):
        report.notes.append("the entity master is disabled; no source was consulted")
        return report

    entity = (
        await session.execute(
            select(LegalEntity).where(LegalEntity.id == legal_entity_id)
        )
    ).scalar_one_or_none()
    if entity is None:
        report.notes.append("no such legal entity; no source was consulted")
        return report

    report.legal_entity_id = entity.id
    report.entity_key = entity.entity_key
    effective_query = query or IdentifierQuery(
        legal_name=entity.legal_name, jurisdiction=entity.jurisdiction
    )

    for source in sources:
        source_id = getattr(source, "source_id", source.__class__.__name__)
        report.sources_consulted.append(source_id)
        try:
            result = await source.lookup(effective_query)
        except Exception as exc:  # noqa: BLE001 - containment is the point
            # A registry timing out is a normal Tuesday. Letting it abort the
            # promotion of every other identifier would make the mechanism as
            # reliable as its least reliable dependency.
            report.withheld.append(
                WithheldFinding(
                    scheme="*",
                    reason=WITHHELD_SOURCE_ERROR,
                    detail=f"{source_id} raised {type(exc).__name__}: {exc}",
                )
            )
            continue

        report.withheld.extend(result.withheld)
        report.notes.extend(result.notes)
        for claim in result.claims:
            outcome = await verify_identifier_claim(
                session,
                claim=claim,
                cfg=cfg,
                legal_entity_id=entity.id,
                verified_at=verified_at,
            )
            if outcome.accepted:
                report.accepted.append(outcome)
            else:
                # Kept, with its reason. CLAUDE.md rule 8 applied to identity: a
                # refused claim is what makes a source's accuracy measurable.
                report.rejected.append(outcome)

    # ``entity_key`` is untouched by construction: nothing above assigns it. That is
    # the whole design — rewriting it when a stronger identifier arrives would
    # silently merge the identity's history and break every key a caller had
    # recorded. It is pinned by a test rather than by an ``assert`` here, because an
    # assertion that can be stripped with ``-O`` is documentation wearing a
    # guarantee's clothes.
    return report


__all__ = ["PromotionReport", "promote_entity_identity"]
