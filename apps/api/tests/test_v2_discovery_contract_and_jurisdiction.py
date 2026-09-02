"""The discovery council's economic answer must survive the API, and SEC is one venue.

TWO DEFECTS, BOTH FOUND ONLY BY RUNNING THE PRODUCT LIVE.

BLOCKER A — the economic fields were STRIPPED.
``_aggregate_chair`` has written ``upside_drivers``, ``downside_drivers``,
``resilience``, ``key_financial_signal`` and ``strongest_dimension`` into every
bucket entry since the comparison fields were added, and ``to_storage_dict``
persists them. ``DiscoveryCouncilCandidateEntry`` did not DECLARE them, so
Pydantic dropped them on the way out and every reader downstream saw nothing.
The live European Luxury run therefore rendered "Not established" on every
economic dimension — not because the council had said nothing, but because the
response model had no field to put it in.

JURISDICTION — SEC is not a universal requirement.
The evidence pack handed the council a bare ``sec_eligible: false`` per
candidate and a run-level known gap reading "N candidate(s) are not
SEC-eligible". The council read that as written: a live European Luxury run
concluded that Richemont, Pandora, Kering, Moncler and Swatch all lacked
regulated filings, counted it against every one of them, and fell back to price
momentum. SEC EDGAR does not cover any of those issuers.

All offline — no network, no credentials, no LLM calls.
"""

from __future__ import annotations

import uuid

from app.schemas.market_discovery import (
    DiscoveryCouncilCandidateEntry,
    DiscoveryCouncilReviewResponse,
)
from app.services.llm.discovery_council import _aggregate_chair
from app.services.llm.discovery_evidence_pack import (
    build_discovery_evidence_pack,
    regulated_venue_state,
)
from app.services.llm.discovery_prompts import system_prompt_for
from app.services.llm.discovery_schemas import (
    AGENT_CANDIDATE_PRIORITIZATION,
    CandidateEvidence,
    CandidateNote,
    DiscoveryCouncilAgentOutput,
    DiscoveryCouncilResult,
    DiscoveryEvidencePack,
)
from app.services.sources.jurisdiction_source_classes import (
    applicable_regulated_venue,
)


def _pack_with(cid: str, ticker: str, exchange: str) -> DiscoveryEvidencePack:
    return DiscoveryEvidencePack(
        candidates=[
            CandidateEvidence(
                id=cid,
                candidate_id=str(uuid.uuid4()),
                ticker=ticker,
                exchange=exchange,
                company_name=ticker,
            )
        ]
    )


def _chair_note(**kwargs) -> DiscoveryCouncilAgentOutput:
    return DiscoveryCouncilAgentOutput(
        agent_name="discovery_chair",
        candidate_notes=[CandidateNote(**kwargs)],
    )


# ---------------------------------------------------------------------------
# 16-17. The economic fields survive the FULL round trip
# ---------------------------------------------------------------------------

ECONOMIC_FIELDS = (
    "upside_drivers",
    "downside_drivers",
    "resilience",
    "key_financial_signal",
    "strongest_dimension",
)


def test_economic_fields_survive_llm_to_api_round_trip() -> None:
    """LLM output → chair aggregation → storage → API response, field by field."""
    pack = _pack_with("C1", "CFR", "SW")
    chair = _chair_note(
        candidate_ref="C1",
        ticker="CFR",
        exchange="SW",
        internal_action="research_next",
        rationale="High-margin jewellery plus net cash.",
        upside_drivers=["Jewellery margin expansion", "Net cash funds buybacks"],
        downside_drivers=["Watch division close to break-even"],
        resilience="Net cash position absorbs a demand slowdown.",
        key_financial_signal="Jewellery operating margin above 30%.",
        strongest_dimension="profitability",
    )

    # 1. Chair aggregation → the persisted bucket entry.
    buckets = _aggregate_chair(pack, chair)
    entry = buckets["candidates_to_research_next"][0]
    for field in ECONOMIC_FIELDS:
        assert field in entry, f"{field} lost in chair aggregation"

    # 2. The persisted storage dict.
    result = DiscoveryCouncilResult(llm_used=True, **buckets)
    stored = result.to_storage_dict()
    stored_entry = stored["candidates_to_research_next"][0]
    for field in ECONOMIC_FIELDS:
        assert field in stored_entry, f"{field} lost in to_storage_dict"

    # 3. THE STEP THAT WAS BROKEN — the API response model.
    response = DiscoveryCouncilReviewResponse.from_storage(uuid.uuid4(), stored)
    api_entry = response.candidates_to_research_next[0]
    assert api_entry.upside_drivers == [
        "Jewellery margin expansion",
        "Net cash funds buybacks",
    ]
    assert api_entry.downside_drivers == ["Watch division close to break-even"]
    assert api_entry.resilience == "Net cash position absorbs a demand slowdown."
    assert api_entry.key_financial_signal == "Jewellery operating margin above 30%."
    assert api_entry.strongest_dimension == "profitability"

    # 4. And out through the serializer the client actually receives.
    payload = response.model_dump(mode="json")
    wire_entry = payload["candidates_to_research_next"][0]
    for field in ECONOMIC_FIELDS:
        assert field in wire_entry, f"{field} stripped on serialization"


