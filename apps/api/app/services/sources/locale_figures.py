"""Read the figures of a non-English text in English notation (V3.20 slice 10.2).

The platform's figure checks (``ledger.claim_checks.figures``) read English notation
only: ``1,234.5`` and ``million``. A German annual report writes ``1.234,5 Mio. EUR``
and a Japanese one ``1兆2,340億円``. Read as English, the first is 1.2345 and the second
is nothing, so a finding that quotes a non-English source correctly would fail its
check, or a wrong one could pass.

``normalise_figures(text, language)`` rewrites each figure of ``text`` into English
notation and leaves every other character alone:

* decimal-comma languages (DE IT FR ES NL SV NO DA FI PL CS): ``1.234,5`` → ``1234.5``,
  ``1 234,5`` → ``1234.5``, ``0,85`` → ``0.85``, and the scale or percent word that
  follows (``Mio.``, ``Mrd.``, ``milliards``, ``mln``, ``tkr``, ``Prozent``…) → the
  English word;
* Japanese, Chinese, Korean: a compound of number and unit (``兆 億 万 百万 千``,
  ``조 억 만 백만 천``) → one value with an English scale word, written to the precision
  of its smallest unit (``12,340億`` → ``1234.0 billion``).

``normalise_translation_figures(text, source_language)`` reads an English machine
translation the way its source is written: the translation keeps the source's number
notation (``EUR 1.234 million``), so the source language's rules apply to it, and for
CJK the literal scale words (``2,340 hundred million``) are combined.

What it never does: guess. A single ``.`` followed by anything but three digits in a
decimal-comma text (``1.5``, a section ``3.2``) is left as written, and a date
(``31.12.2025``) is not a figure. The result is for CHECKING and for an English reader;
the original text stays the evidence.
"""

from __future__ import annotations

import math
import re
import unicodedata

#: Languages that write a decimal COMMA, in which a ``.`` (or a space) groups thousands.
DECIMAL_COMMA_LANGUAGES = frozenset(
    {"de", "it", "fr", "es", "nl", "sv", "no", "da", "fi", "pl", "cs"})
#: Of those, the ones that group with a plain space ("1 234 567").
_SPACE_GROUPING = frozenset({"fr", "sv", "no", "fi", "pl", "cs"})
CJK_LANGUAGES = frozenset({"ja", "zh", "ko"})

_SCALE_WORDS: dict[str, tuple[str, ...]] = {
    "million": (
        "mio", "millionen", "million", "millionen", "mill", "mln", "milioni", "milione",
        "millions", "millones", "millón", "millon", "miljoen", "miljoenen", "miljoner",
        "miljon", "millioner", "miljoonaa", "miljoona", "milj", "milionów", "miliony",
        "milion", "milionů", "milionu", "meur", "msek", "mnok", "mdkk", "mkr",
        "mchf", "mpln", "mczk", "m€",
    ),
    "billion": (
        "mrd", "mrd€", "milliarden", "milliarde", "miliardi", "miliardo", "mld", "mld€",
        "milliards", "milliard", "md", "mds", "md€", "millardos", "millardo",
        "mil millones", "miljard", "miljarden", "mdr", "miljarder", "milliarder", "mia",
        "miljardia", "miljardi", "miliardów", "miliardy", "miliard", "mdkr",
    ),
    "thousand": (
        "tsd", "tausend", "teur", "t€", "mila", "migliaia", "milliers", "k€", "miles",
        "duizend", "tkr", "tsek", "tnok", "tdkk", "tusen", "tusinde", "t.kr", "tuhatta",
        "tys", "tis", "tisíc", "keur",
    ),
    "percent": (
        "prozent", "pour cent", "por ciento", "per cento", "procent", "prosenttia",
        "procento", "procenta", "pct",
    ),
}
_WORD_TO_SCALE = {w: scale for scale, words in _SCALE_WORDS.items() for w in words}
_SCALE_RE = re.compile(
    r"[ \t\u00a0\u202f]?(?P<w>"
    + "|".join(re.escape(w) for w in sorted(_WORD_TO_SCALE, key=len, reverse=True))
    + r")(?P<dot>\.)?(?![\w€])",
    re.IGNORECASE,
)

#: A scale word with its currency written into it ("MSEK" = million SEK).
_MERGED_CURRENCY = {
    "meur": " EUR", "msek": " SEK", "mnok": " NOK", "mdkk": " DKK", "mkr": " SEK",
    "mchf": " CHF", "mpln": " PLN", "mczk": " CZK", "m€": " EUR", "mrd€": " EUR",
    "mld€": " EUR", "md€": " EUR", "teur": " EUR", "t€": " EUR", "k€": " EUR",
    "keur": " EUR", "tsek": " SEK", "tnok": " NOK", "tdkk": " DKK", "tkr": " SEK",
}

_GROUP_CHARS = ".\u00a0\u202f'\u2019"


def _number_re(language: str) -> re.Pattern[str]:
    group = re.escape(_GROUP_CHARS + (" " if language in _SPACE_GROUPING else ""))
    return re.compile(
        r"(?<![\w.,])"
        rf"(?P<num>\d{{1,3}}(?:[{group}]\d{{3}})+(?:,\d+)?|\d+,\d+|\d+)"
        r"(?![\w]|[.,]\d)"
    )


_NUMBER_RES = {lang: _number_re(lang) for lang in DECIMAL_COMMA_LANGUAGES}


def _english_number(raw: str) -> str:
    body = re.sub(rf"[{re.escape(_GROUP_CHARS)} ]", "", raw)
    return body.replace(",", ".")


