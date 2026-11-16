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

from app.services.web_research.source_policy import (
    USE_OPEN_LICENCE,
    USE_PRIVATE_USE_PERMITTED,
)

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
    #: The edition of the pinned file (``--edition`` overrides it).
    default_edition: str
    #: Host suffixes the pinned URL may redirect to ONCE (the publisher's own file store,
    #: e.g. a signed object-storage link). Empty = no redirect is followed.
    redirect_hosts: tuple[str, ...] = ()


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
        default_edition="2026-02-03",
    ),
    "aemo_tai": GridSource(
        key="aemo_tai",
        name="AEMO NEM Transmission Augmentation Information (August 2026)",
        plan_name="AEMO Integrated System Plan / NEM transmission augmentations",
        landing_url=("https://www.aemo.com.au/energy-systems/electricity/national-electricity-"
                     "market-nem/nem-forecasting-and-planning/forecasting-and-planning-data/"
                     "transmission-augmentation-information"),
        download_url=("https://www.aemo.com.au/-/media/files/electricity/nem/"
                      "planning_and_forecasting/transmission-augmentation-information/2026/"
                      "nem-transmission-augmentation-information-august-2026.xlsx"),
        licence=("AEMO copyright permissions (read 2026-11-16): general permission for "
                 "anyone to use AEMO Material for any purpose, with accurate attribution of "
                 "the material and AEMO as its author. The workbook says it may be used under "
                 "those permissions."),
        use_constraint=USE_OPEN_LICENCE,
        attribution=("Source: Australian Energy Market Operator (AEMO), NEM Transmission "
                     "Augmentation Information, August 2026."),
        flag="v3_grid_plans_aemo_enabled",
        default_edition="2026-08",
    ),
    "lbl_queued_up": GridSource(
        key="lbl_queued_up",
        name="Berkeley Lab Queued Up 2026 data file (interconnection queues through 2025)",
        plan_name="US interconnection queues (Berkeley Lab Queued Up)",
        landing_url="https://emp.lbl.gov/queues",
        download_url=("https://emp.lbl.gov/sites/default/files/2026-05/"
                      "LBNL_Ix_Queue_Data_File_thru2025.xlsx"),
        licence=("CC BY 4.0 (stated on emp.lbl.gov/queues, read 2026-11-16): use, share or "
                 "adapt with attribution to Lawrence Berkeley National Laboratory and "
                 "GridTracker."),
        use_constraint=USE_OPEN_LICENCE,
        attribution=("Source: Lawrence Berkeley National Laboratory and GridTracker, Queued Up "
                     "2026 data file (CC BY 4.0)."),
        flag="v3_grid_plans_lbl_enabled",
        default_edition="2026-05",
    ),
    "neso_tec": GridSource(
        key="neso_tec",
        name="NESO Transmission Entry Capacity (TEC) register",
        plan_name="Great Britain transmission connections (NESO TEC register)",
        landing_url="https://www.neso.energy/data-portal/transmission-entry-capacity-tec-register",
        download_url=("https://api.neso.energy/dataset/cbd45e54-e6e2-4a38-99f1-8de6fd96d7c1/"
                      "resource/17becbab-e3e8-473f-b303-3806f43a6a10/download/"
                      "tec-register-13-november-2026.csv"),
        licence=("NESO Open Data Licence v1.0 (based on the Open Government Licence v3.0; "
                 "read on neso.energy 2026-11-16): copy, adapt and use commercially or not, "
                 "with the attribution \"Supported by National Energy SO Open Data\"; no "
                 "implied endorsement. The dataset page names this licence."),
        use_constraint=USE_OPEN_LICENCE,
        attribution=("Supported by National Energy SO Open Data. Source: NESO, Transmission "
                     "Entry Capacity (TEC) register."),
        flag="v3_grid_plans_neso_enabled",
        default_edition="2026-11-13",
        # The portal answers 302 with a signed link to its Cloudflare R2 bucket.
        redirect_hosts=(".r2.cloudflarestorage.com",),
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
_SPLIT = re.compile(r"\s*(?:[,;]|\band\b|\btogether with\b|&)\s*", re.I)
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


_MONTH_YY = re.compile(
    r"\b(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*[- ](\d{2})(?!\d)", re.I)
_EXCEL_EPOCH_ORDINAL = 693594  # date(1899, 12, 30).toordinal()


def serial_to_iso(text: str | None) -> str | None:
    """A bare spreadsheet date serial ("46204") as an ISO date; anything else unchanged."""
    t = (text or "").strip()
    if re.fullmatch(r"\d{5}(?:\.0+)?", t) and 20000 <= int(float(t)) <= 80000:
        from datetime import date

        return date.fromordinal(_EXCEL_EPOCH_ORDINAL + int(float(t))).isoformat()
    return text


def commissioning_year(text: str | None) -> int | None:
    """The first year stated: "2032(6GWh)/2038" → 2032, "12-2027" → 2027, "Oct-26" →
    2026, and a bare spreadsheet date serial ("47452") → its year."""
    t = (text or "").strip()
    if re.fullmatch(r"\d{5}(?:\.0+)?", t):
        from datetime import date

        serial = int(float(t))
        if 20000 <= serial <= 80000:
            return date.fromordinal(_EXCEL_EPOCH_ORDINAL + serial).year
    hits = [(m.start(), int(m.group(1))) for m in _YEAR.finditer(t)]
    hits += [(m.start(), 2000 + int(m.group(1))) for m in _MONTH_YY.finditer(t)]
    return min(hits)[1] if hits else None


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
           "serial_to_iso", "split_promoters", "status_class", "technology_tags"]
