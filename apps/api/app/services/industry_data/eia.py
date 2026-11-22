"""US EIA API v2 — V3.21.4 (ADR-111).

WHAT IT IS
==========
``api.eia.gov/v2`` — US Energy Information Administration statistics, public domain (US
Government work), JSON. It needs a free API key (``EIA_API_KEY``). Without one this
source is **inert and says so**: it makes no request. Verified live on 2026-11-22 with the
public probing key, which is never a default here. Three bounded, aggregated requests,
all annual, US total:

* ``electricity/retail-sales`` — sales to ultimate customers (million kWh) and the
  average retail price (cents per kWh), all sectors;
* ``electricity/electric-power-operational-data`` — net generation by fuel (thousand
  MWh), all sectors;
* ``electricity/state-electricity-profiles/capability`` — net summer capacity by energy
  source (MW), batteries included.

Form 860M's planned additions (``operating-generator-capacity``) are generator-level
rows with no server-side aggregation, so a national total would mean paging tens of
thousands of rows; not built here, recorded as a gap (ADR-111).

THE KEY NEVER LEAVES THE REQUEST
================================
The key is put in the request URL's query string and nowhere else: the ``source_ref``
stored with every observation is built from the SAME parameters WITHOUT the key (never by
redacting a URL that had it), every error message names an exception type or an HTTP
status and never a URL, and the guarded fetcher records only secret-stripped URLs. The
platform caps the ``httpx`` logger at WARNING, so the request line is never emitted.
Tests assert the key appears in none of these.

VINTAGE
=======
The API states no release time. The vintage is the UTC day the platform first saw the
value (``first_seen``); a later fetch with the same value writes nothing, and a revised
value is a new row at the day it was first seen.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlencode

from app.services.industry_data.common import (
    SOURCE_EIA,
    YEARS_KEPT,
    IndustryDataset,
    IndustryObservation,
    IndustryRelease,
    parse_float,
)
from app.services.macro.definitions import FREQ_ANNUAL, DatasetSpec, SeriesSpec

EIA_HOST = "api.eia.gov"
EIA_BASE = f"https://{EIA_HOST}/v2"
MAX_JSON_BYTES = 2_000_000
#: The most rows one request asks for. Each route here returns < 100 for six years.
PAGE_LENGTH = 500

EIA_DATASET = IndustryDataset(
    spec=DatasetSpec(
        dataset_key="eia:electricity_v2",
        source_id=SOURCE_EIA,
        display_name="U.S. Energy Information Administration, API v2 (electricity)",
        source_url="https://www.eia.gov/opendata/",
        licence="Public domain (U.S. Government work)",
        update_cadence="monthly / annual",
        access_class="public_official",
    ),
    attribution="U.S. Energy Information Administration (EIA), eia.gov",
)


@dataclass(frozen=True)
class EiaRoute:
    key: str
    path: str
    #: ``(data field, measure slug, label)``
    fields: tuple[tuple[str, str, str], ...]
    facets: tuple[tuple[str, tuple[str, ...]], ...]
    #: The response field whose value names the series' sub-key (a fuel), or None.
    split_field: str | None
    split_label_field: str | None = None


ROUTES: tuple[EiaRoute, ...] = (
    EiaRoute(
        key="retail_sales",
        path="electricity/retail-sales",
        fields=(("sales", "retail_sales", "Retail electricity sales, all sectors"),
                ("price", "retail_price", "Average retail electricity price, all sectors")),
        facets=(("stateid", ("US",)), ("sectorid", ("ALL",))),
        split_field=None,
    ),
    EiaRoute(
        key="net_generation",
        path="electricity/electric-power-operational-data",
        fields=(("generation", "net_generation", "Net generation"),),
        facets=(("location", ("US",)), ("sectorid", ("99",)),
                ("fueltypeid", ("ALL", "COW", "NG", "NUC", "SUN", "WND", "HYC"))),
        split_field="fueltypeid",
        split_label_field="fuelTypeDescription",
    ),
    EiaRoute(
        key="capacity",
        path="electricity/state-electricity-profiles/capability",
        fields=(("capability", "capacity_mw", "Net summer capacity"),),
        facets=(("stateId", ("US",)), ("producertypeid", ("TOT",)),
                ("energysourceid", ("ALL", "BAT", "SOL", "WND", "NG", "COL", "NUC", "HPS"))),
        split_field="energysourceid",
        split_label_field="energySourceDescription",
    ),
)


def _params(route: EiaRoute, *, start_year: int) -> list[tuple[str, str]]:
    params: list[tuple[str, str]] = [("frequency", "annual")]
    params += [(f"data[{i}]", f) for i, (f, _m, _l) in enumerate(route.fields)]
    for facet, values in route.facets:
        params += [(f"facets[{facet}][]", v) for v in values]
    params += [("start", str(start_year)), ("sort[0][column]", "period"),
               ("sort[0][direction]", "desc"), ("length", str(PAGE_LENGTH))]
    return params


def public_url(route: EiaRoute, *, start_year: int) -> str:
    """The request WITHOUT the key — what a citation and every log may show."""
    return f"{EIA_BASE}/{route.path}/data/?{urlencode(_params(route, start_year=start_year))}"


def request_url(route: EiaRoute, *, start_year: int, api_key: str) -> str:
    """The request WITH the key. Passed to the fetcher and nowhere else."""
    params = [("api_key", api_key), *_params(route, start_year=start_year)]
    return f"{EIA_BASE}/{route.path}/data/?{urlencode(params)}"


def _slug(text: str) -> str:
    return "".join(ch if ch.isalnum() else "_" for ch in text.lower()).strip("_")[:40]


def parse_eia_response(body: str, *, route: EiaRoute, source_ref: str
                       ) -> tuple[list[IndustryObservation], list[str]]:
    """``response.data[]`` → annual observations. A row without a unit is NOT stored."""
    notes: list[str] = []
    try:
        payload = json.loads(body)
        rows = payload["response"]["data"]
    except (TypeError, ValueError, KeyError):
        return [], [f"EIA {route.key}: the response was not an API v2 data response"]
    if not isinstance(rows, list):
        return [], [f"EIA {route.key}: no data rows"]
    out: list[IndustryObservation] = []
    for row in rows:
        if not isinstance(row, Mapping):
            continue
        period = str(row.get("period") or "")
        if not (len(period) == 4 and period.isdigit()):
            notes.append(f"EIA {route.key}: unreadable period {period[:10]!r}")
            continue
        split = str(row.get(route.split_field) or "") if route.split_field else "all_sectors"
        if not split:
            continue
        split_label = (str(row.get(route.split_label_field) or split)
                       if route.split_label_field else "all sectors")
        for field_name, measure, label in route.fields:
            unit = str(row.get(f"{field_name}-units") or "").strip()
            if not unit:
                notes.append(f"EIA {route.key}: {field_name} {period} has no unit; not stored")
                continue
            out.append(IndustryObservation(
                series=SeriesSpec(
                    series_key=f"USA.{measure}.{_slug(split)}",
                    display_name=f"{label} — {split_label} (United States)"[:300],
                    unit=unit[:60],
                    frequency=FREQ_ANNUAL,
                    indicator_code=f"{route.path}|{field_name}|{split}"[:120],
                    geography="USA",
                ),
                period_key=period,
                value=parse_float(row.get(field_name)),
                source_ref=source_ref,
            ))
    return out, notes


def _first_seen(now: datetime) -> datetime:
    now = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    return now.astimezone(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)


async def fetch_eia(
    *, cfg: Any, fetcher: Any = None, now: datetime | None = None,
) -> IndustryRelease:
    """The three routes. Inert (no request) without ``EIA_API_KEY``. Never raises."""
    release = IndustryRelease(source_id=SOURCE_EIA, dataset=EIA_DATASET)
    if not getattr(cfg, "v3_industry_data_eia_enabled", False):
        release.inert_reason = "V3_INDUSTRY_DATA_EIA_ENABLED is off"
        return release
    api_key = str(getattr(cfg, "eia_api_key", "") or "").strip()
    if not api_key:
        release.inert_reason = (
            "EIA_API_KEY is not set; the EIA source is inert and fetched nothing")
        return release
    from app.services.sources.document_fetcher import safe_fetch_document

    get = fetcher or safe_fetch_document
    stamp = now or datetime.now(timezone.utc)
    start_year = stamp.year - YEARS_KEPT
    for route in ROUTES:
        release.fetch_attempts += 1
        ref = public_url(route, start_year=start_year)
        try:
            result = await get(
                request_url(route, start_year=start_year, api_key=api_key),
                allowed_domains=(EIA_HOST,), cfg=cfg, resolve_ip=True,
                extra_text_content_types=("application/json",), max_bytes=MAX_JSON_BYTES)
        except Exception as exc:  # noqa: BLE001 - the message could carry the URL; not read
            release.skipped.append(f"EIA {route.key}: request failed ({type(exc).__name__})")
            continue
        content = getattr(result, "content", None) if getattr(result, "ok", False) else None
        if not content or getattr(result, "truncated", False):
            status = getattr(result, "status_code", None)
            release.skipped.append(
                f"EIA {route.key}: no usable answer"
                + (f" (http {int(status)})" if isinstance(status, int) else ""))
            continue
        observations, notes = parse_eia_response(
            bytes(content).decode("utf-8", "replace"), route=route, source_ref=ref)
        release.observations.extend(observations)
        release.skipped.extend(notes)
    if release.observations:
        release.vintage_at, release.vintage_basis = _first_seen(stamp), "first_seen"
    return release


__all__ = [
    "EIA_DATASET",
    "EIA_HOST",
    "ROUTES",
    "EiaRoute",
    "fetch_eia",
    "parse_eia_response",
    "public_url",
    "request_url",
]
