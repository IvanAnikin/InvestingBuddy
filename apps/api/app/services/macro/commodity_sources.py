"""Commodity statistics from public, keyless publishers — V3.18.5.

WHAT THIS CLOSES
================
The platform had five "macro" sources — FRED, IMF, the World Bank Pink Sheet, USGS,
IEA — registered as **reference-only**: each emitted a link and a gap, and none fetched a
number. So a report on a copper producer could say that copper prices "increased" and
nothing more. This module fetches the numbers, through the platform's own guarded
fetcher, into the existing macro observation store, with a vintage and a source on every
reading.

TWO SOURCES, CHOSEN FOR WHAT THEY ARE
=====================================
* **The IMF Primary Commodity Price System, as served by FRED** — monthly benchmark
  prices (copper, nickel, zinc, lead, tin, aluminium, iron ore, uranium). Public, keyless
  CSV. Tier T2 (an international official statistical body).
* **The U.S. Geological Survey's Mineral Commodity Summaries** — per commodity, world mine
  and refinery production and reserves by country, and the survey's price statistics.
  Public-domain U.S. government work. Tier T3 (a specialist statistical agency — the
  taxonomy's own example).

Both are parsed deterministically. A value the parser cannot read unambiguously is NOT
stored — and that includes the USGS tables' superscript footnote markers, which a text
extractor fuses into the numbers ("1145,500" is footnote 11 and 45,500). They are removed
by font size before any text is read, never guessed at afterwards.
"""

from __future__ import annotations

import asyncio
import csv
import io
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from app.services.macro.commodities import Commodity
from app.services.macro.definitions import FREQ_ANNUAL, FREQ_MONTHLY, DatasetSpec, SeriesSpec

FRED_HOST = "fred.stlouisfed.org"
USGS_HOST = "pubs.usgs.gov"

FRED_IMF_DATASET = DatasetSpec(
    dataset_key="imf:pcps_via_fred",
    source_id="imf_pcps_fred",
    display_name="IMF Primary Commodity Prices (via FRED, Federal Reserve Bank of St. Louis)",
    source_url="https://fred.stlouisfed.org",
    licence="Public data; IMF Primary Commodity Price System",
    update_cadence="monthly",
    access_class="public_official",
)
USGS_MCS_DATASET = DatasetSpec(
    dataset_key="usgs:mineral_commodity_summaries",
    source_id="usgs_mcs",
    display_name="U.S. Geological Survey, Mineral Commodity Summaries",
    source_url="https://www.usgs.gov/centers/national-minerals-information-center",
    licence="Public domain (U.S. Government work)",
    update_cadence="annual (February)",
    access_class="public_official",
)

FRED_TIER = "T2_regulator_or_gov"
USGS_TIER = "T3_industry_specialist"

#: How far back a price series is kept on each fetch. 36-month changes need 37 months.
MAX_MONTHS = 60


def fred_url(series: str) -> str:
    return f"https://{FRED_HOST}/graph/fredgraph.csv?id={series}"


def usgs_url(slug: str, year: int) -> str:
    return f"https://{USGS_HOST}/periodicals/mcs{year}/mcs{year}-{slug}.pdf"


@dataclass(frozen=True)
class ParsedObservation:
    series: SeriesSpec
    period_key: str
    value: float | None
    source_ref: str
    #: The publisher marked it an estimate ("2025e").
    estimated: bool = False


@dataclass
class ParsedRelease:
    """Everything one publication yielded, and everything it did not."""

    dataset: DatasetSpec
    observations: list[ParsedObservation] = field(default_factory=list)
    vintage_at: datetime | None = None
    unit_note: str | None = None
    skipped: list[str] = field(default_factory=list)


# ── FRED / IMF monthly prices ──────────────────────────────────────────────── #


