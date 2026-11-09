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

import functools
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
REASON_WEB_CONTEXT_ONLY = "web_context_only"
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
#: ADR-086 — a finding for a LATER period than the one the gap names.
REASON_LATER_PERIOD = "later_period"
#: ADR-086 — a forecast, target or guidance figure, offered for a historical gap.
REASON_FORWARD_LOOKING = "forward_looking_not_historical"
#: ADR-086 — a balance gap named an older date; a later balance supersedes it.
REASON_SUPERSEDED_BY_LATER = "superseded_by_later_period"
#: ADR-086 — the gap's relevant held document was never read (ADR-084).
REASON_HELD_NOT_READ = "held_document_not_read"
#: ADR-086 (review P1-3) — the finding is not positively about the subject: it names a
#: related or compared entity, or another company before its figure.
REASON_SUBJECT_UNCERTAIN = "subject_not_established"

# ── ADR-086: the gap status vocabulary (spec §10) ─────────────────────────── #
#
# The ledger column ``reconciliation_status`` keeps its four values (migration 043's
# CHECK); the status below is the report's vocabulary, persisted per gap in
# ``reconciliation_json.gap_status`` and in the report payload.
GAP_STATUS_OPEN = "OPEN"
GAP_STATUS_PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
GAP_STATUS_CLOSED = "CLOSED"
GAP_STATUS_SUPERSEDED = "SUPERSEDED"
#: A finding speaks to the gap's metric, but its period cannot be compared with the
#: gap's (an undated balance, a forecast offered for a historical gap).
GAP_STATUS_NONCOMPARABLE = "NONCOMPARABLE"
#: The relevant document is held and was not read into an answer (ADR-084).
GAP_STATUS_EXTRACTION_FAILED = "EXTRACTION_FAILED"
GAP_STATUSES: tuple[str, ...] = (
    GAP_STATUS_OPEN,
    GAP_STATUS_PARTIALLY_CLOSED,
    GAP_STATUS_CLOSED,
    GAP_STATUS_SUPERSEDED,
    GAP_STATUS_NONCOMPARABLE,
    GAP_STATUS_EXTRACTION_FAILED,
)
#: Reasons that make a finding's answer NOT COMPARABLE with the gap (rather than a
#: partial answer): nothing places it in time, or it is a forecast.
NONCOMPARABLE_REASONS: frozenset[str] = frozenset(
    {REASON_PERIOD_UNKNOWN, REASON_VALUE_DATE_UNKNOWN, REASON_FORWARD_LOOKING,
     REASON_SUBJECT_UNCERTAIN,
     # ADR-091 (JPM2-D3, PYPL4-4): a segment, product-line or other sub-Group figure is
     # not a partial answer to a Group gap; it is another quantity.
     REASON_SCOPE_DIFFERS,
     # ADR-094 (PYPL5-4, HON4-6): a stale value — a balance more than a year old, or a
     # figure for a period older than the newest one the run holds for the same metric
     # — never partly answers a gap about the company now.
     REASON_VALUE_STALE}
)
#: ADR-091 — reasons saying the finding's figure is for ANOTHER period than the gap's.
PERIOD_DIFFERS_REASONS: frozenset[str] = frozenset(
    {REASON_OLDER_PERIOD, REASON_OTHER_PERIOD, REASON_LATER_PERIOD}
)
#: ADR-091 — reasons saying the finding states a related but different metric.
METRIC_DIFFERS_REASONS: frozenset[str] = frozenset(
    {REASON_SUBTYPE_DIFFERS, REASON_QUALIFIER_DIFFERS}
)

#: ADR-094 — a gap that asks for HISTORY (a trend, a series, the periods compared), so a
#: figure for another period than the one it names is still part of its answer. Bounded
#: and linear: alternation of literal words only.
_HISTORY_ASKED_RE = re.compile(
    r"\b(?:history|historical|trend|trends|trajectory|over[ \t]{1,3}time|"
    r"across[ \t]{1,3}(?:the[ \t]{1,3})?(?:disclosed[ \t]{1,3})?(?:periods|years|quarters)|"
    r"multi-?year|year-on-year|series)\b",
    re.IGNORECASE,
)


def asks_for_history(text: str | None) -> bool:
    """ADR-094 — the gap asks for a series, not one period's value."""
    return _HISTORY_ASKED_RE.search((text or "")[:2000]) is not None


def is_noncomparable(reasons: "Sequence[str]", *, history_asked: bool = False) -> bool:
    """Is a finding's partial answer NOT COMPARABLE with the gap?

    ADR-091: a non-comparable reason (``NONCOMPARABLE_REASONS``). ADR-094 (spec §10):
    also a related but DIFFERENT metric (another capex sub-type, another qualifier), and
    a figure for ANOTHER period than the one the gap names — FY2025 revenue does not
    partly answer "no FY2026 revenue" — unless the gap itself asks for history."""
    found = set(reasons)
    if found & NONCOMPARABLE_REASONS:
        return True
    if found & METRIC_DIFFERS_REASONS:
        return True
    return bool(found & PERIOD_DIFFERS_REASONS) and not history_asked

