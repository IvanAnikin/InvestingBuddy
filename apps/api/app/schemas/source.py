import uuid
from datetime import datetime

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Source schemas
# ---------------------------------------------------------------------------

VALID_SOURCE_TYPES = {
    "annual_report",
    "quarterly_report",
    "earnings_transcript",
    "investor_presentation",
    "regulatory_filing",
    "industry_report",
    "news_article",
    "macro_report",
    "government_contract",
    "patent_filing",
    "hiring_data",
    "insider_transaction",
    "placeholder",
    # Phase 6: provider-tier source types (mapped from SourceTier enum)
    "financial_data_api",   # T5_api_aggregator — Stooq, EODHD, Alpha Vantage
    "government_data",      # T2_regulator_or_gov — SEC EDGAR, GLEIF, Eurostat
    "company_filing",       # T1_primary_filing — company IR, 10-K, 40-F
    "model_estimate",       # T6_model_estimate — agent-derived calculation
}


class SourceCreate(BaseModel):
    source_type: str = Field(..., max_length=50)
    title: str = Field(..., max_length=500)
    url: str | None = Field(None, max_length=2000)
    publisher: str | None = Field(None, max_length=200)
    published_at: datetime | None = None
    retrieved_at: datetime | None = None
    credibility_score: float | None = Field(None, ge=0.0, le=1.0)
    content_hash: str | None = Field(None, max_length=64)
    blob_path: str | None = Field(None, max_length=1000)


class SourceRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    source_type: str
    title: str
    url: str | None
    publisher: str | None
    published_at: datetime | None
    retrieved_at: datetime
    credibility_score: float | None
    content_hash: str | None
    blob_path: str | None
    created_at: datetime


class SourceList(BaseModel):
    items: list[SourceRead]
    total: int


# ---------------------------------------------------------------------------
# Citation schemas
# ---------------------------------------------------------------------------


class CitationCreate(BaseModel):
    source_id: uuid.UUID
    report_id: uuid.UUID | None = None
    agent_run_id: uuid.UUID | None = None
    claim_text: str | None = None
    source_quote: str | None = None
    url: str | None = Field(None, max_length=2000)
    retrieved_at: datetime | None = None
    # Phase 6: structured provenance (set by provider-sourced citations)
    field_path: str | None = Field(None, max_length=200)
    source_tier: str | None = Field(None, max_length=50)
    data_quality: str | None = Field(None, max_length=50)


class CitationRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    source_id: uuid.UUID
    report_id: uuid.UUID | None
    agent_run_id: uuid.UUID | None
    claim_text: str | None
    source_quote: str | None
    url: str | None
    retrieved_at: datetime | None
    # Phase 6: structured provenance
    field_path: str | None = None
    source_tier: str | None = None
    data_quality: str | None = None
    created_at: datetime


class CitationList(BaseModel):
    items: list[CitationRead]
    total: int


# ---------------------------------------------------------------------------
# Citation validation result schema
# ---------------------------------------------------------------------------


class MissingCitationWarning(BaseModel):
    section: str
    claim: str
    reason: str


class CitationValidationResult(BaseModel):
    status: str  # "ok" | "warnings" | "failed"
    total_claims: int
    cited_claims: int
    missing_citations: list[MissingCitationWarning]
    approved_claims: list[str]
    warnings: list[str]
