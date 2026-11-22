"""Ember yearly electricity data — V3.21.4 (ADR-111).

WHAT IT IS
==========
Ember's "Yearly electricity data" is ONE public CSV (``files.ember-energy.org``), keyless,
licensed CC BY 4.0: generation by fuel (TWh), share of generation (%), installed capacity
(GW) and demand (TWh), per country and year. Verified live on 2026-11-22: 16.1 MB
uncompressed, ~104k rows, header::

    Area,ISO 3 code,Year,Area type,Electricity source,Is aggregated source,
    Generation (TWh),...,Share of generation (%),...,Capacity (GW),...

The file is WIDE (one column per measure), and a demand figure is the
``Generation (TWh)`` cell of the ``Demand`` row — read here as what it is, demand.

BOUNDED
=======
Fetched through the guarded fetcher, pinned to the one host, capped at
:data:`MAX_BYTES`. A truncated file is REFUSED, not parsed: a CSV cut mid-way reads as a
shorter, wrong one. Only the configured countries and the last :data:`YEARS_KEPT` years
are kept.

VINTAGE
=======
The file's ``Last-Modified`` header — the publisher's own time for this release. Without
it, the vintage is the start of the year after the latest year the file covers (never
later than now), and the basis says so.
"""

from __future__ import annotations

import csv
import io
import re
from collections.abc import Iterable
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any

from app.services.industry_data.common import (
    SOURCE_EMBER,
    YEARS_KEPT,
    IndustryDataset,
    IndustryObservation,
    IndustryRelease,
    parse_float,
)
from app.services.macro.definitions import FREQ_ANNUAL, DatasetSpec, SeriesSpec

EMBER_HOST = "files.ember-energy.org"
EMBER_URL = (
    f"https://{EMBER_HOST}/public-downloads/generation/outputs/"
    "release_generation_yearly_global.csv"
)
#: The live file is ~16 MB; the cap leaves room for growth and stays under the fetcher's
#: own 35 MB ceiling (which this can only narrow).
MAX_BYTES = 30_000_000

EMBER_DATASET = IndustryDataset(
    spec=DatasetSpec(
        dataset_key="ember:yearly_electricity",
        source_id=SOURCE_EMBER,
        display_name="Ember, Yearly Electricity Data",
        source_url="https://ember-energy.org/data/yearly-electricity-data/",
        licence="CC BY 4.0",
        update_cadence="annual, revised in-year",
        access_class="public_official",
    ),
    attribution="Ember, Yearly Electricity Data (CC BY 4.0), ember-energy.org",
)

#: Ember's electricity-source names kept, and their series slug. ``Net imports`` and the
#: ``Clean`` aggregate are not kept: neither is a generation mix share a report reads.
SOURCE_SLUGS: dict[str, str] = {
    "Total generation": "total_generation",
    "Coal": "coal",
    "Gas": "gas",
    "Other fossil": "other_fossil",
    "Nuclear": "nuclear",
    "Hydro": "hydro",
    "Wind": "wind",
    "Solar": "solar",
    "Bioenergy": "bioenergy",
    "Other renewables": "other_renewables",
    "Wind and solar": "wind_and_solar",
    "Renewables": "renewables",
    "Fossil": "fossil",
}
#: (column, measure slug, unit). Applied to every kept source row.
MEASURES: tuple[tuple[str, str, str], ...] = (
    ("Generation (TWh)", "generation_twh", "TWh"),
    ("Share of generation (%)", "share_pct", "%"),
    ("Capacity (GW)", "capacity_gw", "GW"),
)
DEMAND_SOURCE = "Demand"
REQUIRED_COLUMNS: tuple[str, ...] = (
    "Area", "ISO 3 code", "Year", "Area type", "Electricity source",
    "Generation (TWh)", "Share of generation (%)", "Capacity (GW)",
)
COUNTRY_AREA_TYPE = "Country or economy"


def series_key(iso3: str, measure: str, slug: str | None = None) -> str:
    return f"{iso3}.{measure}" + (f".{slug}" if slug else "")


def _series(iso3: str, area: str, measure: str, unit: str, source: str,
            column: str) -> SeriesSpec:
    slug = None if measure == "demand_twh" else SOURCE_SLUGS[source]
    label = "Electricity demand" if measure == "demand_twh" else {
        "generation_twh": f"Electricity generation — {source}",
        "share_pct": f"Share of electricity generation — {source}",
        "capacity_gw": f"Installed capacity — {source}",
    }[measure]
    return SeriesSpec(
        series_key=series_key(iso3, measure, slug),
        display_name=f"{label} ({area})"[:300],
        unit=unit,
        frequency=FREQ_ANNUAL,
        indicator_code=f"{column}|{source}"[:120],
        geography=iso3,
    )


