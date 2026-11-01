"""Total debt from SEC XBRL: compatible concepts, one period, never partial (ADR-067).

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
* ``ShortTermBorrowings`` excludes it; ``CommercialPaper`` and
  ``OtherShortTermBorrowings`` are its parts;
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

Short-term borrowings are ``ShortTermBorrowings``, else ``CommercialPaper`` +
``OtherShortTermBorrowings`` when a total confirms the sum (see
``_resolve_short_term_parts``; unconfirmed non-zero parts withhold the total). A
noncurrent figure may be derived as (LTD including current - current LTD) when both are
tagged for the period and the noncurrent concept is not.

A short-term term not tagged for the period is accepted only with a basis note — and
never when it is KNOWN to be non-zero: a total without current maturities is withheld
when the filer's own maturity schedule shows principal due in the next twelve months.
When no long-term debt concept is tagged for the period there is no total either. A
total that exceeds the filer's own total-debt concept is withheld, unless that concept
is visibly long-term debt only.

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
#: Short-term borrowings (excluding current maturities of long-term debt), as one total.
SHORT_TERM_BORROWINGS_TOTAL: tuple[str, ...] = ("ShortTermBorrowings",)
#: The parts of short-term borrowings, added together when the total is not tagged.
SHORT_TERM_BORROWINGS_PARTS: tuple[str, ...] = ("CommercialPaper", "OtherShortTermBorrowings")
SHORT_TERM_BORROWINGS: tuple[str, ...] = SHORT_TERM_BORROWINGS_TOTAL + SHORT_TERM_BORROWINGS_PARTS
#: All current debt: short-term borrowings + current maturities of long-term debt.
DEBT_CURRENT: tuple[str, ...] = ("DebtCurrent",)
#: The filer's maturity schedule: long-term principal due within twelve months.
DUE_WITHIN_TWELVE_MONTHS: tuple[str, ...] = (
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextTwelveMonths",
    "LongTermDebtMaturitiesRepaymentsOfPrincipalInNextRollingTwelveMonths",
)
#: The filer's own total-debt concepts, used only as a cross-check — never as the total.
FILER_TOTALS: tuple[str, ...] = (
    "DebtAndCapitalLeaseObligations",
    "DebtLongtermAndShorttermCombinedAmount",
)

ALL_LONG_TERM: tuple[str, ...] = LTD_INCLUDING_CURRENT + LTD_NONCURRENT
ALL_CURRENT: tuple[str, ...] = DEBT_CURRENT + SHORT_TERM_BORROWINGS + LTD_CURRENT

#: Two figures closer than this (USD millions) are the same balance-sheet amount.
SAME_AMOUNT_TOLERANCE_M = 0.5

_MILLION = 1_000_000


def arithmetic_tolerance(amount: float) -> float:
    """Rounding allowance for a figure checked against a sum or difference of others.

    Filers tag each line rounded to the million; a sum of three rounded lines can sit
    a million or two away from a separately tagged total without anything being wrong.
    """
    return max(1.0, 0.001 * abs(amount))


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
    #: For a sum of parts (commercial paper + other short-term borrowings): the parts.
    parts: tuple[DebtComponent, ...] = ()


def _formula(components: list[DebtComponent]) -> str:
    """``Concept (value) + …``, a sum of parts written out part by part."""
    terms = [p for c in components for p in (c.parts or (c,))]
    return " + ".join(f"{p.concept} ({p.value:,.2f}m)" for p in terms)


@dataclass
class TotalDebt:
    """The derivation's result. ``total`` is ``None`` unless the basis is complete."""

    total: float | None = None
    current: float | None = None
    noncurrent: float | None = None
    combination: str | None = None
    formula: str | None = None
    components: list[DebtComponent] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    #: True when no long-term debt concept is tagged for the period: total is withheld.
    long_term_missing: bool = False
    #: Why a total that WAS computable is not published (known partial, or above the
    #: filer's own total). ``None`` when the total is published.
    withheld_reason: str | None = None

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
            "withheld_reason": self.withheld_reason,
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


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= arithmetic_tolerance(b)


#: Concepts that, when tagged, state the short-term borrowings line as one total. Used
#: only to CONFIRM that commercial paper and other short-term borrowings add up — never
#: as the value: ``NotesAndLoansPayable`` is not, by definition, short-term only.
SHORT_TERM_PARENTS: tuple[str, ...] = (
    "NotesAndLoansPayable",
    "ShortTermBankLoansAndNotesPayable",
)


