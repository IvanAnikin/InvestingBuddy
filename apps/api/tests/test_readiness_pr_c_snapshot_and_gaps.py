"""
Private-use production readiness, PR-C — RICHER CANONICAL SNAPSHOT,
SOURCE-NEUTRAL COPY, and PER-COMPANY DFR IDENTITY GAPS.

Three defects confirmed against the code at ``8b516e3``:

D3  The canonical snapshot exposed 7 fields while the parser routinely produced
    15. A validated ``operating_margin`` / ``operating_cash_flow`` / ``net_debt``
    / ``net_cash`` / ``total_equity`` was extracted, validated, persisted and
    cited — then never shown. Two hand-maintained field sets had also drifted
    from the parser AND from each other: one listed ``shareholders_equity`` and
    ``earnings_per_share`` (never emitted) while omitting ``total_equity``
    (emitted routinely), so a real equity fact counted as no fundamental
    anywhere.

D4  US filing vocabulary ("10-K / 40-F", "SEC statement fundamentals") was
    emitted for European issuers with no SEC registration — a source-channel
    contradiction, since the same report elsewhere correctly labels those facts
    issuer-primary.

D5  The DFR pack carried NO per-company identity-completeness signal, so a
    comparative council had only free-text gap prose to work from and could
    generalise one company's missing LEI into "both companies are missing LEI"
    while the other's report rendered a sourced one.

Fully offline and deterministic: no network, no LLM, no Azure, no DB.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

import pytest

from app.agents.analysis_council.valuation_guard_agent import _statement_source_label
from app.agents.research_team.source_quality_agent import annual_filing_name
from app.models.discovery import DiscoveryCandidate
from app.models.report import Report
from app.services.canonical_evidence import PRIMARY_FACT_FIELDS
from app.services.field_review_evidence_pack import (
    IDENTITY_COMPLETENESS_FIELDS,
    _identity_completeness,
    build_company_summary,
)
from app.services.final_report_generator import (
    _PRIMARY_FINANCIAL_FACT_FIELDS,
    _PRIMARY_IDENTITY_FACT_FIELDS,
    _build_financial_snapshot,
)
from app.services.final_research_state import FinancialEvidenceState
from app.services.sources.primary_fact_parser import (
    FINANCIAL_STATEMENT_FIELDS,
    IDENTITY_FIELDS,
    NON_INTERCHANGEABLE_FIELD_PAIRS,
)

_URL = "https://issuer.test/annual-report-2025.pdf"


def _fact(field: str, value: float, *, scope: str | None = "group", **over) -> dict:
    base = {
        "field": field,
        "value": str(value),
        "numeric_value": value,
        "currency": "DKK",
        "scale": "million",
        "period": "2025",
        "scope": scope,
        "confidence": "high",
        "source_url": _URL,
        "page_number": 14,
    }
    base.update(over)
    return base


def _snapshot(**over) -> dict:
    base = {"source_tier": "T1_primary_filing", "is_mock": False}
    base.update(over)
    return base


# =========================================================================== #
# D3 — one vocabulary, derived from the parser                                #
# =========================================================================== #


def test_snapshot_field_set_is_exactly_the_parser_statement_vocabulary() -> None:
    """The set cannot drift from what is actually extractable, because it IS
    what is actually extractable."""
    assert _PRIMARY_FINANCIAL_FACT_FIELDS == FINANCIAL_STATEMENT_FIELDS


def test_canonical_fundamentals_set_covers_every_statement_field() -> None:
    assert FINANCIAL_STATEMENT_FIELDS <= PRIMARY_FACT_FIELDS


def test_previously_dead_field_names_are_gone() -> None:
    """``shareholders_equity`` and ``earnings_per_share`` were listed as
    fundamentals but the parser has never emitted either."""
    assert "shareholders_equity" not in FINANCIAL_STATEMENT_FIELDS
    assert "earnings_per_share" not in FINANCIAL_STATEMENT_FIELDS


def test_previously_omitted_fields_now_count_as_fundamentals() -> None:
    for name in ("total_equity", "net_cash", "operating_margin", "operating_cash_flow"):
        assert name in PRIMARY_FACT_FIELDS, name


def test_employees_is_identity_not_a_fundamental() -> None:
    """Knowing the headcount must never read as "we have financial statements"."""
    assert "employees" in IDENTITY_FIELDS
    assert "employees" not in FINANCIAL_STATEMENT_FIELDS
    assert "employees" not in PRIMARY_FACT_FIELDS


def test_legal_name_is_not_duplicated_into_the_identity_slot_loop() -> None:
    """The section already resolves ``legal_name`` with placeholder detection;
    a second separately-resolved key for the same concept is the
    two-sources-of-truth pattern this campaign removes."""
    assert "company_legal_name" not in _PRIMARY_IDENTITY_FACT_FIELDS


@pytest.mark.parametrize("a,b", NON_INTERCHANGEABLE_FIELD_PAIRS)
def test_non_interchangeable_pairs_are_distinct_snapshot_slots(a: str, b: str) -> None:
    """net debt is not total debt; net cash is not cash; EBIT is not EBITDA."""
    assert a != b
    section = _build_financial_snapshot(
        _snapshot(), None, [_fact(a, 111.0), _fact(b, 222.0)]
    )
    if a in FINANCIAL_STATEMENT_FIELDS and b in FINANCIAL_STATEMENT_FIELDS:
        assert section[f"{a}_primary_filing"]["numeric_value"] == 111.0
        assert section[f"{b}_primary_filing"]["numeric_value"] == 222.0


# =========================================================================== #
# D3 — the richer snapshot in practice                                        #
# =========================================================================== #


def test_every_validated_statement_field_reaches_the_snapshot() -> None:
    facts = [_fact(name, float(i + 1)) for i, name in enumerate(sorted(FINANCIAL_STATEMENT_FIELDS))]
    section = _build_financial_snapshot(_snapshot(), None, facts)
    for name in FINANCIAL_STATEMENT_FIELDS:
        assert f"{name}_primary_filing" in section, name


def test_a_pandora_shaped_fact_set_exposes_the_full_snapshot() -> None:
    facts = [
        _fact("revenue", 32549.0),
        _fact("operating_profit", 7783.0),
        _fact("operating_margin", 23.9, currency=None, scale=None),
        _fact("net_income", 5241.0),
        _fact("operating_cash_flow", 7361.0),
        _fact("free_cash_flow", 5022.0),
        _fact("total_assets", 29603.0),
        _fact("total_equity", 5282.0),
        _fact("net_debt", 13719.0),
    ]
    section = _build_financial_snapshot(_snapshot(), None, facts)
    assert section["revenue_primary_filing"]["numeric_value"] == 32549.0
    assert section["operating_margin_primary_filing"]["numeric_value"] == 23.9
    assert section["operating_cash_flow_primary_filing"]["numeric_value"] == 7361.0
    assert section["free_cash_flow_primary_filing"]["numeric_value"] == 5022.0
    assert section["total_equity_primary_filing"]["numeric_value"] == 5282.0
    assert section["net_debt_primary_filing"]["numeric_value"] == 13719.0
    # ...and nothing that was not sourced was invented.
    assert "total_debt_primary_filing" not in section
    assert "cash_and_equivalents_primary_filing" not in section


def test_every_snapshot_datapoint_carries_period_scale_currency_and_source() -> None:
    section = _build_financial_snapshot(_snapshot(), None, [_fact("revenue", 32549.0)])
    dp = section["revenue_primary_filing"]
    assert dp["period"] == "2025"
    assert dp["currency"] == "DKK"
    assert dp["scale"] == "million"
    assert dp["source_url"] == _URL
    assert dp["source_tier"] == "T1_primary_filing"
    assert dp["needs_human_review"] is True


def test_a_group_slot_is_never_filled_from_a_segment_fact() -> None:
    section = _build_financial_snapshot(
        _snapshot(), None, [_fact("operating_margin", 3.4, scope="Specialist Watchmakers")]
    )
    assert "operating_margin_primary_filing" not in section


def test_market_derived_fields_stay_separate_from_statement_fields() -> None:
    """A P/E or market cap must never be implied to be a filing fact."""
    section = _build_financial_snapshot(
        _snapshot(),
        {"highlights": {"market_capitalization": 1234.0, "pe_ratio": 18.0}},
        [_fact("revenue", 32549.0)],
    )
    assert section["market_cap_usd_m"]["source_tier"] == "T5_api_aggregator"
    assert section["pe_ratio"]["source_tier"] == "T5_api_aggregator"
    assert section["revenue_primary_filing"]["source_tier"] == "T1_primary_filing"
    assert not any(
        k.startswith("market_cap") and k.endswith("_primary_filing") for k in section
    )


def test_an_unsourced_field_is_absent_rather_than_null_filled() -> None:
    section = _build_financial_snapshot(_snapshot(), None, [_fact("revenue", 1.0)])
    assert "net_debt_primary_filing" not in section


# =========================================================================== #
# D4 — source-neutral copy                                                    #
# =========================================================================== #


def _eu_snapshot(exchange: str, country: str) -> dict:
    return {"company_identity": {"exchange": exchange, "country_domicile": country}}


# Italy (``MI``) has no regulated-disclosure connector mapping yet; PR-E adds it
# together with the live CONSOB-authorised venue, and extends this table.
@pytest.mark.parametrize(
    "exchange,country,expect_absent",
    [
        ("CO", "Denmark", "10-K"),
        ("SW", "Switzerland", "10-K"),
        ("PA", "France", "10-K"),
        ("LSE", "United Kingdom", "10-K"),
    ],
)
def test_european_issuers_never_get_us_filing_vocabulary(
    exchange: str, country: str, expect_absent: str
) -> None:
    name = annual_filing_name(_eu_snapshot(exchange, country))
    assert expect_absent not in name
    assert "40-F" not in name
    assert name


def test_us_issuers_keep_the_us_filing_vocabulary() -> None:
    assert "10-K" in annual_filing_name(
        {"company_identity": {"exchange": "NASDAQ", "country_domicile": "United States"}}
    )


def test_an_unresolved_jurisdiction_is_not_silently_relabelled() -> None:
    """Never guess: an issuer whose venue does not resolve keeps the existing
    wording rather than being given a jurisdiction it may not have."""
    assert "10-K" in annual_filing_name({})
    assert "10-K" in annual_filing_name({"company_identity": {}})


def test_statement_source_label_names_the_actual_channel() -> None:
    issuer = FinancialEvidenceState(
        available=True, best_source="issuer_primary_document", best_tier="T1_primary_filing"
    )
    regulator = FinancialEvidenceState(
        available=True, best_source="sec_edgar_xbrl", best_tier="T2_regulator_or_gov"
    )
    assert _statement_source_label(issuer) == "issuer-primary statement fundamentals"
    assert "regulator" in _statement_source_label(regulator)
    # No channel resolved ⇒ no channel claimed.
    assert _statement_source_label(None) == "statement fundamentals"
    assert "SEC" not in _statement_source_label(issuer)


# =========================================================================== #
# D5 — per-company DFR identity gaps                                          #
# =========================================================================== #


def _identity_section(**over) -> dict:
    base = {
        "legal_name": {"value": "Example Issuer A/S"},
        "ticker": {"value": "EXA"},
        "exchange": {"value": "CO"},
        "country_domicile": {"value": "Denmark"},
        "isin": {"value": "DK0060252690"},
        "lei": {"value": "5493001EIDBW9K5VYK55"},
        "sector": {"value": "Consumer Discretionary"},
        "reporting_currency": {"value": "DKK"},
    }
    for key, value in over.items():
        base[key] = {"value": value}
    return base


def test_identity_completeness_reports_present_and_missing_per_field() -> None:
    present, missing = _identity_completeness(_identity_section(lei=None))
    assert "lei" in missing
    assert "isin" in present
    assert set(present) | set(missing) == set(IDENTITY_COMPLETENESS_FIELDS)


def test_a_blank_string_counts_as_missing_not_present() -> None:
    _, missing = _identity_completeness(_identity_section(lei="   "))
    assert "lei" in missing


def test_a_field_the_report_never_carried_counts_as_missing() -> None:
    """Honest: the report did not answer it. Omitting it from BOTH lists would
    leave the council unable to tell "absent" from "not asked"."""
    _, missing = _identity_completeness({"legal_name": {"value": "X"}})
    assert "lei" in missing and "isin" in missing


def _company_summary(ref: str, ticker: str, identity: dict):
    candidate = DiscoveryCandidate(
        id=uuid.uuid4(),
        ticker=ticker,
        exchange="CO",
        company_name=f"{ticker} A/S",
        country="Denmark",
        sector="Consumer Discretionary",
    )
    payload = {"company_identity": identity, "financial_snapshot": {}}
    report = Report(
        id=uuid.uuid4(),
        title=f"{ticker} analysis",
        status="draft",
        content_markdown="```json\n" + json.dumps(payload) + "\n```",
        created_at=datetime.now(timezone.utc),
    )
    return build_company_summary(citation_ref=ref, candidate=candidate, report=report)


def test_case_a_only_the_company_that_lacks_lei_reports_it_missing() -> None:
    """The exact live regression: CFR has a sourced LEI, PNDORA does not."""
    cfr = _company_summary("F1", "CFR", _identity_section())
    pnd = _company_summary("F2", "PNDORA", _identity_section(lei=None))
    assert "lei" in cfr.identity_fields_present
    assert "lei" not in cfr.identity_fields_missing
    assert "lei" in pnd.identity_fields_missing
    assert "lei" not in pnd.identity_fields_present


def test_case_b_both_missing_is_reported_for_both() -> None:
    a = _company_summary("F1", "AAA", _identity_section(lei=None))
    b = _company_summary("F2", "BBB", _identity_section(lei=None))
    assert "lei" in a.identity_fields_missing
    assert "lei" in b.identity_fields_missing


def test_case_c_neither_missing_yields_no_lei_gap_anywhere() -> None:
    a = _company_summary("F1", "AAA", _identity_section())
    b = _company_summary("F2", "BBB", _identity_section())
    assert "lei" not in a.identity_fields_missing
    assert "lei" not in b.identity_fields_missing


@pytest.mark.parametrize("field", ["isin", "sector", "reporting_currency"])
def test_the_same_per_company_rule_holds_for_every_tracked_identity_field(
    field: str,
) -> None:
    have = _company_summary("F1", "AAA", _identity_section())
    lack = _company_summary("F2", "BBB", _identity_section(**{field: None}))
    assert field in have.identity_fields_present
    assert field in lack.identity_fields_missing


def test_one_companys_gap_never_appears_on_another_summary() -> None:
    summaries = [
        _company_summary("F1", "AAA", _identity_section()),
        _company_summary("F2", "BBB", _identity_section(lei=None, isin=None)),
    ]
    union = set(summaries[1].identity_fields_missing)
    assert not (union & set(summaries[0].identity_fields_missing))


# =========================================================================== #
# Live-acceptance corrective (2026-08-26): newer-period disclosure            #
# =========================================================================== #


def test_a_newer_lower_confidence_period_is_disclosed_not_promoted() -> None:
    """A canonical slot keeps the newest HIGH-confidence figure and SAYS that a
    newer one exists — found live on a Kering report showing FY2024 revenue
    beside an FY2025 series."""
    facts_high = [_fact("revenue", 16874.0, period="2024")]
    facts_wider = facts_high + [
        _fact("revenue", 14675.0, period="2025", confidence="medium")
    ]
    section = _build_financial_snapshot(
        _snapshot(), None, facts_high, historical_facts=facts_wider
    )
    dp = section["revenue_primary_filing"]
    # The slot still shows the figure it can stand behind...
    assert dp["numeric_value"] == 16874.0
    assert dp["period"] == "2024"
    # ...and discloses the newer one.
    assert dp["newer_period_available"]["period"] == "FY2025"
    assert dp["newer_period_available"]["value"] == 14675.0
    assert dp["newer_period_available"]["confidence"] == "medium"


def test_no_disclosure_when_the_slot_already_holds_the_newest_period() -> None:
    facts = [_fact("revenue", 32549.0, period="2025")]
    section = _build_financial_snapshot(_snapshot(), None, facts, historical_facts=facts)
    assert "newer_period_available" not in section["revenue_primary_filing"]


def test_a_newer_segment_figure_never_claims_to_supersede_a_group_one() -> None:
    """A newer SEGMENT figure is not a newer version of the Group figure."""
    group = [_fact("revenue", 22420.0, period="2025")]
    wider = group + [
        _fact("revenue", 3100.0, period="2026", scope="Specialist Watchmakers")
    ]
    section = _build_financial_snapshot(_snapshot(), None, group, historical_facts=wider)
    assert "newer_period_available" not in section["revenue_primary_filing"]


def test_an_interim_period_never_counts_as_a_newer_annual_period() -> None:
    annual = [_fact("revenue", 32549.0, period="2025")]
    wider = annual + [_fact("revenue", 14328.0, period="H1 2026", confidence="medium")]
    section = _build_financial_snapshot(_snapshot(), None, annual, historical_facts=wider)
    assert "newer_period_available" not in section["revenue_primary_filing"]


def test_an_unscoped_slot_still_discloses_a_newer_group_period() -> None:
    """The live Kering shape: an UNSCOPED FY2024 prose figure occupies the
    Group slot (under the pipeline's implicit-Group convention) while the
    report's own Group series carries FY2025. Refusing the same convention one
    line later would let the slot claim "this is the Group revenue" while
    silently declining to mention a newer Group revenue exists."""
    high = [_fact("revenue", 17.2, scope=None, period="2024", scale="billion")]
    wider = high + [
        _fact("revenue", 14675.0, scope="group", period="2025", confidence="medium")
    ]
    dp = _build_financial_snapshot(_snapshot(), None, high, historical_facts=wider)[
        "revenue_primary_filing"
    ]
    assert dp["newer_period_available"]["period"] == "FY2025"


def test_a_newer_segment_period_is_ignored_even_for_an_unscoped_slot() -> None:
    """The asymmetry must hold in both directions."""
    high = [_fact("revenue", 17.2, scope=None, period="2024", scale="billion")]
    wider = high + [
        _fact(
            "revenue", 3100.0, scope="Specialist Watchmakers", period="2025",
            confidence="medium",
        )
    ]
    dp = _build_financial_snapshot(_snapshot(), None, high, historical_facts=wider)[
        "revenue_primary_filing"
    ]
    assert "newer_period_available" not in dp
