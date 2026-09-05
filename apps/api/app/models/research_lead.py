"""The persisted research lead — V3.4 Slice 4.4.

One row per claim a provider made, **including the ones that did not survive**.

WHY A REJECTED ROW IS THE POINT
===============================
``verification_survival_rate`` per provider is the metric the benchmark (slice 4.5) is
built on, and it cannot be computed from the rows that succeeded. A provider whose leads
are eloquent and unverifiable is a provider this platform should stop paying for, and
the only record that says so is the pile of rejections with their reasons. Same rule as
``research_tool_calls`` (029), ``calculation_records`` (030) and
``entity_identifiers``' rejected claims: store the refusal, with a reason from a closed
vocabulary, or the aggregate answers nothing.

THE CHECK THAT MATTERS
======================
``ck_research_leads_verified_has_a_platform_fetch``. A lead reaches ``verified`` only
when ``fetched_content_hash`` is present, and that column is only ever written from
bytes **InvestingBuddy fetched itself**. A verification performed against the provider's
own snippet has verified nothing, and the schema makes the row that would record it
unstorable rather than trusting every future writer to remember.

NULLS ARE STATEMENTS
====================
``period_verified`` / ``scope_verified`` default **False** and mean "not independently
confirmed", never "confirmed absent". A lead can be verified for the presence of its
value in a document the platform fetched while nothing has confirmed the period that
value belongs to — and a reader who assumed otherwise would be reading an annual figure
as an interim one. The two booleans exist so that assumption is impossible to make
silently.
"""

import uuid
from datetime import date, datetime, timezone

import sqlalchemy as sa
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class ResearchLeadRecord(Base):
    """One provider claim, with the outcome of putting it through the gate."""

    __tablename__ = "research_leads"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    research_job_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "research_jobs.id",
            ondelete="SET NULL",
            name="fk_research_leads_research_job_id_research_jobs",
        ),
        nullable=True,
    )
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "companies.id",
            ondelete="SET NULL",
            name="fk_research_leads_company_id_companies",
        ),
        nullable=True,
    )
    legal_entity_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey(
            "legal_entities.id",
            ondelete="SET NULL",
            name="fk_research_leads_legal_entity_id_legal_entities",
        ),
        nullable=True,
    )

    provider: Mapped[str] = mapped_column(sa.String(40), nullable=False)
    model: Mapped[str | None] = mapped_column(sa.String(80))
    provider_task_id: Mapped[str | None] = mapped_column(sa.String(120))

    #: A deterministic digest of the normalised claim. Two providers asserting the
    #: identical thing share it, which is what makes ``duplicate`` detectable without
    #: comparing free text at read time.
    lead_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)
    #: The same digest with every numeric token replaced. Two claims about the same
    #: metric, period and scope that DISAGREE share it — which is what ``superseded``
    #: needs and what ``lead_key`` deliberately cannot express.
    slot_key: Mapped[str] = mapped_column(sa.String(64), nullable=False)

    claim_text: Mapped[str] = mapped_column(sa.String(2000), nullable=False)
    claimed_source_url: Mapped[str | None] = mapped_column(sa.String(1000))
    claimed_source_title: Mapped[str | None] = mapped_column(sa.String(500))
    claimed_publisher: Mapped[str | None] = mapped_column(sa.String(200))
    claimed_date: Mapped[date | None] = mapped_column(sa.Date())
    claimed_value: Mapped[str | None] = mapped_column(sa.String(80))
    claimed_unit: Mapped[str | None] = mapped_column(sa.String(40))
    claimed_currency: Mapped[str | None] = mapped_column(sa.String(10))
    claimed_period: Mapped[str | None] = mapped_column(sa.String(40))
    claimed_scope: Mapped[str | None] = mapped_column(sa.String(220))

    #: ``pending`` | ``verifying`` | ``verified`` | ``rejected`` | ``unverifiable``.
    status: Mapped[str] = mapped_column(sa.String(20), nullable=False)
    #: A member of ``contracts.LEAD_REJECTION_REASONS`` when ``status`` is ``rejected``.
    rejection_reason: Mapped[str | None] = mapped_column(sa.String(40))
    rejection_detail: Mapped[str | None] = mapped_column(sa.String(500))

    #: SHA-256 of the bytes THIS PLATFORM fetched. Never a provider-supplied hash.
    #: Written on a REJECTED row too — "we read this document and it does not say that"
    #: is the record that makes a rejection checkable, and a hash only kept on success
    #: would leave every refusal unauditable.
    fetched_content_hash: Mapped[str | None] = mapped_column(sa.String(64))
    #: The URL actually fetched after redirects — which is not always the one claimed,
    #: and a citation has to name the one that produced the bytes.
    fetched_url: Mapped[str | None] = mapped_column(sa.String(1000))
    #: True only when the source's own declared period/scope was compared against the
    #: claim. False means "not checked", never "checked and equal".
    period_verified: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.text("false")
    )
    scope_verified: Mapped[bool] = mapped_column(
        sa.Boolean(), nullable=False, default=False, server_default=sa.text("false")
    )

    promoted_evidence_id: Mapped[str | None] = mapped_column(sa.String(120))
    promoted_fact_id: Mapped[uuid.UUID | None] = mapped_column(sa.Uuid(as_uuid=True))

    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, server_default=sa.func.now()
    )
    verified_at: Mapped[datetime | None] = mapped_column(sa.DateTime(timezone=True))

    __table_args__ = (
        sa.CheckConstraint(
            "status IN ('pending', 'verifying', 'verified', 'rejected', "
            "'unverifiable')",
            name="ck_research_leads_status",
        ),
        # A refusal nothing can aggregate on answers no question.
        sa.CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name="ck_research_leads_rejected_has_a_reason",
        ),
        # A reason sitting beside a verified row is exactly what a reader takes at
        # face value — the same trap `calculation_records` closes for values.
        sa.CheckConstraint(
            "status = 'rejected' OR rejection_reason IS NULL",
            name="ck_research_leads_only_rejected_has_a_reason",
        ),
        # THE ONE THAT MATTERS. Verified means "we fetched it ourselves".
        sa.CheckConstraint(
            "status <> 'verified' OR fetched_content_hash IS NOT NULL",
            name="ck_research_leads_verified_has_a_platform_fetch",
        ),
        # ``unverifiable`` has one meaning: the claim cites no source anybody could
        # go and check. It is not a soft rejection and must not become one.
        sa.CheckConstraint(
            "status <> 'unverifiable' OR claimed_source_url IS NULL",
            name="ck_research_leads_unverifiable_cites_nothing",
        ),
        sa.Index("ix_research_leads_provider_status", "provider", "status"),
        sa.Index("ix_research_leads_company_status", "company_id", "status"),
        sa.Index("ix_research_leads_research_job_id", "research_job_id"),
        sa.Index("ix_research_leads_lead_key", "lead_key"),
        sa.Index("ix_research_leads_slot_key", "slot_key"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return (
            f"<ResearchLeadRecord {self.provider} {self.status} "
            f"{self.rejection_reason or ''}>"
        )