#: ADR-086 — a finding that names ANOTHER entity as its subject ("Peer Co (a
#: peer, not the company) reported …") never answers the subject's gap.
_OTHER_ENTITY_RE = re.compile(
    r"\((?:a|an|the)[ \t]{1,3}(?:peer|competitor|rival|customer|supplier|offtaker|"
    r"off-taker|counterparty|lender|partner|former[ \t]{1,3}subsidiary)\b[^)]{0,80}\)"
    r"|\bnot[ \t]{1,3}the[ \t]{1,3}(?:company|issuer|subject)\b",
    re.IGNORECASE,
)
#: ADR-086 — a clause stating a value as a forecast, target, guidance, an estimate or a
#: hypothetical (review P2-4: "should / could / may / might / would", "is seen at",
#: "analysts see", "goal", "midpoint", "range", "pro forma", "consensus"). "May" the
#: month ("May 2026", "31 May") is not a modal.
_FORWARD_CLAUSE_RE = re.compile(
    r"\b(?:guid(?:ance|ed|es)|outlook|forecasts?|forecasted|projected|projections?|"
    r"target(?:ed|ing)?|budget(?:ed)?|expects?|expected|expectations?|anticipat\w{0,6}|"
    r"estimates?|estimated|plans?[ \t]{1,3}to|planned|will|consensus|should|could|"
    r"might|would|seen|sees|goals?|midpoint|range|pro[- ]forma|aims?|intends?|"
    r"on[ \t]{1,3}track|set[ \t]{1,3}to|analysts?[ \t]{1,3}(?:see|expect|forecast))\b"
    r"|(?<!\d[ \t])\bmay\b(?![ \t]{0,3}(?:\d|,))",
    re.IGNORECASE,
)
#: ADR-086 (review P1-3) — the statement says it is about a related or compared entity:
#: a peer, a customer, a supplier, a counterparty. Never closes or supersedes; the
#: subject is not established, so the answer is NONCOMPARABLE.
_RELATED_ENTITY_RE = re.compile(
    r"\b(?:peers?|competitors?|rivals?|customer|client|supplier|offtakers?|"
    r"off-takers?|counterpart(?:y|ies)|lenders?|unlike|in[ \t]{1,3}contrast[ \t]{1,3}to)\b",
    re.IGNORECASE,
)
#: An organisation name with a legal suffix ("Rival Corp", "Acme Mining Ltd").
_LEGAL_NAME_RE = re.compile(
    r"\b(?P<name>[A-Z][\w&'’.-]{0,40}(?:[ \t]{1,3}[A-Z][\w&'’.-]{0,40}){0,3})[ \t,]{1,3}"
    r"(?:Corp(?:oration)?|Inc|Incorporated|Ltd|Limited|plc|PLC|LLC|GmbH|Pty|SpA|ASA|AG|"
    r"SE|SA|NV|Co)\b"
)
#: ADR-086 (review P2-4) — wording placing a fact below the Group: a business, a region,
#: a unit, a division, a segment, a customer group, a country.
_SUB_SCOPE_RE = re.compile(
    r"\b(?:business(?:es)?|regions?|regional|units?|divisions?|segments?|subsidiar(?:y|ies)|"
    r"operations[ \t]{1,3}in|"
    r"from[ \t]{1,3}(?!contracts?[ \t])(?:[\w-]{1,30}[ \t]{1,3}){1,3}customers|"
    r"in[ \t]{1,3}(?:the[ \t]{1,3})?(?:asia|europe|emea|apac|americas|africa|china|japan|"
    r"india|australia|canada|brazil|mexico|germany|france|latin[ \t]{1,3}america|"
    r"north[ \t]{1,3}america|middle[ \t]{1,3}east|united[ \t]{1,3}kingdom|"
    r"united[ \t]{1,3}states|usa|uk))\b",
    re.IGNORECASE,
)

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

#: ADR-086 — metrics that are only ever HISTORY (a figure for a period that ended, a
#: balance at a date): a forecast of one never closes a gap about it. Capex is not here:
#: a project's capital estimate is the answer a capex gap often wants.
HISTORICAL_FIELDS: frozenset[str] = frozenset(
    {"metric:revenue", "metric:operating_cash_flow", "metric:cash", "metric:net_debt",
     "metric:capex_period"}
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
    #: The open-web trust label the stored statement carries (``trust.label_of_statement``
    #: — "reported in the press; not from a filing", "company describes itself as …").
    #: The statement itself is read WITHOUT it: a label is not part of the claim, and the
    #: relabel text must never be parsed as a figure (open-web W4 review H3).
    trust_label: str | None = None
    #: ADR-088 — the declared statement kind. Guidance, a forecast, a target or an
    #: estimate never answers a gap that asks for a reported value.
    statement_kind: str | None = None

    @classmethod
    def from_row(cls, row: Any) -> "FindingFacts":
        from app.services.web_research.trust import label_of_statement, unlabelled_statement

        raw_statement = str(getattr(row, "statement", "") or "")
        trust_label = label_of_statement(raw_statement)
        statement = unlabelled_statement(raw_statement)
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
            stated_period=text_period(statement),
            trust_label=trust_label,
            statement_kind=getattr(row, "statement_kind", None),
        )

    @property
    def is_non_actual(self) -> bool:
        from app.services.statement_kind import is_non_actual

        return is_non_actual(self.statement_kind)

    @property
    def is_context_only(self) -> bool:
        """Stored with a label that says "context, not the answer" (a web value that is
        not from a filing, an issuer's self-description, an anecdote): it can inform a
        gap but never close it (spec §13.3 / §17.3)."""
        from app.services.web_research.trust import is_non_closing_label

        return is_non_closing_label(self.trust_label)

    @property
    def is_primary(self) -> bool:
        return not self.is_context_only and bool(set(self.source_kinds) & PRIMARY_SOURCE_KINDS)

    @property
    def is_other_entity(self) -> bool:
        """ADR-086 — the statement says its subject is another entity."""
        return _OTHER_ENTITY_RE.search(self.statement or "") is not None

    @property
    def effective_period(self) -> str | None:
        return rf.normalise_period(self.period_key) or self.stated_period

    @property
    def period_conflict(self) -> bool:
        """ADR-094 (LIN4-D3) — the stored period key and the period the statement's own
        words name are in DIFFERENT years ("year to 30 June 2025" stored as 2026): which
        one the figure is for is unknown, so it closes nothing period-bound."""
        stored = rf.period_rank(self.period_key)
        stated = rf.period_rank(self.stated_period)
        return bool(stored and stated and stored[0] != stated[0])

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
            "trust_label": self.trust_label,
            **({"statement_kind": self.statement_kind} if self.statement_kind else {}),
        }


@dataclass(frozen=True)
class GapFacts:
    gap_id: str
    gap_type: str
    description: str
    question_key: str | None = None
    status: str = ledger.GAP_OPEN
    closed_by_finding_id: str | None = None
    #: ADR-086 — whose gap it is (``knowledge_state``) and its question's unresolved
    #: reason: ``acquired_not_read`` (ADR-084) makes an unanswered gap EXTRACTION_FAILED.
    knowledge_state: str | None = None
    unresolved_reason: str | None = None

    @property
    def held_not_read(self) -> bool:
        return ledger.UNRESOLVED_ACQUIRED_NOT_READ in (
            self.knowledge_state, self.unresolved_reason
        )


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


def text_period(statement: str | None) -> str | None:
    """The period a statement's own words name: ``rf.statement_period`` first, then
    (ADR-086) the one non-forward period ``claim_period`` reads — "Quarter ended 30 Sep
    2026", "as of September 30, 2026" — in the same vocabulary, else ``None``."""
    found = rf.statement_period(statement)
    if found:
        return found
    from app.services.ledger.claim_period import single_period

    period = single_period(statement)
    return period.rf_key() if period is not None else None


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
    #: ADR-086 — the report's status vocabulary (``GAP_STATUSES``).
    gap_status: str = GAP_STATUS_OPEN
    superseded_by_finding_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "gap_id": self.gap_id,
            "status": self.status,
            "gap_status": self.gap_status,
            "superseded_by_finding_id": self.superseded_by_finding_id,
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


