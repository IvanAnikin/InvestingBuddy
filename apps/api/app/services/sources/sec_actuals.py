"""Quarterly earnings actuals from SEC XBRL — V3.20 slice 4.1 (spec §3.4).

Pure parsing of SEC ``companyconcept`` payloads (one per us-gaap concept) and of the
``submissions`` filing list into one row per fiscal quarter:

* **The filing's own quarter only.** A 10-Q also carries the prior year's comparative
  quarter and year-to-date figures. A fact is the filing's *current* quarter when its
  duration is a quarter (80–100 days) and it ends on the latest date that filing tags.
* **As first reported, and latest.** ``actual`` is the value in the period's own filing —
  what a consensus of the time was compared with. ``latest`` is the newest filing's
  value for the same dates; a share split or a restatement makes them differ
  (Powell's 2025 Q3 diluted EPS: 3.96 as filed, 1.32 after its 2026 split), and
  ``restated`` says so. Neither is ever silently replaced by the other.
* **Fiscal calendar (ADR-091).** The label is the filer's own fiscal year and quarter
  (``fy`` / ``fp`` of the filing), as ``2026-Q3`` — the ``ExtractedFact.period`` form.
* **Q4.** A 10-K rarely tags a three-month Q4. Q4 revenue is then derived as full year
  minus the Q3 10-Q's nine-month year-to-date of the same fiscal year, and labelled
  ``derived_fy_minus_9m``. Q4 EPS is NEVER derived (per-share figures do not add); it is
  stated only when the 10-K tags a three-month Q4 value.
* **Aliases.** Revenue is tagged under several concepts and a filer moves between them
  (the stale-alias trap: an old alias still holds old years). Per quarter, the concept
  whose current-quarter fact was filed most recently wins.
* **Report date.** The 8-K with Item 2.02 (results of operations) filed between the
  quarter end and the periodic report; else the periodic report's filing date, labelled.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

REVENUE_CONCEPTS: tuple[str, ...] = (
    "RevenueFromContractWithCustomerExcludingAssessedTax",
    "Revenues",
    "SalesRevenueNet",
    "RevenueFromContractWithCustomerIncludingAssessedTax",
)
EPS_CONCEPT = "EarningsPerShareDiluted"
CONCEPT_URL = "https://data.sec.gov/api/xbrl/companyconcept/CIK{cik}/us-gaap/{concept}.json"
FILING_INDEX_URL = "https://www.sec.gov/Archives/edgar/data/{cik}/{accession}-index.htm"

PERIODIC_FORMS = frozenset({"10-Q", "10-K", "10-Q/A", "10-K/A", "10-KT"})
QUARTER_DAYS = (80, 100)
NINE_MONTH_DAYS = (260, 285)
YEAR_DAYS = (350, 380)

REPORT_DATE_8K = "8-K item 2.02"
REPORT_DATE_PERIODIC = "periodic report filing date"
BASIS_REPORTED = "reported_quarter"
BASIS_DERIVED_Q4 = "derived_fy_minus_9m"
BASIS_REPORTED_YEAR = "reported_year"


def concept_url(cik: str, concept: str) -> str:
    return CONCEPT_URL.format(cik=str(int(cik)).zfill(10), concept=concept)


def filing_url(cik: str, accession: str) -> str:
    return FILING_INDEX_URL.format(cik=int(cik), accession=accession)


def _date(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _dec(value: Any) -> Decimal | None:
    try:
        return Decimal(str(value)) if value is not None else None
    except (InvalidOperation, ValueError):
        return None


@dataclass(frozen=True)
class Fact:
    concept: str
    unit: str
    start: date | None
    end: date
    value: Decimal
    accession: str
    form: str
    filed: date
    fy: int | None
    fp: str | None

    @property
    def days(self) -> int:
        return (self.end - self.start).days if self.start else 0

    def within(self, bounds: tuple[int, int]) -> bool:
        return bounds[0] <= self.days <= bounds[1]


def facts_of(payload: dict[str, Any], *, concept: str) -> list[Fact]:
    """Every dated fact of one ``companyconcept`` payload, any unit."""
    out: list[Fact] = []
    for unit, rows in ((payload or {}).get("units") or {}).items():
        for r in rows or []:
            end, filed, value = _date(r.get("end")), _date(r.get("filed")), _dec(r.get("val"))
            if end is None or filed is None or value is None or not r.get("accn"):
                continue
            fy = r.get("fy")
            out.append(Fact(concept=concept, unit=str(unit), start=_date(r.get("start")),
                            end=end, value=value, accession=str(r["accn"]),
                            form=str(r.get("form") or "").upper(), filed=filed,
                            fy=int(fy) if isinstance(fy, int) else None,
                            fp=str(r.get("fp") or "").upper() or None))
    return out


def _current_end(facts: list[Fact]) -> dict[str, date]:
    """Per accession: the latest date it tags — the filing's own period end."""
    ends: dict[str, date] = {}
    for f in facts:
        if f.form in PERIODIC_FORMS:
            ends[f.accession] = max(ends.get(f.accession, f.end), f.end)
    return ends


