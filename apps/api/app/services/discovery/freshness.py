"""May this report be read as CURRENT research? — V3.19.3.

THE DEFECT
==========
``current_research_resolver`` returned a company's newest report carrying a
``final_report_version`` and a parseable content block, and handed it to the discovery
council as the company's current research. A 2026-08 V2 Pandora report — thin business
analysis, no external research, a naive leverage reading — and a V3.18 professional report
were indistinguishable to it. A new discovery run could therefore conclude from research
the platform itself no longer produces.

THE CONTRACT
============
Every report gets a freshness class:

``legacy``      no V3 professional-research block: produced by V1/V2, or by V3 before
                V3.18. It is HISTORY. It may be shown with its date; it is never given to
                a council as current evidence.
``v3_current``  a V3 professional report researched within ``fresh_days`` (default 120)
                and not superseded by a newer annual period the caller knows about.
``v3_stale``    a V3 professional report that is older than that, or superseded. It may be
                given as DATED context, labelled stale — never as current.

Nothing here mutates a report. Old reports stay exactly as they were accepted.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Any

LEGACY = "legacy"
V3_CURRENT = "v3_current"
V3_STALE = "v3_stale"

#: Stamped on every V3 research block from V3.19 on. A block without it but with a
#: professional report was written by V3.18.
ENGINE_VERSION = "v3.19"
INFERRED_V3_18 = "v3.18"

DEFAULT_FRESH_DAYS = 120


@dataclass(frozen=True)
class ResearchFreshness:
    status: str
    research_engine_version: str
    research_depth: str
    report_id: str | None
    researched_at: str | None
    evidence_as_of: str | None
    age_days: int | None
    reason: str

    @property
    def is_current(self) -> bool:
        return self.status == V3_CURRENT

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _aware(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def v3_block(report: Any) -> dict[str, Any] | None:
    summary = getattr(report, "source_summary_json", None)
    if not isinstance(summary, dict):
        return None
    block = summary.get("v3_research")
    return block if isinstance(block, dict) else None


def engine_version_of(report: Any) -> tuple[str, str]:
    """``(engine version, research depth)`` for a report, from what it actually carries."""
    block = v3_block(report)
    if block is None:
        depth = "standard" if getattr(report, "final_report_version", None) else "screening"
        return "legacy", depth
    professional = block.get("professional_research")
    stamped = block.get("research_engine_version")
    if isinstance(professional, dict) and professional:
        return (str(stamped) if stamped else INFERRED_V3_18), "professional"
    # A V3 block without a professional report (pre-V3.18, or its assembly was withheld)
    # is not professional-depth research, whatever else it carries.
    return (str(stamped) if stamped else "v3_pre_professional"), "standard"


def _evidence_as_of(report: Any) -> str | None:
    from app.services.current_research_resolver import extract_report_content

    content = extract_report_content(getattr(report, "content_markdown", None))
    if not isinstance(content, dict):
        return None
    snapshot = content.get("financial_snapshot")
    periods = snapshot.get("reporting_periods") if isinstance(snapshot, dict) else None
    if not isinstance(periods, dict):
        return None
    for key in ("latest_current_period", "latest_annual"):
        value = periods.get(key)
        if isinstance(value, dict):
            value = value.get("value")
        if isinstance(value, str) and value.strip():
            return value.strip()[:40]
    return None


def classify_report(
    report: Any,
    *,
    now: datetime | None = None,
    fresh_days: int = DEFAULT_FRESH_DAYS,
    newer_annual_period: str | None = None,
    with_evidence: bool = True,
) -> ResearchFreshness:
    """The freshness class of ONE report. Never raises on content."""
    now = _aware(now) or datetime.now(timezone.utc)
    created = _aware(getattr(report, "created_at", None))
    age_days = (now - created).days if created else None
    version, depth = engine_version_of(report)
    report_id = str(getattr(report, "id", "") or "") or None
    # ``with_evidence=False`` never touches the report body: a caller that loaded only
    # the freshness columns (the discovery page) must not trigger a lazy load.
    evidence_as_of = _evidence_as_of(report) if with_evidence else None
    base: dict[str, Any] = {
        "research_engine_version": version,
        "research_depth": depth,
        "report_id": report_id,
        "researched_at": created.isoformat() if created else None,
        "evidence_as_of": evidence_as_of,
        "age_days": age_days,
    }
    if depth != "professional":
        return ResearchFreshness(
            status=LEGACY,
            reason=(
                "produced before V3 professional research — historical only, never current "
                "evidence"
            ),
            **base,
        )
    if age_days is None:
        return ResearchFreshness(status=V3_STALE, reason="research date unknown", **base)
    if age_days > fresh_days:
        return ResearchFreshness(
            status=V3_STALE,
            reason=f"researched {age_days} days ago (fresh window {fresh_days} days)",
            **base,
        )
    if newer_annual_period and evidence_as_of and newer_annual_period not in evidence_as_of:
        return ResearchFreshness(
            status=V3_STALE,
            reason=(
                f"a newer annual period ({newer_annual_period}) is known than the report's "
                f"evidence ({evidence_as_of})"
            ),
            **base,
        )
    return ResearchFreshness(
        status=V3_CURRENT,
        reason=f"V3 professional research, {age_days} days old",
        **base,
    )


def fresh_days_from(cfg: Any) -> int:
    value = getattr(cfg, "v3_research_fresh_days", None)
    try:
        return max(1, int(value)) if value else DEFAULT_FRESH_DAYS
    except (TypeError, ValueError):
        return DEFAULT_FRESH_DAYS


__all__ = [
    "DEFAULT_FRESH_DAYS",
    "ENGINE_VERSION",
    "LEGACY",
    "ResearchFreshness",
    "V3_CURRENT",
    "V3_STALE",
    "classify_report",
    "engine_version_of",
    "fresh_days_from",
    "v3_block",
]
