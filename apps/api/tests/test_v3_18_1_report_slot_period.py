"""V3.18.1 — producer → consumer: a fact's own period survives to the report slot.

The line that lost Southern Copper's gross-profit year was not in the normalizer. It was
``snapshot_builder._val``, which kept a datapoint's value and dropped its ``as_of``; and
the line that then lied about it was ``final_report_generator._sec_dp``, which stamped the
bundle's headline period on every slot. Both are exercised here through the REAL
functions, because a test that rebuilt either would agree with a copy of the code.
"""

from __future__ import annotations

import json
import pathlib

from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
from app.services.final_report_generator import _build_financial_snapshot
from app.workflows.snapshot_builder import enrich_snapshot_with_free_real

FIXTURE = (
    pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_discontinued_concept.json"
)


def _snapshot(datapoints: list[dict]) -> dict:
    free_real = {
        "fundamentals": {
            "num_datapoints": len(datapoints),
            "datapoints": datapoints,
            "provider": "sec_edgar",
            "source_tier": "T2_regulator_or_gov",
        }
    }
    return enrich_snapshot_with_free_real({"ticker": "ANY"}, free_real)


def _datapoints() -> list[dict]:
    n = normalize_company_facts(json.loads(FIXTURE.read_text()), "SCCO", "1001838")
    return [dp.model_dump() for dp in n.to_datapoints()]


class TestTheSnapshotKeepsEachFigureOwnPeriod:
    def test_own_period_ends_and_the_reporting_period_are_carried(self) -> None:
        fs = _snapshot(_datapoints())["fundamentals_summary"]
        assert fs["reporting_period_end"] == "2025-12-31"
        assert fs["field_period_ends"]["revenue_usd_m"] == "2025-12-31"
        assert "gross_profit_usd_m" not in fs["field_period_ends"]
        assert fs["gross_profit_usd_m"] is None

    def test_what_was_withheld_crosses_into_the_snapshot(self) -> None:
        fs = _snapshot(_datapoints())["fundamentals_summary"]
        fields = {w["field"]: w for w in fs["withheld_fields"]}
        assert fields["gross_profit"]["end"] == "2019-12-31"


class TestTheReportSlot:
    def test_the_report_says_the_line_is_not_reported_for_the_period(self) -> None:
        section = _build_financial_snapshot(_snapshot(_datapoints()), None)
        assert "gross_profit_usd_m" not in section
        assert section["revenue_usd_m"]["value"] == 13420.0
        assert section["revenue_usd_m"]["period_end"] == "2025-12-31"
        items = {i["field"]: i for i in section["not_reported_for_period"]["items"]}
        assert items["gross_profit"]["latest_period_the_filer_tagged"] == "2019-12-31"

    def test_the_slot_fails_closed_if_a_stale_figure_reaches_it_anyway(self) -> None:
        """The second lock. Simulates ANY upstream path handing the slot a figure whose
        own period is not the reporting period — the normalizer being bypassed, a new
        provider, a cached snapshot. The slot must not publish it as current."""
        datapoints = _datapoints()
        datapoints.append(
            {
                "field_name": "sec_edgar.gross_profit",
                "value": 2914.8,
                "unit": "USD_m",
                "as_of": "2019-12-31",
            }
        )
        section = _build_financial_snapshot(_snapshot(datapoints), None)
        slot = section["gross_profit_usd_m"]
        assert slot["value"] is None
        assert slot["provenance"] == "missing_data"
        assert "2019-12-31" in slot["withheld_reason"]

    def test_mutation_without_the_period_map_the_stale_figure_is_published(self) -> None:
        """Remove the map — the pre-V3.18.1 snapshot shape — and the same input ships
        the FY2019 figure as `sourced_fact` under the FY2025 label. That is the defect,
        reproduced, which is what makes the two tests above mean something."""
        datapoints = _datapoints()
        datapoints.append(
            {"field_name": "sec_edgar.gross_profit", "value": 2914.8, "as_of": "2019-12-31"}
        )
        snapshot = _snapshot(datapoints)
        snapshot["fundamentals_summary"].pop("field_period_ends")
        slot = _build_financial_snapshot(snapshot, None)["gross_profit_usd_m"]
        assert slot["value"] == 2914.8 and slot["provenance"] == "sourced_fact"
        assert "FY2025" in slot["period"]