@dataclass
class Figure:
    """One quarter's value of one metric, with how it was obtained."""

    value: Decimal
    unit: str
    concept: str
    accession: str
    form: str
    filed: date
    basis: str
    latest: Decimal | None = None
    latest_accession: str | None = None

    @property
    def restated(self) -> bool:
        return self.latest is not None and self.latest != self.value


@dataclass
class QuarterActual:
    fiscal_year: int
    fiscal_quarter: int
    period_start: date | None
    period_end: date
    revenue: Figure | None = None
    eps: Figure | None = None
    periodic_accession: str | None = None
    periodic_form: str | None = None
    periodic_filed: date | None = None
    report_date: date | None = None
    report_date_source: str | None = None
    report_date_accession: str | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def is_annual(self) -> bool:
        return self.fiscal_quarter == 0

    @property
    def label(self) -> str:
        """``2026-Q3`` for a quarter, ``2026`` for a fiscal year (``fiscal_quarter`` 0)."""
        if self.is_annual:
            return str(self.fiscal_year)
        return f"{self.fiscal_year}-Q{self.fiscal_quarter}"

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["label"] = self.label
        return out


def _quarter_of(fact: Fact) -> int | None:
    if fact.fp in ("Q1", "Q2", "Q3"):
        return int(fact.fp[1])
    if fact.fp == "FY":
        return 4
    return None


def _latest_for(facts: list[Fact], start: date | None, end: date) -> Fact | None:
    same = [f for f in facts if f.end == end and f.start == start]
    return max(same, key=lambda f: (f.filed, f.accession)) if same else None


def quarterly_figures(facts: list[Fact], *, derive_q4: bool
                      ) -> dict[tuple[int, int], tuple[Figure, date | None, date]]:
    """``{(fiscal_year, quarter): (figure, start, end)}`` from ONE concept's facts."""
    ends = _current_end(facts)
    first: dict[tuple[int, int], tuple[Fact, str]] = {}
    for f in sorted(facts, key=lambda f: (f.filed, f.accession)):
        if f.form not in PERIODIC_FORMS or ends.get(f.accession) != f.end or f.fy is None:
            continue
        quarter = _quarter_of(f)
        if quarter is None or not f.within(QUARTER_DAYS):
            continue
        first.setdefault((f.fy, quarter), (f, BASIS_REPORTED))
    out: dict[tuple[int, int], tuple[Figure, date | None, date]] = {}
    for key, (f, basis) in first.items():
        latest = _latest_for(facts, f.start, f.end)
        out[key] = (Figure(value=f.value, unit=f.unit, concept=f.concept,
                           accession=f.accession, form=f.form, filed=f.filed, basis=basis,
                           latest=latest.value if latest else None,
                           latest_accession=latest.accession if latest else None),
                    f.start, f.end)
    if derive_q4:
        for key, value in _derived_q4(facts, ends).items():
            out.setdefault(key, value)
    return out


