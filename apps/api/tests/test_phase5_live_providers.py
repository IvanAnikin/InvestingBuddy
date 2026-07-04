"""
Offline tests for Phase 5: Live Free Data Provider Integration.

All tests in this file run with:
  - No external network calls
  - No API keys required
  - No Azure credentials
  - Fixture files used for parsing tests
  - httpx mocked for provider method tests

Coverage:
  - StooqProvider: symbol building, CSV parsing, source record, error cases
  - GleifProvider: LEI detection, JSON parsing, source record, error cases
  - SecEdgarProvider: CIK padding, JSON parsing, source record, CIK routing
  - SourceRecordAttrs: build_source_record utility
  - Source tier correctness for all three providers
  - Provider status (all three return ok — no key needed)
  - API diagnostic endpoints (mocked live calls)
  - OpenBB evaluation placeholder (still NotImplementedError, documented)
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.integrations.financial_data_provider import (
    CompanyProfileData,
    DataQuality,
    PriceHistoryData,
    ProviderResponseMetadata,
    ProviderStatus,
    SourceRecordAttrs,
    SourceTier,
    build_source_record,
)
from app.integrations.providers.gleif_provider import (
    GleifProvider,
    _is_lei,
    _parse_gleif_record,
    _record_to_profile,
)
from app.integrations.providers.openbb_provider import OpenBBProvider
from app.integrations.providers.sec_edgar_provider import (
    SecEdgarProvider,
    _pad_cik,
    _parse_edgar_submissions,
    _submissions_to_profile,
)
from app.integrations.providers.stooq_provider import (
    StooqProvider,
    _parse_stooq_csv,
    _stooq_symbol,
)

# ---------------------------------------------------------------------------
# Fixture paths
# ---------------------------------------------------------------------------

_FIXTURES = Path(__file__).parent / "fixtures"


def _read_fixture(name: str) -> str:
    return (_FIXTURES / name).read_text()


def _read_json_fixture(name: str) -> dict:
    return json.loads(_read_fixture(name))


# ---------------------------------------------------------------------------
# StooqProvider — provider metadata
# ---------------------------------------------------------------------------


def test_stooq_provider_name() -> None:
    assert StooqProvider().provider_name == "stooq"


def test_stooq_source_tier_is_t5() -> None:
    assert StooqProvider().source_tier == SourceTier.T5_api_aggregator


def test_stooq_provider_status_is_ok() -> None:
    assert StooqProvider().get_provider_status() == ProviderStatus.ok


def test_stooq_capabilities_include_price_history() -> None:
    from app.integrations.financial_data_provider import ProviderCapability
    caps = StooqProvider().get_supported_capabilities()
    assert ProviderCapability.price_history in caps


def test_stooq_capabilities_exclude_company_profile() -> None:
    from app.integrations.financial_data_provider import ProviderCapability
    caps = StooqProvider().get_supported_capabilities()
    assert ProviderCapability.company_profile not in caps


# ---------------------------------------------------------------------------
# StooqProvider — symbol building
# ---------------------------------------------------------------------------


def test_stooq_symbol_nasdaq_defaults_to_us() -> None:
    assert _stooq_symbol("AAPL", "NASDAQ") == "aapl.us"


def test_stooq_symbol_xetra_maps_to_de() -> None:
    assert _stooq_symbol("VOW3", "XETRA") == "vow3.de"


def test_stooq_symbol_lse_maps_to_uk() -> None:
    assert _stooq_symbol("VOD", "LSE") == "vod.uk"


def test_stooq_symbol_none_exchange_defaults_to_us() -> None:
    assert _stooq_symbol("MSFT", None) == "msft.us"


def test_stooq_symbol_unknown_exchange_defaults_to_us() -> None:
    assert _stooq_symbol("XYZ", "UNKNOWN_EXCHANGE") == "xyz.us"


def test_stooq_symbol_ose_maps_to_no() -> None:
    assert _stooq_symbol("EQNR", "OSE") == "eqnr.no"


def test_stooq_symbol_always_lowercase_ticker() -> None:
    symbol = _stooq_symbol("AAPL", "NASDAQ")
    assert symbol == symbol.lower()


# ---------------------------------------------------------------------------
# StooqProvider — CSV parsing (fixture-based, no network)
# ---------------------------------------------------------------------------


def test_stooq_parse_csv_returns_price_history() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert isinstance(result, PriceHistoryData)


def test_stooq_parse_csv_ticker_preserved() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert result.ticker == "AAPL"


def test_stooq_parse_csv_correct_row_count() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert len(result.price_points) == 5


def test_stooq_parse_csv_first_date() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert result.price_points[0].date == "2026-06-13"


def test_stooq_parse_csv_close_values_positive() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    for pt in result.price_points:
        assert pt.close > 0


def test_stooq_parse_csv_has_volume() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    for pt in result.price_points:
        assert pt.volume is not None
        assert pt.volume > 0


def test_stooq_parse_csv_meta_not_mock() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert result.meta.is_mock is False


def test_stooq_parse_csv_meta_source_tier_is_t5() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert result.meta.source_tier == SourceTier.T5_api_aggregator


def test_stooq_parse_csv_data_quality_is_b_single_credible() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    assert result.data_quality == DataQuality.B_single_credible.value


def test_stooq_parse_csv_source_url_recorded() -> None:
    url = "https://stooq.com/q/d/l/?s=aapl.us&i=d"
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", url)
    assert result.source_url == url


def test_stooq_parse_no_data_raises_value_error() -> None:
    csv_text = _read_fixture("stooq_no_data.csv")
    with pytest.raises(ValueError, match="no price data"):
        _parse_stooq_csv(csv_text, "FAKE999", "NASDAQ", "https://stooq.com/")


def test_stooq_parse_csv_open_high_low_present() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    result = _parse_stooq_csv(csv_text, "AAPL", "NASDAQ", "https://stooq.com/q/d/l/?s=aapl.us&i=d")
    pt = result.price_points[0]
    assert pt.open is not None
    assert pt.high is not None
    assert pt.low is not None


# ---------------------------------------------------------------------------
# StooqProvider — source record helper
# ---------------------------------------------------------------------------


def test_stooq_build_source_record_returns_attrs() -> None:
    provider = StooqProvider()
    attrs = provider.build_source_record_for_prices("AAPL", "NASDAQ")
    assert isinstance(attrs, SourceRecordAttrs)


def test_stooq_build_source_record_tier_is_t5() -> None:
    provider = StooqProvider()
    attrs = provider.build_source_record_for_prices("AAPL", "NASDAQ")
    assert attrs.source_tier == SourceTier.T5_api_aggregator


def test_stooq_build_source_record_source_type_is_financial_data_api() -> None:
    provider = StooqProvider()
    attrs = provider.build_source_record_for_prices("AAPL", "NASDAQ")
    assert attrs.source_type == "financial_data_api"


def test_stooq_build_source_record_has_source_url() -> None:
    provider = StooqProvider()
    attrs = provider.build_source_record_for_prices("AAPL", "NASDAQ")
    assert attrs.url is not None
    assert "stooq" in attrs.url


# ---------------------------------------------------------------------------
# StooqProvider — get_company_profile raises NotImplementedError
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stooq_get_company_profile_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await StooqProvider().get_company_profile("AAPL")


@pytest.mark.asyncio
async def test_stooq_get_fundamentals_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await StooqProvider().get_fundamentals("AAPL")


# ---------------------------------------------------------------------------
# StooqProvider — get_price_history (mock httpx, no real network)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stooq_get_price_history_uses_httpx() -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    mock_response = MagicMock()
    mock_response.text = csv_text
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.stooq_provider.httpx.AsyncClient", return_value=mock_client):
        result = await StooqProvider().get_price_history("AAPL", "NASDAQ")

    assert isinstance(result, PriceHistoryData)
    assert len(result.price_points) == 5
    assert result.meta.is_mock is False


# ---------------------------------------------------------------------------
# GleifProvider — provider metadata
# ---------------------------------------------------------------------------


def test_gleif_provider_name() -> None:
    assert GleifProvider().provider_name == "gleif"


def test_gleif_source_tier_is_t2() -> None:
    assert GleifProvider().source_tier == SourceTier.T2_regulator_or_gov


def test_gleif_provider_status_is_ok() -> None:
    assert GleifProvider().get_provider_status() == ProviderStatus.ok


def test_gleif_capabilities_include_lei_lookup() -> None:
    from app.integrations.financial_data_provider import ProviderCapability
    caps = GleifProvider().get_supported_capabilities()
    assert ProviderCapability.lei_lookup in caps


def test_gleif_capabilities_include_company_profile() -> None:
    from app.integrations.financial_data_provider import ProviderCapability
    caps = GleifProvider().get_supported_capabilities()
    assert ProviderCapability.company_profile in caps


# ---------------------------------------------------------------------------
# GleifProvider — LEI detection
# ---------------------------------------------------------------------------


def test_is_lei_valid_20_char_alphanumeric() -> None:
    assert _is_lei("HWUPKR0MPOU8FGXBT394") is True


def test_is_lei_rejects_ticker() -> None:
    assert _is_lei("AAPL") is False


def test_is_lei_rejects_company_name() -> None:
    assert _is_lei("Apple Inc.") is False


def test_is_lei_rejects_19_chars() -> None:
    assert _is_lei("HWUPKR0MPOU8FGXBT39") is False


def test_is_lei_rejects_21_chars() -> None:
    assert _is_lei("HWUPKR0MPOU8FGXBT3940") is False


def test_is_lei_case_insensitive() -> None:
    assert _is_lei("hwupkr0mpou8fgxbt394") is True


# ---------------------------------------------------------------------------
# GleifProvider — JSON parsing (fixture-based, no network)
# ---------------------------------------------------------------------------


def test_gleif_parse_record_from_fixture() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    assert parsed["legal_name"] == "Apple Inc."


def test_gleif_parse_record_lei() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    assert parsed["lei"] == "HWUPKR0MPOU8FGXBT394"


def test_gleif_parse_record_jurisdiction() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    assert parsed["jurisdiction"] == "US-DE"


def test_gleif_parse_record_country() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    assert parsed["country"] == "US"


def test_gleif_parse_record_status() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    assert parsed["entity_status"] == "ACTIVE"


def test_gleif_record_to_profile_returns_company_profile() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    profile = _record_to_profile(parsed, "https://api.gleif.org/api/v1/lei-records/HWUPKR0MPOU8FGXBT394")
    assert isinstance(profile, CompanyProfileData)


def test_gleif_profile_legal_name() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    profile = _record_to_profile(parsed, "https://api.gleif.org/api/v1/lei-records/HWUPKR0MPOU8FGXBT394")
    assert profile.legal_name == "Apple Inc."


def test_gleif_profile_lei_stored_as_ticker() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    profile = _record_to_profile(parsed, "https://api.gleif.org/api/v1/lei-records/HWUPKR0MPOU8FGXBT394")
    assert profile.lei == "HWUPKR0MPOU8FGXBT394"
    assert profile.ticker == "HWUPKR0MPOU8FGXBT394"


def test_gleif_profile_data_quality_is_a_verified() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    profile = _record_to_profile(parsed, "https://api.gleif.org/api/v1/lei-records/HWUPKR0MPOU8FGXBT394")
    assert profile.data_quality == DataQuality.A_verified.value


def test_gleif_profile_meta_not_mock() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    profile = _record_to_profile(parsed, "https://api.gleif.org/api/v1/lei-records/HWUPKR0MPOU8FGXBT394")
    assert profile.meta.is_mock is False


def test_gleif_profile_source_tier_is_t2() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    profile = _record_to_profile(parsed, "https://api.gleif.org/api/v1/lei-records/HWUPKR0MPOU8FGXBT394")
    assert profile.meta.source_tier == SourceTier.T2_regulator_or_gov


def test_gleif_profile_country_domicile() -> None:
    data = _read_json_fixture("gleif_apple_inc.json")
    record = data["data"][0]
    parsed = _parse_gleif_record(record)
    profile = _record_to_profile(parsed, "https://api.gleif.org/api/v1/lei-records/HWUPKR0MPOU8FGXBT394")
    assert profile.country_domicile == "US"


# ---------------------------------------------------------------------------
# GleifProvider — source record helper
# ---------------------------------------------------------------------------


def test_gleif_build_source_record_for_lei() -> None:
    provider = GleifProvider()
    attrs = provider.build_source_record_for_entity("HWUPKR0MPOU8FGXBT394")
    assert isinstance(attrs, SourceRecordAttrs)
    assert attrs.source_type == "government_data"


def test_gleif_build_source_record_credibility_high() -> None:
    provider = GleifProvider()
    attrs = provider.build_source_record_for_entity("HWUPKR0MPOU8FGXBT394")
    assert attrs.credibility_score >= 0.85


def test_gleif_build_source_record_for_name_search() -> None:
    provider = GleifProvider()
    attrs = provider.build_source_record_for_entity("Apple Inc.")
    assert isinstance(attrs, SourceRecordAttrs)
    assert "gleif" in (attrs.url or "").lower() or "gleif" in attrs.publisher.lower()


# ---------------------------------------------------------------------------
# GleifProvider — get_by_lei and search_by_name (mock httpx)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_gleif_get_by_lei_parses_fixture() -> None:
    raw = _read_fixture("gleif_apple_inc.json")
    fixture_data = json.loads(raw)
    # Direct LEI fetch returns a single "data" object (not a list)
    single_record_data = {"data": fixture_data["data"][0]}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=single_record_data)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.gleif_provider.httpx.AsyncClient", return_value=mock_client):
        profile = await GleifProvider().get_by_lei("HWUPKR0MPOU8FGXBT394")

    assert profile.legal_name == "Apple Inc."
    assert profile.lei == "HWUPKR0MPOU8FGXBT394"


@pytest.mark.asyncio
async def test_gleif_get_by_lei_404_raises_value_error() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.gleif_provider.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="not found in GLEIF registry"):
            await GleifProvider().get_by_lei("XXXXXXXXXXXXXXXXXXXXXXXX"[:20])


@pytest.mark.asyncio
async def test_gleif_search_by_name_returns_list() -> None:
    fixture_data = _read_json_fixture("gleif_apple_inc.json")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=fixture_data)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.gleif_provider.httpx.AsyncClient", return_value=mock_client):
        results = await GleifProvider().search_by_name("Apple Inc.")

    assert isinstance(results, list)
    assert len(results) == 1
    assert results[0].legal_name == "Apple Inc."


@pytest.mark.asyncio
async def test_gleif_search_by_name_empty_returns_empty_list() -> None:
    fixture_data = _read_json_fixture("gleif_empty_result.json")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=fixture_data)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.gleif_provider.httpx.AsyncClient", return_value=mock_client):
        results = await GleifProvider().search_by_name("NONEXISTENT_COMPANY_XYZ")

    assert results == []


@pytest.mark.asyncio
async def test_gleif_get_company_profile_name_no_results_raises() -> None:
    fixture_data = _read_json_fixture("gleif_empty_result.json")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=fixture_data)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.gleif_provider.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="No GLEIF record found"):
            await GleifProvider().get_company_profile("NONEXISTENT_COMPANY_XYZ")


# ---------------------------------------------------------------------------
# SecEdgarProvider — provider metadata
# ---------------------------------------------------------------------------


def test_sec_edgar_provider_name() -> None:
    assert SecEdgarProvider().provider_name == "sec_edgar"


def test_sec_edgar_source_tier_is_t2() -> None:
    assert SecEdgarProvider().source_tier == SourceTier.T2_regulator_or_gov


def test_sec_edgar_provider_status_is_ok() -> None:
    assert SecEdgarProvider().get_provider_status() == ProviderStatus.ok


def test_sec_edgar_capabilities_include_company_profile() -> None:
    from app.integrations.financial_data_provider import ProviderCapability
    caps = SecEdgarProvider().get_supported_capabilities()
    assert ProviderCapability.company_profile in caps


def test_sec_edgar_capabilities_include_fundamentals() -> None:
    from app.integrations.financial_data_provider import ProviderCapability
    caps = SecEdgarProvider().get_supported_capabilities()
    assert ProviderCapability.fundamentals in caps


# ---------------------------------------------------------------------------
# SecEdgarProvider — CIK padding
# ---------------------------------------------------------------------------


def test_pad_cik_short_cik() -> None:
    assert _pad_cik("320193") == "0000320193"


def test_pad_cik_already_padded() -> None:
    assert _pad_cik("0000320193") == "0000320193"


def test_pad_cik_single_digit() -> None:
    assert _pad_cik("1") == "0000000001"


def test_pad_cik_strips_leading_zeros_then_repads() -> None:
    assert _pad_cik("0000320193") == "0000320193"


# ---------------------------------------------------------------------------
# SecEdgarProvider — JSON parsing (fixture-based, no network)
# ---------------------------------------------------------------------------


def test_sec_edgar_parse_submissions_from_fixture() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    assert parsed["name"] == "Apple Inc."


def test_sec_edgar_parse_submissions_tickers() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    assert "AAPL" in parsed["tickers"]


def test_sec_edgar_parse_submissions_exchanges() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    assert "Nasdaq" in parsed["exchanges"]


def test_sec_edgar_parse_submissions_sic_description() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    assert parsed["sic_description"] == "Electronic Computers"


def test_sec_edgar_parse_submissions_fiscal_year_end() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    assert parsed["fiscal_year_end"] == "0930"


def test_sec_edgar_parse_submissions_website() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    assert parsed["website"] == "https://www.apple.com"


def test_sec_edgar_submissions_to_profile_returns_company_profile() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert isinstance(profile, CompanyProfileData)


def test_sec_edgar_profile_legal_name() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert profile.legal_name == "Apple Inc."


def test_sec_edgar_profile_country_domicile_is_us() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert profile.country_domicile == "US"


def test_sec_edgar_profile_fiscal_year_end_parsed() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert profile.fiscal_year_end == "September"


def test_sec_edgar_profile_website() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert profile.website == "https://www.apple.com"


def test_sec_edgar_profile_data_quality_is_a_verified() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert profile.data_quality == DataQuality.A_verified.value


def test_sec_edgar_profile_meta_not_mock() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert profile.meta.is_mock is False


def test_sec_edgar_profile_source_tier_is_t2() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")
    parsed = _parse_edgar_submissions(data, "320193")
    profile = _submissions_to_profile(parsed, "https://data.sec.gov/submissions/CIK0000320193.json")
    assert profile.meta.source_tier == SourceTier.T2_regulator_or_gov


# ---------------------------------------------------------------------------
# SecEdgarProvider — source record helper
# ---------------------------------------------------------------------------


def test_sec_edgar_build_source_record() -> None:
    provider = SecEdgarProvider()
    attrs = provider.build_source_record_for_company("320193")
    assert isinstance(attrs, SourceRecordAttrs)


def test_sec_edgar_build_source_record_type_is_government_data() -> None:
    provider = SecEdgarProvider()
    attrs = provider.build_source_record_for_company("320193")
    assert attrs.source_type == "government_data"


def test_sec_edgar_build_source_record_credibility_high() -> None:
    provider = SecEdgarProvider()
    attrs = provider.build_source_record_for_company("320193")
    assert attrs.credibility_score >= 0.85


def test_sec_edgar_build_source_record_url_contains_cik() -> None:
    provider = SecEdgarProvider()
    attrs = provider.build_source_record_for_company("320193")
    assert "0000320193" in (attrs.url or "")


# ---------------------------------------------------------------------------
# SecEdgarProvider — routing and error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_sec_edgar_get_company_profile_with_cik_calls_get_company_by_cik() -> None:
    data = _read_json_fixture("sec_edgar_aapl_submissions.json")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=data)
    mock_response.raise_for_status = MagicMock()

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.sec_edgar_provider.httpx.AsyncClient", return_value=mock_client):
        profile = await SecEdgarProvider().get_company_profile("320193")

    assert profile.legal_name == "Apple Inc."


@pytest.mark.asyncio
async def test_sec_edgar_get_company_profile_with_ticker_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError, match="ticker-based lookup"):
        await SecEdgarProvider().get_company_profile("AAPL")


@pytest.mark.asyncio
async def test_sec_edgar_get_company_by_cik_404_raises_value_error() -> None:
    mock_response = MagicMock()
    mock_response.status_code = 404

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.sec_edgar_provider.httpx.AsyncClient", return_value=mock_client):
        with pytest.raises(ValueError, match="not found in SEC EDGAR"):
            await SecEdgarProvider().get_company_by_cik("9999999999")


@pytest.mark.asyncio
async def test_sec_edgar_get_price_history_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await SecEdgarProvider().get_price_history("320193")


@pytest.mark.asyncio
async def test_sec_edgar_get_fundamentals_raises_not_implemented() -> None:
    with pytest.raises(NotImplementedError):
        await SecEdgarProvider().get_fundamentals("320193")


# ---------------------------------------------------------------------------
# build_source_record utility
# ---------------------------------------------------------------------------


def test_build_source_record_t1_filing_type() -> None:
    from datetime import datetime, timezone
    meta = ProviderResponseMetadata(
        provider_name="sec_edgar",
        source_tier=SourceTier.T1_primary_filing,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    attrs = build_source_record(meta, source_url="https://example.com/10k.pdf", title="10-K 2025")
    assert attrs.source_type == "company_filing"
    assert attrs.credibility_score == 0.95


def test_build_source_record_t2_gov_type() -> None:
    from datetime import datetime, timezone
    meta = ProviderResponseMetadata(
        provider_name="gleif",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    attrs = build_source_record(meta, source_url="https://api.gleif.org/...", title="GLEIF LEI")
    assert attrs.source_type == "government_data"
    assert attrs.credibility_score == 0.90


def test_build_source_record_t5_api_type() -> None:
    from datetime import datetime, timezone
    meta = ProviderResponseMetadata(
        provider_name="stooq",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    attrs = build_source_record(meta, source_url="https://stooq.com/...", title="Stooq prices")
    assert attrs.source_type == "financial_data_api"
    assert attrs.credibility_score == 0.55


def test_build_source_record_provider_name_preserved() -> None:
    from datetime import datetime, timezone
    meta = ProviderResponseMetadata(
        provider_name="stooq",
        source_tier=SourceTier.T5_api_aggregator,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    attrs = build_source_record(meta)
    assert attrs.provider_name == "stooq"
    assert attrs.publisher == "stooq"


def test_build_source_record_default_title_contains_provider() -> None:
    from datetime import datetime, timezone
    meta = ProviderResponseMetadata(
        provider_name="gleif",
        source_tier=SourceTier.T2_regulator_or_gov,
        retrieved_at=datetime.now(timezone.utc),
        is_mock=False,
        status=ProviderStatus.ok,
    )
    attrs = build_source_record(meta)
    assert "gleif" in attrs.title.lower()


# ---------------------------------------------------------------------------
# OpenBB — evaluation placeholder (still skeleton, documented)
# ---------------------------------------------------------------------------


def test_openbb_provider_name() -> None:
    assert OpenBBProvider().provider_name == "openbb"


def test_openbb_source_tier_is_t5() -> None:
    assert OpenBBProvider().source_tier == SourceTier.T5_api_aggregator


def test_openbb_status_still_not_implemented() -> None:
    assert OpenBBProvider().get_provider_status() == ProviderStatus.not_implemented


@pytest.mark.asyncio
async def test_openbb_raises_not_implemented_for_all_methods() -> None:
    provider = OpenBBProvider()
    with pytest.raises(NotImplementedError):
        await provider.get_company_profile("AAPL")
    with pytest.raises(NotImplementedError):
        await provider.get_price_history("AAPL")
    with pytest.raises(NotImplementedError):
        await provider.get_fundamentals("AAPL")


# ---------------------------------------------------------------------------
# API endpoints — smoke tests using mock providers (no live calls)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_api_stooq_prices_endpoint(client) -> None:
    csv_text = _read_fixture("stooq_aapl_us.csv")
    mock_response = MagicMock()
    mock_response.text = csv_text
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.stooq_provider.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/v1/financial-data/stooq/prices/AAPL?exchange=NASDAQ")

    assert resp.status_code == 200
    body = resp.json()
    assert body["ticker"] == "AAPL"
    assert len(body["price_points"]) == 5
    assert body["meta"]["is_mock"] is False
    assert body["meta"]["source_tier"] == "T5_api_aggregator"


@pytest.mark.asyncio
async def test_api_gleif_entity_endpoint_by_lei(client) -> None:
    fixture_data = _read_json_fixture("gleif_apple_inc.json")
    single_record_data = {"data": fixture_data["data"][0]}

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=single_record_data)
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.gleif_provider.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/v1/financial-data/gleif/entity/HWUPKR0MPOU8FGXBT394")

    assert resp.status_code == 200
    body = resp.json()
    assert body["legal_name"] == "Apple Inc."
    assert body["meta"]["source_tier"] == "T2_regulator_or_gov"
    assert body["meta"]["is_mock"] is False


@pytest.mark.asyncio
async def test_api_sec_edgar_company_endpoint(client) -> None:
    fixture_data = _read_json_fixture("sec_edgar_aapl_submissions.json")

    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json = MagicMock(return_value=fixture_data)
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.sec_edgar_provider.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/v1/financial-data/sec-edgar/company/320193")

    assert resp.status_code == 200
    body = resp.json()
    assert body["legal_name"] == "Apple Inc."
    assert body["meta"]["source_tier"] == "T2_regulator_or_gov"
    assert body["meta"]["is_mock"] is False


@pytest.mark.asyncio
async def test_api_sec_edgar_non_numeric_cik_returns_422(client) -> None:
    resp = await client.get("/api/v1/financial-data/sec-edgar/company/AAPL")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_api_stooq_unknown_ticker_returns_404(client) -> None:
    csv_text = _read_fixture("stooq_no_data.csv")
    mock_response = MagicMock()
    mock_response.text = csv_text
    mock_response.raise_for_status = MagicMock()
    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)

    with patch("app.integrations.providers.stooq_provider.httpx.AsyncClient", return_value=mock_client):
        resp = await client.get("/api/v1/financial-data/stooq/prices/FAKE999XYZ")

    assert resp.status_code == 404
