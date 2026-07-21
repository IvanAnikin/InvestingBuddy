"""
Exchange registry — the single source of truth for listing venues.

Answers three questions the rest of the platform keeps asking separately:

  * Where does this ticker trade (country, region, currency)?
  * Is this a US venue?
  * Can SEC EDGAR authoritatively resolve a ticker on this venue?

``sec_eligible`` is the load-bearing field. SEC's ``company_tickers.json`` is an
index of **US registrants keyed by ticker string alone**. Looking up a non-US
local ticker there does not fail — it silently returns an unrelated US issuer:

    BA + LSE  -> BAE Systems plc, but SEC returns CIK 0000012927 (Boeing Co)
    MC + PA   -> LVMH,            but SEC returns Moelis & Co
    EL + PA   -> EssilorLuxottica, but SEC returns Estee Lauder

Producing another company's financials under this company's name is the most
dangerous failure this platform has (CLAUDE.md rule 6: never invent financial
numbers). ``sec_eligible`` is what stops it, so it is conservative by design:
True only for venues the SEC index actually covers.

OTC is deliberately ``False``. ADRs trade there and ticker collisions are
common, so OTC requires an explicit verified mapping in
``app.integrations.sec_issuer_registry``.
"""

from __future__ import annotations

from dataclasses import dataclass

REGION_NORTH_AMERICA = "North America"
REGION_EUROPE = "Europe"
REGION_JAPAN = "Japan"
REGION_CHINA = "China"
REGION_ASIA = "Asia"
REGION_OCEANIA = "Oceania"
REGION_AFRICA = "Africa"
REGION_SOUTH_AMERICA = "South America"


@dataclass(frozen=True)
class ExchangeInfo:
    """A listing venue. ``code`` is the EODHD-style suffix and canonical key."""

    code: str
    name: str
    mic: str | None
    country: str
    region: str
    currency: str
    is_us: bool
    sec_eligible: bool


def _us(code: str, name: str, mic: str | None) -> ExchangeInfo:
    return ExchangeInfo(
        code=code,
        name=name,
        mic=mic,
        country="United States",
        region=REGION_NORTH_AMERICA,
        currency="USD",
        is_us=True,
        sec_eligible=True,
    )


def _intl(
    code: str,
    name: str,
    mic: str | None,
    country: str,
    region: str,
    currency: str,
) -> ExchangeInfo:
    return ExchangeInfo(
        code=code,
        name=name,
        mic=mic,
        country=country,
        region=region,
        currency=currency,
        is_us=False,
        sec_eligible=False,
    )