def _derived_q4(facts: list[Fact], ends: dict[str, date]
                ) -> dict[tuple[int, int], tuple[Figure, date | None, date]]:
    """Full year (10-K, as first reported) minus the same year's nine-month YTD."""
    out: dict[tuple[int, int], tuple[Figure, date | None, date]] = {}
    years = [f for f in sorted(facts, key=lambda f: (f.filed, f.accession))
             if f.fp == "FY" and f.fy is not None and ends.get(f.accession) == f.end
             and f.within(YEAR_DAYS) and f.form.startswith("10-K")]
    seen: set[int] = set()
    for year in years:
        if year.fy in seen or year.fy is None:
            continue
        seen.add(year.fy)
        nine = [f for f in sorted(facts, key=lambda f: (f.filed, f.accession))
                if f.fp == "Q3" and f.fy == year.fy and ends.get(f.accession) == f.end
                and f.start == year.start and f.within(NINE_MONTH_DAYS) and f.unit == year.unit]
        if not nine:
            continue
        ytd = nine[0]
        out[(year.fy, 4)] = (
            Figure(value=year.value - ytd.value, unit=year.unit, concept=year.concept,
                   accession=year.accession, form=year.form, filed=year.filed,
                   basis=BASIS_DERIVED_Q4),
            ytd.end, year.end)
    return out


def _start_after(end: date) -> date:
    return date.fromordinal(end.toordinal() + 1)


def filings_with_items(submissions: dict[str, Any]) -> list[dict[str, Any]]:
    recent = (((submissions or {}).get("filings") or {}).get("recent") or {})
    forms = recent.get("form") or []
    rows = []
    for i, form in enumerate(forms):
        def col(name: str, i: int = i) -> Any:
            values = recent.get(name) or []
            return values[i] if i < len(values) else None

        rows.append({"form": str(form or "").upper(), "accession": col("accessionNumber"),
                     "filing_date": _date(col("filingDate")),
                     "items": str(col("items") or "")})
    return rows


def _report_date(quarter: QuarterActual, filings: list[dict[str, Any]]) -> None:
    upper = quarter.periodic_filed
    releases = sorted(
        (f for f in filings
         if f["form"] in ("8-K", "8-K/A") and "2.02" in f["items"].split(",")
         and f["filing_date"] is not None and f["filing_date"] > quarter.period_end
         and (upper is None or f["filing_date"] <= upper)),
        key=lambda f: f["filing_date"])
    if releases:
        quarter.report_date = releases[0]["filing_date"]
        quarter.report_date_source = REPORT_DATE_8K
        quarter.report_date_accession = releases[0]["accession"]
    elif upper is not None:
        quarter.report_date = upper
        quarter.report_date_source = REPORT_DATE_PERIODIC
        quarter.report_date_accession = quarter.periodic_accession


def annual_figures(facts: list[Fact]) -> dict[int, tuple[Figure, date | None, date]]:
    """``{fiscal_year: (figure, start, end)}``: the 10-K's own full year, as first
    reported, with the latest filing's value for the same dates."""
    ends = _current_end(facts)
    out: dict[int, tuple[Figure, date | None, date]] = {}
    for f in sorted(facts, key=lambda f: (f.filed, f.accession)):
        if (f.fp != "FY" or f.fy is None or not f.form.startswith("10-K")
                or ends.get(f.accession) != f.end or not f.within(YEAR_DAYS)
                or f.fy in out):
            continue
        latest = _latest_for(facts, f.start, f.end)
        out[f.fy] = (Figure(value=f.value, unit=f.unit, concept=f.concept,
                            accession=f.accession, form=f.form, filed=f.filed,
                            basis=BASIS_REPORTED_YEAR,
                            latest=latest.value if latest else None,
                            latest_accession=latest.accession if latest else None),
                     f.start, f.end)
    return out


