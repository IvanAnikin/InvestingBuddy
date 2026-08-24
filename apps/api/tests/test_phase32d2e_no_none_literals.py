"""
Phase 32D2e — the Python literal ``None`` must never reach a human-facing string.

WHY THIS FILE EXISTS
====================
``dict.get(key, default)`` returns the default only when the key is ABSENT. A
key that is PRESENT with the value ``None`` returns ``None``, and an f-string
renders that as the four characters ``None``.

The company snapshot deliberately stores honest absences as explicit ``None``
values (Phase 32A Slice 6B stopped fabricating placeholders), so every
``profile.get("sector", "unknown sector")`` in the deterministic agents was a
latent leak. Live acceptance on staging found three:

    "Currency risk: reporting currency is 'None'."
    "Risk assessment for PNDORA (PNDORA), None, Denmark."
    "Latest close 783.0 None on 2026-08-21 from eodhd_price_only"

The third is the worst: the price series' OWN summary correctly says DKK, and
the exchange registry resolves the quote currency, but the persisted citation
quote interpolated the RAW provider value — which is honestly ``None`` — so a
sourced currency was rendered as absent, in a citation.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import Any

from app.agents.analysis_council.bear_case_agent import (
    bear_case_output_to_dict,
    run_bear_case_agent,
)
from app.agents.analysis_council.bull_case_agent import (
    bull_case_output_to_dict,
    run_bull_case_agent,
)
from app.agents.analysis_council.investment_committee_chair import (
    committee_chair_output_to_dict,
    run_investment_committee_chair,
)
from app.agents.analysis_council.risk_agent import (
    risk_agent_output_to_dict,
    run_risk_agent,
)
from app.agents.research_team.financial_data_agent import (
    financial_data_agent_output_to_dict,
    run_financial_data_agent,
)
from app.integrations.financial_data_provider import (
    DataQuality,
    PriceHistoryData,
    PricePoint,
    ProviderResponseMetadata,
    SourceTier,
)
from app.workflows.snapshot_builder import get_price_citation_fields

#: The exact failure mode: a key PRESENT with value None.
_ABSENT_SNAPSHOT: dict[str, Any] = {
    "is_mock": False,
    "source_tier": "T6_model_estimate",
    "company_identity": {
        "legal_name": None,
        "ticker": None,
        "exchange": None,
        "country_domicile": None,
        "isin": None,
        "lei": None,
    },
    "profile": {
        "sector": None,
        "industry": None,
        "reporting_currency": None,
        "fiscal_year_end": None,
        "description": None,
        "country_domicile": None,
    },
    "price_history_summary": {"available": False},
    "provider_metadata": {
        "provider_name": "free_real_not_sourced",
        "source_tier": "T6_model_estimate",
        "is_mock": False,
    },
    "missing_fields": [],
}

_NONE_LITERAL = re.compile(r"(?<![A-Za-z])None(?![A-Za-z])")


def _assert_no_none_literal(payload: Any, label: str) -> None:
    body = json.dumps(payload, default=str)
    # JSON nulls are fine — they are structured absence. Only the four-character
    # word inside a STRING is the defect.
    for match in re.finditer(r'"((?:[^"\\]|\\.)*)"', body):
        text = match.group(1)
        assert not _NONE_LITERAL.search(text), f"{label}: 'None' rendered in {text!r}"


def test_risk_agent_renders_no_none_literal() -> None:
    out = risk_agent_output_to_dict(
        run_risk_agent(_ABSENT_SNAPSHOT, {}, {}, {})
    )
    _assert_no_none_literal(out, "risk_agent")
    joined = " ".join(out["financial_risks"])
    assert "reporting currency is 'not sourced'" in joined
    assert "sector not sourced" in out["risk_summary"]


def test_bull_and_bear_render_no_none_literal() -> None:
    bull = bull_case_output_to_dict(
        run_bull_case_agent(_ABSENT_SNAPSHOT, {}, {}, {})
    )
    _assert_no_none_literal(bull, "bull_case")
    bear = bear_case_output_to_dict(
        run_bear_case_agent(_ABSENT_SNAPSHOT, {}, {}, {}, bull_case_summary=bull)
    )
    _assert_no_none_literal(bear, "bear_case")


def test_financial_data_and_committee_render_no_none_literal() -> None:
    fds = financial_data_agent_output_to_dict(
        run_financial_data_agent(_ABSENT_SNAPSHOT)
    )
    _assert_no_none_literal(fds, "financial_data_agent")
    chair = committee_chair_output_to_dict(
        run_investment_committee_chair(
            _ABSENT_SNAPSHOT, {}, {}, {}, {}, {}, {}, {"status": "warnings"}, False
        )
    )
    _assert_no_none_literal(chair, "committee_chair")


def _prices(currency: str | None, exchange: str | None) -> PriceHistoryData:
    return PriceHistoryData(
        ticker="TEST",
        exchange=exchange,
        currency=currency,
        price_points=[PricePoint(date="2026-08-21", close=783.0)],
        data_quality=DataQuality.B_single_credible,
        meta=ProviderResponseMetadata(
            provider_name="eodhd_price_only",
            source_tier=SourceTier.T5_api_aggregator,
            retrieved_at=datetime.now(UTC),
        ),
    )


def test_price_citation_resolves_the_real_quote_currency() -> None:
    """The raw provider currency is honestly None; the exchange knows it."""
    quote = get_price_citation_fields(_prices(None, "CO"))[0]["source_quote"]
    assert "None" not in quote
    assert "DKK" in quote


def test_price_citation_says_not_sourced_when_it_genuinely_is_not() -> None:
    quote = get_price_citation_fields(_prices(None, None))[0]["source_quote"]
    assert "None" not in quote
    assert "currency not sourced" in quote


def test_price_citation_prefers_an_explicit_provider_currency() -> None:
    quote = get_price_citation_fields(_prices("USD", "CO"))[0]["source_quote"]
    assert "USD" in quote