# ── Contradicted absence ───────────────────────────────────────────────────── #

#: The period class an ABSENCE claim is limited to ("no interim or quarterly revenue").
_SUB_ANNUAL_RE = re.compile(
    r"\b(?:interim|quarter(?:ly|s)?|sub[- ]annual|half[- ]year|six[- ]months?|three[- ]months?|"
    r"h[12]|q[1-4])\b|(?:^|[^a-z])(?:q[1-4]|h[12])(?:[^a-z]|$)",
    re.IGNORECASE,
)
_ANNUAL_RE = re.compile(r"\b(?:annual|full[- ]year|fy\s?\d{2,4}|year[- ]ended|fiscal year)\b",
                        re.IGNORECASE)
CONTRADICTED_ABSENCE = "absence_contradicted"
#: A denial about the FUTURE or about a direction is no claim that a figure is absent:
#: "no revenue guidance for FY2027", "revenue did not decline in 2025".
_NOT_AN_ABSENCE_OF_A_FIGURE = re.compile(
    r"\b(?:guid(?:ance|ed)|forecasts?|outlook|projections?|projected|estimates?|expects?|"
    r"expected|targets?|targeted|did\s+not\s+(?:decline|fall|drop|rise|increase|grow|change)|"
    r"not\s+(?:declin\w+|fall\w*|rise|grow\w*)|no\s+(?:decline|change|growth))\b",
    re.IGNORECASE,
)
#: ADR-091 — a CONTRAST is no denial either: "the Group reports a net cash position, not
#: net debt" states the sign of one figure; it says nothing is absent. Pairing it against
#: the net-cash finding it agrees with produced a phantom "net cash vs net debt" conflict.
_CONTRAST_NOT_ABSENCE = re.compile(
    r",\s{0,3}(?:and\s|but\s)?\s{0,3}not\s|\brather\s+than\b|\binstead\s+of\b", re.IGNORECASE
)
#: ADR-091 — a source label ("[web — not from a filing]") negates the SOURCE, not a figure.
_SOURCE_LABEL_NEGATION = re.compile(
    r"\bnot\s+(?:from|in)\s+(?:a|an|the)\s+(?:primary\s+)?(?:filing|source|document)s?\b",
    re.IGNORECASE,
)
_YEAR = re.compile(r"(?<!\d)((?:19|20)\d{2})(?!\d)|\bfy\s?(\d{2})\b", re.IGNORECASE)


def _years_of(text: str, period_key: str | None = None) -> set[str]:
    """Four-digit years a text (and its stored period) names; FY25 reads as 2025."""
    found: set[str] = set()
    for source in (text or "", rf.normalise_period(period_key) or ""):
        for full, short in _YEAR.findall(source):
            found.add(full or f"20{short}")
    return found


def _period_class(text: str, period_key: str | None = None) -> str | None:
    """``sub_annual`` | ``annual`` | None, from the stored period when it has one."""
    key = rf.normalise_period(period_key) or ""
    if re.search(r"(?:^|[-\s])(?:Q[1-4]|H[12])\b", key):
        return "sub_annual"
    if key:
        return "annual"
    if _SUB_ANNUAL_RE.search(text or ""):
        return "sub_annual"
    if _ANNUAL_RE.search(text or ""):
        return "annual"
    return None


def _contrasts_two_fields(clause: str) -> bool:
    """True for "<field A> …, not <field B>": one figure's sign or kind, not an absence.

    The head must be affirmative and name a field, and the contrasted tail must name a
    field too. A tail limited to a period class stays a denial: "revenue is reported
    annually, not quarterly" (tail names no field) and "…, not quarterly revenue" (tail is
    period-limited) both still deny quarterly revenue.
    """
    m = _CONTRAST_NOT_ABSENCE.search(clause)
    if m is None:
        return False
    head_text, tail_text = clause[: m.start()], clause[m.end():]
    if rf.is_negated(head_text.lower(), head_text):
        return False
    if _SUB_ANNUAL_RE.search(tail_text) or _ANNUAL_RE.search(tail_text):
        return False
    return bool(rf.fields_mentioned(head_text)) and bool(rf.fields_mentioned(tail_text))


#: An amount in a contrast's head: the head states a figure (review P3).
_HEAD_FIGURE_RE = re.compile(
    r"(?:[$\u20ac\u00a3\u00a5]|\b(?:USD|EUR|GBP|AUD|CAD|CHF|JPY|INR|A\$|US\$))[ \t]?\d"
    r"|\d[\d,.]{0,20}[ \t]?(?:m|bn|k|million|billion|thousand|%)(?![a-z])",
    re.IGNORECASE,
)


def _denied_fields(clause: str) -> tuple[tuple[str, ...], str]:
    """``(fields, period_text)``: the fields a negated clause denies, and the text whose
    period class and years limit the denial.

    Review P3: in "<head>, not <tail>" with an affirmative head naming a field ("the
    report gives revenue, not EBITDA"), the head's field is STATED, not absent:

    * a tail naming a field denies only the tail's fields;
    * a tail naming only a period class denies the head's field for THAT class:
      "revenue is reported annually, not quarterly" denies quarterly revenue, while
      while a head stating a FIGURE ("Q3 revenue was $5.0bn — quarterly, not annual")
      only describes that figure's period and denies nothing;
    * a tail naming neither ("…, not EBITDA", a metric we do not track) denies nothing.
    """
    m = _CONTRAST_NOT_ABSENCE.search(clause)
    if m is not None:
        head_text = clause[: m.start()]
        if not rf.is_negated(head_text.lower(), head_text) and rf.fields_mentioned(head_text):
            tail_text = clause[m.end():]
            tail_fields = rf.fields_mentioned(tail_text)
            if tail_fields:
                return tail_fields, tail_text
            if _HEAD_FIGURE_RE.search(head_text):
                # "Q3 revenue was USD 4,994m — quarterly, not annual" describes a stated
                # figure's period; it denies nothing (HON2 replay).
                return (), clause
            if _SUB_ANNUAL_RE.search(tail_text) or _ANNUAL_RE.search(tail_text):
                return rf.fields_mentioned(head_text), tail_text
            return (), clause
    return rf.fields_mentioned(clause), clause