def _resolve_short_term_parts(
    parts: list[DebtComponent],
    at: Any,
    debt_cur: DebtComponent | None,
    cur: DebtComponent | None,
) -> tuple[DebtComponent | None, str | None]:
    """Commercial paper and other short-term borrowings, both non-zero: sum, part, or ?

    The taxonomy defines them as separate parts, but filers do not always present them
    so. PR #307 review: one bank's balance sheet has ONE line, "Short-term borrowings
    48,088", tagged ``OtherShortTermBorrowings``; its 20,400 of commercial paper sits
    INSIDE that line, and adding the two overstated its debt by 20.4bn. Another filer's
    "Loans and notes payable" (1,551) really is 1,495 of commercial paper + 56 other.

    So the parts are added only when a total confirms the sum (a parent concept, or
    ``DebtCurrent`` minus current maturities). A total equal to the larger part says
    the smaller is inside it. With no total, which reading is right cannot be told from
    the facts, and the caller withholds total debt rather than guess either way.
    """
    first = parts[0]
    summed = DebtComponent(
        role="short_term_borrowings",
        concept=" + ".join(p.concept for p in parts),
        value=round(sum(p.value for p in parts), 2),
        end=first.end, accn=first.accn, form=first.form, fy=first.fy,
        fp=first.fp, filed=first.filed, parts=tuple(parts),
    )
    larger = max(parts, key=lambda p: p.value)
    totals: list[tuple[str, float]] = [
        (c, own.value) for c in SHORT_TERM_PARENTS
        if (own := at((c,), "short_term_parent")) is not None
    ]
    if debt_cur is not None and cur is not None:
        totals.append((f"{debt_cur.concept} - {cur.concept}", debt_cur.value - cur.value))
    for _label, total in totals:
        if abs(summed.value - total) <= arithmetic_tolerance(total):
            return summed, None
    for _label, total in totals:
        if abs(larger.value - total) <= arithmetic_tolerance(total):
            return larger, None
    return None, (
        f"{' and '.join(f'{p.concept} ({p.value:,.2f}m)' for p in parts)} are both tagged "
        "and no total confirms whether they are two lines or one line and its part; "
        "total debt withheld"
    )


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
    debt_cur = at(DEBT_CURRENT, "debt_current")
    stb = at(SHORT_TERM_BORROWINGS_TOTAL, "short_term_borrowings")
    #: Set when commercial paper and other short-term borrowings are both tagged and
    #: nothing says whether they are two lines or one line and its part.
    stb_unresolved: str | None = None
    if stb is None:
        parts = [p for c in SHORT_TERM_BORROWINGS_PARTS
                 if (p := at((c,), "short_term_borrowings")) is not None]
        nonzero = [p for p in parts if abs(p.value) > SAME_AMOUNT_TOLERANCE_M]
        if len(nonzero) <= 1:
            stb = nonzero[0] if nonzero else (parts[0] if parts else None)
        else:
            stb, stb_unresolved = _resolve_short_term_parts(nonzero, at, debt_cur, cur)
    due_12m = at(DUE_WITHIN_TWELVE_MONTHS, "maturity_schedule")
    notes: list[str] = []

    if incl is None and noncur is None:
        return TotalDebt(
            long_term_missing=True,
            notes=["no long-term debt concept tagged for the period; total debt withheld"],
        )

    def withheld(combination: str, reason: str, parts: list[DebtComponent],
                 noncurrent: float | None) -> TotalDebt:
        return TotalDebt(
            total=None,
            current=None,
            noncurrent=noncurrent,
            combination=combination,
            formula=_formula(parts) or None,
            components=parts,
            notes=notes + [reason],
            withheld_reason=reason,
        )

    # A filer that tags ONE balance-sheet line with both a short-term-borrowings concept
    # and a current-maturities concept has not reported two balances. Summing both
    # counts the line twice (seen live: the "short-term debt" line of two of 19 filers).
    stb_is_cur_line = False
    if stb is not None and cur is not None and _same(stb.value, cur.value):
        if incl is not None and noncur is not None and not _close(
            incl.value - noncur.value, cur.value
        ):
            # The equality is only evidence of one line when the long-term concepts
            # agree that the current maturities ARE that amount.
            return withheld(
                "ambiguous_current_debt",
                f"{stb.concept} equals {cur.concept} ({stb.value:,.2f}m), but "
                f"{incl.concept} - {noncur.concept} is "
                f"{incl.value - noncur.value:,.2f}m: whether one line is tagged twice "
                "cannot be told; total debt withheld",
                [noncur, cur, stb],
                noncur.value,
            )
        notes.append(
            f"{stb.concept} equals {cur.concept} ({stb.value:,.2f}m) — one balance-sheet "
            "line tagged with both concepts; counted once, as current maturities"
        )
        stb_is_cur_line = True
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
        result = TotalDebt(
            total=round(sum(p.value for p in parts), 2),
            current=None if current is None else round(current, 2),
            noncurrent=None if noncurrent is None else round(noncurrent, 2),
            combination=combination,
            formula=_formula(parts),
            components=parts,
            notes=notes + (extra_notes or []),
        )
        return _cross_check_filer_total(result, at, noncur, cur, incl, stb)

    def without_current_maturities(
        combination: str, parts: list[DebtComponent], current: float | None,
        long_term: DebtComponent, note: str,
    ) -> TotalDebt:
        """A basis with no current maturities: published only if none can be due."""
        if due_12m is not None and due_12m.value > SAME_AMOUNT_TOLERANCE_M:
            return withheld(
                combination,
                f"no current maturities of long-term debt tagged for the period, but the "
                f"filer's maturity schedule ({due_12m.concept}) shows "
                f"{due_12m.value:,.2f}m of principal due within twelve months; a total "
                "without it would be known to be low — total debt withheld",
                parts,
                long_term.value,
            )
        return done(combination, parts, current, long_term.value, [note])

    # ── Complete bases ───────────────────────────────────────────────────
    if noncur is not None and debt_cur is not None:
        inside = (cur.value if cur is not None else 0.0) + (stb.value if stb is not None else 0.0)
        if debt_cur.value + arithmetic_tolerance(inside) >= inside:
            return done("noncurrent+debt_current", [noncur, debt_cur],
                        debt_cur.value, noncur.value)
        notes.append(
            f"{debt_cur.concept} ({debt_cur.value:,.2f}m) is smaller than the current "
            f"maturities and short-term borrowings it contains ({inside:,.2f}m); not used"
        )
    if stb_unresolved is not None:
        # Every remaining basis needs the short-term borrowings figure, and it is not
        # known: neither the sum of the parts nor the larger part alone is a fact.
        known = [p for p in (noncur, cur) if p is not None] or [p for p in (incl,) if p]
        return withheld("short_term_parts_unresolved", stb_unresolved, known,
                        noncur.value if noncur is not None else None)
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
        return without_current_maturities(
            "noncurrent+short_term_borrowings", [noncur, stb], stb.value, noncur,
            _not_tagged_note(us_gaap, "current maturities of long-term debt",
                             LTD_CURRENT + DEBT_CURRENT, forms),
        )
    return without_current_maturities(
        "noncurrent", [noncur], None, noncur,
        _not_tagged_note(us_gaap, "current debt", ALL_CURRENT, forms),
    )


