import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.session import get_db
from app.schemas.company import CompanyCreate, CompanyList, CompanyRead
from app.services import company_service

router = APIRouter(prefix="/companies", tags=["companies"])


@router.post("", response_model=CompanyRead, status_code=status.HTTP_201_CREATED)
async def create_company(
    payload: CompanyCreate, db: AsyncSession = Depends(get_db)
) -> CompanyRead:
    existing = await company_service.get_company_by_ticker(
        db, payload.ticker, payload.exchange
    )
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Company {payload.ticker} on {payload.exchange} already exists",
        )
    company = await company_service.create_company(db, payload)
    return CompanyRead.model_validate(company)


@router.get("", response_model=CompanyList)
async def list_companies(
    limit: int = 50, offset: int = 0, db: AsyncSession = Depends(get_db)
) -> CompanyList:
    companies = await company_service.list_companies(db, limit=limit, offset=offset)
    total = await company_service.count_companies(db)
    return CompanyList(
        items=[CompanyRead.model_validate(c) for c in companies],
        total=total,
    )


@router.get("/{company_id}", response_model=CompanyRead)
async def get_company(
    company_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CompanyRead:
    company = await company_service.get_company(db, company_id)
    if not company:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Company {company_id} not found",
        )
    return CompanyRead.model_validate(company)
