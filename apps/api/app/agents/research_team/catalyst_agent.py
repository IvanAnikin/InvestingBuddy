"""
Phase 24 — News / Catalyst Agent (Research Team).

Turns a ``CatalystDiscoveryResult`` into a cautious, source-backed markdown
research summary plus structured context for the Analysis Council.

Tone: internal research only. Source-backed. No investment conclusion, no
recommendation, no price target, no fair value, no upside/downside. Catalyst
direction labels are explicitly flagged as model-derived (T6) and requiring
human review.

The markdown uses the top-level report headings required by Phase 24:
  ## News & Catalyst Discovery
  ## Recent Catalyst Events
  ## SEC Filing Events
  ## Catalyst Evidence Quality
  ## Catalyst Gaps / Next Research Tasks
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field

from app.schemas.catalyst import (
    CatalystCoverageStatus,
    CatalystDirection,
    CatalystDiscoveryResult,
    CatalystEvent,
    EvidenceStrength,
    neutralize_forbidden_terms,
)

_DISCLAIMER = (
    "> **INTERNAL RESEARCH ONLY.** Catalyst categories, directions and strengths "
    "are MODEL-DERIVED labels (T6_model_estimate), not sourced facts. A positive "
    "catalyst is not a reason to act; a negative catalyst is not a reason to act. "
    "No valuation conclusion or trading action is produced. Human review is "
    "required before any use."
)


@dataclass
class CatalystAgentOutput:
    markdown: str
    coverage_status: str
    total_events: int
    positive_count: int
    negative_count: int
    mixed_count: int
    neutral_count: int
    unknown_count: int
    high_strength_count: int
    primary_or_regulator_event_count: int
    aggregator_only_count: int
    latest_event_date: str | None
    # Council context
    bull_context: list[str] = field(default_factory=list)
    bear_context: list[str] = field(default_factory=list)
    risk_flags: list[str] = field(default_factory=list)
    committee_open_questions: list[str] = field(default_factory=list)
    source_quality_recommendations: list[str] = field(default_factory=list)
    next_research_tasks: list[str] = field(default_factory=list)
    human_review_notes: list[str] = field(default_factory=list)
    missing_sources: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def catalyst_agent_output_to_dict(output: CatalystAgentOutput) -> dict:
    return asdict(output)


def _short_link(url: str | None) -> str:
    return f"[source]({url})" if url else "—"


def _clean(text: str | None) -> str:
    return (neutralize_forbidden_terms(text) or "").replace("|", "/").strip()


def _events_table(events: list[CatalystEvent]) -> str:
    if not events:
        return "_No catalyst events found in the lookback window._\n\n"
    lines = [
        "| Date | Tier | Source | Category | Direction | Strength | Headline | Link |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for e in events:
        lines.append(
            "| {date} | {tier} | {src} | {cat} | {dir} | {strg} | {head} | {link} |".format(
                date=e.event_date or e.filing_date or "—",
                tier=e.source_tier,
                src=_clean(e.source_name) or e.provider_name,
                cat=e.catalyst_category,
                dir=e.catalyst_direction,
                strg=e.catalyst_strength,
                head=_clean(e.headline)[:120],
                link=_short_link(e.source_url),
            )
        )
    return "\n".join(lines) + "\n\n"


def _sec_table(events: list[CatalystEvent]) -> str:
    if not events:
        return "_No recent SEC filing events found in the lookback window._\n\n"
    lines = [
        "| Form | Filing Date | Report Date | Items | Category | Initial Direction | Filing |",
        "|---|---|---|---|---|---|---|",
    ]
    for e in events:
        lines.append(
            "| {form} | {fdate} | {rdate} | {items} | {cat} | {dir} | {link} |".format(
                form=e.form_type or e.raw_event_type or "—",
                fdate=e.filing_date or "—",
                rdate=e.report_date or "—",
                items=", ".join(e.item_numbers) if e.item_numbers else "—",
                cat=e.catalyst_category,
                dir=e.catalyst_direction,
                link=_short_link(e.related_filing_url or e.source_url),
            )
        )
    return "\n".join(lines) + "\n\n"


def run_catalyst_agent(result: CatalystDiscoveryResult) -> CatalystAgentOutput:
    """Build the catalyst research summary + council context from a discovery result."""
    s = result.summary
    coverage = result.coverage_quality

    # ── Council context (source-backed, cautious) ────────────────────────
    bull_context: list[str] = []
    bear_context: list[str] = []
    risk_flags: list[str] = []
    committee_open_questions: list[str] = []
    source_quality_recommendations: list[str] = []
    next_research_tasks: list[str] = []
    human_review_notes: list[str] = []

    positive_events = [
        e for e in result.events
        if e.catalyst_direction == CatalystDirection.positive.value
    ]
    negative_events = [
        e for e in result.events
        if e.catalyst_direction in (CatalystDirection.negative.value,)
    ]

    if positive_events:
        bull_context.append(
            f"{len(positive_events)} recent positive catalyst candidate(s) exist "
            "(model-derived label) — requires human validation before use."
        )
    if negative_events:
        bear_context.append(
            f"{len(negative_events)} recent negative/risk catalyst candidate(s) "
            "detected (model-derived label) — requires human validation."
        )

    # Catalyst data-quality risks
    if coverage in (
        CatalystCoverageStatus.none_found.value,
        CatalystCoverageStatus.provider_unavailable.value,
    ):
        risk_flags.append(
            "No recent catalyst coverage — weak/absent catalyst signal is itself a "
            "research risk."
        )
    if coverage == CatalystCoverageStatus.stale.value:
        risk_flags.append(
            "Catalyst coverage is stale (latest event older than the lookback "
            "window) — the 'why now?' question is unresolved."
        )
    if coverage == CatalystCoverageStatus.filings_only.value:
        risk_flags.append(
            "Catalyst coverage is limited to SEC filing metadata; no company "
            "press-release or news context was available."
        )
    if s.aggregator_only_count > 0:
        risk_flags.append(
            f"{s.aggregator_only_count} catalyst(s) rest on aggregator-only "
            "evidence (T5) — not yet confirmed by a primary/regulator source."
        )
    if "company_press_release" in result.missing_sources:
        risk_flags.append(
            "No company-owned press-release source was available (company primary "
            "news source unavailable)."
        )
        source_quality_recommendations.append(
            "Obtain the company's own press-release / investor-relations source "
            "(T1) to confirm catalyst events."
        )

    # Source-quality upgrade recommendations
    if result.filing_events:
        source_quality_recommendations.append(
            "Inspect full SEC filing exhibits (8-K item bodies) — current labels "
            "use filing metadata only."
        )
    if any(e.catalyst_category == "earnings" for e in result.events):
        source_quality_recommendations.append(
            "Obtain the earnings call transcript to confirm earnings-catalyst "
            "direction."
        )
    if s.aggregator_only_count > 0:
        source_quality_recommendations.append(
            "Validate aggregator-only news against a T1/T2 primary source."
        )

    # Committee open questions
    committee_open_questions.append(
        f"Catalyst coverage status is '{coverage}' — is recent-event coverage "
        "sufficient to answer 'why now?' for this company?"
    )
    if positive_events or negative_events:
        committee_open_questions.append(
            "Do the model-derived catalyst directions hold up against the primary "
            "source text? (human review required)"
        )

    # Next research tasks
    if result.filing_events:
        next_research_tasks.append("Review full 8-K exhibits and item bodies.")
    next_research_tasks.append("Verify company press-release / IR source.")
    if any(e.catalyst_category == "earnings" for e in result.events):
        next_research_tasks.append("Review latest earnings release / transcript.")
    next_research_tasks.append("Check analyst / news context for corroboration.")
    next_research_tasks.append("Compare catalysts with sector-peer catalysts.")
    if s.aggregator_only_count > 0:
        next_research_tasks.append(
            "Obtain a primary source for aggregator-only news items."
        )

    human_review_notes.append(
        "All catalyst direction/strength labels are model-derived (T6) and must "
        "be validated against primary sources before any reliance."
    )
    if result.warnings:
        human_review_notes.append(
            "Provider warnings are present — review catalyst coverage gaps below."
        )

    # ── Markdown ──────────────────────────────────────────────────────────
    md: list[str] = []

    md.append("## News & Catalyst Discovery\n")
    md.append(_DISCLAIMER + "\n")
    md.append(
        f"- **Coverage status:** `{coverage}`  \n"
        f"- **Lookback window:** {result.lookback_days} days  \n"
        f"- **Total catalyst events:** {s.total_events}  \n"
        f"- **Direction mix:** positive {s.positive_count} / negative "
        f"{s.negative_count} / mixed {s.mixed_count} / neutral {s.neutral_count} / "
        f"unknown {s.unknown_count}  \n"
        f"- **High-strength events:** {s.high_strength_count}  \n"
        f"- **Primary/regulator-backed events:** "
        f"{s.primary_or_regulator_event_count}  \n"
        f"- **Aggregator-only events:** {s.aggregator_only_count}  \n"
        f"- **Latest event date:** {s.latest_event_date or 'N/A'}  \n"
        f"- **Source coverage:** "
        f"{', '.join(f'{k}×{v}' for k, v in sorted(result.source_summary.items())) or 'none'}  \n"
    )
    if coverage in (
        CatalystCoverageStatus.none_found.value,
        CatalystCoverageStatus.provider_unavailable.value,
    ):
        md.append(
            "\n_No recent catalysts found. Coverage is limited or unavailable "
            "for this company; treat the 'why now?' question as unresolved._\n"
        )
    if result.warnings:
        md.append("\n**Coverage warnings:**\n")
        md.append("\n".join(f"- {neutralize_forbidden_terms(w)}" for w in result.warnings[:10]))
        md.append("")

    md.append("\n## Recent Catalyst Events\n")
    md.append(_events_table(result.events))

    md.append("## SEC Filing Events\n")
    md.append(_sec_table(result.filing_events))

    md.append("## Catalyst Evidence Quality\n")
    primary = [
        e for e in result.events
        if e.evidence_strength in (
            EvidenceStrength.regulator_confirmed.value,
            EvidenceStrength.primary_confirmed.value,
            EvidenceStrength.multi_source_confirmed.value,
        )
    ]
    aggregator = [
        e for e in result.events
        if e.evidence_strength == EvidenceStrength.aggregator_only.value
    ]
    md.append(
        f"- **Primary/regulator-confirmed (T1/T2):** {len(primary)} event(s) — "
        "SEC filings and/or company press releases.  \n"
        f"- **Aggregator-only (T5):** {len(aggregator)} event(s) — unconfirmed by "
        "a primary source.  \n"
        "- **Model-derived:** every catalyst category/direction/strength label is "
        "T6_model_estimate and requires human review.  \n"
    )
    if human_review_notes:
        md.append("\n**Human review notes:**\n")
        md.append("\n".join(f"- {n}" for n in human_review_notes))
        md.append("")
    if result.missing_sources:
        md.append("\n**Missing catalyst sources:**\n")
        md.append("\n".join(f"- `{m}`" for m in result.missing_sources))
        md.append("")

    md.append("\n## Catalyst Gaps / Next Research Tasks\n")
    md.append("\n".join(f"- {t}" for t in next_research_tasks))
    md.append("")

    markdown = "\n".join(md) + "\n"

    return CatalystAgentOutput(
        markdown=markdown,
        coverage_status=coverage,
        total_events=s.total_events,
        positive_count=s.positive_count,
        negative_count=s.negative_count,
        mixed_count=s.mixed_count,
        neutral_count=s.neutral_count,
        unknown_count=s.unknown_count,
        high_strength_count=s.high_strength_count,
        primary_or_regulator_event_count=s.primary_or_regulator_event_count,
        aggregator_only_count=s.aggregator_only_count,
        latest_event_date=s.latest_event_date,
        bull_context=bull_context,
        bear_context=bear_context,
        risk_flags=risk_flags,
        committee_open_questions=committee_open_questions,
        source_quality_recommendations=source_quality_recommendations,
        next_research_tasks=next_research_tasks,
        human_review_notes=human_review_notes,
        missing_sources=list(result.missing_sources),
        warnings=list(result.warnings),
    )
