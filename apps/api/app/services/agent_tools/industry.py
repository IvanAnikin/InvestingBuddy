"""``get_industry_series`` — quantified commodity context, with provenance — V3.18.5.

Declared in the V3.3 tool vocabulary and never implemented, so every question that needed
industry numbers was unassignable at plan time, and the industry analysis of a copper
producer was whatever its own 10-K said about copper.

WHAT A CALL RETURNS
===================
For one commodity:

* the benchmark price — the latest monthly reading and the readings 3, 12 and 36 months
  earlier — with the percentage changes COMPUTED HERE, deterministically, each naming the
  two observations it came from;
* world mine (and refinery) production and reserves, the largest producing countries, and
  each one's share of the world total, from the U.S. Geological Survey;
* the survey's own price statistics.

Every figure is an observation in the macro store with its unit, period, vintage and
source, and its citation id is that row's id — so a finding that states "world copper mine
production was 23.0 Mt in 2025" cites the exact stored reading, and the number in the
sentence can be traced to the publisher's page.

FETCH ON MISS
=============
The tool fetches when the store holds nothing current for the commodity (monthly prices
older than ~6 weeks; the annual survey older than a year), through the platform's guarded
fetcher, bounded to two publisher hosts. A failure is recorded and returned as an honest
gap; it never becomes a zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from app.services.agent_tools.contracts import (
    TOOL_GET_INDUSTRY_SERIES,
    ToolCost,
    ToolSpec,
    units_for,
)
from app.services.macro.commodities import COMMODITIES, commodity_for

if TYPE_CHECKING:  # pragma: no cover
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

INDUSTRY_UNITS: tuple[str, ...] = ("url_fetch_calls",)
MEASURES: frozenset[str] = frozenset({"price", "production", "reserves"})
#: The largest producers returned per measure. The world total is always returned.
TOP_COUNTRIES = 8
PRICE_STALE_AFTER = timedelta(days=45)
SURVEY_STALE_AFTER = timedelta(days=365)
CHANGE_WINDOWS: tuple[int, ...] = (3, 12, 36)


def validate_get_industry_series(arguments: dict[str, Any]) -> dict[str, Any]:
    commodity = commodity_for(str(arguments.get("commodity") or ""))
    if commodity is None:
        raise ValueError(
            "commodity must be one of: " + ", ".join(sorted(c.slug for c in COMMODITIES))
        )
    measures = arguments.get("measures") or sorted(MEASURES)
    if isinstance(measures, str):
        measures = [measures]
    unknown = set(measures) - MEASURES
    if unknown:
        raise ValueError(f"unknown measures {sorted(unknown)}; known: {sorted(MEASURES)}")
    return {"commodity": commodity.slug, "measures": sorted(set(measures))}


def _obs_id(row_id: Any) -> str:
    return f"mo:{row_id}"


async def _latest_retrieval(session: Any, *, dataset_key: str, commodity: str) -> datetime | None:
    from sqlalchemy import func, select

    from app.models.macro import MacroDataset, MacroObservation, MacroSeries

    stmt = (
        select(func.max(MacroObservation.retrieved_at))
        .join(MacroSeries, MacroObservation.series_id == MacroSeries.id)
        .join(MacroDataset, MacroSeries.dataset_id == MacroDataset.id)
        .where(MacroDataset.dataset_key == dataset_key, MacroSeries.commodity == commodity)
    )
    value = (await session.execute(stmt)).scalar_one_or_none()
    if value is not None and value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value


async def _refresh(context: "ToolContext", commodity: Any) -> tuple[int, list[str]]:
    """Fetch what is missing or stale. Returns (fetches made, notes). Never raises."""
    from app.services.macro import commodity_sources as sources

    session = context.session
    now = datetime.now(timezone.utc)
    fetches = 0
    notes: list[str] = []
    plans: list[tuple[str, Any]] = []
    if commodity.fred_series:
        last = await _latest_retrieval(
            session, dataset_key=sources.FRED_IMF_DATASET.dataset_key, commodity=commodity.slug
        )
        if last is None or now - last > PRICE_STALE_AFTER:
            plans.append(
                ("prices", lambda: sources.fetch_fred_prices(commodity, cfg=context.cfg))
            )
    if commodity.usgs_slug:
        last = await _latest_retrieval(
            session, dataset_key=sources.USGS_MCS_DATASET.dataset_key, commodity=commodity.slug
        )
        if last is None or now - last > SURVEY_STALE_AFTER:
            plans.append(
                (
                    "survey",
                    lambda: sources.fetch_usgs_summary(
                        commodity, cfg=context.cfg, year=now.year
                    ),
                )
            )
    for label, start in plans:
        attempt_key = (label, commodity.slug)
        last_attempt = _LAST_ATTEMPT.get(attempt_key)
        if last_attempt is not None and now - last_attempt < RETRY_AFTER:
            # Tried recently and it produced nothing new — a chapter this parser cannot
            # read, a publisher that is down, a month not yet published. Re-downloading
            # it for every question of every run is spend, not diligence.
            notes.append(f"{label}: not re-fetched (last attempt under {RETRY_AFTER} ago)")
            continue
        _LAST_ATTEMPT[attempt_key] = now
        try:
            release = await start()
            fetches += max(1, int(getattr(release, "fetch_attempts", 1) or 1))
            async with session.begin_nested():
                written = await sources.store_release(session, release, commodity=commodity)
            if not written:
                notes.append(f"{label}: nothing new stored ({'; '.join(release.skipped[:2])})")
        except Exception as exc:  # noqa: BLE001 - a publisher failure is a gap, not a crash
            fetches += 1
            notes.append(f"{label}: fetch failed ({type(exc).__name__})")
    return fetches, notes


#: Per process: when each (source, commodity) was last fetched. See `_refresh`.
_LAST_ATTEMPT: dict[tuple[str, str], datetime] = {}
RETRY_AFTER = timedelta(hours=6)


async def _current(session: Any, *, dataset_key: str, commodity: str) -> list[tuple[Any, Any]]:
    from sqlalchemy import select

    from app.models.macro import MacroDataset, MacroObservation, MacroSeries

    stmt = (
        select(MacroSeries, MacroObservation)
        .join(MacroObservation, MacroObservation.series_id == MacroSeries.id)
        .join(MacroDataset, MacroSeries.dataset_id == MacroDataset.id)
        .where(
            MacroDataset.dataset_key == dataset_key,
            MacroSeries.commodity == commodity,
            MacroObservation.is_current.is_(True),
            MacroObservation.value.is_not(None),
        )
        .order_by(MacroSeries.series_key, MacroObservation.period_key.desc())
    )
    return list((await session.execute(stmt)).all())


def _months_back(period_key: str, months: int) -> str:
    year, month = (int(p) for p in period_key.split("-"))
    index = year * 12 + (month - 1) - months
    return f"{index // 12:04d}-{index % 12 + 1:02d}"


def _is_estimate(obs: Any, tier: str) -> bool:
    """The survey's convention: its latest year is an ESTIMATE ("2025e").

    The superscript that says so is removed with the other superscripts, so it is
    re-derived from the edition: a survey reading for the year before its vintage.
    """
    from app.services.macro.commodity_sources import USGS_TIER

    vintage = getattr(obs, "vintage_at", None)
    return (
        tier == USGS_TIER
        and vintage is not None
        and str(obs.period_key) == str(vintage.year - 1)
    )


def _item(series: Any, obs: Any, *, tier: str, commodity: str, **extra: Any) -> dict[str, Any]:
    return {
        "estimated": _is_estimate(obs, tier),
        "id": _obs_id(obs.id),
        "commodity": commodity,
        "series_key": series.series_key,
        "display_name": series.display_name,
        "period_key": obs.period_key,
        "value": float(obs.value),
        "unit": series.unit,
        "currency": series.currency,
        "geography": series.geography,
        "source_tier": tier,
        "source_ref": obs.source_ref,
        "vintage_at": obs.vintage_at.isoformat() if obs.vintage_at else None,
        **extra,
    }


def _price_items(rows: list[tuple[Any, Any]], commodity: str, tier: str) -> list[dict[str, Any]]:
    if not rows:
        return []
    series = rows[0][0]
    by_period = {obs.period_key: obs for _s, obs in rows}
    latest_key = max(by_period)
    latest = by_period[latest_key]
    items = [_item(series, latest, tier=tier, commodity=commodity, role="latest_price")]
    changes: dict[str, Any] = {}
    for months in CHANGE_WINDOWS:
        earlier = by_period.get(_months_back(latest_key, months))
        if earlier is None or not float(earlier.value):
            changes[f"{months}m"] = None  # missing means missing, never 0%
            continue
        items.append(
            _item(series, earlier, tier=tier, commodity=commodity,
                  role=f"price_{months}m_earlier")
        )
        pct = (float(latest.value) / float(earlier.value) - 1.0) * 100.0
        changes[f"{months}m"] = {
            "change_pct": round(pct, 1),
            "from_period": earlier.period_key,
            "from_value": float(earlier.value),
            "to_period": latest.period_key,
            "to_value": float(latest.value),
            "input_ids": [_obs_id(earlier.id), _obs_id(latest.id)],
        }
    items.append(
        {
            "id": f"calc:series_change:{series.series_key}:{latest_key}",
            "commodity": commodity,
            "series_key": series.series_key,
            "display_name": f"{series.display_name} — change over 3, 12 and 36 months",
            "definition": "(latest monthly price / price N months earlier - 1) * 100",
            "directionality": "contextual",
            "interpretation": (
                "A price change for the benchmark, not for the company's realised price; "
                "whether it helps or hurts the company depends on whether it sells or buys "
                "the commodity."
            ),
            "changes": changes,
            "unit": "%",
            "source_tier": "T6_model_estimate",
            "computed_by": "InvestingBuddy, deterministically, from the cited observations",
        }
    )
    return items


def _survey_items(
    rows: list[tuple[Any, Any]], commodity: str, tier: str, measures: set[str]
) -> list[dict[str, Any]]:
    latest: dict[str, tuple[Any, Any]] = {}
    for series, obs in rows:
        current = latest.get(series.series_key)
        if current is None or obs.period_key > current[1].period_key:
            latest[series.series_key] = (series, obs)
    items: list[dict[str, Any]] = []
    for measure in ("mine_production", "refinery_production", "reserves"):
        wanted = "reserves" if measure == "reserves" else "production"
        if wanted not in measures:
            continue
        prefix = f"{commodity}.{measure}."
        entries = {k[len(prefix):]: v for k, v in latest.items() if k.startswith(prefix)}
        if entries:
            # One period per ranking: a country whose latest reading is from an older
            # edition is not ranked beside this year's figures.
            period = max(v[1].period_key for v in entries.values())
            entries = {k: v for k, v in entries.items() if v[1].period_key == period}
        world = entries.get("world")
        world_value = float(world[1].value) if world and float(world[1].value) else None
        if world is not None:
            items.append(_item(*world, tier=tier, commodity=commodity, role=f"{measure}_world"))
        countries = sorted(
            ((k, v) for k, v in entries.items() if k not in {"world", "other", "other_countries"}),
            key=lambda kv: float(kv[1][1].value),
            reverse=True,
        )[:TOP_COUNTRIES]
        for country, (series, obs) in countries:
            share = (
                round(float(obs.value) / world_value * 100.0, 1)
                if world_value and world is not None and world[1].period_key == obs.period_key
                else None
            )
            items.append(
                _item(
                    series, obs, tier=tier, commodity=commodity, role=f"{measure}_country",
                    country=country,
                    share_of_world_pct=share,
                    share_definition=(
                        "country value / world total for the same period and unit * 100, "
                        "computed by InvestingBuddy"
                        if share is not None
                        else None
                    ),
                )
            )
    if "price" in measures:
        for key, (series, obs) in sorted(latest.items()):
            if key.startswith(f"{commodity}.price."):
                items.append(_item(series, obs, tier=tier, commodity=commodity,
                                   role="survey_price"))
    return items


async def _get_industry_series(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    from app.services.macro import commodity_sources as sources

    commodity = commodity_for(arguments["commodity"])
    assert commodity is not None  # validated
    measures = set(arguments["measures"])
    fetches, notes = await _refresh(context, commodity)

    items: list[dict[str, Any]] = []
    if "price" in measures and commodity.fred_series:
        items.extend(
            _price_items(
                await _current(
                    context.session,
                    dataset_key=sources.FRED_IMF_DATASET.dataset_key,
                    commodity=commodity.slug,
                ),
                commodity.slug,
                sources.FRED_TIER,
            )
        )
    items.extend(
        _survey_items(
            await _current(
                context.session,
                dataset_key=sources.USGS_MCS_DATASET.dataset_key,
                commodity=commodity.slug,
            ),
            commodity.slug,
            sources.USGS_TIER,
            measures,
        )
    )
    gaps = list(notes)
    if "price" in measures and not commodity.fred_series:
        gaps.append(
            f"No monthly benchmark price series for {commodity.name} is wired; the "
            "survey's annual price statistics are the platform's only price evidence."
        )
    return {
        "commodity": commodity.slug,
        "items": items,
        "gaps": gaps,
        "summary": (
            f"{len(items)} {commodity.name} statistic(s) from the IMF price system and the "
            "U.S. Geological Survey, each with its unit, period and source"
            + (f"; gaps: {'; '.join(gaps[:2])}" if gaps else "")
        ),
        "consumption": units_for(INDUSTRY_UNITS, url_fetch_calls=fetches),
        # Labels and units are read out of a publisher's PDF: data, never instructions.
        "contains_untrusted_content": True,
    }


GET_INDUSTRY_SERIES_SPEC = ToolSpec(
    name=TOOL_GET_INDUSTRY_SERIES,
    description=(
        "Quantified market context for one commodity: benchmark price with 3/12/36-month "
        "changes, world production and reserves by country with shares, from the IMF "
        "price system and the U.S. Geological Survey. Every figure carries its unit, "
        "period, source and a citable id."
    ),
    handler=_get_industry_series,
    validate_arguments=validate_get_industry_series,
    cost=ToolCost(fetches=2),
    instrumented_units=INDUSTRY_UNITS,
    access_classes=("public_official",),
)


def industry_tools_enabled(cfg: Any) -> bool:
    return bool(getattr(cfg, "v3_commodity_sources_enabled", False))


def register_industry_tools(registry: "ToolRegistry", *, cfg: Any = None) -> "ToolRegistry":
    """Registered only when enabled: the tool fetches from public publishers."""
    if cfg is None:
        from app.core.config import settings as cfg
    if industry_tools_enabled(cfg):
        registry.register(GET_INDUSTRY_SERIES_SPEC)
    return registry


__all__ = [
    "GET_INDUSTRY_SERIES_SPEC",
    "industry_tools_enabled",
    "register_industry_tools",
    "validate_get_industry_series",
]

