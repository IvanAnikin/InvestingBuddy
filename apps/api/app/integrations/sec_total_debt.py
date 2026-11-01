"""Total debt from SEC XBRL: compatible concepts, one period (ADR-067).

WHAT WENT WRONG
===============
Cross-sector QA campaign 2026-11, defect JPM-1: a live report said "Total debt 64,776m"
and called a debt/equity of 0.179 "conservative". 64,776 was JPMorgan's
``ShortTermBorrowings`` alone. The filer last tagged ``LongTermDebt`` in 2013 (withheld,
correctly, by the own-period rule) and now tags
``LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities``, which the
normalizer did not know. Total debt was then "derived" as whichever leg was present: a
partial sum published with a warning nobody reads.

The same derivation also summed any long-term alias with any current alias. In the
US-GAAP taxonomy those concepts overlap:

* ``LongTermDebt`` and ``LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities``
  INCLUDE the current portion of long-term debt;
* ``LongTermDebtNoncurrent`` and ``LongTermDebtAndCapitalLeaseObligations`` EXCLUDE it;
* ``LongTermDebtCurrent`` (and ``LongTermDebtAndCapitalLeaseObligationsCurrent``) IS it;
* ``ShortTermBorrowings`` (``CommercialPaper`` is part of it) excludes it;
* ``DebtCurrent`` is all current debt: short-term borrowings + current maturities.

So ``LongTermDebt + LongTermDebtCurrent`` or ``LongTermDebt + DebtCurrent`` counts the
current maturities twice. Replayed over 19 large filers, 7 of them were overstated that
way (see ADR-067).

THE RULE
========
Total debt is published only as one of these sums, every term at the bundle's
reporting period:

  (b) noncurrent LTD + ``DebtCurrent``
  (c) noncurrent LTD + current LTD + short-term borrowings
  (a) LTD including current maturities + short-term borrowings

A noncurrent figure may be derived as (LTD including current - current LTD) when both
are tagged for the period and the noncurrent concept is not. A short-term term that is
not tagged for the period is accepted only with a basis note that says so; the long-term
term is never optional. When no long-term debt concept is tagged for the period, there
is no total debt — not a partial one.

What the two legs mean (``current`` / ``noncurrent`` here; ``short_term_debt`` /
``long_term_debt`` on the normalized record):

* ``current`` — debt due within one year: short-term borrowings plus current maturities
  of long-term debt (what ``DebtCurrent`` measures).
* ``noncurrent`` — long-term debt due after one year.

``total == current + noncurrent`` whenever both legs are set. A leg is ``None`` when the
total's basis cannot be split into them (basis (a) without a tagged current portion);
the total still stands, because its basis is complete.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

#: Long-term debt INCLUDING its current maturities. First alias tagged for the period wins.
LTD_INCLUDING_CURRENT: tuple[str, ...] = (
    "LongTermDebt",
    "LongTermDebtAndCapitalLeaseObligationsIncludingCurrentMaturities",
)
#: Long-term debt EXCLUDING its current maturities (the noncurrent balance-sheet line).
LTD_NONCURRENT: tuple[str, ...] = (
    "LongTermDebtNoncurrent",
    "LongTermDebtAndCapitalLeaseObligations",
)
#: The current maturities of long-term debt.
LTD_CURRENT: tuple[str, ...] = (
    "LongTermDebtCurrent",
    "LongTermDebtAndCapitalLeaseObligationsCurrent",
)
#: Short-term borrowings, excluding current maturities of long-term debt.
#: ``CommercialPaper`` is a component of ``ShortTermBorrowings`` and is read only when
#: the filer tags no ``ShortTermBorrowings`` for the period.
SHORT_TERM_BORROWINGS: tuple[str, ...] = ("ShortTermBorrowings", "CommercialPaper")
#: All current debt: short-term borrowings + current maturities of long-term debt.
DEBT_CURRENT: tuple[str, ...] = ("DebtCurrent",)

ALL_LONG_TERM: tuple[str, ...] = LTD_INCLUDING_CURRENT + LTD_NONCURRENT
ALL_CURRENT: tuple[str, ...] = DEBT_CURRENT + SHORT_TERM_BORROWINGS + LTD_CURRENT

#: Two figures closer than this (USD millions) are the same balance-sheet amount.
SAME_AMOUNT_TOLERANCE_M = 0.5

_MILLION = 1_000_000


@dataclass(frozen=True)
class DebtComponent:
    """One concept's value at the reporting period, with where it came from."""

    role: str
    concept: str
    value: float  # USD millions
    end: str
    accn: str | None = None
    form: str | None = None
    fy: int | None = None
    fp: str | None = None
    filed: str | None = None
    derived: bool = False


