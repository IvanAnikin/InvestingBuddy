"""A closed vocabulary of research FIELDS, and a generic lexical classifier for it.

WHY THIS EXISTS
===============
Five producers state what a run does not know — ledger gaps, the professional report's
platform evidence gaps, the Chair's GAPS block, per-section open questions, and the V2
``missing_information`` list and council concerns — and none of them read the findings.
A report could say "no capital expenditure was acquired" two sections below a finding
that states the capex figure, because the only key a gap carried was the question it was
raised under, and the capex finding was written under another question.

A FIELD is the missing join: *what* a gap is about and *what* a finding states, in one
closed vocabulary, so the two can be compared regardless of which question either came
from.

THE RULES
=========
* **Closed.** A field invented at a call site is one nothing can compare against. Every
  key below is ``<kind>:<name>``.
* **Families and sub-types.** ``metric:capex`` is the FAMILY key and stays stable; a
  clause that says which capex it is also carries one sub-type key —
  ``metric:capex_project`` (an initial / development capital estimate for a project),
  ``metric:capex_sustaining`` (sustaining / expansion capital) or ``metric:capex_period``
  (capital spent in a reporting period). A gap about one sub-type is never closed by
  another: last year's spend does not answer "what will the project cost".
* **Generic.** Patterns name the concept ("capital expenditure", "nameplate capacity",
  "first production"), never an issuer, a project or a commodity. A project NAME is read
  only as an identity (``project_key``) and never decides a field.
* **Fail closed.** Text that matches no pattern has NO field, and a gap with no field is
  never closed. A finding clause states a field only when it names it, does not negate,
  hedge or withdraw it, and carries the VALUE the field is about: a currency amount for a
  money metric, a quantity with a rate unit for capacity, a tonnage and a grade for a
  resource, "IRR of N%", a TARGET date for a milestone, a signed commitment for offtake.
  "Capex increased by 10%", "capex is described in section 4" and "the pilot plant was
  commissioned in 2022" state nothing a gap asks for.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

# ── The vocabulary ──────────────────────────────────────────────────────────── #

#: What an affirmative clause must also contain. ``NEEDS_FIGURE`` / ``NEEDS_DATE`` keep
#: their names for callers; the stricter kinds below are what the vocabulary uses.
NEEDS_FIGURE = "figure"
NEEDS_DATE = "date"
NEEDS_MONEY = "money"
NEEDS_CAPACITY = "capacity"
NEEDS_TONNAGE_AND_GRADE = "tonnage_and_grade"
NEEDS_PERCENT_RATE = "percent_rate"
NEEDS_TARGET_DATE = "target_date"
NEEDS_DURATION_OR_DATE = "duration_or_date"
NEEDS_COMMITMENT = "commitment"


@dataclass(frozen=True)
class ResearchField:
    key: str
    label: str
    #: Regexes over LOWER-CASED text, each bounded by ``\b``.
    patterns: tuple[str, ...]
    #: What an affirmative statement of this field must also contain.
    needs: str | None = None
    #: Validated fact labels (``extracted_facts.label``) that state this field.
    fact_labels: tuple[str, ...] = ()
    #: Playbook ``required_metrics`` / calculation keys that ask for this field.
    metric_aliases: tuple[str, ...] = ()
    #: The family key for a sub-type (``metric:capex`` for ``metric:capex_project``).
    family: str | None = None
    #: Guidance/estimate fields — the only ones a newer statement may SUPERSEDE. A
    #: period-bound metric (revenue, cash at a date, historical spend) is history, and
    #: history is never "prior guidance".
    supersedable: bool = False

    @property
    def is_milestone(self) -> bool:
        return self.key.startswith("milestone:")


_CAPEX_PATTERNS = (
    r"\bcapex\b",
    r"\bcap-ex\b",
    # "working capital" is not capital expenditure; "capital requirements" is too vague.
    r"(?<!working )\bcapital\s+(?:expenditures?|costs?|spend(?:ing)?|outlays?|budget)\b",
    r"\b(?:initial|development|pre-?production|construction|sustaining|growth|expansion|"
    r"upfront|up-front|start-?up)\s+capital\b",
)
_PROJECT_CAPEX = (
    r"\b(?:initial|development|pre-?production|construction|upfront|up-front|start-?up)\s+"
    r"(?:capital|capex)\b",
    r"\bproject\s+(?:capex|capital\s+cost)\b",
    r"\b(?:capex|capital\s+cost)\s+estimate\b",
    r"\bestimat\w*\s+(?:(?:the\s+)?(?:initial\s+)?(?:capex|capital\s+costs?))\b",
    r"\b(?:dfs|pfs|bfs|feasibility|scoping\s+study|pea)\b",
)
_SUSTAINING_CAPEX = (
    r"\b(?:sustaining|maintenance|stay-in-business|expansion|growth)\s+"
    r"(?:capital|capex|capital\s+expenditure)\b",
)
_PERIOD_CAPEX = (
    r"\bfy\s?'?\d{2,4}\b",
    r"\b(?:h[12]|q[1-4])\b",
    r"\bfor\s+the\s+(?:year|half|quarter|period|six\s+months|three\s+months)\b",
    r"\b(?:year|half-year|quarter|period)\s+ended\b",
    r"\b(?:spent|incurred|invested)\b",
    r"\bduring\s+(?:the\s+)?(?:year|half|quarter|period|(?:19|20)\d{2})\b",
)

FIELDS: tuple[ResearchField, ...] = (
    ResearchField(
        "metric:capex",
        "capital expenditure",
        _CAPEX_PATTERNS,
        needs=NEEDS_MONEY,
        fact_labels=("capital_expenditure", "capex"),
        metric_aliases=("capex", "capital_expenditure", "capex_intensity", "capex_to_ocf"),
    ),
    ResearchField(
        "metric:capex_project",
        "project capital cost estimate",
        _CAPEX_PATTERNS,
        needs=NEEDS_MONEY,
        family="metric:capex",
        supersedable=True,
    ),
    ResearchField(
        "metric:capex_sustaining",
        "sustaining or expansion capital",
        _CAPEX_PATTERNS,
        needs=NEEDS_MONEY,
        family="metric:capex",
    ),
    ResearchField(
        "metric:capex_period",
        "capital expenditure for a reporting period",
        _CAPEX_PATTERNS,
        needs=NEEDS_MONEY,
        family="metric:capex",
    ),
    ResearchField(
        "metric:production_capacity",
        "production capacity",
        (
            r"\b(?:production|nameplate|name-plate|design|processing|plant|installed|annual|"
            r"throughput|refining|manufacturing|milling|smelting|output|operating)\s+capacity\b",
            r"\bcapacity\s+(?:of|to\s+produce|to\s+process)\b",
            r"\b(?:design|nameplate|plant|annual)\s+throughput\b",
            r"\b(?:will|to|would|designed\s+to|expected\s+to)\s+produce\b",
        ),
        needs=NEEDS_CAPACITY,
        metric_aliases=("capacity", "production_capacity", "capacity_overbuild"),
        supersedable=True,
    ),
    ResearchField(
        "milestone:first_production",
        "first production",
        (
            r"\bfirst\s+(?:production|output|ore|concentrate|metal|oxide|product|pour|gold|"
            r"shipment|sales|revenue|deliver(?:y|ies))\b",
            r"\b(?:start|commencement|beginning|onset)\s+of\s+(?:commercial\s+)?production\b",
            r"\bproduction\s+(?:start|start-up|startup|commencement)\b",
            r"\b(?:commence|commences|commencing|begin|begins|start|starts|starting)\s+"
            r"(?:commercial\s+)?production\b",
            r"\bproduction\s+(?:is\s+)?(?:expected|scheduled|planned|targeted|slated)\s+to\s+"
            r"(?:start|begin|commence)\b",
        ),
        needs=NEEDS_TARGET_DATE,
        supersedable=True,
    ),
    ResearchField(
        "milestone:commissioning",
        "commissioning",
        (r"\bcommission(?:ing|ed)\b", r"\bramp-?up\s+to\s+(?:nameplate|full)\b"),
        needs=NEEDS_TARGET_DATE,
        supersedable=True,
    ),
    ResearchField(
        "milestone:final_investment_decision",
        "final investment decision",
        (r"\bfinal\s+investment\s+decision\b", r"\bfid\b"),
        needs=NEEDS_TARGET_DATE,
        supersedable=True,
    ),
    ResearchField(
        "commercial:offtake",
        "offtake",
        (r"\boff-?take\b", r"\boff\s+take\b"),
        needs=NEEDS_COMMITMENT,
    ),
    ResearchField(
        "metric:cash",
        "cash and equivalents",
        (
            r"\bcash\s+and\s+(?:cash\s+)?equivalents\b",
            r"\bcash\s+(?:balance|position|on\s+hand|reserves?|holdings?|at\s+bank)\b",
            r"\b(?:held|holds|had|has)\s[^;]{0,30}?\bin\s+cash\b",
        ),
        needs=NEEDS_MONEY,
        fact_labels=("cash_and_equivalents", "cash"),
        metric_aliases=("cash_and_equivalents", "cash"),
    ),
    ResearchField(
        "metric:cash_runway",
        "cash runway",
        (
            r"\b(?:cash|funding|financial)\s+runway\b",
            r"\brunway\b",
            r"\bfunded\s+(?:into|until|through|to)\b",
            r"\bmonths\s+of\s+(?:cash|funding)\b",
        ),
        needs=NEEDS_DURATION_OR_DATE,
        metric_aliases=("cash_runway", "cash_runway_quarters"),
    ),
    ResearchField(
        "metric:npv",
        "net present value",
        (r"\bnpv(?:\s*\d{1,2}(?:\.\d)?%?)?\b", r"\bnet\s+present\s+value\b"),
        needs=NEEDS_MONEY,
        supersedable=True,
    ),
    ResearchField(
        "metric:irr",
        "internal rate of return",
        (r"\birr\b", r"\binternal\s+rate\s+of\s+return\b"),
        needs=NEEDS_PERCENT_RATE,
        supersedable=True,
    ),
    ResearchField(
        "metric:operating_cash_flow",
        "operating cash flow",
        (
            r"\boperating\s+cash\s*flows?\b",
            r"\bcash\s+(?:flows?\s+)?(?:from|generated\s+(?:by|from))\s+operat(?:ions|ing\s+activities)\b",
            r"\bocf\b",
        ),
        needs=NEEDS_MONEY,
        fact_labels=("operating_cash_flow",),
        metric_aliases=("operating_cash_flow",),
    ),
    ResearchField(
        "metric:revenue",
        "revenue",
        (r"\brevenues?\b", r"\bturnover\b", r"\bnet\s+sales\b"),
        needs=NEEDS_MONEY,
        fact_labels=("revenue",),
        metric_aliases=("revenue",),
    ),
    ResearchField(
        "metric:net_debt",
        "net debt",
        (r"\bnet\s+debt\b", r"\bnet\s+cash\b"),
        needs=NEEDS_MONEY,
        fact_labels=("net_debt", "net_cash"),
        metric_aliases=("net_debt",),
    ),
    ResearchField(
        "metric:mineral_resource",
        "mineral resource or reserve",
        (
            r"\b(?:mineral|ore)\s+(?:resources?|reserves?)\b",
            r"\b(?:measured|indicated|inferred)\s+(?:and\s+(?:indicated|inferred)\s+)?resources?\b",
            r"\b(?:proven|probable)\s+(?:and\s+probable\s+)?reserves?\b",
        ),
        needs=NEEDS_TONNAGE_AND_GRADE,
        supersedable=True,
    ),
)

FIELDS_BY_KEY: dict[str, ResearchField] = {f.key: f for f in FIELDS}
FIELD_KEYS: frozenset[str] = frozenset(FIELDS_BY_KEY)
SUPERSEDABLE_FIELDS: frozenset[str] = frozenset(f.key for f in FIELDS if f.supersedable)
CAPEX_FAMILY = "metric:capex"
CAPEX_SUBTYPES: tuple[str, ...] = (
    "metric:capex_sustaining", "metric:capex_project", "metric:capex_period",
)

_COMPILED: dict[str, tuple[re.Pattern[str], ...]] = {
    f.key: tuple(re.compile(p) for p in f.patterns) for f in FIELDS
}
_SUBTYPE_CUES: dict[str, tuple[re.Pattern[str], ...]] = {
    "metric:capex_sustaining": tuple(re.compile(p) for p in _SUSTAINING_CAPEX),
    "metric:capex_project": tuple(re.compile(p) for p in _PROJECT_CAPEX),
    "metric:capex_period": tuple(re.compile(p) for p in _PERIOD_CAPEX),
}


def family_of(field_key: str) -> str:
    """The family a field belongs to (itself, for a field without sub-types)."""
    found = FIELDS_BY_KEY.get(field_key)
    return (found.family or found.key) if found else field_key


# ── Text helpers ────────────────────────────────────────────────────────────── #

#: A denial scopes to its CLAUSE. Same split the thesis grader uses.
CLAUSE_SPLIT_RE = re.compile(
    r"[;.]\s+|,\s+(?:but|although|though|while|whereas|yet)\s+|\s+but\s+"
)
#: Anywhere in a clause, these make it a statement of absence, withdrawal, deferral or
#: sensitivity — not of a value.
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|none|nothing|nor|neither|without|lacks?|lacking|absent|omits?|"
    r"omitted|unavailable|undisclosed|unknown|unclear|unconfirmed|unquantified|missing|"
    r"cannot|could\s+not|did\s+not|does\s+not|yet\s+to|failed\s+to|unable\s+to|"
    r"withdr[ae]wn?|withdrew|under\s+review|superseded|no\s+longer|deferred|delayed|"
    r"suspended|on\s+hold|paused|sensitive|sensitivity|outside)\b|n['’]t\b",
    re.IGNORECASE,
)
#: Phrases containing a negation word that do NOT negate the value beside them.
_NEUTRAL_NEGATION_RE = re.compile(
    r"\bno\s+(?:material\s+)?changes?\b|\bnot\s+(?:materially\s+)?changed\b|\bunchanged\b",
    re.IGNORECASE,
)
#: A figure: a number that is not merely a year.
_NUMBER_RE = re.compile(r"(?<![\w.])-?\d[\d,]*(?:\.\d+)?")
_YEAR_RE = re.compile(r"\b(?:19|20)\d{2}\b")
_DATE_CUE_RE = re.compile(
    r"\b(?:19|20)\d{2}\b|\b(?:q[1-4]|h[12])\b|\b(?:first|second|third|fourth)\s+"
    r"(?:quarter|half)\b",
    re.IGNORECASE,
)
_CURRENCY = (
    r"us\$|a\$|c\$|nz\$|hk\$|s\$|\$|£|€|\busd|\baud|\bcad|\bgbp|\beur|\bzar|\bnok|\bsek|"
    r"\bdkk|\bchf|\bjpy|\bcny"
)
MONEY_RE = re.compile(
    r"(?P<cur>" + _CURRENCY + r")\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>bn|billion|b|mn|million|m|k|thousand)?(?![\w])",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:k|m)?\s*(?:"
    r"tpa|tpy|t/y(?:r)?|t/a|t/d|tpd|mtpa|ktpa|mtpy|mt/y|mt\s+(?:per|a)\s+(?:annum|year)|"
    r"(?:tonnes?|tons?)\s+(?:per|a|an)\s+(?:annum|year|day)|mw|gw|mwh|gwh|"
    r"oz/y(?:r)?|ounces?\s+(?:per|a)\s+year|lb/y(?:r)?|pounds?\s+(?:per|a)\s+year|"
    r"bpd|barrels?\s+(?:per|a)\s+day|units?\s+(?:per|a)\s+year)\b",
    re.IGNORECASE,
)
_TONNAGE_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:mt|kt|million\s+tonnes|thousand\s+tonnes|tonnes|t)\b",
    re.IGNORECASE,
)
_GRADE_RE = re.compile(r"\d[\d,]*(?:\.\d+)?\s*(?:%|g/t|ppm|oz/t|lb/t)", re.IGNORECASE)
_IRR_RATE_RE = re.compile(
    r"\b(?:irr|internal\s+rate\s+of\s+return)\b[^.;]{0,25}?\d[\d.]*\s*%", re.IGNORECASE
)
_TARGET_CUE = (
    r"expected|expects|scheduled|planned|plans|targeted|targeting|targets?|forecast|"
    r"anticipated|anticipates|slated|due|on\s+track|guided|guidance|aims?|intends?|"
    r"estimated|projected"
)
_TARGET_YEAR_RE = re.compile(
    r"\b(?:" + _TARGET_CUE + r")\b[^.;]{0,45}?"
    r"(?P<sub>\b(?:q[1-4]|h[12]|(?:first|second)\s+half|mid|early|late|end)\b[\s,-]*(?:of\s+)?)?"
    r"\b(?P<year>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_RUNWAY_VALUE_RE = re.compile(
    r"\b\d+(?:\.\d+)?\s*(?:months|quarters|years)\b|"
    r"\b(?:into|until|through|to)\s+(?:(?:q[1-4]|h[12]|mid|early|late|end\s+of)[\s-]*)?"
    r"(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
_COMMITMENT_RE = re.compile(
    r"\b(?:signed|binding|executed|entered\s+into|agreed|secured|contracted)\b", re.IGNORECASE
)
_COUNTERPARTY_RE = re.compile(r"\bwith\s+(?:[A-Z][\w&.-]+)")


def _raw_clauses(text: str | None) -> list[str]:
    return [c for c in CLAUSE_SPLIT_RE.split(text or "") if c and c.strip()]


def clauses(text: str | None) -> list[str]:
    return [c.lower() for c in _raw_clauses(text)]


def is_negated(clause: str) -> bool:
    return _NEGATION_RE.search(_NEUTRAL_NEGATION_RE.sub(" ", clause or "")) is not None


def has_figure(clause: str) -> bool:
    for match in _NUMBER_RE.finditer(clause or ""):
        token = match.group(0).replace(",", "").lstrip("-")
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        return True
    return False


def has_date(clause: str) -> bool:
    return _DATE_CUE_RE.search(clause or "") is not None


def has_money(clause: str) -> bool:
    return MONEY_RE.search(clause or "") is not None


def target_years(clause: str) -> frozenset[str]:
    """The TARGET of a milestone clause: the year after a target cue, with its half or
    quarter ("h2 2027"). Incidental years ("following the 2026 DFS") are not targets."""
    out: set[str] = set()
    for match in _TARGET_YEAR_RE.finditer(clause or ""):
        sub = (match.group("sub") or "").strip().lower()
        sub = re.sub(r"[\s,-]*(?:of\s*)?$", "", sub)
        out.add(f"{sub} {match.group('year')}".strip())
    return frozenset(out)


def _satisfies(needs: str | None, clause_raw: str) -> bool:
    low = clause_raw.lower()
    if needs is None:
        return True
    if needs == NEEDS_FIGURE:
        return has_figure(low)
    if needs == NEEDS_DATE:
        return has_date(low)
    if needs == NEEDS_MONEY:
        return has_money(low)
    if needs == NEEDS_CAPACITY:
        return _CAPACITY_RE.search(low) is not None
    if needs == NEEDS_TONNAGE_AND_GRADE:
        return _TONNAGE_RE.search(low) is not None and _GRADE_RE.search(low) is not None
    if needs == NEEDS_PERCENT_RATE:
        return _IRR_RATE_RE.search(low) is not None
    if needs == NEEDS_TARGET_DATE:
        return bool(target_years(low))
    if needs == NEEDS_DURATION_OR_DATE:
        return _RUNWAY_VALUE_RE.search(low) is not None
    if needs == NEEDS_COMMITMENT:
        return _COMMITMENT_RE.search(low) is not None and (
            _COUNTERPARTY_RE.search(clause_raw) is not None or has_figure(low)
        )
    return False


def _matches(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


def _capex_subtype(low: str) -> str | None:
    for key in CAPEX_SUBTYPES:  # sustaining before project before period
        if _matches(_SUBTYPE_CUES[key], low):
            return key
    return None


def _clause_fields(low: str) -> list[str]:
    """Families named in a clause, plus the capex sub-type when the clause says one."""
    out: list[str] = []
    for f in FIELDS:
        if f.family is not None:
            continue
        if _matches(_COMPILED[f.key], low):
            out.append(f.key)
    if CAPEX_FAMILY in out:
        sub = _capex_subtype(low)
        if sub:
            out.insert(out.index(CAPEX_FAMILY) + 1, sub)
    return out


# ── Classifiers ─────────────────────────────────────────────────────────────── #


def fields_mentioned(text: str | None) -> tuple[str, ...]:
    """Every field ``text`` is ABOUT, negated or not — the reading for a gap.

    A gap is a statement of absence by construction ("no capex figure was acquired"),
    so its negation is what it is; stripping it would leave it about nothing.
    """
    low = (text or "").lower().replace("_", " ")
    out: list[str] = []
    for clause in [low, *clauses(low)]:
        for key in _clause_fields(clause):
            if key not in out:
                out.append(key)
    return tuple(out)


def fields_stated(text: str | None) -> tuple[str, ...]:
    """Every field ``text`` AFFIRMATIVELY states a value for — the reading for a finding.

    Per clause: the clause names the field, does not negate / withdraw / hedge it, and
    carries the value the field needs. "Capex is not disclosed; nameplate capacity of
    20,000 tpa" states capacity and not capex.
    """
    out: list[str] = []
    for raw in _raw_clauses(text):
        low = raw.lower()
        if is_negated(low):
            continue
        for key in _clause_fields(low):
            found = FIELDS_BY_KEY[key]
            if key in out or not _satisfies(found.needs, raw):
                continue
            out.append(key)
    return tuple(out)


def field_clause(text: str | None, field_key: str) -> str | None:
    """The first affirmative clause stating ``field_key``, lower-cased, or ``None``."""
    if field_key not in FIELDS_BY_KEY:
        return None
    for raw in _raw_clauses(text):
        low = raw.lower()
        if is_negated(low) or field_key not in _clause_fields(low):
            continue
        if _satisfies(FIELDS_BY_KEY[field_key].needs, raw):
            return low
    return None


def fields_for_metrics(metrics: Iterable[str]) -> tuple[str, ...]:
    """Fields a question's ``required_metrics`` / calculation keys ask for."""
    wanted = {str(m).strip().lower() for m in metrics if m}
    return tuple(f.key for f in FIELDS if wanted & set(f.metric_aliases))


