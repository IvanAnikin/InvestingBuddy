"""Final report reconciliation — gap closure and temporal supersession.

THE DEFECT
==========
Five producers state what a run does not know, and none of them reads the findings: the
ledger gap a specialist wrote under one question, the professional report's platform
evidence gaps, the Chair's GAPS block, and the V2 ``missing_information`` list and
council concerns (assembled before V3 ran at all). So a report could print "no capital
expenditure was acquired" beside a finding that states the capex. And two findings about
the same milestone of the same project — "first production in 2028" from last year's
announcement and "first production in 2027" from this quarter's — were shown side by
side as if both were current.

THE RULE THAT GOVERNS EVERYTHING BELOW
======================================
> Reconciliation may never HIDE a genuinely open gap or a business risk, and may never
> retire a historical fact as "prior guidance". When unsure: ``partially_closed`` or
> ``still_open``, and the finding is named beside the gap rather than instead of it.

WHAT IT DOES
============
Runs once, after the Red Team and before the Chair and the report are assembled:

1. **Supersession** — ONLY for guidance and estimate fields (a project's capital cost
   estimate, planned capacity, milestone targets, NPV, IRR, a resource estimate). Two
   issuer/official statements of the same field for the same project (stage included),
   scope and period, with different values, are ordered by the publication date of
   their evidence; the newest is current and each older one is kept, marked superseded.
   A third-party source, a currency difference, unknown or equal dates, or two values
   for one reporting period are recorded as DISAGREEMENTS — never a silent pick.
   Revenue, cash at a date, historical spend and every other period-bound metric are
   history and are never superseded.
2. **Gap reconciliation** — each gap becomes exactly one of

   * ``closed``: an ABSENCE gap (evidence unavailable, source unreachable, tool
     unavailable, period missing, transcript unavailable) whose own text names fields
     that non-withdrawn, affirmative, issuer/official findings state — same capex
     sub-type, same project (stage included), compatible scope, the SAME period when the
     gap names one, and a newer source when the gap asks for a current value. Persisted
     through ``ledger.close_gap``;
   * ``partially_closed``: a finding speaks to it, with a named reason (another period,
     another sub-type, a segment figure, one side naming a project, third-party only, a
     field inferred from the question, a gap type that is not about absence, a validated
     fact, recency unconfirmed);
   * ``superseded``: a source-unreachable / tool-unavailable gap recording that a
     document could not be FETCHED, whose document the run now holds, and whose text
     names no field still unanswered;
   * ``still_open``: everything else, including every gap whose field is unknown.
3. **V2 labels** — V2 ``missing_information`` items that name a field, and council
   concerns whose gap cue and field sit in one clause and that carry no risk vocabulary.
   A V2 concern is never removed from the page: at most it is annotated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.services import research_fields as rf
from app.services.ledger import store as ledger

#: Source kinds that speak for the issuer or an official authority.
PRIMARY_SOURCE_KINDS: frozenset[str] = frozenset(
    {"issuer_filing", "issuer_ir", "government_or_regulator", "platform_calculation"}
)
#: The issuer's own documents — what "N from issuer documents" counts.
ISSUER_SOURCE_KINDS: frozenset[str] = frozenset({"issuer_filing", "issuer_ir"})

#: Gap types that say something is ABSENT. Only these can be closed by a finding: a
#: conflict, an unknown scope or an ambiguous entity is not answered by one more figure.
CLOSABLE_GAP_TYPES: frozenset[str] = frozenset(
    {
        ledger.GAP_EVIDENCE_UNAVAILABLE,
        ledger.GAP_SOURCE_UNREACHABLE,
        ledger.GAP_TOOL_UNAVAILABLE,
        ledger.GAP_PERIOD_MISSING,
        ledger.GAP_TRANSCRIPT_UNAVAILABLE,
    }
)
#: Gap types that can record a failed FETCH — the only ones a held document supersedes.
DOCUMENT_GAP_TYPES: frozenset[str] = frozenset(
    {ledger.GAP_SOURCE_UNREACHABLE, ledger.GAP_TOOL_UNAVAILABLE}
)
#: Kept for callers of the first version; equals the closable absence types.
ACQUISITION_GAP_TYPES: frozenset[str] = CLOSABLE_GAP_TYPES

_READY_STATES = frozenset({"ready", "acquired", "reused"})
_GROUP_WORDS_RE = re.compile(r"\b(?:group|consolidated|company-wide)\b", re.IGNORECASE)
_SEGMENT_RE = re.compile(
    r"\b(?P<name>(?:[a-z][\w&-]*\s+){0,3}?)(?:segment|division|business\s+unit)s?\b",
    re.IGNORECASE,
)
_SCOPE_STOPWORDS = frozenset(
    "the a an by each per of for its their our and or split revenue revenues capex cash "
    "margin margins earnings ebitda profit sales operating".split()
)

REASON_FIELD_UNKNOWN = "field_unknown"
REASON_NO_FINDING = "no_finding_states_the_field"
REASON_OLDER_PERIOD = "older_period"
REASON_OTHER_PERIOD = "other_period"
REASON_PERIOD_UNKNOWN = "period_unknown"
REASON_SCOPE_DIFFERS = "scope_differs"
REASON_PROJECT_UNNAMED = "project_unnamed"
REASON_PROJECT_STAGE_DIFFERS = "project_stage_differs"
REASON_SUBTYPE_DIFFERS = "field_subtype_differs"
REASON_THIRD_PARTY_ONLY = "third_party_only"
REASON_FIELD_INFERRED = "field_inferred_from_question"
REASON_GAP_TYPE_NOT_ABSENCE = "gap_type_not_absence"
REASON_RECENCY_UNCONFIRMED = "recency_unconfirmed"
REASON_DOCUMENT_ACQUIRED = "document_acquired"
REASON_FACT_VALIDATED = "validated_fact_acquired"
REASON_ALREADY_CLOSED = "already_closed"
REASON_QUALIFIER_DIFFERS = "value_qualifier_differs"
REASON_HISTORICAL_CITATION = "historical_citation"
REASON_TARGET_PASSED = "target_date_passed"
REASON_PERIOD_UNSPECIFIED = "period_unspecified"
REASON_VALUE_STALE = "value_stale"
REASON_VALUE_DATE_UNKNOWN = "value_date_unknown"

#: Point-in-time balances: a cash or debt figure answers "what is the balance" only when
#: it is recent, whether or not the gap says "current".
POINT_IN_TIME_FIELDS: frozenset[str] = frozenset({"metric:cash", "metric:net_debt"})

#: How recent evidence must be to answer a gap asking for the CURRENT value.
RECENCY_WINDOW_DAYS = 365
#: Relative difference within which a scale-rounded amount is the same amount.
ROUNDED_SAME_TOLERANCE = 0.05
#: Period-bound metrics: without a period a V2 item about one is never hidden.
PERIOD_BOUND_FIELDS: frozenset[str] = frozenset(
    {"metric:revenue", "metric:operating_cash_flow", "metric:cash", "metric:net_debt",
     "metric:capex", "metric:capex_period", "metric:capex_sustaining"}
)

MAX_CLOSING_FINDINGS = 200
MAX_DISAGREEMENTS_PER_GROUP = 4
MAX_DISAGREEMENTS = 20


# ── Inputs ──────────────────────────────────────────────────────────────────── #


@dataclass(frozen=True)
class FindingFacts:
    """A finding as reconciliation reads it."""

    finding_id: str
    statement: str
    fields: tuple[str, ...]
    project: str | None = None
    question_key: str | None = None
    scope_key: str | None = None
    #: The evidence's period key (normalised when it came from a row).
    period_key: str | None = None
    source_kinds: tuple[str, ...] = ()
    published_at: date | None = None
    withdrawn: bool = False
    superseded_by: str | None = None
    #: The reporting period the statement's own words name ("revenue for FY2024").
    stated_period: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "FindingFacts":
        statement = str(getattr(row, "statement", "") or "")
        fields, project = finding_fields(statement, getattr(row, "claim_key", None))
        superseded = getattr(row, "superseded_by_finding_id", None)
        return cls(
            finding_id=str(row.id),
            statement=statement,
            fields=fields,
            project=project,
            question_key=getattr(row, "question_key", None),
            scope_key=getattr(row, "scope_key", None),
            period_key=rf.normalise_period(getattr(row, "period_key", None)),
            source_kinds=tuple(getattr(row, "source_kinds_json", None) or ()),
            published_at=getattr(row, "source_published_at", None),
            withdrawn=getattr(row, "verification_status", None) == "withdrawn",
            superseded_by=str(superseded) if superseded else None,
            stated_period=rf.statement_period(statement),
        )

    @property
    def is_primary(self) -> bool:
        return bool(set(self.source_kinds) & PRIMARY_SOURCE_KINDS)

    @property
    def effective_period(self) -> str | None:
        return rf.normalise_period(self.period_key) or self.stated_period

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "fields": list(self.fields),
            "project": self.project,
            "scope_key": self.scope_key,
            "period_key": self.effective_period,
            "source_kinds": list(self.source_kinds),
            "source_published_at": self.published_at.isoformat() if self.published_at else None,
            "superseded_by_finding_id": self.superseded_by,
            # Bounded; lets a project named without an asset noun still be matched.
            "statement": self.statement[:300],
        }


@dataclass(frozen=True)
class GapFacts:
    gap_id: str
    gap_type: str
    description: str
    question_key: str | None = None
    status: str = ledger.GAP_OPEN
    closed_by_finding_id: str | None = None


@dataclass(frozen=True)
class FactFacts:
    """An active, validated extracted fact."""

    fact_id: str
    label: str
    field_key: str
    period: str | None = None
    scope_type: str | None = None


@dataclass(frozen=True)
class DocumentFacts:
    """A document the run holds in its corpus."""

    kind: str
    ref: str | None = None
    published: str | None = None


def finding_fields(
    statement: str, claim_key: str | None = None
) -> tuple[tuple[str, ...], str | None]:
    """``(fields, project)``: from the persisted claim key when it parses, else the text."""
    parsed = rf.parse_claim_key(claim_key)
    if parsed is not None:
        return parsed
    return tuple(sorted(rf.fields_stated(statement))), rf.project_key(statement)


# ── Output ──────────────────────────────────────────────────────────────────── #


@dataclass
class GapVerdict:
    gap_id: str
    status: str
    fields: tuple[str, ...] = ()
    field_source: str | None = None
    closed_by_finding_id: str | None = None
    #: Findings that address it (fully or partly), best first.
    finding_ids: list[str] = field(default_factory=list)
    reasons: list[str] = field(default_factory=list)
    fact: dict[str, Any] | None = None
    documents: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "status": self.status,
            "fields": list(self.fields),
            "field_labels": [rf.label_of(f) for f in self.fields],
            "field_source": self.field_source,
            "closed_by_finding_id": self.closed_by_finding_id,
            "finding_ids": list(self.finding_ids),
            "reasons": list(self.reasons),
            "fact": self.fact,
            "documents": list(self.documents),
        }


@dataclass(frozen=True)
class Supersession:
    older_id: str
    newer_id: str
    field_key: str
    older_published_at: date
    newer_published_at: date

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.older_id,
            "superseded_by_finding_id": self.newer_id,
            "field": self.field_key,
            "field_label": rf.label_of(self.field_key),
            "source_published_at": self.older_published_at.isoformat(),
            "superseded_on": self.newer_published_at.isoformat(),
        }


@dataclass(frozen=True)
class TemporalDisagreement:
    finding_a_id: str
    finding_b_id: str
    field_key: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_a_id": self.finding_a_id,
            "finding_b_id": self.finding_b_id,
            "field": self.field_key,
            "reason": self.reason,
        }


# ── 19. Temporal supersession ──────────────────────────────────────────────── #

SAME = "same"
DIFFERENT = "different"
INCOMPARABLE = "incomparable"

_PERCENT_RE = re.compile(r"(\d[\d.]*)\s*%")


@dataclass(frozen=True)
class _Value:
    """What a clause says a field IS, in a comparable form."""

    kind: str
    items: tuple[Any, ...]


def _value(field_key: str, clause: str, published: date | None) -> _Value | None:
    from app.services.director.ownership import figures_in

    family = rf.family_of(field_key)
    if field_key.startswith("milestone:"):
        targets = {
            t for t in rf.target_years(clause)
            # A "target" before the statement was published is history, not a target.
            if published is None or int(t[-4:]) >= published.year
        }
        return _Value("date", tuple(sorted(targets))) if targets else None
    if family == rf.CAPEX_FAMILY or field_key == "metric:npv":
        money = rf.money_values(clause)
        return _Value("money", tuple(sorted(money))) if money else None
    if field_key == "metric:irr":
        rates = tuple(sorted({float(p) for p in _PERCENT_RE.findall(clause)}))
        return _Value("percent", rates) if rates else None
    if field_key == "metric:production_capacity":
        found = rf._CAPACITY_RE.findall(clause)  # noqa: SLF001 - same module family
        norm = tuple(sorted({re.sub(r"[\s,]", "", f.lower()) for f in found}))
        return _Value("capacity", norm) if norm else None
    figures = figures_in(clause)
    return _Value("figures", tuple(sorted(figures))) if figures else None


def compare_values(a: _Value, b: _Value) -> str:
    """``same`` | ``different`` | ``incomparable`` (e.g. two currencies)."""
    if a.kind != b.kind:
        return INCOMPARABLE
    if a.kind == "money":
        if {c for c, _v, _t in a.items} != {c for c, _v, _t in b.items}:
            return INCOMPARABLE
        if len(a.items) != len(b.items):
            return DIFFERENT
        for (_c1, v1, t1), (_c2, v2, t2) in zip(a.items, b.items, strict=True):
            gap = abs(v1 - v2)
            if gap <= 1e-9 * max(abs(v1), abs(v2), 1.0):
                continue
            # A scale-rounded form ("US$0.3bn") matches a precise one only within its
            # own rounding AND within 5%: US$0.3bn is US$302m, never US$340m.
            if gap <= max(t1, t2) and gap <= ROUNDED_SAME_TOLERANCE * max(abs(v1), abs(v2)):
                continue
            return DIFFERENT
        return SAME
    if a.kind == "date":
        # "2026" and "h2 2026" are one target stated at two precisions — a refinement,
        # not a change of guidance.
        years_a = {t[-4:] for t in a.items}
        years_b = {t[-4:] for t in b.items}
        if years_a == years_b and (
            all(len(t) == 4 for t in a.items) or all(len(t) == 4 for t in b.items)
        ):
            return SAME
    return SAME if a.items == b.items else DIFFERENT


def _single_field_clause(finding: FindingFacts, field_key: str) -> str | None:
    """The clause stating ``field_key`` — only when it names no other field family and
    does not QUOTE an earlier document ("the 2022 PFS estimated …")."""
    clause = rf.field_clause(finding.statement, field_key)
    if clause is None or rf.is_historical_citation(clause):
        return None
    families = {rf.family_of(f) for f in rf.fields_mentioned(clause)}
    if families != {rf.family_of(field_key)}:
        return None
    return clause


def supersede(
    findings: Sequence[FindingFacts],
) -> tuple[list[Supersession], list[TemporalDisagreement]]:
    """Order same-field GUIDANCE in time. Pure: returns what to persist."""
    groups: dict[tuple[str, str, str, str, str], list[tuple[FindingFacts, _Value]]] = {}
    for finding in findings:
        if finding.withdrawn:
            continue
        for field_key in finding.fields:
            if field_key not in rf.SUPERSEDABLE_FIELDS:
                continue  # history is never "prior guidance"
            clause = _single_field_clause(finding, field_key)
            if clause is None:
                continue
            value = _value(field_key, clause, finding.published_at)
            if value is None:
                continue
            # The stored period, else the period its words name — never a milestone's
            # own target date.
            period = rf.normalise_period(finding.period_key) or (
                None if field_key.startswith("milestone:") else finding.stated_period
            )
            key = (
                field_key,
                (finding.scope_key or "").casefold(),
                finding.project or "",  # EXACT: a stage is its own project
                period or "",
                # Tax basis, discount rate, resource category, product, pilot vs
                # commercial, stage, scenario: any difference means two quantities.
                "|".join(sorted(rf.value_qualifiers(clause, field_key))),
            )
            groups.setdefault(key, []).append((finding, value))

    supersessions: list[Supersession] = []
    disagreements: list[TemporalDisagreement] = []
    for (field_key, _scope, _project, period, _qualifiers), members in groups.items():
        if len(members) < 2:
            continue
        if all(compare_values(members[0][1], v) == SAME for _f, v in members[1:]):
            continue  # one statement, restated
        if rf.is_financial_period(period):
            _capped_pairs(members, field_key, "same_reporting_period", disagreements)
            continue
        _resolve_group(members, field_key, supersessions, disagreements)
    return supersessions, disagreements[:MAX_DISAGREEMENTS]


def _resolve_group(
    members: Sequence[tuple[FindingFacts, _Value]],
    field_key: str,
    supersessions: list[Supersession],
    disagreements: list[TemporalDisagreement],
) -> None:
    primary_dated = [(f, v) for f, v in members if f.is_primary and f.published_at]
    newest = max((f.published_at for f, _v in primary_dated if f.published_at), default=None)
    heads = [(f, v) for f, v in primary_dated if f.published_at == newest]
    if newest is None or any(compare_values(heads[0][1], v) != SAME for _f, v in heads[1:]):
        why = (
            "no_issuer_source" if not any(f.is_primary for f, _v in members)
            else "source_dates_unknown_or_equal"
        )
        _capped_pairs(members, field_key, why, disagreements)
        return
    current, current_value = heads[0]
    raised = 0
    for finding, value in members:
        if finding.finding_id == current.finding_id:
            continue
        verdict = compare_values(current_value, value)
        if verdict == SAME:
            continue
        reason: str | None = None
        if verdict == INCOMPARABLE:
            reason = "values_not_comparable"  # e.g. another currency
        elif not finding.is_primary:
            reason = "non_issuer_source"
        elif finding.published_at is None:
            reason = "source_date_unknown"
        if reason is not None:
            if raised < MAX_DISAGREEMENTS_PER_GROUP:
                disagreements.append(TemporalDisagreement(
                    current.finding_id, finding.finding_id, field_key, reason))
                raised += 1
            continue
        assert finding.published_at is not None
        supersessions.append(Supersession(
            older_id=finding.finding_id, newer_id=current.finding_id, field_key=field_key,
            older_published_at=finding.published_at, newer_published_at=newest,
        ))


def _capped_pairs(
    members: Sequence[tuple[FindingFacts, _Value]],
    field_key: str,
    reason: str,
    out: list[TemporalDisagreement],
) -> None:
    raised = 0
    for i, (a, va) in enumerate(members):
        for b, vb in members[i + 1:]:
            if raised >= MAX_DISAGREEMENTS_PER_GROUP:
                return
            if a.finding_id != b.finding_id and compare_values(va, vb) != SAME:
                out.append(TemporalDisagreement(a.finding_id, b.finding_id, field_key, reason))
                raised += 1


# ── 18. Gap reconciliation ──────────────────────────────────────────────────── #


def _is_group_scope(scope_key: str | None) -> bool:
    from app.services.sources.fact_scope import is_group_label

    return bool(scope_key) and (scope_key == "group" or is_group_label(scope_key))


def gap_scope(text: str | None) -> str | None:
    """``segment:<name>`` | ``segment`` | ``group`` | ``None`` — what the gap is scoped to."""
    low = (text or "").lower()
    match = _SEGMENT_RE.search(low)
    if match:
        tokens = [t for t in re.findall(r"[\w&-]+", match.group("name") or "")
                  if t not in _SCOPE_STOPWORDS]
        return f"segment:{' '.join(tokens)}" if tokens else "segment"
    if _GROUP_WORDS_RE.search(low):
        return "group"
    return None


def requirements(fields: Sequence[str]) -> tuple[str, ...]:
    """What a gap asks for: a capex sub-type when it names one, else the family."""
    named = set(fields)
    out: list[str] = []
    for key in fields:
        found = rf.FIELDS_BY_KEY.get(key)
        if found is None:
            continue
        if found.family is None and any(
            rf.FIELDS_BY_KEY[s].family == key for s in named if s in rf.FIELDS_BY_KEY
        ):
            continue  # the sub-type below is the requirement
        if key not in out:
            out.append(key)
    return tuple(out)


def _field_match(finding: FindingFacts, requirement: str) -> list[str] | None:
    if requirement in finding.fields:
        return []
    if (
        requirement == "metric:capex_period"
        and rf.CAPEX_FAMILY in finding.fields
        and not any(sub in finding.fields for sub in rf.CAPEX_SUBTYPES)
        and rf.is_financial_period(finding.effective_period)
    ):
        return []  # capex the evidence dates to a reporting period IS period capex
    family = rf.family_of(requirement)
    if family != requirement and family in finding.fields:
        return [REASON_SUBTYPE_DIFFERS]  # a capex figure, not the capex asked for
    return None


def _family_subtype_reasons(finding: FindingFacts, requirement: str) -> list[str]:
    """A gap asking for capex in general, answered by money SPENT in a period or by
    sustaining capital: related, not the same — shown, never closed."""
    if requirement != rf.CAPEX_FAMILY:
        return []
    if any(sub in finding.fields for sub in ("metric:capex_period", "metric:capex_sustaining")):
        return [REASON_SUBTYPE_DIFFERS]
    if "metric:capex_project" not in finding.fields and rf.is_financial_period(
        finding.effective_period
    ):
        return [REASON_SUBTYPE_DIFFERS]
    return []


def _scope_reasons(finding: FindingFacts, scope: str | None) -> list[str] | None:
    finding_segment = bool(finding.scope_key) and not _is_group_scope(finding.scope_key)
    if scope and scope.startswith("segment"):
        if not finding_segment:
            return None  # a group figure never answers a segment question
        wanted = scope.partition(":")[2]
        have = (finding.scope_key or "").partition(":")[2].casefold()
        if not wanted or not have:
            return [REASON_SCOPE_DIFFERS]
        return [] if (set(wanted.split()) <= set(have.split())) else None
    return [REASON_SCOPE_DIFFERS] if finding_segment else []


def _project_reasons(finding: FindingFacts, gap_project: str | None) -> list[str] | None:
    if gap_project is None:
        return [REASON_PROJECT_UNNAMED] if finding.project else []
    if finding.project is None:
        words = set(re.findall(r"[\w'’-]+", finding.statement.lower()))
        return [] if words and set(gap_project.split()) <= words else [REASON_PROJECT_UNNAMED]
    if finding.project == gap_project:
        return []
    return [REASON_PROJECT_STAGE_DIFFERS] if rf.projects_compatible(
        gap_project, finding.project) else None


def _period_reasons(finding: FindingFacts, gap_period: str | None) -> list[str]:
    if not gap_period:
        return []
    have = finding.effective_period
    if have is None:
        return [REASON_PERIOD_UNKNOWN]
    if have == gap_period:
        return []
    wanted_rank, have_rank = rf.period_rank(gap_period), rf.period_rank(have)
    if wanted_rank and have_rank and have_rank < wanted_rank:
        return [REASON_OLDER_PERIOD]
    return [REASON_OTHER_PERIOD]


def _recency_reasons(
    finding: FindingFacts, gap_text: str, requirement: str, as_of: date
) -> list[str]:
    clause = rf.field_clause(finding.statement, requirement) if finding.statement else None
    historical = clause is not None and rf.is_historical_citation(clause)
    if not rf.RECENCY_RE.search(gap_text or ""):
        # Not asking for the current value; a quoted old study still only PARTLY answers.
        return [REASON_HISTORICAL_CITATION] if historical else []
    if finding.published_at is None or historical:
        return [REASON_RECENCY_UNCONFIRMED]
    years = rf.years_in(gap_text)
    if years and finding.published_at.year <= max(years):
        return [REASON_RECENCY_UNCONFIRMED]
    if (as_of - finding.published_at).days > RECENCY_WINDOW_DAYS:
        return [REASON_RECENCY_UNCONFIRMED]
    ended = rf.period_end_date(finding.effective_period)
    if ended is not None and (as_of - ended).days > RECENCY_WINDOW_DAYS:
        return [REASON_RECENCY_UNCONFIRMED]  # "current cash" answered by a 2022 balance
    return []


def _balance_reasons(finding: FindingFacts, requirement: str, as_of: date) -> list[str]:
    """A balance (cash, net debt) closes only when dated within a year of the run."""
    if requirement not in POINT_IN_TIME_FIELDS:
        return []
    period = finding.effective_period
    ended = rf.period_end_date(period)
    if ended is None:
        rank = rf.period_rank(period)
        if rank is None:
            return [REASON_VALUE_DATE_UNKNOWN]
        # A year-labelled balance ends at the latest on 31 December of that year.
        ended = date(rank[0], 12, 31)
    return [REASON_VALUE_STALE] if (as_of - ended).days > RECENCY_WINDOW_DAYS else []


def _milestone_reasons(
    finding: FindingFacts, requirement: str, as_of: date
) -> list[str] | None:
    """A milestone answers only with a live TARGET: not one that predates its own
    source, and — shown, never closed — not one whose date has already passed."""
    if not requirement.startswith("milestone:"):
        return []
    clause = rf.field_clause(finding.statement, requirement) if finding.statement else None
    if clause is None:
        return [] if not finding.statement else None
    years = [int(t[-4:]) for t in rf.target_years(clause)]
    if finding.published_at is not None:
        years = [y for y in years if y >= finding.published_at.year]
    if not years:
        return None
    return [REASON_TARGET_PASSED] if max(years) < as_of.year else []


def _qualifier_reasons(finding: FindingFacts, requirement: str, gap_text: str) -> list[str]:
    wanted = {q for q in rf.value_qualifiers(gap_text, requirement)
              if q.split(":", 1)[0] in {"tax", "disc", "cat", "class", "plant", "case", "product"}}
    if not wanted:
        return []
    clause = rf.field_clause(finding.statement, requirement) if finding.statement else None
    have = rf.value_qualifiers(clause or "", requirement)
    return [] if wanted <= have else [REASON_QUALIFIER_DIFFERS]


def _assess(
    finding: FindingFacts,
    requirement: str,
    *,
    gap_text: str,
    gap_project: str | None,
    gap_period: str | None,
    scope: str | None,
    as_of: date | None = None,
) -> list[str] | None:
    """``[]`` when the finding fully answers, reasons when partly, ``None`` when not."""
    today = as_of or date.today()
    reasons: list[str] = []
    for part in (
        _field_match(finding, requirement),
        _project_reasons(finding, gap_project),
        _scope_reasons(finding, scope),
        _milestone_reasons(finding, requirement, today),
    ):
        if part is None:
            return None
        reasons.extend(part)
    reasons.extend(_family_subtype_reasons(finding, requirement))
    reasons.extend(_balance_reasons(finding, requirement, today))
    reasons.extend(_period_reasons(finding, gap_period))
    reasons.extend(_recency_reasons(finding, gap_text, requirement, today))
    reasons.extend(_qualifier_reasons(finding, requirement, gap_text))
    if not finding.is_primary:
        reasons.append(REASON_THIRD_PARTY_ONLY)
    return reasons


def _fact_covers(
    fact: FactFacts, requirement: str, *, gap_project: str | None, gap_period: str | None,
    scope: str | None,
) -> bool:
    """A validated statement fact: a GROUP figure for a reporting period. It never
    answers a project's estimate, a segment, or another period."""
    fact_fields = {fact.field_key}
    if fact.field_key == rf.CAPEX_FAMILY:
        fact_fields.add("metric:capex_period")
    if requirement not in fact_fields or gap_project:
        return False
    if fact.scope_type != "group" or (scope and scope.startswith("segment")):
        return False
    if gap_period and rf.normalise_period(fact.period) != gap_period:
        return False
    return True


