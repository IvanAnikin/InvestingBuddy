"""Berkeley Lab "Queued Up" parser — V3.20 slice 7.2.

Layout pinned on the real ``LBNL_Ix_Queue_Data_File_thru2025.xlsx`` (2026 edition, data
through 2025; downloaded 2026-11-16): sheet "03. Complete Queue Data", header row with
the codebook's field names (``q_status``, ``region``, ``entity``, ``utility``,
``type_clean``, ``mw_1``..``mw_3``, ``IA_phase_clean``, ``prop_year`` ...), looked up BY
NAME.

The file lists ~38,000 interconnection requests. For demand triangulation what matters
is how much capacity is waiting to connect to each transmission provider, so the parser
AGGREGATES the ACTIVE requests into one ``queue_summary`` record per (region,
transmission-provider entity, utility): request count, active MW, MW with an executed
interconnection agreement, MW by resource type, and the span of proposed online years.
Withdrawn / operational / suspended requests are not counted. No capex is stated.
"""

from __future__ import annotations

import re
import statistics
from collections.abc import Sequence
from typing import Any

from app.services.sources.issuer_holdings.base import LayoutError
from app.services.sources.issuer_holdings.xlsx import sheet_names, xlsx_rows

PARSER_VERSION = "2026-11-16.1"
SHEET = "03. Complete Queue Data"
COLUMNS = ("q_status", "region", "entity", "utility", "type_clean", "mw_1", "mw_2", "mw_3",
           "IA_phase_clean", "prop_year", "state")


def _mw(v: str) -> float:
    try:
        x = float(v)
    except ValueError:
        return 0.0
    return x if 0 < x < 100_000 else 0.0


def _slug(*parts: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", " ".join(parts).lower()).strip("-")[:80]


def parse(body: bytes) -> list[dict[str, Any]]:
    if SHEET not in sheet_names(body):
        raise LayoutError("missing_sheet", SHEET)
    rows = xlsx_rows(body, SHEET)
    header_at = next((i for i, r in enumerate(rows[:10])
                      if all(c in [x.strip() for x in r] for c in COLUMNS)), None)
    if header_at is None:
        raise LayoutError("missing_header", SHEET)
    ix = {c.strip(): j for j, c in enumerate(rows[header_at]) if c.strip()}

    def cell(row: Sequence[str], col: str) -> str:
        j = ix[col]
        return row[j].strip() if j < len(row) else ""

    # Keyed on the NORMALISED names: "AMEREN MISSOURI" and "Ameren Missouri" are one
    # provider; the first spelling seen is the one shown.
    groups: dict[str, dict[str, Any]] = {}
    for row in rows[header_at + 1:]:
        if cell(row, "q_status").lower() != "active":
            continue
        region, entity, utility = cell(row, "region"), cell(row, "entity"), cell(row, "utility")
        if not (entity or utility):
            continue
        g = groups.setdefault(_slug(region, entity, utility), {
            "names": (region, entity, utility), "count": 0, "mw": 0.0, "ia_mw": 0.0,
            "types": {}, "years": [], "states": set()})
        mw = sum(_mw(cell(row, c)) for c in ("mw_1", "mw_2", "mw_3"))
        g["count"] += 1
        g["mw"] += mw
        if cell(row, "IA_phase_clean") == "IA Executed":
            g["ia_mw"] += mw
        kind = cell(row, "type_clean") or "Unknown"
        g["types"][kind] = g["types"].get(kind, 0.0) + mw
        if re.fullmatch(r"20\d\d(?:\.0+)?", cell(row, "prop_year")):
            g["years"].append(int(float(cell(row, "prop_year"))))
        if cell(row, "state"):
            g["states"].add(cell(row, "state"))
    if not groups:
        raise LayoutError("no_active_requests", SHEET)
    out: list[dict[str, Any]] = []
    for key, g in sorted(groups.items()):
        region, entity, utility = g["names"]
        names = [n for n in dict.fromkeys((utility, entity)) if n]
        top = sorted(g["types"].items(), key=lambda kv: -kv[1])[:5]
        years = sorted(g["years"])
        out.append({
            "record_kind": "queue_summary",
            "external_id": key,
            "parent_external_id": None,
            "project_name": f"Active interconnection queue — {utility or entity}"[:500],
            "promoters_text": "; ".join(names)[:1000],
            "promoters": names,
            "region": f"US {region}: {', '.join(sorted(g['states']))}"[:200],
            "category": "Generation and storage seeking interconnection",
            "technology_tags": sorted({f"gen_{re.sub(r'[^a-z]+', '_', k.lower()).strip('_')}"
                                       for k, _ in top}),
            "voltage_kv": None,
            "description": None,
            "capacity_mw": round(g["mw"], 2),
            "capacity_text": (
                f"{g['count']} active requests, {g['mw']:,.0f} MW; {g['ia_mw']:,.0f} MW with "
                "an executed interconnection agreement; by type: "
                + ", ".join(f"{k} {v:,.0f} MW" for k, v in top))[:500],
            "commissioning_text": (f"proposed online {years[0]}–{years[-1]}"
                                   if years else None),
            "commissioning_year": int(statistics.median(years)) if years else None,
            "status_text": "active",
            "status_class": "under_consideration",
        })
    return out


__all__ = ["PARSER_VERSION", "parse"]
