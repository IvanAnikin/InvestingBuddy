"""IRENA IRENASTAT, through its PxWeb API — V3.21.4 (ADR-111).

WHAT IT IS
==========
IRENA's statistics database (``pxweb.irena.org``) serves the "Electricity statistics by
Country/area, Technology, Data Type, Grid connection and Year" table through the PxWeb
v1 API: keyless, JSON, IRENA terms (free use with attribution). Verified live on
2026-11-22. Three bounded requests:

1. ``GET`` the folder listing — the table id changes with each edition
   (``Country_ELECSTAT_2026_H2_PX.px``) and the listing's ``updated`` is the edition's
   publication time, which is the vintage;
2. ``GET`` the table's metadata — the codes of countries, technologies, data types and
   years (``"4"`` is "Solar photovoltaic"; codes are positional, so they are READ, never
   hard-coded);
3. ``POST`` one json-stat2 query: the configured countries × the kept technologies ×
   both data types × grid "All" × the last :data:`YEARS_KEPT` years (~2k cells).

A cell IRENA marks ``..`` is NULL, never 0. The table id is checked against a strict
pattern before it is put in a URL, so nothing the listing says can choose another path.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

from app.services.industry_data.common import (
    SOURCE_IRENA,
    YEARS_KEPT,
    IndustryDataset,
    IndustryObservation,
    IndustryRelease,
    parse_float,
)
from app.services.macro.definitions import FREQ_ANNUAL, DatasetSpec, SeriesSpec

IRENA_HOST = "pxweb.irena.org"
FOLDER_URL = f"https://{IRENA_HOST}/api/v1/en/IRENASTAT/Power%20Capacity%20and%20Generation"
_TABLE_ID_RE = re.compile(r"^Country_ELECSTAT_[A-Za-z0-9_]{1,40}\.px$")
MAX_JSON_BYTES = 2_000_000

IRENA_DATASET = IndustryDataset(
    spec=DatasetSpec(
        dataset_key="irena:irenastat_electricity",
        source_id=SOURCE_IRENA,
        display_name="IRENA, IRENASTAT Electricity statistics (Power Capacity and Generation)",
        source_url="https://pxweb.irena.org/pxweb/en/IRENASTAT",
        licence="IRENA terms of use: free use with attribution",
        update_cadence="twice yearly",
        access_class="public_official",
    ),
    attribution=(
        "IRENA (2026), Renewable Energy Statistics, International Renewable Energy "
        "Agency, Abu Dhabi (IRENASTAT)"
    ),
)

#: IRENA technology label → series slug. Read by LABEL from the table's metadata.
TECHNOLOGIES: dict[str, str] = {
    "Total Renewable": "total_renewable",
    "Solar photovoltaic": "solar_pv",
    "Onshore wind energy": "onshore_wind",
    "Offshore wind energy": "offshore_wind",
    "Renewable hydropower": "renewable_hydropower",
    "Mechanical storage (Pumped hydro)": "pumped_hydro_storage",
}
#: IRENA data-type label → (measure slug, unit).
DATA_TYPES: dict[str, tuple[str, str]] = {
    "Electricity Generation (GWh)": ("generation_gwh", "GWh"),
    "Electrical Installed Capacity (MW)": ("capacity_mw", "MW"),
}
GRID_ALL = "All"
DIM_COUNTRY, DIM_TECH, DIM_TYPE, DIM_GRID, DIM_YEAR = (
    "Country/area", "Technology", "Data Type", "Grid connection", "Year")


def series_key(iso3: str, measure: str, slug: str) -> str:
    return f"{iso3}.{measure}.{slug}"


def table_url(table_id: str) -> str | None:
    """The table's URL, or ``None`` for an id that is not an IRENA country table id."""
    if not _TABLE_ID_RE.match(table_id or ""):
        return None
    return f"{FOLDER_URL}/{quote(table_id)}"


def _aware(text: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_table_listing(body: str) -> tuple[str | None, datetime | None, list[str]]:
    """The newest country electricity table: ``(table_id, updated, notes)``."""
    notes: list[str] = []
    try:
        items = json.loads(body)
    except (TypeError, ValueError):
        return None, None, ["the IRENA listing was not JSON"]
    best: tuple[datetime, str] | None = None
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, Mapping) or item.get("type") != "t":
            continue
        table_id = str(item.get("id") or "")
        updated = _aware(str(item.get("updated") or ""))
        if not _TABLE_ID_RE.match(table_id) or updated is None:
            continue
        if best is None or updated > best[0]:
            best = (updated, table_id)
    if best is None:
        notes.append("no Country_ELECSTAT table in the IRENA listing")
        return None, None, notes
    return best[1], best[0], notes


