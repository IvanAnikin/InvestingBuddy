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
* **Generic.** Patterns name the concept ("capital expenditure", "nameplate capacity",
  "first production"), never an issuer, a project or a commodity. A project NAME is read
  only as an identity (``project_key``) and never decides a field.
* **Fail closed.** Text that matches no pattern has NO field, and a gap with no field is
  never closed. A finding clause that negates ("capex is not disclosed") states nothing
  about that field. A metric needs a figure in the same clause and a milestone needs a
  date: "capex was discussed" does not close a gap about the capex figure.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime

# ── The vocabulary ──────────────────────────────────────────────────────────── #

NEEDS_FIGURE = "figure"
NEEDS_DATE = "date"


@dataclass(frozen=True)
class ResearchField:
    key: str
    label: str
    #: Regexes over LOWER-CASED text, each bounded by ``\b``.
    patterns: tuple[str, ...]
    #: What an affirmative statement of this field must also contain to count.
    needs: str | None = None
    #: Validated fact labels (``extracted_facts.label``) that state this field.
    fact_labels: tuple[str, ...] = ()
    #: Playbook ``required_metrics`` / calculation keys that ask for this field.
    metric_aliases: tuple[str, ...] = ()

    @property
    def is_milestone(self) -> bool:
        return self.key.startswith("milestone:")


FIELDS: tuple[ResearchField, ...] = (
    ResearchField(
        "metric:capex",
        "capital expenditure",
        (
            r"\bcapex\b",
            r"\bcap-ex\b",
            r"\bcapital\s+(?:expenditures?|costs?|spend(?:ing)?|outlays?|budget|requirements?)\b",
            r"\b(?:initial|development|pre-?production|construction|sustaining|growth|expansion)"
            r"\s+capital\b",
        ),
        needs=NEEDS_FIGURE,
        fact_labels=("capital_expenditure", "capex"),
        metric_aliases=("capex", "capital_expenditure", "capex_intensity", "capex_to_ocf"),
    ),
    ResearchField(
        "metric:production_capacity",
        "production capacity",
        (
            r"\b(?:production|nameplate|name-plate|design|processing|plant|installed|annual|"
            r"throughput|refining|manufacturing|milling|smelting|output|operating)\s+capacity\b",
            r"\bcapacity\s+(?:of|to\s+produce|to\s+process)\s+(?:up\s+to\s+|about\s+|approximately\s+)?"
            r"[\d,.]+",
            r"\b[\d,.]+\s*(?:k|m)?\s*(?:t|tonnes?|tons?|mt|kt|oz|ounces|lb|pounds|mw|gw|units)\s*"
            r"(?:per\s+(?:annum|year)|a\s+year|/\s*y(?:ea)?r|pa|tpa|p\.a\.)\s+(?:of\s+)?capacity\b",
        ),
        needs=NEEDS_FIGURE,
        metric_aliases=("capacity", "production_capacity", "capacity_overbuild"),
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
        needs=NEEDS_DATE,
    ),
    ResearchField(
        "milestone:commissioning",
        "commissioning",
        (r"\bcommission(?:ing|ed)\b", r"\bramp-?up\s+to\s+(?:nameplate|full)\b"),
        needs=NEEDS_DATE,
    ),
    ResearchField(
        "milestone:final_investment_decision",
        "final investment decision",
        (r"\bfinal\s+investment\s+decision\b", r"\bfid\b"),
        needs=NEEDS_DATE,
    ),
    ResearchField(
        "commercial:offtake",
        "offtake",
        (r"\boff-?take\b", r"\boff\s+take\b"),
    ),
    ResearchField(
        "metric:cash",
        "cash and equivalents",
        (
            r"\bcash\s+and\s+(?:cash\s+)?equivalents\b",
            r"\bcash\s+(?:balance|position|on\s+hand|reserves?|holdings?|at\s+bank)\b",
            r"\b(?:held|holds|had|has)\s[^;]{0,30}?\bin\s+cash\b",
        ),
        needs=NEEDS_FIGURE,
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
        needs=NEEDS_FIGURE,
        metric_aliases=("cash_runway", "cash_runway_quarters"),
    ),
    ResearchField(
        "metric:npv",
        "net present value",
        (r"\bnpv(?:\s*\d{1,2}(?:\.\d)?%?)?\b", r"\bnet\s+present\s+value\b"),
        needs=NEEDS_FIGURE,
    ),
    ResearchField(
        "metric:irr",
        "internal rate of return",
        (r"\birr\b", r"\binternal\s+rate\s+of\s+return\b"),
        needs=NEEDS_FIGURE,
    ),
    ResearchField(
        "metric:operating_cash_flow",
        "operating cash flow",
        (
            r"\boperating\s+cash\s*flows?\b",
            r"\bcash\s+(?:flows?\s+)?(?:from|generated\s+(?:by|from))\s+operat(?:ions|ing\s+activities)\b",
            r"\bocf\b",
        ),
        needs=NEEDS_FIGURE,
        fact_labels=("operating_cash_flow",),
        metric_aliases=("operating_cash_flow",),
    ),
    ResearchField(
        "metric:revenue",
        "revenue",
        (r"\brevenues?\b", r"\bturnover\b", r"\bnet\s+sales\b"),
        needs=NEEDS_FIGURE,
        fact_labels=("revenue",),
        metric_aliases=("revenue",),
    ),
    ResearchField(
        "metric:net_debt",
        "net debt",
        (r"\bnet\s+debt\b", r"\bnet\s+cash\b"),
        needs=NEEDS_FIGURE,
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
        needs=NEEDS_FIGURE,
    ),
)