def contradicted_absences(findings: Sequence[FindingFacts]) -> list[TemporalDisagreement]:
    """Findings that DENY a field another finding of the same run STATES.

    "No interim or quarterly Group revenue appears" and "Q2 2026 net sales were $4,289.0
    million" cannot both stand. Neither is silently dropped: the pair is recorded as a
    disagreement so a reader sees the conflict instead of two confident statements.

    Deliberately narrow, so it never invents a conflict: the denial must be a NEGATED
    clause that names the field; the stating finding must affirmatively state that field in
    the same scope and project; and when the denial is limited to a period class (interim,
    quarterly, annual) the stating finding must be of that class. A denial about one
    segment, project or period never contradicts a statement about another.
    """
    out: list[TemporalDisagreement] = []
    live = [f for f in findings if not f.withdrawn]
    for absent in live:
        for raw_clause in rf._raw_clauses(absent.statement):  # noqa: SLF001 - same module family
            clause = _SOURCE_LABEL_NEGATION.sub(" ", raw_clause)
            low = clause.lower()
            if not rf.is_negated(low, clause):
                continue
            if _NOT_AN_ABSENCE_OF_A_FIGURE.search(clause) or _contrasts_two_fields(clause):
                continue
            denied_keys, period_text = _denied_fields(clause)
            denied = [k for k in denied_keys if k in rf.FIELDS_BY_KEY]
            if not denied:
                continue
            wanted = _period_class(period_text)
            denied_years = _years_of(clause)
            for field_key in denied:
                for stated in live:
                    if stated.finding_id == absent.finding_id or field_key not in stated.fields:
                        continue
                    if (stated.scope_key or "") != (absent.scope_key or ""):
                        continue
                    if (stated.project or "") != (absent.project or ""):
                        continue
                    stated_class = _period_class(stated.statement, stated.effective_period)
                    if wanted and stated_class != wanted:
                        continue
                    # A denial about one year does not contradict a statement about another.
                    if denied_years and not (
                        denied_years & _years_of(stated.statement, stated.effective_period)
                    ):
                        continue
                    out.append(TemporalDisagreement(
                        absent.finding_id, stated.finding_id, field_key, CONTRADICTED_ABSENCE
                    ))
    seen: set[tuple[str, str, str]] = set()
    unique: list[TemporalDisagreement] = []
    for d in out:
        marker = (d.finding_a_id, d.finding_b_id, d.field_key)
        if marker not in seen:
            seen.add(marker)
            unique.append(d)
    return unique[:MAX_DISAGREEMENTS]


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
    if (
        not finding.scope_key
        and not (scope and scope.startswith("segment"))
        and (gap_scope(finding.statement) or "").startswith("segment")
    ):
        # ADR-086: an unscoped finding whose own words are about a segment never
        # closes a Group (or unscoped) gap; it can still partly address it.
        return [REASON_SCOPE_DIFFERS]
    if (
        not finding.scope_key
        and not (scope and scope.startswith("segment"))
        and _SUB_SCOPE_RE.search((finding.statement or "")[:600])
    ):
        # ADR-086 (review P2-4): only a fact positively scoped to the Group, or one
        # with no sub-scope wording at all ("the Asia business", "the services
        # division", "from enterprise customers", "in Europe"), closes a Group gap.
        return [REASON_SCOPE_DIFFERS]
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
    order = _period_order(have, gap_period)
    if order is not None:
        return [REASON_OLDER_PERIOD] if order < 0 else [REASON_LATER_PERIOD]
    wanted_rank, have_rank = rf.period_rank(gap_period), rf.period_rank(have)
    if wanted_rank and have_rank and have_rank < wanted_rank:
        return [REASON_OLDER_PERIOD]
    return [REASON_OTHER_PERIOD]


def _period_order(have: str | None, wanted: str | None) -> int | None:
    """ADR-086 — ``-1`` when ``have`` certainly ENDS before ``wanted``, ``1`` when it
    certainly ends after it, ``None`` when that cannot be told (fiscal labels, an
    undated half)."""
    from app.services.ledger.claim_period import END_SLACK_DAYS, period_of_key

    a, b = period_of_key(have), period_of_key(wanted)
    if a is None or b is None or None in (a.end_lo, a.end_hi, b.end_lo, b.end_hi):
        return None
    assert a.end_lo and a.end_hi and b.end_lo and b.end_hi
    if (b.end_lo - a.end_hi).days > END_SLACK_DAYS:
        return -1
    if (a.end_lo - b.end_hi).days > END_SLACK_DAYS:
        return 1
    return None


def _forward_reasons(finding: FindingFacts, requirement: str, gap_text: str) -> list[str]:
    """ADR-086 — a forecast, target or guidance never closes a HISTORICAL gap. Only for
    period-bound metrics, and only when the gap itself does not ask for guidance."""
    if requirement not in HISTORICAL_FIELDS or _FORWARD_CLAUSE_RE.search(gap_text or ""):
        return []
    if not finding.statement:
        return []
    clause = rf.field_clause(finding.statement, requirement) or finding.statement[:600]
    if _FORWARD_CLAUSE_RE.search(clause):
        return [REASON_FORWARD_LOOKING]
    from app.services.ledger.claim_period import mentions

    if any(p.is_forward for p in mentions(clause)):
        return [REASON_FORWARD_LOOKING]
    return []


@dataclass(frozen=True)
class SubjectWords:
    """ADR-086 — the words that name the research subject, folded.

    ``distinctive``: words that establish the subject when a statement uses them (its
    ADR-083 ``subject_aliases`` and its ticker). ``own``: every word of its name, generic
    ones included ("resources", "group"); a capitalised word from it never counts as
    another entity's name."""

    distinctive: frozenset[str] = frozenset()
    own: frozenset[str] = frozenset()


NO_SUBJECT = SubjectWords()


def subject_words(subject_name: str | None, ticker: str | None = None) -> SubjectWords:
    """The subject's name and ticker as ``SubjectWords`` (empty when unknown)."""
    from app.services.discovery.identity import name_tokens
    from app.services.entities.passage_subject import subject_aliases

    name = subject_name if isinstance(subject_name, str) else ""
    distinctive = set(subject_aliases(name)) if name.strip() else set()
    own = set(name_tokens(name)) | set(distinctive)
    if isinstance(ticker, str) and ticker.strip():
        symbol = ticker.strip().lower()
        for part in {symbol, symbol.split(".")[0], symbol.split(":")[-1]}:
            if len(part) >= 2:
                distinctive.add(part)
                own.add(part)
    return SubjectWords(frozenset(distinctive), frozenset(own))