def parse_metadata(body: str) -> dict[str, dict[str, str]]:
    """``{variable code: {value text: value code}}`` from a PxWeb table's metadata."""
    try:
        meta = json.loads(body)
    except (TypeError, ValueError):
        return {}
    out: dict[str, dict[str, str]] = {}
    for var in meta.get("variables") or [] if isinstance(meta, Mapping) else []:
        if not isinstance(var, Mapping):
            continue
        codes, texts = var.get("values") or [], var.get("valueTexts") or []
        out[str(var.get("code"))] = {str(t): str(c) for c, t in zip(codes, texts, strict=False)}
    return out


def build_query(
    meta: Mapping[str, Mapping[str, str]], *, countries: Iterable[str],
) -> tuple[dict[str, Any] | None, list[str]]:
    """The bounded json-stat2 query, or ``(None, reasons)`` when the table changed shape."""
    notes: list[str] = []
    needed = (DIM_COUNTRY, DIM_TECH, DIM_TYPE, DIM_GRID, DIM_YEAR)
    absent = [d for d in needed if d not in meta]
    if absent:
        return None, [f"the IRENA table no longer has {', '.join(absent)}"]
    country_codes = set(meta[DIM_COUNTRY].values())
    wanted = [c.upper() for c in countries]
    selected = [c for c in wanted if c in country_codes]
    notes.extend(f"{c}: not in the IRENA table" for c in wanted if c not in country_codes)
    techs = [meta[DIM_TECH][t] for t in TECHNOLOGIES if t in meta[DIM_TECH]]
    notes.extend(f"technology {t!r} not in the IRENA table" for t in TECHNOLOGIES
                 if t not in meta[DIM_TECH])
    types = [meta[DIM_TYPE][t] for t in DATA_TYPES if t in meta[DIM_TYPE]]
    grid = meta[DIM_GRID].get(GRID_ALL)
    years = sorted(meta[DIM_YEAR].items(), key=lambda kv: kv[0])[-YEARS_KEPT:]
    if not selected or not techs or not types or grid is None or not years:
        return None, [*notes, "nothing to ask the IRENA table for"]

    def _item(code: str, values: list[str]) -> dict[str, Any]:
        return {"code": code, "selection": {"filter": "item", "values": values}}

    return {
        "query": [
            _item(DIM_COUNTRY, selected),
            _item(DIM_TECH, techs),
            _item(DIM_TYPE, types),
            _item(DIM_GRID, [grid]),
            _item(DIM_YEAR, [code for _text, code in years]),
        ],
        "response": {"format": "json-stat2"},
    }, notes


