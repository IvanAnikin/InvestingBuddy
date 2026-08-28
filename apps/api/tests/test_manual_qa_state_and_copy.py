"""
Manual-QA state/copy reconciliation — five contradictions a human found by
reading three otherwise "0 consistency findings" reports.

None of these is an extraction or period defect. Every one is the report
telling a reader something about its OWN state that the same report disproves
two sections later.

Q1  **A document's facts were evicted, then reported as zero.** The venue
    adapter emits excerpts before facts and the caller truncated the result
    with the generic per-source cap (five) — so five excerpts survived and
    every validated fact was dropped. The card read "5 excerpt(s), 0 fact(s)"
    for the very document whose eight facts the report was presenting in its
    own current-period slots, and the council never saw them as citable
    evidence at all. ``_prioritize_ir_items`` already exists to prevent exactly
    this; it was simply never applied to the venue path. Separately, the two
    counts genuinely describe different populations, and neither row said so.

Q2  **Stale connector state.** ``fetch_filings`` returns the venue reference
    plus an honest "content behind this venue is not fetched" gap;
    ``fetch_events`` then performs the live retrieval. Both results were kept,
    so a Pandora report carried "Denmark regulated-disclosure connector
    scaffolded … pending regulator integration" beside live Nasdaq Nordic
    announcements, and a Moncler report said "live retrieval is disabled"
    above eight facts extracted from a document opened at that very venue.

Q3  **Jurisdiction drift.** ``borsa_italiana`` was added to the connector
    registry and never to the research agent's display-name map, so every
    Italian issuer silently fell through to "Cross-check company name and
    domicile against SEC EDGAR or SEDAR+" — neither of which lists it.

Q4  **"Primary filings (T1/T2) required"** was a fixed string in the risk
    summary, printed unchanged on reports already presenting validated T1
    statement facts.

Q5  **"Regulator filing events (SEC EDGAR)"** was the label for every issuer,
    naming a venue a Danish or Italian company has no relationship with.

Fully offline and deterministic: no network, no LLM, no Azure, no DB.
"""

from __future__ import annotations

import pytest

from app.agents.analysis_council.risk_agent import _incompleteness_clause
from app.agents.research_team.research_completeness_agent import (
    _REGULATOR_DISPLAY_NAMES as _COMPLETENESS_DISPLAY_NAMES,
)
from app.agents.research_team.research_completeness_agent import (
    _jurisdiction_aware_snapshot_tasks,
)
from app.agents.research_team.source_quality_agent import (
    _REGULATOR_DISPLAY_NAMES as _SOURCE_QUALITY_DISPLAY_NAMES,
)
from app.services.canonical_evidence import (
    FundamentalsEvidence,
    build_evidence_channels,
)
from app.services.report_consistency import (
    ALL_INVARIANTS,
    CONNECTOR_STATE_CONTRADICTION,
    FACT_COUNT_SEMANTICS_MISMATCH,
    JURISDICTION_TASK_MISMATCH,
    PRIMARY_FILING_REQUIRED_CONTRADICTION,
    SEVERITY_SERIOUS,
    audit_report_consistency,
)
from app.services.sources.company_evidence import (
    _REGULATOR_VENUE_NAMES,
    REGULATOR_REFERENCE_IDS,
    regulator_venue_display_name,
)
from app.services.sources.connector_state import (
    ConnectorRunState,
    reconcile_connector_state_gaps,
)
from app.services.sources.gaps import GapSeverity, GapType, SourceGap

# =========================================================================== #
# Q2 — connector state is read from THIS run                                  #
# =========================================================================== #


def _gap(source_id: str, gap_type: GapType, message: str) -> SourceGap:
    return SourceGap(
        connector_key=source_id,
        source_id=source_id,
        gap_type=gap_type,
        severity=GapSeverity.info,
        message=message,
        blocks_research_complete=False,
    )


class _Item:
    def __init__(self, source_type: str, content_source: str | None = None) -> None:
        self.source_type = source_type
        self.content_source = content_source


def test_a_gap_survives_when_the_connector_never_went_live() -> None:
    """The reference-only path is UNCHANGED — that gap is simply true."""
    gaps = [_gap("six_swiss", GapType.primary_filing_unavailable, "not fetched")]
    state = ConnectorRunState()
    assert reconcile_connector_state_gaps(gaps, state) == gaps


