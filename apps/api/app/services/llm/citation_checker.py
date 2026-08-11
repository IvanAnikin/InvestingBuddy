"""
Citation + safety enforcement for council agent output.

Runs BEFORE any agent output is merged into a report or displayed. Four jobs:

  1. Safety quarantine — scan the agent's output with the shared safety scanner
     (app.services.safety_terms). If any forbidden investment-action language is
     present, the WHOLE agent output is withheld (status=failed) and replaced
     with a neutral note. No forbidden term is ever echoed forward (not even
     into safety_notes), so the report-level gate cannot re-trip on it.

  2. Citation integrity — drop any citation id that is not in the evidence pack,
     and move an un-cited *material* factual claim into ``unsupported_claims``.
     Gaps/risks may legitimately be un-cited and are left alone.

  3. Semantic-grounding integrity (Phase 32A hotfix) — a claim can cite VALID
     evidence ids yet still silently combine numbers/topics that do not belong
     together: a multi-number claim citing evidence from two different scopes
     (e.g. "group" vs a segment) or two different reporting periods, or a claim
     that invents a topic label (e.g. "macroeconomic headwinds") for a number
     whose own cited source text never discusses that topic. Such a claim is
     DROPPED entirely (never auto-rewritten — an omitted claim is always
     preferable to a manufactured one) and logged for observability.

  4. Committee-label integrity — coerce the chair's label to a safe default if
     it is not one of the allowed internal labels.

  5. Gap-attribution grounding (corrective, post-#99/#100) — a risk/gap item
     that blames a specific cause (translation, bot protection, document not
     found, traversal exhausted, OCR required, extraction failed) is only kept
     when the run's own structured ``known_gaps`` state recorded a compatible
     cause; an ungrounded causal claim is replaced with generic
     insufficient-evidence wording. See ``app.services.llm.gap_attribution``.

Returned issue strings are guaranteed forbidden-term-free.
"""

from __future__ import annotations

import re
from typing import Any

from app.services import safety_terms
from app.services.llm.gap_attribution import ground_gap_text
from app.services.llm.schemas import (
    AGENT_COMMITTEE_CHAIR,
    ALLOWED_COMMITTEE_LABELS,
    DEFAULT_COMMITTEE_LABEL,
    STATUS_FAILED,
    AgentKeyPoint,
    CouncilAgentOutput,
)

# Claims shorter than this are treated as non-material (labels, headers) and are
# not escalated to unsupported_claims when un-cited.
_MATERIAL_MIN_LEN = 12

# A numeric token: a number optionally followed by a magnitude/percent suffix.
# Deliberately loose — used only to COUNT how many distinct figures a claim
# asserts, not to parse them.
_NUMERIC_TOKEN_RE = re.compile(
    r"\d[\d.,]*\s*(?:%|bn|billion|m|mn|million|k|thousand)?", re.IGNORECASE
)

# Generic macro/category vocabulary (never a company/segment name) used ONLY to
# detect a claim asserting a macro-economic topic that its own cited evidence
# never actually discusses.
_MACRO_TOPIC_WORDS = frozenset(
    {
        "macroeconomic",
        "macro-economic",
        "currency headwind",
        "fx headwind",
        "inflation",
        "geopolitical",
        "consumer demand headwind",
        "interest rate headwind",
    }
)


def _split_citations(
    ids: list[str], evidence_ids: set[str]
) -> tuple[list[str], list[str]]:
    valid = [i for i in ids if i in evidence_ids]
    invalid = [i for i in ids if i not in evidence_ids]
    return valid, invalid


def _numeric_tokens(text: str) -> list[str]:
    """Distinct numeric figures a claim asserts (a loose, counting-only regex)."""
    return [m.group(0) for m in _NUMERIC_TOKEN_RE.finditer(text)]


def _cited_evidence(
    key_point: AgentKeyPoint, evidence_by_id: dict[str, Any]
) -> list[Any]:
    return [
        evidence_by_id[cid]
        for cid in key_point.citation_ids
        if cid in evidence_by_id
    ]


def _violates_scope_or_period_compatibility(
    key_point: AgentKeyPoint, evidence_by_id: dict[str, Any]
) -> bool:
    """True when a multi-number claim silently mixes incompatible evidence.

    A claim asserting two or more numeric figures whose cited evidence items
    carry MORE THAN ONE distinct known ``scope`` (e.g. "group" and a segment
    heading) or MORE THAN ONE distinct known ``period`` is treated as an
    unsafe cross-scope / cross-period combination. Evidence with an unknown
    (``None``) scope/period never triggers this — the check only fires when
    scope/period is actually known and genuinely differs.
    """
    cited = _cited_evidence(key_point, evidence_by_id)
    scopes = {e.scope for e in cited if getattr(e, "scope", None)}
    periods = {e.period for e in cited if getattr(e, "period", None)}
    numeric_count = len(_numeric_tokens(key_point.claim))
    if numeric_count >= 2:
        if len(scopes) > 1:
            return True
        if len(periods) > 1:
            return True
    return False


def _violates_macro_category_match(
    key_point: AgentKeyPoint, evidence_by_id: dict[str, Any]
) -> bool:
    """True when a claim invents a macro-topic label for segment-only evidence.

    Fires only when ALL of the following hold: (1) the claim text uses macro
    vocabulary; (2) the claim cites at least one evidence item; (3) EVERY cited
    item has a KNOWN, non-"group" scope (a specific segment/business-unit); and
    (4) NONE of the cited items' own excerpt text uses that same macro
    vocabulary. A claim citing evidence with an unknown scope, or evidence whose
    own text actually discusses the topic, is never flagged.
    """
    claim_low = key_point.claim.lower()
    if not any(w in claim_low for w in _MACRO_TOPIC_WORDS):
        return False
    cited = _cited_evidence(key_point, evidence_by_id)
    if not cited:
        return False
    if not all(
        getattr(e, "scope", None) not in (None, "", "group") for e in cited
    ):
        return False
    for e in cited:
        excerpt_low = (getattr(e, "excerpt", None) or "").lower()
        if any(w in excerpt_low for w in _MACRO_TOPIC_WORDS):
            return False
    return True


