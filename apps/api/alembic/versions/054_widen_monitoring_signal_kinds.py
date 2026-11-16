"""Six more monitoring signal kinds — V3.20 slice 9.4 (spec §3.11).

``monitoring_signals.kind`` is a closed vocabulary (``ck_monitoring_signals_kind``,
migration 038). Slice 9.4 adds six kinds derived from the comparable-set records
(migration 053): ``earnings_released``, ``consensus_revision``,
``passive_ownership_change``, ``relationship_change``,
``competitor_capacity_announcement`` and ``change_event``.

ADDITIVE, REVERSIBLE: the constraint is dropped and recreated wider. Downgrade restores
the narrower one, and REFUSES (saying how many) while any signal of a new kind exists:
signals are history, so a downgrade never deletes them.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "054"
down_revision: str | None = "053"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_V39 = ("new_document", "new_ir_event", "transcript_published", "research_delta",
        "open_gap_closable", "macro_revision")
_V320 = ("earnings_released", "consensus_revision", "passive_ownership_change",
         "relationship_change", "competitor_capacity_announcement", "change_event")


def _check(kinds: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    op.drop_constraint("ck_monitoring_signals_kind", "monitoring_signals", type_="check")
    op.create_check_constraint("ck_monitoring_signals_kind", "monitoring_signals",
                               _check((*_V39, *_V320)))


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM monitoring_signals WHERE " + _check(_V320))
    ).scalar()
    if count:
        raise RuntimeError(
            f"{count} monitoring signal(s) use a V3.20 kind; signals are history and are "
            "not deleted by a downgrade. Export or reclassify them first.")
    op.drop_constraint("ck_monitoring_signals_kind", "monitoring_signals", type_="check")
    op.create_check_constraint("ck_monitoring_signals_kind", "monitoring_signals",
                               _check(_V39))
