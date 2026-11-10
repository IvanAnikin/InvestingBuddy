"""Council, Red Team and Chair claim checks against what they cite — ADR-086.

QA campaign 2026-11 (JPM-6, PYPL-5, PYPL2-5): a claim can cite real evidence and still
state it for the wrong period or scope.

* "Investment Banking revenue was $7.0 billion in Q2 2026, up 42%" — $7.0bn and 42% are
  the SIX-month (H1) figures of the cited filing;
* "Noninterest revenue was $5.2 billion in Q2 2026" — a Corporate-SEGMENT figure, said
  of the firm;
* "Cash and cash equivalents were $8.0 billion as of September 30, 2026" — the FY2025
  balance, dated to a later quarter end;
* "Q3 2026 revenue grew 2% … versus product B (+26%) and product C (+14%)" — Q2 product
  growth spliced into the Q3 results sentence;
* "Return on equity was 15.7%" — the platform's own NI / ending equity, read as the
  issuer's reported ROE.

Citation presence and numeric reconciliation against canonical financials pass all five.

THE CHECK, PER FIGURE
=====================
A figure is a number with a currency, a scale, a percent or a multiple, and is never a
year or a day of a date. Percentages count. For each figure in a claim, the cited sources
that STATE it are found, by value within the rounding the figure is written to. Then:

1. **Period.** The claim's period for that figure is read from its own clause (forward
   mentions such as "FY26 guidance" do not count), else from the sentence when the
   sentence names exactly one period and the clause is not a comparison. The source's
   period is read where it states the figure (its clause, then its sentence), else its
   stored ``period_key``. The claim is wrong only when EVERY source stating the figure
   has a known period and NONE can be the claim's (``claim_period.compatible``).
2. **Scope.** When every source stating the figure is scoped to one named segment (a
   clean name, not a parsed fragment) and the claim never names that segment, the claim
   presents a segment figure as the company's.
3. **A derived ratio.** A return ratio (ROE, ROTCE, ROA, ROIC) whose only support is a
   platform calculation is not the issuer's reported figure: a claim that calls it
   reported is withheld; one that does not say what it is gets "(platform-derived)".

Unknown never fails a claim: a figure no source states, a source with no period, or two
sources that disagree among themselves leave the claim as written.
"""

from __future__ import annotations

import functools
import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.services.ledger import claim_period as cp
from app.services.ledger import segment_hierarchy as sh

RULE_CLAIM_PERIOD = "claim_period_differs_from_cited_source"
RULE_CLAIM_SCOPE = "claim_scope_differs_from_cited_source"
RULE_DERIVED_AS_REPORTED = "derived_ratio_presented_as_reported"
#: ADR-093 — a business line called a (reportable) segment, or the main revenue driver.
RULE_LINE_AS_SEGMENT = "business_line_presented_as_segment"
RULE_LINE_AS_MAIN_DRIVER = "business_line_presented_as_main_revenue_driver"

CLAIM_RULES: tuple[str, ...] = (
    RULE_CLAIM_PERIOD, RULE_CLAIM_SCOPE, RULE_DERIVED_AS_REPORTED,
    RULE_LINE_AS_SEGMENT, RULE_LINE_AS_MAIN_DRIVER,
)

#: A claim longer than this is not checked (kept as written).
MAX_CLAIM_CHARS = 1200
#: Source text read per source, figures read per source and per claim, and sources
#: read per claim: a claim is a sentence and cites a handful of items. A figure past a
#: cap is not checked (kept), so an adversarial claim or source costs a bounded amount
#: (review P3: < 0.5 s).
MAX_SOURCE_CHARS = 2000
MAX_SOURCE_FIGURES = 40
MAX_SOURCE_SENTENCES = 30
MAX_CLAIM_FIGURES = 12
MAX_SOURCES = 8
DERIVED_LABEL = "(platform-derived)"

