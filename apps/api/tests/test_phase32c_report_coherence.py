"""
Phase C — human-facing coherence: one source-quality answer, grouped warnings,
deterministic classification, honest thin-evidence state.

These cover PRESENTATION of evidence Phase B already established. None of them
changes financial fact semantics; several assert that they do not.
"""

from __future__ import annotations

from app.schemas.evidence_state import (
    EvidenceInventory,
    FieldProvenance,
    FinancialDataSummary,
    FundamentalsResolution,
    PriceSummary,
)
from app.schemas.research_quality import (
    CODE_COUNCIL_PARTIAL,
    CODE_NO_FUNDAMENTALS,
    CODE_PRICE_FALLBACK_USED,
    CODE_UNCLASSIFIED,
    QUALITY_ADEQUATE,
    QUALITY_INSUFFICIENT,
    QUALITY_STRONG,
    SCOPE_RUN,
    SEVERITY_BLOCKING,
    SEVERITY_INFO,
    WarningCollector,
    assess_source_quality,
    assess_thin_evidence,
)
from app.services.sector_taxonomy import resolve_sector_classification


def _regulator_inventory() -> EvidenceInventory:
    return EvidenceInventory(
        financial_data=FinancialDataSummary(
            available_fields=["financials.revenue", "financials.net_income"]
        ),
        price=PriceSummary(
            available=True,
            latest_close=214.72,
            provenance=FieldProvenance(
                provider_name="eodhd_price_only", source_tier="T5_api_aggregator"
            ),
        ),
        fundamentals=FundamentalsResolution(
            available=True,
            regulator_facts_available=True,
            fact_count=4,
            period_label="FY2026",
            provenance=FieldProvenance(
                provider_name="sec_edgar_xbrl", source_tier="T2_regulator_or_gov"
            ),
        ),
    )


def _issuer_primary_inventory() -> EvidenceInventory:
    return EvidenceInventory(
        financial_data=FinancialDataSummary(
            available_fields=["financials.revenue", "financials.operating_income"]
        ),
        price=PriceSummary(available=True, latest_close=132.4),
        fundamentals=FundamentalsResolution(
            available=True,
            issuer_primary_facts_available=True,
            fact_count=6,
            period_label="FY2026",
            provenance=FieldProvenance(
                provider_name="issuer_pdf", source_tier="T1_primary_filing"
            ),
        ),
    )


def _thin_inventory() -> EvidenceInventory:
    return EvidenceInventory(
        financial_data=FinancialDataSummary(available_fields=["identity.ticker"]),
        price=PriceSummary(
            available=True,
            latest_close=91.5,
            provenance=FieldProvenance(provider_name="eodhd_price_only"),
        ),
        fundamentals=FundamentalsResolution(available=False),
    )


_FULL_IDENTITY = {
    "legal_name": "Testco",
    "ticker": "TSTC",
    "exchange": "NASDAQ",
    "isin": "US0000000001",
}


# ===========================================================================
# B. ONE canonical source-quality answer
# ===========================================================================
def test_regulator_backed_company_is_strong_on_financial_evidence() -> None:
    q = assess_source_quality(
        inventory=_regulator_inventory(),
        identity=_FULL_IDENTITY,
        catalyst_summary={"regulator_filing_count": 4, "issuer_press_count": 16},
    )
    assert q.financial_evidence_quality.label == QUALITY_STRONG
    assert q.identity_quality.label == QUALITY_STRONG
    assert q.catalyst_evidence_quality.label == QUALITY_STRONG
    assert q.overall_research_evidence_quality.label == QUALITY_STRONG
    # A label is never bare: every dimension explains itself.
    for dim in (
        q.identity_quality,
        q.financial_evidence_quality,
        q.catalyst_evidence_quality,
        q.overall_research_evidence_quality,
    ):
        assert dim.basis, "every quality label must carry its basis"


def test_issuer_primary_facts_are_as_strong_as_regulator_facts() -> None:
    """A European issuer filing is not second-class evidence."""
    q = assess_source_quality(
        inventory=_issuer_primary_inventory(),
        identity=_FULL_IDENTITY,
        catalyst_summary={"issuer_press_count": 5},
    )
    assert q.financial_evidence_quality.label == QUALITY_STRONG
    assert any("issuer primary" in b for b in q.financial_evidence_quality.basis)


def test_overall_reflects_the_weakest_dimension_not_an_average() -> None:
    """Strong identity must never mask absent financials."""
    q = assess_source_quality(
        inventory=_thin_inventory(),
        identity=_FULL_IDENTITY,
        catalyst_summary={},
    )
    assert q.identity_quality.label == QUALITY_STRONG
    assert q.financial_evidence_quality.label == QUALITY_INSUFFICIENT
    assert q.overall_research_evidence_quality.label == QUALITY_INSUFFICIENT


