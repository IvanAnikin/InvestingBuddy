"""
Phase 32A Slice 6B — report-integrity section-builder fixes discovered during a
real staging BRBY (Burberry Group plc, LSE) E2E QA pass.

Covers:
  C5 — blocking-gap contradiction ("32 vs 0"): ``_build_research_completeness_review``
       read the wrong dict key (``blocking_gaps_count`` — never written by the
       producer) instead of ``len(blocking_gaps)``.
  C6 — missing-count contradiction ("17 vs 0"): ``_build_data_availability_summary``'s
       ``missing_count`` is renamed to ``missing_financial_fields_count`` (a
       genuinely NARROWER, financial-agent-only metric than the whole-report
       ``_build_missing_information()`` union) and the absent-summary branch no
       longer hardcodes a false ``0``.
  C9 — source/citation count scope labeling: ``_build_source_citation_appendix``'s
       narrower deterministic pre-council envelope is explicitly labeled, never
       conflated with the broader six-count reconciliation block.

All tests run OFFLINE — pure function calls, no network, no DB, no LLM.
"""

from __future__ import annotations

from typing import Any

from app.services.final_report_generator import (
    _APPENDIX_RECONCILE_NOTE,
    _build_company_identity,
    _build_data_availability_summary,
    _build_missing_information,
    _build_research_completeness_review,
    _build_source_citation_appendix,
    _evidence_reconciliation_counts,
)

# ---------------------------------------------------------------------------
# C1 hotfix — identity resolution must not re-surface the ticker-as-legal-name
# safety stub when a real, verified name already exists in the company DB
# record. Found live on staging: BRBY's company_snapshot legitimately resolves
# legal_name="BRBY" (free_real_provider._not_sourced_profile()'s deliberate
# "never guess a wrong company from an unrelated SEC index entry" stub for
# exchanges SEC EDGAR doesn't cover), which _build_company_identity's
# snapshot-always-wins precedence blindly trusted -- even though
# companies.name already correctly held "Burberry Group plc" (seeded by the
# already-closed C1 fix in market_discovery_service.py/ensure_company). The
# original C1 fix addressed WHERE the DB row gets seeded; this addresses
# WHETHER the seeded value is actually consulted when a snapshot exists.
# ---------------------------------------------------------------------------


def test_identity_prefers_db_record_name_over_snapshot_ticker_stub():
    snapshot = {
        "company_identity": {"legal_name": "BRBY", "ticker": "BRBY", "exchange": "LSE"},
        "profile": {},
        "is_mock": False,
    }
    record = {"name": "Burberry Group plc", "ticker": "BRBY", "exchange": "LSE"}
    identity = _build_company_identity(snapshot, record)
    assert identity["legal_name"]["value"] == "Burberry Group plc"
    assert identity["legal_name"]["source"] == "company_db_record"


def test_identity_keeps_real_snapshot_legal_name_when_genuinely_resolved():
    # A snapshot that DID resolve a real, distinct legal name (SEC/GLEIF/
    # company_ir) must still win over the DB record -- this is the "fresher,
    # this-run identity resolution" precedence the original design intended;
    # the hotfix only applies when the snapshot's own value is the bare-ticker
    # stub, never otherwise.
    snapshot = {
        "company_identity": {
            "legal_name": "Apple Inc.",
            "ticker": "AAPL",
            "exchange": "NASDAQ",
        },
        "profile": {},
        "is_mock": False,
    }
    record = {"name": "Apple Inc. (stale registry copy)", "ticker": "AAPL"}
    identity = _build_company_identity(snapshot, record)
    assert identity["legal_name"]["value"] == "Apple Inc."
    assert identity["legal_name"]["source"] == "company_snapshot"


def test_identity_falls_back_to_ticker_when_db_record_is_also_a_placeholder():
    # No real name anywhere -- must NOT fabricate one. Ticker-as-stub survives
    # honestly rather than being swapped for an equally-uninformative DB value.
    snapshot = {
        "company_identity": {"legal_name": "XYZ", "ticker": "XYZ", "exchange": "LSE"},
        "profile": {},
        "is_mock": False,
    }
    record = {"name": "XYZ", "ticker": "XYZ"}
    identity = _build_company_identity(snapshot, record)
    assert identity["legal_name"]["value"] == "XYZ"
    assert identity["legal_name"]["source"] == "company_snapshot"


def test_identity_handles_venue_suffixed_ticker_stub():
    # free_real_provider / discovery scans sometimes carry a venue-suffixed
    # ticker (e.g. "BRBY.LSE") as the stub legal_name -- is_placeholder_company_name
    # already recognizes this form; confirm the hotfix does too.
    snapshot = {
        "company_identity": {
            "legal_name": "BRBY.LSE",
            "ticker": "BRBY",
            "exchange": "LSE",
        },
        "profile": {},
        "is_mock": False,
    }
    record = {"name": "Burberry Group plc", "ticker": "BRBY"}
    identity = _build_company_identity(snapshot, record)
    assert identity["legal_name"]["value"] == "Burberry Group plc"


