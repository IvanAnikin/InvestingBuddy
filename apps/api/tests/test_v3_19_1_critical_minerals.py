"""V3.19.1 — "critical mineral" is a designation by a government, not a company's wording.

V3.18 KNOWN LIMITATION 1: the ``critical_materials`` thesis dimension could not be
established for anyone, because the platform held no official list. These tests pin the
list's provenance, the commodity mapping, and the report grading it feeds.
"""

from __future__ import annotations

from app.services.pipeline import professional_research as pr
from app.services.pipeline.professional_research import (
    FindingView,
    QuestionView,
    ReportInputs,
)
from app.services.sources.critical_minerals import (
    CURRENT_LIST,
    LIST_2022,
    LIST_2025,
    designation,
    designations_for,
    verify_against_text,
)


def test_lists_carry_their_official_provenance():
    assert len(LIST_2022.minerals) == 50
    assert len(LIST_2025.minerals) == 60
    assert set(LIST_2022.minerals) < set(LIST_2025.minerals)
    assert CURRENT_LIST is LIST_2025
    assert LIST_2025.citation == "90 FR 50494"
    assert LIST_2025.document_number == "2025-19813"
    assert LIST_2022.citation == "87 FR 10381"
    for listing in (LIST_2022, LIST_2025):
        assert listing.source_url.startswith("https://www.")
        assert listing.tier == "T2_regulator_or_gov"


def test_2025_additions_are_exactly_the_published_ten():
    added = set(LIST_2025.minerals) - set(LIST_2022.minerals)
    assert added == {
        "boron", "copper", "lead", "metallurgical coal", "phosphate", "potash",
        "rhenium", "silicon", "silver", "uranium",
    }


def test_composite_commodities_map_to_their_listed_members():
    ree = designation("rare_earths")
    assert ree is not None and ree["designated"]
    assert "neodymium" in ree["list_entries"] and "praseodymium" in ree["list_entries"]
    # Shown as the list's entries for the composite, never as elements the company makes.
    assert ree["listed_as"] == "16 list entries for rare earths"
    assert designation("platinum_group_metals")["designated"]


def test_newest_list_is_cited():
    assert designation("gallium")["list"]["version"] == "2025"
    # Copper was added in 2025: before that, it was not designated at all.
    assert designation("copper")["list"]["version"] == "2025"
    assert designation("copper", lists=(LIST_2022,)) is None


def test_undesignated_commodity_says_so_rather_than_unknown():
    out = designations_for(["gold", "iron_ore", "molybdenum"])
    assert [d["designated"] for d in out] == [False, False, False]


def test_verify_against_text_reports_missing_names():
    assert verify_against_text("aluminum, antimony", LIST_2022)  # most names absent
    full = ", ".join(LIST_2025.minerals)
    assert verify_against_text(full, LIST_2025) == []


# ── report grading ─────────────────────────────────────────────────────────── #


def _inputs(designations):
    questions = [
        QuestionView(
            key="thesis_fit__critical_materials", text="?", domain="thesis_fit",
            contract_status="unmet",
        )
    ]
    return ReportInputs(
        subject={"ticker": "MP", "exchange": "US", "name": "MP Materials"},
        questions=questions,
        findings=[
            FindingView(
                finding_id="a", statement="NdPr oxide output rose 12% in 2025.",
                domain="operations", question_key=None, evidence_ids=("ev:a",),
            )
        ],
        thesis={
            "thesis_text": "critical materials",
            "dimensions": ["critical_materials"],
            "critical_mineral_designations": designations,
        },
    )


def _dimension(report):
    section = next(s for s in report["sections"] if s["key"] == "thesis_fit")
    return next(d for d in section["dimensions"] if d["dimension"] == "critical_materials")


def test_designation_plus_a_product_finding_partially_evidences_the_dimension():
    report = pr.assemble(_inputs(designations_for(["rare_earths"])))
    view = _dimension(report)
    # The finding "NdPr oxide output rose 12%" states the company produces rare earths.
    assert view["status"] == pr.STATUS_PARTIAL
    assert "90 FR 50494" in view["status_basis"]
    assert "supply concentration is not established" in view["status_basis"]


def test_a_designation_without_a_product_finding_does_not_upgrade():
    """A battery maker's filings talk about lithium; that is not producing lithium."""
    inputs = _inputs(designations_for(["lithium"]))
    inputs.findings = [
        FindingView(
            finding_id="b", statement="Battery demand depends on lithium availability.",
            domain="operations", question_key=None, evidence_ids=("ev:b",),
        )
    ]
    view = _dimension(pr.assemble(inputs))
    assert view["status"] == pr.STATUS_NOT_ESTABLISHED
    assert "no finding states the company produces or sells it" in view["status_basis_note"]


def test_a_negated_product_statement_does_not_count():
    inputs = _inputs(designations_for(["gallium"]))
    inputs.findings = [
        FindingView(
            finding_id="n", statement="The company does not produce gallium.",
            domain="operations", question_key=None, evidence_ids=("ev:n",),
        )
    ]
    assert _dimension(pr.assemble(inputs))["status"] == pr.STATUS_NOT_ESTABLISHED


def test_malformed_designations_never_crash_assembly():
    for bad in ([{"designated": True}], [{"designated": True, "commodity": "x", "list": None}],
                ["junk"], {"not": "a list"}):
        pr.assemble(_inputs(bad))


def test_no_designation_leaves_the_dimension_unestablished():
    report = pr.assemble(_inputs(designations_for(["gold"])))
    view = _dimension(report)
    assert view["status"] == pr.STATUS_NOT_ESTABLISHED
    assert "not on the official" in view["status_basis_note"] or "none of" in view[
        "status_basis_note"
    ]


def test_company_wording_never_designates():
    """A thesis without designations (no official source consulted) stays unestablished,
    whatever the findings say about 'critical inputs'."""
    inputs = _inputs(None)
    inputs.findings = [
        FindingView(
            finding_id="w", statement="Our products are a critical input to defence.",
            domain="thesis_fit", question_key="thesis_fit__critical_materials",
            evidence_ids=("ev:w",),
        )
    ]
    view = _dimension(pr.assemble(inputs))
    assert "official_designations" not in view


import pytest  # noqa: E402

from app.services.macro.commodities import commodity_for  # noqa: E402
from app.services.pipeline.professional_research import _names_production  # noqa: E402


@pytest.mark.parametrize(
    ("statement", "slug", "expected"),
    [
        ("NdPr oxide output rose 12% in 2025.", "rare_earths", True),
        ("The company produces copper concentrate in Peru.", "copper", True),
        ("We mined 1.2 Mt of copper in 2025.", "copper", True),
        ("Our revenue is sensitive to lithium prices.", "lithium", False),
        ("Lithium supply constraints could raise our battery costs.", "lithium", False),
        ("Rising copper prices increased our manufacturing costs.", "copper", False),
        ("We sell electric vehicles containing nickel and cobalt.", "nickel", False),
    ],
)
def test_production_requires_the_commodity_as_the_product(statement, slug, expected):
    alternation = "|".join(commodity_for(slug).patterns)
    assert _names_production(statement, alternation) is expected