def subject_alias_set(subject_name: str | None) -> frozenset[str]:
    """The subject's distinctive name words (ADR-083 ``subject_aliases``), folded."""
    return subject_words(subject_name).distinctive


#: Capitalised words that name no entity: sentence-initial metric and period words,
#: months, currencies, units, articles and prepositions (review re-review: a finding
#: that names no proper noun before its figure is the subject's own).
_GENERIC_CAPITALISED: frozenset[str] = frozenset("""
a an the it its this these that our we in at as of for on by to from during over after
before following since until under per with and or according quarter quarterly half
year years annual interim fiscal financial full first second third fourth months
month period ended ending end date as-at fy cy ytd ttm ltm h q revenue revenues sales
cash equivalents balance liquidity net total gross group consolidated company firm
issuer entity bank business operating operations operational free underlying reported
statutory adjusted profit income loss losses margin margins earnings ebitda ebit eps
nii nim cet tier roe roa roic rotce book value assets liabilities equity debt
borrowings capital capex expenditure expenditures opex aisc cost costs production
output volume volumes shares share dividend dividends inventory receivables payables
current non-current long-term short-term report results statement statements filing
disclosure appendix management mgmt says said reported reports posted note notes
usd aud eur gbp cad chf nzd hkd jpy cny us au nz hk uk eu mn bn
january february march april may june july august september october november december
jan feb mar apr jun jul aug sep sept oct nov dec
asia europe emea apac americas africa china japan india australia canada brazil mexico
germany france latin america north south middle east west united kingdom states usa
worldwide global overall company-wide
""".split())
#: Study, asset and document words a statement about the subject's own asset opens with
#: ("The Foo DFS estimates …", "Ore Reserve …", "Nameplate capacity …").
_ASSET_WORDS: frozenset[str] = frozenset("""
dfs pfs bfs scoping study studies definitive feasibility pre-feasibility ore reserve
reserves resource resources mineral mine mines project projects plant refinery stage
phase line lines facility facilities nameplate capacity commissioning construction
expansion deposit concentrate offtake contract agreement
""".split())


@functools.lru_cache(maxsize=1)
def _field_vocabulary() -> frozenset[str]:
    """Words of every research field's label, aliases and patterns ("nameplate",
    "capacity", "capital"): a statement opening with a metric word names no entity."""
    words: set[str] = set(_ASSET_WORDS)
    for f in rf.FIELDS:
        for text in (f.label, *f.fact_labels, *f.metric_aliases, *f.patterns):
            words.update(re.findall(r"[a-z]{3,}", text.lower()))
    return frozenset(words)


