"""Grid plans — shared vocabulary (V3.20 slice 7.1).

* ``SOURCES``: every grid-plan source with its pinned download URL, the licence as read
  on the publisher's own page (date checked), the use constraint (source_policy
  vocabulary), the attribution every row carries, and the flag that gates it.
* ``technology_tags`` / ``max_voltage_kv``: deterministic keyword tags from a project's
  name, category and description — the label is only ever what the text says.
* ``status_class``: a publisher's status text mapped onto one small vocabulary.
* ``commissioning_year``: the first four-digit year stated (``2032(6GWh)/2038`` → 2032).
* ``split_promoters``: "Red Eléctrica, Rte" / "TransnetBW; 50Hertz" / "REN and Red
  Eléctrica" → names.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.services.web_research.source_policy import USE_PRIVATE_USE_PERMITTED

BASE_VERSION = "2026-11-16.1"


@dataclass(frozen=True)
class GridSource:
    key: str
    name: str
    plan_name: str
    landing_url: str
    download_url: str
    licence: str
    use_constraint: str
    attribution: str
    #: Settings attribute that must be True to ingest or read this source.
    flag: str


SOURCES: dict[str, GridSource] = {
    "entsoe_tyndp": GridSource(
        key="entsoe_tyndp",
        name="ENTSO-E TYNDP 2026 project portfolio",
        plan_name="ENTSO-E Ten-Year Network Development Plan 2026",
        landing_url=("https://www.entsoe.eu/news/2026/04/02/tyndp-2026-project-portfolio-"
                     "expands-further-to-199-transmission-and-69-storage-project/"),
        download_url=("https://eepublicdownloads.blob.core.windows.net/public-cdn-container/"
                      "tyndp-documents/TYNDP2026/20260203_TYNDP2026_project_portfolio_final.xlsx"),
        licence=("ENTSO-E site terms (read 2026-11-16): content may be downloaded only for "
                 "non-commercial, personal use; reuse, derivative works or redistribution "
                 "need ENTSO-E's written permission. Owner decision 2026-11-16: private "
                 "use, flag OFF by default; off or licensed before any public launch."),
        use_constraint=USE_PRIVATE_USE_PERMITTED,
        attribution="Source: ENTSO-E, TYNDP 2026 project portfolio (draft).",
        flag="v3_grid_plans_entsoe_enabled",
    ),
}

_TAGS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("hvdc", re.compile(r"\bHVDC\b|direct[- ]current", re.I)),
    ("overhead_line", re.compile(r"\bOHL\b|overhead[- ]line", re.I)),
    ("cable", re.compile(r"\bcables?\b|underground|subsea|submarine", re.I)),
    ("substation", re.compile(r"sub-?stations?", re.I)),
    ("transformer", re.compile(r"transformers?|\bPSTs?\b|phase[- ]shift", re.I)),
    ("reactive_compensation", re.compile(
        r"synchronous condensers?|\bSTATCOMs?\b|\bSVCs?\b|reactors?\b|capacitor banks?", re.I)),
    ("offshore", re.compile(r"offshore", re.I)),
    ("interconnector", re.compile(r"interconnect", re.I)),
    ("converter_station", re.compile(r"converter stations?", re.I)),
    ("storage", re.compile(r"storage|\bBESS\b|batter(?:y|ies)", re.I)),
    ("pumped_hydro", re.compile(r"pumped[- ]?(?:hydro|storage)|\bPHES\b|\bHPS\b", re.I)),
)
_YEAR = re.compile(r"(?<!\d)(19[5-9]\d|20\d\d)(?!\d)")
_SPLIT = re.compile(r"\s*(?:[,;]|\band\b|&)\s*", re.I)
#: A comma before a legal form is part of the name ("Iberdrola, S.A.").
_LEGAL_SUFFIX = re.compile(
    r"^(?:S\.?L\.?U?|S\.?A\.?U?|S\.?p\.?A\.?|S\.?r\.?l\.?|Ltd\.?|Limited|GmbH|AG|SE|plc|"
    r"Inc\.?|LLC|L\.?P\.?|B\.?V\.?|N\.?V\.?|ApS|A/S|AS|ASA|AB|Oy|Oyj|EAD|EOOD|"
    r"S\.?M\.?S\.?A\.?|d\.?o\.?o\.?|a\.?s\.?|SAS|SARL|KG|Co\.?)$", re.I)


def technology_tags(*texts: str | None) -> list[str]:
    blob = " ".join(t for t in texts if t)
    return [tag for tag, rx in _TAGS if rx.search(blob)]


def max_voltage_kv(*texts: str | None) -> int | None:
    blob = " ".join(t for t in texts if t)
    values = [int(v) for m in re.finditer(r"\d{2,4}(?:\s?/\s?\d{2,4})*\s?kV\b", blob, re.I)
              for v in re.findall(r"\d{2,4}", m.group(0))]
    values = [v for v in values if 10 <= v <= 1200]
    return max(values) if values else None


def status_class(text: str | None) -> str:
    t = (text or "").lower()
    if not t:
        return "other"
    if "withdrawn" in t or "cancel" in t or "removed" in t:
        return "withdrawn"
    if "commissioned" in t or "completed" in t or "in-service" in t or "in service" in t:
        return "commissioned"
    if "under construction" in t or "construction started" in t:
        return "under_construction"
    if "committed" in t:
        return "committed"
    if "not yet permitting" in t or "not permitting" in t or "planned" in t \
            or "planning" in t or "anticipated" in t or "actionable" in t:
        return "planned"
    if "permitting" in t:
        return "permitting"
    if "consideration" in t or "future" in t or "proposed" in t:
        return "under_consideration"
    return "other"


def commissioning_year(text: str | None) -> int | None:
    m = _YEAR.search(text or "")
    return int(m.group(1)) if m else None


def split_promoters(text: str | None) -> list[str]:
    names: list[str] = []
    for piece in _SPLIT.split(text or ""):
        piece = piece.strip()
        if names and _LEGAL_SUFFIX.match(piece):
            names[-1] = f"{names[-1]}, {piece}"
        elif piece:
            names.append(piece)
    out: list[str] = []
    for n in (x.strip(" ,") for x in names):
        if len(n) >= 2 and n.lower() not in {x.lower() for x in out}:
            out.append(n[:200])
    return out


__all__ = ["BASE_VERSION", "SOURCES", "GridSource", "commissioning_year", "max_voltage_kv",
           "split_promoters", "status_class", "technology_tags"]
