"""V3.20 slice 8.3 — precedent-transaction admin schemas."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

MetricKey = Literal["ebit", "ebitda", "revenue", "capacity_mw"]


def _layer_ok(v: str | None) -> str | None:
    from app.services.electricity.taxonomy import LAYER_KEYS

    if v is not None and v not in LAYER_KEYS:
        raise ValueError(f"layer must be one of {', '.join(LAYER_KEYS)}")
    return v


class PrecedentCreate(BaseModel):
    acquirer_name: str = Field(min_length=1, max_length=200)
    target_name: str = Field(min_length=1, max_length=200)
    announced_date: date
    currency: str = Field(pattern=r"^[A-Za-z]{3}$")
    enterprise_value: Decimal = Field(gt=0)
    value_basis: Literal["enterprise_value", "consideration"] = "enterprise_value"
    metric_key: MetricKey | None = None
    metric_value: Decimal | None = Field(default=None, gt=0)
    metric_period: str | None = Field(default=None, max_length=40)
    layer: str | None = None
    source_url: str = Field(min_length=10, max_length=2000)
    source_title: str | None = Field(default=None, max_length=500)
    source_excerpt: str | None = Field(default=None, max_length=1000)

    @field_validator("currency")
    @classmethod
    def _upper(cls, v: str) -> str:
        return v.upper()

    @field_validator("source_url")
    @classmethod
    def _https(cls, v: str) -> str:
        if not v.startswith("https://"):
            raise ValueError("source_url must be an https:// URL")
        return v

    @field_validator("announced_date")
    @classmethod
    def _past(cls, v: date) -> date:
        if v > date.today():
            raise ValueError("announced_date cannot be in the future")
        return v

    _layer = field_validator("layer")(classmethod(lambda cls, v: _layer_ok(v)))


class PrecedentApprove(BaseModel):
    layer: str | None = None
    metric_key: MetricKey | None = None
    metric_value: Decimal | None = Field(default=None, gt=0)
    metric_period: str | None = Field(default=None, max_length=40)
    value_basis: Literal["enterprise_value", "consideration"] | None = None
    note: str | None = Field(default=None, max_length=1000)

    _layer = field_validator("layer")(classmethod(lambda cls, v: _layer_ok(v)))


class PrecedentReject(BaseModel):
    note: str = Field(min_length=3, max_length=1000)


class PrecedentScan(BaseModel):
    company_id: uuid.UUID


class PrecedentRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    acquirer_name: str
    target_name: str
    target_company_id: uuid.UUID | None
    layer: str | None
    announced_date: date
    currency: str
    enterprise_value: Decimal | None
    value_basis: str
    metric_key: str | None
    metric_value: Decimal | None
    metric_period: str | None
    multiple: Decimal | None
    source_url: str
    source_title: str | None
    source_excerpt: str | None
    retrieved_at: datetime
    entry_method: str
    status: str
    reviewed_by: str | None
    reviewed_at: datetime | None
    review_note: str | None
    created_at: datetime
