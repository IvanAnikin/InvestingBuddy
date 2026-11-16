"""V3.20 slice 8.3 — precedent transactions: review queue and curation (admin only).

``/api/v1/admin/precedent-transactions`` — reachable from the browser only through the
authenticated, admin-allowlisted Next.js proxy (the prefix is on its list); in staging
the backend's Basic Auth guards every route. Rows are proposed (by the document scan or
by an admin) and become usable only when an admin APPROVES them with the target metric
and the layer. A rejected row is kept with its reason (learning data). Nothing here
fetches a URL: the scan reads documents the platform already holds.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.admin_identity import ADMIN_EMAIL_HEADER, sanitize
from app.core.config import settings
from app.db.session import get_db
from app.models.precedent_transaction import PrecedentTransaction as P
from app.schemas.precedent import (
    PrecedentApprove,
    PrecedentCreate,
    PrecedentRead,
    PrecedentReject,
    PrecedentScan,
)

router = APIRouter(prefix="/admin/precedent-transactions", tags=["admin-precedents"])
_LOCAL_ENVS = frozenset({"development", "test", "local"})


def reviewer(request: Request) -> str:
    """The signed-in admin; the forwarded header counts only after Basic Auth (or locally)."""
    email = getattr(request.state, "admin_email", None)
    if email is None and settings.app_env in _LOCAL_ENVS:
        email = sanitize(request.headers.get(ADMIN_EMAIL_HEADER))
    if not email:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "a signed-in admin is required")
    return str(email)


async def _row(db: AsyncSession, row_id: uuid.UUID) -> P:
    row = await db.get(P, row_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "precedent not found")
    return row


@router.get("", response_model=list[PrecedentRead])
async def list_precedents(status_filter: str | None = None, layer: str | None = None,
                          _: str = Depends(reviewer), db: AsyncSession = Depends(get_db)
                          ) -> list[P]:
    q = select(P).order_by(P.announced_date.desc()).limit(500)
    if status_filter:
        q = q.where(P.status == status_filter)
    if layer:
        q = q.where(P.layer == layer)
    return list((await db.execute(q)).scalars().all())


@router.post("", response_model=PrecedentRead, status_code=status.HTTP_201_CREATED)
async def create_precedent(body: PrecedentCreate, _: str = Depends(reviewer),
                           db: AsyncSession = Depends(get_db)) -> P:
    """A curated row. It is PROPOSED like any other: approval is a separate act."""
    now = datetime.now(timezone.utc)
    row = P(id=uuid.uuid4(), **body.model_dump(), retrieved_at=now, entry_method="curated",
            status="proposed", created_at=now)
    db.add(row)
    try:
        await db.commit()
    except IntegrityError as exc:
        await db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, "this deal is already recorded") from exc
    return row


@router.post("/scan")
async def scan(body: PrecedentScan, _: str = Depends(reviewer),
               db: AsyncSession = Depends(get_db)) -> dict:
    """Propose the deals stated in one company's held documents. Never approves."""
    from app.services.valuation.precedents import scan_company_documents

    counts = await scan_company_documents(db, body.company_id)
    await db.commit()
    return counts


@router.post("/{row_id}/approve", response_model=PrecedentRead)
async def approve(row_id: uuid.UUID, body: PrecedentApprove, who: str = Depends(reviewer),
                  db: AsyncSession = Depends(get_db)) -> P:
    row = await _row(db, row_id)
    for key, value in body.model_dump(exclude={"note"}, exclude_none=True).items():
        setattr(row, key, value)
    missing = [k for k in ("layer", "metric_key", "metric_value", "enterprise_value")
               if getattr(row, k) is None]
    if missing:
        raise HTTPException(422,
                            f"set {', '.join(missing)} before approving")
    row.status, row.reviewed_by = "approved", who
    row.reviewed_at, row.review_note = datetime.now(timezone.utc), body.note
    await db.commit()
    return row


@router.post("/{row_id}/reject", response_model=PrecedentRead)
async def reject(row_id: uuid.UUID, body: PrecedentReject, who: str = Depends(reviewer),
                 db: AsyncSession = Depends(get_db)) -> P:
    row = await _row(db, row_id)
    row.status, row.reviewed_by = "rejected", who
    row.reviewed_at, row.review_note = datetime.now(timezone.utc), body.note
    await db.commit()
    return row
