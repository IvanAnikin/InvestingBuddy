"""
Current-period acceptance — reaching the COUNCIL and the DEEP FIELD REVIEW.

Retrieving a current-period document, and dating its figures correctly, is only
worth anything if the two councils can see it for what it is.

C1  **The council could not tell current from annual.** Interim facts did reach
    it as ordinary per-fact items, but nothing said which of them were the
    issuer's NEWEST reporting, and nothing said they are not comparable with
    the annual figures beside them. A council that cannot see the difference
    writes the two into one sentence. This mirrors what PR-B did for the annual
    TREND, for the same reason: a compact, explicitly-labelled slice that
    survives the evidence cap.

C2  **Venue-sourced facts were budgeted as the lowest-priority bucket.** The new
    `regulated_disclosure_financial_fact` source_type matched no budget
    category, so it fell to `source_reference` and would be dropped FIRST under
    pressure — for an issuer whose own website is down, those are the only
    financial facts it has.

C3  **The Deep Field Review had to infer currentness.** It re-presents each
    linked report's persisted datapoints, so it could see `revenue_current_period`
    but had to read the SUFFIX to know what that meant, and had no way to state
    "no current-period reporting was retrieved for this company" as a fact. That
    is the same shape as the live defect where one company's missing LEI became
    a claim about both: a per-company field must be stated, not inferred across
    companies.

Fully offline and deterministic: no network, no LLM, no Azure, no DB.
"""

from __future__ import annotations

from app.services.field_review_evidence_pack import _reporting_periods
from app.services.final_report_generator import _build_financial_snapshot
from app.services.llm.council import _DOCUMENT_FACT_TYPES, _DOCUMENT_SOURCE_TYPES
from app.services.llm.evidence_budget import (
    CATEGORY_PRIMARY_DOCUMENT,
    CATEGORY_SOURCE_REFERENCE,
    evidence_category,
)
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.schemas import EvidenceItem
from app.services.sources.company_evidence import (
    VENUE_DOCUMENT_EXCERPT_TYPE,
    VENUE_DOCUMENT_FACT_TYPE,
)

_CURRENT_STATE = "current_period_financial_state"


def _fact(field: str, value: float, period: str, **kw) -> dict:
    fact = {
        "field": field,
        "numeric_value": value,
        "value": str(value),
        "period": period,
        "currency": "DKK",
        "scale": "million",
        "confidence": "high",
        "scope": "group",
        "source_url": "https://issuer.test/doc.pdf",
    }
    fact.update(kw)
    return fact


def _pack(facts, **kw):
    return build_evidence_pack(report_content={}, historical_facts=facts, **kw)


def _current_items(pack):
    return [i for i in pack.evidence_items if i.source_type == _CURRENT_STATE]


# =========================================================================== #
# C1 — the council's current-period slice                                     #
# =========================================================================== #


def test_current_period_facts_reach_the_council_with_their_period_labels() -> None:
    """Required test 11."""
    pack = _pack(
        [
            _fact("revenue", 32549, "2025"),
            _fact("revenue", 14328, "2026-H1"),
            _fact("revenue", 7219, "2026-Q2"),
            _fact("net_income", 1817, "2026-H1"),
        ]
    )
    items = _current_items(pack)
    assert items, [i.source_type for i in pack.evidence_items]

    header = items[0]
    assert "Latest annual period: FY2025" in header.excerpt
    assert "Latest interim: H1 2026" in header.excerpt
    assert "Latest quarter: Q2 2026" in header.excerpt

    by_period = {(i.title.split(" — ")[0], i.period): i for i in items[1:]}
    assert by_period[("revenue", "2026-H1")].excerpt.count("H1 2026") == 1
    assert "14328" in by_period[("revenue", "2026-H1")].excerpt
    assert "Q2 2026" in by_period[("revenue", "2026-Q2")].excerpt
    assert "7219" in by_period[("revenue", "2026-Q2")].excerpt
    # The ANNUAL figure is never in this slice.
    assert not any("32549" in i.excerpt for i in items)


def test_the_slice_states_that_the_two_are_not_comparable() -> None:
    items = _current_items(_pack([_fact("revenue", 14328, "2026-H1")]))
    assert "does not supersede the annual figure" in items[0].excerpt
    assert "not been annualised or extrapolated" in items[0].excerpt
    assert all("not comparable with an annual figure" in i.excerpt for i in items[1:])


