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

from app.services.discovery.constraints import SIZE_BANDS_USD as _BANDS


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
        (r"semiconductors?", r"(?<!blue[\s-])\bchips?\b", r"wafers?"),
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
#: so "does SCCO fit a small-cap thesis?" is a comparison anyone can check. V3.19.3 — ONE
#: table for the platform: derived from ``discovery.constraints.SIZE_BANDS_USD``. A
#: "large-cap" thesis keeps its V3.18 meaning (≥ $10bn, mega included).
SIZE_BANDS: dict[str, tuple[float | None, float | None]] = {
    "micro_cap": _BANDS["micro_cap"],
    "small_cap": _BANDS["small_cap"],
    "mid_cap": _BANDS["mid_cap"],
    "large_cap": (_BANDS["large_cap"][0], None),
    "mega_cap": _BANDS["mega_cap"],
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
    #: The size the candidate was matched at, from the discovery snapshot, with its date.
    market_cap_usd: float | None = None
    market_cap_as_of: str | None = None

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
            "market_cap_usd": self.market_cap_usd,
            "market_cap_as_of": self.market_cap_as_of,
        }


def dimensions_in(text: str | None) -> list[str]:
    """The thesis dimensions ``text`` names, in declaration order. Never inferred."""
    found: list[str] = []
    low = (text or "").lower()
    for dimension in THESIS_DIMENSIONS:
        if any(re.search(pattern, low) for pattern in dimension.patterns):
            found.append(dimension.key)
    return found


#: Words that turn a mention into a denial. A finding that says "no data-centre or AI use
#: is stated" NAMES the dimension and denies it, and V3.18.12 graded that as exposure.
_NEGATION_CUES: frozenset[str] = frozenset(
    {"no", "not", "none", "never", "without", "nor", "neither", "lacks", "lack",
     "lacking", "absent", "excludes", "excluding", "omits", "omitted"}
)

#: A denial scopes to its CLAUSE, not to a fixed number of words. "…elevators; no
#: data-centre or AI use is stated" denies both mentions however long the list; "copper
#: is not a semiconductor input, but our foil ships to semiconductor packaging" denies
#: only the first clause. A token window did one of these correctly and never both.
_CLAUSE_SPLIT_RE = re.compile(
    r"[;.]\s+|,\s+(?:but|although|though|while|whereas|yet)\s+"
)

_WORD_TOKEN_RE = re.compile(r"[a-z0-9]+")


def names_dimension(text: str | None, dimension_key: str) -> bool:
    """Does ``text`` name this dimension other than to DENY it? — V3.18.14.

    The affirmative half of ``dimensions_in``. Live on MP Materials, the finding *"Named
    end-uses of NdFeB magnets are EVs, wind, robots, motors, pumps, compressors,
    elevators; no data-centre or AI use is stated"* graded ``ai_data_centres`` as
    **evidenced** — on a sentence whose whole point is that the company's own list of end
    uses omits them. Grading exposure from a denial of it is the "absence read as
    presence" defect, inverted.

    One affirmative mention is enough: "copper is not a semiconductor input, but our foil
    ships to semiconductor packaging" names it. Only when EVERY mention sits in a clause
    that denies it does the dimension go unnamed.
    """
    low = (text or "").lower()
    dimension = next((d for d in THESIS_DIMENSIONS if d.key == dimension_key), None)
    if dimension is None or not low:
        return False
    for clause in _CLAUSE_SPLIT_RE.split(low):
        if not clause:
            continue
        for pattern in dimension.patterns:
            match = re.search(pattern, clause)
            if match is None:
                continue
            before = _WORD_TOKEN_RE.findall(clause[: match.start()])
            if not any(token in _NEGATION_CUES for token in before):
                return True
    return False


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

        # A SAVEPOINT: a failed read must not leave the report's transaction aborted.
        async with session.begin_nested():
            candidate = await session.get(
                DiscoveryCandidate, uuid.UUID(str(discovery_candidate_id))
            )
            run = (
                await session.get(DiscoveryRun, candidate.discovery_run_id)
                if candidate is not None
                else None
            )
        if candidate is None:
            return context
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
    # V3.19.5 — the intent's size constraint (with exclusions applied) is what was asked.
    intent_size = next(
        (c for c in ((parsed.get("discovery_intent") or {}).get("constraints") or [])
         if isinstance(c, dict) and c.get("key") == "size"),
        None,
    )
    if intent_size and intent_size.get("requested"):
        context.size_hints = list(intent_size["requested"])
    context.matched_theme = match.get("theme")
    context.relevance_reason = match.get("relevance_reason")
    context.score_explanation = getattr(candidate, "score_explanation", None)
    context.council_rationale = _council_rationale(run, str(candidate.id)) if run else None
    context.dimensions = dimensions_in(context.thesis_text)
    # V3.19.5 — a VERIFIED market cap from discovery, converted with an official rate,
    # is the size the thesis is tested against, in any listing currency.
    verified = ((match.get("v319") or {}).get("verified_attributes") or {})
    if verified.get("market_cap_usd"):
        context.market_cap_usd = float(verified["market_cap_usd"])
        context.market_cap_as_of = (verified.get("market_cap") or {}).get("as_of")
        return context
    market_cap_mln = getattr(candidate, "market_cap_mln", None)
    from app.services.exchange_registry import is_sec_eligible

    # `market_cap_mln` is close x shares in the LISTING's currency. Only a US listing's
    # figure is in dollars; any other is not compared with USD bands — no FX conversion
    # is made, so an A$2.5bn company is not called "too large for small-cap".
    if market_cap_mln is not None and is_sec_eligible(getattr(candidate, "exchange", None)):
        context.market_cap_usd = float(market_cap_mln) * 1e6
        created = getattr(candidate, "created_at", None)
        context.market_cap_as_of = created.date().isoformat() if created else None
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
                # The user's own thesis TEXT is deliberately not in the question: the
                # question text reaches an external search provider's query context,
                # and what a user typed into discovery is theirs. The dimension it
                # named is enough to research; the text is shown in the report.
                text=(
                    "The company was selected for research under an investment thesis "
                    f"about {dimension.label}. How is it exposed to {dimension.label}: "
                    f"{dimension.exposure}? Classify the exposure as direct, indirect, "
                    "weak or none ONLY from evidence that describes the company's "
                    "products, their end uses or its customers — quantify it where a "
                    "source allows, and cite it. Evidence that does not address the link "
                    "is a gap to report, not a weak link: absence in what was read is "
                    "not absence in the business. Do not force a fit either way."
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
            "note": (
                "A market capitalisation in US dollars is not available (a non-US "
                "listing's value is not converted), so size fit is unknown."
            ),
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
    "names_dimension",
    "SIZE_BANDS",
    "THESIS_DIMENSIONS",
    "ThesisContext",
    "dimensions_in",
    "resolve_thesis",
    "size_fit",
    "thesis_questions",
]
