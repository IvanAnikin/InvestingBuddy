"""A minimal multi-sheet OOXML workbook builder for parser tests (stdlib only)."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape

_NS = 'xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
_R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
_PR = 'xmlns="http://schemas.openxmlformats.org/package/2006/relationships"'


def _col(i: int) -> str:
    out = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        out = chr(65 + rem) + out
    return out


def workbook(sheets: dict[str, list[list[object]]]) -> bytes:
    """Strings go to the shared-string table; ints and floats are numeric cells."""
    strings: list[str] = []
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        book, rels = [], []
        for n, (name, rows) in enumerate(sheets.items(), start=1):
            xml_rows = []
            for r, row in enumerate(rows, start=1):
                cells = []
                for c, value in enumerate(row):
                    ref = f"{_col(c)}{r}"
                    if value is None or value == "":
                        continue
                    if isinstance(value, int | float):
                        cells.append(f'<c r="{ref}"><v>{value}</v></c>')
                    else:
                        strings.append(str(value))
                        cells.append(f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>')
                xml_rows.append(f'<row r="{r}">{"".join(cells)}</row>')
            z.writestr(f"xl/worksheets/sheet{n}.xml",
                       f'<?xml version="1.0"?><worksheet {_NS}><sheetData>'
                       f'{"".join(xml_rows)}</sheetData></worksheet>')
            book.append(f'<sheet name="{escape(name)}" sheetId="{n}" r:id="rId{n}"/>')
            rels.append(f'<Relationship Id="rId{n}" Type="worksheet" '
                        f'Target="worksheets/sheet{n}.xml"/>')
        z.writestr("xl/workbook.xml", f'<?xml version="1.0"?><workbook {_NS} {_R}><sheets>'
                                      f'{"".join(book)}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   f'<?xml version="1.0"?><Relationships {_PR}>{"".join(rels)}</Relationships>')
        sst = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
        z.writestr("xl/sharedStrings.xml", f'<?xml version="1.0"?><sst {_NS}>{sst}</sst>')
    return buf.getvalue()
