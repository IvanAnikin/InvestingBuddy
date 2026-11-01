"""A reported actual cannot exist for a period that has not ended (ADR-072).

Cross-sector QA, 2026-11-01 (defect ADBE-3): Adobe's deep report carried F14, "Trefis
reports Adobe FY2026 total revenue 26.58 Bil and ending ARR growth 10.2%, per its Q4 2026
summary", as a plain fact. Adobe's last 10-K period ended 2025-11-28, so FY2026 ends about
2026-11-28, and the report ran on 2026-11-01. The figure was an estimate shown as a
reported result, and no layer asked whether the stated period had closed.

The rule: a web statement that names the subject and gives a figure as an ACTUAL for a
period that had not ended on the run date is dropped, with one ``conflicting_sources`` gap.
A calendar or dated period has a known end; a fiscal one is projected from the issuer's
own last annual period end (±7 days for 52/53-week years) and is never judged without it.
The issuer below is synthetic.
"""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any

import pytest

from app.services import period_closure as pc
from app.services.agents import investigator as inv_mod
from app.services.agents.investigator import (
    InvestigatorDiagnostics,
    LLMInvestigator,
    _Evidence,
    names_the_subject,
)
from app.services.director.planner import PlannedQuestion

#: Synthetic issuer with Adobe's calendar: the last 10-K period ended 2025-11-28.
NOV_FYE = pc.FiscalYearAnchor(2025, date(2025, 11, 28))
DEC_FYE = pc.FiscalYearAnchor(2025, date(2025, 12, 31))
#: Read from the two issuers' own companyfacts (review of PR #309).
TGT_FYE = pc.FiscalYearAnchor(2025, date(2026, 1, 31))
WMT_FYE = pc.FiscalYearAnchor(2026, date(2026, 1, 31))
RUN_DATE = date(2026, 11, 1)

#: The live F14 shape, with the synthetic issuer's name.
F14 = (
    "Trefis reports Vexmoor FY2026 total revenue 26.58 Bil and ending ARR growth 10.2%, "
    "per its Q4 2026 summary."
)


# ---------------------------------------------------------------------------
# The three review batteries of PR #309, verbatim (round 1: 38, round 2: 48, round 3: 54)
# ---------------------------------------------------------------------------

MSFT_FYE = pc.FiscalYearAnchor(2026, date(2026, 6, 30))
AAPL_FYE = pc.FiscalYearAnchor(2026, date(2026, 9, 26))
BATTERY_ANCHORS = {
    "ADBE": NOV_FYE, "TGT": TGT_FYE, "WMT": WMT_FYE, "HON": DEC_FYE, "MSFT": MSFT_FYE,
    "AAPL": AAPL_FYE,
}
BATTERY_NAMES = {
    "ADBE": "Adobe Inc.", "TGT": "Target Corporation", "WMT": "Walmart Inc.",
    "HON": "Honeywell International Inc.", "MSFT": "Microsoft Corporation", "AAPL": "Apple Inc.",
}
#: The only statements any battery expects dropped (round 3 marks them as controls).
BATTERY_DROPS = frozenset(
    {
        "Trefis reports Adobe FY2026 total revenue 26.58 Bil and ending ARR growth 10.2%, "
        "per its Q4 2026 summary.",
        "Honeywell's full year 2026 sales were $40 billion.",
    }
)