def vintage_from_last_modified(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if parsed is None:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def parse_ember_csv(
    body: str,
    *,
    countries: Iterable[str],
    source_ref: str = EMBER_URL,
    last_modified: str | None = None,
    now: datetime | None = None,
) -> IndustryRelease:
    """The wide CSV → annual observations for the wanted countries. Never raises."""
    release = IndustryRelease(source_id=SOURCE_EMBER, dataset=EMBER_DATASET)
    wanted = {c.upper() for c in countries}
    reader = csv.reader(io.StringIO(body))
    try:
        header = next(reader)
    except (StopIteration, csv.Error):
        release.skipped.append("the response had no CSV header")
        return release
    index = {name.strip(): i for i, name in enumerate(header)}
    missing = [c for c in REQUIRED_COLUMNS if c not in index]
    if missing:
        release.skipped.append(
            f"the CSV header no longer has {', '.join(missing)}; nothing stored")
        return release

    rows: list[list[str]] = []
    try:
        for row in reader:
            if len(row) < len(header):
                continue
            if row[index["Area type"]].strip() != COUNTRY_AREA_TYPE:
                continue
            if row[index["ISO 3 code"]].strip().upper() in wanted:
                rows.append(row)
    except csv.Error:
        release.skipped.append("the CSV could not be read to the end; nothing stored")
        return release

    latest_year: dict[str, int] = {}
    for row in rows:
        year = row[index["Year"]].strip()
        if re.fullmatch(r"\d{4}", year):
            iso = row[index["ISO 3 code"]].strip().upper()
            latest_year[iso] = max(latest_year.get(iso, 0), int(year))

    for row in rows:
        iso = row[index["ISO 3 code"]].strip().upper()
        year = row[index["Year"]].strip()
        if not re.fullmatch(r"\d{4}", year):
            release.skipped.append(f"{iso}: unreadable year {year[:10]!r}")
            continue
        if int(year) <= latest_year[iso] - YEARS_KEPT:
            continue
        area = row[index["Area"]].strip()
        source = row[index["Electricity source"]].strip()
        if source == DEMAND_SOURCE:
            release.observations.append(IndustryObservation(
                series=_series(iso, area, "demand_twh", "TWh", source, "Generation (TWh)"),
                period_key=year,
                value=parse_float(row[index["Generation (TWh)"]]),
                source_ref=source_ref,
            ))
            continue
        if source not in SOURCE_SLUGS:
            continue
        for column, measure, unit in MEASURES:
            raw = row[index[column]]
            if measure == "capacity_gw" and not raw.strip():
                continue  # Ember publishes no capacity for this source; not a NULL year
            release.observations.append(IndustryObservation(
                series=_series(iso, area, measure, unit, source, column),
                period_key=year,
                value=parse_float(raw),
                source_ref=source_ref,
            ))

    for code in sorted(wanted - set(latest_year)):
        release.skipped.append(f"{code}: not in the Ember file")
    stamp = vintage_from_last_modified(last_modified)
    if stamp is not None:
        release.vintage_at, release.vintage_basis = stamp, "publisher_last_modified"
    elif latest_year:
        derived = datetime(max(latest_year.values()) + 1, 1, 1, tzinfo=timezone.utc)
        release.vintage_at = min(derived, now or datetime.now(timezone.utc))
        release.vintage_basis = "period_derived"
    return release


async def fetch_ember(
    *, cfg: Any, countries: Iterable[str], fetcher: Any = None,
) -> IndustryRelease:
    """One bounded download of the yearly CSV, parsed for ``countries``. Never raises."""
    release = IndustryRelease(source_id=SOURCE_EMBER, dataset=EMBER_DATASET)
    if not getattr(cfg, "v3_industry_data_ember_enabled", False):
        release.inert_reason = "V3_INDUSTRY_DATA_EMBER_ENABLED is off"
        return release
    from app.services.sources.document_fetcher import safe_fetch_document

    release.fetch_attempts = 1
    try:
        result = await (fetcher or safe_fetch_document)(
            EMBER_URL,
            allowed_domains=(EMBER_HOST,),
            cfg=cfg,
            resolve_ip=True,
            extra_text_content_types=("text/csv", "application/csv"),
            max_bytes=MAX_BYTES,
        )
    except Exception as exc:  # noqa: BLE001 - a publisher failure is a gap
        release.skipped.append(f"Ember fetch failed ({type(exc).__name__})")
        return release
    if getattr(result, "truncated", False):
        release.skipped.append(
            f"the Ember file exceeded {MAX_BYTES} bytes and was refused, not parsed")
        return release
    content = getattr(result, "content", None) if getattr(result, "ok", False) else None
    if not content:
        release.skipped.append(
            f"Ember unreachable: {getattr(result, 'error', None) or 'no body'}")
        return release
    parsed = parse_ember_csv(
        content.decode("utf-8", "replace"), countries=countries,
        last_modified=getattr(result, "last_modified", None))
    parsed.fetch_attempts = 1
    return parsed


__all__ = [
    "EMBER_DATASET",
    "EMBER_HOST",
    "EMBER_URL",
    "MAX_BYTES",
    "SOURCE_SLUGS",
    "fetch_ember",
    "parse_ember_csv",
    "series_key",
    "vintage_from_last_modified",
]
