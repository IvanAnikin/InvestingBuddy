"""Whose gap is it? — V3.18.1.

THE DEFECT
==========
A live report said, thirty-three times and in eight voices, some version of *"absence of
dividend data"*, *"lack of segment or geographic breakdown"*, *"the pack lacks company IR
disclosures"*. The company in question publishes a dividend history on the face of its
cash-flow statement, a segment note and a geographic note in its 10-K, and an investor
site. None of it was missing from the world. It was missing from **our evidence pack**.

Those are different statements and they call for opposite responses. *"The issuer does
not disclose its segments"* is a governance finding about a company. *"InvestingBuddy has
not acquired the segment note"* is a work item for this platform. The platform had one
vocabulary for both — a bare field name handed to a model, which can only phrase it as an
absence — so a platform limitation was published as a business fact.

THE RULE
========
> Never write "the company lacks X" merely because our corpus lacks X.

A gap therefore carries a **knowledge state**, from a closed vocabulary, and the default
is the humble one. ``not_disclosed_by_issuer`` is the only state that says something about
the company, and it may be claimed only with evidence cited for it — an absence is not
evidence of an absence.

WHAT THIS MODULE DOES
=====================
1. Labels gaps on the way INTO a prompt, so the model is told whose gap each one is.
2. Classifies a model's gap item on the way OUT: a statement that something is missing is
   a ``platform_evidence_gap`` unless it carries a citation, and is worded as one.
3. Rewrites the one sentence shape that is simply false without evidence — *the company
   does not disclose / has no / lacks …* — into the platform-gap wording.

It is deliberately conservative about (3): it matches an assertion ABOUT THE ISSUER, not
every occurrence of "no" or "lack". "No covenant breach was reported" is a finding, and a
guard that ate findings would be a worse defect than the one it fixes. Single words are
matched on word boundaries for the reason recorded in the period-verification traps:
"lacks" must not match "slacks".
"""

from __future__ import annotations

import re

#: The default. This platform has not acquired or verified the information. It says
#: NOTHING about the company.
NOT_ACQUIRED_BY_PLATFORM = "not_acquired_by_platform"
#: The issuer demonstrably does not disclose it. Requires cited evidence.
NOT_DISCLOSED_BY_ISSUER = "not_disclosed_by_issuer"
#: The filer's structured data carries the line only for another period (V3.18.1
#: own-period rule). Verified against the filer's own tagging.
NOT_REPORTED_FOR_PERIOD = "not_reported_in_structured_data_for_period"
#: The item does not apply to this kind of business (a bank has no gross margin).
NOT_APPLICABLE = "not_applicable"

KNOWLEDGE_STATES: frozenset[str] = frozenset(
    {
        NOT_ACQUIRED_BY_PLATFORM,
        NOT_DISCLOSED_BY_ISSUER,
        NOT_REPORTED_FOR_PERIOD,
        NOT_APPLICABLE,
    }
)

#: What kind of thing a council "risks_or_gaps" item is.
KIND_BUSINESS_RISK = "business_risk"
KIND_PLATFORM_EVIDENCE_GAP = "platform_evidence_gap"

GAP_KINDS: frozenset[str] = frozenset({KIND_BUSINESS_RISK, KIND_PLATFORM_EVIDENCE_GAP})

#: The suffix every unacquired gap carries into a prompt.
PLATFORM_GAP_LABEL = (
    "NOT YET ACQUIRED BY INVESTINGBUDDY — a platform evidence gap; it says nothing "
    "about whether the company discloses it"
)

#: The wording that replaces an unevidenced assertion about the issuer.
PLATFORM_GAP_WORDING = (
    "InvestingBuddy has not yet acquired or verified this information. Its absence from "
    "the evidence reviewed is a platform evidence gap, not a finding about the company."
)

_PLATFORM_PREFIX = "Not yet acquired by InvestingBuddy (platform evidence gap): "

# An absence statement: something is missing from what was reviewed.
_ABSENCE_RE = re.compile(
    r"\b("
    r"absence of|lack of|lacks|lacking|not available|unavailable|not provided|"
    r"not sourced|not included|not present|missing|limited detail|"
    r"no\s+(?:[\w/&-]+\s+){0,5}(?:is|are|was|were)\s+(?:available|provided|present|sourced)|"
    r"no\s+(?:segment|geographic|dividend|ebitda|liquidity|peer|production|reserve)"
    r"(?:[\w\s,/&-]{0,40})(?:breakdown|data|information|detail|disclosures?|figures?)"
    r")\b",
    re.IGNORECASE,
)

