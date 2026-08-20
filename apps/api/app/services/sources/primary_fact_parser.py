"""
Conservative primary-fact parser — Phase 29B.2.

Parses only *high-confidence* primary facts out of already-extracted, bounded
document excerpts (``document_text_extractor.DocumentTextExtraction``). It is the
opposite of an aggressive scraper: when a value is at all ambiguous it is *not*
parsed — an honest excerpt with no structured fact is always preferable to a
wrong number.

Hard rules (enforced here + by tests):
  * Every parsed fact carries its provenance: ``source_url`` + ``excerpt_id`` +
    ``page_number`` + ``confidence`` + ``needs_human_review=True``.
  * No inference of a missing financial. If it is not explicitly stated, it is
    not produced.
  * No currency conversion. The reporting currency is recorded as-found.
  * No valuation metric, price target, fair value, or upside/downside is ever
    computed — this parser only reads primary statements of fact.
  * Ambiguous lines (a labelled metric with two candidate magnitudes, or a
    number with no unit) are skipped with no fact emitted.

Facts remain ``needs_human_review=True`` until an admin verifies them — they are
never treated as verified truth downstream.
"""

from __future__ import annotations

import re

from pydantic import BaseModel

from app.services.sources.document_text_extractor import (
    DocumentExcerpt,
    DocumentTextExtraction,
)
from app.services.sources.primary_document_extractor import _infer_scope

# Canonical fact field names (neutral, factual — never a rating vocabulary).
FIELD_LEGAL_NAME = "company_legal_name"
FIELD_REPORTING_CURRENCY = "reporting_currency"
FIELD_FISCAL_YEAR = "fiscal_year"
FIELD_REVENUE = "revenue"
FIELD_OPERATING_PROFIT = "operating_profit"
FIELD_RECURRING_OPERATING_PROFIT = "recurring_operating_profit"
FIELD_OPERATING_MARGIN = "operating_margin"
FIELD_RECURRING_OPERATING_MARGIN = "recurring_operating_margin"
FIELD_NET_INCOME = "net_income"
FIELD_FREE_CASH_FLOW = "free_cash_flow"
FIELD_OPERATING_FREE_CASH_FLOW = "operating_free_cash_flow"
FIELD_OPERATING_CASH_FLOW = "operating_cash_flow"
FIELD_TOTAL_ASSETS = "total_assets"
FIELD_TOTAL_DEBT = "total_debt"
FIELD_NET_DEBT = "net_debt"
FIELD_NET_CASH = "net_cash"
FIELD_CASH = "cash_and_equivalents"
FIELD_TOTAL_EQUITY = "total_equity"
FIELD_EMPLOYEES = "employees"


class PrimaryFact(BaseModel):
    """One high-confidence primary fact parsed from a document excerpt."""

    field: str
    value: str
    numeric_value: float | None = None
    unit: str | None = None
    currency: str | None = None
    scale: str | None = None  # million | billion | thousand | None
    period: str | None = None
    # Best-effort entity/segment scope this fact was reported under (e.g.
    # "group" for a consolidated figure, or a heading like "Segment A" — a
    # generic placeholder — for a segment breakdown). ``None`` when the
    # excerpt's heading gives no scope signal — never guessed. See
    # ``primary_document_extractor._infer_scope``.
    scope: str | None = None
    source_url: str | None = None
    excerpt_id: str | None = None
    page_number: int | None = None
    confidence: str = "medium"  # low | medium | high
    parser_warning: str | None = None
    needs_human_review: bool = True


# --------------------------------------------------------------------------- #
# Currency + number helpers
# --------------------------------------------------------------------------- #

_CURRENCY_WORDS: dict[str, str] = {
    "euro": "EUR",
    "euros": "EUR",
    "eur": "EUR",
    "€": "EUR",
    "swiss franc": "CHF",
    "swiss francs": "CHF",
    "chf": "CHF",
    "sterling": "GBP",
    "pound": "GBP",
    "pounds": "GBP",
    "gbp": "GBP",
    "£": "GBP",
    "danish krone": "DKK",
    "kroner": "DKK",
    "dkk": "DKK",
    "us dollar": "USD",
    "us dollars": "USD",
    "usd": "USD",
    "dollars": "USD",
    "$": "USD",
}
_CURRENCY_SYMBOLS = "€£$"

# A number like 20,616 or 20.6 or 1 234 (thin-space grouping), optionally scaled.
_NUM = r"(?P<num>\d[\d.,  ]*\d|\d)"
_SCALE = r"(?P<scale>million|billion|thousand|bn|mn|m)\b"


