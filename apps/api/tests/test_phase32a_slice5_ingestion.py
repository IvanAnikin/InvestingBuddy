"""
Phase 32A Slice 5, part 3b — WIRING deep primary-document ingestion into the
source-connector / council path, behind the master flag
(``primary_document_ingestion_enabled``), with an aggregate ingestion budget +
telemetry.

Fully OFFLINE and deterministic: every fetch is a hand-built fake httpx client
or an injected fake extractor; DNS is an injected fake ``resolver`` (no real
name resolution); every PDF is built in-code. No network, no LLM, no DB.

Covers:
  * flag OFF → the deep branch is inert; the Phase 29B.2 shallow path is
    unchanged and no artifacts are threaded out (OFF byte-identical gate);
  * ``live_primary_document_extractor`` (B1): fetch + pdfplumber tables + stricter
    validation → an artifact with deep excerpts (page/table location) and at least
    one validated fact from a table;
  * ``resolve_ip`` blocks a DNS-rebinding fixture (fake resolver → private IP):
    honest gap, and the document body is NEVER fetched;
  * connector deep path (B2, flag ON): rich T1 excerpts + a validated
    ``company_ir_financial_fact`` (with table location), metadata-only index stays
    a reference, artifacts threaded OUT through ``collect_company_source_evidence``;
  * multi-doc bounded by ``max_docs_per_issuer`` (B2);
  * AGGREGATE budget exhaustion records an honest gap and stops further fetches
    (B3);
  * scanned / no-text PDF → metadata_only, no fabricated fact;
  * evidence-pack integration (B4): deep excerpts flow as CATEGORY_PRIMARY_DOCUMENT
    at T1, the Task-3a floor/cap applies when the flag is on, SEC/XBRL financial
    facts remain and deep excerpts supplement without duplicating.
"""

from __future__ import annotations

import asyncio
import socket
from typing import Any

from app.core.config import Settings
from app.services.llm.evidence_budget import (
    CATEGORY_FINANCIAL_FACT,
    CATEGORY_PRIMARY_DOCUMENT,
    apply_evidence_budget,
    evidence_category,
)
from app.services.llm.schemas import (
    TIER_T1_PRIMARY_FILING,
    TIER_T2_REGULATOR_OR_GOV,
    TIER_T5_API_AGGREGATOR,
    EvidencePack,
)
from app.services.llm.schemas import (
    EvidenceItem as CouncilEvidenceItem,
)
from app.services.sources.company_evidence import collect_company_source_evidence
from app.services.sources.connector_base import CompanyContext, QueryContext
from app.services.sources.connectors.company_ir import (
    CompanyIrConnector,
    PrimaryDocumentArtifact,
)
from app.services.sources.document_fetcher import safe_fetch_document
from app.services.sources.extracted_fact_validator import (
    VALIDATION_VALIDATED,
    IssuerContext,
    validate_extracted_facts,
)
from app.services.sources.live_fetchers import live_primary_document_extractor
from app.services.sources.primary_document_extractor import (
    STATUS_EXTRACTED,
    STATUS_METADATA_ONLY,
    extract_primary_document,
)
from app.services.sources.primary_fact_parser import FIELD_REVENUE, parse_primary_facts
from app.services.sources.safe_web_fetcher import SafeFetchResult, SafeLink
from app.services.sources.taxonomy import T1_PRIMARY_FILING
from app.services.sources.verified_issuer_sources import get_verified_issuer_source
from tests.helpers.pdf_fixtures import make_pdf, make_pdf_no_text, make_pdf_with_table

_FORBIDDEN = (
    "buy", "sell", "hold", "watch", "price target", "fair value",
    "intrinsic value", "upside", "downside",
)


def _has_forbidden(text: str) -> bool:
    low = (text or "").lower()
    return any(term in low for term in _FORBIDDEN)


def _cfg(**over: Any) -> Settings:
    base: dict[str, Any] = dict(
        source_connector_enabled=True,
        primary_document_ingestion_enabled=True,
        source_connector_max_items_per_source=20,
    )
    base.update(over)
    return Settings(**base)