def test_every_bucket_carries_the_economic_fields() -> None:
    """Not just research_next — a monitored or rejected name is compared too."""
    pack = _pack_with("C1", "PNDORA", "CO")
    for action, field in (
        ("monitor_for_evidence", "candidates_to_monitor"),
        ("insufficient_data", "candidates_insufficient_data"),
        ("reject_for_now", "candidates_to_reject"),
    ):
        chair = _chair_note(
            candidate_ref="C1",
            ticker="PNDORA",
            internal_action=action,
            upside_drivers=["Owned retail mix"],
            resilience="Cash generation covers the dividend.",
            strongest_dimension="cash_generation",
        )
        stored = DiscoveryCouncilResult(
            llm_used=True, **_aggregate_chair(pack, chair)
        ).to_storage_dict()
        entry = DiscoveryCouncilReviewResponse.from_storage(
            uuid.uuid4(), stored
        ).model_dump(mode="json")[field][0]
        assert entry["upside_drivers"] == ["Owned retail mix"]
        assert entry["resilience"] == "Cash generation covers the dividend."
        assert entry["strongest_dimension"] == "cash_generation"


def test_a_review_written_before_these_fields_existed_still_reads() -> None:
    """Absence must read as 'not assessed', never as an error."""
    legacy = {
        "candidates_to_research_next": [
            {"candidate_ref": "C1", "ticker": "MONC", "rationale": "Legacy entry."}
        ]
    }
    response = DiscoveryCouncilReviewResponse.from_storage(uuid.uuid4(), legacy)
    entry = response.candidates_to_research_next[0]
    assert entry.upside_drivers == []
    assert entry.downside_drivers == []
    assert entry.resilience is None
    assert entry.strongest_dimension is None


def test_the_entry_model_declares_every_field_the_council_writes() -> None:
    """A contract test: what CandidateNote writes, the API must be able to hold.

    This is the invariant the defect broke. Comparing the two models directly
    means a new comparison field added to the council cannot silently fail to
    reach a reader again.
    """
    written = set(CandidateNote.model_fields) - {"internal_action", "citation_ids"}
    exposed = set(DiscoveryCouncilCandidateEntry.model_fields)
    missing = written - exposed
    assert not missing, f"the API response model drops {sorted(missing)}"


# ---------------------------------------------------------------------------
# 26-31. Jurisdiction
# ---------------------------------------------------------------------------


def test_us_issuer_has_sec_as_its_applicable_venue() -> None:
    venue = applicable_regulated_venue(exchange="NASDAQ", country="United States")
    assert venue.sec_applicable is True
    assert venue.venue_label == "SEC EDGAR"


def test_non_us_issuers_are_not_measured_against_sec() -> None:
    """Swiss, Danish, Italian and UK issuers each get their OWN venue."""
    cases = {
        "SW": "SIX Swiss Exchange",
        "CO": "Nasdaq Nordic",
        "MI": "eMarket Storage (CONSOB)",
        "LSE": "FCA National Storage Mechanism",
    }
    for exchange, expected in cases.items():
        venue = applicable_regulated_venue(exchange=exchange)
        assert venue.sec_applicable is False, exchange
        assert venue.venue_name == expected, exchange
        assert venue.venue_unresolved is False, exchange


def test_a_missing_applicable_venue_IS_a_gap() -> None:
    """The gap is real when the venue that APPLIES returned nothing."""
    swiss = applicable_regulated_venue(exchange="SW")
    gap = swiss.gap_statement(disclosures_retrieved=0)
    assert gap is not None
    assert "SIX Swiss Exchange" in gap
    assert "SEC" not in gap
    # ...and it is NOT a gap when that venue did return something.
    assert swiss.gap_statement(disclosures_retrieved=3) is None


def test_lack_of_sec_eligibility_is_not_stated_as_a_candidate_gap() -> None:
    """The run-level known gaps must not count a non-US listing against anyone."""
    european = [
        {"ticker": "CFR", "exchange": "SW", "candidate_id": "1", "filing_event_count": 2},
        {"ticker": "PNDORA", "exchange": "CO", "candidate_id": "2", "filing_event_count": 5},
        {"ticker": "MONC", "exchange": "MI", "candidate_id": "3", "filing_event_count": 1},
    ]
    pack = build_discovery_evidence_pack(run={}, candidates=european)
    gaps = " ".join(pack.known_gaps)

    assert "not SEC-eligible" not in gaps
    # The pack states the OPPOSITE, explicitly, so the council cannot infer it.
    assert "NOT covered by SEC EDGAR" in gaps
    assert "MUST NOT be counted against them" in gaps
    # Each candidate carries the venue that actually serves it.
    for candidate in pack.candidates:
        assert candidate.data_coverage["sec_is_applicable_venue"] is False
        assert candidate.data_coverage["regulated_disclosure_state"] == "retrieved"


