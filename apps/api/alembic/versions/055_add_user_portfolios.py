"""Private, hand-entered portfolios — V3.20 slice 12.1 (ADR-100).

TWO TABLES, PRIVATE
===================
``user_portfolios`` (owner_key = SHA-256 of the signed-in admin's e-mail, never the
address) and ``user_portfolio_positions`` (issuer, entry date, optional entry price with
its currency, optional weight). Entered by hand; no broker link. No public table
references them.

ADDITIVE, REVERSIBLE: two new tables; downgrade drops them (positions first).
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "055"
down_revision: str | None = "054"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "user_portfolios",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("owner_key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("owner_key", "name", name="uq_user_portfolios_owner_name"),
    )
    op.create_index("ix_user_portfolios_owner_key", "user_portfolios", ["owner_key"])
    op.create_table(
        "user_portfolio_positions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("portfolio_id", sa.Uuid(),
                  sa.ForeignKey("user_portfolios.id", ondelete="CASCADE",
                                name="fk_user_portfolio_positions_portfolio_id"),
                  nullable=False),
        sa.Column("company_id", sa.Uuid(),
                  sa.ForeignKey("companies.id", ondelete="SET NULL",
                                name="fk_user_portfolio_positions_company_id"),
                  nullable=True),
        sa.Column("ticker", sa.String(20), nullable=False),
        sa.Column("exchange", sa.String(20), nullable=True),
        sa.Column("entry_date", sa.Date(), nullable=False),
        sa.Column("entry_price", sa.Numeric(20, 6), nullable=True),
        sa.Column("entry_currency", sa.String(3), nullable=True),
        sa.Column("weight_pct", sa.Numeric(6, 3), nullable=True),
        sa.Column("note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("portfolio_id", "ticker", "exchange", "entry_date",
                            name="uq_user_portfolio_positions_entry"),
        sa.CheckConstraint("entry_price IS NULL OR entry_price > 0",
                           name="ck_user_portfolio_positions_price_positive"),
        sa.CheckConstraint("weight_pct IS NULL OR (weight_pct > 0 AND weight_pct <= 100)",
                           name="ck_user_portfolio_positions_weight_range"),
        sa.CheckConstraint("(entry_price IS NULL) = (entry_currency IS NULL)",
                           name="ck_user_portfolio_positions_price_has_currency"),
    )
    op.create_index("ix_user_portfolio_positions_portfolio_id", "user_portfolio_positions",
                    ["portfolio_id"])


def downgrade() -> None:
    op.drop_index("ix_user_portfolio_positions_portfolio_id",
                  table_name="user_portfolio_positions")
    op.drop_table("user_portfolio_positions")
    op.drop_index("ix_user_portfolios_owner_key", table_name="user_portfolios")
    op.drop_table("user_portfolios")
