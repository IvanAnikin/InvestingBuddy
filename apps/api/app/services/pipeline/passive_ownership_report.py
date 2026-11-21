"""The report's "Passive ownership" block — V3.20 slice 3.4 (spec §3.3).

Pure: built from the per-company view (``passive_ownership.view``, slice 3.3) and placed
in ``sections[valuation_context].passive_ownership``. It states:

* the % of shares outstanding held by US-registered funds / ETFs, ALWAYS with its label
  ("UCITS and other non-US funds are not included") and its denominator's date — or,
  when withheld, why;
* the funds (name, sponsor, ETF yes / no / not stated, shares, value, weight in the
  fund, report date), merged across the company's counted share lines;
* the change since the previous data set, and top-three concentration;
* a theme flag on a fund whose NAME names a theme the vertical studies (grid,
  electrification, clean energy, small cap, …). It is a name match, labelled as one —
  not a statement of the fund's mandate.

Every fund name passes the shared safety scan; a flagged one drops that fund (counted),
so a fund's own name can never withhold the report.
"""

from __future__ import annotations

import re
from typing import Any

MAX_FUNDS = 15

#: (flag, pattern over the fund name). Names only: the flag says so.
THEME_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (flag, re.compile(pattern, re.IGNORECASE)) for flag, pattern in (
        ("grid_and_electrification",
         r"\b(smart[\s-]?grid|grid|electrif\w*|power|utilit\w*|infrastructure)\b"),
        ("clean_energy", r"\b(clean|renewable|solar|wind|energy transition|hydrogen)\b"),
        ("nuclear", r"\b(nuclear|uranium)\b"),
        ("materials_for_electrification", r"\b(copper|lithium|battery|batteries)\b"),
        ("small_cap", r"\b(small[\s-]?cap|micro[\s-]?cap|russell 2000|smallcap)\b"),
    )
)

STATE_HELD = "held"
STATE_NONE_FOUND = "none_found"
STATE_NOT_RESEARCHED = "not_researched"

_NOT_RESEARCHED_NOTE = {
    "entity_unresolved": "The company is not yet in the entity master, so its securities "
                         "could not be matched to fund holdings.",
    "no_identifiers": "No ISIN or LEI is known for this company, so fund holdings could "
                      "not be matched.",
}
_NONE_FOUND_NOTE = ("No US-registered fund reported a position in the data sets ingested so "
                    "far, or the newest data set has not been ingested. This is not a "
                    "statement that no fund owns the company.")


def _safe(value: Any) -> bool:
    from app.services import safety_terms

    return not safety_terms.scan_value(value, path="passive_ownership")


def theme_flags(name: str | None) -> list[str]:
    return [flag for flag, pattern in THEME_PATTERNS if name and pattern.search(name)]


def _etf_text(value: Any) -> str:
    return {True: "yes", False: "no"}.get(value, "not_stated")


