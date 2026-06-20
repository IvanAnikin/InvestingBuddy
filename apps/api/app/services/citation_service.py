import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.source import Citation
from app.schemas.source import (
    CitationCreate,
    CitationValidationResult,
    MissingCitationWarning,
)


async def create_citation(db: AsyncSession, data: CitationCreate) -> Citation:
    citation = Citation(
        source_id=data.source_id,
        report_id=data.report_id,
        agent_run_id=data.agent_run_id,
        claim_text=data.claim_text,
        source_quote=data.source_quote,
        url=data.url,
        retrieved_at=data.retrieved_at,
    )
    db.add(citation)
    await db.commit()
    await db.refresh(citation)
    return citation


async def list_citations_for_report(
    db: AsyncSession, report_id: uuid.UUID
) -> list[Citation]:
    result = await db.execute(
        select(Citation)
        .where(Citation.report_id == report_id)
        .order_by(Citation.created_at.asc())
    )
    return list(result.scalars().all())


async def count_citations_for_report(db: AsyncSession, report_id: uuid.UUID) -> int:
    result = await db.execute(
        select(Citation).where(Citation.report_id == report_id)
    )
    return len(result.scalars().all())


async def list_citations_for_agent_run(
    db: AsyncSession, agent_run_id: uuid.UUID
) -> list[Citation]:
    result = await db.execute(
        select(Citation)
        .where(Citation.agent_run_id == agent_run_id)
        .order_by(Citation.created_at.asc())
    )
    return list(result.scalars().all())


# ---------------------------------------------------------------------------
# Citation validation helper
# ---------------------------------------------------------------------------

# Claims in a report that must have citations to pass validation.
_REQUIRED_CLAIM_SECTIONS = [
    "financial_metrics",
    "rating",
    "thesis",
    "bull_case",
    "bear_case",
]


def validate_citations_for_draft(
    analysis_output: dict,
    citations: list[Citation],
) -> CitationValidationResult:
    """
    Check whether important claims in a structured draft report have citations.

    analysis_output: the analysis_output dict from the workflow state.
    citations: Citation records already linked to the report.

    Returns a CitationValidationResult with status "ok", "warnings" or "failed".
    This is a structural check — it does not fetch external URLs.
    """
    cited_claim_texts = {c.claim_text for c in citations if c.claim_text}
    missing: list[MissingCitationWarning] = []
    approved: list[str] = []
    warnings: list[str] = []

    # Check placeholder flag
    if analysis_output.get("is_placeholder"):
        warnings.append(
            "Analysis output is marked is_placeholder=true. "
            "All claims are placeholder data — no real citations required yet."
        )

    # Check financial_metrics
    financial_metrics = analysis_output.get("financial_metrics") or {}
    if not financial_metrics:
        warnings.append(
            "financial_metrics is empty — no financial numbers to validate yet"
        )
    else:
        for metric_name, metric_value in financial_metrics.items():
            claim = f"financial_metrics.{metric_name}"
            if claim in cited_claim_texts:
                approved.append(claim)
            else:
                missing.append(
                    MissingCitationWarning(
                        section="financial_metrics",
                        claim=claim,
                        reason="No citation found for this metric",
                    )
                )

    # Check thesis
    thesis = analysis_output.get("thesis") or ""
    if thesis:
        claim = "thesis"
        if claim in cited_claim_texts:
            approved.append(claim)
        else:
            missing.append(
                MissingCitationWarning(
                    section="thesis",
                    claim=thesis[:120],
                    reason="Thesis statement has no linked citation",
                )
            )
    else:
        warnings.append("thesis field is empty")

    # Check rating
    rating = analysis_output.get("rating")
    if rating:
        claim = f"rating:{rating}"
        if claim in cited_claim_texts or citations:
            approved.append(f"rating:{rating}")
        else:
            missing.append(
                MissingCitationWarning(
                    section="rating",
                    claim=f"Rating={rating}",
                    reason="Rating has no supporting citations at all",
                )
            )

    total_claims = len(missing) + len(approved)

    # Determine overall status
    if analysis_output.get("is_placeholder"):
        overall = "warnings"
    elif not missing:
        overall = "ok"
    elif len(approved) == 0:
        overall = "failed"
    else:
        overall = "warnings"

    return CitationValidationResult(
        status=overall,
        total_claims=total_claims,
        cited_claims=len(approved),
        missing_citations=missing,
        approved_claims=approved,
        warnings=warnings,
    )
