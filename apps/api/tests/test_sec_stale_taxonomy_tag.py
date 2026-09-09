"""A company that changed XBRL tags must not keep reporting the old tag's last year.

WHAT WENT WRONG IN PRODUCTION
=============================
The live MRNA report said, in five separate agent sections:

    "FY2025 revenue was $19.263 billion with an operating loss of $3.074 billion
     and net loss of $2.822 billion."

The operating loss and the net loss are correct FY2025 figures from Moderna's 10-K.
The revenue is **FY2022** — the COVID peak year — presented as FY2025. Moderna's actual
FY2025 total revenue is **$1.944bn**, so the report overstated it by a factor of ten and
then reasoned about a "revenue decline" from the wrong base.

The cause is two defects compounding:

1. ``parse_company_facts`` walks its alias list in order and takes the FIRST concept
   that has any annual data, comparing periods only *within* that concept. Moderna
   stopped tagging ``Revenues`` after FY2022 and moved to
   ``RevenueFromContractWithCustomerExcludingAssessedTax``. The stale tag still has
   annual data, so it won, for ever.
2. ``sec_edgar_fundamentals`` then merged the period-aware normalizer *underneath* the
   legacy parser — "existing field_names from parse_company_facts win to preserve
   behavior" — so ``_select_metric``, which was written for exactly this bug and gets
   it right, had its answer discarded.

The fixture is cut from Moderna's real SEC companyfacts payload, reduced to the two
most recent annual entries per concept. It is small, but nothing about the defect is
synthetic.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from app.integrations.providers.sec_edgar_fundamentals import (
    merge_fundamentals,
    parse_company_facts,
)
from app.integrations.sec_fundamentals_normalizer import normalize_company_facts

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "sec_companyfacts_stale_tag.json"

#: Moderna's FY2025 10-K, in millions. These are the numbers the report must show.
FY2025_REVENUE = 1944.0
FY2025_OPERATING_INCOME = -3074.0
FY2025_NET_INCOME = -2822.0
#: What the stale ``Revenues`` tag last reported, in FY2022. It reached production.
STALE_FY2022_REVENUE = 19263.0


@pytest.fixture
def facts() -> dict:
    return json.loads(FIXTURE.read_text())


def _merge(data: dict) -> dict[str, object]:
    """The SHIPPED merge, called rather than reproduced.

    Reproducing it here would let the test and the code drift, and a test that agrees
    with a copy of the code instead of the code is precisely how the original defect
    survived a green suite.
    """
    base, _ = parse_company_facts(data, "MRNA", "1682852")
    merged, _warnings = merge_fundamentals(data, "MRNA", "1682852", base)
    return {dp.field_name: dp for dp in merged}


def _merge_warnings(data: dict) -> list[str]:
    base, _ = parse_company_facts(data, "MRNA", "1682852")
    _merged, warnings = merge_fundamentals(data, "MRNA", "1682852", base)
    return warnings


class TestTheStaleTagCannotWin:
    def test_the_legacy_parser_alone_still_returns_the_stale_year(self, facts) -> None:
        """Pinned deliberately. This is the defect, and it is still reachable — the
        fix is that nothing SHIPS this value, not that the old parser changed."""
        base, _ = parse_company_facts(facts, "MRNA", "1682852")
        legacy = {dp.field_name: dp for dp in base}
        assert legacy["sec_edgar.revenue"].value == STALE_FY2022_REVENUE
        assert legacy["sec_edgar.revenue"].as_of == "2022-12-31"

    def test_the_normalizer_picks_the_current_tag(self, facts) -> None:
        n = normalize_company_facts(facts, "MRNA", "1682852")
        assert n.revenue == FY2025_REVENUE
        assert n.field_periods["revenue"]["fy"] == 2025
        assert n.field_periods["revenue"]["end"] == "2025-12-31"
        assert (
            n.field_periods["revenue"]["concept"]
            == "RevenueFromContractWithCustomerExcludingAssessedTax"
        )

    def test_what_ships_is_the_filing_figure(self, facts) -> None:
        """THE regression. This assertion fails on the code that produced the live
        MRNA report, which is the only reason it is worth having."""
        merged = _merge(facts)
        assert merged["sec_edgar.revenue"].value == FY2025_REVENUE
        assert merged["sec_edgar.revenue"].value != STALE_FY2022_REVENUE

    def test_the_figures_that_were_already_right_stay_right(self, facts) -> None:
        """A fix that moved the correct numbers would be a worse defect than the one
        it replaced."""
        merged = _merge(facts)
        assert merged["sec_edgar.operating_income"].value == FY2025_OPERATING_INCOME
        assert merged["sec_edgar.net_income"].value == FY2025_NET_INCOME


class TestEveryFactCarriesItsOwnPeriod:
    def test_as_of_is_the_period_end_not_the_filing_date(self, facts) -> None:
        """`as_of` on a financial fact means the period it describes. Using the filing
        date makes a three-year-old figure look current, because the FILING is current."""
        merged = _merge(facts)
        for name in ("revenue", "operating_income", "net_income", "total_assets"):
            dp = merged[f"sec_edgar.{name}"]
            assert dp.as_of == "2025-12-31", f"{name} carries {dp.as_of}"

    def test_the_note_names_the_period_and_the_concept(self, facts) -> None:
        """So a reader of the evidence pack can tell which filing a number came from
        without trusting the bundle's headline year."""
        merged = _merge(facts)
        note = merged["sec_edgar.revenue"].note or ""
        assert "FY2025" in note
        assert "2025-12-31" in note
        assert "RevenueFromContractWithCustomerExcludingAssessedTax" in note

    def test_a_bundle_spanning_two_years_says_so(self) -> None:
        """The headline `fiscal_year` is one field's period, not every field's. When
        they genuinely differ the bundle must not be presented as one year's accounts —
        that presentation is what turned a stale figure into "FY2025 revenue"."""
        data = json.loads(FIXTURE.read_text())
        # Remove the current revenue tag so revenue falls back to the FY2022 concept
        # while the other statements stay FY2025 — the exact production shape.
        del data["facts"]["us-gaap"]["RevenueFromContractWithCustomerExcludingAssessedTax"]
        n = normalize_company_facts(data, "MRNA", "1682852")
        assert n.revenue == STALE_FY2022_REVENUE, "precondition: revenue is now stale"
        assert any("span more than one fiscal year" in w for w in n.warnings), (
            f"a mixed-period bundle must be declared; warnings were {n.warnings}"
        )


class TestTheSupersessionIsRecorded:
    def test_replacing_a_shipped_value_is_said_out_loud(self, facts) -> None:
        """The merge changes a number that a previous run published. Silence about
        that is how nobody notices a figure moved by a factor of ten."""
        warnings = _merge_warnings(facts)
        assert any(
            "superseded the legacy alias-order value" in w
            and "sec_edgar.revenue" in w
            for w in warnings
        ), f"the supersession must be recorded; warnings were {warnings}"

    def test_nothing_is_reported_as_superseded_when_nothing_changed(self, facts) -> None:
        """The other direction — otherwise the warning is noise on every run."""
        data = json.loads(FIXTURE.read_text())
        del data["facts"]["us-gaap"]["Revenues"]
        base, _ = parse_company_facts(data, "MRNA", "1682852")
        _merged, warnings = merge_fundamentals(data, "MRNA", "1682852", base)
        assert not any("superseded" in w for w in warnings)
