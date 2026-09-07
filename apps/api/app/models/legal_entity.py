"""The entity master — V3.2 Slice 2.1.

    legal_entities ──< securities ──< security_listings
          │                 │
          │                 └──< entity_identifiers  (isin, figi)
          ├──< entity_identifiers  (lei, cik, company_register)
          └──< entity_aliases      (former legal name, trade name, …)

WHAT THIS REPLACES, AND WHAT IT DOES NOT
========================================
``companies`` is keyed ``UNIQUE (ticker, exchange)`` and is **not touched by this
slice**. Every existing ``company_id`` FK keeps resolving; the entity master is
built beside it, and ``companies.legal_entity_id`` arrives in slice 2.2 with the
backfill. Nothing here can break a V2 report because nothing here is referenced by
one yet.

What it makes structurally impossible is the failure that keyed identity produced
live: ``BA`` + LSE resolving to Boeing's CIK when the issuer was BAE Systems. A
CIK is an identifier **of a legal entity**. It is reached from a listing by way of
the security and the entity, never derived from a ticker string, and two listings
that share a ticker on different venues cannot see each other's identifiers.

WHY EVERY TABLE HAS AN EXPLICIT KEY
===================================
``entity_key``, ``security_key`` and ``venue_key`` exist because a *name* is not
an identity and a nullable column cannot enforce one. The two failure directions
are not symmetric:

* splitting one issuer into two entities produces a **duplicate** — visible,
  repairable, and it never attributes anything to the wrong company;
* merging two issuers into one entity attributes one company's filings to
  another, which is the most dangerous failure this platform has (CLAUDE.md
  rule 6).

So the keys deliberately **over-split**. An entity created from nothing but a
listing gets ``listing:XCSE:PNDORA``, and when a LEI arrives later the key is not
rewritten — promoting or merging an entity is an explicit, evidenced operation and
it belongs to slice 2.3.

NULLABILITY IS A STATEMENT
==========================
``jurisdiction`` NULL means the platform has not established where the entity is
registered. ``entity_status`` defaults to ``unknown`` and is never upgraded to
``active`` because a document was found — a dissolved entity's last annual report
is still fetchable. ``effective_from`` / ``effective_to`` NULL mean the window is
open at that end, not that it is unknown; ``effective_to IS NULL`` is what "still
current" means and the partial unique indexes are built on it.
"""

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base
from app.services.entities.vocabulary import DEFAULT_ENTITY_STATUS, LISTING_UNKNOWN


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LegalEntity(Base):
    """The issuer as a legal person — the thing a filing is filed by."""

    __tablename__ = "legal_entities"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: The stable identity, derived from the strongest identifier available when
    #: the row was created: ``lei:…`` | ``cik:…`` | ``register:DK:…`` |
    #: ``listing:<venue>:<ticker>``. Never rewritten. See the module docstring for
    #: why the fallback over-splits on purpose.
    entity_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: Best-known legal name. Advisory: two entities may legally share a name in
    #: different jurisdictions, so there is deliberately **no** unique constraint
    #: on it and nothing resolves an entity from it.
    legal_name: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: Case- and punctuation-folded ``legal_name``, for *lookup* only. A match here
    #: is evidence for slice 2.3 to weigh, never an identity.
    normalized_name: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: ISO 3166-1 alpha-2 country of registration. NULL means not established.
    jurisdiction: Mapped[str | None] = mapped_column(sa.String(2))
    #: ``A/S`` | ``plc`` | ``Inc.`` — as the entity writes it, not normalised into
    #: a taxonomy this platform would then have to maintain per country.
    legal_form: Mapped[str | None] = mapped_column(sa.String(60))
    #: ``active`` | ``inactive`` | ``dissolved`` | ``unknown``. Defaults to
    #: ``unknown``; see the module docstring.
    entity_status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=DEFAULT_ENTITY_STATUS
    )
    #: Which source established ``entity_status`` — a status with no source is a
    #: guess, and this column is what makes that visible.
    status_source: Mapped[str | None] = mapped_column(sa.String(100))
    #: How the row came to exist (``gleif`` | ``sec_edgar`` | ``company_backfill``
    #: | ``manual``). Provenance, not authority.
    source: Mapped[str | None] = mapped_column(sa.String(100))
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.UniqueConstraint("entity_key", name="uq_legal_entities_entity_key"),
        # "Which entities look like this name?" is slice 2.3's candidate query and
        # must not be a sequential scan over the universe.
        sa.Index("ix_legal_entities_normalized_name", "normalized_name"),
        sa.Index("ix_legal_entities_jurisdiction", "jurisdiction"),
    )


