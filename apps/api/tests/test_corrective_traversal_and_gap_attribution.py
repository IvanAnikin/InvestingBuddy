"""
Corrective follow-up from live staging acceptance (post-PR #99/#100).

Two tightly related, previously-unproven gaps:

Problem 1 (traversal topology) — the bounded second hop added by PR #99
(``CompanyIrConnector._hop_into_landing_pages``) fetched a results-LANDING
page but only ever looked at its plain, report-keyword-filtered anchors
(``child.links``). A landing page's actual rich content — a same-domain HTML
press release, or a verified issuer PDF — is very often reachable only via a
link labelled with PRESS vocabulary ("Press release", "announcement", …) that
``ANNUAL_REPORT_KEYWORDS`` alone never matches, or via the richer hydration/
JSON-LD discovery strategies already used at depth 0 but never applied to the
depth-1 page. The live-proven symptom: MC (LVMH-style) reached a "2026 First
Half Results" landing page and extracted only 2 thin excerpts / 0 structured
facts, even though that very landing page links to a rich English press
release with the real figures.

The fix widens the hop's own discovery to reuse ``_discover_deep_targets``
(the same hydration/JSON-LD strategies already used at depth 0) with a wider,
still-generic keyword vocabulary (``_HOP_KEYWORDS`` = report + press
vocabulary), and adds a small, explicitly bounded "keep going a little further
if everything ingested so far is thin" fallback inside
``_extract_primary_documents_deep`` so a genuinely rich candidate ranked just
outside the normal per-issuer cap is not silently dropped.

Problem 2 (gap-attribution grounding) — council prose could blame a specific
cause (e.g. "untranslated French filings") for thin evidence even when the
run's own structured gap state never recorded that cause. The fix
(``app.services.llm.gap_attribution.ground_gap_text``) is a generic,
closed-vocabulary grounding check: a causal claim only survives when the run's
``known_gaps`` recorded a compatible cause; otherwise it is replaced with
generic insufficient-evidence wording. A gap item asserting no specific cause
is never touched.

Fully offline: every fetch is a hand-built fake async function; no real
network call is ever made. Company/issuer names referenced (KER/Kering,
CFR/Richemont) are the platform's own pre-existing verified-issuer test
fixtures, used only as generic stand-ins — nothing here is company-specific
product logic.
"""

from __future__ import annotations

import asyncio

from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.gap_attribution import GROUNDING_FALLBACK_MESSAGE, ground_gap_text
from app.services.llm.schemas import AgentRiskGap, CouncilAgentOutput
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import (
    _HOP_KEYWORDS,
    _MAX_CHILD_LANDING_PAGES,
    _MAX_THIN_FALLBACK_DOCS,
    CompanyIrConnector,
    PrimaryDocumentArtifact,
    _has_financial_signal,
)
from app.services.sources.primary_document_extractor import (
    METHOD_NATIVE_PDF,
    STATUS_EXTRACTED,
    PrimaryDocumentExcerpt,
    PrimaryDocumentExtraction,
)
from app.services.sources.safe_web_fetcher import SafeFetchResult, SafeLink
from app.services.sources.verified_issuer_sources import get_verified_issuer_source


def _q(max_items: int = 5) -> QueryContext:
    return QueryContext(max_items=max_items)


# =========================================================================== #
# Problem 1 — bounded traversal topology
# =========================================================================== #

_KER = get_verified_issuer_source("KER", "PA")
_LANDING_URL = "https://www.kering.com/en/finance/publications/half-year-2026-results/"
_PRESS_HTML_URL = (
    "https://www.kering.com/en/finance/publications/half-year-2026-results/press-release/"
)
_PRESS_PDF_URL = (
    "https://www.kering.com/en/finance/publications/half-year-2026-results/"
    "kering-h1-2026-press-release.pdf"
)


def test_t1_hop_discovers_rich_same_domain_press_release_html():
    """index -> thin results landing -> rich same-domain HTML press release.

    The landing page's OWN body links to a same-domain HTML press release
    labelled only "Press release" — text ``ANNUAL_REPORT_KEYWORDS`` alone
    never matches, and the fake fetcher deliberately returns an EMPTY
    ``links`` list (mirroring a page whose anchors did not pass the ORIGINAL
    report-only keyword filter) with only ``body_html`` populated, so this
    proves the widened, body_html-driven discovery — not the pre-existing
    anchor list — is what finds it.
    """

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        if url == _KER.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            )
        if url == _LANDING_URL:
            body = f'<a href="{_PRESS_HTML_URL}">Press release</a>'
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=fetcher)
    res = asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    ar_items = [i for i in res.evidence_items if i.source_type == "company_ir_annual_report"]
    assert any(i.url == _PRESS_HTML_URL for i in ar_items), [i.url for i in ar_items]