def test_one_assessment_serves_every_surface_identically() -> None:
    """The whole point: sections read ONE object, so they cannot disagree.

    The historical defect was the same report showing strong/adequate/weak in
    different places because each computed its own answer.
    """
    inventory = _regulator_inventory()
    a = assess_source_quality(inventory=inventory, identity=_FULL_IDENTITY,
                              catalyst_summary={"regulator_filing_count": 2})
    b = assess_source_quality(inventory=inventory, identity=_FULL_IDENTITY,
                              catalyst_summary={"regulator_filing_count": 2})
    assert a.to_payload() == b.to_payload()


def test_aggregator_only_fundamentals_are_adequate_not_strong() -> None:
    inventory = EvidenceInventory(
        fundamentals=FundamentalsResolution(available=True, fact_count=3)
    )
    q = assess_source_quality(inventory=inventory, identity=_FULL_IDENTITY,
                              catalyst_summary={"issuer_press_count": 1})
    assert q.financial_evidence_quality.label == QUALITY_ADEQUATE
    assert any("not filing-verified" in b for b in q.financial_evidence_quality.basis)


# ===========================================================================
# C. Warning grouping
# ===========================================================================
def test_twenty_identical_warnings_collapse_to_one_group_with_count() -> None:
    collector = WarningCollector()
    for i in range(20):
        collector.add(
            "5 financial fundamental categories missing. Filings, XBRL data or "
            "a fundamentals-capable provider required.",
            subject=f"TCK{i}",
        )
    groups = collector.groups()
    assert len(groups) == 1
    assert groups[0].code == CODE_NO_FUNDAMENTALS
    assert groups[0].count == 20
    # Repetition across many subjects is a RUN-level observation.
    assert groups[0].scope == SCOPE_RUN
    # Raw instances survive for diagnostics.
    assert len(collector.raw_instances) == 20


def test_different_root_causes_stay_separate() -> None:
    collector = WarningCollector()
    collector.add("Stooq price provider unavailable; used EODHD price-only fallback.", subject="A")
    collector.add("5 financial fundamental categories missing.", subject="A")
    collector.add("committee_chair: budget_exhausted", subject="A")
    codes = {g.code for g in collector.groups()}
    assert {CODE_PRICE_FALLBACK_USED, CODE_NO_FUNDAMENTALS, CODE_COUNCIL_PARTIAL} <= codes


def test_blocking_warning_is_never_swallowed_by_deduplication() -> None:
    collector = WarningCollector()
    for i in range(30):
        collector.add("5 financial fundamental categories missing.", subject=f"T{i}")
    collector.add("Safety gate quarantined an agent output.",
                  subject="T1", severity=SEVERITY_BLOCKING)
    groups = collector.groups()
    blocking = [g for g in groups if g.severity == SEVERITY_BLOCKING]
    assert len(blocking) == 1
    assert blocking[0] is groups[0], "blocking warnings must surface first"
    assert blocking[0].count == 1


def test_unclassified_warnings_are_shown_not_dropped() -> None:
    collector = WarningCollector()
    collector.add("Something entirely novel happened", subject="A")
    collector.add("A different novel thing happened", subject="B")
    groups = collector.groups()
    assert all(g.code == CODE_UNCLASSIFIED for g in groups)
    # Genuinely different unknown problems must not merge into one bucket.
    assert len(groups) == 2


def test_two_hundred_warning_wall_becomes_a_short_human_list() -> None:
    """The live symptom: ~200 strings, mostly the same handful repeated."""
    messages: list[str] = []
    for i in range(25):
        messages += [
            f"TCK{i}: Stooq price provider unavailable; used EODHD price-only fallback.",
            f"TCK{i}: News provider 'gdelt' returned no company results in the lookback window.",
            f"TCK{i}: 5 financial fundamental categories missing.",
            f"TCK{i}: price_history.latest_close: citation from T5_api_aggregator only — upgrade to T1/T2 primary source before publication",
        ]
    collector = WarningCollector.from_messages(messages)
    groups = collector.groups()
    assert len(messages) == 100
    assert len(groups) <= WarningCollector.MAX_GROUPS
    assert sum(g.count for g in groups) == 100, "no instance may be lost"
    assert len(collector.raw_instances) == 100
    payload = collector.to_payload()
    assert payload["raw_instance_count"] == 100
    assert payload["warnings_schema_version"] >= 1


def test_price_fallback_is_informational_not_a_failure() -> None:
    collector = WarningCollector()
    collector.add("Stooq price provider unavailable; used EODHD price-only fallback.")
    assert collector.groups()[0].severity == SEVERITY_INFO


