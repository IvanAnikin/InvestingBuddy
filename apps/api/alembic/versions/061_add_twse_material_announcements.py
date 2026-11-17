"""TWSE material announcements, held — V3.20 slice 11.4.

ONE TABLE
=========
``twse_material_announcements`` — every row TWSE's open-data feed ``t187ap04_L``
(上市公司每日重大訊息) was read to state: company code and short name, announcement time,
clause, fact date, subject and body, keyed by a ref derived from those fields. The feed
covers one announcement day only, so its rows are held as they are read; a listing reads
the held rows. Public exchange data about listed companies — no user data.

ADDITIVE, REVERSIBLE: one new table; downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "061"
down_revision: str | None = "060"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "twse_material_announcements",
        sa.Column("ref", sa.String(length=40), primary_key=True),
        sa.Column("company_code", sa.String(length=6), nullable=False),
        sa.Column("company_name", sa.String(length=100), nullable=False),
        sa.Column("announced_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("clause", sa.String(length=40), nullable=False, server_default=""),
        sa.Column("fact_date", sa.Date(), nullable=True),
        sa.Column("subject", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("content_sha256", sa.String(length=64), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_twse_material_announcements_code_announced",
                    "twse_material_announcements", ["company_code", "announced_at"])


def downgrade() -> None:
    op.drop_index("ix_twse_material_announcements_code_announced",
                  table_name="twse_material_announcements")
    op.drop_table("twse_material_announcements")