def reconcile(
    gaps: Sequence[GapFacts],
    findings: Sequence[FindingFacts],
    *,
    facts: Sequence[FactFacts] = (),
    documents: Sequence[DocumentFacts] = (),
    question_fields: Mapping[str, Sequence[str]] | None = None,
    as_of: date | None = None,
) -> list[GapVerdict]:
    """One verdict per gap. Pure: the caller persists. ``as_of`` is the run date that
    "current" and "has this target passed" are judged against."""
    live = [f for f in findings if not f.withdrawn]
    # Current guidance before prior guidance, primary sources before third parties.
    live.sort(key=lambda f: (f.superseded_by is not None, not f.is_primary))
    ready_docs: dict[str, DocumentFacts] = {}
    for doc in documents:
        ready_docs.setdefault(doc.kind, doc)
    by_question = dict(question_fields or {})
    today = as_of or date.today()
    return [_reconcile_one(gap, live, facts, ready_docs, by_question, today) for gap in gaps]


def _reconcile_one(
    gap: GapFacts,
    findings: Sequence[FindingFacts],
    facts: Sequence[FactFacts],
    ready_docs: Mapping[str, DocumentFacts],
    question_fields: Mapping[str, Sequence[str]],
    as_of: date | None = None,
) -> GapVerdict:
    if gap.status == ledger.GAP_CLOSED:
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_CLOSED,
            closed_by_finding_id=gap.closed_by_finding_id,
            finding_ids=[gap.closed_by_finding_id] if gap.closed_by_finding_id else [],
            reasons=[REASON_ALREADY_CLOSED],
        )
    text = gap.description or ""
    text_fields = rf.fields_mentioned(text)
    fields, source = text_fields, ("gap_text" if text_fields else None)
    if not fields and gap.question_key and question_fields.get(gap.question_key):
        fields, source = tuple(question_fields[gap.question_key]), "question"

    # SUPERSEDED by a document: only a failed FETCH, only once the document is held, and
    # only when the gap's own words name no field that may still be unanswered.
    kinds = rf.document_kinds_mentioned(text)
    if (
        kinds and not text_fields
        and gap.gap_type in DOCUMENT_GAP_TYPES
        and rf.is_acquisition_failure(text)
        and all(kind in ready_docs for kind in kinds)
    ):
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_SUPERSEDED, fields=fields, field_source=source,
            reasons=[REASON_DOCUMENT_ACQUIRED],
            documents=[
                {"kind": k, "ref": ready_docs[k].ref, "published": ready_docs[k].published}
                for k in kinds
            ],
        )
    wanted = requirements(fields)
    if not wanted:
        return GapVerdict(gap.gap_id, ledger.RECONCILED_STILL_OPEN,
                          reasons=[REASON_FIELD_UNKNOWN])

    gap_project = rf.project_key(text)
    gap_period = rf.requested_period(text)
    scope = gap_scope(text)
    full: dict[str, str] = {}
    partial: dict[str, tuple[str, list[str]]] = {}
    for requirement in wanted:
        for finding in findings:
            reasons = _assess(finding, requirement, gap_text=text, gap_project=gap_project,
                              gap_period=gap_period, scope=scope, as_of=as_of)
            if reasons is None:
                continue
            if not reasons:
                full[requirement] = finding.finding_id
                break
            partial.setdefault(requirement, (finding.finding_id, reasons))
    covering_facts = {
        req: fact for req in wanted for fact in facts
        if _fact_covers(fact, req, gap_project=gap_project, gap_period=gap_period, scope=scope)
    }
    ordered_ids = list(dict.fromkeys(
        [full[r] for r in wanted if r in full]
        + [partial[r][0] for r in wanted if r in partial and r not in full]
    ))
    all_full = len(full) == len(wanted)
    if all_full and source == "gap_text" and gap.gap_type in CLOSABLE_GAP_TYPES:
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_CLOSED, fields=fields, field_source=source,
            closed_by_finding_id=full[wanted[0]], finding_ids=ordered_ids,
        )
    if full or partial or covering_facts:
        reasons = sorted({r for req in wanted if req in partial and req not in full
                          for r in partial[req][1]})
        if source != "gap_text":
            reasons.insert(0, REASON_FIELD_INFERRED)
        if gap.gap_type not in CLOSABLE_GAP_TYPES:
            reasons.insert(0, REASON_GAP_TYPE_NOT_ABSENCE)
        fact = next(iter(covering_facts.values()), None)
        if fact is not None:
            reasons.append(REASON_FACT_VALIDATED)
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_PARTIALLY_CLOSED, fields=fields,
            field_source=source, finding_ids=ordered_ids, reasons=reasons,
            fact=({"fact_id": fact.fact_id, "label": fact.label, "period": fact.period}
                  if fact is not None else None),
        )
    return GapVerdict(gap.gap_id, ledger.RECONCILED_STILL_OPEN, fields=fields,
                      field_source=source, reasons=[REASON_NO_FINDING])