def _norm_number(raw: str) -> float | None:
    """Parse a grouped number string to float, else None. No unit inference."""
    s = raw.strip().replace(" ", "").replace(" ", "").replace(" ", "")
    if not s:
        return None
    # Decide decimal separator: if both , and . present, the last one is decimal.
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        # Ambiguous: 20,616 (grouping) vs 20,6 (decimal). Treat a single comma
        # with exactly 3 trailing digits as grouping; else as decimal.
        parts = s.split(",")
        if len(parts) == 2 and len(parts[1]) == 3:
            s = s.replace(",", "")
        else:
            s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _scale_word(w: str | None) -> str | None:
    if not w:
        return None
    w = w.lower()
    if w in ("billion", "bn"):
        return "billion"
    if w in ("million", "mn", "m"):
        return "million"
    if w == "thousand":
        return "thousand"
    return None


def _find_currency(text: str) -> str | None:
    low = text.lower()
    # Prefer explicit "in millions of euros" / "reporting currency" phrasing.
    # A currency WORD (as opposed to a symbol like "€") must be matched at
    # letter-boundaries, not as a raw substring — "eur" is also a substring
    # of "Europe"/"European", "usd" can sit inside issuer-specific tickers,
    # etc. Symbols (€, £, $) never collide with ordinary words, so they are
    # still matched as plain substrings.
    for word, code in _CURRENCY_WORDS.items():
        if word in _CURRENCY_SYMBOLS:
            if word in text:
                return code
            continue
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low):
            return code
    return None


# --------------------------------------------------------------------------- #
# Field patterns — each requires an explicit label near a single number.
# --------------------------------------------------------------------------- #

# An optional "for/in/during <period>" qualifier that can sit BETWEEN a label
# and its connector word (e.g. "Revenue for fiscal year 2024 was ..."). Without
# consuming this first, the year inside it is close enough to the label that
# the loose label→value gap below could grab IT instead of the real value —
# a real, pre-existing regex weakness this fix surfaces once prose parsing
# feeds the stricter validated-fact pipeline (a wrongly-parsed prose duplicate
# of a correct table value reads as a same-method "conflict" and silently
# downgrades the correct table fact to excerpt_only).
#
# Phase 32A corrective — a YEAR-LESS qualifier ("for the year", "for the
# period", "during the year") must ALSO be fully consumed, not just a
# dated one ("for fiscal year 2024"). Before this fix the trailing 4-digit
# year was mandatory, so "for the year" (no digits) matched nothing here —
# leaving it, and the trend verb immediately following it (see
# ``_TREND_CLAUSE`` below), unconsumed and within reach of the final loose
# ``[^\d\n]{0,25}?`` catch-all, which then grabbed the FIRST digit it found
# — a percentage CHANGE, not the absolute value (a real, live-observed
# failure on an actual issuer report: "Operating profit for the year grew
# by 1% to €4,492 million" parsed as ``1``, not ``4,492``).
_PERIOD_QUALIFIER = (
    r"(?:\s+(?:for|in|during)\s+(?:the\s+)?"
    r"(?:"
    r"(?:fiscal\s+year|financial\s+year|fiscal|year|period|half[- ]year|h[12])"
    r"(?:\s+ended)?(?:\s+(?:19|20)\d{2})?"
    r"|(?:19|20)\d{2}"
    r")"
    r")?"
)

# An optional "<trend verb> by X%" clause that can sit BETWEEN a label and its
# connector (e.g. "net cash rose by 3% to €8,496 million"). Real financial
# narrative very commonly states a percentage CHANGE before the absolute
# value — without consuming it here, the loose label→value gap below could
# grab the percentage figure instead of the real value (a real, live-observed
# failure on an actual issuer report — Phase 32A corrective).
#
# The trailing ``?+`` is a POSSESSIVE optional quantifier (not a plain ``?``):
# once this clause matches a trend phrase here, that consumption is locked in
# and can never be given up by backtracking. Without this, a sentence with a
# trend percentage but NO absolute value at all (e.g. "operating profit was
# up by 23% in Europe.") would have the engine retry the whole match with the
# trend clause "un-consumed", letting the plain connector+gap fallback below
# grab the trend percentage itself as if it were the metric's value — an
# independently reproduced fabrication bug (Phase 32A corrective, PR #107
# merge-blocker fix). A plain ``?`` cannot prevent this: it only controls
# whether the clause is *tried*, not whether a successful match may later be
# undone to let a different branch succeed.
# Live CFR staging finding (financial excerpt relevance-ranking dedicated
# slice, 2026-08-20): real financial narrative extremely commonly qualifies
# an FX-driven trend percentage with "at constant/actual exchange rates"
# BEFORE stating the absolute value — e.g. "sales increased by 5% at actual
# exchange rates to EUR22,420 million". Without consuming this generic
# (never issuer-specific), standard financial-reporting phrase here, it fell
# into the connector+gap budget below, whose 25-char cap was too tight to
# also reach past it to the real value — the label→value gap silently
# failed for exactly the sentence shape carrying a document's own headline
# Group revenue figure. Possessive-optional for the same non-backtracking
# reason as the trend clause itself.
_FX_RATE_QUALIFIER = r"(?:\s+at\s+(?:constant|actual)\s+exchange\s+rates)?+"