def field_for_fact_label(label: str | None) -> str | None:
    low = str(label or "").strip().lower()
    for f in FIELDS:
        if low in f.fact_labels:
            return f.key
    return None


def label_of(field_key: str) -> str:
    found = FIELDS_BY_KEY.get(field_key)
    return found.label if found else field_key


def is_known_field(value: str | None) -> bool:
    return bool(value) and value in FIELD_KEYS


# ── Values: what a clause says a field IS ──────────────────────────────────── #

_SCALE = {
    "": 1.0, "k": 1e3, "thousand": 1e3, "m": 1e6, "mn": 1e6, "million": 1e6,
    "b": 1e9, "bn": 1e9, "billion": 1e9,
}
_CURRENCY_ALIASES = {"$": "usd", "us$": "usd", "usd": "usd", "a$": "aud", "aud": "aud",
                     "c$": "cad", "cad": "cad", "£": "gbp", "gbp": "gbp", "€": "eur",
                     "eur": "eur"}


def money_values(clause: str | None) -> tuple[tuple[str, float, float], ...]:
    """``(currency, value, tolerance)`` per amount, scale-normalised.

    The tolerance is half a unit of the last digit written, so "US$0.3bn" and "US$302m"
    are the same statement at different precision, not a change of guidance.
    """
    out: list[tuple[str, float, float]] = []
    for match in MONEY_RE.finditer(clause or ""):
        cur_raw = match.group("cur").lower().strip()
        currency = _CURRENCY_ALIASES.get(cur_raw, cur_raw)
        number = match.group("num").replace(",", "")
        scale = _SCALE.get((match.group("scale") or "").lower(), 1.0)
        try:
            value = float(number) * scale
        except ValueError:
            continue
        decimals = len(number.split(".", 1)[1]) if "." in number else 0
        out.append((currency, value, 0.5 * (10 ** -decimals) * scale))
    return tuple(out)