# An assertion that THE ISSUER does not publish/have something.
_ISSUER_ASSERTION_RE = re.compile(
    r"\b(?:the\s+)?(?:company|issuer|group|management|firm)\b[^.;]{0,40}?"
    r"\b(?:does\s+not|doesn['’]t|did\s+not|do\s+not|fails?\s+to|has\s+not|has\s+no|"
    r"have\s+no|lacks?|without|no\s+longer)\b[^.;]{0,50}?"
    r"\b(?:disclos\w*|report\w*|publish\w*|provid\w*|break\s*down|breakdown|segments?|"
    r"investor[\s-]relations|\bIR\b|website|dividends?|guidance|transparen\w*)",
    re.IGNORECASE,
)

_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+")


def _disowned(statement: str) -> str:
    """The statement, kept so the topic is not lost, and explicitly not endorsed.

    Deleting it would hide what the model tried to say; keeping it verbatim would
    publish an unevidenced claim about a company. So it is quoted and disowned.
    """
    quoted = statement.strip().rstrip(".").strip()
    return (
        f"Platform evidence gap — the evidence InvestingBuddy reviewed does not establish "
        f"this: \u201c{quoted}\u201d. {PLATFORM_GAP_WORDING}"
    )


def label_gap(gap: str, state: str = NOT_ACQUIRED_BY_PLATFORM) -> str:
    """A gap as a prompt should see it: the item, and whose gap it is."""
    text = str(gap or "").strip()
    if not text or PLATFORM_GAP_LABEL in text:
        return text
    if state == NOT_ACQUIRED_BY_PLATFORM:
        return f"{text} — {PLATFORM_GAP_LABEL}"
    if state == NOT_REPORTED_FOR_PERIOD:
        return (
            f"{text} — NOT REPORTED in the filer's structured data for the current period "
            "(verified against the filer's own tagging; an older value was not carried "
            "forward)"
        )
    if state == NOT_APPLICABLE:
        return f"{text} — not applicable to this kind of business"
    return text


def is_absence_statement(text: str | None) -> bool:
    return bool(_ABSENCE_RE.search(text or ""))


def asserts_issuer_non_disclosure(text: str | None) -> bool:
    return bool(_ISSUER_ASSERTION_RE.search(text or ""))


def classify_gap_item(text: str | None, *, has_citation: bool) -> str:
    """``platform_evidence_gap`` or ``business_risk``.

    An absence statement with no citation is about our evidence. With a citation it is a
    claim a reader can check, and is left as the model made it.
    """
    if has_citation:
        return KIND_BUSINESS_RISK
    if asserts_issuer_non_disclosure(text) or is_absence_statement(text):
        return KIND_PLATFORM_EVIDENCE_GAP
    return KIND_BUSINESS_RISK


def word_as_platform_gap(text: str | None, *, has_citation: bool) -> tuple[str, str]:
    """``(text, kind)`` for one gap item, worded so it cannot be read as a company fact."""
    original = str(text or "").strip()
    kind = classify_gap_item(original, has_citation=has_citation)
    if kind != KIND_PLATFORM_EVIDENCE_GAP:
        return original, kind
    if asserts_issuer_non_disclosure(original):
        # The one shape that is simply false without evidence. Not softened: disowned.
        return _disowned(original), kind
    if original.startswith(_PLATFORM_PREFIX):
        return original, kind
    return f"{_PLATFORM_PREFIX}{original}", kind


def correct_issuer_assertions(text: str | None) -> tuple[str, int]:
    """Replace unevidenced *"the company does not disclose …"* sentences in free prose.

    Returns the text and how many sentences were replaced. Used on a summary, which
    carries no per-sentence citations, so every such sentence in it is unevidenced by
    construction. Absence statements that do NOT assert anything about the issuer
    ("the absence of EBITDA limits the assessment") are left alone: they are clumsy, and
    they are not false.
    """
    original = str(text or "")
    if not original.strip():
        return original, 0
    replaced = 0
    out: list[str] = []
    for sentence in _SENTENCE_SPLIT_RE.split(original):
        if asserts_issuer_non_disclosure(sentence):
            replaced += 1
            out.append(_disowned(sentence))
            continue
        out.append(sentence)
    return " ".join(out), replaced


__all__ = [
    "GAP_KINDS",
    "KIND_BUSINESS_RISK",
    "KIND_PLATFORM_EVIDENCE_GAP",
    "KNOWLEDGE_STATES",
    "NOT_ACQUIRED_BY_PLATFORM",
    "NOT_APPLICABLE",
    "NOT_DISCLOSED_BY_ISSUER",
    "NOT_REPORTED_FOR_PERIOD",
    "PLATFORM_GAP_LABEL",
    "PLATFORM_GAP_WORDING",
    "asserts_issuer_non_disclosure",
    "classify_gap_item",
    "correct_issuer_assertions",
    "is_absence_statement",
    "label_gap",
    "word_as_platform_gap",
]
