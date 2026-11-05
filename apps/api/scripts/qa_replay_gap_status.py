"""Replay gap reconciliation (ADR-086 gap status) and the chair claim checks over
stored report payloads.

Usage::

    PYTHONPATH=$PWD python scripts/qa_replay_gap_status.py <reports-dir> --json out.json
    PYTHONPATH=$PWD python scripts/qa_replay_gap_status.py <reports-dir> \
        --compare before.json [--json after.json]

``<reports-dir>`` holds one sub-directory per report with a ``report.json`` (the report
API's shape, ``source_summary_json.v3_research`` …). Nothing is fetched and nothing is
written back.

Each payload gap is reconciled against the payload's findings with the code on
``PYTHONPATH`` — run it once with the base code (``--json before.json``) and once with
the change (``--compare before.json``) to list every gap whose status changed, with its
closing / superseding finding, and every chair sentence the ADR-082 enforcer newly
withholds or rewrites. A replay holds only what the payload holds: no validated facts,
no corpus documents, and question-sourced fields from the stored verdict record, so
before and after are judged on the same inputs.

The output names gap ids, finding ids, statuses and the enforcer's own violation record.
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any

#: The pre-ADR-086 verdict status → the ADR-086 vocabulary, for base code that has no
#: ``gap_status``.
_LEGACY_STATUS = {
    "closed": "CLOSED",
    "partially_closed": "PARTIALLY_CLOSED",
    "superseded": "SUPERSEDED",
    "still_open": "OPEN",
}


def _as_of(report: dict[str, Any]) -> date | None:
    created = str(report.get("created_at") or "")[:10]
    try:
        return date.fromisoformat(created)
    except ValueError:
        return None


def _finding_row(item: dict[str, Any]) -> SimpleNamespace:
    from app.services import research_fields as rf

    return SimpleNamespace(
        id=item.get("finding_id"),
        statement=item.get("statement"),
        claim_key=None,
        question_key=item.get("question_key"),
        scope_key=item.get("scope_key"),
        period_key=item.get("period_key"),
        source_kinds_json=item.get("source_kinds") or [],
        source_published_at=rf.parse_date(item.get("source_published_at")),
        verification_status=item.get("verification_status"),
        superseded_by_finding_id=item.get("superseded_by_finding_id"),
        period_type=item.get("period_basis"),
    )


def _gap_facts(cls: Any, **values: Any) -> Any:
    """Build a ``GapFacts`` with only the fields the code on the path knows."""
    known = {f.name for f in dataclasses.fields(cls)}
    return cls(**{k: v for k, v in values.items() if k in known})


def _subject(report: dict[str, Any]) -> tuple[str | None, str | None]:
    """The subject's name and ticker from the report title ("LLM Council Analysis Draft
    — ANTO — Antofagasta plc")."""
    parts = [p.strip() for p in str(report.get("title") or "").split("\u2014")]
    if len(parts) >= 3:
        return parts[-1] or None, parts[-2] or None
    return None, None


def replay_gaps(v3: dict[str, Any], as_of: date | None,
                subject: tuple[str | None, str | None] = (None, None)) -> dict[str, Any]:
    import inspect

    from app.services.pipeline import gap_reconciliation as gr

    stored = {str(g.get("gap_id")): g for g in
              ((v3.get("gap_reconciliation") or {}).get("gaps") or []) if g.get("gap_id")}
    findings = [gr.FindingFacts.from_row(_finding_row(f)) for f in v3.get("findings") or []
                if f.get("finding_id")]
    gaps, question_fields = [], {}
    for g in v3.get("gaps") or []:
        gid = str(g.get("gap_id") or "")
        if not gid:
            continue
        record = stored.get(gid) or {}
        question_key = None
        if record.get("field_source") == "question" and record.get("fields"):
            question_key = f"replay:{gid}"
            question_fields[question_key] = tuple(record["fields"])
        gaps.append(_gap_facts(
            gr.GapFacts, gap_id=gid, gap_type=str(g.get("gap_type") or ""),
            description=str(g.get("description") or ""), question_key=question_key,
            knowledge_state=g.get("knowledge_state"),
        ))
    extra: dict[str, Any] = {}
    params = inspect.signature(gr.reconcile).parameters
    if "subject_name" in params:  # code that knows the subject (ADR-086)
        extra["subject_name"] = subject[0]
    if "subject_ticker" in params:
        extra["subject_ticker"] = subject[1]
    verdicts = gr.reconcile(gaps, findings, question_fields=question_fields, as_of=as_of,
                            **extra)
    out = {}
    for v in verdicts:
        status = getattr(v, "gap_status", None) or _LEGACY_STATUS.get(v.status, v.status)
        out[v.gap_id] = {
            "gap_status": status,
            "status": v.status,
            "reasons": list(v.reasons),
            "finding_ids": list(v.finding_ids),
            "closed_by_finding_id": v.closed_by_finding_id,
            "superseded_by_finding_id": getattr(v, "superseded_by_finding_id", None),
        }
    return out