_TREND_CLAUSE = (
    r"(?:\s+(?:rose|grew|grow|increased|increase|fell|fall|declined|decline"
    r"|decreased|decrease|dropped|drop|climbed|slipped|improved|improve"
    r"|was up|were up|was down|were down)"
    rf"\s+(?:by\s+)?\d+(?:[.,]\d+)?\s*%{_FX_RATE_QUALIFIER})?+"
)

# Same idea as ``_TREND_CLAUSE`` but for percent-level fields (e.g. operating
# margin), where the trend unit can also be "percentage points" or "basis
# points" — never a bare "%" change mistaken for the metric's own level. Also
# possessive-optional for the same non-backtracking reason.
_PERCENT_TREND_CLAUSE = (
    r"(?:\s+(?:rose|grew|grow|increased|increase|fell|fall|declined|decline"
    r"|decreased|decrease|dropped|drop|climbed|slipped|improved|improve"
    r"|was up|were up|was down|were down)"
    r"\s+(?:by\s+)?\d+(?:[.,]\d+)?\s*"
    rf"(?:percentage\s+points?|basis\s+points?|%){_FX_RATE_QUALIFIER})?+"
)


# label -> compiled regex capturing (number, optional scale). ``exclude_prefix``
# is a FIXED-WIDTH literal negative-lookbehind guard so a more specific label
# immediately preceding this one (e.g. "recurring " before "operating profit",
# or "operating " before "free cash flow") is never ALSO counted under the
# broader/plain field — each span of text promotes to exactly one field,
# preserving metric identity instead of conflating two distinct disclosed
# figures that happen to share a common suffix word.
# Raw label alternations for every money/percent field, collected as each
# pattern is built (side effect of ``_money_pattern``/``_percent_pattern``
# below) so a single combined "is this text some OTHER recognized metric
# label" regex (``_ANY_LABEL_RE``) can be assembled once every field is
# defined — see ``_iter_clause_safe``.
_ALL_LABEL_ALTS: list[str] = []


def _money_pattern(
    label_alts: str,
    *,
    exclude_prefix: str | None = None,
) -> re.Pattern[str]:
    """Build a label -> (number, scale) pattern.

    The free-form span between the label (plus its fixed, controlled-
    vocabulary period-qualifier/trend-clause/connector) and the number is
    captured as a NAMED group (``gap``) so ``_iter_clause_safe`` can reject
    a match whose gap crosses into a different clause — a sentence/
    paragraph boundary, a semicolon, an adversative conjunction ("but",
    "while", ...), or another recognized metric label — before it is ever
    used to build a fact. This is the sole, generic invariant this parser
    relies on: a label may claim a value only when the value is in the
    SAME clause as the label (Phase 32A corrective — same-region
    fabrication fix). It replaces the earlier field-specific
    ``strict_clause`` period guard, which only blocked a full stop and left
    same-sentence adversative-clause and semicolon-separated captures open.
    """
    guard = rf"(?<!{re.escape(exclude_prefix)})" if exclude_prefix else ""
    _ALL_LABEL_ALTS.append(label_alts)
    return re.compile(
        rf"{guard}(?:{label_alts})"
        rf"{_PERIOD_QUALIFIER}"
        rf"{_TREND_CLAUSE}"
        rf"(?:\s+(?:of|was|were|to|at|amounted to|reached|totalled|totaled|:))?"
        rf"(?P<gap>[^\d\n]{{0,25}}?)"
        # A money value is never itself expressed with a trailing "%" — this
        # negative lookahead is the second, defence-in-depth guard (beyond
        # the possessive ``_TREND_CLAUSE`` above) against a percentage
        # CHANGE figure being captured as though it were an absolute money
        # amount, e.g. "operating profit was up by 23% in Europe." (Phase
        # 32A corrective, PR #107 merge-blocker fix).
        rf"[€£$]?\s*{_NUM}\s*(?:{_SCALE})?(?!\s*%)",
        re.IGNORECASE,
    )