def parse_fred_csv(body: str, *, commodity: Commodity, source_ref: str) -> ParsedRelease:
    """``observation_date,<SERIES>`` rows → monthly observations. Never raises.

    A period the publisher left blank (``.``) is recorded as NULL rather than dropped:
    "no price published for that month" is information.
    """
    release = ParsedRelease(dataset=FRED_IMF_DATASET)
    series = SeriesSpec(
        series_key=str(commodity.fred_series),
        display_name=f"Global price of {commodity.name} (IMF)",
        unit=commodity.fred_unit or "USD",
        frequency=FREQ_MONTHLY,
        indicator_code=commodity.fred_series,
        currency="USD",
        geography="WLD",
    )
    try:
        rows = list(csv.reader(io.StringIO(body)))
    except csv.Error:
        release.skipped.append("the response was not CSV")
        return release
    if not rows or len(rows[0]) < 2:
        release.skipped.append("no header row")
        return release
    for row in rows[1:][-MAX_MONTHS:]:
        if len(row) < 2:
            continue
        date_text, raw = row[0].strip(), row[1].strip()
        match = re.fullmatch(r"(\d{4})-(\d{2})-01", date_text)
        if not match:
            release.skipped.append(f"unreadable date {date_text[:20]!r}")
            continue
        value: float | None
        if raw in {"", "."}:
            value = None
        else:
            try:
                value = float(raw)
            except ValueError:
                release.skipped.append(f"unreadable value for {date_text}")
                continue
        release.observations.append(
            ParsedObservation(
                series=series,
                period_key=f"{match.group(1)}-{match.group(2)}",
                value=value,
                source_ref=source_ref,
            )
        )
    # FRED's CSV carries no release timestamp. The vintage is the latest period the
    # series covers — the honest, publisher-derived statement of how current it is —
    # never the time we happened to fetch it.
    dated = [o.period_key for o in release.observations]
    if dated:
        year, month = (int(p) for p in max(dated).split("-"))
        release.vintage_at = datetime(year, month, 1, tzinfo=timezone.utc)
    return release


# ── USGS Mineral Commodity Summaries ─────────────────────────────────────────── #

_NUMBER = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?"
_CELL = rf"(?:>?{_NUMBER}|—|-|W|NA|XX)"
_ROW_RE = re.compile(
    rf"^(?P<label>[A-Z][A-Za-z .,()'’/-]*?[a-z)\.])\s+(?P<cells>(?:{_CELL}\s*)+)$"
)
_UNIT_RE = re.compile(r"\[Data in ([^\]]+)\]|\(Data in ([^)]+(?:\([^)]*\)[^)]*)*)\)", re.IGNORECASE)
_YEAR_ROW_RE = re.compile(r"^((?:(?:19|20)\d{2}e?\s*)+)$")


def pdf_text_without_superscripts(content: bytes) -> str:
    """Text of a PDF with superscript characters removed by font size. SYNC — callers
    run it off the event loop (a PDF parse on the loop once had workers killed)."""
    import pdfplumber

    pages: list[str] = []
    with pdfplumber.open(io.BytesIO(content)) as pdf:
        for page in pdf.pages[:6]:
            sizes = Counter(round(c["size"], 1) for c in page.chars)
            if not sizes:
                continue
            body = sizes.most_common(1)[0][0]
            clean = page.filter(
                lambda o, body=body: o.get("object_type") != "char"
                or o.get("size", body) >= body * 0.85
            )
            pages.append(clean.extract_text() or "")
    return "\n".join(pages)


def _cell_value(cell: str) -> float | None:
    cell = cell.strip().lstrip(">")
    if cell in {"—", "-", "W", "NA", "XX", ""}:
        return None
    try:
        return float(cell.replace(",", ""))
    except ValueError:
        return None


