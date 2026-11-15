"""Per-company passive ownership — V3.20 slice 3.3 (spec §3.3).

Reads what the offline N-PORT ingest stored (``security_passive_ownership``) for ONE
company's securities and states, for the newest period:

* each security's holders (fund count, ETF count, index-fund count, shares, value, the
  top funds), and the change from the previous period;
* **% of shares outstanding held by US-registered funds / ETFs** — labelled exactly so,
  because N-PORT covers only US-registered funds (UCITS and other non-US funds are not
  in it). The denominator is the SEC cover-page ``dei:EntityCommonStockSharesOutstanding``
  and its date is always shown;
* concentration: the top three funds' share of the passive holding.

ADR-061 (issuer ≠ security): rows are found by the securities' ISINs (and the CUSIP a
US / CA ISIN embeds) and by the issuer's LEI. Only ordinary / class shares count toward
the %; a depositary receipt represents underlying shares at a ratio and an LEI-only
match may be any instrument of the issuer, so those are listed but never added.

The % is withheld (with a reason) when there is no denominator, when it exceeds 100 %
(a data error, never shown), or when nothing counts toward it.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

SecFetch = Callable[[str, str], Awaitable[tuple[int, bytes | None]]]

COVER_SHARES_URL = ("https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/dei/"
                    "EntityCommonStockSharesOutstanding.json")
PCT_LABEL = ("Held by US-registered funds and ETFs only (SEC Form N-PORT). UCITS and other "
             "non-US funds are not included.")
FRESHNESS_NOTE = ("N-PORT holdings are as of each fund's report date; funds file 50–59 days "
                  "after it, so the newest data set is 2–5 months old.")
COUNTED_SECURITY_TYPES = frozenset({"ordinary_share", "class_share"})


@dataclass(frozen=True)
class CoverShares:
    """The cover-page share count of one filing at one date (all classes summed)."""

    shares: Decimal
    as_of: date
    accession: str | None
    form: str | None
    filed: str | None
    classes: int
    source_url: str

    def to_dict(self) -> dict[str, Any]:
        return {"shares": float(self.shares), "as_of": self.as_of.isoformat(),
                "accession": self.accession, "form": self.form, "filed": self.filed,
                "classes": self.classes, "source_url": self.source_url,
                "concept": "dei:EntityCommonStockSharesOutstanding"}


def cover_shares_url(cik: str) -> str:
    return COVER_SHARES_URL.format(cik=str(int(cik)).zfill(10))


def parse_cover_shares(payload: dict[str, Any], *, source_url: str) -> CoverShares | None:
    """The newest cover-page date; at that date the newest filing; its rows summed
    (a multi-class issuer reports one row per class)."""
    rows = [r for r in ((payload.get("units") or {}).get("shares") or [])
            if r.get("val") is not None and r.get("end")]
    if not rows:
        return None
    latest = max(r["end"] for r in rows)
    at_date = [r for r in rows if r["end"] == latest]
    newest = max(at_date, key=lambda r: (r.get("filed") or "", r.get("accn") or ""))
    same = [r for r in at_date if r.get("accn") == newest.get("accn")]
    try:
        total = sum((Decimal(str(r["val"])) for r in same), Decimal(0))
        as_of = date.fromisoformat(latest)
    except (ArithmeticError, ValueError):
        return None
    if total <= 0:
        return None
    return CoverShares(shares=total, as_of=as_of, accession=newest.get("accn"),
                       form=newest.get("form"), filed=newest.get("filed"),
                       classes=len(same), source_url=source_url)


async def fetch_cover_shares(cik: str, *, fetch: SecFetch | None = None
                             ) -> tuple[CoverShares | None, str | None]:
    """``(shares, None)`` or ``(None, reason)``. Never raises."""
    get: SecFetch
    if fetch is None:  # pragma: no cover - network
        from app.integrations.sec_relationships import _default_fetch

        get = _default_fetch
    else:
        get = fetch
    url = cover_shares_url(cik)
    try:
        status, body = await get(url, "application/json")
    except Exception as exc:  # noqa: BLE001 - a missing denominator is a stated state
        return None, f"denominator_fetch_failed: {type(exc).__name__}"
    if status == 404:
        return None, "no_cover_page_share_count"
    if status != 200 or not body:
        return None, f"denominator_http_{status}"
    try:
        parsed = parse_cover_shares(json.loads(body), source_url=url)
    except (ValueError, TypeError):
        return None, "denominator_unreadable"
    return (parsed, None) if parsed else (None, "no_cover_page_share_count")


def _num(value: Any) -> float | None:
    return float(value) if value is not None else None


def _pct(part: Decimal | None, whole: Decimal | None) -> float | None:
    if part is None or not whole:
        return None
    return round(float(part / whole * 100), 2)


def _cusip(isin: str) -> str | None:
    return isin[2:11] if isin[:2] in ("US", "CA") and len(isin) == 12 else None


async def _identifiers(session: Any, entity_id: uuid.UUID) -> tuple[
        dict[tuple[str, str], tuple[uuid.UUID | None, str]], str | None, str | None]:
    """``{(kind, identifier): (security_id, security_type)}``, the LEI and the CIK."""
    from sqlalchemy import select

    from app.models.legal_entity import EntityIdentifier, Security

    out: dict[tuple[str, str], tuple[uuid.UUID | None, str]] = {}
    rows = await session.execute(
        select(Security.id, Security.security_type, EntityIdentifier.value_normalized)
        .join(EntityIdentifier, EntityIdentifier.security_id == Security.id)
        .where(Security.legal_entity_id == entity_id, EntityIdentifier.scheme == "isin"))
    for security_id, security_type, isin in rows.all():
        if not isin:
            continue
        out[("isin", isin)] = (security_id, security_type)
        cusip = _cusip(isin)
        if cusip:
            out[("cusip", cusip)] = (security_id, security_type)
    lei = cik = None
    rows = await session.execute(
        select(EntityIdentifier.scheme, EntityIdentifier.value_normalized).where(
            EntityIdentifier.legal_entity_id == entity_id,
            EntityIdentifier.scheme.in_(("lei", "cik"))))
    for scheme, value in rows.all():
        if scheme == "lei" and value and lei is None:
            lei = value
        elif scheme == "cik" and value and cik is None:
            cik = value
    if lei:
        out[("lei", lei)] = (None, "issuer")
    return out, lei, cik


def _row_dict(row: Any, security_id: uuid.UUID | None, security_type: str,
              previous: Any | None) -> dict[str, Any]:
    counted = row.identifier_kind != "lei" and security_type in COUNTED_SECURITY_TYPES
    reason = None
    if row.identifier_kind == "lei":
        reason = "matched_by_issuer_lei_only"
    elif not counted:
        reason = f"security_type_{security_type}"
    out: dict[str, Any] = {
        "identifier_kind": row.identifier_kind, "identifier": row.identifier,
        "security_id": str(security_id) if security_id else None,
        "security_type": security_type, "counted_in_pct": counted,
        "not_counted_reason": reason,
        "issuer_name": row.issuer_name, "isin": row.isin, "cusip": row.cusip,
        "fund_count": row.fund_count, "etf_count": row.etf_count,
        "etf_unknown_count": row.etf_unknown_count,
        "index_fund_count": row.index_fund_count,
        "shares_held": _num(row.shares_held), "etf_shares_held": _num(row.etf_shares_held),
        "value_usd": _num(row.value_usd), "non_share_holdings": row.non_share_holdings,
        "report_date_min": row.report_date_min.isoformat() if row.report_date_min else None,
        "report_date_max": row.report_date_max.isoformat() if row.report_date_max else None,
        "top_funds": list(row.top_funds_json or []),
        "source_url": row.source_url, "period": row.period,
        "previous": None,
    }
    if previous is not None:
        delta = (None if row.shares_held is None or previous.shares_held is None
                 else row.shares_held - previous.shares_held)
        out["previous"] = {
            "period": previous.period, "fund_count": previous.fund_count,
            "shares_held": _num(previous.shares_held),
            "change_shares": _num(delta),
            "change_pct": _pct(delta, previous.shares_held),
            "change_fund_count": row.fund_count - previous.fund_count,
        }
    return out


def _concentration(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Top three funds' share of the counted passive holding (share units only)."""
    shares = [float(f["shares"]) for r in rows if r["counted_in_pct"]
              for f in r["top_funds"] if f.get("shares") and f.get("unit", "shares") == "shares"]
    total = sum(r["shares_held"] or 0 for r in rows if r["counted_in_pct"])
    if not shares or not total:
        return None
    top3 = sum(sorted(shares, reverse=True)[:3])
    return {"top3_share_of_passive_pct": round(top3 / total * 100, 2),
            "basis": "top three fund positions ÷ shares held by all US-registered funds"}


