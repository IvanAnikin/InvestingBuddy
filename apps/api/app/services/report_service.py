import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report
from app.schemas.report import ReportCreate


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
    result = await db.execute(select(Report).where(Report.id == report_id))
    return result.scalar_one_or_none()


async def list_reports(
    db: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[Report]:
    stmt = select(Report)
    if status:
        stmt = stmt.where(Report.status == status)
    stmt = stmt.order_by(Report.created_at.desc()).limit(limit).offset(offset)
    result = await db.execute(stmt)
    return list(result.scalars().all())


async def count_reports(db: AsyncSession, *, status: str | None = None) -> int:
    from sqlalchemy import func

    stmt = select(func.count()).select_from(Report)
    if status:
        stmt = stmt.where(Report.status == status)
    result = await db.execute(stmt)
    return result.scalar_one()
