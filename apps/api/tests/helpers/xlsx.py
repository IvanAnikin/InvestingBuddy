"""A minimal .xlsx built with the standard library, for directory parsers (ADR-120)."""

from __future__ import annotations

import io
import zipfile
from xml.sax.saxutils import escape


def _col(n: int) -> str:
    out = ""
    while n:
        n, rem = divmod(n - 1, 26)
        out = chr(65 + rem) + out
    return out


def build_xlsx(rows: list[list[str]]) -> bytes:
    """One worksheet; every cell an inline string, placed by its column letter."""
    body = []
    for r, row in enumerate(rows, start=1):
        cells = "".join(
            f'<c r="{_col(c)}{r}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'
            for c, v in enumerate(row, start=1) if v != ""
        )
        body.append(f'<row r="{r}">{cells}</row>')
    sheet = ('<?xml version="1.0" encoding="UTF-8"?>'
             '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
             f'<sheetData>{"".join(body)}</sheetData></worksheet>')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


LSE_HEADER = ["TIDM", "Issuer Name", "Instrument Name", "ISIN", "MiFIR Identifier Code",
              "MiFIR Indentifier Name", "ICB Industry", "ICB Super-Sector Name", "Start date",
              "Country of Incorporation", "Trading Currency", "LSE Market"]


def lse_instrument_list(*instruments: tuple[str, str, str, str]) -> bytes:
    """(tidm, issuer, isin, mifir) rows under the real file's title and header rows."""
    rows = [[""], ["All Equity Instruments"], ["As at 31 October 2026"], [""], LSE_HEADER]
    for tidm, issuer, isin, mifir in instruments:
        rows.append([tidm, issuer, "ORD 1P", isin, mifir, "Shares", "Technology",
                     "Technology", "01/01/2020", "United Kingdom", "GBX", "AIM"])
    return build_xlsx(rows)
