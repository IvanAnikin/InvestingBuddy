"""Real-asset block from evidence — V3.20 slice 5.4 (spec §3.5).

Builds the electricity real-asset block from the run's own ledger findings: capacity in
its natural unit, utilisation, announced expansions and qualification certificates with
their dates. Deterministic and pure; the caller loads the findings.

THE RULE: every value is read from a finding's statement, carries that finding's id and
evidence ids, and is quoted. A field with nothing behind it is ``not_disclosed`` — never a
placeholder, never an estimate. Replacement value appears only when a finding states one
(cited context, D5), never computed here.

Units
=====
| family | units |
|---|---|
| ``apparent_power`` | kVA, MVA, GVA, MVAr (transformer / reactor ratings) |
| ``power`` | kW, MW, GW |
| ``energy`` | kWh, MWh, GWh |
| ``length`` | km / kilometres / miles **of cable, line or conductor** (not a distance) |
| ``mass_per_year`` | tonnes / tons / kt / Mt per year, tpa, ktpa, Mtpa |
| ``units_per_year`` | units, transformers, panels, chargers … per year |
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

NOT_DISCLOSED = "not_disclosed"
DISCLOSED = "disclosed"

#: Layer → the schema's ``real_asset_block.asset_type`` enum.
ASSET_TYPE_BY_LAYER: dict[str, str] = {
    "generation_fuel": "generation_assets",
    "utilities_grid_services": "generation_assets",
    "transmission": "transmission_distribution",
    "distribution_grid_edge": "transmission_distribution",
    "upstream_electrical_materials": "processing_refining",
    "sensing_protection_digital": "manufacturing_capacity",
    "storage": "manufacturing_capacity",
    "power_electronics": "manufacturing_capacity",
    "demand_side_decentralised": "manufacturing_capacity",
}

_NUM = r"(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
_PER_YEAR = r"\s*(?:per|a|an|/)\s*(?:year|annum|yr)\b"

_CAPACITY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("apparent_power", re.compile(_NUM + r"(?P<unit>GVA|MVA|kVA|MVAr|Mvar)\b"
                                  r"(?P<per>" + _PER_YEAR + r")?")),
    ("energy", re.compile(_NUM + r"(?P<unit>GWh|MWh|kWh)\b")),
    ("power", re.compile(_NUM + r"(?P<unit>GW|MW|kW)\b(?!h)")),
    ("length", re.compile(
        _NUM + r"(?P<unit>circuit[- ]km|km|kilomet(?:re|er)s|miles)\s+(?:of\s+)?"
        r"(?:[A-Za-z-]+\s+){0,3}?(?:cables?|lines?|conductors?|transmission|wire)\b",
        re.IGNORECASE)),
    ("mass_per_year", re.compile(
        _NUM + r"(?:(?P<scale>thousand|million)\s+)?"
        r"(?:(?P<unit>tonnes|tons|t|kt|mt)" + _PER_YEAR + r"|(?P<unit2>tpa|ktpa|mtpa))",
        re.IGNORECASE)),
    ("units_per_year", re.compile(
        _NUM + r"(?P<unit>units|transformers|panels|switchboards|chargers|inverters|"
        r"reactors|breakers)" + _PER_YEAR, re.IGNORECASE)),
)

_UTILISATION_RE = re.compile(
    r"(?:utili[sz]ation|load\s+factor|capacity\s+factor|running\s+at|operating\s+at)"
    r"[^.;%]{0,40}?(?P<pct>\d{1,3}(?:\.\d+)?)\s*(?:%|per\s?cent)",
    re.IGNORECASE,
)
_EXPANSION_RE = re.compile(
    r"\b(?:expan(?:d|ds|ded|ding|sion)|new\s+(?:plant|factory|facility|line)|"
    r"additional\s+capacity|increase\s+(?:its\s+|our\s+)?capacity|doubl(?:e|es|ing)|"
    r"greenfield|brownfield|under\s+construction)\b",
    re.IGNORECASE,
)

_CERT_RE = re.compile(
    r"\b(?:type[- ]test(?:ed|s|ing)?|short[- ]circuit\s+test(?:ed)?|certifi(?:ed|cate|cation)|"
    r"pre-?qualif(?:ied|ication)|qualified\s+(?:supplier|vendor|by|with)|"
    r"approved\s+(?:vendor|supplier)|grid[- ]code\s+(?:compliance|approval|certificate)|"
    r"homologat(?:ed|ion))\b",
    re.IGNORECASE,
)
_STANDARD_RE = re.compile(
    r"\b(?:IEC\s?\d{4,5}(?:-\d+)*|IEEE\s?C?\d+(?:\.\d+)*|UL\s?\d{2,4}|"
    r"ISO\s?\d{4,5}|EN\s?\d{4,5}(?:-\d+)*|ANSI\s?C?\d+(?:\.\d+)*)\b"
)
_BODIES = ("KEMA", "DNV", "CESI", "IPH", "TÜV", "TUV", "Intertek", "CSA", "Bureau Veritas",
           "UL Solutions", "SGS", "STL")
_BODY_RE = re.compile(r"\b(?:" + "|".join(re.escape(b) for b in _BODIES) + r")\b")
_DATE_RE = re.compile(
    r"\b(?:(?:jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec)[a-z]*\.?\s+"
    r"(?:\d{1,2},?\s+)?(?:19|20)\d{2}|(?:19|20)\d{2}-\d{2}(?:-\d{2})?|(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_REPLACEMENT_RE = re.compile(
    r"\breplacement\s+(?:value|cost)\b|\breinstatement\s+(?:value|cost)\b", re.IGNORECASE)


def _num(text: str) -> float | None:
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _cite(finding: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "finding_id": str(finding.get("finding_id") or ""),
        "evidence_ids": [str(e) for e in (finding.get("evidence_ids") or [])],
        "as_of": finding.get("source_published_at"),
        "quote": " ".join(str(finding.get("statement") or "").split())[:400],
    }


def _usable(finding: Mapping[str, Any]) -> bool:
    """A finding the block may read: stated, cited, not withdrawn, not superseded."""
    return bool(
        str(finding.get("statement") or "").strip()
        and finding.get("evidence_ids")
        and finding.get("verification_status") != "withdrawn"
        and not finding.get("superseded_by")
    )


def capacity_readings(statement: str) -> list[dict[str, Any]]:
    """Every natural-unit capacity figure in one statement (no year figures)."""
    out: list[dict[str, Any]] = []
    taken: list[tuple[int, int]] = []
    for family, pattern in _CAPACITY_PATTERNS:
        for m in pattern.finditer(statement):
            if any(m.start() < e and s < m.end() for s, e in taken):
                continue
            value = _num(m.group("num"))
            if value is None:
                continue
            groups = m.groupdict()
            unit = groups.get("unit") or groups.get("unit2") or ""
            scale = groups.get("scale")
            if family == "mass_per_year":
                unit = (f"{scale} " if scale else "") + unit.lower() + (
                    " per year" if groups.get("unit") else "")
            elif family in ("units_per_year",):
                unit = unit.lower() + " per year"
            elif family == "apparent_power" and groups.get("per"):
                unit = unit + " per year"
            elif family == "length":
                unit = unit.lower()
            taken.append((m.start(), m.end()))
            out.append({"value": value, "unit": unit, "unit_family": family,
                        "matched": m.group(0).strip()})
    return out


def certifications_in(statement: str) -> list[dict[str, Any]]:
    """One record per statement that names a qualification, with body, standard, date."""
    if not _CERT_RE.search(statement):
        return []
    body = _BODY_RE.search(statement)
    standard = _STANDARD_RE.search(statement)
    date = _DATE_RE.search(statement)
    return [{
        "certification": _CERT_RE.search(statement).group(0).lower(),  # type: ignore[union-attr]
        "standard": standard.group(0) if standard else None,
        "body": body.group(0) if body else None,
        "date": date.group(0) if date else None,
        "date_state": DISCLOSED if date else NOT_DISCLOSED,
    }]


def real_asset_block(
    findings: Sequence[Mapping[str, Any]],
    classification: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """The electricity real-asset block, or ``None`` for a company not in the vertical."""
    if not classification or classification.get("status") != "classified":
        return None
    primary = classification.get("primary_layer")
    capacity: list[dict[str, Any]] = []
    expansions: list[dict[str, Any]] = []
    utilisation: list[dict[str, Any]] = []
    certifications: list[dict[str, Any]] = []
    replacement: list[dict[str, Any]] = []
    for finding in findings:
        if not _usable(finding):
            continue
        statement = str(finding["statement"])
        cite = _cite(finding)
        readings = capacity_readings(statement)
        is_expansion = bool(_EXPANSION_RE.search(statement))
        for r in readings:
            (expansions if is_expansion else capacity).append({**r, **cite})
        for m in _UTILISATION_RE.finditer(statement):
            pct = _num(m.group("pct"))
            if pct is not None and 0 <= pct <= 100:
                utilisation.append({"value": pct, "unit": "%", **cite})
        for c in certifications_in(statement):
            certifications.append({**c, **cite})
        if _REPLACEMENT_RE.search(statement):
            replacement.append({"stated_by_source": True, **cite})

    def state(rows: list[dict[str, Any]]) -> str:
        return DISCLOSED if rows else NOT_DISCLOSED

    return {
        "asset_type": ASSET_TYPE_BY_LAYER.get(str(primary), "mixed"),
        "primary_layer": primary,
        "capacity": {"state": state(capacity), "readings": capacity},
        "expansions": {"state": state(expansions), "readings": expansions},
        "utilisation": {"state": state(utilisation), "readings": utilisation},
        "certifications": {"state": state(certifications), "items": certifications},
        "replacement_value": {"state": state(replacement), "statements": replacement},
        "note": (
            "Read from this run's cited findings only. Capacity is shown in the unit the "
            "source used; nothing is converted or estimated. A field with no finding behind "
            "it is not_disclosed. Replacement value appears only where a source states it."
        ),
    }


__all__ = [
    "ASSET_TYPE_BY_LAYER",
    "DISCLOSED",
    "NOT_DISCLOSED",
    "capacity_readings",
    "certifications_in",
    "real_asset_block",
]
