"""
Phase 32A — Slice 1: lineage / identity / real-mock provenance + deterministic
section preservation.

These tests pin the three Slice-1 changes and the required controls (RC-1..RC-6):

  1. Round-trip: the Phase-9 writer's structured-state envelope, embedded in a
     draft's markdown, is recovered by ``generate_from_report`` so the
     deterministic council sections + real identity + is_mock=false + populated
     financial snapshot survive regeneration (AD-1 / RC-3).
  2. Dark regression: a mock / catalyst-only draft skips the envelope entirely,
     and the adapter's recovery of a legacy (envelope-free) markdown is
     unchanged (dark-safe).
  3. Checklist freshness: after validation flips schema_valid True the PERSISTED
     checklist + workflow_status agree with the header and the stale
     "Schema invalid" note is gone; the safety item comes from the real gate
     (RC-6).
  4. Identity fallback: a legacy report with a DB-resolvable parent recovers
     identity (never "Unknown") while the report-level provenance stays
     "unknown" and is_mock is NOT True (RC-2 / RC-5).
  5. Provenance model: explicit mock suppresses numbers; real / absent(unknown)
     do NOT (AD-2).
  6. RC-1: the serialized envelope passes the safety gate, is secret-free and
     size-bounded, yet a poisoned council summary still flips safety_valid False
     (the envelope is never exempt).
  7. Live-path regression: ``generate_from_workflow_state`` with a real snapshot
     keeps is_mock=false + identity + sections (the shared assembler/completer/
     checklist edits must not regress the good path).

All tests run OFFLINE (mock AsyncSession, LLM council disabled) — no network,
no credentials, no real DB.
"""

from __future__ import annotations

import json
import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.report import Report
from app.services import final_report_generator as frg
from app.services import safety_terms
from app.services.data_provenance import (
    derive_data_provenance,
    provenance_to_is_mock,
)
from app.services.final_report_generator import (
    FinalReportGeneratorService,
    _build_company_identity,
    _extract_workflow_state_from_report,
    _resolve_company_record_from_lineage,
)
from app.services.real_asset_report_completer import build_schema_complete_report
from app.services.sources.redaction import url_has_secret
from app.workflows.company_analysis import (
    _ENVELOPE_LIST_CAP,
    _ENVELOPE_STR_CAP,
    _ENVELOPE_TOTAL_CHAR_CAP,
    build_analysis_state_envelope,
)

# asyncio_mode = "auto" (pyproject.toml) — async tests need no marker.


# ---------------------------------------------------------------------------
# Fixtures / factories
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock(spec=AsyncSession)


def _aapl_snapshot() -> dict[str, Any]:
    return {
        "is_mock": False,
        "source_tier": "T2_regulator_or_gov",
        "retrieved_at": "2026-07-31T00:00:00Z",
        "company_identity": {
            "legal_name": "Apple Inc.",
            "ticker": "AAPL",
            "exchange": "NASDAQ",
            "country_domicile": "US",
        },
        "profile": {"sector": "Technology", "reporting_currency": "USD"},
        "price_history_summary": {
            "available": True,
            "latest_close": 195.3,
            "currency": "USD",
            "date_range": {"end": "2026-07-10"},
        },
        "provider_metadata": {"provider_name": "free_real", "is_mock": False},
    }


def _fundamentals() -> dict[str, Any]:
    return {
        "highlights": {
            "market_capitalization": 3010000.0,
            "revenue_ttm": 383000.0,
            "ebitda": None,
            "pe_ratio": 31.2,
        }
    }


def _council_summaries() -> dict[str, Any]:
    return {
        "bull_case_summary": {
            "positive_thesis_points": ["Durable ecosystem lock-in."],
            "confidence_level": "moderate",
        },
        "bear_case_summary": {
            "negative_thesis_points": ["Hardware cycle sensitivity."],
        },
        "risk_summary": {"business_risks": ["Product concentration."]},
        "valuation_guard_summary": {
            "valuation_ready": False,
            "blockers": ["No verified DCF inputs."],
        },
        "committee_chair_summary": {
            "provisional_internal_status": "ready_for_deeper_analysis",
        },
    }


