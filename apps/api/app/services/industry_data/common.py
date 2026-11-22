"""What the three industry sources share — V3.21.4 (ADR-111).

A PARSED RELEASE, AND HOW IT IS STORED
======================================
Each source parses one publication into a :class:`IndustryRelease`: the dataset it
belongs to (with its licence and attribution), the observations it yielded, the vintage
the PUBLISHER stated, and what it refused to read and why. Parsing never touches the
database and never raises, so a parser is tested on a real fixture with nothing else.

:func:`store_industry_release` writes a release into the existing macro store
(``macro_datasets`` / ``macro_series`` / ``macro_observations``). It is append-only, by
the store's own rule, and idempotent in two ways:

* the same vintage of the same period is never written twice (a re-run of the job);
* a NEWER vintage whose value equals the current reading is not written either. A bulk
  file re-published with the same historical numbers is not a revision, and storing it
  as one would turn every weekly fetch into thousands of rows that say nothing.

A value that DID change is a new row at the new vintage; the old one stays, readable
through ``as_of``. A NULL the publisher printed (``..``) is a real, NULL row — never a 0.

COUNTRIES
=========
A company's country arrives as a venue country name ("Germany"), an ISO-2 code ("DE") or
an ISO-3 code. :func:`iso3_for` maps the countries the platform lists on; anything else
returns ``None`` and is reported as unresolved, never guessed.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal
from typing import Any

from app.services.macro.definitions import DatasetSpec, SeriesSpec

#: How many years of an annual series are kept per fetch. A growth figure over five
#: years needs six readings.
YEARS_KEPT = 6

#: Source ids, as the registry and the report name them.
SOURCE_EMBER = "ember_electricity"
SOURCE_IRENA = "irena_irenastat"
SOURCE_EIA = "eia_api_v2"
SOURCES: tuple[str, ...] = (SOURCE_EMBER, SOURCE_IRENA, SOURCE_EIA)

#: The settings flag of each source.
SOURCE_FLAGS: dict[str, str] = {
    SOURCE_EMBER: "v3_industry_data_ember_enabled",
    SOURCE_IRENA: "v3_industry_data_irena_enabled",
    SOURCE_EIA: "v3_industry_data_eia_enabled",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class IndustryDataset:
    """A dataset spec plus the attribution line its licence requires."""

    spec: DatasetSpec
    attribution: str


@dataclass(frozen=True)
class IndustryObservation:
    series: SeriesSpec
    period_key: str
    value: float | None
    #: The exact request this value came from. NEVER carries a credential.
    source_ref: str


@dataclass
class IndustryRelease:
    """Everything one publication yielded, and everything it did not."""

    source_id: str
    dataset: IndustryDataset
    observations: list[IndustryObservation] = field(default_factory=list)
    vintage_at: datetime | None = None
    #: How the vintage was obtained: ``publisher_last_modified`` | ``publisher_updated``
    #: | ``first_seen`` (the publisher states no release time) | ``period_derived``.
    vintage_basis: str | None = None
    skipped: list[str] = field(default_factory=list)
    #: HTTP requests this release took, for honest metering.
    fetch_attempts: int = 0
    #: Set when the source did not run at all (flag off, no key). Said, not silent.
    inert_reason: str | None = None


def configured_countries(cfg: Any) -> list[str]:
    """The ISO-3 codes the weekly refresh covers, from settings. Unknown codes dropped."""
    raw = str(getattr(cfg, "v3_industry_data_countries", "") or "")
    out: list[str] = []
    for part in raw.split(","):
        code = part.strip().upper()
        if len(code) == 3 and code.isalpha() and code not in out:
            out.append(code)
    return out


#: ISO-3 code → (ISO-2, display name, other names). The countries the exchange registry
#: lists, plus the configured refresh list. Closed: anything else is "unresolved".
_COUNTRIES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "USA": ("US", "United States", ("united states of america", "usa", "u.s.")),
    "CAN": ("CA", "Canada", ()),
    "MEX": ("MX", "Mexico", ()),
    "BRA": ("BR", "Brazil", ()),
    "GBR": ("GB", "United Kingdom", ("uk", "great britain", "england")),
    "IRL": ("IE", "Ireland", ()),
    "FRA": ("FR", "France", ()),
    "DEU": ("DE", "Germany", ()),
    "ITA": ("IT", "Italy", ()),
    "ESP": ("ES", "Spain", ()),
    "PRT": ("PT", "Portugal", ()),
    "NLD": ("NL", "Netherlands", ("the netherlands",)),
    "BEL": ("BE", "Belgium", ()),
    "AUT": ("AT", "Austria", ()),
    "CHE": ("CH", "Switzerland", ()),
    "DNK": ("DK", "Denmark", ()),
    "NOR": ("NO", "Norway", ()),
    "SWE": ("SE", "Sweden", ()),
    "FIN": ("FI", "Finland", ()),
    "POL": ("PL", "Poland", ()),
    "JPN": ("JP", "Japan", ()),
    "KOR": ("KR", "South Korea", ("korea", "republic of korea")),
    "CHN": ("CN", "China", ("people's republic of china",)),
    "HKG": ("HK", "Hong Kong", ()),
    "TWN": ("TW", "Taiwan", ()),
    "IND": ("IN", "India", ()),
    "AUS": ("AU", "Australia", ()),
    "NZL": ("NZ", "New Zealand", ()),
    "ZAF": ("ZA", "South Africa", ()),
}
_LOOKUP: dict[str, str] = {}
for _iso3, (_iso2, _name, _aliases) in _COUNTRIES.items():
    for _key in (_iso3, _iso2, _name, *_aliases):
        _LOOKUP[_key.strip().lower()] = _iso3


def iso3_for(value: str | None) -> str | None:
    """ISO-3 for a country name / ISO-2 / ISO-3 the platform knows; else ``None``."""
    if not value:
        return None
    return _LOOKUP.get(str(value).strip().lower())


def country_name(iso3: str) -> str:
    entry = _COUNTRIES.get(iso3)
    return entry[1] if entry else iso3


async def store_industry_release(session: Any, release: IndustryRelease) -> int:
    """Write a parsed release into the macro store. Returns observations written.

    Append-only and idempotent: see the module docstring. Nothing is written without a
    vintage — an observation nobody can date is not a reading.
    """
    from sqlalchemy import select

    from app.models.macro import MacroObservation
    from app.services.macro.store import record_observation, upsert_dataset, upsert_series

    if release.vintage_at is None or not release.observations:
        return 0
    dataset = await upsert_dataset(session, release.dataset.spec)
    written = 0
    series_rows: dict[str, Any] = {}
    for obs in release.observations:
        row = series_rows.get(obs.series.series_key)
        if row is None:
            row = await upsert_series(session, dataset, obs.series)
            series_rows[obs.series.series_key] = row
        same_vintage = await session.scalar(
            select(MacroObservation.id).where(
                MacroObservation.series_id == row.id,
                MacroObservation.period_key == obs.period_key,
                MacroObservation.vintage_at == release.vintage_at,
            )
        )
        if same_vintage is not None:
            continue
        current = (
            await session.execute(
                select(MacroObservation.value).where(
                    MacroObservation.series_id == row.id,
                    MacroObservation.period_key == obs.period_key,
                    MacroObservation.is_current.is_(True),
                )
            )
        ).first()
        if current is not None and _same_value(current[0], obs.value):
            continue  # re-published, not revised
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


#: The store's own precision (``NUMERIC(30, 10)``). A publisher's ``15.440000000000001``
#: is stored as ``15.4400000000``; comparing at any finer precision would read every such
#: cell as a revision on every fetch.
_STORE_QUANTUM = Decimal("1e-10")


def _same_value(stored: Any, new: float | None) -> bool:
    if stored is None or new is None:
        return stored is None and new is None
    return (Decimal(str(stored)).quantize(_STORE_QUANTUM)
            == Decimal(str(new)).quantize(_STORE_QUANTUM))


def parse_float(raw: Any) -> float | None:
    """A publisher's cell → float, or ``None`` for blank / not-available. Never 0."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)) and not isinstance(raw, bool):
        return float(raw)
    text = str(raw).strip()
    if text in {"", ".", "..", "-", "NA", "N/A", "null", "None"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


__all__ = [
    "SOURCES",
    "SOURCE_EIA",
    "SOURCE_EMBER",
    "SOURCE_FLAGS",
    "SOURCE_IRENA",
    "YEARS_KEPT",
    "IndustryDataset",
    "IndustryObservation",
    "IndustryRelease",
    "configured_countries",
    "country_name",
    "iso3_for",
    "parse_float",
    "store_industry_release",
]
