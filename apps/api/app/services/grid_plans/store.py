"""Grid plans — ingest into ``grid_capex_plans`` (V3.20 slice 7.1).

``ingest_file`` records a ``grid_plan_ingests`` row first, parses the file with the
source's pinned parser, and upserts one row per record keyed on (source, edition, kind,
external id) — a re-ingest of the same edition updates in place; a new edition adds
rows beside the old ones (history is kept). A layout the parser was not written against
marks the ingest ``failed`` with its reason and writes no plan row.

A source whose flag is off is refused BEFORE anything is parsed or stored
(``SourceDisabled``) — its licence class is the reason the flag exists.
"""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.services.grid_plans.base import SOURCES, GridSource
from app.services.sources.issuer_holdings.base import LayoutError


class SourceDisabled(RuntimeError):
    """The source's flag is off; nothing was read or stored."""


def parser_for(key: str) -> Callable[[bytes], list[dict[str, Any]]]:
    if key == "entsoe_tyndp":
        from app.services.grid_plans.entsoe_tyndp import parse

        return parse
    if key == "aemo_tai":
        from app.services.grid_plans.aemo_tai import parse as parse_aemo

        return parse_aemo
    if key == "lbl_queued_up":
        from app.services.grid_plans.lbl_queued_up import parse as parse_lbl

        return parse_lbl
    if key == "neso_tec":
        from app.services.grid_plans.neso_tec import parse as parse_neso

        return parse_neso
    if key == "occto_supply_plan":
        from app.services.grid_plans.occto_supply_plan import parse as parse_occto

        return parse_occto
    if key == "cea_tbcb_uc":
        from app.services.grid_plans.cea_transmission import parse_tbcb

        return parse_tbcb
    if key == "cea_rtm_uc":
        from app.services.grid_plans.cea_transmission import parse_rtm

        return parse_rtm
    raise KeyError(key)


def source(key: str, cfg: Any) -> GridSource:
    src = SOURCES.get(key)
    if src is None:
        raise KeyError(f"unknown grid-plan source: {key}")
    if not getattr(cfg, src.flag, False):
        raise SourceDisabled(f"{key} is off ({src.flag.upper()}); {src.licence}")
    return src


def redirect_allowed(location: str | None, src: GridSource) -> bool:
    """May ``--download`` follow this one redirect? Only https, only to a host suffix the
    source names (its publisher's file store)."""
    from urllib.parse import urlsplit

    if not location or not src.redirect_hosts:
        return False
    parts = urlsplit(location)
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(
        host.endswith(suffix) and host != suffix.lstrip(".") for suffix in src.redirect_hosts)


def default_edition(src: GridSource) -> str:
    return src.default_edition


async def ingest_file(session: Any, *, key: str, body: bytes, cfg: Any,
                      source_url: str | None = None, edition: str | None = None,
                      retrieved_at: datetime | None = None) -> Any:
    """Parse ``body`` as source ``key`` and upsert its rows. Returns the ingest row."""
    from sqlalchemy import select

    from app.models.grid_plan import (
        INGEST_COMPLETE,
        INGEST_FAILED,
        INGEST_RUNNING,
        GridCapexPlan,
        GridPlanIngest,
    )

    src = source(key, cfg)
    now = datetime.now(timezone.utc)
    retrieved = retrieved_at or now
    edition = (edition or default_edition(src))[:80]
    url = source_url or src.download_url
    ingest = GridPlanIngest(id=uuid.uuid4(), source_key=key, plan_edition=edition,
                            status=INGEST_RUNNING, source_url=url,
                            file_sha256=hashlib.sha256(body).hexdigest(),
                            retrieved_at=retrieved)
    session.add(ingest)
    await session.flush()
    try:
        records = parser_for(key)(body)
    except LayoutError as exc:
        ingest.status, ingest.error = INGEST_FAILED, str(exc)[:500]
        ingest.finished_at = datetime.now(timezone.utc)
        await session.flush()
        return ingest
    existing = {(r.record_kind, r.external_id): r for r in (await session.execute(
        select(GridCapexPlan).where(GridCapexPlan.source_key == key,
                                    GridCapexPlan.plan_edition == edition))).scalars()}
    written, seen = 0, set()
    for rec in records:
        # A parser must give unique keys; a repeat in one file is suffixed, never merged.
        base_key = (rec["record_kind"], rec["external_id"][:76])
        n, ext = 1, rec["external_id"][:80]
        while (rec["record_kind"], ext) in seen:
            n += 1
            ext = f"{base_key[1]}~{n}"
        seen.add((rec["record_kind"], ext))
        rec = {**rec, "external_id": ext}
        values = {
            "ingest_id": ingest.id, "source_key": key, "plan_name": src.plan_name,
            "plan_edition": edition, "record_kind": rec["record_kind"],
            "external_id": rec["external_id"][:80],
            "parent_external_id": rec.get("parent_external_id"),
            "project_name": rec["project_name"], "promoters_text": rec.get("promoters_text"),
            "promoters_json": rec.get("promoters") or [], "region": rec.get("region"),
            "category": rec.get("category"),
            "technology_tags_json": rec.get("technology_tags") or [],
            "voltage_kv": rec.get("voltage_kv"), "description": rec.get("description"),
            "capex_amount": rec.get("capex_amount"),
            "capex_currency": rec.get("capex_currency") if rec.get("capex_amount") else None,
            "capex_period": rec.get("capex_period"), "capacity_text": rec.get("capacity_text"),
            "capacity_mw": rec.get("capacity_mw"),
            "commissioning_text": rec.get("commissioning_text"),
            "commissioning_year": rec.get("commissioning_year"),
            "status_text": rec.get("status_text"), "status_class": rec["status_class"],
            "source_url": url, "source_title": src.name, "retrieved_at": retrieved,
            "use_constraint": src.use_constraint, "attribution": src.attribution,
        }
        row = existing.get((rec["record_kind"], values["external_id"]))
        if row is None:
            session.add(GridCapexPlan(id=uuid.uuid4(), **values))
        else:
            for k, v in values.items():
                setattr(row, k, v)
        written += 1
    ingest.status, ingest.rows_read, ingest.rows_written = INGEST_COMPLETE, len(records), written
    ingest.detail_json = {"kinds": _count(records, "record_kind"),
                          "status_classes": _count(records, "status_class"),
                          "with_capex": sum(1 for r in records if r.get("capex_amount"))}
    ingest.finished_at = datetime.now(timezone.utc)
    await session.flush()
    return ingest


def _count(records: list[dict[str, Any]], key: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in records:
        out[str(r.get(key))] = out.get(str(r.get(key)), 0) + 1
    return out


__all__ = ["SourceDisabled", "default_edition", "ingest_file", "parser_for", "source"]
