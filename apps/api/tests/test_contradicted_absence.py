"""A finding that DENIES what another finding of the same run STATES — the Southern Copper
contradiction on the first live acceptance run.

One finding read "no interim or quarterly Group revenue appears, so sub-annual movement is
unobservable"; another, in the same report, read "2026-Q2 net sales were $4,289.0 million".
Both stood. The reconciler now records the pair as a disagreement — never silently keeping
two confident, incompatible statements — and stays narrow enough not to invent conflicts:
the denial must be a negated clause naming the field, in the same scope and project, and a
period-limited denial only conflicts with a statement of that period class.
"""

from __future__ import annotations

from app.services.pipeline import gap_reconciliation as gr

ABSENT_SUB_ANNUAL = (
    "The only period type carried by the evidence is annual (FY2025); no interim or "
    "quarterly Group revenue appears, so sub-annual movement is unobservable."
)
Q2 = "2026-Q2 net sales were $4,289.0 million for the three months ended June 30, 2026."
FY = "Group revenue for FY2025 (period ended 2025-12-31, Form 10-K) was USD 13,420.0 million."


def finding(fid: str, text: str, **kw: object) -> gr.FindingFacts:
    fields, project = gr.finding_fields(text)
    return gr.FindingFacts(
        finding_id=fid, statement=text, fields=fields, project=project,
        source_kinds=("issuer_filing",), **kw,  # type: ignore[arg-type]
    )


def pairs(*findings: gr.FindingFacts) -> list[tuple[str, str, str]]:
    return [(d.finding_a_id, d.finding_b_id, d.field_key)
            for d in gr.contradicted_absences(list(findings))]


class TestTheSouthernCopperCase:
    def test_a_quarterly_denial_contradicts_a_quarterly_statement(self) -> None:
        assert pairs(finding("a", ABSENT_SUB_ANNUAL), finding("b", Q2)) == [
            ("a", "b", "metric:revenue")
        ]

    def test_it_does_not_contradict_the_annual_statement_it_never_denied(self) -> None:
        assert pairs(finding("a", ABSENT_SUB_ANNUAL), finding("c", FY)) == []

    def test_the_conflict_is_recorded_as_a_disagreement_with_its_reason(self) -> None:
        (d,) = gr.contradicted_absences([finding("a", ABSENT_SUB_ANNUAL), finding("b", Q2)])
        assert d.reason == gr.CONTRADICTED_ABSENCE == "absence_contradicted"
        assert d.to_dict()["field"] == "metric:revenue"


class TestItNeverInventsAConflict:
    def test_an_annual_denial_contradicts_the_annual_statement_only(self) -> None:
        absent = finding("x", "No annual revenue figure appears in the evidence.")
        assert pairs(absent, finding("c", FY), finding("b", Q2)) == [("x", "c", "metric:revenue")]

    def test_a_denial_about_one_segment_does_not_contradict_a_group_statement(self) -> None:
        seg = finding("s", "No revenue is reported for the Mexico segment.",
                      scope_key="segment:mexico")
        assert pairs(seg, finding("c", FY)) == []

    def test_a_denial_about_one_project_does_not_contradict_another(self) -> None:
        a = finding("a", "No capex figure is disclosed for the Tia Maria project.")
        b = finding("b", "Capex for the El Pilar project is US$ 450 million.")
        assert pairs(a, b) == []

    def test_a_withdrawn_finding_takes_no_part(self) -> None:
        gone = finding("a", ABSENT_SUB_ANNUAL, withdrawn=True)
        assert pairs(gone, finding("b", Q2)) == []
        assert pairs(finding("a", ABSENT_SUB_ANNUAL), finding("b", Q2, withdrawn=True)) == []

    def test_a_denial_of_a_different_field_is_not_a_conflict(self) -> None:
        a = finding("a", "No capex figure is disclosed.")
        assert pairs(a, finding("b", Q2)) == []

    def test_a_finding_never_contradicts_itself(self) -> None:
        both = finding(
            "a", "No annual revenue appears; 2026-Q2 net sales were $4,289.0 million.")
        assert pairs(both) == []

    def test_an_affirmative_statement_is_never_a_denial(self) -> None:
        assert pairs(finding("c", FY), finding("b", Q2)) == []

    def test_the_list_is_bounded(self) -> None:
        many = [finding(f"s{i}", Q2) for i in range(60)]
        out = gr.contradicted_absences([finding("a", ABSENT_SUB_ANNUAL), *many])
        assert len(out) <= gr.MAX_DISAGREEMENTS


class TestWrongPeriodAndNotAFigureDenials:
    """Review finding: the first version compared only the period CLASS, so these denials were
    flagged against "FY2025 revenue was USD 13,420.0 million"."""

    import pytest as _pytest

    @_pytest.mark.parametrize(
        "denial",
        [
            "No revenue was reported for FY2024.",
            "There is no revenue guidance for FY2027.",
            "Revenue did not decline in 2025.",
            "No revenue for the 2023 period appears in the evidence.",
            "Management did not provide forecast revenue.",
            "No quarterly revenue appears for Q1 2025.",
            "Revenue is not guided for Q3 2027.",
        ],
    )
    def test_a_denial_about_another_period_or_a_forecast_is_not_a_conflict(
        self, denial: str
    ) -> None:
        assert pairs(finding("a", denial), finding("c", FY), finding("b", Q2)) == []

    def test_a_denial_of_the_same_year_still_conflicts(self) -> None:
        a = finding("a", "No revenue figure for FY2025 appears in the evidence.")
        assert pairs(a, finding("c", FY)) == [("a", "c", "metric:revenue")]
