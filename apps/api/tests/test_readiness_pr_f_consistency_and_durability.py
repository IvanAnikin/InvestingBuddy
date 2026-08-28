"""
Private-use production readiness, PR-F — REPORT CONSISTENCY INVARIANTS and
JOB DURABILITY VISIBILITY.

**Consistency.** Every corrective slice in this codebase's history has been the
same story: a report said two incompatible things at once, a human noticed, and
a targeted fix followed. A segment figure in a Group slot. "Source the annual
report" beside a T1 revenue figure extracted from that very report. "All current
data is T6" next to a validated T1 fact. A Python ``None`` rendered into a
sentence. Each was found by READING. That does not scale and is not a readiness
bar, so the contradiction CLASSES become assertions.

Each invariant is tested from BOTH sides: a report that violates it must be
caught, and a correct report must NOT be flagged. A checker that only ever fires
is as useless as one that never does.

**Durability.** A full analysis runs in a process-local background task, so an
app restart mid-run leaves the stored envelope on ``running`` forever. The state
was always recoverable — a fresh POST past the stale threshold restarts it — but
nothing SAID so, and a researcher watching the status endpoint could not tell a
working job from one that died an hour ago.

Fully offline and deterministic: no network, no LLM, no Azure.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.services.market_discovery_service import (
    ANALYSIS_STATUS_INTERRUPTED,
    _new_analysis_job_envelope,
    _store_analysis_job_envelope,
    analysis_job_stale_after_minutes,
    describe_analysis_job,
    get_analysis_job_envelope,
    start_candidate_analysis,
    sweep_interrupted_analysis_jobs,
)
from app.services.report_consistency import (
    ALL_INVARIANTS,
    CURRENT_PERIOD_CONTRADICTION,
    DFR_FIELD_GAP_FALSE_POSITIVE,
    DUPLICATE_DOCUMENT_IDENTITY,
    DUPLICATE_EVENT_IDENTITY,
    ENUM_REPR_LEAK,
    FACT_PRESENT_AND_MISSING,
    HISTORICAL_AS_CURRENT,
    INTERIM_AS_ANNUAL,
    NONE_LITERAL_LEAK,
    PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP,
    REGULATOR_VS_ISSUER_CHANNEL_MISMATCH,
    SCOPE_CONTRADICTION,
    SERIOUS_INVARIANTS,
    SOURCE_TIER_CONTRADICTION,
    audit_report_consistency,
)


def _dp(value, **over) -> dict:
    base = {
        "value": str(value),
        "numeric_value": value,
        "period": "2025",
        "currency": "DKK",
        "scale": "million",
        "source_tier": "T1_primary_filing",
        "source_url": "https://issuer.test/ar.pdf",
        "provenance": "sourced_fact",
    }
    base.update(over)
    return base


def _clean_report() -> dict:
    """A report with NOTHING wrong with it. Every negative test uses this as
    its baseline, so a checker that fires on a correct report is caught."""
    return {
        "financial_snapshot": {
            "type": "financial_snapshot",
            "source_tier": "T1_primary_filing",
            "revenue_primary_filing": _dp(32549.0, scope="group"),
            "operating_profit_primary_filing": _dp(7783.0, scope="group"),
            "revenue_current_period": _dp(14328.0, period="H1 2026", scope="group"),
            "market_cap_usd_m": _dp(
                1234.0, source_tier="T5_api_aggregator", period=None
            ),
            "fundamentals_note": {
                "value": "Statements sourced from the issuer's own annual report.",
                "fundamentals_source": "issuer_primary_document",
            },
        },
        "historical_trends": {
            "type": "historical_trends",
            "series": {
                "value": [
                    {
                        "metric": "revenue",
                        "scope": "Group",
                        "scope_type": "group",
                        "period_type": "annual",
                        "periods": [
                            {"period": "FY2024", "value": 31673.0},
                            {"period": "FY2025", "value": 32549.0},
                        ],
                    }
                ]
            },
        },
        "missing_information": {
            "type": "missing_information",
            "missing_items": {"value": [{"field": "identity.lei"}]},
        },
        "source_quality_review": {
            "type": "source_quality_review",
            "missing_primary_sources": {
                "value": [
                    "Issuer annual report — ALREADY INGESTED; remaining gap is "
                    "completeness, not acquisition."
                ]
            },
        },
        "primary_documents": {"documents": {"value": [{"content_hash": "a" * 64}]}},
        "news_catalyst_discovery": {
            "catalysts": {
                "value": [{"title": "Q2 results published", "date": "2026-08-12"}]
            }
        },
    }


# =========================================================================== #
# The clean baseline                                                          #
# =========================================================================== #


def test_a_correct_report_produces_no_serious_finding() -> None:
    audit = audit_report_consistency(_clean_report(), company_country="Denmark")
    assert audit.is_clean, audit.summary()
    assert audit.serious == []


def test_an_empty_or_malformed_report_never_crashes() -> None:
    for payload in (None, {}, {"financial_snapshot": "not-a-dict"}, {"x": [1, 2]}):
        audit = audit_report_consistency(payload)  # type: ignore[arg-type]
        assert isinstance(audit.findings, list)


def test_every_named_invariant_is_registered() -> None:
    # 13 from PR-F, plus the four manual-QA state/copy invariants (see
    # ``tests/test_manual_qa_state_and_copy.py``).
    assert len(ALL_INVARIANTS) == 17
    assert SERIOUS_INVARIANTS <= set(ALL_INVARIANTS)
    assert len(set(ALL_INVARIANTS)) == len(ALL_INVARIANTS)


# =========================================================================== #
# One test per class, from BOTH sides                                         #
# =========================================================================== #


def test_fact_present_and_missing_is_caught() -> None:
    report = _clean_report()
    report["missing_information"]["missing_items"]["value"].append(
        {"field": "financials.revenue"}
    )
    audit = audit_report_consistency(report)
    assert FACT_PRESENT_AND_MISSING in audit.counts()


def test_a_genuinely_missing_field_is_not_flagged() -> None:
    """``identity.lei`` really IS missing — it must stay listed."""
    audit = audit_report_consistency(_clean_report())
    assert FACT_PRESENT_AND_MISSING not in audit.counts()


def test_acquisition_gap_beside_an_ingested_filing_is_caught() -> None:
    report = _clean_report()
    report["source_quality_review"]["missing_primary_sources"]["value"] = [
        "Annual report — T1_primary_filing required for financials"
    ]
    audit = audit_report_consistency(report)
    assert PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP in audit.counts()


def test_an_already_ingested_note_is_not_an_acquisition_gap() -> None:
    audit = audit_report_consistency(_clean_report())
    assert PRIMARY_SOURCE_PRESENT_BUT_ACQUISITION_GAP not in audit.counts()


def test_a_segment_figure_in_a_group_slot_is_caught() -> None:
    report = _clean_report()
    report["financial_snapshot"]["operating_profit_primary_filing"] = _dp(
        107.0, scope="Specialist Watchmakers"
    )
    audit = audit_report_consistency(report)
    assert SCOPE_CONTRADICTION in audit.counts()


def test_a_series_that_mixes_scopes_is_caught() -> None:
    report = _clean_report()
    report["historical_trends"]["series"]["value"][0]["periods"] = [
        {"period": "FY2024", "value": 1.0, "scope": "Group"},
        {"period": "FY2025", "value": 2.0, "scope": "Jewellery Maisons"},
    ]
    audit = audit_report_consistency(report)
    assert SCOPE_CONTRADICTION in audit.counts()


def test_an_interim_period_in_an_annual_slot_is_caught() -> None:
    report = _clean_report()
    report["financial_snapshot"]["revenue_primary_filing"] = _dp(
        14328.0, period="H1 2026", scope="group"
    )
    audit = audit_report_consistency(report)
    assert INTERIM_AS_ANNUAL in audit.counts()


def test_a_full_year_period_in_a_current_period_slot_is_caught() -> None:
    report = _clean_report()
    report["financial_snapshot"]["revenue_current_period"] = _dp(
        32549.0, period="2025", scope="group"
    )
    audit = audit_report_consistency(report)
    assert CURRENT_PERIOD_CONTRADICTION in audit.counts()


def test_correctly_separated_annual_and_interim_slots_are_not_flagged() -> None:
    audit = audit_report_consistency(_clean_report())
    assert INTERIM_AS_ANNUAL not in audit.counts()
    assert CURRENT_PERIOD_CONTRADICTION not in audit.counts()


def test_a_stale_annual_figure_beside_a_newer_series_is_caught() -> None:
    report = _clean_report()
    report["financial_snapshot"]["revenue_primary_filing"] = _dp(
        31673.0, period="2024", scope="group"
    )
    audit = audit_report_consistency(report)
    assert HISTORICAL_AS_CURRENT in audit.counts()


def test_the_latest_annual_figure_is_not_flagged_as_stale() -> None:
    audit = audit_report_consistency(_clean_report())
    assert HISTORICAL_AS_CURRENT not in audit.counts()


def test_a_market_metric_claiming_a_filing_tier_is_caught() -> None:
    report = _clean_report()
    report["financial_snapshot"]["market_cap_usd_m"] = _dp(
        1234.0, source_tier="T1_primary_filing"
    )
    audit = audit_report_consistency(report)
    assert SOURCE_TIER_CONTRADICTION in audit.counts()


def test_a_primary_filing_slot_with_a_non_t1_tier_is_caught() -> None:
    report = _clean_report()
    report["financial_snapshot"]["revenue_primary_filing"] = _dp(
        32549.0, scope="group", source_tier="T5_api_aggregator"
    )
    audit = audit_report_consistency(report)
    assert SOURCE_TIER_CONTRADICTION in audit.counts()


def test_correct_tiers_are_not_flagged() -> None:
    audit = audit_report_consistency(_clean_report())
    assert SOURCE_TIER_CONTRADICTION not in audit.counts()


def test_issuer_facts_described_as_sec_xbrl_is_caught() -> None:
    report = _clean_report()
    report["financial_snapshot"]["fundamentals_note"]["value"] = (
        "Statements from SEC XBRL structured facts."
    )
    audit = audit_report_consistency(report)
    assert REGULATOR_VS_ISSUER_CHANNEL_MISMATCH in audit.counts()


def test_us_filing_vocabulary_for_a_european_issuer_is_caught() -> None:
    report = _clean_report()
    report["source_quality_review"]["missing_primary_sources"]["value"] = [
        "Annual report / 10-K / 40-F narrative is not sourced"
    ]
    audit = audit_report_consistency(report, company_country="Denmark")
    assert REGULATOR_VS_ISSUER_CHANNEL_MISMATCH in audit.counts()


def test_us_filing_vocabulary_for_a_us_issuer_is_correct() -> None:
    report = _clean_report()
    report["source_quality_review"]["missing_primary_sources"]["value"] = [
        "Annual report / 10-K / 40-F narrative is not sourced"
    ]
    audit = audit_report_consistency(report, company_country="United States")
    assert REGULATOR_VS_ISSUER_CHANNEL_MISMATCH not in audit.counts()


def test_a_none_literal_in_rendered_text_is_caught() -> None:
    report = _clean_report()
    report["missing_information"]["note"] = "Revenue for None was not sourced."
    audit = audit_report_consistency(report)
    assert NONE_LITERAL_LEAK in audit.counts()


@pytest.mark.parametrize(
    "text",
    [
        "none of the above applies",
        "There are no nonexistent sections.",
        "Nonetheless the figure is sourced.",
    ],
)
def test_ordinary_prose_containing_none_as_a_word_part_is_not_flagged(
    text: str,
) -> None:
    report = _clean_report()
    report["missing_information"]["note"] = text
    audit = audit_report_consistency(report)
    assert NONE_LITERAL_LEAK not in audit.counts()


def test_a_url_containing_none_is_not_a_rendering_defect() -> None:
    report = _clean_report()
    report["financial_snapshot"]["revenue_primary_filing"]["source_url"] = (
        "https://issuer.test/None/report.pdf"
    )
    audit = audit_report_consistency(report)
    assert NONE_LITERAL_LEAK not in audit.counts()


def test_an_enum_repr_in_rendered_text_is_caught() -> None:
    report = _clean_report()
    report["missing_information"]["note"] = (
        "Tier was SourceTier.T1_PRIMARY_FILING for this datapoint."
    )
    audit = audit_report_consistency(report)
    assert ENUM_REPR_LEAK in audit.counts()


def test_an_ordinary_dotted_phrase_is_not_an_enum_repr() -> None:
    report = _clean_report()
    report["missing_information"]["note"] = "See section 4.2 of the annual report."
    audit = audit_report_consistency(report)
    assert ENUM_REPR_LEAK not in audit.counts()


def test_a_duplicated_document_identity_is_caught() -> None:
    report = _clean_report()
    report["primary_documents"]["documents"]["value"].append({"content_hash": "a" * 64})
    audit = audit_report_consistency(report)
    assert DUPLICATE_DOCUMENT_IDENTITY in audit.counts()


def test_a_duplicated_event_identity_is_caught() -> None:
    report = _clean_report()
    report["news_catalyst_discovery"]["catalysts"]["value"].append(
        {
            "title": "Company Announcement No. 1015: Q2 results published",
            "date": "2026-08-12",
        }
    )
    audit = audit_report_consistency(report)
    assert DUPLICATE_EVENT_IDENTITY in audit.counts()


def test_two_genuinely_different_events_are_not_flagged() -> None:
    report = _clean_report()
    report["news_catalyst_discovery"]["catalysts"]["value"].append(
        {"title": "New CFO appointed", "date": "2026-08-12"}
    )
    audit = audit_report_consistency(report)
    assert DUPLICATE_EVENT_IDENTITY not in audit.counts()


def test_a_dfr_field_listed_present_and_missing_is_caught() -> None:
    audit = audit_report_consistency(
        _clean_report(),
        field_review_companies=[
            {
                "id": "F1",
                "identity_fields_present": ["lei", "isin"],
                "identity_fields_missing": ["lei"],
            }
        ],
    )
    assert DFR_FIELD_GAP_FALSE_POSITIVE in audit.counts()


def test_consistent_dfr_field_lists_are_not_flagged() -> None:
    audit = audit_report_consistency(
        _clean_report(),
        field_review_companies=[
            {
                "id": "F1",
                "identity_fields_present": ["isin"],
                "identity_fields_missing": ["lei"],
            },
            {
                "id": "F2",
                "identity_fields_present": ["lei", "isin"],
                "identity_fields_missing": [],
            },
        ],
    )
    assert DFR_FIELD_GAP_FALSE_POSITIVE not in audit.counts()


def test_the_summary_is_human_readable_and_leaks_nothing() -> None:
    report = _clean_report()
    report["missing_information"]["missing_items"]["value"].append(
        {"field": "financials.revenue"}
    )
    audit = audit_report_consistency(report)
    assert "FACT_PRESENT_AND_MISSING" in audit.summary()
    assert "None" not in audit.summary()


# =========================================================================== #
# Job durability visibility                                                   #
# =========================================================================== #


class _FakeCandidate:
    def __init__(self, raw: dict | None = None) -> None:
        self.id = uuid.uuid4()
        self.ticker = "PNDORA"
        self.raw_signal_json = raw or {}
        self.updated_at = datetime.now(timezone.utc)


def _envelope(status: str, *, age_minutes: int) -> dict:
    started = datetime.now(timezone.utc) - timedelta(minutes=age_minutes)
    return _new_analysis_job_envelope(status=status, started_at=started.isoformat())


def test_a_running_job_within_its_budget_is_reported_as_running() -> None:
    described = describe_analysis_job(_envelope("running", age_minutes=1))
    assert described["status"] == "running"
    assert "recoverable" not in described


def test_an_abandoned_job_is_reported_as_interrupted_and_recoverable() -> None:
    stale = analysis_job_stale_after_minutes() + 5
    described = describe_analysis_job(_envelope("running", age_minutes=stale))
    assert described["status"] == ANALYSIS_STATUS_INTERRUPTED
    assert described["recoverable"] is True
    assert "restart" in described["interrupted_reason"].lower()
    assert "None" not in described["interrupted_reason"]


def test_a_completed_job_is_never_relabelled_interrupted() -> None:
    old = _envelope("completed", age_minutes=100_000)
    assert describe_analysis_job(old)["status"] == "completed"


def test_a_failed_job_is_never_relabelled_interrupted() -> None:
    old = _envelope("failed", age_minutes=100_000)
    assert describe_analysis_job(old)["status"] == "failed"


def test_describing_a_missing_envelope_is_safe() -> None:
    assert describe_analysis_job(None) == {}
    assert describe_analysis_job({}) == {}


def test_describing_never_mutates_the_stored_envelope() -> None:
    """The dead worker's audit trail must survive untouched."""
    stale = _envelope("running", age_minutes=analysis_job_stale_after_minutes() + 5)
    snapshot = dict(stale)
    describe_analysis_job(stale)
    assert stale == snapshot


