"""Line rank on extracted facts — ADR-092 review (QA campaign 2026-11, CFR3).

WHAT THIS CARRIES
=================
* ``extracted_facts.line_rank`` — where the validator read the figure
  (``statement_line_selector``): ``0`` the Group's primary statement table, ``1``
  another table, ``2`` prose. ``NULL`` on every row written before 046 and on inline
  XBRL facts (read as "not judged": such a read never sets another read aside).

The statements view sets a disagreeing, unscoped prose read aside only when the
agreeing reads it disagrees with are rank-0 statement rows or inline XBRL tags. Without
the column, every table locator looked like a statement, so a highlights or
parent-company table silenced the prose and filled the slot.

ADDITIVE ONLY
=============
One nullable SMALLINT with no default and no backfill, and one CHECK on its closed
vocabulary (``NULL`` allowed). Downgrade drops exactly what upgrade added, in reverse.
Extraction pipeline version 24 re-extracts every held document with table-derived facts
on reuse, which fills the column for them.

DEPLOY ORDER
============
The ORM maps the column, so apply 046 BEFORE deploying the code: every read of
``extracted_facts`` selects it.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "046"
down_revision: str | None = "045"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE = "extracted_facts"
COLUMN = "line_rank"
CHECK_NAME = "ck_extracted_facts_line_rank"
CHECK_SQL = "line_rank IS NULL OR line_rank IN (0, 1, 2)"


def upgrade() -> None:
    op.add_column(TABLE, sa.Column(COLUMN, sa.SmallInteger(), nullable=True))
    op.create_check_constraint(CHECK_NAME, TABLE, CHECK_SQL)


def downgrade() -> None:
    op.drop_constraint(CHECK_NAME, TABLE, type_="check")
    op.drop_column(TABLE, COLUMN)
