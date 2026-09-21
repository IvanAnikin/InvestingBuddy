"""Which tier a fetched public page belongs to, from its host — V3.18.3.

WHY THIS EXISTS
===============
``v3_pipeline.external_source_tier`` knew three regulator hosts and called everything
else ``T5_api_aggregator``. So a page InvestingBuddy fetched from the U.S. Geological
Survey, the World Bank or a national statistics office counted, for every contract and
every source-diversity figure, exactly as much as a content farm. An industry question
whose contract asks for an INDEPENDENT authority could never be satisfied by the web,
however good the source the research actually found.

WHAT IT DOES, AND WHAT IT REFUSES TO DO
=======================================
It maps a HOST to a tier by suffix, from short explicit lists. It never guesses an
issuer's own domain — ``verified_issuer_sources`` owns that question, because a
lookalike domain read as the company's own is the failure a tier exists to prevent. An
unknown host stays at the honest default, ``T5``.

A tier here says who PUBLISHED the page. It is not a verdict on the claim: every claim
still has to survive ``verify_lead`` against the bytes this platform fetched.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from app.services.sources.taxonomy import (
    T1_PRIMARY_FILING,
    T2_REGULATOR_OR_GOV,
    T3_INDUSTRY_SPECIALIST,
    T4_QUALITY_MEDIA,
    T5_API_AGGREGATOR,
)

#: Securities regulators and official filing systems: the page IS a filing.
_FILING_HOSTS: tuple[str, ...] = (
    "sec.gov",
    "sedarplus.ca",
    "sedar.com",
    "fca.org.uk",
    "esma.europa.eu",
    "asx.com.au",
    "cnmv.es",
    "bmv.com.mx",
    "smv.gob.pe",
)

#: Specialist statistical and technical agencies for a domain (the taxonomy's own
#: examples of T3 are USGS, IEA and ENTSO-E). Listed BEFORE the generic government
#: suffixes so a ``.gov`` specialist keeps its specialist tier.
_SPECIALIST_HOSTS: tuple[str, ...] = (
    "usgs.gov",
    "eia.gov",
    "iea.org",
    "irena.org",
    "entsoe.eu",
    "icsg.org",
    "ilzsg.org",
    "insg.org",
    "world-aluminium.org",
    "worldsteel.org",
    "silverinstitute.org",
    "gold.org",
    "copperalliance.org",
    "lme.com",
    "cmegroup.com",
    "bgs.ac.uk",
    "ga.gov.au",
    "nrcan.gc.ca",
    "cochilco.cl",
    "ingemmet.gob.pe",
    "minem.gob.pe",
    "sgm.gob.mx",
)

#: Governments, central banks and multilateral statistical bodies.
_GOVERNMENT_HOSTS: tuple[str, ...] = (
    "worldbank.org",
    "imf.org",
    "oecd.org",
    "un.org",
    "unctad.org",
    "wto.org",
    "europa.eu",
    "stlouisfed.org",
    "federalreserve.gov",
    "ecb.europa.eu",
    "bis.org",
    "energy.gov",
    "doe.gov",
    "defense.gov",
    "commerce.gov",
    "bls.gov",
    "census.gov",
    "treasury.gov",
    "whitehouse.gov",
    "congress.gov",
)
#: Government suffixes, matched on a label boundary: ``.gov``, ``.gov.au``, ``.gob.mx``.
_GOVERNMENT_SUFFIX_LABELS: tuple[str, ...] = ("gov", "gob", "gouv", "gv", "go", "govt")

#: Editorially accountable media and established trade press.
_QUALITY_MEDIA_HOSTS: tuple[str, ...] = (
    "reuters.com",
    "ft.com",
    "bloomberg.com",
    "wsj.com",
    "economist.com",
    "nytimes.com",
    "apnews.com",
    "mining.com",
    "mining-journal.com",
    "fastmarkets.com",
    "spglobal.com",
    "argusmedia.com",
    "bnamericas.com",
    "northernminer.com",
)


def host_of(url: str | None) -> str | None:
    try:
        host = (urlsplit(url or "").hostname or "").lower().strip(".")
    except ValueError:
        return None
    return host.removeprefix("www.") or None


def _matches(host: str, suffixes: tuple[str, ...]) -> bool:
    return any(host == s or host.endswith("." + s) for s in suffixes)


def _is_government(host: str) -> bool:
    if _matches(host, _GOVERNMENT_HOSTS):
        return True
    labels = host.split(".")
    # "x.gov", "x.gov.au", "x.gob.mx", "x.gouv.fr" — the government label must be the
    # TLD or the second-level label under a two-letter country code. "gov.example.com"
    # is not a government.
    if labels[-1] in _GOVERNMENT_SUFFIX_LABELS:
        return True
    return len(labels) >= 3 and len(labels[-1]) == 2 and labels[-2] in _GOVERNMENT_SUFFIX_LABELS


def publisher_tier(url: str | None) -> str:
    """The tier of the publisher behind ``url``. Unknown is ``T5``, never promoted."""
    host = host_of(url)
    if not host:
        return T5_API_AGGREGATOR
    if _matches(host, _FILING_HOSTS):
        return T1_PRIMARY_FILING
    if _matches(host, _SPECIALIST_HOSTS):
        return T3_INDUSTRY_SPECIALIST
    if _is_government(host):
        return T2_REGULATOR_OR_GOV
    if _matches(host, _QUALITY_MEDIA_HOSTS):
        return T4_QUALITY_MEDIA
    return T5_API_AGGREGATOR


__all__ = ["host_of", "publisher_tier"]