EXCHANGES: dict[str, ExchangeInfo] = {
    # --- United States (SEC-eligible) ---------------------------------------
    "US": _us("US", "United States (composite)", None),
    "NYSE": _us("NYSE", "New York Stock Exchange", "XNYS"),
    "NASDAQ": _us("NASDAQ", "Nasdaq Stock Market", "XNAS"),
    "AMEX": _us("AMEX", "NYSE American", "XASE"),
    "ARCA": _us("ARCA", "NYSE Arca", "ARCX"),
    "BATS": _us("BATS", "Cboe BZX", "BATS"),
    # OTC is US-domiciled but NOT sec_eligible — see module docstring.
    "OTC": ExchangeInfo(
        code="OTC",
        name="OTC Markets",
        mic="OTCM",
        country="United States",
        region=REGION_NORTH_AMERICA,
        currency="USD",
        is_us=True,
        sec_eligible=False,
    ),
    # --- Europe -------------------------------------------------------------
    "LSE": _intl("LSE", "London Stock Exchange", "XLON", "United Kingdom", REGION_EUROPE, "GBP"),
    "XETRA": _intl("XETRA", "Deutsche Boerse Xetra", "XETR", "Germany", REGION_EUROPE, "EUR"),
    "F": _intl("F", "Frankfurt Stock Exchange", "XFRA", "Germany", REGION_EUROPE, "EUR"),
    "PA": _intl("PA", "Euronext Paris", "XPAR", "France", REGION_EUROPE, "EUR"),
    "MI": _intl("MI", "Borsa Italiana", "XMIL", "Italy", REGION_EUROPE, "EUR"),
    "AS": _intl("AS", "Euronext Amsterdam", "XAMS", "Netherlands", REGION_EUROPE, "EUR"),
    "BR": _intl("BR", "Euronext Brussels", "XBRU", "Belgium", REGION_EUROPE, "EUR"),
    "MC": _intl("MC", "Bolsa de Madrid", "XMAD", "Spain", REGION_EUROPE, "EUR"),
    "SW": _intl("SW", "SIX Swiss Exchange", "XSWX", "Switzerland", REGION_EUROPE, "CHF"),
    "VX": _intl("VX", "SIX Swiss (blue chip)", "XVTX", "Switzerland", REGION_EUROPE, "CHF"),
    "ST": _intl("ST", "Nasdaq Stockholm", "XSTO", "Sweden", REGION_EUROPE, "SEK"),
    "CO": _intl("CO", "Nasdaq Copenhagen", "XCSE", "Denmark", REGION_EUROPE, "DKK"),
    "HE": _intl("HE", "Nasdaq Helsinki", "XHEL", "Finland", REGION_EUROPE, "EUR"),
    "OL": _intl("OL", "Oslo Bors", "XOSL", "Norway", REGION_EUROPE, "NOK"),
    "IR": _intl("IR", "Euronext Dublin", "XDUB", "Ireland", REGION_EUROPE, "EUR"),
    "LS": _intl("LS", "Euronext Lisbon", "XLIS", "Portugal", REGION_EUROPE, "EUR"),
    "VI": _intl("VI", "Wiener Boerse", "XWBO", "Austria", REGION_EUROPE, "EUR"),
    "WA": _intl("WA", "Warsaw Stock Exchange", "XWAR", "Poland", REGION_EUROPE, "PLN"),
    # --- Asia / Oceania -----------------------------------------------------
    "TSE": _intl("TSE", "Tokyo Stock Exchange", "XTKS", "Japan", REGION_JAPAN, "JPY"),
    "HK": _intl("HK", "Hong Kong Stock Exchange", "XHKG", "Hong Kong", REGION_ASIA, "HKD"),
    "SHG": _intl("SHG", "Shanghai Stock Exchange", "XSHG", "China", REGION_CHINA, "CNY"),
    "SHE": _intl("SHE", "Shenzhen Stock Exchange", "XSHE", "China", REGION_CHINA, "CNY"),
    "KO": _intl("KO", "Korea Exchange (KOSPI)", "XKRX", "South Korea", REGION_ASIA, "KRW"),
    "KQ": _intl("KQ", "Korea Exchange (KOSDAQ)", "XKOS", "South Korea", REGION_ASIA, "KRW"),
    "TW": _intl("TW", "Taiwan Stock Exchange", "XTAI", "Taiwan", REGION_ASIA, "TWD"),
    "NSE": _intl("NSE", "National Stock Exchange of India", "XNSE", "India", REGION_ASIA, "INR"),
    "BSE": _intl("BSE", "BSE Limited", "XBOM", "India", REGION_ASIA, "INR"),
    "AU": _intl("AU", "Australian Securities Exchange", "XASX", "Australia", REGION_OCEANIA, "AUD"),
    "NZ": _intl("NZ", "New Zealand Exchange", "XNZE", "New Zealand", REGION_OCEANIA, "NZD"),
    # --- Americas (non-US) / Africa -----------------------------------------
    "TO": _intl("TO", "Toronto Stock Exchange", "XTSE", "Canada", REGION_NORTH_AMERICA, "CAD"),
    "V": _intl("V", "TSX Venture Exchange", "XTSX", "Canada", REGION_NORTH_AMERICA, "CAD"),
    "MX": _intl("MX", "Bolsa Mexicana de Valores", "XMEX", "Mexico", REGION_NORTH_AMERICA, "MXN"),
    "SA": _intl("SA", "B3 Brasil Bolsa Balcao", "BVMF", "Brazil", REGION_SOUTH_AMERICA, "BRL"),
    "JSE": _intl("JSE", "Johannesburg Exchange", "XJSE", "South Africa", REGION_AFRICA, "ZAR"),
}

