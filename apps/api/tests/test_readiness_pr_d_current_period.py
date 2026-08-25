"""
Private-use production readiness, PR-D — CURRENT-PERIOD (INTERIM) EVIDENCE.

Three defects confirmed against the code at ``99df1b9``:

D7a **Interim documents could never be selected.** ``rank_documents`` orders
     strictly by kind (annual 0 < results 1 < interim 2) and
     ``primary_document_max_docs_per_issuer`` is 3. On an issuer page listing
     many annual reports — Richemont's results page links roughly thirty, back
     to 1993 — no interim document could ever be reached. Among the annuals
     there was no recency preference at all: DOM order decided which one was
     ingested.

D7b **Interim figures were stamped as full years.** ``_period_near`` returned a
     BARE YEAR for every prose fact, so a real Hermès sentence — "consolidated
     revenue in the first half of 2026 amounted to EUR8.2 billion" — produced
     ``period="2026"``. The same held for a table headed "First-half 2026".
     That is the ``INTERIM_AS_ANNUAL`` contradiction, and the two existing LVMH
     fixtures (both explicitly H1 releases) asserted the wrong periods.

D7c **An interim figure could occupy the annual slot.** ``_high_confidence_
     facts_for`` took the FIRST high-confidence fact per field, which was safe
     only while every ingested document was an annual report.

Fully offline and deterministic: no network, no LLM, no Azure, no DB.
Issuer-shaped URLs and sentences appear only in fixtures.
"""

from __future__ import annotations

import pytest

from app.services.final_report_generator import (
    _PRIMARY_FINANCIAL_FACT_FIELDS,
    _build_financial_snapshot,
    _current_period_facts_for,
    _high_confidence_facts_for,
)
from app.services.sources.document_discovery import (
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    PERIOD_CLASS_ANNUAL,
    PERIOD_CLASS_CURRENT,
    DiscoveredDocument,
    classify_document_kind,
    document_period_class,
    document_recency_hint,
    select_period_diverse,
)
from app.services.sources.document_text_extractor import (
    DocumentExcerpt,
    DocumentTextExtraction,
)
from app.services.sources.primary_fact_parser import parse_primary_facts
from app.services.sources.verified_issuer_sources import get_verified_issuer_source

# --------------------------------------------------------------------------- #
# Real, live-verified issuer document URLs (2026-08-25). Used ONLY as
# classification/recency fixtures — no figure is asserted from them here.
# --------------------------------------------------------------------------- #

_PNDORA_ANNUAL = "https://pandora.a.bigcontent.io/v1/static/Annual%20Report%202025"
_PNDORA_Q2 = (
    "https://pandora.a.bigcontent.io/v1/static/"
    "Pandora%20Q2%202026%20Interim%20Report%20Company%20announcement_No_1015"
)
_CFR_ANNUAL = "https://www.richemont.com/media/ud3bety3/richemont-fy26-annual-report.pdf"
_CFR_Q1 = (
    "https://www.richemont.com/media/xikaciqj/"
    "ad-hoc-announcement-pursuant-to-art-53-lr-fy27-q1-sales-en.pdf"
)
_CFR_OLD_ANNUAL = "https://www.richemont.com/media/1lfi4y4d/annual-results-2010.pdf"
_RMS_H1 = (
    "https://assets-finance.hermes.com/s3fs-public/node/pdf_file/2026-07/1785260662/"
    "hermes_20260729_pr_firsthalfresults_va.pdf"
)
_RMS_URD = (
    "https://assets-finance.hermes.com/s3fs-public/node/pdf_file/2026-04/1777391712/"
    "260320_hermes_urd2025_en.pdf"
)
_KER_H1 = (
    "https://assets-keringcom.keringapps.com/02_First_Half_Report_2026_EN_d6129bcfad.pdf"
)
_KER_URD = (
    "https://assets-keringcom.keringapps.com/"
    "02_Universal_Registration_Document_2025_EN_216d1c494b.pdf"
)
_UHR_H1 = (
    "https://www.swatchgroup.com/sites/default/files/media-files/"
    "pr_halfyear_report_2026_en_0.pdf"
)
_UHR_ANNUAL = (
    "https://www.swatchgroup.com/sites/default/files/media-files/"
    "swatchgroup_annual_report_en_2025.pdf"
)


# =========================================================================== #
# D7a — classification and recency on REAL issuer URLs                        #
# =========================================================================== #


