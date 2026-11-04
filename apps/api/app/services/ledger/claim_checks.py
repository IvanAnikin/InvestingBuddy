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

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from app.services.ledger import claim_period as cp

RULE_CLAIM_PERIOD = "claim_period_differs_from_cited_source"
RULE_CLAIM_SCOPE = "claim_scope_differs_from_cited_source"
RULE_DERIVED_AS_REPORTED = "derived_ratio_presented_as_reported"

CLAIM_RULES: tuple[str, ...] = (
    RULE_CLAIM_PERIOD, RULE_CLAIM_SCOPE, RULE_DERIVED_AS_REPORTED,
)

#: A claim longer than this is not checked (kept as written).
MAX_CLAIM_CHARS = 1200
#: Source text read per source, figures read per source and per claim, and sources
#: read per claim: a claim is a sentence and cites a handful of items.
MAX_SOURCE_CHARS = 4000
MAX_SOURCE_FIGURES = 200
MAX_CLAIM_FIGURES = 40
MAX_SOURCES = 16
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
    r"target(?:ed|ing)?|budget(?:ed)?|expects?|expected|anticipat\w{0,6}|will|"
    r"consensus|plans?[ \t]{1,3}to|planned)\b",
    re.IGNORECASE,
)
#: A source's sentences and clauses (tables put one row per line).
_SOURCE_SPLIT_RE = re.compile(r"(?<=[.!?])[ \t]{1,3}(?=[A-Z\[(])|\n|;")
#: A clause about ANOTHER period than the sentence's: it never inherits the sentence's.
_COMPARATIVE_RE = re.compile(
    r"\b(?:(?:prior|previous|preceding|last|same)[ \t]{1,3}(?:year|quarter|period|half)"
    r"|year[- ](?:ago|earlier)|a[ \t]{1,3}year[ \t]{1,3}(?:earlier|ago|before)|from"
    r"|restated|comparative)\b",
    re.IGNORECASE,
)
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


def figures(text: str) -> list[_Figure]:
    """Every figure in ``text`` (ids, dates and period labels masked first)."""
    out: list[_Figure] = []
    for m in _FIGURE_RE.finditer(_masked(text)):
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


def _tolerance(fig: _Figure) -> float:
    return 0.5 * (10 ** -fig.decimals)


def same_figure(a: _Figure, b: _Figure) -> bool:
    """One value written twice, within the rounding either is written to."""
    if a.kind != b.kind:
        return False
    if a.kind != "amt":
        return abs(a.value - b.value) <= max(_tolerance(a), _tolerance(b)) + 1e-9
    scales_a = (a.scale,) if a.scale else (1.0, 1e3, 1e6, 1e9)
    scales_b = (b.scale,) if b.scale else (1.0, 1e3, 1e6, 1e9)
    for sa in scales_a:
        for sb in scales_b:
            va, vb = a.value * sa, b.value * sb
            if abs(va - vb) <= max(_tolerance(a) * sa, _tolerance(b) * sb) + 1e-9:
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


def _source_index(source: ClaimSource) -> list[_Stated]:
    """Every figure the source states, each with its period: read where the source
    states it (its clause, then its sentence), else the stored key. Bounded by
    ``MAX_SOURCE_FIGURES``."""
    text = source.text[:MAX_SOURCE_CHARS]
    stored = cp.period_of_key(source.period) if source.period else None
    out: list[_Stated] = []
    for s_start, s_end in _clauses(text, _SOURCE_SPLIT_RE):
        sentence = text[s_start:s_end]
        if not _HAS_DIGIT_RE.search(sentence):
            continue
        periods = [p for p in cp.mentions(sentence) if not p.is_forward]
        sentence_distinct = _distinct(periods)
        if len(sentence_distinct) == 1:
            sentence_period: cp.Period | None = sentence_distinct[0]
        elif sentence_distinct:
            sentence_period = None
        else:
            sentence_period = stored
        for c_start, c_end in _clauses(sentence, _CLAUSE_SPLIT_RE):
            local = _distinct(p for p in periods if c_start <= p.start < c_end)
            period = (local[0] if len(local) == 1
                      else None if local else sentence_period)
            if _FORWARD_RE.search(sentence[c_start:c_end]):
                period = None  # a forecast's figure is not the document period's
            for fig in figures(sentence[c_start:c_end]):
                out.append(_Stated(fig, period))
                if len(out) >= MAX_SOURCE_FIGURES:
                    return out
    return out