@dataclass
class TotalDebt:
    """The derivation's result. ``total`` is ``None`` unless the basis is compatible."""

    total: float | None = None
    current: float | None = None
    noncurrent: float | None = None
    combination: str | None = None
    formula: str | None = None
    components: list[DebtComponent] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: True when no long-term debt concept is tagged for the period: total is withheld.
    long_term_missing: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "combination": self.combination,
            "formula": self.formula,
            "total": self.total,
            "current": self.current,
            "noncurrent": self.noncurrent,
            "components": [
                {"role": c.role, "concept": c.concept, "value": c.value, "end": c.end,
                 "accn": c.accn, "derived": c.derived}
                for c in self.components
            ],
            "notes": list(self.notes),
        }


def _parse(value: str | None) -> datetime | None:
    try:
        return datetime.strptime((value or "")[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return None


def _fy(entry: dict) -> int | None:
    fy = entry.get("fy")
    return fy if isinstance(fy, int) else None


def component_at_period(
    us_gaap: dict[str, Any],
    concepts: Iterable[str],
    *,
    role: str,
    period_end: datetime,
    forms: Collection[str],
    prefer_accn: str | None,
    tolerance_days: int,
) -> DebtComponent | None:
    """The first alias with a balance at ``period_end`` in a filing of ``forms``.

    Alias order decides between concepts; within a concept the reporting filing
    (``prefer_accn``) is preferred, then the most recently filed. A flow (an entry
    with a ``start``) is not a balance and is ignored.
    """
    for concept in concepts:
        rows = ((us_gaap.get(concept) or {}).get("units") or {}).get("USD") or []
        matches = []
        for row in rows:
            if row.get("val") is None or row.get("start") or row.get("form") not in forms:
                continue
            end = _parse(row.get("end"))
            if end is None or abs((end - period_end).days) > tolerance_days:
                continue
            matches.append(row)
        if not matches:
            continue
        best = max(
            matches,
            key=lambda r: (bool(prefer_accn) and r.get("accn") == prefer_accn,
                           r.get("filed") or ""),
        )
        return DebtComponent(
            role=role,
            concept=concept,
            value=round(float(best["val"]) / _MILLION, 2),
            end=str(best.get("end")),
            accn=best.get("accn"),
            form=best.get("form"),
            fy=_fy(best),
            fp=best.get("fp"),
            filed=best.get("filed"),
        )
    return None


def _last_tagged(
    us_gaap: dict[str, Any], concepts: Iterable[str], forms: Collection[str]
) -> tuple[str, str] | None:
    """(concept, latest balance date) across ``concepts`` in ``forms``; for basis notes."""
    best: tuple[str, str] | None = None
    for concept in concepts:
        rows = ((us_gaap.get(concept) or {}).get("units") or {}).get("USD") or []
        for row in rows:
            end = row.get("end")
            if row.get("val") is None or row.get("start") or not end:
                continue
            if row.get("form") not in forms:
                continue
            if best is None or end > best[1]:
                best = (concept, end)
    return best


def _not_tagged_note(
    us_gaap: dict[str, Any], what: str, concepts: tuple[str, ...], forms: Collection[str]
) -> str:
    last = _last_tagged(us_gaap, concepts, forms)
    tail = (
        f" (last tagged as {last[0]} for the period ending {last[1]})" if last else ""
    )
    return f"no {what} tagged for the period{tail}; none is included"


def _same(a: float, b: float) -> bool:
    return abs(a - b) <= SAME_AMOUNT_TOLERANCE_M


def derive_total_debt(
    us_gaap: dict[str, Any],
    *,
    period_end: str | None,
    forms: Collection[str],
    prefer_accn: str | None,
    tolerance_days: int,
) -> TotalDebt:
    """Total debt at ``period_end`` from compatible concepts only. Never a partial sum."""
    end = _parse(period_end)
    if end is None:
        return TotalDebt(long_term_missing=True,
                         notes=["no reporting period to read debt balances for"])

    def at(concepts: tuple[str, ...], role: str) -> DebtComponent | None:
        return component_at_period(
            us_gaap, concepts, role=role, period_end=end, forms=forms,
            prefer_accn=prefer_accn, tolerance_days=tolerance_days,
        )

    incl = at(LTD_INCLUDING_CURRENT, "long_term_debt_including_current")
    noncur = at(LTD_NONCURRENT, "long_term_debt_noncurrent")
    cur = at(LTD_CURRENT, "long_term_debt_current")
    stb = at(SHORT_TERM_BORROWINGS, "short_term_borrowings")
    debt_cur = at(DEBT_CURRENT, "debt_current")
    notes: list[str] = []

    if incl is None and noncur is None:
        return TotalDebt(
            long_term_missing=True,
            notes=["no long-term debt concept tagged for the period; total debt withheld"],
        )

    # A filer that tags ONE balance-sheet line with both a short-term-borrowings concept
    # and a current-maturities concept has not reported two balances. Summing both
    # counts the line twice (seen live: the "short-term debt" line of two of 19 filers).
    stb_is_cur_line = stb is not None and cur is not None and _same(stb.value, cur.value)
    if stb is not None and cur is not None and stb_is_cur_line:  # (narrows for mypy)
        notes.append(
            f"{stb.concept} equals {cur.concept} ({stb.value:,.2f}m) — one balance-sheet "
            "line tagged with both concepts; counted once, as current maturities"
        )
        stb = None

    if noncur is None and incl is not None and cur is not None and incl.value >= cur.value:
        noncur = DebtComponent(
            role="long_term_debt_noncurrent",
            concept=f"{incl.concept} - {cur.concept}",
            value=round(incl.value - cur.value, 2),
            end=incl.end, accn=incl.accn, form=incl.form, fy=incl.fy, fp=incl.fp,
            filed=incl.filed, derived=True,
        )

    def done(
        combination: str,
        parts: list[DebtComponent],
        current: float | None,
        noncurrent: float | None,
        extra_notes: list[str] | None = None,
    ) -> TotalDebt:
        total = round(sum(p.value for p in parts), 2)
        formula = " + ".join(f"{p.concept} ({p.value:,.2f}m)" for p in parts)
        return TotalDebt(
            total=total,
            current=None if current is None else round(current, 2),
            noncurrent=None if noncurrent is None else round(noncurrent, 2),
            combination=combination,
            formula=formula,
            components=parts,
            notes=notes + (extra_notes or []),
        )

    # ── Complete bases ───────────────────────────────────────────────────
    if noncur is not None and debt_cur is not None:
        consistent = (cur is None or debt_cur.value + SAME_AMOUNT_TOLERANCE_M >= cur.value) and (
            stb is None or debt_cur.value + SAME_AMOUNT_TOLERANCE_M >= stb.value
        )
        if consistent:
            return done("noncurrent+debt_current", [noncur, debt_cur],
                        debt_cur.value, noncur.value)
        notes.append(
            f"{debt_cur.concept} ({debt_cur.value:,.2f}m) is smaller than a component "
            "of it; not used"
        )
    if noncur is not None and cur is not None and stb is not None:
        return done("noncurrent+current_ltd+short_term_borrowings", [noncur, cur, stb],
                    cur.value + stb.value, noncur.value)
    if incl is not None and stb is not None:
        return done("including_current+short_term_borrowings", [incl, stb], None, None,
                    ["current and noncurrent legs not split: the current maturities of "
                     "long-term debt are not tagged for the period"])

    # ── Bases with a short-term term not tagged for the period ───────────
    if noncur is not None and cur is not None:
        return done("noncurrent+current_ltd", [noncur, cur], cur.value, noncur.value,
                    [] if stb_is_cur_line else
                    [_not_tagged_note(us_gaap, "short-term borrowings",
                                      SHORT_TERM_BORROWINGS, forms)])
    if incl is not None:
        extra = [
            _not_tagged_note(us_gaap, "short-term borrowings", SHORT_TERM_BORROWINGS, forms)
        ]
        if debt_cur is not None:
            extra.append(
                f"{debt_cur.concept} not combined with {incl.concept}: both contain the "
                "current maturities of long-term debt"
            )
        extra.append("current and noncurrent legs not split")
        return done("including_current", [incl], None, None, extra)
    if noncur is None:  # unreachable: incl is None here and one of the two is set
        return TotalDebt(long_term_missing=True, notes=notes)
    if stb is not None:
        return done("noncurrent+short_term_borrowings", [noncur, stb], stb.value,
                    noncur.value,
                    [_not_tagged_note(us_gaap, "current maturities of long-term debt",
                                      LTD_CURRENT + DEBT_CURRENT, forms)])
    return done("noncurrent", [noncur], None, noncur.value,
                [_not_tagged_note(us_gaap, "current debt", ALL_CURRENT, forms)])


__all__ = [
    "ALL_CURRENT",
    "ALL_LONG_TERM",
    "DEBT_CURRENT",
    "LTD_CURRENT",
    "LTD_INCLUDING_CURRENT",
    "LTD_NONCURRENT",
    "SHORT_TERM_BORROWINGS",
    "DebtComponent",
    "TotalDebt",
    "component_at_period",
    "derive_total_debt",
]
