"""Canonical numeric reconciliation — V3.0 Slice 4.

WHY THIS MODULE EXISTS
======================
Council prose must not contradict the report's own canonical figures. A report
that shows "revenue of DKK 14,328m" in one section and a council sentence
asserting a different revenue for the same period and scope is not showing two
views; it is contradicting itself, and a reader has no way to know which to
trust.

That check already existed and worked. It lived entirely in the browser
(``apps/web/src/components/research/numericConsistency.ts``), which is the wrong
place for it and is the thing this slice moves. Three concrete consequences of
where it was:

* **The verdict was not in the record.** A report was persisted, an admin
  approved it, and nowhere did the stored research state say which statements
  had been reconciled or which had failed. The decision existed only for as long
  as a browser tab was open.
* **The presentation layer was deciding what is true.** ADR-037 says the product
  layer *presents* the research state and never reconciles it. A guard that
  suppresses a sentence is reconciling.
* **It could only see what it rendered.** The backend holds typed scope, typed
  period, currency, scale and the extractor's own unit; the browser sees whatever
  survived into the view model. The duplicated group-scope vocabulary in the TS
  file is the visible symptom — a hand-maintained copy of
  ``fact_scope.GROUP_SCOPE_LABELS`` that a change on either side could silently
  desynchronise. This module imports the real one.

THE FRONTEND GUARD STAYS
========================
Deliberately, as defence in depth, and the deployment shape is the reason: 1,057
reports already exist and **report content is persisted**, so no existing report
will ever carry a verification record. The browser guard is the only thing that
protects those, and deleting it would silently un-protect the majority of the
corpus. It is also a genuine second opinion on new reports — the two run over the
same figures by different code, and a conflict from either withholds.

WHAT THIS DOES NOT DO
=====================
It does not pick a winner, and it does not edit the prose. A contradiction is
recorded as a contradiction against the statement it concerns; the original text
stays exactly as the council wrote it, because a suppressed sentence that has
been overwritten in the record cannot be reviewed later (CLAUDE.md rule 8), and
silently choosing one of two conflicting numbers is the failure mode the whole
mechanism exists to prevent.

CONSERVATIVE BY CONSTRUCTION
============================
A sentence is adjudicated only when it names a metric this report holds a
canonical value for, AT THE SCOPE THE SENTENCE IS ABOUT. Everything else is
``unchecked``, which is the common and correct answer. Every exclusion below was
learned from a real live report where the guard withheld correct analysis:

* scope is part of the key — a segment figure never adjudicates a Group claim,
  and Group is never assumed for an unscoped sentence (CFR: 32 correct statements
  withheld);
* a multiple ("2.6x equity") is a relationship, never a level (PNDORA);
* a percentage beside an amount metric is a change, not a level, and vice versa
  (CFR: 9 correct Group statements withheld);
* a number that is a DIFFERENT named metric's figure is not evidence against this
  one (PNDORA);
* a period the report does not hold cannot be adjudicated at all (PNDORA: 10 of
  11 false positives);
* a bare year is a date and a small bare integer is a count.

MRNA is the counterweight and the reason none of this may become blanket
suppression: it has **8 genuine** numeric conflicts, and a guard that reports
zero on MRNA is broken, not safe.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Iterable

from app.services.sources.fact_scope import (
    GROUP_SCOPE_LABELS,
    SCOPE_TYPE_GROUP,
    SCOPE_TYPE_SEGMENT,
)

#: Bumped whenever the adjudication semantics change, so a persisted verdict is
#: never assumed compatible with current code — the same discipline
#: ``CURRENT_EXTRACTION_PIPELINE_VERSION`` applies to facts.
NUMERIC_VERIFICATION_VERSION = 1

#: The report-content section this module writes.
SECTION_KEY = "numeric_verification"

CONFLICT_NOTICE = "Conflicting evidence — technical review required."

VERDICT_CONSISTENT = "consistent"
VERDICT_UNCHECKED = "unchecked"
VERDICT_CONFLICTING = "conflicting"

#: The consolidated entity's scope key. One spelling, shared with the frontend.
GROUP_SCOPE_KEY = "group"

#: Words that identify a canonical metric inside a sentence.
METRIC_WORDS: dict[str, tuple[str, ...]] = {
    "revenue": ("revenue", "sales", "turnover"),
    "operating_profit": ("operating profit", "ebit"),
    "recurring_operating_profit": ("recurring operating profit",),
    "operating_margin": ("operating margin",),
    "recurring_operating_margin": ("recurring operating margin",),
    "net_income": (
        "net income",
        "net profit",
        "net result",
        "profit for the year",
    ),
    "operating_cash_flow": ("operating cash flow", "cash from operations"),
    "free_cash_flow": ("free cash flow", "fcf"),
    "total_assets": ("total assets",),
    "total_equity": (
        "total equity",
        "shareholders' equity",
        "shareholders equity",
    ),
    "cash_and_equivalents": ("cash and equivalents", "cash and cash equivalents"),
    "total_debt": ("total debt", "gross debt"),
    "net_debt": ("net debt",),
    "net_cash": ("net cash",),
}

#: Words a SENTENCE uses to say it is talking about the consolidated entity.
_GROUP_PROSE_WORDS = (
    "group",
    "consolidated",
    "groupwide",
    "group-wide",
    "company-wide",
    "companywide",
    "total company",
)

#: Unit spellings that all mean "this value IS a percentage". The extractor does
#: not write one spelling: a trend series carries ``%``, a snapshot slot carries
#: ``percent``. Comparing against a literal ``%`` did not merely miss checks — it
#: made WRONG ones, by classifying a canonical margin as an amount and then
#: testing the operating-profit figure in the same sentence against it.
_PERCENT_UNITS = frozenset({"%", "percent", "percentage", "pct"})

_SCALE_MULTIPLIER: dict[str, float] = {
    "thousand": 1e3,
    "million": 1e6,
    "billion": 1e9,
}

_CURRENCY_TOKENS: dict[str, str] = {
    "€": "EUR",
    "$": "USD",
    "£": "GBP",
    "chf": "CHF",
    "eur": "EUR",
    "usd": "USD",
    "gbp": "GBP",
    "dkk": "DKK",
    "sek": "SEK",
    "nok": "NOK",
    "jpy": "JPY",
}

#: Relative tolerance — prose legitimately rounds ("DKK 32.5 billion").
_TOLERANCE = 0.02

#: How close a number must be to a metric's name to count as a claim ABOUT it.
#: Analytical prose names several metrics in one sentence; without proximity,
#: "net debt 13,719m vs equity 5,282m … if EBIT falls" reads as a contradictory
#: operating-profit claim.
_PROXIMITY_CHARS = 40

_WS_RE = re.compile(r"\s+")
_PERIOD_TOKEN_RE = re.compile(
    r"\b(?:fy\s?\d{4}|[hq][1-4]\s?(?:fy\s?)?\d{4}|\d{4}[-\s]?[hq][1-4]|\d{4})\b",
    re.IGNORECASE,
)
_NUMBER_RE = re.compile(
    r"(?:(chf|eur|usd|gbp|dkk|sek|nok|jpy|[€$£])\s*)?"
    r"(?<![A-Za-z0-9.])(-?\d(?:[\d,\s]*\d)?(?:\.\d+)?)"
    r"\s*(%|percent|bn\b|billion|m\b|million|k\b|thousand|x\b|times\b)?",
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Scope
# ---------------------------------------------------------------------------


def _normalise_label(raw: Any) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    text = _WS_RE.sub(" ", raw).strip().strip("–—-:•·|").strip()
    return text or None


def scope_key_of(raw: Any) -> str | None:
    """``"group"``, ``"segment:<casefolded name>"``, or None for UNKNOWN.

    UNKNOWN is a real answer, not a synonym for Group. A series whose heading
    carried no scope signal is not evidence about the consolidated entity, and
    treating it as one is exactly how a segment figure came to adjudicate a
    Group claim on a live CFR report.
    """
    label = _normalise_label(raw)
    if label is None:
        return None
    if label.casefold() in GROUP_SCOPE_LABELS:
        return GROUP_SCOPE_KEY
    return f"segment:{label.casefold()}"


# ---------------------------------------------------------------------------
# The canonical index
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CanonicalFigure:
    """One canonical value of one metric, at one period and one scope."""

    key: str
    value: float
    scale: str | None = None
    #: ``"%"`` means the canonical value IS a percentage.
    unit: str | None = None
    period: str | None = None
    scope_key: str | None = None
    scope_name: str | None = None
    currency: str | None = None

    @property
    def magnitude(self) -> float:
        return self.value * _SCALE_MULTIPLIER.get(self.scale or "", 1.0)

    @property
    def is_percent(self) -> bool:
        return (self.unit or "").strip() == "%"


@dataclass(frozen=True)
class CanonicalIndex:
    """Every canonical figure, plus the scope names this report actually holds."""

    figures: dict[str, list[CanonicalFigure]] = field(default_factory=dict)
    #: Longest first, so "specialist watchmakers" is not shadowed by a shorter
    #: segment name that is a substring of it. A sentence is only credited with
    #: naming a segment THIS report reports — never an arbitrary capitalised
    #: phrase.
    segment_names: tuple[tuple[str, str], ...] = ()

    def __bool__(self) -> bool:
        return bool(self.figures)


EMPTY_INDEX = CanonicalIndex()


def _is_percent_unit(unit: Any) -> bool:
    if not unit or not isinstance(unit, str):
        return False
    return unit.strip().casefold() in _PERCENT_UNITS


def _currency_code(raw: Any) -> str | None:
    if not raw or not isinstance(raw, str):
        return None
    text = raw.strip().casefold()
    token = _CURRENCY_TOKENS.get(text)
    if token:
        return token
    return text.upper() if re.fullmatch(r"[a-z]{3}", text) else None


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        value = float(raw)
        return value if value == value and abs(value) != float("inf") else None
    return None


def _as_dict(raw: Any) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _unwrap(raw: Any) -> Any:
    """Report sections wrap some values as ``{"value": …}``."""
    if isinstance(raw, dict) and "value" in raw:
        return raw["value"]
    return raw


#: Metric slots the financial snapshot carries, per period family.
_SNAPSHOT_SUFFIXES = ("_primary_filing", "_current_period")


def build_canonical_index(content: dict[str, Any] | None) -> CanonicalIndex:
    """The canonical figures for one assembled report.

    The whole set, not the headline slots. The snapshot carries one annual and
    one current-period value per metric; the report also carries a reconstructed
    multi-year series, and the council legitimately cites it. Checked against a
    real Pandora run: with only the snapshot slots, 13 of 111 sentences were
    called contradictory and every one of them was correct — they quoted a
    historical period the index could not see. A guard that suppresses correct
    analysis is worse than no guard.

    Segment series are IN the index, carrying their own scope key. They are never
    used as if they were Group figures; having them present is what lets a
    segment claim be judged on its own terms.
    """
    content = _as_dict(content)
    figures: dict[str, list[CanonicalFigure]] = {}
    segments: dict[str, str] = {}

    def add(figure: CanonicalFigure) -> None:
        figures.setdefault(figure.key, []).append(figure)
        if figure.scope_key and figure.scope_key.startswith("segment:"):
            if figure.scope_name:
                segments[figure.scope_key] = figure.scope_name

    snapshot = _as_dict(content.get("financial_snapshot"))
    for slot, raw in snapshot.items():
        metric = next(
            (slot[: -len(sfx)] for sfx in _SNAPSHOT_SUFFIXES if slot.endswith(sfx)),
            None,
        )
        if metric is None or metric not in METRIC_WORDS:
            continue
        dp = _as_dict(raw)
        value = _number(dp.get("numeric_value"))
        if value is None:
            value = _number(dp.get("value"))
        if value is None:
            continue
        scope_name = _normalise_label(dp.get("scope"))
        add(
            CanonicalFigure(
                key=metric,
                value=value,
                scale=dp.get("scale") if isinstance(dp.get("scale"), str) else None,
                unit="%" if _is_percent_unit(dp.get("unit")) else dp.get("unit"),
                period=dp.get("period") if isinstance(dp.get("period"), str) else None,
                # The snapshot's slots ARE the consolidated slots: the report
                # layer fills them from Group-scoped facts, and from an unscoped
                # fact only under its long-standing implicit-Group convention.
                # A slot carrying an explicit segment label keeps it.
                scope_key=scope_key_of(dp.get("scope")) or GROUP_SCOPE_KEY,
                scope_name=scope_name,
                currency=_currency_code(dp.get("currency")),
            )
        )

    trends = _as_dict(content.get("historical_trends"))
    rows = _unwrap(trends.get("series"))
    for entry in rows if isinstance(rows, list) else []:
        series = _as_dict(entry)
        metric = series.get("metric")
        if not isinstance(metric, str) or metric not in METRIC_WORDS:
            continue
        unit_text = str(series.get("unit") or "").casefold()
        scale = next(
            (name for name in _SCALE_MULTIPLIER if name in unit_text), None
        )
        is_percent = _is_percent_unit(unit_text) or "percent" in unit_text
        # ``scope_type`` is the backend's own decidable answer, written from the
        # typed FactScope (migration 018). The free-text label is the fallback
        # for a series written before those columns existed.
        scope_type = series.get("scope_type")
        if scope_type == SCOPE_TYPE_GROUP:
            scope_key: str | None = GROUP_SCOPE_KEY
        elif scope_type == SCOPE_TYPE_SEGMENT:
            scope_key = scope_key_of(series.get("scope"))
        else:
            scope_key = scope_key_of(series.get("scope"))
        scope_name = _normalise_label(series.get("scope"))
        currency = _currency_code(series.get("currency"))
        periods = series.get("periods")
        for point in periods if isinstance(periods, list) else []:
            p = _as_dict(point)
            # A superseded period was replaced by a later restatement.
            if p.get("superseded") is True:
                continue
            value = _number(p.get("value"))
            if value is None:
                continue
            add(
                CanonicalFigure(
                    key=metric,
                    value=value,
                    scale=scale,
                    unit="%" if is_percent else None,
                    period=p.get("period") if isinstance(p.get("period"), str) else None,
                    scope_key=scope_key,
                    scope_name=scope_name,
                    currency=currency,
                )
            )

    segment_names = tuple(
        sorted(
            ((name, key) for key, name in segments.items()),
            key=lambda pair: len(pair[0]),
            reverse=True,
        )
    )
    return CanonicalIndex(figures=figures, segment_names=segment_names)


# ---------------------------------------------------------------------------
# Numbers in prose
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProseNumber:
    raw: float
    scaled: float
    index: int
    is_percent: bool
    #: A multiple is a RELATIONSHIP between two levels, never a level. Reading
    #: "net debt ~2.6x equity" as a net-debt level made the guard call a correct
    #: live PNDORA sentence a contradiction of DKK 13,719m.
    is_multiple: bool
    currency: str | None


def prose_numbers(sentence: str) -> list[ProseNumber]:
    """The magnitudes a sentence actually asserts as quantities.

    Two exclusions, both learned from real council prose: a digit glued to a
    letter is part of a token ("H1", "Q3", "FY2025") rather than a number, and a
    bare four-digit year is a date. What remains carries a scale word, a percent
    sign, a decimal point or thousands grouping, or is large enough not to be an
    ordinal.
    """
    out: list[ProseNumber] = []
    for match in _NUMBER_RE.finditer(sentence):
        written = match.group(2)
        if not written:
            continue
        digits = re.sub(r"[,\s]", "", written)
        if not digits:
            continue
        try:
            raw = float(digits)
        except ValueError:
            continue

        unit = (match.group(3) or "").casefold()
        grouped = bool(re.search(r"[,\s]", written.strip()))
        fractional = "." in written

        if not unit and not grouped and not fractional:
            # A bare year is a date; a small bare integer is a count.
            if 1900 <= raw <= 2100 or abs(raw) < 100:
                continue

        scaled = raw
        if unit in ("bn", "billion"):
            scaled = raw * 1e9
        elif unit in ("m", "million"):
            scaled = raw * 1e6
        elif unit in ("k", "thousand"):
            scaled = raw * 1e3

        out.append(
            ProseNumber(
                raw=raw,
                scaled=scaled,
                # The magnitude's own position, not the currency prefix's:
                # proximity to a metric name is measured from the number.
                index=match.start(2),
                is_percent=unit in ("%", "percent"),
                is_multiple=unit in ("x", "times"),
                currency=_currency_code(match.group(1)),
            )
        )
    return out


def _agrees(prose: ProseNumber, figure: CanonicalFigure) -> bool:
    canonical = figure.magnitude
    for candidate in (prose.scaled, prose.raw):
        if canonical == 0:
            if candidate == 0:
                return True
            continue
        if abs(candidate - canonical) / abs(canonical) <= _TOLERANCE:
            return True
    return False


def _currency_blocks(prose: ProseNumber, figure: CanonicalFigure) -> bool:
    """True when a stated currency rules a canonical figure out entirely.

    Two figures in different currencies are not two views of one number. This
    layer holds no exchange rate and must never invent one, so a mismatch means
    "not comparable" — which is neither agreement nor a contradiction.
    """
    if not prose.currency or not figure.currency:
        return False
    return prose.currency != figure.currency


def _period_key(text: Any) -> str | None:
    """Normalise so "FY2025", "fy 2025" and "2025" compare equal."""
    if not text or not isinstance(text, str):
        return None
    t = re.sub(r"\s+", "", text.casefold())
    half = re.search(r"([hq][1-4])(?:fy)?(\d{4})", t)
    if half:
        return f"{half.group(1)}-{half.group(2)}"
    half_after = re.search(r"(\d{4})-?([hq][1-4])", t)
    if half_after:
        return f"{half_after.group(2)}-{half_after.group(1)}"
    year = re.search(r"(\d{4})", t)
    return year.group(1) if year else None


def _periods_in(sentence: str) -> set[str]:
    out: set[str] = set()
    for match in _PERIOD_TOKEN_RE.findall(sentence):
        key = _period_key(match)
        if key:
            out.add(key)
    return out


#: Words that turn coexisting figures into a COMPARATIVE claim. Two numbers printed
#: side by side assert nothing; "fell from" asserts a direction, and a direction across
#: incompatible spans is arithmetic nobody performed.
#:
#: Deliberately narrow. "Revenue was $145m in Q2 2026 and $1.9bn in FY2025" is two facts
#: coexisting and stays untouched — the canonical rule allows same-frequency comparison
#: OR explicitly labelled non-comparative coexistence, and this only refuses the first.
#: Multi-word phrases, matched as substrings because they cannot occur inside a word.
_COMPARISON_PHRASES: tuple[str, ...] = (
    "compared to", "compared with", "versus", "vs", "vs.",
    "down from", "up from", "year-over-year", "year over year", "yoy",
)
#: Single words, matched on WORD BOUNDARIES. Substring matching flagged "fellow" for
#: "fell", "arose" for "rose" and "dropout" for "drop" — the last is ruinous for a
#: biotech pipeline, where a trial dropout is ordinary prose. Found by review, after the
#: author had already removed "trend" for one instance of the same class.
_COMPARISON_TOKENS: frozenset[str] = frozenset(
    {
        "decline", "declined", "declines", "declining",
        "decrease", "decreased", "decreases", "decreasing",
        "grew", "increase", "increased", "increases", "increasing",
        "rose", "risen", "fell", "fallen",
        "drop", "dropped", "drops", "dropping",
    }
)
#: "trend" is deliberately NOT in that list. It appears in "limiting trend analysis" —
#: an honest statement about what the data does NOT support — and flagging that would
#: withhold a gap rather than a claim. Every real violation in the live report also
#: carried "decline" or "drop", so nothing is lost by leaving it out, and an audit that
#: suppresses honest gaps is worse than one that misses a redundant hit.


def is_comparative(sentence: str) -> bool:
    """Whether a sentence asserts a direction of change rather than stating figures.

    "growth" is deliberately absent: "withdrew FY2025 growth guidance" names a thing,
    it does not assert a direction. The verbs do.
    """
    text = (sentence or "").casefold()
    if any(phrase in text for phrase in _COMPARISON_PHRASES):
        return True
    return any(token in _COMPARISON_TOKENS for token in re.findall(r"[a-z-]+", text))


def incompatible_periods(sentence: str) -> tuple[str, str] | None:
    """The first pair of named periods that cannot be compared, or ``None``.

    A quarter set against a full year is the ``INTERIM_AS_ANNUAL`` contradiction: the
    two measure different-length spans, so no growth or decline follows from putting
    them side by side.

    **Frequency, deliberately, and not** ``ReportingPeriod.comparable_with``. That
    method answers a stricter and different question — may these two periods sit on one
    trend line — and so requires the same ordinal, refusing Q1 against Q2. Quarter-on-
    quarter is a real comparison an analyst makes, and refusing it here would suppress
    true statements. This campaign has made that mistake before: a numeric guard built
    on the group figure alone withheld 32 correct segment sentences from one report.

    This reports what the SENTENCE NAMES. Whether the sentence actually compares them
    is :func:`comparative_period_conflict`'s question, because coexistence is allowed.
    """
    return _first_incompatible(sorted(_periods_in((sentence or "").casefold())))


def _first_incompatible(keys: list[str]) -> tuple[str, str] | None:
    """The first pair of period keys measuring different-length spans."""
    from app.services.sources.financial_period import parse_period

    for i, left in enumerate(keys):
        for right in keys[i + 1 :]:
            a, b = parse_period(left), parse_period(right)
            if a.is_unknown or b.is_unknown:
                continue
            if a.period_type != b.period_type:
                return (left, right)
    return None


def _clause_is_comparative(clause: str) -> bool:
    """`is_comparative`, applied to one clause rather than the whole sentence."""
    if any(phrase in clause for phrase in _COMPARISON_PHRASES):
        return True
    return any(token in _COMPARISON_TOKENS for token in re.findall(r"[a-z-]+", clause))


def comparative_period_conflict(sentence: str) -> tuple[str, str] | None:
    """A comparative claim spanning incompatible periods, or ``None``.

    THE DEFECT THIS EXISTS FOR. A live report said "Q2 2026 revenue compared to FY2025
    annual revenue indicates a continuing revenue decline trend", and repeated the
    inference across the Red Team, the bear case and the chair. Moderna's Q2 2026
    revenue was $145m against Q2 2025's $142m — a rise. The "decline" came entirely
    from setting one quarter against a full year.

    Note it takes NO numbers. The sentence above quotes none, so every numeric check
    in this module skipped it — the claim was invalid on its periods alone, and that is
    what is checked here.
    """
    if not is_comparative(sentence):
        return None

    text = (sentence or "").casefold()
    if len(_periods_in(text)) < 2:
        return None

    # Only the periods ADJACENT TO the comparison are compared.
    #
    # Scanning every pair in the sentence flagged pairs the sentence never sets against
    # each other: "Q2 2026 revenue rose to 145 from 142 in Q2 2025, and FY2026 guidance
    # was reiterated" is a valid quarter-on-quarter comparison, and the whole-sentence
    # scan refused it over the guidance clause. Found by review — and it is exactly the
    # over-suppression this module's own docstring warns about.
    clauses = re.split(r"[;,]| and | but | while ", text)
    for index, clause in enumerate(clauses):
        if not _clause_is_comparative(clause):
            continue
        local = sorted(_periods_in(clause))
        if len(local) < 2:
            # The comparison and the periods it compares can straddle a clause boundary
            # — "revenue fell sharply, from FY2025 to Q2 2026". Widen to the neighbours,
            # but ONLY here: a clause already naming two periods is self-contained, and
            # widening it would drag in an unrelated period from elsewhere, which is the
            # defect this fix exists to remove.
            window = clauses[max(0, index - 1) : index + 2]
            local = sorted({key for c in window for key in _periods_in(c)})
        conflict = _first_incompatible(local)
        if conflict is not None:
            return conflict
    return None


def scopes_in(sentence: str, index: CanonicalIndex) -> set[str]:
    """Which reporting entities a sentence is talking about.

    An empty set means the sentence stated no scope, which is a different answer
    from "the sentence said Group".
    """
    text = sentence.casefold()
    out: set[str] = set()
    for name, key in index.segment_names:
        if name.casefold() in text:
            out.add(key)
    for word in _GROUP_PROSE_WORDS:
        if re.search(rf"\b{re.escape(word)}\b", text):
            out.add(GROUP_SCOPE_KEY)
            break
    return out


# ---------------------------------------------------------------------------
# Adjudication
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SentenceVerdict:
    verdict: str
    metric: str | None = None
    scope: str | None = None

    @property
    def conflicting(self) -> bool:
        return self.verdict == VERDICT_CONFLICTING


UNCHECKED = SentenceVerdict(VERDICT_UNCHECKED)


def check_sentence(sentence: str, index: CanonicalIndex) -> SentenceVerdict:
    """Adjudicate ONE council sentence against the canonical figures.

    ``unchecked`` is the common and correct answer. Only a sentence that names a
    metric AND a scope this report holds that metric at, AND states a number
    matching none of that scope's canonical values, is called conflicting.
    """
    text = (sentence or "").casefold()
    if not text.strip():
        return UNCHECKED

    # Checked BEFORE the numeric gates, and independently of them. A comparative claim
    # across incompatible spans is invalid whether or not it quotes a figure, and the
    # live sentence that motivated this quoted none — so every numeric check skipped it.
    pair = comparative_period_conflict(text)
    if pair is not None:
        return SentenceVerdict(VERDICT_CONFLICTING, metric=None, scope=None)

    if not index.figures:
        return UNCHECKED

    numbers = prose_numbers(text)
    if not numbers:
        return UNCHECKED

    # Longest metric phrase first, so "recurring operating profit" is not
    # matched as "operating profit".
    named = sorted(
        (
            (metric, words)
            for metric, words in METRIC_WORDS.items()
            if metric in index.figures and any(w in text for w in words)
        ),
        key=lambda pair: max(len(w) for w in pair[1]),
        reverse=True,
    )
    if not named:
        return UNCHECKED

    sentence_periods = _periods_in(text)
    sentence_scopes = scopes_in(text, index)

    def figures_for(metric: str) -> list[CanonicalFigure]:
        figures = index.figures.get(metric, [])
        if sentence_scopes:
            figures = [
                f for f in figures if f.scope_key is not None and f.scope_key in sentence_scopes
            ]
        if sentence_periods:
            figures = [
                f for f in figures if (_period_key(f.period) or "") in sentence_periods
            ]
        return figures

    def explained_by_another_metric(n: ProseNumber, metric: str) -> bool:
        """A number that is some OTHER named metric's figure is not evidence here.

        "Total assets of DKK 29.603 billion relative to revenue and equity…" —
        29.603bn is the total-assets figure and it is correct, but "revenue" sits
        inside the proximity window. The exclusion is per-METRIC and scope
        filtered: a number matching a DIFFERENT SCOPE of the SAME metric is
        precisely the mis-scoping this exists to catch, so it excuses nothing.
        """
        return any(
            other != metric
            and any(
                not _currency_blocks(n, f) and _agrees(n, f)
                for f in figures_for(other)
            )
            for other, _ in named
        )

    checked_metric: str | None = None
    checked_scope: str | None = None

    for metric, words in named:
        figures = figures_for(metric)
        if not figures:
            continue

        positions: list[int] = []
        for word in words:
            start = text.find(word)
            while start != -1:
                positions.extend((start, start + len(word)))
                start = text.find(word, start + 1)

        canonical_is_percent = any(f.is_percent for f in figures)
        nearby = [
            n
            for n in numbers
            if any(abs(n.index - pos) <= _PROXIMITY_CHARS for pos in positions)
            and not n.is_multiple
            # The written form must match the canonical form. A percentage
            # beside an AMOUNT metric is a change or a ratio, not a level; an
            # amount beside a PERCENTAGE metric is some other quantity that
            # happens to sit in the sentence.
            and n.is_percent == canonical_is_percent
        ]
        if not nearby:
            continue

        comparable = [n for n in nearby if any(not _currency_blocks(n, f) for f in figures)]
        if not comparable:
            continue

        checked_metric = metric
        checked_scope = figures[0].scope_key

        # Prose routinely carries a comparison figure beside the current one
        # ("32,516 against 31,200 a year earlier"), so a single match clears it.
        if any(
            not _currency_blocks(n, f) and _agrees(n, f)
            for n in comparable
            for f in figures
        ):
            continue

        unexplained = [
            n for n in comparable if not explained_by_another_metric(n, metric)
        ]
        if not unexplained:
            continue

        return SentenceVerdict(VERDICT_CONFLICTING, metric, checked_scope)

    return SentenceVerdict(
        VERDICT_CONSISTENT if checked_metric else VERDICT_UNCHECKED,
        checked_metric,
        checked_scope,
    )


# ---------------------------------------------------------------------------
# Whole-report verification
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class StatementConflict:
    """One statement the canonical figures contradict."""

    path: str
    statement: str
    metric: str | None
    scope: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "statement": self.statement,
            "metric": self.metric,
            "scope": self.scope,
        }


def _council_statements(
    content: dict[str, Any],
) -> Iterable[tuple[str, str, str]]:
    """Every piece of council prose, as ``(path, adjudicated_text, statement)``.

    ``adjudicated_text`` may join two fields — an implication's number often sits
    in its ``mechanism`` while the metric it concerns is named in its
    ``statement`` — while ``statement`` is the field a reader sees withheld. The
    two are reported separately so the presentation can match on what it renders
    without having to know how the text was composed.
    """
    council = _as_dict(content.get("llm_council_analysis"))
    agents = council.get("agents")
    for a_index, raw_agent in enumerate(agents if isinstance(agents, list) else []):
        agent = _as_dict(raw_agent)
        name = agent.get("agent_name") or f"agent_{a_index}"
        points = agent.get("key_points")
        for p_index, raw_point in enumerate(points if isinstance(points, list) else []):
            claim = _as_dict(raw_point).get("claim")
            if isinstance(claim, str) and claim.strip():
                yield f"agents.{name}.key_points.{p_index}", claim, claim
        implications = agent.get("implications")
        for i_index, raw_imp in enumerate(
            implications if isinstance(implications, list) else []
        ):
            imp = _as_dict(raw_imp)
            statement = imp.get("statement")
            if not isinstance(statement, str) or not statement.strip():
                continue
            mechanism = imp.get("mechanism")
            joined = statement
            if isinstance(mechanism, str) and mechanism.strip():
                joined = f"{statement} {mechanism}"
            yield f"agents.{name}.implications.{i_index}", joined, statement


def verify_report_content(content: dict[str, Any] | None) -> dict[str, Any]:
    """Reconcile every council statement in one assembled report.

    Returns the record persisted under ``numeric_verification``. Never raises and
    never edits the prose: the original text stays exactly as the council wrote
    it, so a withheld statement can still be reviewed.
    """
    content = _as_dict(content)
    index = build_canonical_index(content)

    conflicts: list[StatementConflict] = []
    counts = {VERDICT_CONSISTENT: 0, VERDICT_UNCHECKED: 0, VERDICT_CONFLICTING: 0}

    for path, adjudicated, statement in _council_statements(content):
        verdict = check_sentence(adjudicated, index)
        counts[verdict.verdict] = counts.get(verdict.verdict, 0) + 1
        if verdict.conflicting:
            conflicts.append(
                StatementConflict(
                    path=path,
                    statement=statement,
                    metric=verdict.metric,
                    scope=verdict.scope,
                )
            )

    checked = sum(counts.values())
    return {
        "engine_version": NUMERIC_VERIFICATION_VERSION,
        # The population each number counts, named — the fact-count rule
        # (``fact_count_scopes``) applies here too: the point is not to make the
        # numbers agree, it is to say what they are counting.
        "statements_examined": checked,
        "canonical_metrics": len(index.figures),
        "canonical_figures": sum(len(v) for v in index.figures.values()),
        "consistent": counts[VERDICT_CONSISTENT],
        "unchecked": counts[VERDICT_UNCHECKED],
        "conflicting": counts[VERDICT_CONFLICTING],
        "conflict_notice": CONFLICT_NOTICE,
        "conflicts": [c.to_dict() for c in conflicts],
    }
