"""NESO Transmission Entry Capacity (TEC) register parser — V3.20 slice 7.4.

Layout pinned on the register as published on the NESO data portal (read 2026-11-16, file
``tec-register-13-november-2026.csv``, 2,187 rows): a CSV whose header names the columns
``Project Name``, ``Customer Name``, ``Connection Site``, ``Stage``, ``MW Connected``,
``MW Increase / Decrease``, ``Cumulative Total Capacity (MW)``, ``MW Effective From``,
``Project Status``, ``Agreement Type``, ``HOST TO``, ``Plant Type``, ``Project ID``,
``Project Number``, ``Gate`` — looked up BY NAME. Dates are day-first (``31/10/2034``) in
the file and ISO in NESO's data API; both are read and shown as ISO.

Two kinds of record:

* one ``project`` per register row. The promoter is the CUSTOMER (the developer holding
  the TEC contract); tags come from the semicolon-separated plant types; the status maps
  Scoping → under_consideration, Awaiting Consents → permitting, Consents Approved →
  planned, Under Construction/Commissioning → under_construction, Built → commissioned;
  the year is that of "MW Effective From".
* one ``queue_summary`` per host transmission owner (NGET, SPT, SHET) over the projects
  that are NOT built: how much capacity is contracted to connect to that owner's network
  — a demand signal for the owner, not capex. OFTO (offshore transmission owners, many
  companies) gets no summary.

No capex is stated. Licence: NESO Open Data Licence (attribution "Supported by National
Energy SO Open Data").
"""

from __future__ import annotations

import csv
import io
import re
import statistics
from collections.abc import Mapping
from typing import Any

from app.services.sources.issuer_holdings.base import LayoutError

PARSER_VERSION = "2026-11-16.1"
COLUMNS = ("Project Name", "Customer Name", "Connection Site", "MW Increase / Decrease",
           "Cumulative Total Capacity (MW)", "MW Effective From", "Project Status",
           "Agreement Type", "HOST TO", "Plant Type", "Project Number")
MAX_ROWS = 50_000

STATUS = {
    "scoping": "under_consideration",
    "awaiting consents": "permitting",
    "consents approved": "planned",
    "under construction/commissioning": "under_construction",
    "built": "commissioned",
}
#: Host transmission owner code → the names a filing would use for it.
HOST_TO: dict[str, tuple[str, ...]] = {
    "NGET": ("National Grid Electricity Transmission", "NGET"),
    "SPT": ("SP Transmission", "SP Energy Networks", "SPT"),
    "SHET": ("Scottish Hydro Electric Transmission", "SSEN Transmission", "SHET"),
}
_PLANT_TAGS: dict[str, tuple[str, ...]] = {
    "energy storage system": ("storage",),
    "laes": ("storage",),
    "pump storage": ("storage", "pumped_hydro"),
    "reactive compensation": ("reactive_compensation",),
    "interconnector": ("interconnector",),
    "substation": ("substation",),
    "demand": ("demand_connection",),
    "wind offshore": ("offshore", "gen_wind_offshore"),
}


def _plant_key(text: str) -> str:
    return re.sub(r"\s*\(.*\)\s*$", "", text).strip().lower()


def plant_tags(text: str | None) -> list[str]:
    tags: set[str] = set()
    for part in (p.strip() for p in (text or "").split(";")):
        if not part:
            continue
        key = _plant_key(part)
        mapped = _PLANT_TAGS.get(key)
        if mapped:
            tags.update(mapped)
        else:
            tags.add("gen_" + re.sub(r"[^a-z0-9]+", "_", key).strip("_"))
    return sorted(tags)


def _mw(v: str) -> float:
    try:
        x = float(v.replace(",", ""))
    except ValueError:
        return 0.0
    return x if -100_000 < x < 100_000 else 0.0


def _iso_date(v: str) -> str | None:
    """The CSV writes ``31/10/2034`` (day first); NESO's data API returns ``2034-10-31``."""
    v = v.strip()
    if m := re.fullmatch(r"(\d{1,2})/(\d{1,2})/((?:19|20)\d\d)", v):
        day, month, year = int(m.group(1)), int(m.group(2)), m.group(3)
        return f"{year}-{month:02d}-{day:02d}" if 1 <= month <= 12 and 1 <= day <= 31 else None
    if m := re.match(r"((?:19|20)\d\d)-(\d\d)-(\d\d)", v):
        return m.group(0)
    return None


def _year(v: str) -> int | None:
    iso = _iso_date(v)
    return int(iso[:4]) if iso else None


