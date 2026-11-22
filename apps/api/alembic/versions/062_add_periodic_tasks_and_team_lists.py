"""Periodic-task state, two monitoring signal kinds, watchlist entry provenance — V3.21.2.

THREE CHANGES (spec §3 and §9, ADR-109)
=======================================
1. ``source_poll_state`` — one row per periodic task (``task_key`` PK): when it last
   started, when it last succeeded, its last error, and an ``etag`` / ``last_modified`` /
   ``detail_json`` cursor for the later source pollers. The registry claims a UTC bucket
   with a guarded UPDATE on ``last_started_at``, so two worker restarts on one day give
   one pass.
2. ``monitoring_signals.kind`` widened with ``missing_information`` and
   ``catalyst_window``.
3. ``watchlist_entries.added_by_hash`` (SHA-256 of the proxied admin e-mail, never the
   address) and ``source`` (``manual`` | ``discovery_candidate`` | ``report`` |
   ``auto_report``), both NULL for existing rows.
4. ``ux_watchlists_team_name``: one team list (``owner`` NULL) per name. The existing
   ``(owner, name)`` unique index does not hold for NULL owners on PostgreSQL (NULLs are
   distinct), and the team lists are created on first use by concurrent callers.

ADDITIVE, REVERSIBLE: downgrade drops the table and the two columns and restores the
narrower kind CHECK. It REFUSES (saying how many) while a signal of a new kind exists:
signals are history and a downgrade never deletes them.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "062"
down_revision: str | None = "061"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BEFORE = ("new_document", "new_ir_event", "transcript_published", "research_delta",
           "open_gap_closable", "macro_revision", "earnings_released",
           "consensus_revision", "passive_ownership_change", "relationship_change",
           "competitor_capacity_announcement", "change_event")
_V321 = ("missing_information", "catalyst_window")
_SOURCES = ("manual", "discovery_candidate", "report", "auto_report")


def _check(kinds: Sequence[str]) -> str:
    return "kind IN (" + ", ".join(f"'{k}'" for k in kinds) + ")"


def upgrade() -> None:
    op.create_table(
        "source_poll_state",
        sa.Column("task_key", sa.Text(), primary_key=True),
        sa.Column("last_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_ok_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("last_modified", sa.Text(), nullable=True),
        sa.Column("detail_json", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.func.now()),
    )

    op.drop_constraint("ck_monitoring_signals_kind", "monitoring_signals", type_="check")
    op.create_check_constraint("ck_monitoring_signals_kind", "monitoring_signals",
                               _check((*_BEFORE, *_V321)))

    op.add_column("watchlist_entries", sa.Column("added_by_hash", sa.Text(), nullable=True))
    op.add_column("watchlist_entries", sa.Column("source", sa.Text(), nullable=True))
    op.create_check_constraint(
        "ck_watchlist_entries_source", "watchlist_entries",
        "source IS NULL OR source IN (" + ", ".join(f"'{s}'" for s in _SOURCES) + ")")
    op.create_index("ux_watchlists_team_name", "watchlists", ["name"], unique=True,
                    postgresql_where=sa.text("owner IS NULL"))


def downgrade() -> None:
    count = op.get_bind().execute(
        sa.text("SELECT count(*) FROM monitoring_signals WHERE " + _check(_V321))
    ).scalar()
    if count:
        raise RuntimeError(
            f"{count} monitoring signal(s) use a V3.21 kind; signals are history and are "
            "not deleted by a downgrade. Export or reclassify them first.")
    op.drop_index("ux_watchlists_team_name", table_name="watchlists")
    op.drop_constraint("ck_watchlist_entries_source", "watchlist_entries", type_="check")
    op.drop_column("watchlist_entries", "source")
    op.drop_column("watchlist_entries", "added_by_hash")
    op.drop_constraint("ck_monitoring_signals_kind", "monitoring_signals", type_="check")
    op.create_check_constraint("ck_monitoring_signals_kind", "monitoring_signals",
                               _check(_BEFORE))
    op.drop_table("source_poll_state")