class Security(Base):
    """An instrument the entity issued — ordinary shares, an ADR, a share class."""

    __tablename__ = "securities"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: CASCADE because this is composition, not lineage: a security without its
    #: issuer is meaningless. Entities are never deleted; the cascade exists so
    #: the schema cannot hold an orphan.
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="CASCADE",
            name="fk_securities_legal_entity_id_legal_entities",
        ),
        nullable=False,
    )
    #: Stable identity within one entity: ``isin:…`` when the instrument declares
    #: one, else ``<security_type>:<ordinal>``.
    security_key: Mapped[str] = mapped_column(sa.String(120), nullable=False)
    #: A closed vocabulary — see ``app.services.entities.vocabulary``. ``adr`` and
    #: ``gdr`` are named rather than folded into ``ordinary_share`` because a
    #: depositary receipt represents underlying shares at a ratio, and treating
    #: one as an ordinary share applies the wrong per-share arithmetic silently.
    security_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    description: Mapped[str | None] = mapped_column(sa.String(300))
    #: The instrument research is *about* by default. At most one per entity,
    #: enforced by a partial unique index rather than by the writer.
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    #: The instrument a depositary receipt represents (V3.2 Slice 2.4). The ADR ↔
    #: ordinary link belongs between INSTRUMENTS, not entities: a receipt represents
    #: underlying *shares* at a ratio. ``SET NULL`` rather than CASCADE — losing the
    #: underlying record must not delete the receipt that referenced it.
    underlying_security_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "securities.id",
            ondelete="SET NULL",
            name="fk_securities_underlying_security_id_securities",
        ),
        nullable=True,
    )
    #: **Underlying shares represented by ONE receipt.** The direction is stated
    #: because a bare "ratio" of 4 could mean four receipts per share or four shares
    #: per receipt, and a reader who guesses wrong is out by 16x. ``4`` is a receipt
    #: over four shares; ``0.25`` is four receipts to the share.
    #:
    #: NULL means **not established**, never 1 — that substitution is why per-share
    #: arithmetic must be able to refuse. See
    #: ``app.services.entities.scopes.underlying_shares_per_unit``.
    receipt_ratio: Mapped[float | None] = mapped_column(sa.Numeric(20, 6))
    source: Mapped[str | None] = mapped_column(sa.String(100))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index(
            "ix_securities_entity_key", "legal_entity_id", "security_key", unique=True
        ),
        sa.Index(
            "ix_securities_one_primary",
            "legal_entity_id",
            unique=True,
            postgresql_where=sa.text("is_primary"),
            sqlite_where=sa.text("is_primary"),
        ),
        # A ratio of zero or less is not a ratio. NULL stays allowed and means
        # "not established".
        sa.CheckConstraint(
            "receipt_ratio IS NULL OR receipt_ratio > 0",
            name="ck_securities_receipt_ratio_positive",
        ),
    )


class SecurityListing(Base):
    """A venue listing — where ``(ticker, exchange)`` finally belongs.

    The partial unique index on ``(venue_key, ticker)`` for open windows is what
    retires the Boeing bug at the schema level: one ticker on one venue is one
    instrument at a time, and ``BA``@``XLON`` and ``BA``@``XNYS`` are two rows that
    cannot reach each other's issuer.
    """

    __tablename__ = "security_listings"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    security_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "securities.id",
            ondelete="CASCADE",
            name="fk_security_listings_security_id_securities",
        ),
        nullable=False,
    )
    #: The MIC where the venue has one, else the ``exchange_registry`` code.
    #: **NOT NULL by construction** — the uniqueness guarantee is a partial unique
    #: index over ``(venue_key, ticker)``, and PostgreSQL treats NULLs as distinct
    #: in a unique index, so a nullable venue column would have silently permitted
    #: the very collision the index exists to prevent.
    venue_key: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: ISO 10383 MIC. NULL when the venue has none in ``exchange_registry``.
    mic: Mapped[str | None] = mapped_column(sa.String(4))
    #: The ``exchange_registry`` / EODHD-style code, kept so the existing pipeline
    #: can look a listing up with what it already has.
    exchange_code: Mapped[str | None] = mapped_column(sa.String(20))
    ticker: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: The price-QUOTE unit at this venue, from
    #: ``exchange_registry.price_quote_currency_for_exchange`` — **not** the
    #: issuer's reporting currency and not ``ExchangeInfo.currency``. LSE main-market
    #: equities are quoted in pence (``GBX``), so storing the venue's general
    #: currency here would mislabel every London price as 100x its real pound value.
    #: That distinction already cost this repository a fix once; the column name says
    #: which of the two it is so a reader cannot assume the other.
    quote_currency: Mapped[str | None] = mapped_column(sa.String(10))
    listing_status: Mapped[str] = mapped_column(
        sa.String(20), nullable=False, default=LISTING_UNKNOWN
    )
    #: NULL means the window is open at that end. ``effective_to IS NULL`` is what
    #: "still current" means, and the uniqueness index is built on it — so a ticker
    #: change is closing one window and opening another, never an UPDATE that
    #: rewrites history.
    effective_from: Mapped[date | None] = mapped_column(sa.Date())
    effective_to: Mapped[date | None] = mapped_column(sa.Date())
    #: The venue research quotes by default. At most one per security.
    is_primary: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    source: Mapped[str | None] = mapped_column(sa.String(100))
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        # One ticker, one venue, one instrument AT A TIME. Historical windows are
        # excluded, so a ticker may be reused after a delisting.
        sa.Index(
            "ix_security_listings_current_venue_ticker",
            "venue_key",
            "ticker",
            unique=True,
            postgresql_where=sa.text("effective_to IS NULL"),
            sqlite_where=sa.text("effective_to IS NULL"),
        ),
        # The lookup the existing pipeline performs: "who is TICKER on EXCHANGE?"
        sa.Index("ix_security_listings_exchange_ticker", "exchange_code", "ticker"),
        sa.Index("ix_security_listings_security_id", "security_id"),
        sa.Index(
            "ix_security_listings_one_primary",
            "security_id",
            unique=True,
            postgresql_where=sa.text("is_primary"),
            sqlite_where=sa.text("is_primary"),
        ),
    )


