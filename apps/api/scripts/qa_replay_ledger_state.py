"""Replay the report-status invariants (ADR-082) over stored report payloads.

Usage::

    PYTHONPATH=$PWD python scripts/qa_replay_ledger_state.py <reports-dir> [--json out.json]
        [--show N]

``<reports-dir>`` holds one sub-directory per report with a ``report.json`` — the
report API's shape (``source_summary_json.v3_research`` …). Nothing is fetched and
nothing is written back: the canonical state is DERIVED from each stored payload
(``derived_from: "payload"``) and the checker reports what it would repair or withhold.
At generation time the state is computed from the ledger itself.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.services.ledger.canonical_state import build_canonical_state
from app.services.ledger.report_invariants import InvariantResult, enforce, enforce_content


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


def replay_report(report: dict[str, Any]) -> dict[str, Any]:
    summary = report.get("source_summary_json") or {}
    v3 = summary.get("v3_research") or {}
    legacy = summary.get("llm_council")
    state = build_canonical_state(v3, llm_council=legacy, as_of=report.get("created_at"))
    result = enforce(v3, state, llm_council=legacy)
    _, content_violations = enforce_content(_v2_content(report.get("content_markdown")), state)
    merged = InvariantResult(
        v3=result.v3, llm_council=result.llm_council,
        violations=[*result.violations, *content_violations],
    )
    return {"state": state, "result": merged.to_dict()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--show", type=int, default=3,
                        help="example violations printed per report")
    args = parser.parse_args(argv)

    rows: dict[str, Any] = {}
    for path in sorted(args.reports_dir.glob("*/report.json")):
        name = path.parent.name
        replay = replay_report(json.loads(path.read_text()))
        rows[name] = replay
        result = replay["result"]
        state = replay["state"]
        print(f"{name:7s} violations={result['violations_total']:3d} "
              f"findings={state['findings']['total']} "
              f"council={state['council']['status_text']!r}")
        for rule, n in sorted(result["by_rule"].items()):
            print(f"          {n:3d}  {rule}")
        for v in result["violations"][: args.show]:
            print(f"          - [{v['action']}] {v['path']}: {v['text'][:150]!r}")
    total = sum(r["result"]["violations_total"] for r in rows.values())
    print(f"TOTAL violations across {len(rows)} reports: {total}")
    if args.json:
        args.json.write_text(json.dumps(
            {k: v["result"] for k, v in rows.items()}, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