def test_a_not_live_gap_is_replaced_when_that_connector_returned_events() -> None:
    state = ConnectorRunState()
    state.observe_events(
        "nordic_disclosures",
        [_Item("regulated_disclosure_event", "Nasdaq Nordic company news")],
    )
    out = reconcile_connector_state_gaps(
        [
            _gap(
                "nordic_disclosures",
                GapType.primary_filing_unavailable,
                "…is not fetched at report time; only a source reference…",
            )
        ],
        state,
    )
    assert len(out) == 1
    assert "not fetched at report time" not in out[0].message
    assert "Nasdaq Nordic company news" in out[0].message
    # The replacement still states what is NOT covered — nothing is softened.
    assert "bounded" in out[0].message.lower()
    assert "lookback" in out[0].message.lower()


def test_a_not_live_gap_is_replaced_when_that_connector_opened_a_document() -> None:
    state = ConnectorRunState()
    state.observe_document("borsa_italiana", venue="eMarket Storage (CONSOB-authorised)")
    out = reconcile_connector_state_gaps(
        [
            _gap(
                "borsa_italiana",
                GapType.primary_filing_unavailable,
                "…live retrieval is disabled; only a source reference…",
            )
        ],
        state,
    )
    assert "live retrieval is disabled" not in out[0].message
    assert "opened and extracted" in out[0].message


def test_only_connector_state_claims_are_reconciled() -> None:
    """A gap about the ISSUER is untouched — live retrieval says nothing
    about whether the issuer has an ISIN."""
    state = ConnectorRunState()
    state.observe_events(
        "nordic_disclosures", [_Item("regulated_disclosure_event", "Nasdaq Nordic")]
    )
    issuer_gap = _gap(
        "nordic_disclosures", GapType.translation_required, "may be Danish-language"
    )
    assert reconcile_connector_state_gaps([issuer_gap], state) == [issuer_gap]


def test_a_connector_with_two_stale_gaps_yields_one_replacement() -> None:
    state = ConnectorRunState()
    state.observe_events(
        "nordic_disclosures", [_Item("regulated_disclosure_event", "Nasdaq Nordic")]
    )
    out = reconcile_connector_state_gaps(
        [
            _gap("nordic_disclosures", GapType.connector_scaffolded, "scaffolded"),
            _gap("nordic_disclosures", GapType.primary_filing_unavailable, "not fetched"),
        ],
        state,
    )
    assert len(out) == 1


def test_a_run_with_no_live_connector_reports_none() -> None:
    state = ConnectorRunState()
    assert state.any_live is False
    assert state.is_live("nordic_disclosures") is False
    assert state.is_live(None) is False
    # A non-event evidence item must never count as live retrieval.
    state.observe_events("nordic_disclosures", [_Item("company_ir_annual_report")])
    assert state.any_live is False


def test_the_sec_gap_no_longer_asserts_another_connectors_state() -> None:
    import inspect

    from app.services.sources.connectors import sec_edgar

    source = inspect.getsource(sec_edgar)
    assert "scaffolded, not yet live" not in source


# =========================================================================== #
# Q3 — jurisdiction-aware next tasks, drift made unrepresentable              #
# =========================================================================== #


@pytest.mark.parametrize(
    ("exchange", "country", "expected"),
    [
        ("MI", "Italy", "eMarket Storage"),
        ("CO", "Denmark", "Nasdaq Nordic"),
        ("SW", "Switzerland", "SIX Swiss"),
        ("PA", "France", "Euronext"),
        ("LSE", "United Kingdom", "FCA"),
    ],
)
def test_a_non_us_issuer_is_sent_to_its_own_venue(
    exchange: str, country: str, expected: str
) -> None:
    tasks = _jurisdiction_aware_snapshot_tasks(
        {"company_identity": {"exchange": exchange, "country_domicile": country}}
    )
    domicile = [t for t in tasks if "Cross-check company name and domicile" in t]
    assert len(domicile) == 1
    assert expected in domicile[0]
    assert "SEC EDGAR" not in domicile[0]
    assert "SEDAR" not in domicile[0]


def test_a_us_issuer_still_goes_to_sec_edgar() -> None:
    tasks = _jurisdiction_aware_snapshot_tasks(
        {"company_identity": {"exchange": "NASDAQ", "country_domicile": "United States"}}
    )
    assert any("SEC EDGAR or SEDAR+" in t for t in tasks)


def test_an_unresolvable_jurisdiction_is_never_guessed_at() -> None:
    tasks = _jurisdiction_aware_snapshot_tasks(
        {"company_identity": {"exchange": "ZZ", "country_domicile": "Atlantis"}}
    )
    assert any("SEC EDGAR or SEDAR+" in t for t in tasks)


