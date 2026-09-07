"""The `(ticker, exchange)` → entity compatibility adapter — V3.2 Slice 2.2.

Every V2 caller asks "which company is TICKER on EXCHANGE?" and gets a
``companies`` row. This adapter answers the same question from the entity master
when an entity exists and falls back to the ``companies`` row when one does not —
so callers can migrate one at a time instead of all at once.

IT ALWAYS SAYS WHICH ONE ANSWERED
=================================
``EntityResolution.source`` is the point of the type. A caller that must not act on
a fallback — anything that would attach a CIK, a filing or a financial fact to an
issuer — can check it, and a caller that only needs a name does not have to. An
adapter that returned a bare value would make "we have real identity for this
issuer" and "we have a row keyed by a ticker string" indistinguishable, which is
the ambiguity the entity master exists to remove.

It also does **not** resolve. There is no name matching, no fuzzy match and no
scoring here: the lookup is exact on ``(venue_key, ticker)``, and anything less
certain is slice 2.3's resolver, which can record ambiguity as a research gap.

DATED FOR REMOVAL
=================
This adapter exists because ``companies`` and the entity master coexist. It is
removed when every ``companies`` row is linked and slice 2.3's resolver supersedes
it. An undated adapter becomes permanent architecture by accident
(``MIGRATION_AND_COMPATIBILITY_PLAN.md`` §4).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.company import Company
from app.models.legal_entity import LegalEntity
from app.services.entities.master import resolve_entity_by_listing
from app.services.exchange_registry import normalize_exchange

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

#: The entity master answered — there is real legal-entity identity behind this.
SOURCE_ENTITY_MASTER = "entity_master"
#: Only a ``companies`` row answered. Identity is a ``(ticker, exchange)`` string
#: pair, which is precisely what V3.2 exists to stop treating as identity.
SOURCE_COMPANIES_FALLBACK = "companies_fallback"
#: Nothing answered.
SOURCE_NONE = "none"


@dataclass(frozen=True)
class EntityResolution:
    """What the adapter found, and how much it is worth."""

    legal_entity: LegalEntity | None
    company: Company | None
    source: str

    @property
    def found(self) -> bool:
        return self.legal_entity is not None or self.company is not None

    @property
    def has_entity_identity(self) -> bool:
        """True only when a legal entity answered.

        The check a caller makes before attaching an identifier, a filing or a
        financial fact to an issuer.
        """
        return self.legal_entity is not None


async def resolve_entity(
    session: Any,
    *,
    ticker: str,
    exchange: str | None,
    cfg: "Settings",
) -> EntityResolution:
    """Resolve `(ticker, exchange)` to a legal entity, falling back to `companies`.

    With the entity master disabled this skips it entirely and returns the
    ``companies`` row, which is exactly V2 behaviour.
    """
    sym = (ticker or "").strip().upper()
    if not sym:
        return EntityResolution(None, None, SOURCE_NONE)

    entity = await resolve_entity_by_listing(
        session, ticker=sym, exchange=exchange, cfg=cfg
    )
    company = await _company_for(session, ticker=sym, exchange=exchange)
    if entity is not None:
        return EntityResolution(entity, company, SOURCE_ENTITY_MASTER)
    if company is not None:
        return EntityResolution(None, company, SOURCE_COMPANIES_FALLBACK)
    return EntityResolution(None, None, SOURCE_NONE)


async def _company_for(
    session: Any, *, ticker: str, exchange: str | None
) -> Company | None:
    """The ``companies`` row for this pair, matching V2's own key.

    Tries the exchange string as given first, then its normalised form — a
    ``companies`` row may have been written under either, and this adapter must not
    report "not found" for a row the V2 path would have located.
    """
    code = (exchange or "").strip().upper()
    candidates = [code] if code else []
    normalized = normalize_exchange(exchange)
    if normalized and normalized not in candidates:
        candidates.append(normalized)
    for candidate in candidates:
        row = (
            await session.execute(
                select(Company).where(
                    Company.ticker == ticker, Company.exchange == candidate
                )
            )
        ).scalar_one_or_none()
        if row is not None:
            return row
    return None


async def legal_entity_for_company(
    session: Any,
    *,
    company: Company,
    cfg: "Settings",
) -> LegalEntity | None:
    """The linked entity for a company row, or ``None`` when it has none.

    ``None`` is a real answer: a row can be permanently unlinked when the backfill
    found two names on one derived key and refused to guess.
    """
    if not getattr(cfg, "v3_entity_master_enabled", False):
        return None
    if company.legal_entity_id is None:
        return None
    return (
        await session.execute(
            select(LegalEntity).where(LegalEntity.id == company.legal_entity_id)
        )
    ).scalar_one_or_none()


async def companies_for_legal_entity(
    session: Any,
    *,
    legal_entity_id: uuid.UUID,
    cfg: "Settings",
) -> list[Company]:
    """Every ``companies`` row linked to one entity.

    More than one is normal and correct: NYSE and NASDAQ rows for one US issuer
    collapse onto a single venue key, which is the de-duplication the entity master
    makes possible.
    """
    if not getattr(cfg, "v3_entity_master_enabled", False):
        return []
    return list(
        (
            await session.execute(
                select(Company).where(Company.legal_entity_id == legal_entity_id)
            )
        )
        .scalars()
        .all()
    )


__all__ = [
    "SOURCE_COMPANIES_FALLBACK",
    "SOURCE_ENTITY_MASTER",
    "SOURCE_NONE",
    "EntityResolution",
    "companies_for_legal_entity",
    "legal_entity_for_company",
    "resolve_entity",
]