def _q() -> QueryContext:
    return QueryContext(max_items=20)


ISSUER = IssuerContext(company_name="Compagnie Financiere Richemont SA", ticker="CFR")

# A ruled-grid table with a currency+scale header cell and a period header row so a
# clean revenue/net-income fact validates end-to-end (deterministic, offline).
CFR_TABLE_ROWS = [
    ["EUR million", "2024", "2023"],
    ["Revenue", "20,616", "19,182"],
    ["Net income", "2,357", "2,101"],
]


# --------------------------------------------------------------------------- #
# Fake httpx client (no real network) — mirrors the Phase 29B.2 test style.
# --------------------------------------------------------------------------- #


class _FakeStream:
    def __init__(self, *, status_code=200, headers=None, body=b"", is_redirect=False, raise_exc=None):
        self.status_code = status_code
        self.headers = headers or {}
        self.is_redirect = is_redirect
        self._body = body
        self._raise = raise_exc

    async def __aenter__(self):
        if self._raise is not None:
            raise self._raise
        return self

    async def __aexit__(self, *a):
        return False

    async def aiter_bytes(self):
        for i in range(0, max(1, len(self._body)), 1024):
            yield self._body[i : i + 1024]


class _FakeClient:
    def __init__(self, script, **kw):
        self._script = list(script)
        self._i = 0
        self.kw = kw

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def stream(self, method, url):
        item = self._script[min(self._i, len(self._script) - 1)]
        self._i += 1
        return item


def _patch_httpx(monkeypatch, script):
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda **kw: _FakeClient(script, **kw))


class _ExplodingClient:
    """An httpx.AsyncClient stand-in that fails the test if it is ever used."""

    def __init__(self, **kw):
        raise AssertionError("httpx must not be constructed when the fetch is blocked")


def _resolver_returning(ip: str):
    def _resolve(host, *a, **k):
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (ip, 443))]

    return _resolve


# --------------------------------------------------------------------------- #
# Fake fetchers / extractors for the connector DI path.
# --------------------------------------------------------------------------- #


def _page_fetcher(links: list[SafeLink]):
    async def _fetch(url, *, allowed_domains, keywords, fallback_keywords=()):
        return SafeFetchResult(requested_url=url, status_code=200, links=list(links))

    return _fetch


def _cfr_links(n: int = 1) -> list[SafeLink]:
    return [
        SafeLink(
            url=f"https://www.richemont.com/reports/ar{2024 - i}.pdf",
            text=f"Annual Report {2024 - i}",
            is_document=True,
        )
        for i in range(n)
    ]


def _deep_extractor(bytes_by_url: dict[str, bytes], *, cfg: Settings, calls: list[str] | None = None):
    """A deep extractor that runs the REAL extraction + validation on fixture bytes.

    Mirrors ``live_primary_document_extractor`` without any network: it looks the
    document bytes up by URL, runs ``extract_primary_document`` +
    ``validate_extracted_facts`` and returns a ``PrimaryDocumentArtifact``.
    """

    async def _extract(url, *, allowed_domains, title_hint=None, original_language=None, issuer_context=None):
        if calls is not None:
            calls.append(url)
        raw = bytes_by_url.get(url)
        if raw is None:
            return PrimaryDocumentArtifact(
                source_url=url,
                document_type="pdf",
                status="extraction_failed",
                source_gaps=[],
            )
        ext = extract_primary_document(raw, document_type="pdf", cfg=cfg)
        facts = (
            validate_extracted_facts(ext, issuer_context=issuer_context or IssuerContext(), cfg=cfg)
            if ext.status == STATUS_EXTRACTED
            else []
        )
        return PrimaryDocumentArtifact(
            source_url=url,
            document_type="pdf",
            title=title_hint,
            status=ext.status,
            extraction=ext,
            validated_facts=facts,
            fetch_ms=1,
            extraction_ms=1,
        )

    return _extract


