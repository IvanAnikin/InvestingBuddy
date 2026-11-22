"""V3.21 live finding: the classifier reads the 10-K's business section, not its header.

Staging, 2026-11-22: Powell Industries' held 10-K (accession 0000080420-26-000143) had no
section paths. Its first nine prose chunks were the hidden inline-XBRL header, followed
by the cover page and the forward-looking-statements notice. The classifier read the
first twelve chunks, found no product, and called a switchgear maker "not_electricity".
On the same rows, the business-section reader below selects chunks 14-33 and the layer
classifier returns ``distribution_grid_edge``.
"""

from __future__ import annotations

from app.services.electricity.classifier import CitedText, classify
from app.services.pipeline.electricity_step import (
    MAX_BUSINESS_CHUNKS,
    business_section_rows,
)

XBRL = ("0000080420 us-gaap:CommonStockMember 2025-10-01 2026-09-30 0000080420 "
        "us-gaap:AdditionalPaidInCapitalMember us-gaap:RetainedEarningsMember "
        "powl:PetrochemicalMember country:US iso4217:USD xbrli:shares")
COVER = ("For the fiscal year ended September 30, 2026 (Exact name of registrant as "
         "specified in its charter) Delaware")
CHECK = "Indicate by check mark whether the registrant has submitted electronically every file."
MARKET = "The aggregate market value of the common stock held by non-affiliates was $4.5bn."
FLS = ("This Annual Report on Form 10-K contains “forward-looking statements” "
       "within the meaning of the securities laws.")
BUSINESS = [
    "We develop, design, manufacture and service custom-engineered equipment and systems "
    "that distribute, control and monitor the flow of electrical energy.",
    "Our principal products include integrated power control room substations, "
    "electrical houses, medium-voltage switchgear, low-voltage switchgear and circuit "
    "breakers for utilities and industrial customers.",
    "In the electric utility market, we serve power generation and distribution "
    "customers and their substations.",
]


def _rows(texts: list[str], paths: list[str] | None = None) -> list[tuple]:
    paths = paths or [""] * len(texts)
    return [(t, p, i) for i, (t, p) in enumerate(zip(texts, paths, strict=True))]


def _classify(rows: list[tuple]) -> dict:
    texts = [CitedText(text=r[0], source_kind="annual_report_business_section",
                       source_url="https://www.sec.gov/powl-10k.htm", as_of="2026-11-17",
                       label="10-K") for r in rows]
    return classify(texts, [])


def test_the_powell_shape_skips_the_xbrl_header_and_cover() -> None:
    texts = [XBRL] * 9 + [COVER, CHECK, MARKET, FLS, *BUSINESS]
    chosen = business_section_rows(_rows(texts))
    assert [r[2] for r in chosen] == [13, 14, 15]
    result = _classify(chosen)
    assert result["status"] == "classified"
    assert result["primary_layer"]


def test_the_first_twelve_chunks_alone_name_no_product() -> None:
    texts = [XBRL] * 9 + [COVER, CHECK, MARKET]
    assert business_section_rows(_rows(texts)) == []
    assert _classify(_rows(texts))["status"] != "classified"


def test_the_item_1_heading_starts_the_section_and_the_toc_is_skipped() -> None:
    toc = "Table of contents Item 1. Business 4 Item 1A. Risk Factors 12 Item 2. Properties 30"
    intro = "General information about the registrant's securities and its officers."
    heading = "Item 1. Business " + BUSINESS[0]
    texts = [toc, intro, heading, BUSINESS[1], BUSINESS[2]]
    chosen = business_section_rows(_rows(texts))
    assert [r[2] for r in chosen] == [2, 3, 4]


def test_a_business_section_path_still_wins() -> None:
    texts = ["Risk text about cybersecurity.", *BUSINESS]
    paths = ["Item 1A Risk Factors", "Item 1 Business", "Item 1 Business", "Other"]
    chosen = business_section_rows(_rows(texts, paths))
    assert [r[2] for r in chosen] == [1, 2]


def test_the_section_is_bounded() -> None:
    texts = [BUSINESS[0]] * (MAX_BUSINESS_CHUNKS + 15)
    assert len(business_section_rows(_rows(texts))) == MAX_BUSINESS_CHUNKS


def test_empty_chunks_are_ignored() -> None:
    assert business_section_rows(_rows(["", "", BUSINESS[0]])) == [(BUSINESS[0], "", 2)]
