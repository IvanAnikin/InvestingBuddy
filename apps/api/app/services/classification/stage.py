"""Is this company a pre-revenue resource DEVELOPER? — item 20.

THE DEFECT
==========
A company building its first mine or processing plant was researched as a producer:
the base model asked for revenue trajectory and margins, the mining playbook's blocking
question asked what SHARE of revenue each commodity represents, and the report called
it "fundamentally incomplete" for lacking revenue and EBITDA it has never had.

THE RULE: POSITIVE PROOF ONLY
=============================
A company is marked development-stage only when its OWN evidence shows it — never from
an absence alone, and never from a name, ticker or list:

* **P1 — no or immaterial revenue** in the latest annual or half-year statements the
  platform extracted: no revenue line beside a LOSS or an operating cash OUTFLOW, or a
  revenue line below 10% of the stated operating costs (administrative expenses and
  expensed exploration). Interest or other income is not revenue (the extractor already
  refuses it). **No extracted statement ⇒ no P1**: "we read nothing" is not "it earns
  nothing".
* **P2 — exploration or development spend**: an extracted exploration / development
  line, or the corpus naming exploration-and-evaluation or capitalised development
  expenditure or assets under construction.
* **P3 — project-disclosure vocabulary** in the company's own corpus: at least two of
  JORC, NI 43-101, S-K 1300, PERC, Mineral Resource, Ore Reserve, scoping study,
  pre-feasibility / definitive / bankable feasibility study, final investment decision,
  offtake.

``development_stage_resource`` = P1 and (P2 or P3). ``resource_extraction`` = P3 and
the company's own documents name a commodity (the subject profile). The detector never
emits ``pre_revenue``: that signal selects the biotech playbook.

Never raises. A failure is an assessment with no signals and the reason recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

SIGNAL_DEVELOPMENT_STAGE = "development_stage_resource"
SIGNAL_RESOURCE_EXTRACTION = "resource_extraction"

#: Revenue below this share of stated operating costs is immaterial for P1.
IMMATERIAL_REVENUE_SHARE = 0.10
#: Distinct project-disclosure terms the corpus must name for P3.
MIN_PROJECT_TERMS = 2
#: Chunks read — the same bound the subject profile uses.
MAX_CHUNKS = 600

_P1_ACTIVITY_FIELDS = frozenset(
    {
        "revenue",
        "net_income",
        "operating_cash_flow",
        "administrative_expenses",
        "exploration_expensed",
    }
)
_OPEX_FIELDS = ("administrative_expenses", "exploration_expensed")
_SPEND_FIELDS = frozenset(
    {
        "exploration_expensed",
        "exploration_capitalised",
        "exploration_payments",
        "development_expenditure",
    }
)

#: P2 in words. Generic accounting vocabulary, never a project or issuer name.
_SPEND_TEXT_RE = re.compile(
    r"\bexploration\s+and\s+evaluation\s+(?:expenditure|assets?|costs?)\b"
    r"|\bcapitali[sz]ed\s+(?:exploration|development)\b"
    r"|\bassets?\s+under\s+construction\b"
    r"|\bmine\s+development\s+(?:expenditure|costs?|assets?)\b",
    re.IGNORECASE,
)

#: P3 — each entry is ONE term, however it is spelt.
_PROJECT_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        ("jorc", r"(?-i:\bJORC\b)"),
        ("ni_43_101", r"\bNI\s?43-?101\b|\bNational\s+Instrument\s+43-?101\b"),
        ("sk_1300", r"\bS-K\s?1300\b|\bSubpart\s+1300\b"),
        ("perc", r"(?-i:\bPERC\b)(?=[^.\n]{0,60}\b(?:code|standard|reporting)\b)"),
        ("mineral_resource", r"\bMineral\s+Resources?\b"),
        ("ore_reserve", r"\b(?:Ore|Mineral)\s+Reserves?\b"),
        ("scoping_study", r"\bscoping\s+study\b"),
        (
            "feasibility_study",
            r"\b(?:pre-?feasibility|definitive\s+feasibility|bankable\s+feasibility)"
            r"(?:\s+study)?\b|(?-i:\b(?:PFS|DFS|BFS)\b)",
        ),
        ("final_investment_decision", r"\bfinal\s+investment\s+decision\b|(?-i:\bFID\b)"),
        ("offtake", r"\boff-?take\s+(?:agreement|contract|term\s+sheet|partner|MOU)"),
    )
)


@dataclass
class StageAssessment:
    """What the detector found, and on what evidence. Recorded on the run."""

    signals: tuple[str, ...] = ()
    basis: list[str] = field(default_factory=list)
    evidence_refs: list[str] = field(default_factory=list)
    reason: str | None = None
    proofs: dict[str, bool] = field(default_factory=dict)

    @property
    def is_development_stage(self) -> bool:
        return SIGNAL_DEVELOPMENT_STAGE in self.signals

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": SIGNAL_DEVELOPMENT_STAGE if self.is_development_stage else None,
            "signals": list(self.signals),
            "proofs": dict(self.proofs),
            "basis": list(self.basis),
            "evidence_refs": list(self.evidence_refs)[:20],
            "reason": self.reason,
        }


# ── P1 ─────────────────────────────────────────────────────────────────────── #


def _latest_period(facts: list[dict[str, Any]]) -> Any:
    from app.services.sources.financial_period import (
        PERIOD_TYPE_ANNUAL,
        PERIOD_TYPE_HALF,
        parse_period,
    )
    from app.services.sources.period_state import period_end_quarter

    best = None
    best_key: tuple[int, int] | None = None
    for fact in facts:
        period = parse_period(fact.get("period"))
        if period.period_type not in (PERIOD_TYPE_ANNUAL, PERIOD_TYPE_HALF):
            continue
        key = (period.year or 0, period_end_quarter(period) or 4)
        if best_key is None or key > best_key:
            best, best_key = period, key
    return best


def assess_revenue(facts: list[dict[str, Any]]) -> tuple[bool, str | None, list[str]]:
    """P1 from extracted statement facts. ``(proved, basis, fact_ids)``. Pure."""
    from app.services.sources.fact_scope import parse_scope
    from app.services.sources.financial_period import parse_period

    usable = [
        f for f in facts
        if f.get("field") in _P1_ACTIVITY_FIELDS
        and f.get("confidence") == "high"
        and not parse_scope(f.get("scope")).is_segment
        and f.get("numeric_value") is not None
    ]
    period = _latest_period(usable)
    if period is None:
        return False, None, []
    in_period: dict[str, dict[str, Any]] = {}
    for fact in usable:
        if parse_period(fact.get("period")).key == period.key:
            in_period.setdefault(str(fact["field"]), fact)
    refs = [str(f.get("fact_id")) for f in in_period.values() if f.get("fact_id")]
    revenue = in_period.get("revenue")
    if revenue is None:
        loss = in_period.get("net_income")
        ocf = in_period.get("operating_cash_flow")
        if loss is not None and float(loss["numeric_value"]) < 0:
            return True, (
                f"no revenue line in the {period.label()} statements, which report a loss"
            ), refs
        if ocf is not None and float(ocf["numeric_value"]) < 0:
            return True, (
                f"no revenue line in the {period.label()} statements, which report an "
                "operating cash outflow"
            ), refs
        return False, None, []
    costs = [in_period[f] for f in _OPEX_FIELDS if f in in_period]
    if not costs:
        return False, None, []
    if any(
        (c.get("currency"), c.get("scale")) != (revenue.get("currency"), revenue.get("scale"))
        for c in costs
    ):
        return False, None, []
    opex = sum(abs(float(c["numeric_value"])) for c in costs)
    if opex > 0 and abs(float(revenue["numeric_value"])) < IMMATERIAL_REVENUE_SHARE * opex:
        return True, (
            f"{period.label()} revenue is under {int(IMMATERIAL_REVENUE_SHARE * 100)}% of the "
            "stated operating costs"
        ), refs
    return False, None, []


# ── P2 / P3 ────────────────────────────────────────────────────────────────── #


def assess_spend(facts: list[dict[str, Any]], texts: list[str]) -> tuple[bool, str | None]:
    """P2. Pure."""
    for fact in facts:
        if fact.get("field") in _SPEND_FIELDS and float(fact.get("numeric_value") or 0) > 0:
            return True, f"an extracted {str(fact['field']).replace('_', ' ')} line"
    for text in texts:
        if _SPEND_TEXT_RE.search(text or ""):
            return True, "the company's documents report exploration or development spend"
    return False, None


def project_terms(texts: list[str]) -> list[str]:
    """P3 vocabulary the corpus names, distinct, in declaration order. Pure."""
    found: list[str] = []
    for name, pattern in _PROJECT_TERMS:
        if any(pattern.search(text or "") for text in texts):
            found.append(name)
    return found


def assess(
    facts: list[dict[str, Any]],
    texts: list[str],
    *,
    has_commodity: bool,
) -> StageAssessment:
    """The pure decision. See the module docstring for the rule."""
    p1, p1_basis, refs = assess_revenue(facts)
    p2, p2_basis = assess_spend(facts, texts)
    terms = project_terms(texts)
    p3 = len(terms) >= MIN_PROJECT_TERMS
    out = StageAssessment(
        proofs={"no_or_immaterial_revenue": p1, "exploration_or_development_spend": p2,
                "project_disclosure_vocabulary": p3},
        evidence_refs=refs,
    )
    if p1_basis:
        out.basis.append(f"P1: {p1_basis}")
    if p2_basis:
        out.basis.append(f"P2: {p2_basis}")
    if terms:
        out.basis.append(f"P3: project disclosure vocabulary ({', '.join(terms)})")
    signals: list[str] = []
    if p1 and (p2 or p3):
        signals.append(SIGNAL_DEVELOPMENT_STAGE)
    if p3 and has_commodity:
        signals.append(SIGNAL_RESOURCE_EXTRACTION)
    out.signals = tuple(signals)
    if not signals:
        out.reason = (
            "no statement figures were extracted, so revenue could not be assessed"
            if not facts
            else "the evidence does not positively show a pre-revenue resource developer"
        )
    return out


async def detect_stage(
    session: Any, company: Any, *, subject_profile: Any = None
) -> StageAssessment:
    """Read the company's statement facts and corpus, then decide. Never raises."""
    try:
        from sqlalchemy import select

        from app.models.research_chunk import ResearchDocumentChunk
        from app.services.pipeline.issuer_financials import load_statement_facts

        facts = await load_statement_facts(session, getattr(company, "id", None))
        texts = list(
            (
                await session.execute(
                    select(ResearchDocumentChunk.text)
                    .where(ResearchDocumentChunk.company_id == company.id)
                    .limit(MAX_CHUNKS)
                )
            ).scalars().all()
        )
    except Exception as exc:  # noqa: BLE001 - a stage signal must not end the run
        return StageAssessment(reason=f"stage evidence unreadable ({type(exc).__name__})")
    commodities = list(getattr(subject_profile, "commodities", None) or [])
    return assess(facts, [str(t or "") for t in texts], has_commodity=bool(commodities))


__all__ = [
    "IMMATERIAL_REVENUE_SHARE",
    "MIN_PROJECT_TERMS",
    "SIGNAL_DEVELOPMENT_STAGE",
    "SIGNAL_RESOURCE_EXTRACTION",
    "StageAssessment",
    "assess",
    "assess_revenue",
    "assess_spend",
    "detect_stage",
    "project_terms",
]