def _real_state(**over: Any) -> dict[str, Any]:
    st: dict[str, Any] = {
        "is_mock": False,
        "company_snapshot": _aapl_snapshot(),
        "financial_data_summary": {
            "available_count": 3,
            "missing_count": 1,
            "available_fields": ["revenue", "market_cap"],
            "missing_fields": ["ebitda"],
            "warnings": [],
        },
        "source_quality_summary": {"overall_source_quality": "moderate"},
        "research_completeness_summary": {"completeness_score": 0.6},
        "upgraded_citation_validation": {"status": "ok"},
        "fundamentals_data": _fundamentals(),
        "fundamentals_available": True,
        "schema_validation_result": {"is_valid": False, "errors": [], "warnings": []},
        "source_tier": "T2_regulator_or_gov",
        "catalyst_discovery": None,
    }
    st.update(_council_summaries())
    st.update(over)
    return st


def _json_block(payload: dict[str, Any]) -> str:
    return "```json\n" + json.dumps(payload, default=str) + "\n```\n"


def _envelope_markdown(state: dict[str, Any]) -> str:
    envelope = build_analysis_state_envelope(state)
    return "# Draft\n\n" + _json_block(envelope)


def _report_with_markdown(
    markdown: str,
    *,
    created_by_agent_run_id: uuid.UUID | None = None,
    scorecard_id: uuid.UUID | None = None,
) -> Report:
    return Report(
        id=uuid.uuid4(),
        title="Legacy draft",
        slug=f"legacy-{uuid.uuid4().hex[:8]}",
        report_type="company_deep_dive",
        status="draft",
        content_markdown=markdown,
        review_status="draft",
        created_by_agent_run_id=created_by_agent_run_id,
        scorecard_id=scorecard_id,
    )


def _saved_report(mock_db: AsyncMock) -> Report:
    assert mock_db.add.called, "expected a final report to be saved"
    return mock_db.add.call_args_list[-1][0][0]


def _saved_report_content(saved: Report) -> dict[str, Any]:
    """Parse the report_content JSON block out of the saved report markdown."""
    md = saved.content_markdown or ""
    blocks = re.findall(r"```json\s*(.*?)\s*```", md, re.DOTALL)
    assert blocks, "saved report has no JSON block"
    return json.loads(blocks[-1])


async def _run_from_report(
    mock_db: AsyncMock,
    source_report: Report,
    *,
    scorecard: Any = None,
    resolver: dict[str, Any] | None = None,
) -> Any:
    """Drive ``generate_from_report`` with mocked DB loaders (council disabled)."""
    patches = [
        patch.object(frg, "_load_report_by_id", AsyncMock(return_value=source_report)),
        patch.object(frg, "_load_scorecard_for_report", AsyncMock(return_value=scorecard)),
        patch.object(frg, "_load_scorecard_by_id", AsyncMock(return_value=scorecard)),
        patch.object(frg, "_load_citations_for_report", AsyncMock(return_value=[])),
        patch.object(frg, "_load_sources_for_citations", AsyncMock(return_value=[])),
        patch.object(
            frg,
            "_resolve_company_record_from_lineage",
            AsyncMock(return_value=resolver),
        ),
    ]
    for p in patches:
        p.start()
    try:
        return await FinalReportGeneratorService().generate_from_report(
            mock_db, source_report.id
        )
    finally:
        for p in patches:
            p.stop()


# ---------------------------------------------------------------------------
# 1. Round-trip: envelope → adapter restores sections + identity + snapshot
# ---------------------------------------------------------------------------


def test_envelope_roundtrips_all_adapter_keys() -> None:
    state = _real_state()
    md = _envelope_markdown(state)
    parsed = _extract_workflow_state_from_report(_report_with_markdown(md))

    assert parsed["company_snapshot"]["company_identity"]["legal_name"] == "Apple Inc."
    for key in (
        "bull_case_summary",
        "bear_case_summary",
        "risk_summary",
        "valuation_guard_summary",
        "committee_chair_summary",
        "financial_data_summary",
        "fundamentals_data",
    ):
        assert parsed[key] is not None, key
    assert parsed["source_tier"] == "T2_regulator_or_gov"
    # catalyst_discovery is NOT carried by the envelope (RC-3): the adapter reads
    # it as None here (its own block is its sole source).
    assert parsed["catalyst_discovery"] is None


