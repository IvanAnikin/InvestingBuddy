"""Customers / Suppliers / Parent & group for the professional report — V3.20 slice 2.6.

Pure: it reads the company's relationship view (``entities.relationship_view``, already
serialised) and what this run's relationship steps did (``outcome.relationships``), and
returns one block, attached to the report's ``business_model`` section.

THREE STATES PER GROUP, NEVER TWO
=================================
* ``disclosed`` — at least one cited edge;
* ``not_disclosed`` — the sources were read and named nothing. Said as such: "not
  disclosed in the sources read" is a fact about the disclosures, not that the company
  has no customers;
* ``not_researched`` — no step read a source for this group (off, failed, not an SEC
  registrant, no model). The reader is told why, so an empty group is never mistaken
  for a disclosure.

Every row keeps its citation (source URL, evidence ref, excerpt, period, as-of). A row
whose excerpt trips the shared safety scan loses the excerpt (the URL stays); a row
whose NAME trips it is withheld and counted — the report itself must never be withheld
for a counterparty's wording.

``customer_concentration`` fills the contract's ``business.customer_concentration``
datapoint from the largest disclosed single-customer share of revenue (newest period);
an aggregate ("three customers 76 %") is kept as context, never as the single value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from typing import Any

STATE_DISCLOSED = "disclosed"
STATE_NOT_DISCLOSED = "not_disclosed"
STATE_NOT_RESEARCHED = "not_researched"

#: Step states that mean the step READ its source (whatever it found).
_READ_STATES = frozenset({"written", "none_tagged", "none_verified", "already_read"})
_MAX_ROWS = {"customers": 25, "suppliers": 25, "parents": 5, "ultimate_parents": 5,
             "significant_holders": 20, "subsidiaries": 40, "holdings": 20}
_EXCERPT_MAX = 400
_SOURCE_NAMES = {
    "xbrl_dimension": "10-K inline XBRL",
    "text_llm_verified": "company document (verified quote)",
    "gleif_api": "GLEIF Level 2",
    "exhibit_21": "10-K Exhibit 21",
    "filing_xml": "SEC Schedule 13D/13G",
}
_FY_RE = re.compile(r"FY(\d{4})")
_AGGREGATE_HINT = re.compile(r"\b(customers|clients|total|combined|aggregate|and)\b", re.I)


def _safe(value: Any) -> bool:
    from app.services import safety_terms

    return not safety_terms.scan_value(value, path="relationship")


def _row(edge: Mapping[str, Any]) -> dict[str, Any] | None:
    method = edge.get("extraction_method")
    excerpt = str(edge.get("evidence_excerpt") or "")[:_EXCERPT_MAX] or None
    row = {
        "name": str(edge.get("counterparty_name") or "")[:300],
        "resolved": bool(edge.get("counterparty_resolved")),
        "share_pct": edge.get("disclosed_share_pct"),
        "share_basis": edge.get("share_basis"),
        "period": edge.get("period") or None,
        "as_of": edge.get("as_of"),
        "source_name": _SOURCE_NAMES.get(str(method), str(edge.get("source") or "")),
        "source_kind": edge.get("source_kind"),
        "extraction_method": method,
        "source_url": edge.get("source_url"),
        "evidence_ref": edge.get("evidence_ref"),
        "excerpt": excerpt,
        "confidence": edge.get("confidence"),
    }
    if not row["name"] or not _safe(row["name"]):
        return None
    if excerpt and not _safe(excerpt):
        row["excerpt"] = None
        row["excerpt_withheld"] = True
    if not (row["source_url"] or row["evidence_ref"]):
        return None  # no uncited row reaches the page
    return row


def _rows(view: Mapping[str, Any], group: str) -> tuple[list[dict[str, Any]], int, int]:
    """(rows, withheld for safety, omitted over the cap)."""
    edges = (view.get("groups") or {}).get(group) or []
    rows: list[dict[str, Any]] = []
    withheld = 0
    for edge in edges:
        row = _row(edge)
        if row is None:
            withheld += 1
        else:
            rows.append(row)
    cap = _MAX_ROWS.get(group, 20)
    return rows[:cap], withheld, max(0, len(rows) - cap)


def _researched(steps: Mapping[str, Any], *names: tuple[str, ...]) -> tuple[bool, list[str]]:
    """Whether any named step read its source, and why not when none did."""
    reasons: list[str] = []
    for path in names:
        node: Any = steps
        for key in path:
            node = node.get(key) if isinstance(node, Mapping) else None
        state = node.get("state") if isinstance(node, Mapping) else None
        if state in _READ_STATES:
            return True, []
        label = " ".join(path).replace("_", " ")
        reasons.append(f"{label}: {state or 'not run'}")
    return False, reasons


def _group_state(rows: Sequence[Any], researched: bool) -> str:
    if rows:
        return STATE_DISCLOSED
    return STATE_NOT_DISCLOSED if researched else STATE_NOT_RESEARCHED


def _year(period: str | None) -> int:
    match = _FY_RE.search(period or "")
    return int(match.group(1)) if match else 0


def customer_concentration(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any] | None:
    """The largest disclosed single-customer share of revenue, newest period."""
    revenue = [r for r in rows if r.get("share_basis") == "revenue"
               and isinstance(r.get("share_pct"), (int, float)) and _year(r.get("period"))]
    if not revenue:
        return None
    newest = max(_year(r.get("period")) for r in revenue)
    current = [r for r in revenue if _year(r.get("period")) == newest]
    single = [r for r in current if not _AGGREGATE_HINT.search(str(r.get("name")))]
    aggregate = [r for r in current if r not in single]
    if not single:
        return None
    top = max(single, key=lambda r: float(r["share_pct"]))
    note = (f"Largest single customer disclosed: {top['name']}, {top['share_pct']:g}% of "
            f"revenue, {top['period']}.")
    if aggregate:
        biggest = max(aggregate, key=lambda r: float(r["share_pct"]))
        note += (f" The filer also discloses {biggest['name']} at {biggest['share_pct']:g}% "
                 "of revenue together.")
    return {
        "value": float(top["share_pct"]),
        "unit": "%",
        "as_of": top.get("as_of"),
        "period": top.get("period"),
        "source_name": top.get("source_name"),
        "source_url": top.get("source_url"),
        "evidence_ref": top.get("evidence_ref"),
        "note": note,
    }


_NOT_DISCLOSED_NOTE = {
    "customers": ("No customer is named or tagged in the sources read (the 10-K's inline "
                  "XBRL, the company's own documents). Not disclosed there is not the same "
                  "as having no concentrated customers."),
    "suppliers": ("No supplier is named in the company's documents read so far. Not "
                  "disclosed there is not the same as having no critical supplier."),
    "parent_and_group": ("No parent, significant holder or subsidiary was found in the "
                         "registries and filings read."),
}


def relationship_block(
    view: Mapping[str, Any] | None, steps: Mapping[str, Any] | None,
) -> dict[str, Any] | None:
    """The block, or ``None`` when the relationship graph is off (the report is then
    unchanged)."""
    if not view or view.get("state") == "disabled":
        return None
    steps = steps or {}
    if view.get("state") == "entity_unresolved":
        reason = "the company is not linked to a legal entity yet"
        return {
            "state": "entity_unresolved",
            "customers": {"state": STATE_NOT_RESEARCHED, "rows": [], "note": reason},
            "suppliers": {"state": STATE_NOT_RESEARCHED, "rows": [], "note": reason},
            "parent_and_group": {"state": STATE_NOT_RESEARCHED, "note": reason},
            "customer_concentration": None,
            "withheld_for_safety": 0,
            "note": str(view.get("note") or ""),
        }
    withheld = 0
    groups: dict[str, list[dict[str, Any]]] = {}
    omitted: dict[str, int] = {}
    for group in _MAX_ROWS:
        groups[group], hidden, over = _rows(view, group)
        withheld += hidden
        if over:
            omitted[group] = over

    customers_read, customer_why = _researched(
        steps, ("sec_filings", "customer_concentration"), ("document_counterparties",))
    suppliers_read, supplier_why = _researched(steps, ("document_counterparties",))
    parents_read, parent_why = _researched(
        steps, ("gleif_parents",), ("sec_filings",))
    exceptions = [
        {k: e.get(k) for k in ("relationship_type", "reason", "reason_label", "source",
                               "source_url", "as_of")}
        for e in view.get("reporting_exceptions") or []
    ]

    def section(name: str, rows: list[dict[str, Any]], read: bool,
                why: list[str]) -> dict[str, Any]:
        state = _group_state(rows, read)
        note = (_NOT_DISCLOSED_NOTE[name] if state == STATE_NOT_DISCLOSED else
                "Not researched in this run (" + "; ".join(why) + ")."
                if state == STATE_NOT_RESEARCHED else None)
        return {"state": state, "rows": rows, "note": note}

    customers = section("customers", groups["customers"], customers_read, customer_why)
    suppliers = section("suppliers", groups["suppliers"], suppliers_read, supplier_why)
    group_rows = [*groups["parents"], *groups["ultimate_parents"],
                  *groups["significant_holders"], *groups["subsidiaries"], *groups["holdings"]]
    parent_state = _group_state(group_rows or exceptions, parents_read)
    parent = {
        "state": parent_state,
        "direct_parents": groups["parents"],
        "ultimate_parents": groups["ultimate_parents"],
        "reporting_exceptions": exceptions,
        "significant_holders": groups["significant_holders"],
        "subsidiaries": groups["subsidiaries"],
        "holdings": groups["holdings"],
        "note": (_NOT_DISCLOSED_NOTE["parent_and_group"] if parent_state ==
                 STATE_NOT_DISCLOSED else "Not researched in this run ("
                 + "; ".join(parent_why) + ")." if parent_state == STATE_NOT_RESEARCHED
                 else None),
    }
    return {
        "state": "ok",
        "customers": customers,
        "suppliers": suppliers,
        "parent_and_group": parent,
        "customer_concentration": customer_concentration(groups["customers"]),
        "omitted": omitted,
        "withheld_for_safety": withheld,
        "note": str(view.get("note") or ""),
    }


__all__ = [
    "STATE_DISCLOSED",
    "STATE_NOT_DISCLOSED",
    "STATE_NOT_RESEARCHED",
    "customer_concentration",
    "relationship_block",
]
