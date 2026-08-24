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


def make_encrypted_pdf(
    pages_text: list[str] | None = None, *, password: str = "secret-pw"
) -> bytes:
    """Build a valid PDF encrypted with a NON-EMPTY user password.

    The ``%PDF-`` magic byte stays valid (so the fetch magic-sniff passes) but the
    body is encrypted, so the extractor's single empty-password ``pdfplumber.open``
    attempt fails — exercising the encrypted/decrypt-fail degradation branch. Uses
    pypdf lazily so the pure-stdlib fixtures above stay dependency-free.
    """
    import io as _io

    from pypdf import PdfReader, PdfWriter

    raw = make_pdf(pages_text or ["Encrypted annual report body; do not fabricate."])
    reader = PdfReader(_io.BytesIO(raw))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.encrypt(password)
    out = _io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_pdf_with_image(
    text: str = "Consolidated Balance Sheet",
    *,
    width: int = 600,
    height: int = 200,
) -> bytes:
    """A valid PDF whose page is a rendered image with NO text layer.

    Unlike ``make_pdf_no_text`` (a vector-only filled rectangle), this embeds a
    genuine raster ``/Image`` XObject — no ``Tj``/``TJ`` text-showing operator
    anywhere in the page — so it exercises BOTH the existing "scanned, no
    text" classification path (``pdfplumber.extract_text()`` returns ``""``,
    same as ``make_pdf_no_text``) AND a real OCR-recoverable raster for the
    Phase 32A Slice 5B.2 OCR-path tests (a fake OCR provider can assert it was
    handed genuine image bytes, not an empty/degenerate image).

    Renders ``text`` via Pillow (already a transitive dependency through
    pdfplumber) as an uncompressed 8-bit grayscale raster, imported lazily so
    the pure-stdlib fixtures above stay dependency-free.
    """
    from PIL import Image, ImageDraw

    img = Image.new("L", (width, height), color=255)
    draw = ImageDraw.Draw(img)
    draw.text((10, max(0, height // 2 - 10)), text, fill=0)
    raw_gray = img.tobytes()  # DeviceGray, 8 bits/component, uncompressed

    image_obj = (
        f"<< /Type /XObject /Subtype /Image /Width {width} /Height {height} "
        f"/ColorSpace /DeviceGray /BitsPerComponent 8 /Length {len(raw_gray)} >>"
    ).encode() + b"\nstream\n" + raw_gray + b"\nendstream"

    content = f"q {width} 0 0 {height} 0 0 cm /Im0 Do Q".encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Contents 4 0 R /Resources << /XObject << /Im0 5 0 R >> >> >>"
        ).encode(),
        b"<< /Length %d >>\nstream\n" % len(content) + content + b"\nendstream",
        image_obj,
    ]
    return _assemble(objs)


def make_multi_page_scanned_pdf(page_texts: list[str], *, width: int = 600, height: int = 200) -> bytes:
    """A valid, multi-page, NO-text-layer PDF (each page a rendered image).

    Built by rendering one single-page ``make_pdf_with_image``-style document
    per entry in ``page_texts`` and merging them with ``pypdf.PdfWriter`` —
    used to exercise page SELECTION (:func:`select_ocr_pages` choosing a
    bounded subset from a larger real-shaped document) and page-subset
    extraction (:func:`app.services.sources.ocr_provider.extract_page_subset`)
    together, which a single-page fixture cannot cover (a 1-page source's
    "subset" is trivially the whole document).
    """
    import io as _io

    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for text in page_texts:
        page_pdf = make_pdf_with_image(text, width=width, height=height)
        reader = PdfReader(_io.BytesIO(page_pdf))
        writer.add_page(reader.pages[0])
    out = _io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_pdf_with_outline(pages_text: list[str], bookmarks: dict[int, str]) -> bytes:
    """A multi-page, text-extractable PDF (via :func:`make_pdf`) with a real
    pypdf outline/bookmark tree added on top — ``bookmarks`` maps a 1-based
    page number to its bookmark title. Used to exercise the Phase 32A
    corrective (Problem C) targeted supplemental-page selection, which reads
    ONLY the outline metadata, never a second full-text pass.
    """
    import io as _io

    from pypdf import PdfReader, PdfWriter

    raw = make_pdf(pages_text)
    reader = PdfReader(_io.BytesIO(raw))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    for page_no, title in bookmarks.items():
        writer.add_outline_item(title, page_no - 1)
    out = _io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_pdf_two_column(
    left_lines: list[str],
    right_lines: list[str],
    *,
    heading: str | None = None,
    left_x: int = 60,
    right_x: int = 320,
    top_y: int = 700,
    line_height: int = 16,
    width: int = 612,
    height: int = 792,
) -> bytes:
    """Build a single-page PDF with two independently-positioned text columns.

    Each column line is placed with an ABSOLUTE ``Tm`` matrix (not relative
    ``Td``/``T*``), so a left/right line pair at the SAME row index shares the
    SAME vertical position on the page — exactly the shape that defeats
    pdfplumber's default top-to-bottom ``extract_text()`` reading order (it
    interleaves the two columns line-by-line instead of reading one column
    fully, then the other). An optional ``heading`` is drawn as one wide,
    full-page-width line above both columns. Used to prove the Phase 32A
    corrective generic column-reconstruction fix without any hardcoded
    company/document content.
    """

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops: list[str] = ["BT /F1 11 Tf"]
    if heading:
        ops.append(f"1 0 0 1 40 {top_y + 2 * line_height} Tm ({_esc(heading)}) Tj")
    for i, ln in enumerate(left_lines):
        y = top_y - i * line_height
        ops.append(f"1 0 0 1 {left_x} {y} Tm ({_esc(ln)}) Tj")
    for i, ln in enumerate(right_lines):
        y = top_y - i * line_height
        ops.append(f"1 0 0 1 {right_x} {y} Tm ({_esc(ln)}) Tj")
    ops.append("ET")
    cb = "\n".join(ops).encode()

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ).encode(),
        b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _assemble(objs)


