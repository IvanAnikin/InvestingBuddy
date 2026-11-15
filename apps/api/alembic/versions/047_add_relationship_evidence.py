"""Relationship evidence on entity_relationships — V3.20 slice 2.1 (spec §3.2).

WHAT THIS CARRIES
=================
The relationship graph (parent, ultimate parent, significant holder, supplier/customer)
needs three things migration 028 could not hold:

* **An unresolved end.** "Customer A accounted for 14% of revenue" is a concentration
  fact even though the customer is anonymous, and a GLEIF parent may have no row in the
  entity master yet. ``subject_entity_id`` and ``object_entity_id`` become nullable;
  exactly ONE end may be unresolved, and then ``counterparty_name_raw`` /
  ``counterparty_name_key`` name it.
* **Evidence.** ``evidence_ref`` (ledger finding / extracted fact / filing accession),
  ``source_kind``, ``extraction_method``, ``evidence_excerpt`` (verbatim), ``as_of``.
  The writer refuses an edge with neither ``source_url`` nor ``evidence_ref``.
* **A disclosed share.** ``disclosed_share_pct`` (0–100) with ``share_basis``
  (revenue | receivables | purchases | voting_rights | capital), per ``period``.

``period`` is NOT NULL with default ``''`` so it can sit in the unique indexes: a FY2024
and a FY2025 concentration figure for the same customer are two rows, not one.

INDEXES
=======
* ``ix_entity_relationships_current`` is rebuilt to include ``period``.
* Two partial unique indexes cover an unresolved object / an unresolved subject, keyed
  by the counterparty's name key and the period.

CHECKS
======
* the old "no self reference" check is replaced by one that tolerates a NULL end;
* at least one end is resolved;
* an unresolved end has a name key;
* ``disclosed_share_pct`` is within 0–100.

ADDITIVE, REVERSIBLE
====================
No backfill: every existing row has both ends resolved and ``period = ''``. Downgrade
deletes any row with an unresolved end (it cannot exist under the 028 schema) and
restores 028 exactly. The ORM maps the new columns, so apply 047 BEFORE the code.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "047"
down_revision: str | None = "046"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "entity_relationships"

NEW_COLUMNS: tuple[sa.Column, ...] = (
    sa.Column("evidence_ref", sa.String(200), nullable=True),
    sa.Column("source_kind", sa.String(40), nullable=True),
    sa.Column("extraction_method", sa.String(30), nullable=True),
    sa.Column("evidence_excerpt", sa.String(1000), nullable=True),
    sa.Column("as_of", sa.Date(), nullable=True),
    sa.Column("period", sa.String(40), nullable=False, server_default=sa.text("''")),
    sa.Column("disclosed_share_pct", sa.Float(), nullable=True),
    sa.Column("share_basis", sa.String(30), nullable=True),
    sa.Column("counterparty_name_raw", sa.String(300), nullable=True),
    sa.Column("counterparty_name_key", sa.String(300), nullable=True),
)

OLD_SELF_CHECK = "ck_entity_relationships_no_self_reference"
NEW_CHECKS: dict[str, str] = {
    "ck_entity_relationships_no_self_reference_v2": (
        "subject_entity_id IS NULL OR object_entity_id IS NULL "
        "OR subject_entity_id <> object_entity_id"
    ),
    "ck_entity_relationships_one_end_resolved": (
        "subject_entity_id IS NOT NULL OR object_entity_id IS NOT NULL"
    ),
    "ck_entity_relationships_unresolved_end_named": (
        "(subject_entity_id IS NOT NULL AND object_entity_id IS NOT NULL) "
        "OR counterparty_name_key IS NOT NULL"
    ),
    "ck_entity_relationships_share_range": (
        "disclosed_share_pct IS NULL OR "
        "(disclosed_share_pct >= 0.0 AND disclosed_share_pct <= 100.0)"
    ),
}


def upgrade() -> None:
    for column in NEW_COLUMNS:
        op.add_column(TABLE, column)
    op.alter_column(TABLE, "subject_entity_id", existing_type=sa.Uuid(), nullable=True)
    op.alter_column(TABLE, "object_entity_id", existing_type=sa.Uuid(), nullable=True)

    op.drop_constraint(OLD_SELF_CHECK, TABLE, type_="check")
    for name, sql in NEW_CHECKS.items():
        op.create_check_constraint(name, TABLE, sql)

    op.drop_index("ix_entity_relationships_current", table_name=TABLE)
    op.create_index(
        "ix_entity_relationships_current",
        TABLE,
        ["subject_entity_id", "object_entity_id", "relationship_type", "period"],
        unique=True,
        postgresql_where=sa.text(
            "effective_to IS NULL AND subject_entity_id IS NOT NULL "
            "AND object_entity_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "effective_to IS NULL AND subject_entity_id IS NOT NULL "
            "AND object_entity_id IS NOT NULL"
        ),
    )
    op.create_index(
        "ix_entity_relationships_current_unresolved_object",
        TABLE,
        ["subject_entity_id", "relationship_type", "counterparty_name_key", "period"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL AND object_entity_id IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL AND object_entity_id IS NULL"),
    )
    op.create_index(
        "ix_entity_relationships_current_unresolved_subject",
        TABLE,
        ["object_entity_id", "relationship_type", "counterparty_name_key", "period"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL AND subject_entity_id IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL AND subject_entity_id IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_entity_relationships_current_unresolved_subject", table_name=TABLE)
    op.drop_index("ix_entity_relationships_current_unresolved_object", table_name=TABLE)
    op.drop_index("ix_entity_relationships_current", table_name=TABLE)
    # Rows with an unresolved end cannot exist under 028; a period-split pair would
    # violate the 028 unique index, so only the earliest live row per triple is kept.
    op.execute(
        "DELETE FROM entity_relationships "
        "WHERE subject_entity_id IS NULL OR object_entity_id IS NULL"
    )
    op.execute(
        "DELETE FROM entity_relationships r USING entity_relationships k "
        "WHERE r.effective_to IS NULL AND k.effective_to IS NULL "
        "AND r.subject_entity_id = k.subject_entity_id "
        "AND r.object_entity_id = k.object_entity_id "
        "AND r.relationship_type = k.relationship_type "
        "AND r.created_at > k.created_at"
    )
    op.create_index(
        "ix_entity_relationships_current",
        TABLE,
        ["subject_entity_id", "object_entity_id", "relationship_type"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
    )
    for name in NEW_CHECKS:
        op.drop_constraint(name, TABLE, type_="check")
    op.create_check_constraint(
        OLD_SELF_CHECK, TABLE, "subject_entity_id <> object_entity_id"
    )
    op.alter_column(TABLE, "object_entity_id", existing_type=sa.Uuid(), nullable=False)
    op.alter_column(TABLE, "subject_entity_id", existing_type=sa.Uuid(), nullable=False)
    for column in reversed(NEW_COLUMNS):
        op.drop_column(TABLE, column.name)
