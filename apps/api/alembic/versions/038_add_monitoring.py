"""Watchlists and monitoring signals — V3.9 Slice 9.1.

PURPOSE
=======
> **Memory before monitoring.** A watchlist alert is only meaningful as "this changed
> relative to what we concluded".

"Pandora filed something" is a feed. "Pandora filed something, and it bears on the
cash-runway question your last analysis left open" is research — which is why this phase
comes ninth rather than second, and why an issuer with no prior research produces no
signals at all.

ADDITIVE ONLY. Three new tables, nothing altered. **Nothing schedules any of it:**
[OPEN DECISION #15](../../../docs/v3/OPEN_DECISIONS.md#15-monitoring-cadence) is user-owned
and unresolved, ``V3_MONITORING_ENABLED`` defaults off, and a test asserts no cron, worker
job or scheduler references the detector.

THE PARTIAL UNIQUE INDEX IS THE WHOLE POINT
===========================================
``ix_monitoring_signals_open_key`` — **one OPEN signal per key**. A monitor that re-raises
the same signal every time it runs is a monitor everybody turns off. The closed ones stay
as history, so "we told you in March and again in June" is answerable while "we told you
eleven times in March" is impossible.

The key is derived from **what was observed**, never from when it was noticed: a key
containing a timestamp would make every pass produce new signals, which is exactly the
behaviour being prevented.

THREE CHECK CONSTRAINTS
=======================
* ``ck_monitoring_signals_kind`` / ``_status`` — closed vocabularies. There is deliberately
  no ``dismissed`` status: a signal somebody looked at and judged unimportant is
  *acknowledged*, and one the world overtook is *superseded*. Neither is a deletion.
* ``ck_monitoring_signals_acknowledged_has_a_time`` — "somebody dealt with this" with no
  time on it is indistinguishable from a status nobody set deliberately.

A SIGNAL IS AN OBSERVATION, NOT A CONCLUSION
============================================
``summary`` records what changed and ``relates_to_question_key`` what it bears on. Never
what it means — a signal carrying an interpretation would be an unreviewed conclusion with
an alert's authority, and the platform has a whole council for reaching conclusions.

WATCHLIST OWNERSHIP FROM DAY ONE
================================
``watchlists.owner`` is nullable while the platform is single-user, and present because
retrofitting a tenant boundary onto a store that never had an owner column is a rewrite
(governance §8). Adding it now is a nullable field.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "038"
down_revision: str | None = "037"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "watchlists",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "name",
            sa.String(length=200),
            nullable=False,
        ),
        sa.Column(
            "owner",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "description",
            sa.String(length=1000),
            nullable=True,
        ),
        sa.Column(
            "is_active",
            sa.Boolean(),
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_watchlists_owner_name",
        "watchlists",
        ["owner", "name"],
        unique=True,
    )

    op.create_table(
        "watchlist_entries",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "watchlist_id",
            sa.Uuid(as_uuid=True),
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "legal_entity_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "watched_kinds_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "note",
            sa.String(length=500),
            nullable=True,
        ),
        sa.Column(
            "added_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_watchlist_entries_company_id",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_watchlist_entries_legal_entity_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["watchlist_id"],
            ["watchlists.id"],
            name="fk_watchlist_entries_watchlist_id",
            ondelete="CASCADE",
        ),
    )
    op.create_index(
        "ix_watchlist_entries_company_id",
        "watchlist_entries",
        ["company_id"],
    )
    op.create_index(
        "ix_watchlist_entries_list_company",
        "watchlist_entries",
        ["watchlist_id", "company_id"],
        unique=True,
    )

    op.create_table(
        "monitoring_signals",
        sa.Column(
            "id",
            sa.Uuid(as_uuid=True),
            primary_key=True,
            nullable=False,
        ),
        sa.Column(
            "company_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "kind",
            sa.String(length=40),
            nullable=False,
        ),
        sa.Column(
            "signal_key",
            sa.String(length=120),
            nullable=False,
        ),
        sa.Column(
            "summary",
            sa.String(length=1000),
            nullable=False,
        ),
        sa.Column(
            "relates_to_question_key",
            sa.String(length=80),
            nullable=True,
        ),
        sa.Column(
            "relates_to_run_id",
            sa.Uuid(as_uuid=True),
            nullable=True,
        ),
        sa.Column(
            "source_ref",
            sa.String(length=200),
            nullable=True,
        ),
        sa.Column(
            "detail_json",
            JSONB,
            nullable=True,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            nullable=False,
            server_default=sa.text("'open'"),
        ),
        sa.Column(
            "observed_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column(
            "acknowledged_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_monitoring_signals_company_id",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["relates_to_run_id"],
            ["research_runs.id"],
            name="fk_monitoring_signals_run_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status <> 'acknowledged' OR acknowledged_at IS NOT NULL",
            name="ck_monitoring_signals_acknowledged_has_a_time",
        ),
        sa.CheckConstraint(
            "kind IN ('new_document', 'new_ir_event', 'transcript_published', "
            "'research_delta', 'open_gap_closable', 'macro_revision')",
            name="ck_monitoring_signals_kind",
        ),
        sa.CheckConstraint(
            "status IN ('open', 'acknowledged', 'superseded')",
            name="ck_monitoring_signals_status",
        ),
    )
    op.create_index(
        "ix_monitoring_signals_company_status",
        "monitoring_signals",
        ["company_id", "status"],
    )
    op.create_index(
        "ix_monitoring_signals_kind_observed",
        "monitoring_signals",
        ["kind", "observed_at"],
    )
    op.create_index(
        "ix_monitoring_signals_open_key",
        "monitoring_signals",
        ["signal_key"],
        unique=True,
        postgresql_where=sa.text("status = 'open'"),
    )


def downgrade() -> None:
    op.drop_index("ix_monitoring_signals_open_key", table_name="monitoring_signals")
    op.drop_index("ix_monitoring_signals_kind_observed", table_name="monitoring_signals")
    op.drop_index("ix_monitoring_signals_company_status", table_name="monitoring_signals")
    op.drop_table("monitoring_signals")
    op.drop_index("ix_watchlist_entries_list_company", table_name="watchlist_entries")
    op.drop_index("ix_watchlist_entries_company_id", table_name="watchlist_entries")
    op.drop_table("watchlist_entries")
    op.drop_index("ix_watchlists_owner_name", table_name="watchlists")
    op.drop_table("watchlists")