def _shallow_extractor():
    """A Phase 29B.2 shallow bundle extractor (no deep tables)."""
    from app.services.sources.connectors.company_ir import PrimaryDocumentBundle
    from app.services.sources.document_text_extractor import extract_document_text

    async def _extract(url, *, allowed_domains, title_hint=None, original_language=None):
        ext = extract_document_text(
            make_pdf(["Richemont Annual Report 2024. Revenue: 20,616 million euros (EUR)."]),
            document_type="pdf",
            source_url=url,
        )
        return PrimaryDocumentBundle(
            source_url=url, document_type="pdf", extraction=ext, facts=parse_primary_facts(ext)
        )

    return _extract


class _Clock:
    def __init__(self, values: list[float]):
        self._values = values
        self._i = 0

    def __call__(self) -> float:
        v = self._values[min(self._i, len(self._values) - 1)]
        self._i += 1
        return v


# =========================================================================== #
# 1. OFF byte-identical: the deep branch is inert; shallow path unchanged.
# =========================================================================== #


def test_1_flag_off_uses_shallow_path_no_artifacts():
    # No deep extractor injected (master flag off in production) → Phase 29B.2
    # shallow path runs and produces its excerpt/fact, and NO artifacts leak out.
    collected = asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_page_fetcher(_cfr_links(1)),
            document_extractor=_shallow_extractor(),
            cfg=_cfg(primary_document_ingestion_enabled=False),
        )
    )
    types = {i.source_type for i in collected.evidence_items}
    assert "company_ir_annual_report_excerpt" in types
    # OFF path never threads deep artifacts out.
    assert collected.primary_document_artifacts == []
    # No deep table-derived excerpt marker (shallow path has no table locations).
    assert all("table=" not in " ".join(i.provenance) for i in collected.evidence_items)


def test_2_connector_deep_branch_off_when_not_injected():
    # With no deep extractor injected, the connector never populates artifacts.
    conn = CompanyIrConnector(
        verified_source=get_verified_issuer_source("CFR", "SW"),
        page_fetcher=_page_fetcher(_cfr_links(1)),
    )
    asyncio.run(conn.fetch_filings(CompanyContext(ticker="CFR", exchange="SW"), _q()))
    assert conn.collected_primary_document_artifacts == []


# =========================================================================== #
# 2. B1 — live_primary_document_extractor (real fetch, fake httpx + resolver).
# =========================================================================== #


def test_3_live_deep_extractor_yields_excerpts_and_validated_fact(monkeypatch):
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=200, headers={"content-type": "application/pdf"}, body=make_pdf_with_table(CFR_TABLE_ROWS))],
    )
    artifact = asyncio.run(
        live_primary_document_extractor(
            "https://www.richemont.com/reports/ar2024.pdf",
            allowed_domains=("richemont.com",),
            title_hint="Annual Report 2024",
            issuer_context=ISSUER,
            cfg=_cfg(),
            resolver=_resolver_returning("93.184.216.34"),
        )
    )
    assert artifact.status == STATUS_EXTRACTED
    assert artifact.extraction is not None and artifact.extraction.tables  # deep tables
    assert artifact.extraction.excerpts  # deep excerpts (page located)
    validated = [f for f in artifact.validated_facts if f.validation_status == VALIDATION_VALIDATED]
    assert validated, "expected at least one validated fact from a table"
    rev = next(f for f in validated if f.label == FIELD_REVENUE and f.period == "2024")
    assert rev.value_numeric == 20616.0
    assert rev.table_location == "p1:t0"  # table location preserved
    assert rev.currency == "EUR" and rev.scale == "million"
    # No secret in the stored URL; artifact is timestamped.
    assert "api_token" not in artifact.source_url
    assert artifact.retrieved_at is not None
    assert artifact.fetch_ms is not None and artifact.extraction_ms is not None