def test_no_interim_figure_is_annualised_for_the_council() -> None:
    items = _current_items(_pack([_fact("revenue", 14328, "2026-H1")]))
    joined = " ".join(i.excerpt for i in items)
    assert "28656" not in joined  # 2x
    assert "run rate" not in joined.lower()


def test_a_segment_current_period_figure_states_its_own_scope() -> None:
    """Required test 8, at the council surface."""
    items = _current_items(
        _pack(
            [
                _fact("revenue", 14328, "2026-H1", scope="group"),
                _fact("revenue", 200.3, "2026-H1", scope="Stone Island"),
            ]
        )
    )
    scoped = {i.scope for i in items[1:]}
    assert scoped == {"group", "Stone Island"}
    assert any("Stone Island" in i.excerpt for i in items[1:])


def test_an_issuer_with_no_current_period_reporting_gets_no_slice() -> None:
    assert _current_items(_pack([_fact("revenue", 32549, "2025")])) == []
    assert _current_items(_pack([])) == []
    assert _current_items(_pack(None)) == []


def test_the_slice_is_bounded() -> None:
    facts = [
        _fact(metric, 100 + n, "2026-H1", scope=f"Segment {n}")
        for n, metric in enumerate(
            ["revenue", "operating_profit", "net_income"] * 8, start=1
        )
    ]
    items = _current_items(_pack(facts))
    assert len(items) <= 1 + 8  # header + max_lines


# =========================================================================== #
# C2 — venue-sourced facts must not be dropped first                          #
# =========================================================================== #


def _item(source_type: str) -> EvidenceItem:
    return EvidenceItem(
        id="E1",
        source_tier="T1_primary_filing",
        content_tier="T1_primary_filing",
        source_type=source_type,
        data_quality="B",
    )


def test_a_venue_document_fact_is_budgeted_as_primary_document_evidence() -> None:
    assert evidence_category(_item(VENUE_DOCUMENT_FACT_TYPE)) == CATEGORY_PRIMARY_DOCUMENT
    assert (
        evidence_category(_item(VENUE_DOCUMENT_EXCERPT_TYPE))
        == CATEGORY_PRIMARY_DOCUMENT
    )
    # The regression: it used to fall through to the first-dropped bucket.
    assert evidence_category(_item("something_unknown")) == CATEGORY_SOURCE_REFERENCE


def test_a_venue_document_fact_counts_as_document_evidence() -> None:
    assert VENUE_DOCUMENT_FACT_TYPE in _DOCUMENT_FACT_TYPES
    assert VENUE_DOCUMENT_FACT_TYPE in _DOCUMENT_SOURCE_TYPES
    assert VENUE_DOCUMENT_EXCERPT_TYPE in _DOCUMENT_SOURCE_TYPES


# =========================================================================== #
# C3 — the Deep Field Review reads the EXACT linked report's state            #
# =========================================================================== #


def test_the_review_reads_the_reporting_states_off_the_linked_report() -> None:
    """Required test 12."""
    snapshot = _build_financial_snapshot(
        None,
        None,
        primary_facts=[
            _fact("revenue", 32549, "2025"),
            _fact("revenue", 14328, "2026-H1"),
            _fact("operating_profit", 2951, "2026-Q2"),
        ],
    )
    periods = _reporting_periods(snapshot)
    assert periods.latest_annual == "FY2025"
    assert periods.latest_interim == "H1 2026"
    assert periods.latest_quarter == "Q2 2026"
    assert periods.latest_current_period == "Q2 2026"


def test_a_company_with_no_current_period_reporting_says_so() -> None:
    """The lesson from the merged-identity-gap defect: a per-company field is
    STATED, never inferred across companies."""
    snapshot = _build_financial_snapshot(
        None, None, primary_facts=[_fact("revenue", 32549, "2025")]
    )
    periods = _reporting_periods(snapshot)
    assert periods.latest_annual == "FY2025"
    assert periods.latest_current_period is None
    assert periods.latest_interim is None


def test_a_report_without_the_block_yields_all_unknown_never_a_borrowed_value() -> None:
    for snapshot in ({}, {"reporting_periods": "not a dict"}, {"type": "x"}):
        periods = _reporting_periods(snapshot)
        assert periods.latest_annual is None
        assert periods.latest_current_period is None
