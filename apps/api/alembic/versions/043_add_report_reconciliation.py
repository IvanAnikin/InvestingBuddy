"""Report reconciliation — gap closure and temporal supersession.

WHAT THIS CARRIES
=================
* ``research_gaps.reconciliation_status`` / ``reconciliation_json`` — what the final
  reconciliation step decided about a gap once every finding, validated fact and acquired
  document of the run was known: ``closed`` (a named finding states the field),
  ``partially_closed`` (a finding states it, for an older period, another scope or from a
  third party only), ``superseded`` (the acquisition failure it records was overcome) or
  ``still_open``. NULL means "written before reconciliation existed".
* ``research_findings.source_published_at`` — the publication date of the evidence a
  finding cites, so that two findings about the same field of the same project can be
  ordered by WHEN the issuer said it.
* ``research_findings.superseded_by_finding_id`` — the newer finding that replaced this
  one as current guidance. Both are kept; the older is shown as prior guidance.

ADDITIVE ONLY
=============
Nullable columns, one self-referencing foreign key (``ON DELETE SET NULL``) and three
CHECKs that every existing row satisfies (all new columns are NULL). No backfill.

The ledger's own ``ck_research_gaps_closed_names_a_finding`` still decides the ledger
status; the new CHECK makes the reconciliation status obey the same rule: a gap may be
reconciled ``closed`` only when a finding is named.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "043"
# down_revision re-pointed 041 -> 042 (web search provenance): done at the merge with the
# open-web line. Final chain: 041 -> 042 -> 043 -> 044.
down_revision: str | None = "042"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: ``(table, column name, type)`` in creation order. Downgrade walks it backwards.
_COLUMNS: tuple[tuple[str, str, sa.types.TypeEngine | type], ...] = (
    ("research_gaps", "reconciliation_status", sa.String(30)),
    ("research_gaps", "reconciliation_json", JSONB),
    ("research_findings", "source_published_at", sa.Date),
    ("research_findings", "superseded_by_finding_id", sa.Uuid(as_uuid=True)),
)

_FK = (
    "fk_research_findings_superseded_by_finding_id",
    "research_findings",
    "research_findings",
    ["superseded_by_finding_id"],
    ["id"],
)

_CHECKS: tuple[tuple[str, str, str], ...] = (
    (
        "ck_research_gaps_reconciliation_status",
        "research_gaps",
        "reconciliation_status IS NULL OR reconciliation_status IN "
        "('closed', 'partially_closed', 'superseded', 'still_open')",
    ),
    (
        "ck_research_gaps_reconciled_closed_names_a_finding",
        "research_gaps",
        "reconciliation_status IS NULL OR reconciliation_status <> 'closed' "
        "OR closed_by_finding_id IS NOT NULL",
    ),
    (
        "ck_research_findings_not_superseded_by_itself",
        "research_findings",
        "superseded_by_finding_id IS NULL OR superseded_by_finding_id <> id",
    ),
)


def upgrade() -> None:
    for table, name, type_ in _COLUMNS:
        op.add_column(table, sa.Column(name, type_, nullable=True))
    name, source, referent, local_cols, remote_cols = _FK
    op.create_foreign_key(
        name, source, referent, local_cols, remote_cols, ondelete="SET NULL"
    )
    for check_name, table, condition in _CHECKS:
        op.create_check_constraint(check_name, table, condition)


def downgrade() -> None:
    for check_name, table, _condition in reversed(_CHECKS):
        op.drop_constraint(check_name, table, type_="check")
    op.drop_constraint(_FK[0], _FK[1], type_="foreignkey")
    for table, name, _type in reversed(_COLUMNS):
        op.drop_column(table, name)
