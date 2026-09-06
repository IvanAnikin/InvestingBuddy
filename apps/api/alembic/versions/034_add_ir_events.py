"""IR events and their materials — V3.4 Slice 4.8.

PURPOSE
=======
[ADR-051](../../../docs/DECISIONS.md): no paid transcript subscription, the canonical
model implemented anyway, acquisition from free public issuer sources, and **missing
means missing**.

That last part only works if the schema can tell three states apart that a naive one
collapses into a single empty result:

1. the issuer held a call and published a transcript;
2. the issuer held a call and published **none** — a research gap a human can close;
3. nobody has looked.

An absent ``ir_event_materials`` row is state 3. State 2 is a **row** whose
``availability`` is ``not_published``. Without it, "CFR published no FY2025 transcript"
and "CFR held no call" are the same absence — and the second is a far stranger fact about
a listed company than the first.

ADDITIVE ONLY. Two new tables, nothing altered. Nothing writes to them unless the V3
transcript path is exercised, which nothing schedules.

FOUR CHECK CONSTRAINTS
======================
* ``ck_ir_events_event_type`` / ``ck_ir_event_materials_material_type`` /
  ``ck_ir_event_materials_availability`` — closed vocabularies, so "no transcript" can
  be aggregated. Free text cannot be.
* ``ck_ir_events_only_occurred_has_an_occurrence`` — an *announced* event carrying an
  occurrence time is a calendar entry asserting a past nobody confirmed.
* ``ck_ir_event_materials_available_has_a_location`` — "available" with no URL and no
  ingested version is a claim nothing can act on or check.
* ``ck_ir_event_materials_only_available_is_ingested`` — a corpus document attached to a
  ``not_published`` row is one record contradicting itself.

FOREIGN KEYS
============
``SET NULL`` to companies and entities (lineage: an event outlives the row that pointed
at an issuer), ``SET NULL`` to the corpus version (a de-ingested document leaves the
availability record standing), and ``CASCADE`` from event to material — a material whose
event is gone has nothing to be a material *of*.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "034"
down_revision: str | None = "033"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "ir_events",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("event_key", sa.String(length=200), nullable=False),
        sa.Column("event_type", sa.String(length=40), nullable=False),
        sa.Column("title", sa.String(length=400), nullable=True),
        sa.Column("fiscal_period_key", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("source_ref", sa.String(length=200), nullable=True),
        sa.Column(
            "first_seen_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
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
            name="fk_ir_events_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_ir_events_legal_entity_id_legal_entities",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "event_type IN ('earnings_call', 'results_release', "
            "'capital_markets_day', 'agm', 'investor_day', 'guidance_update')",
            name="ck_ir_events_event_type",
        ),
        sa.CheckConstraint(
            "status IN ('announced', 'occurred', 'cancelled')",
            name="ck_ir_events_status",
        ),
        sa.CheckConstraint(
            "status = 'occurred' OR occurred_at IS NULL",
            name="ck_ir_events_only_occurred_has_an_occurrence",
        ),
    )
    op.create_index("ix_ir_events_event_key", "ir_events", ["event_key"], unique=True)
    op.create_index(
        "ix_ir_events_company_period", "ir_events", ["company_id", "fiscal_period_key"]
    )
    op.create_index(
        "ix_ir_events_type_occurred", "ir_events", ["event_type", "occurred_at"]
    )

    op.create_table(
        "ir_event_materials",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("ir_event_id", sa.Uuid(as_uuid=True), nullable=False),
        sa.Column("material_type", sa.String(length=30), nullable=False),
        sa.Column("availability", sa.String(length=20), nullable=False),
        sa.Column("url", sa.String(length=1000), nullable=True),
        sa.Column(
            "research_document_version_id", sa.Uuid(as_uuid=True), nullable=True
        ),
        sa.Column("note", sa.String(length=500), nullable=True),
        sa.Column("checked_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["ir_event_id"],
            ["ir_events.id"],
            name="fk_ir_event_materials_event_id_ir_events",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["research_document_version_id"],
            ["research_document_versions.id"],
            name="fk_ir_event_materials_version_id",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "material_type IN ('transcript', 'presentation', 'press_release', "
            "'report', 'webcast', 'audio', 'qa')",
            name="ck_ir_event_materials_material_type",
        ),
        sa.CheckConstraint(
            "availability IN ('available', 'not_published', 'paywalled', 'unknown')",
            name="ck_ir_event_materials_availability",
        ),
        sa.CheckConstraint(
            "availability <> 'available' OR url IS NOT NULL "
            "OR research_document_version_id IS NOT NULL",
            name="ck_ir_event_materials_available_has_a_location",
        ),
        sa.CheckConstraint(
            "research_document_version_id IS NULL OR availability = 'available'",
            name="ck_ir_event_materials_only_available_is_ingested",
        ),
    )
    op.create_index(
        "ix_ir_event_materials_event_type",
        "ir_event_materials",
        ["ir_event_id", "material_type"],
        unique=True,
    )
    op.create_index(
        "ix_ir_event_materials_availability", "ir_event_materials", ["availability"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_ir_event_materials_availability", table_name="ir_event_materials"
    )
    op.drop_index("ix_ir_event_materials_event_type", table_name="ir_event_materials")
    op.drop_table("ir_event_materials")
    op.drop_index("ix_ir_events_type_occurred", table_name="ir_events")
    op.drop_index("ix_ir_events_company_period", table_name="ir_events")
    op.drop_index("ix_ir_events_event_key", table_name="ir_events")
    op.drop_table("ir_events")
