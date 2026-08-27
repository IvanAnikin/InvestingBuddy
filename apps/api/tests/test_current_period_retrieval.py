"""
Current-period acceptance — RETRIEVAL: reaching the issuer's newest reporting.

The private-use readiness campaign closed with PNDORA and CFR both showing
``Current = —`` on the final acceptance matrix, even though each issuer had
published an official current-period report that this system could fetch and
extract. Reproduced against the code at ``3b316ff``, the loss was entirely in
RETRIEVAL — three independent defects, none of which any unit test could see
because each exercised a piece rather than the path:

R1  **The current-period document was never DISCOVERED (Pandora).** Its Q2 2026
    interim report exists only inside a Next.js App Router streaming payload
    (``self.__next_f.push([1,"…"])``) — a sequence of JSON-encoded string
    FRAGMENTS of one logical stream. ``next_data`` looks for a hydration script
    id that App Router does not emit; ``embedded_json`` needs a balanced JSON
    literal inside one script body, and a value routinely spans pushes. The
    interim INDEX page was therefore selected as the current-period "document",
    and it carries no figures at all.

R2  **The current-period document was never a CANDIDATE (Richemont).** Its
    newest reporting is a quarterly SALES release
    (``…-fy27-q1-sales-en.pdf``, the quarter ended 30 June 2026), and
    ``ANNUAL_REPORT_KEYWORDS`` — which covers annual, full-year, half-year and
    "interim" wording — has no quarterly vocabulary at all. The reserve worked
    perfectly and had nothing to reserve a slot for.

R3  **The reserved slot was never REACHED.** ``_extract_primary_documents_deep``
    ranks ``max_docs_per_issuer + _MAX_THIN_FALLBACK_DOCS`` candidates but stops
    ingesting at ``max_docs_per_issuer`` once one document has real financial
    content. The reserve was applied at the END of the longer list, so the
    current-period document sat in slot five of five and was ranked, logged and
    never fetched.

Fixing R1-R3 exposed three ordering defects that had been masked by the
narrower candidate set, each of which chose the wrong document live:

R4  a ZIP archive named ``… (PAND-2025-12-31-en.zip)`` counted as a document,
    because the filename ends in a parenthesis;
R5  a news page headlined "Richemont publishes FY26 Annual Report" outranked
    the annual report PDF beside it (labelled merely "Download"), because the
    anchor-wording heuristic sat ahead of downloadability AND recency — the
    same term made Pandora's "Annual Report 2024" outrank "Annual Report 2025";
R6  one document reached the ranker under two spellings (the anchor's
    raw-space href and the discovery layer's percent-encoded form) and spent
    two of three bounded ingestion slots on itself.

Fully offline and deterministic: no network, no LLM, no Azure, no DB. Payload
shapes and URL forms are real; every figure is fixture data.
"""

from __future__ import annotations

import json

from app.services.sources.connectors.company_ir import (
    _INDEX_KEYWORDS,
    CompanyIrConnector,
    _is_supporting_material,
    _reserve_current_period,
)
from app.services.sources.document_discovery import (
    DEFAULT_STRATEGIES,
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    PERIOD_CLASS_CURRENT,
    STRATEGY_NEXT_FLIGHT,
    classify_document_kind,
    discover_documents,
    discover_from_next_flight,
    document_period_class,
    document_recency_hint,
)
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    CURRENT_PERIOD_KEYWORDS,
    SafeLink,
)

_ISSUER = "https://issuer.test/investor/reports"
_ALLOWED = ("issuer.test", "cdn.issuer.test")


# --------------------------------------------------------------------------- #
# Payload builders — the real App Router shape, not a simplification.
# --------------------------------------------------------------------------- #


def _flight_page(*chunks: str) -> str:
    """Wrap flight ``chunks`` the way Next.js App Router actually streams them.

    Each chunk is its own ``push`` of a JSON-encoded string, so a value that
    spans chunks is only readable once they are concatenated.
    """
    scripts = "".join(
        f"<script>self.__next_f.push([1,{json.dumps(chunk)}])</script>"
        for chunk in chunks
    )
    return f"<html><body><p>No anchors at all.</p>{scripts}</body></html>"


# =========================================================================== #
# R1 — the App Router streaming payload                                       #
# =========================================================================== #