_CURRENCY = (
    r"us\$|a\$|c\$|nz\$|hk\$|s\$|\$a(?=[ \t]?\d)|\$|€|£|"
    r"\b(?:usd|aud|eur|gbp|cad|chf|dkk|sek|nok|jpy|cny|zar|inr)\b"
)
_FIGURE_RE = re.compile(
    r"(?:(?P<cur>" + _CURRENCY + r")[ \t]{0,2}|(?<![\w.,]))"
    r"(?P<sign>[-+−])?(?P<num>\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"(?:[ \t]{0,2}(?P<unit>%|percent\b|per[ \t]cent\b|bn\b|billion\b|b\b|mn\b|million\b|"
    r"m\b|k\b|thousand\b|x\b|×))?",
    re.IGNORECASE,
)
_HAS_DIGIT_RE = re.compile(r"\d")
_SCALES = {"bn": 1e9, "billion": 1e9, "b": 1e9, "mn": 1e6, "million": 1e6, "m": 1e6,
           "k": 1e3, "thousand": 1e3}
_UUID_RE = re.compile(r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b", re.IGNORECASE)
_ID_RE = re.compile(r"\b(?:F|E|D)\d{1,4}\b|\bfinding_id\b|\bev:[\w:]{1,80}", re.IGNORECASE)
#: Where a claim's clause ends: ";", a comma before a connective, or a contrast.
_CLAUSE_SPLIT_RE = re.compile(
    r";|\n|,[ \t]{1,3}(?=(?:with|while|whereas|but|and|compared|versus|vs|against|up|down|"
    r"from|as|after|before|including|though|although)\b)"
    r"|[ \t]{1,3}(?=(?:versus|vs\.?|whereas|while|but|though|although)\b)",
    re.IGNORECASE,
)
#: A clause stating a forecast, guidance or target: its figure belongs to the period it
#: forecasts, not to the period of the document that states it, so neither the claim's
#: nor the source's period is compared for it.
_FORWARD_RE = re.compile(
    r"\b(?:guid(?:ance|ed|es)|outlook|forecasts?|forecasted|projected|projections?|"
    r"target(?:ed|ing)?|budget(?:ed)?|expects?|expected|expectations?|anticipat\w{0,6}|"
    r"will|consensus|plans?[ \t]{1,3}to|planned|seen|sees|on[ \t]{1,3}track|"
    r"set[ \t]{1,3}to|aims?|goals?|estimates?|estimated|midpoint|range|should|could|"
    r"would|might|pro[- ]forma)\b",
    re.IGNORECASE,
)
#: A source's sentences and clauses (tables put one row per line).
_SOURCE_SPLIT_RE = re.compile(r"(?<=[.!?])[ \t]{1,3}(?=[A-Z\[(])|\n|;")
#: Words naming a comparison period without naming it: "a year earlier", "the
#: prior-year quarter", "last year", "year-ago".
_COMPARISON_PERIOD_WORDS = (
    r"(?:prior|previous|preceding|last|same)[- \t]{1,3}(?:year|quarter|period|half)"
    r"|year[- \t]ago|year[- \t]earlier|a[ \t]{1,3}(?:year|quarter)[ \t]{1,3}"
    r"(?:earlier|ago|before)|last[ \t]{1,3}year"
)
#: A clause about ANOTHER period than the sentence's: it never inherits the sentence's.
_COMPARATIVE_RE = re.compile(
    r"\b(?:" + _COMPARISON_PERIOD_WORDS + r"|from|restated|comparative)\b",
    re.IGNORECASE,
)
#: A comparison cue IMMEDIATELY before a figure ("from $7.9bn", "versus $7.9bn",
#: "compared with $7.9bn", "up from the prior-year $7.9bn"): the figure is the
#: comparison's, not the clause's own period's (review P1-1). "versus stronger BNPL
#: (+26%)" is a contrast of two products in one period, not a comparison period: the cue
#: must be followed by nothing but the figure, an article or comparison-period words.
_COMPARISON_CUE_RE = re.compile(
    r"\b(?:from|versus|vs\.?|compared[ \t]{1,3}(?:with|to)|against|than)"
    r"(?:[ \t]{1,3}(?:the|a|an|its))?"
    r"(?:[ \t]{1,3}(?:" + _COMPARISON_PERIOD_WORDS + r")(?:['’]s)?)?"
    r"[ \t(:]{0,4}$",
    re.IGNORECASE,
)
#: Comparison-period words right AFTER a figure ("$7.9bn a year earlier", "$7.9bn in the
#: prior-year quarter").
_COMPARISON_AFTER_RE = re.compile(r"\b(?:" + _COMPARISON_PERIOD_WORDS + r")\b", re.IGNORECASE)
#: A cumulative figure: nine months, year to date, trailing twelve months. Inside a
#: quarterly text its period is not the quarter's (review P3).
_CUMULATIVE_RE = re.compile(
    r"\b(?:nine[- \t]months?|9[- \t]?months?|9m|year[- \t]to[- \t]date|ytd|"
    r"trailing[- \t](?:twelve|12)[- \t]months?|ttm|ltm|last[ \t]{1,3}twelve[ \t]{1,3}months)\b",
    re.IGNORECASE,
)
#: A percentage that is a TERM, not a period's result: an ownership stake, a royalty, a
#: coupon, a tax or interest rate (review P2-6). Read just after the figure …
_TERM_AFTER_RE = re.compile(
    r"^[ \t]{0,3}(?:-[ \t]{0,2})?(?:owned|ownership|stake|interest|equity[ \t]{1,3}interest|"
    r"holding|shareholding|royalty|royalties|nsr|coupon|tax[ \t]{1,3}rate|interest[ \t]{1,3}"
    r"rate|rate|margin[ \t]{1,3}requirement)\b",
    re.IGNORECASE,
)
#: … or just before it ("a royalty of 2.5%", "a stake of 50%", "tax rate was 21%").
_TERM_BEFORE_RE = re.compile(
    r"\b(?:royalty|royalties|coupon|tax[ \t]{1,3}rate|interest[ \t]{1,3}rate|"
    r"margin[ \t]{1,3}requirement|stake|ownership|owns|owned|holds|holding|"
    r"interest[ \t]{1,3}(?:of|in))\b[^.;\d]{0,20}$",
    re.IGNORECASE,
)
#: Upper-case tokens that are never a segment's acronym.
_NOT_SEGMENT_ACRONYMS: frozenset[str] = frozenset({
    "FY", "CY", "YOY", "QOQ", "YTD", "TTM", "LTM", "USD", "EUR", "GBP", "AUD", "CAD", "CHF",
    "JPY", "CNY", "EPS", "ROE", "ROA", "ROIC", "ROTCE", "CET", "EBIT", "EBITDA", "NII",
    "GAAP", "IFRS", "TPV", "BNPL", "CEO", "CFO", "AGM", "NAV", "AUM", "US", "UK", "EU",
})
_ACRONYM_RE = re.compile(r"\b[A-Z][A-Z&]{1,4}\b")
_GROUP_WORDS_RE = re.compile(
    r"\b(?:group|firm|firm-?wide|consolidated|company-wide|total[ \t]{1,3}company)\b",
    re.IGNORECASE,
)
_RATIO_RE = re.compile(
    r"\b(?P<roe>return[ \t]{1,3}on[ \t]{1,3}(?:average[ \t]{1,3})?(?:common[ \t]{1,3})?"
    r"(?:shareholders'?[ \t]{1,3}|stockholders'?[ \t]{1,3})?equity|roe)\b"
    r"|\b(?P<rotce>return[ \t]{1,3}on[ \t]{1,3}(?:average[ \t]{1,3})?tangible[ \t]{1,3}"
    r"(?:common[ \t]{1,3})?equity|rotce)\b"
    r"|\b(?P<roa>return[ \t]{1,3}on[ \t]{1,3}(?:average[ \t]{1,3})?(?:total[ \t]{1,3})?"
    r"assets|roa)\b"
    r"|\b(?P<roic>return[ \t]{1,3}on[ \t]{1,3}invested[ \t]{1,3}capital|roic)\b",
    re.IGNORECASE,
)
_REPORTED_RE = re.compile(
    r"\b(?:reported|reports|as[ \t]{1,3}reported|disclosed|discloses|stated|states|"
    r"according[ \t]{1,3}to[ \t]{1,3}the[ \t]{1,3}(?:company|issuer|filing|bank))\b",
    re.IGNORECASE,
)
_DERIVED_WORDS_RE = re.compile(
    r"\b(?:calculated|computed|derived|platform|implied|our[ \t]{1,3}calculation|"
    r"estimated)\b|platform-derived",
    re.IGNORECASE,
)
_CLEAN_SCOPE_NAME_RE = re.compile(r"^[a-z][a-z&' -]{1,40}$")
_SCOPE_FILLER = frozenset({"segment", "segments", "division", "business", "unit", "the",
                           "and", "of", "brand", "reportable", "operating"})


@dataclass(frozen=True)
class ClaimSource:
    """One cited finding or evidence item, as the check reads it."""

    source_id: str
    text: str = ""
    #: The stored period key (``period_key`` / an evidence item's ``period``).
    period: str | None = None
    #: The stored scope key ("group", "segment:<name>", "brand:<name>", …).
    scope: str | None = None
    #: A platform calculation, not a reported figure.
    derived: bool = False
    #: Return ratios a derived source carries ("roe", "rotce", "roa", "roic").
    ratio_keys: frozenset[str] = frozenset()


@dataclass(frozen=True)
class ClaimVerdict:
    rule: str
    reason: str
    #: A repaired text (a derived label added); ``None`` means withhold.
    replacement: str | None = None
    source_ids: tuple[str, ...] = field(default_factory=tuple)


@dataclass(frozen=True)
class _Figure:
    kind: str  # "pct" | "mult" | "amt"
    value: float
    decimals: int
    scale: float | None
    currency: bool
    text: str
    start: int


# ── Figures ──────────────────────────────────────────────────────────────── #


def _blank(text: str, spans: Iterable[tuple[int, int]]) -> str:
    chars = list(text)
    for start, end in spans:
        for i in range(max(0, start), min(len(chars), end)):
            chars[i] = " "
    return "".join(chars)


def _masked(text: str) -> str:
    """The text with ids and period mentions blanked: a day of a date, a quarter
    number or an id is never a figure."""
    spans = [(m.start(), m.end()) for m in _UUID_RE.finditer(text)]
    spans += [(m.start(), m.end()) for m in _ID_RE.finditer(text)]
    spans += [(p.start, p.end) for p in cp.mentions(text, classify=False)]
    return _blank(text, spans)


#: ``figures`` reads at most this much text and returns at most this many figures,
#: whatever its caller passes (a claim is <= ``MAX_CLAIM_CHARS``, a source is read to
#: ``MAX_SOURCE_CHARS``).
MAX_FIGURE_TEXT_CHARS = 4000
MAX_FIGURES = 200


def figures(text: str, limit: int | None = None) -> list[_Figure]:
    """Every figure in ``text`` (ids, dates and period labels masked first), at most
    ``limit`` (and never more than ``MAX_FIGURES`` from the first
    ``MAX_FIGURE_TEXT_CHARS`` characters)."""
    out: list[_Figure] = []
    limit = MAX_FIGURES if limit is None else min(limit, MAX_FIGURES)
    if limit <= 0:
        return out
    for m in _FIGURE_RE.finditer(_masked(text[:MAX_FIGURE_TEXT_CHARS])):
        if len(out) >= limit:
            break
        number = m.group("num").replace(",", "")
        try:
            value = abs(float(number))
        except ValueError:
            continue
        unit = re.sub(r"[ \t]+", " ", (m.group("unit") or "").lower())
        currency = bool(m.group("cur"))
        decimals = len(number.split(".", 1)[1]) if "." in number else 0
        if unit in ("%", "percent", "per cent"):
            kind, scale = "pct", None
        elif unit in ("x", "×"):
            kind, scale = "mult", None
        else:
            kind, scale = "amt", _SCALES.get(unit)
            if not currency and scale is None and decimals == 0:
                # A bare integer is a year, a count, a section or note number, a page:
                # never a figure these rules compare ("section 172", "25 targets").
                continue
        out.append(_Figure(kind, value, decimals, scale, currency, m.group(0).strip(),
                           m.start()))
    return out


_FigureKey = tuple[str, float, int, float | None]


def same_figure(a: _Figure, b: _Figure) -> bool:
    """One value written twice, within the rounding either is written to."""
    if a.kind != b.kind:
        return False
    return _same_key((a.kind, a.value, a.decimals, a.scale),
                     (b.kind, b.value, b.decimals, b.scale))


@functools.lru_cache(maxsize=16384)
def _same_key(a: _FigureKey, b: _FigureKey) -> bool:
    """``same_figure`` on value keys, memoised: a report repeats the same figures in
    many sentences, each compared with the same cited sources."""
    kind, value_a, decimals_a, scale_a = a
    _kind, value_b, decimals_b, scale_b = b
    tol_a, tol_b = 0.5 * (10 ** -decimals_a), 0.5 * (10 ** -decimals_b)
    if kind != "amt":
        return abs(value_a - value_b) <= max(tol_a, tol_b) + 1e-9
    scales_a = (scale_a,) if scale_a else (1.0, 1e3, 1e6, 1e9)
    scales_b = (scale_b,) if scale_b else (1.0, 1e3, 1e6, 1e9)
    for sa in scales_a:
        for sb in scales_b:
            if abs(value_a * sa - value_b * sb) <= max(tol_a * sa, tol_b * sb) + 1e-9:
                return True
    return False


# ── Clauses and their periods ────────────────────────────────────────────── #


def _clauses(text: str, splitter: re.Pattern[str]) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    start = 0
    for m in splitter.finditer(text):
        if m.start() > start:
            out.append((start, m.start()))
        start = m.end() if m.end() > m.start() else m.start()
    if start < len(text):
        out.append((start, len(text)))
    return out


def _distinct(periods: Iterable[cp.Period]) -> list[cp.Period]:
    seen: dict[str, cp.Period] = {}
    for p in periods:
        seen.setdefault(p.key, p)
    return list(seen.values())


@dataclass(frozen=True)
class _Stated:
    """A figure a source states, and the period it states it for (``None``: unknown)."""

    figure: _Figure
    period: cp.Period | None


#: Why a figure has no period of its own: it is a forecast, a comparison's, cumulative,
#: or a term (a stake, a royalty, a rate). ``None``: it has one (or inherits one).
_UNBOUND_FORWARD = "forward"
_UNBOUND_COMPARISON = "comparison"
_UNBOUND_CUMULATIVE = "cumulative"
_UNBOUND_TERM = "term"


def _unbound(clause: str, fig: _Figure, prev_end: int, next_start: int, *,
             forward: bool, cumulative: bool) -> str | None:
    """Whether ``fig`` (inside ``clause``) states no period of the clause's own.

    * a forecast, guidance, target, outlook clause (``forward``: ``_FORWARD_RE``);
    * a comparison figure: a cue immediately before it ("from $7.9bn", "versus
      $7.9bn") or comparison-period words just after it ("$7.9bn a year earlier");
    * a cumulative figure (``cumulative``: nine months, year to date) unless its
      period says so;
    * a percentage that is a term (a stake, a royalty, a coupon, a rate).
    """
    if forward:
        return _UNBOUND_FORWARD
    end = fig.start + len(fig.text)
    before = clause[max(prev_end, fig.start - 60):fig.start]
    after = clause[end:min(next_start, end + 40)]
    if _COMPARISON_CUE_RE.search(before) or _COMPARISON_AFTER_RE.search(after):
        return _UNBOUND_COMPARISON
    if cumulative:
        return _UNBOUND_CUMULATIVE
    if fig.kind == "pct" and (
        _TERM_AFTER_RE.search(clause[end:end + 30])
        or _TERM_BEFORE_RE.search(clause[max(0, fig.start - 35):fig.start])
    ):
        return _UNBOUND_TERM
    return None


def _bind(clause: str, figs: Sequence[_Figure], local: Sequence[cp.Period],
          clause_start: int) -> list[tuple[_Figure, str | None, list[cp.Period]]]:
    """Each figure of one clause, why it is unbound (or ``None``), and the periods it
    is stated for in the clause: a comparison figure only the periods named after its
    cue (none: unknown); a cumulative figure only a nine-month / year span; any other
    figure every period the clause names (``local``)."""
    out: list[tuple[_Figure, str | None, list[cp.Period]]] = []
    forward = bool(figs) and _FORWARD_RE.search(clause) is not None
    cumulative = bool(figs) and _CUMULATIVE_RE.search(clause) is not None
    for i, fig in enumerate(figs):
        prev_end = figs[i - 1].start + len(figs[i - 1].text) if i else 0
        next_start = figs[i + 1].start if i + 1 < len(figs) else len(clause)
        why = _unbound(clause, fig, prev_end, next_start, forward=forward,
                       cumulative=cumulative)
        if why == _UNBOUND_COMPARISON:
            # Its own period is one named after it, before the next figure ("versus
            # $7.9bn in Q3 2025"); none named: unknown.
            at, hi = clause_start + fig.start, clause_start + next_start
            out.append((fig, why, _distinct(p for p in local if at <= p.start < hi)))
        elif why == _UNBOUND_CUMULATIVE:
            out.append((fig, why, [p for p in local if p.span in (cp.SPAN_NINE, cp.SPAN_YEAR)
                                   and p.label is None]))
        elif why is not None:
            out.append((fig, why, []))
        else:
            out.append((fig, None, list(local)))
    return out


@functools.lru_cache(maxsize=1024)
def _source_index(source: ClaimSource) -> tuple[_Stated, ...]:
    """Every figure the source states, each with its period: read where the source
    states it (its clause, then its sentence), else the stored key — but only when the
    source text is UNAMBIGUOUS about its period (review P1-2): a text naming more than
    one period or date, or a table header with two years, never lends its stored key to
    a figure. Bare dates count as periods here ("September 30, 2026 $ 6,120").
    Bounded by ``MAX_SOURCE_CHARS`` / ``MAX_SOURCE_FIGURES``; cached per source."""
    text = source.text[:MAX_SOURCE_CHARS]
    stored = cp.period_of_key(source.period) if source.period else None
    if stored is not None and (
        cp.has_two_year_header(text)
        # Unclassified: a forward mention also makes the text name two periods, which
        # only ever withholds the stored key (keeps the claim).
        or len(_distinct(cp.mentions(text, classify=False, bare_dates=True))) > 1
    ):
        stored = None
    out: list[_Stated] = []
    for s_start, s_end in _clauses(text, _SOURCE_SPLIT_RE)[:MAX_SOURCE_SENTENCES]:
        sentence = text[s_start:s_end]
        if not _HAS_DIGIT_RE.search(sentence):
            continue
        periods = [p for p in cp.mentions(sentence, bare_dates=True) if not p.is_forward]
        sentence_distinct = _distinct(periods)
        if len(sentence_distinct) == 1:
            sentence_period: cp.Period | None = sentence_distinct[0]
        elif sentence_distinct:
            sentence_period = None
        else:
            sentence_period = stored
        for c_start, c_end in _clauses(sentence, _CLAUSE_SPLIT_RE):
            clause = sentence[c_start:c_end]
            local = _distinct(p for p in periods if c_start <= p.start < c_end)
            figs = figures(clause, MAX_SOURCE_FIGURES - len(out))
            for fig, why, bound in _bind(clause, figs, local, c_start):
                if why is not None:
                    found = _distinct(bound)
                    period = found[0] if len(found) == 1 else None
                elif local:
                    period = local[0] if len(local) == 1 else None
                elif _COMPARATIVE_RE.search(clause):
                    period = None  # another period than the sentence's, unnamed
                else:
                    period = sentence_period
                out.append(_Stated(fig, period))
            if len(out) >= MAX_SOURCE_FIGURES:
                return tuple(out)
    return tuple(out)


def _holders(fig: _Figure, indexed: Sequence[tuple[ClaimSource, Sequence[_Stated]]]
             ) -> list[tuple[ClaimSource, cp.Period | None]]:
    """The sources stating ``fig``. A source stating it twice for two periods is
    unknown (``None``) unless one reading is shared."""
    out: list[tuple[ClaimSource, cp.Period | None]] = []
    for source, stated in indexed:
        periods = [s.period for s in stated if same_figure(fig, s.figure)]
        if not periods:
            continue
        known = _distinct(p for p in periods if p is not None)
        out.append((source, known[0] if len(known) == 1 and None not in periods else None))
    return out


# ── Scope ────────────────────────────────────────────────────────────────── #


def _segment_name(scope: str | None) -> str | None:
    """A CLEAN segment / brand name from a scope key, or ``None`` (group, unknown, a
    parsed fragment such as "2. segment reporting")."""
    if not scope or ":" not in scope:
        return None
    kind, _sep, name = scope.partition(":")
    if kind not in ("segment", "brand", "division"):
        return None
    name = name.strip().lower()
    if not _CLEAN_SCOPE_NAME_RE.match(name):
        return None
    words = [w for w in re.split(r"[ \t]+", name) if w and w not in _SCOPE_FILLER]
    if not words or len(name.split()) > 4:
        return None
    return " ".join(words)


_INITIAL_SKIP: frozenset[str] = frozenset({"&", "and", "of", "the"})


def _spelled_out(claim: str, name: str) -> bool:
    """ADR-091 — the source names the segment by an acronym ("CIB segment") and the
    claim spells it out ("the Commercial & Investment Bank segment")."""
    if " " in name or not 2 <= len(name) <= 5 or not name.isalpha():
        return False
    stated = stated_segment(claim)
    if stated is None:
        return False
    words = [w for w in stated.removeprefix("segment:").split() if w not in _INITIAL_SKIP]
    return "".join(w[0] for w in words if w) == name


def _names_scope(claim: str, name: str, hierarchy: sh.SegmentHierarchy | None = None) -> bool:
    """The claim names the segment: one of its words, or (review P3) a short upper-case
    acronym — "AWM" for "asset & wealth management". An acronym that is not obviously
    something else is taken as the segment's: the check cannot tell, so it keeps.

    ADR-093: when the issuer's hierarchy knows the segment, the claim names it only by
    its name, its acronym or one of its business lines ("Example Holdings Bank" does not
    name the "Commercial Bank" segment)."""
    segment = hierarchy.segment(name) if hierarchy else None
    if hierarchy and segment is None:
        line = hierarchy.line(name)
        segment = hierarchy.segment(line.segment) if line else None
    if hierarchy and segment is not None:
        return segment.name in sh.names_in(claim, hierarchy, owned_only=False).owners
    claim_low = claim.lower()
    if any(len(w) >= 4 and w in claim_low for w in name.split()) or name in claim_low:
        return True
    if _spelled_out(claim, name):
        return True
    return any(token not in _NOT_SEGMENT_ACRONYMS
               for token in _ACRONYM_RE.findall(claim[:MAX_CLAIM_CHARS]))


# ── The check ────────────────────────────────────────────────────────────── #


def _period_words(periods: Sequence[cp.Period]) -> str:
    """Reader labels ("Q3 2026", "six months to 30 Jun 2026"), never stored keys."""
    return " / ".join(dict.fromkeys(p.human() for p in periods[:3]))


def _derived_ratio(clause: str, figs: Sequence[_Figure],
                   indexed: Sequence[tuple[ClaimSource, Sequence[_Stated]]]
                   ) -> tuple[str, re.Match[str]] | None:
    match = _RATIO_RE.search(clause)
    if match is None or not any(f.kind == "pct" for f in figs):
        return None
    key = next(k for k in ("roe", "rotce", "roa", "roic") if match.group(k))
    relevant = [s for s, _stated in indexed if key in s.ratio_keys]
    for fig in figs:
        if fig.kind != "pct":
            continue
        relevant += [s for s, _p in _holders(fig, indexed) if s not in relevant]
    if relevant and all(s.derived for s in relevant):
        return key, match
    return None


def check_claim(
    text: str | None,
    sources: Sequence[ClaimSource],
    hierarchy: sh.SegmentHierarchy | None = None,
) -> ClaimVerdict | None:
    """The first way ``text`` misstates what it cites, or ``None``. Pure; never
    raises. ``hierarchy`` (ADR-093): the issuer's segments and business lines — a claim
    is then also held to them (``check_hierarchy``, the metric scope rule)."""
    claim = str(text or "")
    if not claim.strip() or len(claim) > MAX_CLAIM_CHARS:
        return None
    try:
        verdict = check_hierarchy(claim, hierarchy)
        if verdict is not None or not sources:
            return verdict
        return _check(claim, sources, hierarchy) or metric_scope(claim, sources, hierarchy)
    except (TypeError, ValueError, IndexError):  # pragma: no cover - defensive
        return None


# ── ADR-093: the segment hierarchy ─────────────────────────────────────────── #

#: "primarily driven by (its)", "mainly generated from", "led by" — right before a name.
_DRIVER_BEFORE_RE = re.compile(
    r"\b(?:(?:primarily|mainly|mostly|largely|chiefly|predominantly|principally)[ \t]{1,3}"
    r"(?:driven|generated|led|derived|earned|produced)|(?:driven|generated|derived)"
    r"[ \t]{1,3}(?:primarily|mainly|mostly|largely|chiefly|predominantly|principally))"
    r"[ \t]{1,3}(?:by|from)(?:[ \t]{1,3}(?:its|the|their|our))?[ \t]{0,3}$"
    r"|\b(?:main|largest|biggest|primary|principal|leading|dominant|key)[ \t]{1,3}"
    r"(?:revenue[ \t]{1,3}|earnings[ \t]{1,3}|profit[ \t]{1,3})?(?:driver|source|contributor|"
    r"segment|business|engine)[ \t]{1,3}(?:is|was|remains|being)(?:[ \t]{1,3}(?:its|the))?"
    r"[ \t]{0,3}$",
    re.IGNORECASE,
)
#: "<name> (segment) is the largest / main / dominant …" — right after a name.
_DRIVER_AFTER_RE = re.compile(
    r"^[ \t]{0,3}(?:(?:segment|division|business)[ \t]{1,3})?(?:is|was|remains)[ \t]{1,3}"
    r"(?:the|its|their)[ \t]{1,3}(?:single[ \t]{1,3})?(?:largest|main|biggest|primary|"
    r"principal|leading|dominant|key)\b",
    re.IGNORECASE,
)
#: "<business line> segment" / "division" / "reportable segment" right after a name.
_AS_SEGMENT_AFTER_RE = re.compile(
    r"^[ \t]{1,3}(?:reportable[ \t]{1,3}|operating[ \t]{1,3})?(?:segment|division)\b(?!s)",
    re.IGNORECASE,
)
_REVENUE_WORDS_RE = re.compile(
    r"\b(?:revenues?|sales|income|earnings|turnover|profits?)\b", re.IGNORECASE)


def check_hierarchy(claim: str, hierarchy: sh.SegmentHierarchy | None) -> ClaimVerdict | None:
    """ADR-093 — a claim held to the issuer's own segment hierarchy.

    * **Main driver.** A claim naming a BUSINESS LINE as the main source / driver of
      revenue ("revenue is primarily driven by its Markets segment", "Markets is the
      largest revenue segment") is withheld when the issuer reports more than one
      reportable segment: a line of one segment is not the company's main driver unless
      a source compares it with the other segments, and none of the platform's checks can
      show that. A reportable segment named as the driver is not checked.
    * **Line as segment.** A business line called a "segment" / "division" is rewritten:
      "Markets segment" → "Markets business (part of the Commercial & Investment Bank
      segment)"."""
    if not hierarchy or not hierarchy.business_lines:
        return None
    named = sh.names_in(claim, hierarchy, owned_only=False)
    line_spans = [(s, e, kind[5:], seg) for s, e, kind, seg in named.spans
                  if kind.startswith("line:")]
    if not line_spans:
        return None
    if len(hierarchy.segments) > 1 and _REVENUE_WORDS_RE.search(claim):
        for start, end, line, seg in line_spans:
            if (_DRIVER_BEFORE_RE.search(claim[max(0, start - 80):start])
                    or _DRIVER_AFTER_RE.search(claim[end:end + 60])):
                return ClaimVerdict(
                    RULE_LINE_AS_MAIN_DRIVER,
                    f"it names {line}, a business line of the {seg} segment, as the main "
                    f"revenue driver; the issuer reports {len(hierarchy.segments)} "
                    "reportable segments and no cited source compares them",
                )
    for start, end, line, seg in reversed(line_spans):
        after = _AS_SEGMENT_AFTER_RE.search(claim[end:end + 40])
        if after is None:
            continue
        repaired = (claim[:end] + f" business (part of the {seg} segment)"
                    + claim[end + after.end():])
        return ClaimVerdict(
            RULE_LINE_AS_SEGMENT,
            f"it calls {line} a segment; {line} is a business line of the {seg} segment",
            replacement=repaired,
        )
    return None


#: Words that never make a metric phrase.
_METRIC_STOPWORDS: frozenset[str] = frozenset({
    "a", "an", "the", "and", "or", "of", "in", "on", "at", "to", "for", "from", "by",
    "with", "as", "is", "was", "were", "are", "be", "been", "its", "their", "our", "this",
    "that", "these", "those", "it", "than", "vs", "versus", "compared", "while", "but",
    "year", "years", "quarter", "quarters", "period", "periods", "prior", "previous",
    "fiscal", "month", "months", "total", "reported", "company", "group", "firm",
    "show", "shows", "showed", "indicate", "indicates", "indicating", "modest", "slight",
    "significant", "rising", "falling", "rose", "fell", "increase", "increased",
    "decrease", "decreased", "decline", "declined", "reduction", "growth", "grew",
    "higher", "lower", "up", "down", "over", "earlier", "ago", "same", "recent", "latest",
    "current", "also", "alongside", "both", "which", "has", "had", "have", "not", "no",
    "remain", "remains", "remained", "strong", "weak", "stable", "solid", "some",
})
_METRIC_WORD_RE = re.compile(r"[A-Za-z][A-Za-z-]{1,30}|[^A-Za-z \t]")


def _metric_bigrams(text: str) -> dict[str, int]:
    """Adjacent content-word pairs ("nonperforming assets", "credit losses") and where
    they end. A capitalised pair inside a sentence is a name, not a metric."""
    out: dict[str, int] = {}
    previous: tuple[str, bool] | None = None
    for m in _METRIC_WORD_RE.finditer(text[:MAX_SOURCE_CHARS]):
        token = m.group(0)
        if not token[:1].isalpha():
            previous = None
            continue
        low = token.lower()
        if low in _METRIC_STOPWORDS:
            previous = None
            continue
        capital = token[:1].isupper() and m.start() > 0 and text[m.start() - 2:m.start()] != ". "
        if previous is not None and not (previous[1] and capital):
            out.setdefault(f"{previous[0]} {low}", m.end())
        previous = (low, capital)
    return out


def metric_scope(
    claim: str, sources: Sequence[ClaimSource], hierarchy: sh.SegmentHierarchy | None,
    *, limit: int = MAX_SOURCES,
) -> ClaimVerdict | None:
    """ADR-093 — a metric a claim states WITHOUT a figure ("a modest reduction in
    nonperforming assets"), when every cited source that states a figure for that metric
    is scoped to ONE segment, and the claim does not name it: the segment's metric is
    presented as the company's. Withheld."""
    claim_pairs = _metric_bigrams(claim)
    if not claim_pairs:
        return None
    for pair in claim_pairs:
        holders: list[ClaimSource] = []
        for source in list(sources)[:limit]:
            text = source.text[:MAX_SOURCE_CHARS]
            if pair in _metric_bigrams(text) and figures(text, 1):
                holders.append(source)
        if not holders:
            continue
        names = {_segment_name(s.scope) for s in holders}
        if len(names) != 1 or None in names:
            continue
        name = next(iter(names))
        assert name is not None
        if _names_scope(claim, name, hierarchy):
            continue
        return ClaimVerdict(
            RULE_CLAIM_SCOPE,
            f"it states {pair} without its scope: every cited source states it for the "
            f"{name} segment only",
            source_ids=tuple(s.source_id for s in holders),
        )
    return None


_WS_RE = re.compile(r"\s+")


#: ADR-093 review: the only lead a verbatim copy may add — a short label ending in a
#: colon ("Business model: "). Letters, spaces and & / - only: no digit, no period.
_LABEL_LEAD_RE = re.compile(r"^[a-z][a-z &/-]{0,40}:[ \t]?$")
_TAIL_PUNCT_RE = re.compile(r"^[ \t.;,)\]]{0,4}$")
#: Re-review: a label naming a period in words ("Latest quarter:", "In the third
#: quarter:") is a period claim, not a label.
_LABEL_PERIOD_RE = re.compile(
    r"\b(?:quarter(?:ly)?|year|annual|half|months?|fy|q[1-4]|h[12]|ytd|period|latest|"
    r"current|prior|recent|last|interim|first|second|third|fourth)\b",
    re.IGNORECASE,
)


def _verbatim(claim: str, sources: Sequence[ClaimSource],
              hierarchy: sh.SegmentHierarchy | None = None) -> bool:
    """ADR-093 — the claim IS a cited source's own text (an editor sentence copying its
    finding): it cannot misstate what it cites.

    Review P1: the claim must hold the WHOLE source text, and what it adds may only be a
    short label ending in a colon before it and punctuation after it. A label naming a
    period, a figure or a segment / business line is not a label, and a claim that
    drops part of its source (a period, a scope label) is checked as any other."""
    as_written = _WS_RE.sub(" ", claim[:MAX_CLAIM_CHARS]).strip().rstrip(".")
    wanted = as_written.lower()
    if len(wanted) < 20:
        return False
    for source in sources:
        text = _WS_RE.sub(" ", source.text[:MAX_SOURCE_CHARS]).strip().rstrip(".").lower()
        if len(text) < 20:
            continue
        at = wanted.find(text)
        if at < 0:
            continue
        lead, tail = wanted[:at], wanted[at + len(text):]
        if lead and not _LABEL_LEAD_RE.match(lead):
            continue
        if not _TAIL_PUNCT_RE.match(tail):
            continue
        if lead and (cp.mentions(lead, classify=False) or _LABEL_PERIOD_RE.search(lead)
                     or (hierarchy is not None
                         and sh.names_in(as_written[:at], hierarchy,
                                         owned_only=False).owners)):
            continue
        return True
    return False


def _check(claim: str, sources: Sequence[ClaimSource],
           hierarchy: sh.SegmentHierarchy | None = None) -> ClaimVerdict | None:
    if not figures(claim, 1):
        return None  # nothing to check: no source is read
    if _verbatim(claim, sources, hierarchy):
        return None
    indexed = [(source, _source_index(source)) for source in list(sources)[:MAX_SOURCES]]
    mentions = cp.mentions(claim)
    stated = [p for p in mentions if not p.is_forward]
    sentence_periods = _distinct(p for p in stated if not p.is_historical)
    #: Every period the claim names (comparison periods included): a source stating the
    #: figure for ANY of them is not a positive mismatch (review P1-1).
    claim_periods = _distinct(stated)
    budget = MAX_CLAIM_FIGURES
    for c_start, c_end in _clauses(claim, _CLAUSE_SPLIT_RE):
        clause = claim[c_start:c_end]
        figs = figures(clause, budget)
        if not figs:
            continue
        budget -= len(figs)
        derived = _derived_ratio(clause, figs, indexed)
        if derived is not None:
            key, match = derived
            if _REPORTED_RE.search(clause):
                return ClaimVerdict(
                    RULE_DERIVED_AS_REPORTED,
                    f"it presents a platform-derived {key.upper()} as reported; the cited "
                    "support is the platform's own calculation, not the issuer's figure",
                )
            if not _DERIVED_WORDS_RE.search(clause):
                at = c_start + match.end()
                return ClaimVerdict(
                    RULE_DERIVED_AS_REPORTED,
                    f"its {key.upper()} is the platform's own calculation, labelled as such",
                    replacement=f"{claim[:at]} {DERIVED_LABEL}{claim[at:]}",
                )
        local = _distinct(p for p in stated if c_start <= p.start < c_end)
        if local:
            inherited = local
        elif _COMPARATIVE_RE.search(clause) or len(sentence_periods) != 1:
            inherited = []
        else:
            inherited = sentence_periods
        for fig, why, bound in _bind(clause, figs, local, c_start):
            holders = _holders(fig, indexed)
            if not holders:
                continue
            # A forecast or a term is checked for nothing but its scope; a comparison
            # or cumulative figure only against the period it names itself.
            periods = bound if why is not None else inherited
            if (
                periods and why not in (_UNBOUND_FORWARD, _UNBOUND_TERM)
                and all(p is not None for _s, p in holders)
                and not any(cp.any_compatible(periods, [p])
                            for _s, p in holders if p is not None)
                and not any(cp.any_compatible(claim_periods, [p])
                            for _s, p in holders if p is not None)
            ):
                first_source, first_period = holders[0]
                assert first_period is not None
                return ClaimVerdict(
                    RULE_CLAIM_PERIOD,
                    f"it states {fig.text} for {_period_words(periods)}, but the cited "
                    f"source states it for {first_period.human()}",
                    source_ids=tuple(s.source_id for s, _p in holders),
                )
            names = {_segment_name(s.scope) for s, _p in holders}
            if len(names) == 1 and None not in names:
                name = next(iter(names))
                assert name is not None
                if not _names_scope(claim, name, hierarchy):
                    return ClaimVerdict(
                        RULE_CLAIM_SCOPE,
                        f"it states {fig.text} without its scope: the cited source states "
                        f"it for the {name} segment only"
                        + (", not the whole company" if _GROUP_WORDS_RE.search(clause)
                           else ""),
                        source_ids=tuple(s.source_id for s, _p in holders),
                    )
    return None


def ratio_keys_of(text: str | None) -> frozenset[str]:
    """Return ratios a text names (for a derived source)."""
    found: set[str] = set()
    for m in _RATIO_RE.finditer(str(text or "")[:MAX_SOURCE_CHARS].replace("_", " ")):
        found.update(k for k in ("roe", "rotce", "roa", "roic") if m.group(k))
    return frozenset(found)


#: Finding source kinds that are a platform calculation, not a reported figure.
DERIVED_SOURCE_KINDS: frozenset[str] = frozenset({"platform_calculation"})


#: ADR-091 — a statement that names ONE segment by its capitalised name: "In the
#: Corporate segment, net revenue was $93m", "the Commercial & Investment Bank segment".
#: Bounded repetition over a closed token class; the plural ("segments") lists several.
_STATED_SEGMENT_RE = re.compile(
    r"\b(?P<name>[A-Z][\w&'’-]{0,30}(?:[ \t]{1,3}(?:&[ \t]{1,3})?[A-Z][\w&'’-]{0,30}){0,3})"
    r"[ \t]{1,3}(?:segment|division|business[ \t]{1,3}unit|line[ \t]{1,3}of[ \t]{1,3}"
    r"business)\b(?!s)"
)
_NOT_A_SEGMENT_NAME: frozenset[str] = frozenset(
    {"group", "the group", "each", "every", "reportable", "operating", "the", "one",
     "another", "other", "this", "that", "its"}
)


#: Review P1-2 — whole-company wording: the statement's figure is the Group's, whatever
#: segment it also mentions. "(not Group)" / "non-group" is not such wording.
_WHOLE_COMPANY_RE = re.compile(
    r"(?<!not )(?<!non-)\b(?:group|total|consolidated|firm-?wide|group-?wide|"
    r"company-?wide|overall|worldwide)\b",
    re.IGNORECASE,
)
#: A segment that is only MENTIONED next to a figure, never its owner.
_MENTIONED_SEGMENT_RE = re.compile(
    r"\b(?:driven[ \t]{1,3}by|led[ \t]{1,3}by|helped[ \t]{1,3}by|excluding|excl\.?|"
    r"except(?:[ \t]{1,3}for)?|other[ \t]{1,3}than|outside|sale[ \t]{1,3}of|disposal[ \t]{1,3}of|"
    r"divestment[ \t]{1,3}of|divestiture[ \t]{1,3}of|exit[ \t]{1,3}from|spin-?off[ \t]{1,3}of)"
    r"(?:[ \t]{1,3}[\w&'’-]{1,30}){0,4}[ \t]{0,3}$",
    re.IGNORECASE,
)
_SEGMENT_CLAUSE_RE = re.compile(r";|\.(?:[ \t]|$)|\n")


def stated_segment(statement: str | None) -> str | None:
    """ADR-091 — ``segment:<name>`` when an unscoped finding's own words name exactly
    one segment that OWNS its figure, else ``None``. Used only for the claim check: a
    chair claim restating such a figure without the segment is a figure presented as the
    company's.

    Review P1-2: the segment must sit in the same clause as a figure and before it; a
    segment named after "driven by", "excluding", "sale of" … is only mentioned; and any
    whole-company wording (group, total, consolidated, firm-wide) means the figure is the
    company's."""
    text = str(statement or "")[:600]
    if _WHOLE_COMPANY_RE.search(text):
        return None
    names: set[str] = set()
    start = 0
    bounds = [m.start() for m in _SEGMENT_CLAUSE_RE.finditer(text)] + [len(text)]
    for end in bounds:
        clause = text[start:end]
        offset = start
        start = end + 1
        figs = figures(clause, 4)
        if not figs:
            continue
        first = figs[0].start
        for m in _STATED_SEGMENT_RE.finditer(clause):
            if m.start() > first:
                continue
            if _MENTIONED_SEGMENT_RE.search(text[max(0, offset + m.start() - 60):
                                                 offset + m.start()]):
                continue
            names.add(
                m.group("name").strip().lower().removeprefix("the ").removeprefix("in ")
                .strip())
    names = {n for n in names if n not in _NOT_A_SEGMENT_NAME}
    if len(names) != 1:
        return None
    return f"segment:{next(iter(names))}"


def source_of_finding(
    finding: dict[str, object], hierarchy: sh.SegmentHierarchy | None = None,
) -> ClaimSource | None:
    """A payload finding (``v3_research.findings[*]``) as a claim source. ADR-093: an
    unscoped finding naming business lines of one segment (``hierarchy``) carries that
    segment as its claim scope."""
    fid = str(finding.get("finding_id") or "")
    if not fid:
        return None
    raw_kinds = finding.get("source_kinds")
    kinds = ({str(k) for k in raw_kinds if k}
             if isinstance(raw_kinds, (list, tuple, set, frozenset)) else set())
    statement = str(finding.get("statement") or "")
    derived = bool(kinds) and kinds <= DERIVED_SOURCE_KINDS
    named = sh.scope_finding(statement, hierarchy) if hierarchy else None
    return ClaimSource(
        source_id=fid,
        text=statement,
        period=str(finding.get("period_key") or "") or None,
        scope=(str(finding.get("scope_key") or "") or stated_segment(statement)
               or (named.scope_key if named else None)),
        derived=derived,
        ratio_keys=ratio_keys_of(statement) if derived else frozenset(),
    )


#: Legacy evidence-pack items that are computed by the platform, not reported.
DERIVED_EVIDENCE_TYPES: frozenset[str] = frozenset({"derived_financial_metric"})


def source_of_evidence(item: object) -> ClaimSource | None:
    """A legacy evidence-pack ``EvidenceItem`` (an ``E#``) as a claim source.

    Its ``scope`` is a heading label ("group", a segment name, or ``None``); a
    non-group label is read as a segment so a segment figure stated as the
    company's is caught. Its ``period`` is the structured fact's period key."""
    eid = str(getattr(item, "id", "") or "")
    if not eid:
        return None
    label = str(getattr(item, "scope", "") or "").strip().lower()
    scope = None if not label or label == "group" else f"segment:{label}"
    source_type = str(getattr(item, "source_type", "") or "")
    tier = str(getattr(item, "content_tier", "") or getattr(item, "source_tier", "") or "")
    derived = source_type in DERIVED_EVIDENCE_TYPES or tier.startswith("T6")
    excerpt = str(getattr(item, "excerpt", "") or "")
    fields = " ".join(str(f) for f in (getattr(item, "fields_supported", None) or ()))
    return ClaimSource(
        source_id=eid,
        text=excerpt,
        period=str(getattr(item, "period", "") or "") or None,
        scope=scope,
        derived=derived,
        ratio_keys=ratio_keys_of(f"{fields} {excerpt}") if derived else frozenset(),
    )


def cited_ids(text: str | None) -> list[str]:
    """Finding ids written into a claim ("(finding_id: <uuid>, <uuid>)")."""
    return [m.group(0).lower() for m in _UUID_RE.finditer(str(text or "")[:MAX_CLAIM_CHARS])]


__all__ = [
    "CLAIM_RULES",
    "DERIVED_LABEL",
    "DERIVED_EVIDENCE_TYPES",
    "DERIVED_SOURCE_KINDS",
    "RULE_CLAIM_PERIOD",
    "RULE_CLAIM_SCOPE",
    "RULE_DERIVED_AS_REPORTED",
    "RULE_LINE_AS_MAIN_DRIVER",
    "RULE_LINE_AS_SEGMENT",
    "ClaimSource",
    "ClaimVerdict",
    "check_claim",
    "check_hierarchy",
    "cited_ids",
    "figures",
    "ratio_keys_of",
    "same_figure",
    "source_of_evidence",
    "source_of_finding",
]