async def company_passive_ownership(
    session: Any, *, company: Any, cfg: Any, fetch: SecFetch | None = None,
) -> dict[str, Any]:
    """The per-company view. Never raises for missing data: every gap is a state."""
    from sqlalchemy import or_, select

    from app.models.passive_ownership import SOURCE_NPORT, SecurityPassiveOwnership

    base: dict[str, Any] = {
        "company_id": str(company.id), "legal_entity_id": None, "state": "ok",
        "period": None, "previous_period": None, "securities": [],
        "shares_held": None, "etf_shares_held": None,
        "pct_of_shares_outstanding": None, "etf_pct_of_shares_outstanding": None,
        "pct_label": PCT_LABEL, "pct_withheld_reason": None, "denominator": None,
        "holdings_as_of": None, "concentration": None,
        "source": "SEC Form N-PORT data sets (US-registered funds)",
        "freshness_note": FRESHNESS_NOTE,
    }
    if not getattr(cfg, "v3_entity_master_enabled", False):
        return base | {"state": "disabled"}
    entity_id = getattr(company, "legal_entity_id", None)
    if entity_id is None:
        return base | {"state": "entity_unresolved"}
    base["legal_entity_id"] = str(entity_id)
    ids, _lei, cik = await _identifiers(session, entity_id)
    if not ids:
        return base | {"state": "no_identifiers"}
    clauses = [(SecurityPassiveOwnership.identifier_kind == kind)
               & (SecurityPassiveOwnership.identifier == value) for kind, value in ids]
    stored = (await session.execute(select(SecurityPassiveOwnership).where(
        SecurityPassiveOwnership.source_kind == SOURCE_NPORT, or_(*clauses)))).scalars().all()
    if not stored:
        return base | {"state": "not_held_or_not_ingested"}
    period = max(r.period for r in stored)
    earlier = sorted({r.period for r in stored if r.period < period})
    prev_period = earlier[-1] if earlier else None
    prev_of = {(r.identifier_kind, r.identifier): r for r in stored if r.period == prev_period}
    rows = []
    for r in sorted((r for r in stored if r.period == period),
                    key=lambda r: (r.identifier_kind == "lei", r.identifier)):
        security_id, security_type = ids.get((r.identifier_kind, r.identifier), (None, "other"))
        rows.append(_row_dict(r, security_id, security_type,
                              prev_of.get((r.identifier_kind, r.identifier))))
    counted = [r for r in rows if r["counted_in_pct"]]
    shares = (sum((Decimal(str(r["shares_held"])) for r in counted if r["shares_held"]),
                  Decimal(0)) if counted else None)
    etf_shares = (sum((Decimal(str(r["etf_shares_held"])) for r in counted
                       if r["etf_shares_held"]), Decimal(0)) if counted else None)
    dates = [r["report_date_max"] for r in rows if r["report_date_max"]]
    out = base | {"period": period, "previous_period": prev_period, "securities": rows,
                  "shares_held": _num(shares), "etf_shares_held": _num(etf_shares),
                  "holdings_as_of": max(dates) if dates else None,
                  "concentration": _concentration(rows)}
    if not counted or not shares:
        return out | {"pct_withheld_reason": "no_ordinary_share_holdings"}
    if not cik:
        return out | {"pct_withheld_reason": "no_sec_cik_for_cover_page_share_count"}
    cover, reason = await fetch_cover_shares(cik, fetch=fetch)
    if cover is None:
        return out | {"pct_withheld_reason": reason}
    out["denominator"] = cover.to_dict()
    pct = _pct(shares, cover.shares)
    if pct is None or pct > 100:
        return out | {"pct_withheld_reason": "exceeds_100_pct_data_error"}
    return out | {"pct_of_shares_outstanding": pct,
                  "etf_pct_of_shares_outstanding": _pct(etf_shares, cover.shares)}


__all__ = [
    "COUNTED_SECURITY_TYPES",
    "CoverShares",
    "PCT_LABEL",
    "company_passive_ownership",
    "cover_shares_url",
    "fetch_cover_shares",
    "parse_cover_shares",
]