def _quarantine(output: CouncilAgentOutput, hit_count: int, tiers: list[str]) -> CouncilAgentOutput:
    note = (
        f"Quarantined: {hit_count} forbidden term(s) removed by the council "
        f"safety gate ({', '.join(tiers)}). Human review required."
    )
    return CouncilAgentOutput(
        agent_name=output.agent_name,
        status=STATUS_FAILED,
        summary=(
            "[Output withheld: the internal safety gate flagged forbidden "
            "investment-action language. Nothing from this agent was kept.]"
        ),
        key_points=[],
        risks_or_gaps=[],
        unsupported_claims=[],
        safety_notes=[note],
        committee_label=None,
    )


def check_and_sanitize(
    output: CouncilAgentOutput,
    evidence_ids: set[str],
    evidence_by_id: dict[str, Any] | None = None,
    known_gaps: list[str] | None = None,
) -> tuple[CouncilAgentOutput, list[str]]:
    """Return a safe, citation-checked copy of ``output`` plus issue notes.

    ``evidence_by_id`` (optional, id -> evidence-pack ``EvidenceItem``) enables
    the semantic-grounding check (job 3, above). When omitted (the historical
    call shape), citation-id membership + safety + committee-label checks still
    run exactly as before; the semantic check simply never fires, so it never
    changes existing behaviour for a caller that has not been updated.

    ``known_gaps`` (optional, the run's ``EvidencePack.known_gaps``) enables
    the gap-attribution grounding check (job 5). Omitted ⇒ any recognised
    causal claim is treated as ungrounded (never assumed true), which is the
    conservative/safe default for a caller that has not been updated.
    """
    issues: list[str] = []
    evidence_by_id = evidence_by_id or {}

    # 1. Safety first. Scan the raw model output; quarantine on any hit.
    hits = safety_terms.scan_value(output.model_dump())
    if hits:
        tiers = sorted({h.tier for h in hits})
        issues.append(
            f"{output.agent_name}: output quarantined by safety gate "
            f"({len(hits)} hit(s))."
        )
        return _quarantine(output, len(hits), tiers), issues

    # 2. Citation integrity for key points.
    clean_points = []
    dropped_for_semantic_mismatch = 0
    for kp in output.key_points:
        valid, invalid = _split_citations(kp.citation_ids, evidence_ids)
        if invalid:
            issues.append(
                f"{output.agent_name}: dropped {len(invalid)} citation id(s) "
                "not present in the evidence pack."
            )
        material = len(kp.claim.strip()) >= _MATERIAL_MIN_LEN
        if not valid and material and not kp.is_limitation and not kp.is_model_inference:
            output.unsupported_claims.append(kp.claim)
            issues.append(
                f"{output.agent_name}: an un-cited material claim was moved to "
                "unsupported_claims."
            )
            continue
        candidate = kp.model_copy(update={"citation_ids": valid})

        # 3. Semantic-grounding check — valid citation ids are NOT enough; the
        # cited evidence must also be compatible with each other. Only runs
        # when the caller supplied evidence objects (evidence_by_id).
        if evidence_by_id and (
            _violates_scope_or_period_compatibility(candidate, evidence_by_id)
            or _violates_macro_category_match(candidate, evidence_by_id)
        ):
            dropped_for_semantic_mismatch += 1
            issues.append(
                f"{output.agent_name}: dropped a claim citing evidence with "
                "incompatible scope/period, or asserting a topic its cited "
                "evidence does not support (semantic mismatch)."
            )
            continue

        clean_points.append(candidate)
    output.key_points = clean_points
    if dropped_for_semantic_mismatch:
        issues.append(
            f"{output.agent_name}: {dropped_for_semantic_mismatch} claim(s) "
            "dropped_for_semantic_mismatch."
        )

    # 2b. Citation integrity for risks/gaps (un-cited is allowed; bad ids dropped).
    # 5. Gap-attribution grounding — a causal claim (e.g. "untranslated French
    # filings") is only kept when ``known_gaps`` recorded a compatible cause;
    # an ungrounded claim is replaced with generic insufficient-evidence
    # wording. A gap item asserting no specific cause is never touched.
    clean_risks = []
    for rg in output.risks_or_gaps:
        valid, invalid = _split_citations(rg.citation_ids, evidence_ids)
        if invalid:
            issues.append(
                f"{output.agent_name}: dropped {len(invalid)} risk citation id(s) "
                "not present in the evidence pack."
            )
        grounded_item = ground_gap_text(rg.item, known_gaps)
        if grounded_item != rg.item:
            issues.append(
                f"{output.agent_name}: an ungrounded causal gap-attribution was "
                "replaced with generic insufficient-evidence wording (no "
                "matching structured cause recorded for this run)."
            )
        clean_risks.append(
            rg.model_copy(update={"citation_ids": valid, "item": grounded_item})
        )
    output.risks_or_gaps = clean_risks

    # 4. Committee-label integrity.
    if output.agent_name == AGENT_COMMITTEE_CHAIR:
        if output.committee_label not in ALLOWED_COMMITTEE_LABELS:
            issues.append(
                "committee_chair: label not in the allowed internal set; "
                f"coerced to '{DEFAULT_COMMITTEE_LABEL}'."
            )
            output.committee_label = DEFAULT_COMMITTEE_LABEL

    return output, issues
