"""Deterministic, bounded, pattern-aware financial evidence signal.

Phase 32A dedicated slice — financial excerpt relevance ranking (follow-up
to PR #107-#110). Detects whether a candidate excerpt/block contains a
CANONICAL financial metric LABEL followed, within the same short clause, by
a plausible VALUE (a currency amount or a percentage) — used ONLY to rank
which bounded excerpts survive ``primary_document_max_excerpts_per_document``
/ ``source_document_extraction_max_excerpts``, never to build or validate a
structured fact.

Why this is a SEPARATE, self-contained module rather than importing
``primary_fact_parser``'s own ``_MONEY_FIELDS``/``_PERCENT_FIELDS``/
``_iter_clause_safe``: this module must be importable from
``document_text_extractor`` (the base layer both ``primary_document_
extractor`` and ``primary_fact_parser`` build on), but ``primary_fact_
parser`` already imports FROM both of those — importing it back here would
create a cycle. The label vocabulary below is therefore intentionally kept
IN SYNC with (not literally shared with) ``primary_fact_parser.FIELD_*`` /
its label alternations; if a canonical metric label changes there, mirror
it here too. A false positive/negative here can only ever shift which
excerpt is ranked higher or lower — it can never fabricate or promote a
structured fact. ``primary_fact_parser``/``extracted_fact_validator``
remain the sole authority for whether a fact is safe to emit.
"""

from __future__ import annotations

import re

from app.services.sources.financial_fact_categories import (
    financial_fact_category,
)

# (field_name, label alternation) pairs for a MONEY-valued metric — field
# names mirror primary_fact_parser.FIELD_* / financial_fact_categories'
# recognized field vocabulary so a match's category classification (below)
# stays consistent with the rest of Phase 32A's category-diversity work.
_MONEY_METRIC_LABELS: tuple[tuple[str, str], ...] = (
    ("revenue", r"revenue|net sales|total sales|sales|turnover"),
    (
        "recurring_operating_profit",
        r"recurring operating (?:profit|income|result)"
        r"|profit from recurring operations",
    ),
    ("operating_profit", r"operating profit|operating income|operating result|ebit\b"),
    (
        "net_income",
        r"net income|net profit|profit attributable|net result"
        # "profit for the year" alone means the net/bottom-line figure, but
        # is ALSO a literal substring of "operating profit for the year" —
        # mirrors the same guard primary_fact_parser.FIELD_NET_INCOME uses,
        # so this ranking signal never double-tags one sentence as both
        # operating profit AND net income.
        r"|(?<!operating )(?<!recurring operating )profit for the year",
    ),
    ("operating_free_cash_flow", r"operating free cash flow"),
    ("free_cash_flow", r"free cash flow"),
    (
        "operating_cash_flow",
        r"(?:net )?cash (?:flow )?(?:generated |provided )?from operating activities"
        r"|cash flow from operations|operating cash flow",
    ),
    ("total_assets", r"total assets"),
    ("total_debt", r"total debt|gross debt|total borrowings|gross borrowings"),
    ("net_debt", r"net (?:financial )?debt"),
    ("cash_and_equivalents", r"cash and cash equivalents"),
    ("net_cash", r"net cash position|net cash"),
    (
        "total_equity",
        r"total (?:shareholders|stockholders)[’']?\s*equity"
        r"|shareholders[’']?\s*equity|total equity",
    ),
)
# (field_name, label alternation) pairs for a PERCENTAGE-valued metric.
_PERCENT_METRIC_LABELS: tuple[tuple[str, str], ...] = (
    ("recurring_operating_margin", r"recurring operating margin"),
    ("operating_margin", r"operating margin"),
)

