"""The query sanitiser — open-web W1 (threat model §5, QI-01…QI-06; spec §4, §24 G1).

Every string that could reach a web search provider passes through here twice: once
when the planner builds it (:func:`sanitise_query`, which cleans) and once at the
orchestrator's gate (:func:`validate_outgoing_query`, which only judges). Cleaning and
judging are separate so the gate can never "fix" a query into something the planner did
not produce.

WHAT IT DOES
============
* **QI-01 — operators.** Every search operator in free text is stripped (``site:``,
  ``inurl:``, ``filetype:``, ``cache:``, ``related:`` …). The only operators that reach a
  provider are the ones the *planner* passes in explicitly: ``site:<domain>`` for
  planner-selected domains and ``filetype:pdf``.
* **QI-02 — URLs.** URLs in the text are pulled out and returned separately (they belong
  to the user-supplied-URL path and the fetch policy, spec §21). They are never sent as
  query text.
* **QI-03 — private tokens (rule G1).** A query containing any token from the run's
  private-token set (portfolio holdings, uploaded-document phrases, user identity) is
  **refused** with ``G1_private_token``. Nothing is redacted and sent anyway: a query with
  the private part removed is a different query the user never asked for.
* **QI-05 — length.** More than :data:`MAX_QUERY_CHARS` characters is refused.
* **QI-06 — blocklist.** A small list of illegal-content phrases refuses the query.
* **Evasion (review S4).** Invisible and private-use characters (Unicode categories
  Cf/Co/Cs, e.g. a zero-width space or a soft hyphen inside a name) refuse the query.
  Private-token matching runs on an NFKC + case-folded + confusable-skeleton form of
  both sides, so full-width ``ＡＡＰＬ`` and a Cyrillic ``Аcme`` still match, and ``_`` is
  a word boundary so ``AAPL_Q3`` still contains ``AAPL``.
* **Filters (review S5).** Domain filters must be plain hostnames, country and language
  must be ISO codes, and every filter value is checked against the private tokens too.

QI-04 (instructions aimed at private systems) is structural rather than textual: a
query only ever goes to the configured provider, whose host is fixed by the adapter's
allowlist. It is tested there.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Collection, Iterable
from dataclasses import dataclass

#: Threat model QI-05.
MAX_QUERY_CHARS = 400

REFUSAL_EMPTY = "empty_query"
REFUSAL_TOO_LONG = "query_too_long"
REFUSAL_PRIVATE_TOKEN = "G1_private_token"
REFUSAL_BLOCKLIST = "blocklisted_term"
REFUSAL_URL_IN_QUERY = "url_in_query"
REFUSAL_OPERATOR = "operator_not_allowed"
REFUSAL_BAD_SITE = "site_domain_invalid"
REFUSAL_INVISIBLE_CHAR = "invisible_char"
REFUSAL_FILTER_INVALID = "filter_invalid"

REFUSAL_CODES: frozenset[str] = frozenset(
    {
        REFUSAL_EMPTY,
        REFUSAL_TOO_LONG,
        REFUSAL_PRIVATE_TOKEN,
        REFUSAL_BLOCKLIST,
        REFUSAL_URL_IN_QUERY,
        REFUSAL_OPERATOR,
        REFUSAL_BAD_SITE,
        REFUSAL_INVISIBLE_CHAR,
        REFUSAL_FILTER_INVALID,
    }
)

#: QI-06. Deliberately small and phrase-level: the planner builds queries from closed
#: vocabularies, so this is a backstop against free text, not a content classifier.
BLOCKLIST: tuple[str, ...] = (
    "child sexual",
    "csam",
    "child porn",
    "bomb making",
    "make a bomb",
    "build a bomb",
    "nerve agent synthesis",
    "buy stolen credit card",
    "stolen credit cards",
    "credit card dumps",
    "hire a hitman",
    "ransomware kit",
    "buy ransomware",
)

_URL_RE = re.compile(
    r"(?i)\b(?:(?:https?|ftp|file)://[^\s<>\"']+|www\.[^\s<>\"']+)"
)
#: ``name:value`` operators, known and unknown. Letters only before the colon, so a
#: ratio like ``Q1:2025`` or a time ``10:30`` is not an operator; ``word: text`` (with a
#: space) is prose, not an operator.
_OPERATOR_RE = re.compile(r"(?i)(?<![\w.])([a-z][a-z_]{1,19}):(?!//)(\"[^\"]*\"|\S+)")
_LEADING_SIGN_RE = re.compile(r"(?<!\S)[-+](?=\S)")
_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")
_DOMAIN_RE = re.compile(
    r"^(?=.{4,253}$)(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$"
)
#: The planner operators the outgoing gate accepts, and nothing else.
_ALLOWED_OUTGOING_OPERATOR_RE = re.compile(r"(?i)^(site:[a-z0-9.-]+|filetype:pdf)$")


#: Format (zero-width, soft hyphen, bidi controls), private-use and surrogate code
#: points: they render as nothing and exist in a query only to defeat matching.
_INVISIBLE_CATEGORIES: frozenset[str] = frozenset({"Cf", "Co", "Cs"})

#: The common Cyrillic/Greek letters that render like Latin ones. Applied to BOTH sides
#: of the private-token comparison only — the outgoing query is never rewritten.
_CONFUSABLES = str.maketrans(
    {
        "а": "a", "в": "b", "е": "e", "к": "k", "м": "m", "н": "h", "о": "o",
        "р": "p", "с": "c", "т": "t", "у": "y", "х": "x", "і": "i", "ј": "j",
        "ѕ": "s", "ԁ": "d", "ɡ": "g", "ӏ": "l",
        "α": "a", "β": "b", "ε": "e", "ι": "i", "κ": "k", "μ": "m", "ν": "v",
        "ο": "o", "ρ": "p", "τ": "t", "υ": "u", "χ": "x", "ζ": "z", "η": "n",
    }
)
_ISO_COUNTRY_RE = re.compile(r"^[A-Za-z]{2}$")
_ISO_LANGUAGE_RE = re.compile(r"^[A-Za-z]{2,3}$")


def has_invisible_chars(text: str) -> bool:
    """True when ``text`` holds a Cf/Co/Cs code point (zero-width, soft hyphen, …)."""
    return any(unicodedata.category(ch) in _INVISIBLE_CATEGORIES for ch in text or "")


def fold(text: str) -> str:
    """The comparison form: NFKC, case-folded, confusable skeleton, whitespace collapsed."""
    visible = "".join(
        ch for ch in (text or "") if unicodedata.category(ch) not in _INVISIBLE_CATEGORIES
    )
    folded = unicodedata.normalize("NFKC", visible).casefold()
    folded = unicodedata.normalize("NFKC", folded).translate(_CONFUSABLES)
    return " ".join(folded.split())


@dataclass(frozen=True)
class SanitisedQuery:
    """The outcome of cleaning one query. ``text`` is what may be sent, if not refused."""

    text: str
    extracted_urls: tuple[str, ...] = ()
    stripped_operators: tuple[str, ...] = ()
    refusal: str | None = None

    @property
    def ok(self) -> bool:
        return self.refusal is None


def normalise_private_tokens(tokens: Iterable[str] | None) -> frozenset[str]:
    """Private tokens in :func:`fold` form; anything under 2 chars dropped."""
    out: set[str] = set()
    for token in tokens or ():
        norm = fold(str(token))
        if len(norm) >= 2:
            out.add(norm)
    return frozenset(out)


def _contains_phrase(haystack: str, phrase: str) -> bool:
    """Phrase match on folded text. Letters and digits bound a word; ``_`` does not."""
    pattern = r"(?<![^\W_])" + re.escape(phrase) + r"(?![^\W_])"
    return re.search(pattern, haystack) is not None


def find_private_token(text: str, private_tokens: Collection[str]) -> bool:
    """True when ``text`` contains any private token. Never says which (no echo)."""
    haystack = fold(text)
    return any(_contains_phrase(haystack, t) for t in normalise_private_tokens(private_tokens))


def find_blocklisted(text: str) -> bool:
    haystack = fold(text)
    return any(_contains_phrase(haystack, fold(phrase)) for phrase in BLOCKLIST)


def _clean_free_text(raw: str) -> tuple[str, tuple[str, ...], tuple[str, ...]]:
    # NFKC first (W5 review F3): a full-width ``ｓｉｔｅ：evil.com`` or ``filetype：env`` is the
    # same operator to a search engine and must be stripped like the ASCII spelling.
    text = _CONTROL_RE.sub(" ", unicodedata.normalize("NFKC", raw or ""))
    urls = tuple(m.group(0).rstrip(".,;:!?)]}") for m in _URL_RE.finditer(text))
    text = _URL_RE.sub(" ", text)
    operators = tuple(m.group(0) for m in _OPERATOR_RE.finditer(text))
    text = _OPERATOR_RE.sub(" ", text)
    text = _LEADING_SIGN_RE.sub("", text)
    text = " ".join(text.split())
    return text, urls, operators


def _valid_site_domain(domain: str) -> bool:
    return bool(_DOMAIN_RE.match((domain or "").strip().lower()))


def sanitise_query(
    raw: str,
    *,
    site_domains: Iterable[str] = (),
    filetype_pdf: bool = False,
    private_tokens: Collection[str] = (),
) -> SanitisedQuery:
    """Clean free text into a sendable query, appending only planner operators.

    ``site_domains`` and ``filetype_pdf`` are the planner's own operators (QI-01): they
    are appended after cleaning, and a ``site:`` domain must be a plain hostname.
    """
    if has_invisible_chars(raw or ""):
        return SanitisedQuery("", (), (), REFUSAL_INVISIBLE_CHAR)
    text, urls, operators = _clean_free_text(raw)
    planner_ops: list[str] = []
    for domain in site_domains:
        d = (domain or "").strip().lower()
        if not _valid_site_domain(d):
            return SanitisedQuery("", urls, operators, REFUSAL_BAD_SITE)
        planner_ops.append(f"site:{d}")
    if filetype_pdf:
        planner_ops.append("filetype:pdf")
    if not text:
        return SanitisedQuery("", urls, operators, REFUSAL_EMPTY)
    final = " ".join([text, *planner_ops])
    refusal = _judge(final, private_tokens)
    return SanitisedQuery(final if refusal is None else "", urls, operators, refusal)


def _judge(text: str, private_tokens: Collection[str]) -> str | None:
    if not text.strip():
        return REFUSAL_EMPTY
    if len(text) > MAX_QUERY_CHARS:
        return REFUSAL_TOO_LONG
    if find_private_token(text, private_tokens):
        return REFUSAL_PRIVATE_TOKEN
    if find_blocklisted(text):
        return REFUSAL_BLOCKLIST
    return None


def validate_outgoing_query(text: str, private_tokens: Collection[str] = ()) -> str | None:
    """The orchestrator's gate: ``None`` if ``text`` may leave the process, else a code.

    Judges and never rewrites. A URL, a control character or any operator other than
    ``site:<domain>`` / ``filetype:pdf`` means the text did not come from
    :func:`sanitise_query`, and it is refused rather than cleaned.
    """
    if _CONTROL_RE.search(text or ""):
        return REFUSAL_OPERATOR
    if has_invisible_chars(text or ""):
        return REFUSAL_INVISIBLE_CHAR
    # Judged on the NFKC form too (W5 review F3): a compatibility-form operator or URL
    # reads as the real thing once the vendor normalises it.
    normal = unicodedata.normalize("NFKC", text or "")
    if _URL_RE.search(normal):
        return REFUSAL_URL_IN_QUERY
    if normal != (text or "") and len(_OPERATOR_RE.findall(normal)) != len(
        _OPERATOR_RE.findall(text or "")
    ):
        # An operator that only exists once normalised was never produced by the planner
        # (which writes ASCII ``site:``/``filetype:``), even if its NFKC form looks valid.
        return REFUSAL_OPERATOR
    for match in _OPERATOR_RE.finditer(normal):
        if not _ALLOWED_OUTGOING_OPERATOR_RE.match(match.group(0)):
            return REFUSAL_OPERATOR
        if match.group(1).lower() == "site" and not _valid_site_domain(match.group(2)):
            return REFUSAL_BAD_SITE
    return _judge(text or "", private_tokens)


def valid_domain(domain: str) -> bool:
    """A plain lower-case-able hostname: no scheme, path, port, IP literal or wildcard."""
    return _valid_site_domain(domain)


def validate_filters(
    *,
    include_domains: Iterable[str] = (),
    exclude_domains: Iterable[str] = (),
    country: str | None = None,
    language: str | None = None,
    private_tokens: Collection[str] = (),
) -> str | None:
    """``None`` if the request's filters may leave the process, else a refusal code.

    Filters leave the process too (``include_domains`` is sent to the vendor), so they
    get the same rule G1 check as the query text.
    """
    include = [str(d) for d in include_domains]
    exclude = [str(d) for d in exclude_domains]
    values = [*include, *exclude, country or "", language or ""]
    if any(has_invisible_chars(v) or _CONTROL_RE.search(v) for v in values):
        return REFUSAL_INVISIBLE_CHAR
    if find_private_token(" ".join(values), private_tokens):
        return REFUSAL_PRIVATE_TOKEN
    if not all(_valid_site_domain(d.strip().lower()) for d in (*include, *exclude)):
        return REFUSAL_FILTER_INVALID
    if country is not None and not _ISO_COUNTRY_RE.match(country):
        return REFUSAL_FILTER_INVALID
    if language is not None and not _ISO_LANGUAGE_RE.match(language):
        return REFUSAL_FILTER_INVALID
    return None


__all__ = [
    "BLOCKLIST",
    "MAX_QUERY_CHARS",
    "REFUSAL_BAD_SITE",
    "REFUSAL_BLOCKLIST",
    "REFUSAL_CODES",
    "REFUSAL_EMPTY",
    "REFUSAL_FILTER_INVALID",
    "REFUSAL_INVISIBLE_CHAR",
    "REFUSAL_OPERATOR",
    "REFUSAL_PRIVATE_TOKEN",
    "REFUSAL_TOO_LONG",
    "REFUSAL_URL_IN_QUERY",
    "SanitisedQuery",
    "find_blocklisted",
    "find_private_token",
    "fold",
    "has_invisible_chars",
    "normalise_private_tokens",
    "sanitise_query",
    "valid_domain",
    "validate_filters",
    "validate_outgoing_query",
]