@pytest.mark.asyncio
async def test_an_interrupted_job_can_be_restarted_without_duplicating() -> None:
    """Recovery must be safe: re-running an abandoned job starts ONE fresh job,
    and re-running a COMPLETED one starts none."""

    class _Session:
        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

    candidate = _FakeCandidate()
    _store_analysis_job_envelope(
        candidate,
        _envelope("running", age_minutes=analysis_job_stale_after_minutes() + 5),
    )
    envelope, scheduled = await start_candidate_analysis(_Session(), candidate)
    assert scheduled is True
    assert envelope["status"] == "pending"

    # A second immediate attempt must NOT start another expensive council run.
    _, scheduled_again = await start_candidate_analysis(_Session(), candidate)
    assert scheduled_again is False


@pytest.mark.asyncio
async def test_a_completed_job_is_not_restarted_by_the_recovery_path() -> None:
    class _Session:
        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

    candidate = _FakeCandidate()
    _store_analysis_job_envelope(candidate, _envelope("completed", age_minutes=99_999))
    _, scheduled = await start_candidate_analysis(_Session(), candidate)
    assert scheduled is False


def test_the_stale_threshold_is_derived_from_the_real_budgets() -> None:
    """A fixed literal could mark a legitimately long council run as dead."""
    from app.core.config import Settings

    small = Settings(llm_council_total_budget_seconds=60)
    large = Settings(llm_council_total_budget_seconds=6000)
    assert analysis_job_stale_after_minutes(large) > analysis_job_stale_after_minutes(
        small
    )


