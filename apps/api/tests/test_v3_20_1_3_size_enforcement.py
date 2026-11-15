"""V3.20 slice 1.3 — the size mandate at the other enforcement points (spec §3.1).

* report-level ``size_fit`` re-checks the size at ANALYSIS time from the exchange's own
  figure, applies the numeric mandate (not only the bands), and states its as-of;
* a discovery snapshot that cannot be refreshed is used WITH its age, flagged when stale;
* the legacy screener compares bounds in USD and no longer passes an unknown size;
* universe generation carries no market cap, so the dynamic stage stays the gate.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from app.services.director.thesis import ThesisContext, resolve_thesis, size_fit
from app.services.discovery import constraints as cons
from app.services.discovery import fx
from app.services.discovery.directories import DirectoryListing
from app.services.discovery.fx import USD_IDENTITY, FxRate
from app.services.discovery.size_recheck import (
    SizeObservation,
    age_days,
    recheck_market_cap,
    to_size_observation,
)

TODAY = datetime.now(timezone.utc).date()


@pytest.fixture(autouse=True)
def _clear_fx():
    fx._CACHE.clear()
    yield
    fx._CACHE.clear()


def _ctx(**kw) -> ThesisContext:
    base = dict(thesis_text="small cap grid suppliers", size_hints=["micro_cap", "small_cap"],
                discovery_candidate_id="c")
    base.update(kw)
    return ThesisContext(**base)


def _obs(usd: float, *, origin="analysis_time", as_of=None, method="exchange_directory"):
    return SizeObservation(usd, as_of or TODAY.isoformat(), method, "https://asx.example/x",
                           origin)


# --- size_fit -----------------------------------------------------------------------


def test_numeric_mandate_fails_inside_the_band():
    # 1.5bn is small-cap by band, but above a 1bn mandate.
    fit = size_fit(_ctx(size_max_usd=1e9), 1.5e9, observation=_obs(1.5e9))
    assert fit["fits"] is False
    assert fit["mandate"] == {"min_usd": None, "max_usd": 1e9}
    assert "mandate" in fit["note"]


def test_within_the_mandate_fits_and_states_as_of():
    fit = size_fit(_ctx(size_max_usd=2e9), 1.2e9, observation=_obs(1.2e9))
    assert fit["fits"] is True
    assert fit["market_cap_as_of"] == TODAY.isoformat()
    assert fit["market_cap_origin"] == "analysis_time"
    assert fit["market_cap_method"] == "exchange_directory"
    assert fit["stale"] is False


def test_old_snapshot_is_flagged_stale():
    old = (TODAY - timedelta(days=45)).isoformat()
    fit = size_fit(_ctx(size_max_usd=2e9), 1.2e9,
                   observation=_obs(1.2e9, origin="discovery_snapshot", as_of=old))
    assert fit["fits"] is True
    assert fit["stale"] is True
    assert fit["market_cap_age_days"] == 45
    assert "45 days old" in fit["note"]


def test_lower_bound_never_proves_a_ceiling():
    fit = size_fit(_ctx(size_max_usd=2e9), 0.5e9,
                   observation=_obs(0.5e9, method=cons.FLOAT_FLOOR_METHOD))
    assert fit["fits"] is None
    assert "lower bound" in fit["note"]
    above = size_fit(_ctx(size_max_usd=2e9), 5e9,
                     observation=_obs(5e9, method=cons.FLOAT_FLOOR_METHOD))
    assert above["fits"] is False


def test_discovery_and_analysis_values_are_both_shown():
    snap = _obs(1.8e9, origin="discovery_snapshot", as_of=(TODAY - timedelta(days=20)).isoformat())
    fit = size_fit(_ctx(size_max_usd=2e9), 2.3e9, observation=_obs(2.3e9), previous=snap)
    assert fit["fits"] is False
    assert fit["market_cap_at_discovery"]["market_cap_usd"] == 1.8e9
    assert "moved" in fit["note"]


def test_legacy_call_shape_is_unchanged():
    fit = size_fit(_ctx(), 900e6)
    assert fit["fits"] is True
    assert set(fit) >= {"requested", "market_cap_usd", "fits", "bands_usd", "note"}
    assert size_fit(_ctx(size_hints=[]), 900e6) is None
    unknown = size_fit(_ctx(), None)
    assert unknown["fits"] is None


def test_mandate_without_bands_is_still_checked():
    fit = size_fit(_ctx(size_hints=[], size_max_usd=2e9), 3e9)
    assert fit is not None and fit["fits"] is False


def test_age_days():
    assert age_days(None) is None
    assert age_days("garbage") is None
    assert age_days("2026-11-01", today=date(2026, 11, 15)) == 14


# --- resolve_thesis carries the mandate ---------------------------------------------


async def test_resolve_thesis_reads_the_mandate_from_the_intent():
    import contextlib

    cand_id, run_id = uuid.uuid4(), uuid.uuid4()
    candidate = SimpleNamespace(
        id=cand_id, discovery_run_id=run_id, exchange="AU", market_cap_mln=None,
        score_explanation=None, created_at=None,
        thesis_match_json={"theme": "grid", "v319": {"verified_attributes": {
            "market_cap_usd": 1.5e9,
            "market_cap": {"amount": 2.3e9, "currency": "AUD", "as_of": "2026-11-01",
                           "as_of_basis": "fetch_date"},
            "market_cap_method": "exchange_directory"}}},
    )
    run = SimpleNamespace(
        thesis_text="grid suppliers", config_json={},
        parsed_thesis_json={"discovery_intent": {"constraints": [
            {"key": "size", "requested": ["micro_cap", "small_cap"], "max_usd": 2e9,
             "min_usd": None}]}},
    )

    class _Session:
        @contextlib.asynccontextmanager
        async def begin_nested(self):
            yield

        async def get(self, model, key):
            return candidate if key == cand_id else run

    context = await resolve_thesis(_Session(), discovery_candidate_id=cand_id)
    assert context.size_max_usd == 2e9
    assert context.market_cap_usd == 1.5e9
    assert context.market_cap_native == {"amount": 2.3e9, "currency": "AUD",
                                         "as_of": "2026-11-01", "as_of_basis": "fetch_date"}
    assert context.to_dict()["size_max_usd"] == 2e9


# --- recheck_market_cap ---------------------------------------------------------------


ASX_LISTING = DirectoryListing(
    name="GRIDCO LIMITED", ticker="GRD", exchange="AU", isin=None, country="Australia",
    market_cap=2.6e9, currency="AUD", price=None, industry="Capital Goods", mic=None,
    directory="asx", source_url="https://asx.example/directory.csv", tier="T1_exchange",
    as_of=TODAY.isoformat(),
)


async def test_recheck_reads_the_exchange_figure_and_converts(monkeypatch):
    from app.services.discovery import directories

    async def _find(**kw):
        assert kw["ticker"] == "GRD" and kw["venue"] == "AU"
        return ASX_LISTING, None

    monkeypatch.setattr(directories, "find_listing", _find)
    aud = FxRate("AUD", 0.65, TODAY.isoformat(), "DEXUSAL", "https://fred.example")

    async def _rate(currency, as_of=None, **kw):
        return aud, None

    monkeypatch.setattr("app.services.discovery.size_recheck.usd_rate", _rate)
    obs, reason = await recheck_market_cap(name="GridCo", ticker="GRD", venue="AU")
    assert reason is None
    assert obs.market_cap_usd == pytest.approx(2.6e9 * 0.65)
    assert obs.origin == "analysis_time"
    assert obs.native_currency == "AUD"
    assert obs.fx["series"] == "DEXUSAL"


async def test_recheck_without_a_directory_is_a_reason(monkeypatch):
    from app.services.discovery import directories

    async def _find(**kw):
        return None, None

    monkeypatch.setattr(directories, "find_listing", _find)
    obs, reason = await recheck_market_cap(name="X", ticker="X", venue="KS")
    assert obs is None and "no official directory" in reason
    obs, reason = await recheck_market_cap(name="X", ticker=None, venue=None)
    assert obs is None and reason


async def test_recheck_directory_error_is_a_reason(monkeypatch):
    from app.services.discovery import directories

    async def _find(**kw):
        raise ConnectionError

    monkeypatch.setattr(directories, "find_listing", _find)
    obs, reason = await recheck_market_cap(name="X", ticker="X", venue="AU")
    assert obs is None and "ConnectionError" in reason


async def test_to_size_observation_uses_identity_for_usd():
    obs, _ = await to_size_observation(
        cons.MarketCapObservation(amount=1e9, currency="USD", as_of="2026-11-13",
                                  as_of_basis="stated", source_url="u", source_tier="T1",
                                  verified=True, method="exchange_directory"),
        origin="discovery_snapshot",
    )
    assert obs.market_cap_usd == 1e9 and obs.fx is None
    assert USD_IDENTITY.usd_per_unit == 1.0


async def test_to_size_observation_without_rate_is_a_reason():
    obs, reason = await to_size_observation(
        cons.MarketCapObservation(amount=1e9, currency="XYZ", as_of=None,
                                  as_of_basis="fetch_date", source_url="u", source_tier="T1",
                                  verified=True),
        origin="discovery_snapshot",
    )
    assert obs is None and "XYZ" in reason


# --- legacy screener: USD bounds, no unknown pass-through ---------------------------


def _raw(cap, currency):
    return {"ticker": "T", "exchange": "OSE", "name": "T Co", "country": "Norway",
            "sector": "Industrials", "description": "d", "market_cap": cap,
            "currency": currency}


def _build(raw, *, lo=None, hi=None, rates=None):
    from app.services.screener import CompanyScreener

    return CompanyScreener()._build_candidate(
        raw=raw, theme=None, source_tier="T6_model_estimate", data_quality="D_weak_or_stale",
        market_cap_min=lo, market_cap_max=hi, fx_rates=rates,
    )


NOK = FxRate("NOK", 0.09, "2026-11-13", "DEXNOUS", "https://fred.example")


def test_screener_converts_before_the_ceiling():
    # NOK 20,000m ≈ USD 1,800m: under a USD 2,000m ceiling (NOK compared raw would fail).
    assert _build(_raw(20_000.0, "NOK"), hi=2_000.0, rates={"NOK": NOK}).candidate_status \
        != "rejected_by_screen"
    # NOK 30,000m ≈ USD 2,700m: above.
    c = _build(_raw(30_000.0, "NOK"), hi=2_000.0, rates={"NOK": NOK})
    assert c.candidate_status == "rejected_by_screen"
    assert "USD" in c.discovery_reasons[0]


def test_screener_without_a_rate_does_not_pass_the_filter():
    c = _build(_raw(20_000.0, "NOK"), hi=2_000.0, rates={})
    assert c.candidate_status == "needs_data"
    assert any("not verified" in w for w in c.warnings)


def test_screener_unknown_cap_does_not_pass_the_filter():
    c = _build(_raw(None, "USD"), hi=2_000.0)
    assert c.candidate_status == "needs_data"
    assert any("not verified" in w for w in c.warnings)


def test_screener_usd_needs_no_rate():
    assert _build(_raw(999_999.0, "USD"), hi=5_000.0).candidate_status == "rejected_by_screen"
    assert _build(_raw(1_000.0, "USD"), hi=5_000.0).candidate_status != "rejected_by_screen"


def test_screener_without_bounds_is_unchanged():
    assert _build(_raw(None, "NOK")).candidate_status != "rejected_by_screen"


async def test_screener_rates_are_resolved_per_currency(monkeypatch):
    from app.services import company_discovery_service as svc

    calls = []

    async def _rate(currency, as_of=None, **kw):
        calls.append(currency)
        return (NOK, None) if currency == "NOK" else (None, "no series")

    monkeypatch.setattr("app.services.discovery.fx.usd_rate", _rate)
    rates = await svc.screening_fx_rates(["NOK", "NOK", "USD", None, "XYZ"])
    assert rates == {"NOK": NOK, "USD": USD_IDENTITY}
    assert sorted(calls) == ["NOK", "XYZ"]
