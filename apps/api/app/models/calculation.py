"""The calculation record — V3.3 Slice 3.3.

One row per calculation **attempted**, computed or refused. Refusals are stored for the
same reason refused tool calls and rejected identifier claims are: an aggregate of
``scope_unknown`` per metric is a statement about the *extraction* layer, and an
aggregate of ``currency_mismatch`` is a statement about the *sources* — neither of
which the successful rows can make.

WHAT MAKES A RESULT CHECKABLE
=============================
``definition_key`` + ``definition_version`` + ``inputs_json`` + ``formula``. A number
whose inputs cannot be named is a number nobody can check, and a number computed under
a formula version nobody recorded is a different metric wearing the same name — the
reason ``CURRENT_EXTRACTION_PIPELINE_VERSION`` exists, applied to arithmetic.

``input_fact_ids_json`` is separate from ``inputs_json`` so "which facts did this
depend on" is answerable without parsing the payload — the query a corrective needs
when a fact turns out to be wrong and everything derived from it has to be found.

NULLS ARE STATEMENTS
====================
``value`` NULL with ``status = 'refused'`` is the whole point of the row.
``result_currency`` NULL on a percentage is correct: stamping a currency on a ratio
would invite a reader to think it had been converted from something.
``result_scale`` NULL means the value is in base units, which is what a result computed
from two known scales is; a non-NULL scale means the inputs shared an unstated one and
the result carries it forward rather than pretending to a precision it does not have.
"""

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CalculationRecord(Base):
    """One attempted calculation, computed or refused."""

    __tablename__ = "calculation_records"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_calculation_records_company_id_companies",
        ),
        nullable=True,
    )
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="SET NULL",
            name="fk_calculation_records_legal_entity_id_legal_entities",
        ),
        nullable=True,
    )
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_jobs.id",
            ondelete="SET NULL",
            name="fk_calculation_records_research_job_id_research_jobs",
        ),
        nullable=True,
    )
    definition_key: Mapped[str] = mapped_column(sa.String(60), nullable=False)
    #: The formula version this row was computed under. A silently redefined metric
    #: makes every historical value a different metric wearing the same name.
    definition_version: Mapped[int] = mapped_column(sa.Integer(), nullable=False)
    formula: Mapped[str | None] = mapped_column(sa.String(300))
    #: ``computed`` | ``refused``.
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    value: Mapped[float | None] = mapped_column(sa.Numeric(30, 10))
    result_unit: Mapped[str | None] = mapped_column(sa.String(30))
    result_currency: Mapped[str | None] = mapped_column(sa.String(10))
    result_scale: Mapped[str | None] = mapped_column(sa.String(20))
    period_key: Mapped[str | None] = mapped_column(sa.String(20))
    period_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_type: Mapped[str | None] = mapped_column(sa.String(20))
    scope_key: Mapped[str | None] = mapped_column(sa.String(220))
    #: A member of ``engine.REFUSAL_REASONS`` when ``status`` is ``refused``.
    refusal_reason: Mapped[str | None] = mapped_column(sa.String(60))
    detail: Mapped[str | None] = mapped_column(sa.String(500))
    #: Every input by role, with its own period, scope, unit, scale and fact id.
    inputs_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    #: The fact ids alone, so "what depended on this fact" is one indexed query rather
    #: than a scan that parses every payload.
    input_fact_ids_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )

    __table_args__ = (
        sa.Index(
            "ix_calculation_records_company_definition",
            "company_id",
            "definition_key",
            "period_key",
        ),
        # "Which metrics does the platform keep failing to produce, and why" — the
        # research-gap query, and the reason refusals are stored at all.
        sa.Index(
            "ix_calculation_records_definition_status",
            "definition_key",
            "status",
            "refusal_reason",
        ),
        sa.Index("ix_calculation_records_research_job_id", "research_job_id"),
        sa.CheckConstraint(
            "status IN ('computed', 'refused')",
            name="ck_calculation_records_status",
        ),
        # A refusal with no reason cannot be aggregated, which is the only thing a
        # stored refusal is for.
        sa.CheckConstraint(
            "status <> 'refused' OR refusal_reason IS NOT NULL",
            name="ck_calculation_records_refusal_has_a_reason",
        ),
        # A computed row with no value is not a computation.
        sa.CheckConstraint(
            "status <> 'computed' OR value IS NOT NULL",
            name="ck_calculation_records_computed_has_a_value",
        ),
        # A refused row must NOT carry a value: a number beside a refusal is the one
        # thing a reader would take at face value.
        sa.CheckConstraint(
            "status <> 'refused' OR value IS NULL",
            name="ck_calculation_records_refused_has_no_value",
        ),
    )