class EntityIdentifier(Base):
    """One sourced, effective-dated identifier for an entity **or** a security.

    Never a bare string on the parent row. An identifier has a scheme, a source, a
    confidence and a validity window, and a platform that stores it as a column
    loses all four the first time two sources disagree.
    """

    __tablename__ = "entity_identifiers"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: Exactly one of these two is set — a CHECK constraint enforces it. An ISIN on
    #: a legal entity would be meaningful for a single-security issuer and silently
    #: wrong for every cross-listed or multi-class one.
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="CASCADE",
            name="fk_entity_identifiers_legal_entity_id_legal_entities",
        ),
        nullable=True,
    )
    security_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "securities.id",
            ondelete="CASCADE",
            name="fk_entity_identifiers_security_id_securities",
        ),
        nullable=True,
    )
    #: ``lei`` | ``cik`` | ``company_register`` | ``isin`` | ``figi``. Closed set.
    scheme: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    #: The value exactly as the source gave it, for auditing.
    value: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: The validated, normalised form the uniqueness index and every join use.
    value_normalized: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    #: ``*`` for the globally unique registries, the jurisdiction for a national
    #: company register. NOT NULL — see ``SecurityListing.venue_key`` for why a
    #: nullable scope column would have defeated the index.
    scope_key: Mapped[str] = mapped_column(sa.String(10), nullable=False)
    jurisdiction: Mapped[str | None] = mapped_column(sa.String(2))
    #: True only when the scheme has check digits AND they were verified. A CIK has
    #: none; a FIGI's are deliberately not checked while OPEN DECISION #10 is open.
    #: A reader deciding how far to trust a value needs this, not a comment.
    checksum_verified: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.false()
    )
    source: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    #: 0.0-1.0. An identifier straight from its own registry is 1.0; one inferred
    #: from a third party is not, and the difference must survive persistence.
    confidence: Mapped[float] = mapped_column(
        sa.Float(), nullable=False, default=1.0, server_default=sa.text("1.0")
    )
    effective_from: Mapped[date | None] = mapped_column(sa.Date())
    effective_to: Mapped[date | None] = mapped_column(sa.Date())
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        # Two legal entities cannot both CURRENTLY hold one LEI. Enforced by the
        # database, because a read-then-write check is one two concurrent writers
        # can both pass.
        sa.Index(
            "ix_entity_identifiers_current_value",
            "scheme",
            "value_normalized",
            "scope_key",
            unique=True,
            postgresql_where=sa.text("effective_to IS NULL"),
            sqlite_where=sa.text("effective_to IS NULL"),
        ),
        # "Which entity has this LEI?" — the resolution lookup.
        sa.Index("ix_entity_identifiers_scheme_value", "scheme", "value_normalized"),
        sa.Index("ix_entity_identifiers_legal_entity_id", "legal_entity_id"),
        sa.Index("ix_entity_identifiers_security_id", "security_id"),
        # A schema that can hold an identifier attached to neither subject, or to
        # both, can hold an identifier nobody can interpret.
        sa.CheckConstraint(
            "(legal_entity_id IS NOT NULL AND security_id IS NULL) OR "
            "(legal_entity_id IS NULL AND security_id IS NOT NULL)",
            name="ck_entity_identifiers_exactly_one_subject",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_entity_identifiers_confidence_range",
        ),
    )