def annual_actuals(
    revenue_payloads: dict[str, dict[str, Any]],
    eps_payload: dict[str, Any] | None,
    submissions: dict[str, Any] | None = None,
    *,
    limit: int = 3,
) -> list[QuarterActual]:
    """Newest ``limit`` fiscal years (``fiscal_quarter`` 0), newest first — what annual
    guidance is compared with (slice 4.3)."""
    revenue: dict[int, tuple[Figure, date | None, date]] = {}
    for concept in REVENUE_CONCEPTS:
        payload = revenue_payloads.get(concept)
        if not payload:
            continue
        for year, value in annual_figures(facts_of(payload, concept=concept)).items():
            held = revenue.get(year)
            if held is None or value[0].filed > held[0].filed:
                revenue[year] = value
    eps = annual_figures(facts_of(eps_payload, concept=EPS_CONCEPT)) if eps_payload else {}
    rows: list[QuarterActual] = []
    for year in sorted(set(revenue) | set(eps), reverse=True)[:limit]:
        _figure, start, end = revenue.get(year) or eps[year]
        row = QuarterActual(fiscal_year=year, fiscal_quarter=0, period_start=start,
                            period_end=end)
        row.revenue = revenue[year][0] if year in revenue else None
        row.eps = eps[year][0] if year in eps else None
        anchor = row.revenue or row.eps
        assert anchor is not None
        row.periodic_accession, row.periodic_form, row.periodic_filed = (
            anchor.accession, anchor.form, anchor.filed)
        rows.append(row)
    filings = filings_with_items(submissions or {})
    for row in rows:
        _report_date(row, filings)
    return rows


def quarterly_actuals(
    revenue_payloads: dict[str, dict[str, Any]],
    eps_payload: dict[str, Any] | None,
    submissions: dict[str, Any] | None = None,
    *,
    limit: int = 12,
) -> list[QuarterActual]:
    """Newest ``limit`` fiscal quarters, newest first."""
    revenue: dict[tuple[int, int], tuple[Figure, date | None, date]] = {}
    for concept in REVENUE_CONCEPTS:
        payload = revenue_payloads.get(concept)
        if not payload:
            continue
        for key, value in quarterly_figures(facts_of(payload, concept=concept),
                                            derive_q4=True).items():
            held = revenue.get(key)
            # The concept filed most recently for this quarter wins (stale-alias trap);
            # a reported quarter always beats a derived one.
            rank = (value[0].basis == BASIS_REPORTED, value[0].filed)
            if held is None or rank > (held[0].basis == BASIS_REPORTED, held[0].filed):
                revenue[key] = value
    eps = (quarterly_figures(facts_of(eps_payload, concept=EPS_CONCEPT), derive_q4=False)
           if eps_payload else {})
    quarters: dict[tuple[int, int], QuarterActual] = {}
    for key in set(revenue) | set(eps):
        source = revenue.get(key) or eps.get(key)
        assert source is not None
        figure, start, end = source
        q = QuarterActual(fiscal_year=key[0], fiscal_quarter=key[1], period_start=start,
                          period_end=end)
        if key in revenue:
            q.revenue = revenue[key][0]
            ytd_end = revenue[key][1]
            if q.revenue.basis == BASIS_DERIVED_Q4:
                q.period_start = _start_after(ytd_end) if ytd_end else None
        if key in eps:
            q.eps = eps[key][0]
            q.period_start, q.period_end = eps[key][1], eps[key][2]
        elif key[1] == 4:
            q.notes.append("q4_eps_not_tagged_as_a_quarter")
        anchor = q.eps or q.revenue
        assert anchor is not None
        q.periodic_accession, q.periodic_form, q.periodic_filed = (
            anchor.accession, anchor.form, anchor.filed)
        quarters[key] = q
    rows = sorted(quarters.values(), key=lambda q: (q.fiscal_year, q.fiscal_quarter),
                  reverse=True)[:limit]
    filings = filings_with_items(submissions or {})
    for q in rows:
        _report_date(q, filings)
    return rows


__all__ = [
    "BASIS_DERIVED_Q4",
    "BASIS_REPORTED_YEAR",
    "BASIS_REPORTED",
    "EPS_CONCEPT",
    "Fact",
    "Figure",
    "QuarterActual",
    "annual_actuals",
    "annual_figures",
    "REPORT_DATE_8K",
    "REPORT_DATE_PERIODIC",
    "REVENUE_CONCEPTS",
    "concept_url",
    "facts_of",
    "filing_url",
    "filings_with_items",
    "quarterly_actuals",
    "quarterly_figures",
]