def test_a_document_url_inside_a_flight_payload_is_discovered() -> None:
    html = _flight_page(
        '3:["$","div",null,{"blocks":[{"files":['
        '{"name":"Issuer Q2 2026 Interim Report",'
        '"url":"https://cdn.issuer.test/v1/static/Issuer Q2 2026 Interim Report"}'
        "]}]}]"
    )
    found = discover_from_next_flight(
        html,
        base_url=_ISSUER,
        allowed_domains=_ALLOWED,
        keywords=ANNUAL_REPORT_KEYWORDS,
    )
    assert [d.url for d in found] == [
        "https://cdn.issuer.test/v1/static/Issuer%20Q2%202026%20Interim%20Report"
    ]
    assert found[0].doc_kind == DOC_KIND_INTERIM_REPORT
    assert found[0].strategy == STRATEGY_NEXT_FLIGHT
    assert found[0].title == "Issuer Q2 2026 Interim Report"


def test_a_value_split_across_pushes_is_reassembled_before_scanning() -> None:
    """THE reason ``embedded_json`` cannot see this payload: no single script
    body contains a parseable literal."""
    html = _flight_page(
        '3:["$","div",null,{"files":[{"name":"Issuer H1 2026 Interim Report",',
        '"url":"https://issuer.test/reports/h1-2026-interim-report.pdf"}]}]',
    )
    found = discover_from_next_flight(
        html, base_url=_ISSUER, allowed_domains=_ALLOWED,
        keywords=ANNUAL_REPORT_KEYWORDS,
    )
    assert [d.url for d in found] == [
        "https://issuer.test/reports/h1-2026-interim-report.pdf"
    ]


def test_the_title_comes_from_the_urls_own_sibling_key() -> None:
    """Two documents in one list must not share the first one's title."""
    html = _flight_page(
        '{"files":['
        '{"name":"Issuer Annual Report 2025",'
        '"url":"https://issuer.test/a/annual-report-2025.pdf"},'
        '{"name":"Issuer H1 2026 Interim Report",'
        '"url":"https://issuer.test/a/h1-2026-interim-report.pdf"}'
        "]}"
    )
    found = discover_from_next_flight(
        html, base_url=_ISSUER, allowed_domains=_ALLOWED,
        keywords=ANNUAL_REPORT_KEYWORDS,
    )
    kinds = {d.title: d.doc_kind for d in found}
    assert kinds["Issuer Annual Report 2025"] == DOC_KIND_ANNUAL_REPORT
    assert kinds["Issuer H1 2026 Interim Report"] == DOC_KIND_INTERIM_REPORT


def test_the_flight_strategy_applies_the_same_url_guards() -> None:
    html = _flight_page(
        '{"files":['
        '{"name":"Annual Report 2025","url":"http://issuer.test/a/ar-2025.pdf"},'
        '{"name":"Annual Report 2025","url":"https://evil.test/a/ar-2025.pdf"},'
        '{"name":"Annual Report 2025","url":"https://127.0.0.1/a/ar-2025.pdf"},'
        '{"name":"Annual Report 2025",'
        '"url":"https://issuer.test/a/ar-2025.pdf?token=secret"}'
        "]}"
    )
    found = discover_from_next_flight(
        html, base_url=_ISSUER, allowed_domains=_ALLOWED,
        keywords=ANNUAL_REPORT_KEYWORDS,
    )
    urls = [d.url for d in found]
    assert urls == ["https://issuer.test/a/ar-2025.pdf"]
    assert "secret" not in urls[0]


def test_a_malformed_flight_payload_never_raises() -> None:
    for html in (
        "<script>self.__next_f.push([1,</script>",
        "<script>self.__next_f.push(</script>",
        '<script>self.__next_f.push(["not json at all)</script>',
        "",
    ):
        assert discover_from_next_flight(
            html, base_url=_ISSUER, allowed_domains=_ALLOWED,
            keywords=ANNUAL_REPORT_KEYWORDS,
        ) == []


def test_the_flight_strategy_is_on_by_default_and_bounded() -> None:
    assert STRATEGY_NEXT_FLIGHT in DEFAULT_STRATEGIES
    html = _flight_page(
        '{"files":['
        + ",".join(
            f'{{"name":"Annual Report {y}",'
            f'"url":"https://issuer.test/a/annual-report-{y}.pdf"}}'
            for y in range(1990, 2026)
        )
        + "]}"
    )
    found = discover_documents(
        html, base_url=_ISSUER, allowed_domains=_ALLOWED, max_documents=4
    )
    assert len(found) == 4


# =========================================================================== #
# R2 — quarterly vocabulary at depth 0                                        #
# =========================================================================== #