def test_t2_hop_discovers_verified_issuer_pdf_document_link():
    """index -> thin landing -> verified issuer PDF document, DEEP path.

    Same body_html-only discovery as t1, but the linked document is a PDF and
    the deep extractor is injected, so this proves the discovered candidate
    actually reaches ``_extract_primary_documents_deep`` and is ingested
    (not merely listed as shallow link metadata).
    """

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        if url == _KER.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            )
        if url == _LANDING_URL:
            body = f'<a href="{_PRESS_PDF_URL}">Press release (PDF)</a>'
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    async def extractor(url, *, allowed_domains, title_hint=None, original_language=None, issuer_context=None):
        return PrimaryDocumentArtifact(
            source_url=url,
            status=STATUS_EXTRACTED,
            extraction=PrimaryDocumentExtraction(
                content_hash="b" * 64,
                mime_type="application/pdf",
                extraction_method=METHOD_NATIVE_PDF,
                status=STATUS_EXTRACTED,
                excerpts=[
                    PrimaryDocumentExcerpt(
                        excerpt_id="X1",
                        text="Group revenue for the half-year was strong.",
                        confidence=0.8,
                    )
                ],
            ),
        )

    conn = CompanyIrConnector(
        verified_source=_KER, page_fetcher=fetcher, primary_document_extractor=extractor
    )
    res = asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    assert conn.collected_primary_document_artifacts
    assert conn.collected_primary_document_artifacts[0].source_url == _PRESS_PDF_URL
    assert any(
        i.source_type == "company_ir_annual_report_excerpt" for i in res.evidence_items
    ), [i.source_type for i in res.evidence_items]


def test_t3_hop_never_recurses_a_second_level_deep():
    """Maximum traversal depth enforced: a link discovered BY the hop is never
    itself fetched again — a single bounded extra hop only, no crawler."""
    grandchild_url = "https://www.kering.com/en/finance/publications/half-year-2026-results/press-release/appendix.pdf"
    calls: list[str] = []

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        calls.append(url)
        if url == _KER.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            )
        if url == _LANDING_URL:
            body = f'<a href="{_PRESS_HTML_URL}">Press release</a>'
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        if url == _PRESS_HTML_URL:
            # If the hop recursed, it would fetch this page too and discover
            # ``grandchild_url`` from ITS body — but it must never be called.
            body = f'<a href="{grandchild_url}">Press release appendix</a>'
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=fetcher)
    asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    assert _PRESS_HTML_URL not in calls
    assert grandchild_url not in calls


def test_t4_total_fetch_count_bounded_independent_of_depth():
    """Total fetch count stays bounded (index page(s) + at most
    ``_MAX_CHILD_LANDING_PAGES`` hop fetches) regardless of how many
    candidate links exist on the index page."""
    calls: list[str] = []
    many_candidates = [
        SafeLink(
            url=f"https://www.kering.com/en/finance/results-{i}/",
            text=f"Financial Results {2015 + i}",
            is_document=False,
        )
        for i in range(20)
    ]

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        calls.append(url)
        if url == _KER.annual_reports_url:
            return SafeFetchResult(requested_url=url, status_code=200, links=many_candidates)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=fetcher)
    asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    # 1 primary index + up to 1 IR index (same URL here, so de-duped by the
    # connector's own equality check) + at most _MAX_CHILD_LANDING_PAGES hops.
    assert len(calls) <= 1 + 1 + _MAX_CHILD_LANDING_PAGES


def test_t5_visited_url_prevents_loop():
    """A landing page that links back to ITSELF never causes a repeat fetch or
    an infinite loop (non-recursive hop + de-duped merge is sufficient)."""
    calls: list[str] = []

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        calls.append(url)
        if url == _KER.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            )
        if url == _LANDING_URL:
            # Self-referential anchor (e.g. a "back to top" / canonical link).
            body = f'<a href="{_LANDING_URL}">Half-Year 2026 Results</a>'
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=fetcher)
    res = asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    assert calls.count(_LANDING_URL) == 1
    assert res is not None  # completes without hanging/raising


