"""add company_id FK to reports table

Revision ID: 012
Revises: 011
Create Date: 2026-08-02

Phase 32A hotfix — company-scoped ``from-company`` final-report selection.

Adds a nullable ``company_id`` column to the existing ``reports`` table with an
FK to ``companies.id`` (``ondelete=SET NULL``) plus a supporting index. This
gives a report a first-class link to the company it is about, so the
``/api/v1/final-reports/from-company/{company_id}`` route can select the most
recent completed analysis report FOR THAT COMPANY instead of the globally
newest completed report (which could belong to a different company).

No investment recommendations, price targets, fair values, or upside
percentages are stored. Human review is still required before any action.

Reversible. No data backfill — existing rows keep ``company_id`` NULL until a
new analysis run links them (SET NULL preserves research history on company
deletion).
"""

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "reports",
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "companies.id",
                ondelete="SET NULL",
                name="fk_reports_company_id_companies",
            ),
            nullable=True,
        ),
    )
    op.create_index(
        "ix_reports_company_id",
        "reports",
        ["company_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_reports_company_id", table_name="reports")
    op.drop_column("reports", "company_id")
