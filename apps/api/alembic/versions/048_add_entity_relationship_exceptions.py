"""Relationship reporting exceptions — V3.20 slice 2.2 (spec §3.2, GLEIF Level 2).

WHY A TABLE
===========
GLEIF Level 2 answers "who is this entity's parent?" in one of THREE ways, and only one
of them is an edge:

* a parent LEI record                  → an ``entity_relationships`` row (``parent_of`` /
                                          ``ultimate_parent_of``), cited to GLEIF;
* a REPORTING EXCEPTION                → "no parent reported, because NATURAL_PERSONS /
  (``NATURAL_PERSONS``, ``NON_CONSOLIDATING``, NON_CONSOLIDATING / NO_KNOWN_PERSON / …".
  ``NO_KNOWN_PERSON``, …)                 A fact about the entity, with a reason, and NOT
                                          the same thing as "independent";
* nothing at all                       → unknown. No row.

The second answer has nowhere to live in ``entity_relationships`` (there is no other end
to name), and dropping it would turn "controlled by private individuals" into the same
blank as "never asked". So it gets a row here, effective-dated like an edge: when a
parent is later reported the exception is CLOSED (``effective_to``), never deleted.

One current row per (entity, relationship type) — a partial unique index.

ADDITIVE, REVERSIBLE
====================
A new table; nothing reads it until the code ships. Downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "048"
down_revision: str | None = "047"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "entity_relationship_exceptions"


def upgrade() -> None:
    op.create_table(
        TABLE,
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column(
            "legal_entity_id",
            sa.Uuid(),
            sa.ForeignKey(
                "legal_entities.id",
                ondelete="CASCADE",
                name="fk_entity_relationship_exceptions_legal_entities",
            ),
            nullable=False,
        ),
        sa.Column("relationship_type", sa.String(40), nullable=False),
        sa.Column("category", sa.String(80), nullable=True),
        sa.Column("reason", sa.String(60), nullable=False),
        sa.Column("source", sa.String(100), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("as_of", sa.Date(), nullable=True),
        sa.Column("effective_from", sa.Date(), nullable=True),
        sa.Column("effective_to", sa.Date(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_entity_relationship_exceptions_current",
        TABLE,
        ["legal_entity_id", "relationship_type"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_entity_relationship_exceptions_current", table_name=TABLE)
    op.drop_table(TABLE)
