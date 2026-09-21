"""A ratio of two statement lines that the evidence does not contain — V3.18.1.

WHAT WENT WRONG
===============
A live council wrote *"net income … approximately 88.4% of net operating cash flow"* and,
two agents later, read the fall from 113.4% as **weakening cash conversion**. Neither
figure is anywhere in the evidence pack. The model divided one evidence item by another,
chose which went on top, and then chose what the result meant — and on the conventional
definition (operating cash flow over net income) it chose backwards.

The prohibition existed only as a prompt instruction, which is not a control. This is the
control: a **ratio between statement lines** quoted in a model's claim must be findable in
the evidence pack. One that is not was computed by the model, has no definition, no
declared direction and no calculation record, and is moved to ``unsupported_claims``.

WHY IT IS THIS NARROW
=====================
The first version refused *any* percentage the pack did not contain, and review showed
what that eats: "a 15% royalty" where the filing says "fifteen percent", "the 100% owned
subsidiary" where it says "wholly owned", "the US statutory rate of 21%", "top 10% of
peers". None of those is a computed ratio, and a guard that moves true sourced statements
to ``unsupported_claims`` trains its readers to ignore that list.

So a percentage is examined only when the sentence gives it the **shape of a financial
ratio**: "X% of <statement line>", or a margin / conversion / payout / return / coverage
figure. Everything else is left to the citation rules that already govern it.

It stays loose in the other direction too: the comparison is pack-wide rather than
cited-only, accepts any evidence number that rounds to the claimed one at the claim's own
precision (``52.17`` supports "52.2%" and "52%"), and checks both ends of a range. It is
built to miss a borderline case rather than to suppress a true one. What it cannot miss is
a figure like 88.4 that appears nowhere at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:[.,]\d{1,3})?"
#: "88.4%", "88,4 %", "88.4 percent", "50-55%", "50 to 55 per cent".
_PERCENT_RE = re.compile(
    rf"(?<![\w.])(?:({_NUM})\s*(?:-|–|—|to)\s*)?({_NUM})\s*(?:%|percent\b|per\s+cent\b)",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(rf"(?<![\w])-?(?:{_NUM})")

#: Statement lines a model can divide. "of revenue / of sales" is deliberately absent:
#: a share of sales is routinely SOURCED ("three quarters of sales came from …"), in
#: words the numeric comparison cannot read.
_STATEMENT_LINE = (
    r"(?:net\s+)?operating\s+cash\s+flows?|cash\s+flows?\s+from\s+operations|"
    r"free\s+cash\s+flows?|(?:net\s+)?cash\s+flows?|net\s+income|net\s+profit|net\s+earnings|"
    r"operating\s+(?:income|profit)|ebitda|ebit|capex|capital\s+expenditures?|"
    r"total\s+debt|net\s+debt|shareholders['’]?\s+equity|total\s+assets"
)
_OF_A_STATEMENT_LINE_RE = re.compile(
    rf"(?:%|percent|per\s+cent)\s+of\s+(?:the\s+|its\s+|total\s+|reported\s+)?(?:{_STATEMENT_LINE})\b",
    re.IGNORECASE,
)
_RATIO_WORD_RE = re.compile(
    r"\b(?:margins?|cash\s+conversion|conversion\s+(?:ratio|rate|efficiency)|"
    r"(?:fcf|free\s+cash\s+flow)\s+conversion|payout\s+ratio|return\s+on\s+(?:equity|assets|"
    r"capital|invested\s+capital)|\bro(?:e|a|ic)\b|capex\s+intensity|capital\s+intensity|"
    r"(?:interest|dividend)\s+cover(?:age)?)\b",
    re.IGNORECASE,
)


def _to_float(token: str) -> float | None:
    text = token.strip()
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", text):
        text = text.replace(",", "")  # 13,420 and 1,250.5 — thousands separators
    elif text.count(",") == 1 and "." not in text:
        text = text.replace(",", ".")  # 88,4 — a decimal comma
    try:
        return float(text)
    except ValueError:
        return None


def _decimals(raw: str) -> int:
    if re.fullmatch(r"-?\d{1,3}(?:,\d{3})+(?:\.\d+)?", raw):
        return len(raw.split(".")[1]) if "." in raw else 0
    for separator in (".", ","):
        if separator in raw:
            return len(raw.split(separator)[1])
    return 0


def percentages_in(text: str | None) -> list[tuple[str, float, int]]:
    """``(as written, value, decimals)`` for every percentage in ``text``.

    A range yields BOTH ends: "50-55%" is two claims, and testing only the high end
    flags every quoted range whose low end is the sourced figure.
    """
    found: list[tuple[str, float, int]] = []
    for match in _PERCENT_RE.finditer(text or ""):
        for raw in (match.group(1), match.group(2)):
            if not raw:
                continue
            value = _to_float(raw)
            if value is not None:
                found.append((f"{raw}%", value, _decimals(raw)))
    return found


def evidence_numbers(texts: Iterable[str | None]) -> list[float]:
    numbers: list[float] = []
    for text in texts:
        for match in _NUMBER_RE.finditer(text or ""):
            value = _to_float(match.group(0).lstrip("-"))
            if value is not None:
                numbers.append(abs(value))
    return numbers


def has_financial_ratio_shape(sentence: str | None) -> bool:
    """Does this sentence present a percentage AS a ratio between statement lines?"""
    value = sentence or ""
    return bool(_OF_A_STATEMENT_LINE_RE.search(value) or _RATIO_WORD_RE.search(value))


_SENTENCE_RE = re.compile(r"(?<=[.!?;])\s+")


def unsupported_percentages(claim: str | None, numbers: list[float]) -> list[str]:
    """Ratio-shaped percentages in ``claim`` that no evidence number rounds to.

    An empty ``numbers`` list answers ``[]``: with no evidence to compare against the
    guard has no basis to refuse anything, and the citation rules already cover a claim
    with nothing behind it.
    """
    if not numbers:
        return []
    missing: list[str] = []
    for sentence in _SENTENCE_RE.split(claim or ""):
        if not has_financial_ratio_shape(sentence):
            continue
        for written, value, decimals in percentages_in(sentence):
            tolerance = 0.5 * 10 ** (-decimals) + 1e-9
            if not any(abs(number - value) <= tolerance for number in numbers):
                missing.append(written)
    return missing


__all__ = [
    "evidence_numbers",
    "has_financial_ratio_shape",
    "percentages_in",
    "unsupported_percentages",
]