# ===========================================================================
# G. Jurisdiction-neutral framing
# ===========================================================================
def test_missing_sec_mapping_is_informational_for_a_non_us_issuer() -> None:
    """"No SEC CIK" must not read as the primary failure for a Swiss issuer."""
    collector = WarningCollector()
    collector.add("No SEC mapping / CIK for this listing.", subject="TEUR")
    group = collector.groups()[0]
    assert group.severity == SEVERITY_INFO
    assert "regulator connector" in group.message.lower()


def test_report_ui_avoids_sec_centric_fundamentals_label() -> None:
    import pathlib

    web = pathlib.Path(__file__).resolve().parents[2] / "web" / "src" / "app"
    page = (web / "admin" / "discovery" / "page.tsx").read_text(encoding="utf-8")
    assert "Fundamentals (SEC / derived)" not in page
    assert "Financial Fundamentals" in page


# ===========================================================================
# H. Classification conflict — deterministic, conflict retained
# ===========================================================================
def test_provider_curated_conflict_resolves_deterministically() -> None:
    """Synthetic: curated says A, provider says B, industry implies A."""
    result = resolve_sector_classification(
        provider_sector="Financials",
        curated_sector="Real Estate",
        industry="Real Estate Investment Trusts",
    )
    assert result["canonical_sector"] == "Real Estate"
    assert result["sector_conflict"] is True
    # Nothing is discarded: both inputs remain inspectable.
    assert result["provider_sector"] == "Financials"
    assert result["curated_sector"] == "Real Estate"


def test_no_conflict_leaves_provider_classification_untouched() -> None:
    result = resolve_sector_classification(
        provider_sector="Technology", curated_sector=None, industry="Semiconductors"
    )
    assert result["canonical_sector"] == "Technology"
    assert result["sector_conflict"] is False


def test_a_genuine_financials_company_is_not_reclassified() -> None:
    """The industry rule must not sweep up real Financials companies."""
    result = resolve_sector_classification(
        provider_sector="Financials", curated_sector=None, industry="Banks"
    )
    assert result["canonical_sector"] == "Financials"


def test_classification_is_stable_between_universe_and_queue() -> None:
    """The observed symptom: universe said one thing, the queue another."""
    universe = resolve_sector_classification(
        provider_sector=None, curated_sector="Real Estate",
        industry="Real Estate Investment Trusts",
    )
    queue = resolve_sector_classification(
        provider_sector="Financials", curated_sector="Real Estate",
        industry="Real Estate Investment Trusts",
    )
    assert universe["canonical_sector"] == queue["canonical_sector"]


# ===========================================================================
# F. Thin-evidence trigger
# ===========================================================================
def test_thin_company_triggers_short_form_with_reasons() -> None:
    a = assess_thin_evidence(
        inventory=_thin_inventory(),
        identity={"ticker": "THIN", "legal_name": "Testco Thin AS"},
        primary_fact_count=0,
        catalyst_summary={},
        source_locations=["https://example.invalid/investors"],
    )
    assert a.is_thin is True
    assert len(a.reasons) == 3
    # The short form leads with what IS known.
    assert a.has_price is True
    assert a.has_identity is True
    assert a.known_source_locations, "official source locations must be retained"


def test_regulator_backed_company_is_not_thin() -> None:
    a = assess_thin_evidence(
        inventory=_regulator_inventory(),
        identity=_FULL_IDENTITY,
        primary_fact_count=0,
        catalyst_summary={"regulator_filing_count": 4},
    )
    assert a.is_thin is False
    assert a.reasons == []


def test_primary_document_facts_alone_prevent_thin_form() -> None:
    """Issuer-extracted facts are real evidence, even without fundamentals."""
    a = assess_thin_evidence(
        inventory=EvidenceInventory(fundamentals=FundamentalsResolution(available=False)),
        identity=_FULL_IDENTITY,
        primary_fact_count=7,
        catalyst_summary={},
    )
    assert a.is_thin is False


def test_thin_trigger_is_company_agnostic() -> None:
    """No ticker/company-specific logic: identity cannot change the verdict."""
    base = dict(
        inventory=_thin_inventory(), primary_fact_count=0, catalyst_summary={}
    )
    for ticker in ("PNDORA", "AAAA", "ZZZZ"):
        assert assess_thin_evidence(identity={"ticker": ticker}, **base).is_thin is True


# ===========================================================================
# No research-semantics regression
# ===========================================================================
def test_presentation_layer_does_not_alter_evidence_values() -> None:
    inventory = _regulator_inventory()
    before = inventory.to_payload()
    assess_source_quality(inventory=inventory, identity=_FULL_IDENTITY, catalyst_summary={})
    assess_thin_evidence(inventory=inventory, identity=_FULL_IDENTITY, primary_fact_count=0)
    assert inventory.to_payload() == before
