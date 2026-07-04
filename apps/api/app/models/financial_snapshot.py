"""
CompanyFinancialSnapshot — Phase 13 DB model.

Persists structured provider payload snapshots so:
  - Agent steps can reference what data was available at analysis time.
  - Raw payloads are preserved for auditing and future re-parsing.
  - Deduplication by raw_payload_hash prevents duplicate storage.

Every record traces to a specific company, agent run, provider, and retrieval time.
No API keys or secrets are stored here.
"""

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CompanyFinancialSnapshot(Base):
    """
    A single provider data snapshot for a company.

    snapshot_type encodes what kind of data is stored:
      "fundamentals"  — full EODHD /fundamentals response
      "price_history" — OHLCV data from any provider
      "profile"       — company profile data

    raw_payload_json is the parsed JSON from the provider (no API keys).
    raw_payload_hash is SHA-256 of the raw payload for deduplication.
    datapoints_json is the list of FundamentalDataPoint dicts (serialized).
    """

    __tablename__ = "company_financial_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4
    )

    # ── Company reference ────────────────────────────────────────────────────
    company_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("companies.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    ticker: Mapped[str] = mapped_column(sa.String(20), nullable=False, index=True)
    exchange: Mapped[str | None] = mapped_column(sa.String(20), nullable=True)

    # ── Agent run reference ──────────────────────────────────────────────────
    agent_run_id: Mapped[uuid.UUID | None] = mapped_column(
        sa.Uuid(as_uuid=True),
        sa.ForeignKey("agent_runs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    # ── Provider metadata ────────────────────────────────────────────────────
    provider_name: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    source_tier: Mapped[str] = mapped_column(sa.String(50), nullable=False)
    snapshot_type: Mapped[str] = mapped_column(sa.String(50), nullable=False)

    # ── Data provenance ──────────────────────────────────────────────────────
    retrieved_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), nullable=False
    )
    data_quality: Mapped[str] = mapped_column(
        sa.String(50), nullable=False, default="B_single_credible"
    )

    # ── Payload storage ──────────────────────────────────────────────────────
    raw_payload_json: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    raw_payload_hash: Mapped[str | None] = mapped_column(
        sa.String(64), nullable=True, index=True
    )
    datapoints_json: Mapped[list | None] = mapped_column(JSONB, nullable=True)

    # ── Record timestamps ────────────────────────────────────────────────────
    created_at: Mapped[datetime] = mapped_column(
        sa.DateTime(timezone=True), default=_utcnow, nullable=False
    )

    # ── Indexes ──────────────────────────────────────────────────────────────
    __table_args__ = (
        sa.Index("ix_cfs_provider_ticker", "provider_name", "ticker"),
        sa.Index("ix_cfs_snapshot_type", "snapshot_type"),
    )

    @classmethod
    def from_fundamentals_data(
        cls,
        ticker: str,
        exchange: str | None,
        provider_name: str,
        source_tier: str,
        retrieved_at: datetime,
        raw_payload: dict[str, Any],
        datapoints: list[dict[str, Any]],
        company_id: uuid.UUID | None = None,
        agent_run_id: uuid.UUID | None = None,
        data_quality: str = "B_single_credible",
    ) -> "CompanyFinancialSnapshot":
        """Factory method for creating a snapshot from fundamentals data."""
        import json

        raw_hash = hashlib.sha256(
            json.dumps(raw_payload, sort_keys=True, default=str).encode()
        ).hexdigest()

        return cls(
            company_id=company_id,
            ticker=ticker.upper(),
            exchange=exchange,
            agent_run_id=agent_run_id,
            provider_name=provider_name,
            source_tier=source_tier,
            snapshot_type="fundamentals",
            retrieved_at=retrieved_at,
            data_quality=data_quality,
            raw_payload_json=raw_payload,
            raw_payload_hash=raw_hash,
            datapoints_json=datapoints,
        )
