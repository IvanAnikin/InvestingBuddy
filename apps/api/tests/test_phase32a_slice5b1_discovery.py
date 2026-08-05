"""
Phase 32A Slice 5B.1 — bounded, non-browser document discovery.

Fully OFFLINE and deterministic: every fixture is an in-code HTML/XML literal,
nothing here opens a socket, imports httpx, or runs a browser. The module under
test is pure parsing of bytes a caller already fetched.

Covers:
  A. the four in-page strategies (anchors / json_ld / next_data / embedded_json)
     including the core 5B fix — a JS-gated page with ZERO matching anchors;
  B. the caller-driven feed strategy + its XXE guard;
  C. the SSRF-relevant invariants re-applied to every strategy (https-only,
     allowlist, no private/internal hosts);
  D. identity/dedup, classification, ranking, caps and never-raises behaviour;
  E. ``find_json_endpoints`` (reported, never fetched).
"""

from __future__ import annotations

import json

from app.services.sources.document_discovery import (
    DEFAULT_MAX_DOCUMENTS,
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_OTHER,
    DOC_KIND_PRESENTATION,
    DOC_KIND_RESULTS_RELEASE,
    STRATEGY_ANCHORS,
    STRATEGY_EMBEDDED_JSON,
    STRATEGY_FEED,
    STRATEGY_JSON_LD,
    STRATEGY_NEXT_DATA,
    classify_document_kind,
    discover_documents,
    discover_from_anchors,
    discover_from_embedded_json,
    discover_from_feed,
    discover_from_json_ld,
    discover_from_next_data,
    document_identity,
    find_json_endpoints,
)

BASE_URL = "https://www.lux-issuer.example.com/en/investors/results"
ALLOWED: tuple[str, ...] = ("lux-issuer.example.com",)

AR_2024 = "https://www.lux-issuer.example.com/docs/annual-report-2024.pdf"


def _urls(docs) -> list[str]:  # noqa: ANN001 - list[DiscoveredDocument]
    return [d.url for d in docs]


# =========================================================================== #
# A. In-page strategies
# =========================================================================== #


def test_anchors_still_find_a_plain_annual_report_link():
    """Slice 5A behaviour is preserved: a plain <a href> is still discovered."""
    html = b"""
    <html><body>
      <a href="/docs/annual-report-2024.pdf">Annual Report 2024</a>
      <a href="/careers">Careers</a>
      <a href="#top">Back to top</a>
      <a href="mailto:ir@lux-issuer.example.com">Email IR</a>
    </body></html>
    """.decode()
    docs = discover_from_anchors(html, base_url=BASE_URL, allowed_domains=ALLOWED, max_documents=10)
    assert len(docs) == 1
    doc = docs[0]
    assert doc.url == AR_2024
    assert doc.strategy == STRATEGY_ANCHORS
    assert doc.doc_kind == DOC_KIND_ANNUAL_REPORT
    assert doc.is_document is True
    assert doc.identity == AR_2024.lower()


