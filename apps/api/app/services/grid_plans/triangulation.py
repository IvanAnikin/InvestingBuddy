"""Demand-side triangulation — V3.20 slice 7.3 (spec §3.7).

For a company classified into the electricity stack whose CUSTOMERS are named (the
relationship block, slice 2.6), look each customer up among the promoters of the held
grid plans (``grid_capex_plans``: ENTSO-E TYNDP when its flag is on, AEMO, the NESO TEC
register, Berkeley Lab queues) and ask: does that customer publish ACTIVE projects in
the subject's technology?

Per customer:
* ``corroborated`` — it promotes at least one relevant project that is planned, in
  permitting, committed or under construction (and not due before this year), or it is
  a transmission provider with active interconnection requests (a queue summary row);
* ``not_corroborated`` — it appears in a held plan, but with nothing relevant and active;
* ``not_in_held_plans`` — no held plan names it (it may not be a grid owner, or its plan
  is not held). This is NOT evidence against the claim.

Overall: ``no_customer_named`` / ``corroborated`` / ``not_corroborated`` /
``no_grid_customer_matched``. The subject's own backlog / visibility statements are
referred to by finding LABEL (a finding is stated once). None of the held plans states
capex, so the SIZE of a backlog claim is not checked; the block says so. Pure.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from datetime import date
from typing import Any

BLOCK_VERSION = "2026-11-16.1"
MAX_PROJECTS_SHOWN = 5
ACTIVE = frozenset({"planned", "permitting", "committed", "under_construction"})

#: Technology tags that a layer's products go into (grid_plans/base.py vocabulary).
#: ``gen_*`` tags come from interconnection-queue summaries.
LAYER_TECH: dict[str, frozenset[str]] = {
    "transmission": frozenset({"hvdc", "cable", "overhead_line", "transformer", "substation",
                               "converter_station", "interconnector"}),
    "distribution_grid_edge": frozenset({"substation", "transformer", "cable",
                                         "overhead_line"}),
    "sensing_protection_digital": frozenset({"substation", "transformer", "hvdc",
                                             "converter_station", "reactive_compensation"}),
    "power_electronics": frozenset({"hvdc", "converter_station", "reactive_compensation"}),
    "storage": frozenset({"storage", "pumped_hydro"}),
    "upstream_electrical_materials": frozenset({"cable", "overhead_line", "transformer",
                                                "hvdc"}),
    "utilities_grid_services": frozenset({"*"}),
    "generation_fuel": frozenset({"gen_*", "pumped_hydro"}),
}
#: Layers for which a transmission provider's active interconnection queue is demand:
#: new connections need network equipment, protection and power electronics.
QUEUE_LAYERS = frozenset({"transmission", "distribution_grid_edge",
                          "sensing_protection_digital", "power_electronics", "storage",
                          "utilities_grid_services", "generation_fuel",
                          "upstream_electrical_materials"})

#: Versioned aliases: a customer name as filings write it → a promoter name as plans do.
ALIAS_VERSION = "2026-11-17.1"
ALIASES: dict[str, tuple[str, ...]] = {
    "american electric power": ("aep",),
    "pacific gas and electric": ("pg e", "pgae"),
    "southern california edison": ("sce",),
    "commonwealth edison": ("comed",),
    "reseau de transport d electricite": ("rte",),
    "redes energeticas nacionais": ("ren", "rede eletrica nacional"),
    "red electrica": ("ree", "red electrica de espana"),
    "national grid electricity transmission": ("national grid", "nget"),
    "austrian power grid": ("apg",),
    "independent power transmission operator": ("ipto", "admie"),
    "bonneville power administration": ("bpa",),
    "electric reliability council of texas": ("ercot",),
    "duke energy": ("duke", "duke energy progress", "duke energy carolinas"),
    "transgrid": ("nsw electricity networks", "transgrid"),
    "tennet": ("tennet tso", "tennet de", "tennet nl", "tennet germany",
               "tennet netherlands", "tennet offshore"),
    "sp transmission": ("spt", "sp energy networks", "scottish power transmission"),
    "scottish hydro electric transmission": ("shet", "ssen transmission",
                                             "scottish and southern electricity networks"),
}

_LEGAL = re.compile(
    r"\b(?:inc|incorporated|corp|corporation|co|company|ltd|limited|plc|llc|lp|ag|se|sa|"
    r"sau|sl|slu|srl|spa|nv|bv|gmbh|ab|asa|as|oy|oyj|ead|pty|holdings?|group|the)\b")


def _plain(name: str | None) -> str:
    text = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    text = re.sub(r"\([^)]*\)", " ", text.lower()).replace(".", "")  # S.A.U. -> sau
    return " ".join(re.sub(r"[^a-z0-9]+", " ", text).split())


def name_key(name: str | None) -> str:
    return " ".join(_LEGAL.sub(" ", _plain(name)).split())


def _aliases(k: str) -> set[str]:
    out: set[str] = set()
    for canonical, alts in ALIASES.items():
        if k == canonical or k in alts:
            out |= {canonical, *alts}
    return out


def _keys(name: str) -> set[str]:
    k = name_key(name)
    return ({k} if k else set()) | _aliases(k)


def _prefix_keys(name: str) -> set[str]:
    """Keys that may match as the START of a longer name. A one-word key qualifies only
    when the name really is one word ("Transgrid", "Amprion"): one left over after the
    legal form is removed ("Southern Company" -> "southern") is too generic to match
    inside other names. Curated aliases always qualify."""
    k = name_key(name)
    own = {k} if k and (" " in k or k == _plain(name)) else set()
    return own | _aliases(k)


def _matches(customer: str, promoter: str) -> bool:
    if _keys(customer) & _keys(promoter):
        return True
    # Prefix containment: "Transgrid" ~ "Transgrid NSW", "RWE Renewables UK" ~ "RWE
    # Renewables UK Dogger Bank South". Never a fragment in the middle of a name.
    for short_side, long_side in ((customer, promoter), (promoter, customer)):
        for short in _prefix_keys(short_side):
            if len(short) < 5:
                continue
            for long_ in _keys(long_side):
                if long_ != short and long_.startswith(short + " "):
                    return True
    return False


def _relevant(row: Mapping[str, Any], tech: frozenset[str], layer: str) -> bool:
    if row.get("record_kind") == "queue_summary":
        return layer in QUEUE_LAYERS and (row.get("capacity_mw") or 0) > 0
    tags = set(row.get("technology_tags") or [])
    if "*" in tech:
        return True
    if "gen_*" in tech and any(t.startswith("gen_") for t in tags):
        return True
    return bool(tags & tech)


def _active(row: Mapping[str, Any], today: date) -> bool:
    if row.get("record_kind") == "queue_summary":
        return True
    year = row.get("commissioning_year")
    return row.get("status_class") in ACTIVE and (year is None or year >= today.year)


def _claim_labels(report: Mapping[str, Any] | None) -> list[str]:
    rx = re.compile(r"backlog|order book|order intake|orders? (?:on hand|received)|"
                    r"visibility|framework agreement|remaining performance obligation", re.I)
    out: list[str] = []
    for section in (report or {}).get("sections") or []:
        for f in section.get("findings") or []:
            if rx.search(str(f.get("statement") or "")) and f.get("label"):
                out.append(str(f["label"]))
    return list(dict.fromkeys(out))[:10]


def triangulation_block(
    *, relationships: Mapping[str, Any] | None, layers: Mapping[str, Any] | None,
    plans: Iterable[Mapping[str, Any]], today: date, report: Mapping[str, Any] | None = None,
    sources_read: Sequence[str] = (),
) -> dict[str, Any] | None:
    """``None`` unless the company is classified into the electricity stack."""
    if not layers or layers.get("status") != "classified" or not layers.get("primary_layer"):
        return None
    layer = str(layers["primary_layer"])
    tech = LAYER_TECH.get(layer, frozenset())
    customers_node = (relationships or {}).get("customers") or {}
    rows = [r for r in customers_node.get("rows") or [] if r.get("name")]
    base = {
        "version": BLOCK_VERSION, "alias_version": ALIAS_VERSION, "layer": layer,
        "technologies": sorted(tech - {"*", "gen_*"}) or (["all"] if "*" in tech else []),
        "sources_read": list(sources_read),
        "subject_claim_labels": _claim_labels(report),
        "capex_note": ("The held plans state no capex (ENTSO-E's portfolio precedes its CBA "
                       "results; AEMO's workbook, the Berkeley Lab queues and the NESO TEC "
                       "register carry none), so "
                       "the SIZE of a backlog claim is not checked — only whether the named "
                       "customers publish active projects in this technology."),
    }
    if not rows:
        why = customers_node.get("note") or (
            "the relationship graph is off" if relationships is None
            else "no customer is disclosed")
        return {**base, "state": "no_customer_named", "customers": [],
                "reason": str(why)[:300]}
    plan_rows = list(plans)
    out: list[dict[str, Any]] = []
    for c in rows:
        cname = str(c["name"])
        hits = [p for p in plan_rows
                if any(_matches(cname, str(n)) for n in (p.get("promoters") or []))]
        relevant = [p for p in hits if _relevant(p, tech, layer) and _active(p, today)]
        status = ("corroborated" if relevant else
                  "not_corroborated" if hits else "not_in_held_plans")
        relevant.sort(key=lambda p: (p.get("commissioning_year") or 9999,
                                     str(p.get("project_name"))))
        out.append({
            "name": c["name"], "customer_source_url": c.get("source_url"),
            "status": status, "plan_records": len(hits), "relevant_active": len(relevant),
            "matched_promoters": sorted({str(n) for p in hits for n in p.get("promoters") or []
                                         if _matches(cname, str(n))})[:5],
            "projects": [{
                "project_name": p.get("project_name"), "source_key": p.get("source_key"),
                "plan_edition": p.get("plan_edition"), "record_kind": p.get("record_kind"),
                "status_class": p.get("status_class"),
                "commissioning_text": p.get("commissioning_text"),
                "technology_tags": list(p.get("technology_tags") or []),
                "capacity_text": p.get("capacity_text"),
                "capex": ({"amount": p["capex_amount"], "currency": p.get("capex_currency")}
                          if p.get("capex_amount") else None),
                "source_url": p.get("source_url"), "attribution": p.get("attribution"),
            } for p in relevant[:MAX_PROJECTS_SHOWN]],
        })
    statuses = {c["status"] for c in out}
    state = ("corroborated" if "corroborated" in statuses else
             "not_corroborated" if "not_corroborated" in statuses else
             "no_grid_customer_matched")
    return {**base, "state": state, "customers": out,
            "counts": {s: sum(1 for c in out if c["status"] == s)
                       for s in ("corroborated", "not_corroborated", "not_in_held_plans")}}


async def held_plans(session: Any, cfg: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """The newest edition of every ENABLED grid-plan source, as plain dicts."""
    from sqlalchemy import func, select

    from app.models.grid_plan import GridCapexPlan as G
    from app.services.grid_plans.base import SOURCES

    keys = [k for k, s in SOURCES.items() if getattr(cfg, s.flag, False)]
    if not keys:
        return [], []
    newest = dict((await session.execute(
        select(G.source_key, func.max(G.plan_edition)).where(G.source_key.in_(keys))
        .group_by(G.source_key))).all())
    rows: list[dict[str, Any]] = []
    for key, edition in newest.items():
        for r in (await session.execute(select(G).where(
                G.source_key == key, G.plan_edition == edition).limit(5000))).scalars():
            rows.append({
                "source_key": r.source_key, "plan_edition": r.plan_edition,
                "record_kind": r.record_kind, "project_name": r.project_name,
                "promoters": list(r.promoters_json or []),
                "technology_tags": list(r.technology_tags_json or []),
                "status_class": r.status_class, "commissioning_year": r.commissioning_year,
                "commissioning_text": r.commissioning_text, "capacity_text": r.capacity_text,
                "capacity_mw": float(r.capacity_mw) if r.capacity_mw is not None else None,
                "capex_amount": float(r.capex_amount) if r.capex_amount is not None else None,
                "capex_currency": r.capex_currency,
                "source_url": r.source_url, "attribution": r.attribution,
            })
    return rows, [f"{k}@{e}" for k, e in sorted(newest.items())]


__all__ = ["ACTIVE", "ALIASES", "BLOCK_VERSION", "LAYER_TECH", "held_plans", "name_key",
           "triangulation_block"]
