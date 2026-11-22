"""V3.21.1b — segment precision and the 10-K business section as exposure evidence (ADR-115).

Live finding (staging run 914e8362, 2026-11-22): the thesis "US small-cap cybersecurity
software companies with growing recurring revenue, under-followed by analysts" produced
segment terms ['cybersecurity', 'recurring', 'followed', 'analysts'], kept the synonym
"enterprise software", left six size-verified US small caps at industry 'unknown' (no
issuer page, no web search) and therefore auto-reported nothing.

Under test:
1. the qualifier-clause cut and the descriptor words in ``extract_segment_terms``;
2. the synonym filter anchored on SPECIFIC term words only;
3. the SEC 10-K "Item 1. Business" section read from EDGAR as T1 exposure evidence
   (fixture: OneSpan Inc., CIK 1044777, 10-K for FY2025, trimmed; never the network);
4. the auto-report fallback ``industry_unverified_fallback``.
"""

from __future__ import annotations

import json
import uuid
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.core.config import Settings
from app.services.discovery import sec_business as sb
from app.services.discovery.identity import IdentityOutcome
from app.services.discovery.leads import CompanyLead
from app.services.discovery.screening import screen_issuer, segment_term_patterns
from app.services.discovery.segment_expansion import sanitise_proposals
from app.services.market_thesis_parser import extract_segment_terms, parse_thesis
from app.services.sources.document_fetcher import DocumentFetchResult

FIXTURES = Path(__file__).parent / "fixtures" / "sec_business"
SUBMISSIONS = (FIXTURES / "sec_submissions_CIK0001044777.json").read_bytes()
TEN_K = (FIXTURES / "sec_10k_ospn_20251231.htm").read_bytes()
SUB_URL = "https://data.sec.gov/submissions/CIK0001044777.json"
TEN_K_URL = ("https://www.sec.gov/Archives/edgar/data/1044777/000104477726000008/"
             "ospn-20251231.htm")
LIVE = ("US small-cap cybersecurity software companies with growing recurring revenue, "
        "under-followed by analysts")


@pytest.fixture(autouse=True)
def _offline(monkeypatch):  # noqa: ANN001, ANN202
    """No test here may reach the network: the real fetcher is replaced by a tripwire."""
    from app.services.sources import document_fetcher

    async def _tripwire(url, **_kw):  # noqa: ANN001, ANN202
        raise AssertionError(f"network fetch attempted: {url}")

    monkeypatch.setattr(document_fetcher, "safe_fetch_document", _tripwire)
    sb._CACHE.clear()
    yield
    sb._CACHE.clear()


class FakeSec:
    """EDGAR from the fixtures. Records every call and its allowed domains."""

    def __init__(self, *, submissions: bytes | None = SUBMISSIONS,
                 filing: bytes | None = TEN_K) -> None:
        self.calls: list[tuple[str, tuple[str, ...], int | None]] = []
        self.submissions = submissions
        self.filing = filing

    async def __call__(self, url: str, **kw: Any) -> DocumentFetchResult:
        self.calls.append((url, tuple(kw["allowed_domains"]), kw.get("max_bytes")))
        body = self.submissions if "/submissions/" in url else self.filing
        if body is None:
            return DocumentFetchResult(requested_url=url, error="nf", failure_code="http_404")
        kind = "text" if url.endswith(".json") else "html"
        return DocumentFetchResult(requested_url=url, final_url=url, status_code=200,
                                   content_type="text/html", document_type=kind,
                                   content=body)


def _issuer(*, cik: str | None = "1044777", evidence: str | None = None) -> IdentityOutcome:
    lead = CompanyLead(name="OneSpan Inc.", ticker="OSPN", exchange_raw="NASDAQ",
                       country="United States", listing_source_url=None,
                       evidence_url=evidence, why=None, source="model_recall")
    listing = {"directory": "sec", "ticker": "OSPN", "cik": cik} if cik else None
    return IdentityOutcome(lead=lead, status="verified", ticker="OSPN", exchange="NASDAQ",
                           name="OneSpan Inc.", directory_listing=listing)


def _cyber_terms():  # noqa: ANN202
    return segment_term_patterns(["cybersecurity"])


# ── 1. Segment terms ────────────────────────────────────────────────────── #


