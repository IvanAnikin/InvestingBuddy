"""Cross-sector QA campaign 2026-11 — SEC XBRL period, perimeter and measure regressions.

Found on a live Honeywell report after its Aerospace spin-off and 1-for-2 share
consolidation (2026-06-29). Every case is a SHAPE of companyfacts data, built here
synthetically; no issuer is special-cased.

1. The prior fiscal year was chosen by the XBRL ``fy`` field — the fiscal year of the
   FILING — so the restated comparative in this year's 10-K was skipped and the value as
   originally filed a year earlier was used: revenue "fell 2.7%" when it grew 7.9%.
2. The latest 10-Q was never used: every US report showed only the annual bundle.
3. A filer's total segment profit, tagged ``OperatingIncomeLoss``, was presented as group
   operating income with a "group operating margin".
4. A newer filing's restatement of earlier periods (a recast perimeter, a share
   consolidation) was not noticed, so pre-spin figures and pre-consolidation EPS were
   presented as current.
"""

from __future__ import annotations

from typing import Any

from app.integrations.sec_fundamentals_normalizer import (
    BASIS_RECAST,
    BASIS_SHARE_CONSOLIDATION,
    BASIS_SHARE_SPLIT,
    basis_changes_since_annual,
    latest_quarter_facts,
    normalize_company_facts,
    operating_income_excess,
)
from app.services.agent_tools.peers import statement_items

K25 = "0000000001-26-000010"  # FY2025 10-K, filed 2026-02-17
K24 = "0000000001-25-000010"  # FY2024 10-K, filed 2025-02-14
Q325 = "0000000001-25-000090"  # Q3 2025 10-Q
Q326 = "0000000001-26-000090"  # Q3 2026 10-Q, filed 2026-10-22


def _e(val: float, end: str, accn: str, filed: str, form: str, fy: int, fp: str,
       start: str | None = None) -> dict[str, Any]:
    row: dict[str, Any] = {"val": val, "end": end, "accn": accn, "filed": filed,
                           "form": form, "fy": fy, "fp": fp}
    if start:
        row["start"] = start
    return row


def _facts(**concepts: list[dict[str, Any]]) -> dict[str, Any]:
    us_gaap: dict[str, Any] = {}
    for name, rows in concepts.items():
        unit = "USD/shares" if name.startswith("EarningsPerShare") else "USD"
        us_gaap[name] = {"units": {unit: rows}}
    return {"cik": 1, "facts": {"us-gaap": us_gaap, "dei": {}}}


M = 1_000_000


def _annual_rows(concept_vals: dict[str, tuple[float, float, float]]) -> dict[str, list]:
    """FY2025 10-K with FY2025 + restated FY2024; FY2024 10-K with FY2024 as filed."""
    out: dict[str, list] = {}
    for concept, (fy25, fy24_restated, fy24_original) in concept_vals.items():
        out[concept] = [
            _e(fy24_original, "2024-12-31", K24, "2025-02-14", "10-K", 2024, "FY", "2024-01-01"),
            _e(fy25, "2025-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2025-01-01"),
            _e(fy24_restated, "2024-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2024-01-01"),
        ]
    return out


# ── 1. Prior year = the period the latest filing restates ───────────────── #


def test_yoy_uses_the_restated_comparative_in_the_same_filing() -> None:
    rows = _annual_rows({
        "Revenues": (37442 * M, 34717 * M, 38498 * M),
        "NetIncomeLoss": (4729 * M, 5705 * M, 5705 * M),
    })
    result = normalize_company_facts(_facts(**rows), "XYZ", "1")
    assert result.revenue == 37442.0
    assert result.revenue_yoy_growth == 7.85  # not -2.74 against the original 38,498


def test_yoy_without_a_restated_comparative_falls_back_to_the_latest_filed() -> None:
    rows = {
        "Revenues": [
            _e(100 * M, "2024-12-31", K24, "2025-02-14", "10-K", 2024, "FY", "2024-01-01"),
            _e(110 * M, "2025-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2025-01-01"),
        ]
    }
    result = normalize_company_facts(_facts(**rows), "XYZ", "1")
    assert result.revenue_yoy_growth == 10.0


def test_a_quarter_is_never_the_prior_year() -> None:
    rows = {
        "Revenues": [
            _e(110 * M, "2025-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2025-01-01"),
            _e(30 * M, "2024-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2024-10-01"),
        ]
    }
    result = normalize_company_facts(_facts(**rows), "XYZ", "1")
    assert result.revenue_yoy_growth is None


