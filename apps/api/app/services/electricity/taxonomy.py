"""The electricity value-chain taxonomy — V3.20 slice 5.1 (spec §3.5).

Industry methodology is versioned configuration, not a prompt (the playbook rule,
``services/playbooks/schema.py``). This file is that configuration for the electricity
vertical: nine *layers of the stack*, each with sub-segments, and the keyword anchors a
company's own disclosure has to contain before a layer can be assigned to it.

What this file does NOT do
==========================
It does not classify a company. It declares what a classification may say and what text
would support it; the classifier (slice 5.2) applies it to cited text — a segment note or
a business description — and keeps the quoted anchor as the evidence. A layer with no
quoted anchor is never assigned.

Anchors
=======
Matched as whole words, singular or plural (``transformer`` matches ``transformers``).
An anchor that contains an upper-case letter is an acronym or a proper term and is
matched case-sensitively (``HVDC``, ``UPS``, ``SiC``), so the word "ups" in ordinary prose
never reads as an uninterruptible power supply. All other anchors are case-insensitive.

Exclusions
==========
General mining, oil & gas exploration/production/refining, and coal are out of scope for
the vertical **unless** the dominant-end-market test passes: the share of revenue the
company itself attributes to power generation or grid equipment is at least
``DOMINANT_END_MARKET_SHARE``. A share that is not disclosed does not pass — an
excluded company is not let back in on a guess.

Theme tags
==========
``report_schema.json`` (real_asset_equity v1) requires at least one ``theme_tags`` entry
from a closed enum. Each layer and sub-segment declares the enum members it maps to; a
unit test pins every declared tag against the schema file.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache

#: Bump on every change to a layer, sub-segment, anchor, exclusion or tag mapping.
#: A classification records the version it was made under.
TAXONOMY_VERSION = "2026-11-16.2"

#: The dominant-end-market threshold for the exclusion override (spec §3.5).
DOMINANT_END_MARKET_SHARE = 0.5

#: The structural stand-in the schema-completion layer emits when nothing classified the
#: company. The schema has no neutral enum member and requires one tag; the completer
#: discloses that this one is a stand-in, not a researched classification.
UNCLASSIFIED_THEME_TAG = "reshoring_supply_chain"


@dataclass(frozen=True)
class SubSegment:
    key: str
    display_name: str
    anchors: tuple[str, ...]
    theme_tags: tuple[str, ...] = ()


@dataclass(frozen=True)
class Layer:
    key: str
    display_name: str
    description: str
    sub_segments: tuple[SubSegment, ...]
    #: Tags every company in the layer gets; sub-segment tags are added after these.
    theme_tags: tuple[str, ...]
    #: Layer-level anchors that do not name one sub-segment.
    anchors: tuple[str, ...] = ()

    def sub_segment(self, key: str) -> SubSegment | None:
        return next((s for s in self.sub_segments if s.key == key), None)


@dataclass(frozen=True)
class ExclusionRule:
    key: str
    display_name: str
    anchors: tuple[str, ...]
    #: Anchors that, in the same text, describe the power-related part of such a
    #: company (they make the override *worth testing*; they never pass it alone).
    power_anchors: tuple[str, ...] = field(default=())


LAYERS: tuple[Layer, ...] = (
    Layer(
        "generation_fuel", "Generation & fuel",
        "Equipment, fuel and services for producing electricity.",
        (
            SubSegment("nuclear", "Nuclear",
                       ("nuclear reactor", "small modular reactor", "SMR",
                        "nuclear fuel", "uranium enrichment", "fuel fabrication"),
                       ("nuclear_fuel_cycle",)),
            SubSegment("uranium", "Uranium", ("uranium", "yellowcake", "U3O8"),
                       ("uranium", "nuclear_fuel_cycle")),
            SubSegment("gas_turbines", "Gas turbines & engines",
                       ("gas turbine", "combined cycle", "reciprocating engine",
                        "aeroderivative")),
            SubSegment("hydro", "Hydropower", ("hydropower", "hydroelectric", "hydro turbine")),
            SubSegment("wind", "Wind", ("wind turbine", "nacelle", "rotor blade", "monopile",
                                        "offshore wind", "onshore wind", "wind tower")),
            SubSegment("solar", "Solar", ("photovoltaic", "solar module", "solar panel",
                                          "solar cell", "polysilicon", "solar tracker")),
            SubSegment("geothermal", "Geothermal", ("geothermal",)),
        ),
        ("generation", "energy_transition"),
        ("power generation", "power plant", "generating capacity"),
    ),
    Layer(
        "transmission", "Transmission",
        "High-voltage, long-distance movement of power.",
        (
            SubSegment("hvdc", "HVDC", ("HVDC", "high-voltage direct current",
                                        "converter station")),
            SubSegment("hv_cables", "High-voltage & subsea cables",
                       ("high-voltage cable", "submarine cable", "subsea cable",
                        "export cable", "interconnector"),
                       ("grid_switchgear_cables",)),
            SubSegment("overhead_lines", "Overhead lines & towers",
                       ("overhead line", "transmission tower", "transmission line",
                        "lattice tower", "pylon")),
            SubSegment("power_transformers", "Power transformers & reactors",
                       ("power transformer", "large power transformer", "shunt reactor",
                        "autotransformer", "generator step-up")),
            SubSegment("hv_substations", "HV substations & switchgear",
                       ("high-voltage substation", "gas-insulated switchgear",
                        "high-voltage switchgear", "circuit breaker"),
                       ("grid_switchgear_cables",)),
        ),
        ("grid_transmission",),
        ("transmission grid", "transmission system operator", "TSO"),
    ),
    Layer(
        "distribution_grid_edge", "Distribution & grid edge",
        "Medium- and low-voltage networks down to the meter.",
        (
            SubSegment("distribution_transformers", "Distribution transformers",
                       ("distribution transformer", "pad-mounted transformer",
                        "pole-mounted transformer", "transformer")),
            SubSegment("mv_lv_switchgear", "MV/LV switchgear",
                       ("medium-voltage switchgear", "low-voltage switchgear",
                        "ring main unit", "recloser", "switchboard", "panelboard",
                        "switchgear"),
                       ("grid_switchgear_cables",)),
            SubSegment("mv_lv_cables", "MV/LV cables & wire",
                       ("medium-voltage cable", "low-voltage cable", "building wire",
                        "power cable", "wire and cable"),
                       ("grid_switchgear_cables",)),
            SubSegment("metering", "Metering", ("smart meter", "electricity meter",
                                                "advanced metering")),
            SubSegment("line_hardware", "Poles & line hardware",
                       ("utility pole", "line hardware", "insulator")),
        ),
        ("grid_distribution",),
        ("distribution grid", "distribution network", "distribution system operator"),
    ),
    Layer(
        "sensing_protection_digital", "Sensing, protection & digital",
        "What measures, protects and controls the grid.",
        (
            SubSegment("protection_relays", "Protection & relays",
                       ("protective relay", "protection relay", "relay protection")),
            SubSegment("grid_monitoring", "Grid sensing & monitoring",
                       ("line monitoring", "dynamic line rating", "phasor measurement",
                        "grid sensor", "fault indicator"),
                       ("robotics_sensors_automation",)),
            SubSegment("grid_software", "Grid software & control",
                       ("SCADA", "ADMS", "DERMS", "energy management system",
                        "grid automation", "substation automation")),
            SubSegment("ot_security", "OT cybersecurity",
                       ("operational technology security", "grid cybersecurity"),
                       ("resilience_security",)),
        ),
        ("grid_automation",),
    ),
    Layer(
        "storage", "Storage",
        "Storing electricity, from cells to grid-scale systems.",
        (
            SubSegment("battery_cells", "Battery cells & packs",
                       ("lithium-ion battery", "battery cell", "battery pack",
                        "sodium-ion"),
                       ("batteries",)),
            SubSegment("bess", "Battery energy storage systems",
                       ("battery energy storage", "BESS", "grid-scale storage",
                        "energy storage system"),
                       ("batteries",)),
            SubSegment("long_duration", "Long-duration storage",
                       ("flow battery", "pumped storage", "compressed air energy storage",
                        "thermal energy storage", "long-duration storage")),
            SubSegment("battery_recycling", "Battery recycling",
                       ("battery recycling", "black mass"),
                       ("battery_recycling", "recycling_secondary_supply")),
        ),
        ("energy_transition",),
    ),
    Layer(
        "power_electronics", "Power electronics",
        "Converting and conditioning power between AC, DC, voltages and frequencies.",
        (
            SubSegment("inverters", "Inverters", ("inverter", "microinverter",
                                                  "string inverter")),
            SubSegment("converters", "Converters & drives",
                       ("power converter", "rectifier", "variable frequency drive",
                        "motor drive")),
            SubSegment("power_semis", "Power semiconductors",
                       ("power semiconductor", "silicon carbide", "SiC", "gallium nitride",
                        "GaN", "IGBT", "MOSFET")),
            SubSegment("ups_power_quality", "UPS & power quality",
                       ("uninterruptible power supply", "UPS", "power quality",
                        "voltage regulator")),
            SubSegment("facts", "FACTS & synchronous condensers",
                       ("STATCOM", "FACTS", "static var compensator",
                        "synchronous condenser"),
                       ("grid_automation",)),
        ),
        ("electrification",),
    ),
    Layer(
        "demand_side_decentralised", "Demand side & decentralised",
        "Where power is used or produced locally, including data-centre power.",
        (
            SubSegment("data_centre_power", "Data-centre power",
                       ("data center power", "data centre power", "power distribution unit",
                        "PDU", "busway", "critical power")),
            SubSegment("ev_charging", "EV charging",
                       ("EV charging", "electric vehicle charging", "charging station",
                        "charger")),
            SubSegment("onsite_generation", "On-site generation",
                       ("generator set", "genset", "standby generator", "fuel cell",
                        "microgrid")),
            SubSegment("heat_pumps", "Heat pumps & electrified heat",
                       ("heat pump", "electric boiler")),
            SubSegment("demand_response", "Demand response & DER",
                       ("demand response", "distributed energy resource",
                        "virtual power plant")),
        ),
        ("electrification",),
    ),
    Layer(
        "utilities_grid_services", "Utilities & grid services",
        "Owners and operators of generation and networks, and the contractors who build them.",
        (
            SubSegment("regulated_utility", "Regulated utilities",
                       ("electric utility", "regulated utility", "rate base",
                        "regulated asset base")),
            SubSegment("ipp", "Independent power producers",
                       ("independent power producer", "IPP", "power purchase agreement",
                        "PPA", "merchant power"),
                       ("generation",)),
            SubSegment("grid_contracting", "Grid construction & services",
                       ("utility services", "transmission construction",
                        "line construction", "substation construction",
                        "electrical contractor", "EPC contractor")),
        ),
        ("energy_transition",),
    ),
    Layer(
        "upstream_electrical_materials", "Upstream electrical materials",
        "Materials the electrical equipment above is made from.",
        (
            SubSegment("copper_conductors", "Copper & aluminium conductors",
                       ("copper rod", "copper wire", "magnet wire", "winding wire",
                        "aluminium conductor", "aluminum conductor"),
                       ("materials_production",)),
            SubSegment("electrical_steel", "Electrical steel & cores",
                       ("electrical steel", "grain-oriented", "GOES", "transformer core",
                        "amorphous metal"),
                       ("materials_production",)),
            SubSegment("insulation", "Insulation & bushings",
                       ("bushing", "insulating oil", "transformer oil", "pressboard",
                        "SF6"),
                       ("specialty_chemicals_intermediates",)),
            SubSegment("magnets_rare_earths", "Magnets & rare earths",
                       ("rare earth magnet", "permanent magnet", "NdFeB", "rare earth"),
                       ("rare_earths", "critical_materials")),
            SubSegment("battery_materials", "Battery materials",
                       ("cathode", "anode", "lithium hydroxide", "lithium carbonate",
                        "graphite anode"),
                       ("critical_materials",)),
        ),
        ("critical_materials",),
    ),
)

EXCLUSIONS: tuple[ExclusionRule, ...] = (
    ExclusionRule(
        "general_mining", "General mining",
        ("mining company", "gold mine", "gold mining", "iron ore", "silver mine",
         "mineral exploration", "exploration-stage", "precious metals"),
        ("uranium", "copper", "rare earth", "lithium"),
    ),
    ExclusionRule(
        "oil_gas_upstream_refining", "Oil & gas E&P and refining",
        ("oil and gas exploration and production", "oil and gas production",
         "crude oil production", "oil refinery", "petroleum refining", "upstream oil",
         "oilfield services", "drilling rig"),
        ("power generation", "gas turbine", "power plant"),
    ),
    ExclusionRule(
        "coal", "Coal",
        ("coal mining", "thermal coal", "metallurgical coal", "coal mine", "coal producer"),
        ("power plant", "power generation"),
    ),
)

LAYER_KEYS: tuple[str, ...] = tuple(layer.key for layer in LAYERS)


def layer(key: str) -> Layer | None:
    return next((x for x in LAYERS if x.key == key), None)


@dataclass(frozen=True)
class AnchorHit:
    """One anchor found in a text: the quoted span is the evidence."""

    layer: str
    sub_segment: str | None
    anchor: str
    start: int
    end: int
    quote: str


@lru_cache(maxsize=512)
def _pattern(anchor: str) -> re.Pattern[str]:
    flags = 0 if any(c.isupper() for c in anchor) else re.IGNORECASE
    body = re.escape(anchor).replace(r"\ ", r"[\s-]+").replace(r"\-", r"[\s-]?")
    return re.compile(rf"(?<![\w-]){body}(?:s|es)?(?![\w-])", flags)


def find_anchor(anchor: str, text: str) -> re.Match[str] | None:
    return _pattern(anchor).search(text)


def match_layers(text: str) -> list[AnchorHit]:
    """Every layer/sub-segment anchor found in ``text``, in taxonomy order.

    One hit per (layer, sub-segment, anchor) — the first occurrence — so a description
    that repeats a word does not count it twice.
    """
    hits: list[AnchorHit] = []
    if not text:
        return hits
    for lay in LAYERS:
        for anchor in lay.anchors:
            m = find_anchor(anchor, text)
            if m:
                hits.append(AnchorHit(lay.key, None, anchor, m.start(), m.end(), m.group(0)))
        for sub in lay.sub_segments:
            for anchor in sub.anchors:
                m = find_anchor(anchor, text)
                if m:
                    hits.append(AnchorHit(lay.key, sub.key, anchor, m.start(), m.end(),
                                          m.group(0)))
    return hits


def match_exclusions(text: str) -> list[tuple[ExclusionRule, str]]:
    """The exclusion rules whose anchors ``text`` contains, with the matched anchor."""
    found: list[tuple[ExclusionRule, str]] = []
    if not text:
        return found
    for rule in EXCLUSIONS:
        for anchor in rule.anchors:
            if find_anchor(anchor, text):
                found.append((rule, anchor))
                break
    return found


def dominant_end_market_passes(power_share: float | None) -> bool:
    """The override for an excluded company: a *disclosed* power share ≥ the threshold."""
    return power_share is not None and power_share >= DOMINANT_END_MARKET_SHARE


def theme_tags_for(layer_keys: list[str] | tuple[str, ...],
                   sub_segment_keys: list[str] | tuple[str, ...] = ()) -> list[str]:
    """The schema ``theme_tags`` for a classification, ordered and de-duplicated.

    Layer tags first (primary layer first, as the caller orders them), then the tags of
    the named sub-segments. Unknown keys contribute nothing. Never empty: with no known
    layer the result is ``[UNCLASSIFIED_THEME_TAG]``.
    """
    tags: list[str] = []
    for key in layer_keys:
        lay = layer(key)
        if lay is None:
            continue
        for tag in lay.theme_tags:
            if tag not in tags:
                tags.append(tag)
    for sub_key in sub_segment_keys:
        for lay in LAYERS:
            sub = lay.sub_segment(sub_key)
            if sub is None:
                continue
            for tag in sub.theme_tags:
                if tag not in tags:
                    tags.append(tag)
    return tags or [UNCLASSIFIED_THEME_TAG]


def describe() -> dict[str, object]:
    """The taxonomy as plain data (for the API/docs and for a run record)."""
    return {
        "version": TAXONOMY_VERSION,
        "dominant_end_market_share": DOMINANT_END_MARKET_SHARE,
        "layers": [
            {"key": lay.key, "display_name": lay.display_name,
             "description": lay.description, "theme_tags": list(lay.theme_tags),
             "sub_segments": [{"key": s.key, "display_name": s.display_name}
                              for s in lay.sub_segments]}
            for lay in LAYERS
        ],
        "exclusions": [{"key": r.key, "display_name": r.display_name} for r in EXCLUSIONS],
    }