# label -> compiled regex capturing an EXPLICIT percentage near its label
# (e.g. "operating margin of 20.0%"). A margin/percentage field is ONLY ever
# taken from a percent sign explicitly present in the source text — this
# parser never computes a margin from a profit/revenue pair (see module
# docstring: "No inference of a missing financial").
#
# ``_PERCENT_TREND_CLAUSE`` (possessive-optional, see its definition) absorbs
# a leading trend phrase — "was up by 23%", "rose 120 basis points" — before
# the connector/gap/number below ever runs, so a bare percentage CHANGE with
# no absolute level stated afterward yields NO match at all (Phase 32A
# corrective, PR #107 merge-blocker fix), while "...rose 120 basis points to
# 20.0%" still correctly parses the trailing 20.0 as the level.
def _percent_pattern(label_alts: str, *, exclude_prefix: str | None = None) -> re.Pattern[str]:
    guard = rf"(?<!{re.escape(exclude_prefix)})" if exclude_prefix else ""
    _ALL_LABEL_ALTS.append(label_alts)
    return re.compile(
        rf"{guard}(?:{label_alts})"
        rf"{_PERIOD_QUALIFIER}"
        rf"{_PERCENT_TREND_CLAUSE}"
        rf"(?:\s+(?:of|was|were|stood at|reached|at|:))?"
        rf"(?P<gap>[^\d\n%]{{0,25}}?)"
        rf"(?P<num>\d[\d.,]*)\s*%",
        re.IGNORECASE,
    )


_MONEY_FIELDS: list[tuple[str, re.Pattern[str]]] = [
    (FIELD_REVENUE, _money_pattern(r"revenue|net sales|total sales|sales|turnover")),
    (
        FIELD_RECURRING_OPERATING_PROFIT,
        # "profit from recurring operations" (Phase 32A corrective — LVMH
        # vocabulary gap) is the SAME canonical concept as "recurring
        # operating profit/income/result", just phrased the other way
        # around — generic financial-reporting vocabulary, not an
        # issuer-specific term.
        _money_pattern(
            r"recurring operating (?:profit|income|result)"
            r"|profit from recurring operations"
        ),
    ),
    (
        FIELD_OPERATING_PROFIT,
        _money_pattern(
            r"operating profit|operating income|operating result|ebit\b",
            exclude_prefix="recurring ",
        ),
    ),
    (
        FIELD_NET_INCOME,
        _money_pattern(
            r"net income|net profit|profit attributable|net result"
            # "profit for the year" alone means the bottom-line/net figure in
            # standard IFRS wording, but is ALSO a literal substring of
            # "operating profit for the year" / "recurring operating profit
            # for the year" — guarded so it never mislabels those as net
            # income (a real, live-observed collision — Phase 32A corrective).
            r"|(?<!operating )(?<!recurring operating )profit for the year"
        ),
    ),
    (FIELD_OPERATING_FREE_CASH_FLOW, _money_pattern(r"operating free cash flow")),
    (
        FIELD_FREE_CASH_FLOW,
        _money_pattern(r"free cash flow", exclude_prefix="operating "),
    ),
    (
        FIELD_OPERATING_CASH_FLOW,
        _money_pattern(
            r"(?:net )?cash (?:flow )?(?:generated |provided )?from operating activities"
            r"|cash flow from operations|operating cash flow"
            r"|(?:net )?cash flows? from operating activities"
        ),
    ),
    (FIELD_TOTAL_ASSETS, _money_pattern(r"total assets")),
    # Bare "borrowings" (no total/gross qualifier) is deliberately EXCLUDED: a
    # real-report finding (Phase 32A corrective live CFR run) showed a bare
    # "borrowings" mention describing what a net-cash figure is COMPRISED OF
    # — not itself stating a debt figure — matching a nearby unrelated number.
    # "total"/"gross" qualified mentions are a genuine, low-ambiguity signal;
    # the bare word alone is not.
    (
        FIELD_TOTAL_DEBT,
        _money_pattern(r"total debt|gross debt|total borrowings|gross borrowings"),
    ),
    (FIELD_NET_DEBT, _money_pattern(r"net (?:financial )?debt")),
    (FIELD_CASH, _money_pattern(r"cash and cash equivalents")),
    (
        FIELD_NET_CASH,
        _money_pattern(
            r"net cash position|net cash(?!\s*(?:flow|generated|provided|from|and))"
        ),
    ),
    (
        FIELD_TOTAL_EQUITY,
        _money_pattern(
            r"total (?:shareholders|stockholders)[’']?\s*equity"
            r"|shareholders[’']?\s*equity|total equity"
        ),
    ),
]

