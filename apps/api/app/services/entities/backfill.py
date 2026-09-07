"""Backfill the entity master from ``companies`` — V3.2 Slice 2.2.

Gives every existing ``companies`` row a ``LegalEntity`` + ``Security`` +
``SecurityListing`` derived from what that row actually records, and sets
``companies.legal_entity_id``.

CALLED BY NOTHING
=================
Deliberately. A backfill that starts itself on the first request after a deploy is
how a migration becomes an outage, and this repository already follows the rule for
``corpus.documents.backfill_from_extracted_documents``. A test asserts no other
module calls it. It is resumable (only unlinked rows are selected), idempotent
(running it twice does nothing the second time) and bounded (an explicit ``limit``).

WHAT IT DELIBERATELY DOES NOT CLAIM
===================================
A ``companies`` row is thin, and the temptation is to fill the entity master from
whatever field looks closest. Four things are therefore left unstated:

**``jurisdiction`` stays NULL.** ``companies.country`` is largely the *listing
venue's* country, and where a company trades is not where it is incorporated.
Writing it into ``jurisdiction`` would turn a listing location into a claim about a
legal register. NULL means "not established", which is exactly true.

**``quote_currency`` comes from the venue, not from ``companies.currency``.** That
column does not distinguish a reporting currency from a price-quote unit, and LSE
quotes in pence — so taking it would record GBP where prices are GBX and mislabel
every London price as 100x its real value.

**``entity_status`` stays ``unknown``.** A ``companies`` row says nothing about
whether the entity is still registered.

**The security is ``ordinary_share``.** A ``companies`` row does not record that a
ticker is an ADR. Slice 2.3 may correct it from a source, and ``upsert_security``
refuses to overwrite a type once asserted — so a later correction survives a re-run
of this backfill rather than being quietly undone by it.

TWO COMPANIES, ONE DERIVED KEY
==============================
``normalize_exchange`` collapses NYSE/NASDAQ/AMEX onto ``US``, so two ``companies``
rows can derive one ``entity_key``. When they do, the **name decides**: matching
names link to one entity, which is the intended de-duplication; differing names
leave the second row **unlinked** and counted as ambiguous. Two different names on
one venue and ticker is evidence of a real problem, and picking one is the silent
merge the entity master exists to prevent. ``legal_entity_id`` NULL is a safe,
representable state — the FK is nullable and the compatibility adapter falls back
to the ``companies`` row.

IT LOOKS FOR THE LISTING BEFORE IT LOOKS FOR THE KEY
====================================================
The order matters and it is not cosmetic. A ``listing:`` key is the *weakest* form
of identity, so an entity for this ticker may well exist under a stronger one —
``lei:…`` created by slice 2.3 from a real source. Searching by the derived key
alone would miss it, create a second entity for the same issuer, and then hit
``upsert_listing``'s refusal to move a live ticker between securities. That
exception would abort the whole batch and leave a stray entity with no listing
behind it.

So the lookup is: the **open listing** for ``(venue, ticker)`` first — which is the
real identity of a live symbol — then the derived key, and only then create. One
consequence is worth naming: when the entity exists but its listing has been
**closed** (a delisting recorded by a later slice), this links the company and
writes no listing. Re-opening a window somebody deliberately closed would be a
backfill silently contradicting a sourced decision.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.company import Company
from app.models.legal_entity import LegalEntity
from app.services.entities.master import (
    EntityInput,
    entity_key_for,
    normalize_entity_name,
    resolve_entity_by_listing,
    upsert_legal_entity,
    upsert_listing,
    upsert_security,
    venue_key_for,
)
from app.services.entities.vocabulary import SECURITY_ORDINARY_SHARE

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

#: Where a backfilled row came from, recorded on every entity it creates.
BACKFILL_SOURCE = "company_backfill"

#: The one security a ``companies`` row implies. Not "the only security the issuer
#: has" — the only one this row is evidence of.
PRIMARY_SECURITY_KEY = f"{SECURITY_ORDINARY_SHARE}:1"


@dataclass
class BackfillResult:
    """What one bounded backfill pass did. Counts, not a boolean."""

    examined: int = 0
    linked: int = 0
    reused: int = 0
    #: Rows left deliberately unlinked because linking would have been a merge.
    ambiguous: int = 0
    skipped: int = 0
    #: ``(ticker, exchange, entity_key, existing_name)`` for each ambiguous row, so
    #: an operator can see WHICH rows need a human rather than only how many.
    ambiguous_rows: list[tuple[str, str, str, str]] = field(default_factory=list)
    #: The bound this pass ran under, so ``remaining_possible`` is a fact about the
    #: query rather than an inference from the outcome.
    limit: int = 0

    @property
    def remaining_possible(self) -> bool:
        """True when this pass filled its limit, so another may find more rows.

        Derived from the LIMIT, not from the outcome: a pass that examined its full
        limit and linked none of them (all ambiguous) has still not reached the end
        of the table, and inferring "done" from "nothing linked" would stop a
        resumable backfill early.
        """
        return self.limit > 0 and self.examined >= self.limit


async def backfill_entities_from_companies(
    session: Any,
    *,
    cfg: "Settings",
    company_id: uuid.UUID | None = None,
    limit: int = 200,
) -> BackfillResult:
    """Link unlinked ``companies`` rows to the entity master. Flush-only.

    Returns an empty result — and issues no query — when the entity master is
    disabled. Selects only rows with ``legal_entity_id IS NULL``, so it is safe to
    run repeatedly and safe to run in batches.
    """
    bound = max(1, int(limit))
    result = BackfillResult(limit=bound)
    if not getattr(cfg, "v3_entity_master_enabled", False):
        return result

    stmt = (
        select(Company)
        .where(Company.legal_entity_id.is_(None))
        .order_by(Company.created_at, Company.id)
        .limit(bound)
    )
    if company_id is not None:
        stmt = stmt.where(Company.id == company_id)

    rows = (await session.execute(stmt)).scalars().all()
    for company in rows:
        result.examined += 1
        ticker = (company.ticker or "").strip().upper()
        if not ticker:
            # A company with no ticker gives no key at all. Leaving it unlinked is
            # the honest outcome; inventing one would create an entity nothing can
            # ever be matched back to.
            result.skipped += 1
            continue

        venue = venue_key_for(company.exchange)
        key = entity_key_for(venue_key=venue, ticker=ticker)
        name = (company.name or ticker).strip()

        # The OPEN LISTING first — it is the real identity of a live symbol, and the
        # entity holding it may carry a much stronger key than the one derived here.
        existing = await resolve_entity_by_listing(
            session, ticker=ticker, exchange=company.exchange, cfg=cfg
        )
        # Then the derived key, which finds an entity a previous pass created whose
        # listing has since been closed.
        if existing is None:
            existing = (
                await session.execute(
                    select(LegalEntity).where(LegalEntity.entity_key == key)
                )
            ).scalar_one_or_none()

        if existing is not None:
            if normalize_entity_name(existing.legal_name) != normalize_entity_name(
                name
            ):
                # Two names on one venue and ticker. Which is right is an evidence
                # question and this backfill has none — so the row stays unlinked
                # and is reported, rather than being attributed to the other
                # company's entity.
                result.ambiguous += 1
                result.ambiguous_rows.append(
                    (ticker, company.exchange or "", key, existing.legal_name)
                )
                continue
            # Link only. The entity already has whatever securities and listings it
            # should have, and writing more would either demote a primary a
            # better-informed slice promoted or re-open a window somebody
            # deliberately closed.
            company.legal_entity_id = existing.id
            result.reused += 1
            await session.flush()
            continue

        entity = await upsert_legal_entity(
            session,
            EntityInput(
                entity_key=key,
                legal_name=name,
                # jurisdiction, legal_form and entity_status are deliberately not
                # supplied — see the module docstring.
                source=BACKFILL_SOURCE,
            ),
            cfg=cfg,
        )
        if entity is None:  # pragma: no cover - the flag was checked above
            result.skipped += 1
            continue

        security = await upsert_security(
            session,
            legal_entity_id=entity.id,
            security_key=PRIMARY_SECURITY_KEY,
            cfg=cfg,
            # Not asserted: a `companies` row is no evidence of instrument type, and
            # asserting `ordinary_share` here would let a re-run undo a later
            # correction to `adr`.
            security_type=None,
            is_primary=True,
            source=BACKFILL_SOURCE,
        )
        if security is None:  # pragma: no cover - the flag was checked above
            result.skipped += 1
            continue

        await upsert_listing(
            session,
            security_id=security.id,
            ticker=ticker,
            exchange=company.exchange,
            cfg=cfg,
            # quote_currency is left to the venue resolver on purpose — see the
            # module docstring. `company.currency` cannot say whether it means a
            # reporting currency or a quote unit.
            is_primary=True,
            source=BACKFILL_SOURCE,
        )

        company.legal_entity_id = entity.id
        result.linked += 1
        await session.flush()

    return result


__all__ = [
    "BACKFILL_SOURCE",
    "PRIMARY_SECURITY_KEY",
    "BackfillResult",
    "backfill_entities_from_companies",
]
