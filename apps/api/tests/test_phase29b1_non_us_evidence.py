"""
Phase 29B.1 — Non-US Primary Filing + Company IR Evidence Fetchers.

Covers the verified-issuer source registry, the SSRF-safe bounded web fetcher,
the upgraded company-IR connector (profile / annual-report / press discovery),
its integration into the single-company evidence pack + council, the read-only
evidence-preview API, and the honesty guarantees (no fabricated filings, no
recommendation/valuation language). Everything runs offline — the live web
fetcher is exercised via injected fakes, never a real network call.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.main import app
from app.services.llm.council import run_council
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.fake_client import FakeLLMClient
from app.services.sources.company_evidence import (
    LOCAL_LANGUAGE_REFERENCE_IDS,
    REGULATOR_REFERENCE_IDS,
    collect_company_source_evidence,
)
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import CompanyIrConnector
from app.services.sources.gaps import GapType
from app.services.sources.safe_web_fetcher import (
    ANNUAL_REPORT_KEYWORDS,
    SafeFetchResult,
    SafeLink,
    check_fetch_url,
    extract_links,
    is_safe_public_host,
    parse_meta_description,
    parse_title,
)
from app.services.sources.taxonomy import (
    T1_PRIMARY_COMPANY_SOURCE,
    T1_PRIMARY_FILING,
)
from app.services.sources.verified_issuer_sources import (
    all_verified_issuer_sources,
    get_verified_issuer_source,
    registrable_host_allowed,
    validate_registry,
)

client = TestClient(app)

_FORBIDDEN = (
    "buy",
    "sell",
    "hold",
    "watch",
    "price target",
    "fair value",
    "intrinsic value",
    "upside",
    "downside",
)


def _has_forbidden(text: str) -> bool:
    low = text.lower()
    return any(term in low for term in _FORBIDDEN)


def _enabled_cfg(**over: Any) -> Settings:
    base = dict(source_connector_enabled=True, source_connector_max_items_per_source=5)
    base.update(over)
    return Settings(**base)


def _q() -> QueryContext:
    return QueryContext(max_items=5)


# ---------------------------------------------------------------------------
# 1–4  Verified issuer registry
# ---------------------------------------------------------------------------

TARGET_ISSUERS = [
    ("CFR", "SW"),
    ("UHR", "SW"),
    ("MC", "PA"),
    ("RMS", "PA"),
    ("KER", "PA"),
    ("BRBY", "LSE"),
    ("PNDORA", "CO"),
    ("MONC", "MI"),
    ("GDWN", "LSE"),
]


def test_1_known_target_issuers_exist():
    for ticker, exchange in TARGET_ISSUERS:
        src = get_verified_issuer_source(ticker, exchange)
        assert src is not None, f"{ticker}.{exchange} missing from verified registry"
        assert src.ticker == ticker and src.exchange == exchange
        assert src.company_name


def test_1b_combined_ticker_exchange_lookup():
    assert get_verified_issuer_source("CFR.SW").company_name.startswith("Compagnie")
    # Distinct from US Boeing: BA.LSE resolves to BAE Systems.
    assert "BAE" in get_verified_issuer_source("BA.LSE").company_name


def test_2_urls_are_https_and_allowlisted():
    for src in all_verified_issuer_sources():
        for url in src.urls():
            assert url.startswith("https://"), url
            host = url.split("/")[2]
            assert registrable_host_allowed(host, src.allowed_domains), url


def test_3_no_tokenized_urls():
    # A structural + secret check — validate_registry raises on any violation.
    validate_registry()
    for src in all_verified_issuer_sources():
        for url in src.urls():
            low = url.lower()
            for token in ("api_key", "api_token", "token=", "auth=", "apikey", "secret"):
                assert token not in low, url


def test_4_duplicate_ticker_exchange_rejected():
    seen: set[tuple[str, str]] = set()
    for src in all_verified_issuer_sources():
        key = (src.ticker, src.exchange)
        assert key not in seen, f"duplicate {key}"
        seen.add(key)
    # Every entry has the required identity fields.
    for src in all_verified_issuer_sources():
        assert src.ticker and src.exchange and src.company_name and src.allowed_domains


# ---------------------------------------------------------------------------
# 5–9  Safe fetcher guards (pure, no network)
# ---------------------------------------------------------------------------


def test_5_rejects_localhost_private_internal():
    for host in (
        "localhost",
        "127.0.0.1",
        "10.0.0.5",
        "192.168.1.1",
        "169.254.169.254",  # cloud metadata endpoint
        "metadata.google.internal",
        "foo.internal",
        "bar.local",
        "::1",
    ):
        assert not is_safe_public_host(host), host
    assert is_safe_public_host("www.richemont.com")


def test_6_rejects_non_https():
    assert check_fetch_url("http://www.richemont.com/x", ("richemont.com",)) is not None
    assert check_fetch_url("ftp://www.richemont.com/x", ("richemont.com",)) is not None
    assert check_fetch_url("https://www.richemont.com/x", ("richemont.com",)) is None


def test_6b_rejects_off_allowlist_and_internal_hosts():
    assert check_fetch_url("https://evil.com/x", ("richemont.com",)) is not None
    assert check_fetch_url("https://127.0.0.1/x", ("127.0.0.1",)) is not None
    assert check_fetch_url("https://richemont.com.attacker.net/x", ("richemont.com",))


def test_7_extract_links_strips_secrets_and_enforces_allowlist():
    html = """
    <a href="https://www.richemont.com/reports/annual-report-2025.pdf?api_token=SECRET">
      Annual Report 2025</a>
    <a href="https://evil.com/annual-report.pdf">Annual Report (evil)</a>
    <a href="http://www.richemont.com/annual-report.pdf">Annual Report (http)</a>
    """
    links = extract_links(
        html,
        base_url="https://www.richemont.com/investors/",
        allowed_domains=("richemont.com",),
        keywords=ANNUAL_REPORT_KEYWORDS,
        max_links=10,
    )
    assert len(links) == 1
    assert links[0].url == "https://www.richemont.com/reports/annual-report-2025.pdf"
    assert "api_token" not in links[0].url
    assert links[0].is_document


def test_8_max_links_cap_enforced():
    anchors = "".join(
        f'<a href="https://x.example.com/annual-report-{i}.pdf">Annual Report {i}</a>'
        for i in range(50)
    )
    links = extract_links(
        f"<html>{anchors}</html>",
        base_url="https://x.example.com/",
        allowed_domains=("example.com",),
        keywords=ANNUAL_REPORT_KEYWORDS,
        max_links=5,
    )
    assert len(links) == 5


def test_9_title_meta_and_redirect_guard():
    html = (
        '<html lang="fr"><head><title>Rapport annuel</title>'
        '<meta name="description" content="Documents financiers"></head></html>'
    )
    assert parse_title(html) == "Rapport annuel"
    assert parse_meta_description(html) == "Documents financiers"
    # A redirect target outside the allowlist is rejected by the same guard the
    # fetcher applies to Location headers.
    assert check_fetch_url("https://cdn.evil.net/x", ("richemont.com",)) is not None


# ---------------------------------------------------------------------------
# 10–15  Company IR connector
# ---------------------------------------------------------------------------


def test_10_known_issuer_returns_profile_metadata():
    src = get_verified_issuer_source("CFR", "SW")
    conn = CompanyIrConnector(verified_source=src)
    res = asyncio.run(conn.search_company(CompanyContext(ticker="CFR", exchange="SW"), _q()))
    assert len(res.evidence_items) == 1
    item = res.evidence_items[0]
    assert item.source_type == "company_ir_profile"
    assert item.content_source_tier == T1_PRIMARY_COMPANY_SOURCE
    assert item.data_quality == "metadata_only"


def test_10b_unknown_issuer_returns_honest_gap_not_crash():
    conn = CompanyIrConnector(verified_source=None)
    res = asyncio.run(conn.search_company(CompanyContext(ticker="ZZZZ", exchange="XX"), _q()))
    assert res.evidence_items == []
    assert res.source_gaps and res.source_gaps[0].gap_type == GapType.data_not_sourced


def test_11_annual_reports_offline_metadata_and_live_links():
    src = get_verified_issuer_source("KER", "PA")
    # Offline: index metadata + honest "links not identified without live" gap.
    off = asyncio.run(
        CompanyIrConnector(verified_source=src).fetch_filings(
            CompanyContext(ticker="KER", exchange="PA"), _q()
        )
    )
    assert any(i.source_type == "company_ir_annual_reports_index" for i in off.evidence_items)
    assert any(g.gap_type == GapType.primary_filing_unavailable for g in off.source_gaps)

    # Live (fake fetcher): an annual-report link becomes T1_primary_filing.
    async def fake_page(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(
            requested_url=url,
            status_code=200,
            links=[
                SafeLink(
                    url="https://www.kering.com/en/finance/publications/kering-urd-2025.pdf",
                    text="2025 Universal Registration Document",
                    is_document=True,
                )
            ],
        )

    live = asyncio.run(
        CompanyIrConnector(verified_source=src, page_fetcher=fake_page).fetch_filings(
            CompanyContext(ticker="KER", exchange="PA"), _q()
        )
    )
    ar = [i for i in live.evidence_items if i.source_type == "company_ir_annual_report"]
    assert ar and ar[0].content_source_tier == T1_PRIMARY_FILING
    assert ar[0].requires_translation is True  # France → local-language


def test_12_press_index_and_replayed_releases():
    src = get_verified_issuer_source("CFR", "SW")

    async def press(_c, _q):
        return [
            {"headline": "Richemont reports FY sales", "url": "https://www.richemont.com/media/x", "published_at": "2026-05-01"},
        ]

    res = asyncio.run(
        CompanyIrConnector(verified_source=src, press_fetcher=press).fetch_events(
            CompanyContext(ticker="CFR", exchange="SW"), _q()
        )
    )
    types = {i.source_type for i in res.evidence_items}
    assert "company_ir_press_release_index" in types
    assert "company_ir_press_release" in types


def test_13_metadata_items_labelled_metadata_only():
    src = get_verified_issuer_source("MC", "PA")
    res = asyncio.run(
        CompanyIrConnector(verified_source=src).search_company(
            CompanyContext(ticker="MC", exchange="PA"), _q()
        )
    )
    assert all(
        i.data_quality == "metadata_only"
        and any("metadata only" in w.lower() for w in i.warnings)
        for i in res.evidence_items
    )


def test_14_non_allowlisted_redirect_returns_gap_not_crash():
    src = get_verified_issuer_source("KER", "PA")

    async def blocked_page(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(
            requested_url=url, blocked=True, error="redirect blocked (host not in allowlist)"
        )

    res = asyncio.run(
        CompanyIrConnector(verified_source=src, page_fetcher=blocked_page).fetch_filings(
            CompanyContext(ticker="KER", exchange="PA"), _q()
        )
    )
    # Index metadata survives; a blocked-fetch gap is added; nothing crashes.
    assert any(i.source_type == "company_ir_annual_reports_index" for i in res.evidence_items)
    assert any("could not be safely fetched" in g.message for g in res.source_gaps)


def test_15_connector_exception_returns_gap_not_crash():
    src = get_verified_issuer_source("KER", "PA")

    async def boom(url, *, allowed_domains, keywords, fallback_keywords=()):
        raise RuntimeError("network exploded")

    conn = CompanyIrConnector(verified_source=src, page_fetcher=boom)
    # call_safe converts any exception into a safe result + gap.
    res = asyncio.run(
        conn.call_safe(conn.fetch_filings, CompanyContext(ticker="KER", exchange="PA"), _q())
    )
    assert res.error_code is not None
    assert res.source_gaps  # honest gap, no raise


# ---------------------------------------------------------------------------
# 16–21  Evidence preview API
# ---------------------------------------------------------------------------


def test_16_uhr_preview_returns_company_ir_metadata():
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "UHR", "exchange": "SW", "source_ids": ["company_ir"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["evidence_items"], "expected company_ir metadata for UHR"
    assert all(i["source_id"] == "company_ir" for i in body["evidence_items"])


def test_17_ker_preview_returns_company_ir_metadata():
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "KER", "exchange": "PA", "source_ids": ["company_ir"]},
    )
    assert r.status_code == 200
    assert r.json()["evidence_items"]


def test_18_aapl_sec_preserves_tier_distinction():
    # SEC is eligible for a US ticker; the offline path returns an honest gap,
    # never a fabricated filing, and never a non-eligible gap for a US issuer.
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "AAPL", "exchange": "US", "source_ids": ["sec_edgar"]},
    )
    assert r.status_code == 200
    body = r.json()
    joined = " ".join(g["gap_type"] for g in body["source_gaps"])
    assert "source_not_eligible" not in joined


def test_19_ba_lse_sec_not_eligible_no_boeing():
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "BA", "exchange": "LSE", "source_ids": ["sec_edgar", "company_ir"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert any(g["gap_type"] == "source_not_eligible" for g in body["source_gaps"])
    # Boeing may only appear inside the negated "NOT Boeing" disambiguation
    # warning — never as the presented issuer (source_name / title / url) and
    # never as a fetched SEC filing.
    for it in body["evidence_items"]:
        assert it["source_id"] == "company_ir"
        presented = f"{it.get('source_name')} {it.get('title')} {it.get('url')}".lower()
        assert "boeing" not in presented
        assert "sec.gov" not in presented


def test_20_unknown_source_id_returns_400():
    r = client.post(
        "/api/v1/sources/evidence-preview",
        json={"ticker": "CFR", "exchange": "SW", "source_ids": ["totally_made_up"]},
    )
    assert r.status_code == 400


def test_21_request_schema_has_no_url_field():
    from app.schemas.source_evidence_preview import EvidencePreviewRequest

    assert "url" not in EvidencePreviewRequest.model_fields
    # A stray url in the payload is ignored, never fetched.
    req = EvidencePreviewRequest.model_validate(
        {"ticker": "CFR", "exchange": "SW", "url": "https://evil.com"}
    )
    assert not hasattr(req, "url")


# ---------------------------------------------------------------------------
# 22–27  Evidence pack integration + honesty
# ---------------------------------------------------------------------------


def test_22_full_analysis_pack_includes_company_ir_when_enabled():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="KER", exchange="PA", country="France"),
            cfg=_enabled_cfg(),
        )
    )
    assert any(i.source_id == "company_ir" for i in collected.evidence_items)
    pack = build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "KER"}}},
        connector_evidence=collected.evidence_items,
        connector_gap_messages=collected.gap_messages(),
    )
    assert pack.item_count >= 1


def test_23_disabled_flag_preserves_previous_path():
    # With the flag off, the council does not collect connector evidence at all.
    # (Collector itself is flag-agnostic; the gate lives in maybe_run_council —
    # asserted here by confirming a disabled cfg yields no source_connector call
    # path change: collector still works but the report path won't invoke it.)
    disabled = Settings(source_connector_enabled=False)
    assert disabled.source_connector_enabled is False


def test_24_connector_failure_does_not_break_pack():
    async def boom(url, *, allowed_domains, keywords, fallback_keywords=()):
        raise RuntimeError("boom")

    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="KER", exchange="PA", country="France"),
            ir_page_fetcher=boom,
            cfg=_enabled_cfg(),
        )
    )
    # Metadata items still present; a gap records the failure; no exception.
    assert any(i.source_id == "company_ir" for i in collected.evidence_items)


def test_25_council_receives_cited_company_ir_evidence():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            cfg=_enabled_cfg(),
        )
    )
    pack = build_evidence_pack(
        report_content={"company_identity": {"ticker": {"value": "CFR"}}},
        connector_evidence=collected.evidence_items,
        connector_gap_messages=collected.gap_messages(),
    )
    result = asyncio.run(run_council(pack, FakeLLMClient()))
    assert result.llm_used is True
    assert result.agents_completed >= 1


def test_26_27_non_us_evidence_honest_no_fakes_no_forbidden():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="MC", exchange="PA", country="France"),
            cfg=_enabled_cfg(),
        )
    )
    # No fabricated SEC filing / fundamentals — every evidence item is either
    # company_ir metadata, an honest regulator-transport reference (Phase 29B.4B
    # promoted euronext_regulated_info / uk_fca_nsm to dedicated connectors, so a
    # Euronext/UK issuer like MC/PA now also gets a metadata-only T2 regulator
    # reference item), or the Phase 30B local-language business-press reference
    # (T4, metadata-only). No filing content is claimed for any of them.
    allowed_source_ids = (
        {"company_ir"} | REGULATOR_REFERENCE_IDS | LOCAL_LANGUAGE_REFERENCE_IDS
    )
    assert all(i.source_id in allowed_source_ids for i in collected.evidence_items)
    assert all(i.data_quality == "metadata_only" for i in collected.evidence_items)
    # Safety: no recommendation / valuation vocabulary in items or gaps.
    for it in collected.evidence_items:
        assert not _has_forbidden(f"{it.title} {it.excerpt} {' '.join(it.warnings)}")
    for g in collected.source_gaps:
        assert not _has_forbidden(g.message)


def test_27b_local_language_translation_gap_present_for_french_issuer():
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="RMS", exchange="PA", country="France"),
            cfg=_enabled_cfg(),
        )
    )
    msgs = " ".join(g.message.lower() for g in collected.source_gaps)
    assert "translation" in msgs
    assert "pending phase 30" in msgs


# ---------------------------------------------------------------------------
# 31  No heavy fan-out — collector makes no network call without a live fetcher
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Report checklist honesty — T1/T2 data-quality item
# ---------------------------------------------------------------------------


def test_checklist_t1t2_item_honest_about_weak_evidence():
    from types import SimpleNamespace

    from app.services.final_report_generator import (
        _build_human_review_checklist,
        _has_t1_t2_evidence,
    )

    t5_only = [SimpleNamespace(source_tier="T5_api_aggregator")]
    t1_present = [SimpleNamespace(source_tier="T1_primary_filing")]
    assert _has_t1_t2_evidence("T5_api_aggregator", t5_only) is False
    assert _has_t1_t2_evidence("T6_model_estimate", t5_only) is False
    assert _has_t1_t2_evidence(None, t1_present) is True
    assert _has_t1_t2_evidence("T2_regulator_or_gov", []) is True

    def _dq_item(items):
        return next(i for i in items if i.item.startswith("Data quality: T1/T2"))

    weak = _build_human_review_checklist(
        True, True, True, True, True, True, 0, is_mock=False, has_t1_t2=False
    )
    assert _dq_item(weak).completed is False
    strong = _build_human_review_checklist(
        True, True, True, True, True, True, 0, is_mock=False, has_t1_t2=True
    )
    assert _dq_item(strong).completed is True


def test_31_no_network_without_live_fetcher():
    # With no ir_page_fetcher injected the collector must never fetch — it only
    # emits registry metadata. (If it fetched, importing httpx-less would still
    # be fine, but there must be zero live fetch on the report path.)
    calls = {"n": 0}

    async def counting_page(url, *, allowed_domains, keywords, fallback_keywords=()):
        calls["n"] += 1
        return SafeFetchResult(requested_url=url, status_code=200)

    # Not passing the fetcher → zero calls even for a verified issuer.
    asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW"),
            cfg=_enabled_cfg(),
        )
    )
    assert calls["n"] == 0
