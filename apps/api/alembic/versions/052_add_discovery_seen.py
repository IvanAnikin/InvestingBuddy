"""Discovery anti-convergence store — V3.20 slice 6.4 (spec §3.6).

ONE TABLE
=========
``discovery_seen`` — one row per listed issuer (``EXCHANGE:TICKER``) discovery has
surfaced: first / last seen, times seen and shortlisted, when it was last researched, the
newest change-event date known at that research, the last discovery run, and the
diversity cell (layer × driver × regime) it fell in. The novelty gate reads it; research
history itself stays where it is and is never deleted.

ADDITIVE, REVERSIBLE: one new table; downgrade drops it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

revision: str = "052"
down_revision: str | None = "051"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "discovery_seen",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("issuer_key", sa.String(64), nullable=False),
        sa.Column("company_id", sa.Uuid(),
                  sa.ForeignKey("companies.id", ondelete="SET NULL",
                                name="fk_discovery_seen_company_id_companies"),
                  nullable=True),
        sa.Column("first_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("times_seen", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("times_shortlisted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_shortlisted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_researched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_event_date", sa.Date(), nullable=True),
        sa.Column("last_discovery_run_id", sa.Uuid(), nullable=True),
        sa.Column("cell_json", JSONB(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(),
                  nullable=False),
        sa.UniqueConstraint("issuer_key", name="uq_discovery_seen_issuer_key"),
    )
    op.create_index("ix_discovery_seen_company_id", "discovery_seen", ["company_id"])


def downgrade() -> None:
    op.drop_index("ix_discovery_seen_company_id", table_name="discovery_seen")
    op.drop_table("discovery_seen")
