"""A contradiction is two single claims about one quantity (ADR-071).

Live Adobe report (cross-sector QA campaign, 2026-11): a filing-matching segment
breakdown and a geography breakdown were compared against single group figures (one
of them the operating income read out of a margin sentence), and the "conflicts" this
manufactured relabelled both true statements "[reported in the press; a filing figure
differs]". The fixtures are the live statements' shapes; the issuer is synthetic.
"""

from __future__ import annotations

from app.services import research_fields as rf
from app.services.web_research import trust

ORIGIN = "issuer:synthetic"


def _filing(fid: str, statement: str) -> trust.ClaimSide:
    return trust.ClaimSide(
        finding_id=fid * 32, statement=statement, period="FY2025",
        fields=tuple(sorted(rf.fields_stated(statement))),
        support=(trust.SupportItem("fact-1", "issuer_filing", ORIGIN),),
    )


def _web(fid: str, statement: str, origin: str = "press.example") -> trust.ClaimSide:
    return trust.ClaimSide(
        finding_id=fid * 32, statement=statement, period="FY2025",
        fields=tuple(sorted(rf.fields_stated(statement))),
        support=(trust.SupportItem("ev:x:1", "unknown_web", origin, web=True),),
    )


MARGIN_SENTENCE = _filing(
    "a",
    "Synthetic Group FY2025: operating margin 36.63% and net margin 30.00%, computed from "
    "operating income USD 8,706m, net income USD 7,130m and revenue USD 23,769m.",
)
GROUP_REVENUE = _filing("b", "Synthetic Group revenue for FY2025 was USD 23,769m.")
SEGMENTS = _web(
    "c",
    "FY2025 revenue was split across three reportable segments (dollars in millions): "
    "Digital Media $17,649, Digital Experience $5,864, Publishing and Advertising $256, "
    "total $23,769.",
)
GEOGRAPHY = _web(
    "d",
    "FY2025 revenue by geography: United States $12,529M, other Americas $1,591M "
    "(Total Americas $14,120M), EMEA $6,289M, APAC $3,360M.",
)


def test_fixtures_state_revenue() -> None:
    for side in (MARGIN_SENTENCE, GROUP_REVENUE, SEGMENTS, GEOGRAPHY):
        assert "metric:revenue" in side.fields, side.statement


def test_a_breakdown_never_contradicts_a_single_group_figure() -> None:
    assert trust.find_contradictions(SEGMENTS, [GROUP_REVENUE, MARGIN_SENTENCE]) == []
    assert trust.find_contradictions(GEOGRAPHY, [GROUP_REVENUE, MARGIN_SENTENCE]) == []


def test_a_sentence_naming_several_amounts_is_not_one_revenue_value() -> None:
    single = _web("e", "Synthetic Group revenue for FY2025 was $23.8 billion.")
    assert trust.find_contradictions(single, [MARGIN_SENTENCE]) == []


def test_a_segment_figure_is_not_the_group_figure() -> None:
    segment = _web("f", "Digital Media segment revenue for FY2025 was $17.6 billion.")
    assert trust.find_contradictions(segment, [GROUP_REVENUE]) == []
    region = _web("g", "Revenue in the Americas region for FY2025 was $14.1 billion.")
    assert trust.find_contradictions(region, [GROUP_REVENUE]) == []


def test_two_single_group_figures_that_differ_still_contradict() -> None:
    wrong = _web("h", "Synthetic Group revenue for FY2025 was $31.2 billion.")
    (conflict,) = trust.find_contradictions(wrong, [GROUP_REVENUE])
    assert conflict.canonical_finding_id == GROUP_REVENUE.finding_id


def test_two_segment_figures_that_differ_still_contradict() -> None:
    a = _web("i", "Digital Media segment revenue for FY2025 was $17.6 billion.", "a.example")
    b = _web("j", "Digital Media segment revenue for FY2025 was $21.0 billion.", "b.example")
    assert len(trust.find_contradictions(a, [b])) == 1


def test_a_rounded_restatement_of_the_filing_is_not_a_conflict() -> None:
    rounded = _web("k", "Synthetic Group revenue for FY2025 was $23.8 billion.")
    assert trust.find_contradictions(rounded, [GROUP_REVENUE]) == []


def test_two_different_parts_are_not_one_quantity() -> None:
    media = _web("l", "Digital Media segment revenue for FY2025 was $17.6 billion.", "a.example")
    experience = _web(
        "m", "Digital Experience segment revenue for FY2025 was $5.9 billion.", "b.example")
    americas = _web("n", "Revenue in the Americas region for FY2025 was $14.1 billion.",
                    "c.example")
    assert trust.find_contradictions(media, [experience, americas]) == []


def test_one_amount_in_two_scales_is_one_value() -> None:
    twice = _web("o", "Synthetic Group revenue for FY2025 was $4.1bn (US$4,100m).")
    (conflict,) = trust.find_contradictions(twice, [GROUP_REVENUE])
    assert conflict.canonical_finding_id == GROUP_REVENUE.finding_id