def _holders(fig: _Figure, indexed: Sequence[tuple[ClaimSource, list[_Stated]]]
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


def _names_scope(claim_low: str, name: str) -> bool:
    return any(len(w) >= 4 and w in claim_low for w in name.split()) or name in claim_low


# ── The check ────────────────────────────────────────────────────────────── #


def _period_words(periods: Sequence[cp.Period]) -> str:
    return " / ".join(p.key for p in periods[:3])


def _derived_ratio(clause: str, figs: Sequence[_Figure],
                   indexed: Sequence[tuple[ClaimSource, list[_Stated]]]
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


def check_claim(text: str | None, sources: Sequence[ClaimSource]) -> ClaimVerdict | None:
    """The first way ``text`` misstates what it cites, or ``None``. Pure; never
    raises."""
    claim = str(text or "")
    if not claim.strip() or not sources or len(claim) > MAX_CLAIM_CHARS:
        return None
    try:
        return _check(claim, sources)
    except (TypeError, ValueError, IndexError):  # pragma: no cover - defensive
        return None


def _check(claim: str, sources: Sequence[ClaimSource]) -> ClaimVerdict | None:
    indexed = [(source, _source_index(source)) for source in list(sources)[:MAX_SOURCES]]
    mentions = cp.mentions(claim)
    stated = [p for p in mentions if not p.is_forward]
    sentence_periods = _distinct(p for p in stated if not p.is_historical)
    claim_low = claim.lower()
    for c_start, c_end in _clauses(claim, _CLAUSE_SPLIT_RE):
        clause = claim[c_start:c_end]
        figs = figures(clause)[:MAX_CLAIM_FIGURES]
        if not figs:
            continue
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
        periods = _distinct(p for p in stated if c_start <= p.start < c_end)
        if _FORWARD_RE.search(clause):
            periods = []  # a forecast is checked for nothing but its scope
        elif not periods:
            if _COMPARATIVE_RE.search(clause) or len(sentence_periods) != 1:
                periods = []
            else:
                periods = sentence_periods
        for fig in figs:
            holders = _holders(fig, indexed)
            if not holders:
                continue
            if periods and all(p is not None for _s, p in holders) and not any(
                cp.any_compatible(periods, [p]) for _s, p in holders if p is not None
            ):
                first_source, first_period = holders[0]
                assert first_period is not None
                return ClaimVerdict(
                    RULE_CLAIM_PERIOD,
                    f"it states {fig.text} for {_period_words(periods)}, but the cited "
                    f"source states it for {first_period.key}",
                    source_ids=tuple(s.source_id for s, _p in holders),
                )
            names = {_segment_name(s.scope) for s, _p in holders}
            if len(names) == 1 and None not in names:
                name = next(iter(names))
                assert name is not None
                if not _names_scope(claim_low, name):
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


def source_of_finding(finding: dict[str, object]) -> ClaimSource | None:
    """A payload finding (``v3_research.findings[*]``) as a claim source."""
    fid = str(finding.get("finding_id") or "")
    if not fid:
        return None
    raw_kinds = finding.get("source_kinds")
    kinds = ({str(k) for k in raw_kinds if k}
             if isinstance(raw_kinds, (list, tuple, set, frozenset)) else set())
    statement = str(finding.get("statement") or "")
    derived = bool(kinds) and kinds <= DERIVED_SOURCE_KINDS
    return ClaimSource(
        source_id=fid,
        text=statement,
        period=str(finding.get("period_key") or "") or None,
        scope=str(finding.get("scope_key") or "") or None,
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
    "ClaimSource",
    "ClaimVerdict",
    "check_claim",
    "cited_ids",
    "figures",
    "ratio_keys_of",
    "same_figure",
    "source_of_evidence",
    "source_of_finding",
]
