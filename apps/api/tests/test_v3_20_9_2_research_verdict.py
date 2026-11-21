"""V3.20 slice 9.2 — the eight-pillar rubric and the computed verdict (ADR-098).

The rules under test: the label is computed, never written; a pillar without cited
directional evidence is not scored (never a silent 3); thin evidence and a missing
valuation range forbid SHORTLIST; the label passes the gate only at its own path; the
record and the portfolio review carry it; the web reader reads the producer's keys.
"""

from __future__ import annotations

import copy
import pathlib
import uuid
from datetime import date
from types import SimpleNamespace

from app.core.config import Settings
from app.services import portfolio_review as review_mod
from app.services import research_verdict as rv
from app.services import safety_terms
from app.services.pipeline.v3_pipeline import _report_safety_hits
from app.services.research_records import build_record

VALUED = {"valuation_range": {"state": "computed"}, "relative_position": "undervalued"}


def _f(label: str, direction: str = "supportive", kinds: tuple[str, ...] = ("issuer_filing",),
       domain: str = "x") -> dict:
    return {"label": label, "finding_id": label.lower(), "direction": direction,
            "source_kinds": list(kinds), "domain": domain, "statement": "s"}


def _report(**overrides: list[dict]) -> dict:
    sections = {
        "operations": [_f("F1"), _f("F2")],
        "financial_capacity": [_f("F3"), _f("F4")],
        "competitive_position": [_f("F5"), _f("F6")],
        "industry_and_market": [_f("F7"), _f("F8")],
        "growth_and_catalysts": [_f("F9"), _f("F10")],
        "risks_and_counter_thesis": [_f("F11", domain="governance"),
                                     _f("F12", domain="governance"),
                                     _f("F13", "adverse", domain="risks")],
        "valuation_context": [],
    }
    sections.update(overrides)
    out = [{"key": k, "findings": v} for k, v in sections.items()]
    next(s for s in out if s["key"] == "valuation_context")["obscurity"] = {"score": 70}
    return {"version": 9, "sections": out}


def test_a_well_evidenced_report_with_a_range_is_shortlisted() -> None:
    block = rv.verdict_block(_report(), valuation=VALUED)
    assert block["recommendation"] == rv.SHORTLIST
    assert block["caps"] == [] and block["capped"] is False
    assert block["confidence"]["band"] == "high"
    scores = {p["key"]: p["score"] for p in block["pillars"]}
    assert scores["underresearched_edge"] == 4 and scores["valuation_gap"] == 5
    # Governance reads only governance-domain findings: the adverse risk is not counted.
    gov = next(p for p in block["pillars"] if p["key"] == "management_governance")
    assert [e["label"] for e in gov["evidence"]] == ["F11", "F12"]
    assert rv.verdict_block(copy.deepcopy(_report()), valuation=VALUED) == block


def test_without_a_valuation_range_it_cannot_be_shortlisted() -> None:
    block = rv.verdict_block(_report())
    assert block["recommendation"] == rv.WATCHLIST and block["capped"] is True
    assert "valuation_insufficient" in {c["key"] for c in block["caps"]}
    assert "valuation_gap_unscored" in {c["key"] for c in block["caps"]}
    assert "A computed valuation range from cited inputs" in block["watchlist_triggers"]


def test_missing_evidence_is_not_scored_and_lowers_confidence() -> None:
    sparse = _report(operations=[], financial_capacity=[], industry_and_market=[])
    block = rv.verdict_block(sparse, valuation=VALUED)
    unscored = {p["key"] for p in block["pillars"] if p["state"] == rv.UNSCORED}
    assert {"asset_quality", "balance_sheet_resilience"} <= unscored
    assert all(p["score"] is None for p in block["pillars"] if p["key"] in unscored)
    assert block["confidence"]["coverage"] < 1
    assert block["recommendation"] == rv.WATCHLIST  # a key pillar is unscored
    assert any("Financial quality" in m for m in block["missing_information"])


def test_thin_key_pillar_and_adverse_evidence() -> None:
    web = _report(competitive_position=[_f("F5", kinds=("web",)), _f("F6", kinds=("web",))])
    assert "moat_competitive_thin" in {c["key"] for c in
                                       rv.verdict_block(web, valuation=VALUED)["caps"]}
    bad = {k: [_f(f"A{i}{j}", "adverse") for j in range(2)] for i, k in enumerate(
        ("operations", "financial_capacity", "competitive_position", "industry_and_market",
         "growth_and_catalysts"))}
    block = rv.verdict_block(_report(**bad), valuation={
        "valuation_range": {"state": "computed"}, "relative_position": "overvalued"})
    assert block["recommendation"] == rv.PASS
    assert "pillar_at_one" in {c["key"] for c in block["caps"]}
    assert rv.verdict_block({"sections": []})["recommendation"] == rv.PASS