def _v2_content(markdown: str | None) -> dict[str, Any] | None:
    """The V2 report content: the fenced JSON block in ``content_markdown``."""
    text = str(markdown or "")
    start, end = text.find("```json"), text.rfind("```")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(text[start + len("```json"):end].strip())
    except ValueError:
        return None
    return parsed if isinstance(parsed, dict) else None


def replay_enforcer(report: dict[str, Any]) -> list[dict[str, Any]]:
    from app.services.ledger.canonical_state import build_canonical_state
    from app.services.ledger.report_invariants import enforce, enforce_content

    summary = report.get("source_summary_json") or {}
    v3 = summary.get("v3_research") or {}
    legacy = summary.get("llm_council")
    state = build_canonical_state(v3, llm_council=legacy, as_of=report.get("created_at"))
    result = enforce(v3, state, llm_council=legacy).to_dict()
    _, content = enforce_content(_v2_content(report.get("content_markdown")), state)
    return [*(result.get("violations") or []), *(v.to_dict() for v in content)]


def replay_report(report: dict[str, Any]) -> dict[str, Any]:
    v3 = (report.get("source_summary_json") or {}).get("v3_research") or {}
    return {
        "gaps": replay_gaps(v3, _as_of(report), _subject(report)),
        "stored": {str(g.get("gap_id")): g.get("reconciliation_status")
                   for g in v3.get("gaps") or []},
        "violations": replay_enforcer(report),
    }


def _key(v: dict[str, Any]) -> tuple[str, str, str]:
    return (str(v.get("rule")), str(v.get("path")), str(v.get("action")))


def compare(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """Status distributions, every changed gap, every new / gone enforcer violation."""
    dist_before: Counter[str] = Counter()
    dist_after: Counter[str] = Counter()
    changed, new_violations, gone_violations = [], [], []
    for name in sorted(after):
        b, a = before.get(name) or {}, after[name]
        for gid, verdict in a["gaps"].items():
            old = (b.get("gaps") or {}).get(gid) or {}
            dist_after[verdict["gap_status"]] += 1
            dist_before[old.get("gap_status", "MISSING")] += 1
            if old.get("gap_status") != verdict["gap_status"]:
                changed.append({"report": name, "gap_id": gid,
                                "before": old.get("gap_status"),
                                "after": verdict["gap_status"],
                                "reasons": verdict["reasons"],
                                "finding_ids": verdict["finding_ids"],
                                "closed_by": verdict["closed_by_finding_id"],
                                "superseded_by": verdict["superseded_by_finding_id"]})
        old_keys = {_key(v) for v in b.get("violations") or []}
        new_keys = {_key(v) for v in a.get("violations") or []}
        new_violations += [{"report": name, **v} for v in a.get("violations") or []
                           if _key(v) not in old_keys]
        gone_violations += [{"report": name, **v} for v in b.get("violations") or []
                            if _key(v) not in new_keys]
    return {"distribution_before": dict(dist_before), "distribution_after": dict(dist_after),
            "changed_gaps": changed, "new_violations": new_violations,
            "gone_violations": gone_violations}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("reports_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--compare", type=Path, default=None)
    args = parser.parse_args(argv)

    rows = {path.parent.name: replay_report(json.loads(path.read_text()))
            for path in sorted(args.reports_dir.glob("*/report.json"))}
    for name, row in rows.items():
        dist = Counter(v["gap_status"] for v in row["gaps"].values())
        print(f"{name:7s} gaps={len(row['gaps']):3d} {dict(sorted(dist.items()))} "
              f"violations={len(row['violations'])}")
    if args.json:
        args.json.write_text(json.dumps(rows, indent=2, default=str))
    if args.compare:
        diff = compare(json.loads(args.compare.read_text()), rows)
        print(json.dumps(diff, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