_PERCENT_FIELDS: list[tuple[str, re.Pattern[str]]] = [
    (
        FIELD_RECURRING_OPERATING_MARGIN,
        _percent_pattern(r"recurring operating margin"),
    ),
    (
        FIELD_OPERATING_MARGIN,
        _percent_pattern(r"operating margin", exclude_prefix="recurring "),
    ),
]

# --------------------------------------------------------------------------- #
# Clause-safety guard — Phase 32A corrective (same-region fabrication fix).
#
# A label may claim a monetary/percentage value only when that value sits in
# the SAME semantic clause as the label. ``_money_pattern``/``_percent_pattern``
# already capture the loosely-bounded span between the label (plus its fixed,
# controlled-vocabulary period-qualifier/trend-clause/connector) and the value
# as a named ``gap`` group — this is the only free-form part of the match, and
# therefore the only part that needs checking. A gap is UNSAFE (and the match
# discarded, never falling back to a weaker guess) when it crosses a sentence/
# paragraph boundary, a semicolon-style clause boundary, an adversative
# conjunction that changes subject ("but", "while", "whereas", ...), or
# another recognized financial-metric label — any of these mean the value no
# longer belongs to the first label. This is deliberately generic: it never
# inspects a specific label/value combination or issuer wording, only whether
# the label and its candidate value share one clause.
# --------------------------------------------------------------------------- #

_CLAUSE_BOUNDARY_RE = re.compile(
    r"[.!?;\n]|\b(?:but|while|whereas|however|although|yet|whilst)\b",
    re.IGNORECASE,
)

# Built once every money/percent field pattern has registered its label
# alternation (see ``_ALL_LABEL_ALTS`` in ``_money_pattern``/``_percent_pattern``
# above). Matches any OTHER recognized metric label — used to reject a gap
# that has silently drifted onto a different metric's clause.
_ANY_LABEL_RE = re.compile(
    "|".join(f"(?:{alt})" for alt in _ALL_LABEL_ALTS), re.IGNORECASE
)


def _iter_clause_safe(pattern: re.Pattern[str], text: str):
    """Yield only ``pattern`` matches whose ``gap`` group stays within one
    clause. Fails closed: a match whose gap is unsafe is skipped entirely,
    never used as a weaker/fallback candidate — an honest "no fact" is
    always preferable to a fabricated one attached to the wrong label.
    """
    for m in pattern.finditer(text):
        gap = m.group("gap")
        if _CLAUSE_BOUNDARY_RE.search(gap) or _ANY_LABEL_RE.search(gap):
            continue
        yield m


_FISCAL_YEAR_RE = re.compile(
    r"(?:fiscal year|financial year|year ended|for the year|as at 31|as of 31|"
    r"annual report)[^\n]{0,40}?((?:19|20)\d{2})",
    re.IGNORECASE,
)
_EMPLOYEES_RE = re.compile(
    r"(\d[\d.,  ]*\d)\s+(?:employees|people employed|staff|full-time equivalents)",
    re.IGNORECASE,
)
_EMPLOYEES_RE2 = re.compile(
    r"(?:employ(?:s|ed)?|workforce of|headcount of)\s+(?:approximately\s+)?"
    r"(\d[\d.,  ]*\d)",
    re.IGNORECASE,
)


def _ambiguous_multiple(
    pattern: re.Pattern[str], text: str, *, require_scale_or_currency: bool = False
) -> bool:
    """True when a labelled metric matches with two *different* magnitudes.

    ``require_scale_or_currency`` mirrors the exact validity gate the money-field
    caller applies before it will ever emit a fact from a match (``if scale is
    None and currency is None: continue`` in the ``_MONEY_FIELDS`` loop). Without
    this, a second, unrelated same-label mention later in the excerpt that states
    only a bare percentage CHANGE with no absolute value nearby (e.g. "operating
    profit was up by 23%" following a fully-qualified "...grew by 1% to
    \u20ac4,492 million" earlier in the SAME excerpt) injects a false second
    "magnitude" purely from its trend-clause digits \u2014 silently discarding an
    otherwise complete, correctly-parsed fact that would itself pass every other
    check (a real, live-observed failure \u2014 Phase 32A corrective).
    """
    vals: set[float] = set()
    for m in _iter_clause_safe(pattern, text):
        num = _norm_number(m.group("num"))
        if num is None:
            continue
        if require_scale_or_currency and "scale" in m.re.groupindex:
            scale = _scale_word(m.group("scale"))
            currency = _find_currency(m.group(0))
            if scale is None and currency is None:
                continue
        vals.add(num)
    return len(vals) > 1


