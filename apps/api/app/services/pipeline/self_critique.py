"""Self-critique — V3.20 slice 9.3, deterministic.

The report argues against itself before anyone reads it. Built from what the run
already recorded, never written fresh by a model:

* **Bear case** — the counter-thesis findings and every adverse finding, BY LABEL (a
  finding is stated once, in its own section; this refers to it), and each Red Team
  challenge the responder could not resolve (the Red Team's words, clipped, and scanned:
  a challenge whose text trips the safety gate is withheld and counted).
* **Weakest links** — rubric pillars scored 2 or less or resting mainly on open-web
  sources (when the 9.2 verdict ran), thesis dimensions not established, and blocking
  questions left open.
* **Data-quality warnings** — findings in the decision-critical sections cited only from
  open-web sources, and the count of platform evidence gaps.
* **Disconfirming search** — how many adverse and counter-thesis findings the run holds
  and what the Red Team round did, so "did it look for the other side?" is answerable.

``state`` is ``complete`` only when the Red Team round raised at least one challenge AND
the bear case holds at least one item; otherwise ``incomplete``, and the report is not
publication-ready (spec §3.10: a report without a bear case is not publication-ready).
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

VERSION = "2026-11-16.1"
MAX_BEAR_ITEMS = 10
MAX_WARNINGS = 12
DECISION_SECTIONS: tuple[str, ...] = ("financial_capacity", "competitive_position",
                                      "valuation_context", "operations")
THIN_SOURCE_KINDS = frozenset({"web", "website", "web_search", "web_context", "news"})
COMPLETE, INCOMPLETE = "complete", "incomplete"


def _thin(f: Mapping[str, Any]) -> bool:
    kinds = {str(k) for k in f.get("source_kinds") or () if k}
    return not kinds or kinds <= THIN_SOURCE_KINDS


def _findings(report: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    out = []
    for s in report.get("sections") or ():
        if not isinstance(s, Mapping):
            continue
        for f in s.get("findings") or ():
            if isinstance(f, Mapping) and not f.get("superseded_by_finding_id"):
                out.append((str(s.get("key")), f))
    return out


def self_critique_block(report: Mapping[str, Any], challenges: Mapping[str, Any] | None,
                        verdict: Mapping[str, Any] | None = None) -> dict[str, Any]:
    from app.services import safety_terms

    challenges = challenges or {}
    findings = _findings(report)
    labels = {str(k).lower(): str(v) for k, v in (report.get("finding_labels") or {}).items()}

    bear: list[dict[str, Any]] = []
    for section, f in findings:
        if f.get("domain") == "counter_thesis":
            bear.append({"kind": "counter_thesis_finding", "label": f.get("label"),
                         "section": section})
    for section, f in findings:
        if f.get("direction") == "adverse" and f.get("domain") != "counter_thesis":
            bear.append({"kind": "adverse_finding", "label": f.get("label"),
                         "section": section})
    withheld = 0
    for item in challenges.get("unresolved_items") or ():
        if not isinstance(item, Mapping):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        if safety_terms.scan_text(text):
            withheld += 1
            continue
        bear.append({"kind": "unresolved_challenge",
                     "label": labels.get(str(item.get("finding_id") or "").lower()),
                     "weakness_class": item.get("weakness_class"),
                     "statement": text[:400], "author": "red_team_model"})
    bear_total = len(bear)
    bear = bear[:MAX_BEAR_ITEMS]

    weakest: list[dict[str, Any]] = []
    for p in (verdict or {}).get("pillars") or ():
        if not isinstance(p, Mapping):
            continue
        if p.get("state") == "scored" and isinstance(p.get("score"), int) and p["score"] <= 2:
            weakest.append({"kind": "pillar", "key": p.get("key"),
                            "text": f"{p.get('label')}: scored {p['score']} of 5"})
        elif (p.get("thin_share") or 0) > 0.5:
            weakest.append({"kind": "pillar", "key": p.get("key"),
                            "text": f"{p.get('label')}: rests mainly on open-web sources"})
    for s in report.get("sections") or ():
        if not isinstance(s, Mapping):
            continue
        if s.get("key") == "thesis_fit":
            for d in s.get("dimensions") or ():
                if isinstance(d, Mapping) and d.get("status") == "not_established":
                    weakest.append({"kind": "thesis_dimension", "key": d.get("dimension"),
                                    "text": f"Thesis dimension not established: "
                                            f"{d.get('dimension')}"})
        for q in s.get("open_questions") or ():
            if isinstance(q, Mapping) and q.get("blocking"):
                weakest.append({"kind": "open_question", "key": q.get("question_key"),
                                "text": f"Blocking question left open: {q.get('question_key')}"})

    warnings: list[dict[str, Any]] = [
        {"label": f.get("label"), "section": section,
         "reason": "cited only from open-web sources"}
        for section, f in findings if section in DECISION_SECTIONS and _thin(f)
    ][:MAX_WARNINGS]
    evidence = next((s for s in report.get("sections") or ()
                     if isinstance(s, Mapping) and s.get("key") == "evidence_quality_and_gaps"),
                    {})
    gaps = len(evidence.get("platform_evidence_gaps") or ())

    adverse = sum(1 for _, f in findings if f.get("direction") == "adverse")
    supportive = sum(1 for _, f in findings if f.get("direction") == "supportive")
    counter = sum(1 for _, f in findings if f.get("domain") == "counter_thesis")
    raised = int(challenges.get("challenges") or 0)
    red_team = {k: int(challenges.get(k) or 0) for k in (
        "challenges", "resolved", "partially_resolved", "unresolved", "withdrawn_findings",
        "confidence_lowered")}

    if raised == 0:
        state, reason = INCOMPLETE, "the Red Team round raised no challenge"
    elif bear_total == 0:
        state, reason = INCOMPLETE, "no counter-thesis, adverse or unresolved item is held"
    else:
        state, reason = COMPLETE, None
    return {
        "version": VERSION,
        "state": state,
        "reason": reason,
        "publication_ready": state == COMPLETE,
        "bear_case": bear,
        "bear_case_total": bear_total,
        "challenges_withheld_for_safety": withheld,
        "weakest_links": weakest[:MAX_WARNINGS],
        "data_quality_warnings": warnings,
        "platform_evidence_gaps": gaps,
        "red_team": red_team,
        "disconfirming_search": {
            "adverse_findings": adverse, "supportive_findings": supportive,
            "counter_thesis_findings": counter, "challenges_raised": raised,
            "statement": (f"The run holds {adverse} adverse and {counter} counter-thesis "
                          f"finding(s) against {supportive} supportive; the Red Team raised "
                          f"{raised} challenge(s), {red_team['unresolved']} left unresolved "
                          f"and {red_team['withdrawn_findings']} finding(s) withdrawn."),
        },
        "note": ("Assembled from the run's own adverse evidence and the Red Team round; "
                 "challenge text is the Red Team model's, clipped. It argues against the "
                 "report and is not a judgement of the company."),
    }


__all__ = ["COMPLETE", "INCOMPLETE", "VERSION", "self_critique_block"]