#: A capitalised word in a statement (a possessive "'s" is stripped by the caller).
_CAPITALISED_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z][A-Za-z&'’-]{0,40}")
_STATEMENT_WORD_RE = re.compile(r"[a-z0-9&]+")


def _subject_reasons(
    finding: FindingFacts, subject: SubjectWords, gap_text: str = ""
) -> list[str]:
    """ADR-086 (review P1-3; re-review) — a finding closes or supersedes a gap only when
    its subject is POSITIVELY the research subject. Findings carry no persisted subject
    verdict (ADR-083 drops another entity's statement at creation), so the text decides:

    * a related / compared-entity marker (peer, customer, supplier, counterparty,
      "unlike") anywhere → not established;
    * the subject's distinctive name word or ticker in the statement → established;
    * otherwise established only when no capitalised proper noun precedes the first
      figure: every capitalised word there is generic (``_GENERIC_CAPITALISED``: "Revenue",
      "Cash", "The company", "Group", months, currencies) or one of the subject's own
      name words. "Albemarle reported cash …", "SQM's FY2025 revenue …" are not.

    Not established is NONCOMPARABLE: never CLOSED, never SUPERSEDED."""
    text = (finding.statement or "")[:600]
    if not text:
        return []
    if _RELATED_ENTITY_RE.search(text):
        return [REASON_SUBJECT_UNCERTAIN]
    from app.services.discovery.identity import _fold  # noqa: PLC2701

    words = set(_STATEMENT_WORD_RE.findall(_fold(text)))
    if subject.distinctive and words & subject.distinctive:
        return []
    # The finding's own scope (a segment, a region) and its own project (the subject's
    # asset, ``finding_fields``) name parts of the subject, not another entity.
    scope_words = set(_STATEMENT_WORD_RE.findall(
        _fold((finding.scope_key or "").partition(":")[2])))
    project_words = set(_STATEMENT_WORD_RE.findall(_fold(finding.project or "")))
    # A word the subject's own gap uses ("Capex for the Foo Project …") names the
    # subject's asset; metric and asset vocabulary names no entity.
    gap_words = set(_STATEMENT_WORD_RE.findall(_fold(gap_text or "")))
    allowed = subject.own | scope_words | project_words | gap_words | _field_vocabulary()
    money = rf.MONEY_RE.search(text)
    head = text[:money.start()] if money else text
    for m in _LEGAL_NAME_RE.finditer(head):
        name_words = subject_alias_set(m.group("name"))
        if not (subject.distinctive and name_words and name_words & subject.distinctive):
            return [REASON_SUBJECT_UNCERTAIN]
    for m in _CAPITALISED_RE.finditer(head):
        token = _fold(m.group(0)).rstrip("'’-")
        if token.endswith(("'s", "’s")):
            token = token[:-2]
        parts = [t for t in re.split(r"[-&'’]+", token) if t]
        if all(len(t) <= 1 or t in _GENERIC_CAPITALISED or t in allowed for t in parts):
            continue
        return [REASON_SUBJECT_UNCERTAIN]
    return []


#: ADR-091 — a metric word a product line, business or segment name precedes
#: ("Braintree revenue", "Payments revenue"). Literal alternatives only.
_LINE_METRIC_RE = re.compile(r"\b(?:revenues?|sales|turnover|net[ \t]revenue)\b", re.IGNORECASE)
#: Capitalised words before a metric that still name the whole company: the generic
#: vocabulary (``_GENERIC_CAPITALISED``) plus these.
_WHOLE_COMPANY_WORDS: frozenset[str] = frozenset(
    "firmwide groupwide wide overall headline record organic comparable managed "
    "recurring core one two three four five six nine twelve ifrs gaap non pro forma "
    "preliminary unaudited audited worldwide global company total company-wide "
    "total-company".split()
)
#: Review P1-4 — legal suffixes after the subject's name ("Cellectis S.A.", "Lynas Rare
#: Earths Ltd") are skipped like a qualifier, so the name before them is read.
_LEGAL_SUFFIX_TOKENS: frozenset[str] = frozenset(
    "s.a. sa s.a ltd ltd. limited inc inc. incorporated plc corp corp. corporation co "
    "co. ag se nv n.v. spa s.p.a. asa gmbh pty llc".split()
)
#: Requirements the product-line rule applies to: the revenue family.
_REVENUE_FAMILY: frozenset[str] = frozenset({"metric:revenue"})
#: How many tokens before the metric word are read for the line's name.
_LINE_NAME_TOKENS = 3


def _requirement_clause(statement: str, requirement: str) -> str | None:
    """The first affirmative clause stating ``requirement``, in its ORIGINAL case."""
    found = rf.FIELDS_BY_KEY.get(requirement)
    if found is None:
        return None
    for raw in rf._raw_clauses(statement):  # noqa: SLF001 - same module family
        low = raw.lower()
        if rf.is_negated(low, raw) or requirement not in rf._stated_keys(low):  # noqa: SLF001
            continue
        if rf._satisfies(found.needs, raw):  # noqa: SLF001
            return raw
    return None


def _sub_line_reasons(
    finding: FindingFacts, subject: SubjectWords, scope: str | None,
    requirement: str | None = None,
) -> list[str]:
    """ADR-091 (JPM2-D3, PYPL4-4) — an UNSCOPED finding that states a product line's,
    business's or segment's figure is not the Group's.

    "Braintree revenue up ~$1.1bn and Venmo up ~$230m", "FY2025: Payments revenue
    $19.3bn, Lending $7.6bn, Markets & Securities Services $41.3bn": a capitalised name
    directly before the revenue word that is neither generic, the subject's own name nor
    whole-company wording. A segment gap, and a finding that carries a scope, are judged
    by ``_scope_reasons``. Token scan, linear.

    Review P1-4: only for a revenue-family requirement, and only over the clause that
    carries the requirement's figure — a cash gap is never judged by a product-revenue
    clause beside the cash figure. ``requirement=None`` (a direct call) reads the whole
    statement."""
    if finding.scope_key or (scope and scope.startswith("segment")):
        return []
    if requirement is not None and requirement not in _REVENUE_FAMILY:
        return []
    text = (finding.statement or "")[:600]
    if requirement is not None:
        text = _requirement_clause(text, requirement) or ""
    if not text:
        return []
    from app.services.discovery.identity import _fold  # noqa: PLC2701

    for match in _LINE_METRIC_RE.finditer(text):
        before = text[max(0, match.start() - 80): match.start()].split()
        tokens = before[-_LINE_NAME_TOKENS:]
        # Skip "net" / "total" style qualifiers between the name and the metric.
        while tokens and (
            _fold(tokens[-1]).strip(",;:()") in _LEGAL_SUFFIX_TOKENS
            or _fold(tokens[-1]).strip(",.;:()") in _GENERIC_CAPITALISED
        ):
            tokens = tokens[:-1]
        if not tokens:
            continue
        word = tokens[-1].strip(",;:()\"'")
        if not word or not word[0].isupper() or any(ch.isdigit() for ch in word):
            continue
        folded = _fold(word).rstrip("'’")
        if folded.endswith(("'s", "’s")):
            folded = folded[:-2]
        parts = [t for t in re.split(r"[-&'’]+", folded) if t]
        if all(
            len(t) <= 1 or t in _GENERIC_CAPITALISED or t in _WHOLE_COMPANY_WORDS
            or t in subject.own or t in subject.distinctive
            for t in parts
        ):
            continue
        return [REASON_SCOPE_DIFFERS]
    return []


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
    subject_aliases: SubjectWords = NO_SUBJECT,
    newest_year: int | None = None,
) -> list[str] | None:
    """``[]`` when the finding fully answers, reasons when partly, ``None`` when not.

    ``newest_year`` (ADR-094) — the newest year the run holds ANY figure of this metric
    for; a gap naming no period asks about the company now, so an older figure is stale.
    """
    if finding.is_non_actual and _asks_for_reported_value(requirement):
        # ADR-088: guidance, a forecast, a target or an estimate is not the reported
        # value a history field asks for (a guidance-type field is answered by guidance).
        return None
    today = as_of or date.today()
    if finding.is_other_entity:
        return None  # ADR-086: a peer's or counterparty's fact is not the subject's
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
    reasons.extend(_forward_reasons(finding, requirement, gap_text))
    reasons.extend(_subject_reasons(finding, subject_aliases, gap_text))
    if REASON_SCOPE_DIFFERS not in reasons:
        reasons.extend(_sub_line_reasons(finding, subject_aliases, scope, requirement))
    reasons.extend(_balance_reasons(finding, requirement, today))
    reasons.extend(_period_reasons(finding, gap_period))
    reasons.extend(_currency_of_period_reasons(finding, requirement, gap_period, newest_year))
    reasons.extend(_recency_reasons(finding, gap_text, requirement, today))
    reasons.extend(_qualifier_reasons(finding, requirement, gap_text))
    if not finding.is_primary:
        reasons.append(REASON_THIRD_PARTY_ONLY)
    if finding.is_context_only:
        # Never a closing finding, whatever source kinds it cites (review H3).
        reasons.append(REASON_WEB_CONTEXT_ONLY)
    return reasons


def _currency_of_period_reasons(
    finding: FindingFacts, requirement: str, gap_period: str | None, newest_year: int | None
) -> list[str]:
    """ADR-094 (spec §10) — a stale period never closes a current gap.

    * a finding whose stored period and own words name different years: its period is
      unknown, whatever the gap asks (LIN4-D3);
    * a gap naming no period asks about the company NOW: a figure for a year older than
      the newest year the run holds this metric for is stale (PYPL5-4, HON4) — the hole
      ADR-091's ``_fact_is_stale`` left open, since it judged validated facts only.
    Period-bound metrics only; a guidance or estimate field is judged by supersession."""
    if requirement not in PERIOD_BOUND_FIELDS:
        return []
    if finding.period_conflict:
        return [REASON_PERIOD_UNKNOWN]
    if gap_period or newest_year is None:
        return []
    rank = rf.period_rank(finding.effective_period)
    if rank is not None and rank[0] < newest_year:
        return [REASON_VALUE_STALE]
    return []


