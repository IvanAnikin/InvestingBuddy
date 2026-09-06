"""Macro, government and industry observations — V3.4 Slice 4.7.

PURPOSE
=======
Fifteen macro sources sit in the registry today and every one is **reference-only by
design**: it emits a bounded pointer plus an honest gap and makes no network call at
report time. That was right when nothing could store a time series. These three tables
are the somewhere for one to land.

ADDITIVE ONLY. Three new tables, nothing altered. Nothing writes to them unless
``V3_MACRO_SOURCES_ENABLED`` is on, which is off by default.

THE PARTIAL UNIQUE INDEX IS THE WHOLE DESIGN
============================================
A macro series is **revised**. Euro-area GDP for Q2 is one number in July and a different
number in October, and both were correct when published. A store that overwrote the first
would silently change the macro context of every report that cited it, months after that
report was signed off.

So:

* ``ix_macro_observations_series_period_vintage`` is UNIQUE on
  ``(series_id, period_key, vintage_at)`` — a revision is a **new row**, and writing the
  same vintage twice is a conflict rather than an overwrite. That is what makes "the
  publisher re-released" and "our connector ran twice" different events.
* ``ix_macro_observations_one_current`` is a **partial** unique index on
  ``(series_id, period_key) WHERE is_current`` — exactly one current reading per period,
  enforced by the database rather than by every writer remembering.

NULLABLE `value` IS DELIBERATE
==============================
A statistical agency publishes "no observation for this period" constantly, and that is
information: it is not the same as the platform never having asked. A NULL observation is
a real row with a real vintage. What is never written is a zero standing in for one.

FOREIGN KEYS
============
``CASCADE`` here, unlike the ``SET NULL`` used for company lineage. An observation of a
series that no longer exists is not a partial record — it is uninterpretable, because the
unit and frequency that make a number mean something live on the series.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "033"
down_revision: str | None = "032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "macro_datasets",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("dataset_key", sa.String(length=120), nullable=False),
        sa.Column("source_id", sa.String(length=60), nullable=False),
        sa.Column("display_name", sa.String(length=200), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("licence", sa.String(length=200), nullable=True),
        sa.Column("update_cadence", sa.String(length=60), nullable=True),
        sa.Column(
            "access_class",
            sa.String(length=30),
            nullable=False,
            server_default=sa.text("'public_official'"),
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index(
        "ix_macro_datasets_dataset_key", "macro_datasets", ["dataset_key"], unique=True
    )

    op.create_table(
        "macro_series",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("dataset_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("series_key", sa.String(length=200), nullable=False),
        sa.Column("display_name", sa.String(length=300), nullable=False),
        sa.Column("indicator_code", sa.String(length=120), nullable=True),
        sa.Column("unit", sa.String(length=60), nullable=False),
        sa.Column("currency", sa.String(length=10), nullable=True),
        sa.Column("scale", sa.String(length=20), nullable=True),
        sa.Column("geography", sa.String(length=20), nullable=True),
        sa.Column("frequency", sa.String(length=20), nullable=False),
        sa.Column("seasonal_adjustment", sa.String(length=10), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["dataset_id"],
            ["macro_datasets.id"],
            name="fk_macro_series_dataset_id_macro_datasets",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "frequency IN ('annual', 'quarterly', 'monthly', 'weekly', 'daily')",
            name="ck_macro_series_frequency",
        ),
        sa.CheckConstraint(
            "seasonal_adjustment IS NULL OR seasonal_adjustment IN ('sa', 'nsa')",
            name="ck_macro_series_seasonal_adjustment",
        ),
    )
    op.create_index(
        "ix_macro_series_dataset_key",
        "macro_series",
        ["dataset_id", "series_key"],
        unique=True,
    )
    op.create_index(
        "ix_macro_series_geography_frequency",
        "macro_series",
        ["geography", "frequency"],
    )

    op.create_table(
        "macro_observations",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("series_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("period_key", sa.String(length=20), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=True),
        sa.Column("period_end", sa.Date(), nullable=True),
        sa.Column("value", sa.Numeric(30, 10), nullable=True),
        sa.Column("vintage_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("retrieved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source_ref", sa.String(length=1000), nullable=True),
        sa.Column(
            "is_current", sa.Boolean(), nullable=False, server_default=sa.true()
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["series_id"],
            ["macro_series.id"],
            name="fk_macro_observations_series_id_macro_series",
            ondelete="CASCADE",
        ),
        sa.CheckConstraint(
            "period_end IS NULL OR period_start IS NULL OR period_end >= period_start",
            name="ck_macro_observations_period_order",
        ),
    )
    op.create_index(
        "ix_macro_observations_series_period_vintage",
        "macro_observations",
        ["series_id", "period_key", "vintage_at"],
        unique=True,
    )
    op.create_index(
        "ix_macro_observations_series_period",
        "macro_observations",
        ["series_id", "period_key"],
    )
    # Exactly one current reading per (series, period). A PARTIAL unique index, so the
    # superseded vintages sit beside it without contending for the slot.
    op.create_index(
        "ix_macro_observations_one_current",
        "macro_observations",
        ["series_id", "period_key"],
        unique=True,
        postgresql_where=sa.text("is_current"),
    )


def downgrade() -> None:
    op.drop_index("ix_macro_observations_one_current", table_name="macro_observations")
    op.drop_index("ix_macro_observations_series_period", table_name="macro_observations")
    op.drop_index(
        "ix_macro_observations_series_period_vintage", table_name="macro_observations"
    )
    op.drop_table("macro_observations")
    op.drop_index("ix_macro_series_geography_frequency", table_name="macro_series")
    op.drop_index("ix_macro_series_dataset_key", table_name="macro_series")
    op.drop_table("macro_series")
    op.drop_index("ix_macro_datasets_dataset_key", table_name="macro_datasets")
    op.drop_table("macro_datasets")
