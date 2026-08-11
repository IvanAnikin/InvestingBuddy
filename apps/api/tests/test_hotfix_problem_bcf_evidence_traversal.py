"""
Phase 32A hotfix — Problems B, C, F.

Problem B (bounded issuer-publication traversal gap): the company-IR connector
historically fetched ONLY the issuer's ``annual_reports_url``. The separately
registered ``investor_relations_url`` stayed inert metadata, and nothing ever
followed a discovered link one page deeper — so a genuinely-available, official,
CURRENT-results document could sit on the issuer's own site and never be
traversed (the confirmed LVMH-style gap). Covers: the second index page, the
ONE bounded extra hop into a "results landing page" child link, generic
candidate ranking, the SSRF/allowlist boundary staying intact through the
widened keyword vocabulary, and the new distinct gap-message states.

Problem C (long-document financial-statement passage selection): a bounded
evidence pack could surface only narrative prose from a long annual report,
even when the source document also had balance-sheet / cash-flow / segment
table content — because table-derived material had no distinct evidence
category and lost the append-order tie-break against narrative within the same
category. Covers: the new ``classify_statement_type`` heading classifier, the
new operating-cash-flow fact pattern, and the new ``CATEGORY_STATEMENT_TABLE``
evidence-budget floor.

Problem F (document language / translation metadata wrongly domicile-based):
an issuer's own registered country was used to FORCE a translation-pending
label even when the actual document content was confidently English (or vice
versa) — a domicile guess pre-empted a confident content-based result. Covers:
content-first language priority, hint-as-weak-fallback-only, and honest
"undetermined" behaviour.

Fully offline: every fetch is a hand-built fake (dict/URL-keyed) async
function or a fake httpx client; no real network call is ever made. Company
names referenced (e.g. "LVMH-style", "CFR-style", the CFR/KER/MC registry
entries) are the platform's own pre-existing verified-issuer fixtures, used
here only as stand-ins for the generic scenario under test — nothing here is
company-specific logic.
"""

from __future__ import annotations

import asyncio

from app.services.llm.evidence_budget import (
    CATEGORY_PRIMARY_DOCUMENT,
    CATEGORY_STATEMENT_TABLE,
    apply_evidence_budget,
    evidence_category,
)
from app.services.llm.schemas import TIER_T1_PRIMARY_FILING, EvidencePack
from app.services.llm.schemas import EvidenceItem as CouncilEvidenceItem
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import (
    _MAX_CHILD_LANDING_PAGES,
    CompanyIrConnector,
    PrimaryDocumentArtifact,
    _rank_candidate_links,
)
from app.services.sources.extracted_fact_validator import (
    FIELD_OPERATING_CASH_FLOW,
    VALIDATION_EXCERPT_ONLY,
    VALIDATION_VALIDATED,
    IssuerContext,
    ValidatedFact,
    validate_extracted_facts,
)
from app.services.sources.language import detect_language, detect_language_with_confidence
from app.services.sources.primary_document_extractor import (
    METHOD_NATIVE_PDF,
    STATUS_EXTRACTED,
    ExtractedTable,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
    classify_statement_type,
    extract_pdf,
)
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    SafeFetchResult,
    SafeLink,
    extract_links,
    safe_fetch_page,
)
from app.services.sources.verified_issuer_sources import get_verified_issuer_source
from tests.helpers.pdf_fixtures import make_pdf


def _q(max_items: int = 5) -> QueryContext:
    return QueryContext(max_items=max_items)


# =========================================================================== #
# Problem B — bounded issuer-publication traversal
# =========================================================================== #


def test_b1_bounded_child_hop_discovers_current_results_document():
    """Index page → non-document 'landing page' link → ONE bounded extra hop
    discovers the actual current-results document (the proven LVMH-style gap)."""
    v = get_verified_issuer_source("KER", "PA")
    landing_url = "https://www.kering.com/en/finance/publications/half-year-2026-results/"
    pdf_url = (
        "https://www.kering.com/en/finance/publications/half-year-2026-results/"
        "kering-h1-2026-results.pdf"
    )

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        if url == v.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[
                    SafeLink(
                        url=landing_url, text="Half-Year 2026 Results", is_document=False
                    )
                ],
            )
        if url == landing_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[
                    SafeLink(
                        url=pdf_url,
                        text="Half-Year 2026 Results Report",
                        is_document=True,
                    )
                ],
            )
        # investor_relations_url (or anything else) — nothing new here.
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=v, page_fetcher=fetcher)
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q())
    )
    ar_items = [i for i in res.evidence_items if i.source_type == "company_ir_annual_report"]
    assert any(i.url == pdf_url for i in ar_items), [i.url for i in ar_items]
    # No gap complains about a missing candidate — the hop found one.
    assert not any(
        "no candidate" in g.message for g in res.source_gaps
    )


