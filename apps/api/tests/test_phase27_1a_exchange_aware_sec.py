"""
Phase 27.1A — safety scanner accuracy + exchange-aware SEC lookup.

Two Phase 27 defects are covered here:

1. Safety gates matched forbidden terms as bare substrings, so legitimate
   company names failed ("ENEOS Holdings" -> HOLD, "Swatch Group" -> WATCH).
   The shared three-tier scanner must let real names through while still
   catching every genuine rating label, rating context, and valuation phrase.

2. SEC CIK resolution ignored the exchange and matched on ticker alone against
   a US-registrant index, so BA.LSE (BAE Systems) resolved to Boeing. The gate
   must fire BEFORE any network call, and non-US issuers must degrade to an
   honest not_sourced result rather than raising or borrowing US data.

See docs/PHASE_27_1_SPEC.md §3.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from app.agents.analysis_council.investment_committee_chair import _check_forbidden_output
from app.integrations import sec_issuer_registry
from app.integrations.providers.free_real_provider import (
    REASON_NON_US_NO_SEC_MAPPING,
    EodhdFreeRealProvider,
    FreeRealProvider,
)
from app.integrations.providers.sec_edgar_fundamentals import (
    SecEdgarFundamentalsProvider,
    SecExchangeNotSupportedError,
)
from app.integrations.sec_issuer_registry import (
    MAPPING_FOREIGN_PRIVATE_ISSUER,
    SecIssuerMapping,
)
from app.services import safety_terms
from app.services.exchange_registry import (
    EXCHANGES,
    country_for_exchange,
    get_exchange,
    is_sec_eligible,
    normalize_exchange,
    region_for_exchange,
)
from app.services.final_report_generator import run_safety_gate
from app.services.market_discovery_service import scan_forbidden_terms
from app.services.research_judge_service import _scan_for_forbidden_terms
from app.services.scoring_engine import _check_forbidden_terms

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

# A minimal stand-in for SEC's company_tickers.json. Every ticker here is a real
# US registrant that collides with a non-US issuer of the same ticker.
_SEC_INDEX: dict[str, dict[str, Any]] = {
    "0": {"cik_str": 12927, "ticker": "BA", "title": "BOEING CO"},
    "1": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc."},
    "2": {"cik_str": 789019, "ticker": "MSFT", "title": "MICROSOFT CORP"},
    "3": {"cik_str": 1045810, "ticker": "NVDA", "title": "NVIDIA CORP"},
    "4": {"cik_str": 6951, "ticker": "AMAT", "title": "APPLIED MATERIALS INC"},
    "5": {"cik_str": 1059556, "ticker": "MC", "title": "MOELIS & CO"},
    "6": {"cik_str": 1001250, "ticker": "EL", "title": "ESTEE LAUDER COMPANIES INC"},
    "7": {"cik_str": 39263, "ticker": "CFR", "title": "CULLEN/FROST BANKERS INC"},
}

BOEING_CIK = "12927"


class _NetworkCalled(AssertionError):
    """Raised when a test touches the network, proving the gate came too late."""


def _forbid_network(*args: Any, **kwargs: Any) -> Any:
    raise _NetworkCalled(
        "httpx.AsyncClient was constructed — the SEC eligibility gate must "
        "reject an ineligible exchange BEFORE any network call."
    )


@pytest.fixture
def sec_provider() -> SecEdgarFundamentalsProvider:
    """A provider preloaded with the US index (cached against the US venue)."""
    provider = SecEdgarFundamentalsProvider()
    provider._load_cik_index_sync(_SEC_INDEX)
    return provider


@pytest.fixture(autouse=True)
def _clean_issuer_registry():
    """The registry ships empty; no test may leak a mapping into another."""
    sec_issuer_registry.clear_mappings()
    yield
    sec_issuer_registry.clear_mappings()


# ---------------------------------------------------------------------------
# 1. Safety scanner — strings that MUST PASS
# ---------------------------------------------------------------------------

MUST_PASS = [
    # Real company names that the old substring gate rejected
    "Swatch Group AG",
    "The Swatch Group AG (UHR.SW)",
    "Watches & Jewelry",
    "ENEOS Holdings",
    "Compagnie Financiere Richemont SA",
    # Ordinary English containing a rating word as a substring or lower case
    "buyback",
    "Buyback programme announced",
    "shareholder holdings",
    "Holdings increased year over year",
    "watchmaker",
    "watch industry",
    "household products",
    "insiders hold 12% of shares",
    "reject rate improved",
    # Upper-cased company strings still must not trip Tier 1
    "SWATCH GROUP AG",
    "WATCHES & JEWELRY",
    "ENEOS HOLDINGS",
    # Compliant disclaimer copy
    "this is not a recommendation",
    "human review required",
    # Phase 27.1A degradation vocabulary must itself be clean
    "not_sourced",
    "requires_human_research",
    "non_us_exchange_no_sec_mapping",
]


@pytest.mark.parametrize("text", MUST_PASS)
def test_scanner_allows_legitimate_text(text: str) -> None:
    assert safety_terms.scan_text(text) == [], (
        f"{text!r} is legitimate research text and must not be flagged"
    )


def test_rating_intent_does_not_fire_across_a_sentence_boundary() -> None:
    """
    The Tier 2 window must not span sentences.

    "rating" and "hold" sit only 25 characters apart here but belong to
    unrelated sentences, so a plain 40-character window would false-positive.
    """
    text = "The firm uses rating agencies. The board will hold its AGM in May."
    assert safety_terms.scan_text(text) == []


# ---------------------------------------------------------------------------
# 2. Safety scanner — strings that MUST FAIL
# ---------------------------------------------------------------------------

MUST_FAIL = [
    # Tier 1: ALL-CAPS rating labels
    "Rating: BUY",
    "We recommend BUY",
    "Recommendation: HOLD",
    "STRONG BUY",
    "SELL now",
    "This is a BUY opportunity",
    "internal_status: SHORTLIST_HIGH",
    # Tier 2: rating context, any case, either word order
    "Rating: Buy",
    "we recommend a buy",
    "Rated hold",
    "assigned a watch rating",
    "Analyst rating: Outperform",
    # Tier 3: valuation phrases
    "price target",
    "our price target is 120 NOK",
    "Fair value estimate",
    "fair value estimate of 50",
    "Upside to target",
    "intrinsic value of",
    "upside of 30% expected",
    "guaranteed return on investment",
    "The stock will go up significantly",
    "This is a buy signal",
    "The stock appears undervalued",
    "the shares look overvalued",
]


@pytest.mark.parametrize("text", MUST_FAIL)
def test_scanner_flags_forbidden_text(text: str) -> None:
    assert safety_terms.scan_text(text), (
        f"{text!r} is prohibited investment-action language and must be flagged"
    )


# ---------------------------------------------------------------------------
# 3. All gates share one scanner — parametrized across every migrated gate
# ---------------------------------------------------------------------------


def _gate_final_report(text: str) -> bool:
    """True when the gate flags the text."""
    return not run_safety_gate({"section": {"note": text}}).passed


def _gate_scoring_engine(text: str) -> bool:
    return bool(_check_forbidden_terms(text))


def _gate_committee_chair(text: str) -> bool:
    return bool(_check_forbidden_output(text))


def _gate_research_judge(text: str) -> bool:
    return bool(_scan_for_forbidden_terms(text))


def _gate_market_discovery(text: str) -> bool:
    return bool(scan_forbidden_terms(text))


ALL_GATES = [
    pytest.param(_gate_final_report, id="final_report_generator"),
    pytest.param(_gate_scoring_engine, id="scoring_engine"),
    pytest.param(_gate_committee_chair, id="investment_committee_chair"),
    pytest.param(_gate_research_judge, id="research_judge_service"),
    pytest.param(_gate_market_discovery, id="market_discovery_service"),
]


@pytest.mark.parametrize("gate", ALL_GATES)
@pytest.mark.parametrize("text", MUST_PASS)
def test_every_gate_allows_legitimate_text(gate, text: str) -> None:
    assert gate(text) is False, f"gate wrongly flagged legitimate text {text!r}"


@pytest.mark.parametrize("gate", ALL_GATES)
@pytest.mark.parametrize("text", MUST_FAIL)
def test_every_gate_flags_forbidden_text(gate, text: str) -> None:
    assert gate(text) is True, f"gate failed to flag prohibited text {text!r}"


def test_no_gate_defines_its_own_term_list() -> None:
    """
    Exactly one forbidden-vocabulary definition may exist.

    llm_provider.py is deliberately excluded: it guards raw LLM text on the way
    in and out and is intentionally stricter (spec §3.2).
    """
    import pathlib

    app_dir = pathlib.Path(__file__).parent.parent / "app"
    offenders: list[str] = []
    for path in app_dir.rglob("*.py"):
        if path.name in {"safety_terms.py", "llm_provider.py"}:
            continue
        text = path.read_text()
        for marker in ("_FORBIDDEN_TERMS = ", "_FORBIDDEN_OUTPUTS = ", "_FORBIDDEN_PATTERNS = "):
            if marker in text:
                offenders.append(f"{path.name}: {marker.strip()}")
    assert offenders == [], f"forbidden-term lists must not be redefined: {offenders}"


# ---------------------------------------------------------------------------
# 4. Exchange registry
# ---------------------------------------------------------------------------


def test_normalize_exchange_collapses_us_venues() -> None:
    assert normalize_exchange("NASDAQ") == "US"
    assert normalize_exchange("NYSE") == "US"
    assert normalize_exchange("AMEX") == "US"
    assert normalize_exchange("US") == "US"
    assert normalize_exchange("LSE") == "LSE"
    assert normalize_exchange("  lse ") == "LSE"


def test_sec_eligibility_rules() -> None:
    assert is_sec_eligible("US") is True
    assert is_sec_eligible("NYSE") is True
    assert is_sec_eligible("NASDAQ") is True
    assert is_sec_eligible("LSE") is False
    assert is_sec_eligible("PA") is False
    assert is_sec_eligible("SW") is False
    assert is_sec_eligible("XETRA") is False


def test_absent_exchange_is_sec_eligible_for_legacy_flows() -> None:
    """
    None must stay eligible or every legacy ticker-only run regresses.

    Phase 25 ticker runs pass exchange explicitly, but older callers do not,
    and treating absent as ineligible would degrade AAPL/MSFT/NVDA to
    "not sourced" (spec R5).
    """
    assert is_sec_eligible(None) is True
    assert is_sec_eligible("") is True
    assert is_sec_eligible("   ") is True


def test_otc_is_not_sec_eligible() -> None:
    """ADRs trade OTC and ticker collisions are common — require a mapping."""
    assert is_sec_eligible("OTC") is False
    otc = get_exchange("OTC")
    assert otc is not None and otc.is_us is True


def test_unknown_exchange_is_not_sec_eligible() -> None:
    """Refuse to guess: a wrong CIK is worse than no data."""
    assert is_sec_eligible("NOT_A_REAL_VENUE") is False


def test_registry_entries_are_complete() -> None:
    for code, info in EXCHANGES.items():
        assert info.code == code
        assert info.country, f"{code} missing country"
        assert info.region, f"{code} missing region"
        assert info.currency, f"{code} missing currency"


def test_region_and_country_lookups() -> None:
    assert region_for_exchange("SW") == "Europe"
    assert region_for_exchange("LSE") == "Europe"
    assert region_for_exchange("US") == "North America"
    assert region_for_exchange("TSE") == "Japan"
    assert country_for_exchange("SW") == "Switzerland"
    assert country_for_exchange("PA") == "France"
    assert country_for_exchange(None) is None


# ---------------------------------------------------------------------------
# 5. Ticker/exchange collisions — the headline defect
# ---------------------------------------------------------------------------

COLLISIONS = [
    pytest.param("BA", "LSE", "BAE Systems plc", id="BA.LSE-not-Boeing"),
    pytest.param("MC", "PA", "LVMH", id="MC.PA-not-Moelis"),
    pytest.param("EL", "PA", "EssilorLuxottica", id="EL.PA-not-Estee-Lauder"),
    pytest.param("CFR", "SW", "Richemont", id="CFR.SW-not-Cullen-Frost"),
]


@pytest.mark.parametrize("ticker,exchange,issuer", COLLISIONS)
async def test_non_us_ticker_never_resolves_to_us_issuer(
    sec_provider: SecEdgarFundamentalsProvider,
    ticker: str,
    exchange: str,
    issuer: str,
) -> None:
    """The whole point of Phase 27.1A: no silent wrong-company resolution."""
    with patch("httpx.AsyncClient", _forbid_network):
        with pytest.raises(SecExchangeNotSupportedError) as exc_info:
            await sec_provider.resolve_cik(ticker, exchange)
    assert exc_info.value.ticker == ticker
    assert exc_info.value.exchange == exchange


@pytest.mark.parametrize("ticker,exchange,_issuer", COLLISIONS)
async def test_collision_gate_precedes_any_network_call(
    sec_provider: SecEdgarFundamentalsProvider,
    ticker: str,
    exchange: str,
    _issuer: str,
) -> None:
    """
    The gate must reject before fetching company_tickers.json.

    _forbid_network raises AssertionError if the client is constructed, so a
    late gate fails this test rather than silently making a network request.
    """
    with patch("httpx.AsyncClient", _forbid_network):
        with pytest.raises(SecExchangeNotSupportedError):
            await sec_provider.resolve_cik(ticker, exchange)


def test_sec_exchange_error_is_a_valueerror() -> None:
    """
    EodhdFreeRealProvider catches ValueError to fall back to an EODHD stub;
    that path must keep working (spec §3.5).
    """
    assert issubclass(SecExchangeNotSupportedError, ValueError)


@pytest.mark.parametrize("exchange", ["US", "NYSE", "NASDAQ", None])
async def test_us_tickers_still_resolve(
    sec_provider: SecEdgarFundamentalsProvider, exchange: str | None
) -> None:
    """Legacy US behavior is preserved, including the exchange-less call."""
    with patch("httpx.AsyncClient", _forbid_network):
        assert await sec_provider.resolve_cik("BA", exchange) == BOEING_CIK


@pytest.mark.parametrize("ticker", ["AAPL", "MSFT", "NVDA", "AMAT"])
async def test_legacy_ticker_discovery_unchanged(
    sec_provider: SecEdgarFundamentalsProvider, ticker: str
) -> None:
    """AAPL/MSFT/NVDA and the US semiconductor thesis must be unaffected."""
    with patch("httpx.AsyncClient", _forbid_network):
        assert await sec_provider.resolve_cik(ticker, "US")
        assert await sec_provider.resolve_cik(ticker, None)


async def test_cik_cache_is_keyed_by_ticker_and_exchange(
    sec_provider: SecEdgarFundamentalsProvider,
) -> None:
    """A US hit must not satisfy the same ticker on a foreign venue (spec R6)."""
    with patch("httpx.AsyncClient", _forbid_network):
        assert await sec_provider.resolve_cik("BA", "US") == BOEING_CIK
        with pytest.raises(SecExchangeNotSupportedError):
            await sec_provider.resolve_cik("BA", "LSE")


# ---------------------------------------------------------------------------
# 6. Explicit SEC issuer registry
# ---------------------------------------------------------------------------


def test_issuer_registry_ships_empty() -> None:
    """
    Empty is the correct default.

    A speculative CIK reproduces the Boeing bug with extra steps, so entries
    are added only after a manual sec.gov check (spec §3.4).
    """
    assert sec_issuer_registry.SEC_ISSUER_MAPPINGS == {}


def test_registry_requires_provenance() -> None:
    with pytest.raises(ValueError, match="source_url or verified_on"):
        sec_issuer_registry.register_mapping(
            SecIssuerMapping(
                ticker="UHR",
                exchange="SW",
                cik="0000000000",
                issuer_name="Unverified",
                mapping_type=MAPPING_FOREIGN_PRIVATE_ISSUER,
                source_url="",
                verified_on="",
            )
        )


async def test_explicit_mapping_enables_a_non_us_ticker() -> None:
    """A verified mapping is the sanctioned way to enable a foreign issuer."""
    sec_issuer_registry.register_mapping(
        SecIssuerMapping(
            ticker="BA",
            exchange="LSE",
            cik="0000123456",
            issuer_name="BAE Systems plc",
            mapping_type=MAPPING_FOREIGN_PRIVATE_ISSUER,
            source_url=(
                "https://www.sec.gov/cgi-bin/browse-edgar"
                "?action=getcompany&CIK=0000123456"
            ),
            verified_on="2026-07-21",
        )
    )
    provider = SecEdgarFundamentalsProvider()
    with patch("httpx.AsyncClient", _forbid_network):
        assert await provider.resolve_cik("BA", "LSE") == "0000123456"


async def test_mapping_does_not_leak_across_exchanges() -> None:
    """A mapping registered for LSE must not answer for a different venue."""
    sec_issuer_registry.register_mapping(
        SecIssuerMapping(
            ticker="BA",
            exchange="LSE",
            cik="0000123456",
            issuer_name="BAE Systems plc",
            mapping_type=MAPPING_FOREIGN_PRIVATE_ISSUER,
            source_url="https://www.sec.gov/cgi-bin/browse-edgar?CIK=0000123456",
            verified_on="2026-07-21",
        )
    )
    provider = SecEdgarFundamentalsProvider()
    with patch("httpx.AsyncClient", _forbid_network):
        with pytest.raises(SecExchangeNotSupportedError):
            await provider.resolve_cik("BA", "PA")


# ---------------------------------------------------------------------------
# 7. Honest degradation — never raise, never fabricate
# ---------------------------------------------------------------------------


async def test_profile_degrades_instead_of_raising() -> None:
    """
    workflows/company_analysis calls get_company_profile unguarded, so a raise
    would abort the node and fail an entire European thesis run (spec §3.6).
    """
    provider = FreeRealProvider()
    with patch("httpx.AsyncClient", _forbid_network):
        profile = await provider.get_company_profile("UHR", "SW")

    assert profile.legal_name == "UHR", "legal_name must be the ticker, never invented"
    assert profile.country_domicile == "Switzerland", "registry country is factual"
    assert profile.sector is None
    assert profile.industry is None
    assert profile.data_quality == "D_weak_or_stale"
    assert "not_sourced" in (profile.meta.note or "")
    assert "requires_human_research" in (profile.meta.note or "")


async def test_fundamentals_degrade_to_zero_datapoints() -> None:
    """No datapoints beats another company's datapoints (CLAUDE.md rule 6)."""
    provider = FreeRealProvider()
    with patch("httpx.AsyncClient", _forbid_network):
        fundamentals = await provider.get_fundamentals("UHR", "SW")
    assert fundamentals.datapoints == []