ROUND_1: list[tuple[str, str, str]] = [
    ('ADBE', '2026-11-01', "Adobe's FY2026 Q3 revenue was $6.19 billion."),
    ('ADBE', '2026-11-01', "Adobe's fiscal 2026 third quarter revenue was $6.19 billion."),
    ('ADBE', '2026-11-01', "Adobe's fiscal 2026 Q3 revenue was $6.19 billion, up 10%."),
    ('ADBE', '2026-11-01', "Adobe's fiscal-year 2026 third-quarter revenue was $6.19 billion."),
    ('ADBE', '2026-11-01', "Adobe's third quarter of fiscal 2026 revenue was $6.19 billion."),
    ('ADBE', '2026-11-01', "Adobe's revenue in the nine months of FY2026 was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's 9M FY2026 revenue was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's H1 FY2026 revenue was $11.4 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue through the third quarter was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's six-month FY2026 revenue was $11.4 billion."),
    ('ADBE', '2026-11-01', 'In fiscal 2026, Adobe repurchased $8.0 billion of its shares.'),
    ('ADBE', '2026-11-01', 'During FY2026 Adobe completed its $1.9 billion acquisition of Semrush.'),
    ('ADBE', '2026-11-01', "Adobe's FY2025 revenue was $23.77 billion; its Q4 FY2026 results are due December 11, 2026."),
    ('ADBE', '2026-11-01', "Adobe's FY2025 revenue was $23.77 billion, and its shares fell 20% in calendar 2026."),
    ('ADBE', '2026-11-01', 'For fiscal 2026, the company expects revenue of $25.9 billion.'),
    ('ADBE', '2026-11-01', 'For fiscal 2026, Adobe targets revenue of $25.9 billion.'),
    ('ADBE', '2026-11-01', "Adobe's FY2026 Q3 revenue rose 10% to $6.19B from $5.99B in Q3 FY2025."),
    ('ADBE', '2026-11-01', 'Trefis reports Adobe FY2026 total revenue 26.58 Bil and ending ARR growth 10.2%, per its Q4 2026 summary.'),
    ('ADBE', '2026-11-01', 'Adobe FY2026 revenue was $26.58 billion (Trefis estimate).'),
    ('ADBE', '2026-11-01', 'Adobe FY2026 revenue was $26.58 billion according to Visible Alpha consensus.'),
    ('ADBE', '2026-11-01', 'Adobe FY2026 revenue was $26.58 billion per Wall Street.'),
    ('ADBE', '2026-11-01', 'Adobe FY2026 revenue was $26.58 billion per FactSet mean.'),
    ('ADBE', '2026-11-25', "Adobe's fiscal 2026 revenue was $26.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's fiscal 2025 fourth quarter revenue was $6.19 billion."),
    ('TGT', '2026-03-15', "Target's fiscal 2025 revenue was $104.8 billion."),
    ('TGT', '2026-03-15', "Target's FY2026 revenue was $104.8 billion (fiscal year ended January 31, 2026)."),
    ('TGT', '2026-03-15', "Target's FY2026 revenue was $104.8 billion."),
    ('TGT', '2026-11-01', "Target's Q2 2026 revenue was $25.2 billion."),
    ('TGT', '2026-11-01', "Target's fiscal 2026 second quarter revenue was $25.2 billion."),
    ('WMT', '2026-11-01', "Walmart's Q2 FY2027 revenue was $177 billion."),
    ('WMT', '2026-11-01', "Walmart's fiscal 2027 second-quarter revenue was $177 billion."),
    ('HON', '2026-11-01', "Honeywell's Q3 2026 sales were $10.4 billion."),
    ('HON', '2026-11-01', "Honeywell's 2026 third-quarter sales were $10.4 billion."),
    ('HON', '2026-11-01', "Honeywell's third-quarter 2026 sales were $10.4 billion."),
    ('HON', '2026-11-01', "Honeywell's 3Q26 sales were $10.4 billion, and FY2026 sales are $40B."),
    ('HON', '2026-11-01', "Honeywell's full year 2026 sales were $40 billion."),
    ('HON', '2026-11-01', 'Honeywell reported sales of $10.4 billion for the quarter ended September 30, 2026.'),
    ('HON', '2026-11-01', "Honeywell's Q3 FY2026 sales were $10.4 billion, and Q4 2026 has begun."),
]