# Exchange name/alias -> canonical registry code. Absorbs the former
# ``identifier_resolver._EXCHANGE_TO_SUFFIX``.
_EXCHANGE_ALIASES: dict[str, str] = {
    "NASDAQ": "US",
    "NYSE": "US",
    "AMEX": "US",
    "ARCA": "US",
    "BATS": "US",
    "NYSE MKT": "US",
    "NYSE AMERICAN": "US",
    "NASDAQ GLOBAL SELECT": "US",
    "OSE": "OL",
    "OSLO": "OL",
    "STO": "ST",
    "TSX": "TO",
    "TSXV": "V",
    "ASX": "AU",
    "SIX": "SW",
    "EURONEXT PARIS": "PA",
    "EURONEXT AMSTERDAM": "AS",
    "EURONEXT BRUSSELS": "BR",
    "BORSA ITALIANA": "MI",
    "FRA": "F",
    "ETR": "XETRA",
    "TYO": "TSE",
    "HKEX": "HK",
}


def get_exchange(code: str | None) -> ExchangeInfo | None:
    """Return the ``ExchangeInfo`` for ``code``, or None when unknown."""
    if not code:
        return None
    key = code.strip().upper()
    if not key:
        return None
    if key in EXCHANGES:
        return EXCHANGES[key]
    aliased = _EXCHANGE_ALIASES.get(key)
    if aliased:
        return EXCHANGES.get(aliased)
    return None


def normalize_exchange(code: str | None) -> str:
    """
    Canonicalize an exchange code ("NASDAQ"/"NYSE"/"AMEX" -> "US").

    Aliases are resolved BEFORE direct registry hits so that the US venues,
    which are registry entries in their own right, still collapse onto the
    single "US" key. Callers use this as a cache key, and NYSE and NASDAQ must
    not address different cache slots than US for the same SEC-registrant data.

    Unknown codes are returned upper-cased and stripped rather than dropped, so
    an unrecognized venue stays visible (and, being unknown, is not
    SEC-eligible) instead of silently becoming a US lookup.
    """
    if not code:
        return ""
    key = code.strip().upper()
    aliased = _EXCHANGE_ALIASES.get(key)
    if aliased:
        return aliased
    return key


def is_sec_eligible(code: str | None) -> bool:
    """
    True when SEC EDGAR's ticker index can authoritatively resolve this venue.

    ``None``/empty returns **True** by design. Legacy ticker-only flows
    (AAPL/MSFT/NVDA) call through without an exchange and must keep working;
    treating an absent exchange as ineligible would regress every one of them
    to "not sourced". An explicitly non-US code is what triggers the gate.
    """
    if not code or not code.strip():
        return True
    info = get_exchange(code)
    if info is None:
        # Unknown venue: refuse to guess. A wrong CIK is worse than no data.
        return False
    return info.sec_eligible


def is_us_exchange(code: str | None) -> bool:
    """True when ``code`` is a US venue. Unknown/absent codes are not US."""
    info = get_exchange(code)
    return bool(info and info.is_us)


def region_for_exchange(code: str | None) -> str | None:
    info = get_exchange(code)
    return info.region if info else None


def country_for_exchange(code: str | None) -> str | None:
    info = get_exchange(code)
    return info.country if info else None


def currency_for_exchange(code: str | None) -> str | None:
    info = get_exchange(code)
    return info.currency if info else None


def _build_country_to_region() -> dict[str, str]:
    """Derive country -> region from the registry (absorbs the old table)."""
    mapping: dict[str, str] = {}
    for info in EXCHANGES.values():
        mapping.setdefault(info.country, info.region)
    return mapping


COUNTRY_TO_REGION: dict[str, str] = _build_country_to_region()


def region_for_country(country: str | None) -> str | None:
    """Return the canonical region for a country name, or None when unknown."""
    if not country:
        return None
    return COUNTRY_TO_REGION.get(country.strip())