def _newest_year(
    requirement: str, findings: Sequence[FindingFacts], facts: Sequence[FactFacts]
) -> int | None:
    """ADR-094 — the newest year any live primary finding or validated fact of this
    metric is for (an interim year counts: H1 2026 is newer than FY2025)."""
    years: list[int] = []
    for finding in findings:
        if (
            requirement in finding.fields and finding.is_primary
            and not finding.is_non_actual and not finding.period_conflict
            and (rank := rf.period_rank(finding.effective_period)) is not None
        ):
            years.append(rank[0])
    for fact in facts:
        if fact.field_key == requirement and (rank := rf.period_rank(fact.period)) is not None:
            years.append(rank[0])
    return max(years) if years else None


def _asks_for_reported_value(requirement: str) -> bool:
    """A money field that is HISTORY — never a guidance-type (supersedable) field."""
    field = rf.FIELDS_BY_KEY.get(requirement)
    return (
        requirement not in rf.SUPERSEDABLE_FIELDS
        and field is not None
        and field.needs == rf.NEEDS_MONEY
    )


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


def _newest_annual_year(facts: Sequence[FactFacts]) -> int | None:
    """ADR-091 — the newest full year any validated statement fact is for."""
    years = [
        rank[0] for fact in facts
        if (rank := rf.period_rank(fact.period)) is not None and rank[1] == 9
    ]
    return max(years) if years else None


def _fact_is_stale(fact: FactFacts, newest_annual: int | None) -> bool:
    """ADR-091 (PYPL4-4, JPM2-D3) — a statement fact for a year OLDER than the newest
    full year the run holds facts for (a 2023 capex fact beside FY2025 statements) does
    not answer a gap that names no period: the gap asks about the company now."""
    rank = rf.period_rank(fact.period)
    return bool(newest_annual and rank is not None and rank[0] < newest_annual)


def reconcile(
    gaps: Sequence[GapFacts],
    findings: Sequence[FindingFacts],
    *,
    facts: Sequence[FactFacts] = (),
    documents: Sequence[DocumentFacts] = (),
    question_fields: Mapping[str, Sequence[str]] | None = None,
    as_of: date | None = None,
    subject_name: str | None = None,
    subject_ticker: str | None = None,
) -> list[GapVerdict]:
    """One verdict per gap. Pure: the caller persists. ``as_of`` is the run date that
    "current" and "has this target passed" are judged against. ``subject_name`` lets a
    finding that names the subject's own legal name close its gap (ADR-086)."""
    live = [f for f in findings if not f.withdrawn]
    # Current guidance before prior guidance, primary sources before third parties.
    live.sort(key=lambda f: (f.superseded_by is not None, not f.is_primary))
    ready_docs: dict[str, DocumentFacts] = {}
    for doc in documents:
        ready_docs.setdefault(doc.kind, doc)
    by_question = dict(question_fields or {})
    today = as_of or date.today()
    aliases = subject_words(subject_name, subject_ticker)
    return [_reconcile_one(gap, live, facts, ready_docs, by_question, today, aliases)
            for gap in gaps]


def _reconcile_one(
    gap: GapFacts,
    findings: Sequence[FindingFacts],
    facts: Sequence[FactFacts],
    ready_docs: Mapping[str, DocumentFacts],
    question_fields: Mapping[str, Sequence[str]],
    as_of: date | None = None,
    aliases: SubjectWords = NO_SUBJECT,
) -> GapVerdict:
    verdict = _reconcile_core(gap, findings, facts, ready_docs, question_fields, as_of,
                              aliases)
    verdict.gap_status = gap_status_of(verdict, gap)
    if verdict.gap_status == GAP_STATUS_EXTRACTION_FAILED:
        verdict.reasons.append(REASON_HELD_NOT_READ)
    return verdict


def gap_status_of(verdict: GapVerdict, gap: GapFacts | None = None) -> str:
    """ADR-086 — the report's status for one verdict (``GAP_STATUSES``).

    * ``closed`` → CLOSED; ``superseded`` → SUPERSEDED;
    * ``partially_closed`` → NONCOMPARABLE when every finding that speaks to it is one
      whose period cannot be compared with the gap's (``NONCOMPARABLE_REASONS``), else
      PARTIALLY_CLOSED;
    * ``still_open`` → EXTRACTION_FAILED when the gap's relevant document is held and
      was not read (ADR-084 ``acquired_not_read``), else OPEN.
    """
    if verdict.status == ledger.RECONCILED_CLOSED:
        return GAP_STATUS_CLOSED
    if verdict.status == ledger.RECONCILED_SUPERSEDED:
        return GAP_STATUS_SUPERSEDED
    if verdict.status == ledger.RECONCILED_PARTIALLY_CLOSED:
        if REASON_NONCOMPARABLE_ONLY in verdict.reasons:
            return GAP_STATUS_NONCOMPARABLE
        return GAP_STATUS_PARTIALLY_CLOSED
    if gap is not None and gap.held_not_read:
        return GAP_STATUS_EXTRACTION_FAILED
    return GAP_STATUS_OPEN


#: ADR-086 — recorded on a partial verdict whose every addressing finding is
#: non-comparable (``NONCOMPARABLE_REASONS``).
REASON_NONCOMPARABLE_ONLY = "noncomparable_only"


