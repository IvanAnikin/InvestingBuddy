"""Is the database at the schema this code maps? — V3.18.2.

WHY THIS EXISTS
===============
Migrations never run in the deploy workflow; a human applies them over SSH. So there is
always a window in which new code runs against the previous schema — and code whose ORM
maps a column the database does not have fails EVERY query on that table. For the V3
research ledger that means every V3 run fails, and it fails as an opaque database error
nobody can read from outside the network.

This module answers one question, cheaply and read-only: are the columns a feature needs
present? The V3 pipeline asks before it writes, and degrades with a NAMED reason when
they are not; an admin endpoint exposes the same answer, because the database cannot be
reached from outside its network and "was the migration applied?" has to be checkable
over HTTP.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any

#: Columns migration 041 adds, by table. Kept in step with the migration by a test that
#: reads the migration's own column list.
MIGRATION_041_COLUMNS: dict[str, tuple[str, ...]] = {
    "research_questions": (
        "domain",
        "owner_role",
        "evidence_contract_json",
        "acquisition_log_json",
    ),
    "research_findings": ("topic_key", "domain", "claim_key"),
    "research_gaps": ("knowledge_state",),
    "research_runs": ("thesis_json",),
    "research_leads": ("matched_excerpt",),
    "macro_series": ("commodity",),
}

_CACHE_SECONDS = 300.0
_cache: dict[str, tuple[float, bool, tuple[str, ...]]] = {}


@dataclass(frozen=True)
class Readiness:
    ready: bool
    missing: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {"ready": self.ready, "missing": list(self.missing)}


async def _columns(session: Any, table: str) -> set[str]:
    from sqlalchemy import inspect as sa_inspect

    connection = await session.connection()
    return await connection.run_sync(
        lambda sync: {c["name"] for c in sa_inspect(sync).get_columns(table)}
    )


async def migration_041_readiness(session: Any, *, use_cache: bool = True) -> Readiness:
    """Whether every column migration 041 adds is present. Never raises."""
    now = time.monotonic()
    cached = _cache.get("041")
    if use_cache and cached and now - cached[0] < _CACHE_SECONDS:
        return Readiness(cached[1], cached[2])
    missing: list[str] = []
    try:
        for table, columns in MIGRATION_041_COLUMNS.items():
            present = await _columns(session, table)
            missing.extend(f"{table}.{c}" for c in columns if c not in present)
    except Exception:  # noqa: BLE001 - "could not tell" is reported as not ready
        missing.append("schema could not be inspected")
    ready = not missing
    # Only a READY answer is cached for long: once applied, a migration stays applied,
    # while "not ready" must flip to ready as soon as a human runs the upgrade.
    if ready:
        _cache["041"] = (now, ready, tuple(missing))
    return Readiness(ready, tuple(missing))


def reset_cache() -> None:
    _cache.clear()


__all__ = ["MIGRATION_041_COLUMNS", "Readiness", "migration_041_readiness", "reset_cache"]
