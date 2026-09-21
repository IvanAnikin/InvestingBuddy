"""The originating thesis, carried into deep research — V3.18.8.

THE DEFECT
==========
SCCO reached deep research because a discovery run asked for *"small-cap mining and
critical-materials companies exposed to semiconductor, AI hardware and EV supply
chains"*. The deep report never mentioned any of that. The thesis text reached two report
sections as metadata and NO prompt at any layer; on the escalation path the job payload
carried no discovery ids at all. So the platform researched a company without knowing why,
and could not say whether the reason held.

WHAT THIS DOES
==============
* Resolves the thesis from the discovery candidate: the user's own words, the parsed
  themes, the match reason and the discovery council's rationale for this candidate.
* Derives the thesis DIMENSIONS the text names (critical materials, electrification, EVs,
  AI / data centres, semiconductors, size …) from a closed vocabulary — never from a
  model, and never assuming a dimension the text did not name.
* Turns each dimension into a ``thesis_fit`` question the research must answer with
  evidence — and whose honest answer may be "weak" or "none". A thesis is tested, not
  confirmed.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ThesisDimension:
    key: str
    label: str
    patterns: tuple[str, ...]
    #: What evidence of exposure looks like, for the question text.
    exposure: str


THESIS_DIMENSIONS: tuple[ThesisDimension, ...] = (
    ThesisDimension(
        "critical_materials", "critical materials / critical minerals",
        (r"critical[\s-]+(?:materials?|minerals?)", r"strategic minerals?"),
        "whether its products are on official critical-mineral lists and how concentrated "
        "their supply is",
    ),
    ThesisDimension(
        "electrification", "electrification and power grids",
        (r"electrif\w*", r"power grids?", r"\bgrid\b", r"transmission"),
        "how much of the demand for its products comes from grids, wiring and power "
        "equipment",
    ),
    ThesisDimension(
        "electric_vehicles", "electric vehicles and batteries",
        (r"\bevs?\b", r"electric vehicles?", r"batter(?:y|ies)"),
        "how much of the demand for its products comes from EVs and batteries",
    ),
    ThesisDimension(
        "ai_data_centres", "AI hardware and data centres",
        (r"\bai\b", r"artificial intelligence", r"data[\s-]?cent(?:er|re)s?",
         r"ai hardware"),
        "whether and how much its products are used in data-centre and AI hardware "
        "build-out",
    ),
    ThesisDimension(
        "semiconductors", "semiconductor supply chains",
        (r"semiconductors?", r"\bchips?\b", r"wafers?"),
        "whether its products are inputs to semiconductor manufacturing, and how much",
    ),
    ThesisDimension(
        "defence", "defence and security supply chains",
        (r"defen[cs]e", r"military", r"national security"),
        "whether it supplies defence programmes or benefits from security-driven policy",
    ),
    ThesisDimension(
        "renewables", "renewable energy",
        (r"renewables?", r"solar", r"wind power", r"clean energy"),
        "how much of the demand for its products comes from renewable generation",
    ),
)

#: Size bands a thesis may name, by market capitalisation in USD. Declared thresholds,
#: so "does SCCO fit a small-cap thesis?" is a comparison anyone can check.
SIZE_BANDS: dict[str, tuple[float | None, float | None]] = {
    "micro_cap": (None, 300e6),
    "small_cap": (300e6, 2e9),
    "mid_cap": (2e9, 10e9),
    "large_cap": (10e9, None),
}


@dataclass
class ThesisContext:
    discovery_run_id: str | None = None
    discovery_candidate_id: str | None = None
    thesis_text: str | None = None
    themes: list[str] = field(default_factory=list)
    industries: list[str] = field(default_factory=list)
    regions: list[str] = field(default_factory=list)
    size_hints: list[str] = field(default_factory=list)
    matched_theme: str | None = None
    relevance_reason: str | None = None
    score_explanation: str | None = None
    council_rationale: str | None = None
    dimensions: list[str] = field(default_factory=list)

    @property
    def present(self) -> bool:
        return bool(self.thesis_text or self.discovery_candidate_id)

    def to_dict(self) -> dict[str, Any]:
        return {
            "discovery_run_id": self.discovery_run_id,
            "discovery_candidate_id": self.discovery_candidate_id,
            "thesis_text": self.thesis_text,
            "themes": list(self.themes),
            "industries": list(self.industries),
            "regions": list(self.regions),
            "size_hints": list(self.size_hints),
            "matched_theme": self.matched_theme,
            "relevance_reason": self.relevance_reason,
            "score_explanation": self.score_explanation,
            "council_rationale": self.council_rationale,
            "dimensions": list(self.dimensions),
        }


def dimensions_in(text: str | None) -> list[str]:
    """The thesis dimensions ``text`` names, in declaration order. Never inferred."""
    found: list[str] = []
    low = (text or "").lower()
    for dimension in THESIS_DIMENSIONS:
        if any(re.search(pattern, low) for pattern in dimension.patterns):
            found.append(dimension.key)
    return found


def _council_rationale(run: Any, candidate_id: str) -> str | None:
    envelope = ((getattr(run, "config_json", None) or {}).get("discovery_council") or {})
    review = envelope.get("review") or {}
    for bucket in review.values():
        if not isinstance(bucket, list):
            continue
        for entry in bucket:
            if isinstance(entry, dict) and str(entry.get("candidate_id")) == candidate_id:
                rationale = entry.get("rationale")
                return str(rationale)[:1000] if rationale else None
    return None


async def resolve_thesis(
    session: Any, *, discovery_candidate_id: str | uuid.UUID | None
) -> ThesisContext:
    """The thesis behind a discovery candidate, or an empty context. Never raises."""
    context = ThesisContext()
    if not discovery_candidate_id:
        return context
    try:
        from app.models.discovery import DiscoveryCandidate, DiscoveryRun

        candidate = await session.get(DiscoveryCandidate, uuid.UUID(str(discovery_candidate_id)))
        if candidate is None:
            return context
        run = await session.get(DiscoveryRun, candidate.discovery_run_id)
    except Exception:  # noqa: BLE001 - a thesis that cannot be read is absent, not fatal
        return context
    parsed = (getattr(run, "parsed_thesis_json", None) or {}) if run else {}
    match = getattr(candidate, "thesis_match_json", None) or {}
    context.discovery_run_id = str(candidate.discovery_run_id)
    context.discovery_candidate_id = str(candidate.id)
    context.thesis_text = getattr(run, "thesis_text", None) if run else None
    context.themes = list(parsed.get("themes") or [])
    context.industries = list(parsed.get("industries") or [])
    context.regions = list(parsed.get("regions") or [])
    context.size_hints = list(parsed.get("size_hints") or [])
    context.matched_theme = match.get("theme")
    context.relevance_reason = match.get("relevance_reason")
    context.score_explanation = getattr(candidate, "score_explanation", None)
    context.council_rationale = _council_rationale(run, str(candidate.id)) if run else None
    context.dimensions = dimensions_in(context.thesis_text)
    return context


def thesis_questions(context: ThesisContext) -> list[Any]:
    """One ``thesis_fit`` question per dimension the thesis names."""
    from app.services.director.contracts import EvidenceContract
    from app.services.director.planner import PlannedQuestion
    from app.services.ledger import store as ledger

    if not context.present:
        return []
    by_key = {d.key: d for d in THESIS_DIMENSIONS}
    questions: list[PlannedQuestion] = []
    for key in context.dimensions:
        dimension = by_key[key]
        questions.append(
            PlannedQuestion(
                key=f"thesis_fit__{key}"[:80],
                text=(
                    f"The company was selected for research under the thesis "
                    f"“{(context.thesis_text or '')[:240]}”. How is it exposed to "
                    f"{dimension.label}: {dimension.exposure}? Classify the exposure as "
                    "direct, indirect, weak or none, quantify it where a source allows, "
                    "and cite evidence. A weak or absent link is an acceptable answer — "
                    "do not force a fit."
                ),
                origin=ledger.ORIGIN_DIRECTOR,
                required_tools=frozenset({"search_company_corpus"}),
                optional_tools=frozenset({"get_industry_series"}),
                priority=1,
                domain="thesis_fit",
                owner_role="industry_analyst",
                why_it_matters=(
                    "The company is being researched BECAUSE of this thesis; whether the "
                    "link is real, and how large, is the first thing a reader needs."
                ),
                evidence_contract=EvidenceContract(
                    min_items=2, min_distinct_sources=2, allow_external=True,
                    max_external_searches=1,
                ),
                search_intents=(
                    f"{{commodity}} demand {dimension.label} share",
                    f"{{company}} {dimension.label}",
                ),
            )
        )
    return questions


def size_fit(context: ThesisContext, market_cap_usd: float | None) -> dict[str, Any] | None:
    """Deterministic: does the company's size match the size the thesis asked for?"""
    wanted = [h for h in context.size_hints if h in SIZE_BANDS]
    if not wanted:
        return None
    if market_cap_usd is None:
        return {
            "requested": wanted,
            "market_cap_usd": None,
            "fits": None,
            "note": "Market capitalisation is not available, so size fit is unknown.",
        }
    fits = any(
        (low is None or market_cap_usd >= low) and (high is None or market_cap_usd < high)
        for low, high in (SIZE_BANDS[h] for h in wanted)
    )
    return {
        "requested": wanted,
        "market_cap_usd": market_cap_usd,
        "fits": fits,
        "bands_usd": {h: SIZE_BANDS[h] for h in wanted},
        "note": (
            "Within the size band the thesis names."
            if fits
            else "OUTSIDE the size band the thesis names — the thesis does not describe "
            "this company on size, whatever its exposure."
        ),
    }


__all__ = [
    "SIZE_BANDS",
    "THESIS_DIMENSIONS",
    "ThesisContext",
    "dimensions_in",
    "resolve_thesis",
    "size_fit",
    "thesis_questions",
]
