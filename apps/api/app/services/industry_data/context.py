"""The report's ``industry_context`` block — V3.21.4 (ADR-111).

WHAT IT SHOWS
=============
Dated, attributed figures about the electricity market a company sells into, read from
the macro store (the weekly refresh put them there; a report makes NO network call). The
series are chosen by two things the report already knows:

* **country** — the listing venue's country, then the stated domicile (at most two; the
  platform holds no per-company operating-country list, and the block says so);
* **electricity layer** — the classifier's primary layer (``business_model.
  electricity_layers``):

  ========================================  ==========================================
  layer                                     focus
  ========================================  ==========================================
  ``storage``                               battery / storage capacity (EIA batteries
                                            for the US, IRENA pumped hydro), wind and
                                            solar capacity, demand
  ``generation_fuel``                       generation mix (Ember shares), renewable
                                            capacity (IRENA), total generation
  ``transmission``, ``distribution_grid_    demand and generation growth
  edge``, ``utilities_grid_services``
  anything else, or unclassified            electricity demand
  ========================================  ==========================================

Every figure carries its source, series id, period, value, unit, retrieval time, vintage
and licence / attribution, and the stored observation's id. A change over time is
computed HERE, deterministically, naming the two observations it came from.

ABSENT IS SAID, NEVER ZERO
==========================
A series the store does not have is listed under ``missing`` with the reason (source
off, country not refreshed, not yet refreshed, or not published — battery capacity
outside the US is published by none of the three). With no figure at all the state is
``no_data``; with no resolvable country it is ``no_country``. The block is ``None`` only
when all three sources are off, so such a report is unchanged.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from app.services.industry_data.common import (
    SOURCE_EIA,
    SOURCE_EMBER,
    SOURCE_FLAGS,
    SOURCE_IRENA,
    YEARS_KEPT,
    configured_countries,
    country_name,
    iso3_for,
)

logger = logging.getLogger(__name__)

BLOCK_VERSION = 1
MAX_COUNTRIES = 2

FOCUS_STORAGE = "storage_capacity"
FOCUS_GENERATION_MIX = "generation_mix"
FOCUS_DEMAND_GROWTH = "demand_and_generation_growth"
FOCUS_DEMAND = "electricity_demand"

FOCUS_LABELS: dict[str, str] = {
    FOCUS_STORAGE: "Storage capacity, variable renewables and demand",
    FOCUS_GENERATION_MIX: "Generation mix and renewable capacity",
    FOCUS_DEMAND_GROWTH: "Electricity demand and generation growth",
    FOCUS_DEMAND: "Electricity demand",
}

LAYER_FOCUS: dict[str, str] = {
    "storage": FOCUS_STORAGE,
    "generation_fuel": FOCUS_GENERATION_MIX,
    "transmission": FOCUS_DEMAND_GROWTH,
    "distribution_grid_edge": FOCUS_DEMAND_GROWTH,
    "utilities_grid_services": FOCUS_DEMAND_GROWTH,
}

DATASET_KEYS: dict[str, str] = {
    SOURCE_EMBER: "ember:yearly_electricity",
    SOURCE_IRENA: "irena:irenastat_electricity",
    SOURCE_EIA: "eia:electricity_v2",
}
SOURCE_NAMES: dict[str, str] = {
    SOURCE_EMBER: "Ember, Yearly Electricity Data",
    SOURCE_IRENA: "IRENA, IRENASTAT",
    SOURCE_EIA: "U.S. Energy Information Administration (API v2)",
}

NOTE = (
    "Market context for the countries the company is listed or domiciled in, from public "
    "statistical publishers. It describes the market, not the company: whether a trend "
    "reaches the company depends on what it sells and to whom."
)
NO_BATTERY_REASON = (
    "Not published by these sources: neither Ember's yearly data nor IRENASTAT reports "
    "battery storage capacity by country, and the EIA covers the United States only."
)


@dataclass(frozen=True)
class Want:
    source_id: str
    #: ``{iso3}`` is substituted. EIA series are US-only.
    series_template: str
    label: str
    growth: bool = False
    us_only: bool = False


_DEMAND = Want(SOURCE_EMBER, "{iso3}.demand_twh", "Electricity demand", growth=True)
_TOTAL_GEN = Want(SOURCE_EMBER, "{iso3}.generation_twh.total_generation",
                  "Total electricity generation", growth=True)
_US_SALES = Want(SOURCE_EIA, "USA.retail_sales.all_sectors",
                 "Retail electricity sales, all sectors", growth=True, us_only=True)

FOCUS_WANTS: dict[str, tuple[Want, ...]] = {
    FOCUS_DEMAND: (_DEMAND, _US_SALES),
    FOCUS_DEMAND_GROWTH: (_DEMAND, _TOTAL_GEN, _US_SALES),
    FOCUS_GENERATION_MIX: (
        _TOTAL_GEN,
        *(Want(SOURCE_EMBER, f"{{iso3}}.share_pct.{slug}", f"Share of generation — {name}")
          for slug, name in (("coal", "coal"), ("gas", "gas"), ("nuclear", "nuclear"),
                             ("hydro", "hydro"), ("wind", "wind"), ("solar", "solar"),
                             ("renewables", "all renewables"), ("fossil", "all fossil"))),
        Want(SOURCE_IRENA, "{iso3}.capacity_mw.solar_pv", "Installed solar PV capacity",
             growth=True),
        Want(SOURCE_IRENA, "{iso3}.capacity_mw.onshore_wind",
             "Installed onshore wind capacity", growth=True),
        Want(SOURCE_IRENA, "{iso3}.capacity_mw.offshore_wind",
             "Installed offshore wind capacity", growth=True),
        Want(SOURCE_EIA, "USA.net_generation.all", "US net generation, all fuels",
             growth=True, us_only=True),
    ),
    FOCUS_STORAGE: (
        Want(SOURCE_EIA, "USA.capacity_mw.bat", "US battery storage capacity (net summer)",
             growth=True, us_only=True),
        Want(SOURCE_IRENA, "{iso3}.capacity_mw.pumped_hydro_storage",
             "Pumped hydro storage capacity"),
        Want(SOURCE_EMBER, "{iso3}.capacity_gw.wind_and_solar",
             "Installed wind and solar capacity", growth=True),
        _DEMAND,
    ),
}


def sources_on(cfg: Any) -> dict[str, bool]:
    return {s: bool(getattr(cfg, flag, False)) for s, flag in SOURCE_FLAGS.items()}


def focus_for(layers_block: dict[str, Any] | None) -> tuple[str | None, str]:
    """``(primary layer or None, focus)`` from the electricity-layers block."""
    if not layers_block or layers_block.get("status") != "classified":
        return None, FOCUS_DEMAND
    layer = layers_block.get("primary_layer")
    return (str(layer) if layer else None), LAYER_FOCUS.get(str(layer or ""), FOCUS_DEMAND)


def countries_for(company: Any) -> tuple[list[dict[str, str]], list[str]]:
    """``([{iso3, name, basis}], notes)``: listing country first, then domicile."""
    from app.services.exchange_registry import country_for_exchange

    out: list[dict[str, str]] = []
    notes: list[str] = []
    listing_raw = country_for_exchange(getattr(company, "exchange", None))
    for raw, basis in ((listing_raw, "listing venue"),
                       (getattr(company, "country", None), "stated domicile")):
        if not raw:
            continue
        iso3 = iso3_for(raw)
        if iso3 is None:
            notes.append(f"the {basis} country {str(raw)[:60]!r} is not one the platform "
                         "maps to market statistics")
            continue
        if all(c["iso3"] != iso3 for c in out):
            out.append({"iso3": iso3, "name": country_name(iso3), "basis": basis})
    return out[:MAX_COUNTRIES], notes


async def _series_rows(session: Any, dataset_key: str, series_key: str
                       ) -> tuple[Any, Any, list[Any]] | None:
    from sqlalchemy import select

    from app.models.macro import MacroDataset, MacroObservation, MacroSeries

    found = (await session.execute(
        select(MacroDataset, MacroSeries)
        .join(MacroSeries, MacroSeries.dataset_id == MacroDataset.id)
        .where(MacroDataset.dataset_key == dataset_key,
               MacroSeries.series_key == series_key)
        .limit(1))).first()
    if found is None:
        return None
    dataset, series = found
    rows = (await session.execute(
        select(MacroObservation)
        .where(MacroObservation.series_id == series.id,
               MacroObservation.is_current.is_(True))
        .order_by(MacroObservation.period_key.desc())
        .limit(YEARS_KEPT))).scalars().all()
    return dataset, series, list(rows)


def _iso(value: Any) -> str | None:
    return value.isoformat() if value is not None else None


def _figure(source_id: str, dataset: Any, series: Any, obs: Any, *, country: str,
            role: str, label: str) -> dict[str, Any]:
    from app.services.industry_data.eia import EIA_DATASET
    from app.services.industry_data.ember import EMBER_DATASET
    from app.services.industry_data.irena import IRENA_DATASET

    attribution = {SOURCE_EMBER: EMBER_DATASET, SOURCE_IRENA: IRENA_DATASET,
                   SOURCE_EIA: EIA_DATASET}[source_id].attribution
    return {
        "observation_id": f"mo:{obs.id}",
        "source_id": source_id,
        "source_name": SOURCE_NAMES[source_id],
        "dataset_key": dataset.dataset_key,
        "series_id": series.series_key,
        "indicator_code": series.indicator_code,
        "label": label,
        "display_name": series.display_name,
        "country": country,
        "period": obs.period_key,
        "value": float(obs.value),
        "unit": series.unit,
        "retrieved_at": _iso(obs.retrieved_at),
        "vintage_at": _iso(obs.vintage_at),
        "source_ref": obs.source_ref,
        "licence": dataset.licence,
        "attribution": attribution,
        "role": role,
    }


def _change(series: Any, latest: Any, earlier: Any, years: int) -> dict[str, Any] | None:
    if earlier is None or earlier.value is None or not float(earlier.value):
        return None
    pct = (float(latest.value) / float(earlier.value) - 1.0) * 100.0
    return {
        "series_id": series.series_key,
        "years": years,
        "from_period": earlier.period_key,
        "from_value": float(earlier.value),
        "to_period": latest.period_key,
        "to_value": float(latest.value),
        "unit": series.unit,
        "change_pct": round(pct, 1),
        "input_ids": [f"mo:{earlier.id}", f"mo:{latest.id}"],
        "definition": "(later value / earlier value - 1) * 100, computed by InvestingBuddy",
    }


async def industry_context_block(
    session: Any, company: Any, *, cfg: Any, layers_block: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """The block, or ``None`` when every source is off (report unchanged). Never raises."""
    on = sources_on(cfg)
    if not any(on.values()):
        return None
    try:
        # A savepoint: a failed read must not poison the report's own transaction.
        async with session.begin_nested():
            return await _build(session, company, cfg=cfg, layers_block=layers_block,
                                on=on)
    except Exception:  # noqa: BLE001 - an additive block never costs the report
        logger.warning("industry context block failed")
        return None


async def _build(session: Any, company: Any, *, cfg: Any,
                 layers_block: dict[str, Any] | None, on: dict[str, bool]) -> dict[str, Any]:
    layer, focus = focus_for(layers_block)
    countries, notes = countries_for(company)
    refreshed = set(configured_countries(cfg))
    eia_keyed = bool(str(getattr(cfg, "eia_api_key", "") or "").strip())
    figures: list[dict[str, Any]] = []
    changes: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []

    def _miss(country: str, want: Want, reason: str) -> None:
        missing.append({"country": country, "source_id": want.source_id,
                        "series_id": want.series_template.format(iso3=country),
                        "label": want.label, "reason": reason})

    for place in countries:
        iso3 = place["iso3"]
        if focus == FOCUS_STORAGE and iso3 != "USA":
            missing.append({"country": iso3, "source_id": None, "series_id": None,
                            "label": "Battery storage capacity", "reason": NO_BATTERY_REASON})
        for want in FOCUS_WANTS[focus]:
            if want.us_only and iso3 != "USA":
                continue
            if not on[want.source_id]:
                _miss(iso3, want, f"{SOURCE_NAMES[want.source_id]} is switched off")
                continue
            if want.source_id == SOURCE_EIA and not eia_keyed:
                _miss(iso3, want, "EIA_API_KEY is not set, so the EIA source is inert")
                continue
            series_key = want.series_template.format(iso3=iso3)
            held = await _series_rows(session, DATASET_KEYS[want.source_id], series_key)
            usable = [r for r in (held[2] if held else []) if r.value is not None]
            if held is None or not usable:
                reason = (
                    "not in the refreshed countries (V3_INDUSTRY_DATA_COUNTRIES)"
                    if iso3 not in refreshed and not want.us_only
                    else ("published as not available for every stored year" if held
                          else "not in the platform's store yet (the weekly refresh has "
                               "not stored it)"))
                _miss(iso3, want, reason)
                continue
            dataset, series, _rows = held
            latest = usable[0]
            figures.append(_figure(want.source_id, dataset, series, latest, country=iso3,
                                   role="latest", label=want.label))
            if want.growth:
                by_period = {r.period_key: r for r in usable}
                for years in (1, 5):
                    earlier = by_period.get(str(int(latest.period_key) - years))
                    change = _change(series, latest, earlier, years)
                    if change is not None:
                        change["country"], change["label"] = iso3, want.label
                        changes.append(change)

    state = "available" if figures else ("no_data" if countries else "no_country")
    if not countries:
        notes.append("no listing or domicile country could be mapped to market statistics")
    return {
        "version": BLOCK_VERSION,
        "state": state,
        "layer": layer,
        "focus": focus,
        "focus_label": FOCUS_LABELS[focus],
        "countries": countries,
        "country_note": (
            "Countries are the listing venue's and the stated domicile; the platform "
            "keeps no list of the countries a company operates in."),
        "figures": figures,
        "changes": changes,
        "missing": missing,
        "notes": notes,
        "sources": [
            {"source_id": s, "name": SOURCE_NAMES[s], "enabled": on[s],
             "inert_reason": ("EIA_API_KEY is not set" if s == SOURCE_EIA and on[s]
                              and not eia_keyed else None)}
            for s in (SOURCE_EMBER, SOURCE_IRENA, SOURCE_EIA)
        ],
        "note": NOTE,
    }


__all__ = [
    "BLOCK_VERSION",
    "FOCUS_DEMAND",
    "FOCUS_DEMAND_GROWTH",
    "FOCUS_GENERATION_MIX",
    "FOCUS_STORAGE",
    "LAYER_FOCUS",
    "countries_for",
    "focus_for",
    "industry_context_block",
    "sources_on",
]
