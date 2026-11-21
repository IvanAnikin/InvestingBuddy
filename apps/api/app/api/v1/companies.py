import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.db.session import get_db
from app.schemas.company import CompanyCreate, CompanyList, CompanyRead
from app.schemas.passive_ownership import CompanyPassiveOwnershipRead
from app.schemas.relationships import CompanyRelationshipsRead
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


@router.get("/{company_id}/relationships", response_model=CompanyRelationshipsRead)
async def get_company_relationships(
    company_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CompanyRelationshipsRead:
    """Parents, subsidiaries, customers, suppliers and significant holders — V3.20 2.1.

    Every edge carries its citation; an unresolved counterparty (an anonymous
    "Customer A") is returned by name with ``counterparty_resolved: false``.
    """
    from app.services.entities.relationship_view import company_relationships

    company = await company_service.get_company(db, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    return await company_relationships(db, company=company, cfg=settings)


@router.get("/{company_id}/passive-ownership", response_model=CompanyPassiveOwnershipRead)
async def get_company_passive_ownership(
    company_id: uuid.UUID, db: AsyncSession = Depends(get_db)
) -> CompanyPassiveOwnershipRead:
    """US-registered funds / ETFs holding the company's securities — V3.20 3.3.

    From the stored SEC N-PORT aggregates (offline ingest). The % of shares outstanding
    is labelled "US-registered funds / ETFs only" and carries its denominator (SEC
    cover-page share count) and that count's date; it is withheld, with a reason, when
    it cannot be stated honestly.
    """
    from app.services.passive_ownership.view import company_passive_ownership

    company = await company_service.get_company(db, company_id)
    if company is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Company not found")
    view = await company_passive_ownership(db, company=company, cfg=settings)
    return CompanyPassiveOwnershipRead.model_validate(view)
