"""
Citation + safety enforcement for DEEP FIELD REVIEW agent output (Slice 6D).

Runs BEFORE any agent output is stored or displayed. Five jobs (mirroring the
28A/28B checkers):

  1. Safety quarantine — scan the agent's output with the shared safety scanner
     (app.services.safety_terms). If any forbidden investment-action language is
     present, the WHOLE agent output is withheld (status=failed) and replaced
     with a neutral note. Nothing is "sanitized and passed through": a tripped
     agent is quarantined. No forbidden term is ever echoed forward — the
     quarantine note records the offending TIER NAMES, never the terms.

  2. Citation integrity — drop any citation id not in the field pack, and move an
     un-cited *material* claim into ``unsupported_claims``. A company note is
     grounded when it cites a valid id OR its ``company_ref`` is a real company
     id (referencing a company in the pack is itself a citation).

  3. Confidence integrity — coerce a confidence value outside the allowed set.

  4. Chair-verdict integrity — every priority entry must reference a REAL company
     id and carry at least one valid citation; an entry that does not is dropped
     and recorded as an issue. Duplicate placements across buckets are removed
     (first bucket wins) so a company can never appear in two tiers.

  5. Field-quality integrity — coerce the chair's ``field_quality`` to a safe
     default when it is not one of the allowed labels.

Returned issue strings are guaranteed forbidden-term-free.
"""

from __future__ import annotations

from app.services import safety_terms
from app.services.llm.field_review_schemas import (
    ALLOWED_CONFIDENCE,
    ALLOWED_FIELD_QUALITY,
    DEFAULT_CONFIDENCE,
    DEFAULT_FIELD_QUALITY,
    STATUS_FAILED,
    FieldChairVerdict,
    FieldPriorityEntry,
    FieldReviewAgentOutput,
)

# Claims/rationales shorter than this are treated as non-material (labels,
# headers) and are not escalated to unsupported_claims when un-cited.
_MATERIAL_MIN_LEN = 12

_CHAIR_BUCKETS = (
    "strongest_candidates",
    "second_tier",
    "blocked_insufficient_evidence",
)


def _split_citations(
    ids: list[str], evidence_ids: set[str]
) -> tuple[list[str], list[str]]:
    valid = [i for i in ids if i in evidence_ids]
    invalid = [i for i in ids if i not in evidence_ids]
    return valid, invalid


def _coerce_confidence(value: str) -> str:
    return value if value in ALLOWED_CONFIDENCE else DEFAULT_CONFIDENCE


def _quarantine(
    output: FieldReviewAgentOutput, hit_count: int, tiers: list[str]
) -> FieldReviewAgentOutput:
    note = (
        f"Quarantined: {hit_count} forbidden term(s) removed by the deep field "
        f"review safety gate ({', '.join(tiers)}). Human review required."
    )
    return FieldReviewAgentOutput(
        agent_name=output.agent_name,
        status=STATUS_FAILED,
        summary=(
            "[Output withheld: the internal safety gate flagged forbidden "
            "investment-action language. Nothing from this agent was kept.]"
        ),
        company_notes=[],
        field_notes=[],
        evidence_gaps=[],
        unsupported_claims=[],
        safety_notes=[note],
        next_research_tasks=[],
        chair_verdict=None,
    )