def test_4_resolve_ip_blocks_rebinding_and_never_fetches_body(monkeypatch):
    # Fake resolver returns a PRIVATE ip → the fetch is blocked BEFORE any request;
    # httpx must never be constructed.
    monkeypatch.setattr("httpx.AsyncClient", _ExplodingClient)
    artifact = asyncio.run(
        live_primary_document_extractor(
            "https://www.richemont.com/reports/ar2024.pdf",
            allowed_domains=("richemont.com",),
            issuer_context=ISSUER,
            cfg=_cfg(),
            resolver=_resolver_returning("10.0.0.5"),
        )
    )
    assert artifact.status != STATUS_EXTRACTED
    assert artifact.extraction is None
    assert artifact.validated_facts == []
    assert artifact.source_gaps  # honest gap, no fabricated document


def test_5_live_deep_extractor_scanned_pdf_is_metadata_only(monkeypatch):
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=200, headers={"content-type": "application/pdf"}, body=make_pdf_no_text())],
    )
    artifact = asyncio.run(
        live_primary_document_extractor(
            "https://www.richemont.com/reports/ar2024.pdf",
            allowed_domains=("richemont.com",),
            issuer_context=ISSUER,
            cfg=_cfg(),
            resolver=_resolver_returning("93.184.216.34"),
        )
    )
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.validated_facts == []  # never a fabricated fact
    assert artifact.extraction is not None and not artifact.extraction.has_content


def test_6_live_deep_extractor_non_pdf_body_degrades_honestly(monkeypatch):
    # A .pdf link that actually serves an HTML error page (octet-stream) → the
    # %PDF magic guard degrades to metadata_only, no parser fed a non-PDF blob.
    _patch_httpx(
        monkeypatch,
        [_FakeStream(status_code=200, headers={"content-type": "application/octet-stream"}, body=b"<html>404 not found</html>")],
    )
    artifact = asyncio.run(
        live_primary_document_extractor(
            "https://www.richemont.com/reports/ar2024.pdf",
            allowed_domains=("richemont.com",),
            issuer_context=ISSUER,
            cfg=_cfg(),
            resolver=_resolver_returning("93.184.216.34"),
        )
    )
    assert artifact.status == STATUS_METADATA_ONLY
    assert artifact.validated_facts == []
    assert any("not a valid PDF" in g.message for g in artifact.source_gaps)


def test_7_safe_fetch_document_resolve_ip_default_off_is_unchanged():
    # resolve_ip defaults OFF: a would-be-rejecting resolver is never consulted, so
    # existing callers keep byte-for-byte behaviour (http scheme still blocked).
    r = asyncio.run(
        safe_fetch_document(
            "http://www.richemont.com/x.pdf",
            allowed_domains=("richemont.com",),
            resolver=_resolver_returning("10.0.0.5"),
        )
    )
    assert r.blocked and "non-https" in (r.error or "")


# =========================================================================== #
# 3. B2 — connector deep path via collect_company_source_evidence.
# =========================================================================== #


def _collect_deep(bytes_by_url, *, links, cfg=None, calls=None):
    cfg = cfg or _cfg()
    return asyncio.run(
        collect_company_source_evidence(
            company=CompanyContext(ticker="CFR", exchange="SW", country="Switzerland"),
            source_ids=["company_ir"],
            ir_page_fetcher=_page_fetcher(links),
            primary_document_extractor=_deep_extractor(bytes_by_url, cfg=cfg, calls=calls),
            cfg=cfg,
        )
    )


