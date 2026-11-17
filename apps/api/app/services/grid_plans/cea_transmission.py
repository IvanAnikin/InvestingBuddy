"""CEA monthly progress reports of India's under-construction transmission projects —
V3.20 slice 11.5.

India's Central Electricity Authority publishes, monthly, two reports of transmission
projects under construction (Electricity Act 2003, section 73(i)&(j)):

* projects awarded through Tariff Based Competitive Bidding (TBCB);
* projects under the Regulated Tariff Mechanism (RTM).

Layout pinned on the October 2026 reports (downloaded 2026-11-17): one 17-column table
running across pages, its header on the first page only. A row with a serial number is
a PROJECT: its name and scope, the executing agency, its cost in Rs crore, the SPV's
transfer date and the original and anticipated completion. The rows under it are its
ELEMENTS (lines in ckm, transformation in MVA), with progress columns and remarks.
Columns are found BY NAME (whitespace removed: the PDF breaks words across lines, e.g.
"Executin g Agency"); anything else is a ``LayoutError``.

Capex: the project's stated cost, Rs crore × 10⁷ = rupees, currency INR, with the
stated figure kept in ``capex_period``. An element states no cost of its own and gets
none. The SPV a project names ("SPV: POWERGRID Vataman Transmission Ltd") is a second
promoter beside the executing agency; "PGCIL" also carries Power Grid Corporation of
India's full name. Nothing else is renamed.
"""

from __future__ import annotations

import io
import re
import unicodedata
from decimal import Decimal
from typing import Any

from app.services.grid_plans.base import commissioning_year, max_voltage_kv, technology_tags
from app.services.sources.issuer_holdings.base import LayoutError

PARSER_VERSION = "2026-11-17.1"

#: Column → the whitespace-free, lower-case header it must start with.
COLUMNS: dict[str, str] = {
    "name": "nameofthetransmissionproject",
    "agency": "executingagency",
    "ckm": "length(ckm)",
    "mva": "mva",
    "cost": "cost(rs",
    "spv_date": "dateoftransferofspv",
    "target_original": "completiontarget-original",
    "target_anticipated": "completiontarget-anticipated",
    "remarks": "remarks",
}
#: Full names for executing agencies the report abbreviates.
AGENCY_NAMES: dict[str, str] = {
    "PGCIL": "Power Grid Corporation of India Limited",
}
_SPV = re.compile(r"\(\s*SPV\s*[:\-]\s*([^()]{3,200}?)\s*\)", re.I)
_SERIAL = re.compile(r"^\d{1,4}$")
_CRORE = Decimal(10) ** 7
#: The report's own abbreviations, which the shared English tagger does not read.
_TAGS_IN: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("substation", re.compile(r"\bS/s\b|switching station|pooling station|\bGIS\b", re.I)),
    ("transformer", re.compile(r"\bICTs?\b", re.I)),
)


def tags(name: str) -> list[str]:
    found = technology_tags(name)
    return found + [t for t, rx in _TAGS_IN if rx.search(name) and t not in found]


def _cell(value: Any) -> str:
    return " ".join(unicodedata.normalize("NFKC", str(value or "")).split())


def _compact(value: Any) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", str(value or ""))).lower()


def header_columns(row: list[Any]) -> dict[str, int] | None:
    """Column positions when ``row`` is the report's header, else None."""
    cells = [_compact(c) for c in row]
    found: dict[str, int] = {}
    for key, prefix in COLUMNS.items():
        hit = next((j for j, c in enumerate(cells) if c.startswith(prefix)), None)
        if hit is None:
            return None
        found[key] = hit
    return found


def _amount(text: str) -> Decimal | None:
    """Indian-grouped crore ("1,74,248", "4,231") → Decimal; anything else → None."""
    t = text.replace(",", "").strip()
    if not re.fullmatch(r"\d{1,9}(?:\.\d{1,4})?", t):
        return None
    value = Decimal(t)
    return value if value > 0 else None


def _month(text: str) -> str:
    """"Mar - 27" → "Mar-27"."""
    return re.sub(r"\s*-\s*", "-", text).strip()


def promoters_of(agency: str, name: str) -> list[str]:
    out = [agency] if agency else []
    if agency in AGENCY_NAMES:
        out.append(AGENCY_NAMES[agency])
    spv = _SPV.search(name)
    if spv:
        out.append(" ".join(spv.group(1).split()))
    unique: dict[str, str] = {}
    for p in out:
        unique.setdefault(p.lower(), p)
    return list(unique.values())