# --------------------------------------------------------------------------- #
# Prose scope inference — Phase 32A corrective (live CFR gap)
#
# ``_infer_scope`` (``primary_document_extractor``) is HEADING-based only.
# Real issuer prose can carry an explicit scope in the SENTENCE itself even
# when the excerpt's own heading gives no (or a misleadingly generic)
# signal — e.g. "The Group's Specialist Watchmakers reported sales of
# €3.1 billion" sitting in a general-narrative excerpt with no
# segment-specific heading of its own. Generic and structural — never a
# hardcoded issuer vocabulary; derived purely from sentence shape.
# --------------------------------------------------------------------------- #

_SCOPE_REPORT_VERBS = (
    r"reported|generated|posted|recorded|delivered|achieved|announced"
)

# "Group's <Named Segment> reported/generated/posted ..." — the segment
# (never "Group") is the scope, even though "Group" appears in the sentence.
# The presence of "Group's" before a segment name must never turn a segment
# figure into a Group figure (mission requirement).
_GROUP_OWNED_SEGMENT_RE = re.compile(
    rf"\bGroup['’]s\s+([A-Z][A-Za-z0-9&,.\-\s]{{1,60}}?)\s+"
    rf"(?:{_SCOPE_REPORT_VERBS})\b"
)

# A bare named subject (no "Group's" prefix), sentence-initial only,
# immediately followed by a reporting verb — the subject IS the scope.
_NAMED_SUBJECT_RE = re.compile(
    rf"(?:^|[.!?]\s+)(?:The\s+)?([A-Z][A-Za-z0-9&,.\-\s]{{1,60}}?)\s+"
    rf"(?:{_SCOPE_REPORT_VERBS})\b"
)

_GROUP_SUBJECT_WORDS = frozenset({"group", "the group"})
# Generic financial-statement vocabulary that is never itself a business/
# segment NAME — excluded so a plain metric noun accidentally captured as a
# "subject" is never mistaken for a named scope.
_GENERIC_SCOPE_BLOCKLIST = frozenset(
    {
        "revenue", "sales", "net sales", "total sales", "turnover",
        "operating profit", "operating income", "operating result",
        "recurring operating profit", "net income", "net profit", "profit",
        "margin", "operating margin", "cash flow", "free cash flow",
        "operating free cash flow", "operating cash flow", "total assets",
        "total debt", "net debt", "net cash", "cash and cash equivalents",
        "total equity", "management", "the board", "results", "performance",
        "the company", "company",
    }
)


def _clean_scope_label(raw: str) -> str | None:
    label = " ".join((raw or "").split()).strip(" .,")
    if not label:
        return None
    if len(label) > 80:
        label = label[:79].rstrip() + "…"
    return label


def _infer_prose_scope(sentence: str) -> str | None:
    """Best-effort scope from a SENTENCE's own grammatical subject.

    Independent of the excerpt's heading (see ``_infer_scope``) and of how
    many other candidates matched elsewhere in the excerpt: every material
    fact gets its OWN scope decision from its own local sentence, so a
    segment figure sitting in an excerpt that also discusses Group
    performance is never defaulted to "group" merely because it was the
    only regex match in the excerpt (mission requirement — the semantic
    model must not depend on two matches existing). Returns ``None``
    (unknown — never guessed) when the sentence gives no clear structural
    signal; the caller falls back to the excerpt-heading scope, if any.
    """
    if not sentence:
        return None
    m = _GROUP_OWNED_SEGMENT_RE.search(sentence)
    if m:
        label = _clean_scope_label(m.group(1))
        if label and label.lower() not in _GENERIC_SCOPE_BLOCKLIST:
            return label
    m = _NAMED_SUBJECT_RE.search(sentence)
    if m:
        raw_subject = m.group(1)
        lowered = " ".join(raw_subject.split()).strip(" .,").lower()
        if lowered in _GROUP_SUBJECT_WORDS or "consolidated" in lowered:
            return "group"
        if lowered and lowered not in _GENERIC_SCOPE_BLOCKLIST:
            return _clean_scope_label(raw_subject)
    return None


def _sentence_around(text: str, pos: int) -> str:
    """The sentence (bounded by ``. ! ? \\n``) containing position ``pos``."""
    start = max((text.rfind(ch, 0, pos) for ch in ".!?\n"), default=-1)
    start = start + 1 if start >= 0 else 0
    ends = [i for i in (text.find(ch, pos) for ch in ".!?\n") if i != -1]
    end = min(ends) + 1 if ends else len(text)
    return text[start:end]


