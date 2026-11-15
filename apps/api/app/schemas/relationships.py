"""Company relationship graph read model — V3.20 slice 2.1 (spec §3.2)."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Literal

from pydantic import BaseModel, Field

RelationshipGroup = Literal[
    "parents",
    "ultimate_parents",
    "subsidiaries",
    "customers",
    "suppliers",
    "significant_holders",
    "holdings",
    "other",
]


class RelationshipEdgeRead(BaseModel):
    """One edge read from the company's point of view, with its evidence."""

    relationship_id: uuid.UUID | None
    label: str = Field(description="The relationship as THIS company reads it, e.g. customer_of.")
    canonical_type: str
    counterparty_entity_id: uuid.UUID | None = Field(
        description="NULL when the counterparty is unresolved (e.g. an anonymous 'Customer A')."
    )
    counterparty_name: str
    counterparty_resolved: bool
    disclosed_share_pct: float | None = None
    share_basis: str | None = None
    period: str | None = None
    as_of: date | None = None
    source: str
    source_kind: str | None = None
    source_url: str | None = None
    evidence_ref: str | None = None
    evidence_excerpt: str | None = None
    extraction_method: str | None = None
    confidence: float
    effective_from: date | None = None


class ReportingExceptionRead(BaseModel):
    """A registry's stated reason for NOT reporting a parent (GLEIF Level 2).

    "No parent reported: controlled by natural persons" is a fact about the company and
    is NOT the same as "independent".
    """

    relationship_type: str
    group: str = Field(description="The group the missing edge would sit in (parents, …).")
    reason: str = Field(description="GLEIF's reason as published, e.g. NATURAL_PERSONS.")
    reason_label: str | None = None
    category: str | None = None
    source: str
    source_url: str | None = None
    as_of: date | None = None


class CompanyRelationshipsRead(BaseModel):
    company_id: uuid.UUID
    legal_entity_id: uuid.UUID | None
    #: ``ok`` | ``entity_unresolved`` (the company has no entity-master row yet) |
    #: ``disabled`` (V3_ENTITY_MASTER_ENABLED is off).
    state: Literal["ok", "entity_unresolved", "disabled"]
    #: Keys are the ``RelationshipGroup`` values; every group is always present.
    groups: dict[str, list[RelationshipEdgeRead]]
    total: int
    reporting_exceptions: list[ReportingExceptionRead] = Field(default_factory=list)
    note: str = (
        "Relationships come only from cited sources (GLEIF, SEC filings, XBRL, the "
        "company's own documents). An empty group means nothing was found in the sources "
        "read — not that the company has none."
    )
