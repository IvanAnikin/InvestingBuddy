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
    r"\b(?:capex|capital\s+(?:cost|expenditure))s?\s+estimates?\b",
    r"\bcapital\s+costs?\b",
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
    r"\b(?:spent|incurred|invested|paid)\b",
    r"\bduring\s+(?:the\s+)?(?:year|half|quarter|period|(?:19|20)\d{2})\b",
    r"\b(?:in|for)\s+the\s+(?:year|half|half-year|quarter|six\s+months|twelve\s+months)\s+"
    r"(?:to|ended|ending)\b",
    r"\b(?:year|half|half-year|quarter|six\s+months)\s+to\s+\d",
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
            r"\bcash\s+(?:at|as\s+at|as\s+of)\s+\d",
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
    "metric:capex_sustaining", "metric:capex_period", "metric:capex_project",
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
    r"[;.]\s+|,\s+(?:but|although|though|while|whereas|yet)\s+|\s+but\s+|,\s+and\s+(?=the\s)"
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
    r"(?P<cur>" + _CURRENCY + r"|\brmb|\br(?=\d))\s?(?P<num>\d[\d,]*(?:\.\d+)?)\s*"
    r"(?P<scale>bn|billion|b|mn|million|m|k|thousand)?(?![\w])",
    re.IGNORECASE,
)
#: "302 million US dollars", "302m USD": the currency AFTER the amount.
MONEY_SUFFIX_RE = re.compile(
    r"(?<![\w.$£€])(?P<num>\d[\d,]*(?:\.\d+)?)\s*(?P<scale>bn|billion|mn|million|m|k|thousand)?"
    r"\s*(?P<cur>usd|aud|cad|gbp|eur|zar|rmb|cny|us\s+dollars|australian\s+dollars|"
    r"canadian\s+dollars|pounds\s+sterling|euros|rand)\b",
    re.IGNORECASE,
)
_CAPACITY_RE = re.compile(
    r"\d[\d,]*(?:\.\d+)?\s*(?:k|m|million\s+|thousand\s+)?\s*(?:"
    r"tpa|tpy|t/y(?:r)?|t/a|t/d|tpd|mtpa|ktpa|mtpy|mt/y|"
    r"(?:mt|kt|t|tonnes?|tons?|oz|ounces?|lbs?|pounds?|units?|barrels?)\s+"
    r"(?:of\s+[a-z][a-z\s-]{0,30}?\s+)?(?:per|a|an)\s+(?:annum|year|day)|"
    r"mw|gw|mwh|gwh|oz/y(?:r)?|lb/y(?:r)?|bpd)\b",
    re.IGNORECASE,
)
#: Output in a ramp-up or a first year is not nameplate capacity.
_RAMP_RE = re.compile(
    r"\bramp[\s-]?up\b|\bfirst\s+(?:full\s+)?year\b|\byear\s+(?:one|1)\b|"
    r"\binitial\s+(?:production\s+)?rate\b",
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


#: A value WITHDRAWN, deferred, unconfirmed or replaced — anywhere in its clause,
#: including a relative clause ("…, which was withdrawn in March"). Checked BEFORE any
#: subordinate material is set aside: a withdrawn value never closes or supersedes.
_WITHDRAWAL_RE = re.compile(
    r"\bwithdr[ae]w\w*|\bdefer\w*|\bsuspend\w*|\bsuspension\b|\bcancel\w*|\babandon\w*|"
    r"\brevok\w*|\brescind\w*|\breplaced\b|\bsuperseded\b|\bno\s+longer\b|"
    r"\bunder\s+review\b|\bunconfirmed\b|\bnot\s+(?:yet\s+)?(?:been\s+)?(?:approved|"
    r"confirmed|finali[sz]ed|sanctioned)\b|\bpending\s+(?:approval|review|confirmation)\b",
    re.IGNORECASE,
)
#: Subordinate material whose negation governs something ELSE, set aside before the
#: negation check — narrowly:
#:  * a relative clause expecting NO CHANGE ("which is not expected to change");
#:  * "excluding / not including <cost noun phrase>" up to its noun, never the main
#:    verb after it ("… excluding contingency has not been confirmed" stays negated);
#:  * "includes / with no contingency", "with no further delays expected";
#:  * "after the delayed FID" (another milestone's delay, not this value's).
_SUBORDINATE_RE = re.compile(
    r",?\s*(?:which|that)\s+(?:is|are|was|were)\s+not\s+(?:currently\s+)?expected\s+to\s+"
    r"(?:change|be\s+revised|be\s+changed|move|increase)\b[^,;]*|"
    r"\b(?:not\s+including|excluding|exclusive\s+of)\s+(?:[^\s,;]+\s+){0,5}?"
    r"(?:costs?|contingency|contingencies|capital|fees?|tax(?:es)?|royalt(?:y|ies)|"
    r"allowances?|escalation|leases?)\b|"
    r"\b(?:includes?|including|with)\s+no\s+(?:contingency|allowance|escalation)\b|"
    r"\bwith\s+no\s+(?:further\s+)?(?:delays?|changes?)\s+(?:expected|anticipated|forecast)\b|"
    r"\b(?:after|following)\s+the\s+delayed\s+[\w-]+",
    re.IGNORECASE,
)
_PLACE_OUTSIDE_RE = re.compile(r"\boutside\s+(?:of\s+)?(?:the\s+)?[A-Z][\w-]*")


def is_withdrawn(clause: str | None) -> bool:
    return _WITHDRAWAL_RE.search(clause or "") is not None


def is_negated(clause: str, raw: str | None = None) -> bool:
    """Does a negation govern THIS clause's value? ``raw`` (original case) lets a place
    name after "outside" be recognised as geography. A withdrawal anywhere in the clause
    always negates."""
    text = raw if raw is not None else clause or ""
    if is_withdrawn(text):
        return True
    text = _PLACE_OUTSIDE_RE.sub(" ", text)
    text = _SUBORDINATE_RE.sub(" ", text)
    return _NEGATION_RE.search(_NEUTRAL_NEGATION_RE.sub(" ", text)) is not None


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
    return MONEY_RE.search(clause or "") is not None or (
        MONEY_SUFFIX_RE.search(clause or "") is not None
    )


def target_years(clause: str) -> frozenset[str]:
    """The TARGET of a milestone clause: the year after a target cue, with its half or
    quarter ("h2 2027"). Incidental years ("following the 2026 DFS") are not targets."""
    out: set[str] = set()
    for match in _TARGET_YEAR_RE.finditer(clause or ""):
        before = (clause or "")[: match.start()].lower()
        # "was expected in 2021", "had been planned for 2024": a FORMER target.
        if re.search(r"\b(?:was|were|had\s+been|originally|previously|initially)\s*$", before):
            continue
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
        return _CAPACITY_RE.search(low) is not None and _RAMP_RE.search(low) is None
    if needs == NEEDS_TONNAGE_AND_GRADE:
        # An Exploration Target is explicitly NOT a resource estimate.
        return (
            _TONNAGE_RE.search(low) is not None and _GRADE_RE.search(low) is not None
            and "exploration target" not in low
        )
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
    # Sustaining, then a REPORTING-PERIOD cue, then study words: "capex for FY2024 was
    # £3.1m, mostly on DFS work" is money spent in a period, not a project estimate.
    for key in CAPEX_SUBTYPES:
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


def _stated_keys(low: str) -> list[str]:
    """Fields a clause can STATE: when it names two milestones ("first production in
    2027 after the delayed FID") its one date belongs to neither with certainty."""
    keys = _clause_fields(_SUBORDINATE_RE.sub(" ", low))
    milestones = [k for k in keys if k.startswith("milestone:")]
    if len(milestones) > 1:
        keys = [k for k in keys if not k.startswith("milestone:")]
    return keys


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
        if is_negated(low, raw):
            continue
        for key in _stated_keys(low):
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
        if is_negated(low, raw) or field_key not in _stated_keys(low):
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
_CURRENCY_ALIASES = {"$": "usd", "us$": "usd", "usd": "usd", "us dollars": "usd",
                     "a$": "aud", "aud": "aud", "australian dollars": "aud",
                     "c$": "cad", "cad": "cad", "canadian dollars": "cad",
                     "£": "gbp", "gbp": "gbp", "pounds sterling": "gbp",
                     "€": "eur", "eur": "eur", "euros": "eur",
                     "r": "zar", "zar": "zar", "rand": "zar", "rmb": "cny", "cny": "cny"}


def money_values(clause: str | None) -> tuple[tuple[str, float, float], ...]:
    """``(currency, value, tolerance)`` per amount, scale-normalised.

    The tolerance is half a unit of the last digit written; ``compare_values`` decides
    how far a rounded form may stretch.
    """
    out: list[tuple[str, float, float]] = []
    for pattern in (MONEY_RE, MONEY_SUFFIX_RE):
        for match in pattern.finditer(clause or ""):
            cur_raw = re.sub(r"\s+", " ", match.group("cur").lower().strip())
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
_MONTH_ALT = "|".join(_MONTHS)
_DAY_MONTH_YEAR = (
    r"(?P<d>\d{1,2})\s+(?P<m>" + _MONTH_ALT + r")\s+(?P<y>(?:19|20)\d{2})"
)
_FY_RE = re.compile(r"\bfy\s?'?(?P<y>(?:19|20)?\d{2})\b", re.IGNORECASE)
#: "half-year ended 31 December 2025", "six months to 30 June 2026" — a HALF, whatever
#: word "year" appears in it. Matched before any full-year form.
_HALF_ENDED_RE = re.compile(
    r"\b(?:half[\s-]year|half|six\s+months|6\s+months|interim\s+period)\s+"
    r"(?:ended|ending|to)\s+" + _DAY_MONTH_YEAR,
    re.IGNORECASE,
)
_YEAR_ENDED_DATE_RE = re.compile(
    r"\b(?:year|twelve\s+months|12\s+months|financial\s+year)\s+(?:ended|ending|to)\s+"
    + _DAY_MONTH_YEAR,
    re.IGNORECASE,
)
_YEAR_ENDED_RE = re.compile(
    r"\b(?:year|twelve\s+months|12\s+months)\s+ended\s+(?P<y>(?:19|20)\d{2})\b",
    re.IGNORECASE,
)
_QUARTER_RE = re.compile(
    r"\b(?P<q>q[1-4])\s*(?P<fy>fy\s?'?)?(?P<y>(?:19|20)?\d{2})\b|"
    r"\b(?P<y2>(?:19|20)\d{2})[-\s](?P<q2>q[1-4])\b",
    re.IGNORECASE,
)
_HALF_RE = re.compile(
    r"\b(?:(?P<h>h[12])|(?P<hw>first|second)\s+half)\s*(?:of\s+)?(?P<fy>fy\s?'?)?"
    r"(?P<y>(?:19|20)?\d{2})\b|\b(?P<y2>(?:19|20)\d{2})[-\s](?P<h2>h[12])\b",
    re.IGNORECASE,
)
_CALENDAR_RE = re.compile(
    r"\b(?:calendar(?:\s+year)?|cy)\s?(?P<y>(?:19|20)\d{2})\b|"
    r"\b(?:revenues?|sales|turnover|capex|capital\s+expenditure|cash\s+flows?|earnings|"
    r"ebitda|results|net\s+debt)\s+(?:for|in)\s+(?:the\s+year\s+)?"
    r"(?P<y2>(?:19|20)\d{2})\b(?!\s*-)|"
    r"\b(?P<y3>(?:19|20)\d{2})\s+(?:revenues?|sales|turnover|capex|capital\s+expenditure|"
    r"cash\s+flows?|earnings|ebitda|results|net\s+debt)\b",
    re.IGNORECASE,
)
_ASAT_RE = re.compile(r"\b(?:at|as\s+at|as\s+of)\s+" + _DAY_MONTH_YEAR, re.IGNORECASE)


def _iso(match: re.Match[str]) -> str | None:
    month = _MONTHS.index(match.group("m").lower()) + 1
    try:
        return date(int(match.group("y")), month, int(match.group("d"))).isoformat()
    except ValueError:
        return None


def _year4(value: str) -> str:
    return value if len(value) == 4 else "20" + value


def statement_period(text: str | None) -> str | None:
    """The reporting period a statement is FOR, read from its words, normalised.

    ``FY2025`` (a fiscal year) | ``FY2026-H1`` / ``FY2026-Q3`` (a fiscal half/quarter) |
    ``2026-H1`` / ``2025-Q3`` (a calendar half/quarter) | ``CY2025`` (a calendar year) |
    ``FYE2026-06-30`` (a year ENDED on a date) | ``HYE2025-12-31`` (a half ended on a
    date) | ``2026-06-30`` (a balance at a date) | ``None``.

    Deliberately literal: FY2025, CY2025 and "year ended 30 June 2025" are three
    different strings, and only an equal string is the same period. Which calendar a
    company's fiscal year follows is not something a sentence settles.
    """
    source = text or ""
    half_ended = _HALF_ENDED_RE.search(source)
    if half_ended:
        iso = _iso(half_ended)
        return f"HYE{iso}" if iso else None
    year_ended = _YEAR_ENDED_DATE_RE.search(source)
    if year_ended:
        iso = _iso(year_ended)
        return f"FYE{iso}" if iso else None
    q = _QUARTER_RE.search(source)
    if q and (q.group("y2") or len(q.group("y") or "") == 4 or q.group("fy")):
        if q.group("y2"):
            return f"{q.group('y2')}-{q.group('q2').upper()}"
        year = _year4(q.group("y"))
        quarter = q.group("q").upper()
        return f"FY{year}-{quarter}" if q.group("fy") else f"{year}-{quarter}"
    h = _HALF_RE.search(source)
    if h and (h.group("y2") or len(h.group("y") or "") == 4 or h.group("fy")):
        if h.group("y2"):
            return f"{h.group('y2')}-{h.group('h2').upper()}"
        half = (h.group("h") or "").upper() or (
            "H1" if (h.group("hw") or "").lower() == "first" else "H2"
        )
        year = _year4(h.group("y"))
        return f"FY{year}-{half}" if h.group("fy") else f"{year}-{half}"
    fy = _FY_RE.search(source)
    if fy:
        return f"FY{_year4(fy.group('y'))}"
    ended = _YEAR_ENDED_RE.search(source)
    if ended:
        return f"FY{ended.group('y')}"
    calendar = _CALENDAR_RE.search(source)
    if calendar:
        return f"CY{calendar.group('y') or calendar.group('y2') or calendar.group('y3')}"
    asat = _ASAT_RE.search(source)
    if asat:
        return _iso(asat)
    return None


def normalise_period(period_key: str | None) -> str | None:
    """A stored period key in the ``statement_period`` vocabulary."""
    text = str(period_key or "").strip()
    if not text:
        return None
    if re.fullmatch(r"(?:FYE|HYE)?\d{4}-\d{2}-\d{2}|CY\d{4}|FY\d{4}(?:-[HQ]\d)?|\d{4}-[HQ]\d",
                    text, re.IGNORECASE):
        return text.upper()
    parsed = statement_period(text)
    if parsed:
        return parsed
    if re.fullmatch(r"(?:19|20)\d{2}", text):
        return f"FY{text}"
    return text.upper()


def period_rank(period_key: str | None) -> tuple[int, int] | None:
    """A comparable (year, sub-period), or ``None`` when the period is not rankable.

    Sub-period: Q1..Q4 → 1..4, H1 → 5, H2 → 6, a full year or a date → 9. A half ended
    on a date is not ranked: which half it is depends on the fiscal calendar.
    """
    normalised = normalise_period(period_key)
    if not normalised:
        return None
    match = re.fullmatch(r"(?:FY|CY)(\d{4})", normalised)
    if match:
        return int(match.group(1)), 9
    match = re.fullmatch(r"(?:FY)?(\d{4})-Q([1-4])", normalised)
    if match:
        return int(match.group(1)), int(match.group(2))
    match = re.fullmatch(r"(?:FY)?(\d{4})-H([12])", normalised)
    if match:
        return int(match.group(1)), 4 + int(match.group(2))
    match = re.fullmatch(r"(?:FYE)?(\d{4})-\d{2}-\d{2}", normalised)
    if match:
        return int(match.group(1)), 9
    return None


def period_end_date(period: str | None) -> date | None:
    """The date a DATED period ends on (a balance date, a year or half ended)."""
    match = re.search(r"(\d{4}-\d{2}-\d{2})$", str(period or ""))
    return parse_date(match.group(1)) if match else None


_REAL_PERIOD_RE = re.compile(
    r"^(?:FY\d{4}(?:-[HQ]\d)?|CY\d{4}|\d{4}-Q[1-4]|\d{4}-H[12]|\d{4}|"
    r"(?:FYE|HYE)?\d{4}-\d{2}-\d{2})$",
    re.IGNORECASE,
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


#: …and without a year: "The PFS estimated …", "the previous capex estimate was …".
_HISTORICAL_NO_YEAR_RE = re.compile(
    r"\b(?:dfs|pfs|bfs|pea|scoping\s+study|feasibility\s+study|study)\s+(?:had\s+)?"
    r"(?:estimated|forecast|projected|assumed|put|valued|showed)\b|"
    r"\b(?:previous|prior|original|earlier|former|old|superseded|outdated)\s+"
    r"(?:[a-z-]+\s+){0,2}?(?:capex|capital|cost|costs|estimate|estimates|guidance|target|"
    r"schedule|timeline|plan|forecast|figure|npv|irr|resource|reserve|capacity)\b",
    re.IGNORECASE,
)


def is_historical_citation(clause: str | None) -> bool:
    return (
        _HISTORICAL_CITATION_RE.search(clause or "") is not None
        or _HISTORICAL_NO_YEAR_RE.search(clause or "") is not None
    )


# ── Qualifiers: what makes two values of one field NOT the same quantity ────── #

_QUALIFIER_RES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("plant", re.compile(r"\b(pilot|demonstration|demo|commercial|trial|test)\s+"
                         r"(?:plant|scale|facility|operation|module|line|circuit)\b", re.I)),
    ("tax", re.compile(r"\b(pre|post|after|before)[\s-]?tax\b", re.I)),
    ("case", re.compile(r"\b(base|expanded|expansion|upside|downside|low|high|alternative)"
                        r"\s+(?:case|scenario)\b", re.I)),
    ("cat", re.compile(r"\b(measured|indicated|inferred|proven|proved|probable)\b", re.I)),
    ("basis", re.compile(r"\b(real|nominal)\s+(?:terms|basis|dollars|discount)\b", re.I)),
)
_DISCOUNT_RE = re.compile(
    r"\bnpv\s?(\d{1,2}(?:\.\d)?)\b|(\d{1,2}(?:\.\d)?)\s*%\s*(?:real\s+|nominal\s+)?"
    r"discount",
    re.IGNORECASE,
)
_PRODUCT_STOP = frozenset(
    "of per a an the at by for from in into and or to with on is was will be which that "
    "capacity nameplate design plant".split()
)


def value_qualifiers(clause: str | None, field_key: str) -> frozenset[str]:
    """Qualifiers that make two values of one field different QUANTITIES.

    Tax basis and discount rate for NPV/IRR, category and resource-vs-reserve for a
    mineral estimate, the product a capacity is of, pilot vs commercial plant, a stage,
    phase or expansion, a named scenario. Two statements with different qualifier sets
    are never ordered in time, and a gap asking for one is not closed by another.
    """
    text = clause or ""
    out: set[str] = set()
    for kind, pattern in _QUALIFIER_RES:
        for match in pattern.finditer(text):
            value = match.group(1).lower()
            value = {"after": "post", "before": "pre", "proved": "proven",
                     "demo": "demonstration"}.get(value, value)
            out.add(f"{kind}:{value}")
    for match in _DISCOUNT_RE.finditer(text):
        out.add(f"disc:{match.group(1) or match.group(2)}")
    for match in _STAGE_RE.finditer(text):
        out.add(f"stage:{match.group(1).lower()} {match.group(2).lower()}")
    if _EXPANSION_RE.search(text):
        out.add("stage:expansion")
    if field_key == "metric:mineral_resource":
        low = text.lower()
        if "reserve" in low:
            out.add("class:reserve")
        if "resource" in low:
            out.add("class:resource")
    if field_key == "metric:production_capacity":
        for match in _CAPACITY_RE.finditer(text):
            words = re.findall(r"[a-z][a-z0-9-]*", text[match.end(): match.end() + 40].lower())
            product: list[str] = []
            for word in words:
                if word in _PRODUCT_STOP:
                    if product:
                        break
                    continue
                product.append(word)
                if len(product) == 2:
                    break
            if product:
                out.add("product:" + " ".join(product))
            of = re.search(r"\bof\s+([a-z][a-z-]*(?:\s+(?!per\b|an?\b)[a-z][a-z-]*)?)",
                           match.group(0).lower())
            if of:
                out.add("product:" + of.group(1))
    return frozenset(out)


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
    "is_withdrawn",
    "label_of",
    "money_values",
    "normalise_period",
    "parse_claim_key",
    "parse_date",
    "period_rank",
    "period_end_date",
    "project_key",
    "projects_compatible",
    "requested_period",
    "statement_period",
    "target_years",
    "value_qualifiers",
    "years_in",
]