def _cross_check_filer_total(
    result: TotalDebt,
    at: Any,
    noncur: DebtComponent | None,
    cur: DebtComponent | None,
    incl: DebtComponent | None,
    stb: DebtComponent | None,
) -> TotalDebt:
    """Withhold a total that exceeds the filer's own total-debt concept.

    The filer's concept is a check, never the answer: filers use these concepts
    loosely. One of 19 tags long-term debt ONLY as ``DebtLongtermAndShorttermCombinedAmount``
    — so a total above it is kept when the concept equals the long-term debt including
    current maturities AND the excess is a distinct borrowings line. Distinct means
    larger than the current maturities: a borrowings figure no bigger than them could
    be (part of) the current-maturities line itself, and accepting any excess as
    borrowings would let exactly that double count through (review probe: noncurrent
    10,000 + current 1,000 + borrowings 400 against a filer total of 11,000). Any other
    excess means a term was counted twice, and the total is withheld.
    """
    if result.total is None:
        return result
    if noncur is not None and cur is not None:
        long_term_including_current: float | None = noncur.value + cur.value
    else:
        long_term_including_current = incl.value if incl is not None else None
    for concept in FILER_TOTALS:
        own = at((concept,), "filer_total")
        if own is None:
            continue
        if result.total <= own.value + arithmetic_tolerance(own.value):
            if result.total < own.value - arithmetic_tolerance(own.value):
                result.notes.append(
                    f"below the filer's own {concept} ({own.value:,.2f}m): that concept "
                    "includes lines outside the concepts read here (e.g. finance leases)"
                )
            continue
        borrowings_distinct = (
            stb is not None
            and cur is not None
            and stb.value > cur.value + arithmetic_tolerance(cur.value)
        )
        if (
            borrowings_distinct
            and long_term_including_current is not None
            and _close(long_term_including_current, own.value)
        ):
            result.notes.append(
                f"the filer's {concept} ({own.value:,.2f}m) equals its long-term debt "
                "including current maturities; short-term borrowings are outside it"
            )
            continue
        reason = (
            f"{result.total:,.2f}m exceeds the filer's own {concept} "
            f"({own.value:,.2f}m): a term is counted twice — total debt withheld"
        )
        return TotalDebt(
            total=None,
            current=None,
            noncurrent=result.noncurrent,
            combination=result.combination,
            formula=result.formula,
            components=result.components,
            notes=result.notes + [reason],
            withheld_reason=reason,
        )
    return result


__all__ = [
    "ALL_CURRENT",
    "ALL_LONG_TERM",
    "DEBT_CURRENT",
    "DUE_WITHIN_TWELVE_MONTHS",
    "FILER_TOTALS",
    "LTD_CURRENT",
    "LTD_INCLUDING_CURRENT",
    "LTD_NONCURRENT",
    "SHORT_TERM_BORROWINGS",
    "DebtComponent",
    "TotalDebt",
    "arithmetic_tolerance",
    "component_at_period",
    "derive_total_debt",
]
