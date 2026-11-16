"""Precedent transactions — V3.20 slice 8.3.

Three parts:

* ``extract_deals(text)`` — pure, deterministic. Finds sentences such as "Acme has agreed
  to acquire Grid Co for an enterprise value of USD 1.2 billion" and returns the parties,
  the amount, its currency and whether the figure is an enterprise value or an equity
  consideration, with the sentence as the excerpt. A sentence that also states "12.5x
  EBITDA" yields the implied metric (EV ÷ multiple), labelled as derived from the text.
  Nothing is inferred beyond what the sentence says; an ambiguous currency is skipped.
* ``scan_company_documents`` — runs the extractor over a company's held document chunks
  and stores each new deal as a ``proposed`` row with the document's URL, publication
  date and retrieval time. A deal without a publication date is not proposed.
* ``precedent_block`` — pure. The APPROVED rows in a layer within ``MAX_AGE_YEARS``: each
  with its multiple and source, and the median per metric when at least
  ``MIN_FOR_MEDIAN`` rows share it. Precedent multiples include a control premium; the
  block says so.
"""

from __future__ import annotations

import re
import statistics
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

BLOCK_VERSION = "2026-11-16.1"
MAX_AGE_YEARS = 10
MIN_FOR_MEDIAN = 3
MAX_CHUNKS = 400
MULTIPLE_KEYS = {"ebit": "ev_to_ebit", "ebitda": "ev_to_ebitda", "revenue": "ev_to_revenue",
                 "capacity_mw": "ev_per_mw"}

_CCY = {"us$": "USD", "usd": "USD", "$": "USD", "€": "EUR", "eur": "EUR", "£": "GBP",
        "gbp": "GBP", "a$": "AUD", "aud": "AUD", "c$": "CAD", "cad": "CAD", "chf": "CHF",
        "sek": "SEK", "nok": "NOK", "dkk": "DKK", "jpy": "JPY", "¥": "JPY"}
_SCALE = {"billion": Decimal("1e9"), "bn": Decimal("1e9"), "million": Decimal("1e6"),
          "mn": Decimal("1e6"), "m": Decimal("1e6")}

_NAME = (r"[A-Z][\w&.'\-]*(?:\s+(?:[A-Z][\w&.'\-]*|of|and|&|de|du|la|AG|SA|SE|plc|"
         r"Inc\.?|Ltd\.?|N\.V\.|S\.A\.|GmbH)){0,7}")
_VERB = (r"(?:has\s+|have\s+)?(?:agreed\s+to\s+acquire|will\s+acquire|to\s+acquire|"
         r"(?:has\s+)?completed\s+(?:the|its)\s+acquisition\s+of|has\s+acquired|acquired|"
         r"(?:has\s+)?entered\s+into\s+a\s+definitive\s+agreement\s+to\s+acquire)")
_AMOUNT = (r"(?P<ccy>US\$|A\$|C\$|USD|EUR|GBP|AUD|CAD|CHF|SEK|NOK|DKK|JPY|\$|€|£|¥)\s?"
           r"(?P<amt>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>billion|bn|million|mn|m)\b")
_DEAL_RE = re.compile(
    rf"(?P<acq>{_NAME})\s+{_VERB}\s+(?P<tgt>{_NAME})"
    rf"(?P<mid>[^.;]{{0,120}}?)\b(?:for|valued\s+at|at)\b[^.;\d$€£¥]{{0,60}}{_AMOUNT}",
)
_MULT_RE = re.compile(r"(?P<x>\d+(?:\.\d+)?)\s?x\s+(?:\w+\s+){0,3}?"
                      r"(?P<metric>EBITDA|EBIT|revenue|sales)\b", re.IGNORECASE)
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z])")
_STOP_SUFFIX = re.compile(r"\s+(?:for|in|at|from|which|that|valued)$", re.IGNORECASE)


