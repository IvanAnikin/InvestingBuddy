"""Consensus snapshots + vendor call budget — V3.20 slice 4.2 (spec §3.4, ADR-097).

TWO TABLES
==========
* ``consensus_snapshots`` — what a third-party provider said the expectation for one
  company's fiscal period was, ON ONE DATE (``as_of``): EPS and revenue average / high /
  low / analyst count, and the EPS revision history the provider gives (7/30/60/90 days
  ago, revisions up/down). ``is_pre_report`` says whether the snapshot was taken before
  that period's results were announced; ADR-097 §2: only a pre-report snapshot is an
  expectation, a later one is kept but never used for a surprise. Also one row per
  reported quarter for the provider's own historical record (``horizon`` =
  ``reported_quarter``): its reported EPS, its estimate, its surprise — on the
  PROVIDER'S EPS basis (usually adjusted), which is why it is stored apart from the
  filed diluted EPS and never subtracted from it.
* ``vendor_api_calls`` — calls per vendor per UTC day: the provider's free-tier budget
  (Alpha Vantage: 25/day) enforced across processes.

ADDITIVE, REVERSIBLE: two new tables; downgrade drops them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "051"
down_revision: str | None = "050"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _stamps() -> list[sa.Column]:
    return [
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
    ]


def upgrade() -> None:
    money = sa.Numeric(24, 2)
    per_share = sa.Numeric(18, 6)
    op.create_table(
        "consensus_snapshots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(),
                  sa.ForeignKey("companies.id", ondelete="CASCADE",
                                name="fk_consensus_snapshots_company_id_companies"),
                  nullable=False),
        sa.Column("source", sa.String(40), nullable=False),
        sa.Column("symbol", sa.String(20), nullable=False),
        #: ``fiscal_quarter`` | ``fiscal_year`` | ``reported_quarter``
        sa.Column("horizon", sa.String(20), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("is_pre_report", sa.Boolean(), nullable=True),
        sa.Column("eps_avg", per_share, nullable=True),
        sa.Column("eps_high", per_share, nullable=True),
        sa.Column("eps_low", per_share, nullable=True),
        sa.Column("eps_analyst_count", sa.Integer(), nullable=True),
        sa.Column("revenue_avg", money, nullable=True),
        sa.Column("revenue_high", money, nullable=True),
        sa.Column("revenue_low", money, nullable=True),
        sa.Column("revenue_analyst_count", sa.Integer(), nullable=True),
        sa.Column("eps_avg_7d_ago", per_share, nullable=True),
        sa.Column("eps_avg_30d_ago", per_share, nullable=True),
        sa.Column("eps_avg_60d_ago", per_share, nullable=True),
        sa.Column("eps_avg_90d_ago", per_share, nullable=True),
        sa.Column("eps_revisions_up_30d", sa.Integer(), nullable=True),
        sa.Column("eps_revisions_down_30d", sa.Integer(), nullable=True),
        # reported_quarter rows: the provider's own record of the print.
        sa.Column("vendor_reported_date", sa.Date(), nullable=True),
        sa.Column("vendor_reported_eps", per_share, nullable=True),
        sa.Column("vendor_surprise", per_share, nullable=True),
        sa.Column("vendor_surprise_pct", sa.Numeric(12, 4), nullable=True),
        sa.Column("vendor_report_time", sa.String(20), nullable=True),
        sa.Column("source_url", sa.String(500), nullable=True),
        sa.Column("detail_json", JSONB, nullable=True),
        *_stamps(),
        sa.UniqueConstraint("company_id", "source", "horizon", "period_end", "as_of",
                            name="uq_consensus_snapshots_key"),
    )
    op.create_index("ix_consensus_snapshots_company_period", "consensus_snapshots",
                    ["company_id", "period_end"])
    op.create_table(
        "vendor_api_calls",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("vendor", sa.String(40), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("calls", sa.Integer(), nullable=False, server_default="0"),
        *_stamps(),
        sa.UniqueConstraint("vendor", "day", name="uq_vendor_api_calls_vendor_day"),
    )


def downgrade() -> None:
    op.drop_table("vendor_api_calls")
    op.drop_index("ix_consensus_snapshots_company_period", table_name="consensus_snapshots")
    op.drop_table("consensus_snapshots")