def test_b2_off_domain_child_link_is_rejected():
    """An off-domain link sitting next to a legitimate landing-page link on the
    SAME index page never survives allowlisted link extraction — even though
    both now match the widened keyword vocabulary."""
    html = (
        '<a href="https://www.kering.com/en/finance/publications/'
        'half-year-results-2026/">Half-Year Results 2026</a>'
        '<a href="https://evil.example.com/half-year-results-2026/">'
        "Half-Year Results 2026 (off-domain mirror)</a>"
    )
    links = extract_links(
        html,
        base_url="https://www.kering.com/en/finance/publications/",
        allowed_domains=("kering.com",),
        keywords=ANNUAL_REPORT_KEYWORDS,
        max_links=10,
    )
    assert len(links) == 1
    assert links[0].url.startswith("https://www.kering.com/")
    assert "evil.example.com" not in links[0].url


class _RedirectStream:
    def __init__(self, *, status_code: int, headers: dict[str, str]):
        self.status_code = status_code
        self.headers = headers
        self.is_redirect = True

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        return
        yield  # pragma: no cover - never reached (redirect short-circuits)


class _RedirectClient:
    def __init__(self, **kw):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        return _RedirectStream(
            status_code=302, headers={"location": "https://evil.example.com/steal"}
        )


def test_b3_redirect_outside_allowlist_is_blocked(monkeypatch):
    """A candidate landing page that redirects OFF the issuer's allowlist is
    blocked by the existing, unmodified redirect guard — never followed."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _RedirectClient(**kw))
    result = asyncio.run(
        safe_fetch_page(
            "https://www.kering.com/en/finance/publications/half-year-2026-results/",
            allowed_domains=("kering.com",),
        )
    )
    assert result.blocked is True
    assert "redirect blocked" in (result.error or "")


def test_b4_child_hop_bounded_by_named_constant():
    """The extra hop is capped at ``_MAX_CHILD_LANDING_PAGES`` — well more
    candidates than the cap must still only trigger that many fetches."""
    v = get_verified_issuer_source("KER", "PA")
    calls: list[str] = []

    async def counting_fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        calls.append(url)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    candidates = [
        SafeLink(
            url=f"https://www.kering.com/en/finance/results-{i}/",
            text=f"Financial Results {2020 + i}",
            is_document=False,
        )
        for i in range(5)
    ]
    conn = CompanyIrConnector(verified_source=v, page_fetcher=counting_fetcher)
    discovered, examined, with_candidates = asyncio.run(
        conn._hop_into_landing_pages(candidates, v)
    )
    assert _MAX_CHILD_LANDING_PAGES == 3
    assert examined == 3
    assert len(calls) == 3
    assert discovered == []
    assert with_candidates == 0


def test_b5_ranking_favors_current_financial_result_over_generic_link():
    annual = SafeLink(
        url="https://issuer.example.com/annual-report-2025.pdf",
        text="Annual Report 2025",
        is_document=True,
    )
    interim = SafeLink(
        url="https://issuer.example.com/h1-2026-results.pdf",
        text="Half-Year 2026 Results",
        is_document=True,
    )
    generic_corporate = SafeLink(
        url="https://issuer.example.com/about-us",
        text="Corporate Overview",
        is_document=False,
    )
    current_result = SafeLink(
        url="https://issuer.example.com/financial-results-2026.pdf",
        text="Financial Results 2026",
        is_document=True,
    )

    # Annual beats interim beats a generic corporate/about-us link.
    ranked = _rank_candidate_links([interim, generic_corporate, annual])
    assert ranked[0] is annual
    assert ranked[-1] is generic_corporate

    # A current financial-result link outranks an unrelated corporate page.
    ranked2 = _rank_candidate_links([generic_corporate, current_result])
    assert ranked2[0] is current_result


def test_b6_fetch_filings_ranks_annual_report_first_regardless_of_discovery_order():
    v = get_verified_issuer_source("KER", "PA")
    half_year_url = "https://www.kering.com/en/finance/publications/kering-h1-2026.pdf"
    annual_url = "https://www.kering.com/en/finance/publications/kering-ar-2025.pdf"

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(
            requested_url=url,
            status_code=200,
            # Interim result discovered BEFORE the annual report on the page.
            links=[
                SafeLink(url=half_year_url, text="Half-Year 2026 Results", is_document=True),
                SafeLink(url=annual_url, text="Annual Report 2025", is_document=True),
            ],
        )

    conn = CompanyIrConnector(verified_source=v, page_fetcher=fetcher)
    res = asyncio.run(
        conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q())
    )
    ar_items = [i for i in res.evidence_items if i.source_type == "company_ir_annual_report"]
    assert ar_items and ar_items[0].url == annual_url


def test_b7_failure_state_distinguishes_no_candidate_vs_hop_attempted():
    v = get_verified_issuer_source("KER", "PA")

    # (a) No landing-page candidate anywhere -> the extra hop is never attempted.
    async def empty_fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    res_a = asyncio.run(
        CompanyIrConnector(verified_source=v, page_fetcher=empty_fetcher).fetch_filings(
            CompanyContext(ticker="KER", exchange="PA"), _q()
        )
    )
    msgs_a = [g.message for g in res_a.source_gaps]
    assert any("no candidate on primary index" in m for m in msgs_a)
    assert not any("child result-page hop attempted" in m for m in msgs_a)

    # (b) A landing-page candidate IS found and followed, but it yields nothing.
    landing_url = "https://www.kering.com/en/finance/publications/half-year-2026-results/"

    async def hop_but_empty_fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        if url == v.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[
                    SafeLink(
                        url=landing_url, text="Half-Year 2026 Results", is_document=False
                    )
                ],
            )
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    res_b = asyncio.run(
        CompanyIrConnector(
            verified_source=v, page_fetcher=hop_but_empty_fetcher
        ).fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q())
    )
    msgs_b = [g.message for g in res_b.source_gaps]
    assert any("child result-page hop attempted, no candidate" in m for m in msgs_b)
    # Distinct from (a): a landing-page candidate DID exist and WAS followed —
    # this is a materially different, more granular status than "nothing at
    # primary index at all".
    assert not any("no candidate on primary index" in m for m in msgs_b)

    # (c) A genuine fetch/extraction failure (primary index blocked) keeps its
    # OWN, already-distinct message — never conflated with "no candidate".
    async def blocked_fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(requested_url=url, blocked=True, error="blocked")

    res_c = asyncio.run(
        CompanyIrConnector(verified_source=v, page_fetcher=blocked_fetcher).fetch_filings(
            CompanyContext(ticker="KER", exchange="PA"), _q()
        )
    )
    msgs_c = [g.message for g in res_c.source_gaps]
    assert any("could not be safely fetched" in m for m in msgs_c)
    assert not any("no candidate" in m for m in msgs_c)


# =========================================================================== #
# Problem C — long-document financial-statement passage selection
# =========================================================================== #


def test_c1_classify_statement_type_recognizes_generic_headings():
    assert classify_statement_type("Consolidated Balance Sheet") == "balance_sheet"
    assert classify_statement_type("Statement of Cash Flows") == "cash_flow_statement"
    assert classify_statement_type("Segment Information") == "segment_reporting"
    assert classify_statement_type("Consolidated Income Statement") == "income_statement"
    assert classify_statement_type("Chairman's Letter to Shareholders") is None
    assert classify_statement_type(None) is None


def test_c2_operating_cash_flow_recognized_when_labeled_with_currency_and_scale():
    table = ExtractedTable(
        table_location="p3:t0",
        table_index=0,
        page_number=3,
        rows=[
            ["EUR million", "2024", "2023"],
            ["Net cash from operating activities", "1,234", "1,100"],
        ],
        row_count=2,
        col_count=3,
        extraction_method=METHOD_NATIVE_PDF,
    )
    ext = PrimaryDocumentExtraction(
        content_hash="a" * 64,
        mime_type="application/pdf",
        extraction_method=METHOD_NATIVE_PDF,
        status=STATUS_EXTRACTED,
        tables=[table],
    )
    facts = validate_extracted_facts(
        ext, issuer_context=IssuerContext(company_name="Test Co", ticker="TST")
    )
    ocf = [f for f in facts if f.label == FIELD_OPERATING_CASH_FLOW]
    assert ocf, [f.label for f in facts]
    validated = [f for f in ocf if f.validation_status == VALIDATION_VALIDATED]
    assert validated
    rec = next(f for f in validated if f.period == "2024")
    assert rec.value_numeric == 1234.0
    assert rec.currency == "EUR" and rec.scale == "million"


def _council_item(
    id_: str, *, source_type: str, excerpt: str = "text", title: str = "t"
) -> CouncilEvidenceItem:
    return CouncilEvidenceItem(
        id=id_,
        source_tier=TIER_T1_PRIMARY_FILING,
        source_type=source_type,
        content_tier=TIER_T1_PRIMARY_FILING,
        transport_tier=TIER_T1_PRIMARY_FILING,
        title=title,
        excerpt=excerpt,
    )


def test_c3_statement_table_content_survives_alongside_narrative_under_tight_budget():
    narrative = [
        _council_item(f"N{i}", source_type="company_ir_annual_report_excerpt", excerpt=f"narrative paragraph {i}")
        for i in range(10)
    ]
    statement = [
        _council_item(f"S{i}", source_type="company_ir_statement_excerpt", excerpt=f"balance sheet row {i}")
        for i in range(3)
    ]
    pack = EvidencePack(evidence_items=narrative + statement)
    from app.core.config import Settings

    out = apply_evidence_budget(
        pack, max_items=5, cfg=Settings(llm_council_evidence_budgets_enabled=True)
    )
    cats = [evidence_category(i) for i in out.evidence_items]
    assert cats.count(CATEGORY_STATEMENT_TABLE) == 3
    assert cats.count(CATEGORY_PRIMARY_DOCUMENT) >= 1


def test_c4_earlier_generic_narrative_cannot_consume_every_slot():
    """Narrative appended FIRST (mirrors the root-cause construction-order bug),
    but under a tight budget the statement floor still guarantees survival —
    generic prose can no longer crowd out every slot."""
    narrative = [
        _council_item(
            f"N{i}", source_type="company_ir_annual_report_excerpt",
            excerpt=f"generic narrative paragraph number {i}",
        )
        for i in range(6)
    ]
    statement = [
        _council_item(
            f"S{i}", source_type="company_ir_statement_excerpt",
            excerpt=f"balance sheet line item {i}",
        )
        for i in range(3)
    ]
    pack = EvidencePack(evidence_items=narrative + statement)  # narrative FIRST
    from app.core.config import Settings

    out = apply_evidence_budget(
        pack, max_items=3, cfg=Settings(llm_council_evidence_budgets_enabled=True)
    )
    cats = [evidence_category(i) for i in out.evidence_items]
    assert cats.count(CATEGORY_STATEMENT_TABLE) == 3
    assert cats.count(CATEGORY_PRIMARY_DOCUMENT) == 0


def test_c5_artifact_to_evidence_tags_statement_excerpt_and_demoted_table_row():
    v = get_verified_issuer_source("CFR", "SW")
    conn = CompanyIrConnector(verified_source=v)
    ext = PrimaryDocumentExtraction(
        content_hash="a" * 64,
        mime_type="application/pdf",
        extraction_method=METHOD_NATIVE_PDF,
        status=STATUS_EXTRACTED,
        language="en",
        excerpts=[
            PrimaryDocumentExcerpt(
                excerpt_id="X1",
                text="The Group continues to see strong demand across its business.",
                heading="Business overview",
                confidence=0.8,
            ),
            PrimaryDocumentExcerpt(
                excerpt_id="X2",
                text="Cash and cash equivalents at year end were EUR 1.2 billion.",
                heading="Consolidated Balance Sheet",
                confidence=0.8,
            ),
        ],
    )
    fact = ValidatedFact(
        label="total_assets",
        value_numeric=1000.0,
        value_text="1,000",
        unit="currency_amount",
        currency="EUR",
        scale="million",
        period="2024",
        page_number=4,
        table_location="p4:t0",
        extraction_method=METHOD_NATIVE_PDF,
        confidence=0.5,
        validation_status=VALIDATION_EXCERPT_ONLY,
    )
    artifact = PrimaryDocumentArtifact(
        source_url="https://www.richemont.com/reports/ar2024.pdf",
        status=STATUS_EXTRACTED,
        extraction=ext,
        validated_facts=[fact],
    )
    target = SafeLink(
        url=artifact.source_url, text="Annual Report 2024", is_document=True
    )
    items, _gaps = conn._artifact_to_evidence(artifact, target, 1, _q())
    by_id = {i.id: i for i in items}
    assert by_id["IRDOC1X1"].source_type == "company_ir_annual_report_excerpt"
    assert by_id["IRDOC1X2"].source_type == "company_ir_statement_excerpt"
    assert by_id["IRTBL1_1"].source_type == "company_ir_statement_excerpt"


# =========================================================================== #
# Problem F — document language / translation metadata wrongly domicile-based
# =========================================================================== #

_ENGLISH_TEXT = (
    "The Group reported record revenue for the period and the company "
    "continues to see strong demand across all of its business segments and "
    "geographic regions worldwide this year, with the board noting solid "
    "growth and continued momentum across every division."
)
_FRENCH_TEXT = (
    "Le groupe a annoncé une forte croissance du chiffre d'affaires au cours "
    "de l'exercice et les perspectives de la société restent solides pour "
    "l'ensemble des maisons du groupe dans toutes les régions du monde."
)


def test_f1_english_content_wins_over_non_english_domicile_hint():
    # CFR-style scenario: content-based detection must win over a non-English
    # domicile hint (e.g. a French registry hint on a Swiss/French-domicile
    # issuer's genuinely English document).
    raw = make_pdf([_ENGLISH_TEXT])
    result = extract_pdf(raw, original_language="fr")
    assert result.language == "en"
    assert result.requires_translation is False


def test_f1b_deep_evidence_item_not_marked_translation_pending_for_english_content():
    # LVMH-style scenario: the issuer's registered country implies "fr", but
    # the document is genuinely English.
    v = get_verified_issuer_source("MC", "PA")
    conn = CompanyIrConnector(verified_source=v)
    assert conn._original_language() == "fr"  # confirms the domicile hint exists

    raw = make_pdf([_ENGLISH_TEXT])
    ext = extract_pdf(raw, original_language=conn._original_language())
    assert ext.language == "en"
    assert ext.requires_translation is False

    artifact = PrimaryDocumentArtifact(
        source_url="https://www.lvmh.com/en/publications/half-year-2026-results.pdf",
        status=STATUS_EXTRACTED,
        extraction=ext,
    )
    target = SafeLink(
        url=artifact.source_url, text="Half-Year 2026 Results", is_document=True
    )
    items, _gaps = conn._artifact_to_evidence(artifact, target, 1, _q())
    assert items
    for it in items:
        assert it.requires_translation is False
        assert it.original_language is None
        assert not any("translation pending" in w.lower() for w in it.warnings)


def test_f2_non_english_content_detected_via_content_not_just_domicile():
    # Genuinely non-English content is still detected even WITHOUT a hint.
    raw = make_pdf([_FRENCH_TEXT])
    result = extract_pdf(raw, original_language=None)
    assert result.language == "fr"
    assert result.requires_translation is True

    # And content wins even when the (wrong) hint says something else.
    code, confident = detect_language_with_confidence(_FRENCH_TEXT, hint="de")
    assert code == "fr" and confident is True


def test_f3_undetermined_language_is_honest_never_a_forced_guess():
    # Too short / no distinctive signal in ANY language, and no hint at all.
    code, confident = detect_language_with_confidence("Q3 numbers.", hint=None)
    assert code == "en"
    assert confident is False  # an honest "unknown", not a confirmed detection

    # A hint is a WEAK fallback used ONLY when content is genuinely inconclusive.
    assert detect_language("", hint="fr") == "fr"
    assert detect_language("", hint=None) == "en"

    # At the extraction level: a block with no distinctive stopword signal in
    # any language and no domicile hint never invents a translation requirement.
    numeric_only = "1234567890 998877665544 AAA111 BBB222 CCC333 DDD444 EEE555"
    raw = make_pdf([numeric_only])
    result = extract_pdf(raw, original_language=None)
    assert result.language == "en"
    assert result.requires_translation is False