# ── Project identity ────────────────────────────────────────────────────────── #

_ASSET_NOUNS = (
    "project|projects|mine|mines|plant|refinery|facility|deposit|hub|operation|"
    "concentrator|smelter|site|field"
)
_PROJECT_RE = re.compile(
    r"\b((?:[A-Z][\w'’-]*|\d+)(?:\s+(?:[A-Z][\w'’-]*|\d+)){0,3})\s+(?i:"
    + _ASSET_NOUNS + r")\b"
)
_PROJECT_STOPWORDS = frozenset(
    "the this that these those its our their each a an any all new existing current "
    "company group issuer first second main proposed planned flagship production "
    "processing mining capex capital initial sustaining development construction "
    "commissioning nameplate design".split()
)
_ASSET_TOKENS = frozenset(_ASSET_NOUNS.split("|"))
_STAGE_RE = re.compile(r"\b(stage|phase|train|module)\s*(\d+|[ivx]{1,4})\b", re.IGNORECASE)
_EXPANSION_RE = re.compile(r"\bexpansion\b", re.IGNORECASE)


def project_key(text: str | None) -> str | None:
    """The project or asset a statement names, WITH its stage ("foo stage 2").

    Read from capitalised words before an asset noun — a shape, never a list of names.
    The stage, phase or expansion named beside it is part of the identity: Stage 1 and
    Stage 2 of one project are two projects with two schedules. ``None`` when nothing is
    named.
    """
    source = text or ""
    for match in _PROJECT_RE.finditer(source):
        tokens = [
            t for t in re.findall(r"[\w'’-]+", match.group(1).lower())
            if t not in _PROJECT_STOPWORDS and t not in _ASSET_TOKENS and not t.isdigit()
            and t not in {"stage", "phase", "train", "module"}
            and not re.fullmatch(r"[ivx]{1,4}", t)
        ]
        if not tokens:
            continue
        window = source[max(0, match.start() - 30): match.end() + 40]
        qualifiers = _qualifiers(window)
        return " ".join(tokens + qualifiers)[:120]
    qualifiers = _qualifiers(source)
    return " ".join(qualifiers) if qualifiers else None


