"""
Source registry + connector framework — Phase 29A.

A unified way to describe *where evidence comes from* and *how to fetch it*,
built before wiring the long tail of external sources one by one. This package
provides:

  taxonomy         — canonical source tiers (transport vs content), provider
                     types, cost/access/status vocabularies.
  evidence         — ``EvidenceSource`` / ``EvidenceItem`` framework models with
                     tier validation + URL secret-stripping.
  connector_base   — the ``SourceConnector`` interface + safe result/health
                     shapes; a connector failure can never crash a run.
  registry         — the code-defined catalogue: enabled (migrated) sources +
                     planned placeholders for future phases.
  gaps             — normalized ``SourceGap`` reporting for missing coverage.
  rate_limit/cache — connector-politeness groundwork (not wired live in 29A).

Phase 29A does NOT connect every source and does NOT change the live report /
discovery evidence flow — the councils still read the existing deterministic
evidence packs. Connectors land per-source in Phase 29B+.
"""

from app.services.sources.company_evidence import (
    CompanySourceEvidence,
    collect_company_source_evidence,
    press_items_from_catalyst,
    sec_filings_from_catalyst,
)
from app.services.sources.connector_base import (
    CompanyContext,
    ConnectorHealth,
    ConnectorResult,
    QueryContext,
    SourceConnector,
)
from app.services.sources.errors import ConnectorError, ConnectorErrorCode
from app.services.sources.evidence import (
    EvidenceItem,
    EvidenceSource,
    build_evidence_item,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap
from app.services.sources.macro_evidence import (
    ThemeMacroEvidence,
    collect_theme_macro_evidence,
)
from app.services.sources.registry import (
    RegisteredSource,
    SourceRegistry,
    assert_registry_safe,
    build_registry,
    registry_gap_messages,
    tier_legend,
)
from app.services.sources.taxonomy import (
    CANONICAL_TIERS,
    AccessMode,
    ConnectorStatus,
    CostModel,
    ProviderType,
    SourceStatus,
    SourceTier,
    is_valid_tier,
    sec_tier_pair,
)

__all__ = [
    # taxonomy
    "SourceTier",
    "CANONICAL_TIERS",
    "ProviderType",
    "CostModel",
    "AccessMode",
    "SourceStatus",
    "ConnectorStatus",
    "is_valid_tier",
    "sec_tier_pair",
    # evidence
    "EvidenceSource",
    "EvidenceItem",
    "build_evidence_item",
    # connectors
    "SourceConnector",
    "CompanyContext",
    "QueryContext",
    "ConnectorHealth",
    "ConnectorResult",
    "ConnectorError",
    "ConnectorErrorCode",
    # company evidence (Phase 29B)
    "CompanySourceEvidence",
    "collect_company_source_evidence",
    "sec_filings_from_catalyst",
    "press_items_from_catalyst",
    # macro evidence (Phase 29C.1)
    "ThemeMacroEvidence",
    "collect_theme_macro_evidence",
    # gaps
    "SourceGap",
    "GapType",
    "GapSeverity",
    # registry
    "RegisteredSource",
    "SourceRegistry",
    "build_registry",
    "tier_legend",
    "assert_registry_safe",
    "registry_gap_messages",
]