def test_live_thesis_yields_only_the_segment():
    parsed = parse_thesis(LIVE)
    assert parsed.segment_terms == ["cybersecurity"]
    assert parsed.needs_narrowing is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (LIVE, ["cybersecurity"]),
        ("OT security for utilities", ["ot security", "utilities"]),  # "for" never cuts
        ("companies supplying transformers with long backlogs", ["transformers"]),
        ("subsea cable installation vessels", ["subsea cable", "installation vessels"]),
        ("Japanese power semiconductors", ["power semiconductors"]),
        ("cybersecurity names under-followed by analysts", ["cybersecurity"]),
        ("desalination companies that benefit from rising water demand", ["desalination"]),
        ("hydrogen electrolyser makers driven by subsidies", ["hydrogen electrolyser"]),
        ("gas pipeline operators", ["gas pipeline"]),  # "pipeline" is not a descriptor
        ("government agencies software", ["government agencies"]),  # not a descriptor
    ],
)
def test_qualifier_clauses_are_cut(text, expected):
    assert extract_segment_terms(text) == expected


def test_a_connective_with_no_segment_before_it_is_not_a_cut():
    # "companies" is no segment, so "with" does not cut; the segment follows it.
    assert extract_segment_terms("companies with exposure to cybersecurity") == [
        "cybersecurity"]


@pytest.mark.parametrize("word", ["recurring", "analysts", "coverage", "overlooked",
                                  "neglected", "mispriced", "tailwinds", "backlogs",
                                  "demand", "spending"])
def test_descriptor_words_are_never_terms(word):
    assert word not in extract_segment_terms(f"vertical farming {word} stocks")
    assert extract_segment_terms(f"{word} companies") == []


def test_fixed_theme_text_is_unchanged():
    # "OT security for utilities" matches a fixed theme; no segment terms are computed.
    parsed = parse_thesis("OT security for utilities")
    assert parsed.themes and parsed.segment_terms == []


# ── 2. Synonym filter ───────────────────────────────────────────────────── #


def test_enterprise_software_is_rejected_and_security_synonyms_kept():
    kept, dropped = sanitise_proposals(
        ["enterprise software", "information security", "network security", "it security",
         "recurring revenue software", "analyst coverage", "cyber security"],
        terms=["cybersecurity"], text=LIVE, limit=8)
    assert kept == ["information security", "network security", "it security",
                    "cyber security"]
    assert dropped == 3


def test_the_raw_text_is_no_anchor():
    # "farming" is only in the text, not in the term: a proposal tied to it is dropped.
    kept, _ = sanitise_proposals(["dairy farming"], terms=["hydroponics"],
                                 text="hydroponics farming companies", limit=8)
    assert kept == []


def test_a_generic_only_term_anchors_on_its_own_words():
    kept, _ = sanitise_proposals(["enterprise software"], terms=["software"], text="",
                                 limit=8)
    assert kept == ["enterprise software"]


# ── 3. SEC 10-K business section ────────────────────────────────────────── #


def test_latest_annual_document_skips_other_forms():
    picked = sb.latest_annual_document(json.loads(SUBMISSIONS), "1044777",
                                       today=date(2026, 11, 22))
    assert picked == ("10-K", TEN_K_URL, "2026-02-26")


def test_a_stale_annual_report_is_not_read():
    assert sb.latest_annual_document(json.loads(SUBMISSIONS), "1044777",
                                     today=date(2029, 1, 1)) is None


@pytest.mark.parametrize("bad", [None, {}, {"filings": {"recent": {"form": "10-K"}}}, [1]])
def test_latest_annual_document_tolerates_bad_records(bad):
    assert sb.latest_annual_document(bad, "1044777") is None


def test_business_section_skips_the_table_of_contents():
    toc = "Item 1. Business 1 Item 1A. Risk Factors 10 "
    body = "Item 1 – Business Overview We sell network firewalls. " + "x " * 800
    text = "cover " * 10 + toc + body + "Item 1A - Risk Factors cybersecurity incidents"
    section, basis = sb.business_section(text, "10-K")
    assert basis == "item_heading"
    assert section.startswith("Overview We sell network firewalls")
    assert "cybersecurity incidents" not in section  # Item 1A is never read


def test_business_section_20f_item_4():
    text = ("Item 4. Information on the Company A. History. We make power semiconductors. "
            + "y " * 800 + "Item 4A. Unresolved Staff Comments")
    section, basis = sb.business_section(text, "20-F")
    assert basis == "item_heading" and "power semiconductors" in section


async def test_onespan_10k_states_cybersecurity_exposure():
    fake = FakeSec()
    found, fetches = await sb.sec_business_exposures(_issuer(), _cyber_terms(),
                                                     cfg=Settings(), fetcher=fake)
    assert fetches == 2
    assert [(c[0], c[1]) for c in fake.calls] == [
        (SUB_URL, ("data.sec.gov",)), (TEN_K_URL, ("www.sec.gov",))]
    assert fake.calls[1][2] == sb.MAX_FILING_BYTES
    assert len(found) == 1
    exposure = found[0]
    assert exposure.term == "cybersecurity" and exposure.exposure == "direct"
    assert exposure.verified and exposure.source_tier == "T1_primary_filing"
    assert exposure.source_url == TEN_K_URL
    assert "OneSpan helps organizations" in exposure.statement