class EntityAlias(Base):
    """A name the entity has also been known by.

    **Not a resolution mechanism.** Matching on a name is precisely how two
    companies get merged wrongly, so an alias is stored as *evidence* for slice
    2.3 to weigh alongside identifiers, and nothing resolves an entity from one.
    Its real job is the case the platform has already met: a ticker change or a
    rename that would otherwise rewrite history, and a report whose ``legal_name``
    came back as the ticker.
    """

    __tablename__ = "entity_aliases"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="CASCADE",
            name="fk_entity_aliases_legal_entity_id_legal_entities",
        ),
        nullable=False,
    )
    alias: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    normalized_alias: Mapped[str] = mapped_column(sa.String(300), nullable=False)
    #: ``former_legal_name`` | ``trade_name`` | ``short_name`` |
    #: ``transliteration`` | ``former_ticker``. Closed set.
    alias_type: Mapped[str] = mapped_column(sa.String(30), nullable=False)
    effective_from: Mapped[date | None] = mapped_column(sa.Date())
    effective_to: Mapped[date | None] = mapped_column(sa.Date())
    source: Mapped[str | None] = mapped_column(sa.String(100))
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index(
            "ix_entity_aliases_entity_type_name",
            "legal_entity_id",
            "alias_type",
            "normalized_alias",
            unique=True,
        ),
        sa.Index("ix_entity_aliases_normalized_alias", "normalized_alias"),
    )


class EntityRelationship(Base):
    """One typed, sourced, effective-dated link between two legal entities.

    ONE CANONICAL DIRECTION
    =======================
    ``parent_of`` and ``subsidiary_of`` are inverses, and a schema that stores both
    can hold two rows that contradict each other with no rule for which wins. So the
    vocabulary declares one canonical direction per pair and the writer normalises to
    it: asserting ``subsidiary_of(A, B)`` stores ``parent_of(B, A)``. The inverse is a
    query, never a row — see ``app.services.entities.relationships``.

    WHY IT MATTERS BEYOND TIDINESS
    ==============================
    Consolidated versus subsidiary reporting is currently unrepresentable, so the
    platform cannot reason about whether a filing covers the group or a part of it.
    That is a correctness question about every number in a subsidiary's accounts.
    """

    __tablename__ = "entity_relationships"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    #: CASCADE both sides: this is composition of the identity graph, and a
    #: relationship missing one of its ends is uninterpretable rather than degraded.
    subject_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="CASCADE",
            name="fk_entity_relationships_subject_legal_entities",
        ),
        nullable=False,
    )
    object_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="CASCADE",
            name="fk_entity_relationships_object_legal_entities",
        ),
        nullable=False,
    )
    #: A member of ``CANONICAL_RELATIONSHIP_TYPES`` only. An alias a caller asserted
    #: is resolved before the write.
    relationship_type: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    source: Mapped[str] = mapped_column(sa.String(100), nullable=False)
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    #: 0.0-1.0. A relationship read off an org chart in a filing is not a
    #: relationship inferred from a shared address, and the difference must survive.
    confidence: Mapped[float] = mapped_column(
        sa.Float(), nullable=False, default=1.0, server_default=sa.text("1.0")
    )
    #: ``effective_to IS NULL`` means still current — the same convention listings
    #: and identifiers use, and the partial unique index is built on it.
    effective_from: Mapped[date | None] = mapped_column(sa.Date())
    effective_to: Mapped[date | None] = mapped_column(sa.Date())
    note: Mapped[str | None] = mapped_column(sa.String(500))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index(
            "ix_entity_relationships_current",
            "subject_entity_id",
            "object_entity_id",
            "relationship_type",
            unique=True,
            postgresql_where=sa.text("effective_to IS NULL"),
            sqlite_where=sa.text("effective_to IS NULL"),
        ),
        sa.Index("ix_entity_relationships_subject", "subject_entity_id"),
        sa.Index("ix_entity_relationships_object", "object_entity_id"),
        # An entity is not its own parent, and a schema that can hold that can hold
        # a cycle nothing detects.
        sa.CheckConstraint(
            "subject_entity_id <> object_entity_id",
            name="ck_entity_relationships_no_self_reference",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_entity_relationships_confidence_range",
        ),
    )