async def test_eodhd_free_real_also_degrades() -> None:
    """The second SEC caller must receive identical treatment (spec §0)."""
    provider = EodhdFreeRealProvider()
    with patch("httpx.AsyncClient", _forbid_network):
        profile = await provider.get_company_profile("UHR", "SW")
        fundamentals = await provider.get_fundamentals("UHR", "SW")
    assert profile.legal_name is not None
    assert fundamentals.datapoints == []


async def test_degraded_profile_carries_no_boeing_data() -> None:
    """Explicit regression for the reported bug."""
    provider = FreeRealProvider()
    with patch("httpx.AsyncClient", _forbid_network):
        profile = await provider.get_company_profile("BA", "LSE")
    assert "BOEING" not in (profile.legal_name or "").upper()
    assert profile.legal_name == "BA"
    assert profile.country_domicile == "United Kingdom"


async def test_degraded_copy_passes_the_safety_gate() -> None:
    """
    Degradation wording must not itself trip the gate.

    Per the Phase 26 GOTCHA the gate scans values, so this copy avoids
    "placeholder" / "sell-side" style landmines.
    """
    provider = FreeRealProvider()
    with patch("httpx.AsyncClient", _forbid_network):
        profile = await provider.get_company_profile("UHR", "SW")
    assert safety_terms.scan_text(profile.meta.note or "") == []
    assert run_safety_gate({"identity": {"note": profile.meta.note}}).passed is True


