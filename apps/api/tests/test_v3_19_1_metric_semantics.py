"""V3.19.1 — a balance is not the cost of carrying it, nor a multiple of it.

Kering's reported "cost of net debt" of €122m (an income-statement charge) was read as its
net debt. These tests pin the general rule in every label matcher that maps text to a
balance field: prose, table rows and the excerpt ranker. No issuer is named in the code.
"""

from __future__ import annotations

import pytest

from app.services.sources.document_text_extractor import (
    DocumentExcerpt,
    DocumentTextExtraction,
)
from app.services.sources.extracted_fact_validator import _match_label
from app.services.sources.financial_metric_signal import metric_value_matches
from app.services.sources.metric_semantics import is_not_a_balance
from app.services.sources.primary_fact_parser import parse_primary_facts


def _facts(text: str) -> list:
    extraction = DocumentTextExtraction(
        source_url="https://issuer.example/annual-report-2025.pdf",
        document_type="pdf",
        title="Annual Report 2025",
        inferred_year=2025,
        excerpts=[
            DocumentExcerpt(
                excerpt_id="X1",
                page_number=12,
                text=text,
                char_count=len(text),
                confidence="high",
                evidence_type="financial",
            )
        ],
    )
    return parse_primary_facts(extraction)


def _by_field(text: str) -> dict[str, list[float]]:
    out: dict[str, list[float]] = {}
    for fact in _facts(text):
        out.setdefault(fact.field, []).append(fact.numeric_value)
    return out


# ── prose ──────────────────────────────────────────────────────────────────── #


def test_cost_of_net_debt_is_not_net_debt_in_prose():
    got = _by_field("In 2025 the cost of net debt was €122 million.")
    assert 122.0 not in got.get("net_debt", [])


def test_cost_of_net_debt_beside_real_net_debt_keeps_only_the_balance():
    text = (
        "Net debt amounted to €10,522 million at December 31, 2025. "
        "The cost of net debt was €122 million."
    )
    got = _by_field(text)
    assert got.get("net_debt") == [10522.0]


@pytest.mark.parametrize(
    "text",
    [
        "Interest on net debt of €95 million was charged in 2025.",
        "The change in net debt of €300 million reflects acquisitions.",
        "Average net debt of €4,100 million over the year.",
        "Change in cash and cash equivalents of €250 million.",
    ],
)
def test_flow_prefixes_never_yield_a_balance(text):
    got = _by_field(text)
    assert not got.get("net_debt")
    assert not got.get("cash")


def test_net_debt_to_ebitda_is_not_net_debt():
    got = _by_field("Net debt / EBITDA of 2.1x at year end; net debt to EBITDA 2 times.")
    assert not got.get("net_debt")


def test_plain_net_debt_still_parses():
    """The guard must not over-suppress: the balance itself still reads."""
    got = _by_field("Net debt was €2,315 million at the end of the year.")
    assert got.get("net_debt") == [2315.0]


def test_reduced_net_debt_to_a_value_still_parses():
    """ "to" followed by a money amount is not a ratio."""
    got = _by_field("Net debt: €1,800 million.")
    assert got.get("net_debt") == [1800.0]


# ── table rows ─────────────────────────────────────────────────────────────── #


@pytest.mark.parametrize(
    "row",
    [
        "Cost of net debt",
        "Cost of net financial debt",
        "Net debt / EBITDA",
        "Net debt to EBITDA ratio",
        "Net interest-bearing debt / EBITDA",
        "Change in cash and cash equivalents",
        "Interest on net debt",
    ],
)
def test_table_row_for_a_flow_or_ratio_is_refused(row):
    assert _match_label(row) is None


@pytest.mark.parametrize(
    ("row", "field"),
    [
        ("Net debt", "net_debt"),
        ("Net financial debt", "net_debt"),
        ("Net interest-bearing debt (NIBD)", "net_debt"),
        ("Cash and cash equivalents", "cash_and_equivalents"),
    ],
)
def test_table_row_for_the_balance_still_matches(row, field):
    assert _match_label(row) == field


# ── the shared predicate and the excerpt ranker ────────────────────────────── #


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Cost of net debt", True),
        ("Net debt / EBITDA", True),
        ("net debt-to-equity", True),
        ("Gearing: net debt ratio", True),
        ("Net debt", False),
        ("Net cash position", False),
        ("Total borrowings", False),
    ],
)
def test_is_not_a_balance(label, expected):
    assert is_not_a_balance(label) is expected


def test_excerpt_ranker_does_not_credit_cost_of_net_debt_as_net_debt():
    def fields(text: str) -> set[str]:
        return {field for field, _span in metric_value_matches(text)}

    assert "net_debt" not in fields("The cost of net debt was €122 million.")
    assert "net_debt" in fields("Net debt was €2,315 million.")
