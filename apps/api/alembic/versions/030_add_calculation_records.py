"""Add the calculation record — V3.3 Slice 3.3.

PURPOSE
=======
One row per calculation **attempted**, computed or refused, with everything needed to
reproduce it and to attribute a wrong answer: the definition key and its version, the
formula, every input by role with its own period, scope, unit and scale, and the input
fact ids on their own.

ADDITIVE ONLY. One new table, nothing altered. Nothing writes to it unless
``V3_AGENT_TOOLS_ENABLED`` is on, which is off by default.

WHY REFUSALS ARE STORED
=======================
The same reason refused tool calls (029) and rejected identifier claims are. An
aggregate of ``scope_unknown`` per metric is a statement about the **extraction** layer;
an aggregate of ``currency_mismatch`` is a statement about the **sources**. Neither of
those can be made from the rows that succeeded, and the acceptance strategy's
demonstration for V3.3 — *a calculation with incompatible periods or scopes is refused,
not computed* — is only auditable if the refusals exist.

FOUR CHECK CONSTRAINTS, EACH A FAILURE THAT WOULD OTHERWISE BE STORABLE
======================================================================
* ``ck_calculation_records_status`` — a closed vocabulary.
* ``ck_calculation_records_refusal_has_a_reason`` — a refusal nothing can aggregate on
  is a refusal that answers no question.
* ``ck_calculation_records_computed_has_a_value`` — a computed row with no value is not
  a computation.
* ``ck_calculation_records_refused_has_no_value`` — **the important one.** A number
  sitting beside a refusal is exactly the thing a reader takes at face value, so the
  schema makes that row unstorable rather than trusting every writer and every later
  migration to leave the column alone.

FOREIGN KEYS
============
All three ``SET NULL``: lineage, not composition. A calculation that a company row
outlived is still evidence of what the platform computed and why (CLAUDE.md rule 15).

INDEXES
=======
* ``ix_calculation_records_company_definition`` — "this issuer's operating margin by
  period", the read path.
* ``ix_calculation_records_definition_status`` — "which metrics does the platform keep
  failing to produce, and why": the research-gap query, and the whole reason refusals
  are kept.
* ``ix_calculation_records_research_job_id`` — everything one run computed.

NULLABILITY IS A STATEMENT
==========================
``value`` NULL beside ``status = 'refused'`` is the point of the row.
``result_currency`` NULL on a percentage is correct — stamping a currency on a ratio
invites a reader to think it was converted from something. ``result_scale`` NULL means
base units, which is what a result from two *known* scales is; a non-NULL scale means
the inputs shared an unstated one and the result carries it forward rather than claiming
a precision it does not have.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

from alembic import op

# revision identifiers
revision: str = "030"
down_revision: str | None = "029"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "calculation_records",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("company_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("legal_entity_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("research_job_id", sa.Uuid(as_uuid=True), nullable=True),
        sa.Column("definition_key", sa.String(length=60), nullable=False),
        sa.Column("definition_version", sa.Integer(), nullable=False),
        sa.Column("formula", sa.String(length=300), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("value", sa.Numeric(30, 10), nullable=True),
        sa.Column("result_unit", sa.String(length=30), nullable=True),
        sa.Column("result_currency", sa.String(length=10), nullable=True),
        sa.Column("result_scale", sa.String(length=20), nullable=True),
        sa.Column("period_key", sa.String(length=20), nullable=True),
        sa.Column("period_type", sa.String(length=20), nullable=True),
        sa.Column("scope_type", sa.String(length=20), nullable=True),
        sa.Column("scope_key", sa.String(length=220), nullable=True),
        sa.Column("refusal_reason", sa.String(length=60), nullable=True),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("inputs_json", JSONB, nullable=True),
        sa.Column("input_fact_ids_json", JSONB, nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.ForeignKeyConstraint(
            ["company_id"],
            ["companies.id"],
            name="fk_calculation_records_company_id_companies",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["legal_entity_id"],
            ["legal_entities.id"],
            name="fk_calculation_records_legal_entity_id_legal_entities",
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["research_job_id"],
            ["research_jobs.id"],
            name="fk_calculation_records_research_job_id_research_jobs",
            ondelete="SET NULL",
        ),
        sa.CheckConstraint(
            "status IN ('computed', 'refused')",
            name="ck_calculation_records_status",
        ),
        sa.CheckConstraint(
            "status <> 'refused' OR refusal_reason IS NOT NULL",
            name="ck_calculation_records_refusal_has_a_reason",
        ),
        sa.CheckConstraint(
            "status <> 'computed' OR value IS NOT NULL",
            name="ck_calculation_records_computed_has_a_value",
        ),
        sa.CheckConstraint(
            "status <> 'refused' OR value IS NULL",
            name="ck_calculation_records_refused_has_no_value",
        ),
    )
    op.create_index(
        "ix_calculation_records_company_definition",
        "calculation_records",
        ["company_id", "definition_key", "period_key"],
    )
    op.create_index(
        "ix_calculation_records_definition_status",
        "calculation_records",
        ["definition_key", "status", "refusal_reason"],
    )
    op.create_index(
        "ix_calculation_records_research_job_id",
        "calculation_records",
        ["research_job_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_calculation_records_research_job_id", table_name="calculation_records"
    )
    op.drop_index(
        "ix_calculation_records_definition_status", table_name="calculation_records"
    )
    op.drop_index(
        "ix_calculation_records_company_definition", table_name="calculation_records"
    )
    op.drop_table("calculation_records")
