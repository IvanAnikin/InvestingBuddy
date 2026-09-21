"""A percentage the evidence does not contain is a ratio the model computed — V3.18.1.

WHAT WENT WRONG
===============
A live council wrote *"net income … approximately 88.4% of net operating cash flow"* and,
two agents later, read the fall from 113.4% as **weakening cash conversion**. Neither
figure is anywhere in the evidence pack. The model divided one evidence item by another,
chose which went on top, and then chose what the result meant — and on the conventional
definition (operating cash flow over net income) it chose backwards.

The prohibition existed only as a prompt instruction, which is not a control. This is the
control: a percentage in a model's claim must be **findable in the evidence pack**. One
that is not was computed by the model, has no definition, no declared direction and no
calculation record, and is moved to ``unsupported_claims`` rather than kept.

WHY PACK-WIDE, AND WHY ROUNDING-TOLERANT
========================================
The check is deliberately the *loose* one. It looks in every evidence item rather than
only the cited ones, and it accepts any evidence number that rounds to the claimed one at
the claim's own precision (``52.17`` supports "52.2%" and "52%"). This codebase has
withheld correct sentences before by being clever with a threshold, so the guard is built
to miss a borderline case rather than to suppress a true one. What it cannot miss is a
figure like 88.4 that appears nowhere at all.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

#: "88.4%", "88,4 %", "88.4 percent", "88.4 per cent".
_PERCENT_RE = re.compile(
    r"(?<![\w.])(\d{1,4}(?:[.,]\d{1,3})?)\s*(?:%|percent\b|per\s+cent\b)", re.IGNORECASE
)
_NUMBER_RE = re.compile(r"(?<![\w])-?\d[\d,]*(?:\.\d+)?")


def _to_float(token: str) -> float | None:
    text = token.strip().replace(",", ".") if token.count(",") == 1 and "." not in token else token
    text = text.replace(",", "")
    try:
        return float(text)
    except ValueError:
        return None


def percentages_in(text: str | None) -> list[tuple[str, float, int]]:
    """``(as written, value, decimals)`` for every percentage in ``text``."""
    found: list[tuple[str, float, int]] = []
    for match in _PERCENT_RE.finditer(text or ""):
        raw = match.group(1)
        value = _to_float(raw)
        if value is None:
            continue
        separator = "." if "." in raw else ("," if "," in raw else "")
        decimals = len(raw.split(separator)[1]) if separator else 0
        found.append((match.group(0).strip(), value, decimals))
    return found


def evidence_numbers(texts: Iterable[str | None]) -> list[float]:
    numbers: list[float] = []
    for text in texts:
        for match in _NUMBER_RE.finditer(text or ""):
            value = _to_float(match.group(0))
            if value is not None:
                numbers.append(abs(value))
    return numbers


def unsupported_percentages(claim: str | None, numbers: list[float]) -> list[str]:
    """Percentages in ``claim`` that no evidence number rounds to.

    An empty ``numbers`` list answers ``[]``: with no evidence to compare against the
    guard has no basis to refuse anything, and the citation rules already cover a claim
    with nothing behind it.
    """
    if not numbers:
        return []
    missing: list[str] = []
    for written, value, decimals in percentages_in(claim):
        tolerance = 0.5 * 10 ** (-decimals) + 1e-9
        if not any(abs(number - value) <= tolerance for number in numbers):
            missing.append(written)
    return missing


__all__ = ["evidence_numbers", "percentages_in", "unsupported_percentages"]
