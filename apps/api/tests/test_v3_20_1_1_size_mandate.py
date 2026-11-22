"""V3.20 slice 1.1 — the USD 2bn size mandate (owner decision D1).

* default ceiling USD 2bn, applied only when nothing else asked for a size;
* an explicit ceiling narrows a size the thesis already named, or is the size alone;
* null removes the ceiling;
* a verified cap above the ceiling FAILS, below it PASSES, near it is borderline;
* an unverified cap is UNKNOWN, and such a record is kept OFF the shortlist.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.schemas.market_discovery import ThesisDiscoveryRunCreate
from app.services.discovery import constraints as cons
from app.services.discovery.fx import USD_IDENTITY, FxRate
from app.services.discovery.intent import (
    HARD,
    Constraint,
    apply_size_ceiling,
    buckets_below,
    build_intent,
    intent_from_dict,
)
from app.services.discovery.pipeline import CandidateRecord, size_unverified
from app.services.market_discovery_service import resolve_size_mandate

TWO_BN = 2_000_000_000.0


def _usd_rate() -> FxRate:
    return USD_IDENTITY


def _obs(amount: float, *, method: str = "exchange_directory", currency: str = "USD"):
    return cons.MarketCapObservation(
        amount=amount, currency=currency, as_of="2026-11-13", as_of_basis="stated",
        source_url="https://example.test/listing", source_tier="T1", verified=True,
        method=method,
    )


# --- intent ------------------------------------------------------------------


def test_default_ceiling_applies_when_the_thesis_names_no_size():
    intent = build_intent("grid transformer suppliers in Europe",
                          default_max_market_cap_usd=TWO_BN)
    assert intent.size is not None
    assert intent.size.max_usd == TWO_BN
    assert intent.size.hardness == HARD
    assert intent.size.requested == ("micro_cap", "small_cap")
    assert "default size mandate" in intent.size.hardness_basis


def test_default_ceiling_does_not_override_a_size_the_thesis_names():
    intent = build_intent("mid-cap grid equipment makers", default_max_market_cap_usd=TWO_BN)
    assert intent.size is not None
    assert intent.size.requested == ("mid_cap",)
    assert intent.size.max_usd is None


def test_explicit_ceiling_narrows_a_named_size():
    intent = build_intent("small-cap grid equipment makers", max_market_cap_usd=1e9)
    # V3.21.1 (ADR-108): under a ceiling a small-size word no longer cuts the bands; the
    # ceiling decides (micro caps under USD 1bn are in), and the words are kept.
    assert intent.size.requested == ("micro_cap", "small_cap")
    assert intent.size.words == ("small-cap",)
    assert intent.size.max_usd == 1e9


def test_explicit_ceiling_alone_becomes_the_size_constraint():
    intent = build_intent("HVDC cable suppliers", max_market_cap_usd=5e9)
    assert intent.size.requested == ("micro_cap", "small_cap", "mid_cap")
    assert intent.size.max_usd == 5e9


def test_explicit_ceiling_below_a_named_size_warns():
    size = Constraint("size", ("large_cap",), HARD, "words", "large cap")
    out, notes = apply_size_ceiling(size, max_market_cap_usd=TWO_BN,
                                    default_max_market_cap_usd=None)
    assert out.max_usd == TWO_BN
    assert notes and "entirely above" in notes[0]


def test_no_ceiling_when_neither_is_given():
    assert build_intent("HVDC cable suppliers").size is None


def test_ceiling_survives_storage_round_trip():
    intent = build_intent("grid transformer suppliers", default_max_market_cap_usd=TWO_BN)
    back = intent_from_dict(intent.to_dict())
    assert back is not None and back.size is not None
    assert back.size.max_usd == TWO_BN


def test_buckets_below():
    assert buckets_below(TWO_BN) == ("micro_cap", "small_cap")
    assert buckets_below(250e6) == ("micro_cap",)


# --- API field semantics -------------------------------------------------------


def test_omitted_field_uses_platform_default(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "discovery_default_max_market_cap_usd", TWO_BN)
    payload = ThesisDiscoveryRunCreate(thesis_text="grid suppliers")
    assert resolve_size_mandate(payload) == {
        "explicit_max_usd": None, "default_max_usd": TWO_BN, "source": "default",
    }


def test_explicit_number_is_explicit():
    payload = ThesisDiscoveryRunCreate(thesis_text="grid suppliers", max_market_cap_usd=5e8)
    assert resolve_size_mandate(payload)["explicit_max_usd"] == 5e8
    assert resolve_size_mandate(payload)["source"] == "explicit"


def test_explicit_null_removes_the_ceiling():
    payload = ThesisDiscoveryRunCreate(thesis_text="grid suppliers", max_market_cap_usd=None)
    assert resolve_size_mandate(payload) == {
        "explicit_max_usd": None, "default_max_usd": None, "source": "none",
    }


def test_disabled_default_setting_means_none(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "discovery_default_max_market_cap_usd", None)
    payload = ThesisDiscoveryRunCreate(thesis_text="grid suppliers")
    assert resolve_size_mandate(payload)["source"] == "none"


@pytest.mark.parametrize("bad", [0, -1, 2e13])
def test_field_bounds(bad):
    with pytest.raises(ValueError):
        ThesisDiscoveryRunCreate(thesis_text="grid suppliers", max_market_cap_usd=bad)


# --- verification ----------------------------------------------------------------


def _mandate(max_usd: float = TWO_BN) -> Constraint:
    return Constraint("size", buckets_below(max_usd), HARD, "mandate", "< 2bn", max_usd=max_usd)


def test_verified_cap_below_ceiling_passes():
    r = cons.verify_size(_mandate(), _obs(1.2e9), _usd_rate())
    assert r.status == cons.PASS
    assert r.value["mandate"] == {"min_usd": None, "max_usd": TWO_BN}


def test_verified_cap_above_ceiling_fails():
    r = cons.verify_size(_mandate(), _obs(3.4e9), _usd_rate())
    assert r.status == cons.FAIL


def test_ceiling_inside_a_band_fails_on_the_number():
    # 1.5bn is small-cap (band passes) but above a 1bn ceiling.
    r = cons.verify_size(_mandate(1e9), _obs(1.5e9), _usd_rate())
    assert r.status == cons.FAIL
    assert "outside the size mandate" in r.basis


def test_cap_at_ceiling_is_borderline():
    r = cons.verify_size(_mandate(1e9), _obs(0.95e9), _usd_rate())
    assert r.status == cons.PASS
    assert r.borderline is True


def test_unverified_cap_is_unknown():
    r = cons.verify_size(_mandate(), None, None)
    assert r.status == cons.UNKNOWN


def test_non_usd_cap_converts_before_the_ceiling():
    gbp = FxRate(currency="GBP", usd_per_unit=1.27, rate_date="2026-11-13",
                 series="DEXUSUK", source_url="https://fred.example")
    # 1.7bn GBP ≈ 2.16bn USD → above the 2bn ceiling
    assert cons.verify_size(_mandate(), _obs(1.7e9, currency="GBP"), gbp).status == cons.FAIL
    # 1.4bn GBP ≈ 1.78bn USD → below
    assert cons.verify_size(_mandate(), _obs(1.4e9, currency="GBP"), gbp).status == cons.PASS


def test_public_float_floor_can_only_fail_a_ceiling():
    above = cons.verify_size(_mandate(), _obs(5e9, method=cons.FLOAT_FLOOR_METHOD), _usd_rate())
    assert above.status == cons.FAIL
    below = cons.verify_size(_mandate(), _obs(0.5e9, method=cons.FLOAT_FLOOR_METHOD), _usd_rate())
    assert below.status == cons.UNKNOWN


def test_open_band_with_ceiling_is_never_proved_by_a_floor():
    size = Constraint("size", ("mega_cap",), HARD, "w", "mega", max_usd=TWO_BN)
    r = cons.verify_size(size, _obs(900e9, method=cons.FLOAT_FLOOR_METHOD), _usd_rate())
    assert r.status == cons.FAIL


def test_band_only_constraint_is_unchanged():
    size = Constraint("size", ("small_cap",), HARD, "w", "small cap")
    r = cons.verify_size(size, _obs(1.5e9), _usd_rate())
    assert r.status == cons.PASS
    assert "mandate" not in r.value


# --- shortlist ---------------------------------------------------------------------


def _record(size_status: str, hardness: str = HARD) -> CandidateRecord:
    size = cons.ConstraintResult("size", ["small_cap"], hardness, size_status,
                                 value={"usd": 1e9} if size_status == cons.PASS else None,
                                 sources=[{"url": "u"}] if size_status == cons.PASS else [])
    return SimpleNamespace(results=[size])  # type: ignore[return-value]


def test_size_unverified_predicate():
    assert size_unverified(_record(cons.UNKNOWN)) is True
    assert size_unverified(_record(cons.PASS)) is False
    assert size_unverified(_record(cons.UNKNOWN, hardness="soft")) is False
    assert size_unverified(SimpleNamespace(results=[])) is False  # type: ignore[arg-type]


# --- the whole dynamic stage under a mandate (V3.19.4 harness) -------------------

from tests.test_v3_19_4_dynamic_discovery import (  # noqa: E402
    _clear_fx_cache,  # noqa: F401 - autouse fixture, imported to apply here
    _curated,
    _fetch,
    _Provider,
    _Session,
)


async def _mandate_stage(thesis: str, **intent_kwargs):
    from app.core.config import settings
    from app.services.discovery.pipeline import run_dynamic_stage

    return await run_dynamic_stage(
        _Session(), intent=build_intent(thesis, **intent_kwargs), run_universe=_curated(),
        cfg=settings, provider=_Provider(), fetcher=_fetch, max_candidates=10,
    )


async def test_mandate_keeps_unverified_size_off_the_shortlist():
    stage = await _mandate_stage("growing european luxury companies",
                                 default_max_market_cap_usd=TWO_BN)
    shortlisted = {r.identity.ticker for r in stage.candidates}
    unverified = {r.identity.ticker for r in stage.size_unverified}
    assert "MONC" in unverified and "MONC" not in shortlisted
    assert "MEX" in shortlisted  # verified small cap under 2bn
    excluded = {r.identity.ticker for r in stage.excluded}
    assert "KER" in excluded  # verified large cap above the ceiling
    payload = stage.to_dict()
    assert {r["identity"]["ticker"] for r in payload["size_unverified"]} == unverified
    assert all("size" in r["unknown_constraints"] for r in payload["size_unverified"])
    assert stage.funnel["size_unverified"] == len(unverified)
    assert any("size unverified" in w for w in stage.warnings)


async def test_words_only_size_keeps_the_v319_ranking():
    stage = await _mandate_stage("small cap growing european luxury companies")
    assert stage.size_unverified == []
    assert "size_unverified" not in stage.to_dict()
    assert "size_unverified" not in stage.funnel


# --- the default setting parses safely from the environment -----------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [("", None), ("none", None), ("OFF", None), ("0", None), ("-1", None),
     ("500000000", 5e8), ("2e9", 2e9)],
)
def test_default_setting_env_parsing(monkeypatch, raw, expected):
    from app.core.config import Settings

    monkeypatch.setenv("DISCOVERY_DEFAULT_MAX_MARKET_CAP_USD", raw)
    assert Settings().discovery_default_max_market_cap_usd == expected


def test_default_setting_when_unset(monkeypatch):
    from app.core.config import Settings

    monkeypatch.delenv("DISCOVERY_DEFAULT_MAX_MARKET_CAP_USD", raising=False)
    assert Settings().discovery_default_max_market_cap_usd == 2e9
