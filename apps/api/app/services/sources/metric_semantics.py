"""A balance is not the cost of carrying it — V3.19.1.

THE DEFECT
==========
A Kering report stated *net debt €122m*. Kering's €122m was its **cost of net debt** — the
interest charge on the net debt, an income-statement line — while the balance itself was
several billion euros. Nothing in the label matchers told the two apart: ``net debt`` was
found inside ``cost of net debt`` in prose, and a table row captioned *"Cost of net debt"*
matched the ``net_debt`` row pattern on its own, so it was taken as the balance with full
confidence. A row captioned *"Net debt / EBITDA"* has the same problem the other way round:
it is a leverage multiple, not a balance.

THE RULE
========
A balance label (net debt, net cash, cash, total debt) names the balance only when nothing
next to it turns it into something else:

* a **flow prefix** — *cost of*, *interest on*, *change in*, *average* … — makes it the
  carrying cost, the movement or the average of the balance;
* a **ratio suffix** — */ EBITDA*, *to equity*, *ratio*, *multiple* — makes it a multiple.

Either one means the text is about a DIFFERENT metric, and the balance field refuses it. The
rule is generic financial-reporting vocabulary; no issuer is named anywhere.
"""

from __future__ import annotations

import re

#: The balance fields this rule protects, by the names each matcher uses.
BALANCE_FIELDS: frozenset[str] = frozenset(
    {"net_debt", "net_cash", "cash", "cash_and_equivalents", "total_debt"}
)

#: The net-debt label. Deliberately plain: WHAT SURROUNDS it is judged by
#: ``is_flow_or_ratio_at``, anchored on the label's own span, so all three matchers apply
#: one rule instead of three regex variants that drift apart.
NET_DEBT_LABEL = r"net (?:financial |interest[- ]bearing )?debt"

#: Text ENDING immediately before a balance label that makes it a flow or an average:
#: "cost of", "interest on the", "finance costs on", "change in", "average" …
_FLOW_PREFIX_TAIL = re.compile(
    r"(?:\b(?:costs?|interest|expenses?|income|charges?|changes?|movements?|financing"
    r"|remuneration|servicing|service|increases?|decreases?|reductions?|rises?|falls?"
    r"|growth|declines?|improvements?|deteriorations?|repayments?)"
    r"(?:\s+\w+)?\s+(?:of|on|in|for)(?:\s+the)?"
    r"|\baverage)\s*$",
    re.IGNORECASE,
)

#: Text STARTING immediately after a balance label that makes it a flow or a multiple:
#: "cost", "costs", "/ EBITDA", "to LTM EBITDA", "to total capital", "ratio" …
_NON_BALANCE_SUFFIX_HEAD = re.compile(
    r"^\s*(?:"
    r"(?:costs?|expenses?|charges?|interest|servicing)\b"
    r"|(?:/|÷|-\s*to\s*-|\bto\b)\s*(?:[\w.]+\s+){0,3}?"
    r"(?:ebitda|ebit|equity|capital|capitalisation|capitalization|market\s+cap\w*)\b"
    r"|(?:ratio|multiple|leverage|gearing|coverage)\b"
    # V3.19.8 — a CASH-FLOW line, not a balance: "Net cash received from operating
    # activities 3,100" (Kering URD 2025, p.54) was read as net cash of EUR 3.1bn while the
    # group reports net debt. A flow verb, or "… operating/investing/financing
    # activities", right after the label makes it a flow.
    r"|\(?\s*(?:used|received|generated|provided|paid|spent|absorbed|inflows?|outflows?"
    r"|flows?)\b"
    r"|(?:[\w()/]+\s+){0,4}?(?:from|in|by|for)\s+(?:the\s+)?(?:operating|investing|financing)"
    r"\s+activities\b"
    r")",
    re.IGNORECASE,
)

_WS = re.compile(r"\s+")


def is_flow_or_ratio_at(text: str, start: int, end: int) -> bool:
    """Does the balance label at ``text[start:end]`` actually name a flow or a multiple?

    Judged on the label's own neighbourhood only — the few words immediately before and
    after it, whitespace-normalised so a line wrap cannot hide "cost of" — never on the
    whole cell or sentence, so "Net debt (average cost of debt 2.1%)" is still net debt.
    """
    before = _WS.sub(" ", text[max(0, start - 48) : start])
    after = _WS.sub(" ", text[end : end + 64])
    return bool(_FLOW_PREFIX_TAIL.search(before) or _NON_BALANCE_SUFFIX_HEAD.match(after))


def label_end(label_pattern: str, text: str, start: int) -> int:
    """Where the label that a match begins with ends. ``start`` when it cannot be found."""
    found = re.match(label_pattern, text[start:], re.IGNORECASE)
    return start + found.end() if found else start


def is_not_a_balance(label_text: str | None, label_pattern: str = NET_DEBT_LABEL) -> bool:
    """True when the balance label inside ``label_text`` names a flow or a ratio of it.

    ``"Cost of net debt"`` → True. ``"Net debt / LTM EBITDA"`` → True. ``"Net debt cost"``
    → True. ``"Net debt"`` → False. ``"Net debt (average cost of debt 2.1%)"`` → False.
    """
    text = label_text or ""
    found = re.search(label_pattern, text, re.IGNORECASE)
    if found is None:
        return False
    return is_flow_or_ratio_at(text, found.start(), found.end())


__all__ = [
    "BALANCE_FIELDS",
    "NET_DEBT_LABEL",
    "is_flow_or_ratio_at",
    "is_not_a_balance",
    "label_end",
]
