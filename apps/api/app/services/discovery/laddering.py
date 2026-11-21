"""Supply-chain laddering — V3.20 slice 6.1 (spec §3.6).

Two halves, both deterministic:

1. **The graph walk.** From a seed company (the obvious beneficiary, hop 0), climb the
   relationship graph's ``supplier_of`` edges upstream: an edge ``A supplier_of B`` with
   ``B`` on the current frontier makes ``A`` a candidate one hop further up. Up to
   ``MAX_HOPS``; cycles are cut; an edge that ended (``effective_to`` in the past) is not
   walked. Every candidate carries the path that reached it, edge by edge, with each
   edge's source and evidence — no uncited hop.
2. **The query plan.** From a driver, its bottlenecks and their upstream inputs
   (``electricity.drivers``), the value-chain queries for each step up, with the step
   number. ``{company}`` and ``{ticker}`` are never filled by a model.

``supply_chain_distance_from_obvious`` (report schema, 0–5) is the hop count of the path
a candidate was reached by: 0 for the seed itself, the shortest path otherwise.

This slice plans and walks; it does not run web searches. A discovery run that executes
the plan admits candidates through the existing admission path.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services.electricity import drivers as dv

MAX_HOPS = 3
#: The schema bounds ``supply_chain_distance_from_obvious`` to 0–5.
SCHEMA_MAX_DISTANCE = 5
#: Edges read per frontier step (a bound, not a sample: the walk says when it hit it).
MAX_EDGES_PER_STEP = 500


@dataclass(frozen=True)
class Hop:
    supplier_entity_id: str
    customer_entity_id: str
    source: str | None
    source_url: str | None
    evidence_ref: str | None
    as_of: str | None


@dataclass
class LadderCandidate:
    entity_id: str
    hops: int
    path: list[Hop] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entity_id": self.entity_id,
            "hops": self.hops,
            "supply_chain_distance_from_obvious": distance(self.hops),
            "path": [hop.__dict__ for hop in self.path],
        }


def distance(hops: int | None) -> int:
    """The schema field: the hop count, clamped to the schema's 0–5."""
    if hops is None or hops < 0:
        return 0
    return min(int(hops), SCHEMA_MAX_DISTANCE)


async def _supplier_edges(session: Any, customers: Iterable[str],
                          today: date) -> list[Any]:
    from sqlalchemy import or_, select

    from app.models.legal_entity import EntityRelationship
    from app.services.entities.vocabulary import REL_SUPPLIER_OF

    ids = [uuid.UUID(c) for c in customers]
    if not ids:
        return []
    stmt = (
        select(EntityRelationship)
        .where(
            EntityRelationship.relationship_type == REL_SUPPLIER_OF,
            EntityRelationship.object_entity_id.in_(ids),
            EntityRelationship.subject_entity_id.is_not(None),
            or_(EntityRelationship.effective_to.is_(None),
                EntityRelationship.effective_to >= today),
        )
        .limit(MAX_EDGES_PER_STEP)
    )
    async with session.begin_nested():
        return list((await session.execute(stmt)).scalars().all())


async def ladder(
    session: Any,
    seed_entity_id: uuid.UUID | str,
    *,
    max_hops: int = MAX_HOPS,
    today: date | None = None,
) -> dict[str, Any]:
    """Upstream suppliers of the seed, breadth-first, shortest path first."""
    today = today or date.today()
    seed = str(seed_entity_id)
    found: dict[str, LadderCandidate] = {seed: LadderCandidate(seed, 0)}
    frontier = [seed]
    truncated = False
    for hop in range(1, max(0, min(max_hops, SCHEMA_MAX_DISTANCE)) + 1):
        edges = await _supplier_edges(session, frontier, today)
        truncated = truncated or len(edges) >= MAX_EDGES_PER_STEP
        next_frontier: list[str] = []
        for edge in edges:
            supplier = str(edge.subject_entity_id)
            customer = str(edge.object_entity_id)
            if supplier in found:  # a cycle, or already reached by a shorter path
                continue
            via = found[customer]
            step = Hop(
                supplier_entity_id=supplier, customer_entity_id=customer,
                source=edge.source, source_url=edge.source_url,
                evidence_ref=getattr(edge, "evidence_ref", None),
                as_of=edge.as_of.isoformat() if getattr(edge, "as_of", None) else None,
            )
            found[supplier] = LadderCandidate(supplier, hop, [*via.path, step])
            next_frontier.append(supplier)
        if not next_frontier:
            break
        frontier = next_frontier
    candidates = sorted((c for c in found.values() if c.entity_id != seed),
                        key=lambda c: (c.hops, c.entity_id))
    return {
        "seed_entity_id": seed,
        "max_hops": max_hops,
        "candidates": [c.to_dict() for c in candidates],
        "truncated": truncated,
    }


def query_plan(driver_key: str, *, bottleneck_keys: Iterable[str] | None = None,
               max_steps: int = MAX_HOPS) -> dict[str, Any] | None:
    """The value-chain queries for a driver, step by step up the chain.

    Step 1 asks who makes the bottleneck itself; step 2 who supplies each upstream input;
    step 3 who supplies the inputs of those inputs. ``None`` for an unknown driver.
    """
    drv = dv.driver(driver_key)
    if drv is None:
        return None
    chosen = [k for k in drv.bottlenecks if bottleneck_keys is None or k in set(bottleneck_keys)]
    steps: list[dict[str, Any]] = []
    for key in chosen:
        b = dv.bottleneck(key)
        if b is None:
            continue
        name = b.display_name
        steps.append({"step": 1, "bottleneck": key, "target": name,
                      "queries": [f"{name} manufacturers", f"{name} capacity expansion"]})
        if max_steps < 2:
            continue
        for item in b.upstream:
            steps.append({"step": 2, "bottleneck": key, "target": item.name,
                          "queries": [f"{item.name} suppliers", f"{item.name} producers"]})
            if max_steps < 3:
                continue
            for sub in item.inputs:
                steps.append({"step": 3, "bottleneck": key, "target": sub,
                              "queries": [f"{sub} suppliers for {item.name}"]})
    return {
        "catalogue_version": dv.CATALOGUE_VERSION,
        "driver": drv.key,
        "bottlenecks": chosen,
        "steps": steps,
    }


__all__ = ["MAX_HOPS", "distance", "ladder", "query_plan"]
