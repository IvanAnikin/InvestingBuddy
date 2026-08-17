"""
Phase 32A corrective — layout-aware two-column PDF reading order + safe debt
parsing + HTML-over-PDF method preference.

Fully OFFLINE and deterministic: real (but tiny, in-code) PDF bytes via
``tests/helpers/pdf_fixtures.py``; no network, no LLM, no DB.

Root cause (proved live on staging, Phase 32A corrective — see memory):
pdfplumber's ``page.extract_text()`` has no column-aware reading order, so a
genuine two-column PDF page gets its columns interleaved line-by-line. A real
CFR staging run showed this splice a debt-vocabulary fragment from one column
directly next to a value that actually belonged to a DIFFERENT metric in the
other column (operating cash flow persisted as ``total_debt``).

Covers:
  A. Pure column-detection primitives (synthetic word lists — no PDF bytes):
     single-column not detected; clean two-column detected; too-narrow gutter
     rejected; too few lines each side rejected; pathological word count
     skips reconstruction (bounded).
  B. Real-PDF integration via ``extract_pdf``: single-column page byte-for-
     byte unaffected; clean two-column reading order recovered; full-width
     heading + two columns below ordered heading-then-column-then-column;
     table extraction on a table+prose page is unaffected by the text-layout
     change.
  C. End-to-end CFR-shaped regression: operating cash flow and total debt
     resolve to their OWN correct values from a two-column page — neither
     value contaminates the other's field.
  D. Safe debt parsing: a debt label and a value separated by a sentence
     boundary (full stop) must not be associated (defense-in-depth,
     independent of the layout fix).
  E. HTML-over-PDF method preference in cross-method resolution.
"""

from __future__ import annotations

