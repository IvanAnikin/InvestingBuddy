"""EDINET filing index — V3.20 slice 11.2.

TWO TABLES
==========
``edinet_index_days`` — one row per calendar day whose EDINET document list was read:
when, how many filings, and whether the read is final (a past day) or provisional.

``edinet_filings`` — the metadata of each filing listed on those days (EDINET document
id as the key; filer EDINET code and securities code; document type; period; submission
time; withdrawal status; which formats exist). Never content: the content is the
official PDF, fetched into the corpus by the disclosure acquisition bridge.

ADDITIVE, REVERSIBLE: two new tables; downgrade drops them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "059"
down_revision: str | None = "058"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "edinet_index_days",
        sa.Column("day", sa.Date(), primary_key=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("filings", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("final", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.create_table(
        "edinet_filings",
        sa.Column("doc_id", sa.String(16), primary_key=True),
        sa.Column("edinet_code", sa.String(8)),
        sa.Column("sec_code", sa.String(8)),
        sa.Column("filer_name", sa.String(300)),
        sa.Column("doc_type_code", sa.String(8)),
        sa.Column("ordinance_code", sa.String(8)),
        sa.Column("form_code", sa.String(16)),
        sa.Column("doc_description", sa.String(500)),
        sa.Column("period_start", sa.Date()),
        sa.Column("period_end", sa.Date()),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("parent_doc_id", sa.String(16)),
        sa.Column("withdrawal_status", sa.String(4)),
        sa.Column("pdf_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("xbrl_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("csv_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("english_flag", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("index_day", sa.Date(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_edinet_filings_edinet_code_submitted", "edinet_filings",
                    ["edinet_code", "submitted_at"])
    op.create_index("ix_edinet_filings_day", "edinet_filings", ["index_day"])


def downgrade() -> None:
    op.drop_index("ix_edinet_filings_day", table_name="edinet_filings")
    op.drop_index("ix_edinet_filings_edinet_code_submitted", table_name="edinet_filings")
    op.drop_table("edinet_filings")
    op.drop_table("edinet_index_days")