# ── V2 items: missing_information and council concerns ─────────────────────── #

#: A concern is a GAP only when a clause says something is missing …
_GAP_SHAPED_RE = re.compile(
    r"\b(?:not\s+(?:disclosed|provided|available|reported|stated|found|acquired|quantified|"
    r"confirmed)|undisclosed|unavailable|missing|no\s+(?:data|disclosure|figure|information|"
    r"evidence|detail)|lack\s+of|unknown|absence\s+of)\b",
    re.IGNORECASE,
)
#: … and never when it is about a RISK. "Capex overruns could strain funding given the
#: lack of committed debt financing" is a business risk whatever a finding says of capex.
_RISK_RE = re.compile(
    r"\b(?:risks?|risky|overruns?|could|may|might|inflation|exposure|exposed|pressures?|"
    r"uncertain|uncertainty|volatil\w*|strain|erode|threat\w*|concern\w*|dilution|"
    r"shortfall|delay\w*|slippage|escalat\w*)\b",
    re.IGNORECASE,
)


def normalise_item_text(text: str | None) -> str:
    """Lower-case, whitespace collapsed, quotes unified, trailing punctuation dropped —
    the same normalisation the web applies before matching a label to a concern."""
    value = re.sub(r"[‘’]", "'", str(text or ""))
    value = re.sub(r"[“”]", '"', value)
    value = re.sub(r"\s+", " ", value).strip().lower()
    return value.rstrip(" .;:!?")


