"""AEMO NEM Transmission Augmentation Information parser — V3.20 slice 7.2.

Layout pinned on the real ``nem-transmission-augmentation-information-august-2026.xlsx``
(downloaded 2026-11-16): sheet "Transmission augmentations", header row found by its
column names (looked up BY NAME). One row per committed / anticipated / actionable /
future ISP project; the proponents are the TNSPs (Transgrid, Powerlink, ElectraNet,
AusNet, VicGrid, EnergyCo, ...). The workbook states no capex. AEMO gives no project id,
so the record key is the region + project name. Missing sheet or column → LayoutError.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

from app.services.grid_plans.base import (
    commissioning_year,
    max_voltage_kv,
    serial_to_iso,
    split_promoters,
    status_class,
    technology_tags,
)
from app.services.sources.issuer_holdings.base import LayoutError
from app.services.sources.issuer_holdings.xlsx import sheet_names, xlsx_rows

PARSER_VERSION = "2026-11-16.1"
SHEET = "Transmission augmentations"
COLUMNS = ("Region", "Project type", "Project name", "Proponent(s)", "Project status",
           "Project description", "Increased network capacity", "In service date")
#: AEMO's status words → the shared vocabulary (checked before the generic mapping).
_STATUS = (("committed", "committed"), ("anticipated", "planned"),
           ("actionable", "planned"), ("future", "under_consideration"))


def _status(text: str) -> str:
    t = text.lower()
    return next((v for k, v in _STATUS if k in t), status_class(text))


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
        j = ix.get(col)
        return " ".join(row[j].split()) if j is not None and j < len(row) else ""

    out: list[dict[str, Any]] = []
    for row in rows[header_at + 1:]:
        name = cell(row, "Project name")
        if not name:
            continue
        region, kind = cell(row, "Region"), cell(row, "Project type")
        desc, status = cell(row, "Project description"), cell(row, "Project status")
        when, proponents = cell(row, "In service date"), cell(row, "Proponent(s)")
        out.append({
            "record_kind": "project",
            "external_id": _slug(region, name),
            "parent_external_id": None,
            "project_name": name[:500],
            "promoters_text": proponents[:1000] or None,
            "promoters": split_promoters(proponents),
            "region": f"Australia NEM: {region}"[:200] if region else "Australia NEM",
            "category": kind[:120] or None,
            "technology_tags": technology_tags(name, kind, desc),
            "voltage_kv": max_voltage_kv(name, desc),
            "description": desc[:4000] or None,
            "capacity_text": cell(row, "Increased network capacity")[:500] or None,
            "commissioning_text": (serial_to_iso(when) or "")[:120] or None,
            "commissioning_year": commissioning_year(when),
            "status_text": status[:200] or None,
            "status_class": _status(status),
        })
    if not out:
        raise LayoutError("no_projects", SHEET)
    return out


__all__ = ["PARSER_VERSION", "parse"]
