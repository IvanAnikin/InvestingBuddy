"""V3.21 slice 21.7 — research coverage index, report block, obscurity component (ADR-114).

The Edison sitemap fixture (``tests/fixtures/edison_sitemap.xml``) is 30 real ``<url>``
entries cut from the live file fetched once on 2026-11-22 (18 ``/equity/`` pages with and
without a ``BMC-`` id, 6 ``/research/`` notes, and 6 other pages); ``edison_robots.txt``
is the live robots.txt of the same day. No test reaches the network.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.ext.compiler import compiles

from app.core.config import Settings
from app.db.base import Base
from app.models.company import Company
from app.models.research_coverage import ResearchCoveragePage
from app.models.source_poll_state import SourcePollState
from app.services.discovery.obscurity import (
    NOT_AVAILABLE,
    PREVIOUS_SCORE_VERSION,
    SCORE_VERSION,
    coverage_points,
    obscurity_block,
)
from app.services.research_coverage import coverage, names, refresh_job, sitemap
from app.services.research_coverage.coverage import (
    SEARCH_DISABLED,
    SEARCH_NOT_SEARCHABLE,
    SEARCH_SEARCHED,
    SEARCH_THROTTLED,
    SEARCH_UNAVAILABLE,
    STATE_FOUND,
    STATE_INDEX_NOT_BUILT,
    STATE_NONE_FOUND,
    SearchAnswer,
    SearchHit,
    research_coverage_step,
)
from app.services.research_coverage.providers import EDISON, SEARCH_DOMAINS, SPONSORED_NOTE
from app.services.research_coverage.sitemap import (
    EDISON_ROBOTS_URL,
    EDISON_SITEMAP_URL,
    RawAnswer,
    classify_edison_url,
    parse_sitemap,
    refresh_edison_index,
)
from app.services.safety_terms import scan_value
from app.services.scheduling import periodic
from tests import test_v3_entity_relationships as _base

# The real seams, captured before the autouse no-network fixture replaces them.
REAL_DEFAULT_FETCHER = sitemap.default_fetcher

session = _base.session

FIXTURES = Path(__file__).parent / "fixtures"
SITEMAP = (FIXTURES / "edison_sitemap.xml").read_bytes()
ROBOTS = (FIXTURES / "edison_robots.txt").read_bytes()
NOW = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)


@compiles(JSONB, "sqlite")
def _jsonb_as_json(element, compiler, **kw):  # noqa: ANN001, ANN202
    return "JSON"


def _fetcher(robots: RawAnswer | None = None, page: RawAnswer | None = None,
             calls: list[str] | None = None) -> sitemap.Fetcher:
    async def fetch(url: str, max_bytes: int) -> RawAnswer:
        if calls is not None:
            calls.append(url)
        if url == EDISON_ROBOTS_URL:
            return robots or RawAnswer(200, ROBOTS)
        assert url == EDISON_SITEMAP_URL
        return page or RawAnswer(200, SITEMAP)

    return fetch


def _company(name: str, ticker: str = "CWR", exchange: str = "LSE") -> Company:
    return Company(id=uuid.uuid4(), ticker=ticker, exchange=exchange, name=name)


# ── sitemap parsing (fixture) ─────────────────────────────────────────────── #


def test_fixture_parses_equity_pages_and_notes_only() -> None:
    parsed = parse_sitemap(SITEMAP)
    assert parsed.error is None
    assert parsed.urls_total == 30
    assert parsed.equity_pages == 18 and parsed.notes == 6
    assert parsed.skipped_other_paths == 6 and parsed.skipped_off_host == 0
    by_url = {e.url: e for e in parsed.entries}
    ceres = by_url["https://www.edisongroup.com/equity/ceres-power-holdings/BMC-415/"]
    assert (ceres.page_kind, ceres.company_slug, ceres.name_key) == (
        "equity", "ceres-power-holdings", "cerespower")
    assert ceres.title == "Ceres Power Holdings"
    assert ceres.lastmod is not None and ceres.lastmod.tzinfo is not None
    legacy = by_url["https://www.edisongroup.com/equity/artnet-2/"]
    assert legacy.name_key == "artnet" and legacy.title == "Artnet"
    note = by_url["https://www.edisongroup.com/research/converting-promises-to-cash/BM-4361/"]
    assert note.page_kind == "note" and note.name_key is None  # a headline, not a company
    assert not any("/edison-tv/" in u or "/podcast/" in u or "/analyst/" in u
                   for u in by_url)


def test_classify_and_parse_refuse_off_host_and_unsafe_shapes() -> None:
    assert classify_edison_url("https://www.edisongroup.com/equity/itm-power/BMC-372/") == (
        "equity", "itm-power")
    for bad in ("http://www.edisongroup.com/equity/itm-power/",
                "https://evil.example/equity/itm-power/",
                "https://www.edisongroup.com.evil.example/equity/itm-power/",
                "https://www.edisongroup.com/equity/itm-power/?x=1",
                "https://user@www.edisongroup.com/equity/itm-power/",
                "https://www.edisongroup.com:8443/equity/itm-power/",
                "https://www.edisongroup.com/equities/energy-resources/"):
        assert classify_edison_url(bad) is None, bad
    injected = SITEMAP.replace(
        b"https://www.edisongroup.com/equity/itm-power/BMC-372/",
        b"https://evil.example/equity/itm-power/BMC-372/")
    parsed = parse_sitemap(injected)
    assert parsed.skipped_off_host == 1 and parsed.equity_pages == 17
    assert parse_sitemap(b'<!DOCTYPE x [<!ENTITY a "b">]><urlset/>').error == (
        "doctype_refused")
    assert parse_sitemap(b"<sitemapindex xmlns='http://www.sitemaps.org/schemas/"
                         b"sitemap/0.9'/>").error == "not_a_urlset"
    assert parse_sitemap(b"<urlset><url>").error == "malformed_xml"


# ── robots and host pinning ──────────────────────────────────────────────── #


async def test_robots_disallow_or_unreachable_fetches_no_sitemap(session) -> None:  # noqa: ANN001
    calls: list[str] = []
    out = await refresh_edison_index(session, fetch=_fetcher(
        robots=RawAnswer(200, b"User-agent: *\nDisallow: /\n"), calls=calls), now=NOW)
    assert out.state == sitemap.STATE_ROBOTS_DISALLOWED and calls == [EDISON_ROBOTS_URL]
    calls.clear()
    out = await refresh_edison_index(session, fetch=_fetcher(
        robots=RawAnswer(503), calls=calls), now=NOW)
    assert out.state == sitemap.STATE_ROBOTS_UNAVAILABLE and calls == [EDISON_ROBOTS_URL]
    out = await refresh_edison_index(session, fetch=_fetcher(
        robots=RawAnswer(None, failed=True)), now=NOW)
    assert out.state == sitemap.STATE_ROBOTS_UNAVAILABLE
    # Our own product token named and disallowed.
    out = await refresh_edison_index(session, fetch=_fetcher(robots=RawAnswer(
        200, b"User-agent: InvestingBuddy-Research-Bot\nDisallow: /sitemap.xml\n")), now=NOW)
    assert out.state == sitemap.STATE_ROBOTS_DISALLOWED
    assert (await session.execute(select(func.count(ResearchCoveragePage.id)))).scalar() == 0
    # The live robots.txt allows the sitemap; a 404 robots.txt means no restriction.
    assert await sitemap.robots_decision(_fetcher()) == "allowed"
    assert await sitemap.robots_decision(_fetcher(robots=RawAnswer(404))) == "no_robots"


async def test_default_fetcher_is_pinned_to_the_edison_hosts(monkeypatch) -> None:  # noqa: ANN001
    from app.services.sources import document_fetcher

    seen: list[dict[str, Any]] = []

    async def fake_safe_fetch(url: str, **kw: Any) -> Any:
        seen.append({"url": url, **kw})
        return SimpleNamespace(blocked=False, error=None, status_code=200,
                               content=SITEMAP, truncated=False)

    monkeypatch.setattr(document_fetcher, "safe_fetch_document", fake_safe_fetch)
    answer = await REAL_DEFAULT_FETCHER(EDISON_SITEMAP_URL, sitemap.MAX_SITEMAP_BYTES)
    assert answer.status == 200 and answer.body == SITEMAP
    assert seen[0]["allowed_domains"] == EDISON.hosts
    assert seen[0]["resolve_ip"] is True
    assert set(seen[0]["extra_text_content_types"]) == {"text/xml", "application/xml"}
    assert seen[0]["max_bytes"] == sitemap.MAX_SITEMAP_BYTES


async def test_real_guard_blocks_an_off_host_url_before_any_dns() -> None:
    from app.services.sources.document_fetcher import safe_fetch_document

    resolved: list[str] = []

    def resolver(host: str, *a: Any, **k: Any) -> list[Any]:
        resolved.append(host)
        raise OSError("no network in tests")

    result = await safe_fetch_document("https://evil.example/sitemap.xml",
                                       allowed_domains=EDISON.hosts, resolve_ip=True,
                                       resolver=resolver)
    assert result.blocked and resolved == []


# ── upsert ───────────────────────────────────────────────────────────────── #


async def test_refresh_upserts_by_url_and_is_idempotent(session) -> None:  # noqa: ANN001
    first = await refresh_edison_index(session, fetch=_fetcher(), now=NOW)
    assert first.ok and (first.inserted, first.updated) == (24, 0)
    later = NOW + timedelta(days=7)
    second = await refresh_edison_index(session, fetch=_fetcher(), now=later)
    assert second.ok and (second.inserted, second.updated) == (0, 24)
    rows = (await session.execute(select(ResearchCoveragePage))).scalars().all()
    assert len(rows) == 24
    assert {r.discovered_via for r in rows} == {"sitemap"}
    row = next(r for r in rows if r.company_slug == "itm-power")
    assert row.first_seen_at.replace(tzinfo=timezone.utc) == NOW
    assert row.last_seen_at.replace(tzinfo=timezone.utc) == later
    dry = await refresh_edison_index(session, fetch=_fetcher(), now=later, dry_run=True)
    assert dry.ok and dry.inserted == 0 and dry.updated == 0 and dry.equity_pages == 18


async def test_drift_and_truncation_leave_the_table_untouched(session) -> None:  # noqa: ANN001
    no_equity = (b'<?xml version="1.0"?><urlset xmlns="http://www.sitemaps.org/schemas/'
                 b'sitemap/0.9"><url><loc>https://www.edisongroup.com/</loc></url></urlset>')
    out = await refresh_edison_index(session, fetch=_fetcher(page=RawAnswer(200, no_equity)),
                                     now=NOW)
    assert out.state == sitemap.STATE_LAYOUT_DRIFT
    out = await refresh_edison_index(session, fetch=_fetcher(
        page=RawAnswer(200, SITEMAP[:2000], truncated=True)), now=NOW)
    assert out.state == sitemap.STATE_TRUNCATED
    out = await refresh_edison_index(session, fetch=_fetcher(page=RawAnswer(403)), now=NOW)
    assert out.state == sitemap.STATE_FETCH_FAILED and out.error == "http 403"
    assert (await session.execute(select(func.count(ResearchCoveragePage.id)))).scalar() == 0


# ── name matching precision ─────────────────────────────────────────────── #


@pytest.mark.parametrize(("company", "slug"), [
    ("Ceres Power Holdings plc", "ceres-power-holdings"),
    ("ITM Power PLC", "itm-power"),
    ("Metlen Energy & Metals S.A.", "metlenenergyandmetals"),
    ("Costain Group PLC", "costain-group"),
    ("artnet AG", "artnet-2"),
    ("AB Dynamics plc", "ab-dynamics"),
    ("Jersey Electricity plc", "jersey-electricity"),
    ("Gresham House Energy Storage Fund plc", "gresham-house-energy-storage-fund"),
    ("2G Energy AG", "2g-energy"),
    ("Oncoinvent ASA", "oncoinvent"),
])
def test_names_that_match(company: str, slug: str) -> None:
    assert names.name_key(company) is not None
    assert names.name_key(company) == names.slug_key(slug)


@pytest.mark.parametrize(("company", "slug"), [
    ("Ceres Inc", "ceres-power-holdings"),           # a prefix is not a match
    ("Ceres Power Systems Ltd", "ceres-power-holdings"),
    ("Jersey Electric Co", "jersey-electricity"),
    ("XP Power Systems", "xp-power"),
    ("Oman Cables Industry SAOG", "oman-cables"),    # conservative: a word more
    ("Phoenix Group Holdings", "phoenix-power"),
    ("Loop Industries", "loop-energy"),
])
def test_names_that_must_not_match(company: str, slug: str) -> None:
    assert names.name_key(company) != names.slug_key(slug)


def test_weak_names_have_no_key() -> None:
    for weak in ("Power Corp", "Energy plc", "Group plc", "ABC", "", None, "The Trust"):
        assert names.name_key(weak) is None, weak
    assert names.slug_key("energy") is None
    tokens = names.name_tokens("Ceres Power Holdings plc")
    assert names.mentions(tokens, "Ceres Power: strong H1 order intake")
    assert names.mentions(tokens, "/en/company/ceres-power/research")
    assert not names.mentions(tokens, "Ceres Group results")
    assert not names.mentions(tokens, "Power stocks in Ceres county")


# ── search-only providers ───────────────────────────────────────────────── #


def _searcher(hits: list[SearchHit], calls: list[tuple[str, tuple[str, ...]]],
              executed: bool = True) -> coverage.Searcher:
    async def search(_s: Any, query: str, domains: tuple[str, ...], _cid: Any,
                     _cfg: Any) -> SearchAnswer:
        calls.append((query, domains))
        return SearchAnswer(executed=executed, state="ok" if executed else
                            "web_search_unavailable", hits=list(hits) if executed else [])

    return search


HITS = [
    SearchHit("https://www.redeye.se/company/ceres-power/research/1234",
              "Ceres Power - Redeye", datetime(2026, 10, 2, tzinfo=timezone.utc)),
    SearchHit("https://www.researchtree.co.uk/companies/x/ceres-power-holdings-plc/",
              "Ceres Power Holdings plc research"),
    SearchHit("https://www.redeye.se/company/other/research/1", "Other AB"),  # no mention
    SearchHit("https://evil.example/ceres-power", "Ceres Power"),             # off host
    SearchHit("https://www.edisongroup.com/equity/ceres-power-holdings/",
              "Ceres Power"),                                                  # not search-only
]


async def test_search_disabled_and_not_searchable_paths(session) -> None:  # noqa: ANN001
    calls: list[Any] = []
    company = _company("Ceres Power Holdings plc")
    session.add(company)
    await session.flush()
    tokens = names.name_tokens(company.name)
    out = await coverage.search_only_providers(
        session, company, tokens=tokens, key=names.key_from_tokens(tokens),
        cfg=SimpleNamespace(v3_web_search_enabled=False), searcher=_searcher(HITS, calls))
    assert out["state"] == SEARCH_DISABLED and calls == []
    out = await coverage.search_only_providers(
        session, company, tokens=("power",), key=None,
        cfg=SimpleNamespace(v3_web_search_enabled=True), searcher=_searcher(HITS, calls))
    assert out["state"] == SEARCH_NOT_SEARCHABLE and calls == []


async def test_search_runs_once_per_30_days_and_keeps_only_named_on_host_hits(
        session) -> None:  # noqa: ANN001
    calls: list[tuple[str, tuple[str, ...]]] = []
    company = _company("Ceres Power Holdings plc")
    session.add(company)
    await session.flush()
    tokens = names.name_tokens(company.name)
    key = names.key_from_tokens(tokens)
    cfg = SimpleNamespace(v3_web_search_enabled=True)
    out = await coverage.search_only_providers(session, company, tokens=tokens, key=key,
                                               cfg=cfg, searcher=_searcher(HITS, calls),
                                               now=NOW)
    assert out["state"] == SEARCH_SEARCHED and out["hits_kept"] == 2
    assert len(calls) == 1 and calls[0][1] == SEARCH_DOMAINS
    assert "ceres power" in calls[0][0]
    rows = (await session.execute(select(ResearchCoveragePage))).scalars().all()
    assert {(r.provider, r.discovered_via, r.name_key) for r in rows} == {
        ("redeye", "search", "cerespower"), ("research_tree", "search", "cerespower")}
    assert next(r for r in rows if r.provider == "redeye").page_kind == "equity"
    # Within 30 days: throttled, no call.
    out = await coverage.search_only_providers(
        session, company, tokens=tokens, key=key, cfg=cfg,
        searcher=_searcher(HITS, calls), now=NOW + timedelta(days=29))
    assert out["state"] == SEARCH_THROTTLED and len(calls) == 1
    assert out["searched_at"].startswith("2026-11-22")
    # After 30 days: one more search; the same URLs are refreshed, not duplicated.
    out = await coverage.search_only_providers(
        session, company, tokens=tokens, key=key, cfg=cfg,
        searcher=_searcher(HITS, calls), now=NOW + timedelta(days=31))
    assert out["state"] == SEARCH_SEARCHED and len(calls) == 2
    assert (await session.execute(select(func.count(ResearchCoveragePage.id)))).scalar() == 2
    throttle = await session.get(SourcePollState, f"research_coverage_search.{company.id}")
    assert throttle is not None and throttle.last_ok_at is not None


async def test_unavailable_search_degrades_and_waits_a_day(session) -> None:  # noqa: ANN001
    calls: list[Any] = []
    company = _company("Ceres Power Holdings plc")
    session.add(company)
    await session.flush()
    tokens = names.name_tokens(company.name)
    key = names.key_from_tokens(tokens)
    cfg = SimpleNamespace(v3_web_search_enabled=True)
    out = await coverage.search_only_providers(
        session, company, tokens=tokens, key=key, cfg=cfg,
        searcher=_searcher(HITS, calls, executed=False), now=NOW)
    assert out["state"] == SEARCH_UNAVAILABLE and len(calls) == 1
    out = await coverage.search_only_providers(
        session, company, tokens=tokens, key=key, cfg=cfg,
        searcher=_searcher(HITS, calls, executed=False), now=NOW + timedelta(hours=3))
    assert out["state"] == SEARCH_UNAVAILABLE and len(calls) == 1  # not retried yet
    out = await coverage.search_only_providers(
        session, company, tokens=tokens, key=key, cfg=cfg,
        searcher=_searcher(HITS, calls), now=NOW + timedelta(hours=25))
    assert out["state"] == SEARCH_SEARCHED and len(calls) == 2

    async def boom(*_a: Any) -> SearchAnswer:
        raise RuntimeError("provider down")

    other = _company("ITM Power PLC", ticker="ITM")
    session.add(other)
    await session.flush()
    t2 = names.name_tokens(other.name)
    out = await coverage.search_only_providers(
        session, other, tokens=t2, key=names.key_from_tokens(t2), cfg=cfg, searcher=boom,
        now=NOW)
    assert out["state"] == SEARCH_UNAVAILABLE


# ── the report block ─────────────────────────────────────────────────────── #


def _cfg(**over: Any) -> SimpleNamespace:
    base = {"v3_research_coverage_enabled": True, "v3_web_search_enabled": False,
            "v3_web_fetch_enabled": False}
    base.update(over)
    return SimpleNamespace(**base)


async def test_block_states_index_not_built_none_found_and_found(session) -> None:  # noqa: ANN001
    ceres = _company("Ceres Power Holdings plc")
    nobody = _company("Quiet Grid Components AB", ticker="QGC")
    session.add_all([ceres, nobody])
    await session.flush()

    block, cov = await research_coverage_step(session, ceres, cfg=_cfg(), now=NOW)
    assert block is not None and cov is not None
    assert block["state"] == STATE_INDEX_NOT_BUILT and block["provider_count"] == 0
    assert "never been refreshed" in block["absence"]
    assert cov == {**cov, "state": "unknown", "provider_count": None}

    await refresh_edison_index(session, fetch=_fetcher(), now=NOW)

    block, cov = await research_coverage_step(session, nobody, cfg=_cfg(), now=NOW)
    assert block["state"] == STATE_NONE_FOUND
    assert block["absence"] == (
        "None found in the indexed providers (Edison sitemap; search-only providers not "
        "searched, web search is off).")
    assert cov["state"] == "known" and cov["provider_count"] == 0

    block, cov = await research_coverage_step(session, ceres, cfg=_cfg(), now=NOW)
    assert block["state"] == STATE_FOUND and block["absence"] is None
    assert block["provider_count"] == 1 and cov["provider_count"] == 1
    edison = block["providers"][0]
    assert edison["name"] == "Edison Group"
    page = edison["pages"][0]
    assert page["url"] == "https://www.edisongroup.com/equity/ceres-power-holdings/BMC-415/"
    assert page["date"] and page["date_basis"] == "sitemap lastmod"
    assert page["in_latest_index"] is True
    assert block["sponsored_note"] == SPONSORED_NOTE
    assert "paid for by the issuer" in block["sponsored_note"]
    assert block["ingested"]["state"] == "disabled"
    assert scan_value(block) == []
    json.dumps(block)  # JSON-safe

    # With search on, the search-only providers' hits join the same block.
    calls: list[Any] = []
    block, cov = await research_coverage_step(
        session, ceres, cfg=_cfg(v3_web_search_enabled=True),
        searcher=_searcher(HITS, calls), now=NOW)
    assert block["provider_count"] == 3 and cov["provider_count"] == 3
    assert block["search_only_providers"]["state"] == SEARCH_SEARCHED
    assert {p["provider"] for p in block["providers"]} == {"edison", "redeye",
                                                           "research_tree"}


async def test_optional_ingest_is_bounded_to_one_edison_page(session) -> None:  # noqa: ANN001
    await refresh_edison_index(session, fetch=_fetcher(), now=NOW)
    ceres = _company("Ceres Power Holdings plc")
    session.add(ceres)
    await session.flush()
    seen: list[str] = []

    async def ingester(_s: Any, _c: Any, page: Any, _cfg: Any) -> dict[str, Any]:
        seen.append(page.url)
        return {"state": "ingested", "url": page.url, "source_class": "research_consultancy",
                "tier": "T5_api_aggregator"}

    block, _ = await research_coverage_step(session, ceres,
                                            cfg=_cfg(v3_web_fetch_enabled=True),
                                            ingester=ingester, now=NOW)
    assert seen == ["https://www.edisongroup.com/equity/ceres-power-holdings/BMC-415/"]
    assert block["ingested"]["state"] == "ingested"
    assert coverage.MAX_INGESTS_PER_RUN == 1


def test_research_providers_classify_as_t5_research_consultancy() -> None:
    from app.services.web_research.classify import classify_source

    for url in ("https://www.edisongroup.com/equity/itm-power/BMC-372/",
                "https://www.redeye.se/company/x", "https://www.researchtree.co.uk/x",
                "https://www.research-hub.de/companies/x"):
        got = classify_source(url)
        assert got.source_class == "research_consultancy", url
        assert got.tier == "T5_api_aggregator", url


# ── obscurity component ─────────────────────────────────────────────────── #


def _obscurity(cov: dict[str, Any] | None) -> dict[str, Any]:
    return obscurity_block(analyst={"count": 1, "as_of": "2026-11-16", "source": "x"},
                           passive_block=None, news=None, layers_block=None, sector=None,
                           industry=None, listing_country="Germany", coverage=cov)


def test_obscurity_component_points_unknown_and_version() -> None:
    assert (coverage_points(0), coverage_points(1), coverage_points(2),
            coverage_points(5), coverage_points(None)) == (1.0, 0.5, 0.1, 0.1, None)
    before = _obscurity(None)
    assert before["version"] == PREVIOUS_SCORE_VERSION == "2026-11-16.1"
    assert "independent_research_coverage" not in {c["key"] for c in before["components"]}
    assert SCORE_VERSION != PREVIOUS_SCORE_VERSION

    unknown = _obscurity({"state": "unknown", "provider_count": None, "providers": [],
                          "as_of": None, "source": "s", "looked_at": "Edison sitemap"})
    comp = {c["key"]: c for c in unknown["components"]}["independent_research_coverage"]
    assert comp["state"] == NOT_AVAILABLE and comp["points"] is None
    assert "never been refreshed" in comp["note"]
    assert unknown["version"] == SCORE_VERSION
    assert unknown["components_available"] == before["components_available"]

    for count, points in ((0, 1.0), (1, 0.5), (3, 0.1)):
        block = _obscurity({"state": "known", "provider_count": count,
                            "providers": ["Edison Group"] * min(count, 1),
                            "as_of": "2026-11-22", "source": "s", "looked_at": "x"})
        comp = {c["key"]: c for c in block["components"]}["independent_research_coverage"]
        assert comp["points"] == points and comp["value"] == count
        assert block["components_available"] == before["components_available"] + 1
    none = _obscurity({"state": "known", "provider_count": 0, "providers": [],
                       "as_of": "2026-11-22", "source": "s", "looked_at": "x"})
    assert "none found in the indexed providers" in {
        c["key"]: c for c in none["components"]}["independent_research_coverage"]["note"]


# ── flag off ─────────────────────────────────────────────────────────────── #


async def test_flag_off_leaves_the_report_and_obscurity_unchanged(session) -> None:  # noqa: ANN001
    from app.services.pipeline import professional_research as pr
    from app.services.pipeline import v3_pipeline

    ceres = _company("Ceres Power Holdings plc")
    session.add(ceres)
    await session.flush()
    off = SimpleNamespace(v3_research_coverage_enabled=False, v3_web_search_enabled=True)
    assert await research_coverage_step(session, ceres, cfg=off) == (None, None)
    assert await v3_pipeline._research_coverage_section(session, ceres, cfg=off) == (
        None, None)
    subject = {"ticker": "CWR", "exchange": "LSE", "name": "Ceres"}
    plain = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=()))
    evidence = next(s for s in plain["sections"] if s["key"] == "evidence_quality_and_gaps")
    assert "independent_research" not in evidence
    block, _ = await research_coverage_step(session, ceres, cfg=_cfg(), now=NOW)
    report = pr.assemble(pr.ReportInputs(subject=subject, questions=(), findings=(),
                                         independent_research=block))
    evidence = next(s for s in report["sections"] if s["key"] == "evidence_quality_and_gaps")
    assert evidence["independent_research"]["state"] == STATE_INDEX_NOT_BUILT
    assert Settings().v3_research_coverage_enabled is True


async def test_a_failing_read_never_costs_the_report(monkeypatch, session) -> None:  # noqa: ANN001
    async def boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("db gone")

    monkeypatch.setattr(coverage, "index_status", boom)
    assert await research_coverage_step(session, _company("Ceres Power plc"),
                                        cfg=_cfg()) == (None, None)


# ── periodic task and job ────────────────────────────────────────────────── #


@pytest.fixture
async def factory(tmp_path, monkeypatch):  # noqa: ANN001, ANN201
    import app.db.session as session_mod

    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path}/v3217.db",
                                 connect_args={"check_same_thread": False, "timeout": 30})
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(session_mod, "async_session_factory", maker)
    yield maker
    await engine.dispose()


@pytest.fixture
def only_coverage_task():  # noqa: ANN201
    saved = list(periodic.registered_tasks())
    periodic.clear_periodic_tasks()
    periodic.register_periodic_task(refresh_job.RESEARCH_COVERAGE_TASK)
    yield
    periodic.clear_periodic_tasks()
    for t in saved:
        periodic.register_periodic_task(t)


def test_periodic_task_is_registered_weekly_behind_the_flag() -> None:
    task = {t.key: t for t in periodic.registered_tasks()}["research_coverage_refresh"]
    assert task.interval == periodic.WEEKLY
    assert task.job_type == "research_coverage_refresh"
    assert task.flag_attr == "v3_research_coverage_enabled"
    from app.services.jobs.worker import registry

    assert "research_coverage_refresh" in registry
    handlers = Path("app/services/jobs/handlers.py").read_text()
    assert "from app.services.research_coverage import refresh_job" in handlers
    off = SimpleNamespace(v3_periodic_tasks_enabled=True, v3_research_coverage_enabled=False)
    assert periodic.task_enabled(task, off) is False


async def test_sweep_enqueues_once_per_week(factory, only_coverage_task) -> None:  # noqa: ANN001
    from app.models.research_job import ResearchJob

    cfg = Settings(v3_periodic_tasks_enabled=True,  # type: ignore[call-arg]
                   v3_research_coverage_enabled=True)
    first = await periodic.run_periodic_tasks(cfg=cfg, session_factory=factory, now=NOW)
    again = await periodic.run_periodic_tasks(cfg=cfg, session_factory=factory,
                                              now=NOW + timedelta(hours=5))
    assert first == ["research_coverage_refresh:2026-W47"] and again == []
    async with factory() as s:
        jobs = (await s.execute(select(ResearchJob))).scalars().all()
    assert [j.job_type for j in jobs] == ["research_coverage_refresh"]


async def test_job_writes_detail_and_failure_raises(factory, monkeypatch) -> None:  # noqa: ANN001
    async def ok_fetch(url: str, max_bytes: int) -> RawAnswer:
        return await _fetcher()(url, max_bytes)

    monkeypatch.setattr(sitemap, "default_fetcher", ok_fetch)
    ctx = SimpleNamespace(payload={periodic.PAYLOAD_TASK_KEY: refresh_job.TASK_KEY},
                          job_id=uuid.uuid4())

    async def checkpoint(_stage: str) -> None:
        return None

    ctx.checkpoint = checkpoint
    outcome = await refresh_job.run_research_coverage_refresh_job(ctx)  # type: ignore[arg-type]
    assert outcome.result_type == "research_coverage_refresh"
    async with factory() as s:
        row = await s.get(SourcePollState, refresh_job.TASK_KEY)
        count = (await s.execute(select(func.count(ResearchCoveragePage.id)))).scalar()
    assert count == 24
    assert row is not None and row.last_ok_at is not None
    assert row.detail_json["state"] == "ok" and row.detail_json["equity_pages"] == 18

    async def disallowed(url: str, max_bytes: int) -> RawAnswer:
        return RawAnswer(200, b"User-agent: *\nDisallow: /\n")

    monkeypatch.setattr(sitemap, "default_fetcher", disallowed)
    with pytest.raises(refresh_job.CoverageRefreshError):
        await refresh_job.run_research_coverage_refresh_job(ctx)  # type: ignore[arg-type]
    async with factory() as s:
        row = await s.get(SourcePollState, refresh_job.TASK_KEY)
    assert row.last_error == "CoverageRefreshError"
    assert row.detail_json["state"] == "robots_disallowed"


async def test_operator_script_dry_run_writes_nothing(factory, monkeypatch) -> None:  # noqa: ANN001
    import importlib.util

    monkeypatch.setattr(sitemap, "default_fetcher", _fetcher())
    spec = importlib.util.spec_from_file_location(
        "refresh_research_coverage", "scripts/refresh_research_coverage.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "async_session_factory", factory)
    assert await module.main(["--dry-run"]) == 0
    async with factory() as s:
        assert (await s.execute(select(func.count(ResearchCoveragePage.id)))).scalar() == 0
    assert await module.main([]) == 0
    async with factory() as s:
        assert (await s.execute(select(func.count(ResearchCoveragePage.id)))).scalar() == 24


# ── migration ────────────────────────────────────────────────────────────── #


def test_migration_064_shape_and_single_head() -> None:
    from alembic.config import Config
    from alembic.script import ScriptDirectory

    text = Path("alembic/versions/064_add_research_coverage_pages.py").read_text()
    assert 'revision: str = "064"' in text
    assert 'down_revision: str | None = "063"' in text
    for needle in ("research_coverage_pages", "uq_research_coverage_pages_url",
                   "ck_research_coverage_pages_page_kind",
                   "ck_research_coverage_pages_discovered_via", "name_key", "lastmod",
                   "first_seen_at", "last_seen_at", "def downgrade"):
        assert needle in text, needle
    assert "DELETE FROM" not in text.upper()
    script = ScriptDirectory.from_config(Config("alembic.ini"))
    assert script.get_heads() == ["064"]
    cols = set(ResearchCoveragePage.__table__.columns.keys())
    assert cols == {"id", "provider", "url", "page_kind", "company_slug", "name_key",
                    "title", "lastmod", "first_seen_at", "last_seen_at", "discovered_via"}


def test_web_reader_pins_the_block_keys() -> None:
    web = Path(__file__).resolve().parents[2] / "web" / "src" / "components" / "research"
    reader = (web / "independentResearch.ts").read_text()
    for key in ("state", "provider_count", "providers", "absence", "looked_at",
                "search_only_providers", "indexed_providers", "match", "ingested",
                "sponsored_note", "note", "url", "title", "page_kind", "date",
                "date_basis", "discovered_via", "in_latest_index", "latest_date",
                "pages_total", "refreshed_at", "searched_at"):
        assert f".{key}" in reader, key
    assert "readIndependentResearchBlock(r.independent_research)" in (
        web / "v3Research.ts").read_text()
    assert "independent_research_coverage" in (
        web / "report" / "ObscuritySection.tsx").read_text()
