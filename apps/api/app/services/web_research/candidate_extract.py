"""Listed-company mentions in fetched pages — open-web W6b (spec §4.4 wave 1, §16.1, §6.2).

WHAT THIS DOES
==============
Reads a fetched page's VISIBLE extracted text (paragraphs and table rows) and returns the
companies it names *as listed companies*: a name printed beside a ticker and a venue
(``Pensana plc (AIM: PRE)``, ``ASX:PSA``), beside a checksum-valid ISIN, in a table row
that carries both, or a legal-form name with the listing venue stated in the same
paragraph (``Foo Resources Ltd, listed on the ASX``). **Deterministic, regex-only, no model
and no network.** A page's words are data here: nothing in this module follows, executes
or even interprets an instruction in them, and the bounded caps below mean a hostile page
costs a bounded amount of CPU.

A MENTION IS A CLAIM, NEVER AN IDENTITY
=======================================
A name next to a ticker on a trade-press page is a *lead*. It admits nothing until
``discovery.identity.verify_identity`` finds it in the exchange's OWN directory (rule A2)
and, for admission, a passage ties it to the theme (rule A3). A bare name — a capitalised
phrase with no ticker, no ISIN and no venue — is not even a lead (``name_only`` never
admits, spec §16.1), and a name that is a common word needs a ticker.

THE A3 PASSAGE
==============
Every mention keeps the paragraph or table row it was found in (bounded) and the theme /
catalyst / downside terms that occur IN THAT SAME PASSAGE. Co-occurrence is paragraph- or
row-level only: a theme term three paragraphs away is not evidence the company is about
the theme.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.services.web_research.entities import COMMON_WORDS, fold

EXTRACTOR_VERSION = "w6b.1"

MAX_PARAGRAPHS = 600
MAX_PARAGRAPH_CHARS = 2_500
MAX_TABLE_ROWS = 400
MAX_MENTIONS_PER_PAGE = 80
MAX_PASSAGE_CHARS = 420
MAX_NAME_TOKENS = 6

PASSAGE_PARAGRAPH = "paragraph"
PASSAGE_TABLE_ROW = "table_row"

METHOD_TICKER_VENUE = "ticker_venue"
METHOD_ISIN = "isin"
METHOD_TABLE_ROW = "table_row"
METHOD_NAME_VENUE_CONTEXT = "name_venue_context"

# --------------------------------------------------------------------------- #
# Vocabulary (versioned data)
# --------------------------------------------------------------------------- #

#: Venue words as printed beside a ticker. Longest first so ``NYSE American`` beats
#: ``NYSE``. ``discovery.identity.normalise_venue`` turns these into registry codes.
VENUE_WORDS: tuple[str, ...] = tuple(
    sorted(
        {
            "NYSE American",
            "NYSE Arca",
            "NYSE",
            "NASDAQ Stockholm",
            "NASDAQ Copenhagen",
            "NASDAQ Helsinki",
            "NASDAQ First North",
            "Nasdaq First North",
            "First North",
            "NASDAQ",
            "Euronext Growth Oslo",
            "Euronext Growth Milan",
            "Euronext Growth Paris",
            "Euronext Growth",
            "Euronext Paris",
            "Euronext Amsterdam",
            "Euronext Brussels",
            "Euronext Milan",
            "Euronext Oslo",
            "Euronext Lisbon",
            "Euronext Dublin",
            "Euronext",
            "TSX Venture",
            "TSXV",
            "TSX-V",
            "TSX",
            "CSE",
            "ASX",
            "AIM",
            "LSE",
            "XETRA",
            "Xetra",
            "Frankfurt",
            "SIX",
            "OTCQX",
            "OTCQB",
            "OTC Pink",
            "OTC",
            "NZX",
            "HKEX",
            "TSE",
            "JSE",
            "STO",
            "CPH",
            "HEL",
            "OSL",
            "EPA",
            "BIT",
            "BME",
            "WSE",
            "NGM",
            "Spotlight",
            "Oslo Børs",
            "Oslo Bors",
            "Warsaw",
        },
        key=len,
        reverse=True,
    )
)
_VENUE_ALT = "|".join(re.escape(v) for v in VENUE_WORDS)

_TICKER = r"[A-Z0-9]{1,6}(?:\.[A-Z]{1,2})?"
#: ``(ASX: PSA)``, ``AIM:PRE``, ``[TSXV: ABC]``, ``Nasdaq First North Growth Market: XYZ``.
_TICKER_MENTION_RE = re.compile(
    rf"(?:(?:\(|\[|,)\s*)?(?:the\s+)?(?P<venue>(?i:{_VENUE_ALT})(?:\s+Growth\s+Market)?)"
    rf"\s*:\s*(?P<ticker>{_TICKER})(?![A-Za-z0-9])"
)
_ISIN_RE = re.compile(r"\bISIN\s*[:\-]?\s*(?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)\b")
_BARE_ISIN_RE = re.compile(r"\b(?P<isin>[A-Z]{2}[A-Z0-9]{9}\d)\b")

_LEGAL_FORMS = (
    r"(?:Pty\s+Ltd|Ltd\.?|Limited|PLC|plc|Inc\.?|Corp\.?|Corporation|AG|SA|S\.A\.|ASA|AB|"
    r"Oyj|NV|N\.V\.|SE|SpA|S\.p\.A\.|GmbH|A/S|AS|Holdings|Group)"
)
_NAME_WITH_FORM_RE = re.compile(rf"(?P<name>(?:[A-Z][\w&'’.\-]*\s+){{1,5}}{_LEGAL_FORMS})(?![\w])")
_VENUE_CONTEXT_RE = re.compile(
    rf"(?i:(?:listed|traded|trading|quoted)\s+(?:on|in)\s+(?:the\s+)?)"
    rf"(?P<venue>(?i:{_VENUE_ALT}))\b"
    rf"|(?P<venue2>(?i:{_VENUE_ALT}))[\s-]+(?i:listed)\b"
)

_CONNECTORS = frozenset(
    {
        "&",
        "of",
        "and",
        "de",
        "du",
        "van",
        "von",
        "la",
        "le",
        "del",
        "della",
        "di",
        "da",
        "des",
        "den",
        "af",
        "och",
        "og",
        "oy",
        "the",
    }
)
_BAD_LEADING = frozenset(
    {
        "the",
        "a",
        "an",
        "in",
        "on",
        "at",
        "as",
        "by",
        "for",
        "from",
        "with",
        "shares",
        "following",
        "after",
        "today",
        "yesterday",
        "recently",
        "meanwhile",
        "also",
        "however",
        "last",
        "this",
        "these",
        "those",
        "stock",
        "australian",
        "canadian",
        "british",
        "european",
        "announced",
        "said",
        "says",
        "company",
        "companies",
        "listed",
        "like",
        "such",
        "including",
        "and",
        "or",
        "but",
        "its",
        "their",
        "our",
        "both",
        "other",
        "see",
        "per",
    }
)
_BAD_NAMES = frozenset(
    {
        "company",
        "the company",
        "shares",
        "stock",
        "exchange",
        "announcement",
        "limited",
        "group",
        "holdings",
        "corporation",
        "ltd",
        "plc",
        "inc",
        "market",
        "markets",
    }
)
_LEGAL_FORM_WORDS = frozenset(
    {
        "pty",
        "ltd",
        "ltd.",
        "limited",
        "plc",
        "inc",
        "inc.",
        "corp",
        "corp.",
        "corporation",
        "ag",
        "sa",
        "s.a.",
        "asa",
        "ab",
        "oyj",
        "nv",
        "n.v.",
        "se",
        "spa",
        "s.p.a.",
        "gmbh",
        "a/s",
        "as",
        "holdings",
        "group",
    }
)

# --------------------------------------------------------------------------- #
# ISIN validation (Luhn over the letter-expanded digits)
# --------------------------------------------------------------------------- #

#: The primary listing venue (registry code) of an ISIN's country, for the countries
#: whose listings sit on ONE venue family. Anything else yields no venue.
ISIN_COUNTRY_VENUE: dict[str, str] = {
    "AU": "AU",
    "GB": "LSE",
    "US": "US",
    "FR": "PA",
    "DE": "XETRA",
    "IT": "MI",
    "ES": "MC",
    "SE": "ST",
    "DK": "CO",
    "NO": "OL",
    "FI": "HE",
    "NL": "AS",
    "BE": "BR",
    "CH": "SW",
    "PT": "LS",
    "AT": "VI",
    "PL": "WA",
    "IE": "IR",
}


def valid_isin(value: str) -> bool:
    """A 12-character ISIN with a correct check digit."""
    if not re.fullmatch(r"[A-Z]{2}[A-Z0-9]{9}\d", value or ""):
        return False
    digits = "".join(str(int(c, 36)) for c in value)
    total = 0
    for i, ch in enumerate(reversed(digits)):
        n = int(ch)
        if i % 2 == 1:
            n *= 2
            n = n - 9 if n > 9 else n
        total += n
    return total % 10 == 0


# --------------------------------------------------------------------------- #
# Theme vocabulary (A3's "theme term from the intent vocabulary")
# --------------------------------------------------------------------------- #

_RISK_TERMS: tuple[str, ...] = (
    "delay",
    "delayed",
    "lawsuit",
    "litigation",
    "profit warning",
    "going concern",
    "dilution",
    "impairment",
    "suspended",
    "suspension",
    "default",
    "insolvency",
    "write-down",
    "writedown",
    "downgrade",
    "shortfall",
    "cancelled",
    "canceled",
    "terminated",
    "investigation",
    "recall",
    "cost overrun",
    "cost overruns",
    "loss",
)
_CATALYST_TERMS: tuple[str, ...] = (
    "offtake",
    "permit",
    "permits",
    "permitting",
    "grant",
    "funding",
    "loan",
    "contract",
    "award",
    "awarded",
    "order",
    "orders",
    "backlog",
    "commissioning",
    "first production",
    "ramp-up",
    "approval",
    "approved",
    "acquisition",
    "expansion",
    "capacity",
    "partnership",
    "supply agreement",
    "feasibility study",
    "final investment decision",
    "financing",
)


def _stem(token: str) -> str:
    return (
        token[:-1] if len(token) > 4 and token.endswith("s") and not token.endswith("ss") else token
    )


def _tokens(text: str) -> list[str]:
    return [_stem(t) for t in re.findall(r"[^\W_]+", fold(text), re.UNICODE)]


@dataclass(frozen=True)
class ThemeVocabulary:
    """Phrases (folded) a passage may state to be about the theme.

    A phrase matches when its stemmed tokens occur contiguously in the passage's stemmed
    tokens, so ``transformers`` matches ``transformer`` and ``rare earth`` matches ``Rare
    Earths``. CJK text has no spaces, so a phrase with no ASCII letter matches as a
    substring.
    """

    phrases: tuple[str, ...] = ()

    def _compiled(self) -> list[tuple[str, tuple[str, ...]]]:
        return [(p, tuple(_tokens(p))) for p in self.phrases if p]

    def match(self, passage: str) -> tuple[str, ...]:
        passage_tokens = _tokens(passage)
        folded = fold(passage)
        found: list[str] = []
        for phrase, tokens in self._compiled():
            if not tokens:
                continue
            if not re.search(r"[a-z]", fold(phrase)):
                if phrase in passage or phrase in folded:
                    found.append(phrase)
                continue
            n = len(tokens)
            if any(
                tuple(passage_tokens[i : i + n]) == tokens
                for i in range(len(passage_tokens) - n + 1)
            ):
                found.append(phrase)
        return tuple(dict.fromkeys(found))


def terms_in(passage: str, terms: Iterable[str]) -> tuple[str, ...]:
    return ThemeVocabulary(tuple(terms)).match(passage)


# --------------------------------------------------------------------------- #
# Records
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class RawMention:
    """One listed-company mention and the passage it sits in."""

    name: str
    ticker: str | None
    venue_raw: str | None
    isin: str | None
    method: str
    passage: str
    passage_kind: str
    theme_terms: tuple[str, ...] = ()
    catalyst_terms: tuple[str, ...] = ()
    risk_terms: tuple[str, ...] = ()

    @property
    def key(self) -> str:
        """The identity this mention contributes to: ``venue:ticker`` else the folded name."""
        if self.ticker:
            return f"{fold(self.venue_raw or '')}:{self.ticker.upper()}"
        if self.isin:
            return f"isin:{self.isin}"
        return f"name:{fold(self.name)}"


@dataclass
class ExtractionStats:
    paragraphs: int = 0
    table_rows: int = 0
    mentions: int = 0
    name_only_dropped: int = 0
    rejected_names: int = 0
    truncated: bool = False
    notes: list[str] = field(default_factory=list)


# --------------------------------------------------------------------------- #
# Name extraction
# --------------------------------------------------------------------------- #


def _clean_token(token: str) -> str:
    return token.strip("\"'“”‘’()[]{}")


def _is_name_token(token: str) -> bool:
    t = _clean_token(token)
    if not t:
        return False
    if t.lower() in _CONNECTORS or t.lower() in _LEGAL_FORM_WORDS:
        return True
    return t[0].isupper() or t[0].isdigit()


def name_before(text: str, end: int) -> str | None:
    """The capitalised-token run that ends right before ``text[end]`` — the company name.

    Walks backwards token by token: a name token starts upper-case (or is a connector
    such as ``&`` / ``of`` / ``de``); a token ending a clause (``,`` ``;`` ``:``) ends the
    run, exclusive. At most :data:`MAX_NAME_TOKENS` tokens. Leading sentence-starters and
    connectors are trimmed.
    """
    before = text[:end].rstrip(" ([,–—-\t")
    tokens = before.split()
    run: list[str] = []
    for token in reversed(tokens):
        if run and token[-1:] in ",;:":
            break
        if any(ch in token for ch in "()[]{}|"):
            break
        if not _is_name_token(token):
            break
        run.append(token.rstrip(",;:"))
        if len(run) >= MAX_NAME_TOKENS:
            break
    run.reverse()
    while run and (run[0].lower() in _BAD_LEADING or run[0].lower() in _CONNECTORS):
        run.pop(0)
    while run and run[-1].lower() in _CONNECTORS:
        run.pop()
    name = " ".join(run).strip(" ,;:-–—")
    if name.endswith(".") and (name.split() or [""])[-1].lower() not in _LEGAL_FORM_WORDS:
        name = name.rstrip(".")
    return name or None


def _name_ok(name: str | None, *, has_identifier: bool) -> bool:
    if not name or len(name) < 3 or not re.search(r"[A-Za-z]", name):
        return False
    low = name.lower()
    if low in _BAD_NAMES:
        return False
    words = low.split()
    if all(w in _LEGAL_FORM_WORDS or w in _CONNECTORS for w in words):
        return False
    if not has_identifier and len(words) == 1 and fold(name) in COMMON_WORDS:
        return False
    return True


def _venue_text(raw: str) -> str:
    """``Nasdaq First North Growth Market`` -> ``Nasdaq First North`` (what the registry knows)."""
    return re.sub(r"\s+Growth\s+Market$", "", raw.strip(), flags=re.IGNORECASE)


def _passage(text: str, limit: int = MAX_PASSAGE_CHARS) -> str:
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _tag(
    passage: str, theme: ThemeVocabulary
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        theme.match(passage),
        terms_in(passage, _CATALYST_TERMS),
        terms_in(passage, _RISK_TERMS),
    )


# --------------------------------------------------------------------------- #
# Paragraphs
# --------------------------------------------------------------------------- #


def _paragraph_mentions(
    paragraph: str, theme: ThemeVocabulary, stats: ExtractionStats
) -> list[RawMention]:
    text = paragraph[:MAX_PARAGRAPH_CHARS]
    out: list[RawMention] = []
    taken: list[tuple[int, int]] = []
    passage_text: str | None = None

    def passage() -> str:
        nonlocal passage_text
        if passage_text is None:
            passage_text = _passage(text)
        return passage_text

    def emit(
        name: str | None,
        ticker: str | None,
        venue: str | None,
        isin: str | None,
        method: str,
        span: tuple[int, int],
    ) -> None:
        if not _name_ok(name, has_identifier=bool(ticker or isin)):
            stats.rejected_names += 1
            return
        # The stored passage is the PARAGRAPH around the mention, clipped around the name
        # so a long paragraph still shows it. The terms are read from the WHOLE paragraph:
        # co-occurrence is paragraph-level (spec §6.2 A3).
        whole_terms = _tag(text, theme)
        shown = passage()
        if name and name not in shown:
            at = text.find(name)
            if at >= 0:
                shown = _passage(text[max(0, at - 120) :], MAX_PASSAGE_CHARS)
        out.append(
            RawMention(
                name=name or "",
                ticker=ticker,
                venue_raw=venue,
                isin=isin,
                method=method,
                passage=shown,
                passage_kind=PASSAGE_PARAGRAPH,
                theme_terms=whole_terms[0],
                catalyst_terms=whole_terms[1],
                risk_terms=whole_terms[2],
            )
        )
        taken.append(span)

    for m in _TICKER_MENTION_RE.finditer(text):
        emit(
            name_before(text, m.start()),
            m.group("ticker"),
            _venue_text(m.group("venue")),
            None,
            METHOD_TICKER_VENUE,
            m.span(),
        )
    for m in _ISIN_RE.finditer(text):
        isin = m.group("isin")
        if not valid_isin(isin):
            continue
        emit(name_before(text, m.start()), None, None, isin, METHOD_ISIN, m.span())
    if not out:
        # A legal-form name with the listing venue stated in the same paragraph.
        venue_match = _VENUE_CONTEXT_RE.search(text)
        if venue_match is not None:
            venue = venue_match.group("venue") or venue_match.group("venue2")
            for nm in _NAME_WITH_FORM_RE.finditer(text):
                # The legal-form regex is the bound for this pattern.
                name = nm.group("name").strip(" ,;")
                words = name.split()
                while words and words[0].lower() in _BAD_LEADING:
                    words.pop(0)
                name = " ".join(words)
                emit(name, None, venue, None, METHOD_NAME_VENUE_CONTEXT, nm.span())
    return out


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

_NAME_HEADER = re.compile(r"company|name|issuer|business|firma|société|unternehmen", re.I)
_TICKER_HEADER = re.compile(r"ticker|symbol|code|epic|tidm|kürzel", re.I)
_VENUE_HEADER = re.compile(r"exchange|market|listing|venue|bourse|börse|boerse", re.I)


def _row_text(cells: Sequence[str]) -> str:
    return " | ".join(c.strip() for c in cells if c and c.strip())


def _table_mentions(
    rows: Sequence[Sequence[str]], theme: ThemeVocabulary, stats: ExtractionStats
) -> list[RawMention]:
    out: list[RawMention] = []
    if not rows:
        return out
    header = [str(c or "") for c in rows[0]]
    name_col = next((i for i, c in enumerate(header) if _NAME_HEADER.search(c)), None)
    ticker_col = next((i for i, c in enumerate(header) if _TICKER_HEADER.search(c)), None)
    venue_col = next((i for i, c in enumerate(header) if _VENUE_HEADER.search(c)), None)
    for row in rows[1:MAX_TABLE_ROWS]:
        cells = [str(c or "").strip() for c in row]
        if not any(cells):
            continue
        stats.table_rows += 1
        text = _row_text(cells)
        name: str | None = None
        ticker: str | None = None
        venue: str | None = None
        method = METHOD_TABLE_ROW
        # 1. A "VENUE: TICKER" cell, whatever the header says.
        for i, cell in enumerate(cells):
            m = _TICKER_MENTION_RE.fullmatch(cell.strip()) or _TICKER_MENTION_RE.search(cell)
            if m:
                ticker, venue = m.group("ticker"), _venue_text(m.group("venue"))
                method = METHOD_TICKER_VENUE
                for j in list(range(i - 1, -1, -1)) + list(range(i + 1, len(cells))):
                    candidate = cells[j]
                    if (
                        candidate
                        and re.search(r"[A-Za-z]", candidate)
                        and not (_TICKER_MENTION_RE.search(candidate))
                        and candidate[0].isupper()
                    ):
                        name = candidate
                        break
                break
        # 2. Header-mapped columns (Company | Exchange | Ticker).
        if ticker is None and name_col is not None and ticker_col is not None:
            if name_col < len(cells) and ticker_col < len(cells):
                raw_ticker = cells[ticker_col].strip()
                if re.fullmatch(_TICKER, raw_ticker):
                    ticker = raw_ticker
                    name = cells[name_col]
                    venue = (
                        cells[venue_col].strip()
                        if venue_col is not None and venue_col < len(cells)
                        else None
                    )
                    if not venue:
                        # ``AIM: PRE`` style combined cells are handled above; a bare
                        # ticker with no venue cannot be verified, so it is not a lead.
                        stats.name_only_dropped += 1
                        continue
        if not ticker:
            continue
        if not _name_ok(name, has_identifier=True):
            stats.rejected_names += 1
            continue
        t, c, r = _tag(text, theme)
        out.append(
            RawMention(
                name=(name or "").strip(),
                ticker=ticker,
                venue_raw=venue,
                isin=None,
                method=method,
                passage=_passage(text),
                passage_kind=PASSAGE_TABLE_ROW,
                theme_terms=t,
                catalyst_terms=c,
                risk_terms=r,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Public entry point
# --------------------------------------------------------------------------- #


def extract_mentions(
    paragraphs: Sequence[str],
    tables: Sequence[Sequence[Sequence[str]]] = (),
    theme: ThemeVocabulary | None = None,
) -> tuple[list[RawMention], ExtractionStats]:
    """Listed-company mentions in ``paragraphs`` and ``tables``. Bounded and deterministic."""
    vocabulary = theme or ThemeVocabulary()
    stats = ExtractionStats()
    found: list[RawMention] = []
    for paragraph in paragraphs[:MAX_PARAGRAPHS]:
        if not paragraph or not paragraph.strip():
            continue
        stats.paragraphs += 1
        found.extend(_paragraph_mentions(paragraph, vocabulary, stats))
        if len(found) >= MAX_MENTIONS_PER_PAGE:
            stats.truncated = True
            break
    if len(paragraphs) > MAX_PARAGRAPHS:
        stats.truncated = True
    for rows in tables:
        if len(found) >= MAX_MENTIONS_PER_PAGE:
            stats.truncated = True
            break
        found.extend(_table_mentions(rows, vocabulary, stats))
    found = found[:MAX_MENTIONS_PER_PAGE]
    stats.mentions = len(found)
    return found, stats


def paragraphs_of(text: str | None) -> list[str]:
    """Paragraphs of extracted main text (one per line break)."""
    return [p for p in re.split(r"\n+", text or "") if p.strip()]


__all__ = [
    "EXTRACTOR_VERSION",
    "ISIN_COUNTRY_VENUE",
    "PASSAGE_PARAGRAPH",
    "PASSAGE_TABLE_ROW",
    "ExtractionStats",
    "RawMention",
    "ThemeVocabulary",
    "extract_mentions",
    "name_before",
    "paragraphs_of",
    "terms_in",
    "valid_isin",
]