# ---------------------------------------------------------------------------
# 8. data_coverage contract
# ---------------------------------------------------------------------------


def test_data_coverage_reports_unsupported_venue() -> None:
    from app.workflows.company_analysis import _build_data_coverage

    class _Meta:
        provider_name = "free_real_not_sourced"

    class _Profile:
        ticker = "UHR"
        meta = _Meta()

    coverage = _build_data_coverage(
        exchange="SW", profile=_Profile(), fundamentals=None, price_source="stooq"
    )
    assert coverage["sec_eligible"] is False
    assert coverage["reason"] == REASON_NON_US_NO_SEC_MAPPING
    assert coverage["requires_human_research"] is True
    assert coverage["fundamentals_source"] == "not_sourced"
    assert coverage["price_source"] == "stooq", (
        "non-US names may still carry exchange-aware prices where the provider supports it"
    )


def test_data_coverage_reports_covered_us_issuer() -> None:
    from app.workflows.company_analysis import _build_data_coverage

    class _Meta:
        provider_name = "sec_edgar_fundamentals"

    class _Profile:
        ticker = "AAPL"
        meta = _Meta()

    class _Fundamentals:
        datapoints = [object()]

    coverage = _build_data_coverage(
        exchange="US",
        profile=_Profile(),
        fundamentals=_Fundamentals(),
        price_source="stooq",
    )
    assert coverage["sec_eligible"] is True
    assert coverage["requires_human_research"] is False
    assert coverage["reason"] == "sec_covered"


def test_data_coverage_vocabulary_is_safety_clean() -> None:
    """Every reason string is persisted and later scanned — it must be clean."""
    from app.workflows.company_analysis import _build_data_coverage

    class _Meta:
        provider_name = "free_real_not_sourced"

    class _Profile:
        ticker = "UHR"
        meta = _Meta()

    coverage = _build_data_coverage(
        exchange="SW", profile=_Profile(), fundamentals=None, price_source=None
    )
    assert safety_terms.scan_value(coverage) == []
