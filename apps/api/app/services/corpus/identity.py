"""What makes two retrievals the same *logical* document — V3.1 Slice 1.2.

THE QUESTION THIS MODULE ANSWERS
===============================
A content hash identifies bytes. It cannot answer "is this the same document I
already have?", because a restated annual report, a re-typeset PDF and the same
report served from a different CDN path all have different bytes while being, to
a reader, the same document.

    ResearchDocument           "Pandora Annual Report 2025"   ← this module
    ResearchDocumentVersion    one retrieval of it, one hash  ← content_hash

Getting this wrong in either direction has a cost, and they are not symmetric:

* **Splitting** one document into two produces a duplicate in the corpus. Search
  returns the same page twice; a reader sees a redundant citation. Annoying.
* **Merging** two documents into one attributes one report's pages to another
  report's identity — the same class of error as entity mis-resolution, and it
  ends with a figure cited to a document it never appeared in.

So the rule is: **claim identity only where the document itself states it.**
A document that names its kind and its period ("annual report", "FY2025") has
declared what it is, and a later retrieval of that same kind and period for that
same company is the same document. A document that states neither falls back to
its canonical URL, which asserts nothing beyond "this is the same address" — and
two different addresses stay two documents rather than being merged on a guess.

WHY THE URL FALLBACK IS HASHED
==============================
The key is stored in a bounded column and compared for equality; a 2,000-character
URL is neither. Hashing the *canonicalised* URL (credential-stripped, percent-
encoding normalised — the same canonicalisation the citation layer already
applies) gives a fixed-width key while keeping the property that matters: the same
published address maps to the same document, and a signed-token variant of it
does not become a second one.
"""

from __future__ import annotations

import hashlib
import re

from app.services.sources.document_discovery import (
    DOC_KIND_ANNUAL_REPORT,
    DOC_KIND_INTERIM_REPORT,
    DOC_KIND_OTHER,
    DOC_KIND_PRESENTATION,
    DOC_KIND_RESULTS_RELEASE,
)
from app.services.sources.redaction import canonicalize_source_url

#: The document kinds the discovery layer already classifies, reused verbatim
#: rather than reinvented. A corpus that used its own vocabulary here would need a
#: translation table nobody would keep current.
DOCUMENT_TYPES: frozenset[str] = frozenset(
    {
        DOC_KIND_ANNUAL_REPORT,
        DOC_KIND_INTERIM_REPORT,
        DOC_KIND_RESULTS_RELEASE,
        DOC_KIND_PRESENTATION,
        DOC_KIND_OTHER,
    }
)

#: Prefix marking a key that asserts nothing beyond "the same published address".
URL_KEY_PREFIX = "url:"

_DOCUMENT_KEY_MAX = 120


# --------------------------------------------------------------------------- #
# The annual-document period, and why it is derived HERE rather than in
# ``document_period``
# --------------------------------------------------------------------------- #
#
# ``document_period.detect_document_period`` deliberately refuses a bare year. It
# exists to stop a quarterly release's headline being stamped as ANNUAL revenue,
# and its own docstring notes that "no period stated" is "the common case for an
# annual report". That refusal is right for a FACT: a bare "2025" beside a figure
# is exactly the guess ``financial_period.parse_period`` declines to make.
#
# But a document whose own title says "Annual Report 2025" has stated what it
# covers, in words, on its cover. Refusing to read that leaves every annual report
# with a URL-derived identity — which means a restated annual report at a new URL
# becomes a second document rather than a second version, and "version, not
# overwrite" never fires for the single most important document class in the
# corpus.
#
# So the rule below is deliberately narrow:
#
#   * it requires an ANNUAL-DOCUMENT PHRASE adjacent to the year — "annual report
#     2025", "2025 universal registration document" — never a loose four-digit
#     number anywhere in a filename;
#   * it refuses when two different years match, because an ambiguous cover is not
#     evidence;
#   * it is CORPUS-ONLY. It reaches document identity and the corpus period
#     columns, and it never touches ``extracted_fact_validator``,
#     ``financial_period`` or any fact's period. A test asserts that.
#
# The blast radius of being wrong here is therefore a mis-filed document, not a
# mis-dated figure.

#: Recorded in ``period_basis`` so this derivation is always distinguishable from
#: ``document_period``'s own bases in the audit trail.
BASIS_ANNUAL_TITLE_LABEL = "annual_title_label"

PERIOD_TYPE_ANNUAL = "annual"
PERIOD_TYPE_SPLIT_YEAR = "split_year"

_MIN_YEAR = 1990
_MAX_YEAR = 2100

