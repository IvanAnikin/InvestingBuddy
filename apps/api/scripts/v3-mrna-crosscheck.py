#!/usr/bin/env python3
"""Audit a REAL persisted report for the defect classes this campaign found.

Run against producer output, never a hand-written fixture:

    python scripts/v3-mrna-crosscheck.py <report.json>

Exit code is the number of findings, so it can gate a release.
"""
from __future__ import annotations

import collections
import json
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from app.services.numeric_verification import comparative_period_conflict  # noqa: E402
from app.services.providers.leads import parse_number_candidates  # noqa: E402

#: Moderna's real FY2025 10-K, in millions.
FY2025 = {"revenue": 1944.0, "operating_income": -3074.0, "net_income": -2822.0}
#: What the stale `Revenues` tag last reported, in FY2022, and shipped as FY2025.
#: Matched in every way a report writes it — "19.263 billion", "19,263", "19.3 billion"
#: — because the first version of this detector stripped commas but not decimal points
#: and therefore found nothing in a report that says "$19.263 billion" six times.
STALE_FORMS = ("19263", "19.263", "19.3billion", "19.3bn")


def audit(report: dict) -> list[tuple[str, str]]:
    findings: list[tuple[str, str]] = []
    raw = report.get("content_markdown") or ""
    if not isinstance(raw, str):
        raw = json.dumps(raw)
    summary = report.get("source_summary_json") or {}
    v3 = summary.get("v3_research") or {}

    def strings() -> list[str]:
        out = []
        for m in re.findall(r'"((?:[^"\\]|\\.){20,600})"', raw):
            out.append(m.encode().decode("unicode_escape", errors="ignore"))
        return out

    # 1. Stale-period alias.
    for text in strings():
        flat = re.sub(r"[,\s$]", "", text).lower()
        if any(form in flat for form in STALE_FORMS) and re.search(
            r"fy\s*2025|2025", text, re.I
        ):
            findings.append(("stale_alias", text[:150]))

    # 2. Incompatible period comparisons.
    for text in strings():
        if comparative_period_conflict(text):
            findings.append(("period_comparison", text[:150]))

    # 3. Internally inconsistent FY2025 figures.
    for name, expected in FY2025.items():
        for text in strings():
            if not re.search(r"fy\s*2025", text, re.I):
                continue
            if name.split("_")[0] not in text.lower():
                continue
            nums = {abs(n) for n in parse_number_candidates_in(text)}
            if nums and not any(abs(abs(expected) - n) <= abs(expected) * 0.02 for n in nums):
                findings.append((f"figure_{name}", text[:150]))
                break

    # 4. Duplicate filing events, by accession-bearing URL.
    urls = re.findall(r'"source_url":\s*"([^"]+sec\.gov[^"]*)"', raw)
    for url, count in collections.Counter(urls).items():
        if count > 2:  # two views of one event are by design; more is duplication
            findings.append(("duplicate_event", f"x{count} {url[-70:]}"))

    # 5. Duplicate claims.
    claims = re.findall(r'"claim":\s*"((?:[^"\\]|\\.){10,400})"', raw)
    norm = lambda t: re.sub(r"[^a-z0-9 ]", "", t.lower()).strip()  # noqa: E731
    for text, count in collections.Counter(norm(c) for c in claims).items():
        if count > 1:
            findings.append(("duplicate_claim", f"x{count} {text[:110]}"))

    # 6. Annual metadata inconsistency.
    m = re.search(r'"latest_annual":\s*(null|"[^"]*")', raw)
    if m and m.group(1) == "null" and re.search(r"fy\s*2025", raw, re.I):
        findings.append(("annual_metadata", "latest_annual is null beside FY2025 claims"))

    # 7. Evidence tier inversion / unsupported canonical evidence.
    for lead in (v3.get("external_research") or {}).get("leads", []):
        if lead.get("is_canonical_evidence") and not lead.get("content_hash"):
            findings.append(("unsupported_evidence", str(lead.get("claim"))[:110]))
        if lead.get("is_canonical_evidence") and lead.get("corroborating_only") is None:
            findings.append(("tier_unknown", str(lead.get("claim"))[:110]))

    # 8. Numeric normalisation failures.
    for lead in (v3.get("external_research") or {}).get("leads", []):
        value = lead.get("claimed_value")
        if value and not parse_number_candidates(value):
            findings.append(("numeric_unreadable", f"{value!r}"))

    # 9. Misleading telemetry labels.
    consumption = v3.get("consumption") or {}
    if consumption.get("model_by_vendor") == {} and (
        consumption.get("provider_input_tokens") or 0
    ) > 0:
        findings.append(
            ("telemetry_attribution", "provider tokens spent but model_by_vendor empty")
        )
    return findings


def parse_number_candidates_in(text: str) -> list[float]:
    out: list[float] = []
    for token in re.findall(r"[-+(]?\$?\s?\d[\d.,]*\s*(?:billion|million|bn|m)?\)?", text):
        out.extend(parse_number_candidates(token.strip()))
    return out


def main() -> int:
    path = pathlib.Path(sys.argv[1])
    findings = audit(json.loads(path.read_text()))
    by_kind = collections.Counter(k for k, _ in findings)
    print(f"=== cross-check: {path.name} ===")
    for kind in sorted(
        {
            "stale_alias", "period_comparison", "figure_revenue", "figure_operating_income",
            "figure_net_income", "duplicate_event", "duplicate_claim", "annual_metadata",
            "unsupported_evidence", "tier_unknown", "numeric_unreadable",
            "telemetry_attribution",
        }
    ):
        print(f"  {kind:24} {by_kind.get(kind, 0)}")
    print(f"  {'TOTAL':24} {len(findings)}")
    for kind, detail in findings[:20]:
        print(f"    [{kind}] {detail}")
    return len(findings)


if __name__ == "__main__":
    raise SystemExit(min(main(), 250))