@pytest.mark.parametrize(
    "url,expected_class",
    [
        (_PNDORA_ANNUAL, PERIOD_CLASS_ANNUAL),
        (_PNDORA_Q2, PERIOD_CLASS_CURRENT),
        (_CFR_ANNUAL, PERIOD_CLASS_ANNUAL),
        (_CFR_Q1, PERIOD_CLASS_CURRENT),
        (_RMS_H1, PERIOD_CLASS_CURRENT),
        (_RMS_URD, PERIOD_CLASS_ANNUAL),
        (_KER_H1, PERIOD_CLASS_CURRENT),
        (_KER_URD, PERIOD_CLASS_ANNUAL),
        (_UHR_H1, PERIOD_CLASS_CURRENT),
        (_UHR_ANNUAL, PERIOD_CLASS_ANNUAL),
    ],
)
def test_real_issuer_documents_classify_into_the_right_quota(
    url: str, expected_class: str
) -> None:
    assert document_period_class(classify_document_kind("", url)) == expected_class


def test_concatenated_filenames_are_recognised() -> None:
    """Real issuer filenames run the words together — ``firsthalfresults``,
    ``halfyear``. The two-variant keyword scan classified both as ``other``, so
    neither could ever be selected as a current-period document."""
    assert classify_document_kind("", _RMS_H1) == DOC_KIND_INTERIM_REPORT
    assert classify_document_kind("", _UHR_H1) == DOC_KIND_INTERIM_REPORT


def test_urd_abbreviation_with_a_year_is_an_annual_report() -> None:
    assert classify_document_kind("", _RMS_URD) == DOC_KIND_ANNUAL_REPORT


def test_the_urd_token_never_fires_without_a_year() -> None:
    """A three-letter token must not match an unrelated slug."""
    assert classify_document_kind("", "https://x/urdu-language-guide.pdf") != (
        DOC_KIND_ANNUAL_REPORT
    )


def test_recency_prefers_the_newer_of_two_annual_reports() -> None:
    assert document_recency_hint("", _CFR_ANNUAL) > document_recency_hint(
        "", _CFR_OLD_ANNUAL
    )


def test_a_two_digit_fiscal_year_is_read_as_a_recency_hint() -> None:
    assert document_recency_hint("", _CFR_ANNUAL)[0] == 2026


def test_a_compact_publication_date_supplies_the_year() -> None:
    """``hermes_20260729_pr_...`` — invisible to a 4-digit year scan because
    the year is surrounded by digits."""
    assert document_recency_hint("", _RMS_H1)[0] == 2026


def test_a_quarter_orders_after_an_earlier_quarter_of_the_same_year() -> None:
    q1 = document_recency_hint("Q1 2026 Interim Report", "https://x/q1-2026.pdf")
    q2 = document_recency_hint("Q2 2026 Interim Report", "https://x/q2-2026.pdf")
    assert q2 > q1


def test_an_implausible_year_is_not_treated_as_a_period() -> None:
    assert document_recency_hint("", "https://x/doc-1234567.pdf")[0] == 0


# =========================================================================== #
# D7a — the selection quota                                                   #
# =========================================================================== #


def _doc(url: str, title: str = "") -> DiscoveredDocument:
    kind = classify_document_kind(title, url)
    return DiscoveredDocument(
        url=url, title=title, doc_kind=kind, strategy="anchors",
        is_document=True, identity=url,
    )


def test_an_interim_document_survives_a_page_full_of_annual_reports() -> None:
    """THE regression. Thirty annual reports and one current-period release;
    under a kind-only rank the interim could never be reached."""
    docs = [
        _doc(f"https://issuer.test/annual-report-{year}.pdf")
        for year in range(1995, 2026)
    ] + [_doc(_PNDORA_Q2)]
    chosen = select_period_diverse(docs, max_documents=3)
    kinds = [document_period_class(d.doc_kind) for d in chosen]
    assert PERIOD_CLASS_CURRENT in kinds
    assert PERIOD_CLASS_ANNUAL in kinds


def test_the_newest_annual_is_chosen_not_the_first_in_document_order() -> None:
    docs = [
        _doc("https://issuer.test/annual-report-1995.pdf"),
        _doc("https://issuer.test/annual-report-2025.pdf"),
        _doc("https://issuer.test/annual-report-2010.pdf"),
    ]
    assert "2025" in select_period_diverse(docs, max_documents=1)[0].url


def test_the_newest_current_period_document_wins_its_slot() -> None:
    docs = [
        _doc("https://issuer.test/annual-report-2025.pdf"),
        _doc("https://issuer.test/q1-2026-interim-report.pdf"),
        _doc("https://issuer.test/q2-2026-interim-report.pdf"),
    ]
    urls = [d.url for d in select_period_diverse(docs, max_documents=2)]
    assert any("q2-2026" in u for u in urls)
    assert not any("q1-2026" in u for u in urls)


def test_no_slot_is_wasted_when_the_issuer_publishes_no_interim_reporting() -> None:
    docs = [
        _doc(f"https://issuer.test/annual-report-{y}.pdf") for y in (2023, 2024, 2025)
    ]
    chosen = select_period_diverse(docs, max_documents=3)
    assert len(chosen) == 3
    assert all(document_period_class(d.doc_kind) == PERIOD_CLASS_ANNUAL for d in chosen)


