"""Financial depth from SEC XBRL — V3.20 slice 8.5 (spec §3.9), US filers.

Pure parsing of ``companyconcept`` payloads (``sec_actuals.facts_of``) into three series,
each fact with its accession and filing date:

* **Debt maturity ladder** — the six ``LongTermDebtMaturitiesRepaymentsOfPrincipal…``
  buckets (next 12 months, years 2–5, after year 5) as of ONE balance-sheet date: the
  newest year-end any annual filing tagged, each bucket from the newest annual filing
  for that date. A bucket the filer did not tag is ``not_disclosed``, never zero; the
  total is given only when all six are tagged.
* **Dilution** — ``WeightedAverageNumberOfDilutedSharesOutstanding`` per fiscal year
  (duration ≈ one year, from annual filings), newest filing's value for each year, and
  the year-on-year change. A split shows as a step change; it is not adjusted here, and
  ``latest_filing_restated`` says when a later filing reported a different count for
  the same year (the split signal).
* **Contract assets / liabilities** — ``ContractWithCustomerAssetNet`` and
  ``ContractWithCustomerLiability`` at each fiscal year-end: quality-of-earnings inputs
  (unbilled revenue; customer advances and deferred revenue), shown as a series, not a
  verdict.
"""

from __future__ import annotations

from collections.abc import Mapping
from decimal import Decimal
from typing import Any

from app.services.sources.sec_actuals import YEAR_DAYS, Fact, facts_of

ANNUAL_FORMS = frozenset({"10-K", "10-K/A", "10-KT"})
_PREFIX = "LongTermDebtMaturitiesRepaymentsOfPrincipal"
LADDER: tuple[tuple[str, str], ...] = (
    ("next_12_months", f"{_PREFIX}InNextTwelveMonths"),
    ("year_2", f"{_PREFIX}InYearTwo"),
    ("year_3", f"{_PREFIX}InYearThree"),
    ("year_4", f"{_PREFIX}InYearFour"),
    ("year_5", f"{_PREFIX}InYearFive"),
    ("after_year_5", f"{_PREFIX}AfterYearFive"),
)
DILUTED_SHARES = "WeightedAverageNumberOfDilutedSharesOutstanding"
CONTRACT_ASSETS = "ContractWithCustomerAssetNet"
CONTRACT_LIABILITIES = "ContractWithCustomerLiability"
ALL_CONCEPTS: tuple[str, ...] = (*(c for _, c in LADDER), DILUTED_SHARES, CONTRACT_ASSETS,
                                 CONTRACT_LIABILITIES)
MAX_YEARS = 6

NOT_DISCLOSED = "not_disclosed"
DISCLOSED = "disclosed"


def _currency(unit: str) -> str | None:
    head = unit.split("/")[0].upper()
    return head if len(head) == 3 and head.isalpha() else None


def _instants(payload: Mapping[str, Any] | None, concept: str) -> list[Fact]:
    return [f for f in facts_of(dict(payload or {}), concept=concept)
            if f.start is None and f.form in ANNUAL_FORMS and _currency(f.unit)]


def _newest_by_end(facts: list[Fact]) -> dict[Any, Fact]:
    """Per period end, the fact from the newest annual filing."""
    out: dict[Any, Fact] = {}
    for f in facts:
        cur = out.get(f.end)
        if cur is None or f.filed > cur.filed:
            out[f.end] = f
    return out


def _row(f: Fact, *, source_url: str | None) -> dict[str, Any]:
    return {"value": float(f.value), "currency": _currency(f.unit), "unit": f.unit,
            "accession": f.accession, "form": f.form, "filed": f.filed.isoformat(),
            "source_url": source_url}


def debt_ladder(payloads: Mapping[str, Mapping[str, Any] | None], *,
                url_for: Any = None) -> dict[str, Any]:
    by_bucket = {key: _newest_by_end(_instants(payloads.get(concept), concept))
                 for key, concept in LADDER}
    ends = {end for facts in by_bucket.values() for end in facts}
    if not ends:
        return {"state": NOT_DISCLOSED,
                "reason": "no debt maturity bucket is tagged in an annual filing"}
    as_of = max(ends)
    buckets = []
    for key, concept in LADDER:
        f = by_bucket[key].get(as_of)
        buckets.append({"bucket": key, "concept": f"us-gaap:{concept}",
                        **(_row(f, source_url=url_for(f.accession) if url_for else None)
                           if f else {"state": NOT_DISCLOSED, "value": None})})
    tagged = [b for b in buckets if b.get("value") is not None]
    currencies = {b["currency"] for b in tagged}
    total = (float(sum(Decimal(str(b["value"])) for b in tagged))
             if len(tagged) == len(LADDER) and len(currencies) == 1 else None)
    return {"state": DISCLOSED, "as_of": as_of.isoformat(), "buckets": buckets,
            "buckets_tagged": len(tagged), "total": total,
            "currency": next(iter(currencies)) if len(currencies) == 1 else None,
            "total_note": None if total is not None else
            "a total needs all six buckets tagged in one currency"}