def _amount(raw: str, scale: str) -> Decimal | None:
    try:
        return Decimal(raw.replace(",", "")) * _SCALE[scale.lower()]
    except (InvalidOperation, KeyError):
        return None


def _clean_name(name: str) -> str:
    return _STOP_SUFFIX.sub("", name.strip(" ,"))[:200]


def extract_deals(text: str) -> list[dict[str, Any]]:
    """Deals stated in ``text``; one per sentence at most."""
    out: list[dict[str, Any]] = []
    for sentence in _SENTENCE_RE.split(text or ""):
        sentence = " ".join(sentence.split())
        if len(sentence) > 600:
            continue
        m = _DEAL_RE.search(sentence)
        if not m:
            continue
        ccy = _CCY.get(m.group("ccy").lower())
        value = _amount(m.group("amt"), m.group("scale"))
        acquirer, target = _clean_name(m.group("acq")), _clean_name(m.group("tgt"))
        if not ccy or value is None or value <= 0 or not acquirer or not target \
                or acquirer.lower() == target.lower():
            continue
        basis = ("enterprise_value" if re.search(r"enterprise\s+value", sentence, re.I)
                 else "consideration")
        deal: dict[str, Any] = {"acquirer_name": acquirer, "target_name": target,
                                "currency": ccy, "enterprise_value": value,
                                "value_basis": basis, "excerpt": sentence[:1000]}
        mult = _MULT_RE.search(sentence)
        if mult and basis == "enterprise_value":
            x = Decimal(mult.group("x"))
            key = {"ebitda": "ebitda", "ebit": "ebit", "revenue": "revenue",
                   "sales": "revenue"}[mult.group("metric").lower()]
            if x > 0:
                deal.update(metric_key=key, metric_value=(value / x).quantize(Decimal("1")),
                            metric_period="as stated in the announcement (derived: EV / "
                                          f"{x}x)")
        out.append(deal)
    return out


