"""Deterministic, in-code PDF byte fixtures for offline extraction tests.

These builders emit real, minimal PDF files (no external tooling, no network)
so extraction tests are fully offline and reproducible. Lifted from
``tests/test_phase29b2_document_extraction.py`` so both the Phase 29B.2 tests and
the Phase 32A Slice 5 tests share one source of truth.
"""

from __future__ import annotations

import io


def _assemble(objs: list[bytes]) -> bytes:
    """Serialize a list of PDF object bodies (1-indexed) into a valid PDF file."""
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets: list[int] = []
    for i, body in enumerate(objs, start=1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref_pos = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(
        f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n"
        f"{xref_pos}\n%%EOF".encode()
    )
    return out.getvalue()


def make_pdf(pages_text: list[str]) -> bytes:
    """Build a minimal, text-extractable multi-page PDF (Tj operators)."""
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages_text)))
    objs.append(
        f"<< /Type /Pages /Kids [{kids}] /Count {len(pages_text)} >>".encode()
    )
    font_obj_num = 3 + len(pages_text) * 2
    for i, text in enumerate(pages_text):
        content_num = 4 + i * 2
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
                f"/Contents {content_num} 0 R /Resources << /Font "
                f"<< /F1 {font_obj_num} 0 R >> >> >>"
            ).encode()
        )
        lines = text.split("\n")
        content = "BT /F1 12 Tf 72 720 Td 14 TL "
        for j, ln in enumerate(lines):
            esc = ln.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
            content += (f"({esc}) Tj " if j == 0 else f"T* ({esc}) Tj ")
        content += "ET"
        cb = content.encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return _assemble(objs)


def make_pdf_no_text() -> bytes:
    """A valid PDF whose only content is a filled rectangle — no extractable text."""
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << >> >>",
    ]
    content = b"0 0 1 rg 100 100 200 200 re f"
    objs.append(b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream")
    return _assemble(objs)


def make_pdf_with_table(rows: list[list[str]]) -> bytes:
    """Build a single-page PDF with a ruled grid table pdfplumber can detect.

    Draws vertical + horizontal stroke lines forming the cell grid, then places
    each cell's text inside its cell, so pdfplumber's default (line-based) table
    detection recovers the rows exactly. ``rows`` must be rectangular.
    """
    if not rows or not rows[0]:
        raise ValueError("rows must be a non-empty rectangular grid")
    n_rows = len(rows)
    n_cols = len(rows[0])

    # Column x-boundaries (n_cols + 1) and row y-boundaries (n_rows + 1), top-down.
    col_x = [100 + c * 130 for c in range(n_cols + 1)]
    row_y = [700 - r * 30 for r in range(n_rows + 1)]

    ops: list[str] = ["1 w"]
    for x in col_x:  # vertical rules
        ops.append(f"{x} {row_y[-1]} m {x} {row_y[0]} l S")
    for y in row_y:  # horizontal rules
        ops.append(f"{col_x[0]} {y} m {col_x[-1]} {y} l S")

    ops.append("BT /F1 10 Tf")
    for r in range(n_rows):
        for c in range(n_cols):
            x = col_x[c] + 5
            y = row_y[r] - 20
            val = (
                str(rows[r][c])
                .replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
            )
            ops.append(f"1 0 0 1 {x} {y} Tm ({val}) Tj")
    ops.append("ET")

    cb = "\n".join(ops).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
        b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _assemble(objs)


__all__ = ["make_pdf", "make_pdf_no_text", "make_pdf_with_table"]