def test_json_ld_block_yields_a_pdf_url():
    html = b"""
    <html><head>
    <script type="application/ld+json">
    {"@context":"https://schema.org","@type":"DigitalDocument",
     "name":"Annual Report 2024",
     "url":"https://www.lux-issuer.example.com/docs/annual-report-2024.pdf"}
    </script>
    </head><body><div id="app"></div></body></html>
    """.decode()
    docs = discover_from_json_ld(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == 1
    assert docs[0].url == AR_2024
    assert docs[0].strategy == STRATEGY_JSON_LD
    assert docs[0].title == "Annual Report 2024"
    assert docs[0].doc_kind == DOC_KIND_ANNUAL_REPORT


def test_json_ld_tolerates_a_list_and_a_comment_wrapper():
    html = b"""
    <script type="application/ld+json">/* schema */
    [{"@type":"Report","headline":"Annual Report 2024",
      "contentUrl":"/docs/annual-report-2024.pdf"}]
    </script>
    """.decode()
    docs = discover_from_json_ld(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]
    assert docs[0].title == "Annual Report 2024"


def test_next_data_finds_a_pdf_that_has_no_anchor_at_all():
    """The core Slice 5B fix: a JS-gated SPA page with ZERO matching anchors."""
    html = b"""
    <html><body>
      <div id="__next"></div>
      <a href="/en/investors">Investors</a>
      <script id="__NEXT_DATA__" type="application/json">
      {"props":{"pageProps":{"documents":[
        {"title":"Annual Report 2024",
         "file":{"url":"/docs/annual-report-2024.pdf","size":123}},
        {"title":"Careers brochure","file":{"url":"/docs/careers.pdf"}}
      ]}}}
      </script>
    </body></html>
    """.decode()
    assert discover_from_anchors(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []
    docs = discover_from_next_data(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == 2  # both are .pdf documents on an allowlisted host
    annual = [d for d in docs if d.doc_kind == DOC_KIND_ANNUAL_REPORT]
    assert len(annual) == 1
    assert annual[0].url == AR_2024
    assert annual[0].strategy == STRATEGY_NEXT_DATA
    assert annual[0].title == "Annual Report 2024"


def test_window_initial_state_variant_is_parsed():
    html = b"""
    <html><body><script>
      window.__INITIAL_STATE__ = {"reports":[
        {"name":"Annual Report 2024",
         "href":"https://www.lux-issuer.example.com/docs/annual-report-2024.pdf"}
      ]};
    </script></body></html>
    """.decode()
    docs = discover_from_next_data(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]
    assert docs[0].strategy == STRATEGY_NEXT_DATA


def test_truncated_hydration_payload_falls_back_to_the_regex_sweep():
    # A hydration blob cut off mid-object (or minified beyond JSON validity) is
    # exactly the real-world SPA case — it must still yield the document.
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"docs":[{"title":"Annual Report 2024",'
        '"url":"/docs/annual-report-2024.pdf"'
    )
    docs = discover_from_next_data(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]
    assert docs[0].strategy == STRATEGY_NEXT_DATA


def test_embedded_script_json_yields_a_pdf():
    html = b"""
    <html><body><script>
      var reportData = {"items":[{"label":"Annual Report 2024",
                                  "path":"/docs/annual-report-2024.pdf"}]};
      renderReports(reportData);
    </script></body></html>
    """.decode()
    docs = discover_from_embedded_json(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]
    assert docs[0].strategy == STRATEGY_EMBEDDED_JSON
    assert docs[0].title == "Annual Report 2024"


def test_embedded_script_regex_fallback_when_json_is_not_parseable():
    # Unquoted keys + single quotes: valid JS, invalid JSON -> regex sweep.
    html = b"""
    <html><body><script>
      renderDocs([{title: 'Annual Report 2024', url: '/docs/annual-report-2024.pdf'},]);
    </script></body></html>
    """.decode()
    docs = discover_from_embedded_json(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]
    assert docs[0].strategy == STRATEGY_EMBEDDED_JSON
    assert docs[0].doc_kind == DOC_KIND_ANNUAL_REPORT


def test_embedded_script_regex_fallback_handles_absolute_urls():
    html = (
        "<script>var s = 'x'; loadPdf(https://www.lux-issuer.example.com"
        "/docs/annual-report-2024.pdf);</script>"
    )
    docs = discover_from_embedded_json(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]


def test_scripts_without_pdf_are_skipped_by_embedded_json():
    html = b"""<script>var cfg = {"tracking":"/analytics/collect"};</script>""".decode()
    assert discover_from_embedded_json(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []


# =========================================================================== #
# B. Feeds (XML text the CALLER fetched) + XXE guard
# =========================================================================== #


def test_rss_feed_yields_document_links():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <title>Investor news</title>
      <link>https://www.lux-issuer.example.com/en/investors</link>
      <item>
        <title>Annual Report 2024</title>
        <link>https://www.lux-issuer.example.com/news/annual-report-2024</link>
        <enclosure url="https://www.lux-issuer.example.com/docs/annual-report-2024.pdf"
                   type="application/pdf"/>
        <pubDate>Mon, 01 Apr 2024 08:00:00 GMT</pubDate>
      </item>
      <item>
        <title>Store opening</title>
        <link>https://www.lux-issuer.example.com/news/store-opening</link>
      </item>
    </channel></rss>
    """.decode()
    docs = discover_from_feed(xml, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert AR_2024 in _urls(docs)
    pdf = [d for d in docs if d.is_document]
    assert len(pdf) == 1
    assert pdf[0].strategy == STRATEGY_FEED
    assert pdf[0].doc_kind == DOC_KIND_ANNUAL_REPORT
    assert pdf[0].published_hint == "Mon, 01 Apr 2024 08:00:00 GMT"
    # The non-document news item is not a candidate.
    assert all("store-opening" not in u for u in _urls(docs))


def test_sitemap_loc_yields_document_links():
    xml = b"""<?xml version="1.0" encoding="UTF-8"?>
    <urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">
      <url>
        <loc>https://www.lux-issuer.example.com/docs/annual-report-2024.pdf</loc>
        <lastmod>2024-04-01</lastmod>
      </url>
      <url><loc>https://www.lux-issuer.example.com/careers</loc></url>
    </urlset>
    """.decode()
    docs = discover_from_feed(xml, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]
    assert docs[0].published_hint == "2024-04-01"
    assert docs[0].strategy == STRATEGY_FEED


def test_feed_with_doctype_entity_is_rejected_xxe_guard():
    xml = b"""<?xml version="1.0"?>
    <!DOCTYPE rss [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>
    <rss><channel><item>
      <title>Annual Report 2024</title>
      <link>https://www.lux-issuer.example.com/docs/annual-report-2024.pdf</link>
    </item></channel></rss>
    """.decode()
    assert discover_from_feed(xml, base_url=BASE_URL, allowed_domains=ALLOWED) == []


def test_feed_with_billion_laughs_entity_block_is_rejected():
    xml = (
        '<?xml version="1.0"?><!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;">]><rss><channel><item><title>&lol2;</title>'
        "<link>https://www.lux-issuer.example.com/docs/annual-report-2024.pdf</link>"
        "</item></channel></rss>"
    )
    assert discover_from_feed(xml, base_url=BASE_URL, allowed_domains=ALLOWED) == []


def test_malformed_and_empty_xml_return_empty_without_raising():
    assert discover_from_feed("", base_url=BASE_URL, allowed_domains=ALLOWED) == []
    assert discover_from_feed("<rss><item>", base_url=BASE_URL, allowed_domains=ALLOWED) == []
    assert discover_from_feed("not xml at all", base_url=BASE_URL, allowed_domains=ALLOWED) == []


# =========================================================================== #
# C. Safety invariants re-applied to EVERY strategy
# =========================================================================== #


_OFF_ALLOWLIST_PDF = "https://cdn.attacker.example.net/docs/annual-report-2024.pdf"


def test_off_allowlist_host_is_dropped_in_every_strategy():
    anchor_html = f'<a href="{_OFF_ALLOWLIST_PDF}">Annual Report 2024</a>'
    ld_html = (
        '<script type="application/ld+json">'
        f'{{"name":"Annual Report 2024","url":"{_OFF_ALLOWLIST_PDF}"}}</script>'
    )
    next_html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        f'{{"docs":[{{"title":"Annual Report 2024","url":"{_OFF_ALLOWLIST_PDF}"}}]}}'
        "</script>"
    )
    embedded_html = f'<script>var d = {{"url":"{_OFF_ALLOWLIST_PDF}"}};</script>'
    feed_xml = (
        "<rss><channel><item><title>Annual Report 2024</title>"
        f"<link>{_OFF_ALLOWLIST_PDF}</link></item></channel></rss>"
    )

    assert discover_from_anchors(anchor_html, base_url=BASE_URL, allowed_domains=ALLOWED) == []
    assert discover_from_json_ld(ld_html, base_url=BASE_URL, allowed_domains=ALLOWED) == []
    assert discover_from_next_data(next_html, base_url=BASE_URL, allowed_domains=ALLOWED) == []
    assert (
        discover_from_embedded_json(embedded_html, base_url=BASE_URL, allowed_domains=ALLOWED) == []
    )
    assert discover_from_feed(feed_xml, base_url=BASE_URL, allowed_domains=ALLOWED) == []
    assert (
        discover_documents(
            anchor_html + ld_html + next_html + embedded_html,
            base_url=BASE_URL,
            allowed_domains=ALLOWED,
        )
        == []
    )


def test_http_scheme_is_dropped():
    insecure = "http://www.lux-issuer.example.com/docs/annual-report-2024.pdf"
    html = (
        f'<a href="{insecure}">Annual Report 2024</a>'
        '<script id="__NEXT_DATA__" type="application/json">'
        f'{{"docs":["{insecure}"]}}</script>'
    )
    assert discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []


def test_private_and_internal_hosts_are_dropped_even_if_allowlisted():
    # Belt-and-braces: even a (mis)configured allowlist cannot reach localhost
    # or an IP literal — is_safe_public_host still rejects them.
    for host, domains in (
        ("localhost", ("localhost",)),
        ("10.0.0.5", ("10.0.0.5",)),
        ("metadata.google.internal", ("google.internal",)),
    ):
        url = f"https://{host}/docs/annual-report-2024.pdf"
        html = (
            f'<a href="{url}">Annual Report 2024</a>'
            '<script id="__NEXT_DATA__" type="application/json">'
            f'{{"docs":["{url}"]}}</script>'
            f'<script>var d = {{"url":"{url}"}};</script>'
        )
        base = f"https://{host}/investors"
        assert discover_documents(html, base_url=base, allowed_domains=domains) == []
        assert (
            discover_from_feed(
                f"<rss><channel><item><title>Annual Report</title><link>{url}</link>"
                "</item></channel></rss>",
                base_url=base,
                allowed_domains=domains,
            )
            == []
        )


def test_javascript_and_data_urls_are_dropped():
    html = (
        '<a href="javascript:alert(1)">Annual Report 2024</a>'
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"docs":["data:application/pdf;base64,QQ==","javascript:void(0)"]}</script>'
    )
    assert discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []


def test_free_text_in_json_is_never_turned_into_a_url():
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"docs":[{"title":"Annual Report 2024","summary":"Our annual report is out"}]}'
        "</script>"
    )
    assert discover_from_next_data(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []


# =========================================================================== #
# D. Identity, dedup, classification, ranking, caps, never-raises
# =========================================================================== #


def test_document_identity_drops_the_entire_query_string():
    a = document_identity("https://www.lux-issuer.example.com/docs/ar.pdf?token=a")
    b = document_identity("https://www.lux-issuer.example.com/docs/ar.pdf?token=b")
    c = document_identity("https://www.lux-issuer.example.com/docs/ar.pdf?download=1")
    d = document_identity("https://WWW.Lux-Issuer.example.com/docs/ar.pdf#page=3")
    assert a == b == c == d == "https://www.lux-issuer.example.com/docs/ar.pdf"
    assert document_identity("https://www.lux-issuer.example.com/docs/") == (
        "https://www.lux-issuer.example.com/docs"
    )
    assert document_identity("") == ""


def test_signed_query_duplicates_collapse_to_one_document():
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"docs":['
        '{"title":"Annual Report 2024","url":"/docs/annual-report-2024.pdf?token=a"},'
        '{"title":"Annual Report 2024","url":"/docs/annual-report-2024.pdf?token=b"},'
        '{"title":"Annual Report 2024","url":"/docs/annual-report-2024.pdf?download=1"},'
        '{"title":"Annual Report 2024","url":"/docs/annual-report-2024.pdf?download=2"}'
        "]}</script>"
    )
    docs = discover_from_next_data(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == 1
    assert docs[0].identity == AR_2024.lower()
    # The persisted URL never carries the credential-bearing parameter.
    assert "token" not in docs[0].url


def test_same_document_found_by_two_strategies_appears_once():
    html = (
        f'<a href="{AR_2024}">Annual Report 2024</a>'
        '<script type="application/ld+json">'
        f'{{"name":"Annual Report 2024","url":"{AR_2024}"}}</script>'
        '<script id="__NEXT_DATA__" type="application/json">'
        f'{{"docs":["{AR_2024}"]}}</script>'
    )
    docs = discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == 1
    assert docs[0].strategy == STRATEGY_ANCHORS  # first strategy to find it wins


def test_classification_precedence():
    assert (
        classify_document_kind("Annual Report 2024", "/docs/ar-2024.pdf") == DOC_KIND_ANNUAL_REPORT
    )
    assert (
        classify_document_kind("Universal Registration Document", "/docs/urd.pdf")
        == DOC_KIND_ANNUAL_REPORT
    )
    # Annual beats presentation even in a mixed title.
    assert (
        classify_document_kind("Annual results presentation", "/docs/arp.pdf")
        == DOC_KIND_ANNUAL_REPORT
    )
    # Annual beats interim.
    assert (
        classify_document_kind("Annual report and interim update", "/docs/x.pdf")
        == DOC_KIND_ANNUAL_REPORT
    )
    assert (
        classify_document_kind("Half-year report 2024", "/docs/hy.pdf") == DOC_KIND_INTERIM_REPORT
    )
    assert classify_document_kind("Q1 trading update", "/docs/q1.pdf") == DOC_KIND_INTERIM_REPORT
    assert (
        classify_document_kind("Press release: store opening", "/news/pr.html")
        == DOC_KIND_RESULTS_RELEASE
    )
    assert (
        classify_document_kind("Investor presentation", "/docs/deck.pdf") == DOC_KIND_PRESENTATION
    )
    assert classify_document_kind("Careers brochure", "/docs/careers.pdf") == (DOC_KIND_OTHER)
    # A URL slug alone is enough (separator-normalised).
    assert (
        classify_document_kind("Download", "/docs/annual_report_2024.pdf") == DOC_KIND_ANNUAL_REPORT
    )


def test_ranking_puts_annual_report_ahead_of_presentation():
    html = (
        '<a href="/docs/results-presentation-2024.pdf">Results presentation 2024</a>'
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"docs":[{"title":"Annual Report 2024",'
        '"url":"/docs/annual-report-2024.pdf"}]}</script>'
    )
    docs = discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == 2
    assert docs[0].doc_kind == DOC_KIND_ANNUAL_REPORT
    assert docs[0].url == AR_2024
    assert docs[1].doc_kind == DOC_KIND_PRESENTATION


def test_max_documents_cap_is_respected():
    items = ",".join(
        f'{{"title":"Annual Report {year}","url":"/docs/annual-report-{year}.pdf"}}'
        for year in range(2000, 2030)
    )
    html = f'<script id="__NEXT_DATA__" type="application/json">{{"docs":[{items}]}}</script>'
    assert (
        len(discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED))
        == DEFAULT_MAX_DOCUMENTS
    )
    assert (
        len(discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED, max_documents=3))
        == 3
    )
    assert (
        len(
            discover_from_next_data(
                html, base_url=BASE_URL, allowed_domains=ALLOWED, max_documents=2
            )
        )
        == 2
    )


def test_cfg_supplies_the_cap_when_present_and_falls_back_when_absent():
    class _Cfg:
        primary_document_max_discovery_candidates = 2

    items = ",".join(
        f'{{"title":"Annual Report {year}","url":"/docs/annual-report-{year}.pdf"}}'
        for year in range(2010, 2030)
    )
    html = f'<script id="__NEXT_DATA__" type="application/json">{{"docs":[{items}]}}</script>'
    assert (
        len(
            discover_documents(
                html,
                base_url=BASE_URL,
                allowed_domains=ALLOWED,
                cfg=_Cfg(),  # type: ignore[arg-type]
            )
        )
        == 2
    )
    # No cfg / no attribute -> module default, never a crash.
    assert (
        len(discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED))
        == DEFAULT_MAX_DOCUMENTS
    )


def test_strategy_selection_can_be_restricted():
    html = (
        f'<a href="{AR_2024}">Annual Report 2024</a>'
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"docs":["/docs/interim-report-2024.pdf"]}</script>'
    )
    only_next = discover_documents(
        html,
        base_url=BASE_URL,
        allowed_domains=ALLOWED,
        strategies=(STRATEGY_NEXT_DATA,),
    )
    assert len(only_next) == 1
    assert only_next[0].strategy == STRATEGY_NEXT_DATA
    assert only_next[0].doc_kind == DOC_KIND_INTERIM_REPORT


def test_malformed_html_and_json_return_empty_and_never_raise():
    for html in (
        "",
        "<<<>>> not really html {",
        "<html><body><a href=",
        '<script type="application/ld+json">{not json at all}</script>',
        '<script id="__NEXT_DATA__" type="application/json">{"a":</script>',
        "<script>var x = {broken;</script>",
        "<script>" + "{" * 500 + "</script>",
    ):
        assert discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []
        assert find_json_endpoints(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []


def test_deeply_nested_json_terminates_and_is_not_walked_past_the_depth_cap():
    payload = json.dumps({"url": "/docs/annual-report-2024.pdf"})
    for _ in range(300):
        payload = '{"child":' + payload + "}"
    html = f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'
    # Terminates (depth cap 8) and the buried URL is NOT reached.
    assert discover_from_next_data(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []


def test_huge_json_array_is_bounded_and_terminates():
    urls = [f"/docs/annual-report-{i}.pdf" for i in range(20_000)]
    html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        + json.dumps({"docs": urls})
        + "</script>"
    )
    docs = discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == DEFAULT_MAX_DOCUMENTS


def test_unterminated_script_block_still_parses():
    html = (
        '<html><body><script id="__NEXT_DATA__" type="application/json">'
        '{"docs":["/docs/annual-report-2024.pdf"]}'
    )
    docs = discover_from_next_data(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert _urls(docs) == [AR_2024]


# =========================================================================== #
# E. find_json_endpoints — reported for the caller, never fetched here
# =========================================================================== #


def test_find_json_endpoints_returns_allowlisted_same_origin_urls_only():
    html = b"""
    <html><head>
      <link rel="preload" href="/data/reports.json"/>
      <a href="https://other.example.org/api/data.json">third party</a>
      <script>
        fetch("/api/documents/list").then(r => r.json());
        fetch("https://cdn.attacker.example.net/api/list.json");
        fetch("https://www.lux-issuer.example.com/api/press/list");
        var img = "/img/logo.svg";
      </script>
    </head><body></body></html>
    """.decode()
    endpoints = find_json_endpoints(
        html, base_url=BASE_URL, allowed_domains=ALLOWED, max_endpoints=10
    )
    assert set(endpoints) == {
        "https://www.lux-issuer.example.com/data/reports.json",
        "https://www.lux-issuer.example.com/api/documents/list",
        "https://www.lux-issuer.example.com/api/press/list",
    }
    assert all(e.startswith("https://www.lux-issuer.example.com/") for e in endpoints)


def test_find_json_endpoints_drops_other_subdomains_and_respects_the_cap():
    # A sibling host inside the allowlist is still a DIFFERENT origin.
    html = (
        '<link href="https://cdn.lux-issuer.example.com/data/reports.json"/>'
        '<link href="/data/a.json"/><link href="/data/b.json"/>'
        '<link href="/data/c.json"/>'
    )
    endpoints = find_json_endpoints(
        html, base_url=BASE_URL, allowed_domains=ALLOWED, max_endpoints=2
    )
    assert len(endpoints) == 2
    assert all("cdn.lux-issuer" not in e for e in endpoints)


def test_find_json_endpoints_is_empty_when_nothing_matches():
    html = '<a href="/en/investors">Investors</a><script>var x = 1;</script>'
    assert find_json_endpoints(html, base_url=BASE_URL, allowed_domains=ALLOWED) == []


# =========================================================================== #
# F. Credential hygiene on a discovered URL (security review L3)
# =========================================================================== #


def test_userinfo_credentials_are_stripped_from_a_discovered_url():
    """``user:pass@`` must never survive onto an EvidenceItem URL."""
    html = (
        '<a href="https://alice:s3cret@www.lux-issuer.example.com/docs/'
        'annual-report-2024.pdf">Annual Report 2024</a>'
    )
    docs = discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == 1
    url = docs[0].url
    assert "@" not in url
    assert "alice" not in url and "s3cret" not in url
    assert url == AR_2024


def test_signed_query_secrets_are_still_stripped_from_a_discovered_url():
    html = (
        '<a href="/docs/annual-report-2024.pdf?api_token=SHOULD_NOT_PERSIST">'
        "Annual Report 2024</a>"
    )
    docs = discover_documents(html, base_url=BASE_URL, allowed_domains=ALLOWED)
    assert len(docs) == 1
    assert "SHOULD_NOT_PERSIST" not in docs[0].url


# =========================================================================== #
# G. Connector wiring — the config knobs must actually reach discovery
#    (PR-review blocker 4: they were documented but dead).
# =========================================================================== #


def _connector(cfg):  # noqa: ANN001 - duck-typed settings stand-in
    from app.services.sources.connectors.company_ir import CompanyIrConnector
    from app.services.sources.verified_issuer_sources import VerifiedIssuerSource

    verified = VerifiedIssuerSource(
        ticker="LUX",
        exchange="PA",
        company_name="Lux Issuer SA",
        country="France",
        official_website_domain="lux-issuer.example.com",
        allowed_domains=ALLOWED,
        investor_relations_url=BASE_URL,
    )
    return CompanyIrConnector(verified_source=verified, cfg=cfg)


def _fetched(html: str):
    from app.services.sources.safe_web_fetcher import SafeFetchResult

    return SafeFetchResult(requested_url=BASE_URL, final_url=BASE_URL, body_html=html)


class _DiscoveryCfg:
    """Only the two knobs discovery reads (everything else is irrelevant here)."""

    def __init__(self, *, candidates: int, strategies: str) -> None:
        self.primary_document_max_discovery_candidates = candidates
        self.primary_document_discovery_strategies = strategies


_HYDRATION_HTML = (
    f'<a href="{AR_2024}">Annual Report 2024</a>'
    '<script id="__NEXT_DATA__" type="application/json">'
    '{"docs":['
    '{"title":"Annual Report 2023","url":"/docs/annual-report-2023.pdf"},'
    '{"title":"Annual Report 2022","url":"/docs/annual-report-2022.pdf"},'
    '{"title":"Annual Report 2021","url":"/docs/annual-report-2021.pdf"},'
    '{"title":"Annual Report 2020","url":"/docs/annual-report-2020.pdf"}'
    "]}</script>"
)


def test_connector_honours_the_configured_discovery_candidate_cap():
    connector = _connector(
        _DiscoveryCfg(candidates=2, strategies="anchors,json_ld,next_data,embedded_json")
    )
    links = connector._discover_deep_targets(_fetched(_HYDRATION_HTML))
    # One anchor was already known; discovery may only ADD up to the cap total.
    assert len(links) == 2, [ln.url for ln in links]


def test_connector_honours_a_restricted_discovery_strategy_list():
    connector = _connector(_DiscoveryCfg(candidates=12, strategies=" ANCHORS , "))
    links = connector._discover_deep_targets(_fetched(_HYDRATION_HTML))
    # With only ``anchors`` enabled the hydration payload is never parsed.
    assert [ln.url for ln in links] == [AR_2024]


def test_connector_falls_back_to_the_default_strategies_on_a_junk_setting():
    connector = _connector(_DiscoveryCfg(candidates=12, strategies="nope,,   "))
    links = connector._discover_deep_targets(_fetched(_HYDRATION_HTML))
    # An unusable setting must not silently switch discovery off.
    assert len(links) > 1
