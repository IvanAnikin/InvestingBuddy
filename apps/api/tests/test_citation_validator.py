"""
Unit tests for CitationValidator agent skeleton.

No database or LLM required — pure logic tests.
"""

from app.agents.validation.citation_validator import (
    CitationValidator,
    CitationValidatorInput,
    run_citation_validator,
)


def _placeholder_analysis(rating: str = "WATCH") -> dict:
    return {
        "ticker": "VOW3",
        "company_name": "Volkswagen AG",
        "rating": rating,
        "confidence_score": 0.50,
        "thesis": "Company is a placeholder candidate for research.",
        "financial_metrics": {},
        "is_placeholder": True,
    }


def _real_analysis(has_metrics: bool = False) -> dict:
    analysis = {
        "ticker": "VOW3",
        "company_name": "Volkswagen AG",
        "rating": "WATCH",
        "confidence_score": 0.50,
        "thesis": "Real thesis here.",
        "financial_metrics": {},
        "is_placeholder": False,
    }
    if has_metrics:
        analysis["financial_metrics"] = {
            "market_cap": {"value": 60_000_000_000, "currency": "EUR", "source_id": "src-001"}
        }
    return analysis


def _make_citation(claim_text: str | None = "thesis") -> dict:
    return {
        "claim_text": claim_text,
        "source_id": "44444444-4444-4444-4444-444444444444",
    }


# ---------------------------------------------------------------------------
# Placeholder analysis
# ---------------------------------------------------------------------------


def test_placeholder_analysis_status_is_warnings() -> None:
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=_placeholder_analysis(),
            citations=[_make_citation("thesis")],
        )
    )
    assert result.status == "warnings"
    assert result.is_placeholder is True


def test_placeholder_analysis_has_warning_message() -> None:
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=_placeholder_analysis(),
            citations=[],
        )
    )
    assert any("is_placeholder" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Real analysis — no citations
# ---------------------------------------------------------------------------


def test_real_analysis_no_citations_is_failed() -> None:
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=_real_analysis(),
            citations=[],
        )
    )
    assert result.status == "failed"
    assert result.is_placeholder is False


def test_real_analysis_missing_thesis_in_missing_citations() -> None:
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=_real_analysis(),
            citations=[],
        )
    )
    sections = [m["section"] for m in result.missing_citations]
    assert "thesis" in sections
    assert "rating" in sections


# ---------------------------------------------------------------------------
# Real analysis — with thesis citation
# ---------------------------------------------------------------------------


def test_real_analysis_with_thesis_citation_approves_thesis() -> None:
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=_real_analysis(),
            citations=[_make_citation("thesis")],
        )
    )
    assert "thesis" in result.approved_claims


def test_real_analysis_empty_thesis_adds_warning() -> None:
    analysis = _real_analysis()
    analysis["thesis"] = ""
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=analysis,
            citations=[],
        )
    )
    assert any("thesis field is empty" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# Financial metrics
# ---------------------------------------------------------------------------


def test_metric_with_source_id_is_approved() -> None:
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=_real_analysis(has_metrics=True),
            citations=[_make_citation("thesis")],
        )
    )
    assert "financial_metrics.market_cap" in result.approved_claims


def test_metric_without_source_id_is_missing() -> None:
    analysis = _real_analysis()
    analysis["financial_metrics"] = {
        "market_cap": {"value": 60_000_000_000, "currency": "EUR"}  # no source_id
    }
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=analysis,
            citations=[_make_citation("thesis")],
        )
    )
    sections = [m["section"] for m in result.missing_citations]
    assert "financial_metrics" in sections


def test_empty_financial_metrics_adds_warning() -> None:
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker="VOW3",
            analysis_output=_real_analysis(has_metrics=False),
            citations=[_make_citation("thesis")],
        )
    )
    assert any("financial_metrics" in w for w in result.warnings)


# ---------------------------------------------------------------------------
# run_citation_validator convenience function
# ---------------------------------------------------------------------------


def test_run_citation_validator_returns_dict() -> None:
    result = run_citation_validator(
        ticker="VOW3",
        analysis_output=_placeholder_analysis(),
        citations=[_make_citation("thesis")],
    )
    assert isinstance(result, dict)
    assert "status" in result
    assert "missing_citations" in result
    assert "approved_claims" in result
    assert "warnings" in result
    assert result["is_placeholder"] is True


def test_run_citation_validator_no_citations() -> None:
    result = run_citation_validator(
        ticker="VOW3",
        analysis_output=_placeholder_analysis(),
    )
    assert result["status"] == "warnings"
