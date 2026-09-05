"""Reporting scopes and segment disclosures — V3.2 Slice 2.4.

Gives durable identity to the scope vocabulary ``fact_scope.py`` already enforces in
memory, and separates the *scope* from a *period's disclosure* of it.

THE KEY IS ``fact_scope``'s KEY, BYTE FOR BYTE
==============================================
``ReportingScope.scope_key`` is ``FactScope.scope_key``: ``'group'`` or
``'segment:<casefolded name>'``. Not a similar string — the same one, produced by
calling that module. Every fact, chunk and calculation carrying a scope already
computes it, so any other choice would need a translation layer between two
definitions of scope, and a translation layer between two definitions of scope is
precisely how ``scope`` came to have three incompatible interpretations in three
layers before ``fact_scope`` existed.

IT OVER-SPLITS ON A RENAME, AND THAT IS THE DESIGN
==================================================
The key derives from the name, so "Watches" and "Specialist Watchmakers" are two
scopes and the series splits — visibly, as two rows a reader can see. Linking them is
an explicit sourced assertion (``link_scope_rename``), and the database refuses a
predecessor without a ``rename_source``. Split by default, link on evidence: the same
asymmetry ``entity_key`` uses, for the same reason. Silently merging two scopes joins
one segment's figures onto another's history — for CFR, exactly Specialist
Watchmakers figures becoming Group.

SCOPE VERSUS DISCLOSURE
=======================
A ``ReportingScope`` is the durable identity. A ``BusinessSegment`` is what one
report called it in one period, **verbatim**. Two periods of one scope are two
disclosures and one series; a rename is two disclosures with different names, and one
lineage only once a source says so.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.legal_entity import BusinessSegment, ReportingScope, Security
from app.services.entities.master import normalize_entity_name
from app.services.entities.vocabulary import (
    is_depositary_receipt,
    require_reporting_scope_type,
)
from app.services.sources.fact_scope import (
    SCOPE_TYPE_GROUP,
    SCOPE_TYPE_SEGMENT,
    FactScope,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

_NAME_MAX = 200
_KEY_MAX = 220


def _enabled(cfg: "Settings") -> bool:
    return bool(getattr(cfg, "v3_entity_master_enabled", False))


def scope_key_for(scope_type: str, scope_name: str | None) -> str:
    """The persisted key for a scope — produced BY ``fact_scope``, not beside it.

    For ``group`` and ``segment`` this delegates to ``FactScope.scope_key`` so the two
    cannot drift. ``region`` and ``division`` have no ``fact_scope`` equivalent and
    get the same shape, ``<type>:<casefolded name>``, which is what a later
    ``fact_scope`` extension would produce for them too.
    """
    kind = require_reporting_scope_type(scope_type)
    if kind == SCOPE_TYPE_GROUP:
        key = FactScope(scope_type=SCOPE_TYPE_GROUP).scope_key
        if key is None:  # pragma: no cover - fact_scope always keys `group`
            raise ValueError("fact_scope produced no key for the Group scope.")
        return key
    name = (scope_name or "").strip()
    if not name:
        raise ValueError(
            f"A {kind} scope needs a name; only 'group' is nameless. An unnamed "
            "segment cannot be told from another unnamed segment."
        )
    if kind == SCOPE_TYPE_SEGMENT:
        key = FactScope(scope_type=SCOPE_TYPE_SEGMENT, scope_name=name).scope_key
        if key is None:  # pragma: no cover - a named segment always keys
            raise ValueError(f"fact_scope produced no key for segment {name!r}.")
        return key
    return f"{kind}:{name.casefold()}"[:_KEY_MAX]


@dataclass
class ScopeInput:
    """What a caller knows about one reporting scope."""

    scope_type: str
    scope_name: str | None = None
    source: str | None = None
    period_key: str | None = None


async def upsert_reporting_scope(
    session: Any,
    *,
    legal_entity_id: uuid.UUID,
    payload: ScopeInput,
    cfg: "Settings",
) -> ReportingScope | None:
    """Get-or-create one scope for one entity. Flush-only.

    ``first_seen_period`` / ``last_seen_period`` widen monotonically, so the row
    records the span the platform has actually observed rather than the last thing it
    was told.
    """
    if not _enabled(cfg):
        return None
    kind = require_reporting_scope_type(payload.scope_type)
    name = (payload.scope_name or "").strip()[:_NAME_MAX] or None
    key = scope_key_for(kind, name)

    existing = (
        await session.execute(
            select(ReportingScope).where(
                ReportingScope.legal_entity_id == legal_entity_id,
                ReportingScope.scope_key == key,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.source = existing.source or payload.source
        if payload.period_key:
            existing.first_seen_period = (
                min(existing.first_seen_period, payload.period_key)
                if existing.first_seen_period
                else payload.period_key
            )
            existing.last_seen_period = (
                max(existing.last_seen_period, payload.period_key)
                if existing.last_seen_period
                else payload.period_key
            )
        await session.flush()
        return existing

    scope = ReportingScope(
        id=uuid.uuid4(),
        legal_entity_id=legal_entity_id,
        scope_type=kind,
        scope_name=name,
        scope_key=key,
        source=payload.source,
        first_seen_period=payload.period_key,
        last_seen_period=payload.period_key,
    )
    session.add(scope)
    await session.flush()
    return scope


async def link_scope_rename(
    session: Any,
    *,
    successor: ReportingScope,
    predecessor: ReportingScope,
    rename_source: str,
    cfg: "Settings",
) -> ReportingScope | None:
    """Assert that one scope replaced another. Requires a source, by construction.

    This is the only way two scopes become one series, and it is deliberately narrow:
    the database itself refuses a predecessor with no ``rename_source``, so an
    unsourced merge is not storable even by a direct INSERT.
    """
    if not _enabled(cfg):
        return None
    if not (rename_source or "").strip():
        raise ValueError(
            "A rename needs a source. Linking two scopes without one joins one "
            "segment's figures onto another's history."
        )
    if successor.id == predecessor.id:
        raise ValueError("A scope cannot succeed itself.")
    if successor.legal_entity_id != predecessor.legal_entity_id:
        raise ValueError(
            "A rename is within one issuer. Two entities' scopes are not each "
            "other's history."
        )
    successor.predecessor_scope_id = predecessor.id
    successor.rename_source = rename_source.strip()[:200]
    await session.flush()
    return successor


async def scope_lineage(
    session: Any, *, scope: ReportingScope, cfg: "Settings"
) -> list[ReportingScope]:
    """The scope and every predecessor, oldest last. One series, in order.

    Bounded: a chain longer than the number of scopes an issuer has cannot be real,
    and following a cycle forever is worse than reporting a short answer.
    """
    if not _enabled(cfg):
        return []
    chain = [scope]
    seen = {scope.id}
    current = scope
    while current.predecessor_scope_id is not None:
        if current.predecessor_scope_id in seen:
            break
        previous = (
            await session.execute(
                select(ReportingScope).where(
                    ReportingScope.id == current.predecessor_scope_id
                )
            )
        ).scalar_one_or_none()
        if previous is None:
            break
        chain.append(previous)
        seen.add(previous.id)
        current = previous
    return chain


async def record_segment_disclosure(
    session: Any,
    *,
    reporting_scope: ReportingScope,
    reported_name: str,
    period_key: str,
    cfg: "Settings",
    period_type: str | None = None,
    source: str | None = None,
    source_url: str | None = None,
) -> BusinessSegment | None:
    """Record what one report called a scope in one period. Flush-only.

    ``reported_name`` is stored **verbatim** — the exact wording is what a citation
    has to be able to quote, and normalising it away would make the quote a
    paraphrase.
    """
    if not _enabled(cfg):
        return None
    name = (reported_name or "").strip()[:_NAME_MAX]
    if not name:
        raise ValueError("A segment disclosure needs the name as printed.")
    period = (period_key or "").strip()
    if not period:
        raise ValueError(
            "A segment disclosure needs a period. Without one it cannot be placed in "
            "a series, which is the only question this record answers."
        )

    existing = (
        await session.execute(
            select(BusinessSegment).where(
                BusinessSegment.reporting_scope_id == reporting_scope.id,
                BusinessSegment.period_key == period,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.reported_name = name
        existing.normalized_name = normalize_entity_name(name)[:_NAME_MAX]
        existing.period_type = existing.period_type or period_type
        existing.source = existing.source or source
        existing.source_url = existing.source_url or source_url
        await session.flush()
        return existing

    record = BusinessSegment(
        id=uuid.uuid4(),
        reporting_scope_id=reporting_scope.id,
        legal_entity_id=reporting_scope.legal_entity_id,
        reported_name=name,
        normalized_name=normalize_entity_name(name)[:_NAME_MAX],
        period_key=period,
        period_type=period_type,
        source=source,
        source_url=source_url,
    )
    session.add(record)
    await session.flush()
    return record


async def segment_series(
    session: Any, *, reporting_scope_id: uuid.UUID, cfg: "Settings"
) -> list[BusinessSegment]:
    """Every period this scope was disclosed in, oldest first."""
    if not _enabled(cfg):
        return []
    return list(
        (
            await session.execute(
                select(BusinessSegment)
                .where(BusinessSegment.reporting_scope_id == reporting_scope_id)
                .order_by(BusinessSegment.period_key)
            )
        )
        .scalars()
        .all()
    )


# ── The ADR ratio, and why it exists ─────────────────────────────────────── #


class PerShareNotPermittedError(ValueError):
    """Per-share arithmetic was attempted where the denominator is unknown."""


def underlying_shares_per_unit(security: Security) -> Decimal:
    """How many **underlying shares one tradable unit represents**, or refuse.

    THE DIRECTION IS THE WHOLE POINT, SO IT IS IN THE NAME
    =====================================================
    A "ratio" of 4 could mean four receipts per share or four shares per receipt, and
    a reader who guesses wrong is out by 16x. So: the returned value multiplies a
    **per-share** figure to give a **per-unit** figure.

        per_unit_value = per_share_value * underlying_shares_per_unit(security)

    An ordinary share returns ``Decimal(1)``. A depositary receipt returns its stored
    ``receipt_ratio``, which is defined the same way — underlying shares per one
    receipt — so ``4`` is a receipt over four shares and ``0.25`` is four receipts to
    the share.

    IT RAISES WHEN THE RATIO IS UNKNOWN, AND THAT IS WHY THE COLUMN EXISTS
    =====================================================================
    A receipt with no established ratio must make per-share arithmetic **stop**, not
    assume 1:1. Assuming it silently reports an earnings-per-receipt figure as an
    earnings-per-share figure, and for a receipt over four underlying shares that is a
    4x error in a number a human will act on.
    """
    if not is_depositary_receipt(security.security_type):
        return Decimal(1)
    ratio = security.receipt_ratio
    if ratio is None:
        raise PerShareNotPermittedError(
            f"security {security.security_key!r} is a "
            f"{security.security_type} with no established receipt_ratio; per-share "
            "arithmetic must not assume 1:1"
        )
    value = Decimal(str(ratio))
    if value <= 0:
        raise PerShareNotPermittedError(
            f"receipt_ratio {value} is not a ratio for {security.security_key!r}"
        )
    return value


__all__ = [
    "PerShareNotPermittedError",
    "ScopeInput",
    "link_scope_rename",
    "record_segment_disclosure",
    "underlying_shares_per_unit",
    "scope_key_for",
    "scope_lineage",
    "segment_series",
    "underlying_shares_per_unit",
    "upsert_reporting_scope",
]
