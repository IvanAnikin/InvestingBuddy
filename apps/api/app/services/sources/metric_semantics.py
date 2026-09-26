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

#: Words that, placed immediately before a balance label, make it a flow or an average of
#: that balance. Each is a fixed-width literal so it can serve as a regex lookbehind.
BALANCE_FLOW_PREFIXES: tuple[str, ...] = (
    "cost of ",
    "costs of ",
    "cost of the ",
    "cost on ",
    "interest on ",
    "interest on the ",
    "expense on ",
    "expenses on ",
    "income on ",
    "charge on ",
    "change in ",
    "changes in ",
    "movement in ",
    "movements in ",
    "average ",
    "financing of ",
    "remuneration of ",
)

#: A ratio suffix after a balance label: "net debt / EBITDA", "net debt to equity",
#: "net debt-to-EBITDA", "net debt ratio", "net debt multiple".
RATIO_SUFFIX_LOOKAHEAD = (
    r"(?!\s*(?:/|÷|-\s*to\s*-|\bto\b)\s*(?:adjusted\s+|underlying\s+|reported\s+|recurring\s+)?"
    r"(?:ebitda|ebit|equity|capital|market\s+cap)"
    r"|\s*(?:ratio|multiple|leverage|gearing|coverage)\b)"
)

#: The net-debt label alternation, with the ratio guard built in. Shared by every label
#: matcher so the prose parser, the table matcher and the excerpt ranker cannot disagree.
NET_DEBT_LABEL = (
    r"net (?:financial |interest[- ]bearing )?debt" + RATIO_SUFFIX_LOOKAHEAD
)

_FLOW_PREFIX_RE = re.compile(
    r"(?:"
    + "|".join(re.escape(p.strip()) for p in BALANCE_FLOW_PREFIXES)
    + r")\s+(?:the\s+)?(?:net\s+|total\s+|gross\s+)?"
    r"(?:financial\s+|interest[- ]bearing\s+)?(?:debt|cash|borrowings)",
    re.IGNORECASE,
)
_RATIO_RE = re.compile(
    r"(?:debt|cash|borrowings)\s*(?:/|÷|-\s*to\s*-|\bto\b)\s*"
    r"(?:adjusted\s+|underlying\s+|reported\s+|recurring\s+)?"
    r"(?:ebitda|ebit|equity|capital|market\s+cap)"
    r"|(?:debt|cash|borrowings)\s*(?:ratio|multiple|leverage|gearing|coverage)\b",
    re.IGNORECASE,
)


def is_not_a_balance(label_text: str | None) -> bool:
    """True when a label that names a debt/cash balance is really a flow or a ratio of it.

    ``"Cost of net debt"`` → True. ``"Net debt / EBITDA"`` → True.
    ``"Net debt"`` → False. ``"Net interest-bearing debt (NIBD)"`` → False.
    """
    text = label_text or ""
    return bool(_FLOW_PREFIX_RE.search(text) or _RATIO_RE.search(text))


__all__ = [
    "BALANCE_FLOW_PREFIXES",
    "NET_DEBT_LABEL",
    "RATIO_SUFFIX_LOOKAHEAD",
    "is_not_a_balance",
]
