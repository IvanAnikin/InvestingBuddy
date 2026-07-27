"""
Theme macro-evidence collector — Phase 29C.1 (macro) + 29C.2 (commodity / energy).

The macro analog of ``collect_company_source_evidence``: given a discovery theme
(and optional region) it runs the reference-only macro connectors and returns
bounded, tiered ``EvidenceItem`` **source references** plus honest ``SourceGap``s
("live figures not fetched at report time"). It never fetches and never
fabricates a macro number. It iterates the full ``ALL_MACRO_SOURCES`` table, so
the 29C.2 commodity / energy references (USGS, IEA, IRENA, US EIA, ENTSO-E)
surface automatically for a relevant commodity / energy theme.

Design guarantees:
  * **Dark by default.** When ``cfg.source_macro_enabled`` is False the collector
    returns completely empty (no evidence, no gaps) — the macro layer is off, so
    CI and the Phase 29A/29B behaviour are unchanged.
  * **No network / no keys.** Every macro connector is reference-only; the report
    path makes no macro call. FRED-style API keys are never introduced.
  * **Bounded.** At most ``source_macro_max_items`` (or the caller's override)
    references are returned across all macro sources.
  * **Never raises.** Each connector call goes through ``call_safe``.

This module only *collects*; wiring these references into the discovery council
and the company-report macro block is Phase 29C.1 Task 2.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.services.sources.connector_base import QueryContext
from app.services.sources.connectors.macro_reference import ALL_MACRO_SOURCES
from app.services.sources.evidence import EvidenceItem
from app.services.sources.gaps import SourceGap
from app.services.sources.registry import SourceRegistry, build_registry


class ThemeMacroEvidence(BaseModel):
    """Everything the macro reference layer produced for one theme / region."""

    evidence_items: list[EvidenceItem] = Field(default_factory=list)
    source_gaps: list[SourceGap] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    def gap_messages(self) -> list[str]:
        """Compact, de-duplicated gap strings for an evidence pack's known_gaps."""
        seen: dict[str, None] = {}
        for g in self.source_gaps:
            seen.setdefault(g.as_message(), None)
        return list(seen)


async def collect_theme_macro_evidence(
    theme: str | None,
    region: str | None = None,
    cfg: Settings | None = None,
    *,
    max_items: int | None = None,
    registry: SourceRegistry | None = None,
) -> ThemeMacroEvidence:
    """Collect bounded macro source references + honest gaps for one theme.

    Returns empty (dark) unless ``cfg.source_macro_enabled`` is True. When on, it
    builds a ``QueryContext`` from ``theme`` / ``region`` and calls each relevant
    macro connector's ``fetch_macro_context`` via ``call_safe``, collecting the
    reference ``EvidenceItem``s and honest ``SourceGap``s, capped at
    ``max_items`` (defaulting to ``cfg.source_macro_max_items``).
    """
    cfg = cfg or default_settings
    if not cfg.source_macro_enabled:
        # Macro layer disabled — fully dark: no evidence, no gaps.
        return ThemeMacroEvidence()

    cap = max_items if max_items is not None else cfg.source_macro_max_items
    cap = max(1, cap)
    registry = registry or build_registry(cfg)
    query = QueryContext(query=theme, region=region, max_items=cap)

    items: list[EvidenceItem] = []
    gaps: list[SourceGap] = []
    warnings: list[str] = []

    for spec in ALL_MACRO_SOURCES:
        if len(items) >= cap:
            break
        conn = registry.connectors().get(spec.source_id)
        if conn is None:
            continue
        res = await conn.call_safe(conn.fetch_macro_context, query)
        items.extend(res.evidence_items)
        gaps.extend(res.source_gaps)
        warnings.extend(res.warnings)

    return ThemeMacroEvidence(
        evidence_items=items[:cap], source_gaps=gaps, warnings=warnings
    )


__all__ = ["ThemeMacroEvidence", "collect_theme_macro_evidence"]