def test_every_regulator_connector_has_a_display_name() -> None:
    """THE Q3 regression, made unrepeatable.

    ``borsa_italiana`` was registered as a connector and left out of the
    research agent's map, and the only symptom was a European issuer being
    quietly told to look itself up on SEC EDGAR. Adding a connector without a
    display name now fails here instead.
    """
    for label, mapping in (
        ("research_completeness_agent", _COMPLETENESS_DISPLAY_NAMES),
        ("source_quality_agent", _SOURCE_QUALITY_DISPLAY_NAMES),
        ("company_evidence venue names", _REGULATOR_VENUE_NAMES),
    ):
        missing = sorted(set(REGULATOR_REFERENCE_IDS) - set(mapping))
        assert not missing, f"{label} has no display name for {missing}"


def test_a_venue_name_is_never_guessed_for_an_unknown_jurisdiction() -> None:
    assert regulator_venue_display_name("NASDAQ", "United States") is None
    assert regulator_venue_display_name(None, None) is None
    assert regulator_venue_display_name("MI", "Italy") == "eMarket Storage (CONSOB)"


# =========================================================================== #
# Q4 — the incompleteness reason states what is actually missing              #
# =========================================================================== #


class _FinEv:
    def __init__(self, *, backed: bool, open_categories: tuple[str, ...] = ()) -> None:
        self.is_primary_backed = backed
        self.open_categories = open_categories
        self.resolved_categories = ("revenue",)


def test_with_no_primary_evidence_the_original_demand_is_unchanged() -> None:
    for fin_ev in (None, _FinEv(backed=False)):
        clause = _incompleteness_clause(fin_ev)
        assert "primary filings (T1/T2) required" in clause
        assert "Assessment is incomplete" in clause


def test_with_primary_evidence_the_demand_names_what_remains() -> None:
    clause = _incompleteness_clause(
        _FinEv(backed=True, open_categories=("ebitda", "total_debt"))
    )
    assert "primary filings (T1/T2) required" not in clause
    assert "primary filing is ingested" in clause
    assert "ebitda" in clause
    # The warning is NOT reduced.
    assert "Assessment is incomplete" in clause
    assert "before any investment decision" in clause


def test_with_every_category_resolved_identity_confirmation_still_remains() -> None:
    clause = _incompleteness_clause(_FinEv(backed=True, open_categories=()))
    assert "identity/regulatory confirmation" in clause
    assert "Assessment is incomplete" in clause


# =========================================================================== #
# Q5 — source-neutral regulated-event channel                                 #
# =========================================================================== #


def _channels(**kwargs):
    out = build_evidence_channels(
        fundamentals=FundamentalsEvidence(available=False), **kwargs
    )
    return {c["channel"]: c for c in out["channels"]}


def test_a_european_issuer_channel_names_its_own_venue() -> None:
    ch = _channels(
        sec_eligible=False,
        regulator_facts_venue="eMarket Storage (CONSOB)",
        regulator_filings_venue="eMarket Storage (CONSOB)",
    )
    for key in ("regulator_structured_facts", "regulator_filing_events"):
        assert "SEC" not in ch[key]["label"]
        assert "eMarket Storage (CONSOB)" in ch[key]["label"]
        assert ch[key]["venue"] == "eMarket Storage (CONSOB)"


def test_an_sec_issuer_still_says_sec() -> None:
    ch = _channels(sec_eligible=True)
    assert "SEC XBRL" in ch["regulator_structured_facts"]["label"]
    assert "SEC EDGAR" in ch["regulator_filing_events"]["label"]


def test_an_unresolved_jurisdiction_gets_a_source_neutral_label() -> None:
    ch = _channels(sec_eligible=False)
    facts = ch["regulator_structured_facts"]
    filings = ch["regulator_filing_events"]
    assert facts["label"] == "Regulator structured financial facts"
    assert filings["label"] == "Official regulated disclosures / filing events"
    assert facts["venue"] is None and filings["venue"] is None
    assert "SEC" not in facts["label"] and "SEC" not in filings["label"]


# =========================================================================== #
# Q6 — the four invariants, from BOTH sides                                   #
# =========================================================================== #


def _report(**over) -> dict:
    report = {
        "company_identity": {
            "country_domicile": {"value": "Italy"},
            "exchange": {"value": "MI"},
        },
        "financial_snapshot": {
            "type": "financial_snapshot",
            "revenue_current_period": {
                "value": "245.4",
                "period": "H1 2026",
                "source_tier": "T1_primary_filing",
            },
        },
        "regulated_disclosures": {
            "type": "regulated_disclosures",
            "available": True,
            "events": {
                "value": [
                    {
                        "title": "H1 2026 Financial Results",
                        "venue": "eMarket Storage (CONSOB-authorised)",
                    }
                ]
            },
        },
        "research_memo": {
            "primary_evidence_summary": {
                "primary_fact_count": 8,
                "primary_documents": [
                    {
                        "title": "H1 2026 Financial Results",
                        "fact_count": 0,
                        "excerpt_count": 5,
                        "counts_basis": "Counts the CITED-EVIDENCE items…",
                    }
                ],
            }
        },
    }
    report.update(over)
    return report