FIELDS_BY_KEY: dict[str, ResearchField] = {f.key: f for f in FIELDS}
FIELD_KEYS: frozenset[str] = frozenset(FIELDS_BY_KEY)

_COMPILED: tuple[tuple[ResearchField, tuple[re.Pattern[str], ...]], ...] = tuple(
    (f, tuple(re.compile(p) for p in f.patterns)) for f in FIELDS
)

# ── Text helpers ────────────────────────────────────────────────────────────── #

#: A denial scopes to its CLAUSE. Same split the thesis grader uses.
CLAUSE_SPLIT_RE = re.compile(
    r"[;.]\s+|,\s+(?:but|although|though|while|whereas|yet)\s+|\s+but\s+"
)
#: Anywhere in a clause, these make it a statement of absence, not of a value.
_NEGATION_RE = re.compile(
    r"\b(?:no|not|never|none|nor|neither|without|lacks?|lacking|absent|omits?|omitted|"
    r"unavailable|undisclosed|unknown|unclear|unconfirmed|unquantified|missing|"
    r"cannot|could\s+not|did\s+not|does\s+not|yet\s+to|failed\s+to|unable\s+to)\b|n['’]t\b",
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


def clauses(text: str | None) -> list[str]:
    return [c for c in CLAUSE_SPLIT_RE.split((text or "").lower()) if c and c.strip()]


def is_negated(clause: str) -> bool:
    return _NEGATION_RE.search(clause or "") is not None


def has_figure(clause: str) -> bool:
    for match in _NUMBER_RE.finditer(clause or ""):
        token = match.group(0).replace(",", "").lstrip("-")
        if re.fullmatch(r"(?:19|20)\d{2}", token):
            continue
        return True
    return False


def has_date(clause: str) -> bool:
    return _DATE_CUE_RE.search(clause or "") is not None


def _matches(patterns: Iterable[re.Pattern[str]], text: str) -> bool:
    return any(p.search(text) for p in patterns)


# ── Classifiers ─────────────────────────────────────────────────────────────── #


def fields_mentioned(text: str | None) -> tuple[str, ...]:
    """Every field ``text`` is ABOUT, negated or not — the reading for a gap.

    A gap is a statement of absence by construction ("no capex figure was acquired"),
    so its negation is what it is; stripping it would leave it about nothing.
    """
    low = (text or "").lower().replace("_", " ")
    return tuple(f.key for f, patterns in _COMPILED if _matches(patterns, low))


def fields_stated(text: str | None) -> tuple[str, ...]:
    """Every field ``text`` AFFIRMATIVELY states a value for — the reading for a finding.

    Per clause: the clause names the field, does not negate, and carries what the field
    needs (a figure for a metric, a date for a milestone). "Capex of US$1.2bn; capacity
    is not disclosed" states capex and not capacity.
    """
    out: list[str] = []
    for clause in clauses(text):
        if is_negated(clause):
            continue
        for f, patterns in _COMPILED:
            if f.key in out or not _matches(patterns, clause):
                continue
            if f.needs == NEEDS_FIGURE and not has_figure(clause):
                continue
            if f.needs == NEEDS_DATE and not has_date(clause):
                continue
            out.append(f.key)
    return tuple(out)


def field_clause(text: str | None, field_key: str) -> str | None:
    """The first affirmative clause stating ``field_key``, or ``None``."""
    found = FIELDS_BY_KEY.get(field_key)
    if found is None:
        return None
    patterns = next(p for f, p in _COMPILED if f.key == field_key)
    for clause in clauses(text):
        if is_negated(clause) or not _matches(patterns, clause):
            continue
        if found.needs == NEEDS_FIGURE and not has_figure(clause):
            continue
        if found.needs == NEEDS_DATE and not has_date(clause):
            continue
        return clause
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


# ── Project identity ────────────────────────────────────────────────────────── #

_ASSET_NOUNS = (
    "project|projects|mine|mines|plant|refinery|facility|deposit|hub|operation|"
    "concentrator|smelter|site|field|expansion"
)
_PROJECT_RE = re.compile(
    r"\b((?:[A-Z][\w'’-]*|\d+)(?:\s+(?:[A-Z][\w'’-]*|\d+)){0,3})\s+(?i:" + _ASSET_NOUNS + r")\b"
)
_PROJECT_STOPWORDS = frozenset(
    "the this that these those its our their each a an any all new existing current "
    "company group issuer phase stage first second main proposed planned flagship "
    "production processing mining".split()
)


def project_key(text: str | None) -> str | None:
    """The project or asset a statement names ("the Foo Bar Project" → "foo bar").

    Read from capitalised words before an asset noun — a shape, never a list of names.
    ``None`` when no project is named, which matches only another ``None``.
    """
    for match in _PROJECT_RE.finditer(text or ""):
        tokens = [
            t for t in re.findall(r"[\w'’-]+", match.group(1).lower())
            if t not in _PROJECT_STOPWORDS and not t.isdigit()
        ]
        if tokens:
            return " ".join(tokens)[:120]
    return None


def projects_compatible(a: str | None, b: str | None) -> bool | None:
    """``True`` same project, ``False`` different projects, ``None`` when one is unnamed.

    Token containment, so "longonjo" and "longonjo ndpr" are one project.
    """
    if not a or not b:
        return None if (a or b) else True
    ta, tb = set(a.split()), set(b.split())
    return ta <= tb or tb <= ta


# ── Periods and dates ───────────────────────────────────────────────────────── #

_PERIOD_RE = re.compile(
    r"\b(?:fy\s?(?P<fy>(?:19|20)\d{2})|(?P<y>(?:19|20)\d{2})(?:-q(?P<q>[1-4]))?)\b",
    re.IGNORECASE,
)
_REAL_PERIOD_RE = re.compile(r"^(?:FY\d{4}|\d{4}-Q[1-4]|\d{4}-H[12]|\d{4})$", re.IGNORECASE)


def period_rank(period_key: str | None) -> tuple[int, int] | None:
    """A comparable (year, sub-period) for a period key, or ``None`` when unreadable."""
    text = str(period_key or "").strip()
    if not text:
        return None
    match = _PERIOD_RE.search(text)
    if match is None:
        return None
    if match.group("fy"):
        return int(match.group("fy")), 9
    year = int(match.group("y"))
    quarter = match.group("q")
    return year, int(quarter) if quarter else 9


def is_financial_period(period_key: str | None) -> bool:
    """A REAL reporting period — FY2025, 2025-Q3 — as opposed to no period at all."""
    return bool(period_key) and _REAL_PERIOD_RE.match(str(period_key).strip()) is not None


def requested_period(text: str | None) -> str | None:
    """A fiscal period a gap names explicitly ("FY2025", "2025-Q2"), else ``None``."""
    match = re.search(r"\bFY\s?((?:19|20)\d{2})\b|\b((?:19|20)\d{2})-Q([1-4])\b", text or "",
                      re.IGNORECASE)
    if match is None:
        return None
    if match.group(1):
        return f"FY{match.group(1)}"
    return f"{match.group(2)}-Q{match.group(3)}"


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


# ── The persisted claim key ─────────────────────────────────────────────────── #

_CLAIM_KEY_MAX = 240


def claim_key_for(statement: str | None) -> str | None:
    """``research_findings.claim_key``: the fields a finding states, and its project.

    ``"metric:capex+metric:production_capacity@foo bar"`` — sorted fields, then the
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


def document_kinds_mentioned(text: str | None) -> tuple[str, ...]:
    return tuple(kind for kind, pattern in _DOC_PATTERNS.items() if pattern.search(text or ""))


__all__ = [
    "DOC_ANNUAL",
    "DOC_INTERIM",
    "DOC_RESULTS",
    "FIELDS",
    "FIELDS_BY_KEY",
    "FIELD_KEYS",
    "ResearchField",
    "claim_key_for",
    "clauses",
    "document_kinds_mentioned",
    "field_clause",
    "field_for_fact_label",
    "fields_for_metrics",
    "fields_mentioned",
    "fields_stated",
    "has_date",
    "has_figure",
    "is_financial_period",
    "is_known_field",
    "is_negated",
    "label_of",
    "parse_claim_key",
    "parse_date",
    "period_rank",
    "project_key",
    "projects_compatible",
    "requested_period",
    "years_in",
]
