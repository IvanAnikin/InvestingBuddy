"""SEC Form 4 insider transactions — V3.20 slice 6.2 (spec §3.6, ``insider_cluster_buy``).

Reads the issuer's recent Form 4 filings from EDGAR (the issuer's own ``submissions``
JSON lists every Form 4 filed about it), parses each one's raw ownership XML and keeps
the **non-derivative open-market purchases** — transaction code ``P`` with acquired /
disposed code ``A``. Grants (``A``), option exercises (``M``), gifts (``G``) and sales
(``S``) are not a purchase signal and are not kept.

An **insider cluster** is ``MIN_CLUSTER_INSIDERS`` or more distinct insiders buying in
the open market within ``CLUSTER_WINDOW_DAYS`` of one another. A joint filing (one Form 4
with several reporting owners, e.g. a fund and its general partner) is ONE insider —
keyed by the first reporting owner — so a single economic buyer never makes a cluster.

Every transaction keeps its filing URL, its accession and the filing date; prices are
the filer's own USD figures (Form 4 is a US form; a price the filer did not report stays
``None`` and no value is computed from it). The parser refuses a DTD / entity and a body
over ``MAX_XML_BYTES``. Network access goes through a ``fetch`` seam, the SEC rate
limiter and the research User-Agent.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import asdict, dataclass, field
from datetime import date, timedelta
from typing import Any

from app.integrations.sec_relationships import (
    MAX_XML_BYTES,
    _default_fetch,
    _local,
    recent_filings,
    submissions_url,
)
from app.services.sources.sec_filing_documents import build_document_url

CLUSTER_WINDOW_DAYS = 30
MIN_CLUSTER_INSIDERS = 3
#: Form 4 filings read per company (newest first) — a bound, reported when hit.
MAX_FORM4_FILINGS = 40
LOOKBACK_DAYS = 365

PURCHASE_CODE = "P"
FORM4_FORMS = frozenset({"4", "4/A"})

Fetch = Callable[[str, str], Awaitable[tuple[int, bytes | None]]]


@dataclass(frozen=True)
class InsiderTransaction:
    insider_key: str
    insider_name: str
    relationship: str | None
    transaction_date: date
    code: str
    acquired_disposed: str | None
    shares: float | None
    price_usd: float | None
    security_title: str | None
    accession: str | None = None
    filing_date: date | None = None
    source_url: str | None = None

    @property
    def is_open_market_purchase(self) -> bool:
        return self.code == PURCHASE_CODE and self.acquired_disposed in (None, "A")

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        for key in ("transaction_date", "filing_date"):
            out[key] = out[key].isoformat() if out[key] else None
        return out


@dataclass
class Form4Report:
    cik: str
    transactions: list[InsiderTransaction] = field(default_factory=list)
    filings_read: int = 0
    filings_available: int = 0
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


# ── Parsing ─────────────────────────────────────────────────────────────── #


def _child(el: ET.Element, name: str) -> ET.Element | None:
    return next((c for c in el if _local(c.tag) == name), None)


def _path_text(el: ET.Element, *names: str) -> str | None:
    """Text at a child path; a Form 4 wraps most leaves in ``<value>``."""
    node: ET.Element | None = el
    for name in names:
        node = _child(node, name) if node is not None else None
    if node is None:
        return None
    value = _child(node, "value")
    text = (value.text if value is not None else node.text) or ""
    return text.strip() or None


def _num(text: str | None) -> float | None:
    try:
        value = float(str(text).replace(",", "").strip())
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _iso(text: str | None) -> date | None:
    try:
        return date.fromisoformat(str(text).strip()[:10])
    except (TypeError, ValueError):
        return None


def _relationship(owner: ET.Element) -> str | None:
    rel = _child(owner, "reportingOwnerRelationship")
    if rel is None:
        return None

    def flag(name: str) -> bool:
        return (_path_text(rel, name) or "").lower() in ("1", "true")

    parts = []
    if flag("isDirector"):
        parts.append("director")
    if flag("isOfficer"):
        title = _path_text(rel, "officerTitle")
        parts.append(f"officer ({title})" if title else "officer")
    if flag("isTenPercentOwner"):
        parts.append("10% owner")
    if flag("isOther"):
        parts.append("other")
    return ", ".join(parts) or None


def parse_form4(body: bytes | None) -> list[InsiderTransaction] | None:
    """A Form 4 ownership XML → its non-derivative transactions. ``None`` when unreadable.

    Every non-derivative transaction is returned (callers filter purchases with
    ``is_open_market_purchase``); a transaction without a date or code is skipped.
    """
    if not body or len(body) > MAX_XML_BYTES:
        return None
    if b"<!DOCTYPE" in body[:4096].upper() or b"<!ENTITY" in body.upper():
        return None
    try:
        root = ET.fromstring(body)
    except ET.ParseError:
        return None
    if _local(root.tag) != "ownershipDocument":
        return None
    owners = [c for c in root if _local(c.tag) == "reportingOwner"]
    if not owners:
        return None
    first = owners[0]
    owner_id = _child(first, "reportingOwnerId")
    cik = _path_text(owner_id, "rptOwnerCik") if owner_id is not None else None
    name = (_path_text(owner_id, "rptOwnerName") if owner_id is not None else None) or ""
    if not (cik or name):
        return None
    key = f"cik:{int(cik)}" if cik and cik.isdigit() else f"name:{name.casefold()}"
    relationship = _relationship(first)
    if len(owners) > 1:
        relationship = ", ".join(x for x in (relationship, "joint filing") if x)

    out: list[InsiderTransaction] = []
    table = _child(root, "nonDerivativeTable")
    for tx in (table if table is not None else []):
        if _local(tx.tag) != "nonDerivativeTransaction":
            continue
        tx_date = _iso(_path_text(tx, "transactionDate"))
        code = (_path_text(tx, "transactionCoding", "transactionCode") or "").upper()
        if tx_date is None or not code:
            continue
        out.append(InsiderTransaction(
            insider_key=key,
            insider_name=name[:300],
            relationship=relationship,
            transaction_date=tx_date,
            code=code,
            acquired_disposed=(_path_text(
                tx, "transactionAmounts", "transactionAcquiredDisposedCode") or None),
            shares=_num(_path_text(tx, "transactionAmounts", "transactionShares")),
            price_usd=_num(_path_text(tx, "transactionAmounts", "transactionPricePerShare")),
            security_title=_path_text(tx, "securityTitle"),
        ))
    return out


# ── Clusters ────────────────────────────────────────────────────────────── #


def insider_clusters(
    transactions: Iterable[InsiderTransaction],
    *,
    window_days: int = CLUSTER_WINDOW_DAYS,
    min_insiders: int = MIN_CLUSTER_INSIDERS,
) -> list[dict[str, Any]]:
    """Open-market purchase clusters: maximal runs where every purchase is within
    ``window_days`` of the run's first, with ``min_insiders`` distinct insiders.

    Deterministic: purchases sorted by date then insider; overlapping windows that
    qualify are merged into one cluster (a cluster is reported once, not per day).
    """
    buys = sorted((t for t in transactions if t.is_open_market_purchase),
                  key=lambda t: (t.transaction_date, t.insider_key))
    windows: list[tuple[int, int]] = []
    for i, start in enumerate(buys):
        end = i
        while (end + 1 < len(buys) and
               (buys[end + 1].transaction_date - start.transaction_date).days <= window_days):
            end += 1
        if len({t.insider_key for t in buys[i:end + 1]}) >= min_insiders:
            windows.append((i, end))
    merged: list[list[int]] = []
    for i, end in windows:
        if merged and i <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([i, end])
    clusters = []
    for i, end in merged:
        members = buys[i:end + 1]
        insiders = sorted({t.insider_key for t in members})
        valued = [t.shares * t.price_usd for t in members
                  if t.shares is not None and t.price_usd is not None]
        clusters.append({
            "trigger_type": "insider_cluster_buy",
            "start_date": members[0].transaction_date.isoformat(),
            "end_date": members[-1].transaction_date.isoformat(),
            "distinct_insiders": len(insiders),
            "purchases": len(members),
            "shares": round(sum(t.shares or 0 for t in members), 4),
            # Only when every purchase reported both shares and price.
            "value_usd": round(sum(valued), 2) if len(valued) == len(members) else None,
            "currency": "USD",
            "insiders": [{"name": n, "relationship": r or None} for n, r in sorted(
                {(t.insider_name, t.relationship or "") for t in members})],
            "transactions": [t.to_dict() for t in members],
            "rule": (f">= {min_insiders} distinct insiders, open-market purchases "
                     f"(code P), within {window_days} days"),
        })
    return clusters


# ── The lookup ──────────────────────────────────────────────────────────── #


def raw_xml_name(primary_document: str | None) -> str | None:
    """EDGAR lists a Form 4's primary document as its XSL rendering
    (``xslF345X05/doc.xml``); the raw XML is the same name without the folder."""
    if not primary_document:
        return None
    name = str(primary_document).rsplit("/", 1)[-1].strip()
    return name if name.lower().endswith(".xml") else None


async def fetch_form4_transactions(
    cik: str,
    *,
    fetch: Fetch | None = None,
    today: date | None = None,
    lookback_days: int = LOOKBACK_DAYS,
    max_filings: int = MAX_FORM4_FILINGS,
) -> Form4Report:
    """The issuer's Form 4 transactions over the lookback. Never raises."""
    get = fetch or _default_fetch
    report = Form4Report(cik=str(cik))
    since = (today or date.today()) - timedelta(days=lookback_days)
    try:
        status, body = await get(submissions_url(cik), "application/json")
        if status != 200 or not body:
            report.notes.append(f"submissions unavailable (HTTP {status})")
            return report
        filings = [f for f in recent_filings(json.loads(body))
                   if f["form"] in FORM4_FORMS and f["filing_date"]
                   and f["filing_date"] >= since]
    except Exception as exc:  # noqa: BLE001 - an unreadable index is a note, not a crash
        report.notes.append(f"submissions unreadable ({type(exc).__name__})")
        return report
    report.filings_available = len(filings)
    report.truncated = len(filings) > max_filings
    for filing in filings[:max_filings]:
        url = build_document_url(cik, filing["accession"],
                                 raw_xml_name(filing["primary_document"]))
        if url is None:
            report.notes.append(f"no raw XML for {filing['accession']}")
            continue
        try:
            status, body = await get(url, "application/xml")
        except Exception as exc:  # noqa: BLE001
            report.notes.append(f"{filing['accession']}: {type(exc).__name__}")
            continue
        parsed = parse_form4(body) if status == 200 else None
        if parsed is None:
            report.notes.append(f"{filing['accession']}: unreadable (HTTP {status})")
            continue
        report.filings_read += 1
        for tx in parsed:
            report.transactions.append(InsiderTransaction(
                **{**asdict(tx), "accession": filing["accession"],
                   "filing_date": filing["filing_date"], "source_url": url}))
    return report


__all__ = [
    "CLUSTER_WINDOW_DAYS",
    "MAX_FORM4_FILINGS",
    "MIN_CLUSTER_INSIDERS",
    "Form4Report",
    "InsiderTransaction",
    "fetch_form4_transactions",
    "insider_clusters",
    "parse_form4",
    "raw_xml_name",
]