# ── 2. The latest 10-Q's own quarter ─────────────────────────────────────── #


def _quarter_rows() -> dict[str, list]:
    rows = _annual_rows({"Revenues": (37442 * M, 34717 * M, 38498 * M)})
    rows["Revenues"] += [
        # Q3 2026 10-Q: this quarter, the year to date, and last year's comparatives.
        _e(4994 * M, "2026-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3", "2026-07-01"),
        _e(15004 * M, "2026-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3", "2026-01-01"),
        _e(4929 * M, "2025-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3", "2025-07-01"),
        # The same quarter as filed in 2025, and as carried in the FY2025 10-K.
        _e(10408 * M, "2025-09-30", Q325, "2025-10-23", "10-Q", 2025, "Q3", "2025-07-01"),
        _e(9437 * M, "2025-09-30", K25, "2026-02-17", "10-K", 2025, "FY", "2025-07-01"),
    ]
    rows["CashAndCashEquivalentsAtCarryingValue"] = [
        _e(12273 * M, "2025-12-31", Q326, "2026-10-22", "10-Q", 2026, "Q3"),
        _e(5694 * M, "2026-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3"),
        _e(12487 * M, "2025-12-31", K25, "2026-02-17", "10-K", 2025, "FY"),
    ]
    rows["NetIncomeLoss"] = [
        _e(4729 * M, "2025-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2025-01-01"),
        _e(1825 * M, "2025-09-30", Q325, "2025-10-23", "10-Q", 2025, "Q3", "2025-07-01"),
        _e(1825 * M, "2025-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3", "2025-07-01"),
        _e(664 * M, "2026-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3", "2026-07-01"),
    ]
    rows["EarningsPerShareDiluted"] = [
        _e(7.36, "2025-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2025-01-01"),
        _e(2.86, "2025-09-30", Q325, "2025-10-23", "10-Q", 2025, "Q3", "2025-07-01"),
        _e(5.71, "2025-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3", "2025-07-01"),
        _e(2.08, "2026-09-30", Q326, "2026-10-22", "10-Q", 2026, "Q3", "2026-07-01"),
    ]
    return rows


def test_latest_quarter_facts_are_the_newest_10q_single_quarter_only() -> None:
    facts = _facts(**_quarter_rows())
    out = latest_quarter_facts(facts["facts"]["us-gaap"], annual_filed="2026-02-17", cik="1")
    by_field = {f["field"]: f for f in out}
    assert by_field["revenue"]["numeric_value"] == 4994.0  # not 15,004 YTD, not 4,929
    assert by_field["revenue"]["period"] == "Q3 2026"
    assert by_field["cash_and_equivalents"]["numeric_value"] == 5694.0
    assert by_field["net_income"]["numeric_value"] == 664.0
    for fact in out:
        assert fact["scope"] == "Group"
        assert fact["confidence"] == "high"
        assert fact["source_url"].endswith("/1/000000000126000090/")
        assert fact["source_tier"] == "T2_regulator_or_gov"


def test_no_quarter_facts_when_the_10q_is_older_than_the_annual() -> None:
    facts = _facts(**_quarter_rows())
    out = latest_quarter_facts(facts["facts"]["us-gaap"], annual_filed="2026-11-01", cik="1")
    assert out == []


def test_normalised_bundle_carries_the_quarter_beside_never_inside_the_annual() -> None:
    result = normalize_company_facts(_facts(**_quarter_rows()), "XYZ", "1")
    assert result.revenue == 37442.0
    assert {f["field"] for f in result.current_period_facts} >= {"revenue", "net_income"}
    names = {dp.field_name for dp in result.to_datapoints()}
    assert "sec_edgar.current_period_facts" in names


# ── 3. Segment profit is not operating income ────────────────────────────── #


def _income_statement(op_income: float) -> dict[str, list]:
    def fy(v: float) -> dict[str, Any]:
        return _e(v * M, "2025-12-31", K25, "2026-02-17", "10-K", 2025, "FY", "2025-01-01")

    return {
        "Revenues": [fy(37442)],
        "CostOfGoodsAndServicesSold": [fy(23613)],
        "SellingGeneralAndAdministrativeExpense": [fy(5450)],
        "ResearchAndDevelopmentExpense": [fy(1812)],
        "OperatingIncomeLoss": [fy(op_income)],
        "NetIncomeLoss": [fy(4729)],
    }