class ReportingScope(Base):
    """The durable identity of a scope a figure can be reported at.

    ``scope_key`` is **exactly** ``fact_scope.FactScope.scope_key`` — ``'group'`` or
    ``'segment:<casefolded name>'`` — so the persisted identity and the in-memory
    semantics cannot drift. ``region`` and ``division`` extend it with the levels the
    architecture document names.

    IT OVER-SPLITS, AND THAT IS THE DESIGN
    ======================================
    ``scope_key`` derives from the name, so a renamed segment produces a **new**
    scope: "Watches" is not "Specialist Watchmakers" and the series splits, visibly,
    as two rows. Linking them is an explicit sourced assertion via
    ``predecessor_scope_id``, which may only be set together with a ``rename_source``.
    Split by default, link on evidence — the same asymmetry ``entity_key`` uses.
    Merging two scopes silently would join one segment's figures onto another's
    history, which for CFR means Specialist Watchmakers figures becoming Group.
    """

    __tablename__ = "reporting_scopes"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="CASCADE",
            name="fk_reporting_scopes_legal_entity_id_legal_entities",
        ),
        nullable=False,
    )
    #: ``group`` | ``segment`` | ``region`` | ``division``.
    scope_type: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: The label as first seen. NULL for ``group``, which has no name of its own.
    scope_name: Mapped[str | None] = mapped_column(sa.String(200))
    #: ``fact_scope.FactScope.scope_key``, byte-for-byte. The join key for every
    #: fact, chunk and calculation that carries a scope.
    scope_key: Mapped[str] = mapped_column(sa.String(220), nullable=False)
    #: The scope this one replaced. Settable ONLY with ``rename_source`` — a rename
    #: is an assertion about the world and needs a source like any other.
    predecessor_scope_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "reporting_scopes.id",
            ondelete="SET NULL",
            name="fk_reporting_scopes_predecessor_reporting_scopes",
        ),
        nullable=True,
    )
    rename_source: Mapped[str | None] = mapped_column(sa.String(200))
    source: Mapped[str | None] = mapped_column(sa.String(100))
    first_seen_period: Mapped[str | None] = mapped_column(sa.String(20))
    last_seen_period: Mapped[str | None] = mapped_column(sa.String(20))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index(
            "ix_reporting_scopes_entity_key",
            "legal_entity_id",
            "scope_key",
            unique=True,
        ),
        sa.Index("ix_reporting_scopes_scope_key", "scope_key"),
        # A predecessor without a source is an unsourced merge of two series.
        sa.CheckConstraint(
            "predecessor_scope_id IS NULL OR rename_source IS NOT NULL",
            name="ck_reporting_scopes_rename_needs_a_source",
        ),
        sa.CheckConstraint(
            "predecessor_scope_id IS NULL OR predecessor_scope_id <> id",
            name="ck_reporting_scopes_no_self_predecessor",
        ),
    )


class BusinessSegment(Base):
    """One period's *disclosure* of a reporting scope, with the name as printed.

    A ``ReportingScope`` is the durable identity; this is what a specific report
    called it in a specific period. The separation is what makes a renaming visible
    rather than lossy — two rows, different ``reported_name``, different
    ``period_key``, and once a source says so, one scope lineage behind them.
    """

    __tablename__ = "business_segments"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    reporting_scope_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "reporting_scopes.id",
            ondelete="CASCADE",
            name="fk_business_segments_reporting_scope_id_reporting_scopes",
        ),
        nullable=False,
    )
    #: Denormalized so "every segment this issuer disclosed" is one index scan.
    legal_entity_id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="CASCADE",
            name="fk_business_segments_legal_entity_id_legal_entities",
        ),
        nullable=False,
    )
    #: Verbatim, as the report printed it. Never normalised away — the exact wording
    #: is what a citation has to be able to quote.
    reported_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    normalized_name: Mapped[str] = mapped_column(sa.String(200), nullable=False)
    #: ``ReportingPeriod.key`` form. NOT NULL: a disclosure with no period cannot be
    #: placed in a series, and "which period was this segment reported for" is the
    #: only question this table exists to answer.
    period_key: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    period_type: Mapped[str | None] = mapped_column(sa.String(20))
    source: Mapped[str | None] = mapped_column(sa.String(100))
    source_url: Mapped[str | None] = mapped_column(sa.String(2000))
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True),
        default=_utcnow,
        server_default=sa.func.now(),
        onupdate=_utcnow,
    )

    __table_args__ = (
        sa.Index(
            "ix_business_segments_scope_period",
            "reporting_scope_id",
            "period_key",
            unique=True,
        ),
        sa.Index("ix_business_segments_entity_period", "legal_entity_id", "period_key"),
        sa.Index("ix_business_segments_normalized_name", "normalized_name"),
    )