def _period_near(text: str, pos: int, *, window: int = 120) -> str | None:
    """Best-effort period from the year NEAREST ``pos`` (a matched value's own
    position) rather than the first year mentioned anywhere in the excerpt —
    a comparative aside elsewhere in the excerpt ("...up from EUR8.1bn in
    2025...") must never steal an unrelated fact's period (a real,
    live-observed failure — Phase 32A corrective)."""
    lo, hi = max(0, pos - window), min(len(text), pos + window)
    m = re.search(r"\b(19|20)\d{2}\b", text[lo:hi])
    if m:
        return m.group(0)
    m = re.search(r"\b(19|20)\d{2}\b", text)
    return m.group(0) if m else None


def _parse_excerpt(excerpt: DocumentExcerpt, source_url: str | None) -> list[PrimaryFact]:
    text = excerpt.text
    facts: list[PrimaryFact] = []
    seen_fields: set[str] = set()
    # Best-effort scope inferred from the excerpt's own heading (already carried
    # by ``DocumentExcerpt`` regardless of which extractor produced it), with
    # the immediately-enclosing ancestor heading (Phase 32A corrective,
    # Problem C) as a second signal — a named leaf heading with no generic
    # scope vocabulary of its own (e.g. a specific segment/business-unit
    # name) still resolves when its ancestor heading IS generic
    # segment/business-area vocabulary. ``None`` when neither gives a scope
    # signal — every fact below stays honest.
    scope = _infer_scope(excerpt.heading, excerpt.ancestor_heading)

    def add(fact: PrimaryFact) -> None:
        if fact.field in seen_fields:
            return
        seen_fields.add(fact.field)
        if fact.scope is None and scope is not None:
            fact = fact.model_copy(update={"scope": scope})
        facts.append(fact)

    # -- reporting currency (only if a currency is explicitly present) --------
    _currency_cues = ("in millions", "reporting currency", "in thousands", "in eur", "in chf")
    if any(kw in text.lower() for kw in _currency_cues):
        cur = _find_currency(text)
        if cur:
            add(
                PrimaryFact(
                    field=FIELD_REPORTING_CURRENCY,
                    value=cur,
                    currency=cur,
                    source_url=source_url,
                    excerpt_id=excerpt.excerpt_id,
                    page_number=excerpt.page_number,
                    confidence="high",
                )
            )

    # -- fiscal year ----------------------------------------------------------
    fy = _FISCAL_YEAR_RE.search(text)
    if fy:
        year = fy.group(1)
        add(
            PrimaryFact(
                field=FIELD_FISCAL_YEAR,
                value=year,
                numeric_value=float(year),
                period=year,
                source_url=source_url,
                excerpt_id=excerpt.excerpt_id,
                page_number=excerpt.page_number,
                confidence="high",
            )
        )

    # -- money fields ---------------------------------------------------------
    for field, pattern in _MONEY_FIELDS:
        # Phase 32A corrective — only ever a CLAUSE-SAFE candidate; a nearer
        # but clause-unsafe match (crosses a sentence, semicolon, adversative
        # conjunction, or another metric's label) is skipped entirely rather
        # than used as a fallback.
        #
        # Live CFR staging finding (financial excerpt relevance-ranking
        # dedicated slice, 2026-08-20): taking unconditionally the FIRST
        # clause-safe candidate (purely by text position) let a bare, weak
        # number with NEITHER its own scale nor its own currency — e.g. "31"
        # from "...ended 31 March 2026" following a bare "Sales" heading —
        # win over a genuinely-qualified value stated moments later in the
        # SAME excerpt ("...to EUR22,420 million"), because the weak match
        # merely happened to sit earlier in the text. The whole-excerpt
        # currency-inference fallback below then made this weak match look
        # superficially acceptable (borrowing a currency symbol from
        # elsewhere in the excerpt), silently reporting a wrong headline
        # figure with only a "verify" warning. A candidate that carries its
        # OWN local scale-or-currency is now preferred over one that has
        # neither; the original first-candidate behaviour (and the
        # whole-excerpt inference fallback) is unchanged when NO candidate
        # in this excerpt has its own local signal.
        candidates = list(_iter_clause_safe(pattern, text))
        if not candidates:
            continue
        m = next(
            (
                c
                for c in candidates
                if _scale_word(c.group("scale")) or _find_currency(c.group(0))
            ),
            candidates[0],
        )
        num = _norm_number(m.group("num"))
        if num is None:
            continue
        scale = _scale_word(m.group("scale"))
        currency = _find_currency(m.group(0)) or _find_currency(text)
        # Refuse ambiguity: the same label with two different magnitudes, or a
        # bare number with neither a scale nor a currency (too weak to trust).
        if _ambiguous_multiple(pattern, text, require_scale_or_currency=True):
            continue
        if scale is None and currency is None:
            continue
        conf = "high" if (scale and currency) else "medium"
        warning = (
            None
            if (scale and currency)
            else "Scale or currency inferred from surrounding text; verify."
        )
        # Phase 32A corrective — scope + period are derived from THIS fact's
        # own local sentence (never the whole excerpt), so a segment-scoped
        # figure or a comparative aside elsewhere in the excerpt can never be
        # misattributed to this fact. ``scope=None`` here still lets ``add()``
        # fall back to the excerpt-heading scope, if any.
        sentence = _sentence_around(text, m.start())
        add(
            PrimaryFact(
                field=field,
                value=m.group(0).strip(),
                numeric_value=num,
                unit="currency_amount",
                currency=currency,
                scale=scale,
                scope=_infer_prose_scope(sentence),
                period=_period_near(text, m.start()),
                source_url=source_url,
                excerpt_id=excerpt.excerpt_id,
                page_number=excerpt.page_number,
                confidence=conf,
                parser_warning=warning,
            )
        )

    # -- percent fields (margins) ----------------------------------------------
    # Only ever an EXPLICIT percentage found in the text next to its label —
    # never computed from a profit/revenue pair (module-level guarantee).
    for field, pattern in _PERCENT_FIELDS:
        m = next(_iter_clause_safe(pattern, text), None)
        if not m:
            continue
        num = _norm_number(m.group("num"))
        if num is None:
            continue
        if _ambiguous_multiple(pattern, text):
            continue
        sentence = _sentence_around(text, m.start())
        add(
            PrimaryFact(
                field=field,
                value=m.group(0).strip(),
                numeric_value=num,
                unit="percent",
                scope=_infer_prose_scope(sentence),
                period=_period_near(text, m.start()),
                source_url=source_url,
                excerpt_id=excerpt.excerpt_id,
                page_number=excerpt.page_number,
                confidence="high",
            )
        )

    # -- employees ------------------------------------------------------------
    for rx in (_EMPLOYEES_RE, _EMPLOYEES_RE2):
        em = rx.search(text)
        if em:
            num = _norm_number(em.group(1))
            if num is not None and num >= 10:
                add(
                    PrimaryFact(
                        field=FIELD_EMPLOYEES,
                        value=em.group(1).strip(),
                        numeric_value=num,
                        unit="people",
                        source_url=source_url,
                        excerpt_id=excerpt.excerpt_id,
                        page_number=excerpt.page_number,
                        confidence="medium",
                    )
                )
            break

    return facts