def test_the_annual_report_is_still_selected_first() -> None:
    """It is the deepest source; the current-period reserve must not cost it."""
    docs = [_doc(_PNDORA_Q2), _doc(_PNDORA_ANNUAL)]
    assert document_period_class(
        select_period_diverse(docs, max_documents=2)[0].doc_kind
    ) == PERIOD_CLASS_ANNUAL


def test_selection_is_bounded_and_deduplicated() -> None:
    docs = [_doc(_PNDORA_ANNUAL), _doc(_PNDORA_ANNUAL), _doc(_PNDORA_Q2)]
    chosen = select_period_diverse(docs, max_documents=2)
    assert len(chosen) == 2
    assert len({d.identity for d in chosen}) == 2


def test_selection_of_an_empty_candidate_list_is_empty() -> None:
    assert select_period_diverse([], max_documents=3) == []


# =========================================================================== #
# D7b — interim periods in prose and table headers                            #
# =========================================================================== #


def _parse(text: str, *, heading: str | None = None):
    extraction = DocumentTextExtraction(
        content_hash="a" * 64,
        mime_type="text/html",
        extraction_method="html",
        status="extracted",
        page_count=1,
        excerpts=[
            DocumentExcerpt(
                excerpt_id="X1",
                text=text,
                page_number=1,
                section=heading,
                heading=heading,
                extraction_method="html",
                confidence="high",
                char_count=len(text),
            )
        ],
    )
    return {f.field: f for f in parse_primary_facts(extraction)}


def test_a_first_half_sentence_yields_an_h1_period_not_a_bare_year() -> None:
    """THE contradiction: EUR8.2bn is HALF a year of revenue."""
    facts = _parse(
        "The group consolidated revenue in the first half of 2026 amounted to "
        "EUR8.2 billion."
    )
    assert facts["revenue"].period == "H1 2026"


def test_a_quarter_sentence_yields_a_quarter_period() -> None:
    facts = _parse(
        "Group sales in the second quarter of 2026 reached EUR6,329 million."
    )
    assert facts["revenue"].period == "Q2 2026"


def test_a_full_year_sentence_still_yields_a_bare_year() -> None:
    """No interim marker ⇒ no invented one."""
    facts = _parse("The Group reported sales of EUR22,420 million in fiscal 2026.")
    assert facts["revenue"].period == "2026"


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("in the first half of 2026", "H1 2026"),
        ("for the six months ended 30 June 2026", "H1 2026"),
        ("in the second half of 2026", "H2 2026"),
        ("in H1 2026", "H1 2026"),
        ("in Q3 2026", "Q3 2026"),
        ("in the third quarter of 2026", "Q3 2026"),
        ("in 2026", "2026"),
        ("in fiscal 2026", "2026"),
    ],
)
def test_interim_marker_vocabulary(phrase: str, expected: str) -> None:
    facts = _parse(f"Group revenue was EUR1,000 million {phrase}.")
    assert facts["revenue"].period == expected


def test_a_quarter_beats_a_half_when_both_are_stated() -> None:
    """A quarter is the more specific claim."""
    facts = _parse(
        "In the second quarter of the first half of 2026, Group revenue was "
        "EUR1,000 million."
    )
    assert facts["revenue"].period == "Q2 2026"


def test_an_interim_marker_far_from_the_value_does_not_reclassify_it() -> None:
    """Same discipline as scope: only the value's OWN local window counts."""
    filler = "This paragraph discusses strategy and governance at length. " * 6
    facts = _parse(
        "The first half of the year was eventful. "
        + filler
        + "The Group reported sales of EUR22,420 million in fiscal 2026."
    )
    assert facts["revenue"].period == "2026"


# =========================================================================== #
# D7c — an interim figure never occupies the annual slot                      #
# =========================================================================== #


def _fact(field: str, value: float, period: str, **over) -> dict:
    base = {
        "field": field,
        "value": str(value),
        "numeric_value": value,
        "period": period,
        "scope": "group",
        "currency": "DKK",
        "scale": "million",
        "confidence": "high",
        "source_url": "https://issuer.test/doc.pdf",
    }
    base.update(over)
    return base


def test_the_annual_slot_takes_the_latest_annual_not_the_first_fact() -> None:
    facts = [
        _fact("revenue", 14328.0, "H1 2026"),
        _fact("revenue", 31673.0, "2024"),
        _fact("revenue", 32549.0, "2025"),
    ]
    selected = dict(_high_confidence_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert selected["revenue"]["numeric_value"] == 32549.0


def test_an_interim_fact_is_never_promoted_to_the_annual_slot() -> None:
    facts = [_fact("revenue", 14328.0, "H1 2026")]
    assert _high_confidence_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS) == []


