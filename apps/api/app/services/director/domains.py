"""Research domains — V3.18.2.

WHY A CLOSED VOCABULARY
=======================
The SCCO baseline report had eight specialists and one subject: margins, net income,
operating cash flow, capex, debt — eight times. Nothing told any of them that a question
about copper demand, a question about the Tia Maria project and a question about Grupo
Mexico's control of the board were *different jobs owned by different people*. The only
organising key a question carried was its own name.

A domain is that missing key. Every question belongs to exactly one; every finding
inherits it; every domain has one default owner; and the report is assembled domain by
domain. "Financial statements are one analytical dimension rather than the organising
centre of the report" is, operationally, this table.

It is closed for the reason every vocabulary in this codebase is closed: "which domain
does this platform keep failing to answer?" is a question about the pipeline, and free
text cannot answer it.
"""

from __future__ import annotations

from dataclasses import dataclass

BUSINESS_MODEL = "business_model"
INDUSTRY_ECONOMICS = "industry_economics"
PRODUCTS_SERVICES = "products_services"
CUSTOMERS_END_MARKETS = "customers_end_markets"
OPERATIONS_ASSETS = "operations_assets"
COMPETITIVE_POSITION = "competitive_position"
GROWTH_PIPELINE = "growth_pipeline"
FINANCIAL_CAPACITY = "financial_capacity"
CAPITAL_ALLOCATION = "capital_allocation"
VALUATION_CONTEXT = "valuation_context"
GOVERNANCE = "governance"
RISKS = "risks"
CATALYSTS = "catalysts"
THESIS_FIT = "thesis_fit"
COUNTER_THESIS = "counter_thesis"


@dataclass(frozen=True)
class DomainSpec:
    domain: str
    label: str
    #: The role that owns findings in this domain unless a question names another.
    default_owner: str
    #: The reader-facing report section this domain's findings are assembled into.
    report_section: str


DOMAIN_SPECS: tuple[DomainSpec, ...] = (
    DomainSpec(THESIS_FIT, "Thesis fit", "industry_analyst", "thesis_fit"),
    DomainSpec(BUSINESS_MODEL, "Business model", "business_analyst", "business_model"),
    DomainSpec(PRODUCTS_SERVICES, "Products and services", "business_analyst", "operations"),
    DomainSpec(
        CUSTOMERS_END_MARKETS, "Customers and end markets", "business_analyst", "business_model"
    ),
    DomainSpec(OPERATIONS_ASSETS, "Operations and assets", "business_analyst", "operations"),
    DomainSpec(
        INDUSTRY_ECONOMICS, "Industry economics", "industry_analyst", "industry_and_market"
    ),
    DomainSpec(
        COMPETITIVE_POSITION, "Competitive position", "competitive_analyst", "competitive_position"
    ),
    DomainSpec(GROWTH_PIPELINE, "Growth pipeline", "event_analyst", "growth_and_catalysts"),
    DomainSpec(CATALYSTS, "Catalysts", "event_analyst", "growth_and_catalysts"),
    DomainSpec(
        FINANCIAL_CAPACITY, "Financial capacity", "financial_analyst", "financial_capacity"
    ),
    DomainSpec(
        CAPITAL_ALLOCATION, "Capital allocation", "financial_analyst", "financial_capacity"
    ),
    DomainSpec(
        VALUATION_CONTEXT, "Valuation context", "valuation_context_analyst", "valuation_context"
    ),
    DomainSpec(GOVERNANCE, "Governance", "risk_analyst", "risks_and_counter_thesis"),
    DomainSpec(RISKS, "Risks", "risk_analyst", "risks_and_counter_thesis"),
    DomainSpec(COUNTER_THESIS, "Counter-thesis", "risk_analyst", "risks_and_counter_thesis"),
)

DOMAINS: frozenset[str] = frozenset(spec.domain for spec in DOMAIN_SPECS)
_BY_DOMAIN: dict[str, DomainSpec] = {spec.domain: spec for spec in DOMAIN_SPECS}

#: The order sections appear in the professional report. Declared once, here, so the
#: report and the audit trail cannot disagree about it.
REPORT_SECTION_ORDER: tuple[str, ...] = (
    "executive_synthesis",
    "thesis_fit",
    "business_model",
    "industry_and_market",
    "operations",
    "competitive_position",
    "growth_and_catalysts",
    "financial_capacity",
    "valuation_context",
    "risks_and_counter_thesis",
    "sensitivities",
    "what_would_change_the_thesis",
    "evidence_quality_and_gaps",
)


def spec_for(domain: str | None) -> DomainSpec | None:
    return _BY_DOMAIN.get(domain or "")


def default_owner(domain: str | None) -> str | None:
    spec = spec_for(domain)
    return spec.default_owner if spec else None


def report_section_for(domain: str | None) -> str | None:
    spec = spec_for(domain)
    return spec.report_section if spec else None


def label_for(domain: str | None) -> str:
    spec = spec_for(domain)
    return spec.label if spec else (domain or "unclassified")


__all__ = [
    "BUSINESS_MODEL",
    "CAPITAL_ALLOCATION",
    "CATALYSTS",
    "COMPETITIVE_POSITION",
    "COUNTER_THESIS",
    "CUSTOMERS_END_MARKETS",
    "DOMAINS",
    "DOMAIN_SPECS",
    "FINANCIAL_CAPACITY",
    "GOVERNANCE",
    "GROWTH_PIPELINE",
    "INDUSTRY_ECONOMICS",
    "OPERATIONS_ASSETS",
    "PRODUCTS_SERVICES",
    "REPORT_SECTION_ORDER",
    "RISKS",
    "THESIS_FIT",
    "VALUATION_CONTEXT",
    "DomainSpec",
    "default_owner",
    "label_for",
    "report_section_for",
    "spec_for",
]
