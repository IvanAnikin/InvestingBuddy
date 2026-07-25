"""
Source gaps — Phase 29A.

A normalized way to say "the evidence you'd want here is not available, and
here's why". Gaps make missing coverage *visible and honest* instead of silently
absent: a planned connector, an un-implemented jurisdiction, a primary filing
that could not be retrieved, or content that needs a translation agent that does
not exist yet.

Gaps are surfaced to the source critic (evidence-pack ``known_gaps``), the final
report source summary, and the registry API. Messages here are deliberately
recommendation-free and never contain any rating / price-target vocabulary, so
they pass the report safety gate unchanged.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class GapType(str, Enum):
    connector_not_implemented = "connector_not_implemented"
    connector_planned = "connector_planned"
    connector_scaffolded = "connector_scaffolded"
    connector_disabled = "connector_disabled"
    connector_error = "connector_error"
    data_not_sourced = "data_not_sourced"
    primary_filing_unavailable = "primary_filing_unavailable"
    source_not_eligible = "source_not_eligible"
    translation_required = "translation_required"


class GapSeverity(str, Enum):
    info = "info"
    low = "low"
    medium = "medium"
    high = "high"


class SourceGap(BaseModel):
    """One normalized, safety-clean statement of missing source coverage."""

    source_id: str | None = None
    connector_key: str | None = None
    gap_type: GapType
    severity: GapSeverity = GapSeverity.info
    message: str
    suggested_followup_phase: str | None = None
    # A gap only blocks research-complete when it removes evidence a report
    # genuinely needs. Planned future connectors are informational, not blocking.
    blocks_research_complete: bool = False

    def as_message(self) -> str:
        """A compact single-line form for evidence-pack ``known_gaps``."""
        phase = (
            f" (planned: {self.suggested_followup_phase})"
            if self.suggested_followup_phase
            else ""
        )
        return f"{self.message}{phase}"


def planned_connector_gap(
    *,
    source_id: str,
    name: str,
    connector_key: str | None,
    phase: str | None,
) -> SourceGap:
    """Build the informational gap for a planned (not-yet-implemented) source."""
    return SourceGap(
        source_id=source_id,
        connector_key=connector_key,
        gap_type=GapType.connector_planned,
        severity=GapSeverity.info,
        message=f"{name} connector is planned but not implemented yet.",
        suggested_followup_phase=phase,
        blocks_research_complete=False,
    )


def disabled_connector_gap(*, source_id: str, name: str, connector_key: str | None) -> SourceGap:
    return SourceGap(
        source_id=source_id,
        connector_key=connector_key,
        gap_type=GapType.connector_disabled,
        severity=GapSeverity.low,
        message=f"{name} connector is implemented but currently disabled.",
        blocks_research_complete=False,
    )


def scaffolded_connector_gap(
    *,
    source_id: str,
    name: str,
    connector_key: str | None,
    phase: str | None,
    for_issuer: bool = False,
) -> SourceGap:
    """Honest gap for a scaffolded connector (class exists, no live fetch yet).

    Never implies data was fetched — a scaffolded connector fabricates nothing.
    """
    suffix = " for this issuer" if for_issuer else ""
    return SourceGap(
        source_id=source_id,
        connector_key=connector_key,
        gap_type=GapType.connector_scaffolded,
        severity=GapSeverity.info,
        message=f"{name} connector scaffold present; live fetch pending{suffix}.",
        suggested_followup_phase=phase,
        blocks_research_complete=False,
    )


__all__ = [
    "GapType",
    "GapSeverity",
    "SourceGap",
    "planned_connector_gap",
    "disabled_connector_gap",
    "scaffolded_connector_gap",
]