#: Words that make an item a DERIVED metric (a growth rate, a ratio, a margin): a
#: finding stating the underlying figure does not answer it.
_DERIVED_RE = re.compile(
    r"\b(?:growth|ratio|ratios|margin|margins|yield|intensity|conversion|cagr|change|"
    r"coverage|leverage|multiple|per\s+share|to|vs|versus|pct|percent|percentage|"
    r"return|returns|payout|turnover\s+ratio)\b",
    re.IGNORECASE,
)
_UNIT_SUFFIX_RE = re.compile(r"(?:\s+(?:mln|mn|m|bn|usd|eur|gbp|aud|local|reported))+$")


def _exact_field_names() -> dict[str, str]:
    names: dict[str, str] = {}
    for f in rf.FIELDS:
        for alias in (f.key.split(":", 1)[1], f.label, *f.metric_aliases, *f.fact_labels):
            names.setdefault(alias.replace("_", " ").strip().lower(), f.key)
    return names


_EXACT_NAMES = _exact_field_names()


def exact_field_for_item(name: str) -> str | None:
    """The field a V2 missing-information NAME is, exactly, or ``None``.

    ``fundamentals.capital_expenditure`` → capex; ``revenue_growth``,
    ``net_debt_to_ebitda``, ``gross_margin`` → ``None``: a derived metric is not the
    figure it is derived from.
    """
    tail = str(name or "").strip().lower().rsplit(".", 1)[-1].replace("_", " ")
    tail = _UNIT_SUFFIX_RE.sub("", re.sub(r"\s+", " ", tail)).strip()
    if not tail or _DERIVED_RE.search(tail):
        return None
    return _EXACT_NAMES.get(tail)


