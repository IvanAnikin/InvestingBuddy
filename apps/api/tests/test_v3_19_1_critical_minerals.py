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
    assert "neodymium" in ree["listed_as"] and "praseodymium" in ree["listed_as"]
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


def test_designation_partially_evidences_the_dimension():
    report = pr.assemble(_inputs(designations_for(["rare_earths"])))
    view = _dimension(report)
    assert view["status"] == pr.STATUS_PARTIAL
    assert "90 FR 50494" in view["status_basis"]
    assert "supply concentration is not established" in view["status_basis"]


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