def _clean_chair_verdict(
    agent_name: str,
    verdict: FieldChairVerdict,
    evidence_ids: set[str],
    company_ids: set[str],
    issues: list[str],
) -> FieldChairVerdict:
    """Drop ungrounded / duplicate priority entries and coerce the quality label."""
    cleaned = FieldChairVerdict(
        field_uncertainties=list(verdict.field_uncertainties),
        field_quality=verdict.field_quality,
    )
    placed: set[str] = set()

    for bucket in _CHAIR_BUCKETS:
        kept: list[FieldPriorityEntry] = []
        for entry in getattr(verdict, bucket):
            ref = (entry.company_ref or "").strip()
            if ref not in company_ids:
                issues.append(
                    f"{agent_name}: dropped a {bucket} entry referencing an "
                    "unknown company_ref."
                )
                continue
            if ref in placed:
                issues.append(
                    f"{agent_name}: dropped a duplicate placement of "
                    f"'{ref}' (already placed in an earlier bucket)."
                )
                continue
            valid, invalid = _split_citations(entry.citation_ids, evidence_ids)
            if invalid:
                issues.append(
                    f"{agent_name}: dropped {len(invalid)} {bucket} citation "
                    "id(s) not present in the field pack."
                )
            # Referencing a real company IS a citation; keep it explicit so every
            # stored entry carries at least one resolvable id.
            if ref not in valid:
                valid = [ref, *valid]
            placed.add(ref)
            kept.append(
                entry.model_copy(
                    update={
                        "company_ref": ref,
                        "citation_ids": valid,
                        "confidence": _coerce_confidence(entry.confidence),
                    }
                )
            )
        setattr(cleaned, bucket, kept)

    if cleaned.field_quality not in ALLOWED_FIELD_QUALITY:
        issues.append(
            f"{agent_name}: field_quality not in the allowed set; coerced to "
            f"'{DEFAULT_FIELD_QUALITY}'."
        )
        cleaned.field_quality = DEFAULT_FIELD_QUALITY
    return cleaned


def check_and_sanitize(
    output: FieldReviewAgentOutput,
    evidence_ids: set[str],
    company_ids: set[str],
    *,
    is_chair: bool = False,
) -> tuple[FieldReviewAgentOutput, list[str]]:
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

    # 2. Company-note integrity.
    clean_company_notes = []
    for note in output.company_notes:
        valid, invalid = _split_citations(note.citation_ids, evidence_ids)
        if invalid:
            issues.append(
                f"{output.agent_name}: dropped {len(invalid)} company-note "
                "citation id(s) not present in the field pack."
            )
        ref_ok = bool(note.company_ref) and note.company_ref in company_ids
        if note.company_ref and not ref_ok:
            issues.append(
                f"{output.agent_name}: dropped an unknown company_ref "
                f"'{note.company_ref}'."
            )
        grounded = bool(valid) or ref_ok
        material = len(note.rationale.strip()) >= _MATERIAL_MIN_LEN
        if material and not grounded:
            output.unsupported_claims.append(note.rationale)
            issues.append(
                f"{output.agent_name}: an un-cited company rationale was moved "
                "to unsupported_claims."
            )
            continue
        clean_company_notes.append(
            note.model_copy(
                update={
                    "citation_ids": valid,
                    "company_ref": note.company_ref if ref_ok else None,
                    "confidence": _coerce_confidence(note.confidence),
                }
            )
        )
    output.company_notes = clean_company_notes

    # 3. Field-note integrity.
    clean_field_notes = []
    for fn in output.field_notes:
        valid, invalid = _split_citations(fn.citation_ids, evidence_ids)
        if invalid:
            issues.append(
                f"{output.agent_name}: dropped {len(invalid)} field-note "
                "citation id(s) not present in the field pack."
            )
        material = len(fn.claim.strip()) >= _MATERIAL_MIN_LEN
        if material and not valid:
            output.unsupported_claims.append(fn.claim)
            issues.append(
                f"{output.agent_name}: an un-cited material field claim was "
                "moved to unsupported_claims."
            )
            continue
        clean_field_notes.append(
            fn.model_copy(
                update={
                    "citation_ids": valid,
                    "confidence": _coerce_confidence(fn.confidence),
                }
            )
        )
    output.field_notes = clean_field_notes

    # 4/5. Chair verdict integrity (chair only).
    if is_chair:
        verdict = output.chair_verdict
        if verdict is None:
            issues.append(
                "field_chair: no chair_verdict was returned; an empty verdict "
                f"with field_quality '{DEFAULT_FIELD_QUALITY}' was recorded."
            )
            output.chair_verdict = FieldChairVerdict(
                field_quality=DEFAULT_FIELD_QUALITY
            )
        else:
            output.chair_verdict = _clean_chair_verdict(
                output.agent_name, verdict, evidence_ids, company_ids, issues
            )
    elif output.chair_verdict is not None:
        # Only the chair may set a verdict.
        output.chair_verdict = None

    return output, issues