def _label_item(
    text: str, closers: Sequence[FindingFacts], as_of: date | None = None
) -> dict[str, Any] | None:
    fields = rf.fields_mentioned(text)
    if not fields:
        return None
    gap = GapFacts(gap_id="", gap_type=ledger.GAP_EVIDENCE_UNAVAILABLE, description=text)
    verdict = _reconcile_one(gap, closers, (), {}, {}, as_of)
    return {
        "status": verdict.status,
        "fields": list(verdict.fields),
        "field_labels": [rf.label_of(f) for f in verdict.fields],
        "finding_ids": list(verdict.finding_ids),
        "reasons": list(verdict.reasons),
    }


def _gap_clause(text: str) -> str | None:
    """The one clause holding BOTH a gap cue and a field, when the concern is no risk."""
    if _RISK_RE.search(text) or _DERIVED_RE.search(text):
        return None
    # Original case: a project is named by its capitalised words.
    for clause in rf.CLAUSE_SPLIT_RE.split(text):
        if clause and _GAP_SHAPED_RE.search(clause) and rf.fields_mentioned(clause):
            return clause
    return None


def label_v2_items(
    *,
    missing_items: Iterable[Mapping[str, Any] | str],
    concerns: Iterable[Mapping[str, Any]],
    closers: Sequence[FindingFacts],
    as_of: date | None = None,
) -> dict[str, list[dict[str, Any]]]:
    """Label the V2 report's own gap statements against the V3 findings.

    Only items that name a field are labelled; everything else is left exactly as V2
    wrote it. The web drops a ``closed`` missing-information FIELD NAME, but never drops
    a council concern: a concern is at most annotated with the finding that speaks to it.
    """
    live = sorted(
        (f for f in closers if not f.withdrawn),
        key=lambda f: (f.superseded_by is not None, not f.is_primary),
    )
    missing_out: list[dict[str, Any]] = []
    for item in missing_items:
        name = item.get("field") if isinstance(item, Mapping) else item
        source = item.get("source") if isinstance(item, Mapping) else None
        if not name:
            continue
        # EXACT names only: "revenue_growth" is not "revenue".
        field_key = exact_field_for_item(str(name))
        if field_key is None:
            continue
        labelled = _label_item(rf.label_of(field_key), live, as_of)
        if labelled is None:
            continue
        if (
            labelled["status"] == ledger.RECONCILED_CLOSED
            and field_key in PERIOD_BOUND_FIELDS
            and rf.statement_period(str(name)) is None
        ):
            # "revenue" with no period: a finding for SOME year is not THE figure the
            # item names. Annotated, never hidden.
            labelled["status"] = ledger.RECONCILED_PARTIALLY_CLOSED
            labelled["reasons"] = [*labelled["reasons"], REASON_PERIOD_UNSPECIFIED]
        missing_out.append({"field": str(name), "source": source, **labelled})
    concerns_out: list[dict[str, Any]] = []
    for index, concern in enumerate(concerns):
        text = str(concern.get("text") or "")
        clause = _gap_clause(text) if text else None
        if clause is None:
            continue
        labelled = _label_item(clause, live, as_of)
        if labelled is not None:
            concerns_out.append({
                "text": text,
                "key": normalise_item_text(text),
                "agent": concern.get("agent"),
                "index": concern.get("index", index),
                **labelled,
            })
    return {"missing_information": missing_out[:80], "council_concerns": concerns_out[:80]}