async def test_generate_from_report_restores_sections_identity_and_snapshot(
    mock_db,
) -> None:
    state = _real_state()
    source_report = _report_with_markdown(_envelope_markdown(state))
    resp = await _run_from_report(mock_db, source_report)

    content = _saved_report_content(_saved_report(mock_db))

    # Deterministic council sections survive the round-trip (available:True).
    for section in ("bull_case", "bear_case", "risk_analysis"):
        assert content[section]["available"] is True, section

    # Real identity + provenance.
    identity = content["company_identity"]
    assert identity["legal_name"]["value"] == "Apple Inc."
    assert identity["is_mock"] is False
    assert identity["data_provenance"] == "real"

    # Populated financial snapshot (real, sourced price + fundamentals).
    fin = content["financial_snapshot"]
    assert fin["is_mock"] is False
    assert fin["data_provenance"] == "real"
    assert fin["latest_close"]["value"] == 195.3
    assert fin["market_cap_usd_m"]["value"] == 3010000.0

    # Report-level provenance is auditable + real.
    assert resp.schema_valid is True
    assert _saved_report(mock_db).source_summary_json["data_provenance"] == "real"


# ---------------------------------------------------------------------------
# 2. Dark regression: mock / catalyst-only → envelope skipped, adapter unchanged
# ---------------------------------------------------------------------------


def test_envelope_skipped_for_mock_run() -> None:
    mock_state = _real_state(is_mock=True)
    mock_state["company_snapshot"]["is_mock"] = True
    assert build_analysis_state_envelope(mock_state) == {}


def test_envelope_skipped_for_unknown_provenance_run() -> None:
    unknown_state = _real_state()
    del unknown_state["is_mock"]  # no explicit signal
    assert build_analysis_state_envelope(unknown_state) == {}


def test_adapter_recovery_of_catalyst_only_markdown_unchanged() -> None:
    # A legacy (pre-envelope) draft that carries ONLY the catalyst block: the
    # adapter recovers catalyst_discovery and leaves every other key None — the
    # exact pre-Phase-32A behaviour (dark-safe for old reports).
    md = "# Legacy\n\n" + _json_block({"catalyst_discovery": {"recent_events": []}})
    parsed = _extract_workflow_state_from_report(_report_with_markdown(md))
    assert parsed["catalyst_discovery"] == {"recent_events": []}
    assert parsed["company_snapshot"] is None
    assert parsed["bull_case_summary"] is None
    assert parsed["source_tier"] is None


# ---------------------------------------------------------------------------
# 3. Checklist freshness / no-contradiction (RC-6)
# ---------------------------------------------------------------------------


async def test_persisted_checklist_and_status_agree_after_validation(mock_db) -> None:
    source_report = _report_with_markdown(_envelope_markdown(_real_state()))
    resp = await _run_from_report(mock_db, source_report)
    content = _saved_report_content(_saved_report(mock_db))

    checklist = content["human_review_checklist"]
    safety_item = next(c for c in checklist if "Safety gate" in c["item"])
    schema_item = next(c for c in checklist if "Schema validation" in c["item"])

    # Schema item reflects the FINAL (post-completion) schema_valid — no stale note.
    assert resp.schema_valid is True
    assert schema_item["completed"] is True
    assert schema_item["note"] is None
    # workflow_status twin matches the header (no contradiction).
    assert content["workflow_status"]["schema_valid"] == resp.schema_valid
    # Safety item is sourced from the real gate, never the assembly stale-True.
    assert safety_item["completed"] == resp.safety_valid
    # Recompute never flips these invariants.
    assert resp.publication_ready is False
    assert resp.human_review_required is True


async def test_no_stale_schema_invalid_note_in_persisted_content(mock_db) -> None:
    source_report = _report_with_markdown(_envelope_markdown(_real_state()))
    await _run_from_report(mock_db, source_report)
    saved = _saved_report(mock_db)
    assert "Schema invalid" not in (saved.content_markdown or "")


# ---------------------------------------------------------------------------
# 4. Identity fallback (RC-5): DB lineage → identity resolved, provenance unknown
# ---------------------------------------------------------------------------


