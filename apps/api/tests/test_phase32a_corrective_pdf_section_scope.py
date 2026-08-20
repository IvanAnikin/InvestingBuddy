"""
Phase 32A corrective — PDF structural section-scope context.

Fully OFFLINE and deterministic: real (but tiny, in-code) PDF bytes via
``tests/helpers/pdf_fixtures.make_pdf_with_sized_lines`` (each physical line
carries its own font size + bold flag); no network, no LLM, no DB.

Root cause (proved live on staging against the REAL Richemont FY26 annual
report, report ``f7721676-21a1-4015-9ed3-af5065bb6a7b``): a Specialist
Watchmakers segment note's operating-result sentence ("The operating result
reached EUR 107 million...") sits several ``_blocks()``-sized chunks away from
its own governing "Specialist Watchmakers" heading on a single, continuous,
blank-line-free PDF page — PDF extraction carried NO heading/ancestor context
at all (``section``/``ancestor`` were hardcoded ``None`` for every PDF block),
so the fact's own local sentence had zero scope signal and the validator
correctly, honestly withheld it rather than guess.

Fix: ``primary_document_extractor._page_heading_sizes`` detects heading-like
PDF lines generically from pdfplumber word geometry (font size strictly
larger than the page's own dominant body-text size, optionally bold — no
company/section vocabulary, no hardcoded absolute point size), and
``_tag_blocks_with_headings`` threads a font-size-keyed heading-LEVEL stack
(the PDF analogue of ``_DocumentHtmlParser``'s DOM h1-h6 stack) through
``_extract_one_page``'s existing page loop, populating the PREVIOUSLY-ALWAYS-
``None`` ``section``/``ancestor`` block slots. This reuses the EXISTING
``_infer_scope`` machinery unchanged (an ancestor carrying generic segment
vocabulary lets a named leaf heading with no vocabulary of its own resolve as
the scope label) and the EXISTING non-contiguous-page-jump reset (mirrors
``running_scope``) — no ranking/parser financial-value logic is touched.

Covers (standing corrective brief section 7):
  A. Same-page heading -> fact.
  B. Adjacent-page continuation -> fact (heading persists across a
     CONTIGUOUS page break with no repeated heading).
  C. A new heading on a later page resets the active section.
  D. A non-contiguous (targeted supplemental) page jump resets the section —
     never leaks a distant heading onto an unrelated page.
  E. An explicit LOCAL "Group" sentence scope overrides an inherited segment
     heading (local sentence scope always wins).
  F. An explicit LOCAL different-segment sentence overrides a prior,
     inherited segment heading.
  G. A page with no heading-like line at all leaves the fact unscoped
     (fail-closed — never guessed).
  H. A full-width heading followed by two-column body text still resolves
     (heading detection is not restricted to any one column side).

Also (brief section 8): a CFR-shaped multi-section fixture (Group headline +
two named segments, the second segment's own figure stated on a LATER page
with no local heading) proving no cross-section scope leakage end-to-end
through ``validate_extracted_facts``.
"""

from __future__ import annotations

import io