def _reconcile_core(
    gap: GapFacts,
    findings: Sequence[FindingFacts],
    facts: Sequence[FactFacts],
    ready_docs: Mapping[str, DocumentFacts],
    question_fields: Mapping[str, Sequence[str]],
    as_of: date | None = None,
    aliases: SubjectWords = NO_SUBJECT,
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
    # only when the gap's own words name no field that may still be unanswered. Never a
    # gap whose held document was not READ (ADR-086 / ADR-084): holding it is not reading
    # it, and that gap stays EXTRACTION_FAILED.
    kinds = rf.document_kinds_mentioned(text)
    if (
        kinds and not text_fields
        and not gap.held_not_read
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
    if source != "gap_text":
        # ADR-094 (HON4-6, PYPL5): a field INFERRED from the question is not what the gap
        # asks for — "no customer concentration" under a revenue question is not partly
        # answered by revenue. Such a gap stays open and goes to the re-read queue
        # (``gap_resolution``), which reads it by its own words.
        return GapVerdict(gap.gap_id, ledger.RECONCILED_STILL_OPEN, fields=fields,
                          field_source=source, reasons=[REASON_FIELD_INFERRED])

    gap_project = rf.project_key(text)
    gap_period = rf.requested_period(text)
    scope = gap_scope(text)
    full: dict[str, str] = {}
    partial: dict[str, tuple[str, list[str]]] = {}
    #: ADR-086 — requirements some finding answers in a COMPARABLE way (not only an
    #: undated or forward-looking figure), and balances a LATER balance supersedes.
    comparable: set[str] = set()
    later: dict[str, str] = {}
    history = asks_for_history(text)
    newest = {req: _newest_year(req, findings, facts) for req in wanted}
    for requirement in wanted:
        for finding in findings:
            reasons = _assess(finding, requirement, gap_text=text, gap_project=gap_project,
                              gap_period=gap_period, scope=scope, as_of=as_of,
                              subject_aliases=aliases, newest_year=newest[requirement])
            if reasons is None:
                continue
            if not reasons:
                full[requirement] = finding.finding_id
                break
            noncomparable = is_noncomparable(reasons, history_asked=history)
            if requirement not in partial or (
                not noncomparable and requirement not in comparable
            ):
                # ADR-091: a comparable partial answer is the one shown; a
                # non-comparable one only when nothing comparable speaks to it.
                partial[requirement] = (finding.finding_id, reasons)
            if not noncomparable:
                comparable.add(requirement)
            if (
                requirement in POINT_IN_TIME_FIELDS and reasons == [REASON_LATER_PERIOD]
                and requirement not in later
            ):
                later[requirement] = finding.finding_id
    newest_annual = _newest_annual_year(facts)
    covering_facts = {
        req: fact for req in wanted for fact in facts
        if _fact_covers(fact, req, gap_project=gap_project, gap_period=gap_period, scope=scope)
        and not (gap_period is None and (
            _fact_is_stale(fact, newest_annual)
            # ADR-094: and older than the newest year ANY figure of the metric is for.
            or _fact_is_stale(fact, newest.get(req))
        ))
    }
    ordered_ids = list(dict.fromkeys(
        [full[r] for r in wanted if r in full]
        + [partial[r][0] for r in wanted if r in partial and r not in full
           # ADR-091: a non-comparable finding is not shown as answering a gap that a
           # comparable statement fact answers.
           and not (r in covering_facts and r not in comparable)]
    ))
    all_full = len(full) == len(wanted)
    closable_text = source == "gap_text" and gap.gap_type in CLOSABLE_GAP_TYPES
    if all_full and closable_text:
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_CLOSED, fields=fields, field_source=source,
            closed_by_finding_id=full[wanted[0]], finding_ids=ordered_ids,
        )
    if (
        closable_text and later
        and all(req in full or req in later for req in wanted)
    ):
        # ADR-086: a gap naming an older balance date, answered by a LATER balance of
        # the same metric, scope and entity from a primary source. The older figure is
        # still not held, and the gap says so as SUPERSEDED, naming the newer finding.
        newer = next(later[r] for r in wanted if r in later)
        return GapVerdict(
            gap.gap_id, ledger.RECONCILED_SUPERSEDED, fields=fields, field_source=source,
            finding_ids=list(dict.fromkeys([*ordered_ids, newer])),
            reasons=[REASON_SUPERSEDED_BY_LATER], superseded_by_finding_id=newer,
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
        if not full and fact is None and partial and not comparable:
            reasons.append(REASON_NONCOMPARABLE_ONLY)
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
    subject_name: str | None = None,
    subject_ticker: str | None = None,
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
    row_facts = [FindingFacts.from_row(r) for r in finding_rows]
    supersessions, temporal = supersede(row_facts)
    # A finding that denies what another states is a disagreement too (never silently kept).
    temporal = [*temporal, *contradicted_absences(row_facts)][:MAX_DISAGREEMENTS]
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
                f"One finding says {rf.label_of(d.field_key)} is absent from the evidence; "
                f"another finding in this run states it. Both cannot stand; neither is "
                f"treated as settled."
                if d.reason == CONTRADICTED_ABSENCE
                else (
                    f"Two statements of {rf.label_of(d.field_key)} for the same scope and "
                    f"project differ and are not ordered in time "
                    f"({d.reason.replace('_', ' ')}). Neither is treated as current."
                )
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
    unresolved_by_question = {
        str(q.question_key): getattr(q, "unresolved_reason", None) for q in question_rows
    }
    verdicts = reconcile(
        [
            GapFacts(
                gap_id=str(row.id), gap_type=row.gap_type, description=row.description,
                question_key=row.question_key, status=row.status,
                closed_by_finding_id=(str(row.closed_by_finding_id)
                                      if row.closed_by_finding_id else None),
                knowledge_state=getattr(row, "knowledge_state", None),
                unresolved_reason=unresolved_by_question.get(str(row.question_key)),
            )
            for row in gap_rows
        ],
        findings,
        facts=facts,
        documents=ready_documents(core_filings, core_disclosures),
        question_fields=question_field_map(question_rows),
        as_of=as_of,
        subject_name=subject_name,
        subject_ticker=subject_ticker,
    )
    gaps_by_id = {str(row.id): row for row in gap_rows}
    for verdict in verdicts:
        row = gaps_by_id[verdict.gap_id]
        closer = by_id.get(verdict.closed_by_finding_id or "")
        if verdict.status == ledger.RECONCILED_CLOSED and closer is None:
            verdict.status = ledger.RECONCILED_STILL_OPEN  # never closed without a row
            verdict.gap_status = GAP_STATUS_OPEN
        await ledger.reconcile_gap(
            session, row, status=verdict.status, detail=verdict.to_dict(),
            finding=closer if verdict.status == ledger.RECONCILED_CLOSED else None,
        )
    counts: dict[str, int] = {}
    status_counts: dict[str, int] = {}
    for verdict in verdicts:  # AFTER any downgrade above, so the counts are the truth
        counts[verdict.status] = counts.get(verdict.status, 0) + 1
        status_counts[verdict.gap_status] = status_counts.get(verdict.gap_status, 0) + 1

    closers = [f for f in findings if f.fields and not f.withdrawn]
    return {
        "version": 4,
        "as_of": as_of.isoformat(),
        "gaps": [v.to_dict() for v in verdicts][:120],
        "counts": dict(sorted(counts.items())),
        # ADR-086 — the same verdicts in the report's status vocabulary.
        "gap_status_counts": dict(sorted(status_counts.items())),
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
            statement_kind=item.get("statement_kind"),

            stated_period=text_period(str(item.get("statement") or "")),
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
    "GAP_STATUSES",
    "GapVerdict",
    "Supersession",
    "TemporalDisagreement",
    "closers_from_payload",
    "compare_values",
    "finding_fields",
    "gap_scope",
    "gap_status_of",
    "label_v2_items",
    "normalise_item_text",
    "question_field_map",
    "ready_documents",
    "reconcile",
    "reconcile_run",
    "requirements",
    "supersede",
    "text_period",
]
