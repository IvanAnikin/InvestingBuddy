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

#: Official filing systems: a page here IS a filing — of SOME entity. Whose is not a
#: question the host answers, so the tier never says "the issuer's own" (see
#: ``contracts.evidence_ref_for``).
_FILING_HOSTS: tuple[str, ...] = (
    "sedarplus.ca",
    "sedar.com",
    "asx.com.au",
    "cnmv.es",
    "bmv.com.mx",
    "smv.gob.pe",
)
#: sec.gov is a regulator's whole website — speeches, statistics, enforcement notices.
#: Only its filing archive is a filing.
_SEC_FILING_PATHS: tuple[str, ...] = ("/archives/", "/cgi-bin/browse-edgar", "/ix")

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
    "sec.gov",
    "fca.org.uk",
    "esma.europa.eu",
)
#: Government second-level domains, EXPLICITLY. A rule on the label alone ("gov" under
#: any two-letter TLD) accepted ``gov.io`` and ``go.me`` — second-level names anyone
#: can register under an open ccTLD. Each entry here is a registry reserved for its
#: government.
_GOVERNMENT_SLDS: tuple[str, ...] = (
    "gov.uk", "gov.au", "gov.br", "gov.in", "gov.za", "gov.cn", "gov.sg", "gov.hk",
    "gov.tw", "gov.il", "gov.it", "gov.pl", "gov.co", "gov.ph", "gov.my", "gov.sa",
    "gov.ae", "gov.tr", "gov.ar", "gov.cl", "gov.ie", "gov.pt", "gov.gr", "gov.ng",
    "gov.kz", "gov.mn", "gov.cd", "gov.zm", "gov.bo",
    "gob.mx", "gob.pe", "gob.cl", "gob.ar", "gob.es", "gob.ec", "gob.bo", "gob.gt",
    "gouv.fr", "gouv.qc.ca", "gc.ca", "canada.ca",
    "go.jp", "go.kr", "go.id", "go.th", "go.ke", "go.tz",
    "govt.nz", "gv.at",
)

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
    # ".gov" is the one government-only TLD; everything else must be a listed registry.
    # A bare registry ("gov.uk" itself) is not a publisher.
    if host.endswith(".gov"):
        return True
    return any(host.endswith("." + sld) for sld in _GOVERNMENT_SLDS)


def _path_of(url: str | None) -> str:
    try:
        return (urlsplit(url or "").path or "").lower()
    except ValueError:
        return ""


def publisher_tier(url: str | None) -> str:
    """The tier of the publisher behind ``url``. Unknown is ``T5``, never promoted."""
    host = host_of(url)
    if not host:
        return T5_API_AGGREGATOR
    if _matches(host, _FILING_HOSTS):
        return T1_PRIMARY_FILING
    if _matches(host, ("data.sec.gov", "efts.sec.gov")):
        return T1_PRIMARY_FILING
    if _matches(host, ("sec.gov",)) and _path_of(url).startswith(_SEC_FILING_PATHS):
        return T1_PRIMARY_FILING
    if _matches(host, _SPECIALIST_HOSTS):
        return T3_INDUSTRY_SPECIALIST
    if _is_government(host):
        return T2_REGULATOR_OR_GOV
    if _matches(host, _QUALITY_MEDIA_HOSTS):
        return T4_QUALITY_MEDIA
    return T5_API_AGGREGATOR


__all__ = ["host_of", "publisher_tier"]