#: An issuer's own name for its yearly report. Up to three intervening words
#: covers "annual and sustainability report" and "annual integrated report"
#: without stretching to unrelated sentences.
_ANNUAL_PHRASE = (
    r"(?:annual(?:\s+\w+){0,3}?\s+report"
    r"|universal\s+registration\s+document"
    r"|integrated\s+report"
    r"|annual\s+review"
    r"|rapport\s+annuel"
    r"|geschaeftsbericht"
    r"|jahresbericht)"
)
_YEAR = r"((?:19|20)\d{2}(?:/\d{2})?)"

_PHRASE_THEN_YEAR = re.compile(rf"{_ANNUAL_PHRASE}\s+(?:for\s+)?(?:fy\s*)?{_YEAR}")
_YEAR_THEN_PHRASE = re.compile(rf"{_YEAR}\s+{_ANNUAL_PHRASE}")

#: A split fiscal year — "2025/26", "2025-26" — but never a RANGE like
#: "2025-2026", which is two years rather than one period.
_SPLIT_YEAR_JOIN = re.compile(r"(?<=\d{4})[/\-](?=\d{2}(?!\d))")
_NON_TEXT = re.compile(r"[^a-z0-9/]+")


def _normalize_for_period(text: str | None) -> str:
    """Lower-case, separator-collapsed text that still distinguishes ``2025/26``."""
    if not text:
        return ""
    lowered = str(text).lower()
    # Join a split fiscal year FIRST, while its separator is still visible.
    joined = _SPLIT_YEAR_JOIN.sub("/", lowered)
    return _NON_TEXT.sub(" ", joined).strip()


def annual_period_from(
    *, title: str | None, url: str | None
) -> tuple[str | None, str | None, str | None]:
    """``(period_key, period_type, basis)`` when a document names its own year.

    Returns ``(None, None, None)`` unless an annual-document phrase sits directly
    beside a year, and also when two candidate years disagree — an ambiguous cover
    is not evidence, and picking one would be the guess this whole layer refuses.
    """
    years: set[str] = set()
    for source in (_normalize_for_period(title), _normalize_for_period(url)):
        if not source:
            continue
        for pattern in (_PHRASE_THEN_YEAR, _YEAR_THEN_PHRASE):
            for match in pattern.finditer(source):
                raw = match.group(1)
                head = int(raw[:4])
                if _MIN_YEAR <= head <= _MAX_YEAR:
                    years.add(raw)
    if len(years) != 1:
        return None, None, None
    found = years.pop()
    if "/" in found:
        return found, PERIOD_TYPE_SPLIT_YEAR, BASIS_ANNUAL_TITLE_LABEL
    return found, PERIOD_TYPE_ANNUAL, BASIS_ANNUAL_TITLE_LABEL


def normalize_document_type(raw: str | None) -> str:
    """A recognised document kind, or ``other``. Never a guess dressed as a fact."""
    value = (raw or "").strip().lower()
    return value if value in DOCUMENT_TYPES else DOC_KIND_OTHER


def document_key_for(
    *,
    document_type: str | None,
    period_key: str | None,
    canonical_url: str | None,
) -> str:
    """The stable logical identity of one document, within one company.

    ``"<kind>:<period>"`` when the document states both — e.g.
    ``annual_report:2025``, ``interim_report:2026-H1`` — so a restatement
    retrieved next year becomes a new *version* of the same document rather than a
    second document.

    ``"url:<digest>"`` otherwise. The document did not say what it is or what it
    covers, so the only honest claim left is that this is the same address.
    """
    kind = normalize_document_type(document_type)
    period = (period_key or "").strip()
    if period and kind != DOC_KIND_OTHER:
        return f"{kind}:{period}"[:_DOCUMENT_KEY_MAX]
    canonical = canonicalize_source_url(canonical_url) or (canonical_url or "")
    digest = hashlib.sha256(canonical.encode("utf-8", "replace")).hexdigest()
    return f"{URL_KEY_PREFIX}{digest[:40]}"


def is_url_derived_key(document_key: str | None) -> bool:
    """True when the identity is address-based rather than declared by the document.

    Worth knowing at a call site: a URL-derived key means the corpus could not
    determine what this document is, which is a research gap rather than a fact
    about the document.
    """
    return bool(document_key) and (document_key or "").startswith(URL_KEY_PREFIX)


__all__ = [
    "BASIS_ANNUAL_TITLE_LABEL",
    "DOCUMENT_TYPES",
    "PERIOD_TYPE_ANNUAL",
    "PERIOD_TYPE_SPLIT_YEAR",
    "URL_KEY_PREFIX",
    "annual_period_from",
    "document_key_for",
    "is_url_derived_key",
    "normalize_document_type",
]