def test_a_quarterly_sales_release_is_a_candidate_at_depth_zero() -> None:
    """The real Richemont filename. Under ``ANNUAL_REPORT_KEYWORDS`` alone it
    matched nothing, so no current-period document existed to reserve."""
    url = (
        "https://issuer.test/media/ad-hoc-announcement-pursuant-to-art-53-lr-"
        "fy27-q1-sales-en.pdf"
    )
    html = f'<html><body><a href="{url}">Download</a></body></html>'

    assert discover_documents(
        html, base_url=_ISSUER, allowed_domains=_ALLOWED,
        keywords=ANNUAL_REPORT_KEYWORDS,
    ) == []
    found = discover_documents(
        html, base_url=_ISSUER, allowed_domains=_ALLOWED, keywords=_INDEX_KEYWORDS
    )
    assert [d.url for d in found] == [url]
    assert document_period_class(found[0].doc_kind) == PERIOD_CLASS_CURRENT
    assert document_recency_hint("", url) == (2027, 1)


def test_the_current_period_vocabulary_is_period_wording_only() -> None:
    """A boutique opening must not compete for the bounded candidate cap."""
    noise = "https://issuer.test/news/maison-opens-its-first-boutique-in-madrid/"
    assert discover_documents(
        f'<a href="{noise}">Read more</a>',
        base_url=_ISSUER, allowed_domains=_ALLOWED, keywords=_INDEX_KEYWORDS,
    ) == []
    assert set(ANNUAL_REPORT_KEYWORDS) <= set(_INDEX_KEYWORDS)
    assert set(CURRENT_PERIOD_KEYWORDS) <= set(_INDEX_KEYWORDS)


# =========================================================================== #
# R3 — the reserve must land where ingestion actually reaches                 #
# =========================================================================== #


def _link(url: str, text: str = "", is_document: bool = True) -> SafeLink:
    return SafeLink(url=url, text=text, is_document=is_document)


def _connector(links: "list[SafeLink] | None" = None, max_docs: int = 3) -> CompanyIrConnector:
    """A connector whose classification map is populated the way discovery does.

    ``_rank_deep_targets`` keys its primary rank on the DISCOVERY layer's own
    classification, so a ranking test that leaves that map empty is testing a
    state the live path never reaches.
    """
    connector = CompanyIrConnector(max_docs_per_issuer=max_docs)
    for link in links or []:
        connector._document_kinds[link.url] = (
            classify_document_kind(link.text or "", link.url),
            "anchors",
        )
    return connector


_ANNUALS = [
    _link(f"https://issuer.test/a/annual-report-{y}.pdf", "Annual Report")
    for y in (2026, 2025, 2024, 2023, 2022)
]
_QUARTER = _link("https://issuer.test/a/fy27-q1-sales-en.pdf", "Download")


def test_the_reserved_current_period_document_lands_inside_the_ingestion_cap() -> None:
    """THE R3 regression: ranked into slot five of five, never fetched."""
    links = [*_ANNUALS, _QUARTER]
    targets = _connector(links)._rank_deep_targets(links, limit=5, reserve_within=3)
    reached = targets[:3]
    assert any("fy27-q1-sales" in link.url for link in reached), [
        link.url for link in targets
    ]
    # The annual report is the deepest source and must not pay for the reserve.
    assert "annual-report-2026" in reached[0].url


def test_the_reserve_is_not_honoured_beyond_the_ingestion_cap() -> None:
    """Ranking five candidates while ingesting three must reserve within three.

    Asking for the reserve at the END of the longer list is exactly the defect:
    the document is selected and never fetched.
    """
    links = [*_ANNUALS, _QUARTER]
    connector = _connector(links)
    unbounded = connector._rank_deep_targets(links, limit=5)
    assert not any("fy27-q1-sales" in link.url for link in unbounded[:3])
    bounded = connector._rank_deep_targets(links, limit=5, reserve_within=3)
    assert any("fy27-q1-sales" in link.url for link in bounded[:3])


def test_the_reserve_never_costs_more_than_one_slot() -> None:
    links = [*_ANNUALS, _QUARTER]
    targets = _connector(links)._rank_deep_targets(links, limit=5, reserve_within=3)
    assert len(targets) == 5
    assert len([t for t in targets[:3] if "annual-report" in t.url]) == 2


def test_without_a_current_period_candidate_no_slot_is_reserved() -> None:
    targets = _connector(_ANNUALS)._rank_deep_targets(
        _ANNUALS, limit=5, reserve_within=3
    )
    assert all("annual-report" in t.url for t in targets[:3])


_REPORT = _link(
    "https://cdn.issuer.test/v1/static/Issuer Q2 2026 Interim Report",
    "Issuer Q2 2026 Interim Report",
)
_APPENDIX = _link(
    "https://cdn.issuer.test/v1/static/Appendix Company Announcement Q2 2026",
    "Appendix Company Announcement Q2 2026",
)
_DECK = _link(
    "https://cdn.issuer.test/v1/static/Investor Presentation Q2 2026",
    "Investor Presentation Q2 2026",
)