# ── Running it over one ledger run ─────────────────────────────────────────── #


def ready_documents(
    core_filings: Mapping[str, Any] | None, core_disclosures: Mapping[str, Any] | None
) -> list[DocumentFacts]:
    """The documents the run HOLDS, from what the pre-run acquisition reported."""
    out: list[DocumentFacts] = []
    slot_kind = {"annual": rf.DOC_ANNUAL, "quarterly": rf.DOC_INTERIM}
    for slot, kind in slot_kind.items():
        item = (core_filings or {}).get(slot) or {}
        if isinstance(item, Mapping) and item.get("state") in _READY_STATES:
            out.append(DocumentFacts(kind=kind, ref=item.get("form"),
                                     published=item.get("filing_date")))
    for item in (core_disclosures or {}).get("documents") or []:
        if not isinstance(item, Mapping) or item.get("state") != "ready":
            continue
        kind = str(item.get("document_kind") or "")
        if kind in {rf.DOC_ANNUAL, rf.DOC_INTERIM, rf.DOC_RESULTS}:
            out.append(DocumentFacts(kind=kind, ref=item.get("document_ref") or item.get("id"),
                                     published=item.get("filing_date")))
    return out


def question_field_map(question_rows: Iterable[Any]) -> dict[str, tuple[str, ...]]:
    """Fields each question asks for: its required metrics, then its own words."""
    out: dict[str, tuple[str, ...]] = {}
    for row in question_rows:
        fields = list(rf.fields_for_metrics(getattr(row, "required_metrics_json", None) or ()))
        for f in rf.fields_mentioned(getattr(row, "text", "") or ""):
            if f not in fields:
                fields.append(f)
        if fields:
            out[str(row.question_key)] = tuple(fields)
    return out


