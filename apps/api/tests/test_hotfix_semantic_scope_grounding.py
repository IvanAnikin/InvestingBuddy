"""Hotfix: semantic (scope/period/topic) grounding for council citations.

Root cause pinned by these tests: evidence carried no ``scope``/``period``, so a
claim could cite two VALID evidence ids that were individually real but
described DIFFERENT things (e.g. a Group-level figure and a segment-level
figure, or two different reporting periods) and the citation checker had no way
to detect the resulting silent misattribution. Two real regressions motivated
this fix:

  1. "Group operating profit EUR4.492bn" was combined with a SEGMENT operating
     margin ("Jewellery Maisons operating margin 30.5%") into one claim that
     reassigned the segment percentage to the Group.
  2. A segment operating-result figure ("EUR107m", Specialist Watchmakers) was
     relabelled as "quantified macroeconomic headwinds" — a topic invented with
     no basis in the cited evidence's own text.

The fix adds a best-effort, generic (never company-specific) ``scope`` field to
evidence, threads it (plus ``period``) into the council's evidence schema, and
adds a deterministic post-citation-check in
``app.services.llm.citation_checker.check_and_sanitize`` that DROPS (never
rewrites) a claim citing evidence that is semantically incompatible.

No real company name is used in any PRODUCTION code touched by this fix — the
Richemont-style names below appear ONLY in test fixtures/docstrings, to model
the real regression shape.
"""

from __future__ import annotations

from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.schemas import (
    AgentKeyPoint,
    CouncilAgentOutput,
)
from app.services.llm.schemas import EvidenceItem as CouncilEvidenceItem
from app.services.sources.primary_document_extractor import _infer_scope


def _evidence(
    id: str,
    *,
    scope: str | None = None,
    period: str | None = None,
    excerpt: str | None = None,
) -> CouncilEvidenceItem:
    return CouncilEvidenceItem(
        id=id,
        source_tier="T1_primary_filing",
        source_type="company_ir_financial_fact",
        excerpt=excerpt,
        scope=scope,
        period=period,
    )


def _kp(claim: str, citation_ids: list[str]) -> AgentKeyPoint:
    return AgentKeyPoint(
        claim=claim,
        citation_ids=citation_ids,
        confidence="medium",
        data_quality="B",
    )


def _output(key_points: list[AgentKeyPoint]) -> CouncilAgentOutput:
    return CouncilAgentOutput(
        agent_name="financial_analyst",
        status="completed",
        summary="Test agent output.",
        key_points=key_points,
    )


# --------------------------------------------------------------------------- #
# 1. Cross-scope combination (Group profit + segment margin) — CFR-style bug.
# --------------------------------------------------------------------------- #


def test_1_cross_scope_group_and_segment_claim_is_dropped() -> None:
    evidence = {
        "E1": _evidence(
            "E1", scope="group", excerpt="Group operating profit was EUR4.492bn."
        ),
        "E2": _evidence(
            "E2",
            scope="Jewellery Maisons operating margin",
            excerpt="Jewellery Maisons operating margin was 30.5%.",
        ),
    }
    kp = _kp(
        "Group operating profit was EUR4.49bn with an operating margin of 30.5%.",
        ["E1", "E2"],
    )
    output = _output([kp])
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("semantic mismatch" in i for i in issues)


# --------------------------------------------------------------------------- #
# 2. Macro/category mismatch (segment result relabelled as macro headwinds).
# --------------------------------------------------------------------------- #


def test_2_macro_relabel_of_segment_figure_is_dropped() -> None:
    evidence = {
        "E3": _evidence(
            "E3",
            scope="Specialist Watchmakers operating result",
            excerpt="Specialist Watchmakers operating result was EUR107m, "
            "reflecting lower sell-in volumes.",
        ),
    }
    kp = _kp(
        "Quantified macroeconomic headwinds reduced results by EUR107m.",
        ["E3"],
    )
    output = _output([kp])
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("semantic mismatch" in i for i in issues)


# --------------------------------------------------------------------------- #
# 3. Cross-period combination, same scope.
# --------------------------------------------------------------------------- #