def test_the_label_passes_the_gate_only_at_its_own_path() -> None:
    block = rv.verdict_block(_report(), valuation=VALUED)
    assert safety_terms.scan_value(block, path="verdict",
                                   allowances=safety_terms.V320_REPORT_ALLOWANCES) == []
    # Without the allowance, the only hit is the label itself: no other string names it.
    hits = safety_terms.scan_value(block, path="verdict")
    assert {h.path for h in hits} == {"verdict.recommendation"}

    report = {**_report(), "verdict": block}
    assert _report_safety_hits(report) == []
    leaked = copy.deepcopy(report)
    leaked["sections"][0]["lead"] = {"text": "We rate this SHORTLIST."}
    assert _report_safety_hits(leaked)
    free_text = copy.deepcopy(report)
    free_text["verdict"]["rationale"] = "SHORTLIST on the evidence."
    assert _report_safety_hits(free_text)


def test_the_record_stores_the_verdict_and_it_is_off_by_default() -> None:
    assert Settings().v3_research_verdict_enabled is False
    company = SimpleNamespace(ticker="POWL", exchange="NASDAQ", sector=None, industry=None)
    report = {**_report(), "verdict": rv.verdict_block(_report())}
    values = build_record(report=report, company=company, size_fit=None, as_of=date.today())
    assert values["rubric_json"]["recommendation"] == rv.WATCHLIST
    assert build_record(report=_report(), company=company, size_fit=None,
                        as_of=date.today())["rubric_json"] is None


def _rec(as_of: date, verdict: dict | None) -> SimpleNamespace:
    return SimpleNamespace(id=uuid.uuid4(), as_of=as_of, record_json={"blocks": {}},
                           ev_to_ebit=None, research_run_id=None, rubric_json=verdict)


def test_the_portfolio_review_shows_both_verdicts_and_a_lower_one_weakens() -> None:
    entry = _rec(date(2026, 1, 1), rv.verdict_block(_report(), valuation=VALUED))
    current = _rec(date(2026, 11, 1), rv.verdict_block(_report()))
    assert review_mod.verdict_of(entry)["recommendation"] == rv.SHORTLIST
    assert review_mod.verdict_of(_rec(date(2026, 1, 1), None))["state"] == "not_available"
    weak, _ = review_mod.weakening_items(entry, current, [], company_id=uuid.uuid4())
    assert [w["kind"] for w in weak] == ["verdict_change"]
    assert "lower" in weak[0]["summary"]

    review = {"portfolio_id": "p", "name": "Buy list", "holdings": [
        {"ticker": "POWL", "entry_verdict": review_mod.verdict_of(entry),
         "current_verdict": review_mod.verdict_of(current), "weakening": weak}]}
    gated = review_mod.gate_review(copy.deepcopy(review))
    assert gated["holdings"][0]["entry_verdict"]["recommendation"] == rv.SHORTLIST
    assert "safety_hits" not in gated  # the user's own portfolio name is not output
    review["holdings"][0]["weakening"].append({"summary": "We would trim the position"})
    review["holdings"][0]["note"] = "price target 40"
    withheld = review_mod.gate_review(review)
    assert withheld["holdings"][0]["entry_verdict"]["state"] == "withheld"


def test_the_web_reader_reads_the_producer_keys() -> None:
    root = pathlib.Path(__file__).resolve().parents[2] / "web" / "src"
    reader = (root / "components" / "research" / "researchVerdict.ts").read_text()
    for key in ("recommendation", "meaning", "composite_score", "capped", "rationale", "note",
                "confidence", "band", "coverage", "pillars", "state", "score", "thin_share",
                "rule", "evidence", "direction", "signal", "delta", "observed", "source_url",
                "quality", "caps", "missing_information", "watchlist_triggers"):
        assert key in reader, key
    assert "readResearchVerdictBlock(raw.verdict)" in (
        root / "components" / "research" / "v3Research.ts").read_text()
    review_client = (root / "lib" / "portfolioReviewApi.ts").read_text()
    for key in ("entry_verdict", "current_verdict", "record_as_of", "confidence_band"):
        assert key in review_client, key