def test_t6_off_domain_child_link_in_body_html_rejected():
    """An off-domain link inside the landing page's OWN body (discoverable
    only via the widened body_html strategy) is still rejected by the
    unmodified allowlist — the widened vocabulary never widens the domain
    boundary."""

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        if url == _KER.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            )
        if url == _LANDING_URL:
            body = (
                '<a href="https://evil.example.com/press-release/">Press release</a>'
            )
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=fetcher)
    res = asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    assert not any("evil.example.com" in (i.url or "") for i in res.evidence_items)


def test_t7_unsafe_redirect_from_hop_candidate_rejected(monkeypatch):
    """A landing-page candidate that redirects OFF the issuer's allowlist is
    blocked by the existing, unmodified redirect guard when driven through
    the REAL ``safe_fetch_page`` (not a fake) via the connector's hop."""
    import httpx

    from app.services.sources.safe_web_fetcher import safe_fetch_page

    class _RedirectStream:
        def __init__(self):
            self.status_code = 302
            self.headers = {"location": "https://evil.example.com/steal"}
            self.is_redirect = True

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        async def aiter_bytes(self):
            return
            yield  # pragma: no cover

    class _RedirectClient:
        def __init__(self, **kw):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, method, url):
            return _RedirectStream()

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _RedirectClient(**kw))
    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=safe_fetch_page)
    discovered, examined, with_candidates = asyncio.run(
        conn._hop_into_landing_pages(
            [SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            _KER,
        )
    )
    assert examined == 1
    assert discovered == []
    assert with_candidates == 0


def test_t8_thin_document_promotes_extended_fallback_reaches_rich_candidate():
    """A rich financial page outranks / is still reached past a thin
    "report published" announcement: with the per-issuer cap set to 1, the
    top-ranked (thin) candidate alone would leave the run with 0 financial
    signal — the bounded thin-fallback must reach the second, genuinely rich
    candidate using leftover budget."""
    thin_url = "https://www.kering.com/en/finance/publications/annual-report-published.pdf"
    rich_url = "https://www.kering.com/en/finance/publications/full-year-2025-results.pdf"
    links = [
        # Ranks first (DOC_KIND_ANNUAL_REPORT via "annual report" in text).
        SafeLink(url=thin_url, text="Annual Report 2025 Published", is_document=True),
        # Ranks second (DOC_KIND_RESULTS_RELEASE).
        SafeLink(url=rich_url, text="Full-Year 2025 Results", is_document=True),
    ]

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(requested_url=url, status_code=200, links=links)

    async def extractor(url, *, allowed_domains, title_hint=None, original_language=None, issuer_context=None):
        if url == thin_url:
            return PrimaryDocumentArtifact(
                source_url=url,
                status=STATUS_EXTRACTED,
                extraction=PrimaryDocumentExtraction(
                    content_hash="c" * 64,
                    mime_type="application/pdf",
                    extraction_method=METHOD_NATIVE_PDF,
                    status=STATUS_EXTRACTED,
                    excerpts=[
                        PrimaryDocumentExcerpt(
                            excerpt_id="X1",
                            text="The annual report has now been published on our website.",
                            confidence=0.8,
                        )
                    ],
                ),
            )
        return PrimaryDocumentArtifact(
            source_url=url,
            status=STATUS_EXTRACTED,
            extraction=PrimaryDocumentExtraction(
                content_hash="d" * 64,
                mime_type="application/pdf",
                extraction_method=METHOD_NATIVE_PDF,
                status=STATUS_EXTRACTED,
                excerpts=[
                    PrimaryDocumentExcerpt(
                        excerpt_id="X1",
                        text="Group revenue and operating margin both improved this year.",
                        confidence=0.8,
                    )
                ],
            ),
        )

    conn = CompanyIrConnector(
        verified_source=_KER,
        page_fetcher=fetcher,
        primary_document_extractor=extractor,
        max_docs_per_issuer=1,
    )
    asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    urls = {a.source_url for a in conn.collected_primary_document_artifacts}
    assert thin_url in urls
    assert rich_url in urls, "the thin-fallback must reach the rich candidate"
    assert len(urls) <= 1 + _MAX_THIN_FALLBACK_DOCS
    # The rich candidate's excerpt is genuinely detected as financially useful.
    rich_artifact = next(a for a in conn.collected_primary_document_artifacts if a.source_url == rich_url)
    assert _has_financial_signal(rich_artifact)
    thin_artifact = next(a for a in conn.collected_primary_document_artifacts if a.source_url == thin_url)
    assert not _has_financial_signal(thin_artifact)


def test_t8b_rich_result_at_cap_stops_the_fallback_early():
    """When the document already ingested WITHIN the normal cap is genuinely
    rich, the bounded fallback never fires (no wasted fetches)."""
    rich_url = "https://www.kering.com/en/finance/publications/full-year-2025-results.pdf"
    unreached_url = "https://www.kering.com/en/finance/publications/never-fetched.pdf"
    links = [
        SafeLink(url=rich_url, text="Full-Year 2025 Results", is_document=True),
        SafeLink(url=unreached_url, text="Other Document", is_document=True),
    ]
    calls: list[str] = []

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(requested_url=url, status_code=200, links=links)

    async def extractor(url, *, allowed_domains, title_hint=None, original_language=None, issuer_context=None):
        calls.append(url)
        return PrimaryDocumentArtifact(
            source_url=url,
            status=STATUS_EXTRACTED,
            extraction=PrimaryDocumentExtraction(
                content_hash="e" * 64,
                mime_type="application/pdf",
                extraction_method=METHOD_NATIVE_PDF,
                status=STATUS_EXTRACTED,
                excerpts=[
                    PrimaryDocumentExcerpt(
                        excerpt_id="X1", text="Group revenue and net profit both improved.", confidence=0.8
                    )
                ],
            ),
        )

    conn = CompanyIrConnector(
        verified_source=_KER,
        page_fetcher=fetcher,
        primary_document_extractor=extractor,
        max_docs_per_issuer=1,
    )
    asyncio.run(conn.fetch_filings(CompanyContext(ticker="KER", exchange="PA"), _q()))
    assert calls == [rich_url]
    assert unreached_url not in calls


def test_t9_non_financial_child_does_not_consume_all_useful_candidate_slots():
    """A non-financial child (a webcast link) ranks below the genuine
    press-release / results-release candidate, so it never crowds out the
    useful one under a tight per-issuer cap."""
    webcast_url = "https://www.kering.com/en/finance/publications/half-year-2026-webcast/"
    press_url = "https://www.kering.com/en/finance/publications/half-year-2026-press-release/"

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        if url == _KER.annual_reports_url:
            return SafeFetchResult(
                requested_url=url,
                status_code=200,
                links=[SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            )
        if url == _LANDING_URL:
            body = (
                f'<a href="{webcast_url}">Webcast</a>'
                f'<a href="{press_url}">Press release</a>'
            )
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=fetcher, max_docs_per_issuer=1)
    discovered = asyncio.run(
        conn._hop_into_landing_pages(
            [SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            _KER,
        )
    )[0]
    ranked = conn._rank_deep_targets(discovered)
    assert ranked, [d.url for d in discovered]
    assert ranked[0].url == press_url


def test_t10_traversal_status_records_exact_stopping_stage():
    """Traversal status distinguishes the exact stopping stage: a hop that WAS
    attempted and found genuine candidates records both a non-zero
    ``pages_examined`` and a non-zero ``pages_with_candidates`` — materially
    different from the "hop attempted, nothing found" state already covered
    by the pre-existing Problem B suite."""

    async def fetcher(url, *, allowed_domains, keywords, fallback_keywords=()):
        if url == _LANDING_URL:
            body = f'<a href="{_PRESS_HTML_URL}">Press release</a>'
            return SafeFetchResult(requested_url=url, status_code=200, links=[], body_html=body)
        return SafeFetchResult(requested_url=url, status_code=200, links=[])

    conn = CompanyIrConnector(verified_source=_KER, page_fetcher=fetcher)
    discovered, examined, with_candidates = asyncio.run(
        conn._hop_into_landing_pages(
            [SafeLink(url=_LANDING_URL, text="Half-Year 2026 Results", is_document=False)],
            _KER,
        )
    )
    assert examined == 1
    assert with_candidates == 1
    assert discovered and discovered[0].url == _PRESS_HTML_URL


def test_hop_keywords_widen_report_vocabulary_with_press_vocabulary():
    """``_HOP_KEYWORDS`` is a strict, deduplicated superset of the report-only
    vocabulary — proves the widening is additive, never a replacement."""
    from app.services.sources.safe_web_fetcher import ANNUAL_REPORT_KEYWORDS, PRESS_KEYWORDS

    assert set(ANNUAL_REPORT_KEYWORDS).issubset(set(_HOP_KEYWORDS))
    assert set(PRESS_KEYWORDS).issubset(set(_HOP_KEYWORDS))
    assert len(_HOP_KEYWORDS) == len(set(_HOP_KEYWORDS))


# =========================================================================== #
# Problem 2 — grounded gap-attribution
# =========================================================================== #


def test_g1_english_issuer_evidence_no_translation_blame():
    """English issuer evidence + French domicile -> no translation blame: a
    claim blaming translation is stripped when no translation-related gap was
    ever recorded for this run."""
    text = "Analysis is limited because filings are only available in untranslated French."
    known_gaps = [
        "Company IR annual-reports page could not be safely fetched (blocked); "
        "annual-report links are not identified."
    ]
    assert ground_gap_text(text, known_gaps) == GROUNDING_FALLBACK_MESSAGE


def test_g2_genuinely_translation_blocked_source_allows_translation_explanation():
    """A genuinely translation-blocked relevant source -> translation
    explanation allowed: the claim survives unchanged when the run's own
    known_gaps recorded a compatible translation cause."""
    text = "Evidence is thin because the relevant filing requires translation."
    known_gaps = ["Primary source requires translation before it can be used."]
    assert ground_gap_text(text, known_gaps) == text


def test_g3_traversal_depth_exhausted_allows_traversal_explanation():
    """Traversal depth exhausted -> traversal explanation allowed."""
    text = "No further page was discovered after the bounded traversal was exhausted."
    known_gaps = [
        "Company IR index page(s) were fetched and 3 child result-page "
        "candidate(s) were followed one hop deeper, but no further document "
        "or page was discovered there (child result-page hop attempted, no candidate)."
    ]
    assert ground_gap_text(text, known_gaps) == text


def test_g4_bot_protection_allows_bot_explanation():
    """Bot protection -> bot explanation allowed."""
    text = "The issuer's page returned a bot protection challenge page."
    known_gaps = [
        "Company IR source fetch was blocked (bot protection / access denied) "
        "— annual report links could not be evaluated."
    ]
    assert ground_gap_text(text, known_gaps) == text


def test_g5_unknown_cause_falls_back_to_generic_insufficient_evidence_wording():
    """Unknown cause -> generic insufficient-evidence wording only, and a gap
    item asserting NO specific cause at all is never touched."""
    causal_text = "Evidence is thin because the document could not be extracted."
    assert ground_gap_text(causal_text, known_gaps=None) == GROUNDING_FALLBACK_MESSAGE
    assert ground_gap_text(causal_text, known_gaps=[]) == GROUNDING_FALLBACK_MESSAGE

    plain_text = "Evidence remains thin for this company at this time."
    assert ground_gap_text(plain_text, known_gaps=None) == plain_text
    assert ground_gap_text(plain_text, known_gaps=[]) == plain_text


def test_g6_check_and_sanitize_grounds_risks_or_gaps_end_to_end():
    """Integration proof: the citation checker actually applies grounding to
    a council agent's ``risks_or_gaps`` items — the exact defect shape (an
    MC-style ungrounded translation claim reaching the report)."""
    output = CouncilAgentOutput(
        agent_name="source_quality_critic",
        status="completed",
        summary="Evidence review.",
        risks_or_gaps=[
            AgentRiskGap(
                item="Evidence remains thin because the issuer's filings are untranslated French.",
                citation_ids=[],
                severity="medium",
            ),
            AgentRiskGap(item="Evidence remains generally limited for this company.", citation_ids=[]),
        ],
    )
    sanitized, issues = check_and_sanitize(output, evidence_ids=set(), known_gaps=[])
    assert sanitized.risks_or_gaps[0].item == GROUNDING_FALLBACK_MESSAGE
    assert sanitized.risks_or_gaps[1].item == "Evidence remains generally limited for this company."
    assert any("ungrounded causal gap-attribution" in i for i in issues)


def test_g7_check_and_sanitize_keeps_grounded_claim_end_to_end():
    """The same integration path keeps a claim unchanged when its cause IS
    grounded in the run's known_gaps."""
    output = CouncilAgentOutput(
        agent_name="source_quality_critic",
        status="completed",
        summary="Evidence review.",
        risks_or_gaps=[
            AgentRiskGap(
                item="The issuer's page returned a bot protection challenge page.",
                citation_ids=[],
            ),
        ],
    )
    known_gaps = [
        "Company IR source fetch was blocked (bot protection / access denied) "
        "— annual report links could not be evaluated."
    ]
    sanitized, issues = check_and_sanitize(output, evidence_ids=set(), known_gaps=known_gaps)
    assert sanitized.risks_or_gaps[0].item == (
        "The issuer's page returned a bot protection challenge page."
    )
    assert not any("ungrounded causal gap-attribution" in i for i in issues)