def test_a_segment_profit_total_is_withheld_as_operating_income() -> None:
    result = normalize_company_facts(_facts(**_income_statement(8127)), "XYZ", "1")
    assert result.operating_income is None
    assert result.operating_margin is None
    assert "segment profit" in result.withheld_fields["operating_income"]["reason"]
    assert result.net_margin is not None


def test_a_real_operating_income_is_kept() -> None:
    result = normalize_company_facts(_facts(**_income_statement(6400)), "XYZ", "1")
    assert result.operating_income == 6400.0
    assert result.operating_margin is not None


def test_a_small_other_operating_income_does_not_trip_the_check() -> None:
    # Bound 6,567; 6,700 is +133 (0.4% of revenue): a disposal gain, not another measure.
    result = normalize_company_facts(_facts(**_income_statement(6700)), "XYZ", "1")
    assert result.operating_income == 6700.0


def test_without_cost_of_revenue_and_sga_nothing_is_said() -> None:
    rows = _income_statement(8127)
    del rows["CostOfGoodsAndServicesSold"]
    us_gaap = _facts(**rows)["facts"]["us-gaap"]
    assert operating_income_excess(
        us_gaap, accn=K25, end="2025-12-31", duration_days=364,
        revenue_m=37442, operating_income_m=8127,
    ) is None


# ── 4. A change of basis since the annual report ─────────────────────────── #


def test_a_perimeter_recast_and_a_share_consolidation_are_detected() -> None:
    us_gaap = _facts(**_quarter_rows())["facts"]["us-gaap"]
    changes = basis_changes_since_annual(us_gaap, annual_filed="2026-02-17")
    kinds = {c["kind"]: c for c in changes}
    assert kinds[BASIS_RECAST]["as_filed"] == 9437.0  # as carried by the annual report
    assert kinds[BASIS_RECAST]["restated"] == 4929.0
    assert kinds[BASIS_SHARE_CONSOLIDATION]["ratio"] == 2


def test_pre_consolidation_eps_is_withheld_and_the_reader_is_told() -> None:
    result = normalize_company_facts(_facts(**_quarter_rows()), "XYZ", "1")
    assert result.eps_diluted is None
    assert "predates_share_count_change" in result.withheld_fields["eps_diluted"]["reason"]
    assert any("share consolidation" in w for w in result.warnings)


def test_a_forward_split_is_named_as_a_split() -> None:
    rows = _quarter_rows()
    rows["EarningsPerShareDiluted"][2]["val"] = 2.86 / 4  # restated quarter
    us_gaap = _facts(**rows)["facts"]["us-gaap"]
    changes = basis_changes_since_annual(us_gaap, annual_filed="2026-02-17")
    split = [c for c in changes if c["kind"] == BASIS_SHARE_SPLIT]
    assert split and split[0]["ratio"] == 4


def test_unchanged_comparatives_report_no_basis_change() -> None:
    rows = _quarter_rows()
    rows["Revenues"][6]["val"] = 4929 * M  # the 2025 10-Q said the same
    rows["Revenues"][7]["val"] = 4929 * M  # and so did the 10-K
    rows["EarningsPerShareDiluted"][1]["val"] = 5.71
    us_gaap = _facts(**rows)["facts"]["us-gaap"]
    assert basis_changes_since_annual(us_gaap, annual_filed="2026-02-17") == []


def test_eps_moving_with_net_income_is_not_a_split() -> None:
    rows = _quarter_rows()
    rows["NetIncomeLoss"][2]["val"] = 3650 * M  # earnings restated too
    us_gaap = _facts(**rows)["facts"]["us-gaap"]
    kinds = {c["kind"] for c in basis_changes_since_annual(us_gaap, annual_filed="2026-02-17")}
    assert BASIS_SHARE_CONSOLIDATION not in kinds


# ── The investigator's statements tool sees the quarter and the basis change ── #


def test_statement_items_carry_the_quarter_under_its_own_ids() -> None:
    items, gaps = statement_items("XYZ", _facts(**_quarter_rows()))
    by_id = {i["id"]: i for i in items}
    assert by_id["secfin:XYZ:2026-Q3:revenue"]["value"] == 4994.0
    assert by_id["secfin:XYZ:2026-Q3:revenue"]["metric_id"] == "revenue_latest_quarter"
    assert by_id["secfin:XYZ:FY2025:revenue"]["value"] == 37442.0
    assert not any(i["id"].endswith(":eps_diluted") for i in items)
    assert any("basis change" in g and "share consolidation" in g for g in gaps)