def dilution_series(payload: Mapping[str, Any] | None, *, url_for: Any = None
                    ) -> dict[str, Any]:
    facts = [f for f in facts_of(dict(payload or {}), concept=DILUTED_SHARES)
             if f.start is not None and f.within(YEAR_DAYS) and f.form in ANNUAL_FORMS]
    if not facts:
        return {"state": NOT_DISCLOSED,
                "reason": "no annual diluted weighted-average share count is tagged"}
    by_period: dict[tuple[Any, Any], list[Fact]] = {}
    for f in facts:
        by_period.setdefault((f.start, f.end), []).append(f)
    rows = []
    for (start, end), group in sorted(by_period.items(), key=lambda kv: kv[0][1]):
        first = min(group, key=lambda f: f.filed)
        latest = max(group, key=lambda f: f.filed)
        rows.append({"period_start": start.isoformat(), "period_end": end.isoformat(),
                     "shares": float(latest.value), "as_first_filed": float(first.value),
                     "latest_filing_restated": first.value != latest.value,
                     "accession": latest.accession, "filed": latest.filed.isoformat(),
                     "source_url": url_for(latest.accession) if url_for else None})
    rows = rows[-MAX_YEARS:]
    for prev, cur in zip(rows, rows[1:]):
        if prev["shares"]:
            cur["change_pct"] = round(100 * (cur["shares"] - prev["shares"]) / prev["shares"],
                                      2)
    return {"state": DISCLOSED, "concept": f"us-gaap:{DILUTED_SHARES}", "years": rows,
            "note": "Weighted-average diluted shares per fiscal year, newest filing's "
                    "figure; a split appears as a step change and is not adjusted."}


def contract_balances(payloads: Mapping[str, Mapping[str, Any] | None], *,
                      url_for: Any = None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, concept in (("contract_assets", CONTRACT_ASSETS),
                         ("contract_liabilities", CONTRACT_LIABILITIES)):
        by_end = _newest_by_end(_instants(payloads.get(concept), concept))
        if not by_end:
            out[key] = {"state": NOT_DISCLOSED, "concept": f"us-gaap:{concept}"}
            continue
        series = [{"as_of": end.isoformat(),
                   **_row(f, source_url=url_for(f.accession) if url_for else None)}
                  for end, f in sorted(by_end.items())][-MAX_YEARS:]
        out[key] = {"state": DISCLOSED, "concept": f"us-gaap:{concept}", "series": series}
    return out


def financial_depth_block(payloads: Mapping[str, Mapping[str, Any] | None], *,
                          url_for: Any = None, retrieved_at: str | None = None
                          ) -> dict[str, Any] | None:
    """The block, or ``None`` when none of the concepts is tagged at all."""
    ladder = debt_ladder(payloads, url_for=url_for)
    dilution = dilution_series(payloads.get(DILUTED_SHARES), url_for=url_for)
    contracts = contract_balances(payloads, url_for=url_for)
    if (ladder["state"] == NOT_DISCLOSED and dilution["state"] == NOT_DISCLOSED
            and all(v["state"] == NOT_DISCLOSED for v in contracts.values())):
        return None
    return {
        "source": "SEC XBRL companyconcept (us-gaap)",
        "retrieved_at": retrieved_at,
        "debt_maturity_ladder": ladder,
        "dilution": dilution,
        **contracts,
        "note": ("The issuer's own tagged figures from annual filings, each with its "
                 "filing. An untagged item is not disclosed in structured data, which is "
                 "not the same as zero."),
    }


__all__ = [
    "ALL_CONCEPTS",
    "LADDER",
    "contract_balances",
    "debt_ladder",
    "dilution_series",
    "financial_depth_block",
]