def _invariants(report: dict) -> set[str]:
    audit = audit_report_consistency(report)
    return {f.invariant for f in audit.findings if f.severity == SEVERITY_SERIOUS}


def test_a_clean_report_raises_none_of_the_four() -> None:
    found = _invariants(_report())
    assert not (
        found
        & {
            CONNECTOR_STATE_CONTRADICTION,
            PRIMARY_FILING_REQUIRED_CONTRADICTION,
            JURISDICTION_TASK_MISMATCH,
            FACT_COUNT_SEMANTICS_MISMATCH,
        }
    ), found


def test_live_events_beside_a_not_live_claim_is_caught() -> None:
    report = _report()
    report["source_quality_review"] = {
        "warnings": ["Italy regulated-disclosure connector scaffolded; …"]
    }
    assert CONNECTOR_STATE_CONTRADICTION in _invariants(report)


def test_a_not_live_claim_with_no_live_events_is_not_flagged() -> None:
    report = _report()
    report["regulated_disclosures"] = {"available": False, "events": {"value": []}}
    report["source_quality_review"] = {
        "warnings": ["Italy regulated-disclosure connector scaffolded; …"]
    }
    assert CONNECTOR_STATE_CONTRADICTION not in _invariants(report)


def test_primary_facts_beside_a_generic_filing_demand_is_caught() -> None:
    report = _report()
    report["risk_analysis"] = {
        "risk_summary_text": {
            "value": "Assessment is incomplete — primary filings (T1/T2) required."
        }
    }
    assert PRIMARY_FILING_REQUIRED_CONTRADICTION in _invariants(report)


def test_the_generic_demand_is_allowed_when_no_primary_fact_exists() -> None:
    report = _report()
    report["financial_snapshot"] = {"type": "financial_snapshot"}
    report["risk_analysis"] = {
        "risk_summary_text": {
            "value": "Assessment is incomplete — primary filings (T1/T2) required."
        }
    }
    assert PRIMARY_FILING_REQUIRED_CONTRADICTION not in _invariants(report)


def test_a_non_us_issuer_sent_to_sec_edgar_is_caught() -> None:
    report = _report()
    report["research_completeness_review"] = {
        "next_research_tasks": {
            "value": ["Cross-check company name and domicile against SEC EDGAR or SEDAR+"]
        }
    }
    assert JURISDICTION_TASK_MISMATCH in _invariants(report)


def test_a_us_issuer_sent_to_sec_edgar_is_not_flagged() -> None:
    report = _report()
    report["company_identity"] = {"country_domicile": {"value": "United States"}}
    report["research_completeness_review"] = {
        "next_research_tasks": {
            "value": ["Cross-check company name and domicile against SEC EDGAR or SEDAR+"]
        }
    }
    assert JURISDICTION_TASK_MISMATCH not in _invariants(report)


def test_explaining_that_sec_does_not_cover_this_issuer_is_not_a_mismatch() -> None:
    """The eligibility GAP is correct and necessary — only a TASK is a mismatch."""
    report = _report()
    report["source_quality_review"] = {
        "warnings": [
            "SEC EDGAR covers US issuers only; this issuer is not SEC-eligible."
        ]
    }
    assert JURISDICTION_TASK_MISMATCH not in _invariants(report)


def test_disagreeing_counts_with_no_stated_basis_are_caught() -> None:
    report = _report()
    del report["research_memo"]["primary_evidence_summary"]["primary_documents"][0][
        "counts_basis"
    ]
    assert FACT_COUNT_SEMANTICS_MISMATCH in _invariants(report)


def test_agreeing_counts_need_no_basis() -> None:
    report = _report()
    summary = report["research_memo"]["primary_evidence_summary"]
    summary["primary_fact_count"] = 0
    del summary["primary_documents"][0]["counts_basis"]
    assert FACT_COUNT_SEMANTICS_MISMATCH not in _invariants(report)


def test_the_four_invariants_are_registered_and_serious() -> None:
    for name in (
        CONNECTOR_STATE_CONTRADICTION,
        PRIMARY_FILING_REQUIRED_CONTRADICTION,
        JURISDICTION_TASK_MISMATCH,
        FACT_COUNT_SEMANTICS_MISMATCH,
    ):
        assert name in ALL_INVARIANTS