# A generic amount: digits with optional thousands separators (comma, dot,
# or a bare space — pdfplumber-reconstructed PDF text often renders
# "4 492" with a plain space, not a thousands comma). The trailing
# ``(?!\d)`` forces the engine to always consume the FULL contiguous digit
# run rather than backtrack to a shorter prefix — without it, a value
# immediately followed by "%" (e.g. "up by 23%") could defeat the
# money-pattern's own percent-exclusion guard below by backtracking to
# match just "2" of "23" (whose next character is "3", not "%").
_AMOUNT = r"(?:\d[\d.,\s]{0,12}\d|\d)(?!\d)"
# A short, same-clause gap: no sentence-ending period, no newline (a region
# boundary), no semicolon — a lightweight analogue of primary_fact_parser's
# clause-safety guard, deliberately simpler since this only ever affects
# RANKING, not fact emission. Digits ARE allowed in the gap (unlike a
# stricter fact-building parser) so a common "label increased by N% to
# VALUE" trend clause still bridges label to value; the ``(?!\d)``/
# ``(?!\s*%)`` guards on the amount itself remain the safety net against
# capturing a trend percentage as though it were the target value.
_GAP = r"[^\n.;]{0,60}?"

_SCALE_WORD = r"(?:million|billion|thousand|m\b|bn\b|k\b)"
# A money value must carry EITHER a currency symbol OR an explicit scale
# word — never a bare, unqualified number. Without this, a bare number
# shortly after a label that is really being used as a HEADING (not
# labelling a value at all) can capture something else entirely close by,
# e.g. "Sales\nFor the year ended 31 March 2026, sales increased..." — the
# bare "31" (from the date) satisfied a looser amount-only pattern with
# nothing to distinguish it from a genuine value, live-observed to collide
# two UNRELATED figures under the same excerpt-diversity key.
_MONEY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (
        field,
        re.compile(
            rf"(?:{alt}){_GAP}"
            rf"(?:[€£$]\s*(?:{_AMOUNT})(?:\s*{_SCALE_WORD})?"
            rf"|(?:{_AMOUNT})\s*{_SCALE_WORD})"
            rf"(?!\s*%)",
            re.IGNORECASE,
        ),
    )
    for field, alt in _MONEY_METRIC_LABELS
)
_PERCENT_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (field, re.compile(rf"(?:{alt}){_GAP}(?:{_AMOUNT})\s*%", re.IGNORECASE))
    for field, alt in _PERCENT_METRIC_LABELS
)
_ALL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    *_MONEY_PATTERNS,
    *_PERCENT_PATTERNS,
)


def metric_value_matches(text: str) -> list[tuple[str, str]]:
    """Return ``(field, matched_span_text)`` for every DISTINCT canonical
    financial metric whose label is followed, in the same short clause, by
    a plausible value. Bounded: a small fixed set of compiled patterns
    (~15) each run once against ``text`` — O(patterns) x O(len(text)), no
    nested/pairwise scanning. ``text`` is itself already bounded (a single
    excerpt candidate, at most a few thousand characters).
    """
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for field, pattern in _ALL_PATTERNS:
        if field in seen:
            continue
        m = pattern.search(text)
        if m:
            seen.add(field)
            out.append((field, m.group(0)))
    return out


# A generic, bounded signal for a "financial highlights"-style section
# heading/context — never an issuer-specific literal.
_HEADLINE_SECTION_RE = re.compile(
    r"\b(?:financial highlights|key financial (?:data|figures)"
    r"|consolidated results|group financial highlights|results for the year"
    r"|summary of financial (?:information|results)|income statement"
    r"|statement of (?:comprehensive )?income|cash flow statement"
    r"|balance sheet|statement of financial position|financial review)\b",
    re.IGNORECASE,
)
# A generic period/temporal-context signal.
_PERIOD_RE = re.compile(
    r"\b(?:FY\s?20\d{2}|20\d{2}|H1\s?20\d{2}|H2\s?20\d{2}"
    r"|(?:six|nine|three)\s+months?\s+ended|year\s+ended|for the year)\b",
    re.IGNORECASE,
)
# Generic table-of-contents / index / navigation line shapes, never an
# issuer-specific string. A contents page typically alternates between
# TWO distinct line shapes rather than one: "<title> .... <page>" on a
# SINGLE line, OR a numbered entry title ("23. Trade payables...") on one
# line followed by a bare page number ("44") on the NEXT line — PDF text
# extraction commonly separates a title from its own trailing page number
# this way. Either shape alone, or a run of numbered-list-style lines with
# no connected prose between them, is treated as boilerplate.
_TOC_TRAILING_NUMBER_RE = re.compile(r"^.{1,70}?\s+\d{1,4}$")
_TOC_NUMBERED_ITEM_RE = re.compile(r"^\d{1,3}\.\s+\S")
_TOC_BARE_NUMBER_RE = re.compile(r"^\d{1,4}$")