def records_from_rows(rows: list[list[Any]], *, route: str) -> list[dict[str, Any]]:
    """Projects and their elements from the report's table rows, in document order."""
    cols: dict[str, int] | None = None
    out: list[dict[str, Any]] = []
    project: dict[str, Any] | None = None
    elements = 0
    for row in rows:
        found = header_columns(row)
        if found is not None:
            cols = found
            continue
        if cols is None:
            continue
        cells = [_cell(c) for c in row] + [""] * 20

        def col(key: str, cells: list[str] = cells) -> str:
            return cells[cols[key]] if cols else ""

        serial, name = cells[0], col("name")
        if not name:
            continue
        agency = col("agency")
        anticipated, original = _month(col("target_anticipated")), _month(
            col("target_original"))
        when = anticipated or original
        when_text = (f"{anticipated} (anticipated; original {original})"
                     if anticipated and original and anticipated != original
                     else when)
        size = "; ".join(p for p in (f"{col('ckm')} ckm" if col("ckm") else "",
                                     f"{col('mva')} MVA" if col("mva") else "") if p)
        common = {
            "promoters_text": agency[:1000] or None,
            "region": "India",
            "technology_tags": tags(name),
            "voltage_kv": max_voltage_kv(name),
            "capacity_text": size[:500] or None,
            "commissioning_text": when_text[:120] or None,
            "commissioning_year": commissioning_year(when),
            "status_text": f"under construction ({route} route; CEA monthly progress report)",
            "status_class": "under_construction",
        }
        if _SERIAL.match(serial):
            cost = _amount(col("cost"))
            elements = 0
            project = {
                **common,
                "record_kind": "project",
                "external_id": f"{route.lower()}-{serial}",
                "parent_external_id": None,
                "project_name": name[:500],
                "promoters": promoters_of(agency, name),
                "category": f"{route} transmission project",
                "description": "; ".join(p for p in (
                    f"SPV transferred {col('spv_date')}" if col("spv_date") else "",
                    f"completion target original {original}" if original else "",
                    f"anticipated {anticipated}" if anticipated else "") if p)[:4000] or None,
                "capex_amount": cost * _CRORE if cost else None,
                "capex_currency": "INR" if cost else None,
                "capex_period": f"project cost as stated: Rs {col('cost')} crore"[:60]
                                if cost else None,
            }
            out.append(project)
        elif project is not None:
            elements += 1
            remarks = col("remarks")
            out.append({
                **common,
                "record_kind": "investment",
                "external_id": f"{project['external_id']}-e{elements}",
                "parent_external_id": project["external_id"],
                "project_name": name[:500],
                "promoters": promoters_of(agency or (project["promoters_text"] or ""),
                                          project["project_name"]),
                "category": f"{route} transmission project element",
                "description": (f"remarks: {remarks}"[:4000]) if remarks else None,
            })
    return out


def rows_from_pdf(body: bytes) -> list[list[Any]]:
    """Every table row of the report, page after page."""
    import pdfplumber

    if not body.startswith(b"%PDF"):
        raise LayoutError("not_a_pdf", "")
    rows: list[list[Any]] = []
    with pdfplumber.open(io.BytesIO(body)) as pdf:
        for page in pdf.pages:
            for table in page.extract_tables():
                rows.extend(table)
    return rows


def _parse(body: bytes, route: str) -> list[dict[str, Any]]:
    rows = rows_from_pdf(body)
    if not any(header_columns(r) for r in rows):
        raise LayoutError("missing_header", "Name of the Transmission Project & Scope")
    out = records_from_rows(rows, route=route)
    if not any(r["record_kind"] == "project" for r in out):
        raise LayoutError("no_projects", route)
    return out


def parse_tbcb(body: bytes) -> list[dict[str, Any]]:
    return _parse(body, "TBCB")


def parse_rtm(body: bytes) -> list[dict[str, Any]]:
    return _parse(body, "RTM")


__all__ = ["AGENCY_NAMES", "COLUMNS", "PARSER_VERSION", "header_columns", "parse_rtm",
           "parse_tbcb", "promoters_of", "records_from_rows", "rows_from_pdf", "tags"]
