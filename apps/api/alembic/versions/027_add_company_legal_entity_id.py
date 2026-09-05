"""Link companies to the entity master — V3.2 Slice 2.2.

PURPOSE
=======
One nullable FK, ``companies.legal_entity_id``, so that the entity master built in
026 is reachable from the 1,057 existing reports rather than being a schema nothing
points at.

ADDITIVE ONLY. One **nullable** column and one index; no existing column is
touched, no constraint is tightened, no FK moves. ``release/v2-current`` code runs
unchanged against a database with this applied: SQLAlchemy emits explicit column
lists, so a V2 ``SELECT`` never sees the column and a V2 ``INSERT`` leaves it NULL.
That is the property §2.1 of the migration plan exists to preserve, and it is what
makes rollback real rather than theoretical.

WHY ``SET NULL`` AND NOT ``CASCADE``
====================================
Every FK *inside* the entity master (026) is ``CASCADE``, because a security
without its issuer is uninterpretable — composition. This one points the other way
and is **lineage**: deleting a legal entity must never delete the ``companies`` row
that a thousand reports, citations and extracted documents refer to (CLAUDE.md
rule 15). A company that loses its entity link degrades to exactly the state every
un-backfilled row is already in, which the compatibility adapter handles.

WHY THE COLUMN IS NULLABLE AND STAYS THAT WAY
=============================================
Three-step pattern from §2.2 of the migration plan: add nullable, backfill
separately, enforce only after the backfill is verified. This migration is step
one. It does **not** run the backfill — a migration that backfills is a migration
that can take longer than the lock timeout allows, and this repository has the
`backfill_from_extracted_documents` precedent for keeping the two apart.

NULL is also a real, permanent answer here rather than only a transitional one.
When two ``companies`` rows derive the same entity key with different names, the
backfill deliberately leaves the second unlinked instead of attributing it to the
first row's entity. ``legal_entity_id IS NULL`` therefore means "no entity has been
established for this company", and the compatibility adapter falls back to the
``companies`` row and says that it did.

INDEX
=====
``ix_companies_legal_entity_id`` — "which companies map to this entity?" is the
reverse lookup the backfill's de-duplication and slice 2.3's resolver both need,
and without it that is a sequential scan of the universe.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "027"
down_revision: str | None = "026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "companies",
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_companies_legal_entity_id_legal_entities",
        "companies",
        "legal_entities",
        ["legal_entity_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_companies_legal_entity_id", "companies", ["legal_entity_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_companies_legal_entity_id", table_name="companies")
    op.drop_constraint(
        "fk_companies_legal_entity_id_legal_entities", "companies", type_="foreignkey"
    )
    op.drop_column("companies", "legal_entity_id")
