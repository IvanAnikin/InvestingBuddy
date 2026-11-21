"""V3.20 slice 9.3 — the report's mandatory self-critique and its renderings.

Rules under test: the bear case comes from what the run recorded (findings BY LABEL,
unresolved Red Team challenges in their own words); a challenge that trips the safety
gate is withheld and counted; without a Red Team challenge or any bear item the
critique is incomplete and the report not publication-ready; the block passes the
report gate; the web reads the producer's keys and offers the print and memo views.
"""

from __future__ import annotations

import pathlib

from app.core.config import Settings
from app.services import research_verdict as rv
from app.services.pipeline.self_critique import COMPLETE, INCOMPLETE, self_critique_block
from app.services.pipeline.v3_pipeline import _report_safety_hits

WEB = pathlib.Path(__file__).resolve().parents[2] / "web" / "src"


def _f(label: str, direction: str, domain: str = "x", kinds=("issuer_filing",)) -> dict:  # noqa: ANN001
    return {"label": label, "finding_id": label.lower(), "direction": direction,
            "domain": domain, "source_kinds": list(kinds), "statement": f"{label} text"}


REPORT = {
    "finding_labels": {"f2": "F2"},
    "sections": [
        {"key": "competitive_position", "findings": [_f("F1", "supportive", kinds=("web",))]},
        {"key": "risks_and_counter_thesis", "findings": [
            _f("F2", "adverse", "risks"), _f("F3", "neutral", "counter_thesis")]},
        {"key": "thesis_fit", "findings": [], "dimensions": [
            {"dimension": "grid_capex", "status": "not_established"}]},
        {"key": "financial_capacity", "findings": [], "open_questions": [
            {"question_key": "cash_runway", "blocking": True}]},
        {"key": "evidence_quality_and_gaps", "platform_evidence_gaps": [{"d": 1}, {"d": 2}]},
    ],
}
CHALLENGES = {"challenges": 3, "resolved": 1, "unresolved": 2, "withdrawn_findings": 0,
              "unresolved_items": [
                  {"finding_id": "F2", "weakness_class": "single_source",
                   "text": "The backlog figure rests on one press release."},
                  {"finding_id": "F1", "weakness_class": "stale",
                   "text": "Investors should buy before the price target is hit."}]}


def test_the_bear_case_cites_by_label_and_withholds_unsafe_challenges() -> None:
    block = self_critique_block(REPORT, CHALLENGES)
    assert block["state"] == COMPLETE and block["publication_ready"] is True
    kinds = [(b["kind"], b.get("label")) for b in block["bear_case"]]
    assert kinds == [("counter_thesis_finding", "F3"), ("adverse_finding", "F2"),
                     ("unresolved_challenge", "F2")]
    # A finding is stated once, in its own section: the critique refers to it.
    assert all("statement" not in b for b in block["bear_case"]
               if b["kind"] != "unresolved_challenge")
    assert block["challenges_withheld_for_safety"] == 1
    weakest = {w["kind"] for w in block["weakest_links"]}
    assert weakest == {"thesis_dimension", "open_question"}
    assert block["data_quality_warnings"] == [
        {"label": "F1", "section": "competitive_position",
         "reason": "cited only from open-web sources"}]
    assert block["platform_evidence_gaps"] == 2
    assert "1 adverse" in block["disconfirming_search"]["statement"]


def test_without_a_red_team_round_it_is_incomplete() -> None:
    block = self_critique_block(REPORT, {})
    assert block["state"] == INCOMPLETE and block["publication_ready"] is False
    assert "raised no challenge" in block["reason"]
    empty = self_critique_block({"sections": []}, {"challenges": 2})
    assert empty["state"] == INCOMPLETE and "no counter-thesis" in empty["reason"]


def test_weak_pillars_from_the_verdict_are_weakest_links() -> None:
    verdict = rv.verdict_block({"sections": [
        {"key": "competitive_position", "findings": [_f("A1", "adverse"), _f("A2", "adverse")]}]})
    block = self_critique_block(REPORT, CHALLENGES, verdict)
    assert any(w["kind"] == "pillar" and w["key"] == "moat_competitive"
               for w in block["weakest_links"])


def test_the_block_passes_the_report_gate_and_is_on_by_default() -> None:
    assert Settings().v3_self_critique_enabled is True
    report = {**REPORT, "self_critique": self_critique_block(REPORT, CHALLENGES)}
    assert _report_safety_hits(report) == []


def test_the_web_reads_the_keys_and_offers_print_and_memo() -> None:
    reader = (WEB / "components" / "research" / "selfCritique.ts").read_text()
    for key in ("state", "reason", "publication_ready", "bear_case", "kind", "label",
                "section", "weakness_class", "statement", "bear_case_total",
                "challenges_withheld_for_safety", "weakest_links", "text",
                "data_quality_warnings", "platform_evidence_gaps", "red_team", "challenges",
                "resolved", "unresolved", "withdrawn_findings", "disconfirming_search",
                "note"):
        assert key in reader, key
    assert "readSelfCritiqueBlock(raw.self_critique)" in (
        WEB / "components" / "research" / "v3Research.ts").read_text()
    print_page = (WEB / "app" / "research" / "reports" / "[id]" / "print" / "page.tsx").read_text()
    assert "ResearchMemo" in print_page and 'format === "memo"' in print_page
    assert "window.print()" in (
        WEB / "components" / "research" / "report" / "PrintButton.tsx").read_text()
    memo = (WEB / "components" / "research" / "report" / "ResearchMemo.tsx").read_text()
    assert "VERDICT_DISCLAIMER" in memo and "report.disclaimer" in memo
    assert "@media print" in (WEB / "app" / "globals.css").read_text()