# ---------------------------------------------------------------------------
# C5 — blocking_gaps_count / non_blocking_gaps_count
# ---------------------------------------------------------------------------


def test_blocking_gaps_count_reflects_real_list_length():
    summary = {
        "complete_sections": ["a"],
        "incomplete_sections": ["b"],
        "blocking_gaps": [
            "Required field missing: revenue",
            "Required field missing: ebitda",
            "Schema validation error: xyz",
        ],
        "non_blocking_gaps": ["Optional section absent: catalysts"],
    }
    review = _build_research_completeness_review(summary)
    assert review["blocking_gaps_count"] == 3
    assert review["non_blocking_gaps_count"] == 1


def test_blocking_gaps_count_zero_when_genuinely_empty():
    summary = {"blocking_gaps": [], "non_blocking_gaps": []}
    review = _build_research_completeness_review(summary)
    assert review["blocking_gaps_count"] == 0
    assert review["non_blocking_gaps_count"] == 0


def test_blocking_gaps_count_ignores_stale_unused_count_key():
    """
    Regression guard: even if a stale ``blocking_gaps_count`` key were present
    (never written by the real producer), the real list length must win —
    never a fabricated/independent count.
    """
    summary = {
        "blocking_gaps": ["a", "b"],
        "blocking_gaps_count": 999,  # stale/foreign key — must be ignored
        "non_blocking_gaps": [],
    }
    review = _build_research_completeness_review(summary)
    assert review["blocking_gaps_count"] == 2


# ---------------------------------------------------------------------------
# C6 — missing_financial_fields_count (renamed, narrower scope)
# ---------------------------------------------------------------------------


def test_missing_financial_fields_count_present_when_summary_present():
    financial_data_summary = {
        "available_count": 5,
        "missing_count": 4,
        "missing_fields": ["ebitda", "revenue_ttm", "pe_ratio", "market_cap"],
    }
    section = _build_data_availability_summary(
        financial_data_summary, fundamentals_available=True, source_tier="T2_regulator_or_gov"
    )
    assert section["missing_financial_fields_count"] == 4
    assert "missing_count" not in section
    assert "scope_note" in section


def test_missing_financial_fields_count_none_not_zero_when_summary_absent():
    """
    Non-US issuers (e.g. BRBY) with no financial_data_summary must show an
    honest not-available marker, never a false 0 that reads as "verified
    complete".
    """
    section = _build_data_availability_summary(
        None, fundamentals_available=None, source_tier=None
    )
    assert section["missing_financial_fields_count"] is None
    assert section["missing_financial_fields_count"] != 0
    assert "scope_note" in section
    assert section["note"]["provenance"] == "missing_data"


def test_whole_report_missing_information_independent_of_rename():
    """
    _build_missing_information's whole-report cross-source count (the
    "17-equivalent") is computed independently and unaffected by the
    data_availability_summary rename.
    """
    company_snapshot = {"missing_fields": ["identity.isin", "identity.lei"]}
    financial_data_summary = {"missing_fields": ["ebitda", "revenue_ttm"]}
    research_completeness_summary = {"incomplete_sections": ["catalysts"]}

    missing = _build_missing_information(
        financial_data_summary, research_completeness_summary, company_snapshot, None
    )
    assert missing["total_missing_items"] == 5
    assert "scope_note" in missing


# ---------------------------------------------------------------------------
# C9 — source/citation appendix scope labeling (no logic change)
# ---------------------------------------------------------------------------


def test_source_citation_appendix_carries_scope_label():
    appendix = _build_source_citation_appendix([], [])
    assert appendix["sources"]["scope"] == "deterministic_pre_council_draft"
    assert appendix["citations"]["scope"] == "deterministic_pre_council_draft"
    # Existing totals are untouched by the labeling-only change.
    assert appendix["sources"]["total"] == 0
    assert appendix["citations"]["total"] == 0


def test_appendix_reconcile_note_mentions_scope_distinction():
    assert "deterministic pre-council draft" in _APPENDIX_RECONCILE_NOTE
    assert "db_persisted_source_count" in _APPENDIX_RECONCILE_NOTE
    assert "council-added evidence" in _APPENDIX_RECONCILE_NOTE


def test_reconciliation_counts_unchanged_by_scope_labeling():
    """
    Regression guard on the correct existing logic: the six reconciliation
    counts must not move as a result of the C9 labeling-only change.
    """

    class _FakeCouncilResult:
        persistable_evidence: list[Any] = []
        primary_document_artifacts: list[Any] = []

    def _compute() -> dict[str, int]:
        return _evidence_reconciliation_counts(
            _FakeCouncilResult(),
            [],
            [],
            {},
        )

    counts_a = _compute()
    counts_b = _compute()
    assert counts_a == counts_b
    assert counts_a == {
        "primary_source_reference_count": 0,
        "extracted_evidence_count": 0,
        "structured_financial_fact_count": 0,
        "db_persisted_source_count": 0,
        "db_persisted_citation_count": 0,
        "council_claim_citation_count": 0,
    }
