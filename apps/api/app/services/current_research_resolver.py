"""Which report is a company's CURRENT research, and what does it actually say?

WHY THIS MODULE EXISTS
======================
The run-level discovery council was given, per candidate, a screening row: some
scores, a source-quality word, a missing-field count and a blocking-gap count.
Nothing about the business. A live European Luxury run therefore concluded that
"all candidates lack sourced fundamentals, no filings, no SEC eligibility, no
catalysts — prioritize mainly using momentum", which is an evidence audit
wearing the clothes of investment research.

That was not the council's fault. Two of those candidates — Pandora and
Richemont — already had complete structured research on this very database, run
by this very platform, with period-labelled revenue, margins, cash generation,
net debt/net cash, a chair synthesis and a resilience assessment. The council
was never shown any of it.

This module reads it, and reads it under the SAME rules the reader-facing UI
uses so the two can never disagree about what "current research" means:

  1. A report belongs to a company via ``reports.company_id`` (migration 012).
     Never a ticker match, never a title match, never the global newest report.
  2. A report is STRUCTURED RESEARCH only when it carries a
     ``final_report_version`` AND a parseable ``report_content`` JSON block.
     A discovery-time Phase-9 draft has neither, and ``analysis_report_id``
     points at exactly that on a freshly screened candidate — which is why
     that column is not consulted here at all.
  3. CURRENT means newest for THAT company by ``(created_at DESC, id DESC)``,
     the ordering ``generate_from_company`` already uses.

Nothing is fetched, nothing is generated and no analysis is run: every value
returned here was already persisted by a completed research run. When a company
has no current structured research the answer is an empty signal set, which
downstream reads as "not established" — never as a substituted gap count.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report

#: How many of a company's reports to consider when resolving the current one.
#: Ordered newest-first, so the current structured report is at the front for
#: any company that has one; the window only bounds a pathological history.
_COHORT_LIMIT = 25

_JSON_BLOCK = re.compile(r"```json\s*(.*?)\s*```", re.DOTALL)

#: Report-content metrics worth carrying into a candidate comparison, with the
#: dimension each one speaks to. Deliberately small: the pack is bounded and a
#: comparison of thirty numbers is not a comparison.
_SNAPSHOT_METRICS: tuple[tuple[str, str], ...] = (
    ("revenue_usd_m", "revenue"),
    ("operating_income_usd_m", "operating profit"),
    ("net_income_usd_m", "net income"),
    ("operating_cash_flow_usd_m", "operating cash flow"),
    ("free_cash_flow_usd_m", "free cash flow"),
    ("total_debt_usd_m", "total debt"),
    ("cash_and_equivalents_usd_m", "cash and equivalents"),
)

_TEXT_MAX = 240
_MAX_POINTS = 3


def _clip(value: Any, limit: int = _TEXT_MAX) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def extract_report_content(markdown: str | None) -> dict[str, Any] | None:
    """The structured ``report_content`` block, or None.

    Mirrors the web client's ``extractFinalReportContent`` exactly: the
    final-report generator writes ONE fenced ```json block, and a report whose
    block is absent or unparseable is not structured research however it is
    versioned.
    """
    if not markdown:
        return None
    match = _JSON_BLOCK.search(markdown)
    if match is None:
        return None
    try:
        parsed = json.loads(match.group(1))
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def is_structured_research_report(report: Report) -> bool:
    """True when this report is a full structured research report.

    Both halves are required, and both are the backend's own semantics: the
    generator always stamps ``final_report_version`` (a NULL version is a
    legacy Phase-9 draft), and a report the pipeline never wrote structured
    content for cannot be read as research whatever its version says.
    """
    if not report.final_report_version:
        return False
    return extract_report_content(report.content_markdown) is not None


async def resolve_current_research_report(
    db: AsyncSession, company_id: uuid.UUID | None
) -> Report | None:
    """The company's CURRENT structured research report, or None.

    Company-scoped and deterministic. Returns None — never another company's
    report, never a screening draft — when the company has no structured
    research yet.
    """
    if company_id is None:
        return None
    rows = (
        await db.execute(
            select(Report)
            .where(Report.company_id == company_id)
            .order_by(Report.created_at.desc(), Report.id.desc())
            .limit(_COHORT_LIMIT)
        )
    ).scalars()
    for report in rows:
        if is_structured_research_report(report):
            return report
    return None


# ---------------------------------------------------------------------------
# What the current research SAYS
# ---------------------------------------------------------------------------


def _unwrap(field_value: Any) -> Any:
    """Unwrap a ``{value, provenance, …}`` envelope, or pass a bare value."""
    if isinstance(field_value, dict) and "value" in field_value:
        return field_value["value"]
    return field_value


def _text_list(value: Any, limit: int = _MAX_POINTS) -> list[str]:
    raw = _unwrap(value)
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list):
        return []
    out: list[str] = []
    for item in raw:
        text = _clip(item if not isinstance(item, dict) else item.get("value"))
        if text:
            out.append(text)
        if len(out) >= limit:
            break
    return out


def _datapoint_phrase(dp: Any, label: str) -> str | None:
    """One financial datapoint as the sentence the report itself would render.

    Values are quoted exactly as extracted — the number, its scale word, its
    currency and its period. Nothing is rescaled, converted or recomputed here;
    a comparison built on a silently rescaled figure is worse than no
    comparison.
    """
    if not isinstance(dp, dict):
        return None
    numeric = dp.get("numeric_value")
    if not isinstance(numeric, (int, float)):
        numeric = dp.get("value")
    if not isinstance(numeric, (int, float)):
        return None
    parts = [f"{numeric:,.0f}" if abs(numeric) >= 100 else f"{numeric:,.2f}"]
    unit = dp.get("unit")
    scale = dp.get("scale")
    currency = dp.get("currency")
    if unit == "%":
        parts = [f"{numeric:g}%"]
    else:
        if isinstance(scale, str) and scale:
            parts.append({"million": "m", "billion": "bn"}.get(scale, scale))
        if isinstance(currency, str) and currency:
            parts.append(currency)
    period = dp.get("period")
    scope = dp.get("scope")
    phrase = f"{label} {' '.join(parts)}"
    if isinstance(scope, str) and scope and scope.lower() != "group":
        phrase += f" ({scope})"
    if isinstance(period, str) and period:
        phrase += f" [{period}]"
    return _clip(phrase, 120)


@dataclass(frozen=True)
class CandidateResearchSignals:
    """Bounded economic signals lifted from ONE company's current research.

    Every field is either something a completed research run persisted, or
    absent. Absent means "not established" — it is never filled with an
    evidence-completeness figure standing in for an economic one.
    """

    report_id: str | None = None
    report_created_at: str | None = None
    latest_annual_period: str | None = None
    latest_current_period: str | None = None
    annual_figures: list[str] = field(default_factory=list)
    current_period_figures: list[str] = field(default_factory=list)
    business_quality: str | None = None
    fundamental_setup: str | None = None
    strongest_positive: list[str] = field(default_factory=list)
    strongest_negative: list[str] = field(default_factory=list)
    resilience_factors: list[str] = field(default_factory=list)
    fragility_factors: list[str] = field(default_factory=list)
    company_risks: list[str] = field(default_factory=list)
    catalysts: list[str] = field(default_factory=list)
    research_confidence: str | None = None
    council_agents_completed: int | None = None

    @property
    def available(self) -> bool:
        return self.report_id is not None

    def to_dict(self) -> dict[str, Any]:
        """Compact JSON form — keys with no value are dropped entirely.

        A key that is present but empty invites the council to read "" as a
        finding. An absent key reads as "not established", which is what it is.
        """
        raw: dict[str, Any] = {
            "current_research_report_id": self.report_id,
            "current_research_as_of": self.report_created_at,
            "latest_annual_period": self.latest_annual_period,
            "latest_current_period": self.latest_current_period,
            "annual_figures": self.annual_figures,
            "current_period_figures": self.current_period_figures,
            "business_quality": self.business_quality,
            "fundamental_setup": self.fundamental_setup,
            "strongest_positive_evidence": self.strongest_positive,
            "strongest_negative_evidence": self.strongest_negative,
            "resilience_factors": self.resilience_factors,
            "fragility_factors": self.fragility_factors,
            "company_risks": self.company_risks,
            "catalysts": self.catalysts,
            "research_confidence": self.research_confidence,
            "council_agents_completed": self.council_agents_completed,
        }
        return {k: v for k, v in raw.items() if v not in (None, "", [], {})}


NO_RESEARCH_SIGNALS = CandidateResearchSignals()


def build_research_signals(report: Report | None) -> CandidateResearchSignals:
    """Lift bounded economic signals out of ONE current research report.

    Reads only fields a completed run persisted. Never derives a growth rate, a
    margin or a trend that the report does not itself carry — the council is
    given the report's own figures and the council's own prior conclusions, and
    it does the reasoning.
    """
    if report is None:
        return NO_RESEARCH_SIGNALS
    content = extract_report_content(report.content_markdown)
    if content is None:
        return NO_RESEARCH_SIGNALS

    snapshot = content.get("financial_snapshot")
    snapshot = snapshot if isinstance(snapshot, dict) else {}
    periods = snapshot.get("reporting_periods")
    periods = periods if isinstance(periods, dict) else {}

    annual: list[str] = []
    current: list[str] = []
    for key, label in _SNAPSHOT_METRICS:
        phrase = _datapoint_phrase(snapshot.get(f"{key}_primary_filing"), label)
        if phrase:
            annual.append(phrase)
        phrase = _datapoint_phrase(snapshot.get(f"{key}_current_period"), label)
        if phrase:
            current.append(phrase)

    chair = content.get("committee_chair_summary")
    chair = chair if isinstance(chair, dict) else {}

    risk = content.get("risk_analysis")
    risk = risk if isinstance(risk, dict) else {}
    company_risks: list[str] = []
    for slot in ("business_risks", "financial_risks", "market_risks"):
        company_risks.extend(_text_list(risk.get(slot), limit=_MAX_POINTS))
        if len(company_risks) >= _MAX_POINTS:
            break

    evidence = content.get("evidence_quality")
    evidence = evidence if isinstance(evidence, dict) else {}

    # The council's own synthesis, when the report carries one. This is the
    # research platform's prior conclusion about the business — the single most
    # useful thing a discovery comparison can be given.
    synthesis = _council_synthesis(report)

    return CandidateResearchSignals(
        report_id=str(report.id),
        report_created_at=report.created_at.isoformat() if report.created_at else None,
        latest_annual_period=_clip(_unwrap(periods.get("latest_annual")), 40),
        latest_current_period=_clip(
            _unwrap(periods.get("latest_current_period")), 40
        ),
        annual_figures=annual[:5],
        current_period_figures=current[:5],
        business_quality=_clip(_unwrap(chair.get("committee_summary"))),
        fundamental_setup=_clip(synthesis.get("fundamental_setup"), 40),
        strongest_positive=_text_list(synthesis.get("strongest_positive_evidence")),
        strongest_negative=_text_list(synthesis.get("strongest_negative_evidence")),
        resilience_factors=_text_list(synthesis.get("resilience_factors")),
        fragility_factors=_text_list(synthesis.get("fragility_factors")),
        company_risks=company_risks[:_MAX_POINTS],
        catalysts=_text_list(
            (content.get("news_catalyst_discovery") or {}).get("catalyst_summaries")
            if isinstance(content.get("news_catalyst_discovery"), dict)
            else None
        ),
        research_confidence=_clip(
            _unwrap(evidence.get("overall_evidence_quality")), 40
        ),
        council_agents_completed=_council_agents_completed(report),
    )


def _llm_council(report: Report) -> dict[str, Any]:
    summary = report.source_summary_json
    if not isinstance(summary, dict):
        return {}
    council = summary.get("llm_council")
    return council if isinstance(council, dict) else {}


def _council_agents_completed(report: Report) -> int | None:
    council = _llm_council(report)
    value = council.get("agents_completed")
    return value if isinstance(value, int) else None


def _council_synthesis(report: Report) -> dict[str, Any]:
    """The committee chair's structured synthesis, or an empty dict."""
    council = _llm_council(report)
    agents = council.get("agents")
    if not isinstance(agents, list):
        return {}
    for agent in agents:
        if not isinstance(agent, dict):
            continue
        if agent.get("agent_name") != "committee_chair":
            continue
        synthesis = agent.get("synthesis")
        if isinstance(synthesis, dict):
            return synthesis
    return {}


async def research_signals_for_company(
    db: AsyncSession, company_id: uuid.UUID | None
) -> CandidateResearchSignals:
    """Resolve + read one company's current research in a single call."""
    report = await resolve_current_research_report(db, company_id)
    return build_research_signals(report)