def _funds(securities: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    """Funds across the counted share lines, largest share position first."""
    merged: dict[str, dict[str, Any]] = {}
    for security in securities:
        if not security.get("counted_in_pct"):
            continue
        for fund in security.get("top_funds") or []:
            if fund.get("unit", "shares") != "shares":
                continue
            key = fund.get("series_id") or fund.get("name") or ""
            row = merged.get(key)
            if row is None:
                merged[key] = row = {
                    "series_id": fund.get("series_id"), "name": fund.get("name"),
                    "sponsor": fund.get("sponsor"), "is_etf": _etf_text(fund.get("is_etf")),
                    "shares": 0.0, "value_usd": None, "pct_of_fund": fund.get("pct_of_fund"),
                    "report_date": fund.get("report_date"),
                    "theme_flags": theme_flags(fund.get("name")),
                }
            row["shares"] += float(fund.get("shares") or 0)
            if fund.get("value_usd") is not None:
                row["value_usd"] = (row["value_usd"] or 0.0) + float(fund["value_usd"])
    withheld = 0
    out = []
    for row in sorted(merged.values(), key=lambda r: r["shares"], reverse=True):
        if not _safe({"name": row["name"], "sponsor": row["sponsor"]}):
            withheld += 1
            continue
        out.append(row)
    return out, withheld


def _change(securities: list[dict[str, Any]]) -> dict[str, Any] | None:
    counted = [s for s in securities if s.get("counted_in_pct") and s.get("previous")]
    if not counted:
        return None
    delta = [s["previous"].get("change_shares") for s in counted]
    before = [s["previous"].get("shares_held") for s in counted]
    if any(v is None for v in delta) or any(v is None for v in before):
        return None
    total_before = sum(before)
    change = sum(delta)
    return {
        "previous_period": counted[0]["previous"].get("period"),
        "change_shares": change,
        "change_pct": round(change / total_before * 100, 2) if total_before else None,
        "change_fund_count": sum(s["previous"].get("change_fund_count") or 0 for s in counted),
    }


def not_ingested_block() -> dict[str, Any]:
    """No N-PORT data set has been ingested yet: nothing was looked up."""
    from app.services.passive_ownership.view import FRESHNESS_NOTE, PCT_LABEL

    return {"state": STATE_NOT_RESEARCHED, "reason": "no_data_set_ingested",
            "note": "No SEC N-PORT data set has been ingested yet, so fund holdings were "
                    "not looked up.",
            "pct_label": PCT_LABEL, "source": "SEC Form N-PORT data sets (US-registered funds)",
            "freshness_note": FRESHNESS_NOTE}


def issuer_block(issuer: dict[str, Any] | None) -> dict[str, Any] | None:
    """V3.20 slice 3.6 — sponsor files: US funds (dated, never added) and UCITS funds."""
    if not issuer:
        return None

    def keep(funds: list[dict[str, Any]]) -> list[dict[str, Any]]:
        out = []
        for f in funds[:MAX_FUNDS]:
            if not (_safe(f.get("name")) and _safe(f.get("fund_ticker"))):
                continue
            out.append({k: f.get(k) for k in ("fund_ticker", "name", "sponsor", "as_of",
                                              "shares", "weight_pct", "source_url")}
                       | {"theme_flags": theme_flags(f.get("name"))})
        return out

    return {"as_of": issuer.get("as_of"), "source": issuer.get("source"),
            "precedence": issuer.get("precedence"),
            "us_funds": keep(list(issuer.get("us_funds") or [])),
            "ucits_funds": keep(list(issuer.get("ucits_funds") or [])),
            "ucits_shares_held": issuer.get("ucits_shares_held")}


def passive_ownership_block(view: dict[str, Any] | None) -> dict[str, Any] | None:
    """``None`` when the view is absent or switched off: the report is then unchanged."""
    if not view or view.get("state") == "disabled":
        return None
    state = view.get("state")
    base: dict[str, Any] = {
        "pct_label": view.get("pct_label"),
        "source": view.get("source"),
        "freshness_note": view.get("freshness_note"),
        "theme_flag_basis": "Theme flags match words in the fund's NAME only; they do not "
                            "describe the fund's mandate.",
    }
    issuer = issuer_block(view.get("issuer_files"))
    if issuer is not None:
        base["issuer_files"] = issuer
    if state in _NOT_RESEARCHED_NOTE:
        return base | {"state": STATE_NOT_RESEARCHED, "reason": state,
                       "note": _NOT_RESEARCHED_NOTE[state]}
    if state != "ok":
        return base | {"state": STATE_NONE_FOUND, "note": _NONE_FOUND_NOTE}
    securities = list(view.get("securities") or [])
    funds, withheld = _funds(securities)
    denominator = view.get("denominator") or None
    other_lines = [
        {"identifier_kind": s["identifier_kind"], "identifier": s["identifier"],
         "reason": s.get("not_counted_reason"), "fund_count": s.get("fund_count"),
         "shares_held": s.get("shares_held")}
        for s in securities if not s.get("counted_in_pct")
    ]
    counted = [s for s in securities if s.get("counted_in_pct")]
    return base | {
        "state": STATE_HELD,
        "period": view.get("period"),
        "holdings_as_of": view.get("holdings_as_of"),
        "pct_of_shares_outstanding": view.get("pct_of_shares_outstanding"),
        "etf_pct_of_shares_outstanding": view.get("etf_pct_of_shares_outstanding"),
        "pct_withheld_reason": view.get("pct_withheld_reason"),
        "denominator": ({k: denominator.get(k) for k in
                         ("shares", "as_of", "form", "accession", "classes", "source_url",
                          "concept")} if denominator else None),
        "shares_held": view.get("shares_held"),
        "etf_shares_held": view.get("etf_shares_held"),
        "fund_count": sum(s.get("fund_count") or 0 for s in counted),
        "etf_count": sum(s.get("etf_count") or 0 for s in counted),
        "etf_not_stated_count": sum(s.get("etf_unknown_count") or 0 for s in counted),
        "index_fund_count": sum(s.get("index_fund_count") or 0 for s in counted),
        "change": _change(securities),
        "concentration": view.get("concentration"),
        "funds": funds[:MAX_FUNDS],
        "funds_omitted": max(0, len(funds) - MAX_FUNDS),
        "withheld_for_safety": withheld,
        "themed_fund_count": sum(1 for f in funds if f["theme_flags"]),
        "other_lines": other_lines,
        "source_urls": sorted({s["source_url"] for s in securities if s.get("source_url")}),
        "note": ("Fund counts add the company's share lines; one fund holding two lines is "
                 "counted on each. Only the largest positions of each line are stored, so "
                 "the fund list can be shorter than the fund count."),
    }


__all__ = [
    "MAX_FUNDS",
    "issuer_block",
    "THEME_PATTERNS",
    "not_ingested_block",
    "passive_ownership_block",
    "theme_flags",
]