from app.core.config import Settings
from app.services.sources.extracted_fact_validator import (
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import extract_pdf
from tests.helpers.pdf_fixtures import _assemble, make_pdf_with_sized_lines

_BODY = 10.0
_HEADING = 16.0


def _issuer() -> IssuerContext:
    return IssuerContext(company_name="Acme Group", source_url="https://example.com/ir")


def _facts(raw: bytes) -> list:
    extraction = extract_pdf(raw, cfg=Settings())
    assert extraction.status == "extracted"
    return validate_extracted_facts(extraction, issuer_context=_issuer())


def _scoped(facts: list, label: str, value: float) -> str | None:
    for f in facts:
        if f.label == label and f.value_numeric == value:
            return f.scope
    raise AssertionError(f"no {label}={value} fact found among {[(f.label, f.value_numeric, f.scope, f.validation_status) for f in facts]}")


def _filler_lines(n: int, *, start: int = 0) -> list[tuple[str, float, bool]]:
    """``n`` distinct, ordinary body-size prose sentences — long enough in
    aggregate to push a page's total text past ``_blocks``'s 2000-char
    single-block threshold, so a heading and a fact sentence stated later on
    the SAME page land in DIFFERENT ``_blocks()`` chunks — exactly the real,
    live-observed shape (a long, continuous, blank-line-free PDF page) that
    separated the Specialist Watchmakers heading from its own operating
    -result sentence in the real Richemont annual report."""
    return [
        (
            f"Additional commentary item number {start + i} discusses various operational "
            "matters and ordinary business developments for the year under review in detail.",
            _BODY,
            False,
        )
        for i in range(n)
    ]


def test_a_same_page_heading_resolves_scope():
    raw = make_pdf_with_sized_lines(
        [
            [
                ("RESULTS BY SEGMENT", _HEADING + 4, True),
                ("SPECIALIST WATCHMAKERS", _HEADING, True),
                ("Sales at the Specialist Watchmakers increased steadily this year under review.", _BODY, False),
                *_filler_lines(20),
                ("The operating result reached EUR 107 million for the year under review.", _BODY, False),
            ],
        ]
    )
    facts = _facts(raw)
    assert _scoped(facts, "operating_profit", 107.0) == "SPECIALIST WATCHMAKERS"


def test_b_adjacent_page_continuation_inherits_scope():
    raw = make_pdf_with_sized_lines(
        [
            [
                ("RESULTS BY SEGMENT", _HEADING + 4, True),
                ("SPECIALIST WATCHMAKERS", _HEADING, True),
                ("Intro text establishing the section for the year under review is presented here.", _BODY, False),
            ],
            [
                ("The operating result reached EUR 107 million for the year under review.", _BODY, False),
            ],
        ]
    )
    facts = _facts(raw)
    assert _scoped(facts, "operating_profit", 107.0) == "SPECIALIST WATCHMAKERS"


def test_c_new_heading_resets_active_section():
    raw = make_pdf_with_sized_lines(
        [
            [
                ("SEGMENT ONE", _HEADING, True),
                ("Segment one intro text for the year under review is presented right here.", _BODY, False),
            ],
            [
                ("SEGMENT TWO", _HEADING, True),
                ("Segment two intro text for the year under review is presented right here.", _BODY, False),
            ],
            [
                ("The operating result reached EUR 88 million for the year under review.", _BODY, False),
            ],
        ]
    )
    facts = _facts(raw)
    assert _scoped(facts, "operating_profit", 88.0) == "SEGMENT TWO"


def test_d_non_contiguous_jump_does_not_leak_stale_section():
    """A bounded, TARGETED supplemental jump (mirroring
    ``_select_statement_pages``) must never inherit a distant SEGMENT ONE
    heading left over from the leading window — the target page's own local
    content carries no compatible heading, so the fact stays unscoped."""
    pages = [
        [
            ("SEGMENT ONE", _HEADING, True),
            ("Segment one intro text for the year under review is presented right here.", _BODY, False),
        ]
    ] + [
        [("Filler page content with no scope-establishing heading at all here.", _BODY, False)]
        for _ in range(19)
    ]
    pages.append(
        [("The operating result reached EUR 61 million for the year under review.", _BODY, False)]
    )
    raw = make_pdf_with_sized_lines(pages)

    from pypdf import PdfReader, PdfWriter

    reader = PdfReader(io.BytesIO(raw))
    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)
    writer.add_outline_item("Segment Note", 20)  # 0-based -> page 21 (1-based)
    out = io.BytesIO()
    writer.write(out)
    bookmarked = out.getvalue()

    cfg = Settings(primary_document_max_pdf_pages=3, primary_document_max_supplemental_pdf_pages=5)
    extraction = extract_pdf(bookmarked, cfg=cfg)
    assert extraction.status == "extracted"
    target_excerpts = [e for e in extraction.excerpts if e.page_number == 21]
    assert target_excerpts, "expected the targeted supplemental page 21 to be reached"
    assert target_excerpts[0].ancestor_heading is None
    facts = validate_extracted_facts(extraction, issuer_context=_issuer())
    assert _scoped(facts, "operating_profit", 61.0) is None


def test_e_explicit_local_group_scope_overrides_inherited_segment():
    raw = make_pdf_with_sized_lines(
        [
            [
                ("SEGMENT ONE", _HEADING, True),
                ("Segment one intro text for the year under review is presented right here.", _BODY, False),
            ],
            [
                ("The Group reported an operating profit of EUR 200 million for the year under review.", _BODY, False),
            ],
        ]
    )
    facts = _facts(raw)
    assert _scoped(facts, "operating_profit", 200.0) == "group"


