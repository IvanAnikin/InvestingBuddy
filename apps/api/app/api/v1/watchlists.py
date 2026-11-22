"""Team watchlists and monitoring signals — V3.21.2 (spec §3, ADR-109).

ADMIN ONLY. Reached through the admin-allowlisted Next.js proxy, behind the backend's
staging Basic Auth; every route also requires the proxied admin identity
(``X-IB-Admin-Email`` on ``request.state``, as for portfolios), and an entry records the
SHA-256 of that address — never the address. Never a public endpoint.

The two team lists (``Shortlist``, ``Watchlist``, owner NULL) are created on first read.
``POST /monitoring/run`` is bounded (one list or one company, at most 200 companies) and
never starts research: it observes what the platform already holds.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.v1.portfolios import owner_key_for
from app.core.admin_identity import ADMIN_EMAIL_HEADER, sanitize
from app.core.config import settings
from app.db.session import get_db
from app.models.company import Company
from app.models.monitoring import MonitoringSignal, Watchlist, WatchlistEntry
from app.schemas.watchlists import (
    EntryCreate,
    EntryCreated,
    MonitoringRunRequest,
    MonitoringRunResult,
    SignalRead,
    WatchlistDetail,
    WatchlistSummary,
)
from app.services.monitoring import pass_job, team_lists, views

router = APIRouter(prefix="/watchlists", tags=["watchlists"])
monitoring_router = APIRouter(prefix="/monitoring", tags=["monitoring"])

_LOCAL_ENVS = frozenset({"development", "test", "local"})


def require_admin(request: Request) -> str:
    """The proxied admin identity, as a SHA-256 hash. 403 without one."""
    email = getattr(request.state, "admin_email", None)
    if email is None and settings.app_env in _LOCAL_ENVS:
        email = sanitize(request.headers.get(ADMIN_EMAIL_HEADER))
    if not email:
        raise HTTPException(status.HTTP_403_FORBIDDEN,
                            "a signed-in admin is required for the team lists")
    return owner_key_for(email)


async def _watchlist(db: AsyncSession, watchlist_id: uuid.UUID) -> Watchlist:
    row = await db.get(Watchlist, watchlist_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "watchlist not found")
    return row


async def _resolve_company(db: AsyncSession, body: EntryCreate) -> uuid.UUID:
    if body.company_id is not None:
        if await db.get(Company, body.company_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "company not found")
        return body.company_id
    stmt = select(Company.id).where(func.upper(Company.ticker) == body.ticker)
    if body.exchange:
        stmt = stmt.where(func.upper(Company.exchange) == body.exchange)
    matches = (await db.execute(stmt.limit(2))).scalars().all()
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND,
                            "no company with that ticker is on record")
    if len(matches) > 1:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            "more than one company has that ticker; give the exchange")
    return matches[0]


@router.get("", response_model=list[WatchlistSummary])
async def list_watchlists(_: str = Depends(require_admin),
                          db: AsyncSession = Depends(get_db)) -> list[dict[str, Any]]:
    await team_lists.ensure_team_lists(db)
    await db.commit()
    return await views.list_watchlists(db)


@router.get("/{watchlist_id}", response_model=WatchlistDetail)
async def get_watchlist(watchlist_id: uuid.UUID, _: str = Depends(require_admin),
                        db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    return await views.watchlist_detail(db, await _watchlist(db, watchlist_id))


@router.post("/{watchlist_id}/entries", response_model=EntryCreated,
             status_code=status.HTTP_201_CREATED)
async def add_entry(watchlist_id: uuid.UUID, body: EntryCreate, response: Response,
                    admin: str = Depends(require_admin),
                    db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """Idempotent: a company already on the list answers 200 with ``created: false``."""
    w = await _watchlist(db, watchlist_id)
    company_id = await _resolve_company(db, body)
    added = await team_lists.add_entry(db, w, company_id, source=body.source,
                                       note=body.note, added_by_hash=admin)
    await db.commit()
    if not added.created:
        response.status_code = status.HTTP_200_OK
    return {"entry_id": added.entry.id, "watchlist_id": w.id, "company_id": company_id,
            "created": added.created}


@router.delete("/{watchlist_id}/entries/{entry_id}",
               status_code=status.HTTP_204_NO_CONTENT)
async def remove_entry(watchlist_id: uuid.UUID, entry_id: uuid.UUID,
                       _: str = Depends(require_admin),
                       db: AsyncSession = Depends(get_db)) -> Response:
    """Removes the entry only. Signals are history and stay."""
    w = await _watchlist(db, watchlist_id)
    row = (await db.execute(select(WatchlistEntry).where(
        WatchlistEntry.id == entry_id, WatchlistEntry.watchlist_id == w.id))
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "entry not found")
    await db.delete(row)
    await db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@monitoring_router.get("/signals", response_model=list[SignalRead])
async def list_signals(
    status_: str = Query("open", alias="status",
                         pattern="^(open|acknowledged|superseded|all)$"),
    company_id: uuid.UUID | None = None,
    limit: int = Query(100, ge=1, le=500),
    _: str = Depends(require_admin), db: AsyncSession = Depends(get_db),
) -> list[dict[str, Any]]:
    rows = await views.list_signals(db, status=None if status_ == "all" else status_,
                                    company_id=company_id, limit=limit)
    return [views.signal_view(s) for s in rows]


@monitoring_router.post("/signals/{signal_id}/acknowledge", response_model=SignalRead)
async def acknowledge(signal_id: uuid.UUID, _: str = Depends(require_admin),
                      db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    signal = await db.get(MonitoringSignal, signal_id)
    if signal is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "signal not found")
    await views.acknowledge_signal(db, signal)
    await db.commit()
    return views.signal_view(signal)


@monitoring_router.post("/run", response_model=MonitoringRunResult)
async def run_monitoring(body: MonitoringRunRequest, _: str = Depends(require_admin),
                         db: AsyncSession = Depends(get_db)) -> dict[str, Any]:
    """A manual pass over one list or one company. Bounded; never starts research.

    With durable jobs on it is enqueued as a ``monitoring_pass`` job (a second request
    while one is live joins it); otherwise it runs in the request.
    """
    if body.watchlist_id is not None:
        await _watchlist(db, body.watchlist_id)
        scope, ident = "watchlist", body.watchlist_id
    else:
        assert body.company_id is not None
        if await db.get(Company, body.company_id) is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "company not found")
        scope, ident = "company", body.company_id
    payload = {f"{scope}_id": str(ident), "manual": True}
    if settings.v3_durable_jobs_enabled:
        from app.services.jobs.job_store import JobStore

        view, created = await JobStore().enqueue(
            job_type=pass_job.JOB_TYPE,
            idempotency_key=f"monitoring_pass_manual:{scope}:{ident}", payload=payload)
        return {"mode": "enqueued", "job_id": str(view.id), "created": created}
    result = await pass_job.run_monitoring_pass(
        db, company_ids=[ident] if scope == "company" else None,
        watchlist_id=ident if scope == "watchlist" else None)
    return {"mode": "inline", "result": result.to_dict()}


__all__ = ["monitoring_router", "require_admin", "router"]