def _discovery_candidate(**over: Any):
    from app.models.discovery import DiscoveryCandidate

    return DiscoveryCandidate(
        id=over.get("id", uuid.uuid4()),
        discovery_run_id=uuid.uuid4(),
        ticker=over.get("ticker", "AAPL"),
        exchange=over.get("exchange", "NASDAQ"),
        company_name=over.get("company_name", "Apple Inc."),
        legal_name=over.get("legal_name", "Apple Inc."),
        sector="Technology",
        country="US",
        agent_run_id=over.get("agent_run_id"),
        analysis_report_id=over.get("analysis_report_id"),
    )


async def test_resolver_recovers_identity_via_agent_run_candidate() -> None:
    run_id = uuid.uuid4()
    cand = _discovery_candidate(agent_run_id=run_id)
    source_report = _report_with_markdown("# legacy", created_by_agent_run_id=run_id)

    result = MagicMock()
    result.scalar_one_or_none.return_value = cand
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=result)

    record = await _resolve_company_record_from_lineage(db, source_report, None)
    assert record is not None
    assert record["name"] == "Apple Inc."
    assert record["ticker"] == "AAPL"


async def test_resolver_returns_none_when_no_lineage() -> None:
    source_report = _report_with_markdown("# legacy")  # no agent run
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = AsyncMock(spec=AsyncSession)
    db.execute = AsyncMock(return_value=result)
    record = await _resolve_company_record_from_lineage(db, source_report, None)
    assert record is None


async def test_legacy_report_with_resolvable_parent_never_unknown(mock_db) -> None:
    # A legacy (envelope-free) draft: state carries no snapshot, so the DB
    # fallback resolves a KNOWN parent. Identity is real (never "Unknown"), but
    # the report-level financial provenance stays "unknown" and is_mock is NOT
    # True (a missing snapshot must not relabel a real company as mock).
    source_report = _report_with_markdown("# legacy draft (no envelope)")
    resolver = {"name": "Apple Inc.", "ticker": "AAPL", "exchange": "NASDAQ"}
    resp = await _run_from_report(mock_db, source_report, resolver=resolver)

    content = _saved_report_content(_saved_report(mock_db))
    identity = content["company_identity"]
    assert identity["legal_name"]["value"] == "Apple Inc."
    assert identity["is_mock"] is not True  # False (real identity), never True
    saved = _saved_report(mock_db)
    assert saved.source_summary_json["data_provenance"] == "unknown"
    # The financial snapshot provenance is honestly unknown (no snapshot round-tripped).
    assert content["financial_snapshot"]["data_provenance"] == "unknown"
    assert resp.human_review_required is True


def test_identity_company_record_branch_is_real_not_mock() -> None:
    identity = _build_company_identity(None, {"name": "Apple Inc.", "ticker": "AAPL"})
    assert identity["legal_name"]["value"] == "Apple Inc."
    assert identity["data_provenance"] == "real"
    assert identity["is_mock"] is False


def test_identity_no_parent_is_unknown_not_mock() -> None:
    identity = _build_company_identity(None, None)
    assert identity["data_provenance"] == "unknown"
    assert identity["is_mock"] is None  # honoured — never coerced to True


# ---------------------------------------------------------------------------
# 5. Provenance model (AD-2) + completer number-suppression gating
# ---------------------------------------------------------------------------


def test_provenance_derivation_from_explicit_signals_only() -> None:
    assert derive_data_provenance(True) == "mock"
    assert derive_data_provenance(False) == "real"
    assert derive_data_provenance(None) == "unknown"  # absence ≠ mock
    assert derive_data_provenance(None, has_real_evidence=True) == "real"

    assert provenance_to_is_mock("mock") is True
    assert provenance_to_is_mock("real") is False
    assert provenance_to_is_mock("mixed") is False
    assert provenance_to_is_mock("unknown") is None  # honoured, not True


def _admin_with_number(**fin_over: Any) -> dict[str, Any]:
    fin = {"source_tier": "T5_api_aggregator", "market_cap_usd_m": {"value": 3010000.0}}
    fin.update(fin_over)
    return {
        "executive_summary": {"company_name": "Apple Inc.", "ticker": "AAPL"},
        "company_identity": {"legal_name": {"value": "Apple Inc."}},
        "financial_snapshot": fin,
    }


def test_completer_explicit_mock_suppresses_numbers() -> None:
    comp = build_schema_complete_report(
        _admin_with_number(is_mock=True, data_provenance="mock"), report_id="m-1"
    )
    assert comp.report["snapshot_financials"]["market_cap_usd_m"]["value"] is None