def parse(body: bytes) -> list[dict[str, Any]]:
    try:
        text = body.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LayoutError("unreadable_csv", str(exc)[:200]) from exc
    reader = csv.DictReader(io.StringIO(text))
    header = [h.strip() for h in (reader.fieldnames or [])]
    missing = [c for c in COLUMNS if c not in header]
    if missing:
        raise LayoutError("missing_header", ", ".join(missing))
    out: list[dict[str, Any]] = []
    groups: dict[str, dict[str, Any]] = {}
    seen: dict[str, int] = {}
    for n, raw in enumerate(reader):
        if n >= MAX_ROWS:
            raise LayoutError("too_many_rows", str(MAX_ROWS))
        row: Mapping[str, str] = {(k or "").strip(): (v or "").strip()
                                  for k, v in raw.items() if k}
        name, customer = row["Project Name"], row["Customer Name"]
        if not (name and customer):
            continue
        status_text = row["Project Status"]
        status = STATUS.get(status_text.lower(), "other")
        host = row["HOST TO"].upper()
        effective = _iso_date(row["MW Effective From"]) or row["MW Effective From"]
        mw_total = _mw(row["Cumulative Total Capacity (MW)"])
        mw_change = _mw(row["MW Increase / Decrease"])
        tags = plant_tags(row["Plant Type"])
        ext = row["Project Number"] or re.sub(r"[^a-z0-9]+", "-", name.lower())[:80]
        seen[ext] = seen.get(ext, 0) + 1
        if seen[ext] > 1:
            ext = f"{ext}~{seen[ext]}"
        out.append({
            "record_kind": "project",
            "external_id": ext[:120],
            "parent_external_id": None,
            "project_name": name[:500],
            "promoters_text": customer[:1000],
            "promoters": [customer],
            "region": f"Great Britain: {host or 'unknown host'} — {row['Connection Site']}"[:200],
            "category": f"TEC contract ({row['Agreement Type'] or 'type not stated'})"[:200],
            "technology_tags": tags,
            "voltage_kv": None,
            "description": (f"Plant type: {row['Plant Type']}" if row["Plant Type"]
                            else None),
            "capacity_mw": round(mw_total, 2) if mw_total > 0 else None,
            "capacity_text": (f"{mw_total:,.1f} MW contracted (change {mw_change:+,.1f} MW)"
                              )[:500],
            "commissioning_text": f"MW effective from {effective}" if effective else None,
            "commissioning_year": _year(effective),
            "status_text": status_text[:200] or None,
            "status_class": status,
        })
        if host in HOST_TO and status != "commissioned":
            g = groups.setdefault(host, {"count": 0, "mw": 0.0, "types": {}, "years": [],
                                         "statuses": {}})
            g["count"] += 1
            g["mw"] += max(mw_change, 0.0)
            kind = row["Plant Type"].split(";")[0].strip() or "Not stated"
            g["types"][kind] = g["types"].get(kind, 0.0) + max(mw_change, 0.0)
            g["statuses"][status_text] = g["statuses"].get(status_text, 0) + 1
            if (y := _year(effective)) is not None:
                g["years"].append(y)
    if not out:
        raise LayoutError("no_rows", "TEC register")
    for host, g in sorted(groups.items()):
        names = list(HOST_TO[host])
        top = sorted(g["types"].items(), key=lambda kv: -kv[1])[:5]
        years = sorted(g["years"])
        out.append({
            "record_kind": "queue_summary",
            "external_id": f"host-{host.lower()}",
            "parent_external_id": None,
            "project_name": f"Contracted connections not yet built — {names[0]}"[:500],
            "promoters_text": "; ".join(names)[:1000],
            "promoters": names,
            "region": f"Great Britain: {host}",
            "category": "Generation, storage and demand holding TEC contracts",
            "technology_tags": sorted({t for k, _ in top for t in plant_tags(k)}),
            "voltage_kv": None,
            "description": None,
            "capacity_mw": round(g["mw"], 2),
            "capacity_text": (
                f"{g['count']} projects not yet built, {g['mw']:,.0f} MW of capacity "
                "increase; by status: "
                + ", ".join(f"{k} {v}" for k, v in sorted(g["statuses"].items()))
                + "; by first plant type: "
                + ", ".join(f"{k} {v:,.0f} MW" for k, v in top))[:500],
            "commissioning_text": (f"MW effective {years[0]}–{years[-1]}" if years else None),
            "commissioning_year": int(statistics.median(years)) if years else None,
            "status_text": "contracted, not built",
            "status_class": "under_consideration",
        })
    return out


__all__ = ["HOST_TO", "PARSER_VERSION", "parse", "plant_tags"]