def make_pdf_bands(
    bands: list[tuple],
    *,
    left_x: int = 60,
    right_x: int = 320,
    top_y: int = 700,
    line_height: int = 16,
    band_gap: int = 24,
    width: int = 612,
    height: int = 792,
) -> bytes:
    """Build a single-page PDF from an ordered sequence of vertical BANDS.

    Each entry in ``bands`` is either ``("full", text)`` — one full-page-width
    line (a heading, section separator, or footer/note) — or
    ``("columns", left_lines, right_lines)`` — a run of two-column body text.
    Bands are laid out top-to-bottom in the given order, each using its own
    ABSOLUTE ``Tm`` placement, so a ``"full"`` band sits at its own true
    vertical position BETWEEN the column content immediately above and below
    it — exactly the shape needed to prove a mid-page section heading is not
    hoisted out of its original position by column reconstruction. Used by
    the Phase 32A corrective vertical-band-ordering regression tests.
    """

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    ops: list[str] = ["BT /F1 11 Tf"]
    y = float(top_y)
    for band in bands:
        if band[0] == "full":
            text = band[1]
            ops.append(f"1 0 0 1 40 {y} Tm ({_esc(text)}) Tj")
            y -= band_gap
        else:
            _, left_lines, right_lines = band
            n = max(len(left_lines), len(right_lines))
            for i in range(n):
                row_y = y - i * line_height
                if i < len(left_lines):
                    ops.append(f"1 0 0 1 {left_x} {row_y} Tm ({_esc(left_lines[i])}) Tj")
                if i < len(right_lines):
                    ops.append(f"1 0 0 1 {right_x} {row_y} Tm ({_esc(right_lines[i])}) Tj")
            y -= n * line_height + band_gap
    ops.append("ET")
    cb = "\n".join(ops).encode()

    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
            f"/Contents 4 0 R /Resources << /Font << /F1 5 0 R >> >> >>"
        ).encode(),
        b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    return _assemble(objs)


def make_pdf_with_sized_lines(
    pages: list[list[tuple[str, float, bool]]],
    *,
    x: int = 72,
    top_y: int = 740,
    line_height: int = 14,
    width: int = 612,
    height: int = 792,
) -> bytes:
    """Multi-page PDF where each physical line carries its OWN font size and
    bold flag — ``pages`` is a list of pages, each a list of ``(text, size,
    bold)`` triples placed top-to-bottom at absolute Y positions (``Tm``).

    Two font resources are registered — ``/F1`` (Helvetica) and ``/F2``
    (Helvetica-Bold) — switched per line via the ``bold`` flag. Used to prove
    the generic, page-relative PDF heading-detection heuristic (a line's own
    font size strictly larger than the page's dominant body-text size,
    optionally bold) without any hardcoded company/document vocabulary.
    """

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_reg_num = 3 + len(pages) * 2
    font_bold_num = font_reg_num + 1
    for i, lines in enumerate(pages):
        content_num = 4 + i * 2
        ops: list[str] = ["BT"]
        y = float(top_y)
        for text, size, bold in lines:
            font = "/F2" if bold else "/F1"
            ops.append(f"{font} {size} Tf")
            ops.append(f"1 0 0 1 {x} {y} Tm ({_esc(text)}) Tj")
            y -= line_height
        ops.append("ET")
        cb = "\n".join(ops).encode()
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {width} {height}] "
                f"/Contents {content_num} 0 R /Resources << /Font "
                f"<< /F1 {font_reg_num} 0 R /F2 {font_bold_num} 0 R >> >> >>"
            ).encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>")
    return _assemble(objs)