def test_completer_real_keeps_numbers() -> None:
    comp = build_schema_complete_report(
        _admin_with_number(is_mock=False, data_provenance="real"), report_id="r-1"
    )
    assert comp.report["snapshot_financials"]["market_cap_usd_m"]["value"] == 3010000.0


def test_completer_unknown_does_not_suppress_numbers() -> None:
    # No is_mock / no data_provenance ⇒ unknown ⇒ numbers that carry their own
    # source are NOT erased (they are surfaced + flagged for human review).
    comp = build_schema_complete_report(_admin_with_number(), report_id="u-1")
    assert comp.report["snapshot_financials"]["market_cap_usd_m"]["value"] == 3010000.0


# ---------------------------------------------------------------------------
# 6. RC-1 — envelope is safe, secret-free, bounded; poison still flips safety
# ---------------------------------------------------------------------------


def _iter_strings(value: Any):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for v in value.values():
            yield from _iter_strings(v)
    elif isinstance(value, list):
        for v in value:
            yield from _iter_strings(v)


def test_envelope_is_safe_secret_free_and_bounded() -> None:
    state = _real_state()
    # Plant a credential in a URL and a sensitive-keyed value.
    state["company_snapshot"]["source_url"] = (
        "https://investor.apple.com/data?api_token=SUPERSECRET123&x=1"
    )
    state["company_snapshot"]["api_token"] = "SUPERSECRET123"
    # And an over-long string + over-long list, to prove bounding.
    state["risk_summary"]["business_risks"] = ["r"] * 100
    state["source_quality_summary"]["blob"] = "x" * (_ENVELOPE_STR_CAP + 5000)

    envelope = build_analysis_state_envelope(state)
    serialized = json.dumps(envelope, default=str)

    # Safety gate: no forbidden recommendation/valuation language.
    assert safety_terms.scan_value(envelope) == []
    # Secret-free: no residual token value or credential query param anywhere.
    assert "SUPERSECRET123" not in serialized
    assert not any(url_has_secret(s) for s in _iter_strings(envelope))
    # Bounded: list truncated to the cap, string truncated, whole block capped.
    assert len(envelope["risk_summary"]["business_risks"]) == _ENVELOPE_LIST_CAP
    assert len(envelope["source_quality_summary"]["blob"]) <= _ENVELOPE_STR_CAP + 32
    assert len(serialized) <= _ENVELOPE_TOTAL_CHAR_CAP


async def test_poisoned_council_summary_flips_safety_valid(mock_db) -> None:
    # A council summary carrying a forbidden phrase round-trips through the
    # envelope into report_content["bull_case"] and is caught by the FINAL
    # safety gate — the envelope is NOT exempt.
    state = _real_state()
    state["bull_case_summary"]["positive_thesis_points"] = [
        "Our price target implies material upside."
    ]
    source_report = _report_with_markdown(_envelope_markdown(state))
    resp = await _run_from_report(mock_db, source_report)
    assert resp.safety_valid is False


# ---------------------------------------------------------------------------
# 7. Live-path regression (generate_from_workflow_state)
# ---------------------------------------------------------------------------


async def test_live_path_real_snapshot_unchanged(mock_db) -> None:
    resp = await FinalReportGeneratorService().generate_from_workflow_state(
        mock_db,
        state=_real_state(),
        company_record={"name": "Apple Inc.", "ticker": "AAPL", "exchange": "NASDAQ"},
    )
    content = _saved_report_content(_saved_report(mock_db))
    assert content["company_identity"]["legal_name"]["value"] == "Apple Inc."
    assert content["company_identity"]["is_mock"] is False
    assert content["financial_snapshot"]["is_mock"] is False
    for section in ("bull_case", "bear_case", "risk_analysis"):
        assert content[section]["available"] is True, section
    assert resp.human_review_required is True
    assert resp.publication_ready is False
    assert _saved_report(mock_db).source_summary_json["data_provenance"] == "real"


# ---------------------------------------------------------------------------
# 8. Slice-1 hotfix: Phase-31 memo's embedded checklist snapshot stays fresh
#    (RC-6 refreshes the memo's snapshot too, so it can never contradict the
#     header). The memo is built + safety-scanned BEFORE validation; the fix
#     re-presents ONLY the not-completed sub-field from the FINAL authoritative
#     checklist — a strict, safety-clean subset.
# ---------------------------------------------------------------------------


