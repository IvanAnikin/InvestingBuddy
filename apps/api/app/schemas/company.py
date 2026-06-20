import uuid
from datetime import datetime

from pydantic import BaseModel, Field


class CompanyCreate(BaseModel):
    ticker: str = Field(..., max_length=20, description="Stock ticker symbol")
    exchange: str = Field(..., max_length=20, description="Exchange code (e.g. LSE, XETRA)")
    name: str = Field(..., max_length=200)
    country: str | None = Field(None, max_length=100)
    region: str | None = Field(None, max_length=100)
    sector: str | None = Field(None, max_length=100)
    industry: str | None = Field(None, max_length=100)
    market_cap: float | None = None
    currency: str | None = Field(None, max_length=10)
    website: str | None = Field(None, max_length=500)
    description: str | None = None


class CompanyRead(BaseModel):
    model_config = {"from_attributes": True}

    id: uuid.UUID
    ticker: str
    exchange: str
    name: str
    country: str | None
    region: str | None
    sector: str | None
    industry: str | None
    market_cap: float | None
    currency: str | None
    website: str | None
    description: str | None
    status: str
    created_at: datetime
    updated_at: datetime


class CompanyList(BaseModel):
    items: list[CompanyRead]
    total: int
