"""Add the research lead — V3.4 Slice 4.4.

PURPOSE
=======
One row per claim an external provider made, **including the ones that did not
survive**. ``verification_survival_rate`` per provider is the metric the benchmark
(slice 4.5) is built on and it cannot be computed from the rows that succeeded: a
provider whose leads are eloquent and unverifiable is one this platform should stop
paying for, and the pile of rejections with their reasons is the only record that says
so.

ADDITIVE ONLY. One new table, nothing altered. Nothing writes to it unless
``V3_PROVIDER_RUNTIME_ENABLED`` is on, which is off by default.

FIVE CHECK CONSTRAINTS, EACH A ROW THAT WOULD OTHERWISE BE STORABLE
==================================================================
* ``ck_research_leads_status`` — a closed vocabulary, the one in
  ``providers.contracts``.
* ``ck_research_leads_rejected_has_a_reason`` — a refusal nothing can aggregate on
  answers no question.
* ``ck_research_leads_only_rejected_has_a_reason`` — and a reason beside a *verified*
  row is exactly what a reader takes at face value.
* ``ck_research_leads_verified_has_a_platform_fetch`` — **the one that matters.** A lead
  reaches ``verified`` only with the SHA-256 of bytes InvestingBuddy fetched itself.
  The column is written on a rejected row too — "we read this and it does not say that"
  is what makes a refusal checkable. A
  verification performed against the provider's own snippet has verified nothing, and
  this makes the row that would record it unstorable rather than trusting every future
  writer to remember.
* ``ck_research_leads_unverifiable_cites_nothing`` — ``unverifiable`` has one meaning:
  the claim cites no source anybody could check. It is not a soft rejection and must not
  become a place to put one.

FOREIGN KEYS
============
All three ``SET NULL``: lineage, not composition. What a provider claimed and whether it
survived outlives the company row it referred to (CLAUDE.md rule 15), and an audit
record a deletion could remove is not an audit record.

INDEXES
=======
* ``ix_research_leads_provider_status`` — the survival-rate query, which is the whole
  reason rejections are kept.
* ``ix_research_leads_company_status`` — "what has been claimed about this issuer".
* ``ix_research_leads_lead_key`` / ``_slot_key`` — duplicate and supersession detection,
  which happens BEFORE a fetch is spent.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers
revision: str = "031"
down_revision: str | None = "030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "research_leads",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("research_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("provider", sa.String(length=40), nullable=False),
        sa.Column("model", sa.String(length=80), nullable=True),
        sa.Column("provider_task_id", sa.String(length=120), nullable=True),
        sa.Column("lead_key", sa.String(length=64), nullable=False),
        sa.Column("slot_key", sa.String(length=64), nullable=False),
        sa.Column("claim_text", sa.String(length=2000), nullable=False),
        sa.Column("claimed_source_url", sa.String(length=1000), nullable=True),
        sa.Column("claimed_source_title", sa.String(length=500), nullable=True),
        sa.Column("claimed_publisher", sa.String(length=200), nullable=True),
        sa.Column("claimed_date", sa.Date(), nullable=True),
        sa.Column("claimed_value", sa.String(length=80), nullable=True),
        sa.Column("claimed_unit", sa.String(length=40), nullable=True),
        sa.Column("claimed_currency", sa.String(length=10), nullable=True),
        sa.Column("claimed_period", sa.String(length=40), nullable=True),
        sa.Column("claimed_scope", sa.String(length=220), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("rejection_reason", sa.String(length=40), nullable=True),
        sa.Column("rejection_detail", sa.String(length=500), nullable=True),
        sa.Column("fetched_content_hash", sa.String(length=64), nullable=True),
        sa.Column("fetched_url", sa.String(length=1000), nullable=True),
        sa.Column(
            "period_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column(
            "scope_verified",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
        sa.Column("promoted_evidence_id", sa.String(length=120), nullable=True),
        sa.Column("promoted_fact_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["research_job_id"],
            ["research_jobs.id"],
            name="fk_research_leads_research_job_id_research_jobs",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_research_leads_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_research_leads_legal_entity_id_legal_entities",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'verifying', 'verified', 'rejected', "
            "'unverifiable')",
            name="ck_research_leads_status",
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name="ck_research_leads_rejected_has_a_reason",
        ),
        sa.CheckConstraint(
            "status = 'rejected' OR rejection_reason IS NULL",
            name="ck_research_leads_only_rejected_has_a_reason",
        ),
        sa.CheckConstraint(
            "status <> 'verified' OR fetched_content_hash IS NOT NULL",
            name="ck_research_leads_verified_has_a_platform_fetch",
        ),
        sa.CheckConstraint(
            "status <> 'unverifiable' OR claimed_source_url IS NULL",
            name="ck_research_leads_unverifiable_cites_nothing",
        ),
    )
    op.create_index(
        "ix_research_leads_provider_status", "research_leads", ["provider", "status"]
    )
    op.create_index(
        "ix_research_leads_company_status", "research_leads", ["company_id", "status"]
    )
    op.create_index(
        "ix_research_leads_research_job_id", "research_leads", ["research_job_id"]
    )
    op.create_index("ix_research_leads_lead_key", "research_leads", ["lead_key"])
    op.create_index("ix_research_leads_slot_key", "research_leads", ["slot_key"])


def downgrade() -> None:
    op.drop_index("ix_research_leads_slot_key", table_name="research_leads")
    op.drop_index("ix_research_leads_lead_key", table_name="research_leads")
    op.drop_index("ix_research_leads_research_job_id", table_name="research_leads")
    op.drop_index("ix_research_leads_company_status", table_name="research_leads")
    op.drop_index("ix_research_leads_provider_status", table_name="research_leads")
    op.drop_table("research_leads")