async def _validated_facts(session: Any, company_id: Any) -> list[FactFacts]:
    from sqlalchemy import select

    from app.models.extracted_document import ExtractedDocument, ExtractedFact
    from app.services.sources.company_documents import company_documents_clause

    labels = sorted({label for f in rf.FIELDS for label in f.fact_labels})
    rows = (
        await session.execute(
            select(ExtractedFact)
            .join(ExtractedDocument, ExtractedDocument.id == ExtractedFact.extracted_document_id)
            .where(
                company_documents_clause(company_id),
                ExtractedFact.is_active.is_(True),
                ExtractedFact.validation_status == "validated",
                ExtractedFact.label.in_(labels),
            )
            .order_by(ExtractedFact.label, ExtractedFact.period.desc())
            .limit(200)
        )
    ).scalars().all()
    out: list[FactFacts] = []
    for row in rows:
        field_key = rf.field_for_fact_label(row.label)
        if field_key:
            out.append(FactFacts(str(row.id), row.label, field_key, row.period, row.scope_type))
    return out


async def reconcile_run(
    session: Any,
    run: Any,
    *,
    company_id: Any = None,
    core_filings: Mapping[str, Any] | None = None,
    core_disclosures: Mapping[str, Any] | None = None,
    as_of: date | None = None,
) -> dict[str, Any]:
    """Supersede, reconcile, persist. Returns the record the report carries."""
    as_of = as_of or date.today()
    from sqlalchemy import select

    from app.models.ledger import (
        ResearchDisagreement,
        ResearchFinding,
        ResearchGap,
        ResearchQuestion,
    )

    finding_rows = list((await session.execute(
        select(ResearchFinding).where(ResearchFinding.research_run_id == run.id)
    )).scalars().all())
    by_id = {str(row.id): row for row in finding_rows}

    # 19 — supersession first, so closure prefers current guidance.
    supersessions, temporal = supersede([FindingFacts.from_row(r) for r in finding_rows])
    # The column holds ONE pointer: the LATEST newer finding. The per-field map in the
    # returned record (``supersessions``) is authoritative — a finding can be prior
    # guidance for one field and current for another.
    latest: dict[str, Supersession] = {}
    for sup in supersessions:
        held = latest.get(sup.older_id)
        if held is None or sup.newer_published_at > held.newer_published_at:
            latest[sup.older_id] = sup
    for older_id, sup in latest.items():
        await ledger.mark_superseded(session, by_id[older_id], by=by_id[sup.newer_id])
    existing_pairs = {
        frozenset((str(a), str(b)))
        for a, b in (await session.execute(
            select(ResearchDisagreement.finding_a_id, ResearchDisagreement.finding_b_id)
            .where(ResearchDisagreement.research_run_id == run.id)
        )).all()
    }
    recorded_disagreements = 0
    for d in temporal:
        pair = frozenset((d.finding_a_id, d.finding_b_id))
        if pair in existing_pairs:
            continue
        existing_pairs.add(pair)
        await ledger.record_disagreement(
            session, run, finding_a=by_id[d.finding_a_id], finding_b=by_id[d.finding_b_id],
            nature="value",
            description=(
                f"Two statements of {rf.label_of(d.field_key)} for the same scope and "
                f"project differ and are not ordered in time "
                f"({d.reason.replace('_', ' ')}). Neither is treated as current."
            ),
        )
        recorded_disagreements += 1

    findings = [FindingFacts.from_row(r) for r in finding_rows]
    gap_rows = list((await session.execute(
        select(ResearchGap).where(ResearchGap.research_run_id == run.id)
        .order_by(ResearchGap.created_at)
    )).scalars().all())
    question_rows = (await session.execute(
        select(ResearchQuestion).where(ResearchQuestion.research_run_id == run.id)
    )).scalars().all()
    facts: list[FactFacts] = []
    if company_id is not None:
        try:
            async with session.begin_nested():
                facts = await _validated_facts(session, company_id)
        except Exception:  # noqa: BLE001 - no facts is the fail-closed answer
            facts = []
    verdicts = reconcile(
        [
            GapFacts(
                gap_id=str(row.id), gap_type=row.gap_type, description=row.description,
                question_key=row.question_key, status=row.status,
                closed_by_finding_id=(str(row.closed_by_finding_id)
                                      if row.closed_by_finding_id else None),
            )
            for row in gap_rows
        ],
        findings,
        facts=facts,
        documents=ready_documents(core_filings, core_disclosures),
        question_fields=question_field_map(question_rows),
        as_of=as_of,
    )
    gaps_by_id = {str(row.id): row for row in gap_rows}
    for verdict in verdicts:
        row = gaps_by_id[verdict.gap_id]
        closer = by_id.get(verdict.closed_by_finding_id or "")
        if verdict.status == ledger.RECONCILED_CLOSED and closer is None:
            verdict.status = ledger.RECONCILED_STILL_OPEN  # never closed without a row
        await ledger.reconcile_gap(
            session, row, status=verdict.status, detail=verdict.to_dict(),
            finding=closer if verdict.status == ledger.RECONCILED_CLOSED else None,
        )
    counts: dict[str, int] = {}
    for verdict in verdicts:  # AFTER any downgrade above, so the counts are the truth
        counts[verdict.status] = counts.get(verdict.status, 0) + 1

    closers = [f for f in findings if f.fields and not f.withdrawn]
    return {
        "version": 3,
        "as_of": as_of.isoformat(),
        "gaps": [v.to_dict() for v in verdicts][:120],
        "counts": dict(sorted(counts.items())),
        "supersessions": [s.to_dict() for s in supersessions][:60],
        "temporal_disagreements": [d.to_dict() for d in temporal][:60],
        "temporal_disagreements_recorded": recorded_disagreements,
        # What attach_to_report labels the V2 report's own gap statements against.
        "closing_findings": [f.to_dict() for f in closers][:MAX_CLOSING_FINDINGS],
        "note": (
            "Each gap was reconciled against every finding, validated fact and acquired "
            "document of the run. Closed and superseded gaps are not shown as open; a "
            "partially closed gap names the finding that addresses it."
        ),
    }