def test_f_explicit_local_different_segment_overrides_inherited_segment():
    raw = make_pdf_with_sized_lines(
        [
            [
                ("SEGMENT ONE", _HEADING, True),
                ("Segment one intro text for the year under review is presented right here.", _BODY, False),
            ],
            [
                (
                    "The Group's Segment Two reported an operating profit of EUR 300 million for the year.",
                    _BODY,
                    False,
                ),
            ],
        ]
    )
    facts = _facts(raw)
    assert _scoped(facts, "operating_profit", 300.0) == "Segment Two"


def test_g_no_heading_at_all_leaves_fact_unscoped():
    raw = make_pdf_with_sized_lines(
        [
            [
                ("The operating result reached EUR 44 million for the year under review.", _BODY, False),
            ],
        ]
    )
    facts = _facts(raw)
    assert _scoped(facts, "operating_profit", 44.0) is None


def test_h_full_width_heading_then_two_column_body_still_resolves():
    """A full-width bold heading above a genuine two-column body (an
    independently-positioned left/right pair per row, defeating naive
    top-to-bottom reading order) — heading detection is not restricted to
    any one column side, so the segment scope still resolves for a fact
    that ends up in either column after reconstruction."""

    def _esc(s: str) -> str:
        return s.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")

    left = [
        "Sales grew steadily this year.",
        "Every region posted gains here.",
        "Operating result reached EUR 107 million.",
    ]
    right = [
        "Costs stayed well controlled here.",
        "Currency moves were adverse though.",
        "Outlook remains confident overall.",
    ]
    ops = [
        "BT",
        f"/F2 {_HEADING + 4} Tf",
        "1 0 0 1 40 776 Tm (RESULTS BY SEGMENT OVERVIEW FOR THE FULL YEAR UNDER REVIEW) Tj",
        f"/F2 {_HEADING} Tf",
        "1 0 0 1 40 756 Tm (SPECIALIST WATCHMAKERS DIVISION PERFORMANCE SUMMARY REPORT) Tj",
    ]
    ops.append(f"/F1 {_BODY} Tf")
    for i, ln in enumerate(left):
        ops.append(f"1 0 0 1 50 {700 - i * 16} Tm ({_esc(ln)}) Tj")
    for i, ln in enumerate(right):
        ops.append(f"1 0 0 1 360 {700 - i * 16} Tm ({_esc(ln)}) Tj")
    ops.append("ET")
    cb = "\n".join(ops).encode()
    objs = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        (
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] "
            b"/Contents 4 0 R /Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> >>"
        ),
        b"<< /Length %d >>\nstream\n" % len(cb) + cb + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold >>",
    ]
    raw = _assemble(objs)
    facts = _facts(raw)
    assert _scoped(facts, "operating_profit", 107.0) == "SPECIALIST WATCHMAKERS DIVISION PERFORMANCE SUMMARY REPORT"


def test_cfr_shaped_no_cross_section_leakage():
    """CFR-equivalent regression (brief section 8): Group headline facts,
    Jewellery-Maisons-shaped facts, and a Specialist-Watchmakers-shaped
    later-page result (no local heading repeated) all resolve to their OWN
    correct, distinct scopes with no cross-section leakage."""
    raw = make_pdf_with_sized_lines(
        [
            [
                ("GROUP FINANCIAL HIGHLIGHTS", _HEADING, True),
                ("Group sales for the year under review reached EUR 900 million overall.", _BODY, False),
                ("Group operating profit for the year under review reached EUR 210 million.", _BODY, False),
            ],
            [
                ("RESULTS BY SEGMENT", _HEADING + 4, True),
                ("JEWELLERY MAISONS", _HEADING, True),
                ("Sales at the Jewellery Maisons remained resilient during the year under review.", _BODY, False),
            ],
            [
                ("The operating margin stood at 30.5% for the year under review overall.", _BODY, False),
            ],
            [
                ("SPECIALIST WATCHMAKERS", _HEADING, True),
                ("Sales at the Specialist Watchmakers were broadly stable during the year under review.", _BODY, False),
            ],
            [
                ("The operating result reached EUR 107 million for the year under review.", _BODY, False),
            ],
        ]
    )
    facts = _facts(raw)
    assert _scoped(facts, "revenue", 900.0) == "group"
    assert _scoped(facts, "operating_profit", 210.0) == "group"
    assert _scoped(facts, "operating_margin", 30.5) == "JEWELLERY MAISONS"
    assert _scoped(facts, "operating_profit", 107.0) == "SPECIALIST WATCHMAKERS"