ROUND_2: list[tuple[str, str, str]] = [
    ('ADBE', '2026-11-01', 'Adobe posted FY2026 revenue guidance of $25.9 billion.'),
    ('ADBE', '2026-11-01', "Adobe's revenue for FY2026 is tracking at $26.5 billion."),
    ('ADBE', '2026-11-01', "Adobe's fiscal 2026 to date revenue was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's revenue for the twelve months to August 2026 was $25.2 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 Q3 revenue was $6.19B and its FY2025 revenue was $23.77B."),
    ('ADBE', '2026-11-01', "Adobe's Q4 FY2026 begins on August 29, 2026 and revenue was $6.19B in Q3."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 Q3 revenue was $6.19 billion versus $5.99 billion a year earlier."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $17.6 billion as of August 29, 2026."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 cumulative revenue was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's 9-month FY2026 revenue was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's 6-month FY2026 revenue was $11.4 billion."),
    ('ADBE', '2026-11-01', "Adobe's 1H FY2026 revenue was $11.4 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue for the 39 weeks ended August 29, 2026 was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 ending ARR was $18.59 billion as of August 29, 2026."),
    ('ADBE', '2026-11-01', "Adobe's Digital Media ARR stood at $18.59 billion in FY2026."),
    ('ADBE', '2026-11-01', "Adobe's RPO stood at $20.4 billion in FY2026, up 13%."),
    ('ADBE', '2026-11-01', "Adobe's revenue for the period ended 29 août 2026 (FY2026) was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue for the period ended 28 agosto 2026 was $17.6 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $17.6 billion until the end of August."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 semi-annual revenue was $11.4 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was hit by a $150 million currency headwind."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $17.6 billion after nine months."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $6.19 billion in its latest quarter."),
    ('ADBE', '2026-11-01', "The company's FY2026 operating margin was 46% at the half."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 Q3 and FY2025 revenue were $6.19B and $23.77B."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 year-on-year revenue growth was 10% for the first 3 fiscal quarters."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $17.6 billion, per its 10-Q for the first three quarters."),
    ('ADBE', '2026-11-01', "Adobe's fiscal 2026 revenue was $17.6 billion over 39 weeks."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $17.6 billion before Q4."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 EPS was $15.0 on a non-GAAP basis in its 10-Q."),
    ('ADBE', '2026-11-01', 'Adobe reported FY2026 revenue of $17.6 billion in its Sept. 22 10-Q.'),
    ('ADBE', '2026-11-01', "Adobe's FY2025 revenue was $23.77 billion, compared with FY2026 Q3 revenue of $6.19 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $17.6bn in the first nine-month period."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 YoY revenue growth was 10.7%."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 revenue was $17.6 billion by August."),
    ('MSFT', '2026-11-01', "Microsoft's fiscal 2027 first-quarter revenue was $77.7 billion."),
    ('MSFT', '2026-11-01', "Microsoft's FY27 Q1 revenue was $77.7 billion."),
    ('MSFT', '2026-11-01', "Microsoft's FY2027 revenue was $77.7 billion in the September quarter."),
    ('MSFT', '2026-11-01', "Microsoft's FY2027 revenue was $77.7 billion for the three months ended September 30, 2026."),
    ('MSFT', '2026-11-01', "Microsoft's FY2027 revenue was $77.7 billion for the 3-month period."),
    ('AAPL', '2026-11-01', "Apple's fiscal 2026 revenue was $416 billion."),
    ('AAPL', '2026-11-01', "Apple's Q4 FY2026 revenue was $102 billion."),
    ('HON', '2026-11-01', "Honeywell's 2026 sales were $30.5 billion for the year ending September."),
    ('HON', '2026-11-01', "Honeywell's FY2026 sales were $30.5 billion as of September 30, 2026."),
    ('HON', '2026-11-01', "Honeywell's CY2026 free cash flow was $3.1 billion as of September."),
    ('WMT', '2026-11-01', "Walmart's fiscal 2027 revenue was $530 billion as of the October quarter-end."),
    ('WMT', '2026-11-01', "Walmart's FY2027 revenue was $350 billion as of July 31, 2026."),
    ('TGT', '2026-11-01', "Target Corp's FY2026 revenue was $50 billion as of August 2, 2026."),
]

ROUND_3: list[tuple[str, str, str]] = [
    ('ADBE', '2026-01-15', "Adobe's full-year 2025 revenue was $23.77 billion."),
    ('ADBE', '2026-11-01', "Adobe's full-year 2025 revenue was $23.77 billion."),
    ('HON', '2026-02-10', "Honeywell's full year 2025 sales were $37.44 billion."),
    ('HON', '2025-12-28', "Honeywell's full year 2025 sales were $37.44 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 annual recurring revenue was $25.2 billion."),
    ('ADBE', '2026-11-01', "Adobe's FY2026 annual-recurring revenue was $25.2 billion."),
    ('HON', '2026-11-01', "Honeywell's annual dividend of $4.76 in FY2026 was paid on revenue of $37.44 billion."),
    ('HON', '2026-11-01', "Honeywell's FY2026 annual dividend is $4.76 per share and its sales were $37.44 billion last year."),
    ('ADBE', '2026-11-01', "At Adobe's FY2026 annual general meeting, shareholders heard revenue was $23.77 billion last year."),
    ('HON', '2026-11-01', "Honeywell's fourth-quarter 2025 sales were $9.8 billion."),
    ('HON', '2026-11-01', "Honeywell's Q4 FY2026 guidance calls for sales of $10.5 billion."),
    ('HON', '2026-11-01', "Honeywell's full-year 2026 outlook was raised to sales of $40.5 billion."),
    ('ADBE', '2026-11-01', 'Adobe expects full-year revenue of $26.0 billion in FY2026.'),
    ('MSFT', '2026-03-01', "Microsoft's total revenue for fiscal 2026 is projected at $325 billion."),
    ('ADBE', '2026-11-01', "Adobe's annualised run-rate revenue was $26 billion in FY2026."),
    ('HON', '2026-11-01', "Honeywell's full year 2026 consensus sales figure was $40.4 billion."),
    ('MSFT', '2026-08-01', "Microsoft's fiscal year 2026 annual revenue was $331.84 billion."),
    ('MSFT', '2026-11-01', "Microsoft's full-year FY2026 revenue was $331.84 billion."),
    ('MSFT', '2026-07-25', "Microsoft's FY2026 full year revenue was $331.84 billion."),
    ('MSFT', '2026-08-01', "Microsoft's Q4 FY2026 revenue was $76.4 billion."),
    ('WMT', '2026-11-01', "Walmart's fiscal 2026 full-year revenue was $713.16 billion."),
    ('WMT', '2026-03-01', "Walmart's full-year fiscal 2026 revenue was $713.16 billion."),
    ('WMT', '2026-03-01', "Walmart's Q4 FY2026 revenue was $190 billion."),
    ('HON', '2026-11-01', "Honeywell's Q4 2025 sales were $9.8 billion, the last quarter of full year 2025."),
    ('HON', '2026-11-01', "Honeywell's 2025 sales were $37.44 billion; full-year results will be reported on Feb 3."),
    ('HON', '2026-11-01', "Honeywell's 2025 sales were $37.44 billion; its full year 2026 results are due early next year."),
    ('HON', '2026-11-01', "Honeywell's 2025 sales were $37.44 billion, and fourth-quarter 2026 results are due soon."),
    ('HON', '2026-11-01', "Honeywell's 2025 full-year sales were $37.44 billion, and its Q4 2026 dividend is $1.19 per share."),
    ('HON', '2026-11-01', "Honeywell's full-year sales were $37.44 billion for 2025, and Q4 2026 is under way."),
    ('HON', '2026-11-01', "Honeywell's annual sales were $37.44 billion last year, a base for full year 2026."),
    ('ADBE', '2026-11-01', "Adobe's annual revenue was $23.77 billion entering fiscal 2026."),
    ('ADBE', '2026-11-01', "Adobe's annual revenue was $23.77 billion at the start of fiscal 2026."),
    ('ADBE', '2026-11-01', "Adobe's prior full-year revenue was $23.77 billion, the base for FY2026."),
    ('ADBE', '2026-11-01', "Adobe's Q4 FY2026 is now under way; its annual revenue was $23.77 billion in 2025."),
    ('ADBE', '2026-11-01', "Adobe's annual revenue was $23.77 billion when fiscal 2026 began."),
    ('MSFT', '2025-11-01', "Microsoft's annual revenue was $281.72 billion, and fiscal year 2026 began this summer."),
    ('MSFT', '2026-11-01', "Microsoft's annual revenue was $331.84 billion, and its fiscal year 2027 is under way."),
    ('MSFT', '2026-11-01', "Microsoft's last full-year revenue was $331.84 billion; Q4 FY2027 is far off."),
    ('WMT', '2026-11-01', "Walmart's annual revenue was $713.16 billion, and fiscal 2027 is its 64th year."),
    ('WMT', '2026-11-01', "Walmart's full-year revenue was $713.16 billion, the largest of any retailer, ahead of full year 2027."),
    ('HON', '2026-11-01', "Honeywell's annual sales were $37.44 billion; Q4 2026 marks its final quarter as a conglomerate."),
    ('HON', '2026-11-15', "Honeywell's annual sales were $37.44 billion, larger than Emerson's full-year 2026 sales of $18.0 billion."),
    ('HON', '2026-11-01', "Honeywell posted annual sales of $37.44 billion in its last reported fiscal year, ahead of FY2026's close."),
    ('ADBE', '2026-11-01', "Adobe's total revenue for the year was $23.77 billion, it said as FY2026 opened."),
    ('ADBE', '2026-12-20', "Adobe's full-year fiscal 2026 revenue was $26.4 billion."),
    ('ADBE', '2026-11-25', "Adobe's fourth-quarter fiscal 2026 revenue was $6.6 billion."),
    ('HON', '2027-01-30', "Honeywell's fourth-quarter 2026 sales were $10.1 billion."),
    ('HON', '2026-11-01', 'Honeywell reported annual sales of $37.44 billion; FY26 Q4 earnings come next.'),
    ('HON', '2026-11-01', "Honeywell's calendar Q3 2026 sales were $10.4 billion."),
    ('MSFT', '2026-11-01', "Microsoft's FY26 full-year revenue was $331.84 billion."),
    ('MSFT', '2026-11-01', "Microsoft's full-year revenue was $331.84 billion in FY 26."),
    ('ADBE', '2026-11-01', "Adobe's FY2025/26 annual revenue was $23.77 billion."),
    ('HON', '2026-11-01', "Honeywell's full year 2026 sales were $40 billion."),
    ('ADBE', '2026-11-01', 'Trefis reports Adobe FY2026 total revenue 26.58 Bil and ending ARR growth 10.2%, per its Q4 2026 summary.'),
]


# ---------------------------------------------------------------------------
# The pure rule
# ---------------------------------------------------------------------------


class TestStatedPeriods:
    @pytest.mark.parametrize(
        "text,kind,year,ordinal",
        [
            ("FY2026 revenue", pc.KIND_FISCAL_YEAR, 2026, None),
            ("FY26 revenue", pc.KIND_FISCAL_YEAR, 2026, None),
            ("fiscal 2026 revenue", pc.KIND_FISCAL_YEAR, 2026, None),
            ("Q4 FY2026 revenue", pc.KIND_FISCAL_QUARTER, 2026, 4),
            ("fourth quarter of fiscal 2026", pc.KIND_FISCAL_QUARTER, 2026, 4),
            ("FY26 Q4 revenue", pc.KIND_FISCAL_QUARTER, 2026, 4),
            # The platform's quarter key is fiscal-keyed (ADR-065), not calendar.
            ("2026-Q4 revenue", pc.KIND_FISCAL_QUARTER, 2026, 4),
            ("Q4 2026 revenue", pc.KIND_FISCAL_QUARTER, 2026, 4),
            ("CY2026 revenue", pc.KIND_CALENDAR_YEAR, 2026, None),
            ("calendar 2026 revenue", pc.KIND_CALENDAR_YEAR, 2026, None),
            ("calendar Q4 2026 revenue", pc.KIND_CALENDAR_QUARTER, 2026, 4),
            ("Q4 CY2026 revenue", pc.KIND_CALENDAR_QUARTER, 2026, 4),
            # Review of PR #309: the year BEFORE the quarter is still the quarter.
            ("fiscal 2026 third quarter revenue", pc.KIND_FISCAL_QUARTER, 2026, 3),
            ("fiscal 2026 Q3 revenue", pc.KIND_FISCAL_QUARTER, 2026, 3),
            ("fiscal-year 2026 third-quarter revenue", pc.KIND_FISCAL_QUARTER, 2026, 3),
            ("fiscal 2027 second-quarter revenue", pc.KIND_FISCAL_QUARTER, 2027, 2),
            ("2026 third-quarter sales", pc.KIND_FISCAL_QUARTER, 2026, 3),
            ("3Q26 sales", pc.KIND_FISCAL_QUARTER, 2026, 3),
        ],
    )
    def test_each_form_is_read(self, text: str, kind: str, year: int, ordinal: int | None) -> None:
        periods = pc.stated_periods(text)
        assert [(p.kind, p.year, p.ordinal) for p in periods] == [(kind, year, ordinal)]

    def test_a_quarter_of_a_fiscal_year_is_not_also_the_year(self) -> None:
        assert [p.kind for p in pc.stated_periods("Q3 FY2026 revenue")] == [
            pc.KIND_FISCAL_QUARTER
        ]

    def test_a_printed_end_date_is_read(self) -> None:
        (period,) = pc.stated_periods("for the quarter ending November 27, 2026")
        assert period.kind == pc.KIND_DATED and period.end == date(2026, 11, 27)

    def test_a_bare_year_is_not_a_period(self) -> None:
        assert pc.stated_periods("In 2026 revenue was $7bn") == []


class TestFiscalAnchor:
    def test_the_next_fiscal_year_ends_a_year_after_the_last(self) -> None:
        assert NOV_FYE.year_end(2026) == date(2026, 11, 28)
        assert NOV_FYE.quarter_end(2026, 3) == date(2026, 8, 28)

    def test_an_anchor_needs_a_year_and_a_date(self) -> None:
        assert pc.anchor_from_normalised(2025, "2025-11-28") == NOV_FYE
        assert pc.anchor_from_normalised(None, "2025-11-28") is None
        assert pc.anchor_from_normalised(2025, None) is None
        assert pc.anchor_from_normalised(2025, "2019-11-28") is None

    def test_a_january_year_end_is_judged_on_the_earlier_reading_and_no_quarter(
        self,
    ) -> None:
        # Target names the year ended 2026-01-31 "fiscal 2025"; Walmart names the same
        # year "fiscal 2026". A writer may use either, so FY2026 is judged as ending
        # 2026-01-31 for both, and no quarter of either is judged.
        assert TGT_FYE.ends_early_in_the_year and WMT_FYE.ends_early_in_the_year
        assert TGT_FYE.year_end(2026) == date(2027, 1, 31)  # its own label
        assert TGT_FYE.judged_year_end(2026) == date(2026, 1, 31)
        assert WMT_FYE.judged_year_end(2027) == date(2027, 1, 31)
        assert TGT_FYE.quarter_end(2026, 2) is None
        # A 52/53-week year ending on 2 January is a December year-end.
        assert not pc.FiscalYearAnchor(2025, date(2026, 1, 2)).ends_early_in_the_year


class TestUnendedActual:
    def test_the_live_f14_shape_on_a_november_year_end_is_caught(self) -> None:
        found = pc.unended_actual(F14, as_of=RUN_DATE, anchor=NOV_FYE)
        assert found is not None
        assert found.period.text == "FY2026" and found.ends == date(2026, 11, 28)

    def test_the_same_statement_after_the_year_end_is_kept(self) -> None:
        assert pc.unended_actual(F14, as_of=date(2026, 12, 15), anchor=NOV_FYE) is None

    def test_inside_the_52_53_week_tolerance_is_kept(self) -> None:
        # Projected end 2026-11-28; a 52/53-week year may end a week earlier.
        assert pc.unended_actual(F14, as_of=date(2026, 11, 22), anchor=NOV_FYE) is None
        assert pc.unended_actual(F14, as_of=date(2026, 11, 20), anchor=NOV_FYE) is not None

    def test_an_unknown_fiscal_calendar_judges_no_fiscal_period(self) -> None:
        assert pc.unended_actual(F14, as_of=RUN_DATE, anchor=None) is None

    @pytest.mark.parametrize(
        "statement",
        [
            "Vexmoor expects FY2026 revenue of USD 26.6bn.",
            "Vexmoor guided FY2026 total revenue to USD 26.6bn.",
            "Consensus puts Vexmoor FY2026 revenue at USD 26.6bn.",
            "Trefis estimates Vexmoor FY2026 total revenue at 26.58 Bil.",
            "Vexmoor's FY2026 revenue is expected to reach USD 26.6bn.",
            "Vexmoor's FY2026 outlook calls for ARR growth of 10.2%.",
            "Vexmoor targets FY2026 revenue of USD 26.6bn.",
            "Vexmoor FY2026 revenue is projected at USD 26.6bn.",
            "Vexmoor FY2026 revenue will be about USD 26.6bn.",
            # A part-period actual of a year that has not ended is a real figure.
            "Vexmoor FY2026 year-to-date revenue was USD 19.5bn.",
            "Vexmoor revenue for the first nine months of FY2026 was USD 19.5bn.",
        ],
    )
    def test_forward_looking_or_part_period_wording_is_kept(self, statement: str) -> None:
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=NOV_FYE) is None

    def test_an_ended_quarter_of_the_open_year_is_kept(self) -> None:
        statement = "Vexmoor's Q3 FY2026 revenue was USD 6,760m (quarter ended 2026-08-28)."
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=NOV_FYE) is None

    def test_a_period_with_no_figure_is_kept(self) -> None:
        assert pc.unended_actual(
            "Vexmoor's FY2026 began on 2025-11-29.", as_of=RUN_DATE, anchor=NOV_FYE
        ) is None

    @pytest.mark.parametrize(
        "statement",
        [
            "Vexmoor calendar Q4 2026 revenue was USD 7.1bn.",
            "Vexmoor revenue for Q4 CY2026 was USD 7.1bn.",
        ],
    )
    def test_a_calendar_quarter_is_not_judged(self, statement: str) -> None:
        # Review round 3: only the open FISCAL year or fiscal fourth quarter is judged.
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=DEC_FYE) is None

    def test_a_platform_quarter_key_on_a_december_year_end_is_a_calendar_quarter(self) -> None:
        statement = "Vexmoor 2026-Q4 revenue was USD 7.1bn."
        found = pc.unended_actual(statement, as_of=RUN_DATE, anchor=DEC_FYE)
        assert found is not None and found.ends == date(2026, 12, 31)
        # Unknown calendar: a fiscal-keyed quarter has no known end.
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=None) is None

    @pytest.mark.parametrize(
        "statement",
        [
            "Vexmoor CY2026 revenue was USD 27bn.",
            # Review round 3: the open label and the figure are not adjacent, or not in
            # the same clause, or an earlier year's figure stands beside the label.
            "Vexmoor's revenue was USD 23.8bn, and FY2026 results are due soon.",
            "Vexmoor's FY2026 revenue rose to USD 26.6bn.",
            "Vexmoor's 2025 revenue was USD 23.8bn; FY2026 revenue was USD 26.6bn.",
            "Vexmoor's FY2026 annual-recurring revenue was USD 25.2bn.",
            "Vexmoor's FY2026 revenue was USD 26.6bn, ARR rising 10%.",
            # A printed date or month anywhere: the figure may stand at that point.
            "Vexmoor revenue for the quarter ending November 27, 2026 was USD 7.1bn.",
            "Vexmoor full-year FY2026 revenue was USD 26.6bn as of 2026-08-28.",
            # A point-in-time metric or a growth rate alone is never judged.
            "Vexmoor's full-year FY2026 ending ARR was USD 18.6bn.",
            "Vexmoor's full-year FY2026 revenue growth was 10.7%.",
        ],
    )
    def test_only_the_allow_listed_shape_is_judged(self, statement: str) -> None:
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=NOV_FYE) is None

    @pytest.mark.parametrize(
        "ticker,as_of,statement",
        [*ROUND_1, *ROUND_2, *ROUND_3],
        ids=[f"r1-{i}" for i in range(len(ROUND_1))]
        + [f"r2-{i}" for i in range(len(ROUND_2))]
        + [f"r3-{i}" for i in range(len(ROUND_3))],
    )
    def test_the_review_batteries(self, ticker: str, as_of: str, statement: str) -> None:
        """The three review batteries of PR #309 (140 statements): every TRUE statement is
        kept; only F14 and an open full-year result are dropped."""
        found = pc.unended_actual(
            statement, as_of=date.fromisoformat(as_of), anchor=BATTERY_ANCHORS[ticker]
        )
        dropped = found is not None and names_the_subject(
            statement, BATTERY_NAMES[ticker], ticker
        )
        assert dropped == (statement in BATTERY_DROPS)

    @pytest.mark.parametrize(
        "statement",
        [
            "Vexmoor's FY2026 revenue was USD 26.6bn.",
            "Vexmoor FY2026 revenue: USD 26.6bn.",
            "Vexmoor's Q4 FY2026 revenue was USD 7.0bn.",
            "Vexmoor's fourth quarter of fiscal 2026 net income was USD 1.9bn.",
        ],
    )
    def test_an_adjacent_open_period_result_is_caught(self, statement: str) -> None:
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=NOV_FYE) is not None

    def test_a_possessive_owner_must_open_the_sentence_and_be_the_subject(self) -> None:
        statement = "Northwind's FY2026 revenue was USD 26.6bn."
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=NOV_FYE) is not None
        assert pc.unended_actual(
            statement, as_of=RUN_DATE, anchor=NOV_FYE, subject_names=("VXM", "vexmoor")
        ) is None
        assert pc.unended_actual(
            "Vexmoor's FY2026 revenue was USD 26.6bn.",
            as_of=RUN_DATE,
            anchor=NOV_FYE,
            subject_names=("VXM", "vexmoor"),
        ) is not None
        assert pc.unended_actual(
            "Vexmoor's revenue trails Northwind's FY2026 revenue of USD 30bn.",
            as_of=RUN_DATE,
            anchor=NOV_FYE,
        ) is None

    def test_a_full_open_year_result_is_still_caught(self) -> None:
        found = pc.unended_actual(
            "Honeywell's full year 2026 sales were $40 billion.", as_of=RUN_DATE, anchor=DEC_FYE
        )
        assert found is not None and found.ends == date(2026, 12, 31)

    def test_target_the_company_is_not_the_word_target(self) -> None:
        assert not pc.is_forward_or_partial("Target's revenue was $1 billion.")
        assert pc.is_forward_or_partial("Adobe targets revenue of $25.9 billion.")
        assert pc.is_forward_or_partial("Analysts raised the price target to $500.")
        # So Target's own open year is still judged (both naming readings open).
        found = pc.unended_actual(
            "Target's full-year FY2026 revenue was $110 billion.",
            as_of=date(2026, 1, 15),
            anchor=TGT_FYE,
        )
        assert found is not None and found.ends == date(2026, 1, 31)

    def test_a_result_needs_a_reporting_verb(self) -> None:
        # "FY2026 sales are $40B" (present tense) and a bare noun phrase are not judged.
        assert pc.unended_actual(
            "Honeywell FY2026 sales are $40 billion.", as_of=RUN_DATE, anchor=DEC_FYE
        ) is None
        assert pc.unended_actual(
            "Vexmoor FY2026: revenue USD 26.6bn.", as_of=RUN_DATE, anchor=NOV_FYE
        ) is None

    def test_the_subjects_own_names(self) -> None:
        assert names_the_subject(F14, "Vexmoor Holdings Inc.", "VXM")
        assert names_the_subject("VXM FY2026 revenue was $7bn", "Vexmoor Holdings Inc.", "VXM")
        assert names_the_subject("The company's FY2026 revenue was $7bn", "Vexmoor", None)
        assert not names_the_subject("FY2026 revenue was $7bn", "Vexmoor Holdings Inc.", "VXM")


