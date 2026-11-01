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
RUN_DATE = date(2026, 11, 1)

#: The live F14 shape, with the synthetic issuer's name.
F14 = (
    "Trefis reports Vexmoor FY2026 total revenue 26.58 Bil and ending ARR growth 10.2%, "
    "per its Q4 2026 summary."
)


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
            "Vexmoor CY2026 revenue was USD 27bn.",
        ],
    )
    def test_a_future_calendar_period_is_caught_without_a_fiscal_calendar(
        self, statement: str
    ) -> None:
        found = pc.unended_actual(statement, as_of=RUN_DATE, anchor=None)
        assert found is not None and found.ends == date(2026, 12, 31)

    def test_a_platform_quarter_key_on_a_december_year_end_is_a_calendar_quarter(self) -> None:
        statement = "Vexmoor 2026-Q4 revenue was USD 7.1bn."
        found = pc.unended_actual(statement, as_of=RUN_DATE, anchor=DEC_FYE)
        assert found is not None and found.ends == date(2026, 12, 31)
        # Unknown calendar: a fiscal-keyed quarter has no known end.
        assert pc.unended_actual(statement, as_of=RUN_DATE, anchor=None) is None

    def test_a_printed_future_end_date_is_caught(self) -> None:
        statement = "Vexmoor revenue for the quarter ending November 27, 2026 was USD 7.1bn."
        found = pc.unended_actual(statement, as_of=RUN_DATE, anchor=None)
        assert found is not None and found.ends == date(2026, 11, 27)

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

    async def test_a_future_calendar_quarter_is_dropped_without_a_fiscal_calendar(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        inv, findings, gaps, reads = await _run(
            monkeypatch, ["Vexmoor calendar Q4 2026 revenue was USD 7.1bn."], anchor=None
        )
        assert findings == []
        assert inv.diagnostics.statements_for_unended_period == 1
        (gap,) = _unended_gaps(gaps)
        assert "calendar Q4 2026 (ends 2026-12-31)" in gap.description
        assert reads == []  # a calendar period needs no issuer calendar

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
                "Vexmoor FY2026 revenue trails Northwind's FY2026 revenue of USD 245bn.",
                # A held company's name as an ordinary lower-case word is not that company.
                F14,
            ],
            other_names=others,
        )
        assert [f.statement for f in findings] == [
            "Vexmoor FY2026 revenue trails Northwind's FY2026 revenue of USD 245bn."
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


class TestLoader:
    async def test_a_subject_sec_does_not_cover_has_no_anchor(self) -> None:
        pc.reset_cache()
        assert await pc.load_fiscal_year_anchor("ALCLS", "PA") is None

    async def test_no_ticker_has_no_anchor(self) -> None:
        assert await pc.load_fiscal_year_anchor(None, "US") is None

    def test_the_counter_merges(self) -> None:
        a, b = InvestigatorDiagnostics(), InvestigatorDiagnostics()
        a.statements_for_unended_period, b.statements_for_unended_period = 1, 2
        a.merge(b)
        assert a.statements_for_unended_period == 3
