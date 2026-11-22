"""V3.21.1 — free-form segment discovery (ADR-107) and size words under a ceiling (ADR-108).

Spec ``docs/v3.21-spec.md`` §1–§2:

* a segment outside the 11 fixed themes ("US small-cap cybersecurity software companies")
  is accepted, its sanitised terms drive recall and the web planner, and a candidate must
  be SHOWN to have exposure to one of them (hard industry constraint);
* purely vague text still needs narrowing;
* the optional model expansion is sanitised, token-checked and failure-tolerant;
* the universe builder skips the sector-wide fallback for a segment;
* under a numeric ceiling, small-size WORDS no longer cut the bands — the number decides.

No network: the research model is a ``FakeDeepSeekTransport``.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.deepseek.transport import FakeDeepSeekTransport
from app.schemas.market_discovery import ThesisDiscoveryRunCreate
from app.services import market_discovery_service as mds
from app.services.discovery import constraints as cons
from app.services.discovery.fx import USD_IDENTITY
from app.services.discovery.intent import HARD, build_intent, intent_from_dict
from app.services.discovery.leads import plan_lead_queries
from app.services.discovery.screening import (
    _terms,
    exposures_from_text,
    screening_question,
    segment_term_patterns,
)
from app.services.discovery.segment_expansion import (
    PROMPT_VERSION,
    expand_intent,
    expand_segment_terms,
    sanitise_proposals,
)
from app.services.market_thesis_parser import (
    _THEME_DISPLAY,
    _THEME_TABLE,
    extract_segment_terms,
    parse_thesis,
    sanitise_segment_term,
)
from app.services.market_universe_builder import _select_registry_entries, build_universe
from app.services.web_research import discovery_planner as dp

CYBER = "US small-cap cybersecurity software companies"
TWO_BN = 2_000_000_000.0


def _cyber(**kw):
    kw.setdefault("default_max_market_cap_usd", TWO_BN)
    return build_intent(CYBER, **kw)


def _cfg(**kw):
    base = {"v3_segment_expansion_enabled": True, "v3_external_search_timeout_seconds": 30}
    base.update(kw)
    return SimpleNamespace(**base)


def _provider(text: str | None = None, *, raises: Exception | None = None):
    transport = FakeDeepSeekTransport(model="fake-research-model", completion_text=text,
                                      raises=raises)
    return SimpleNamespace(transport=transport)


# --- segment terms ------------------------------------------------------------------


def test_cybersecurity_thesis_yields_the_segment_term():
    parsed = parse_thesis(CYBER)
    assert parsed.themes == []
    assert parsed.segment_terms == ["cybersecurity"]
    assert parsed.needs_narrowing is False


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("subsea cable installation vessels", ["subsea cable", "installation vessels"]),
        ("HVDC cable suppliers excluding Chinese", ["hvdc cable"]),
        ("European OT security software vendors", ["ot security"]),
        ("profitable growing vertical farming companies in Germany", ["vertical farming"]),
    ],
)
def test_terms_drop_geography_size_style_and_generic_words(text, expected):
    assert parse_thesis(text).segment_terms == expected


@pytest.mark.parametrize("text", ["good companies", "growth", "best stocks to buy",
                                  "small cap growing european companies",
                                  "some unrelated words with no theme"])
def test_vague_text_still_needs_narrowing(text):
    parsed = parse_thesis(text)
    assert parsed.segment_terms == []
    assert parsed.needs_narrowing is True
    assert build_intent(text).needs_narrowing is True


def test_explicit_industry_keywords_are_sanitised_terms_first():
    terms = extract_segment_terms(
        "water companies", industry_keywords=["Desalination", "<script>", "x"],
    )
    assert terms[0] == "desalination"
    assert "<script>" not in terms and "x" not in terms


def test_terms_are_capped_and_bounded():
    text = " ".join(f"alpha{i} beta{i}," for i in range(12))
    terms = extract_segment_terms(text)
    assert len(terms) <= 6
    assert all(3 <= len(t) <= 40 for t in terms)


@pytest.mark.parametrize("bad", ["a", "x" * 41, "drop table;", "https://x.test", "'quoted'",
                                 "123", None, 7])
def test_sanitiser_refuses(bad):
    assert sanitise_segment_term(bad) is None


def test_sanitiser_canonicalises():
    assert sanitise_segment_term("  Cyber   Security ") == "cyber security"


# --- intent -------------------------------------------------------------------------


def test_intent_builds_a_hard_industry_constraint_on_the_terms():
    intent = _cyber()
    assert intent.segment_terms == ("cybersecurity",)
    assert intent.industry is not None
    assert intent.industry.requested == ("cybersecurity",)
    assert intent.industry.hardness == HARD
    assert not intent.needs_narrowing


def test_intent_round_trips_segment_fields():
    from dataclasses import replace

    intent = replace(_cyber(), segment_synonyms=("cyber security",))
    back = intent_from_dict(intent.to_dict())
    assert back is not None
    assert back.segment_terms == ("cybersecurity",)
    assert back.segment_synonyms == ("cyber security",)
    assert back.size is not None and back.size.words == ("small-cap",)


def test_segment_terms_reach_the_universe_filter():
    assert _cyber().universe_filter()["segment_terms"] == ["cybersecurity"]


# --- fixed themes unchanged -----------------------------------------------------------


@pytest.mark.parametrize("theme", sorted(_THEME_TABLE))
def test_every_fixed_theme_example_has_no_segment(theme):
    for example in _THEME_DISPLAY[theme]["examples"]:
        intent = build_intent(example)
        assert intent.segment_terms == (), example
        assert intent.themes, example
        if intent.industry is not None:
            assert "cybersecurity" not in intent.industry.requested


def test_fixed_theme_universe_unchanged():
    parsed = parse_thesis("European defense suppliers benefiting from NATO spending",
                          region="Europe")
    intent = build_intent("European defense suppliers benefiting from NATO spending",
                          parsed=parsed, region="Europe")
    universe = build_universe({**parsed.to_dict(), **intent.universe_filter()})
    assert "RHM" in {i["ticker"] for i in universe.items}
    assert intent.industry is not None and intent.industry.requested == ("defense",)


def test_materials_thesis_is_not_a_segment():
    intent = build_intent("gallium producers")
    assert intent.materials == ("gallium",)
    assert intent.segment_terms == ()


# --- recall and web planning -----------------------------------------------------------


def test_recall_queries_carry_the_term_and_never_the_raw_text():
    queries = plan_lead_queries(_cyber(), max_queries=4)
    assert queries
    assert all("cybersecurity" in q.text for q in queries)
    assert all(CYBER not in q.text for q in queries)
    assert all("market capitalisation under USD 2 billion" in q.text for q in queries)
    assert all("300 million" not in q.text for q in queries)


def test_recall_names_the_synonyms():
    from dataclasses import replace

    intent = replace(_cyber(), segment_synonyms=("cyber security",))
    queries = plan_lead_queries(intent, max_queries=2)
    assert "cyber security" in queries[0].text


def test_web_planner_uses_the_terms():
    facts = dp.facts_from_intent(_cyber())
    assert facts.nouns() == [("segment", "cybersecurity")]
    plan = dp.build_discovery_plan(facts, mode="standard", max_queries=12)
    texts = [q.request.query for q in plan.queries]
    assert texts and any("cybersecurity" in t for t in texts)
    assert all(CYBER.lower() not in t.lower() for t in texts)
    assert "cybersecurity" in dp.theme_vocabulary_phrases(facts)


def test_screening_question_names_the_term():
    issuer = SimpleNamespace(name="Example Cyber Inc", ticker="EXCY", exchange="NASDAQ")
    assert "cybersecurity" in screening_question(issuer, _cyber())


# --- exposure check --------------------------------------------------------------------


def _listing_pass():
    return cons.verify_listing({"identity_status": "platform_registry", "ticker": "EXCY",
                                "exchange": "NASDAQ"})


def _industry(intent, text):
    exposures = exposures_from_text(text, _terms(intent), source_url="https://issuer.test",
                                    source_tier="issuer", verified=True)
    return cons.verify_industry(intent.industry, exposures)


def test_a_candidate_with_cybersecurity_exposure_passes():
    result = _industry(_cyber(), "We provide cybersecurity software for enterprises.")
    assert result.status == cons.PASS
    assert result.value["matched"] == ["cybersecurity"]


def test_a_candidate_without_exposure_is_never_shortlisted():
    intent = _cyber()
    result = _industry(intent, "We design graphics processors for gaming and data centres.")
    assert result.status == cons.UNKNOWN
    eligibility = cons.decide_eligibility([_listing_pass(), result])
    assert eligibility.status != cons.ELIGIBLE


def test_a_denied_exposure_is_excluded():
    result = _industry(_cyber(), "The company has no cybersecurity business.")
    assert result.status == cons.FAIL
    assert cons.decide_eligibility([_listing_pass(), result]).status == cons.EXCLUDED


def test_a_synonym_match_reports_under_the_requested_term():
    from dataclasses import replace

    intent = replace(_cyber(), segment_synonyms=("cyber security",))
    result = _industry(intent, "A provider of cyber-security services to utilities.")
    assert result.status == cons.PASS
    assert result.value["matched"] == ["cybersecurity"]


def test_segment_patterns_are_word_bounded():
    patterns = segment_term_patterns(("vessel",))
    assert patterns["vessel"].search("installation vessels")
    assert not patterns["vessel"].search("vesselfoo")


# --- universe --------------------------------------------------------------------------


def test_universe_skips_the_sector_fallback_for_a_segment():
    parsed = parse_thesis(CYBER, sector="Technology")
    intent = build_intent(CYBER, parsed=parsed, sector="Technology")
    universe = build_universe({**parsed.to_dict(), **intent.universe_filter()})
    tickers = {i["ticker"] for i in universe.items}
    assert "NVDA" not in tickers and "AMD" not in tickers


def test_sector_fallback_still_works_without_a_segment():
    selected = _select_registry_entries({"sectors": ["Technology"]})
    assert any(e["ticker"] == "NVDA" for _t, e in selected)


def test_registry_keyword_match_against_terms():
    selected = _select_registry_entries({"sectors": ["Energy"], "segment_terms": ["uranium"]})
    # Only entries whose registry industry names the term: no Energy-wide fill.
    assert {e["ticker"] for _t, e in selected} >= {"CCJ", "UEC"}
    assert all("uranium" in e["industry"].lower() for _t, e in selected)


# --- model expansion ---------------------------------------------------------------------


async def test_expansion_is_sanitised_and_token_checked():
    answer = json.dumps({
        "synonyms": ["cyber security", "security software", "information assurance",
                     "<b>hack</b>", "security", "cybersecurity", "endpoint protection"],
        "adjacent_industries": ["identity management", "cyber insurance"],
    })
    provider = _provider(answer)
    out = await expand_segment_terms(["cybersecurity"], text=CYBER, cfg=_cfg(),
                                     provider=provider)
    assert out.status == "completed"
    assert out.prompt_version == PROMPT_VERSION
    assert out.model == "fake-research-model"
    # Kept: share a word with the terms or the text. Dropped: unrelated, markup, the
    # term itself, and a bare fragment of the term.
    assert out.synonyms == ["cyber security", "security software"]
    assert out.adjacent_industries == ["cyber insurance"]
    assert out.dropped >= 5
    transport = provider.transport
    assert transport.json_mode_calls == [True]
    system, user = transport.completions[0]
    assert CYBER not in user and "cybersecurity" in user


async def test_expansion_failure_means_terms_only():
    out = await expand_segment_terms(["cybersecurity"], text=CYBER, cfg=_cfg(),
                                     provider=_provider(raises=TimeoutError()))
    assert out.status == "failed" and out.error == "TimeoutError"
    assert out.kept == []
    intent, record = await expand_intent(_cyber(), cfg=_cfg(),
                                         provider=_provider(raises=RuntimeError("x")))
    assert intent.segment_terms == ("cybersecurity",) and intent.segment_synonyms == ()
    assert record.status == "failed"


async def test_expansion_unparseable_answer_is_a_failure_not_a_crash():
    out = await expand_segment_terms(["cybersecurity"], text=CYBER, cfg=_cfg(),
                                     provider=_provider("not json at all"))
    assert out.status == "failed" and out.error == "unparseable_json"


async def test_expansion_flag_off_and_no_provider():
    off = await expand_segment_terms(["cybersecurity"], text=CYBER,
                                     cfg=_cfg(v3_segment_expansion_enabled=False),
                                     provider=_provider("{}"))
    assert off.status == "disabled"
    none = await expand_segment_terms(["cybersecurity"], text=CYBER, cfg=_cfg(),
                                      provider=SimpleNamespace(transport=None))
    assert none.status == "unavailable"


def test_sanitise_proposals_rejects_non_lists():
    assert sanitise_proposals("cyber", terms=["cybersecurity"], text=CYBER, limit=8) == ([], 0)


def test_expansion_flag_defaults_on():
    from app.core.config import Settings

    assert Settings.model_fields["v3_segment_expansion_enabled"].default is True


# --- run creation and the API ----------------------------------------------------------------


def _mock_session():
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()

    async def _refresh(run):
        now = datetime.now(UTC)
        run.created_at = run.created_at or now
        run.updated_at = run.updated_at or now

    db.refresh = AsyncMock(side_effect=_refresh)
    return db


async def test_thesis_run_stores_terms_and_the_expansion_record(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "v3_dynamic_discovery_enabled", True)
    answer = json.dumps({"synonyms": ["cyber security"], "adjacent_industries": []})
    with patch("app.services.agents.routing.research_provider_for",
               return_value=_provider(answer)):
        run = await mds.create_pending_thesis_run(
            _mock_session(), ThesisDiscoveryRunCreate(thesis_text=CYBER)
        )
    stored = run.parsed_thesis_json
    assert stored["segment_terms"] == ["cybersecurity"]
    assert stored["discovery_intent"]["segment_terms"] == ["cybersecurity"]
    assert stored["discovery_intent"]["segment_synonyms"] == ["cyber security"]
    assert stored["segment_expansion"]["status"] == "completed"
    assert stored["segment_expansion"]["prompt_version"] == PROMPT_VERSION
    industry = next(c for c in stored["discovery_intent"]["constraints"]
                    if c["key"] == "industry")
    assert industry["requested"] == ["cybersecurity"] and industry["hardness"] == HARD


async def test_segment_run_without_dynamic_discovery_points_at_supported_themes(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "v3_dynamic_discovery_enabled", False)
    with pytest.raises(ValueError) as exc:
        await mds.create_pending_thesis_run(
            _mock_session(), ThesisDiscoveryRunCreate(thesis_text=CYBER)
        )
    assert "supported-themes" in str(exc.value)
    assert "narrowing" not in str(exc.value).lower()


@pytest.fixture
async def api(monkeypatch):
    from httpx import ASGITransport, AsyncClient

    from app.core.config import settings
    from app.db.session import get_db
    from app.main import app

    monkeypatch.setattr(settings, "v3_dynamic_discovery_enabled", True)
    monkeypatch.setattr(settings, "v3_durable_jobs_enabled", False)
    db = _mock_session()
    app.dependency_overrides[get_db] = lambda: db
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac
    app.dependency_overrides.clear()


async def test_cybersecurity_thesis_run_is_accepted(api):
    with patch.object(mds, "process_discovery_run_task", AsyncMock()):
        res = await api.post("/api/v1/market-discovery/thesis-runs",
                             json={"thesis_text": CYBER, "provider_name": "free_real"})
    assert res.status_code == 201, res.text
    intent = res.json()["parsed_thesis_json"]["discovery_intent"]
    assert intent["segment_terms"] == ["cybersecurity"]


async def test_vague_thesis_run_is_still_422(api):
    res = await api.post("/api/v1/market-discovery/thesis-runs",
                         json={"thesis_text": "best stocks to buy", "provider_name": "free_real"})
    assert res.status_code == 422
    assert "narrowing" in res.json()["detail"].lower()


async def test_parse_thesis_preview_shows_terms(api):
    res = await api.post("/api/v1/market-discovery/parse-thesis",
                         json={"thesis": "European water companies",
                               "industry_keywords": ["desalination"]})
    assert res.status_code == 200
    body = res.json()
    assert body["segment_terms"][0] == "desalination"
    assert body["segment_synonyms"] == []
    assert body["needs_narrowing"] is False


# --- ADR-108: size words under a ceiling --------------------------------------------------------


def _cap(amount: float):
    return cons.MarketCapObservation(
        amount=amount, currency="USD", as_of="2026-11-20", as_of_basis="stated",
        source_url="https://example.test/listing", source_tier="T1", verified=True,
        method="exchange_directory",
    )


@pytest.mark.parametrize(
    ("name", "usd", "status"),
    [("EGAN", 0.14e9, cons.PASS), ("SMSI", 0.01e9, cons.PASS), ("AGYS", 3.1e9, cons.FAIL)],
)
def test_micro_caps_pass_under_the_2bn_mandate_with_small_cap_words(name, usd, status):
    size = _cyber().size
    assert size is not None
    assert size.requested == ("micro_cap", "small_cap")
    assert size.max_usd == TWO_BN and size.hardness == HARD
    assert size.words == ("small-cap",)
    assert cons.verify_size(size, _cap(usd), USD_IDENTITY).status == status, name


def test_mid_cap_words_keep_todays_behaviour_with_a_warning():
    intent = build_intent("mid-cap grid equipment makers", default_max_market_cap_usd=TWO_BN)
    assert intent.size is not None
    assert intent.size.requested == ("mid_cap",)
    assert intent.size.max_usd is None
    assert any("not entirely below" in w for w in intent.warnings)


def test_large_cap_words_under_an_explicit_ceiling_still_warn():
    intent = build_intent("large-cap grid equipment makers", max_market_cap_usd=TWO_BN)
    assert intent.size is not None and intent.size.max_usd == TWO_BN
    assert any("entirely above" in w for w in intent.warnings)


def test_explicit_bucket_stays_a_hard_band():
    intent = build_intent("grid equipment makers", market_cap_bucket="small_cap",
                          default_max_market_cap_usd=TWO_BN)
    assert intent.size is not None
    assert intent.size.requested == ("small_cap",) and intent.size.hardness == HARD
    assert intent.size.max_usd is None
    capped = build_intent("grid equipment makers", market_cap_bucket="small_cap",
                          max_market_cap_usd=1e9)
    assert capped.size is not None and capped.size.requested == ("small_cap",)


def test_smaller_under_an_explicit_ceiling_lets_the_number_decide():
    intent = build_intent("smaller grid equipment makers", max_market_cap_usd=5e9)
    assert intent.size is not None
    assert intent.size.requested == ("micro_cap", "small_cap", "mid_cap")
    assert intent.size.max_usd == 5e9


def test_micro_cap_words_under_the_default_ceiling():
    intent = build_intent("micro-cap grid equipment makers", default_max_market_cap_usd=TWO_BN)
    assert intent.size is not None
    assert intent.size.requested == ("micro_cap", "small_cap")
    assert intent.size.words == ("micro-cap",)
