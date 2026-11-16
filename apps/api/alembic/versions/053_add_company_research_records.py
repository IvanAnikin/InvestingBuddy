"""Comparable-set store — V3.20 slice 9.1 (spec §3.10).

ONE TABLE
=========
``company_research_records`` — one structured record per completed company research run,
read from that run's own report blocks: layer, size, enterprise value and EV/EBIT,
obscurity score, and the verbatim blocks (``record_json``). ``rubric_json`` stays NULL
until slice 9.2. Rows are never updated after the run and never deleted; peers by layer
(slice 8.2) and monitoring (9.4) read them.

ADDITIVE, REVERSIBLE: one new table; downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "053"
down_revision: str | None = "052"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "company_research_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(),
                  sa.ForeignKey("companies.id", ondelete="CASCADE",
                                name="fk_company_research_records_company_id_companies"),
                  nullable=False),
        sa.Column("research_run_id", sa.Uuid(),
                  sa.ForeignKey(
                      "research_runs.id", ondelete="SET NULL",
                      name="fk_company_research_records_research_run_id_research_runs"),
                  nullable=True),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("record_version", sa.String(40), nullable=False),
        sa.Column("ticker", sa.String(20), nullable=True),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column("sector", sa.String(100), nullable=True),
        sa.Column("industry", sa.String(100), nullable=True),
        sa.Column("layer", sa.String(60), nullable=True),
        sa.Column("market_cap_usd", sa.Numeric(24, 2), nullable=True),
        sa.Column("market_cap_as_of", sa.Date(), nullable=True),
        sa.Column("enterprise_value", sa.Numeric(24, 2), nullable=True),
        sa.Column("ev_currency", sa.String(3), nullable=True),
        sa.Column("ev_to_ebit", sa.Numeric(12, 2), nullable=True),
        sa.Column("obscurity_score", sa.Integer(), nullable=True),
        sa.Column("rubric_json", JSONB(), nullable=True),
        sa.Column("record_json", JSONB(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("research_run_id",
                            name="uq_company_research_records_research_run_id"),
    )
    op.create_index("ix_company_research_records_company_as_of",
                    "company_research_records", ["company_id", "as_of"])
    op.create_index("ix_company_research_records_layer", "company_research_records",
                    ["layer"])


def downgrade() -> None:
    op.drop_index("ix_company_research_records_layer", table_name="company_research_records")
    op.drop_index("ix_company_research_records_company_as_of",
                  table_name="company_research_records")
    op.drop_table("company_research_records")