def parse_jsonstat(body: str, *, source_ref: str) -> tuple[list[IndustryObservation], list[str]]:
    """A json-stat2 dataset → observations. Never raises; a misshapen body is a note."""
    notes: list[str] = []
    try:
        data = json.loads(body)
        ids: list[str] = list(data["id"])
        sizes: list[int] = [int(s) for s in data["size"]]
        dims = data["dimension"]
        values = data["value"]
    except (TypeError, ValueError, KeyError):
        return [], ["the IRENA response was not a json-stat2 dataset"]
    if any(d not in ids for d in (DIM_COUNTRY, DIM_TECH, DIM_TYPE, DIM_YEAR)):
        return [], ["the IRENA response is missing a dimension"]
    expected = 1
    for size in sizes:
        expected *= size
    if not isinstance(values, list) or len(values) != expected:
        return [], ["the IRENA response's value count does not match its dimensions"]

    def _ordered(dim: str) -> list[tuple[str, str]]:
        cat = dims[dim]["category"]
        index = cat["index"]
        codes = (sorted(index, key=lambda k: index[k]) if isinstance(index, Mapping)
                 else list(index))
        return [(c, str(cat.get("label", {}).get(c, c))) for c in codes]

    axes = [_ordered(d) for d in ids]
    strides = [1] * len(sizes)
    for i in range(len(sizes) - 2, -1, -1):
        strides[i] = strides[i + 1] * sizes[i + 1]
    out: list[IndustryObservation] = []
    pos = {d: ids.index(d) for d in ids}
    for flat, raw in enumerate(values):
        coords = [(flat // strides[i]) % sizes[i] for i in range(len(sizes))]
        country_code, country_label = axes[pos[DIM_COUNTRY]][coords[pos[DIM_COUNTRY]]]
        _t, tech_label = axes[pos[DIM_TECH]][coords[pos[DIM_TECH]]]
        _d, type_label = axes[pos[DIM_TYPE]][coords[pos[DIM_TYPE]]]
        _y, year = axes[pos[DIM_YEAR]][coords[pos[DIM_YEAR]]]
        slug, kind = TECHNOLOGIES.get(tech_label), DATA_TYPES.get(type_label)
        if slug is None or kind is None or not re.fullmatch(r"\d{4}", year):
            continue
        measure, unit = kind
        iso3 = country_code.upper()
        out.append(IndustryObservation(
            series=SeriesSpec(
                series_key=series_key(iso3, measure, slug),
                display_name=f"{type_label.split(' (')[0]} — {tech_label} ({country_label})"[:300],
                unit=unit,
                frequency=FREQ_ANNUAL,
                indicator_code=f"{type_label}|{tech_label}"[:120],
                geography=iso3,
            ),
            period_key=year,
            value=parse_float(raw),
            source_ref=source_ref,
        ))
    return out, notes


async def fetch_irena(
    *, cfg: Any, countries: Iterable[str], fetcher: Any = None, poster: Any = None,
) -> IndustryRelease:
    """Listing → metadata → one bounded query. Never raises."""
    release = IndustryRelease(source_id=SOURCE_IRENA, dataset=IRENA_DATASET)
    if not getattr(cfg, "v3_industry_data_irena_enabled", False):
        release.inert_reason = "V3_INDUSTRY_DATA_IRENA_ENABLED is off"
        return release
    from app.services.sources.document_fetcher import safe_fetch_document, safe_post_json

    get = fetcher or safe_fetch_document
    post = poster or safe_post_json
    wanted = list(countries)

    async def _get(url: str) -> str | None:
        release.fetch_attempts += 1
        try:
            result = await get(url, allowed_domains=(IRENA_HOST,), cfg=cfg, resolve_ip=True,
                               extra_text_content_types=("application/json",),
                               max_bytes=MAX_JSON_BYTES)
        except Exception as exc:  # noqa: BLE001
            release.skipped.append(f"IRENA request failed ({type(exc).__name__})")
            return None
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        if not content or getattr(result, "truncated", False):
            release.skipped.append(
                f"IRENA unreachable: {getattr(result, 'error', None) or 'no body'}")
            return None
        return bytes(content).decode("utf-8", "replace")

    listing = await _get(FOLDER_URL)
    if listing is None:
        return release
    table_id, updated, notes = parse_table_listing(listing)
    release.skipped.extend(notes)
    url = table_url(table_id or "")
    if url is None:
        return release
    meta_body = await _get(url)
    if meta_body is None:
        return release
    query, notes = build_query(parse_metadata(meta_body), countries=wanted)
    release.skipped.extend(notes)
    if query is None:
        return release
    release.fetch_attempts += 1
    try:
        result = await post(url, query, allowed_domains=(IRENA_HOST,), cfg=cfg,
                            max_bytes=MAX_JSON_BYTES)
    except Exception as exc:  # noqa: BLE001
        release.skipped.append(f"IRENA query failed ({type(exc).__name__})")
        return release
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        release.skipped.append(
            f"IRENA query unanswered: {getattr(result, 'error', None) or 'no body'}")
        return release
    observations, notes = parse_jsonstat(bytes(content).decode("utf-8", "replace"),
                                         source_ref=url)
    release.observations, release.vintage_at = observations, updated
    release.vintage_basis = "publisher_updated"
    release.skipped.extend(notes)
    return release


__all__ = [
    "DATA_TYPES",
    "FOLDER_URL",
    "IRENA_DATASET",
    "IRENA_HOST",
    "TECHNOLOGIES",
    "build_query",
    "fetch_irena",
    "parse_jsonstat",
    "parse_metadata",
    "parse_table_listing",
    "series_key",
    "table_url",
]
