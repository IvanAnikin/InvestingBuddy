"""Electricity layer classifier — V3.20 slice 5.2 (spec §3.5).

Assigns a company to layers of the electricity stack (``taxonomy.LAYERS``) from text the
platform holds and can cite. Deterministic and pure: the caller loads the inputs, this
module only reads them.

Evidence, in order of weight
============================
1. **Segment revenue.** A reported segment (inline-XBRL business-segment or product axis)
   whose *name* contains a layer anchor maps that segment's revenue to the layer. The
   revenue share by layer is computed from one period, one currency, one scale and one
   axis — never summed across axes, which overlap. Geographic members never map.
2. **Business description text.** A layer is supported by text when the text contains at
   least ``MIN_TEXT_ANCHORS`` distinct anchors of that layer — one stray word ("charger")
   does not make a company an EV-charging business.

A layer with neither is never assigned. Every assigned layer carries its quotes (anchor
span plus the sentence around it) and the source they came from.

Primary layer
=============
The layer with the largest revenue share when any segment mapped; otherwise the layer with
the most distinct text anchors (ties broken by taxonomy order). ``primary_basis`` says
which.

Exclusions
==========
If the text matches an exclusion rule (general mining, O&G E&P/refining, coal), the
company is ``excluded`` unless the dominant-end-market test passes on the *disclosed*
segment share that mapped to the taxonomy. A text-only classification has no disclosed
share and so cannot override an exclusion.
"""

from __future__ import annotations

import re
from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from app.services.electricity import taxonomy as tx

#: Distinct anchors of one layer a text needs before it supports that layer.
MIN_TEXT_ANCHORS = 2
#: Quotes kept per layer.
MAX_QUOTES_PER_LAYER = 4
#: Characters of context either side of an anchor in a quote.
QUOTE_CONTEXT = 90

STATUS_CLASSIFIED = "classified"
STATUS_EXCLUDED = "excluded"
STATUS_NOT_ELECTRICITY = "not_electricity"
STATUS_NO_EVIDENCE = "no_evidence"

BASIS_SEGMENT_REVENUE = "segment_revenue"
BASIS_TEXT_ANCHORS = "text_anchors"

AXIS_BUSINESS_SEGMENT = "business_segment"
AXIS_PRODUCT = "product"
AXIS_GEOGRAPHIC = "geographic"


@dataclass(frozen=True)
class CitedText:
    """A text the platform holds, with where it came from."""

    text: str
    source_kind: str  # e.g. "company_profile", "annual_report_business_section"
    source_url: str | None = None
    as_of: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class SegmentRevenue:
    """One active segment-scoped revenue fact."""

    name: str  # scope_name as stored, incl. " (product)" / " (geographic)" suffix
    value: Decimal
    currency: str | None
    period: str
    scale: str | None = None
    doc_date: str | None = None
    source_url: str | None = None
    fact_id: str | None = None


def axis_of(scope_name: str) -> str:
    low = scope_name.strip().lower()
    if low.endswith("(geographic)"):
        return AXIS_GEOGRAPHIC
    if low.endswith("(product)"):
        return AXIS_PRODUCT
    return AXIS_BUSINESS_SEGMENT


def _bare_name(scope_name: str) -> str:
    return re.sub(r"\s*\((?:geographic|product)\)\s*$", "", scope_name, flags=re.IGNORECASE)


def _quote(text: str, start: int, end: int) -> str:
    left = max(0, start - QUOTE_CONTEXT)
    right = min(len(text), end + QUOTE_CONTEXT)
    # Widen to word boundaries so a quote never starts or ends mid-word.
    while left > 0 and not text[left - 1].isspace():
        left -= 1
    while right < len(text) and not text[right].isspace():
        right += 1
    snippet = " ".join(text[left:right].split())
    return ("…" if left > 0 else "") + snippet + ("…" if right < len(text) else "")