def test_the_reserve_prefers_the_report_over_results_day_material() -> None:
    """Everything published on results day carries the same period, so they all
    tie on recency; DOM order chose Pandora's appendix over its interim report.
    """
    def kind_of(link: SafeLink) -> str:
        return classify_document_kind(link.text or "", link.url)

    def recency(link: SafeLink) -> tuple[int, int]:
        return document_recency_hint(link.text or "", link.url)

    ordered = [
        _link("https://issuer.test/a/annual-report-2025.pdf", "Annual Report"),
        _link("https://issuer.test/a/annual-report-2024.pdf", "Annual Report"),
        _APPENDIX,
        _DECK,
        _REPORT,
    ]
    chosen = _reserve_current_period(ordered, cap=2, kind_of=kind_of, recency=recency)
    assert chosen[-1].url == _REPORT.url


def test_ranking_puts_the_report_ahead_of_its_results_day_bundle() -> None:
    """The reserve's tie-break is a second guarantee; the ORDER must be right
    too, or the reserve never fires because supporting material already fills
    the head."""
    links = [_APPENDIX, _DECK, _REPORT]
    targets = _connector(links)._rank_deep_targets(links, limit=3)
    assert targets[0].url == _REPORT.url


def test_supporting_material_is_recognised_generically() -> None:
    assert _is_supporting_material(_link("https://i.test/q2-2026-transcript.pdf"))
    assert _is_supporting_material(_link("https://i.test/x", "Pre-Q2 consensus"))
    assert not _is_supporting_material(
        _link("https://i.test/q2-2026-interim-report.pdf")
    )


# =========================================================================== #
# R4-R6 — the ordering defects the wider candidate set exposed                #
# =========================================================================== #


def test_an_archive_whose_name_ends_in_a_bracket_is_not_a_document() -> None:
    """The real Pandora XBRL package name."""
    html = (
        '<a href="https://cdn.issuer.test/v1/static/'
        'Annual Report 2025 XHTML (ISS-2025-12-31-en.zip)">Annual Report XHTML</a>'
    )
    found = discover_documents(
        html,
        base_url=_ISSUER,
        allowed_domains=_ALLOWED,
        keywords=_INDEX_KEYWORDS,
        document_domains=("cdn.issuer.test",),
    )
    assert [d.is_document for d in found] == [False]


def test_an_extensionless_cdn_report_is_still_a_document() -> None:
    """The guard above must not close the extension-less CDN path it sits on."""
    html = (
        '<a href="https://cdn.issuer.test/v1/static/Annual Report 2025">'
        "Download the full report</a>"
    )
    found = discover_documents(
        html,
        base_url=_ISSUER,
        allowed_domains=_ALLOWED,
        keywords=_INDEX_KEYWORDS,
        document_domains=("cdn.issuer.test",),
    )
    assert [d.is_document for d in found] == [True]


def test_the_document_outranks_the_page_announcing_it() -> None:
    connector = _connector(max_docs=1)
    targets = connector._rank_deep_targets(
        [
            _link(
                "https://issuer.test/news/issuer-publishes-fy26-annual-report/",
                "Issuer publishes FY26 Annual Report",
                is_document=False,
            ),
            _link("https://issuer.test/media/fy26-annual-report.pdf", "Download"),
        ]
    )
    assert targets[0].url.endswith("fy26-annual-report.pdf")


def test_the_newer_edition_outranks_better_anchor_wording() -> None:
    connector = _connector(max_docs=1)
    targets = connector._rank_deep_targets(
        [
            _link(
                "https://cdn.issuer.test/v1/static/Annual Report 2024",
                "Annual Report PDF",
            ),
            _link(
                "https://cdn.issuer.test/v1/static/Annual Report 2025",
                "Download the full report",
            ),
        ]
    )
    assert targets[0].url.endswith("Annual Report 2025")


def test_two_spellings_of_one_document_take_one_slot() -> None:
    connector = _connector(max_docs=3)
    targets = connector._rank_deep_targets(
        [
            _link(
                "https://cdn.issuer.test/v1/static/Annual Report 2025",
                "Download the full report",
            ),
            _link(
                "https://cdn.issuer.test/v1/static/Annual%20Report%202025",
                "Annual Report",
            ),
            _link("https://issuer.test/a/fy27-q1-sales-en.pdf", "Download"),
        ]
    )
    assert len(targets) == 2
    assert any("fy27-q1-sales" in t.url for t in targets)