def looks_like_boilerplate(text: str) -> bool:
    """True when ``text`` looks like a table of contents / index / repeated
    navigation fragment rather than genuine narrative or tabular content —
    a majority of its non-empty lines are short, numbered-entry- or bare
    -page-number-shaped tokens rather than connected prose.
    """
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if len(lines) < 4:
        return False
    toc_like = sum(
        1
        for ln in lines
        if _TOC_TRAILING_NUMBER_RE.match(ln)
        or _TOC_NUMBERED_ITEM_RE.match(ln)
        or _TOC_BARE_NUMBER_RE.match(ln)
    )
    return toc_like / len(lines) >= 0.6


def has_headline_section_context(text: str) -> bool:
    return bool(_HEADLINE_SECTION_RE.search(text))


def has_period_context(text: str) -> bool:
    return bool(_PERIOD_RE.search(text))


# A generic (never issuer-specific) segment/business-unit-breakdown proximity
# marker — mirrors primary_document_extractor._SEGMENT_HEADING_MARKERS'
# vocabulary (kept in sync manually for the same import-layering reason
# documented at the top of this module). A segment-scoped headline metric
# (e.g. "Specialist Watchmakers... operating result of €107 million") is
# every bit as valuable evidence as the Group-level figure, but tends to
# score lower on flat keyword density alone (a short, single-segment
# sentence has fewer DISTINCT financial keywords than a long multi-topic
# paragraph) — this gives it a modest, bounded boost so it can still
# compete for a bounded excerpt slot.
_SEGMENT_CONTEXT_RE = re.compile(
    r"\b(?:segment|by business|by division|by region|reportable segment"
    r"|operating segment|business area|business group|business unit)\b",
    re.IGNORECASE,
)


def has_segment_context(text: str) -> bool:
    return bool(_SEGMENT_CONTEXT_RE.search(text))


def excerpt_diversity_key(text: str) -> tuple[str, str, str]:
    """A ``(category, sub_key, sub_key_2)`` key for round-robin excerpt
    selection (see ``financial_fact_categories.select_category_diverse``).

    When ``text`` contains a genuine metric+value pairing, the key is
    derived exactly like a real structured fact's diversity key (category
    + field), so a candidate excerpt about revenue, one about operating
    margin, and one about cash flow each claim their OWN slot rather than
    competing for the same one. The matched VALUE's leading digits are
    folded into the sub-key too — this is deliberate, not incidental: two
    excerpts sharing a label but reporting genuinely DIFFERENT values (a
    Group operating margin of 20.0% and a Jewellery Maisons operating
    margin of 30.5% read very similarly in prose) must never collapse into
    one diversity slot merely because the surrounding language overlaps —
    see the mission brief's explicit dedup-identity requirement. A block
    with no metric+value pairing at all falls back to a single shared
    "no_metric_signal" bucket (not one bucket per block), so numerous
    generic/narrative blocks compete AS A GROUP for their share of slots
    rather than each one individually guaranteeing itself a round.
    """
    matches = metric_value_matches(text)
    if not matches:
        return ("no_metric_signal", "", "")
    field, span = matches[0]
    category = financial_fact_category(field, scope=None)
    digits = re.sub(r"[^\d]", "", span)[:6]
    return (category, field, digits)


__all__ = [
    "metric_value_matches",
    "looks_like_boilerplate",
    "has_headline_section_context",
    "has_period_context",
    "has_segment_context",
    "excerpt_diversity_key",
]