def _segment_layer(name: str) -> tuple[str | None, list[str], list[tx.AnchorHit]]:
    """The one layer a segment name maps to (most distinct anchors, taxonomy order)."""
    hits = tx.match_layers(_bare_name(name))
    if not hits:
        return None, [], []
    counts: dict[str, set[str]] = defaultdict(set)
    for h in hits:
        counts[h.layer].add(h.anchor)
    best = max(tx.LAYER_KEYS, key=lambda k: (len(counts.get(k, ())), -tx.LAYER_KEYS.index(k)))
    chosen = [h for h in hits if h.layer == best]
    subs = sorted({h.sub_segment for h in chosen if h.sub_segment})
    return best, subs, chosen


def _period_rows(rows: Sequence[SegmentRevenue]) -> list[SegmentRevenue]:
    """Rows of the newest period with one currency and one scale (positive values only)."""
    usable = [r for r in rows if r.value is not None and r.value > 0 and r.period]
    if not usable:
        return []
    newest = max(usable, key=lambda r: (r.doc_date or "", r.period))
    out: list[SegmentRevenue] = []
    seen: set[str] = set()
    for r in usable:
        # The same segment extracted twice (inline XBRL and PDF) counts once: the caller
        # orders the better reading first.
        name = _bare_name(r.name).casefold()
        if (r.period == newest.period and r.currency == newest.currency
                and r.scale == newest.scale and name not in seen):
            seen.add(name)
            out.append(r)
    return out


def _revenue_shares(segments: Sequence[SegmentRevenue]) -> dict[str, Any] | None:
    """Per-layer revenue shares from the best axis, or None when no segment mapped."""
    by_axis: dict[str, list[SegmentRevenue]] = defaultdict(list)
    for row in segments:
        axis = axis_of(row.name)
        if axis != AXIS_GEOGRAPHIC:
            by_axis[axis].append(row)
    best: dict[str, Any] | None = None
    for axis in (AXIS_BUSINESS_SEGMENT, AXIS_PRODUCT):
        rows = _period_rows(by_axis.get(axis, []))
        total = sum((r.value for r in rows), Decimal(0))
        if not rows or total <= 0:
            continue
        layer_value: dict[str, Decimal] = defaultdict(Decimal)
        layer_subs: dict[str, set[str]] = defaultdict(set)
        mapped_rows: list[dict[str, Any]] = []
        for r in rows:
            lay, subs, hits = _segment_layer(r.name)
            mapped_rows.append({
                "segment": _bare_name(r.name), "layer": lay,
                "revenue": float(r.value), "currency": r.currency, "period": r.period,
                "share": round(float(r.value / total), 4),
                "anchors": sorted({h.anchor for h in hits}),
                "source_url": r.source_url, "fact_id": r.fact_id,
            })
            if lay:
                layer_value[lay] += r.value
                layer_subs[lay].update(subs)
        if not layer_value:
            continue
        mapped = sum(layer_value.values(), Decimal(0))
        candidate = {
            "axis": axis, "period": rows[0].period, "currency": rows[0].currency,
            "total_revenue": float(total),
            "shares": {k: round(float(v / total), 4) for k, v in layer_value.items()},
            "sub_segments": {k: sorted(v) for k, v in layer_subs.items()},
            "mapped_share": round(float(mapped / total), 4),
            "segments": mapped_rows,
        }
        if best is None or candidate["mapped_share"] > best["mapped_share"]:
            best = candidate
    return best


def _text_support(texts: Sequence[CitedText]) -> dict[str, dict[str, Any]]:
    """Per layer: distinct anchors, sub-segments and quotes found across ``texts``."""
    out: dict[str, dict[str, Any]] = {}
    for doc in texts:
        for h in tx.match_layers(doc.text or ""):
            slot = out.setdefault(h.layer, {"anchors": set(), "sub_segments": set(),
                                            "quotes": []})
            if h.anchor in slot["anchors"]:
                continue
            slot["anchors"].add(h.anchor)
            if h.sub_segment:
                slot["sub_segments"].add(h.sub_segment)
            if len(slot["quotes"]) < MAX_QUOTES_PER_LAYER:
                slot["quotes"].append({
                    "anchor": h.anchor, "matched": h.quote,
                    "quote": _quote(doc.text, h.start, h.end),
                    "source_kind": doc.source_kind, "source_url": doc.source_url,
                    "as_of": doc.as_of, "label": doc.label,
                })
    return out