def test_memo_checklist_snapshot_excludes_completed_schema_item() -> None:
    """Direct unit: a COMPLETED schema item is not listed as not-completed and
    no stale 'Schema invalid' note survives in the memo snapshot."""
    checklist = [
        {"item": "Safety gate passed", "required": True, "completed": True, "note": None},
        {"item": "Schema validation", "required": True, "completed": True, "note": None},
        {
            "item": "Human analyst review",
            "required": True,
            "completed": False,
            "note": "Pending analyst sign-off.",
        },
    ]
    snap = frg._memo_human_review_checklist_snapshot(checklist)

    assert snap["total_items"] == 3
    assert snap["not_completed_count"] == 1
    items = snap["not_completed_items"]["value"]
    assert all("Schema validation" not in (i["item"] or "") for i in items)
    assert all("Schema invalid" not in (i.get("note") or "") for i in items)
    # References the authoritative checklist; does not recompute a second one.
    assert "does not" in snap["reference"]
    assert snap["human_review_required"] is True


def test_memo_checklist_snapshot_preserves_incomplete_item_note() -> None:
    """Extraction is behaviour-identical: an INCOMPLETE item keeps its note
    (this is what the memo shows at initial build before RC-6)."""
    checklist = [
        {
            "item": "Schema validation",
            "required": True,
            "completed": False,
            "note": "Schema invalid — review validation errors and add missing fields",
        },
    ]
    snap = frg._memo_human_review_checklist_snapshot(checklist)
    assert snap["not_completed_count"] == 1
    assert snap["not_completed_items"]["value"][0]["note"].startswith("Schema invalid")


async def test_memo_embedded_checklist_refreshed_after_validation(
    mock_db, monkeypatch
) -> None:
    """With the memo enabled and schema_valid flipping True at RC-6, the memo's
    EMBEDDED checklist snapshot agrees with the authoritative checklist — no
    stale 'Schema invalid', no contradiction with the header."""
    monkeypatch.setattr(frg.settings, "source_research_memo_enabled", True)
    source_report = _report_with_markdown(_envelope_markdown(_real_state()))
    resp = await _run_from_report(mock_db, source_report)
    content = _saved_report_content(_saved_report(mock_db))

    memo = content.get("research_memo")
    assert memo is not None, "memo-on report must carry a research_memo block"
    memo_checklist = memo["human_review_checklist"]

    # Authoritative (post-RC-6) checklist.
    authoritative = content["human_review_checklist"]
    auth_not_completed = [c for c in authoritative if not c.get("completed")]

    # Embedded snapshot count agrees with the authoritative checklist.
    assert memo_checklist["not_completed_count"] == len(auth_not_completed)
    # The completed schema item is not re-listed as not-completed, and no stale
    # 'Schema invalid' note remains anywhere in the memo snapshot.
    for item in memo_checklist["not_completed_items"]["value"]:
        assert "Schema validation" not in (item.get("item") or "")
        assert "Schema invalid" not in (item.get("note") or "")

    # Regression guard: RC-6 still holds on the authoritative checklist.
    schema_item = next(c for c in authoritative if "Schema validation" in c["item"])
    assert resp.schema_valid is True
    assert schema_item["completed"] is True
    assert schema_item["note"] is None

    # The refresh touches ONLY the checklist sub-field — invariants unchanged.
    assert memo["human_review_required"] is True
    assert resp.publication_ready is False
    assert resp.human_review_required is True


async def test_memo_absent_and_rc6_unchanged_when_flag_off(
    mock_db, monkeypatch
) -> None:
    """Dark-safe: with the memo flag OFF no research_memo key is added and the
    refresh is a no-op — RC-6 behaviour is unchanged."""
    monkeypatch.setattr(frg.settings, "source_research_memo_enabled", False)
    source_report = _report_with_markdown(_envelope_markdown(_real_state()))
    resp = await _run_from_report(mock_db, source_report)
    content = _saved_report_content(_saved_report(mock_db))

    assert "research_memo" not in content
    schema_item = next(
        c for c in content["human_review_checklist"] if "Schema validation" in c["item"]
    )
    assert resp.schema_valid is True
    assert schema_item["completed"] is True