def test_a_european_issuer_with_no_retrieved_filing_is_a_gap_named_by_venue() -> None:
    pack = build_discovery_evidence_pack(
        run={},
        candidates=[
            {"ticker": "UHR", "exchange": "SW", "candidate_id": "1", "filing_event_count": 0}
        ],
    )
    gaps = " ".join(pack.known_gaps)
    assert "no regulated filing retrieved" in gaps
    assert "SIX Swiss Exchange" in gaps
    assert "no SEC" not in gaps.lower()


def test_us_behaviour_is_not_broken_by_the_european_fix() -> None:
    """An SEC-eligible issuer keeps SEC as its applicable venue and its gap."""
    state = regulated_venue_state(
        {"ticker": "MRNA", "exchange": "NASDAQ", "filing_event_count": 0}
    )
    assert state["sec_is_applicable_venue"] is True
    assert state["applicable_regulated_venue"] == "SEC EDGAR"
    assert "sec_not_applicable_note" not in state

    pack = build_discovery_evidence_pack(
        run={},
        candidates=[
            {"ticker": "MRNA", "exchange": "NASDAQ", "candidate_id": "1", "filing_event_count": 0}
        ],
    )
    gaps = " ".join(pack.known_gaps)
    assert "SEC EDGAR" in gaps
    assert "NOT covered by SEC EDGAR" not in gaps


def test_the_prompt_tells_the_council_what_a_gap_is() -> None:
    prompt = system_prompt_for(AGENT_CANDIDATE_PRIORITIZATION)
    assert "sec_is_applicable_venue" in prompt
    assert "is NOT a research gap" in prompt
    assert "no supported regulated filing was retrieved from the applicable venue" in prompt


def _executable_source(module) -> str:
    """A module's code with comments and docstrings removed.

    The DOCUMENTATION is allowed to name the issuers whose live run exposed the
    defect — that is what makes the comment worth reading. What must not name
    them is the code that decides anything.
    """
    import ast
    import inspect
    import io
    import tokenize

    raw = inspect.getsource(module)
    stripped = tokenize.untokenize(
        tok
        for tok in tokenize.generate_tokens(io.StringIO(raw).readline)
        if tok.type != tokenize.COMMENT
    )
    tree = ast.parse(stripped)
    for node in ast.walk(tree):
        if not isinstance(
            node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        ):
            continue
        body = getattr(node, "body", [])
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            node.body = body[1:] or [ast.Pass()]
    return ast.unparse(tree).lower()


def test_no_issuer_name_appears_in_the_jurisdiction_logic() -> None:
    """Generic by construction — the fix must not be a list of company names.

    The scan is of EXECUTABLE code only. A comment naming the issuers whose
    live council run exposed the defect is the record of why this module
    exists; a branch naming one of them would be the defect coming back in a
    different shape.
    """
    from app.services.llm import discovery_evidence_pack, discovery_prompts
    from app.services.sources import jurisdiction_source_classes

    modules = (
        jurisdiction_source_classes,
        discovery_evidence_pack,
        discovery_prompts,
    )
    for module in modules:
        source = _executable_source(module)
        for name in ("richemont", "pandora", "kering", "moncler", "swatch", "hermes"):
            assert name not in source, f"{name} hardcoded in {module.__name__}"


# ---------------------------------------------------------------------------
# 22-23. Current research is used; a screening draft is not
# ---------------------------------------------------------------------------


def test_candidate_research_signals_reach_the_pack() -> None:
    pack = build_discovery_evidence_pack(
        run={},
        candidates=[
            {
                "ticker": "PNDORA",
                "exchange": "CO",
                "candidate_id": "1",
                "research_signals": {
                    "current_research_report_id": "abc",
                    "annual_figures": ["revenue 32,516 m DKK [FY2025]"],
                    "fundamental_setup": "constructive",
                },
            },
            {"ticker": "UHR", "exchange": "SW", "candidate_id": "2"},
        ],
    )
    with_research, without = pack.candidates
    assert with_research.research_signals["fundamental_setup"] == "constructive"
    # A candidate with no current research carries an EMPTY block, which reads
    # as "not established" — never a substituted gap count.
    assert without.research_signals == {}

    gaps = " ".join(pack.known_gaps)
    assert "1 of 2 candidate(s) have a CURRENT structured research report" in gaps
    assert "NOT ESTABLISHED" in gaps
