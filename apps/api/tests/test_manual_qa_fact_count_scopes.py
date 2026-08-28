"""
Manual-QA — ONE authoritative meaning per displayed fact count.

A single report was showing four differently-scoped numbers under wording a
reader could only take as the same thing. Traced against the three live
reports:

| surface                                        | PNDORA | CFR      | MONC |
|------------------------------------------------|--------|----------|------|
| research memo, per document                    | 3, 14  | 1, 9, 4  | 8    |
| research memo, total                           | 55     | 34       | 8    |
| Primary Documents tab, per document            | 52,0,3 | 9, 24, 1 | 8    |
| Primary Documents tab, run summary             | 55     | 34       | 8    |
| evidence channel "issuer primary facts"        | 9      | 5        | 4    |

Every number is correct. Richemont's "4" and "24" are the SAME document —
its cited-evidence items and its persisted rows. Pandora's "14" and "52" are
the same document. The "9"/"5"/"4" are distinct FIELDS, not facts at all.

Forcing them to agree would mean hiding facts the report holds or inflating a
count past the rows that exist, so the rule is not "make them match". It is:
every displayed fact count names its own population, and two counts that claim
one population must agree.

These are GENERIC lineage tests — one per real document shape (multi-year
annual, three-document annual + results + quarter, single regulated-storage
current-period) — not issuer assertions: the figures are fixtures.

Fully offline and deterministic: no network, no LLM, no Azure, no DB.
"""

from __future__ import annotations

import pytest

from app.services.fact_count_scopes import (
    ALL_SCOPES,
    CITED_EVIDENCE,
    PERSISTED_VALIDATED,
    REPORT_PRIMARY,
    scope_for,
)
from app.services.report_consistency import (
    FACT_COUNT_SEMANTICS_MISMATCH,
    SEVERITY_SERIOUS,
    audit_report_consistency,
)


def _memo(total: int, rows: list[tuple[str, int]]) -> dict:
    """A research memo shaped exactly as the generator emits it."""
    return {
        "research_memo": {
            "primary_evidence_summary": {
                "primary_document_count": len(rows),
                "report_primary_fact_count": total,
                "fact_count_scope": REPORT_PRIMARY.key,
                "fact_count_label": REPORT_PRIMARY.label,
                "primary_documents": [
                    {
                        "title": title,
                        "excerpt_count": 0,
                        "cited_evidence_fact_count": cited,
                        "fact_count_scope": CITED_EVIDENCE.key,
                        "fact_count_label": CITED_EVIDENCE.label,
                    }
                    for title, cited in rows
                ],
            }
        }
    }


def _serious(report: dict) -> set[str]:
    audit = audit_report_consistency(report)
    return {f.invariant for f in audit.findings if f.severity == SEVERITY_SERIOUS}


# =========================================================================== #
# The three real document lineages                                            #
# =========================================================================== #


@pytest.mark.parametrize(
    ("lineage", "total", "rows"),
    [
        # Multi-year annual report + a current-period interim report. The
        # annual's 52 persisted facts become 14 cited-evidence items.
        ("multi_year_annual", 55, [("Annual Report", 14), ("Q2 Interim Report", 3)]),
        # Annual report + annual-results announcement + quarterly sales release.
        # The middle document's 24 persisted facts become 4 cited items.
        ("annual_results_quarter", 34, [("Annual Report", 9), ("Annual Results", 4), ("Q1 Sales", 1)]),
        # One regulated-storage current-period document; here the two
        # populations happen to coincide, which must ALSO be fine.
        ("regulated_storage_current", 8, [("H1 Financial Results", 8)]),
    ],
)
def test_a_lineage_with_differently_scoped_counts_is_not_a_contradiction(
    lineage: str, total: int, rows: list[tuple[str, int]]
) -> None:
    report = _memo(total, rows)
    assert FACT_COUNT_SEMANTICS_MISMATCH not in _serious(report), lineage