def closers_from_payload(items: Iterable[Mapping[str, Any]]) -> list[FindingFacts]:
    """Rebuild the closing findings from the stored record (for the V2 labelling)."""
    out: list[FindingFacts] = []
    for item in items:
        fields = tuple(f for f in (item.get("fields") or ()) if rf.is_known_field(f))
        if not fields or not item.get("finding_id"):
            continue
        out.append(FindingFacts(
            finding_id=str(item["finding_id"]),
            statement=str(item.get("statement") or ""),
            fields=fields,
            project=item.get("project"),
            scope_key=item.get("scope_key"),
            period_key=item.get("period_key"),
            source_kinds=tuple(item.get("source_kinds") or ()),
            published_at=rf.parse_date(item.get("source_published_at")),
            superseded_by=item.get("superseded_by_finding_id"),
        ))
    return out


__all__ = [
    "ACQUISITION_GAP_TYPES",
    "CLOSABLE_GAP_TYPES",
    "DOCUMENT_GAP_TYPES",
    "ISSUER_SOURCE_KINDS",
    "PRIMARY_SOURCE_KINDS",
    "DocumentFacts",
    "FactFacts",
    "FindingFacts",
    "GapFacts",
    "GapVerdict",
    "Supersession",
    "TemporalDisagreement",
    "closers_from_payload",
    "compare_values",
    "finding_fields",
    "gap_scope",
    "label_v2_items",
    "normalise_item_text",
    "question_field_map",
    "ready_documents",
    "reconcile",
    "reconcile_run",
    "requirements",
    "supersede",
]