def test_the_interruption_sweep_is_exported_for_startup_use() -> None:
    assert callable(sweep_interrupted_analysis_jobs)


def test_a_stored_envelope_round_trips_through_the_candidate() -> None:
    candidate = _FakeCandidate()
    envelope = _envelope("running", age_minutes=1)
    _store_analysis_job_envelope(candidate, envelope)
    assert get_analysis_job_envelope(candidate) == envelope


# =========================================================================== #
# Live-acceptance corrective (2026-08-26): disclosing a newer period          #
# =========================================================================== #


def test_a_disclosed_newer_period_is_not_a_historical_as_current_finding() -> None:
    """Found by the invariant checker itself, on a live Kering report.

    The canonical slot showed FY2024 revenue while the report's own Historical
    Trends section showed FY2025 — because the FY2025 fact fell below the
    confidence bar a canonical slot requires. Promoting it would violate that
    bar; hiding the difference is what made it read as a contradiction. So the
    slot DISCLOSES it, and a slot that says exactly which period it can stand
    behind, and where the newer one is, is not presenting historical as
    current."""
    report = _clean_report()
    report["financial_snapshot"]["revenue_primary_filing"] = _dp(
        16874.0,
        period="2024",
        scope="group",
        newer_period_available={
            "period": "FY2025",
            "value": 14675.0,
            "confidence": "medium",
            "note": "A newer FY2025 figure exists at medium confidence.",
        },
    )
    report["historical_trends"]["series"]["value"][0]["periods"] = [
        {"period": "FY2024", "value": 16874.0},
        {"period": "FY2025", "value": 14675.0},
    ]
    audit = audit_report_consistency(report)
    assert HISTORICAL_AS_CURRENT not in audit.counts()


def test_an_undisclosed_older_period_is_still_a_finding() -> None:
    """The disclosure must be an explanation, not an escape hatch."""
    report = _clean_report()
    report["financial_snapshot"]["revenue_primary_filing"] = _dp(
        16874.0, period="2024", scope="group"
    )
    report["historical_trends"]["series"]["value"][0]["periods"] = [
        {"period": "FY2024", "value": 16874.0},
        {"period": "FY2025", "value": 14675.0},
    ]
    audit = audit_report_consistency(report)
    assert HISTORICAL_AS_CURRENT in audit.counts()
