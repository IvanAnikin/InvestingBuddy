"""The report must not print FY2025 figures beside "Latest annual — Not reported".

WHAT WENT WRONG IN PRODUCTION
=============================
The live MRNA report carried, in its own `financial_snapshot`:

    reporting_periods.latest_annual = null      ("Latest annual — Not reported")

while five council sections asserted FY2025 revenue, operating loss and net loss. The
reader was told the report holds no annual reporting, next to annual claims.

THE CAUSE, WHICH IS NOT PRESENTATION
====================================
`resolve_fundamentals` ranks channels by SOURCE QUALITY — issuer document (T1) above
SEC XBRL (T2) — and applied that ranking without regard to PERIOD. Moderna's issuer
channel contributed exactly ONE fact: Q2 2026 revenue, an interim figure from a 10-Q.
That single interim fact made `issuer_primary_document` the canonical channel, which
suppressed a complete, regulator-published FY2025 statement set from the snapshot
entirely. No annual slot was filled, so the derived state said no annual period exists —
correctly, given what it was shown.

The annual numbers still reached the Council, as prose inside an evidence excerpt. So the
report had two interpretations of annual-period availability and printed both.

An interim fact and an annual statement answer different questions, and the section has
separate slots for exactly that reason. One must not displace the other.
"""

from __future__ import annotations

import pytest

from app.services.final_report_generator import _build_financial_snapshot

#: Moderna FY2025, as the 10-K reports it and as the corrected SEC path now resolves it.
FY2025 = {
    "source_tier": "T2_regulator_or_gov",
    "provider": "sec_edgar",
    "num_datapoints": 12,
    "revenue_usd_m": 1944.0,
    "operating_income_usd_m": -3074.0,
    "net_income_usd_m": -2822.0,
    "total_assets_usd_m": 12338.0,
    "fiscal_year": 2025,
    "fiscal_period": "FY",
    "form_type": "10-K",
    "period_basis": "annual",
}

#: The live shape: ONE interim fact from the issuer's 10-Q, and nothing annual.
ONE_INTERIM_FACT = [
    {
        "field": "revenue",
        "value": "revenue of $145 million",
        "numeric_value": 145.0,
        "scale": "million",
        "unit": "currency_amount",
        "currency": "USD",
        "period": "Q2 2026",
        "confidence": "high",
        "source_url": "https://www.sec.gov/Archives/edgar/data/1682852/x/10q.htm",
        "source_tier": "T1_primary_filing",
    }
]


@pytest.fixture
def snapshot() -> dict:
    return {
        "fundamentals_summary": dict(FY2025),
        "is_mock": False,
        "source_tier": "T2_regulator_or_gov",
        "retrieved_at": "2026-09-08T12:00:00+00:00",
    }


class TestAnnualMetadataAgreesWithAnnualFigures:
    def test_latest_annual_is_the_year_of_the_statements_shown(self, snapshot) -> None:
        """THE regression. This is null on the code that produced the live report."""
        section = _build_financial_snapshot(snapshot, None, ONE_INTERIM_FACT, None)
        states = section["reporting_periods"]
        assert states["latest_annual"] == "FY2025", (
            f"annual metadata says {states['latest_annual']!r} beside FY2025 figures"
        )

    def test_the_annual_figures_are_actually_present(self, snapshot) -> None:
        """The metadata must not name a period the section cannot show — that is the
        rule the original derivation was protecting, and it still holds."""
        section = _build_financial_snapshot(snapshot, None, ONE_INTERIM_FACT, None)
        assert section["revenue_usd_m"]["value"] == 1944.0
        assert section["operating_income_usd_m"]["value"] == -3074.0
        assert section["net_income_usd_m"]["value"] == -2822.0
        assert "FY2025" in (section["revenue_usd_m"]["period"] or "")

    def test_the_interim_fact_keeps_its_own_slot_and_period(self, snapshot) -> None:
        """The interim figure is not lost or promoted — both are true and they answer
        different questions."""
        section = _build_financial_snapshot(snapshot, None, ONE_INTERIM_FACT, None)
        assert section["revenue_current_period"]["numeric_value"] == 145.0
        assert section["reporting_periods"]["latest_current_period"] == "Q2 2026"

    def test_an_interim_fact_never_becomes_the_annual_period(self, snapshot) -> None:
        assert (
            _build_financial_snapshot(snapshot, None, ONE_INTERIM_FACT, None)[
                "reporting_periods"
            ]["latest_annual"]
            != "Q2 2026"
        )


class TestItDoesNotInventAnAnnualPeriod:
    def test_no_sec_statements_means_no_annual_period(self) -> None:
        """"Do not infer an annual period merely because prose mentions a year."
        With no annual statements shown, the honest answer is still absence."""
        snap = {"fundamentals_summary": {"fiscal_year": 2025, "period_basis": "annual"}}
        section = _build_financial_snapshot(snap, None, ONE_INTERIM_FACT, None)
        assert section["reporting_periods"]["latest_annual"] is None

    def test_a_quarterly_sec_bundle_is_not_an_annual_period(self) -> None:
        """`period_basis` is the guard: a 10-Q-derived bundle names no annual period."""
        fs = dict(FY2025, period_basis="quarterly", form_type="10-Q")
        section = _build_financial_snapshot(
            {"fundamentals_summary": fs}, None, ONE_INTERIM_FACT, None
        )
        assert section["reporting_periods"]["latest_annual"] is None

    def test_an_issuer_annual_fact_still_wins_the_slot(self) -> None:
        """Source quality is unchanged where the issuer channel HAS annual facts —
        this fix must not lower evidence tier, only stop an interim fact displacing a
        whole annual statement set."""
        annual_from_issuer = ONE_INTERIM_FACT + [
            {
                "field": "revenue",
                "value": "revenue of $1,944 million",
                "numeric_value": 1944.0,
                "scale": "million",
                "unit": "currency_amount",
                "currency": "USD",
                "period": "FY2025",
                "confidence": "high",
                "source_url": "https://www.sec.gov/x/10k.htm",
                "source_tier": "T1_primary_filing",
            }
        ]
        section = _build_financial_snapshot(
            {"fundamentals_summary": dict(FY2025)}, None, annual_from_issuer, None
        )
        assert section["revenue_primary_filing"]["source_tier"] == "T1_primary_filing"
        assert section["reporting_periods"]["latest_annual"] == "FY2025"
