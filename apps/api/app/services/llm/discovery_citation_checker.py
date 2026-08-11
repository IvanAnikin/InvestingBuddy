"""
Citation + safety enforcement for discovery-council agent output (Phase 28B).

Runs BEFORE any agent output is stored or displayed. Four jobs (mirroring the
28A checker):

  1. Safety quarantine — scan the agent's output with the shared safety scanner
     (app.services.safety_terms). If any forbidden investment-action language is
     present, the WHOLE agent output is withheld (status=failed) and replaced
     with a neutral note. No forbidden term is ever echoed forward — the
     quarantine note records the offending TIER NAMES, never the terms.

  2. Citation integrity — drop any citation id not in the evidence pack, and move
     an un-cited *material* claim into ``unsupported_claims``. A candidate note is
     grounded when it cites a valid id OR its ``candidate_ref`` is a real
     candidate id (referencing a candidate in the pack is itself a citation).

  3. Internal-action integrity — coerce a candidate note's ``internal_action`` to
     a safe default when it is not one of the allowed internal actions.

  4. Run-quality integrity — coerce the chair's ``run_quality`` to a safe default
     when it is not one of the allowed labels.

  5. Gap-attribution grounding (corrective, post-#99/#100) — an ``evidence_gaps``
     item that blames a specific cause (translation, bot protection, document
     not found, traversal exhausted, OCR required, extraction failed) is only
     kept when the run's own structured ``known_gaps`` state recorded a
     compatible cause; an ungrounded causal claim is replaced with generic
     insufficient-evidence wording. See ``app.services.llm.gap_attribution``.

Returned issue strings are guaranteed forbidden-term-free.
"""

from __future__ import annotations

from app.services import safety_terms
from app.services.llm.discovery_schemas import (
    ALLOWED_INTERNAL_ACTIONS,
    ALLOWED_RUN_QUALITY,
    DEFAULT_INTERNAL_ACTION,
    DEFAULT_RUN_QUALITY,
    STATUS_FAILED,
    DiscoveryCouncilAgentOutput,
)
from app.services.llm.gap_attribution import ground_gap_text

# Claims/rationales shorter than this are treated as non-material (labels,
# headers) and are not escalated to unsupported_claims when un-cited.
_MATERIAL_MIN_LEN = 12


def _split_citations(
    ids: list[str], evidence_ids: set[str]
) -> tuple[list[str], list[str]]:
    valid = [i for i in ids if i in evidence_ids]
    invalid = [i for i in ids if i not in evidence_ids]
    return valid, invalid


def _quarantine(
    output: DiscoveryCouncilAgentOutput, hit_count: int, tiers: list[str]
) -> DiscoveryCouncilAgentOutput:
    note = (
        f"Quarantined: {hit_count} forbidden term(s) removed by the discovery "
        f"council safety gate ({', '.join(tiers)}). Human review required."
    )
    return DiscoveryCouncilAgentOutput(
        agent_name=output.agent_name,
        status=STATUS_FAILED,
        summary=(
            "[Output withheld: the internal safety gate flagged forbidden "
            "investment-action language. Nothing from this agent was kept.]"
        ),
        candidate_notes=[],
        run_notes=[],
        evidence_gaps=[],
        unsupported_claims=[],
        safety_notes=[note],
        next_source_tasks=[],
        run_quality=None,
    )


def check_and_sanitize(
    output: DiscoveryCouncilAgentOutput,
    evidence_ids: set[str],
    candidate_ids: set[str],
    *,
    is_chair: bool = False,
    known_gaps: list[str] | None = None,
) -> tuple[DiscoveryCouncilAgentOutput, list[str]]:
    """Return a safe, citation-checked copy of ``output`` plus issue notes.

    ``known_gaps`` (the run's ``DiscoveryEvidencePack.known_gaps``) enables the
    gap-attribution grounding check (job 5). Omitted ⇒ any recognised causal
    claim in ``evidence_gaps`` is treated as ungrounded (conservative default).
    """
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

    # 2. Candidate-note integrity.
    clean_candidate_notes = []
    for note in output.candidate_notes:
        valid, invalid = _split_citations(note.citation_ids, evidence_ids)
        if invalid:
            issues.append(
                f"{output.agent_name}: dropped {len(invalid)} candidate-note "
                "citation id(s) not present in the evidence pack."
            )
        ref_ok = bool(note.candidate_ref) and note.candidate_ref in candidate_ids
        if note.candidate_ref and not ref_ok:
            issues.append(
                f"{output.agent_name}: dropped an unknown candidate_ref "
                f"'{note.candidate_ref}'."
            )
        grounded = bool(valid) or ref_ok
        material = len(note.rationale.strip()) >= _MATERIAL_MIN_LEN
        if material and not grounded:
            output.unsupported_claims.append(note.rationale)
            issues.append(
                f"{output.agent_name}: an un-cited candidate rationale was moved "
                "to unsupported_claims."
            )
            continue
        action = note.internal_action
        if action not in ALLOWED_INTERNAL_ACTIONS:
            issues.append(
                f"{output.agent_name}: internal_action '{action}' not allowed; "
                f"coerced to '{DEFAULT_INTERNAL_ACTION}'."
            )
            action = DEFAULT_INTERNAL_ACTION
        clean_candidate_notes.append(
            note.model_copy(
                update={
                    "citation_ids": valid,
                    "candidate_ref": note.candidate_ref if ref_ok else None,
                    "internal_action": action,
                }
            )
        )
    output.candidate_notes = clean_candidate_notes

    # 3. Run-note integrity.
    clean_run_notes = []
    for rn in output.run_notes:
        valid, invalid = _split_citations(rn.citation_ids, evidence_ids)
        if invalid:
            issues.append(
                f"{output.agent_name}: dropped {len(invalid)} run-note citation "
                "id(s) not present in the evidence pack."
            )
        material = len(rn.claim.strip()) >= _MATERIAL_MIN_LEN
        if material and not valid:
            output.unsupported_claims.append(rn.claim)
            issues.append(
                f"{output.agent_name}: an un-cited material run claim was moved "
                "to unsupported_claims."
            )
            continue
        clean_run_notes.append(rn.model_copy(update={"citation_ids": valid}))
    output.run_notes = clean_run_notes

    # 4. Run-quality integrity (chair only).
    if is_chair:
        if output.run_quality not in ALLOWED_RUN_QUALITY:
            issues.append(
                "discovery_chair: run_quality not in the allowed set; "
                f"coerced to '{DEFAULT_RUN_QUALITY}'."
            )
            output.run_quality = DEFAULT_RUN_QUALITY
    elif output.run_quality is not None:
        # Only the chair may set run_quality.
        output.run_quality = None

    # 5. Gap-attribution grounding — an ``evidence_gaps`` item blaming a
    # specific cause is only kept when ``known_gaps`` recorded a compatible
    # cause; an ungrounded claim is replaced with generic insufficient-
    # evidence wording. An item asserting no specific cause is never touched.
    grounded_gaps = []
    for gap in output.evidence_gaps:
        grounded_text = ground_gap_text(gap, known_gaps)
        if grounded_text != gap:
            issues.append(
                f"{output.agent_name}: an ungrounded causal gap-attribution was "
                "replaced with generic insufficient-evidence wording (no "
                "matching structured cause recorded for this run)."
            )
        grounded_gaps.append(grounded_text)
    output.evidence_gaps = grounded_gaps

    return output, issues
