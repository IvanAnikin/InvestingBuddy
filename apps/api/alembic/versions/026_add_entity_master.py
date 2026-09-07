"""Add the entity master — V3.2 Slice 2.1.

PURPOSE
=======
Give the platform durable legal-entity, security and listing identity, so that
"which company is this?" stops being answered by a ``(ticker, exchange)`` string
pair.

    legal_entities ──< securities ──< security_listings
          │                 │
          │                 └──< entity_identifiers  (isin, figi)
          ├──< entity_identifiers  (lei, cik, company_register)
          └──< entity_aliases      (former legal name, trade name, …)

WHAT THIS FIXES, IN THIS REPOSITORY'S OWN HISTORY
=================================================
``companies`` is keyed ``UNIQUE (ticker, exchange)``, and looking a bare ticker up
in SEC's ``company_tickers.json`` returned **Boeing's** CIK for BAE Systems,
Moelis for LVMH and Estée Lauder for EssilorLuxottica. All three were fixed by
special-casing rather than by identity. After this migration a CIK is an
identifier *of a legal entity*, reachable only through a listing → security →
entity chain, and ``ix_security_listings_current_venue_ticker`` makes two live
issuers sharing a ticker on one venue an ``IntegrityError``.

ADDITIVE ONLY. Creates five tables and alters nothing — ``companies`` is not
touched, no existing column changes, no existing FK moves. Code on
``release/v2-current`` therefore runs unchanged against a database with this
applied, which is the property that makes rollback real. Nothing writes to any of
these tables unless ``V3_ENTITY_MASTER_ENABLED`` is on, which is off by default.

FOREIGN KEYS, EACH CHOSEN DELIBERATELY
======================================
Every FK here is ``CASCADE``, and that is a departure from the corpus tables worth
stating. Those used ``SET NULL`` because they record *lineage* — that research was
performed for a company — and CLAUDE.md rule 15 says research history is preserved.
These record *composition*: a security without its issuer, a listing without its
security, an identifier without its subject are not degraded records, they are
uninterpretable ones. Entities are never deleted in normal operation; the cascade
exists so the schema cannot hold an orphan.

No FK points at ``companies``. The link between the two identity models is
``companies.legal_entity_id`` and it arrives in slice 2.2 with the backfill, in one
place, so there is never a period in which two tables disagree about it.

INDEXES, AND WHY EACH ONE EXISTS
================================
* ``uq_legal_entities_entity_key`` — the identity. Derived from the strongest
  identifier available at creation (``lei:`` | ``cik:`` | ``register:`` |
  ``listing:``) and never rewritten.
* ``ix_legal_entities_normalized_name`` — slice 2.3's candidate query. There is
  deliberately **no unique index on the name**: two entities may legally share a
  name in different jurisdictions, and a unique name would force exactly the merge
  this schema exists to prevent.
* ``ix_securities_entity_key`` (UNIQUE) — one instrument per key per entity.
* ``ix_securities_one_primary`` (UNIQUE, PARTIAL on ``is_primary``) — "at most one
  primary security per entity" as a constraint rather than a convention.
* ``ix_security_listings_current_venue_ticker`` (UNIQUE, PARTIAL on
  ``effective_to IS NULL``) — **the Boeing fix.** One ticker on one venue is one
  instrument at a time. Historical windows are excluded, so a symbol may be reused
  after a delisting without the index refusing it.
* ``ix_security_listings_exchange_ticker`` — the lookup the existing pipeline
  already performs, by the code it already holds.
* ``ix_security_listings_one_primary`` (UNIQUE, PARTIAL) — one primary venue.
* ``ix_entity_identifiers_current_value`` (UNIQUE, PARTIAL on
  ``effective_to IS NULL``) — two subjects cannot both *currently* hold one LEI,
  CIK or ISIN. Enforced by the DATABASE because a read-then-write check is one two
  concurrent writers can both pass.
* ``ix_entity_identifiers_scheme_value`` — "which entity has this LEI?".
* ``ix_entity_aliases_entity_type_name`` (UNIQUE) — one alias per kind per entity.

WHY ``scope_key`` AND ``venue_key`` ARE NOT NULL
================================================
Both are the discriminating column of a partial unique index, and **PostgreSQL
treats NULLs as DISTINCT in a unique index**. A nullable jurisdiction column would
have let two rows with the same LEI and a NULL scope coexist — silently permitting
the precise collision the index exists to prevent. So the global case is the
literal string ``*`` and a venue with no MIC falls back to the
``exchange_registry`` code. The uniqueness is a property of the schema rather than
of the writer's memory.

CHECK CONSTRAINTS
=================
* ``ck_entity_identifiers_exactly_one_subject`` — an identifier attached to neither
  subject, or to both, is one nobody can interpret. An ISIN on a legal entity
  reads correctly for a single-security issuer and is silently wrong for every
  cross-listed or multi-class one, which is the entire class of issuer this phase
  exists for.
* ``ck_entity_identifiers_confidence_range`` — confidence is 0.0-1.0 or it is not a
  confidence.

NULLABILITY IS A STATEMENT
==========================
``security_listings.quote_currency`` is the price-QUOTE unit rather than the
issuer's reporting currency, and it is named that way on purpose: LSE main-market
equities are quoted in pence, and a column called ``currency`` invites a reader to
join it against a reporting currency and mislabel every London price as 100x its
real value. ``jurisdiction`` NULL means the platform has not established where the
entity is registered; it is never defaulted to the listing venue's country, because where a
company trades is not where it is incorporated. ``entity_status`` defaults to
``unknown`` and is never upgraded to ``active`` because a document was found — a
dissolved entity's last annual report is still fetchable. ``effective_to`` NULL
means the window is open, which is what "still current" means and what both
partial indexes are built on; a ticker change closes one window and opens another
rather than UPDATE-ing a symbol and rewriting history.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "026"
down_revision: str | None = "025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "legal_entities",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("entity_key", sa.String(length=120), nullable=False),
        sa.Column("legal_name", sa.String(length=300), nullable=False),
        sa.Column("normalized_name", sa.String(length=300), nullable=False),
        sa.Column("jurisdiction", sa.String(length=2), nullable=True),
        sa.Column("legal_form", sa.String(length=60), nullable=True),
        sa.Column("entity_status", sa.String(length=20), nullable=False),
        sa.Column("status_source", sa.String(length=100), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.UniqueConstraint("entity_key", name="uq_legal_entities_entity_key"),
    )
    op.create_index(
        "ix_legal_entities_normalized_name", "legal_entities", ["normalized_name"]
    )
    op.create_index(
        "ix_legal_entities_jurisdiction", "legal_entities", ["jurisdiction"]
    )

    op.create_table(
        "securities",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("security_key", sa.String(length=120), nullable=False),
        sa.Column("security_type", sa.String(length=30), nullable=False),
        sa.Column("description", sa.String(length=300), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_securities_legal_entity_id_legal_entities",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_securities_entity_key",
        "securities",
        ["legal_entity_id", "security_key"],
        unique=True,
    )
    op.create_index(
        "ix_securities_one_primary",
        "securities",
        ["legal_entity_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
        sqlite_where=sa.text("is_primary"),
    )

    op.create_table(
        "security_listings",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("security_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("venue_key", sa.String(length=20), nullable=False),
        sa.Column("mic", sa.String(length=4), nullable=True),
        sa.Column("exchange_code", sa.String(length=20), nullable=True),
        sa.Column("ticker", sa.String(length=20), nullable=False),
        sa.Column("quote_currency", sa.String(length=10), nullable=True),
        sa.Column("listing_status", sa.String(length=20), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "is_primary", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name="fk_security_listings_security_id_securities",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_security_listings_current_venue_ticker",
        "security_listings",
        ["venue_key", "ticker"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "ix_security_listings_exchange_ticker",
        "security_listings",
        ["exchange_code", "ticker"],
    )
    op.create_index(
        "ix_security_listings_security_id", "security_listings", ["security_id"]
    )
    op.create_index(
        "ix_security_listings_one_primary",
        "security_listings",
        ["security_id"],
        unique=True,
        postgresql_where=sa.text("is_primary"),
        sqlite_where=sa.text("is_primary"),
    )

    op.create_table(
        "entity_identifiers",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("security_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("scheme", sa.String(length=30), nullable=False),
        sa.Column("value", sa.String(length=40), nullable=False),
        sa.Column("value_normalized", sa.String(length=40), nullable=False),
        sa.Column("scope_key", sa.String(length=10), nullable=False),
        sa.Column("jurisdiction", sa.String(length=2), nullable=True),
        sa.Column(
            "checksum_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column(
            "confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")
        ),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_entity_identifiers_legal_entity_id_legal_entities",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["security_id"],
            ["securities.id"],
            name="fk_entity_identifiers_security_id_securities",
            ondelete="CASCADE",
        ),
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
    op.create_index(
        "ix_entity_identifiers_current_value",
        "entity_identifiers",
        ["scheme", "value_normalized", "scope_key"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "ix_entity_identifiers_scheme_value",
        "entity_identifiers",
        ["scheme", "value_normalized"],
    )
    op.create_index(
        "ix_entity_identifiers_legal_entity_id",
        "entity_identifiers",
        ["legal_entity_id"],
    )
    op.create_index(
        "ix_entity_identifiers_security_id", "entity_identifiers", ["security_id"]
    )

    op.create_table(
        "entity_aliases",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("alias", sa.String(length=300), nullable=False),
        sa.Column("normalized_alias", sa.String(length=300), nullable=False),
        sa.Column("alias_type", sa.String(length=30), nullable=False),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_entity_aliases_legal_entity_id_legal_entities",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_entity_aliases_entity_type_name",
        "entity_aliases",
        ["legal_entity_id", "alias_type", "normalized_alias"],
        unique=True,
    )
    op.create_index(
        "ix_entity_aliases_normalized_alias", "entity_aliases", ["normalized_alias"]
    )


def downgrade() -> None:
    op.drop_index("ix_entity_aliases_normalized_alias", table_name="entity_aliases")
    op.drop_index("ix_entity_aliases_entity_type_name", table_name="entity_aliases")
    op.drop_table("entity_aliases")

    op.drop_index("ix_entity_identifiers_security_id", table_name="entity_identifiers")
    op.drop_index(
        "ix_entity_identifiers_legal_entity_id", table_name="entity_identifiers"
    )
    op.drop_index("ix_entity_identifiers_scheme_value", table_name="entity_identifiers")
    op.drop_index(
        "ix_entity_identifiers_current_value", table_name="entity_identifiers"
    )
    op.drop_table("entity_identifiers")

    op.drop_index("ix_security_listings_one_primary", table_name="security_listings")
    op.drop_index("ix_security_listings_security_id", table_name="security_listings")
    op.drop_index(
        "ix_security_listings_exchange_ticker", table_name="security_listings"
    )
    op.drop_index(
        "ix_security_listings_current_venue_ticker", table_name="security_listings"
    )
    op.drop_table("security_listings")

    op.drop_index("ix_securities_one_primary", table_name="securities")
    op.drop_index("ix_securities_entity_key", table_name="securities")
    op.drop_table("securities")

    op.drop_index("ix_legal_entities_jurisdiction", table_name="legal_entities")
    op.drop_index("ix_legal_entities_normalized_name", table_name="legal_entities")
    op.drop_table("legal_entities")