def _qualifiers(window: str) -> list[str]:
    out = [f"{m.group(1).lower()} {m.group(2).lower()}" for m in _STAGE_RE.finditer(window)]
    if _EXPANSION_RE.search(window):
        out.append("expansion")
    return list(dict.fromkeys(out))[:2]


def projects_compatible(a: str | None, b: str | None) -> bool | None:
    """``True`` same project, ``False`` different projects, ``None`` when one is unnamed.

    Token containment, so "foo" and "foo bar" are one project. Callers that must not
    mix a project with one of its stages compare keys for equality instead.
    """
    if not a or not b:
        return None if (a or b) else True
    ta, tb = set(a.split()), set(b.split())
    return ta <= tb or tb <= ta


# ── Periods and dates ───────────────────────────────────────────────────────── #

_MONTHS = ("january february march april may june july august september october "
           "november december").split()
_FY_RE = re.compile(r"\bfy\s?'?(?P<y>(?:19|20)?\d{2})\b", re.IGNORECASE)
_HALF_RE = re.compile(
    r"\b(?:(?P<h>h[12])|(?P<hw>first|second)\s+half)\s*(?:of\s+)?(?:fy\s?)?"
    r"(?P<y>(?:19|20)\d{2})\b|\b(?P<y2>(?:19|20)\d{2})[-\s](?P<h2>h[12])\b",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(
    r"\b(?P<q>q[1-4])\s*(?:fy\s?)?(?P<y>(?:19|20)\d{2})\b|"
    r"\b(?P<y2>(?:19|20)\d{2})[-\s](?P<q2>q[1-4])\b",
    re.IGNORECASE,
)
_ASAT_RE = re.compile(
    r"\b(?:at|as\s+at|as\s+of)\s+(?P<d>\d{1,2})\s+(?P<m>" + "|".join(_MONTHS)
    + r")\s+(?P<y>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_YEAR_ENDED_RE = re.compile(
    r"\b(?:year|twelve\s+months|12\s+months)\s+ended\s+(?:\d{1,2}\s+\w+\s+)?"
    r"(?P<y>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)


def statement_period(text: str | None) -> str | None:
    """The reporting period a statement is FOR, read from its words, normalised.

    ``FY2025`` | ``2026-H1`` | ``2025-Q3`` | ``2026-06-30`` (a balance at a date) |
    ``None``. Used when the evidence carried no ``period_key``, so "revenue for FY2024"
    and "revenue for FY2025" are never treated as one period.
    """
    source = text or ""
    q = _QUARTER_RE.search(source)
    if q:
        year = q.group("y") or q.group("y2")
        quarter = (q.group("q") or q.group("q2")).upper()
        return f"{year}-{quarter}"
    h = _HALF_RE.search(source)
    if h:
        year = h.group("y") or h.group("y2")
        half = (h.group("h") or h.group("h2") or "").upper()
        if not half:
            half = "H1" if (h.group("hw") or "").lower() == "first" else "H2"
        return f"{year}-{half}"
    fy = _FY_RE.search(source)
    if fy:
        year = fy.group("y")
        return f"FY{year if len(year) == 4 else '20' + year}"
    ended = _YEAR_ENDED_RE.search(source)
    if ended:
        return f"FY{ended.group('y')}"
    asat = _ASAT_RE.search(source)
    if asat:
        month = _MONTHS.index(asat.group("m").lower()) + 1
        try:
            return date(int(asat.group("y")), month, int(asat.group("d"))).isoformat()
        except ValueError:
            return None
    return None


def normalise_period(period_key: str | None) -> str | None:
    """``FY2025`` / ``2025-Q3`` / ``2025-H1`` / ISO date, from a stored period key."""
    text = str(period_key or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        return text
    parsed = statement_period(text)
    if parsed:
        return parsed
    if re.fullmatch(r"(?:19|20)\d{2}", text):
        return f"FY{text}"
    return text.upper()


def period_rank(period_key: str | None) -> tuple[int, int] | None:
    """A comparable (year, sub-period) for a period key, or ``None`` when unreadable.

    Sub-period: Q1..Q4 → 1..4, H1 → 5, H2 → 6, a full year → 9.
    """
    normalised = normalise_period(period_key)
    if not normalised:
        return None
    match = re.fullmatch(r"FY(\d{4})", normalised)
    if match:
        return int(match.group(1)), 9
    match = re.fullmatch(r"(\d{4})-Q([1-4])", normalised)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"(\d{4})-H([12])", normalised)
    if match:
        return int(match.group(1)), 4 + int(match.group(2))
    match = re.fullmatch(r"(\d{4})-\d{2}-\d{2}", normalised)
    if match:
        return int(match.group(1)), 9
    return None


_REAL_PERIOD_RE = re.compile(
    r"^(?:FY\d{4}|\d{4}-Q[1-4]|\d{4}-H[12]|\d{4}|\d{4}-\d{2}-\d{2})$", re.IGNORECASE
)


def is_financial_period(period_key: str | None) -> bool:
    """A REAL reporting period — FY2025, 2025-Q3, a balance date — not "no period"."""
    return bool(period_key) and _REAL_PERIOD_RE.match(str(period_key).strip()) is not None


def requested_period(text: str | None) -> str | None:
    """The reporting period a gap names explicitly, normalised, else ``None``."""
    return statement_period(text)


def years_in(text: str | None) -> frozenset[int]:
    return frozenset(int(y) for y in _YEAR_RE.findall(text or ""))


def parse_date(value: object) -> date | None:
    """A publication date from a tool field: a date, a datetime or an ISO string."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    match = re.match(r"^(\d{4})-(\d{2})-(\d{2})", text)
    if match is None:
        return None
    try:
        return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))
    except ValueError:
        return None


#: "The 2022 PFS estimated …", "the DFS of 2021": a clause QUOTING an earlier document.
_HISTORICAL_CITATION_RE = re.compile(
    r"\b(?:19|20)\d{2}\s+(?:dfs|pfs|bfs|feasibility|scoping|pea|study|studies|estimate|"
    r"annual\s+report|report|plan|guidance)\b|"
    r"\b(?:dfs|pfs|bfs|feasibility\s+study|scoping\s+study|pea|study)\s+(?:of|from|in|"
    r"dated|published\s+in)\s+(?:\w+\s+)?(?:19|20)\d{2}\b",
    re.IGNORECASE,
)
#: A gap asking for the CURRENT / latest / updated value.
RECENCY_RE = re.compile(
    r"\b(?:current|updated?|latest|revised|most\s+recent|up-to-date|new)\b", re.IGNORECASE
)


def is_historical_citation(clause: str | None) -> bool:
    return _HISTORICAL_CITATION_RE.search(clause or "") is not None


# ── The persisted claim key ─────────────────────────────────────────────────── #

_CLAIM_KEY_MAX = 240


def claim_key_for(statement: str | None) -> str | None:
    """``research_findings.claim_key``: the fields a finding states, and its project.

    ``"metric:capex+metric:capex_project@foo stage 2"`` — sorted fields, then the
    project it names. ``None`` when it states no field of the vocabulary.
    """
    stated = sorted(fields_stated(statement))
    if not stated:
        return None
    key = "+".join(stated)
    project = project_key(statement)
    if project:
        key = f"{key}@{project}"
    return key[:_CLAIM_KEY_MAX]


def parse_claim_key(value: str | None) -> tuple[tuple[str, ...], str | None] | None:
    """``(fields, project)`` from a claim key this module wrote, else ``None``.

    Anything that does not parse into KNOWN fields is not trusted: the caller
    classifies the statement instead.
    """
    text = str(value or "").strip()
    if not text:
        return None
    head, _, project = text.partition("@")
    fields = tuple(part for part in head.split("+") if part)
    if not fields or any(f not in FIELD_KEYS for f in fields):
        return None
    return fields, (project or None)


# ── Document kinds a gap may name ───────────────────────────────────────────── #

DOC_ANNUAL = "annual_report"
DOC_INTERIM = "interim_report"
DOC_RESULTS = "results_release"

_DOC_PATTERNS: dict[str, re.Pattern[str]] = {
    DOC_ANNUAL: re.compile(
        r"\bannual\s+(?:report|accounts|financial\s+(?:report|statements))\b|\b10-k\b|"
        r"\b20-f\b|\b40-f\b",
        re.IGNORECASE,
    ),
    DOC_INTERIM: re.compile(
        r"\b(?:half-?year(?:ly)?|interim|quarterly)\s+(?:report|results|accounts|"
        r"financial\s+statements)\b|\b10-q\b",
        re.IGNORECASE,
    ),
    DOC_RESULTS: re.compile(
        r"\b(?:results\s+(?:announcement|release)|preliminary\s+results)\b", re.IGNORECASE
    ),
}

#: A gap recording that a document could not be HAD — not that it lacks something.
ACQUISITION_FAILURE_RE = re.compile(
    r"\b(?:could\s+not|cannot|couldn['’]t|unable\s+to|failed\s+to)\s+(?:be\s+)?"
    r"(?:fetch|retriev|access|download|reach|acquir|obtain|load|open)\w*|"
    r"\bunreachable\b|"
    r"\b(?:was|were|is|are)\s+not\s+(?:fetched|retrieved|accessed|downloaded|acquired|"
    r"reachable|in\s+the\s+corpus)\b|\bfetch\s+failed\b|\btimed\s+out\b",
    re.IGNORECASE,
)


def document_kinds_mentioned(text: str | None) -> tuple[str, ...]:
    return tuple(kind for kind, pattern in _DOC_PATTERNS.items() if pattern.search(text or ""))


def is_acquisition_failure(text: str | None) -> bool:
    return ACQUISITION_FAILURE_RE.search(text or "") is not None


__all__ = [
    "CAPEX_FAMILY",
    "CAPEX_SUBTYPES",
    "DOC_ANNUAL",
    "DOC_INTERIM",
    "DOC_RESULTS",
    "FIELDS",
    "FIELDS_BY_KEY",
    "FIELD_KEYS",
    "RECENCY_RE",
    "SUPERSEDABLE_FIELDS",
    "ResearchField",
    "claim_key_for",
    "clauses",
    "document_kinds_mentioned",
    "family_of",
    "field_clause",
    "field_for_fact_label",
    "fields_for_metrics",
    "fields_mentioned",
    "fields_stated",
    "has_date",
    "has_figure",
    "has_money",
    "is_acquisition_failure",
    "is_financial_period",
    "is_historical_citation",
    "is_known_field",
    "is_negated",
    "label_of",
    "money_values",
    "normalise_period",
    "parse_claim_key",
    "parse_date",
    "period_rank",
    "project_key",
    "projects_compatible",
    "requested_period",
    "statement_period",
    "target_years",
    "years_in",
]
