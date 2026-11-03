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

import json
import posixpath
import re
from collections.abc import Collection, Iterable
from urllib.parse import parse_qs, unquote, urlsplit

from app.services.sources.publisher_tiers import publisher_tier
from app.services.sources.taxonomy import T1_PRIMARY_FILING

#: The URL's registrant IS the subject (its own CIK or ADR-075's verified sibling line).
EDGAR_OWN = "own"
#: The URL names another registrant.
EDGAR_OTHER = "other"
#: A filing page whose registrant cannot be matched: no CIK in the URL, a subject whose
#: CIK could not be resolved, or a CIK the SEC directory does not name.
EDGAR_UNIDENTIFIED = "unidentified"
#: Another registrant's filing that is ABOUT the subject: a holder's 13D/13G, an
#: insider's Form 3/4/5, an acquirer's 8-K naming it. A third party's filing, kept.
EDGAR_COUNTERPARTY = "counterparty"

EDGAR_VERDICTS: frozenset[str] = frozenset(
    {EDGAR_OWN, EDGAR_OTHER, EDGAR_UNIDENTIFIED, EDGAR_COUNTERPARTY}
)

_DATA_PATH_RE = re.compile(r"/archives/edgar/data/0*([0-9]{1,10})(?:/|$)", re.I)
_CIK_FILE_RE = re.compile(r"/CIK0*([0-9]{1,10})(?:\.json|/|$)", re.I)
_CIK_QUERY_KEYS = ("cik", "ciks")
#: Ownership forms, filed under the HOLDER's or the insider's CIK and about the issuer:
#: only EDGAR's own XSL rendering directories (``xslSCHEDULE_13G_X01/primary_doc.xml``,
#: ``xslF345X05/…``). A document NAME is the filer's choice — "exhibit-form-3-of-
#: agreement.htm" is not a Form 3 — so names are never read.
_OWNERSHIP_RE = re.compile(r"/xslschedule_13[dg](?:_x\d+)?/|/xslf345x0\d/", re.I)
#: A ticker counts only where an exchange (or an ownership form's issuer field) frames
#: it: "(NASDAQ: NET)", "NYSE: A", "Nasdaq: CLLS". A bare "NET" is "NET INCOME".
_EXCHANGE_CONTEXT = (
    r"(?i:\b(?:nasdaq(?:\s*(?:gs|gm|cm|global\s+(?:select\s+)?market|global\s+select|capital\s+market))?"
    r"|nyse(?:\s*(?:american|arca|mkt))?|amex|otc(?:qx|qb|\s+markets)?"
    r"|euronext(?:\s+[a-z]+)?|lse|asx|tsx|six)\s*:\s*)"
)
_ISSUER_SYMBOL_CONTEXT = r"(?i:issuertradingsymbol>\s*)"


def _clean_path(path: str) -> str:
    """Unquoted and normalised, so ``/data/1627281/../1234/`` reads as ``/data/1234``."""
    text = unquote(path or "")
    return posixpath.normpath(text) if text else ""


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
    paths = [_clean_path(parts.path or "")]
    paths.extend(_clean_path(v) for v in query.get("doc", []))
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


def is_ownership_filing_url(url: str | None) -> bool:
    """An EDGAR ownership filing (13D/13G, Form 3/4/5), by its document path."""
    if not is_edgar_filing_url(url):
        return False
    try:
        parts = urlsplit(url or "")
    except ValueError:
        return False
    paths = [_clean_path(parts.path or "")]
    paths.extend(_clean_path(v) for v in parse_qs(parts.query).get("doc", []))
    return any(_OWNERSHIP_RE.search(p) for p in paths)


_UNICODE_ESCAPE_RE = re.compile(r"\\u([0-9a-fA-F]{4})")


def _strings(text: str) -> str:
    """The string values of a JSON evidence record (so ``\\u00e8`` is ``è`` again), or
    the text itself when it is not JSON."""
    try:
        data = json.loads(text)
    except (TypeError, ValueError):
        # Evidence text is a JSON record cut at a fixed length, so a long one no longer
        # parses. Its ``\\uXXXX`` escapes still hide every non-ASCII letter ("Herm\\u00e8s"
        # would never match "Hermès"), so decode them here (ADR-079 review S1).
        return _UNICODE_ESCAPE_RE.sub(lambda m: chr(int(m.group(1), 16)), str(text or ""))
    out: list[str] = []
    stack = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, str):
            out.append(node)
        elif isinstance(node, dict):
            stack.extend(node.values())
        elif isinstance(node, list):
            stack.extend(node)
    return " ".join(reversed(out))


