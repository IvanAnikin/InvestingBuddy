"""
Theme event-evidence collector — Phase 29D.1 (procurement) + 29D.2 (patents).

The event analog of ``collect_theme_macro_evidence``: given a discovery theme
(and optional region) it runs the reference-only event connectors (procurement /
tender venues plus patent office / index venues) and returns bounded, tiered
``EvidenceItem`` **source references** plus honest ``SourceGap``s ("live tenders /
awards / patent filings not fetched at report time"). It never fetches and never
fabricates a tender, award, contractor, amount, contract number, date, or any
patent number / inventor / assignee / claim; a patent reference additionally
draws no legal / infringement / validity conclusion. Each reference is a WEAK
internal research-priority signal (``needs_human_review``), never a materiality
claim or trade signal.

Design guarantees:
  * **Dark by default.** When ``cfg.source_event_enabled`` is False the collector
    returns completely empty (no evidence, no gaps) — the event layer is off, so
    CI and the existing behaviour are unchanged. It is independent of
    ``source_macro_enabled``.
  * **No network / no keys.** Every event connector is reference-only; the report
    path makes no procurement call. No API key is ever introduced.
  * **Bounded.** At most ``source_event_max_items`` (or the caller's override)
    references are returned across all event sources.
  * **Never raises.** Each connector call goes through ``call_safe``.

This module only *collects*; wiring these references into the discovery council
and the company report is Phase 29D.1 Task 2.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.connector_base import QueryContext
from app.services.sources.connectors.event_reference import (
    ALL_EVENT_SOURCES,
    EventReferenceConnector,
)
from app.services.sources.evidence import EvidenceItem
from app.services.sources.gaps import SourceGap
from app.services.sources.registry import SourceRegistry, build_registry


class ThemeEventEvidence(BaseModel):
    """Everything the procurement / tender event layer produced for one theme."""

    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    source_gaps: list[SourceGap] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def gap_messages(self) -> list[str]:
        """Compact, de-duplicated gap strings for an evidence pack's known_gaps."""
        seen: dict[str, None] = {}
        for g in self.source_gaps:
            seen.setdefault(g.as_message(), None)
        return list(seen)


async def collect_theme_event_evidence(
    theme: str | None,
    region: str | None = None,
    cfg: Settings | None = None,
    *,
    max_items: int | None = None,
    registry: SourceRegistry | None = None,
) -> ThemeEventEvidence:
    """Collect bounded procurement / tender source references + honest gaps.

    Returns empty (dark) unless ``cfg.source_event_enabled`` is True. When on, it
    builds a ``QueryContext`` from ``theme`` / ``region`` and calls each relevant
    event connector's ``fetch_events`` via ``call_safe``, collecting the reference
    ``EvidenceItem``s and honest ``SourceGap``s, capped at ``max_items``
    (defaulting to ``cfg.source_event_max_items``).
    """
    cfg = cfg or default_settings
    if not cfg.source_event_enabled:
        # Event layer disabled — fully dark: no evidence, no gaps.
        return ThemeEventEvidence()

    cap = max_items if max_items is not None else cfg.source_event_max_items
    cap = max(1, cap)
    registry = registry or build_registry(cfg)
    query = QueryContext(query=theme, region=region, max_items=cap)

    items: list[EvidenceItem] = []
    gaps: list[SourceGap] = []
    warnings: list[str] = []

    for spec in ALL_EVENT_SOURCES:
        if len(items) >= cap:
            break
        conn = registry.connectors().get(spec.source_id)
        if not isinstance(conn, EventReferenceConnector):
            continue
        res = await conn.call_safe(conn.fetch_events, query)
        items.extend(res.evidence_items)
        gaps.extend(res.source_gaps)
        warnings.extend(res.warnings)

    return ThemeEventEvidence(
        evidence_items=items[:cap], source_gaps=gaps, warnings=warnings
    )


__all__ = ["ThemeEventEvidence", "collect_theme_event_evidence"]
