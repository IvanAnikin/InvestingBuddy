import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.company import Company
from app.schemas.company import CompanyCreate


async def create_company(db: AsyncSession, data: CompanyCreate) -> Company:
    company = Company(
        ticker=data.ticker.upper(),
        exchange=data.exchange.upper(),
        name=data.name,
        country=data.country,
        region=data.region,
        sector=data.sector,
        industry=data.industry,
        market_cap=data.market_cap,
        currency=data.currency,
        website=data.website,
        description=data.description,
        status="new",
    )
    db.add(company)
    await db.commit()
    await db.refresh(company)
    return company


async def get_company(db: AsyncSession, company_id: uuid.UUID) -> Company | None:
    result = await db.execute(select(Company).where(Company.id == company_id))
    return result.scalar_one_or_none()


async def get_company_by_ticker(
    db: AsyncSession, ticker: str, exchange: str
) -> Company | None:
    result = await db.execute(
        select(Company).where(
            Company.ticker == ticker.upper(),
            Company.exchange == exchange.upper(),
        )
    )
    return result.scalar_one_or_none()


async def list_companies(
    db: AsyncSession, limit: int = 100, offset: int = 0
) -> list[Company]:
    result = await db.execute(
        select(Company).order_by(Company.created_at.desc()).limit(limit).offset(offset)
    )
    return list(result.scalars().all())


async def count_companies(db: AsyncSession) -> int:
    result = await db.execute(select(Company))
    return len(result.scalars().all())
