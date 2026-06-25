import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report
from app.schemas.report import ReportCreate


async def list_reports(
    db: AsyncSession, limit: int = 50, offset: int = 0
) -> tuple[list[Report], int]:
    count_result = await db.execute(select(func.count()).select_from(Report))
    total = count_result.scalar_one()
    result = await db.execute(
        select(Report).order_by(Report.created_at.desc()).offset(offset).limit(limit)
    )
    return list(result.scalars().all()), total


async def create_draft_report(db: AsyncSession, data: ReportCreate) -> Report:
    report = Report(
        title=data.title,
        slug=data.slug,
        report_type=data.report_type,
        summary=data.summary,
        content_markdown=data.content_markdown,
        period_start=data.period_start,
        period_end=data.period_end,
        created_by_agent_run_id=data.created_by_agent_run_id,
        status="draft",
    )
    db.add(report)
    await db.commit()
    await db.refresh(report)
    return report


async def get_report(db: AsyncSession, report_id: uuid.UUID) -> Report | None:
    from sqlalchemy import select

    result = await db.execute(select(Report).where(Report.id == report_id))
    return result.scalar_one_or_none()