from app.services.sources.document_text_extractor import (
    DocumentExcerpt,
    DocumentTextExtraction,
)
from app.services.sources.extracted_fact_validator import (
    VALIDATION_REJECTED,
    VALIDATION_VALIDATED,
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import (
    METHOD_HTML,
    METHOD_NATIVE_PDF,
    STATUS_EXTRACTED,
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
    _detect_two_column_gutter,
    _group_words_into_lines,
    _reconstruct_two_column_text,
    extract_pdf,
)
from app.services.sources.primary_fact_parser import (
    FIELD_NET_DEBT,
    FIELD_OPERATING_CASH_FLOW,
    FIELD_REVENUE,
    FIELD_TOTAL_DEBT,
    parse_primary_facts,
)
from tests.helpers.pdf_fixtures import (
    make_pdf,
    make_pdf_bands,
    make_pdf_two_column,
    make_pdf_with_table,
)

ISSUER = IssuerContext(
    company_name="Compagnie Financiere Richemont SA",
    ticker="CFR",
    reporting_currency="EUR",
    default_period="2024",
)


def _word(text: str, x0: float, x1: float, top: float) -> dict:
    return {"text": text, "x0": x0, "x1": x1, "top": top, "bottom": top + 10}


# --------------------------------------------------------------------------- #
# A. Pure column-detection primitives                                        #
# --------------------------------------------------------------------------- #


def _single_column_words(n_lines: int = 8, *, page_width: float = 612.0) -> list[dict]:
    words: list[dict] = []
    for i in range(n_lines):
        top = 80.0 + i * 16.0
        words.append(_word("Some", 72.0, 110.0, top))
        words.append(_word("normal", 115.0, 170.0, top))
        words.append(_word("single-column", 175.0, 300.0, top))
        words.append(_word("body", 305.0, 350.0, top))
        words.append(_word("text.", 355.0, 400.0, top))
    return words


def _two_column_words(n_lines: int = 6, *, page_width: float = 612.0) -> list[dict]:
    words: list[dict] = []
    for i in range(n_lines):
        top = 80.0 + i * 16.0
        words.append(_word("Left", 60.0, 100.0, top))
        words.append(_word("column", 105.0, 180.0, top))
        words.append(_word("text", 185.0, 220.0, top))
        words.append(_word("Right", 340.0, 380.0, top))
        words.append(_word("column", 385.0, 460.0, top))
        words.append(_word("text", 465.0, 500.0, top))
    return words


def test_single_column_words_do_not_detect_a_gutter():
    words = _single_column_words()
    lines = _group_words_into_lines(words)
    assert _detect_two_column_gutter(lines, 612.0) is None
    assert _reconstruct_two_column_text(words, 612.0) is None


def test_clean_two_column_words_detect_a_gutter():
    words = _two_column_words()
    lines = _group_words_into_lines(words)
    gutter = _detect_two_column_gutter(lines, 612.0)
    assert gutter is not None
    gutter_start, gutter_end = gutter
    assert gutter_start < gutter_end
    assert gutter_end - gutter_start >= 14.0


def test_reconstruction_reads_left_column_fully_before_right_column():
    words = _two_column_words(n_lines=4)
    text = _reconstruct_two_column_text(words, 612.0)
    assert text is not None
    left_pos = text.index("Left column text")
    right_pos = text.index("Right column text")
    assert left_pos < right_pos
    # All 4 left-column lines precede the first right-column line.
    assert text.count("Left column text") == 4
    assert text.index("Right column text") > text.rindex("Left column text")


def test_too_narrow_gap_is_not_treated_as_a_gutter():
    # Two blocks separated by only a normal word-spacing gap (< 14pt) must
    # not be mistaken for a column gutter.
    words: list[dict] = []
    for i in range(6):
        top = 80.0 + i * 16.0
        words.append(_word("alpha", 60.0, 200.0, top))
        words.append(_word("beta", 205.0, 340.0, top))  # 5pt gap only
    lines = _group_words_into_lines(words)
    assert _detect_two_column_gutter(lines, 612.0) is None


def test_too_few_lines_on_one_side_is_not_confidently_two_column():
    words = _two_column_words(n_lines=2)  # below _LAYOUT_MIN_COLUMN_LINES(=3)
    lines = _group_words_into_lines(words)
    assert _detect_two_column_gutter(lines, 612.0) is None


def test_pathological_word_count_skips_reconstruction():
    # Object-abuse / CPU-DoS guard: far more words than the bounded cap.
    words = _two_column_words(n_lines=2000)
    assert len(words) > 6000
    assert _reconstruct_two_column_text(words, 612.0) is None


def test_header_plus_two_columns_orders_header_first():
    words = _two_column_words(n_lines=4)
    heading = _word(
        "Consolidated Financial Highlights For The Full Reporting Year Overview",
        40.0,
        560.0,
        40.0,
    )
    text = _reconstruct_two_column_text([heading, *words], 612.0)
    assert text is not None
    lines = text.splitlines()
    assert "Consolidated Financial Highlights" in lines[0]
    assert lines[0].index("Consolidated") < text.index("Left column text")
    assert text.index("Left column text") < text.index("Right column text")


def test_mid_page_separator_stays_between_the_two_column_bands_it_separates():
    """A full-width heading HALFWAY down the page must remain BETWEEN the
    column content above it and the column content below it — never moved to
    the start of the page (Phase 32A corrective, pre-merge correction)."""
    words: list[dict] = []
    for i in range(3):
        top = 80.0 + i * 16.0
        words.append(_word("LeftA", 60.0, 100.0, top))
        words.append(_word("RightA", 340.0, 380.0, top))
    sep_top = 80.0 + 3 * 16.0
    words.append(_word("SECTION", 40.0, 300.0, sep_top))
    words.append(_word("TWO", 305.0, 560.0, sep_top))
    for i in range(3):
        top = sep_top + 16.0 + i * 16.0
        words.append(_word("LeftB", 60.0, 100.0, top))
        words.append(_word("RightB", 340.0, 380.0, top))

    text = _reconstruct_two_column_text(words, 612.0)
    assert text is not None
    sep_pos = text.index("SECTION TWO")
    # Everything from band 1 (both columns) precedes the separator...
    assert text.rindex("LeftA") < sep_pos
    assert text.rindex("RightA") < sep_pos
    # ...and everything from band 2 (both columns) follows it — the
    # separator is NOT hoisted to the very start of the page.
    assert text.index("LeftB") > sep_pos
    assert text.index("RightB") > sep_pos
    # Band 1 itself keeps its own left-then-right order (unaffected by the
    # separator immediately following it).
    assert text.index("LeftA") < text.index("RightA") < sep_pos


# --------------------------------------------------------------------------- #
# B. Real-PDF integration via extract_pdf                                    #
# --------------------------------------------------------------------------- #


def test_single_column_real_pdf_extraction_is_unaffected():
    raw = make_pdf(
        [
            "This is a normal single-column paragraph that simply wraps.\n"
            "It continues onto a second line in the same column, unaffected.\n"
            "A third line follows here, still single-column body text.\n"
            "Final short line."
        ]
    )
    result = extract_pdf(raw)
    assert result.status == STATUS_EXTRACTED
    assert not any("two-column" in w for w in result.warnings)
    assert "normal single-column paragraph" in result.excerpts[0].text


def test_two_column_real_pdf_recovers_column_reading_order():
    left = [
        "Cash flow generated from operating activities",
        "amounted to EUR 4,880 million during the year",
        "under review, reflecting strong trading across",
        "all of our business segments during the period",
        "under review across the full financial year now.",
    ]
    right = [
        "Total borrowings stood at EUR 1,250 million at",
        "the end of the period, reflecting a decrease of",
        "three percent compared to the prior year figure",
        "as reported previously in last years annual",
        "report published for shareholders and investors.",
    ]
    raw = make_pdf_two_column(
        left, right, heading="Consolidated Cash Flow and Financing Overview"
    )
    result = extract_pdf(raw)
    assert result.status == STATUS_EXTRACTED
    assert any("two-column" in w for w in result.warnings)
    text = result.excerpts[0].text
    # The full left-column sentence must appear intact (not interleaved with
    # right-column fragments), and must precede the right column entirely.
    assert "Cash flow generated from operating activities" in text
    left_end = text.index("under review across the full financial year now.")
    right_start = text.index("Total borrowings stood at EUR 1,250 million")
    assert left_end < right_start


def test_full_width_heading_then_two_columns_real_pdf():
    left = ["Left column line one text here.", "Left column line two continues.", "Left column line three follows."]
    right = ["Right column line one is here.", "Right column line two continues.", "Right column line three follows."]
    raw = make_pdf_two_column(
        left,
        right,
        heading="Consolidated Group Results Overview For The Full Reporting Year",
    )
    result = extract_pdf(raw)
    text = result.excerpts[0].text
    assert text.index("Consolidated Group Results Overview") < text.index("Left column line one")
    assert text.index("Left column line three follows") < text.index("Right column line one")


def test_mid_page_separator_real_pdf_keeps_heading_between_bands():
    band1 = (
        "columns",
        ["Left A line one is here today.", "Left A line two continues below.", "Left A line three ends this band."],
        ["Right A line one is here today.", "Right A line two continues below.", "Right A line three ends this band."],
    )
    band2 = (
        "columns",
        ["Left B line one is here today.", "Left B line two continues below.", "Left B line three ends this band."],
        ["Right B line one is here today.", "Right B line two continues below.", "Right B line three ends this band."],
    )
    raw = make_pdf_bands(
        [
            band1,
            ("full", "Section Two Overview Of Additional Financial Disclosures Below"),
            band2,
        ]
    )
    result = extract_pdf(raw)
    text = result.excerpts[0].text
    sep_pos = text.index("Section Two Overview")
    assert text.index("Left A line one is here today.") < sep_pos
    assert text.index("Right A line three ends this band.") < sep_pos
    assert text.index("Left B line one is here today.") > sep_pos
    assert text.index("Right B line three ends this band.") > sep_pos
    # Band A keeps its own left-then-right order, entirely before the separator.
    assert text.index("Left A line three ends this band.") < text.index("Right A line one is here today.")
    # Band B keeps its own left-then-right order, entirely after the separator.
    assert text.index("Left B line three ends this band.") < text.index("Right B line one is here today.")


def test_full_width_footer_below_two_columns_real_pdf():
    band = (
        "columns",
        ["Left line one is here today.", "Left line two continues below.", "Left line three ends this band."],
        ["Right line one is here today.", "Right line two continues below.", "Right line three ends this band."],
    )
    raw = make_pdf_bands(
        [band, ("full", "This document is confidential and published for shareholders only")]
    )
    result = extract_pdf(raw)
    text = result.excerpts[0].text
    footer_pos = text.index("This document is confidential")
    assert text.index("Left line one is here today.") < footer_pos
    assert text.index("Right line three ends this band.") < footer_pos


def test_multiple_full_width_separators_real_pdf():
    raw = make_pdf_bands(
        [
            ("full", "Consolidated Group Results Overview For The Full Reporting Year"),
            (
                "columns",
                ["Left A one is here today.", "Left A two continues below.", "Left A three ends band now."],
                ["Right A one is here today.", "Right A two continues below.", "Right A three ends band now."],
            ),
            ("full", "Section Two Segment Breakdown Additional Disclosures Below"),
            (
                "columns",
                ["Left B one is here today.", "Left B two continues below.", "Left B three ends band now."],
                ["Right B one is here today.", "Right B two continues below.", "Right B three ends band now."],
            ),
            ("full", "This document is confidential footer note published for shareholders"),
        ]
    )
    result = extract_pdf(raw)
    text = result.excerpts[0].text
    title_pos = text.index("Consolidated Group Results Overview")
    sep_pos = text.index("Section Two Segment Breakdown")
    footer_pos = text.index("This document is confidential footer")
    left_a_pos = text.index("Left A one is here today.")
    right_a_pos = text.index("Right A three ends band now.")
    left_b_pos = text.index("Left B one is here today.")
    right_b_pos = text.index("Right B three ends band now.")
    # Strict document order: title, band A (left then right), separator,
    # band B (left then right), footer.
    assert title_pos < left_a_pos < right_a_pos < sep_pos < left_b_pos < right_b_pos < footer_pos


def test_table_plus_two_column_prose_table_unaffected():
    # A table on the same document must be extracted exactly as before —
    # this fix only ever changes ``page.extract_text()`` prose ordering.
    raw = make_pdf_with_table([["", "2024"], ["Revenue", "20,616"]])
    result = extract_pdf(raw)
    assert len(result.tables) == 1
    assert result.tables[0].rows[-1] == ["Revenue", "20,616"]


# --------------------------------------------------------------------------- #
# C. End-to-end CFR-shaped regression: OCF and debt never contaminate        #
# --------------------------------------------------------------------------- #


def test_ocf_and_debt_resolve_independently_from_two_column_page():
    left = [
        "Cash flow generated from operating activities",
        "amounted to EUR 4,880 million during the year",
        "under review, reflecting strong trading across",
        "all of our business segments during the period",
        "under review across the full financial year now.",
    ]
    right = [
        "Total borrowings stood at EUR 1,250 million at",
        "the end of the period, reflecting a decrease of",
        "three percent compared to the prior year figure",
        "as reported previously in last years annual",
        "report published for shareholders and investors.",
    ]
    raw = make_pdf_two_column(
        left, right, heading="Consolidated Cash Flow and Financing Overview"
    )
    extraction = extract_pdf(raw)
    facts = parse_primary_facts(extraction.to_text_extraction())
    by_field = {f.field: f for f in facts}

    assert FIELD_OPERATING_CASH_FLOW in by_field
    assert by_field[FIELD_OPERATING_CASH_FLOW].numeric_value == 4880.0

    assert FIELD_TOTAL_DEBT in by_field
    assert by_field[FIELD_TOTAL_DEBT].numeric_value == 1250.0
    # Never the OCF value under the debt field — the exact live-observed bug.
    assert by_field[FIELD_TOTAL_DEBT].numeric_value != 4880.0


def test_ocf_and_debt_across_a_mid_page_section_separator_stay_isolated():
    """Same regression as above, but the OCF sentence sits in band 1 and the
    debt sentence sits in band 2, split by a full-width section heading.

    Proves requirement #9 (pre-merge correction): content association is not
    corrupted by the vertical-band fix — band 1's own left/right content
    cannot bleed into band 2's, and the section heading does not get spliced
    into either sentence.
    """
    band1 = (
        "columns",
        [
            "Cash flow generated from operating activities",
            "amounted to EUR 4,880 million for the group",
            "as a whole during the full financial year now.",
        ],
        [
            "Group trading remained resilient across all of",
            "our reporting segments during the year under",
            "review, reflecting broad-based demand growth.",
        ],
    )
    band2 = (
        "columns",
        [
            "Total borrowings stood at EUR 1,250 million at",
            "the end of the period under review for the",
            "group as reported in the notes to the accounts.",
        ],
        [
            "This reflects a decrease of three percent when",
            "compared with the prior year figure previously",
            "reported for shareholders and other investors.",
        ],
    )
    raw = make_pdf_bands(
        [
            band1,
            ("full", "Section Two Financing And Liquidity Overview Below"),
            band2,
        ]
    )
    extraction = extract_pdf(raw)
    text = extraction.excerpts[0].text
    sep_pos = text.index("Section Two Financing")
    assert text.index("Cash flow generated from operating activities") < sep_pos
    assert text.index("Total borrowings stood at EUR 1,250 million") > sep_pos

    facts = parse_primary_facts(extraction.to_text_extraction())
    by_field = {f.field: f for f in facts}
    assert by_field[FIELD_OPERATING_CASH_FLOW].numeric_value == 4880.0
    assert by_field[FIELD_TOTAL_DEBT].numeric_value == 1250.0
    assert by_field[FIELD_TOTAL_DEBT].numeric_value != 4880.0


# --------------------------------------------------------------------------- #
# D. Safe debt parsing (defense-in-depth, independent of layout)             #
# --------------------------------------------------------------------------- #


def _extraction_with_text(text: str) -> DocumentTextExtraction:
    return DocumentTextExtraction(
        excerpts=[
            DocumentExcerpt(excerpt_id="X1", text=text, char_count=len(text))
        ]
    )


def test_debt_label_across_a_sentence_boundary_is_not_captured():
    text = (
        "Total borrowings were actively managed throughout the year. "
        "The Group's cash position remained strong at EUR 500 million."
    )
    facts = parse_primary_facts(_extraction_with_text(text))
    fields = {f.field for f in facts}
    assert FIELD_TOTAL_DEBT not in fields
    assert FIELD_NET_DEBT not in fields


def test_ocf_row_with_borrowings_mentioned_elsewhere_yields_ocf_only():
    text = (
        "Cash flow generated from operating activities amounted to "
        "EUR 4,880 million for the year. Separately, borrowings are "
        "discussed elsewhere in this report without a value stated here."
    )
    facts = parse_primary_facts(_extraction_with_text(text))
    by_field = {f.field: f for f in facts}
    assert by_field[FIELD_OPERATING_CASH_FLOW].numeric_value == 4880.0
    assert FIELD_TOTAL_DEBT not in by_field


def test_same_sentence_debt_label_and_value_still_parses():
    text = "Total borrowings amounted to EUR 1,250 million at the year end."
    facts = parse_primary_facts(_extraction_with_text(text))
    by_field = {f.field: f for f in facts}
    assert by_field[FIELD_TOTAL_DEBT].numeric_value == 1250.0


# --------------------------------------------------------------------------- #
# E. HTML-over-PDF method preference in cross-method resolution              #
# --------------------------------------------------------------------------- #


def _table(rows, *, method=METHOD_NATIVE_PDF, page=12, index=1, confidence=0.7):
    return ExtractedTable(
        table_location=f"p{page}:t{index}",
        table_index=index,
        page_number=page,
        rows=rows,
        row_count=len(rows),
        col_count=max((len(r) for r in rows), default=0),
        extraction_method=method,
        confidence=confidence,
    )


def _extraction(tables, *, page=12, method=METHOD_NATIVE_PDF):
    cue = "All figures are stated in millions of euros (EUR)."
    excerpt = PrimaryDocumentExcerpt(
        excerpt_id="X1",
        text=cue,
        page_number=page,
        extraction_method=method,
        confidence=0.6,
        char_count=len(cue),
    )
    return PrimaryDocumentExtraction(
        content_hash="0" * 64,
        mime_type="application/pdf",
        extraction_method=method,
        status=STATUS_EXTRACTED,
        page_count=20,
        excerpts=[excerpt],
        tables=list(tables),
    )


def test_html_is_preferred_over_native_pdf_when_values_agree():
    html_table = _table(
        [["", "2024"], ["Revenue", "22,420"]], method=METHOD_HTML, index=1
    )
    pdf_table = _table(
        [["", "2024"], ["Revenue", "22,420"]], method=METHOD_NATIVE_PDF, index=2
    )
    extraction = _extraction([html_table, pdf_table])
    facts = validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=None)
    rev = next(f for f in facts if f.label == FIELD_REVENUE and f.period == "2024")
    assert rev.validation_status == VALIDATION_VALIDATED
    assert set(rev.methods) == {METHOD_HTML, METHOD_NATIVE_PDF}
    assert rev.extraction_method == METHOD_HTML


def test_html_pdf_conflict_is_an_explicit_rejection_not_a_silent_choice():
    html_table = _table(
        [["", "2024"], ["Revenue", "22,420"]], method=METHOD_HTML, index=1
    )
    pdf_table = _table(
        [["", "2024"], ["Revenue", "3,100"]], method=METHOD_NATIVE_PDF, index=2
    )
    extraction = _extraction([html_table, pdf_table])
    facts = validate_extracted_facts(extraction, issuer_context=ISSUER, cfg=None)
    rev = next(f for f in facts if f.label == FIELD_REVENUE and f.period == "2024")
    assert rev.validation_status == VALIDATION_REJECTED
    assert rev.value_numeric is None
