"""The shortlist cap keeps the best-fitting names, and says which it cut (ADR-073).

Live run E2 (cross-sector QA campaign): 23 candidates met the requirements and the cap
was 15. The sort before the cap ignored thesis relevance, so ties fell to insertion
order (curated, then held, then web). Six of the seven web candidates whose theme was
verified were cut, and nothing in the run said so.
"""

from __future__ import annotations

from app.core.config import settings
from app.services.discovery import constraints as c
from app.services.discovery.intent import build_intent
from app.services.discovery.pipeline import run_dynamic_stage, shortlist
from tests.test_v3_19_4_dynamic_discovery import (
    LUXURY,
    _curated,
    _fetch,
    _Provider,
    _Session,
)


async def _stage(cap: int, relevance=None):  # noqa: ANN001, ANN202
    return await run_dynamic_stage(
        _Session(), intent=build_intent(LUXURY), run_universe=_curated(), cfg=settings,
        provider=_Provider(), fetcher=_fetch, max_candidates=cap, relevance=relevance,
    )


def test_relevance_breaks_a_tie_within_a_tier() -> None:
    tier = c.Eligibility(status=c.ELIGIBLE_UNVERIFIED, reasons=[], hard_passes=2,
                         soft_passes=1, unknown_hard=[], failed_soft=[])
    assert c.ranking_key(tier, 65.0) < c.ranking_key(tier, 42.5)
    # Eligibility and verified passes still come first: fit never lifts an unverified
    # company over an eligible one.
    eligible = c.Eligibility(status=c.ELIGIBLE, reasons=[], hard_passes=2, soft_passes=1,
                             unknown_hard=[], failed_soft=[])
    assert c.ranking_key(eligible, 0.0) < c.ranking_key(tier, 100.0)


async def test_a_name_beyond_the_cap_is_stated() -> None:
    full = await _stage(10)
    assert full.funnel["beyond_cap"] == 0
    capped = await _stage(1)
    assert capped.funnel["returned"] == 1
    assert capped.funnel["beyond_cap"] == len(full.candidates) - 1 >= 1
    cut = full.candidates[1]
    warning = next(w for w in capped.warnings if "beyond the shortlist cap of 1" in w)
    assert f"{cut.identity.exchange}:{cut.identity.ticker}" in warning


async def test_the_scorer_is_consulted_for_every_included_record() -> None:
    seen: list[str] = []

    def relevance(record):  # noqa: ANN001, ANN202
        seen.append(record.identity.ticker)
        return 50.0

    stage = await _stage(10, relevance)
    assert sorted(seen) == sorted(r.identity.ticker for r in stage.candidates)


async def test_a_failing_scorer_degrades_to_the_old_order() -> None:
    def broken(record):  # noqa: ANN001, ANN202
        raise RuntimeError("scorer down")

    baseline = await _stage(10)
    stage = await _stage(10, broken)
    assert [r.identity.ticker for r in stage.candidates] == [
        r.identity.ticker for r in baseline.candidates]


def _rec(ticker: str, status: str = c.ELIGIBLE_UNVERIFIED):  # noqa: ANN202
    from types import SimpleNamespace

    return SimpleNamespace(
        identity=SimpleNamespace(ticker=ticker, exchange="AU"),
        eligibility=c.Eligibility(status=status, reasons=[], hard_passes=2, soft_passes=1,
                                  unknown_hard=[], failed_soft=[]),
    )


def test_fit_decides_which_tier_mate_survives_the_cap() -> None:
    # Inserted curated-first, as the stage does; the web lead fits the thesis better.
    curated, held, web = _rec("CURX"), _rec("HELX"), _rec("WEBX")
    fit = {"CURX": 20.0, "HELX": 30.0, "WEBX": 65.0}
    kept, cut = shortlist([curated, held, web], 2, lambda r: fit[r.identity.ticker])
    assert [r.identity.ticker for r in kept] == ["WEBX", "HELX"]
    assert [r.identity.ticker for r in cut] == ["CURX"]
    # Without a scorer the old insertion order decides: the web lead is the one cut.
    kept, cut = shortlist([curated, held, web], 2)
    assert [r.identity.ticker for r in cut] == ["WEBX"]
    # Fit never lifts an unverified company over an eligible one.
    eligible = _rec("ELGX", c.ELIGIBLE)
    kept, _ = shortlist([web, eligible], 1, lambda r: fit.get(r.identity.ticker, 0.0))
    assert kept[0].identity.ticker == "ELGX"


async def test_the_cap_warning_survives_truncation() -> None:
    capped = await _stage(1)
    assert "beyond the shortlist cap of 1" in capped.warnings[0]
