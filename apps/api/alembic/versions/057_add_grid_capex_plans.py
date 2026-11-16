"""Grid capex plans — V3.20 slice 7.1 (spec §3.7).

TWO TABLES
==========
``grid_plan_ingests`` — one row per ingest attempt (file SHA-256, counts, failure
reason; a failed ingest is kept). ``grid_capex_plans`` — one row per project or
investment in a published grid plan, with promoter(s), plan edition, technology tags,
capex (amount + currency, or neither), commissioning, status class and the source (URL,
retrieval time, use constraint, attribution — all NOT NULL).

ADDITIVE, REVERSIBLE: two new tables; downgrade drops them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "057"
down_revision: str | None = "056"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "grid_plan_ingests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_key", sa.String(40), nullable=False),
        sa.Column("plan_edition", sa.String(80), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("file_sha256", sa.String(64), nullable=True),
        sa.Column("rows_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("rows_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail_json", JSONB(), nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.CheckConstraint("status IN ('running', 'complete', 'failed')",
                           name="ck_grid_plan_ingests_status"),
    )
    op.create_table(
        "grid_capex_plans",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("ingest_id", sa.Uuid(),
                  sa.ForeignKey("grid_plan_ingests.id", ondelete="SET NULL",
                                name="fk_grid_capex_plans_ingest_id"), nullable=True),
        sa.Column("source_key", sa.String(40), nullable=False),
        sa.Column("plan_name", sa.String(200), nullable=False),
        sa.Column("plan_edition", sa.String(80), nullable=False),
        sa.Column("record_kind", sa.String(30), nullable=False),
        sa.Column("external_id", sa.String(80), nullable=False),
        sa.Column("parent_external_id", sa.String(80), nullable=True),
        sa.Column("project_name", sa.String(500), nullable=False),
        sa.Column("promoters_text", sa.String(1000), nullable=True),
        sa.Column("promoters_json", JSONB(), nullable=True),
        sa.Column("region", sa.String(200), nullable=True),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("technology_tags_json", JSONB(), nullable=True),
        sa.Column("voltage_kv", sa.Integer(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("capex_amount", sa.Numeric(24, 2), nullable=True),
        sa.Column("capex_currency", sa.String(3), nullable=True),
        sa.Column("capex_period", sa.String(60), nullable=True),
        sa.Column("capacity_text", sa.String(500), nullable=True),
        sa.Column("capacity_mw", sa.Numeric(14, 2), nullable=True),
        sa.Column("commissioning_text", sa.String(120), nullable=True),
        sa.Column("commissioning_year", sa.Integer(), nullable=True),
        sa.Column("status_text", sa.String(200), nullable=True),
        sa.Column("status_class", sa.String(30), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("source_title", sa.String(500), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("use_constraint", sa.String(40), nullable=False),
        sa.Column("attribution", sa.String(500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
        sa.UniqueConstraint("source_key", "plan_edition", "record_kind", "external_id",
                            name="uq_grid_capex_plans_record"),
        sa.CheckConstraint(
            "(capex_amount IS NULL AND capex_currency IS NULL) OR "
            "(capex_amount > 0 AND capex_currency IS NOT NULL)",
            name="ck_grid_capex_plans_capex_pair"),
        sa.CheckConstraint(
            "status_class IN ('under_consideration', 'planned', 'permitting', 'committed', "
            "'under_construction', 'commissioned', 'withdrawn', 'other')",
            name="ck_grid_capex_plans_status_class"),
    )
    op.create_index("ix_grid_capex_plans_source_edition", "grid_capex_plans",
                    ["source_key", "plan_edition"])


def downgrade() -> None:
    op.drop_index("ix_grid_capex_plans_source_edition", table_name="grid_capex_plans")
    op.drop_table("grid_capex_plans")
    op.drop_table("grid_plan_ingests")
