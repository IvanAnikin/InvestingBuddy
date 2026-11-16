"""Private, hand-entered portfolios — V3.20 slice 12.1 (ADR-100).

Every route is scoped to ONE owner: the admin the proxy authenticated (``X-IB-Admin-Email``,
read by the Basic Auth middleware onto ``request.state.admin_email``). The table stores a
SHA-256 of that address, never the address. Another owner's portfolio is a 404, never a
403, so its existence is not revealed. No route imports from a broker, places an order or
computes a size; the data is what the user typed.

Local development has no Basic Auth layer, so there (``APP_ENV`` development/test only) the
forwarded header is read directly — it scopes rows, it never authenticates anybody.
"""

from __future__ import annotations

import hashlib
import uuid
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import delete as sa_delete
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_identity import ADMIN_EMAIL_HEADER, sanitize
from app.core.config import settings
from app.db.session import get_db
from app.models.company import Company
from app.models.user_portfolio import UserPortfolio, UserPortfolioPosition
from app.schemas.portfolio import (
    PortfolioCreate,
    PortfolioDetail,
    PortfolioRead,
    PositionCreate,
    PositionRead,
)

router = APIRouter(prefix="/portfolios", tags=["portfolios"])

_LOCAL_ENVS = frozenset({"development", "test", "local"})


def owner_key_for(email: str) -> str:
    return hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()


def require_owner(request: Request) -> str:
    email = getattr(request.state, "admin_email", None)
    if email is None and settings.app_env in _LOCAL_ENVS:
        email = sanitize(request.headers.get(ADMIN_EMAIL_HEADER))
    if not email:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "a signed-in owner is required for a private portfolio")
    return owner_key_for(email)


async def _owned(db: AsyncSession, portfolio_id: uuid.UUID, owner: str) -> UserPortfolio:
    row = (await db.execute(select(UserPortfolio).where(
        UserPortfolio.id == portfolio_id, UserPortfolio.owner_key == owner))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "portfolio not found")
    return row


async def _positions(db: AsyncSession, portfolio_id: uuid.UUID) -> list[UserPortfolioPosition]:
    return list((await db.execute(
        select(UserPortfolioPosition).where(UserPortfolioPosition.portfolio_id == portfolio_id)
        .order_by(UserPortfolioPosition.entry_date, UserPortfolioPosition.ticker))
    ).scalars().all())


def _summary(p: UserPortfolio, positions: list[UserPortfolioPosition]) -> dict:
    weights = [x.weight_pct for x in positions if x.weight_pct is not None]
    return {"id": p.id, "name": p.name, "note": p.note, "created_at": p.created_at,
            "position_count": len(positions),
            "weight_pct_total": sum(weights, Decimal(0)) if weights else None}


@router.get("", response_model=list[PortfolioRead])
async def list_portfolios(owner: str = Depends(require_owner),
                          db: AsyncSession = Depends(get_db)) -> list[dict]:
    rows = (await db.execute(select(UserPortfolio).where(UserPortfolio.owner_key == owner)
                             .order_by(UserPortfolio.created_at))).scalars().all()
    return [_summary(p, await _positions(db, p.id)) for p in rows]


@router.post("", response_model=PortfolioRead, status_code=status.HTTP_201_CREATED)
async def create_portfolio(body: PortfolioCreate, owner: str = Depends(require_owner),
                           db: AsyncSession = Depends(get_db)) -> dict:
    p = UserPortfolio(id=uuid.uuid4(), owner_key=owner, name=body.name, note=body.note)
    db.add(p)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "a portfolio with this name already exists") from None
    await db.refresh(p)
    return _summary(p, [])


@router.get("/{portfolio_id}", response_model=PortfolioDetail)
async def get_portfolio(portfolio_id: uuid.UUID, owner: str = Depends(require_owner),
                        db: AsyncSession = Depends(get_db)) -> dict:
    p = await _owned(db, portfolio_id, owner)
    positions = await _positions(db, p.id)
    return {**_summary(p, positions),
            "positions": [PositionRead.model_validate(x) for x in positions]}


@router.get("/{portfolio_id}/review")
async def review(portfolio_id: uuid.UUID, owner: str = Depends(require_owner),
                 db: AsyncSession = Depends(get_db)) -> dict:
    """V3.20 slice 12.2 — the research review: a cited thesis status per holding. It reads
    public research records and writes nothing; it never proposes a trade or a size."""
    from app.services.portfolio_review import review_portfolio

    p = await _owned(db, portfolio_id, owner)
    return {"portfolio_id": str(p.id), "name": p.name,
            **await review_portfolio(db, await _positions(db, p.id))}


@router.delete("/{portfolio_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_portfolio(portfolio_id: uuid.UUID, owner: str = Depends(require_owner),
                           db: AsyncSession = Depends(get_db)) -> Response:
    """The user's own entered data; no research history is touched."""
    p = await _owned(db, portfolio_id, owner)
    # Explicit, not left to ON DELETE CASCADE: a database with FK enforcement off would
    # otherwise keep orphaned positions.
    await db.execute(sa_delete(UserPortfolioPosition)
                     .where(UserPortfolioPosition.portfolio_id == p.id))
    await db.delete(p)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/{portfolio_id}/positions", response_model=PositionRead,
             status_code=status.HTTP_201_CREATED)
async def add_position(portfolio_id: uuid.UUID, body: PositionCreate,
                       owner: str = Depends(require_owner),
                       db: AsyncSession = Depends(get_db)) -> UserPortfolioPosition:
    p = await _owned(db, portfolio_id, owner)
    stmt = select(Company.id).where(func.upper(Company.ticker) == body.ticker)
    if body.exchange:
        stmt = stmt.where(func.upper(Company.exchange) == body.exchange)
    matches = (await db.execute(stmt.limit(2))).scalars().all()
    position = UserPortfolioPosition(
        id=uuid.uuid4(), portfolio_id=p.id,
        company_id=matches[0] if len(matches) == 1 else None,  # ambiguous → unlinked
        ticker=body.ticker, exchange=body.exchange, entry_date=body.entry_date,
        entry_price=body.entry_price, entry_currency=body.entry_currency,
        weight_pct=body.weight_pct, note=body.note)
    db.add(position)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "this position is already entered for that date") from None
    await db.refresh(position)
    return position


@router.delete("/{portfolio_id}/positions/{position_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def delete_position(portfolio_id: uuid.UUID, position_id: uuid.UUID,
                          owner: str = Depends(require_owner),
                          db: AsyncSession = Depends(get_db)) -> Response:
    p = await _owned(db, portfolio_id, owner)
    row = (await db.execute(select(UserPortfolioPosition).where(
        UserPortfolioPosition.id == position_id,
        UserPortfolioPosition.portfolio_id == p.id))).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "position not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


__all__ = ["owner_key_for", "require_owner", "router"]
