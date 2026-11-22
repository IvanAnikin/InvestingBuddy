"""Auto-report record on a discovery run — V3.21.3.

ONE COLUMN (spec §4 and §9, ADR-110)
====================================
``discovery_runs.auto_report_json`` (JSONB, NULL): what the automatic report on the
run's best eligible candidate did — ``{state: queued | skipped | linked | failed,
reason, candidate_id, company_id, job_id, report_id, at}``. It is also the per-run
idempotency record (``auto_report:{run_id}``): a run that already has one is never
auto-reported again, and the per-day cap counts the ``queued`` records of the day.

NULL for every existing row, for ticker runs and while the flag is off.

ADDITIVE, REVERSIBLE: downgrade drops the column. The record is a run annotation; the
research job it points at is a ``research_jobs`` row and is not touched.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "063"
down_revision: str | None = "062"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "discovery_runs",
        sa.Column("auto_report_json", postgresql.JSONB(astext_type=sa.Text()),
                  nullable=True),
    )


def downgrade() -> None:
    op.drop_column("discovery_runs", "auto_report_json")
