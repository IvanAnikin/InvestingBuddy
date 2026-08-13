"""add extracted_documents.pipeline_version

Revision ID: 016
Revises: 015
Create Date: 2026-08-13

Phase 32A corrective — Problem B (derived-fact cache versioning).

Adds a single nullable ``pipeline_version`` INTEGER column to
``extracted_documents``. Existing rows are left NULL (treated as legacy /
stale — never assumed compatible with the current parser/validator; see
``app.services.sources.extraction_pipeline_version``). New rows stamp the
currently-deployed pipeline version at write time. Purely additive, no
backfill, no data loss, reversible.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "016"
down_revision: str | None = "015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "extracted_documents",
        sa.Column("pipeline_version", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("extracted_documents", "pipeline_version")