def test_8_deep_path_emits_excerpts_facts_and_keeps_metadata_reference():
    url = "https://www.richemont.com/reports/ar2024.pdf"
    collected = _collect_deep({url: make_pdf_with_table(CFR_TABLE_ROWS)}, links=_cfr_links(1))

    types = {i.source_type for i in collected.evidence_items}
    assert "company_ir_financial_fact" in types  # validated fact
    assert "company_ir_annual_report_excerpt" in types  # deep excerpt

    # A validated fact item carries the structured PrimaryFactRef + table location.
    facts = [i for i in collected.evidence_items if i.source_type == "company_ir_financial_fact"]
    assert facts and all(i.primary_fact is not None for i in facts)
    assert any("table=" in " ".join(i.provenance) for i in facts)
    # Deep excerpts carry a page location in their provenance.
    excerpts = [i for i in collected.evidence_items if i.source_type == "company_ir_annual_report_excerpt"]
    assert any("page=" in " ".join(i.provenance) for i in excerpts)

    # Metadata-only annual-reports index stays a reference (never promoted).
    assert any(
        i.source_type == "company_ir_annual_reports_index" and i.data_quality == "metadata_only"
        for i in collected.evidence_items
    )
    # Artifacts threaded OUT for a later persistence task.
    assert len(collected.primary_document_artifacts) == 1
    art = collected.primary_document_artifacts[0]
    assert art.status == STATUS_EXTRACTED and art.validated_facts
    # No forbidden recommendation/valuation vocabulary anywhere.
    import json

    assert not _has_forbidden(json.dumps([i.model_dump(mode="json") for i in collected.evidence_items]))


def test_9_deep_path_bounded_by_max_docs_per_issuer():
    urls = [f"https://www.richemont.com/reports/ar{2024 - i}.pdf" for i in range(3)]
    bytes_by_url = {u: make_pdf_with_table(CFR_TABLE_ROWS) for u in urls}
    calls: list[str] = []
    cfg = _cfg(primary_document_max_docs_per_issuer=2)
    collected = _collect_deep(bytes_by_url, links=_cfr_links(3), cfg=cfg, calls=calls)
    # Only 2 documents fetched despite 3 discovered links.
    assert len(calls) == 2
    assert len(collected.primary_document_artifacts) == 2


def test_10_aggregate_budget_exhaustion_records_gap_and_stops(monkeypatch):
    # Drive the connector directly so the budget clock is deterministic.
    urls = [f"https://www.richemont.com/reports/ar{2024 - i}.pdf" for i in range(3)]
    bytes_by_url = {u: make_pdf_with_table(CFR_TABLE_ROWS) for u in urls}
    calls: list[str] = []
    cfg = _cfg(primary_document_max_docs_per_issuer=3)
    # start=0, doc1 check=0 (<10 → fetch), doc2 check=100 (>=10 → exhausted, stop).
    conn = CompanyIrConnector(
        verified_source=get_verified_issuer_source("CFR", "SW"),
        page_fetcher=_page_fetcher(_cfr_links(3)),
        primary_document_extractor=_deep_extractor(bytes_by_url, cfg=cfg, calls=calls),
        max_docs_per_issuer=3,
        ingestion_budget_seconds=10.0,
        clock=_Clock([0.0, 0.0, 100.0, 100.0]),
    )
    res = asyncio.run(conn.fetch_filings(CompanyContext(ticker="CFR", exchange="SW"), _q()))
    assert len(calls) == 1  # further fetches stopped
    assert len(conn.collected_primary_document_artifacts) == 1
    assert any("ingestion_budget_exhausted" in g.message for g in res.source_gaps)


def test_11_scanned_pdf_deep_path_no_fabricated_fact():
    url = "https://www.richemont.com/reports/ar2024.pdf"
    collected = _collect_deep({url: make_pdf_no_text()}, links=_cfr_links(1))
    types = {i.source_type for i in collected.evidence_items}
    assert "company_ir_financial_fact" not in types
    assert "company_ir_annual_report_excerpt" not in types
    # The metadata index survives + an honest gap is present.
    assert any(i.source_type == "company_ir_annual_reports_index" for i in collected.evidence_items)
    assert any("could not be extracted" in g.message or "scanned" in g.message.lower() for g in collected.source_gaps)
    # An artifact is still returned (metadata_only), with no facts.
    assert collected.primary_document_artifacts
    assert collected.primary_document_artifacts[0].validated_facts == []


