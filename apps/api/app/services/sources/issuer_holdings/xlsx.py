"""A minimal, bounded XLSX reader (stdlib only) for issuer holdings files — V3.20 slice 3.5.

Reads the FIRST worksheet — or, since V3.20 slice 7.1, a worksheet NAMED by the caller
(``sheet=``; ``sheet_names`` lists them) — of an Office Open XML workbook into rows of
strings. Shared
strings and inline strings are resolved; numbers stay as their stored text ("3.5753686E7").
No formulas, styles or dates are interpreted. Limits guard against a hostile file: the
archive member sizes are checked before decompression (zip bomb), and a cell count cap
stops runaway sheets. An unreadable workbook raises ``LayoutError("unreadable_xlsx")``.
"""

from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
import zipfile

from app.services.sources.issuer_holdings.base import LayoutError

_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
MAX_MEMBER_BYTES = 40_000_000
MAX_CELLS = 2_000_000
_COL_RE = re.compile(r"^([A-Z]+)")
_REL_NS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"
_PKG_REL_NS = "{http://schemas.openxmlformats.org/package/2006/relationships}"


def _col_index(ref: str | None) -> int | None:
    match = _COL_RE.match(ref or "")
    if not match:
        return None
    index = 0
    for ch in match.group(1):
        index = index * 26 + (ord(ch) - 64)
    return index - 1


def _member(zf: zipfile.ZipFile, name: str) -> bytes | None:
    try:
        info = zf.getinfo(name)
    except KeyError:
        return None
    if info.file_size > MAX_MEMBER_BYTES:
        raise LayoutError("xlsx_member_too_large", name)
    return zf.read(name)


def _first_sheet(zf: zipfile.ZipFile) -> str:
    names = sorted(n for n in zf.namelist()
                   if n.startswith("xl/worksheets/sheet") and n.endswith(".xml"))
    if not names:
        raise LayoutError("unreadable_xlsx", "no worksheet")
    return "xl/worksheets/sheet1.xml" if "xl/worksheets/sheet1.xml" in names else names[0]


def _sheet_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """Sheet name -> archive member, from workbook.xml and its relationships."""
    book, rels = _member(zf, "xl/workbook.xml"), _member(zf, "xl/_rels/workbook.xml.rels")
    if book is None or rels is None:
        return {}
    try:
        targets = {r.get("Id"): r.get("Target") or ""
                   for r in ET.fromstring(rels).iter(f"{_PKG_REL_NS}Relationship")}
        out: dict[str, str] = {}
        for sh in ET.fromstring(book).iter(f"{_NS}sheet"):
            target = targets.get(sh.get(f"{_REL_NS}id"), "")
            if target:
                target = target.lstrip("/")
                out[sh.get("name") or ""] = target if target.startswith("xl/") else f"xl/{target}"
        return out
    except ET.ParseError:
        raise LayoutError("unreadable_xlsx", "malformed XML") from None


def _open(body: bytes) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(io.BytesIO(body))
    except zipfile.BadZipFile:
        raise LayoutError("unreadable_xlsx", "not a zip archive") from None


def sheet_names(body: bytes) -> list[str]:
    """The workbook's sheet names, in workbook order."""
    with _open(body) as zf:
        return list(_sheet_map(zf))


def xlsx_rows(body: bytes, sheet: str | None = None) -> list[list[str]]:
    """The first (or the named) sheet as rows of cell text (gaps filled with "")."""
    zf = _open(body)
    with zf:
        member = _first_sheet(zf)
        if sheet is not None:
            member = _sheet_map(zf).get(sheet, "")
            if not member:
                raise LayoutError("missing_sheet", sheet)
        shared: list[str] = []
        raw = _member(zf, "xl/sharedStrings.xml")
        try:
            if raw is not None:
                for si in ET.fromstring(raw).iter(f"{_NS}si"):
                    shared.append("".join(t.text or "" for t in si.iter(f"{_NS}t")))
            sheet_raw = _member(zf, member)
            root = ET.fromstring(sheet_raw or b"")
        except ET.ParseError:
            raise LayoutError("unreadable_xlsx", "malformed XML") from None
    data = root.find(f"{_NS}sheetData")
    rows: list[list[str]] = []
    cells = 0
    for row in (data.findall(f"{_NS}row") if data is not None else []):
        out: list[str] = []
        for c in row.findall(f"{_NS}c"):
            cells += 1
            if cells > MAX_CELLS:
                raise LayoutError("xlsx_too_many_cells")
            idx = _col_index(c.get("r"))
            kind = c.get("t")
            if kind == "inlineStr":
                text = "".join(t.text or "" for t in c.iter(f"{_NS}t"))
            else:
                v = c.find(f"{_NS}v")
                text = v.text or "" if v is not None else ""
                if kind == "s":
                    try:
                        text = shared[int(text)]
                    except (ValueError, IndexError):
                        raise LayoutError("unreadable_xlsx", "bad shared string") from None
            if idx is None:
                idx = len(out)
            while len(out) < idx:
                out.append("")
            out.append(text.strip())
        rows.append(out)
    return rows
