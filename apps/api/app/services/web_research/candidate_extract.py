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
from functools import cached_property

from app.services.web_research.entities import COMMON_WORDS, fold

EXTRACTOR_VERSION = "w6b.1"

MAX_PARAGRAPHS = 600
MAX_PARAGRAPH_CHARS = 2_500
MAX_TABLE_ROWS = 400
#: Hostile-size bounds (security review B2): no cell, row or page may make the scan cost
#: more than a fixed amount, however large the document is.
MAX_CELL_CHARS = 500
MAX_ROW_CHARS = 2_500
MAX_TOTAL_CHARS = 150_000
#: A paragraph naming more listed companies than this is a LIST (a price table, a spam
#: block): it is not evidence about any one of them.
MAX_LISTED_PER_PARAGRAPH = 5
#: From this many listed mentions in one paragraph a theme term must sit in the SAME clause.
CLAUSE_MODE_MENTIONS = 3
MAX_WINDOW_CHARS = 600
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
    rf"(?:(?:\(|\[|,)\s*)?(?:the\s+)?(?<![A-Za-z0-9])"
    rf"(?P<venue>(?i:{_VENUE_ALT})(?:\s+Growth\s+Market)?)"
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
    rf"(?<![A-Za-z0-9])(?P<venue>(?i:{_VENUE_ALT}))\b"
    rf"|(?<![A-Za-z0-9])(?P<venue2>(?i:{_VENUE_ALT}))[\s-]+(?i:listed)\b"
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