def _year_hint(excerpt: DocumentExcerpt) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", excerpt.text)
    return int(m.group(0)) if m else None


def _year_hint_str(excerpt: DocumentExcerpt) -> str | None:
    """String form of :func:`_year_hint` — ``None`` stays ``None`` (not the
    string ``"None"``); a caller checking ``if fact.period:`` must see an
    honestly-absent period as falsy, not a truthy placeholder string."""
    year = _year_hint(excerpt)
    return str(year) if year is not None else None


def parse_primary_facts(
    extraction: DocumentTextExtraction,
) -> list[PrimaryFact]:
    """Parse conservative, high-confidence primary facts from an extraction.

    Returns an empty list when parsing is weak — the caller then keeps the raw
    excerpt evidence but produces no structured facts (honest under-reporting).
    Every returned fact carries full provenance and ``needs_human_review=True``.
    """
    source_url = extraction.source_url
    out: list[PrimaryFact] = []
    seen: set[str] = set()
    for excerpt in extraction.excerpts:
        for fact in _parse_excerpt(excerpt, source_url):
            # De-dup on (field) across excerpts — first (highest-ranked) wins.
            if fact.field in seen:
                continue
            seen.add(fact.field)
            out.append(fact)
    return out


__all__ = [
    "PrimaryFact",
    "parse_primary_facts",
    "FIELD_LEGAL_NAME",
    "FIELD_REPORTING_CURRENCY",
    "FIELD_FISCAL_YEAR",
    "FIELD_REVENUE",
    "FIELD_OPERATING_PROFIT",
    "FIELD_RECURRING_OPERATING_PROFIT",
    "FIELD_OPERATING_MARGIN",
    "FIELD_RECURRING_OPERATING_MARGIN",
    "FIELD_NET_INCOME",
    "FIELD_FREE_CASH_FLOW",
    "FIELD_OPERATING_FREE_CASH_FLOW",
    "FIELD_OPERATING_CASH_FLOW",
    "FIELD_TOTAL_ASSETS",
    "FIELD_TOTAL_DEBT",
    "FIELD_NET_DEBT",
    "FIELD_NET_CASH",
    "FIELD_CASH",
    "FIELD_TOTAL_EQUITY",
    "FIELD_EMPLOYEES",
]