def _latin(text: str, language: str) -> str:
    out: list[str] = []
    pos = 0
    for m in _NUMBER_RES[language].finditer(text):
        out.append(text[pos:m.start()])
        out.append(_english_number(m.group("num")))
        pos = m.end()
        scale = _SCALE_RE.match(text, pos)
        if scale is not None:
            word = scale.group("w").lower()
            out.append(" " + _WORD_TO_SCALE[word] + _MERGED_CURRENCY.get(word, ""))
            # An abbreviation's own dot ("Mio.") goes with it; a sentence's full stop
            # after a whole word ("12 pour cent.") stays.
            pos = scale.end() if len(word) <= 4 or not scale.group("dot") else scale.end() - 1
    out.append(text[pos:])
    return "".join(out)


#: CJK units, longest first so 百万 is read before 万.
_CJK_UNITS: dict[str, float] = {
    "兆": 1e12, "千万": 1e7, "百万": 1e6, "億": 1e8, "亿": 1e8, "萬": 1e4, "万": 1e4,
    "千": 1e3,
    "조": 1e12, "천만": 1e7, "백만": 1e6, "억": 1e8, "만": 1e4, "천": 1e3,
}
_EN_UNITS: dict[str, float] = {
    "trillion": 1e12, "hundred million": 1e8, "ten thousand": 1e4, "billion": 1e9,
    "million": 1e6, "thousand": 1e3,
}


def _compound_re(units: dict[str, float], sep: str, before: str) -> re.Pattern[str]:
    alt = "|".join(re.escape(u) for u in sorted(units, key=len, reverse=True))
    part = rf"\d{{1,3}}(?:,\d{{3}})+(?:\.\d+)?{sep}(?:{alt})|\d+(?:\.\d+)?{sep}(?:{alt})"
    return re.compile(rf"(?<![{before}.,])(?:{part})(?:[ \t]?(?:and[ \t])?(?:{part}))*"
                      + (r"(?![a-z])" if sep.endswith("]") else ""), re.IGNORECASE)


# CJK has no spaces between words, so only a DIGIT may not precede a figure there.
_CJK_RE = _compound_re(_CJK_UNITS, r"[ \t]?", r"\d")
_EN_RE = _compound_re(_EN_UNITS, r"[ \t]", r"\w")


def _compound_value(text: str, units: dict[str, float]) -> tuple[float, float] | None:
    alt = "|".join(re.escape(u) for u in sorted(units, key=len, reverse=True))
    total, precision = 0.0, math.inf
    for m in re.finditer(rf"(\d[\d,]*(?:\.\d+)?)[ \t]?({alt})", text, re.IGNORECASE):
        raw, unit = m.group(1).replace(",", ""), units[m.group(2).lower()]
        decimals = len(raw.split(".", 1)[1]) if "." in raw else 0
        total += float(raw) * unit
        precision = min(precision, unit * 10 ** -decimals)
    return (total, precision) if precision != math.inf else None


def _write(value: float, precision: float) -> str:
    for scale, word in ((1e9, "billion"), (1e6, "million"), (1e3, "thousand")):
        if value >= scale:
            break
    else:
        scale, word = 1.0, ""
    decimals = min(6, max(0, math.ceil(round(math.log10(scale / precision), 9))))
    number = f"{value / scale:.{decimals}f}"
    return f"{number} {word}".rstrip()


def _compound(text: str, pattern: re.Pattern[str], units: dict[str, float]) -> str:
    def repl(m: re.Match[str]) -> str:
        read = _compound_value(m.group(0), units)
        if read is None:
            return m.group(0)
        before = m.string[m.start() - 1:m.start()] if m.start() else ""
        after = m.string[m.end():m.end() + 1]
        # "は1234.0 billion円" has no word boundaries, and the English figure reader
        # needs them on both sides.
        return ((" " if before and (before.isalnum() or before == "_") else "")
                + _write(*read)
                + (" " if after and (after.isalnum() or after == "_") else ""))

    return pattern.sub(repl, text)


def normalise_figures(text: str, language: str | None) -> str:
    """``text`` with its figures written in English notation (see the module doc).

    English, unknown or unsupported languages come back unchanged."""
    if not text or not language:
        return text or ""
    if language in DECIMAL_COMMA_LANGUAGES:
        return _latin(text, language)
    if language in CJK_LANGUAGES:
        return _compound(unicodedata.normalize("NFKC", text), _CJK_RE, _CJK_UNITS)
    return text


def normalise_translation_figures(text: str, source_language: str | None) -> str:
    """An English translation of a ``source_language`` text, its figures read the way
    the source writes them (the translation copies the source's numbers exactly)."""
    if not text or not source_language:
        return text or ""
    if source_language in DECIMAL_COMMA_LANGUAGES:
        return _latin(text, source_language)
    if source_language in CJK_LANGUAGES:
        return _compound(unicodedata.normalize("NFKC", text), _EN_RE, _EN_UNITS)
    return text


def number_tokens(text: str) -> list[str]:
    """Every number as written, separators included (``1.234,5``, ``12``)."""
    # NFKC folds a no-break space into a plain one, which would split "1 234" into two
    # numbers: characters are normalised one by one and no-break spaces kept as one kind.
    keep = "\u00a0\u202f"
    body = "".join(ch if ch in keep else unicodedata.normalize("NFKC", ch)
                   for ch in (text or "")).replace("\u202f", "\u00a0")
    return re.findall(r"\d+(?:[.,'\u2019\u00a0]\d+)*", body)


__all__ = ["CJK_LANGUAGES", "DECIMAL_COMMA_LANGUAGES", "normalise_figures",
           "normalise_translation_figures", "number_tokens"]