#: Downside triggers: an EVENT, not a common word ("loss", "default" alone are not).
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
    "net loss",
    "event of default",
)
#: Catalyst triggers: a named EVENT. Generic words ("order", "capacity", "grant",
#: "funding", "approval") are deliberately absent: they name nothing by themselves.
_CATALYST_TERMS: tuple[str, ...] = (
    "offtake agreement",
    "offtake",
    "permit approved",
    "permitting",
    "final investment decision",
    "first production",
    "commissioning",
    "ramp-up",
    "feasibility study",
    "supply agreement",
    "purchase order",
    "contract award",
    "awarded a contract",
    "loan guarantee",
    "grant funding",
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
    substring. The phrases are compiled ONCE per vocabulary (not once per match).
    """

    phrases: tuple[str, ...] = ()

    @cached_property
    def _compiled(self) -> tuple[tuple[str, tuple[str, ...], bool], ...]:
        out = []
        for p in self.phrases:
            if not p:
                continue
            out.append((p, tuple(_tokens(p)), bool(re.search(r"[a-z]", fold(p)))))
        return tuple(out)

    def match(self, passage: str) -> tuple[str, ...]:
        passage = passage[:MAX_WINDOW_CHARS * 4]
        passage_tokens = _tokens(passage)
        folded = fold(passage)
        found: list[str] = []
        for phrase, tokens, ascii_phrase in self._compiled:
            if not tokens:
                continue
            if not ascii_phrase:
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


_CATALYST_VOCAB = ThemeVocabulary(_CATALYST_TERMS)
_RISK_VOCAB = ThemeVocabulary(_RISK_TERMS)


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
    list_paragraphs: int = 0
    chars_scanned: int = 0
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


def _without(text: str, name: str | None) -> str:
    """``text`` with every occurrence of the company's name replaced by a space."""
    return text.replace(name, " ") if name else text


def _tag(
    passage: str, theme: ThemeVocabulary
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        theme.match(passage),
        _CATALYST_VOCAB.match(passage),
        _RISK_VOCAB.match(passage),
    )


# --------------------------------------------------------------------------- #
# Paragraphs
# --------------------------------------------------------------------------- #


_SENT_END = re.compile(r"(?<=[.!?;])\s+(?=[A-Z0-9(\[])")


@dataclass
class _Pending:
    name: str
    ticker: str | None
    venue: str | None
    isin: str | None
    method: str
    start: int  # where the NAME starts
    end: int  # end of the identifier span


def _paragraph_mentions(
    paragraph: str, theme: ThemeVocabulary, stats: ExtractionStats
) -> list[RawMention]:
    """Mentions in one paragraph, each with the terms read from ITS OWN clause.

    A theme term counts for a mention only when it sits in the mention's sentence (or,
    when the paragraph names three or more listed companies, in the same clause — the text
    between its neighbours), with every company NAME masked out (a company called "Zeta
    Gallium" is not about gallium by its name). A paragraph naming more than
    :data:`MAX_LISTED_PER_PARAGRAPH` companies is a list, and is evidence about none.
    """
    text = paragraph[:MAX_PARAGRAPH_CHARS]
    pending: list[_Pending] = []

    def add(name: str | None, ticker: str | None, venue: str | None, isin: str | None,
            method: str, span: tuple[int, int]) -> None:
        if len(pending) >= MAX_MENTIONS_PER_PAGE:
            return
        if not _name_ok(name, has_identifier=bool(ticker or isin)):
            stats.rejected_names += 1
            return
        assert name is not None
        start = text.rfind(name, 0, span[0] + 1)
        pending.append(
            _Pending(name, ticker, venue, isin, method, start if start >= 0 else span[0], span[1])
        )

    for m in _TICKER_MENTION_RE.finditer(text):
        add(name_before(text, m.start()), m.group("ticker"), _venue_text(m.group("venue")),
            None, METHOD_TICKER_VENUE, m.span())
        if len(pending) >= MAX_MENTIONS_PER_PAGE:
            break
    for m in _ISIN_RE.finditer(text):
        isin = m.group("isin")
        if valid_isin(isin):
            add(name_before(text, m.start()), None, None, isin, METHOD_ISIN, m.span())
    if not pending:
        # A legal-form name with the listing venue stated in the same paragraph.
        venue_match = _VENUE_CONTEXT_RE.search(text)
        if venue_match is not None:
            venue = venue_match.group("venue") or venue_match.group("venue2")
            for nm in _NAME_WITH_FORM_RE.finditer(text):
                name = nm.group("name").strip(" ,;")
                words = name.split()
                while words and words[0].lower() in _BAD_LEADING:
                    words.pop(0)
                add(" ".join(words), None, venue, None, METHOD_NAME_VENUE_CONTEXT, nm.span())
                if len(pending) >= MAX_MENTIONS_PER_PAGE:
                    break
    if not pending:
        return []

    pending.sort(key=lambda p: p.start)
    cuts = [0] + [m.end() for m in _SENT_END.finditer(text)] + [len(text)]
    is_list = len({fold(p.name) for p in pending}) > MAX_LISTED_PER_PARAGRAPH
    if is_list:
        stats.list_paragraphs += 1
    clause_mode = len(pending) >= CLAUSE_MODE_MENTIONS
    names = [p.name for p in pending]
    out: list[RawMention] = []
    for i, p in enumerate(pending):
        lo = max((c for c in cuts if c <= p.start), default=0)
        hi = min((c for c in cuts if c >= p.end), default=len(text))
        if clause_mode:
            # The text after this mention up to the next one's name: never a neighbour's
            # own clause.
            # Only the FIRST mention may use the lead-in before it ("Gallium producers
            # include Foo, ..."); every later one has only its own tail.
            before = text[lo : p.start] if i == 0 else ""
            after_to = pending[i + 1].start if i + 1 < len(pending) else hi
            window = before + " " + text[p.end : min(hi, after_to)]
        else:
            window = text[lo:hi]
        window = window[:MAX_WINDOW_CHARS]
        for other in names:
            window = _without(window, other)
        theme_terms, catalyst_terms, risk_terms = (
            ((), (), ()) if is_list else _tag(window, theme)
        )
        shown = _passage(text[lo:hi] if hi - lo >= 40 else text[max(0, p.start - 120) :])
        out.append(
            RawMention(
                name=p.name, ticker=p.ticker, venue_raw=p.venue, isin=p.isin, method=p.method,
                passage=shown, passage_kind=PASSAGE_PARAGRAPH, theme_terms=theme_terms,
                catalyst_terms=catalyst_terms, risk_terms=risk_terms,
            )
        )
    return out


# --------------------------------------------------------------------------- #
# Tables
# --------------------------------------------------------------------------- #

_NAME_HEADER = re.compile(r"company|name|issuer|business|firma|société|unternehmen", re.I)
_TICKER_HEADER = re.compile(r"ticker|symbol|code|epic|tidm|kürzel", re.I)
_VENUE_HEADER = re.compile(r"exchange|market|listing|venue|bourse|börse|boerse", re.I)


def _row_text(cells: Sequence[str]) -> str:
    return " | ".join(c.strip() for c in cells if c and c.strip())[:MAX_ROW_CHARS]


def _table_mentions(
    rows: Sequence[Sequence[str]],
    theme: ThemeVocabulary,
    stats: ExtractionStats,
    *,
    room: int = MAX_MENTIONS_PER_PAGE,
    budget: list[int] | None = None,
) -> list[RawMention]:
    out: list[RawMention] = []
    if not rows:
        return out
    header = [str(c or "")[:MAX_CELL_CHARS] for c in rows[0]]
    name_col = next((i for i, c in enumerate(header) if _NAME_HEADER.search(c)), None)
    ticker_col = next((i for i, c in enumerate(header) if _TICKER_HEADER.search(c)), None)
    venue_col = next((i for i, c in enumerate(header) if _VENUE_HEADER.search(c)), None)
    # A table with no header row keeps its first row as DATA (a venue:ticker cell needs
    # no header).
    first = 1 if (name_col is not None or ticker_col is not None) else 0
    for row in rows[first:MAX_TABLE_ROWS]:
        if len(out) >= room or (budget is not None and budget[0] <= 0):
            stats.truncated = True
            break
        # Every cell and the row are bounded BEFORE any pattern runs on them.
        cells = [str(c or "").strip()[:MAX_CELL_CHARS] for c in row[:40]]
        if not any(cells):
            continue
        stats.table_rows += 1
        text = _row_text(cells)
        if budget is not None:
            budget[0] -= len(text)
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
        t, c, r = _tag(_without(text, name), theme)
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
    """Listed-company mentions in ``paragraphs`` and ``tables``.

    Bounded in work, not just in output: at most :data:`MAX_TOTAL_CHARS` characters of a
    page are scanned (each paragraph clipped to :data:`MAX_PARAGRAPH_CHARS`, each table cell
    to :data:`MAX_CELL_CHARS`, each row to :data:`MAX_ROW_CHARS`), whatever the page size.
    """
    vocabulary = theme or ThemeVocabulary()
    stats = ExtractionStats()
    found: list[RawMention] = []
    budget = [MAX_TOTAL_CHARS]
    for paragraph in paragraphs[:MAX_PARAGRAPHS]:
        if not paragraph or not paragraph.strip():
            continue
        if budget[0] <= 0 or len(found) >= MAX_MENTIONS_PER_PAGE:
            stats.truncated = True
            break
        clipped = paragraph[:MAX_PARAGRAPH_CHARS]
        budget[0] -= len(clipped)
        stats.paragraphs += 1
        found.extend(_paragraph_mentions(clipped, vocabulary, stats))
    if len(paragraphs) > MAX_PARAGRAPHS:
        stats.truncated = True
    for rows in tables:
        if len(found) >= MAX_MENTIONS_PER_PAGE or budget[0] <= 0:
            stats.truncated = True
            break
        found.extend(
            _table_mentions(rows, vocabulary, stats, room=MAX_MENTIONS_PER_PAGE - len(found),
                            budget=budget)
        )
    found = found[:MAX_MENTIONS_PER_PAGE]
    stats.mentions = len(found)
    stats.chars_scanned = MAX_TOTAL_CHARS - max(budget[0], 0)
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
