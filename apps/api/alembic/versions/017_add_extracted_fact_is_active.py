"""add extracted_facts.is_active

Revision ID: 017
Revises: 016
Create Date: 2026-08-13

Phase 32A corrective (cache/derivation correctness) — active-vs-historical
``ExtractedFact`` semantics.

The existing schema has no way to represent "this fact was superseded by a
later, more-complete derivation but is kept for audit/history" — every row
was implicitly "current". That made it impossible to safely revalidate a
document whose cached ``excerpts_json`` cannot reconstruct a table-derived
fact: a partial (prose-only) re-derivation had no honest way to replace the
old active set without either deleting history or leaving old and new facts
mixed together as if all were equally current.

Adds a single NOT NULL ``is_active`` BOOLEAN column, defaulted (both at the
Python and server level) to ``true`` so every existing row stays exactly as
it already behaves today (implicitly "current"). A later revalidation that
completely re-derives a document's fact set flips the prior active rows to
``is_active = false`` (never deleted — audit history is preserved) and
inserts the freshly-derived set as the new active rows. Every query that
feeds a CURRENT report must filter ``is_active = true``. Purely additive,
backfills to ``true`` for all existing rows, reversible.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "017"
down_revision: str | None = "016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extracted_facts",
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
    )
    op.create_index(
        "ix_extracted_facts_document_active",
        "extracted_facts",
        ["extracted_document_id", "is_active"],
    )


def downgrade() -> None:
    op.drop_index("ix_extracted_facts_document_active", table_name="extracted_facts")
    op.drop_column("extracted_facts", "is_active")
