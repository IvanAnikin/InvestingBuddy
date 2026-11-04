"""Statement kind on research findings — ADR-088 (QA campaign 2026-11, ADBE-3).

WHAT THIS CARRIES
=================
* ``research_findings.statement_kind`` — what kind of statement the finding is, declared
  by the investigator at extraction: ``actual | guidance | forecast | target | estimate |
  unknown``. ``NULL`` on every row written before ADR-088 (read as "not declared", the
  behaviour before this migration).
* ``research_findings.statement_kind_basis`` — how the kind was set (``model``,
  ``not_declared``, ``forward_cue``, ``reclassified_period_not_ended``,
  ``withheld_period_not_ended``) and, for the period invariant, the judged period and its
  end (``withheld_period_not_ended:2026@2026-11-28``). Never statement text.

A statement whose period had not ended is WITHHELD by setting the existing
``verification_status`` to ``withdrawn`` — every reader already excludes those — so no
reader has to learn a new status. The basis column says why.

ADDITIVE ONLY
=============
Two nullable columns with no default and no backfill, and one CHECK on the kind's closed
vocabulary (``NULL`` allowed). Downgrade drops exactly what upgrade added, in reverse.

DEPLOY ORDER
============
The ORM maps both columns, so apply 045 BEFORE deploying the code. The V3 pipeline checks
for them (``schema_readiness.migration_045_readiness``) and degrades with a named reason
when they are absent, as for 041 and 043.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "045"
down_revision: str | None = "044"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "research_findings"
#: ``(column name, type)`` added by this migration, in order.
COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("statement_kind", sa.String(20)),
    ("statement_kind_basis", sa.String(120)),
)
CHECK_NAME = "ck_research_findings_statement_kind"
CHECK_SQL = (
    "statement_kind IS NULL OR statement_kind IN "
    "('actual', 'guidance', 'forecast', 'target', 'estimate', 'unknown')"
)


def upgrade() -> None:
    for name, type_ in COLUMNS:
        op.add_column(TABLE, sa.Column(name, type_, nullable=True))
    op.create_check_constraint(CHECK_NAME, TABLE, CHECK_SQL)


def downgrade() -> None:
    op.drop_constraint(CHECK_NAME, TABLE, type_="check")
    for name, _type in reversed(COLUMNS):
        op.drop_column(TABLE, name)
