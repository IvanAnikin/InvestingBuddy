"""Macro source interface and the first live one — V3.4 Slice 4.7.

ONE SOURCE, WIRED CORRECTLY
===========================
The platform already learned the alternative the expensive way: 28 reference-only
connectors, none of them queried. So this slice adds **one** live source and the plugin
seam every later one arrives through, rather than four half-wired ones.

The first is the **World Bank Indicators API**: free, public, no credential, no
subscription, an explicit open licence (CC BY 4.0), and it publishes the annual national
accounts a macro question about a country most often needs. Everything V3 requires of a
source is true of it without anybody buying anything.

WHY A SOURCE RETURNS A RESULT AND NOT A LIST
============================================
Same lesson as ``IdentifierSource`` in V3.2.3.1: a source has to be able to say
*"I could not reach it"* and *"it published nothing for that period"* as different
things. A bare list collapses both into empty, and an empty list is then indistinguishable
from a country that had no GDP.

FETCHED CONTENT IS DATA
=======================
A response body is parsed, bounded and never executed or interpreted as instruction. The
fetch goes through the platform's own guarded fetcher with the publisher's host as the
allowlist, so every SSRF, redirect and byte guard applies unchanged.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol, runtime_checkable

from app.services.macro.definitions import (
    FREQ_ANNUAL,
    DatasetSpec,
    SeriesSpec,
)

#: Why a source returned nothing. Closed, for the reason every refusal vocabulary in this
#: codebase is closed: an aggregate over reasons is a finding, and an aggregate over free
#: text is a list of sentences.
FETCH_OK = "ok"
FETCH_UNREACHABLE = "unreachable"
FETCH_UNPARSEABLE = "unparseable"
FETCH_NO_DATA = "no_data"
FETCH_NOT_CONFIGURED = "not_configured"

FETCH_STATUSES: frozenset[str] = frozenset(
    {FETCH_OK, FETCH_UNREACHABLE, FETCH_UNPARSEABLE, FETCH_NO_DATA, FETCH_NOT_CONFIGURED}
)


@dataclass(frozen=True)
class ObservationRecord:
    """One value a source read, before anything is stored."""

    period_key: str
    value: float | None
    vintage_at: datetime
    source_ref: str | None = None


@dataclass
class SeriesFetch:
    """What one source produced for one series request.

    ``status`` distinguishes the four ways "no numbers" happens, because "the publisher
    has no data for this country" is a research gap somebody can close and "we could not
    reach the API" is an operational one.
    """

    status: str
    dataset: DatasetSpec | None = None
    series: SeriesSpec | None = None
    observations: list[ObservationRecord] = field(default_factory=list)
    detail: str | None = None

    def __post_init__(self) -> None:
        if self.status not in FETCH_STATUSES:
            raise ValueError(f"{self.status!r} is not a macro fetch status.")
        if self.status == FETCH_OK and (self.dataset is None or self.series is None):
            raise ValueError(
                "an ok fetch must name its dataset and series: an observation whose "
                "unit and frequency are unknown is not interpretable."
            )

    @property
    def ok(self) -> bool:
        return self.status == FETCH_OK


@runtime_checkable
class MacroSource(Protocol):
    """One official statistical publisher, behind an InvestingBuddy-owned interface."""

    source_id: str

    async def fetch_series(
        self, *, indicator: str, geography: str, limit: int = 60
    ) -> SeriesFetch:
        ...  # pragma: no cover - protocol


@dataclass
class StaticMacroSource:
    """A source backed by a fixture. Real parsing, no network.

    Not a mock: it returns the same ``SeriesFetch`` shape the live source does, including
    its failure states, so a test that passes against this is testing the store's
    contract rather than its own expectations.
    """

    source_id: str = "static_macro"
    responses: dict[tuple[str, str], SeriesFetch] = field(default_factory=dict)
    requests: list[tuple[str, str]] = field(default_factory=list)

    async def fetch_series(
        self, *, indicator: str, geography: str, limit: int = 60
    ) -> SeriesFetch:
        self.requests.append((indicator, geography))
        return self.responses.get(
            (indicator, geography),
            SeriesFetch(
                status=FETCH_NO_DATA,
                detail=f"no fixture for {indicator!r} in {geography!r}",
            ),
        )


# ── The World Bank Indicators API ───────────────────────────────────────────── #

WORLD_BANK_HOST = "api.worldbank.org"
WORLD_BANK_DATASET = DatasetSpec(
    dataset_key="world_bank:wdi",
    source_id="world_bank_indicators",
    display_name="World Bank World Development Indicators",
    source_url="https://data.worldbank.org",
    licence="CC BY 4.0",
    update_cadence="irregular; indicator-dependent",
    access_class="public_official",
)

#: A bound on the response body. The API is well-behaved; the cap is a guard against a
#: pathological one, not a statement about this publisher.
_MAX_OBSERVATIONS = 200


def parse_world_bank_payload(
    body: str, *, indicator: str, geography: str, source_ref: str | None = None
) -> SeriesFetch:
    """Parse the Indicators API response. Never raises; never invents a value.

    The response is ``[metadata, rows]``. A row's ``value`` is ``null`` for a period the
    Bank has not published, and that NULL is retained as a real observation: "the
    publisher has no figure for 2024" is information, and dropping it would make the gap
    indistinguishable from the platform never having asked.

    The vintage is ``lastupdated`` from the response metadata — the Bank's own statement
    of when the data was released. When it is absent the fetch is **refused** rather than
    stamped with the current time: a vintage of "now" on a three-year-old release would
    make every historical reading look freshly published, which is precisely the lie the
    vintage column exists to prevent.
    """
    try:
        payload = json.loads(body)
    except (ValueError, TypeError) as exc:
        return SeriesFetch(
            status=FETCH_UNPARSEABLE, detail=f"response was not JSON ({type(exc).__name__})"
        )
    if not isinstance(payload, list) or len(payload) < 2:
        return SeriesFetch(
            status=FETCH_UNPARSEABLE,
            detail=(
                "expected the Indicators API's [metadata, rows] envelope; "
                f"saw {type(payload).__name__} of length "
                f"{len(payload) if isinstance(payload, list) else 'n/a'}"
            ),
        )
    metadata, rows = payload[0], payload[1]
    if not isinstance(metadata, dict):
        return SeriesFetch(status=FETCH_UNPARSEABLE, detail="metadata was not an object")
    if rows is None or not isinstance(rows, list) or not rows:
        return SeriesFetch(
            status=FETCH_NO_DATA,
            detail=(
                f"the World Bank publishes no {indicator} for {geography}"
                if rows is not None
                else str(metadata.get("message") or "no rows returned")
            ),
        )
    raw_vintage = str(metadata.get("lastupdated") or "").strip()
    vintage = _parse_vintage(raw_vintage)
    if vintage is None:
        return SeriesFetch(
            status=FETCH_UNPARSEABLE,
            detail=(
                "the response carries no usable 'lastupdated' vintage. Stamping the "
                "current time would make a historical release look freshly published."
            ),
        )

    unit: str | None = None
    display_name = indicator
    observations: list[ObservationRecord] = []
    for row in rows[:_MAX_OBSERVATIONS]:
        if not isinstance(row, dict):
            continue
        period = str(row.get("date") or "").strip()
        if not period:
            continue
        indicator_block = row.get("indicator")
        if isinstance(indicator_block, dict) and indicator_block.get("value"):
            display_name = str(indicator_block["value"])
        value = row.get("value")
        if value is not None:
            try:
                value = float(value)
            except (TypeError, ValueError):
                # A value that is present and unreadable is NOT a published NULL. Skip
                # it rather than record an absence the publisher did not declare.
                continue
        if unit is None:
            unit = _unit_from_indicator(indicator, display_name)
        observations.append(
            ObservationRecord(
                period_key=period,
                value=value,
                vintage_at=vintage,
                source_ref=source_ref,
            )
        )
    if not observations:
        return SeriesFetch(
            status=FETCH_NO_DATA,
            detail=f"no dated rows for {indicator} in {geography}",
        )
    return SeriesFetch(
        status=FETCH_OK,
        dataset=WORLD_BANK_DATASET,
        series=SeriesSpec(
            series_key=f"{indicator}:{geography}".upper(),
            display_name=f"{display_name} — {geography}",
            unit=unit or "unknown",
            frequency=FREQ_ANNUAL,
            indicator_code=indicator,
            geography=geography.upper(),
            # The Bank does not state seasonal adjustment on annual national accounts.
            # None means "not stated", which is never read as "not adjusted".
            seasonal_adjustment=None,
        ),
        observations=observations,
    )


def _parse_vintage(raw: str) -> datetime | None:
    for fmt in ("%Y-%m-%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(raw, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _unit_from_indicator(indicator: str, display_name: str) -> str:
    """The unit as the Bank prints it in the indicator NAME, or ``unknown``.

    The Indicators API has no unit field. The name carries it — "GDP (current US$)",
    "Inflation, consumer prices (annual %)" — and reading it from there is the only
    honest option available. What this must never do is guess: an indicator whose name
    states no unit yields ``unknown``, and the store then holds a series a caller can see
    is uninterpretable rather than one silently labelled in dollars.
    """
    text = display_name.lower()
    if "current us$" in text or "constant 2015 us$" in text or "us$" in text:
        return "USD"
    if "annual %" in text or "% of gdp" in text or "(%)" in text or "percent" in text:
        return "percent"
    if "per capita" in text:
        return "unknown"
    if "people" in text or "population" in text:
        return "persons"
    return "unknown"


__all__ = [
    "FETCH_NOT_CONFIGURED",
    "FETCH_NO_DATA",
    "FETCH_OK",
    "FETCH_STATUSES",
    "FETCH_UNPARSEABLE",
    "FETCH_UNREACHABLE",
    "WORLD_BANK_DATASET",
    "WORLD_BANK_HOST",
    "MacroSource",
    "ObservationRecord",
    "SeriesFetch",
    "SeriesSpec",
    "StaticMacroSource",
    "parse_world_bank_payload",
]