async def test_a_segment_the_filing_does_not_name_is_no_evidence():
    found, _ = await sb.sec_business_exposures(
        _issuer(), segment_term_patterns(["subsea cable"]), cfg=Settings(), fetcher=FakeSec())
    assert found == []


async def test_one_read_per_cik_per_day():
    fake = FakeSec()
    await sb.sec_business_exposures(_issuer(), _cyber_terms(), cfg=Settings(), fetcher=fake)
    found, fetches = await sb.sec_business_exposures(_issuer(), _cyber_terms(),
                                                     cfg=Settings(), fetcher=fake)
    assert fetches == 0 and len(fake.calls) == 2 and found


async def test_a_failed_read_is_not_cached():
    failing = FakeSec(submissions=None)
    found, fetches = await sb.sec_business_exposures(_issuer(), _cyber_terms(),
                                                     cfg=Settings(), fetcher=failing)
    assert (found, fetches) == ([], 1)
    found, _ = await sb.sec_business_exposures(_issuer(), _cyber_terms(), cfg=Settings(),
                                               fetcher=FakeSec())
    assert found


async def test_flag_off_no_cik_and_no_terms_fetch_nothing():
    fake = FakeSec()
    off = Settings(v3_discovery_sec_business_exposure_enabled=False)  # type: ignore[call-arg]
    assert await sb.sec_business_exposures(_issuer(), _cyber_terms(), cfg=off,
                                           fetcher=fake) == ([], 0)
    assert await sb.sec_business_exposures(_issuer(cik=None), _cyber_terms(),
                                           cfg=Settings(), fetcher=fake) == ([], 0)
    assert await sb.sec_business_exposures(_issuer(), {}, cfg=Settings(),
                                           fetcher=fake) == ([], 0)
    assert fake.calls == []


def test_flag_defaults_on():
    assert Settings().v3_discovery_sec_business_exposure_enabled is True


@pytest.mark.parametrize(("listing", "issuer", "expected"), [
    ({"cik": "0001044777"}, None, "1044777"),
    (None, {"cik": 1044777}, "1044777"),
    ({"cik": "12; DROP"}, None, None),
    (None, None, None),
])
def test_issuer_cik(listing, issuer, expected):
    assert sb.issuer_cik(SimpleNamespace(directory_listing=listing, issuer=issuer)) == expected


def _provider() -> Any:
    from app.services.providers.contracts import ConsumptionUnits

    answer = SimpleNamespace(status="completed", warnings=[], research_leads=[],
                             consumption=ConsumptionUnits())

    class _P:
        async def investigate(self, **_kw: Any) -> Any:
            return answer

    return _P()


async def test_screening_reads_the_10k_when_the_issuer_site_says_nothing():
    from app.services.discovery.intent import build_intent

    fake = FakeSec()
    result = await screen_issuer(_issuer(), build_intent(LIVE), cfg=Settings(),
                                 provider=_provider(), fetcher=fake)
    assert [e.source_tier for e in result.exposures] == ["T1_primary_filing"]
    assert result.exposures[0].term == "cybersecurity"
    fetch_units = [u for u in result.consumption if getattr(u, "url_fetch_calls", 0)]
    assert fetch_units and fetch_units[-1].url_fetch_calls == 2


async def test_screening_without_a_cik_never_touches_edgar():
    from app.services.discovery.intent import build_intent

    fake = FakeSec()
    result = await screen_issuer(_issuer(cik=None), build_intent(LIVE), cfg=Settings(),
                                 provider=_provider(), fetcher=fake)
    assert result.exposures == [] and fake.calls == []


# ── 4. Auto-report fallback ─────────────────────────────────────────────── #


def _row(key: str, status: str) -> dict[str, Any]:
    return {"key": key, "hardness": "hard", "status": status}


def _cand(ticker: str, rank: int, status: str, rows: list[dict[str, Any]]) -> Any:
    return SimpleNamespace(
        id=uuid.uuid4(), ticker=ticker, exchange="NASDAQ", rank=rank,
        thesis_match_json={"v319": {"eligibility": {"status": status},
                                    "constraint_results": rows}})


def _run(dynamic: dict[str, Any] | None = None) -> Any:
    return SimpleNamespace(universe_json={"dynamic": dynamic or {}})


