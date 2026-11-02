"""Whose filing is an EDGAR page? — ADR-079.

A HOST SAYS "A FILING", NOT "THE SUBJECT'S FILING"
=================================================
``classify_source`` reads ``sec.gov/Archives/…`` as ``regulatory_filing`` from the host
alone, and the evidence contract then read every T1 corpus chunk as ``issuer_filing``.
EDGAR holds every registrant's filings under one host. Live on Cellectis (CIK 1627281), a
Cboe EDGA Exchange financial-statement exhibit and a Calyxt 20-F, both found by web
search, were cited as "the company's" issuer filings.

The registrant is in the URL: ``/Archives/edgar/data/<CIK>/…``, the ``ix?doc=`` viewer
of the same path, ``browse-edgar?…CIK=``, ``data.sec.gov/…/CIK##########.json`` and the
full-text search's ``ciks=``. This module reads it and compares it with the subject's
CIKs. Pure: no network, no database. The subject's CIKs come from the caller (ADR-075's
``sec_listing_for`` and the SEC ticker directory).

The stored class is NOT rewritten: a web version is shared by content hash across
companies, so "the subject's filing" is a per-run judgement, made at read time — exactly
as ``trust.is_verified_issuer`` judges an ``issuer:`` origin every time.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from urllib.parse import parse_qs, unquote, urlsplit

from app.services.sources.publisher_tiers import publisher_tier
from app.services.sources.taxonomy import T1_PRIMARY_FILING

#: The URL's registrant IS the subject (its own CIK or ADR-075's verified sibling line).
EDGAR_OWN = "own"
#: The URL names another registrant.
EDGAR_OTHER = "other"
#: A filing page whose registrant cannot be matched: no CIK in the URL, or a subject
#: that is an SEC registrant whose CIK could not be resolved this run.
EDGAR_UNIDENTIFIED = "unidentified"

EDGAR_VERDICTS: frozenset[str] = frozenset({EDGAR_OWN, EDGAR_OTHER, EDGAR_UNIDENTIFIED})

_DATA_PATH_RE = re.compile(r"/archives/edgar/data/0*([0-9]{1,10})(?:/|$)", re.I)
_CIK_FILE_RE = re.compile(r"/CIK0*([0-9]{1,10})(?:\.json|/|$)", re.I)
_CIK_QUERY_KEYS = ("cik", "ciks")


def _host(url: str | None) -> str:
    try:
        host = (urlsplit(url or "").hostname or "").lower().strip(".")
    except ValueError:
        return ""
    return host.removeprefix("www.")


def is_sec_host(url: str | None) -> bool:
    host = _host(url)
    return host == "sec.gov" or host.endswith(".sec.gov")


def is_edgar_filing_url(url: str | None) -> bool:
    """A page in the SEC's FILING archive (the T1 part of sec.gov), not its speeches,
    rules or press releases — those are already a regulator's publication."""
    return is_sec_host(url) and publisher_tier(url) == T1_PRIMARY_FILING


def normalise_cik(value: object) -> str | None:
    """Digits only, leading zeros stripped; anything else (a ticker in ``CIK=``) None."""
    text = str(value or "").strip()
    if not text.isdigit() or len(text) > 10:
        return None
    stripped = text.lstrip("0")
    return stripped or None


def edgar_cik(url: str | None) -> str | None:
    """The registrant CIK an EDGAR URL names, or None when it names none (or several).

    Never the accession number's prefix: that is the FILING AGENT's CIK (0001193125 is a
    printer that files for hundreds of issuers), not the registrant's.
    """
    if not is_sec_host(url):
        return None
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return None
    query = parse_qs(parts.query)
    paths = [parts.path or ""]
    paths.extend(unquote(v) for v in query.get("doc", []))
    for path in paths:
        found = _DATA_PATH_RE.search(path) or _CIK_FILE_RE.search(path)
        if found:
            return normalise_cik(found.group(1))
    lowered = {k.lower(): v for k, v in query.items()}
    for key in _CIK_QUERY_KEYS:
        values = [c for raw in lowered.get(key, []) for c in raw.split(",") if c.strip()]
        ciks = {normalise_cik(v) for v in values}
        if len(ciks) == 1 and None not in ciks:
            return next(iter(ciks))
    return None


def registrant_verdict(
    url: str | None,
    subject_ciks: Iterable[str],
    *,
    subject_is_sec_registrant: bool,
) -> str | None:
    """Whose filing the page at ``url`` is, judged against the subject. None when the
    URL is not an EDGAR filing page (the rule does not apply).

    * the URL's CIK is one of the subject's → ``own``;
    * the subject is not an SEC registrant at all (no ADR-075 basis) and the URL names a
      registrant → ``other``: whoever filed it, it was not the subject;
    * the URL's CIK is known and the subject's are known and differ → ``other``;
    * no CIK in the URL, or an SEC subject whose CIK could not be resolved →
      ``unidentified``: never the issuer's filing, and not dropped either.
    """
    if not is_edgar_filing_url(url):
        return None
    cik = edgar_cik(url)
    if cik is None:
        return EDGAR_UNIDENTIFIED
    own = {c for c in (normalise_cik(v) for v in subject_ciks) if c}
    if cik in own:
        return EDGAR_OWN
    if not subject_is_sec_registrant:
        return EDGAR_OTHER
    if not own:
        return EDGAR_UNIDENTIFIED
    return EDGAR_OTHER


__all__ = [
    "EDGAR_OTHER",
    "EDGAR_OWN",
    "EDGAR_UNIDENTIFIED",
    "EDGAR_VERDICTS",
    "edgar_cik",
    "is_edgar_filing_url",
    "is_sec_host",
    "normalise_cik",
    "registrant_verdict",
]
