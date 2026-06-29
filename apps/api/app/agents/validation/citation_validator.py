"""
CitationValidator — Phase 3 skeleton.

Checks whether important claims in a structured draft analysis output
have citation references. Operates on structured placeholder data only.
No LLM calls are made in Phase 3.

In Phase 4+, replace the body of `validate()` with an LLM-powered claim
extraction step followed by citation lookup against the sources table.
"""

from dataclasses import dataclass, field


@dataclass
class CitationValidatorInput:
    """Input to the citation validator."""

    ticker: str
    analysis_output: dict
    # List of citation dicts already linked to the report, each with at least:
    #   { "claim_text": str | None, "source_id": str }
    citations: list[dict] = field(default_factory=list)


@dataclass
class CitationValidatorOutput:
    """Structured result from citation validation."""

    status: str  # "ok" | "warnings" | "failed"
    missing_citations: list[dict]  # list of {section, claim, reason}
    approved_claims: list[str]
    warnings: list[str]
    is_placeholder: bool = True


class CitationValidator:
    """
    Structural citation checker for investment analysis drafts.

    Phase 3 behaviour:
    - Operates on structured dict (analysis_output) rather than free text.
    - Checks that key sections (thesis, rating, financial_metrics) have
      at least one linked citation.
    - Returns "warnings" for placeholder outputs since no real sources exist.

    Phase 4+ upgrade path:
    - Replace `_extract_claims()` with an LLM chain that extracts factual
      claims from content_markdown.
    - Replace `_has_citation()` with a vector similarity lookup against
      the sources/source_chunks tables.
    - Set `is_placeholder=False` once real LLM extraction is wired in.
    """

    # Sections we require citations for before a report can be published.
    REQUIRED_SECTIONS = ["thesis", "rating", "financial_metrics"]

    def validate(self, inp: CitationValidatorInput) -> CitationValidatorOutput:
        cited_claims = {c.get("claim_text") for c in inp.citations if c.get("claim_text")}
        is_placeholder = inp.analysis_output.get("is_placeholder", False)

        missing: list[dict] = []
        approved: list[str] = []
        warnings: list[str] = []

        if is_placeholder:
            warnings.append(
                "[PLACEHOLDER] Analysis output is marked is_placeholder=true. "
                "All claims are demo data. Citation requirements are relaxed until "
                "real LLM analysis is wired in (Phase 4+)."
            )

        # Check thesis
        thesis = inp.analysis_output.get("thesis") or ""
        if "thesis" in cited_claims:
            approved.append("thesis")
        elif thesis:
            missing.append({
                "section": "thesis",
                "claim": thesis[:120],
                "reason": "Thesis statement has no linked citation",
            })
        else:
            warnings.append("thesis field is empty")

        # Check rating
        rating = inp.analysis_output.get("rating")
        if rating:
            claim_key = f"rating:{rating}"
            if inp.citations:
                approved.append(claim_key)
            else:
                missing.append({
                    "section": "rating",
                    "claim": f"Rating={rating}",
                    "reason": "No citations at all linked to this report",
                })

        # Check financial_metrics — each metric must have a source_id
        financial_metrics = inp.analysis_output.get("financial_metrics") or {}
        if not financial_metrics:
            warnings.append(
                "financial_metrics is empty — no financial numbers to validate yet"
            )
        else:
            for metric_name, metric_value in financial_metrics.items():
                if isinstance(metric_value, dict) and metric_value.get("source_id"):
                    approved.append(f"financial_metrics.{metric_name}")
                else:
                    missing.append({
                        "section": "financial_metrics",
                        "claim": f"financial_metrics.{metric_name}",
                        "reason": "Metric lacks source_id — unsupported financial claim",
                    })

        # Determine overall status
        if is_placeholder:
            overall = "warnings"
        elif not missing:
            overall = "ok"
        elif len(approved) == 0:
            overall = "failed"
        else:
            overall = "warnings"

        return CitationValidatorOutput(
            status=overall,
            missing_citations=missing,
            approved_claims=approved,
            warnings=warnings,
            is_placeholder=is_placeholder,
        )


def run_citation_validator(
    ticker: str,
    analysis_output: dict,
    citations: list[dict] | None = None,
) -> dict:
    """
    Convenience function for use as a LangGraph node body.

    Returns a dict matching CitationValidatorOutput fields so it can be
    stored directly as agent_step output_json.
    """
    validator = CitationValidator()
    result = validator.validate(
        CitationValidatorInput(
            ticker=ticker,
            analysis_output=analysis_output,
            citations=citations or [],
        )
    )
    return {
        "status": result.status,
        "missing_citations": result.missing_citations,
        "approved_claims": result.approved_claims,
        "warnings": result.warnings,
        "is_placeholder": result.is_placeholder,
    }
