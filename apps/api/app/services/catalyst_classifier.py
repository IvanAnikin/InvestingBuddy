"""
Phase 24 — Deterministic catalyst classifier.

Assigns a model-derived category / direction / strength / evidence-strength /
confidence to a catalyst event from its SEC form metadata, headline keywords and
source tier. Fully deterministic and offline — no LLM, no network.

STRICT PROHIBITION:
  - Classification is NOT a recommendation.
  - A positive catalyst is NOT "buy"; a negative catalyst is NOT "sell".
  - No price targets, fair values, upside/downside, or valuation judgements are
    produced. The label describes the *event*, never an action for the reader.
  - All labels are model-derived (T6_model_estimate) and require human review.

The classifier reads keywords for detection only — it never echoes raw external
text into its explanation, so no third-party recommendation language can leak
through. Explanations are templated from the controlled category/direction
vocabulary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from app.integrations.financial_data_provider import SourceTier
from app.schemas.catalyst import (
    CatalystCategory,
    CatalystDirection,
    CatalystEvent,
    CatalystStrength,
    EvidenceStrength,
)

_T1 = SourceTier.T1_primary_filing.value
_T2 = SourceTier.T2_regulator_or_gov.value
_T3 = SourceTier.T3_industry_specialist.value
_T4 = SourceTier.T4_quality_media.value
_T5 = SourceTier.T5_api_aggregator.value


# ---------------------------------------------------------------------------
# SEC 8-K item → (category, direction) mapping
# ---------------------------------------------------------------------------

# Direction defaults are conservative: filing existence is not itself positive
# or negative. Only clearly adverse items map to negative/risk.
_ITEM_MAP: dict[str, tuple[str, str]] = {
    "1.01": (CatalystCategory.contract.value, CatalystDirection.neutral.value),
    "1.02": (CatalystCategory.risk_event.value, CatalystDirection.negative.value),
    "2.01": (CatalystCategory.mna.value, CatalystDirection.mixed.value),
    "2.02": (CatalystCategory.earnings.value, CatalystDirection.neutral.value),
    "2.03": (CatalystCategory.financing.value, CatalystDirection.neutral.value),
    "2.05": (CatalystCategory.risk_event.value, CatalystDirection.negative.value),
    "2.06": (CatalystCategory.risk_event.value, CatalystDirection.negative.value),
    "3.01": (CatalystCategory.risk_event.value, CatalystDirection.negative.value),
    "3.02": (CatalystCategory.financing.value, CatalystDirection.mixed.value),
    "4.01": (CatalystCategory.risk_event.value, CatalystDirection.negative.value),
    "5.02": (CatalystCategory.management.value, CatalystDirection.unknown.value),
    "7.01": (CatalystCategory.filing_event.value, CatalystDirection.neutral.value),
    "8.01": (CatalystCategory.filing_event.value, CatalystDirection.neutral.value),
    "9.01": (CatalystCategory.filing_event.value, CatalystDirection.neutral.value),
}

# Non-8-K form types → category (all neutral by default; routine filings)
_FORM_MAP: dict[str, str] = {
    "10-K": CatalystCategory.filing_event.value,
    "10-Q": CatalystCategory.filing_event.value,
    "10-K/A": CatalystCategory.filing_event.value,
    "10-Q/A": CatalystCategory.filing_event.value,
    "20-F": CatalystCategory.filing_event.value,
    "40-F": CatalystCategory.filing_event.value,
    "6-K": CatalystCategory.filing_event.value,
    "DEF 14A": CatalystCategory.management.value,
    "DEFA14A": CatalystCategory.management.value,
    "S-3": CatalystCategory.financing.value,
    "S-8": CatalystCategory.financing.value,
    "S-1": CatalystCategory.financing.value,
}


# ---------------------------------------------------------------------------
# Headline keyword sets (detection only — never echoed into output)
# ---------------------------------------------------------------------------

_NEGATIVE_KEYWORDS = (
    "investigation", "lawsuit", "sued", "subpoena", "recall", "impairment",
    "restatement", "restate", "guidance cut", "cuts guidance", "lowers guidance",
    "misses estimate", "missed estimate", "delist", "bankrupt", "downgrade",
    "regulatory warning", "data breach", "fraud", "probe", "penalt", "sanction",
    "resign", "steps down", "short seller", "going concern", "default",
)

_POSITIVE_KEYWORDS = (
    "contract award", "awarded contract", "wins contract", "new partnership",
    "launches", "unveils", "record revenue", "raises guidance", "raised guidance",
    "beats estimate", "beat estimate", "repurchase authorization",
    "dividend increase", "raises dividend", "special dividend", "approval granted",
    "receives approval", "expansion", "record backlog",
)

# category keyword → CatalystCategory
_CATEGORY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("acquisition", "acquires", "acquire", "merger", "merges", "takeover",
      "divest", "disposal"), CatalystCategory.mna.value),
    (("lawsuit", "sued", "litigation", "court", "settlement", "subpoena",
      "class action"), CatalystCategory.litigation.value),
    (("dividend", "repurchase", "capital return"), CatalystCategory.capital_return.value),
    (("partnership", "partner", "collaboration", "joint venture", "alliance"),
     CatalystCategory.partnership.value),
    (("offering", "notes due", "credit facility", "private placement",
      "debt financing", "raises capital", "equity offering", "convertible"),
     CatalystCategory.financing.value),
    (("ceo", "cfo", "chief executive", "chief financial", "appoint", "resign",
      "steps down", "board of directors", "director"),
     CatalystCategory.management.value),
    (("regulator", "regulatory", "fda", "compliance", "antitrust",
      "clearance"), CatalystCategory.regulatory.value),
    (("guidance", "outlook", "forecast"), CatalystCategory.guidance.value),
    (("earnings", "quarterly results", "revenue", "eps", "profit",
      "net income"), CatalystCategory.earnings.value),
    (("launch", "unveil", "product", "release", "new model"),
     CatalystCategory.product.value),
    (("contract", "award", "deal", "order"), CatalystCategory.contract.value),
    (("customer", "client win"), CatalystCategory.customer.value),
    (("restructuring", "layoff", "facility", "production", "capacity",
      "operations"), CatalystCategory.operations.value),
    (("sector", "industry-wide", "macro", "tariff"),
     CatalystCategory.macro_sector.value),
]

# Material categories: a specific, market-relevant event (drives strength=high
# when backed by a primary/regulator source).
_MATERIAL_CATEGORIES = {
    CatalystCategory.mna.value,
    CatalystCategory.litigation.value,
    CatalystCategory.regulatory.value,
    CatalystCategory.capital_return.value,
    CatalystCategory.financing.value,
    CatalystCategory.management.value,
    CatalystCategory.contract.value,
    CatalystCategory.guidance.value,
    CatalystCategory.risk_event.value,
}


class CatalystClassification(BaseModel):
    catalyst_category: str
    catalyst_direction: str
    catalyst_strength: str
    evidence_strength: str
    confidence: float = Field(ge=0.0, le=1.0)
    explanation: str
    warnings: list[str] = Field(default_factory=list)


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(k in text for k in keywords)


def _classify_by_items(item_numbers: list[str]) -> tuple[str | None, str | None]:
    """Return (category, direction) from the most adverse mapped 8-K item."""
    category: str | None = None
    direction: str | None = None
    # Prefer a negative/risk item if present, else the first mapped item.
    for item in item_numbers:
        norm = item.strip()
        mapped = _ITEM_MAP.get(norm)
        if not mapped:
            continue
        cat, dirn = mapped
        if dirn == CatalystDirection.negative.value:
            return cat, dirn
        if category is None:
            category, direction = cat, dirn
    return category, direction


def _classify_headline(headline: str, summary: str | None) -> tuple[str, str]:
    """Return (category, direction) from headline/summary keywords."""
    text = (headline + " " + (summary or "")).lower()

    negative = _contains_any(text, _NEGATIVE_KEYWORDS)
    positive = _contains_any(text, _POSITIVE_KEYWORDS)

    category = CatalystCategory.other.value
    for keywords, cat in _CATEGORY_KEYWORDS:
        if _contains_any(text, keywords):
            category = cat
            break

    if negative and not positive:
        direction = CatalystDirection.negative.value
        if category in (CatalystCategory.other.value, CatalystCategory.operations.value):
            category = CatalystCategory.risk_event.value
    elif positive and not negative:
        direction = CatalystDirection.positive.value
    elif positive and negative:
        direction = CatalystDirection.mixed.value
    else:
        direction = CatalystDirection.neutral.value if category != CatalystCategory.other.value \
            else CatalystDirection.unknown.value

    return category, direction


def _evidence_from_tier(source_tier: str, multi_source: bool) -> str:
    if multi_source:
        return EvidenceStrength.multi_source_confirmed.value
    if source_tier == _T2:
        return EvidenceStrength.regulator_confirmed.value
    if source_tier == _T1:
        return EvidenceStrength.primary_confirmed.value
    if source_tier in (_T3, _T4):
        return EvidenceStrength.single_source_reported.value
    if source_tier == _T5:
        return EvidenceStrength.aggregator_only.value
    return EvidenceStrength.model_inferred.value


def _strength(
    evidence_strength: str,
    category: str,
    direction: str,
    has_detail: bool,
) -> str:
    primary = evidence_strength in (
        EvidenceStrength.regulator_confirmed.value,
        EvidenceStrength.primary_confirmed.value,
        EvidenceStrength.multi_source_confirmed.value,
    )
    is_material = category in _MATERIAL_CATEGORIES
    has_direction = direction != CatalystDirection.unknown.value
    if primary and is_material and has_direction:
        return CatalystStrength.high.value
    if primary or evidence_strength == EvidenceStrength.single_source_reported.value:
        return CatalystStrength.medium.value
    if evidence_strength == EvidenceStrength.aggregator_only.value:
        return CatalystStrength.low.value if has_detail else CatalystStrength.unknown.value
    return CatalystStrength.unknown.value


def _confidence(evidence_strength: str, strength: str, direction: str) -> float:
    base = {
        EvidenceStrength.multi_source_confirmed.value: 0.8,
        EvidenceStrength.regulator_confirmed.value: 0.7,
        EvidenceStrength.primary_confirmed.value: 0.7,
        EvidenceStrength.single_source_reported.value: 0.5,
        EvidenceStrength.aggregator_only.value: 0.35,
        EvidenceStrength.model_inferred.value: 0.2,
        EvidenceStrength.insufficient.value: 0.1,
    }.get(evidence_strength, 0.2)

    if strength == CatalystStrength.high.value:
        base += 0.1
    elif strength == CatalystStrength.unknown.value:
        base -= 0.1
    if direction == CatalystDirection.unknown.value:
        base -= 0.1

    return round(max(0.0, min(1.0, base)), 2)


def classify_catalyst(
    *,
    headline: str,
    summary: str | None,
    source_tier: str,
    form_type: str | None = None,
    item_numbers: list[str] | None = None,
    normalized_event_type: str = "news",
    multi_source: bool = False,
    source_text: str | None = None,
) -> CatalystClassification:
    """
    Classify a catalyst event deterministically.

    SEC form/item metadata (when present) takes precedence over headline
    keywords, because a regulated filing's structure is more reliable than a
    third-party headline. Headline keywords can still shift a neutral filing
    (e.g. an 8-K Item 2.02 whose title mentions "misses estimates") toward a
    non-neutral direction.
    """
    item_numbers = item_numbers or []
    warnings: list[str] = []

    category: str | None = None
    direction: str | None = None
    basis = "headline_keywords"

    # 1. SEC 8-K item mapping (strongest structured signal)
    if item_numbers:
        category, direction = _classify_by_items(item_numbers)
        if category is not None:
            basis = "sec_8k_item_mapping"

    # 2. Non-8-K SEC form mapping
    if category is None and form_type:
        mapped_form = _FORM_MAP.get(form_type.upper()) or _FORM_MAP.get(form_type)
        if mapped_form:
            category = mapped_form
            direction = CatalystDirection.neutral.value
            basis = "sec_form_mapping"

    # 3. Headline keyword refinement
    kw_category, kw_direction = _classify_headline(headline, summary)
    if category is None:
        category, direction = kw_category, kw_direction
    else:
        # Let clearly adverse/positive headline language override a neutral
        # structured default (e.g. earnings filing that "misses estimates").
        if direction in (CatalystDirection.neutral.value, CatalystDirection.unknown.value) and \
                kw_direction in (CatalystDirection.negative.value, CatalystDirection.positive.value,
                                 CatalystDirection.mixed.value):
            direction = kw_direction

    if direction is None:
        direction = CatalystDirection.unknown.value

    has_detail = bool((summary and len(summary) > 20) or source_text)

    evidence_strength = _evidence_from_tier(source_tier, multi_source)
    strength = _strength(evidence_strength, category, direction, has_detail)
    confidence = _confidence(evidence_strength, strength, direction)

    if evidence_strength == EvidenceStrength.aggregator_only.value:
        warnings.append(
            "Aggregator-only evidence: obtain a primary (company) or regulator "
            "source before relying on this catalyst."
        )
    if direction in (CatalystDirection.positive.value, CatalystDirection.negative.value):
        warnings.append(
            "Direction label is model-derived and NOT a recommendation. "
            "Human review required."
        )

    explanation = (
        f"Model-derived label (T6): category={category}, direction={direction}, "
        f"strength={strength}, evidence={evidence_strength} (basis: {basis}). "
        "Internal research signal only — not investment advice, not a recommendation."
    )

    return CatalystClassification(
        catalyst_category=category,
        catalyst_direction=direction,
        catalyst_strength=strength,
        evidence_strength=evidence_strength,
        confidence=confidence,
        explanation=explanation,
        warnings=warnings,
    )


def apply_classification(
    event: CatalystEvent,
    *,
    multi_source: bool = False,
    source_text: str | None = None,
) -> CatalystEvent:
    """Return a copy of ``event`` with model-derived classification fields set."""
    result = classify_catalyst(
        headline=event.headline,
        summary=event.summary,
        source_tier=event.source_tier,
        form_type=event.form_type,
        item_numbers=event.item_numbers,
        normalized_event_type=event.normalized_event_type,
        multi_source=multi_source,
        source_text=source_text,
    )
    return event.model_copy(
        update={
            "catalyst_category": result.catalyst_category,
            "catalyst_direction": result.catalyst_direction,
            "catalyst_strength": result.catalyst_strength,
            "evidence_strength": result.evidence_strength,
            "confidence": result.confidence,
            "classification_explanation": result.explanation,
            "warnings": list(event.warnings) + result.warnings,
            "requires_human_review": True,
        }
    )