def test_fallback_picks_the_best_size_verified_industry_unknown():
    from app.services.discovery import auto_report as ar

    cands = [
        _cand("AAA", 1, "eligible_unverified", [_row("size", "unknown"),
                                                 _row("industry", "unknown")]),
        _cand("BBB", 2, "eligible_unverified", [_row("size", "pass"),
                                                 _row("geography", "unknown"),
                                                 _row("industry", "unknown")]),
        _cand("CCC", 3, "eligible_unverified", [_row("size", "pass"),
                                                 _row("industry", "unknown"),
                                                 _row("listing", "pass")]),
        _cand("DDD", 4, "eligible_unverified", [_row("size", "pass"),
                                                 _row("industry", "unknown")]),
    ]
    assert ar.pick_candidate(_run(), cands) is None
    assert ar.pick_fallback_candidate(_run(), cands).ticker == "CCC"


@pytest.mark.parametrize("rows,status", [
    ([_row("size", "pass"), _row("industry", "fail")], "eligible_unverified"),
    ([_row("size", "pass"), _row("industry", "fail")], "excluded"),
    ([_row("size", "unknown"), _row("industry", "unknown")], "eligible_unverified"),
    ([_row("industry", "unknown")], "eligible_unverified"),  # no size requirement passed
    ([_row("size", "pass"), _row("industry", "unknown")], "excluded"),
    ([_row("size", "pass")], "eligible_unverified"),  # industry not the open check
])
def test_fallback_is_never_an_industry_fail_or_unverified_size(rows, status):
    from app.services.discovery import auto_report as ar

    assert ar.pick_fallback_candidate(_run(), [_cand("AAA", 1, status, rows)]) is None


def test_fallback_skips_held_back_listings():
    from app.services.discovery import auto_report as ar

    rows = [_row("size", "pass"), _row("industry", "unknown")]
    held = {"anti_convergence": {"novelty_held": [
        {"identity": {"ticker": "AAA", "exchange": "NASDAQ"}}]}}
    cands = [_cand("AAA", 1, "eligible_unverified", rows),
             _cand("BBB", 2, "eligible_unverified", rows)]
    assert ar.pick_fallback_candidate(_run(held), cands).ticker == "BBB"


# The run-level path, on the V3.21.3 sqlite fixtures (imported, not copied).
from tests.test_v3_21_3_auto_report import (  # noqa: E402
    FakeSubmit,
    _go,
    _setup,
    _wire,  # noqa: F401 - autouse fixture
    factory,  # noqa: F401 - fixture
)


def _v319_unknown_industry(size: str = "pass", industry: str = "unknown") -> dict[str, Any]:
    return {"identity": {"ticker": "X", "exchange": "NASDAQ", "company_id": None},
            "eligibility": {"status": "eligible_unverified" if industry == "unknown"
                            else "excluded", "unknown_hard": ["industry"]},
            "constraint_results": [_row("size", size), _row("industry", industry)]}


async def test_run_without_eligible_queues_the_fallback(factory) -> None:  # noqa: ANN001, F811
    run_id = await _setup(factory, [("AAA", _v319_unknown_industry(size="unknown")),
                                    ("BBB", _v319_unknown_industry())])
    submit = FakeSubmit()
    record = await _go(factory, run_id, submit=submit)
    assert record["state"] == "queued" and record["ticker"] == "BBB"
    assert record["reason"] == "industry_unverified_fallback"
    assert record["selection"] == "industry_unverified_fallback"
    assert len(submit.calls) == 1


async def test_run_with_only_industry_fail_queues_nothing(factory) -> None:  # noqa: ANN001, F811
    run_id = await _setup(factory, [("AAA", _v319_unknown_industry(industry="fail"))])
    submit = FakeSubmit()
    record = await _go(factory, run_id, submit=submit)
    assert record["state"] == "skipped" and record["reason"] == "no_eligible_candidate"
    assert submit.calls == []


async def test_an_eligible_candidate_still_wins_over_the_fallback(factory) -> None:  # noqa: ANN001, F811
    eligible = {"identity": {"ticker": "X", "exchange": "NASDAQ", "company_id": None},
                "eligibility": {"status": "eligible"},
                "constraint_results": [_row("size", "pass"), _row("industry", "pass")]}
    run_id = await _setup(factory, [("AAA", _v319_unknown_industry()), ("BBB", eligible)])
    record = await _go(factory, run_id, submit=FakeSubmit())
    assert record["ticker"] == "BBB" and record["reason"] is None
    assert record["selection"] == "eligible"
