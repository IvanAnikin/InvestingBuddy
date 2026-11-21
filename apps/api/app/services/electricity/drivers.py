"""Demand drivers and the bottleneck catalogue — V3.20 slice 6.1 (spec §3.6).

Versioned configuration, like the taxonomy and the playbooks. Discovery for the vertical
starts from a **driver** (why demand moves), goes to the **bottlenecks** that driver runs
into (where supply is short), and then climbs the value chain **upstream** of each
bottleneck — the components, materials and services the bottleneck itself depends on.
The under-covered supplier two or three steps up is the target (the schema's
``supply_chain_distance_from_obvious`` sweet spot).

Every bottleneck names the taxonomy layer and sub-segment it sits in (checked by a test
against ``taxonomy.LAYERS``), and its upstream inputs in order, each optionally with
inputs of its own — the third step.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Bump on every change to a driver, bottleneck, input or query template.
CATALOGUE_VERSION = "2026-11-16.1"


@dataclass(frozen=True)
class UpstreamInput:
    name: str
    #: Inputs of this input — the third step up. Empty = the chain stops here.
    inputs: tuple[str, ...] = ()


@dataclass(frozen=True)
class Bottleneck:
    key: str
    display_name: str
    layer: str
    sub_segment: str | None
    why_short: str
    upstream: tuple[UpstreamInput, ...]


@dataclass(frozen=True)
class Driver:
    key: str
    display_name: str
    description: str
    bottlenecks: tuple[str, ...]


BOTTLENECKS: tuple[Bottleneck, ...] = (
    Bottleneck(
        "short_circuit_testing", "Short-circuit testing capacity", "transmission",
        "power_transformers",
        "Large transformers and switchgear need a type test at one of a few high-power "
        "laboratories; slots are booked far ahead.",
        (UpstreamInput("high-power test laboratory", ("test generator", "test transformer")),
         UpstreamInput("type test certification services")),
    ),
    Bottleneck(
        "hvdc_valves", "HVDC converter valves", "transmission", "hvdc",
        "Few qualified suppliers of converter valves and the press-pack semiconductors in "
        "them.",
        (UpstreamInput("press-pack IGBT", ("silicon wafer", "power semiconductor packaging")),
         UpstreamInput("thyristor valve"),
         UpstreamInput("converter transformer", ("grain-oriented electrical steel",))),
    ),
    Bottleneck(
        "subsea_cable_capacity", "Subsea cable slots and installation vessels",
        "transmission", "hv_cables",
        "Extrusion towers for HV subsea cable and cable-laying vessels are booked for years.",
        (UpstreamInput("XLPE insulation compound", ("polyethylene", "peroxide")),
         UpstreamInput("copper conductor", ("copper rod",)),
         UpstreamInput("cable-laying vessel"),
         UpstreamInput("lead sheathing")),
    ),
    Bottleneck(
        "bushings_tap_changers", "Bushings and tap changers", "transmission",
        "power_transformers",
        "Two components every large transformer needs, from a handful of makers.",
        (UpstreamInput("condenser bushing", ("resin impregnated paper", "porcelain insulator")),
         UpstreamInput("on-load tap changer", ("vacuum interrupter",))),
    ),
    Bottleneck(
        "goes", "Grain-oriented electrical steel (GOES)", "upstream_electrical_materials",
        "electrical_steel",
        "Few mills make the transformer-core steel; new capacity takes years.",
        (UpstreamInput("silicon steel slab", ("ferrosilicon",)),
         UpstreamInput("transformer core cutting and stacking")),
    ),
    Bottleneck(
        "non_china_lfp", "Non-China LFP cell qualification", "storage", "battery_cells",
        "Storage buyers outside China need qualified LFP supply chains.",
        (UpstreamInput("LFP cathode material", ("lithium carbonate", "iron phosphate")),
         UpstreamInput("battery separator"),
         UpstreamInput("graphite anode material", ("synthetic graphite",))),
    ),
    Bottleneck(
        "sic_substrates", "SiC substrates", "power_electronics", "power_semis",
        "Silicon-carbide wafers limit inverter and charger power-semiconductor output.",
        (UpstreamInput("silicon carbide boule", ("high-purity silicon carbide powder",)),
         UpstreamInput("SiC epitaxy")),
    ),
    Bottleneck(
        "queue_position", "Interconnection queue position", "utilities_grid_services",
        "ipp",
        "Projects with an interconnection agreement are scarce; queue position is an asset.",
        (UpstreamInput("interconnection studies"),
         UpstreamInput("grid-forming inverter", ("power semiconductor",))),
    ),
    Bottleneck(
        "hv_labour", "High-voltage skilled labour", "utilities_grid_services",
        "grid_contracting",
        "Linemen, cable jointers and HV commissioning engineers are short everywhere.",
        (UpstreamInput("cable jointing services"),
         UpstreamInput("HV commissioning services")),
    ),
)

DRIVERS: tuple[Driver, ...] = (
    Driver("data_centre_load", "Data-centre load growth",
           "New data-centre demand needs grid connections, substations and on-site power.",
           ("short_circuit_testing", "bushings_tap_changers", "goes", "queue_position",
            "hv_labour")),
    Driver("offshore_wind_connection", "Offshore-wind grid connection",
           "Offshore wind needs export cables, HVDC links and installation vessels.",
           ("subsea_cable_capacity", "hvdc_valves", "hv_labour")),
    Driver("transformer_replacement", "Transformer replacement cycle",
           "An ageing transformer fleet is being replaced while new demand also needs units.",
           ("short_circuit_testing", "bushings_tap_changers", "goes")),
    Driver("interconnection_reform", "Interconnection reform",
           "Queue reform and grid-forming requirements change who can connect and when.",
           ("queue_position", "sic_substrates")),
    Driver("capacity_market_tightness", "Capacity-market tightness",
           "Tight capacity markets pay for storage and flexible generation.",
           ("non_china_lfp", "sic_substrates", "queue_position")),
)


def driver(key: str) -> Driver | None:
    return next((d for d in DRIVERS if d.key == key), None)


def bottleneck(key: str) -> Bottleneck | None:
    return next((b for b in BOTTLENECKS if b.key == key), None)


__all__ = [
    "BOTTLENECKS",
    "CATALOGUE_VERSION",
    "DRIVERS",
    "Bottleneck",
    "Driver",
    "UpstreamInput",
    "bottleneck",
    "driver",
]