def _name_phrase(name: str | None) -> tuple[str, ...]:
    """A name as the words that identify it — legal suffixes, "the" and "group" dropped
    (``identity.normalised_name``). A single word must be distinctive: five letters or
    more and not a generic business word; else it identifies nobody."""
    from app.services.discovery.identity import _GENERIC_TOKENS, normalised_name

    words = tuple(normalised_name(name).split())
    if len(words) == 1 and (len(words[0]) < 5 or words[0] in _GENERIC_TOKENS):
        return ()
    return words


def filing_names_subject(
    text: str | None,
    names: Iterable[str],
    tickers: Iterable[str],
    *,
    ownership_form: bool = False,
) -> bool:
    """Does another registrant's filing NAME the subject? (ADR-079 review N2.)

    * its full name — every identifying word, in order ("Cellectis", "Electronic Arts",
      "Sage Therapeutics"); a one-word name must also appear capitalised, as a proper
      noun ("Cloudflare", never "cloudflare-hosted" prose about clouds);
    * or a ticker an exchange frames ("(NASDAQ: NET)", "NYSE: A"), or an ownership
      form's ``issuerTradingSymbol``.

    Never a bare ticker ("NET INCOME", "A summary…") and never a single word of a longer
    name ("electronic" for Electronic Arts, "equity" for Equity Commonwealth).
    """
    from app.services.discovery.identity import normalised_name

    raw = _strings(text or "")
    if not raw.strip():
        return False
    tokens = normalised_name(raw).split()
    for name in names:
        phrase = _name_phrase(name)
        if not phrase:
            continue
        n = len(phrase)
        if not any(tuple(tokens[i:i + n]) == phrase for i in range(len(tokens) - n + 1)):
            continue
        if n > 1:
            return True
        word = phrase[0]
        if re.search(
            rf"(?<![A-Za-z0-9])(?:{re.escape(word.capitalize())}|{re.escape(word.upper())})"
            r"(?![A-Za-z0-9])",
            raw,
        ):
            return True
    contexts = [_EXCHANGE_CONTEXT] + ([_ISSUER_SYMBOL_CONTEXT] if ownership_form else [])
    for ticker in tickers:
        symbol = str(ticker or "").strip().upper()
        if not symbol:
            continue
        for context in contexts:
            if re.search(rf"{context}{re.escape(symbol)}(?![A-Za-z0-9])", raw):
                return True
    return False


def registrant_verdict(
    url: str | None,
    own_ciks: Iterable[str],
    *,
    other_ciks: Collection[str] | None,
) -> str | None:
    """Whose filing the page at ``url`` is. None when the URL is not an EDGAR filing
    page (the rule does not apply).

    ``other_ciks`` is what the caller can POSITIVELY call someone else's:

    * ``None`` — the subject's own CIK is known (an ADR-075 basis with a CIK), so every
      other CIK is another registrant's. With no own CIK, nothing can be said;
    * a set — no SEC basis: only CIKs the SEC directory names as a DIFFERENT registrant
      are another's. A CIK the directory does not hold, or an unreadable directory
      (an empty set), is ``unidentified`` — never dropped on a guess.

    The verdict ``own`` needs the URL's CIK in ``own_ciks``; nothing else is the issuer's.
    """
    if not is_edgar_filing_url(url):
        return None
    cik = edgar_cik(url)
    if cik is None:
        return EDGAR_UNIDENTIFIED
    own = {c for c in (normalise_cik(v) for v in own_ciks) if c}
    if cik in own:
        return EDGAR_OWN
    if other_ciks is None:
        return EDGAR_OTHER if own else EDGAR_UNIDENTIFIED
    others = {c for c in (normalise_cik(v) for v in other_ciks) if c}
    return EDGAR_OTHER if cik in others else EDGAR_UNIDENTIFIED


__all__ = [
    "EDGAR_COUNTERPARTY",
    "EDGAR_OTHER",
    "EDGAR_OWN",
    "EDGAR_UNIDENTIFIED",
    "EDGAR_VERDICTS",
    "edgar_cik",
    "filing_names_subject",
    "is_edgar_filing_url",
    "is_ownership_filing_url",
    "is_sec_host",
    "normalise_cik",
    "registrant_verdict",
]
