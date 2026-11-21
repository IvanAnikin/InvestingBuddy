"""OpenDART corp-code list — V3.20 slice 11.3.

ONE TABLE
=========
``opendart_corp_codes`` — the listed companies of OpenDART's corp-code list: the
eight-digit ``corp_code`` (key), the six-character KRX stock code (indexed), the Korean
and English names, and when the list was read. OpenDART serves the whole list as one
slow download (measured 499 s), so it is held and refreshed by an operator job instead
of being read inside a research run.

ADDITIVE, REVERSIBLE: one new table; downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "060"
down_revision: str | None = "059"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "opendart_corp_codes",
        sa.Column("corp_code", sa.String(length=8), primary_key=True),
        sa.Column("stock_code", sa.String(length=6), nullable=False),
        sa.Column("name_ko", sa.String(length=300), nullable=False),
        sa.Column("name_en", sa.String(length=300), nullable=True),
        sa.Column("refreshed_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_opendart_corp_codes_stock_code", "opendart_corp_codes",
                    ["stock_code"])


def downgrade() -> None:
    op.drop_index("ix_opendart_corp_codes_stock_code", table_name="opendart_corp_codes")
    op.drop_table("opendart_corp_codes")
