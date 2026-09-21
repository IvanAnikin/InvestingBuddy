"""The professional research report, assembled from the ledger — V3.18.8.

WHY THIS EXISTS
===============
The SCCO report a reader saw was the V1 council's: eight specialists handed the same
twelve financial facts, eight financial commentaries, and a closing list of "the company
does not disclose …" that was, in every case, "the platform did not acquire …". The V3
research that ran afterwards was attached as metadata and read by nobody.

This module turns the V3 ledger into the report. Thirteen sections, in the order an
analyst reads, each built from the findings the question graph assigned to it — so a
finding appears exactly ONCE, in the section its domain owns, and every section says
which questions it could not settle and why.

WHAT IS DETERMINISTIC, AND WHAT A MODEL MAY TOUCH
=================================================
Everything but two kinds of sentence is assembled deterministically: which findings go
where, the tables, the thesis-fit status, the size fit, the source diversity, the split
between platform evidence gaps and business risks.

An *editor* model may write the executive synthesis and a one-sentence lead per section.
It sees only the findings, cites them by label, and every sentence it writes is checked
before it is kept:

* it cites at least one finding, and only findings that exist;
* every figure in it appears in a finding it cites (no new numbers);
* it passes the shared safety scanner (no rating labels, targets or fair values);
* it passes the knowledge-state guard (no "the company does not disclose" from absence).

A sentence that fails is dropped, not repaired. If too little survives, the deterministic
synthesis stands. A model that is absent, slow or wrong costs prose, never the report.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from app.services.director.domains import (
    REPORT_SECTION_ORDER,
    label_for,
    report_section_for,
)

REPORT_VERSION = 1

SECTION_TITLES: dict[str, str] = {
    "executive_synthesis": "Executive synthesis",
    "thesis_fit": "Fit with the originating thesis",
    "business_model": "Business model",
    "industry_and_market": "Industry and market",
    "operations": "Products, assets and operations",
    "competitive_position": "Competitive position",
    "growth_and_catalysts": "Growth pipeline and catalysts",
    "financial_capacity": "Financial capacity",
    "valuation_context": "Valuation context",
    "risks_and_counter_thesis": "Risks and counter-thesis",
    "sensitivities": "Sensitivities",
    "what_would_change_the_thesis": "What would change the thesis",
    "evidence_quality_and_gaps": "Evidence quality and unresolved gaps",
}
assert tuple(SECTION_TITLES) == REPORT_SECTION_ORDER

#: Sections no single domain owns: they are built from the others.
DERIVED_SECTIONS: frozenset[str] = frozenset(
    {"executive_synthesis", "what_would_change_the_thesis", "evidence_quality_and_gaps"}
)

STATUS_EVIDENCED = "evidenced"
STATUS_PARTIAL = "partially_evidenced"
STATUS_NOT_ESTABLISHED = "not_established"

MAX_FINDINGS_PER_SECTION = 14
MAX_EDITOR_FINDINGS = 60
EDITOR_MAX_TOKENS = 1_800
EDITOR_TIMEOUT = 90.0
MIN_SYNTHESIS_SENTENCES = 2

DISCLAIMER = (
    "Internal research draft. Not investment advice and not a recommendation. Every "
    "statement rests on the cited findings; a gap marked 'not acquired by the platform' "
    "is a limit of this research, not a fact about the company. Human review required."
)


# ── Inputs ──────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class QuestionView:
    key: str
    text: str
    domain: str | None
    report_section: str | None = None
    contract_status: str | None = None
    unresolved_reason: str | None = None
    missing: tuple[str, ...] = ()
    why_it_matters: str | None = None
    blocking: bool = False


@dataclass(frozen=True)
class FindingView:
    finding_id: str
    statement: str
    domain: str | None
    question_key: str | None
    evidence_ids: tuple[str, ...] = ()
    calculation_ids: tuple[str, ...] = ()
    source_kinds: tuple[str, ...] = ()
    confidence: str | None = None
    direction: str | None = None
    period_key: str | None = None
    references: tuple[str, ...] = ()


@dataclass(frozen=True)
class GapView:
    description: str
    question_key: str | None
    knowledge_state: str | None
    kind: str | None = None


@dataclass
class ReportInputs:
    subject: dict[str, Any]
    questions: Sequence[QuestionView]
    findings: Sequence[FindingView]
    gaps: Sequence[GapView] = ()
    #: Every item research ACQUIRED, as contract refs: ``{"kind", "source_ref", "tier"}``.
    acquired: Sequence[Mapping[str, Any]] = ()
    thesis: Mapping[str, Any] | None = None
    size_fit: Mapping[str, Any] | None = None
    commodity_rows: Sequence[Mapping[str, Any]] = ()
    peer_rows: Sequence[Mapping[str, Any]] = ()
    council_convened: bool = False
    domain_cost: Mapping[str, Any] = field(default_factory=dict)


# ── Deterministic assembly ─────────────────────────────────────────────────── #


def _section_of_question(question: QuestionView | None, domain: str | None) -> str:
    if question is not None and question.report_section in SECTION_TITLES:
        return question.report_section  # type: ignore[return-value]
    return report_section_for(domain or (question.domain if question else None)) or (
        "evidence_quality_and_gaps"
    )


def _finding_dict(finding: FindingView, label: str) -> dict[str, Any]:
    return {
        "label": label,
        "finding_id": finding.finding_id,
        "statement": finding.statement,
        "question_key": finding.question_key,
        "domain": finding.domain,
        "domain_label": label_for(finding.domain),
        "evidence_ids": list(finding.evidence_ids),
        "calculation_ids": list(finding.calculation_ids),
        "source_kinds": list(finding.source_kinds),
        "confidence": finding.confidence,
        "direction": finding.direction,
        "period_key": finding.period_key,
        "references": list(finding.references),
    }


def _question_dict(question: QuestionView) -> dict[str, Any]:
    return {
        "question_key": question.key,
        "text": question.text,
        "why_it_matters": question.why_it_matters,
        "contract_status": question.contract_status,
        "unresolved_reason": question.unresolved_reason,
        "missing": list(question.missing),
    }


def _status(questions: Sequence[QuestionView], findings: Sequence[Any]) -> str:
    if not findings:
        return STATUS_NOT_ESTABLISHED
    if questions and all(q.contract_status == "satisfied" for q in questions):
        return STATUS_EVIDENCED
    return STATUS_PARTIAL


def _source_diversity(acquired: Sequence[Mapping[str, Any]], findings: Sequence[FindingView]
                      ) -> dict[str, Any]:
    by_kind: dict[str, set[str]] = {}
    for ref in acquired:
        kind = str(ref.get("kind") or "unknown")
        by_kind.setdefault(kind, set()).add(str(ref.get("source_ref") or ref.get("id") or ""))
    cited_kinds: dict[str, int] = {}
    for finding in findings:
        for kind in set(finding.source_kinds):
            cited_kinds[kind] = cited_kinds.get(kind, 0) + 1
    return {
        "acquired_distinct_sources_by_kind": {
            kind: len(refs) for kind, refs in sorted(by_kind.items())
        },
        "acquired_distinct_sources": len({r for refs in by_kind.values() for r in refs}),
        "findings_citing_kind": dict(sorted(cited_kinds.items())),
        "explanation": (
            "Distinct sources are counted by document, dataset or registrant, not by "
            "excerpt: twenty passages of one 10-K are one source."
        ),
    }


def assemble(inputs: ReportInputs) -> dict[str, Any]:
    """The report, deterministically. Never calls a model; never raises on content."""
    questions_by_key = {q.key: q for q in inputs.questions}
    labels: dict[str, str] = {}
    by_section: dict[str, list[dict[str, Any]]] = {key: [] for key in REPORT_SECTION_ORDER}
    for index, finding in enumerate(inputs.findings, start=1):
        label = f"F{index}"
        labels[finding.finding_id] = label
        question = questions_by_key.get(finding.question_key or "")
        section = _section_of_question(question, finding.domain)
        by_section[section].append(_finding_dict(finding, label))

    questions_by_section: dict[str, list[QuestionView]] = {k: [] for k in REPORT_SECTION_ORDER}
    for question in inputs.questions:
        questions_by_section[_section_of_question(question, question.domain)].append(question)

    sections: list[dict[str, Any]] = []
    for key in REPORT_SECTION_ORDER:
        if key in DERIVED_SECTIONS:
            continue
        findings = by_section[key]
        questions = questions_by_section[key]
        sections.append(
            {
                "key": key,
                "title": SECTION_TITLES[key],
                "lead": None,
                "status": _status(questions, findings),
                "findings": findings[:MAX_FINDINGS_PER_SECTION],
                "findings_omitted": max(0, len(findings) - MAX_FINDINGS_PER_SECTION),
                "open_questions": [
                    _question_dict(q) for q in questions if q.contract_status != "satisfied"
                ],
                "questions_asked": len(questions),
            }
        )

    # Thesis fit: per dimension, and size — both deterministic.
    thesis_section = next(s for s in sections if s["key"] == "thesis_fit")
    thesis_section["thesis"] = dict(inputs.thesis) if inputs.thesis else None
    thesis_section["dimensions"] = [
        {
            "question_key": q.key,
            "dimension": q.key.removeprefix("thesis_fit__"),
            "status": _status([q], [f for f in by_section["thesis_fit"]
                                    if f["question_key"] == q.key]),
            "finding_labels": [f["label"] for f in by_section["thesis_fit"]
                               if f["question_key"] == q.key],
        }
        for q in questions_by_section["thesis_fit"]
    ]
    thesis_section["size_fit"] = dict(inputs.size_fit) if inputs.size_fit else None
    if not inputs.thesis:
        thesis_section["status"] = "no_thesis"
        thesis_section["note"] = (
            "This research was not launched from a discovery thesis, so there is no "
            "thesis to test."
        )

    industry = next(s for s in sections if s["key"] == "industry_and_market")
    industry["commodity_table"] = [dict(row) for row in inputs.commodity_rows]
    competitive = next(s for s in sections if s["key"] == "competitive_position")
    competitive["peer_table"] = [dict(row) for row in inputs.peer_rows]

    # What would change the thesis: REFERENCES, never restatements (ownership).
    change = {
        "key": "what_would_change_the_thesis",
        "title": SECTION_TITLES["what_would_change_the_thesis"],
        "lead": None,
        "counter_thesis_labels": [
            f["label"] for f in by_section["risks_and_counter_thesis"]
            if (questions_by_key.get(f["question_key"] or "") or QuestionView("", "", None)
                ).domain == "counter_thesis"
        ],
        "catalyst_labels": [
            f["label"] for f in by_section["growth_and_catalysts"] if f["domain"] == "catalysts"
        ],
        "unestablished_thesis_dimensions": [
            d["dimension"] for d in thesis_section["dimensions"]
            if d["status"] == STATUS_NOT_ESTABLISHED
        ],
        "evidence_that_would_settle_open_questions": [
            {"question_key": q.key, "missing": list(q.missing)}
            for q in inputs.questions
            if q.contract_status != "satisfied" and q.missing
        ][:12],
    }

    platform_gaps = [
        {"description": g.description, "question_key": g.question_key,
         "knowledge_state": g.knowledge_state}
        for g in inputs.gaps
        if (g.kind or "platform_evidence_gap") == "platform_evidence_gap"
    ]
    business_risks = [
        f["label"] for f in by_section["risks_and_counter_thesis"]
        if f["domain"] in {"risks", "governance"}
    ]
    evidence = {
        "key": "evidence_quality_and_gaps",
        "title": SECTION_TITLES["evidence_quality_and_gaps"],
        "lead": None,
        "source_diversity": _source_diversity(inputs.acquired, inputs.findings),
        "questions_by_contract_status": _count(
            (q.contract_status or "unmet") for q in inputs.questions
        ),
        "unresolved_by_reason": _count(
            q.unresolved_reason for q in inputs.questions if q.unresolved_reason
        ),
        "platform_evidence_gaps": platform_gaps[:40],
        "business_risk_labels": business_risks,
        "explanation": (
            "Platform evidence gaps are what this research could not acquire — a limit of "
            "the platform, not a statement about the company. Business risks are findings "
            "about the company, each resting on cited evidence."
        ),
        "domain_cost": dict(inputs.domain_cost),
    }

    synthesis = {
        "key": "executive_synthesis",
        "title": SECTION_TITLES["executive_synthesis"],
        "lead": None,
        "sentences": deterministic_synthesis(sections),
        "author": "deterministic",
    }

    # The declared order: the derived sections sit where REPORT_SECTION_ORDER puts them.
    by_key = {s["key"]: s for s in [synthesis, *sections, change, evidence]}
    return {
        "version": REPORT_VERSION,
        "subject": dict(inputs.subject),
        "sections": [by_key[key] for key in REPORT_SECTION_ORDER],
        "finding_labels": labels,
        "council_convened": inputs.council_convened,
        "editor": {"used": False, "reason": "not_run"},
        "disclaimer": DISCLAIMER,
    }


def _count(values: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    for value in values:
        out[str(value)] = out.get(str(value), 0) + 1
    return dict(sorted(out.items()))


def deterministic_synthesis(sections: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """One sentence per evidenced section: its first finding, verbatim, with its label."""
    out: list[dict[str, Any]] = []
    for section in sections:
        findings = section.get("findings") or []
        if not findings:
            continue
        first = findings[0]
        out.append(
            {
                "text": f"{section['title']}: {first['statement']}",
                "labels": [first["label"]],
            }
        )
    return out[:8]


# ── The editor ─────────────────────────────────────────────────────────────── #

_LABEL_RE = re.compile(r"\[(F\d{1,3})\]")
_LABEL_WITH_SPACE_RE = re.compile(r"\s*\[F\d{1,3}\]")
_NON_DISCLOSURE_RE = re.compile(
    r"\b(?:does|do|did|has|have)\s*(?:not|n['’]t)\s+(?:publicly\s+|separately\s+)?"
    r"(?:disclos\w*|publish\w*|provid\w*|report\w*|break\s+(?:out|down)|quantif\w*)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(r"(?<![\w.])[-+]?\$?\d[\d,]*(?:\.\d+)?%?")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"'(])")


def _numbers(text: str) -> set[str]:
    out: set[str] = set()
    for token in _NUMBER_RE.findall(text or ""):
        cleaned = token.replace("$", "").replace(",", "").rstrip("%").lstrip("+")
        if cleaned in {"", "-"}:
            continue
        try:
            value = float(cleaned)
        except ValueError:
            continue
        out.add(f"{value:g}")
    return out


def validate_sentence(
    sentence: str, findings_by_label: Mapping[str, Mapping[str, Any]]
) -> tuple[bool, str | None]:
    """Keep ``sentence`` only if it cites real findings and invents nothing."""
    from app.services import safety_terms
    from app.services.knowledge_state import asserts_issuer_non_disclosure

    labels = _LABEL_RE.findall(sentence)
    if not labels:
        return False, "cites_no_finding"
    if any(label not in findings_by_label for label in labels):
        return False, "cites_unknown_finding"
    cited_text = " ".join(str(findings_by_label[label]["statement"]) for label in labels)
    body = _LABEL_RE.sub("", sentence)
    if not _numbers(body) <= _numbers(cited_text):
        return False, "figure_not_in_cited_findings"
    if safety_terms.scan_value(body, path="editor"):
        return False, "safety_terms"
    if asserts_issuer_non_disclosure(body) or (
        _NON_DISCLOSURE_RE.search(body) and not _NON_DISCLOSURE_RE.search(cited_text)
    ):
        # Stricter than the shared guard, which needs a known disclosure topic: an
        # editor restates findings, so a non-disclosure claim no cited finding makes is
        # one the editor invented from an absence.
        return False, "absence_asserted_as_issuer_fact"
    return True, None


def _strip_labels(sentence: str) -> str:
    return _LABEL_WITH_SPACE_RE.sub("", sentence).strip()


def _split_sentences(text: str) -> list[str]:
    return [part.strip() for part in _SENTENCE_RE.split(text or "") if part.strip()]


async def edit(report: dict[str, Any], client: Any) -> dict[str, Any]:
    """Let a model write the synthesis and section leads, keeping only what verifies."""
    findings_by_label: dict[str, dict[str, Any]] = {}
    for section in report["sections"]:
        for finding in section.get("findings") or []:
            findings_by_label[finding["label"]] = finding
    if client is None:
        report["editor"] = {"used": False, "reason": "no_editor_model_available"}
        return report
    if len(findings_by_label) < MIN_SYNTHESIS_SENTENCES:
        report["editor"] = {"used": False, "reason": "too_few_findings"}
        return report

    system = (
        "You are the editor of an internal equity-research report. You write ONLY from "
        "the findings given, each identified by a label like [F3].\n"
        "RULES:\n"
        "1. Every sentence MUST end with the label(s) of the finding(s) it rests on, "
        "e.g. 'Copper is 78% of revenue [F4].'\n"
        "2. Use no figure that is not in a finding you cite. Do not compute new numbers.\n"
        "3. Never output BUY, SELL, HOLD, a rating, a price target or a fair value.\n"
        "4. Absence of a finding is NOT a fact about the company: never write that the "
        "company does not disclose something.\n"
        "5. Say where the evidence is thin. Prefer plain, specific sentences.\n"
        'Return ONLY JSON: {"synthesis": [str, ...], "leads": {"<section_key>": str}}. '
        "synthesis: 4-7 sentences, the most decision-relevant facts first, across "
        "sections. leads: at most one sentence per section key given."
    )
    lines = ["SECTIONS AND FINDINGS:"]
    for section in report["sections"]:
        findings = section.get("findings") or []
        if not findings:
            continue
        lines.append(f"\n## {section['key']} — {section['title']}")
        for finding in findings:
            lines.append(f"[{finding['label']}] {str(finding['statement'])[:500]}")
    user = "\n".join(lines)[:40_000]

    try:
        if hasattr(client, "complete_json"):
            payload = await client.complete_json(
                system, user, max_tokens=EDITOR_MAX_TOKENS, timeout=EDITOR_TIMEOUT
            )
        else:
            response = await client.complete(
                system=system, user=user, max_tokens=EDITOR_MAX_TOKENS,
                timeout=EDITOR_TIMEOUT,
            )
            payload = getattr(response, "payload", None) or {}
    except Exception as exc:  # noqa: BLE001 - the editor costs prose, never the report
        report["editor"] = {"used": False, "reason": f"editor_failed:{type(exc).__name__}"}
        return report
    if not isinstance(payload, dict):
        report["editor"] = {"used": False, "reason": "editor_returned_no_json"}
        return report

    rejected: dict[str, int] = {}
    kept: list[dict[str, Any]] = []
    raw = payload.get("synthesis")
    candidates = raw if isinstance(raw, list) else _split_sentences(str(raw or ""))
    for text in candidates[:10]:
        for sentence in _split_sentences(str(text)):
            ok, reason = validate_sentence(sentence, findings_by_label)
            if ok:
                kept.append({"text": _strip_labels(sentence),
                             "labels": _LABEL_RE.findall(sentence)})
            else:
                rejected[str(reason)] = rejected.get(str(reason), 0) + 1
    synthesis = report["sections"][0]
    if len(kept) >= MIN_SYNTHESIS_SENTENCES:
        synthesis["sentences"] = kept[:8]
        synthesis["author"] = "editor_model_verified"

    leads = payload.get("leads") if isinstance(payload.get("leads"), dict) else {}
    leads_kept = 0
    for section in report["sections"][1:]:
        text = str((leads or {}).get(section["key"]) or "").strip()
        if not text:
            continue
        sentence = _split_sentences(text)[0] if _split_sentences(text) else ""
        section_labels = {f["label"] for f in section.get("findings") or []}
        ok, reason = validate_sentence(sentence, findings_by_label)
        if ok and set(_LABEL_RE.findall(sentence)) <= section_labels:
            section["lead"] = {"text": _strip_labels(sentence),
                               "labels": _LABEL_RE.findall(sentence)}
            leads_kept += 1
        else:
            key = reason or "lead_cites_another_section"
            rejected[key] = rejected.get(key, 0) + 1
    report["editor"] = {
        "used": True,
        "synthesis_sentences_kept": len(kept) if len(kept) >= MIN_SYNTHESIS_SENTENCES else 0,
        "leads_kept": leads_kept,
        "rejected_by_reason": dict(sorted(rejected.items())),
        "fallback": synthesis["author"] == "deterministic",
    }
    return report


def payload_size(report: Mapping[str, Any]) -> int:
    return len(json.dumps(report, default=str))


__all__ = [
    "DISCLAIMER",
    "REPORT_VERSION",
    "SECTION_TITLES",
    "FindingView",
    "GapView",
    "QuestionView",
    "ReportInputs",
    "assemble",
    "deterministic_synthesis",
    "edit",
    "validate_sentence",
]