def _country_key(label: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_")[:60]


def parse_usgs_mcs(
    text: str, *, commodity: Commodity, year: int, source_ref: str
) -> ParsedRelease:
    """World mine / refinery production and reserves by country, and price statistics.

    Only rows whose cell count matches the table header are stored; a row that does not
    line up is SKIPPED and named, never shifted into the wrong column.
    """
    release = ParsedRelease(dataset=USGS_MCS_DATASET)
    unit_match = _UNIT_RE.search(text)
    unit = (
        (unit_match.group(1) or unit_match.group(2)).strip() if unit_match else "as published"
    )
    release.unit_note = unit
    release.vintage_at = datetime(year, 2, 1, tzinfo=timezone.utc)

    lines = [line.strip() for line in text.splitlines() if line.strip()]
    start = next(
        (i for i, line in enumerate(lines) if line.startswith("World Mine")), None
    )
    if start is None:
        release.skipped.append("no 'World Mine … Production and Reserves' table found")
        return release

    header_i = next(
        (
            i
            for i in range(start + 1, min(start + 8, len(lines)))
            if re.match(r"^(?:mine|refinery|plant|smelter|mine and refinery)\b.*production",
                        lines[i], re.IGNORECASE)
        ),
        None,
    )
    if header_i is None or header_i + 1 >= len(lines):
        release.skipped.append("table header not found")
        return release
    header = lines[header_i].lower()
    year_match = _YEAR_ROW_RE.match(lines[header_i + 1])
    if not year_match:
        release.skipped.append("table year row not found")
        return release
    years = [(y.rstrip("e"), y.endswith("e")) for y in year_match.group(1).split()]
    blocks: list[str] = []
    if "mine production" in header:
        blocks.append("mine_production")
    if "refinery production" in header:
        blocks.append("refinery_production")
    columns: list[tuple[str, str | None, bool]] = []  # (measure, year, estimated)
    per_block = max(1, len(years) // max(1, len(blocks)))
    for index, block in enumerate(blocks):
        for y, est in years[index * per_block : (index + 1) * per_block]:
            columns.append((block, y, est))
    if "reserves" in header:
        columns.append(("reserves", str(year), False))

    for line in lines[header_i + 2 :]:
        if line.startswith(("World Resources", "Substitutes", "eEstimated")):
            break
        match = _ROW_RE.match(line)
        if not match:
            continue
        label = match.group("label").strip()
        label = re.sub(r"\s*\(rounded\)", "", label)
        cells = match.group("cells").split()
        if len(cells) != len(columns):
            release.skipped.append(
                f"{label}: {len(cells)} cells for {len(columns)} columns — not stored"
            )
            continue
        country = "world" if label.lower().startswith("world total") else _country_key(label)
        for (measure, period, estimated), cell in zip(columns, cells, strict=True):
            value = _cell_value(cell)
            if cell.strip() in {"W", "NA", "XX"} or period is None:
                continue  # withheld / not available is not zero, and not a reading
            release.observations.append(
                ParsedObservation(
                    series=SeriesSpec(
                        series_key=f"{commodity.slug}.{measure}.{country}",
                        display_name=(
                            f"{commodity.name} {measure.replace('_', ' ')} — {label}"
                        ),
                        unit=unit[:60],
                        frequency=FREQ_ANNUAL,
                        geography=None,
                    ),
                    period_key=period,
                    value=0.0 if cell.strip() in {"—", "-"} else value,
                    source_ref=source_ref,
                    estimated=estimated,
                )
            )

    # The survey's own price statistics: "Price, …" then rows of five yearly values.
    release.observations.extend(_usgs_prices(lines, commodity, year, source_ref))
    return release


_PRICE_LINE_RE = re.compile(
    rf"^(?P<label>[A-Za-z][A-Za-z0-9 ,.%()'’/+-]*?)\s+(?P<vals>(?:{_NUMBER}\s+){{3,4}}{_NUMBER})$"
)


def _usgs_prices(
    lines: list[str], commodity: Commodity, year: int, source_ref: str
) -> list[ParsedObservation]:
    """Price rows in the 'Salient Statistics' block: five values for year-4 … year-1(e)."""
    out: list[ParsedObservation] = []
    for i, line in enumerate(lines):
        if not line.lower().startswith("price"):
            continue
        context = line
        for follow in lines[i + 1 : i + 8]:
            if follow.lower().startswith(("employment", "net import", "recycling", "stocks")):
                break
            match = _PRICE_LINE_RE.match(follow)
            if not match:
                continue
            values = match.group("vals").split()
            if len(values) != 5:
                continue
            label = f"{context.rstrip(':')} — {match.group('label').strip()}"[:200]
            key = _country_key(match.group("label"))[:40]
            for offset, raw in enumerate(values):
                period = str(year - 5 + offset)
                out.append(
                    ParsedObservation(
                        series=SeriesSpec(
                            series_key=f"{commodity.slug}.price.{key}",
                            display_name=f"{commodity.name} price — {label}",
                            unit=context.split(",")[-1].strip(" :")[:60] or "as published",
                            frequency=FREQ_ANNUAL,
                        ),
                        period_key=period,
                        value=_cell_value(raw),
                        source_ref=source_ref,
                        estimated=offset == 4,
                    )
                )
        # One-line form: "Price, …, cents per pound: 432.3 410.8 395.3 431.8 490".
        inline = re.search(rf"((?:{_NUMBER}\s+){{4}}{_NUMBER})$", line)
        if inline and len(inline.group(1).split()) == 5:
            label = line[: inline.start()].strip(" :,")[:200]
            # The unit may be wrapped onto the next line: "... carbonate, 11,700 …" then
            # "dollars per metric ton".
            unit_line = lines[i + 1] if i + 1 < len(lines) else ""
            wrapped = re.match(r"^((?:dollars|cents) per [a-z ]+?)\d*$", unit_line)
            price_unit = (
                wrapped.group(1).strip()
                if wrapped
                else next(
                    (
                        part.strip()
                        for part in reversed(label.split(","))
                        if re.match(r"^(?:dollars|cents) per", part.strip())
                    ),
                    "as published",
                )
            )
            for offset, raw in enumerate(inline.group(1).split()):
                out.append(
                    ParsedObservation(
                        series=SeriesSpec(
                            series_key=f"{commodity.slug}.price.{_country_key(label)[:40]}",
                            display_name=f"{commodity.name} — {label}",
                            unit=price_unit[:60],
                            frequency=FREQ_ANNUAL,
                        ),
                        period_key=str(year - 5 + offset),
                        value=_cell_value(raw),
                        source_ref=source_ref,
                        estimated=offset == 4,
                    )
                )
    return out


# ── Fetch and store ──────────────────────────────────────────────────────────── #


async def fetch_fred_prices(
    commodity: Commodity, *, cfg: Any, fetcher: Any = None
) -> ParsedRelease:
    release = ParsedRelease(dataset=FRED_IMF_DATASET)
    if not commodity.fred_series:
        release.skipped.append(f"no IMF price series for {commodity.name}")
        return release
    from app.services.sources.document_fetcher import safe_fetch_document

    url = fred_url(commodity.fred_series)
    result = await (fetcher or safe_fetch_document)(
        url,
        allowed_domains=(FRED_HOST,),
        cfg=cfg,
        resolve_ip=True,
        extra_text_content_types=("application/csv", "text/csv"),
    )
    if not getattr(result, "ok", False) or not getattr(result, "content", None):
        release.skipped.append(f"FRED unreachable: {getattr(result, 'error', None) or 'no body'}")
        return release
    return parse_fred_csv(
        result.content.decode("utf-8", "replace"), commodity=commodity, source_ref=url
    )


async def fetch_usgs_summary(
    commodity: Commodity, *, cfg: Any, year: int, fetcher: Any = None
) -> ParsedRelease:
    release = ParsedRelease(dataset=USGS_MCS_DATASET)
    if not commodity.usgs_slug:
        release.skipped.append(f"no USGS chapter for {commodity.name}")
        return release
    from app.services.sources.document_fetcher import safe_fetch_document

    fetch = fetcher or safe_fetch_document
    # The current year's edition is published in late January or February; before it
    # exists, the previous edition is the latest.
    for edition in (year, year - 1):
        url = usgs_url(commodity.usgs_slug, edition)
        result = await fetch(url, allowed_domains=(USGS_HOST,), cfg=cfg, resolve_ip=True)
        if getattr(result, "ok", False) and getattr(result, "content", None):
            text = await asyncio.to_thread(pdf_text_without_superscripts, result.content)
            return parse_usgs_mcs(text, commodity=commodity, year=edition, source_ref=url)
        release.skipped.append(f"USGS {edition} edition unreachable")
    return release


async def store_release(session: Any, release: ParsedRelease, *, commodity: Commodity) -> int:
    """Write a parsed release into the macro store. Returns observations written."""
    from app.services.macro.store import record_observation, upsert_dataset, upsert_series

    if release.vintage_at is None or not release.observations:
        return 0
    dataset = await upsert_dataset(session, release.dataset)
    written = 0
    series_rows: dict[str, Any] = {}
    for obs in release.observations:
        row = series_rows.get(obs.series.series_key)
        if row is None:
            row = await upsert_series(session, dataset, obs.series)
            row.commodity = commodity.slug
            series_rows[obs.series.series_key] = row
        await record_observation(
            session,
            row,
            period_key=obs.period_key,
            value=obs.value,
            vintage_at=release.vintage_at,
            source_ref=obs.source_ref,
        )
        written += 1
    return written


__all__ = [
    "FRED_IMF_DATASET",
    "FRED_TIER",
    "USGS_MCS_DATASET",
    "USGS_TIER",
    "ParsedObservation",
    "ParsedRelease",
    "fetch_fred_prices",
    "fetch_usgs_summary",
    "fred_url",
    "parse_fred_csv",
    "parse_usgs_mcs",
    "pdf_text_without_superscripts",
    "store_release",
    "usgs_url",
]