def _f(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def precedent_block(rows: Sequence[Any], *, layer: str | None, today: date
                    ) -> dict[str, Any] | None:
    """The APPROVED rows in ``layer`` no older than ``MAX_AGE_YEARS`` (pure)."""
    if not layer:
        return None
    cutoff = date(today.year - MAX_AGE_YEARS, today.month, min(today.day, 28))
    usable = [r for r in rows if getattr(r, "status", None) == "approved"
              and getattr(r, "layer", None) == layer and r.announced_date >= cutoff]
    if not usable:
        return None
    items: list[dict[str, Any]] = []
    by_key: dict[str, list[float]] = {}
    for r in sorted(usable, key=lambda r: r.announced_date, reverse=True):
        mult = _f(r.multiple)
        key = MULTIPLE_KEYS.get(r.metric_key or "")
        items.append({
            "id": str(r.id), "acquirer": r.acquirer_name, "target": r.target_name,
            "announced_date": r.announced_date.isoformat(), "currency": r.currency,
            "enterprise_value": _f(r.enterprise_value), "value_basis": r.value_basis,
            "metric_key": r.metric_key, "metric_value": _f(r.metric_value),
            "metric_period": r.metric_period, "multiple_key": key,
            "multiple": None if mult is None else round(mult, 2),
            "source_url": r.source_url, "source_title": r.source_title,
            "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None,
            "entry_method": r.entry_method,
        })
        if key and mult is not None and r.value_basis == "enterprise_value":
            by_key.setdefault(key, []).append(mult)
    medians = {k: {"median": round(statistics.median(v), 2), "count": len(v)}
               for k, v in by_key.items() if len(v) >= MIN_FOR_MEDIAN}
    return {
        "version": BLOCK_VERSION, "layer": layer, "max_age_years": MAX_AGE_YEARS,
        "transactions": items, "medians": medians,
        "median_rule": (f"a median per multiple from at least {MIN_FOR_MEDIAN} approved "
                        "deals stated on an enterprise-value basis"),
        "note": ("Acquisitions in the same electricity layer, each cited and approved by an "
                 "admin. A takeover price includes a control premium, so a precedent "
                 "multiple is typically above a trading multiple."),
    }


def precedent_peer_multiple(block: Mapping[str, Any] | None,
                            ev_block: Mapping[str, Any] | None) -> Any:
    """The engine input ``multiple:precedent_ev_to_ebit``, or ``None``."""
    from app.services.calculations.valuation_engine import Cited, PeerMultiple

    median = ((block or {}).get("medians") or {}).get("ev_to_ebit")
    mult = next((m for m in (ev_block or {}).get("multiples") or []
                 if m.get("key") == "ev_to_ebit" and _f(m.get("ebit")) is not None), None)
    if not median or not mult or (_f(mult.get("ebit")) or 0) <= 0:
        return None
    ccy = ((ev_block or {}).get("enterprise_value") or {}).get("currency")
    return PeerMultiple(
        key="precedent_ev_to_ebit",
        subject_metric=Cited(value=float(mult["ebit"]), source_url=mult.get("source_url"),
                             period=mult.get("period"), currency=ccy),
        peer_median=Cited(value=float(median["median"]), source_url=None,
                          period=f"{median['count']} approved precedents in "
                                 f"{(block or {}).get('layer')}"),
    )


async def scan_company_documents(session: Any, company_id: uuid.UUID, *,
                                 now: datetime | None = None) -> dict[str, int]:
    """Propose the deals stated in the company's held document chunks. Never approves."""
    from sqlalchemy import or_, select

    from app.models.precedent_transaction import PrecedentTransaction as P
    from app.models.research_chunk import ResearchDocumentChunk as C
    from app.models.research_document import ResearchDocumentVersion as V

    now = now or datetime.now(timezone.utc)
    rows = (await session.execute(
        select(C.text, V.canonical_url, V.title, V.published_at, V.retrieved_at)
        .join(V, V.id == C.research_document_version_id)
        .where(C.company_id == company_id,
               or_(C.text.ilike("%acqui%"), C.text.ilike("%merger%")))
        .limit(MAX_CHUNKS))).all()
    found = proposed = undated = 0
    seen: set[tuple[str, str, date]] = set()
    for text, url, title, published, retrieved in rows:
        for deal in extract_deals(text):
            found += 1
            if published is None:
                undated += 1
                continue
            key = (deal["acquirer_name"], deal["target_name"], published)
            if key in seen:
                continue
            seen.add(key)
            exists = await session.scalar(select(P.id).where(
                P.acquirer_name == key[0], P.target_name == key[1],
                P.announced_date == published))
            if exists:
                continue
            session.add(P(
                id=uuid.uuid4(), acquirer_name=key[0], target_name=key[1],
                announced_date=published, currency=deal["currency"],
                enterprise_value=deal["enterprise_value"], value_basis=deal["value_basis"],
                metric_key=deal.get("metric_key"), metric_value=deal.get("metric_value"),
                metric_period=deal.get("metric_period"), source_url=url,
                source_title=(title or None) and str(title)[:500],
                source_excerpt=deal["excerpt"], retrieved_at=retrieved or now,
                entry_method="extracted", status="proposed", created_at=now))
            proposed += 1
    await session.flush()
    return {"chunks_read": len(rows), "deals_found": found, "proposed": proposed,
            "skipped_undated": undated}


async def approved_in_layer(session: Any, layer: str) -> list[Any]:
    from sqlalchemy import select

    from app.models.precedent_transaction import PrecedentTransaction as P

    return list((await session.execute(
        select(P).where(P.layer == layer, P.status == "approved")
        .order_by(P.announced_date.desc()).limit(200))).scalars().all())


__all__ = ["BLOCK_VERSION", "MIN_FOR_MEDIAN", "approved_in_layer", "extract_deals",
           "precedent_block", "precedent_peer_multiple", "scan_company_documents"]
