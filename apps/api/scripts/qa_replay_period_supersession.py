"""Replay the later-period-supersedes labels (ADR-087) over stored report payloads.

Usage::

    PYTHONPATH=$PWD python scripts/qa_replay_period_supersession.py <reports-dir>
        [--json out.json]

``<reports-dir>`` holds one sub-directory per report with a ``report.json`` — the
report API's shape (``source_summary_json.v3_research`` …). Nothing is fetched and
nothing is written back. Every changed text is printed before and after, so each label
can be checked by hand.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from app.services.ledger.period_supersession import apply_period_supersession


def _texts(v3: dict[str, Any]) -> dict[str, str]:
    """Every text the rule may label, by a stable path."""
    out: dict[str, str] = {}
    for i, finding in enumerate(v3.get("findings") or ()):
        if isinstance(finding, dict):
            out[f"findings[{i}]"] = str(finding.get("statement") or "")
    report = v3.get("professional_research") or {}
    for i, section in enumerate(report.get("sections") or ()):
        if not isinstance(section, dict):
            continue
        lead = section.get("lead")
        if isinstance(lead, dict):
            out[f"sections[{i}].lead"] = str(lead.get("text") or "")
        for j, sentence in enumerate(section.get("sentences") or ()):
            if isinstance(sentence, dict):
                out[f"sections[{i}].sentences[{j}]"] = str(sentence.get("text") or "")
        for j, finding in enumerate(section.get("findings") or ()):
            if isinstance(finding, dict):
                out[f"sections[{i}].findings[{j}]"] = str(finding.get("statement") or "")
    chair = v3.get("chair") or {}
    out["chair.synthesis"] = str(chair.get("synthesis") or "")
    for i, point in enumerate(chair.get("key_points") or ()):
        if isinstance(point, dict):
            out[f"chair.key_points[{i}]"] = str(point.get("text") or "")
    return out


def replay_report(report: dict[str, Any]) -> dict[str, Any]:
    v3 = (report.get("source_summary_json") or {}).get("v3_research") or {}
    labelled, record = apply_period_supersession(v3, as_of=report.get("created_at"))
    before, after = _texts(v3), _texts(labelled)
    changed = [
        {"path": path, "before": before[path], "after": after.get(path, "")}
        for path in before if before[path] != after.get(path, "")
    ]
    return {"record": record, "changed": changed}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("reports_dir", type=Path)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args(argv)
    results: dict[str, Any] = {}
    for directory in sorted(p for p in args.reports_dir.iterdir() if p.is_dir()):
        path = directory / "report.json"
        if not path.exists():
            continue
        result = replay_report(json.loads(path.read_text()))
        results[directory.name] = result
        record = result["record"]
        print(f"== {directory.name}: {len(result['changed'])} text(s) changed, "
              f"{len(record['labelled'])} label(s), "
              f"{len(record['periods_filled'])} segment period(s) filled")
        for entry in record["periods_filled"]:
            print(f"   period filled: {entry['finding_id']} -> {entry['period_key']}")
        for change in result["changed"]:
            print(f"   {change['path']}")
            print(f"     before: {change['before']}")
            print(f"     after:  {change['after']}")
    if args.json:
        args.json.write_text(json.dumps(results, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
