"""Precedent transactions — V3.20 slice 8.3.

ONE TABLE, CITED, HUMAN-APPROVED
================================
``precedent_transactions``: acquirer, target, layer, announcement date, currency, EV
(or equity consideration, labelled by ``value_basis``), the target metric the multiple is
taken on, and the source (URL, title, excerpt, retrieval time — URL, date, currency and
retrieval time are NOT NULL). Rows start ``proposed``; a CHECK makes an ``approved`` row
carry EV, metric and layer. Only approved rows reach a report.

ADDITIVE, REVERSIBLE: one new table; downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "056"
down_revision: str | None = "055"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "precedent_transactions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("acquirer_name", sa.String(200), nullable=False),
        sa.Column("target_name", sa.String(200), nullable=False),
        sa.Column("target_company_id", sa.Uuid(),
                  sa.ForeignKey("companies.id", ondelete="SET NULL",
                                name="fk_precedent_transactions_target_company_id"),
                  nullable=True),
        sa.Column("layer", sa.String(60), nullable=True),
        sa.Column("announced_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("enterprise_value", sa.Numeric(24, 2), nullable=True),
        sa.Column("value_basis", sa.String(30), nullable=False),
        sa.Column("metric_key", sa.String(20), nullable=True),
        sa.Column("metric_value", sa.Numeric(24, 2), nullable=True),
        sa.Column("metric_period", sa.String(40), nullable=True),
        sa.Column("source_url", sa.String(2000), nullable=False),
        sa.Column("source_title", sa.String(500), nullable=True),
        sa.Column("source_excerpt", sa.String(1000), nullable=True),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("entry_method", sa.String(20), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("reviewed_by", sa.String(320), nullable=True),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("review_note", sa.String(1000), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("acquirer_name", "target_name", "announced_date",
                            name="uq_precedent_transactions_deal"),
        sa.CheckConstraint("status IN ('proposed', 'approved', 'rejected')",
                           name="ck_precedent_transactions_status"),
        sa.CheckConstraint("entry_method IN ('extracted', 'curated')",
                           name="ck_precedent_transactions_entry_method"),
        sa.CheckConstraint("enterprise_value IS NULL OR enterprise_value > 0",
                           name="ck_precedent_transactions_ev_positive"),
        sa.CheckConstraint(
            "metric_key IS NULL OR metric_key IN ('ebit', 'ebitda', 'revenue', 'capacity_mw')",
            name="ck_precedent_transactions_metric_key"),
        sa.CheckConstraint(
            "status <> 'approved' OR (enterprise_value IS NOT NULL AND metric_key IS NOT NULL "
            "AND metric_value IS NOT NULL AND metric_value > 0 AND layer IS NOT NULL)",
            name="ck_precedent_transactions_approved_complete"),
    )
    op.create_index("ix_precedent_transactions_layer_status", "precedent_transactions",
                    ["layer", "status"])


def downgrade() -> None:
    op.drop_index("ix_precedent_transactions_layer_status",
                  table_name="precedent_transactions")
    op.drop_table("precedent_transactions")
