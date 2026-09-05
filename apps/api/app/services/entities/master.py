"""Entity-master persistence — V3.2 Slice 2.1.

Writes and reads the five identity tables. Flush-only: the caller commits, exactly
as ``app.services.corpus.documents`` does, so an entity write can join a larger
transaction without this module deciding when that transaction ends.

THREE RULES, AND THEY ARE THE WHOLE MODULE
==========================================
**Identity comes from identifiers, never from a name.** ``entity_key`` is derived
from the strongest identifier available — LEI, then CIK, then a national register
number, then, as a last resort, a venue and ticker. Nothing here resolves an entity
from a name, and ``normalized_name`` exists only so slice 2.3 has a candidate query.

**Over-split rather than merge.** Two entities that cannot be shown to be the same
stay separate. A duplicate is visible and repairable; a merge attributes one
issuer's filings to another. Which is why an entity created from a ticker keeps its
``listing:`` key forever: rewriting the key when a LEI arrives would be an implicit
merge, and merging is slice 2.3's job, explicitly and with evidence.

**Refuse rather than record a guess.** Every identifier goes through
``normalize_identifier`` and a checksum failure raises. Every vocabulary value goes
through its ``require_*`` guard. A caller that cannot produce a valid value has
learned something true about its source, and a row that hides it is worse than an
exception.

WHAT THIS MODULE DELIBERATELY DOES NOT DO
=========================================
No fetching. GLEIF, SEC and OpenFIGI are *sources*, they are a network path, and
they belong with the resolution slice (2.3) that can record what a lookup returned
and what it did not. No ``companies`` link — ``companies.legal_entity_id`` is slice
2.2, with the backfill, in one place. No relationships, scopes or segments — 2.4.
No merging, ever, without an explicit evidenced decision.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.legal_entity import (
    EntityAlias,
    EntityIdentifier,
    LegalEntity,
    Security,
    SecurityListing,
)
from app.services.entities.identifiers import (
    GLOBAL_SCOPE,
    SCHEME_CIK,
    SCHEME_COMPANY_REGISTER,
    SCHEME_LEI,
    SUBJECT_LEGAL_ENTITY,
    SUBJECT_SECURITY,
    normalize_identifier,
)
from app.services.entities.vocabulary import (
    DEFAULT_ENTITY_STATUS,
    LISTING_DELISTED,
    LISTING_UNKNOWN,
    SECURITY_ORDINARY_SHARE,
    require_alias_type,
    require_entity_status,
    require_listing_status,
    require_security_type,
)
from app.services.exchange_registry import (
    get_exchange,
    normalize_exchange,
    price_quote_currency_for_exchange,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

_NAME_MAX = 300
_KEY_MAX = 120
_WS_RE = re.compile(r"\s+")
#: Punctuation that carries no identity: "Pandora A/S." and "Pandora A/S" are the
#: same name written twice. Deliberately narrow — removing more would fold names
#: that genuinely differ.
_PUNCT_RE = re.compile(r"[.,;:'\"()\[\]]")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _enabled(cfg: "Settings") -> bool:
    return bool(getattr(cfg, "v3_entity_master_enabled", False))


def normalize_entity_name(raw: str | None) -> str:
    """Fold a name for *lookup*. Never an identity — see the module docstring."""
    text = _PUNCT_RE.sub(" ", (raw or "").strip().lower())
    return _WS_RE.sub(" ", text).strip()[:_NAME_MAX]


def venue_key_for(exchange: str | None) -> str:
    """The venue component of listing identity: the MIC where one exists.

    A MIC is the ISO identity of a trading venue; the ``exchange_registry`` code is
    this platform's own shorthand. Preferring the MIC means two sources that name
    the same venue differently still produce one key — and the code is kept as a
    fallback so a venue the registry has no MIC for is still representable rather
    than being silently dropped into a shared bucket.
    """
    code = normalize_exchange(exchange)
    info = get_exchange(code)
    if info is not None and info.mic:
        return info.mic.strip().upper()[:20]
    return (code or "").strip().upper()[:20] or "UNKNOWN"


def entity_key_for(
    *,
    lei: str | None = None,
    cik: str | None = None,
    register: str | None = None,
    jurisdiction: str | None = None,
    venue_key: str | None = None,
    ticker: str | None = None,
) -> str:
    """Derive the stable entity key from the strongest identifier available.

    The order is a strength order, not a preference: an LEI is issued to one legal
    person globally, a CIK to one filer, a register number to one company within
    one register, and a ticker to whatever currently trades under it on a venue.
    Only the last of those can be reused by a different issuer, which is why it is
    the last resort and why a key built from it is expected to over-split.
    """
    if lei:
        ident = normalize_identifier(SCHEME_LEI, lei)
        return f"lei:{ident.value_normalized}"[:_KEY_MAX]
    if cik:
        ident = normalize_identifier(SCHEME_CIK, cik)
        return f"cik:{ident.value_normalized}"[:_KEY_MAX]
    if register:
        ident = normalize_identifier(
            SCHEME_COMPANY_REGISTER, register, jurisdiction=jurisdiction
        )
        return f"register:{ident.scope_key}:{ident.value_normalized}"[:_KEY_MAX]
    if ticker:
        venue = (venue_key or "UNKNOWN").strip().upper()
        return f"listing:{venue}:{ticker.strip().upper()}"[:_KEY_MAX]
    raise ValueError(
        "An entity key needs at least one identifier or a ticker. An entity with no "
        "identity at all is a row nothing can ever be joined to safely."
    )


# ── Legal entities ───────────────────────────────────────────────────────── #


@dataclass
class EntityInput:
    """What a caller knows about one legal entity."""

    entity_key: str
    legal_name: str
    jurisdiction: str | None = None
    legal_form: str | None = None
    entity_status: str | None = None
    status_source: str | None = None
    source: str | None = None
    source_url: str | None = None


async def upsert_legal_entity(
    session: Any,
    payload: EntityInput,
    *,
    cfg: "Settings",
) -> LegalEntity | None:
    """Get-or-create one legal entity by ``entity_key``. Flush-only.

    Returns ``None`` — and issues no query — when the entity master is disabled.

    On an existing row this fills gaps and does **not** overwrite: a second source
    that knows less must not erase what a better one established. Replacing a
    known jurisdiction with a NULL is the kind of quiet regression that only shows
    up as a filing attributed to the wrong country months later.
    """
    if not _enabled(cfg):
        return None

    key = (payload.entity_key or "").strip()[:_KEY_MAX]
    if not key:
        raise ValueError("A legal entity needs an entity_key.")
    name = (payload.legal_name or "").strip()[:_NAME_MAX]
    if not name:
        raise ValueError("A legal entity needs a legal_name.")
    status = (
        require_entity_status(payload.entity_status)
        if payload.entity_status
        else DEFAULT_ENTITY_STATUS
    )
    juris = (payload.jurisdiction or "").strip().upper() or None

    existing = (
        await session.execute(select(LegalEntity).where(LegalEntity.entity_key == key))
    ).scalar_one_or_none()
    if existing is not None:
        if payload.legal_name and existing.legal_name != name:
            # Adopt the incoming name as the CURRENT one — the key, not the name,
            # is the identity, so a rename is a name change rather than a new
            # entity. The previous name is not lost by this write, but it is not
            # preserved by it either: the caller records it as a
            # ``former_legal_name`` alias, which is what keeps the old name
            # searchable after the rename.
            existing.legal_name = name
            existing.normalized_name = normalize_entity_name(name)
        existing.jurisdiction = existing.jurisdiction or juris
        existing.legal_form = existing.legal_form or payload.legal_form
        if status != DEFAULT_ENTITY_STATUS:
            existing.entity_status = status
            existing.status_source = payload.status_source or existing.status_source
        existing.source = existing.source or payload.source
        existing.source_url = existing.source_url or payload.source_url
        await session.flush()
        return existing

    entity = LegalEntity(
        id=uuid.uuid4(),
        entity_key=key,
        legal_name=name,
        normalized_name=normalize_entity_name(name),
        jurisdiction=juris,
        legal_form=payload.legal_form,
        entity_status=status,
        status_source=payload.status_source,
        source=payload.source,
        source_url=payload.source_url,
    )
    session.add(entity)
    await session.flush()
    return entity


# ── Identifiers ──────────────────────────────────────────────────────────── #


async def record_identifier(
    session: Any,
    *,
    scheme: str,
    value: str,
    cfg: "Settings",
    legal_entity_id: uuid.UUID | None = None,
    security_id: uuid.UUID | None = None,
    source: str,
    source_url: str | None = None,
    jurisdiction: str | None = None,
    confidence: float = 1.0,
    effective_from: date | None = None,
    verified_at: datetime | None = None,
) -> EntityIdentifier | None:
    """Attach one validated identifier to an entity or a security. Flush-only.

    Idempotent for the *same* subject: re-recording a LEI already held by this
    entity returns the existing row. For a *different* subject the database refuses
    it — ``ix_entity_identifiers_current_value`` is a partial unique index on
    ``(scheme, value_normalized, scope_key)`` for open windows, so two entities
    claiming one LEI is an ``IntegrityError`` rather than a silent second row that
    every later join then multiplies.
    """
    if not _enabled(cfg):
        return None

    ident = normalize_identifier(scheme, value, jurisdiction=jurisdiction)

    if ident.subject == SUBJECT_LEGAL_ENTITY and legal_entity_id is None:
        raise ValueError(
            f"{ident.scheme} identifies a legal person; it needs a legal_entity_id."
        )
    if ident.subject == SUBJECT_SECURITY and security_id is None:
        raise ValueError(
            f"{ident.scheme} identifies an instrument; it needs a security_id. "
            "Storing it on the entity would be meaningful for a single-security "
            "issuer and silently wrong for every cross-listed one."
        )
    if legal_entity_id is not None and security_id is not None:
        raise ValueError(
            "An identifier describes exactly one subject. Passing both is a caller "
            "bug the CHECK constraint would reject anyway."
        )
    if not (0.0 <= confidence <= 1.0):
        raise ValueError(f"confidence must be within 0.0-1.0, got {confidence}.")
    if not (source or "").strip():
        raise ValueError(
            "An identifier needs a source. An unsourced identifier is a guess, and "
            "this platform's own history is why that is not stored."
        )

    existing = (
        await session.execute(
            select(EntityIdentifier).where(
                EntityIdentifier.scheme == ident.scheme,
                EntityIdentifier.value_normalized == ident.value_normalized,
                EntityIdentifier.scope_key == ident.scope_key,
                EntityIdentifier.effective_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        same_subject = (
            existing.legal_entity_id == legal_entity_id
            and existing.security_id == security_id
        )
        if same_subject:
            existing.confidence = max(existing.confidence, confidence)
            existing.verified_at = verified_at or existing.verified_at
            existing.source_url = existing.source_url or source_url
            await session.flush()
            return existing
        # Deliberately NOT resolved here. Which of the two subjects is right is an
        # evidence question, and answering it by overwriting is the merge this
        # schema exists to prevent. Let the unique index refuse the write.

    record = EntityIdentifier(
        id=uuid.uuid4(),
        legal_entity_id=legal_entity_id,
        security_id=security_id,
        scheme=ident.scheme,
        value=ident.value[:40],
        value_normalized=ident.value_normalized,
        scope_key=ident.scope_key,
        jurisdiction=ident.jurisdiction,
        checksum_verified=ident.checksum_verified,
        source=source.strip()[:100],
        source_url=source_url,
        confidence=confidence,
        effective_from=effective_from,
        verified_at=verified_at,
    )
    session.add(record)
    await session.flush()
    return record


async def find_entity_by_identifier(
    session: Any,
    *,
    scheme: str,
    value: str,
    cfg: "Settings",
    jurisdiction: str | None = None,
) -> LegalEntity | None:
    """The entity currently holding this identifier, directly or via a security."""
    if not _enabled(cfg):
        return None
    ident = normalize_identifier(scheme, value, jurisdiction=jurisdiction)

    row = (
        await session.execute(
            select(EntityIdentifier).where(
                EntityIdentifier.scheme == ident.scheme,
                EntityIdentifier.value_normalized == ident.value_normalized,
                EntityIdentifier.scope_key == ident.scope_key,
                EntityIdentifier.effective_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    if row is None:
        return None
    if row.legal_entity_id is not None:
        return (
            await session.execute(
                select(LegalEntity).where(LegalEntity.id == row.legal_entity_id)
            )
        ).scalar_one_or_none()
    return (
        await session.execute(
            select(LegalEntity)
            .join(Security, Security.legal_entity_id == LegalEntity.id)
            .where(Security.id == row.security_id)
        )
    ).scalar_one_or_none()


# ── Securities ───────────────────────────────────────────────────────────── #


async def upsert_security(
    session: Any,
    *,
    legal_entity_id: uuid.UUID,
    security_key: str,
    cfg: "Settings",
    security_type: str | None = None,
    description: str | None = None,
    is_primary: bool = False,
    source: str | None = None,
) -> Security | None:
    """Get-or-create one instrument of one entity. Flush-only.

    ``security_type`` of ``None`` means "not asserted" and leaves an existing type
    alone; a caller that asserts a *different* type raises. It is deliberately not
    a silent overwrite: a caller relying on a default of ``ordinary_share`` would
    otherwise downgrade an ``adr`` row, and the whole reason the type is named is
    that per-share arithmetic must refuse to run on a depositary receipt without a
    ratio.

    ``is_primary`` promotion demotes the outgoing primary first and flushes between
    the two writes. SQLAlchemy orders INSERTs before UPDATEs within a flush, so
    doing both in one go would momentarily leave two primaries and trip the partial
    unique index — the same explicit flush order
    ``corpus.documents.upsert_document_version`` needs for ``is_current``.
    """
    if not _enabled(cfg):
        return None
    kind = require_security_type(security_type) if security_type else None
    key = (security_key or "").strip()[:_KEY_MAX]
    if not key:
        raise ValueError("A security needs a security_key.")

    existing = (
        await session.execute(
            select(Security).where(
                Security.legal_entity_id == legal_entity_id,
                Security.security_key == key,
            )
        )
    ).scalar_one_or_none()
    security = existing
    if security is None:
        security = Security(
            id=uuid.uuid4(),
            legal_entity_id=legal_entity_id,
            security_key=key,
            security_type=kind or SECURITY_ORDINARY_SHARE,
            description=description,
            is_primary=False,
            source=source,
        )
        session.add(security)
        await session.flush()
    else:
        if kind is not None and kind != security.security_type:
            raise ValueError(
                f"Security {key!r} is already recorded as {security.security_type!r} "
                f"and cannot be re-asserted as {kind!r}. Two sources disagreeing "
                "about what an instrument IS is evidence of a resolution problem, "
                "not something to settle by overwriting."
            )
        security.description = security.description or description
        security.source = security.source or source

    if is_primary and not security.is_primary:
        await _demote_other_primary_securities(
            session, legal_entity_id=legal_entity_id, keep_id=security.id
        )
        security.is_primary = True
        await session.flush()
    return security


async def _demote_other_primary_securities(
    session: Any, *, legal_entity_id: uuid.UUID, keep_id: uuid.UUID
) -> None:
    rows = (
        (
            await session.execute(
                select(Security).where(
                    Security.legal_entity_id == legal_entity_id,
                    Security.is_primary.is_(True),
                    Security.id != keep_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.is_primary = False
    if rows:
        await session.flush()


# ── Listings ─────────────────────────────────────────────────────────────── #


async def upsert_listing(
    session: Any,
    *,
    security_id: uuid.UUID,
    ticker: str,
    exchange: str | None,
    cfg: "Settings",
    quote_currency: str | None = None,
    listing_status: str | None = None,
    effective_from: date | None = None,
    is_primary: bool = False,
    source: str | None = None,
    source_url: str | None = None,
) -> SecurityListing | None:
    """Get-or-create the *current* listing of a security on a venue. Flush-only.

    The uniqueness that matters is enforced by
    ``ix_security_listings_current_venue_ticker``: one open window per
    ``(venue_key, ticker)``. So this never creates a second current listing for a
    venue+ticker pair, and a pair already held by a *different* security is an
    ``IntegrityError`` — which is exactly the ``BA``@``XLON`` versus ``BA``@``XNYS``
    case, refused by the database rather than by a convention.
    """
    if not _enabled(cfg):
        return None
    sym = (ticker or "").strip().upper()[:20]
    if not sym:
        raise ValueError("A listing needs a ticker.")
    code = normalize_exchange(exchange)
    info = get_exchange(code)
    venue = venue_key_for(exchange)
    status = (
        require_listing_status(listing_status) if listing_status else LISTING_UNKNOWN
    )
    if status == LISTING_DELISTED:
        # A delisted listing with an open validity window is a contradiction: it
        # would keep resolving as the CURRENT listing of its ticker while claiming
        # to be delisted. Closing the window is a different operation and it has
        # its own function, which requires the date the window closed.
        raise ValueError(
            "A delisting closes a validity window — use close_listing(...) with "
            "the date it took effect. Recording `delisted` on an open window would "
            "leave a row that resolves as current and reads as delisted."
        )

    existing = (
        await session.execute(
            select(SecurityListing).where(
                SecurityListing.venue_key == venue,
                SecurityListing.ticker == sym,
                SecurityListing.effective_to.is_(None),
            )
        )
    ).scalar_one_or_none()
    listing = existing
    if listing is not None and listing.security_id != security_id:
        raise ValueError(
            f"{sym} on {venue} is already the current listing of a different "
            "security. Two issuers cannot share a live ticker on one venue — this "
            "is the BA+LSE failure, and it is refused rather than resolved here."
        )

    if listing is None:
        listing = SecurityListing(
            id=uuid.uuid4(),
            security_id=security_id,
            venue_key=venue,
            mic=(info.mic if info is not None else None),
            exchange_code=code or None,
            ticker=sym,
            # The price-QUOTE unit, which is GBX on LSE and not the venue's
            # general currency. See SecurityListing.quote_currency.
            quote_currency=quote_currency
            or price_quote_currency_for_exchange(code)
            or (info.currency if info is not None else None),
            listing_status=status,
            effective_from=effective_from,
            is_primary=False,
            source=source,
            source_url=source_url,
        )
        session.add(listing)
        await session.flush()
    else:
        if status != LISTING_UNKNOWN:
            listing.listing_status = status
        listing.quote_currency = listing.quote_currency or quote_currency
        listing.effective_from = listing.effective_from or effective_from
        listing.source = listing.source or source
        listing.source_url = listing.source_url or source_url

    if is_primary and not listing.is_primary:
        await _demote_other_primary_listings(
            session, security_id=security_id, keep_id=listing.id
        )
        listing.is_primary = True
        await session.flush()
    return listing


async def _demote_other_primary_listings(
    session: Any, *, security_id: uuid.UUID, keep_id: uuid.UUID
) -> None:
    rows = (
        (
            await session.execute(
                select(SecurityListing).where(
                    SecurityListing.security_id == security_id,
                    SecurityListing.is_primary.is_(True),
                    SecurityListing.id != keep_id,
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        row.is_primary = False
    if rows:
        await session.flush()


async def close_listing(
    session: Any,
    *,
    listing: SecurityListing,
    effective_to: date,
    listing_status: str | None = None,
    cfg: "Settings",
) -> SecurityListing | None:
    """Close a listing's validity window — a ticker change or a delisting.

    Closing rather than updating is the whole point. An UPDATE to ``ticker`` would
    rewrite history, and every citation and price series recorded under the old
    symbol would silently start reading as the new one.
    """
    if not _enabled(cfg):
        return None
    if listing.effective_to is not None:
        return listing
    listing.effective_to = effective_to
    if listing_status:
        listing.listing_status = require_listing_status(listing_status)
    listing.is_primary = False
    await session.flush()
    return listing


async def resolve_entity_by_listing(
    session: Any,
    *,
    ticker: str,
    exchange: str | None,
    cfg: "Settings",
    on_date: date | None = None,
) -> LegalEntity | None:
    """The entity listed under ``ticker`` on ``exchange``, or ``None``.

    The V2-compatibility read path. With ``on_date`` it answers the historical
    question — "who was TICKER on this venue then?" — which a keyed
    ``(ticker, exchange)`` row cannot answer at all once a symbol is reused.

    Returns ``None`` rather than guessing when there is no listing. The fallback to
    the ``companies`` row belongs to slice 2.2, where ``companies.legal_entity_id``
    exists; adding it here would mean two places deciding what an unmapped company
    resolves to.
    """
    if not _enabled(cfg):
        return None
    sym = (ticker or "").strip().upper()
    if not sym:
        return None
    venue = venue_key_for(exchange)

    stmt = (
        select(LegalEntity)
        .join(Security, Security.legal_entity_id == LegalEntity.id)
        .join(SecurityListing, SecurityListing.security_id == Security.id)
        .where(
            SecurityListing.venue_key == venue,
            SecurityListing.ticker == sym,
        )
    )
    if on_date is None:
        stmt = stmt.where(SecurityListing.effective_to.is_(None))
    else:
        stmt = stmt.where(
            (SecurityListing.effective_from.is_(None))
            | (SecurityListing.effective_from <= on_date),
            (SecurityListing.effective_to.is_(None))
            | (SecurityListing.effective_to >= on_date),
        )
    rows = (await session.execute(stmt)).scalars().all()
    if len(rows) != 1:
        # Zero is "never seen". More than one can only happen for a historical
        # window query where a symbol was reused, and returning either would be a
        # coin flip presented as an answer. Slice 2.3 turns this into an
        # ``ambiguous`` resolution with a gap; here it is simply not an answer.
        return None
    return rows[0]


async def securities_for_entity(
    session: Any, *, legal_entity_id: uuid.UUID, cfg: "Settings"
) -> list[Security]:
    if not _enabled(cfg):
        return []
    return list(
        (
            await session.execute(
                select(Security).where(Security.legal_entity_id == legal_entity_id)
            )
        )
        .scalars()
        .all()
    )


async def listings_for_entity(
    session: Any,
    *,
    legal_entity_id: uuid.UUID,
    cfg: "Settings",
    include_closed: bool = False,
) -> list[SecurityListing]:
    """Every venue this entity is listed on — the cross-listing answer."""
    if not _enabled(cfg):
        return []
    stmt = (
        select(SecurityListing)
        .join(Security, Security.id == SecurityListing.security_id)
        .where(Security.legal_entity_id == legal_entity_id)
    )
    if not include_closed:
        stmt = stmt.where(SecurityListing.effective_to.is_(None))
    return list((await session.execute(stmt)).scalars().all())


# ── Aliases ──────────────────────────────────────────────────────────────── #


async def record_alias(
    session: Any,
    *,
    legal_entity_id: uuid.UUID,
    alias: str,
    alias_type: str,
    cfg: "Settings",
    effective_from: date | None = None,
    effective_to: date | None = None,
    source: str | None = None,
    source_url: str | None = None,
) -> EntityAlias | None:
    """Record a name the entity has also been known by. Flush-only.

    Storing an alias does **not** make it resolvable. Slice 2.3 may weigh an alias
    match as evidence beside an identifier; on its own a name match is how two
    companies get merged wrongly.
    """
    if not _enabled(cfg):
        return None
    kind = require_alias_type(alias_type)
    text = (alias or "").strip()[:_NAME_MAX]
    if not text:
        raise ValueError("An alias needs a value.")
    normalized = normalize_entity_name(text)

    existing = (
        await session.execute(
            select(EntityAlias).where(
                EntityAlias.legal_entity_id == legal_entity_id,
                EntityAlias.alias_type == kind,
                EntityAlias.normalized_alias == normalized,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.effective_from = existing.effective_from or effective_from
        existing.effective_to = existing.effective_to or effective_to
        existing.source = existing.source or source
        existing.source_url = existing.source_url or source_url
        await session.flush()
        return existing

    record = EntityAlias(
        id=uuid.uuid4(),
        legal_entity_id=legal_entity_id,
        alias=text,
        normalized_alias=normalized,
        alias_type=kind,
        effective_from=effective_from,
        effective_to=effective_to,
        source=source,
        source_url=source_url,
    )
    session.add(record)
    await session.flush()
    return record


__all__ = [
    "EntityInput",
    "GLOBAL_SCOPE",
    "close_listing",
    "entity_key_for",
    "find_entity_by_identifier",
    "listings_for_entity",
    "normalize_entity_name",
    "record_alias",
    "record_identifier",
    "resolve_entity_by_listing",
    "securities_for_entity",
    "upsert_legal_entity",
    "upsert_listing",
    "upsert_security",
    "venue_key_for",
]
