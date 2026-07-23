"""
Citation + safety enforcement for council agent output.

Runs BEFORE any agent output is merged into a report or displayed. Three jobs:

  1. Safety quarantine — scan the agent's output with the shared safety scanner
     (app.services.safety_terms). If any forbidden investment-action language is
     present, the WHOLE agent output is withheld (status=failed) and replaced
     with a neutral note. No forbidden term is ever echoed forward (not even
     into safety_notes), so the report-level gate cannot re-trip on it.

  2. Citation integrity — drop any citation id that is not in the evidence pack,
     and move an un-cited *material* factual claim into ``unsupported_claims``.
     Gaps/risks may legitimately be un-cited and are left alone.

  3. Committee-label integrity — coerce the chair's label to a safe default if
     it is not one of the allowed internal labels.

Returned issue strings are guaranteed forbidden-term-free.
"""

from __future__ import annotations

from app.services import safety_terms
from app.services.llm.schemas import (
    AGENT_COMMITTEE_CHAIR,
    ALLOWED_COMMITTEE_LABELS,
    DEFAULT_COMMITTEE_LABEL,
    STATUS_FAILED,
    CouncilAgentOutput,
)

# Claims shorter than this are treated as non-material (labels, headers) and are
# not escalated to unsupported_claims when un-cited.
_MATERIAL_MIN_LEN = 12


def _split_citations(
    ids: list[str], evidence_ids: set[str]
) -> tuple[list[str], list[str]]:
    valid = [i for i in ids if i in evidence_ids]
    invalid = [i for i in ids if i not in evidence_ids]
    return valid, invalid


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
    output: CouncilAgentOutput, evidence_ids: set[str]
) -> tuple[CouncilAgentOutput, list[str]]:
    """Return a safe, citation-checked copy of ``output`` plus issue notes."""
    issues: list[str] = []

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
        clean_points.append(kp.model_copy(update={"citation_ids": valid}))
    output.key_points = clean_points

    # 3. Citation integrity for risks/gaps (un-cited is allowed; bad ids dropped).
    clean_risks = []
    for rg in output.risks_or_gaps:
        valid, invalid = _split_citations(rg.citation_ids, evidence_ids)
        if invalid:
            issues.append(
                f"{output.agent_name}: dropped {len(invalid)} risk citation id(s) "
                "not present in the evidence pack."
            )
        clean_risks.append(rg.model_copy(update={"citation_ids": valid}))
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
