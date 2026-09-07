"""Relationships, reporting scopes and segments — V3.2 Slice 2.4.

PURPOSE
=======
Make three things representable that the platform currently cannot express:

  1. how entities relate — so consolidated versus subsidiary reporting can be
     reasoned about at all;
  2. durable identity for the ``Group``/segment vocabulary ``fact_scope.py`` already
     enforces in memory, so a segment can be followed across periods and renamings;
  3. the ADR ↔ ordinary link WITH ITS RATIO, so per-share arithmetic can refuse to
     run rather than silently applying the wrong denominator.

ADDITIVE ONLY. Three new tables and two **nullable** columns on ``securities``.
Nothing existing is altered, so ``release/v2-current`` runs unchanged. Nothing writes
to any of it unless ``V3_ENTITY_MASTER_ENABLED`` is on, which is off by default.

ONE CANONICAL DIRECTION FOR A RELATIONSHIP
==========================================
``parent_of`` and ``subsidiary_of`` are inverses, and a schema storing both can hold
two rows that contradict each other with no rule for which wins. The vocabulary
declares one canonical direction per pair and the writer normalises to it; the
inverse is a query. ``ix_entity_relationships_current`` then makes one live
relationship per ``(subject, object, type)`` a database guarantee.

WHY ``reporting_scopes.scope_key`` IS A COPY OF ``fact_scope``'s
===============================================================
It is byte-for-byte ``FactScope.scope_key`` — ``'group'`` or
``'segment:<casefolded name>'``. Every fact, chunk and calculation that carries a
scope already computes that string, so making the persisted identity anything else
would require a translation layer, and a translation layer between two definitions of
scope is how ``scope`` came to have three incompatible interpretations in three
layers before ``fact_scope`` existed. A test compares the two implementations
directly.

The key derives from the NAME, so a renamed segment gets a **new** scope and the
series splits — visibly, as two rows. ``predecessor_scope_id`` links them, and
``ck_reporting_scopes_rename_needs_a_source`` means it cannot be set without a
``rename_source``: split by default, link on evidence. Silently merging two scopes
would join one segment's figures onto another's history, which for CFR is exactly
Specialist Watchmakers figures becoming Group.

CHECK CONSTRAINTS, EACH ONE A FAILURE THAT WOULD OTHERWISE BE STORABLE
======================================================================
* ``ck_entity_relationships_no_self_reference`` — an entity is not its own parent,
  and a schema that can hold that can hold a cycle nothing detects.
* ``ck_entity_relationships_confidence_range`` — confidence is 0.0-1.0 or it is not
  a confidence.
* ``ck_reporting_scopes_rename_needs_a_source`` — see above.
* ``ck_reporting_scopes_no_self_predecessor``.
* ``ck_securities_receipt_ratio_positive`` — a ratio of zero or less is not a ratio.
  NULL stays allowed and means "not established", never 1.

FOREIGN KEYS
============
Relationship, scope and segment FKs onto ``legal_entities`` are ``CASCADE``: this is
composition of the identity graph, and a relationship missing one of its ends is
uninterpretable rather than degraded. Two exceptions, both deliberate:
``reporting_scopes.predecessor_scope_id`` is ``SET NULL`` (losing the predecessor
must not delete the successor's own figures), and
``securities.underlying_security_id`` is ``SET NULL`` (losing the underlying record
must not delete the receipt that referenced it).

NULLABILITY IS A STATEMENT
==========================
``receipt_ratio`` NULL means not established — and per-share arithmetic must refuse,
not assume 1:1. ``reporting_scopes.scope_name`` NULL is correct for ``group``, which
has no name of its own. ``business_segments.period_key`` is NOT NULL, because a
disclosure with no period cannot be placed in a series and that is the only question
the table exists to answer.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "028"
down_revision: str | None = "027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entity_relationships",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("subject_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("object_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("relationship_type", sa.String(length=40), nullable=False),
        sa.Column("source", sa.String(length=100), nullable=False),
        sa.Column("source_url", sa.String(length=2000), nullable=True),
        sa.Column(
            "confidence", sa.Float(), nullable=False, server_default=sa.text("1.0")
        ),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column("note", sa.String(length=500), nullable=True),
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
            ["subject_entity_id"],
            ["legal_entities.id"],
            name="fk_entity_relationships_subject_legal_entities",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["object_entity_id"],
            ["legal_entities.id"],
            name="fk_entity_relationships_object_legal_entities",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "subject_entity_id <> object_entity_id",
            name="ck_entity_relationships_no_self_reference",
        ),
        sa.CheckConstraint(
            "confidence >= 0.0 AND confidence <= 1.0",
            name="ck_entity_relationships_confidence_range",
        ),
    )
    op.create_index(
        "ix_entity_relationships_current",
        "entity_relationships",
        ["subject_entity_id", "object_entity_id", "relationship_type"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL"),
    )
    op.create_index(
        "ix_entity_relationships_subject", "entity_relationships", ["subject_entity_id"]
    )
    op.create_index(
        "ix_entity_relationships_object", "entity_relationships", ["object_entity_id"]
    )

    op.create_table(
        "reporting_scopes",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("scope_type", sa.String(length=20), nullable=False),
        sa.Column("scope_name", sa.String(length=200), nullable=True),
        sa.Column("scope_key", sa.String(length=220), nullable=False),
        sa.Column("predecessor_scope_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("rename_source", sa.String(length=200), nullable=True),
        sa.Column("source", sa.String(length=100), nullable=True),
        sa.Column("first_seen_period", sa.String(length=20), nullable=True),
        sa.Column("last_seen_period", sa.String(length=20), nullable=True),
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
            name="fk_reporting_scopes_legal_entity_id_legal_entities",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["predecessor_scope_id"],
            ["reporting_scopes.id"],
            name="fk_reporting_scopes_predecessor_reporting_scopes",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "predecessor_scope_id IS NULL OR rename_source IS NOT NULL",
            name="ck_reporting_scopes_rename_needs_a_source",
        ),
        sa.CheckConstraint(
            "predecessor_scope_id IS NULL OR predecessor_scope_id <> id",
            name="ck_reporting_scopes_no_self_predecessor",
        ),
    )
    op.create_index(
        "ix_reporting_scopes_entity_key",
        "reporting_scopes",
        ["legal_entity_id", "scope_key"],
        unique=True,
    )
    op.create_index(
        "ix_reporting_scopes_scope_key", "reporting_scopes", ["scope_key"]
    )

    op.create_table(
        "business_segments",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("reporting_scope_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("reported_name", sa.String(length=200), nullable=False),
        sa.Column("normalized_name", sa.String(length=200), nullable=False),
        sa.Column("period_key", sa.String(length=20), nullable=False),
        sa.Column("period_type", sa.String(length=20), nullable=True),
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
            ["reporting_scope_id"],
            ["reporting_scopes.id"],
            name="fk_business_segments_reporting_scope_id_reporting_scopes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_business_segments_legal_entity_id_legal_entities",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_business_segments_scope_period",
        "business_segments",
        ["reporting_scope_id", "period_key"],
        unique=True,
    )
    op.create_index(
        "ix_business_segments_entity_period",
        "business_segments",
        ["legal_entity_id", "period_key"],
    )
    op.create_index(
        "ix_business_segments_normalized_name", "business_segments", ["normalized_name"]
    )

    # The ADR ↔ ordinary link, between INSTRUMENTS. Two nullable columns.
    op.add_column(
        "securities",
        sa.Column("underlying_security_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.add_column(
        "securities", sa.Column("receipt_ratio", sa.Numeric(20, 6), nullable=True)
    )
    op.create_foreign_key(
        "fk_securities_underlying_security_id_securities",
        "securities",
        "securities",
        ["underlying_security_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "ck_securities_receipt_ratio_positive",
        "securities",
        "receipt_ratio IS NULL OR receipt_ratio > 0",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_securities_receipt_ratio_positive", "securities", type_="check"
    )
    op.drop_constraint(
        "fk_securities_underlying_security_id_securities",
        "securities",
        type_="foreignkey",
    )
    op.drop_column("securities", "receipt_ratio")
    op.drop_column("securities", "underlying_security_id")

    op.drop_index(
        "ix_business_segments_normalized_name", table_name="business_segments"
    )
    op.drop_index("ix_business_segments_entity_period", table_name="business_segments")
    op.drop_index("ix_business_segments_scope_period", table_name="business_segments")
    op.drop_table("business_segments")

    op.drop_index("ix_reporting_scopes_scope_key", table_name="reporting_scopes")
    op.drop_index("ix_reporting_scopes_entity_key", table_name="reporting_scopes")
    op.drop_table("reporting_scopes")

    op.drop_index(
        "ix_entity_relationships_object", table_name="entity_relationships"
    )
    op.drop_index(
        "ix_entity_relationships_subject", table_name="entity_relationships"
    )
    op.drop_index("ix_entity_relationships_current", table_name="entity_relationships")
    op.drop_table("entity_relationships")