@pytest.mark.parametrize(
    ("lineage", "total", "rows"),
    [
        ("multi_year_annual", 55, [("Annual Report", 14), ("Q2 Interim Report", 3)]),
        ("annual_results_quarter", 34, [("Annual Report", 9), ("Annual Results", 4), ("Q1 Sales", 1)]),
        ("regulated_storage_current", 8, [("H1 Financial Results", 8)]),
    ],
)
def test_every_count_in_a_lineage_names_its_population(
    lineage: str, total: int, rows: list[tuple[str, int]]
) -> None:
    summary = _memo(total, rows)["research_memo"]["primary_evidence_summary"]
    assert scope_for(summary["fact_count_scope"]) is REPORT_PRIMARY
    assert "report_primary_fact_count" in summary
    assert "primary_fact_count" not in summary, "the unqualified key must be gone"
    for row in summary["primary_documents"]:
        assert scope_for(row["fact_count_scope"]) is CITED_EVIDENCE
        assert "cited_evidence_fact_count" in row
        assert "fact_count" not in row, "the unqualified key must be gone"


@pytest.mark.parametrize(
    ("lineage", "total", "rows"),
    [
        ("multi_year_annual", 55, [("Annual Report", 14), ("Q2 Interim Report", 3)]),
        ("annual_results_quarter", 34, [("Annual Report", 9), ("Annual Results", 4), ("Q1 Sales", 1)]),
    ],
)
def test_stripping_the_scope_from_a_lineage_row_is_caught(
    lineage: str, total: int, rows: list[tuple[str, int]]
) -> None:
    """The negative side of every lineage: drop the name and the audit fires."""
    report = _memo(total, rows)
    row = report["research_memo"]["primary_evidence_summary"]["primary_documents"][0]
    del row["cited_evidence_fact_count"]
    del row["fact_count_scope"]
    row["fact_count"] = 14
    assert FACT_COUNT_SEMANTICS_MISMATCH in _serious(report), lineage


def test_the_two_per_document_populations_may_differ_by_any_amount() -> None:
    """52 persisted vs 14 cited is not an error, and neither is 24 vs 4."""
    report = _memo(55, [("Annual Report", 14)])
    report["research_memo"]["primary_evidence_summary"]["primary_documents"][0][
        "excerpt_count"
    ] = 3
    api_row = {
        "title": "Annual Report",
        "persisted_validated_fact_count": 52,
        "fact_count_scope": PERSISTED_VALIDATED.key,
        "fact_count_label": PERSISTED_VALIDATED.label,
    }
    report["primary_documents_tab"] = {"documents": [api_row]}
    assert FACT_COUNT_SEMANTICS_MISMATCH not in _serious(report)


def test_a_zero_cited_count_is_readable_because_it_is_named() -> None:
    """The Moncler symptom: "0 fact(s)" beside a report total of 8. With the
    population named, zero means "nothing reached the evidence pack", never
    "this document produced nothing"."""
    report = _memo(8, [("H1 Financial Results", 0)])
    row = report["research_memo"]["primary_evidence_summary"]["primary_documents"][0]
    assert row["fact_count_label"] == "Cited evidence facts"
    assert "evidence budget" in scope_for(row["fact_count_scope"]).definition
    assert FACT_COUNT_SEMANTICS_MISMATCH not in _serious(report)


# =========================================================================== #
# The vocabulary itself                                                       #
# =========================================================================== #


def test_the_scope_vocabulary_is_closed_and_distinct() -> None:
    keys = [s.key for s in ALL_SCOPES]
    labels = [s.label for s in ALL_SCOPES]
    assert len(set(keys)) == len(keys)
    assert len(set(labels)) == len(labels)
    for scope in ALL_SCOPES:
        assert scope_for(scope.key) is scope


def test_no_scope_label_is_a_bare_fact_count() -> None:
    """"Fact count" alone is the wording this whole module exists to remove."""
    for scope in ALL_SCOPES:
        assert scope.label.strip().lower() not in {"facts", "fact count"}