def test_an_undated_fact_still_fills_the_annual_slot() -> None:
    """Pre-existing behaviour for undated facts is preserved."""
    facts = [_fact("revenue", 100.0, "")]
    selected = dict(_high_confidence_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert selected["revenue"]["numeric_value"] == 100.0


def test_a_dated_annual_fact_outranks_an_undated_one() -> None:
    facts = [_fact("revenue", 100.0, ""), _fact("revenue", 32549.0, "2025")]
    selected = dict(_high_confidence_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert selected["revenue"]["numeric_value"] == 32549.0


def test_the_current_period_selector_takes_the_latest_interim() -> None:
    facts = [
        _fact("revenue", 14421.0, "H1 2025"),
        _fact("revenue", 14328.0, "H1 2026"),
        _fact("revenue", 32549.0, "2025"),
    ]
    selected = dict(_current_period_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert selected["revenue"]["numeric_value"] == 14328.0


def test_h1_and_h2_are_ordered_within_the_year_not_across_types() -> None:
    facts = [_fact("revenue", 1.0, "H1 2026"), _fact("revenue", 2.0, "Q4 2026")]
    selected = dict(_current_period_facts_for(facts, _PRIMARY_FINANCIAL_FACT_FIELDS))
    assert selected["revenue"]["numeric_value"] == 2.0


def test_snapshot_carries_annual_and_interim_in_separate_labelled_slots() -> None:
    section = _build_financial_snapshot(
        {"source_tier": "T1_primary_filing", "is_mock": False},
        None,
        [
            _fact("revenue", 32549.0, "2025"),
            _fact("revenue", 14328.0, "H1 2026"),
            _fact("operating_profit", 7783.0, "2025"),
        ],
    )
    assert section["revenue_primary_filing"]["numeric_value"] == 32549.0
    assert section["revenue_primary_filing"]["period"] == "2025"
    assert section["revenue_current_period"]["numeric_value"] == 14328.0
    assert section["revenue_current_period"]["period"] == "H1 2026"
    assert section["revenue_current_period"]["period_basis"] == "interim"
    # An annual-only field gets no interim slot rather than a null one.
    assert "operating_profit_current_period" not in section


def test_the_snapshot_states_that_interim_and_annual_are_not_comparable() -> None:
    section = _build_financial_snapshot(
        {"source_tier": "T1_primary_filing", "is_mock": False},
        None,
        [_fact("revenue", 32549.0, "2025"), _fact("revenue", 14328.0, "H1 2026")],
    )
    note = section["current_period_note"]["value"]
    assert "H1 2026" in section["current_period_note"]["periods"]
    assert "not comparable" in note.lower()
    assert "annualised" in note.lower()


def test_no_annualisation_or_extrapolation_is_ever_performed() -> None:
    """An H1 revenue of 14,328 must never appear doubled."""
    section = _build_financial_snapshot(
        {"source_tier": "T1_primary_filing", "is_mock": False},
        None,
        [_fact("revenue", 14328.0, "H1 2026")],
    )
    values = [
        v.get("numeric_value")
        for k, v in section.items()
        if isinstance(v, dict) and "numeric_value" in v
    ]
    assert 28656.0 not in values
    assert values == [14328.0]


# =========================================================================== #
# Issuer registry corrections (live-verified 2026-08-25)                      #
# =========================================================================== #


def test_swatch_registry_urls_were_corrected_off_the_404_paths() -> None:
    uhr = get_verified_issuer_source("UHR", "SW")
    assert uhr is not None
    assert uhr.annual_reports_url == (
        "https://www.swatchgroup.com/en/investors-space/annual-report"
    )
    assert "/en/investors/annual-report" not in (uhr.annual_reports_url or "")


def test_hermes_registry_points_at_the_host_that_actually_serves() -> None:
    rms = get_verified_issuer_source("RMS", "PA")
    assert rms is not None
    assert rms.annual_reports_url is not None
    assert rms.annual_reports_url.startswith("https://finance.hermes.com/")


def test_kering_document_host_is_registered_as_issuer_scoped_authority() -> None:
    ker = get_verified_issuer_source("KER", "PA")
    assert ker is not None
    assert "assets-keringcom.keringapps.com" in ker.document_domains
    # The CDN is a retrieval permission only — never a general fetch domain.
    assert "assets-keringcom.keringapps.com" not in ker.allowed_domains


def test_no_issuer_warning_states_an_already_closed_gap() -> None:
    """A warning is rendered on the report's verified-source rows; stating a
    gap that has since closed is the same contradiction class this campaign
    removes."""
    for ticker, exchange in (("KER", "PA"), ("UHR", "SW")):
        issuer = get_verified_issuer_source(ticker, exchange)
        assert issuer is not None
        joined = " ".join(issuer.warnings).lower()
        assert "bot protection" not in joined
        assert "javascript-gated" not in joined
