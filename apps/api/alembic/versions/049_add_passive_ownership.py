"""Passive / ETF ownership — V3.20 slice 3.1 (spec §3.3).

THREE TABLES
============
* ``funds`` — the fund registry, one row per EDGAR series (``S000004310``): name,
  registrant, and whether it is an ETF / an index fund. ``is_etf`` is THREE-valued:
  the N-CEN data sets omit filings in schema 3.1 (EDGAR 25.2), so "not known" is a real
  answer and is never shown as "not an ETF".
* ``security_passive_ownership`` — per security identifier, per data-set period, per
  source: how many funds and ETFs held it, how many shares (``UNIT = 'NS'`` only; other
  units are counted, never added), their value, the report-date range of the holdings
  and the top 20 funds. Raw holdings are NOT kept (spec §3.3: volume).
* ``fund_holdings_ingests`` — one row per (source kind, period): what an ingest wrote or
  why it failed. It is what makes an ingest idempotent per quarter.

ADDITIVE, REVERSIBLE
====================
Three new tables; nothing reads them until the code ships. Downgrade drops them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "049"
down_revision: str | None = "048"
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
    op.create_table(
        "funds",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("series_id", sa.String(12), nullable=False),
        sa.Column("series_lei", sa.String(20), nullable=True),
        sa.Column("name", sa.String(300), nullable=True),
        sa.Column("registrant_cik", sa.String(10), nullable=True),
        sa.Column("registrant_name", sa.String(300), nullable=True),
        sa.Column("is_etf", sa.Boolean(), nullable=True),
        sa.Column("is_index", sa.Boolean(), nullable=True),
        sa.Column("fund_type_source", sa.String(40), nullable=True),
        sa.Column("fund_type_as_of", sa.Date(), nullable=True),
        sa.Column("net_assets", sa.Numeric(24, 2), nullable=True),
        sa.Column("net_assets_as_of", sa.Date(), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("series_id", name="uq_funds_series_id"),
    )
    op.create_table(
        "security_passive_ownership",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("identifier_kind", sa.String(10), nullable=False),
        sa.Column("identifier", sa.String(20), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("issuer_name", sa.String(300), nullable=True),
        sa.Column("issuer_lei", sa.String(20), nullable=True),
        sa.Column("cusip", sa.String(9), nullable=True),
        sa.Column("isin", sa.String(12), nullable=True),
        sa.Column("fund_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("etf_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("etf_unknown_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("index_fund_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("shares_held", sa.Numeric(28, 4), nullable=True),
        sa.Column("etf_shares_held", sa.Numeric(28, 4), nullable=True),
        sa.Column("value_usd", sa.Numeric(24, 2), nullable=True),
        sa.Column("non_share_holdings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("report_date_min", sa.Date(), nullable=True),
        sa.Column("report_date_max", sa.Date(), nullable=True),
        sa.Column("top_funds_json", JSONB, nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("identifier_kind", "identifier", "period", "source_kind",
                            name="uq_security_passive_ownership_key"),
    )
    op.create_index("ix_security_passive_ownership_isin", "security_passive_ownership",
                    ["isin"])
    op.create_index("ix_security_passive_ownership_cusip", "security_passive_ownership",
                    ["cusip"])
    op.create_index("ix_security_passive_ownership_issuer_lei",
                    "security_passive_ownership", ["issuer_lei"])
    op.create_table(
        "fund_holdings_ingests",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("source_kind", sa.String(30), nullable=False),
        sa.Column("period", sa.String(10), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("source_url", sa.String(2000), nullable=True),
        sa.Column("funds_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("securities_written", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("holdings_read", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("detail_json", JSONB, nullable=True),
        sa.Column("error", sa.String(500), nullable=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        *_stamps(),
        sa.UniqueConstraint("source_kind", "period", name="uq_fund_holdings_ingests_period"),
    )


def downgrade() -> None:
    op.drop_table("fund_holdings_ingests")
    op.drop_index("ix_security_passive_ownership_issuer_lei",
                  table_name="security_passive_ownership")
    op.drop_index("ix_security_passive_ownership_cusip", table_name="security_passive_ownership")
    op.drop_index("ix_security_passive_ownership_isin", table_name="security_passive_ownership")
    op.drop_table("security_passive_ownership")
    op.drop_table("funds")