def classify(texts: Sequence[CitedText],
             segments: Sequence[SegmentRevenue] = ()) -> dict[str, Any]:
    """The layer classification as a plain dict (JSON-safe, report-ready)."""
    revenue = _revenue_shares(segments)
    support = _text_support(texts)
    exclusion_hits: list[tuple[tx.ExclusionRule, str]] = []
    for doc in texts:
        for rule, anchor in tx.match_exclusions(doc.text or ""):
            if all(r.key != rule.key for r, _ in exclusion_hits):
                exclusion_hits.append((rule, anchor))

    layers: dict[str, dict[str, Any]] = {}
    for key in tx.LAYER_KEYS:
        rev_share = (revenue or {}).get("shares", {}).get(key)
        text = support.get(key)
        text_ok = text is not None and len(text["anchors"]) >= MIN_TEXT_ANCHORS
        if rev_share is None and not text_ok:
            continue
        subs = set((revenue or {}).get("sub_segments", {}).get(key, []))
        if text_ok and text is not None:
            subs |= text["sub_segments"]
        layers[key] = {
            "layer": key,
            "display_name": getattr(tx.layer(key), "display_name", key),
            "sub_segments": sorted(subs),
            "revenue_share": rev_share,
            "text_anchor_count": len(text["anchors"]) if text else 0,
            "evidence": list(text["quotes"]) if text else [],
            "segments": [s["segment"] for s in (revenue or {}).get("segments", [])
                         if s["layer"] == key],
        }

    primary: str | None = None
    basis: str | None = None
    if revenue and any(v.get("revenue_share") for v in layers.values()):
        primary = max(layers, key=lambda k: (layers[k]["revenue_share"] or 0,
                                             -tx.LAYER_KEYS.index(k)))
        basis = BASIS_SEGMENT_REVENUE
    elif layers:
        primary = max(layers, key=lambda k: (layers[k]["text_anchor_count"],
                                             -tx.LAYER_KEYS.index(k)))
        basis = BASIS_TEXT_ANCHORS

    power_share = revenue["mapped_share"] if revenue else None
    exclusion: dict[str, Any] | None = None
    if exclusion_hits:
        rule, anchor = exclusion_hits[0]
        exclusion = {
            "rule": rule.key, "display_name": rule.display_name, "anchor": anchor,
            "power_share": power_share,
            "threshold": tx.DOMINANT_END_MARKET_SHARE,
            "override_passed": tx.dominant_end_market_passes(power_share),
            "other_rules": [r.key for r, _ in exclusion_hits[1:]],
        }

    if exclusion and not exclusion["override_passed"]:
        status = STATUS_EXCLUDED
    elif layers:
        status = STATUS_CLASSIFIED
    elif support or revenue or any((t.text or "").strip() for t in texts) or segments:
        status = STATUS_NOT_ELECTRICITY
    else:
        status = STATUS_NO_EVIDENCE

    ordered = ([primary] if primary else []) + [k for k in layers if k != primary]
    classified = status == STATUS_CLASSIFIED
    return {
        "taxonomy_version": tx.TAXONOMY_VERSION,
        "status": status,
        "primary_layer": primary if classified else None,
        "primary_basis": basis if classified else None,
        "layers": [layers[k] for k in ordered] if classified else [],
        "candidate_layers": [layers[k] for k in ordered] if not classified else [],
        "sub_segments": sorted({s for k in ordered for s in layers[k]["sub_segments"]})
        if classified else [],
        "revenue_by_layer": revenue,
        "exclusion": exclusion,
        "theme_tags": tx.theme_tags_for(ordered, sorted(
            {s for k in ordered for s in layers[k]["sub_segments"]})) if classified else [],
        "rules": {"min_text_anchors": MIN_TEXT_ANCHORS,
                  "dominant_end_market_share": tx.DOMINANT_END_MARKET_SHARE},
    }


__all__ = [
    "BASIS_SEGMENT_REVENUE",
    "BASIS_TEXT_ANCHORS",
    "STATUS_CLASSIFIED",
    "STATUS_EXCLUDED",
    "STATUS_NOT_ELECTRICITY",
    "STATUS_NO_EVIDENCE",
    "CitedText",
    "SegmentRevenue",
    "axis_of",
    "classify",
]
