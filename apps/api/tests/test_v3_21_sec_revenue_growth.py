"""V3.21 acceptance: growth from a US filer's own XBRL, and the widened auto-report fallback.

Staging run 09567859 (2026-11-22): "US small-cap cybersecurity software companies with
growing recurring revenue" verified Rapid7 and OneSpan on size and on cybersecurity
exposure, but "growing" was a hard growth check nothing could verify, so both stayed
eligible_unverified and the run was not auto-reported.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from types import SimpleNamespace

from app.services.discovery import constraints as cons
from app.services.discovery.auto_report import is_industry_fallback
from app.services.discovery.directories import annual_revenue_pair, sec_revenue_growth
from app.services.sources.document_fetcher import DocumentFetchResult

FIXTURE = Path(__file__).parent / "fixtures" / "sec_growth" / "ospn_revenue.json"


def _fact(start: str, end: str, val: float, form: str = "10-K", filed: str = "2026-02-20"):
    return {"start": start, "end": end, "val": val, "form": form, "fp": "FY", "filed": filed}


def _fetcher(by_concept: dict[str, dict | None]):
    seen: list[str] = []

    async def fetch(url, **kw):  # noqa: ANN001, ANN202
        seen.append(url)
        assert kw["allowed_domains"] == ("data.sec.gov",)
        concept = url.rsplit("/", 1)[-1].removesuffix(".json")
        body = by_concept.get(concept)
        return DocumentFetchResult(
            requested_url=url, final_url=url, status_code=200 if body else 404,
            content_type="application/json", document_type="html",
            content=json.dumps(body).encode() if body else b"")

    fetch.seen = seen  # type: ignore[attr-defined]
    return fetch


def test_the_real_onespan_payload_gives_two_consecutive_fiscal_years() -> None:
    pair = annual_revenue_pair(json.loads(FIXTURE.read_text()))
    assert pair is not None
    newer, older = pair
    assert newer["_end"] > older["_end"]
    assert 300 <= (newer["_end"] - older["_end"]).days <= 380
    assert newer["form"].startswith("10-K") and older["form"].startswith("10-K")


def test_quarters_stubs_and_gaps_are_not_a_pair() -> None:
    quarter = _fact("2026-07-01", "2026-09-30", 50.0, form="10-Q")
    assert annual_revenue_pair({"units": {"USD": [quarter]}}) is None
    gap = [_fact("2023-01-01", "2023-12-31", 100.0), _fact("2025-01-01", "2025-12-31", 120.0)]
    assert annual_revenue_pair({"units": {"USD": gap}}) is None
    stub = [_fact("2025-07-01", "2025-12-31", 60.0), _fact("2024-01-01", "2024-12-31", 100.0)]
    assert annual_revenue_pair({"units": {"USD": stub}}) is None


def test_a_restated_year_uses_the_latest_filing() -> None:
    facts = [_fact("2024-01-01", "2024-12-31", 100.0, filed="2025-02-20"),
             _fact("2024-01-01", "2024-12-31", 90.0, filed="2026-02-20"),
             _fact("2025-01-01", "2025-12-31", 120.0, filed="2026-02-20")]
    newer, older = annual_revenue_pair({"units": {"USD": facts}})
    assert (newer["_val"], older["_val"]) == (120.0, 90.0)


async def test_growth_is_a_verified_t1_revenue_pair() -> None:
    facts = {"units": {"USD": [_fact("2024-01-01", "2024-12-31", 100.0),
                               _fact("2025-01-01", "2025-12-31", 125.0)]}}
    fetch = _fetcher({"RevenueFromContractWithCustomerExcludingAssessedTax": None,
                      "Revenues": facts})
    obs = await sec_revenue_growth("884144", fetcher=fetch, today=date(2026, 11, 22))
    assert obs is not None and obs.verified and obs.metric == "revenue_pair"
    assert obs.growth_pct == 25.0
    assert obs.source_tier == "T1_primary_filing"
    assert obs.source_url.endswith("/us-gaap/Revenues.json")
    assert len(fetch.seen) == 2  # stops at the first concept with a pair


async def test_no_cik_no_pair_or_a_lapsed_filer_is_none() -> None:
    assert await sec_revenue_growth(None, fetcher=_fetcher({})) is None
    assert await sec_revenue_growth("884144", fetcher=_fetcher({})) is None
    old = {"units": {"USD": [_fact("2019-01-01", "2019-12-31", 100.0),
                             _fact("2020-01-01", "2020-12-31", 125.0)]}}
    fetch = _fetcher({"RevenueFromContractWithCustomerExcludingAssessedTax": old})
    assert await sec_revenue_growth("884144", fetcher=fetch, today=date(2026, 11, 22)) is None


async def test_an_unreadable_response_is_none() -> None:
    async def boom(url, **kw):  # noqa: ANN001, ANN202
        raise RuntimeError("network")

    assert await sec_revenue_growth("884144", fetcher=boom) is None


def _candidate(results: list[dict], status: str = "eligible_unverified") -> SimpleNamespace:
    return SimpleNamespace(
        exchange="US", ticker="RPD",
        thesis_match_json={"v319": {"eligibility": {"status": status},
                                    "constraint_results": results}})


def _r(key: str, status: str) -> dict:
    return {"key": key, "status": status, "hardness": "hard"}


def test_growth_unknown_alone_may_fall_back() -> None:
    ok = [_r("listing", "pass"), _r("geography", "pass"), _r("industry", "pass"),
          _r("size", "pass"), _r("growth", "unknown")]
    assert is_industry_fallback(_candidate(ok), set())


def test_industry_and_growth_unknown_may_fall_back() -> None:
    ok = [_r("size", "pass"), _r("industry", "unknown"), _r("growth", "unknown")]
    assert is_industry_fallback(_candidate(ok), set())


def test_size_geography_or_a_failed_check_never_fall_back() -> None:
    assert not is_industry_fallback(_candidate([_r("size", "unknown"), _r("growth", "unknown")]), set())
    assert not is_industry_fallback(_candidate([_r("size", "pass"), _r("geography", "unknown")]), set())
    assert not is_industry_fallback(_candidate([_r("size", "pass"), _r("growth", "fail")]), set())
    assert not is_industry_fallback(_candidate([_r("size", "pass"), _r("industry", "pass")]), set())


def test_classify_growth_reads_the_sec_pair() -> None:
    obs = cons.growth_from_revenue_pair(125.0, 100.0, period="FY2025", base_period="FY2024",
                                        source_url="u", source_tier="T1_primary_filing",
                                        verified=True)
    status, chosen, _ = cons.classify_growth([obs])
    assert chosen is obs and status != cons.GROWTH_NOT_ESTABLISHED