def test_3_cross_period_same_scope_claim_is_dropped() -> None:
    evidence = {
        "E4": _evidence("E4", scope="group", period="FY2023", excerpt="Revenue was EUR20.6bn in FY2023."),
        "E5": _evidence("E5", scope="group", period="FY2024", excerpt="Revenue was EUR22.4bn in FY2024."),
    }
    kp = _kp("Revenue grew from EUR20.6bn to EUR22.4bn.", ["E4", "E5"])
    output = _output([kp])
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("semantic mismatch" in i for i in issues)


# --------------------------------------------------------------------------- #
# 4. Positive case: same scope + same period, two numbers — must PASS.
# --------------------------------------------------------------------------- #


def test_4_same_scope_same_period_claim_survives() -> None:
    evidence = {
        "E6": _evidence("E6", scope="group", period="FY2024", excerpt="Revenue was EUR22.4bn."),
        "E7": _evidence("E7", scope="group", period="FY2024", excerpt="Operating profit was EUR4.5bn."),
    }
    kp = _kp(
        "Revenue was EUR22.4bn and operating profit was EUR4.5bn.",
        ["E6", "E7"],
    )
    output = _output([kp])
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert len(sanitized.key_points) == 1
    assert sanitized.key_points[0].claim == kp.claim
    assert not any("semantic mismatch" in i for i in issues)


def test_4b_unknown_scope_evidence_never_flagged() -> None:
    """Evidence with scope/period left unknown (None) must never trip the check
    — an under-informative pack must not become MORE restrictive than an
    informative one."""
    evidence = {
        "E8": _evidence("E8", excerpt="Revenue was EUR22.4bn."),
        "E9": _evidence("E9", excerpt="Operating profit was EUR4.5bn."),
    }
    kp = _kp(
        "Revenue was EUR22.4bn and operating profit was EUR4.5bn.",
        ["E8", "E9"],
    )
    output = _output([kp])
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert len(sanitized.key_points) == 1
    assert not any("semantic mismatch" in i for i in issues)


# --------------------------------------------------------------------------- #
# 5. A SECOND, distinct synthetic cross-scope case (different metric pair).
# --------------------------------------------------------------------------- #


def test_5_synthetic_group_debt_vs_regional_cash_is_dropped() -> None:
    evidence = {
        "E10": _evidence(
            "E10", scope="group", excerpt="Group total debt was $12.0bn."
        ),
        "E11": _evidence(
            "E11",
            scope="Region X segment",
            excerpt="Region X segment cash position was $3.0bn.",
        ),
    }
    kp = _kp(
        "Group total debt was $12.0bn against a segment cash position of $3.0bn.",
        ["E10", "E11"],
    )
    output = _output([kp])
    sanitized, issues = check_and_sanitize(output, set(evidence), evidence)
    assert sanitized.key_points == []
    assert any("semantic mismatch" in i for i in issues)


# --------------------------------------------------------------------------- #
# 6. Backward compatibility: no evidence_by_id supplied -> semantic check never
#    fires (existing callers unaffected).
# --------------------------------------------------------------------------- #


def test_6_backward_compatible_without_evidence_by_id() -> None:
    kp = _kp(
        "Group operating profit was EUR4.49bn with an operating margin of 30.5%.",
        ["E1", "E2"],
    )
    output = _output([kp])
    sanitized, issues = check_and_sanitize(output, {"E1", "E2"})
    assert len(sanitized.key_points) == 1
    assert not any("semantic mismatch" in i for i in issues)


# --------------------------------------------------------------------------- #
# 7. _infer_scope heuristic — generic, structure-only, never company-specific.
# --------------------------------------------------------------------------- #


def test_7_infer_scope_segment_heading() -> None:
    assert _infer_scope("Segment information") is not None
    assert _infer_scope("Revenue by region") is not None
    assert _infer_scope("Reportable segment results") is not None


def test_8_infer_scope_group_heading() -> None:
    assert _infer_scope("Consolidated income statement") == "group"
    assert _infer_scope("Group performance overview") == "group"


def test_9_infer_scope_unknown_heading_returns_none() -> None:
    assert _infer_scope(None) is None
    assert _infer_scope("") is None
    assert _infer_scope("Our history and heritage") is None
