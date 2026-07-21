"""
Explicit ticker+exchange -> SEC CIK mappings.

This registry is the **only** sanctioned way to resolve a non-US listing to a
SEC CIK. SEC's ``company_tickers.json`` indexes US registrants by ticker string
alone, so searching it for a non-US local ticker silently returns an unrelated
US issuer (BA.LSE -> Boeing, MC.PA -> Moelis, EL.PA -> Estee Lauder). Deriving a
foreign CIK from a ticker string is therefore never permitted — this table
exists so that every such mapping is a deliberate, sourced, human decision.

PROVENANCE RULE (hard):
    An entry may be added ONLY after the implementer opens

        https://www.sec.gov/cgi-bin/browse-edgar?action=getcompany&CIK=<cik>

    and confirms the CIK belongs to that issuer. ``source_url`` and
    ``verified_on`` are mandatory and must record that check. Guessing a CIK
    reproduces the Boeing bug with extra steps.

SHIPPING THIS EMPTY IS CORRECT. With no entries, non-US issuers degrade to an
honest "not sourced" profile with fundamentals marked as requiring human
research — the safe outcome. Do not populate it speculatively to make a test
pass; tests inject fixtures instead (see
``tests/test_phase27_1a_exchange_aware_sec.py``).
"""

from __future__ import annotations

from dataclasses import dataclass

MAPPING_US_LISTED = "us_listed"
MAPPING_ADR = "adr"
MAPPING_FOREIGN_PRIVATE_ISSUER = "foreign_private_issuer"


@dataclass(frozen=True)
class SecIssuerMapping:
    """A verified ticker+exchange -> CIK mapping. Zero-padding is applied downstream."""

    ticker: str
    exchange: str
    cik: str
    issuer_name: str
    mapping_type: str
    source_url: str
    verified_on: str  # ISO date, e.g. "2026-07-21"


def _key(ticker: str, exchange: str | None) -> tuple[str, str]:
    return (ticker.strip().upper(), (exchange or "").strip().upper())


# Deliberately empty — see module docstring. Every entry needs a source_url and
# a verified_on recording a manual check against sec.gov.
SEC_ISSUER_MAPPINGS: dict[tuple[str, str], SecIssuerMapping] = {}


def lookup_sec_issuer(ticker: str, exchange: str | None) -> SecIssuerMapping | None:
    """
    Return the verified mapping for ``(ticker, exchange)``, or None.

    Matching is exact on the pair. A mapping registered for one venue never
    applies to another — that pairing is the whole point of the table.
    """
    if not ticker:
        return None
    return SEC_ISSUER_MAPPINGS.get(_key(ticker, exchange))


def register_mapping(mapping: SecIssuerMapping) -> None:
    """
    Register a mapping at runtime (used by tests to inject fixtures).

    Rejects entries missing provenance so an unsourced CIK cannot enter the
    registry through this door either.
    """
    if not mapping.source_url or not mapping.verified_on:
        raise ValueError(
            f"SEC issuer mapping for {mapping.ticker}.{mapping.exchange} is "
            "missing source_url or verified_on. Every mapping must record the "
            "sec.gov page that proves the CIK belongs to this issuer."
        )
    SEC_ISSUER_MAPPINGS[_key(mapping.ticker, mapping.exchange)] = mapping


def clear_mappings() -> None:
    """Remove all registered mappings (test isolation helper)."""
    SEC_ISSUER_MAPPINGS.clear()