def test_12_deep_evidence_items_are_t1_primary_filing():
    url = "https://www.richemont.com/reports/ar2024.pdf"
    collected = _collect_deep({url: make_pdf_with_table(CFR_TABLE_ROWS)}, links=_cfr_links(1))
    doc_items = [
        i for i in collected.evidence_items
        if i.source_type in ("company_ir_annual_report_excerpt", "company_ir_financial_fact")
    ]
    assert doc_items
    for it in doc_items:
        assert it.content_source_tier == T1_PRIMARY_FILING
        assert "api_token" not in (it.url or "")


# =========================================================================== #
# 4. B4 — evidence-pack integration (category, floor/cap, SEC supplement).
# =========================================================================== #


def _item(id, *, tier, source_type, excerpt="x", title="t", data_quality=None, fields=None):
    return CouncilEvidenceItem(
        id=id,
        source_tier=tier,
        source_type=source_type,
        content_tier=tier,
        transport_tier=tier,
        title=title,
        excerpt=excerpt,
        data_quality=data_quality,
        fields_supported=fields or [],
    )


def test_13_deep_excerpt_categorizes_as_primary_document_at_t1():
    it = _item("E1", tier=TIER_T1_PRIMARY_FILING, source_type="company_ir_annual_report_excerpt")
    assert evidence_category(it) == CATEGORY_PRIMARY_DOCUMENT


def test_14_primary_document_floor_and_cap_apply_only_when_flag_on():
    # 8 deep primary-document excerpts + 1 material news item.
    items = [
        _item(f"E{i}", tier=TIER_T1_PRIMARY_FILING, source_type="company_ir_annual_report_excerpt", title=f"doc{i}")
        for i in range(8)
    ]
    items.append(_item("N1", tier=TIER_T5_API_AGGREGATOR, source_type="news_article", title="news", fields=["catalyst"]))
    pack = EvidencePack(evidence_items=items)

    on = apply_evidence_budget(
        pack,
        max_items=20,
        cfg=_cfg(
            llm_council_evidence_budgets_enabled=True,
            primary_document_evidence_cap=6,
            primary_document_evidence_floor=1,
        ),
    )
    kept = [evidence_category(i) for i in on.evidence_items]
    assert kept.count(CATEGORY_PRIMARY_DOCUMENT) == 6  # capped when flag ON

    off = apply_evidence_budget(
        pack,
        max_items=20,
        cfg=_cfg(
            llm_council_evidence_budgets_enabled=True,
            primary_document_ingestion_enabled=False,
        ),
    )
    kept_off = [evidence_category(i) for i in off.evidence_items]
    assert kept_off.count(CATEGORY_PRIMARY_DOCUMENT) == 8  # uncapped when flag OFF


def test_15_sec_facts_remain_and_deep_excerpts_supplement_without_duplicating():
    # AAPL-like: SEC/XBRL financial facts + deep annual-report excerpts + one deep
    # validated fact. All distinct → nothing collapses; both categories survive.
    sec = [
        _item(f"S{i}", tier=TIER_T2_REGULATOR_OR_GOV, source_type="sec_financial_statement", title=f"sec{i}", excerpt=f"revenue {i}")
        for i in range(3)
    ]
    deep_excerpts = [
        _item(f"D{i}", tier=TIER_T1_PRIMARY_FILING, source_type="company_ir_annual_report_excerpt", title=f"ar{i}", excerpt=f"segment {i}")
        for i in range(3)
    ]
    deep_fact = _item("DF1", tier=TIER_T1_PRIMARY_FILING, source_type="company_ir_financial_fact", title="ar-fact", excerpt="net income 2024")
    pack = EvidencePack(evidence_items=sec + deep_excerpts + [deep_fact])

    out = apply_evidence_budget(
        pack, max_items=20, cfg=_cfg(llm_council_evidence_budgets_enabled=True)
    )
    cats = [evidence_category(i) for i in out.evidence_items]
    # SEC + deep fact both financial facts survive; deep excerpts supplement.
    assert cats.count(CATEGORY_FINANCIAL_FACT) == 4  # 3 SEC + 1 deep validated fact
    assert cats.count(CATEGORY_PRIMARY_DOCUMENT) == 3
    # No duplication: every input survived (all distinct).
    assert len(out.evidence_items) == 7
