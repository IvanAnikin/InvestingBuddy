"""
Current-period acceptance — the OFFICIAL REGULATED-VENUE path.

Moncler's acceptance row read `Annual — / Current — / 0 T1 facts`. Its own
investor site has served an HTTP 403 maintenance page on every path throughout
this campaign, so nothing could be retrieved from it. Meanwhile the same
H1 2026 Financial Results were already being retrieved, in full, from the
Italian CONSOB-authorised storage mechanism: the connector held the official
PDF URL, put it in an evidence item, and nobody ever opened it.

Opening it is not a secondary-source substitution — a storage mechanism holds
the document the ISSUER filed, unaltered, under a statutory obligation. The
distinction is preserved where it matters: transport stays
`T2_regulator_or_gov`, content stays `T1_primary_filing`, and the venue is
named on every item.

Two extraction defects the real document then exposed, both of which would have
put a WRONG number in a canonical Group slot:

S1  "STONE ISLAND REVENUES: EUR 200.3 million" is a label-colon HEADLINE, not a
    subject-plus-verb sentence, so no scope rule matched and the figure came out
    UNSCOPED — which the pipeline reads as the implicit Group convention. The
    real Group figure (EUR 1,289.9 m) was correctly refused as ambiguous (four
    revenue magnitudes in one excerpt), leaving a BRAND's revenue as the only
    candidate for the Group current-period revenue slot.

S2  "General and administrative expenses were EUR 180.4 million, with a 14.0%
    incidence on revenues, compared with EUR 170.4 million in H1 2025" yielded
    EUR 170.4 m as H1 2025 REVENUE. The parser already excluded "of " before a
    revenue label as a ratio/cost base; "on " is the same construction and was
    not excluded.

Fully offline and deterministic: no network, no LLM, no Azure, no DB. The
document sentences are real; every figure is fixture data.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from app.core.config import Settings
from app.services.sources.company_evidence import (
    VENUE_DOCUMENT_EXCERPT_TYPE,
    VENUE_DOCUMENT_FACT_TYPE,
    _ingest_regulated_storage_document,
)
from app.services.sources.connector_base import CompanyContext
from app.services.sources.connectors.company_ir import PrimaryDocumentArtifact
from app.services.sources.disclosure_documents import (
    has_current_period_document,
    select_current_period_disclosure,
)
from app.services.sources.disclosure_events import (
    EVENT_CATEGORY_GOVERNANCE,
    EVENT_CATEGORY_RESULTS,
    DisclosureEvent,
)
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
    validate_extracted_facts,
)
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    STATUS_METADATA_ONLY,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.primary_fact_parser import _infer_prose_scope
from app.services.sources.taxonomy import T1_PRIMARY_FILING, T2_REGULATOR_OR_GOV

_VENUE = "Example Storage (authorised)"
_HOSTS = ("storage.test", "www.storage.test")
_COMPANY = CompanyContext(
    company_name="Issuer SpA", ticker="ISS", exchange="MI", country="Italy"
)


def _event(
    title: str,
    *,
    url: str | None = "https://www.storage.test/files/20260722_187106.pdf",
    category: str = EVENT_CATEGORY_RESULTS,
    day: int = 22,
) -> DisclosureEvent:
    return DisclosureEvent(
        issuer_ticker="ISS",
        issuer_name="Issuer SpA",
        venue=_VENUE,
        country="Italy",
        published_at=datetime(2026, 7, day, 17, 46, tzinfo=timezone.utc),
        title=title,
        category=category,
        language="en",
        official_url=url,
        attachment_urls=(url,) if url else (),
        source_tier=T2_REGULATOR_OR_GOV,
        retrieved_at=datetime(2026, 8, 27, tzinfo=timezone.utc),
    )


# =========================================================================== #
# Selection                                                                   #
# =========================================================================== #


def test_the_current_period_results_filing_is_selected() -> None:
    chosen = select_current_period_disclosure(
        [
            _event("Ordinary Shareholders' Meeting", category=EVENT_CATEGORY_GOVERNANCE),
            _event("H1 2026 Financial Results"),
            _event("Notice of dividend payment", category=EVENT_CATEGORY_GOVERNANCE),
        ],
        allowed_domains=_HOSTS,
    )
    assert chosen is not None
    assert chosen.title == "H1 2026 Financial Results"
    assert chosen.period.period.key == "2026-H1"
    assert chosen.venue == _VENUE


def test_the_newest_period_wins_not_the_newest_publication_date() -> None:
    """A venue republishes and back-fills; "the newest period reported" is the
    question being asked."""
    chosen = select_current_period_disclosure(
        [
            _event("H1 2026 Financial Results", day=1),
            _event("Q1 2026 Interim Management Statement", day=28),
        ],
        allowed_domains=_HOSTS,
    )
    assert chosen is not None
    assert chosen.period.period.key == "2026-H1"


def test_a_notice_ABOUT_a_filing_is_not_the_filing() -> None:
    """A storage mechanism publishes both; the notice is two pages."""
    chosen = select_current_period_disclosure(
        [
            _event("Notice of publication of the Half-Year Financial Report 2026"),
            _event("H1 2026 Financial Results"),
        ],
        allowed_domains=_HOSTS,
    )
    assert chosen is not None
    assert chosen.title == "H1 2026 Financial Results"


def test_only_a_notice_yields_no_candidate() -> None:
    assert (
        select_current_period_disclosure(
            [_event("Notice of publication of the Half-Year Financial Report 2026")],
            allowed_domains=_HOSTS,
        )
        is None
    )


def test_a_document_off_the_venues_own_host_is_refused() -> None:
    """The URL came off an allowlisted page, but a page can link anywhere and
    this is about to become a fetch target."""
    assert (
        select_current_period_disclosure(
            [_event("H1 2026 Financial Results", url="https://elsewhere.test/x.pdf")],
            allowed_domains=_HOSTS,
        )
        is None
    )


def test_a_disclosure_with_no_document_is_refused() -> None:
    assert (
        select_current_period_disclosure(
            [_event("H1 2026 Financial Results", url=None)], allowed_domains=_HOSTS
        )
        is None
    )


def test_an_annual_or_undated_disclosure_is_not_a_current_period_candidate() -> None:
    assert (
        select_current_period_disclosure(
            [
                _event("2025 Annual Report"),
                _event("Financial Results"),
            ],
            allowed_domains=_HOSTS,
        )
        is None
    )


def test_no_events_yields_no_candidate() -> None:
    assert select_current_period_disclosure(None, allowed_domains=_HOSTS) is None
    assert select_current_period_disclosure([], allowed_domains=_HOSTS) is None


# =========================================================================== #
# The venue path is a FALLBACK                                                #
# =========================================================================== #


def _artifact(title: str, url: str, *, facts=()) -> PrimaryDocumentArtifact:
    return PrimaryDocumentArtifact(
        source_url=url,
        title=title,
        status=STATUS_EXTRACTED,
        extraction=PrimaryDocumentExtraction(
            content_hash="a" * 64,
            mime_type="application/pdf",
            extraction_method="native_pdf",
            status=STATUS_EXTRACTED,
            page_count=10,
            excerpts=[
                PrimaryDocumentExcerpt(
                    excerpt_id="e1",
                    text="Revenue review for the period.",
                    heading="Revenue review",
                    page_number=1,
                    char_count=30,
                    confidence=0.9,
                    extraction_method="native_pdf",
                )
            ],
        ),
        validated_facts=list(facts),
    )


def test_an_issuer_serving_its_own_interim_report_needs_no_venue_document() -> None:
    assert has_current_period_document(
        [_artifact("Issuer Q2 2026 Interim Report", "https://issuer.test/q2.pdf")]
    )


def test_an_issuer_with_only_an_annual_report_does_need_one() -> None:
    assert not has_current_period_document(
        [_artifact("Annual Report 2025", "https://issuer.test/annual-report-2025.pdf")]
    )
    assert not has_current_period_document([])
    assert not has_current_period_document(None)


# =========================================================================== #
# Ingestion                                                                   #
# =========================================================================== #


def _selection():
    chosen = select_current_period_disclosure(
        [_event("H1 2026 Financial Results")], allowed_domains=_HOSTS
    )
    assert chosen is not None
    return chosen


@pytest.mark.asyncio
async def test_a_venue_document_keeps_T1_content_and_T2_transport() -> None:
    fact = ValidatedFact(
        label="revenue",
        value_numeric=1289.9,
        value_text="1,289.9",
        currency="EUR",
        scale="million",
        period="2026-H1",
        extraction_method="native_pdf",
        confidence=0.9,
        validation_status=VALIDATION_VALIDATED,
        scope="group",
    )

    async def extractor(url, **kwargs):
        assert kwargs["allowed_domains"] == _HOSTS
        return _artifact("H1 2026 Financial Results", url, facts=[fact])

    items, gaps, artifacts = await _ingest_regulated_storage_document(
        extractor,
        _selection(),
        company=_COMPANY,
        allowed_domains=_HOSTS,
        connector_key="example_venue",
        source_id="example_venue",
        cfg=Settings(),
    )
    assert gaps == []
    assert len(artifacts) == 1
    facts = [i for i in items if i.source_type == VENUE_DOCUMENT_FACT_TYPE]
    assert len(facts) == 1
    assert facts[0].content_source_tier == T1_PRIMARY_FILING
    assert facts[0].provider_transport_tier == T2_REGULATOR_OR_GOV
    assert _VENUE in facts[0].provider_transport
    assert facts[0].primary_fact.period == "2026-H1"
    assert facts[0].primary_fact.numeric_value == 1289.9
    assert any(i.source_type == VENUE_DOCUMENT_EXCERPT_TYPE for i in items)


@pytest.mark.asyncio
async def test_a_failed_venue_extraction_states_a_precise_technical_reason() -> None:
    async def extractor(url, **kwargs):
        return PrimaryDocumentArtifact(
            source_url=url,
            title="H1 2026 Financial Results",
            status=STATUS_METADATA_ONLY,
            failure_code="scanned_no_text",
        )

    items, gaps, artifacts = await _ingest_regulated_storage_document(
        extractor,
        _selection(),
        company=_COMPANY,
        allowed_domains=_HOSTS,
        connector_key="example_venue",
        source_id="example_venue",
        cfg=Settings(),
    )
    assert items == []
    assert artifacts == []
    assert len(gaps) == 1
    assert "scanned_no_text" in gaps[0].message
    assert "H1 2026" in gaps[0].message
    assert _VENUE in gaps[0].message


@pytest.mark.asyncio
async def test_an_extractor_error_never_breaks_the_report() -> None:
    async def extractor(url, **kwargs):
        raise RuntimeError("boom")

    items, gaps, artifacts = await _ingest_regulated_storage_document(
        extractor,
        _selection(),
        company=_COMPANY,
        allowed_domains=_HOSTS,
        connector_key="example_venue",
        source_id="example_venue",
        cfg=Settings(),
    )
    assert (items, artifacts) == ([], [])
    assert len(gaps) == 1
    assert "boom" not in gaps[0].message  # never provider text


# =========================================================================== #
# S1 — the label-colon headline shape                                         #
# =========================================================================== #


@pytest.mark.parametrize(
    ("sentence", "expected"),
    [
        ("  GROUP CONSOLIDATED REVENUES: EUR 1,289.", "group"),
        ("  CONSOLIDATED SALES: EUR 22,420.", "group"),
        ("  STONE ISLAND REVENUES: EUR 200.", "Stone Island"),
        ("  BRAND TWO REVENUES: EUR 200.", "Brand Two"),
        # A period is not an entity. Fail closed rather than invent "H1".
        ("  H1 REVENUES: EUR 100.", None),
        # No qualifier at all — unchanged, still unscoped.
        ("  REVENUES: EUR 200.3 million.", None),
        ("  NET RESULT: EUR 164.7 million.", None),
    ],
)
def test_headline_scope_shape(sentence: str, expected) -> None:
    assert _infer_prose_scope(sentence) == expected


def test_the_existing_subject_rules_are_untouched() -> None:
    assert (
        _infer_prose_scope(
            "The Group's Specialist Watchmakers reported sales of EUR 3.1 billion."
        )
        == "Specialist Watchmakers"
    )
    assert _infer_prose_scope("At Group level, operating profit came in at 20.") == "group"


def test_a_brand_headline_figure_never_reaches_a_group_slot() -> None:
    """The end-to-end consequence of S1, on the real sentence."""
    text = (
        "3% cFX YoY, notwithstanding the continued optimisation of the "
        "distribution network.  STONE ISLAND REVENUES: EUR 200.3 million in "
        "the first half of 2026, an increase of 11% cFX (+7% at current "
        "exchange rates) compared with EUR 186.7 million in the same period "
        "of 2025."
    )
    extraction = PrimaryDocumentExtraction(
        content_hash="b" * 64,
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status=STATUS_EXTRACTED,
        page_count=17,
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="e1",
                text=text,
                heading="Brand performance",
                page_number=2,
                char_count=len(text),
                confidence=0.9,
                extraction_method="native_pdf",
            )
        ],
    )
    facts = validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(
            company_name="Issuer SpA", legal_name="Issuer SpA", ticker="ISS"
        ),
    )
    revenue = [f for f in facts if f.label == "revenue"]
    assert revenue
    assert all(f.scope == "Stone Island" for f in revenue), [f.scope for f in revenue]


# =========================================================================== #
# S2 — a ratio base is not a revenue figure                                   #
# =========================================================================== #


def test_an_expense_compared_on_a_revenue_base_is_not_revenue() -> None:
    """The real sentence. It was yielding EUR 170.4 m as H1 2025 revenue."""
    text = (
        "General and administrative expenses were EUR 180.4 million, with a "
        "14.0% incidence on revenues, compared with EUR 170.4 million in "
        "H1 2025 (13.9% on revenues)."
    )
    extraction = PrimaryDocumentExtraction(
        content_hash="c" * 64,
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status=STATUS_EXTRACTED,
        page_count=17,
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="e1",
                text=text,
                heading="GROUP INCOME STATEMENT RESULTS",
                page_number=8,
                char_count=len(text),
                confidence=0.9,
                extraction_method="native_pdf",
            )
        ],
    )
    facts = validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(
            company_name="Issuer SpA", legal_name="Issuer SpA", ticker="ISS"
        ),
    )
    assert [f for f in facts if f.label == "revenue"] == []


def test_a_real_revenue_sentence_still_parses() -> None:
    """The guard must exclude the ratio base and nothing else."""
    text = "Group revenue was EUR 1,289.9 million in the first half of 2026."
    extraction = PrimaryDocumentExtraction(
        content_hash="d" * 64,
        mime_type="application/pdf",
        extraction_method="native_pdf",
        status=STATUS_EXTRACTED,
        page_count=17,
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="e1",
                text=text,
                heading="Revenue",
                page_number=2,
                char_count=len(text),
                confidence=0.9,
                extraction_method="native_pdf",
            )
        ],
    )
    facts = validate_extracted_facts(
        extraction,
        issuer_context=IssuerContext(
            company_name="Issuer SpA", legal_name="Issuer SpA", ticker="ISS"
        ),
    )
    revenue = [f for f in facts if f.label == "revenue"]
    assert revenue and revenue[0].value_numeric == 1289.9
