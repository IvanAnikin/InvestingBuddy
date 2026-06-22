"""add citation provenance fields

Revision ID: 003
Revises: 002
Create Date: 2026-06-22

Adds field_path, source_tier, data_quality to citations table.
These columns support Phase 6 provider-sourced citations:
  field_path   — the report schema field this citation covers (e.g. "identity.legal_name")
  source_tier  — T1–T6 from source taxonomy (matches SourceTier enum values)
  data_quality — A_verified / B_single_credible / C_inferred / D_weak_or_stale

All columns are nullable for backward compatibility with Phase 2/3 placeholder citations.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "003"
down_revision: str | None = "002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("citations", sa.Column("field_path", sa.String(200), nullable=True))
    op.add_column("citations", sa.Column("source_tier", sa.String(50), nullable=True))
    op.add_column("citations", sa.Column("data_quality", sa.String(50), nullable=True))
    op.create_index("ix_citations_field_path", "citations", ["field_path"])
    op.create_index("ix_citations_source_tier", "citations", ["source_tier"])


def downgrade() -> None:
    op.drop_index("ix_citations_source_tier", table_name="citations")
    op.drop_index("ix_citations_field_path", table_name="citations")
    op.drop_column("citations", "data_quality")
    op.drop_column("citations", "source_tier")
    op.drop_column("citations", "field_path")