__all__ = [
    "make_pdf",
    "make_pdf_no_text",
    "make_pdf_with_table",
    "make_encrypted_pdf",
    "make_pdf_with_image",
    "make_multi_page_scanned_pdf",
    "make_pdf_with_outline",
    "make_pdf_two_column",
    "make_pdf_bands",
    "make_pdf_with_sized_lines",
]


# --------------------------------------------------------------------------- #
# Positioned-text pages (Phase 32D — borderless multi-year financial tables)
#
# ``make_pdf_with_table`` above draws a RULED grid, which is what pdfplumber's
# line-based ``extract_tables()`` needs. The tables this campaign exists for
# have no rules at all: a five-year summary is held together purely by
# whitespace alignment. These builders place each word at an exact (x, y) so a
# test can pin the real geometry — right-aligned value columns under year
# headers, two tables printed side by side — rather than a grid pdfplumber
# would have found anyway.
# --------------------------------------------------------------------------- #

# Helvetica advance widths (1/1000 em) for the few glyphs these fixtures use.
# Enough to right-align a numeric column the way a real report does.
_HELVETICA_WIDTHS = {
    " ": 278, ",": 278, ".": 278, "%": 889, "-": 333, "(": 333, ")": 333,
    "/": 278, "€": 556, "£": 556, "$": 556,
}
_HELVETICA_DIGIT_WIDTH = 556
_HELVETICA_DEFAULT_WIDTH = 556


def helvetica_width(text: str, size: float) -> float:
    """Approximate rendered width of ``text`` in Helvetica at ``size`` points."""
    total = 0
    for ch in text:
        if ch.isdigit():
            total += _HELVETICA_DIGIT_WIDTH
        else:
            total += _HELVETICA_WIDTHS.get(ch, _HELVETICA_DEFAULT_WIDTH)
    return total * size / 1000.0


def make_pdf_positioned_text(
    pages: list[list[tuple[str, float, float, float]]],
    *,
    page_width: float = 907.0,
    page_height: float = 510.0,
) -> bytes:
    """Build a PDF whose every word is placed at an exact position.

    Each page is a list of ``(text, x_left, y_from_top, font_size)``. ``y`` is
    measured DOWNWARD from the top of the page (the same direction pdfplumber
    reports as ``top``), so a fixture reads in the same order as the page.
    """
    objs: list[bytes] = [b"<< /Type /Catalog /Pages 2 0 R >>"]
    kids = " ".join(f"{3 + i * 2} 0 R" for i in range(len(pages)))
    objs.append(f"<< /Type /Pages /Kids [{kids}] /Count {len(pages)} >>".encode())
    font_obj_num = 3 + len(pages) * 2
    for i, words in enumerate(pages):
        content_num = 4 + i * 2
        objs.append(
            (
                f"<< /Type /Page /Parent 2 0 R /MediaBox "
                f"[0 0 {page_width} {page_height}] /Contents {content_num} 0 R "
                f"/Resources << /Font << /F1 {font_obj_num} 0 R >> >> >>"
            ).encode()
        )
        ops: list[str] = ["BT"]
        for text, x, y_from_top, size in words:
            esc = (
                str(text)
                .replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
            )
            # PDF y grows upward; subtract the font size so the value given is
            # the TOP of the glyph box, matching pdfplumber's ``top``.
            baseline = page_height - y_from_top - size
            ops.append(f"/F1 {size} Tf 1 0 0 1 {x:.2f} {baseline:.2f} Tm ({esc}) Tj")
        ops.append("ET")
        cb = "\n".join(ops).encode()
        objs.append(b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    return _assemble(objs)


def right_aligned_row(
    label: str,
    values: list[str],
    *,
    y: float,
    label_x: float,
    column_right_edges: list[float],
    size: float = 8.0,
) -> list[tuple[str, float, float, float]]:
    """One metric row whose values are RIGHT-aligned on their column edges.

    This is how a real financial table sets a numeric column, and it is what
    makes a value's own x-centre drift left as the number gets wider — the
    exact geometry the column-assignment logic has to cope with.
    """
    out: list[tuple[str, float, float, float]] = [(label, label_x, y, size)]
    for value, right_edge in zip(values, column_right_edges):
        if value == "":
            continue
        out.append((value, right_edge - helvetica_width(value, size), y, size))
    return out
