"""ENTSO-E TYNDP 2026 project portfolio parser — V3.20 slice 7.1.

Layout pinned on the real file ``20260203_TYNDP2026_project_portfolio_final.xlsx``
(downloaded 2026-11-16): sheets "Transmission Projects", "Transmission Investments" and
"Storage Projects", each with a header row whose cells are looked up BY NAME. A missing
sheet or required column fails closed (``LayoutError``) — nothing is half-ingested.

The portfolio states no capex: capex columns stay empty (the CBA results come with the
final TYNDP 2026). An investment inherits its project's promoters and region. Projects
listed as non-submitted or non-compliant are not ingested.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.services.grid_plans.base import (
    commissioning_year,
    max_voltage_kv,
    split_promoters,
    status_class,
    technology_tags,
)
from app.services.sources.issuer_holdings.base import LayoutError
from app.services.sources.issuer_holdings.xlsx import sheet_names, xlsx_rows

PARSER_VERSION = "2026-11-16.1"
SHEET_PROJECTS = "Transmission Projects"
SHEET_INVESTMENTS = "Transmission Investments"
SHEET_STORAGE = "Storage Projects"
#: Optional: "Internal (Portugal)" / "Spain-Portugal" per transmission project.
SHEET_BORDERS = "Borders"
PROJECT_COLUMNS = ("Type", "Project ID", "Name", "Project promoters", "Description",
                   "Project category", "Status", "Commissioning year")
INVESTMENT_COLUMNS = ("Project ID", "Investment ID", "Investment Name",
                      "Description of the investment", "Status")
INVESTMENT_DATE_PREFIX = "Commissioning month and year"


def _table(rows: Sequence[Sequence[str]], required: Sequence[str], sheet: str,
           prefix_cols: Sequence[str] = ()) -> tuple[dict[str, int], list[Sequence[str]]]:
    for i, row in enumerate(rows[:10]):
        cells = [c.strip() for c in row]
        if all(col in cells for col in required):
            index = {c: j for j, c in enumerate(cells) if c}
            for prefix in prefix_cols:
                hit = next((c for c in index if c.startswith(prefix)), None)
                if hit is None:
                    raise LayoutError("missing_column", f"{sheet}: {prefix}")
                index[prefix] = index[hit]
            return index, [r for r in rows[i + 1:] if any(c.strip() for c in r)]
    raise LayoutError("missing_header", sheet)


def _cell(row: Sequence[str], index: dict[str, int], col: str) -> str:
    j = index.get(col)
    return row[j].strip() if j is not None and j < len(row) else ""


def _borders(body: bytes, names: set[str]) -> dict[str, str]:
    if SHEET_BORDERS not in names:
        return {}
    try:
        bix, brows = _table(xlsx_rows(body, SHEET_BORDERS),
                            ("TransmissionProject_Id", "Border"), SHEET_BORDERS)
    except LayoutError:
        return {}  # optional enrichment: the region falls back to the scope column
    out: dict[str, list[str]] = {}
    for row in brows:
        pid, border = _cell(row, bix, "TransmissionProject_Id"), _cell(row, bix, "Border")
        if pid and border and border not in out.setdefault(pid, []):
            out[pid].append(border)
    return {pid: "; ".join(v)[:200] for pid, v in out.items()}


def _project(row: Sequence[str], ix: dict[str, int], *, kind_label: str,
             region: str | None = None) -> dict[str, Any]:
    name, desc = _cell(row, ix, "Name"), _cell(row, ix, "Description")
    category = _cell(row, ix, "Project category")
    border = _cell(row, ix, "Cross-border or internal")
    commissioning = _cell(row, ix, "Commissioning year")
    status = _cell(row, ix, "Status")
    promoters = _cell(row, ix, "Project promoters")
    return {
        "record_kind": "project",
        "external_id": f"{kind_label}:{_cell(row, ix, 'Project ID')}",
        "parent_external_id": None,
        "project_name": name[:500] or f"TYNDP project {_cell(row, ix, 'Project ID')}",
        "promoters_text": promoters[:1000] or None,
        "promoters": split_promoters(promoters),
        "region": region or border[:200] or None,
        "category": " / ".join(x for x in (_cell(row, ix, "Type"), category) if x)[:120],
        "technology_tags": technology_tags(name, category, desc),
        "voltage_kv": max_voltage_kv(name, desc),
        "description": desc[:4000] or None,
        "commissioning_text": commissioning[:120] or None,
        "commissioning_year": commissioning_year(commissioning),
        "status_text": status[:200] or None,
        "status_class": status_class(status),
    }


def parse(body: bytes) -> list[dict[str, Any]]:
    """Every project, storage project and investment in the portfolio, as row dicts."""
    names = set(sheet_names(body))
    for sheet in (SHEET_PROJECTS, SHEET_INVESTMENTS, SHEET_STORAGE):
        if sheet not in names:
            raise LayoutError("missing_sheet", sheet)
    out: list[dict[str, Any]] = []
    borders = _borders(body, names)
    pix, prows = _table(xlsx_rows(body, SHEET_PROJECTS), PROJECT_COLUMNS, SHEET_PROJECTS)
    projects: dict[str, dict[str, Any]] = {}
    for row in prows:
        if not _cell(row, pix, "Project ID"):
            continue
        rec = _project(row, pix, kind_label="transmission",
                       region=borders.get(_cell(row, pix, "Project ID")))
        projects[_cell(row, pix, "Project ID")] = rec
        out.append(rec)
    six, srows = _table(xlsx_rows(body, SHEET_STORAGE), PROJECT_COLUMNS, SHEET_STORAGE)
    for row in srows:
        if _cell(row, six, "Project ID"):
            out.append(_project(row, six, kind_label="storage"))
    iix, irows = _table(xlsx_rows(body, SHEET_INVESTMENTS), INVESTMENT_COLUMNS,
                        SHEET_INVESTMENTS, prefix_cols=(INVESTMENT_DATE_PREFIX,))
    for row in irows:
        pid, inv = _cell(row, iix, "Project ID"), _cell(row, iix, "Investment ID")
        if not inv:
            continue
        parent = projects.get(pid, {})
        name = _cell(row, iix, "Investment Name")
        desc = _cell(row, iix, "Description of the investment")
        when, status = _cell(row, iix, INVESTMENT_DATE_PREFIX), _cell(row, iix, "Status")
        out.append({
            "record_kind": "investment",
            "external_id": f"investment:{inv}",
            "parent_external_id": f"transmission:{pid}" if pid else None,
            "project_name": name[:500] or f"TYNDP investment {inv}",
            "promoters_text": parent.get("promoters_text"),
            "promoters": parent.get("promoters") or [],
            "region": parent.get("region"),
            "category": parent.get("category"),
            "technology_tags": technology_tags(name, desc),
            "voltage_kv": max_voltage_kv(name, desc),
            "description": desc[:4000] or None,
            "commissioning_text": when[:120] or None,
            "commissioning_year": commissioning_year(when),
            "status_text": status[:200] or None,
            "status_class": status_class(status),
        })
    if not projects:
        raise LayoutError("no_projects", SHEET_PROJECTS)
    return out


__all__ = ["PARSER_VERSION", "parse"]