# ---------------------------------------------------------------------------
# The investigator: the check runs at the write-up, on web-only statements
# ---------------------------------------------------------------------------


class _Client:
    def __init__(self, statements: list[str]) -> None:
        self._payload = {
            "findings": [
                {"statement": s, "evidence_ids": ["ev:c:8781a617"], "direction": "neutral"}
                for s in statements
            ]
        }
        self.last_response_truncated = False

    async def complete_json(self, system: str, user: str, **kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
        return self._payload


def _question() -> PlannedQuestion:
    return PlannedQuestion(
        key="industry_economics",
        text="What are the economics of the industry the company sells into?",
        origin="playbook",
        required_tools=frozenset({"search_company_corpus"}),
    )


def _evidence(*, web: bool) -> list[_Evidence]:
    return [
        _Evidence(
            citation_id="ev:c:8781a617",
            kind="search_web_corpus" if web else "search_company_corpus",
            text='{"evidence_id": "ev:c:8781a617", "text": "…"}',
            web=web,
            source_class="secondary_web" if web else None,
        )
    ]


async def _run(
    monkeypatch: pytest.MonkeyPatch,
    statements: list[str],
    *,
    anchor: pc.FiscalYearAnchor | None = NOV_FYE,
    as_of: date = RUN_DATE,
    web: bool = True,
    other_names: dict[str, str] | None = None,
) -> tuple[LLMInvestigator, list[Any], list[Any], list[str]]:
    reads: list[str] = []

    async def _anchor(ticker: str | None, exchange: str | None) -> pc.FiscalYearAnchor | None:
        reads.append(str(ticker))
        return anchor

    monkeypatch.setattr(inv_mod, "_load_fiscal_anchor", _anchor)
    inv = LLMInvestigator(
        session=object(),
        company_id=uuid.uuid4(),
        ticker="VXM",
        exchange="US",
        client=_Client(statements),
        company_name="Vexmoor Holdings Inc.",
        as_of=as_of,
    )
    inv._other_names = dict(other_names or {})
    findings, gaps, _answered = await inv._write_up(
        "industry_analyst", _question(), _evidence(web=web)
    )
    return inv, findings, gaps, reads


def _unended_gaps(gaps: list[Any]) -> list[Any]:
    return [g for g in gaps if g.gap_type == "conflicting_sources"]


class TestInvestigatorGuard:
    async def test_f14_before_the_year_end_is_dropped_with_one_gap(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        kept = "Vexmoor FY2025 revenue was USD 23,769m."
        inv, findings, gaps, reads = await _run(monkeypatch, [F14, F14, kept])
        assert [f.statement for f in findings] == [kept]
        assert inv.diagnostics.statements_for_unended_period == 2
        assert inv.diagnostics.to_dict()["statements_for_unended_period"] == 2
        (gap,) = _unended_gaps(gaps)
        assert "FY2026 (ends about 2026-11-28)" in gap.description
        assert "2026-11-01" in gap.description
        # No statement figure rides into the gap text.
        assert "26.58" not in gap.description and "10.2" not in gap.description
        # The calendar is read once per investigator, and only when it can matter.
        assert reads == ["VXM"]

    async def test_f14_after_the_year_end_is_kept(self, monkeypatch: pytest.MonkeyPatch) -> None:
        inv, findings, gaps, _reads = await _run(monkeypatch, [F14], as_of=date(2026, 12, 15))
        assert len(findings) == 1
        assert inv.diagnostics.statements_for_unended_period == 0
        assert not _unended_gaps(gaps)

    async def test_forward_looking_wording_is_kept_without_reading_the_calendar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv, findings, _gaps, reads = await _run(
            monkeypatch, ["Trefis estimates Vexmoor FY2026 total revenue at 26.58 Bil."]
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_for_unended_period == 0
        assert reads == []

    async def test_an_unknown_fiscal_calendar_keeps_the_statement(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv, findings, gaps, _reads = await _run(monkeypatch, [F14], anchor=None)
        assert len(findings) == 1
        assert inv.diagnostics.statements_for_unended_period == 0
        assert not _unended_gaps(gaps)

    async def test_a_future_calendar_quarter_is_kept(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Review round 3: calendar periods are no longer judged at all.
        inv, findings, gaps, reads = await _run(
            monkeypatch, ["Vexmoor calendar Q4 2026 revenue was USD 7.1bn."], anchor=None
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_for_unended_period == 0
        assert not _unended_gaps(gaps)
        assert reads == []  # the shape is not judged, so the calendar is never read

    async def test_a_statement_resting_on_a_filing_is_not_judged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv, findings, _gaps, reads = await _run(monkeypatch, [F14], web=False)
        assert len(findings) == 1
        assert inv.diagnostics.statements_for_unended_period == 0
        assert reads == []

    async def test_a_statement_that_does_not_name_the_subject_is_not_judged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # It may be another company's fiscal year (a June year-end's FY2026 has ended).
        inv, findings, _gaps, _reads = await _run(
            monkeypatch, ["Northwind FY2026 total revenue was USD 245bn."]
        )
        assert len(findings) == 1
        assert inv.diagnostics.statements_for_unended_period == 0

    async def test_a_statement_naming_another_held_company_is_not_judged(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        others = {"northwind": "Northwind Corp", "total": "Total Holdings SA"}
        inv, findings, _gaps, _reads = await _run(
            monkeypatch,
            [
                "Vexmoor FY2026 revenue was USD 26.6bn, below Northwind.",
                # A held company's name as an ordinary lower-case word is not that company.
                F14,
            ],
            other_names=others,
        )
        assert [f.statement for f in findings] == [
            "Vexmoor FY2026 revenue was USD 26.6bn, below Northwind."
        ]
        assert inv.diagnostics.statements_for_unended_period == 1

    async def test_an_unreadable_calendar_fails_open(self, monkeypatch: pytest.MonkeyPatch) -> None:
        async def _boom(ticker: str | None, exchange: str | None) -> None:
            raise RuntimeError("data.sec.gov unreachable")

        monkeypatch.setattr(inv_mod, "_load_fiscal_anchor", _boom)
        inv = LLMInvestigator(
            session=object(),
            company_id=uuid.uuid4(),
            ticker="VXM",
            exchange="US",
            client=_Client([F14]),
            company_name="Vexmoor Holdings Inc.",
            as_of=RUN_DATE,
        )
        findings, _gaps, _a = await inv._write_up(
            "industry_analyst", _question(), _evidence(web=True)
        )
        assert len(findings) == 1


class _Normalised:
    def __init__(self, basis: str, fiscal_year: int | None, end: str | None) -> None:
        self.period_basis = basis
        self.fiscal_year = fiscal_year
        self.reporting_period_end = end


class TestLoader:
    """``load_fiscal_year_anchor`` with the companyfacts read and the normaliser mocked."""

    @pytest.fixture(autouse=True)
    def _fresh_cache(self) -> Any:  # noqa: ANN401
        pc.reset_cache()
        yield
        pc.reset_cache()

    @staticmethod
    def _mock(
        monkeypatch: pytest.MonkeyPatch,
        *,
        facts: dict | None,
        normalised: _Normalised | None = None,
        delay: float = 0.0,
    ) -> list[str]:
        import asyncio

        from app.integrations import sec_fundamentals_normalizer
        from app.services.agent_tools import peers

        calls: list[str] = []

        async def _facts_for(ticker: str, exchange: str | None, provider: Any):  # noqa: ANN202, ANN401
            calls.append(ticker)
            if delay:
                await asyncio.sleep(delay)
            return facts, None if facts is not None else "unreachable"

        monkeypatch.setattr(peers, "_facts_for", _facts_for)
        monkeypatch.setattr(
            sec_fundamentals_normalizer,
            "normalize_company_facts",
            lambda _facts, _ticker: normalised,
        )
        return calls

    async def test_an_annual_basis_gives_an_anchor_and_is_cached(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls = self._mock(
            monkeypatch, facts={"facts": {}}, normalised=_Normalised("annual", 2025, "2025-11-28")
        )
        assert await pc.load_fiscal_year_anchor("VXM", "US") == NOV_FYE
        assert await pc.load_fiscal_year_anchor("VXM", "US") == NOV_FYE
        assert calls == ["VXM"]

    async def test_a_quarterly_basis_gives_no_anchor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # No 10-K normalised: a 10-Q period end is not a fiscal year end.
        self._mock(
            monkeypatch, facts={"facts": {}}, normalised=_Normalised("quarterly", 2026, "2026-08-28")
        )
        assert await pc.load_fiscal_year_anchor("VXM", "US") is None

    async def test_a_failure_is_retried_after_the_failure_ttl_only(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from datetime import datetime, timezone

        calls = self._mock(monkeypatch, facts=None)
        assert await pc.load_fiscal_year_anchor("VXM", "US") is None
        assert await pc.load_fiscal_year_anchor("VXM", "US") is None
        assert calls == ["VXM"]  # remembered inside the failure TTL
        key = "VXM|US"
        stamp, value = pc._ANCHOR_CACHE[key]
        pc._ANCHOR_CACHE[key] = (stamp - pc.ANCHOR_FAILURE_TTL, value)
        assert await pc.load_fiscal_year_anchor("VXM", "US") is None
        assert calls == ["VXM", "VXM"]
        # A success stays for the long TTL, well past the failure TTL.
        self._mock(
            monkeypatch, facts={"facts": {}}, normalised=_Normalised("annual", 2025, "2025-11-28")
        )
        pc.reset_cache()
        assert await pc.load_fiscal_year_anchor("VXM", "US") == NOV_FYE
        stamp, value = pc._ANCHOR_CACHE[key]
        pc._ANCHOR_CACHE[key] = (stamp - pc.ANCHOR_FAILURE_TTL * 2, value)
        assert pc._ANCHOR_CACHE[key][0] > datetime.now(timezone.utc) - pc.ANCHOR_CACHE_TTL
        assert await pc.load_fiscal_year_anchor("VXM", "US") == NOV_FYE

    async def test_a_slow_read_times_out_to_no_anchor(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(pc, "ANCHOR_READ_TIMEOUT_SECONDS", 0.05)
        self._mock(
            monkeypatch,
            facts={"facts": {}},
            normalised=_Normalised("annual", 2025, "2025-11-28"),
            delay=1.0,
        )
        assert await pc.load_fiscal_year_anchor("VXM", "US") is None

    async def test_a_subject_sec_does_not_cover_has_no_anchor(self) -> None:
        # The real reader: the exchange gate answers before any network call.
        assert await pc.load_fiscal_year_anchor("ALCLS", "PA") is None

    async def test_no_ticker_has_no_anchor(self) -> None:
        assert await pc.load_fiscal_year_anchor(None, "US") is None

    def test_the_counter_merges(self) -> None:
        a, b = InvestigatorDiagnostics(), InvestigatorDiagnostics()
        a.statements_for_unended_period, b.statements_for_unended_period = 1, 2
        a.merge(b)
        assert a.statements_for_unended_period == 3
