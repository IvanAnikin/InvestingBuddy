"""
Phase 22: Research Judge Service — deterministic internal quality evaluator.

Evaluates research report quality using existing stored data only.
Produces internal quality scores — NOT public investment recommendations.

IMPORTANT CONSTRAINTS:
  - No BUY/SELL/HOLD/WATCH public recommendations are produced.
  - No price targets, fair values, or upside percentages are produced.
  - No LLM calls — fully deterministic and offline-capable.
  - CI passes without any Azure OpenAI or EODHD keys.
  - Human review is required before any action on judge output.
  - Judge output is internal metadata only.

Allowed judge statuses (internal evaluation only):
  insufficient_data | useful_research | needs_better_sources |
  poor_evidence_quality | outcome_inconclusive | outcome_review_required

Safety gate: any output containing forbidden terms is flagged and must not
be marked final without human review.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.schemas.backtesting import (
    BACKTESTING_VERSION,
    JudgeEvaluation,
)
from app.services import safety_terms

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Scoring weights
# ---------------------------------------------------------------------------

_WEIGHT_EVIDENCE = 0.35
_WEIGHT_RISK = 0.25
_WEIGHT_DATA_COMPLETENESS = 0.25
_WEIGHT_OUTCOME_ALIGNMENT = 0.15

# Minimum source counts for scoring
_MIN_SOURCES_FOR_FULL_SCORE = 5
_MIN_CITATIONS_FOR_FULL_SCORE = 3


# ---------------------------------------------------------------------------
# Safety gate
# ---------------------------------------------------------------------------


def _scan_for_forbidden_terms(text: str) -> list[str]:
    """Return any forbidden terms found in the text.

    Delegates to the shared three-tier scanner in ``app.services.safety_terms``.
    The previous implementation claimed a word boundary in its comment but did
    not implement one, so "ENEOS Holdings" was flagged for "HOLD".
    """
    return safety_terms.hit_terms(safety_terms.scan_text(text))


def _safety_scan_dict(data: dict[str, Any]) -> list[str]:
    """Recursively scan dict values for forbidden terms."""
    found: list[str] = []
    for value in data.values():
        if isinstance(value, str):
            found.extend(_scan_for_forbidden_terms(value))
        elif isinstance(value, dict):
            found.extend(_safety_scan_dict(value))
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    found.extend(_scan_for_forbidden_terms(item))
    return list(set(found))


# ---------------------------------------------------------------------------
# Sub-scorers
# ---------------------------------------------------------------------------


def _score_evidence_quality(report_data: dict[str, Any]) -> tuple[float, list[str]]:
    """Score evidence/source quality from stored report metadata."""
    notes: list[str] = []
    score = 0.0

    source_summary = report_data.get("source_summary_json") or {}
    source_count = source_summary.get("source_count", 0)
    citation_count = source_summary.get("citation_count", 0)

    if source_count >= _MIN_SOURCES_FOR_FULL_SCORE:
        score += 0.5
    elif source_count > 0:
        score += 0.25
        notes.append(
            f"Only {source_count} sources found; {_MIN_SOURCES_FOR_FULL_SCORE} recommended."
        )
    else:
        notes.append("No sources found in source_summary_json.")

    if citation_count >= _MIN_CITATIONS_FOR_FULL_SCORE:
        score += 0.5
    elif citation_count > 0:
        score += 0.25
        notes.append(
            f"Only {citation_count} citations; {_MIN_CITATIONS_FOR_FULL_SCORE} recommended."
        )
    else:
        notes.append("No citations found in source_summary_json.")

    return round(score, 4), notes


def _score_risk_coverage(report_data: dict[str, Any]) -> tuple[float, list[str]]:
    """Score risk section coverage from stored report content."""
    notes: list[str] = []
    score = 0.0

    content = report_data.get("content_markdown") or ""
    # Check for risk-related sections
    has_risk = bool(re.search(r"(risk|downside|bear.case|headwind)", content, re.IGNORECASE))
    has_bull = bool(
        re.search(r"(bull.case|upside.case|opportunity|catalyst)", content, re.IGNORECASE)
    )

    if has_risk:
        score += 0.5
    else:
        notes.append("No risk/downside section detected in content.")

    if has_bull:
        score += 0.5
    else:
        notes.append("No bull-case/catalyst section detected in content.")

    # Also check safety_validation_json
    safety_json = report_data.get("safety_validation_json") or {}
    if safety_json.get("passed") is False:
        notes.append("Safety validation failed on report — review required.")
        score = max(score - 0.2, 0.0)

    return round(score, 4), notes


def _score_data_completeness(report_data: dict[str, Any]) -> tuple[float, list[str]]:
    """Score data completeness from stored report schema validation."""
    notes: list[str] = []

    schema_json = report_data.get("schema_validation_json") or {}
    if schema_json.get("is_valid"):
        return 1.0, notes

    errors = schema_json.get("errors", [])
    if errors:
        notes.append(f"Schema validation errors: {len(errors)} — completeness reduced.")
        score = max(0.0, 1.0 - (len(errors) * 0.15))
        return round(score, 4), notes

    # No schema data — check basic content presence
    has_content = bool(report_data.get("content_markdown") or report_data.get("summary"))
    if has_content:
        notes.append("No schema validation record found; basic content present.")
        return 0.5, notes

    notes.append("No content or schema validation record found.")
    return 0.0, notes


def _score_outcome_alignment(outcome_data: dict[str, Any] | None) -> tuple[float, list[str]]:
    """Evaluate outcome alignment if historical data is available.

    This is a simple heuristic: if data was available and the absolute return
    was positive (company improved over evaluation period), alignment is higher.

    IMPORTANT: This does NOT generate a recommendation. It only checks
    whether the thesis direction (if one can be inferred) was consistent with
    the historical outcome for quality evaluation purposes.
    """
    if not outcome_data:
        return 0.0, ["No historical outcome data provided — alignment not evaluated."]

    if not outcome_data.get("data_available"):
        return 0.0, ["Historical data not available — outcome alignment skipped."]

    absolute_return = outcome_data.get("absolute_return")
    if absolute_return is None:
        return 0.0, ["absolute_return missing from outcome data."]

    # Simple heuristic: data was available, return was computable
    # We give partial credit just for having evaluable data
    score = 0.5  # baseline for data availability
    notes: list[str] = []
    notes.append(
        f"Historical evaluation period: absolute_return={absolute_return:.4f}. "
        "Score reflects data availability only — not a recommendation."
    )

    return round(score, 4), notes


# ---------------------------------------------------------------------------
# Status determination
# ---------------------------------------------------------------------------


def _determine_judge_status(
    judge_score: float,
    evidence_score: float,
    data_completeness_score: float,
    forbidden_found: list[str],
    missing_data: list[str],
) -> str:
    """Map numeric scores to an internal judge status.

    Returns one of the ALLOWED_JUDGE_STATUSES — never a public recommendation.
    """
    if forbidden_found:
        return "outcome_review_required"

    if data_completeness_score < 0.1 and evidence_score < 0.1:
        return "insufficient_data"

    if evidence_score < 0.25:
        return "needs_better_sources"

    if judge_score < 0.3:
        return "poor_evidence_quality"

    if judge_score >= 0.65 and data_completeness_score >= 0.5:
        return "useful_research"

    if missing_data:
        return "outcome_inconclusive"

    return "needs_better_sources"


# ---------------------------------------------------------------------------
# Main judge service
# ---------------------------------------------------------------------------


class ResearchJudgeService:
    """Deterministic internal research quality judge.

    Evaluates stored report data and produces internal quality scores.
    No LLM calls. No network calls. Fully offline-capable.

    Output is internal metadata only — not investment advice.
    """

    def evaluate_report(
        self,
        report_id: uuid.UUID | None,
        report_data: dict[str, Any],
        outcome_data: dict[str, Any] | None = None,
        company_id: uuid.UUID | None = None,
        ticker: str | None = None,
    ) -> JudgeEvaluation:
        """Evaluate a single research report and return a JudgeEvaluation.

        Args:
            report_id: UUID of the report being evaluated.
            report_data: Dict of report fields (from DB model or mock).
            outcome_data: Optional historical outcome dict from provider.
            company_id: Optional company UUID.
            ticker: Optional ticker symbol.

        Returns:
            JudgeEvaluation with internal quality scores and status.
            Never contains public recommendations or price targets.
        """
        all_notes: list[str] = []
        all_missing: list[str] = []
        all_warnings: list[str] = []

        # Evidence quality
        ev_score, ev_notes = _score_evidence_quality(report_data)
        all_notes.extend(ev_notes)

        # Risk coverage
        risk_score, risk_notes = _score_risk_coverage(report_data)
        all_notes.extend(risk_notes)

        # Data completeness
        dc_score, dc_notes = _score_data_completeness(report_data)
        all_notes.extend(dc_notes)
        if dc_score < 0.5:
            all_missing.append("Incomplete schema validation or content.")

        # Outcome alignment (only if outcome_data provided)
        oa_score, oa_notes = _score_outcome_alignment(outcome_data)
        all_notes.extend(oa_notes)
        if outcome_data and not outcome_data.get("data_available"):
            all_missing.append("Historical outcome data unavailable.")

        # Aggregate score (weighted)
        judge_score = round(
            _WEIGHT_EVIDENCE * ev_score
            + _WEIGHT_RISK * risk_score
            + _WEIGHT_DATA_COMPLETENESS * dc_score
            + _WEIGHT_OUTCOME_ALIGNMENT * oa_score,
            4,
        )

        # Safety scan on the report content and notes
        combined_text = " ".join([
            report_data.get("content_markdown") or "",
            report_data.get("summary") or "",
            report_data.get("title") or "",
        ] + all_notes)
        forbidden_found = _scan_for_forbidden_terms(combined_text)

        if forbidden_found:
            all_warnings.append(
                f"Forbidden output terms detected in report content: {forbidden_found}. "
                "Human review required before marking evaluation final."
            )

        if outcome_data:
            oc_warnings = outcome_data.get("warnings") or []
            all_warnings.extend(oc_warnings)

        # Determine internal status
        status = _determine_judge_status(
            judge_score=judge_score,
            evidence_score=ev_score,
            data_completeness_score=dc_score,
            forbidden_found=forbidden_found,
            missing_data=all_missing,
        )

        # Lessons learned
        lessons: list[str] = []
        if ev_score < 0.5:
            lessons.append("Improve source diversity and citation coverage.")
        if risk_score < 0.5:
            lessons.append("Ensure risk/downside and bull/bear sections are present.")
        if dc_score < 0.5:
            lessons.append("Schema validation failed or content is incomplete.")
        if not lessons:
            lessons.append("Research quality appears adequate for internal review.")

        return JudgeEvaluation(
            report_id=report_id,
            company_id=company_id,
            ticker=ticker,
            judge_score=judge_score,
            evidence_quality_score=ev_score,
            risk_coverage_score=risk_score,
            outcome_alignment_score=oa_score,
            data_completeness_score=dc_score,
            judge_status=status,
            calibration_notes=all_notes,
            lessons_learned=lessons,
            missing_data=all_missing,
            warnings=all_warnings,
            safety_passed=len(forbidden_found) == 0,
            forbidden_terms_found=forbidden_found,
            evaluated_at=datetime.now(timezone.utc),
            judge_version=BACKTESTING_VERSION,
        )

    def normalize_score(self, raw_score: float) -> float:
        """Clamp score to [0.0, 1.0]."""
        return round(max(0.0, min(1.0, raw_score)), 4)

    def scan_output_for_forbidden_terms(self, text: str) -> list[str]:
        """Public helper to scan any text for forbidden output terms."""
        return _scan_for_forbidden_terms(text)
