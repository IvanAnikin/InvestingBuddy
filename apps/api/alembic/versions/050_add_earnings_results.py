"""Earnings results — V3.20 slice 4.1 (spec §3.4).

ONE TABLE, ``earnings_results``: one row per company × fiscal quarter (``2026-Q3``, the
filer's own fiscal calendar, ADR-091) or full fiscal year (``2026``, ``fiscal_quarter``
0 — what annual guidance is compared with in slice 4.3).

* Actuals (4.1): revenue and diluted EPS **as first reported** in the quarter's own
  filing — what a consensus of the time was compared with — beside the **latest**
  restated value (a split or restatement makes them differ). The basis says whether
  Q4 revenue was reported or derived (full year minus nine-month year-to-date); Q4 EPS
  is never derived. Every figure keeps its concept, accession and source URL.
* Report date: the 8-K Item 2.02 release when one exists, else the periodic report's
  filing date — and which one it was.
* Consensus (4.2), guidance (4.3) and surprise (4.4) columns are created now and left
  NULL: NULL is "not looked up", never zero. ``consensus_status`` stays NULL until a
  provider answered (``available`` / ``thin`` / ``none`` / ``source_unavailable``).

ADDITIVE, REVERSIBLE: one new table; downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "050"
down_revision: str | None = "049"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    money = sa.Numeric(24, 2)
    per_share = sa.Numeric(18, 6)
    op.create_table(
        "earnings_results",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("company_id", sa.Uuid(),
                  sa.ForeignKey("companies.id", ondelete="CASCADE",
                                name="fk_earnings_results_company_id_companies"),
                  nullable=False),
        sa.Column("fiscal_period_label", sa.String(10), nullable=False),
        sa.Column("fiscal_year", sa.Integer(), nullable=False),
        sa.Column("fiscal_quarter", sa.SmallInteger(), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("report_date", sa.Date(), nullable=True),
        sa.Column("report_date_source", sa.String(40), nullable=True),
        sa.Column("report_date_accession", sa.String(25), nullable=True),
        # Actuals (4.1)
        sa.Column("actuals_source", sa.String(30), nullable=True),
        sa.Column("actual_revenue", money, nullable=True),
        sa.Column("actual_revenue_latest", money, nullable=True),
        sa.Column("revenue_currency", sa.String(3), nullable=True),
        sa.Column("revenue_basis", sa.String(30), nullable=True),
        sa.Column("revenue_concept", sa.String(120), nullable=True),
        sa.Column("revenue_accession", sa.String(25), nullable=True),
        sa.Column("revenue_source_url", sa.String(2000), nullable=True),
        sa.Column("actual_eps", per_share, nullable=True),
        sa.Column("actual_eps_latest", per_share, nullable=True),
        sa.Column("eps_currency", sa.String(3), nullable=True),
        sa.Column("eps_concept", sa.String(120), nullable=True),
        sa.Column("eps_accession", sa.String(25), nullable=True),
        sa.Column("eps_source_url", sa.String(2000), nullable=True),
        sa.Column("actuals_as_of", sa.DateTime(timezone=True), nullable=True),
        # Consensus (4.2) — third-party expectation data (ADR-097)
        sa.Column("consensus_eps_avg", per_share, nullable=True),
        sa.Column("consensus_eps_high", per_share, nullable=True),
        sa.Column("consensus_eps_low", per_share, nullable=True),
        sa.Column("consensus_revenue_avg", money, nullable=True),
        sa.Column("consensus_revenue_high", money, nullable=True),
        sa.Column("consensus_revenue_low", money, nullable=True),
        sa.Column("analyst_count_eps", sa.Integer(), nullable=True),
        sa.Column("analyst_count_revenue", sa.Integer(), nullable=True),
        sa.Column("consensus_source", sa.String(40), nullable=True),
        sa.Column("consensus_as_of", sa.Date(), nullable=True),
        sa.Column("consensus_status", sa.String(20), nullable=True),
        # Surprise (4.4)
        sa.Column("surprise_eps_abs", per_share, nullable=True),
        sa.Column("surprise_eps_pct", sa.Numeric(12, 4), nullable=True),
        sa.Column("surprise_revenue_abs", money, nullable=True),
        sa.Column("surprise_revenue_pct", sa.Numeric(12, 4), nullable=True),
        # Guidance (4.3)
        sa.Column("guidance_metric", sa.String(30), nullable=True),
        sa.Column("guidance_low", money, nullable=True),
        sa.Column("guidance_high", money, nullable=True),
        sa.Column("guidance_mid", money, nullable=True),
        sa.Column("guidance_evidence_ref", sa.String(80), nullable=True),
        sa.Column("vs_guidance", sa.String(20), nullable=True),
        sa.Column("detail_json", JSONB, nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("company_id", "fiscal_period_label",
                            name="uq_earnings_results_company_period"),
        sa.CheckConstraint("fiscal_quarter BETWEEN 0 AND 4",
                           name="ck_earnings_results_fiscal_quarter"),
    )
    op.create_index("ix_earnings_results_report_date", "earnings_results", ["report_date"])


def downgrade() -> None:
    op.drop_index("ix_earnings_results_report_date", table_name="earnings_results")
    op.drop_table("earnings_results")
