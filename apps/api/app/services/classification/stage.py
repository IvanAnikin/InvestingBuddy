"""Is this company a pre-revenue MINING developer? — item 20.

THE DEFECT
==========
A company building its first mine or processing plant was researched as a producer:
the base model asked for revenue trajectory and margins, the mining playbook's blocking
question asked what SHARE of revenue each commodity represents, and the report called
it "fundamentally incomplete" for lacking revenue and EBITDA it has never had.

THE RULE: POSITIVE MINING EVIDENCE ONLY
=======================================
The overlay is a MINING methodology. It fires only on the company's own positive
evidence of being a mining developer — never on an absence alone, never from a name,
ticker or list, and never on a biotech, a technology company, an energy developer or a
producer (review round 1, B1 / H2 / H3):

* **Mining evidence (required)** — the corpus names at least one mining REPORTING-CODE
  term (JORC, NI 43-101, S-K 1300, PERC, Mineral Resource, Ore Reserve), or the company
  is classified in a mining industry. FID, offtake and feasibility-study acronyms are
  shared by LNG, renewables, batteries and hydrogen: they never count alone.
* **P1 — no or immaterial revenue, two years running.** Judged on the latest ANNUAL
  statement only (an interim loss beside an annual revenue proves nothing), from ONE
  document that states an income-statement line for that year AND the prior year (its
  comparative column): in each of the two years either no revenue fact exists anywhere
  and the document reports a LOSS, or revenue is under 10% of the stated operating costs.
  No extracted annual statement ⇒ no P1.
* **P2 — mining exploration / development spend**: an extracted exploration or mine
  development line, or the corpus naming exploration-and-evaluation expenditure,
  capitalised exploration or mine development. NOT "capitalised development" (R&D) and
  NOT "assets under construction" (an ordinary PP&E note).
* **P3 — project-disclosure vocabulary**: at least two terms, at least one of them a
  mining reporting-code term.

``development_stage_resource`` = mining evidence ∧ P1 ∧ (P2 ∨ P3).
``resource_extraction`` = a mining reporting-code term ∧ the subject profile names a
commodity. ``pre_revenue`` (the biotech signal) is never emitted.

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

#: Income-statement lines — what makes a year "read" in P1. Operating cash flow is a
#: cash-flow line and does not count.
_INCOME_STATEMENT_FIELDS = frozenset(
    {"revenue", "net_income", "administrative_expenses", "exploration_expensed"}
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

#: P2 in words. MINING accounting vocabulary only.
_SPEND_TEXT_RE = re.compile(
    r"\bexploration\s+and\s+evaluation\s+(?:expenditure|assets?|costs?)\b"
    r"|\bcapitali[sz]ed\s+exploration\b"
    r"|\bmine\s+development\s+(?:expenditure|costs?|assets?)\b",
    re.IGNORECASE,
)

#: The mining reporting-code terms. At least one is required for any signal.
MINING_CODE_TERMS: frozenset[str] = frozenset(
    {"jorc", "ni_43_101", "sk_1300", "perc", "mineral_resource", "ore_reserve"}
)

#: A company NAME, not a statement: "Mineral Resources Limited" (an offtake partner)
#: is not a Mineral Resource estimate (review round 2, H8).
_COMPANY_SUFFIX = (
    r"(?!\s*(?:\(|,)?\s*(?:Limited|Ltd|Inc|Incorporated|plc|PLC|Corp|Corporation|Group"
    r"|Pty|N\.?L\.?|S\.?A\.?|AG|ASA|AB|LLC)\b)"
)

#: P3 — each entry is ONE term, however it is spelt. Acronyms are case-sensitive. The
#: mining reporting-code terms are counted only in REPORTING context — an estimate, a
#: statement, a code, a technical report — never as a bare phrase or a company name.
_PROJECT_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (name, re.compile(pattern, re.IGNORECASE))
    for name, pattern in (
        (
            "jorc",
            r"(?-i:\bJORC\b)\s*(?:\(\s*\d{4}\s*\)\s*)?(?:Code|compliant|-compliant)"
            r"|\bin\s+accordance\s+with\s+(?:the\s+)?(?-i:JORC)\b"
            r"|\breported\s+under\s+(?:the\s+)?(?-i:JORC)\b",
        ),
        (
            "ni_43_101",
            r"\bNI\s?43-?101\b\s*(?:technical\s+report|compliant|standards?)"
            r"|\bNational\s+Instrument\s+43-?101\b"
            r"|\bin\s+accordance\s+with\s+NI\s?43-?101\b",
        ),
        ("sk_1300", r"\bS-K\s?1300\b|\bSubpart\s+1300\b"),
        ("perc", r"(?-i:\bPERC\b)(?=[^.\n]{0,60}\b(?:code|standard|reporting)\b)"),
        (
            "mineral_resource",
            r"\b(?:Measured|Indicated|Inferred)(?:\s*(?:,|and|&)\s*(?:Measured|Indicated|"
            r"Inferred))*\s+Mineral\s+Resources?\b" + _COMPANY_SUFFIX
            + r"|\bMineral\s+Resources?\s+(?:estimate|statement|update)\b",
        ),
        (
            "ore_reserve",
            r"\b(?:Proved|Proven|Probable)(?:\s*(?:and|&)\s*(?:Proved|Proven|Probable))*"
            r"\s+(?:Ore|Mineral)\s+Reserves?\b" + _COMPANY_SUFFIX
            + r"|\b(?:Ore|Mineral)\s+Reserves?\s+(?:estimate|statement|update)\b",
        ),
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

#: A sentence that negates ("JORC does not apply", "no Mineral Resource has been
#: estimated") states nothing — review round 2, H8.
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?;])\s+|\n+")
_NEGATION_RE = re.compile(
    r"\b(?:not|no|never|neither|nor|without|n/a)\b|\bnot\s+applicable\b", re.IGNORECASE)


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


def _annual_year(fact: dict[str, Any]) -> int | None:
    from app.services.sources.financial_period import PERIOD_TYPE_ANNUAL, parse_period

    period = parse_period(fact.get("period"))
    if period.period_type != PERIOD_TYPE_ANNUAL or not period.year:
        return None
    return int(period.year)


def _year_shows_no_revenue(
    year: int, document_facts: list[dict[str, Any]], all_revenue_years: dict[int, list[dict]],
) -> bool:
    """One year, judged from one document (plus: no revenue fact for it ANYWHERE)."""
    own = {str(f["field"]): f for f in document_facts if _annual_year(f) == year}
    if not own:
        return False
    revenues = all_revenue_years.get(year, [])
    if not revenues:
        loss = own.get("net_income")
        return loss is not None and float(loss["numeric_value"]) < 0
    costs = [own[f] for f in _OPEX_FIELDS if f in own]
    if not costs or len(revenues) != 1:
        return False
    revenue = revenues[0]
    if any((c.get("currency"), c.get("scale")) != (revenue.get("currency"), revenue.get("scale"))
           for c in costs):
        return False
    opex = sum(abs(float(c["numeric_value"])) for c in costs)
    return opex > 0 and abs(float(revenue["numeric_value"])) < IMMATERIAL_REVENUE_SHARE * opex


def assess_revenue(facts: list[dict[str, Any]]) -> tuple[bool, str | None, list[str]]:
    """P1 from extracted ANNUAL statement facts. ``(proved, basis, fact_ids)``. Pure."""
    from app.services.sources.fact_scope import parse_scope

    usable = [
        f for f in facts
        if f.get("field") in _INCOME_STATEMENT_FIELDS
        and f.get("confidence") == "high"
        and not parse_scope(f.get("scope")).is_segment
        and f.get("numeric_value") is not None
        and _annual_year(f) is not None
    ]
    if not usable:
        return False, None, []
    latest = max(_annual_year(f) or 0 for f in usable)
    # Revenue is looked for at ANY confidence: a lower-confidence revenue figure cannot
    # prove revenue, but its presence means "no revenue" is not proved either.
    revenue_years: dict[int, list[dict]] = {}
    for f in facts:
        if (f.get("field") == "revenue" and f.get("numeric_value") is not None
                and _annual_year(f) is not None
                and not parse_scope(f.get("scope")).is_segment):
            revenue_years.setdefault(_annual_year(f) or 0, []).append(f)
    by_document: dict[str, list[dict[str, Any]]] = {}
    for f in usable:
        by_document.setdefault(str(f.get("document_id") or ""), []).append(f)
    for document, document_facts in by_document.items():
        if not document:
            continue
        if all(_year_shows_no_revenue(y, document_facts, revenue_years)
               for y in (latest, latest - 1)):
            refs = [str(f.get("fact_id")) for f in document_facts
                    if _annual_year(f) in (latest, latest - 1) and f.get("fact_id")]
            return True, (
                f"no or immaterial revenue in FY{latest} and FY{latest - 1}, from one "
                "annual statement that reports both years"
            ), refs
    return False, None, []


# ── P2 / P3 ────────────────────────────────────────────────────────────────── #


def assess_spend(facts: list[dict[str, Any]], texts: list[str]) -> tuple[bool, str | None]:
    """P2 — mining exploration or development spend. Pure."""
    for fact in facts:
        if fact.get("field") in _SPEND_FIELDS and float(fact.get("numeric_value") or 0) > 0:
            return True, f"an extracted {str(fact['field']).replace('_', ' ')} line"
    for text in texts:
        if _SPEND_TEXT_RE.search(text or ""):
            return True, "the company's documents report exploration or mine development spend"
    return False, None


def project_terms(texts: list[str]) -> list[str]:
    """P3 vocabulary the corpus names, distinct, in declaration order. Pure."""
    sentences = [
        sentence
        for text in texts
        for sentence in _SENTENCE_SPLIT_RE.split(text or "")
        if sentence and not _NEGATION_RE.search(sentence)
    ]
    found: list[str] = []
    for name, pattern in _PROJECT_TERMS:
        if any(pattern.search(sentence) for sentence in sentences):
            found.append(name)
    return found


#: Track C review round 3, H3 — who a sentence is about. A reporting-code statement
#: counts as the ISSUER'S mining evidence only when attributed to it or its own
#: project, and never in a supplier / partner / customer / feedstock context.
_ATTRIBUTION_RE = re.compile(r"\b(?:we|our)\b|\bthe\s+(?:Company|Group|Project)\b",
                             re.IGNORECASE)
_THIRD_PARTY_RE = re.compile(
    r"\b(?:suppliers?|partners?|customers?|offtakers?|feedstock|third[- ]part(?:y|ies)"
    r"|counterpart(?:y|ies)|vendors?|purchasers?|buyers?|toll(?:ing)?\s+treat\w*)\b",
    re.IGNORECASE,
)
_LEGAL_SUFFIX_RE = re.compile(
    r"\b(?:limited|ltd|plc|inc|incorporated|corp|corporation|n\.?l\.?|pty|holdings|group"
    r"|company|co)\b\.?", re.IGNORECASE)
#: Attributed reporting-code sentences needed WITHOUT a mining industry classification.
MIN_ATTRIBUTED_CODE_SENTENCES = 2


def _issuer_stem(issuer_name: str | None) -> str | None:
    stem = " ".join(_LEGAL_SUFFIX_RE.sub(" ", issuer_name or "").split())
    return stem if len(stem) >= 4 else None


def attributed_code_sentences(texts: list[str], issuer_name: str | None = None) -> int:
    """How many non-negated sentences state a mining reporting-code term ABOUT the
    issuer or its own project, outside any third-party context. Pure."""
    stem = _issuer_stem(issuer_name)
    stem_re = re.compile(rf"\b{re.escape(stem)}\b", re.IGNORECASE) if stem else None
    count = 0
    for text in texts:
        for sentence in _SENTENCE_SPLIT_RE.split(text or ""):
            if not sentence or _NEGATION_RE.search(sentence) or _THIRD_PARTY_RE.search(sentence):
                continue
            if not (_ATTRIBUTION_RE.search(sentence) or (stem_re and stem_re.search(sentence))):
                continue
            if any(pattern.search(sentence) for name, pattern in _PROJECT_TERMS
                   if name in MINING_CODE_TERMS):
                count += 1
    return count


def assess(
    facts: list[dict[str, Any]],
    texts: list[str],
    *,
    has_commodity: bool,
    mining_sector: bool = False,
    issuer_name: str | None = None,
) -> StageAssessment:
    """The pure decision. See the module docstring for the rule."""
    p1, p1_basis, refs = assess_revenue(facts)
    p2, p2_basis = assess_spend(facts, texts)
    terms = project_terms(texts)
    attributed = attributed_code_sentences(texts, issuer_name)
    codes = [t for t in terms if t in MINING_CODE_TERMS] if attributed else []
    p3 = len(terms) >= MIN_PROJECT_TERMS and attributed >= 1
    own_mining_reporting = attributed >= MIN_ATTRIBUTED_CODE_SENTENCES or (
        attributed >= 1 and mining_sector)
    mining = own_mining_reporting or mining_sector
    out = StageAssessment(
        proofs={"mining_evidence": mining, "no_or_immaterial_revenue": p1,
                "exploration_or_development_spend": p2,
                "project_disclosure_vocabulary": p3},
        evidence_refs=refs,
    )
    if own_mining_reporting:
        out.basis.append(
            f"mining reporting code terms attributed to the issuer in {attributed} "
            f"sentence(s): {', '.join(codes)}")
    elif mining_sector:
        out.basis.append("classified in a mining industry")
    if p1_basis:
        out.basis.append(f"P1: {p1_basis}")
    if p2_basis:
        out.basis.append(f"P2: {p2_basis}")
    if terms:
        out.basis.append(f"P3: project disclosure vocabulary ({', '.join(terms)})")
    signals: list[str] = []
    if mining and p1 and (p2 or p3):
        signals.append(SIGNAL_DEVELOPMENT_STAGE)
    if own_mining_reporting and has_commodity:
        signals.append(SIGNAL_RESOURCE_EXTRACTION)
    out.signals = tuple(signals)
    if not signals:
        out.reason = (
            "no mining reporting-code evidence" if not mining
            else "no annual statement figures were extracted, so revenue could not be assessed"
            if not facts
            else "the evidence does not positively show a pre-revenue mining developer"
        )
    return out


async def detect_stage(
    session: Any, company: Any, *, subject_profile: Any = None, mining_sector: bool = False,
) -> StageAssessment:
    """Read the company's statement facts and corpus, then decide. Never raises.

    The reads run in a SAVEPOINT: a database error inside them is rolled back to it
    rather than left aborting the run's transaction (review M1)."""
    try:
        from sqlalchemy import select

        from app.models.research_chunk import ResearchDocumentChunk
        from app.services.pipeline.issuer_financials import load_statement_facts

        async with session.begin_nested():
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
    return assess(facts, [str(t or "") for t in texts], has_commodity=bool(commodities),
                  mining_sector=mining_sector,
                  issuer_name=str(getattr(company, "name", "") or "") or None)


__all__ = [
    "IMMATERIAL_REVENUE_SHARE",
    "MINING_CODE_TERMS",
    "MIN_ATTRIBUTED_CODE_SENTENCES",
    "attributed_code_sentences",
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
