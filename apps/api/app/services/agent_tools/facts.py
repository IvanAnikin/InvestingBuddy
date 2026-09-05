"""Fact and series tools — V3.3 Slice 3.2.

The first tools that hand an agent **numbers**, which is where getting the semantics
wrong stops being a usability problem and becomes a wrong figure in a memo.

THIS MODULE IS MOSTLY REFUSALS, AND THAT IS THE DESIGN
=====================================================
The querying is trivial: ``extracted_facts`` exists, has ``is_active`` (migration 017)
and has the typed scope triple (018). What is not trivial is deciding what a fact tool
is allowed to return, and every trap below is one this repository has already met:

**A number without its period, scope, unit, currency and scale is not citable.** All
five travel with every fact, from the vocabularies that already own them
(``ReportingPeriod``, ``FactScope``) rather than a second shape invented here. The raw
as-found ``value_text`` travels beside the number too, so an agent quoting a figure can
quote what the document printed rather than this platform's normalisation of it.

**Annual is not interim.** A series is the easiest place in the platform to mix them
and produce something that *looks* continuous. ``ReportingPeriod.comparable_with``
already refuses a cross-type comparison, so ``get_financial_series`` **requires** a
``period_type``. There is no default, because a default is what silently mixed them
before.

**Group is not segment, and unknown is not Group.** ``scope`` is also required, with
four explicit values. The report layer has a legacy convention that reads an absent
scope as implicitly Group; a *tool* applying that convention would hand an agent a
Specialist Watchmakers figure labelled Group, which is the CFR failure the entire scope
triple exists to prevent.

**``is_active`` and ``validation_status`` are not optional filters.** ``is_active``
exists because a superseded derivation was being mixed with a current one as evidence.
``excerpt_only`` is retained text, not a figure — returning it as a fact would be
fabrication with a database behind it.

**Two facts for one period are a conflict, not a series point.** Both are returned and
neither is dropped. MRNA has eight genuine numeric conflicts, and a guard that
suppresses them all is broken rather than safe.

**Every count names its population.** The rule is not to make numbers agree; it is to
say which population each number counts (``fact_count_scopes``).
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from sqlalchemy import func, select

from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.agent_tools.contracts import (
    TOOL_GET_FINANCIAL_FACTS,
    TOOL_GET_FINANCIAL_SERIES,
    TOOL_GET_SEGMENT_FACTS,
    ToolCost,
    ToolSpec,
)
from app.services.sources.fact_scope import (
    SCOPE_TYPE_GROUP,
    SCOPE_TYPE_SEGMENT,
    FactScope,
)
from app.services.sources.financial_period import (
    PERIOD_TYPE_ANNUAL,
    PERIOD_TYPE_HALF,
    PERIOD_TYPE_QUARTER,
    PERIOD_TYPE_SPLIT_YEAR,
    parse_period,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

# ── What a fact tool will return ─────────────────────────────────────────── #

#: Only a validated fact is a fact. ``excerpt_only`` is retained text and
#: ``rejected`` is a recorded failure; neither is a figure.
VALIDATION_VALIDATED = "validated"

#: The four values ``scope`` accepts. There is deliberately **no default**: the
#: report layer's implicit-Group convention is right for a rendered report and wrong
#: for a tool, and a default is how the wrong one would be inherited.
SCOPE_GROUP = SCOPE_TYPE_GROUP
SCOPE_SEGMENT = SCOPE_TYPE_SEGMENT
SCOPE_UNKNOWN = "unknown"
SCOPE_ANY = "any"

SCOPE_ARGUMENTS: frozenset[str] = frozenset(
    {SCOPE_GROUP, SCOPE_SEGMENT, SCOPE_UNKNOWN, SCOPE_ANY}
)

SERIES_PERIOD_TYPES: frozenset[str] = frozenset(
    {PERIOD_TYPE_ANNUAL, PERIOD_TYPE_HALF, PERIOD_TYPE_QUARTER, PERIOD_TYPE_SPLIT_YEAR}
)

#: A bound on rows a single call may return, so a tool cannot become a table scan an
#: agent pays for in tokens.
DEFAULT_LIMIT = 100
MAX_LIMIT = 500


@dataclass
class FactView:
    """One fact, in the only shape that can be cited.

    Every semantic field travels with the number. ``value_text`` is the raw as-found
    string, kept beside ``value_numeric`` so a quote is the document's own words.
    """

    fact_id: str
    label: str
    value_numeric: float | None
    value_text: str | None
    unit: str | None
    currency: str | None
    scale: str | None
    period_key: str | None
    period_type: str | None
    period_label: str
    scope_type: str | None
    scope_name: str | None
    scope_key: str | None
    scope_label: str
    document_id: str
    page_number: int | None
    table_location: str | None
    confidence: float
    needs_human_review: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Population:
    """What this result counted, what it excluded, and what the limit could bite.

    Named after ``fact_count_scopes``'s rule: the point is not to make two numbers
    agree, it is to say which population each number counts. A result without this is
    a number a reader has to guess the meaning of.

    ``conflicted`` is kept separate from ``excluded`` on purpose. A fact returned under
    ``conflicts`` is **present**, not absent, and counting it as excluded would make
    ``returned + excluded`` describe a population that never existed — which is the
    exact failure this class is named after.

    ``filtered_after_query`` names the filters applied in Python rather than in SQL. It
    matters because the row limit is applied by the database, so a filter listed there
    ran on an already-truncated set. Period is the one that cannot move into SQL: the
    column holds the document's own label and the *key* is derived by ``parse_period``,
    so "2025" and "FY2025" are one key and two strings.
    """

    definition: str
    filters: dict[str, Any] = field(default_factory=dict)
    returned: int = 0
    excluded: dict[str, int] = field(default_factory=dict)
    conflicted: int = 0
    filtered_in_query: list[str] = field(default_factory=list)
    filtered_after_query: list[str] = field(default_factory=list)
    row_limit: int = 0

    def exclude(self, reason: str, count: int = 1) -> None:
        if count:
            self.excluded[reason] = self.excluded.get(reason, 0) + count

    def to_dict(self) -> dict[str, Any]:
        return {
            "definition": self.definition,
            "filters": dict(self.filters),
            "returned": self.returned,
            "excluded": dict(self.excluded),
            "excluded_total": sum(self.excluded.values()),
            "conflicted": self.conflicted,
            "filtered_in_query": list(self.filtered_in_query),
            "filtered_after_query": list(self.filtered_after_query),
            "row_limit": self.row_limit,
        }


def _view(row: ExtractedFact) -> FactView:
    period = parse_period(row.period)
    scope = FactScope(scope_type=row.scope_type, scope_name=row.scope_name)
    return FactView(
        fact_id=str(row.id),
        label=row.label,
        value_numeric=(
            float(row.value_numeric) if isinstance(row.value_numeric, Decimal | int | float)
            else None
        ),
        value_text=row.value_text,
        unit=row.unit,
        currency=row.currency,
        scale=row.scale,
        period_key=period.key,
        period_type=period.period_type,
        period_label=period.label(),
        scope_type=row.scope_type,
        scope_name=row.scope_name,
        scope_key=row.scope_key,
        scope_label=scope.human_label(),
        document_id=str(row.extracted_document_id),
        page_number=row.page_number,
        table_location=row.table_location,
        confidence=row.confidence,
        needs_human_review=row.needs_human_review,
    )


# ── Argument validation ──────────────────────────────────────────────────── #


def _require_scope(arguments: dict[str, Any]) -> str:
    raw = str(arguments.get("scope") or "").strip().lower()
    if raw not in SCOPE_ARGUMENTS:
        raise ValueError(
            "scope is required and must be one of "
            f"{', '.join(sorted(SCOPE_ARGUMENTS))}. There is no default on purpose: "
            "the report layer reads an absent scope as implicitly Group, which is "
            "right for a rendered report and would hand an agent a segment figure "
            "labelled Group."
        )
    return raw


def _require_company(arguments: dict[str, Any]) -> str:
    raw = str(arguments.get("company_id") or "").strip()
    if not raw:
        raise ValueError(
            "company_id is required. An unscoped fact query would return one issuer's "
            "figures under another's question."
        )
    try:
        uuid.UUID(raw)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"company_id {raw!r} is not a UUID.") from exc
    return raw


def _limit(arguments: dict[str, Any]) -> int:
    raw = arguments.get("limit") or DEFAULT_LIMIT
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"limit {raw!r} is not a number.") from exc
    return max(1, min(value, MAX_LIMIT))


def _labels(arguments: dict[str, Any]) -> list[str]:
    raw = arguments.get("labels") or []
    if isinstance(raw, str):
        raw = [raw]
    if not isinstance(raw, list | tuple):
        raise ValueError("labels must be a list of strings.")
    return [str(item).strip() for item in raw if str(item).strip()]


def validate_get_financial_facts(arguments: dict[str, Any]) -> dict[str, Any]:
    period_keys = arguments.get("period_keys") or []
    if isinstance(period_keys, str):
        period_keys = [period_keys]
    return {
        "company_id": _require_company(arguments),
        "scope": _require_scope(arguments),
        "labels": _labels(arguments),
        "period_keys": [str(p).strip() for p in period_keys if str(p).strip()],
        "scope_keys": [
            str(s).strip() for s in (arguments.get("scope_keys") or []) if str(s).strip()
        ],
        "limit": _limit(arguments),
    }


def validate_get_financial_series(arguments: dict[str, Any]) -> dict[str, Any]:
    label = str(arguments.get("label") or "").strip()
    if not label:
        raise ValueError("label is required: a series is a series OF something.")
    period_type = str(arguments.get("period_type") or "").strip().lower()
    if period_type not in SERIES_PERIOD_TYPES:
        raise ValueError(
            "period_type is required and must be one of "
            f"{', '.join(sorted(SERIES_PERIOD_TYPES))}. There is no default: annual is "
            "not interim, and a series that mixes them looks continuous and is not."
        )
    return {
        "company_id": _require_company(arguments),
        "scope": _require_scope(arguments),
        "label": label,
        "period_type": period_type,
        "scope_key": str(arguments.get("scope_key") or "").strip() or None,
        "limit": _limit(arguments),
    }


def validate_get_segment_facts(arguments: dict[str, Any]) -> dict[str, Any]:
    period_keys = arguments.get("period_keys") or []
    if isinstance(period_keys, str):
        period_keys = [period_keys]
    return {
        "company_id": _require_company(arguments),
        "labels": _labels(arguments),
        "period_keys": [str(p).strip() for p in period_keys if str(p).strip()],
        "limit": _limit(arguments),
    }


# ── The query all three share ────────────────────────────────────────────── #


def _base_conditions(company_id: str, labels: list[str]) -> list[Any]:
    """The two filters no tool may forget, plus the company and any label narrowing.

    ``is_active`` exists because a superseded derivation was being mixed with a current
    one as evidence. ``validation_status`` exists because ``excerpt_only`` is retained
    text and promoting it to a figure is fabrication with a database behind it.
    """
    conditions: list[Any] = [
        ExtractedDocument.company_id == uuid.UUID(company_id),
        ExtractedFact.is_active.is_(True),
        ExtractedFact.validation_status == VALIDATION_VALIDATED,
    ]
    if labels:
        conditions.append(ExtractedFact.label.in_(labels))
    return conditions


def _scope_condition(scope: str) -> Any | None:
    """The scope predicate, as SQL.

    It has to be SQL rather than a Python filter after the fetch, because the row limit
    is applied by the database: filtering afterwards would mean a company with 200
    Group facts and 5 segment facts returns **zero** segment facts for a limit of 100.
    The limit must bound the population the caller asked for, not the one before it.
    """
    if scope == SCOPE_ANY:
        return None
    if scope == SCOPE_UNKNOWN:
        return ExtractedFact.scope_type.is_(None)
    return ExtractedFact.scope_type == scope


async def _active_validated_facts(
    session: Any,
    *,
    company_id: str,
    labels: list[str],
    limit: int,
    scope: str = SCOPE_ANY,
    scope_keys: list[str] | None = None,
) -> list[ExtractedFact]:
    """Active, validated facts for one company, scope-filtered in the query."""
    stmt = (
        select(ExtractedFact)
        .join(
            ExtractedDocument,
            ExtractedDocument.id == ExtractedFact.extracted_document_id,
        )
        .where(*_base_conditions(company_id, labels))
        .order_by(ExtractedFact.label, ExtractedFact.period, ExtractedFact.created_at)
        .limit(limit)
    )
    condition = _scope_condition(scope)
    if condition is not None:
        stmt = stmt.where(condition)
    if scope_keys:
        stmt = stmt.where(ExtractedFact.scope_key.in_(scope_keys))
    return list((await session.execute(stmt)).scalars().all())


async def _scope_census(
    session: Any, *, company_id: str, labels: list[str]
) -> dict[str | None, int]:
    """How many active validated facts exist per scope type.

    A separate, unlimited COUNT rather than an inference from the fetched rows: once the
    scope filter is in SQL the excluded rows are simply not there, and reporting
    "0 excluded" would be a fabricated measurement of a population never examined.
    """
    rows = (
        await session.execute(
            select(ExtractedFact.scope_type, func.count())
            .join(
                ExtractedDocument,
                ExtractedDocument.id == ExtractedFact.extracted_document_id,
            )
            .where(*_base_conditions(company_id, labels))
            .group_by(ExtractedFact.scope_type)
        )
    ).all()
    return {scope_type: int(count) for scope_type, count in rows}


def _record_scope_exclusions(
    population: Population, census: dict[str | None, int], scope: str
) -> None:
    """Report what the scope filter left behind, from the census."""
    if scope == SCOPE_ANY:
        return
    for scope_type, count in census.items():
        matches = scope_type is None if scope == SCOPE_UNKNOWN else scope_type == scope
        if not matches:
            population.exclude(f"scope_is_not_{scope}", count)


async def _count_other_scope_keys(
    session: Any,
    *,
    company_id: str,
    labels: list[str],
    scope: str,
    scope_keys: list[str],
) -> int:
    """How many in-scope facts a ``scope_key`` narrowing left behind.

    Counted rather than inferred, for the same reason as the scope census: once the
    narrowing is in SQL the other segments' rows are simply absent, and omitting the
    count would silently understate what the query passed over. "Three other segments
    also report this label" is exactly the sort of thing an agent should be told.
    """
    if not scope_keys:
        return 0
    conditions = _base_conditions(company_id, labels)
    scope_condition = _scope_condition(scope)
    stmt = select(func.count()).select_from(ExtractedFact).join(
        ExtractedDocument, ExtractedDocument.id == ExtractedFact.extracted_document_id
    ).where(*conditions, ExtractedFact.scope_key.notin_(scope_keys))
    if scope_condition is not None:
        stmt = stmt.where(scope_condition)
    return int((await session.execute(stmt)).scalar_one() or 0)


# ── The tools ────────────────────────────────────────────────────────────── #


async def _get_financial_facts(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    scope = arguments["scope"]
    population = Population(
        definition=(
            "active, validated ExtractedFact rows for this company, filtered to the "
            f"requested scope ({scope})"
        ),
        filters={
            "company_id": arguments["company_id"],
            "scope": scope,
            "labels": arguments["labels"],
            "period_keys": arguments["period_keys"],
            "is_active": True,
            "validation_status": VALIDATION_VALIDATED,
        },
    )
    population.row_limit = arguments["limit"]
    population.filtered_in_query = ["company_id", "is_active", "validation_status",
                                    "labels", "scope", "scope_keys"]
    population.filtered_after_query = ["period_keys"] if arguments["period_keys"] else []
    rows = await _active_validated_facts(
        context.session,
        company_id=arguments["company_id"],
        labels=arguments["labels"],
        limit=arguments["limit"],
        scope=scope,
        scope_keys=arguments["scope_keys"],
    )
    _record_scope_exclusions(
        population,
        await _scope_census(
            context.session,
            company_id=arguments["company_id"],
            labels=arguments["labels"],
        ),
        scope,
    )
    population.exclude(
        "scope_key_not_requested",
        await _count_other_scope_keys(
            context.session,
            company_id=arguments["company_id"],
            labels=arguments["labels"],
            scope=scope,
            scope_keys=arguments["scope_keys"],
        ),
    )

    wanted_periods = set(arguments["period_keys"])
    items: list[dict[str, Any]] = []
    for row in rows:
        view = _view(row)
        if wanted_periods and (view.period_key or "") not in wanted_periods:
            population.exclude("period_not_requested")
            continue
        items.append(view.to_dict())

    population.returned = len(items)
    return {
        "items": items,
        "population": population.to_dict(),
        "summary": (
            f"{len(items)} validated fact(s), scope={scope}, "
            f"{population.to_dict()['excluded_total']} excluded"
        ),
        "contains_untrusted_content": False,
    }


async def _get_financial_series(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    scope = arguments["scope"]
    period_type = arguments["period_type"]
    population = Population(
        definition=(
            f"active, validated {arguments['label']!r} facts for this company at scope "
            f"{scope}, restricted to {period_type} periods and ordered within that type"
        ),
        filters={
            "company_id": arguments["company_id"],
            "label": arguments["label"],
            "scope": scope,
            "scope_key": arguments["scope_key"],
            "period_type": period_type,
            "is_active": True,
            "validation_status": VALIDATION_VALIDATED,
        },
    )
    population.row_limit = arguments["limit"]
    population.filtered_in_query = ["company_id", "is_active", "validation_status",
                                    "label", "scope", "scope_key"]
    population.filtered_after_query = ["period_type", "period_known"]
    rows = await _active_validated_facts(
        context.session,
        company_id=arguments["company_id"],
        labels=[arguments["label"]],
        limit=arguments["limit"],
        scope=scope,
        scope_keys=[arguments["scope_key"]] if arguments["scope_key"] else None,
    )
    _record_scope_exclusions(
        population,
        await _scope_census(
            context.session,
            company_id=arguments["company_id"],
            labels=[arguments["label"]],
        ),
        scope,
    )
    population.exclude(
        "scope_key_not_requested",
        await _count_other_scope_keys(
            context.session,
            company_id=arguments["company_id"],
            labels=[arguments["label"]],
            scope=scope,
            scope_keys=[arguments["scope_key"]] if arguments["scope_key"] else [],
        ),
    )

    by_period: dict[str, list[FactView]] = {}
    for row in rows:
        period = parse_period(row.period)
        if period.is_unknown:
            # A series is an ordering, and an unknown period cannot be ordered. It is
            # excluded and COUNTED, never quietly dropped.
            population.exclude("period_unknown")
            continue
        if period.period_type != period_type:
            population.exclude(f"period_type_is_not_{period_type}")
            continue
        key = period.key or ""
        by_period.setdefault(key, []).append(_view(row))

    points: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for key in sorted(by_period, key=lambda k: parse_period(k).sort_key):
        views = by_period[key]
        if len(views) == 1:
            points.append(views[0].to_dict())
            continue
        # Two facts for one period is a CONFLICT, not a series point. Both are
        # returned; choosing one silently is how a contradiction becomes a number.
        conflicts.append(
            {
                "period_key": key,
                "values": [v.to_dict() for v in views],
                "reason": (
                    f"{len(views)} validated facts for {key} at this scope; the "
                    "platform does not choose between them"
                ),
            }
        )

    population.returned = len(points)
    # Counted as CONFLICTED, not excluded: those facts are returned under `conflicts`,
    # so calling them excluded would make `returned + excluded` describe a population
    # that never existed.
    population.conflicted = sum(
        len(by_period[k]) for k in by_period if len(by_period[k]) > 1
    )
    return {
        "items": points,
        "conflicts": conflicts,
        "population": population.to_dict(),
        "summary": (
            f"{len(points)} {period_type} point(s) for {arguments['label']!r}, "
            f"{len(conflicts)} conflicting period(s)"
        ),
        "contains_untrusted_content": False,
    }


async def _get_segment_facts(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    population = Population(
        definition=(
            "active, validated ExtractedFact rows for this company whose scope is a "
            "named segment, grouped by scope_key"
        ),
        filters={
            "company_id": arguments["company_id"],
            "labels": arguments["labels"],
            "period_keys": arguments["period_keys"],
            "scope": SCOPE_SEGMENT,
            "is_active": True,
            "validation_status": VALIDATION_VALIDATED,
        },
    )
    population.row_limit = arguments["limit"]
    population.filtered_in_query = ["company_id", "is_active", "validation_status",
                                    "labels", "scope"]
    population.filtered_after_query = ["period_keys"] if arguments["period_keys"] else []
    rows = await _active_validated_facts(
        context.session,
        company_id=arguments["company_id"],
        labels=arguments["labels"],
        limit=arguments["limit"],
        scope=SCOPE_SEGMENT,
    )
    _record_scope_exclusions(
        population,
        await _scope_census(
            context.session,
            company_id=arguments["company_id"],
            labels=arguments["labels"],
        ),
        SCOPE_SEGMENT,
    )

    wanted_periods = set(arguments["period_keys"])
    # Grouped on scope_key — the FactScope identity — so "Specialist Watchmakers" and
    # "SPECIALIST WATCHMAKERS" are one segment. The distinct as-printed names are kept
    # inside the group, because the exact wording is what a citation quotes.
    groups: dict[str, dict[str, Any]] = {}
    total = 0
    for row in rows:
        view = _view(row)
        if wanted_periods and (view.period_key or "") not in wanted_periods:
            population.exclude("period_not_requested")
            continue
        key = row.scope_key or f"segment:{(row.scope_name or '').casefold()}"
        group = groups.setdefault(
            key,
            {"scope_key": key, "reported_names": [], "facts": []},
        )
        if row.scope_name and row.scope_name not in group["reported_names"]:
            group["reported_names"].append(row.scope_name)
        group["facts"].append(view.to_dict())
        total += 1

    items = [groups[key] for key in sorted(groups)]
    population.returned = total
    return {
        "items": items,
        "fact_count": total,
        "population": population.to_dict(),
        "summary": f"{len(items)} segment(s), {total} validated fact(s)",
        "contains_untrusted_content": False,
    }


GET_FINANCIAL_FACTS_SPEC = ToolSpec(
    name=TOOL_GET_FINANCIAL_FACTS,
    description=(
        "Active, validated financial facts for one company at an explicitly requested "
        "scope. Every fact carries its period, scope, unit, currency and scale, plus "
        "the raw as-found text. `scope` is required — there is no default."
    ),
    handler=_get_financial_facts,
    validate_arguments=validate_get_financial_facts,
    cost=ToolCost(),
    instrumented_units=(),
)

GET_FINANCIAL_SERIES_SPEC = ToolSpec(
    name=TOOL_GET_FINANCIAL_SERIES,
    description=(
        "One label's values over time, within ONE period type. `period_type` and "
        "`scope` are both required. Unknown periods are excluded and counted; two "
        "values for one period are returned as a conflict, never resolved."
    ),
    handler=_get_financial_series,
    validate_arguments=validate_get_financial_series,
    cost=ToolCost(),
    instrumented_units=(),
)

GET_SEGMENT_FACTS_SPEC = ToolSpec(
    name=TOOL_GET_SEGMENT_FACTS,
    description=(
        "Validated facts whose scope is a named business segment, grouped by scope "
        "identity. Group figures are never included."
    ),
    handler=_get_segment_facts,
    validate_arguments=validate_get_segment_facts,
    cost=ToolCost(),
    instrumented_units=(),
)


def register_fact_tools(registry: "ToolRegistry") -> "ToolRegistry":
    registry.register(GET_FINANCIAL_FACTS_SPEC)
    registry.register(GET_FINANCIAL_SERIES_SPEC)
    registry.register(GET_SEGMENT_FACTS_SPEC)
    return registry


__all__ = [
    "DEFAULT_LIMIT",
    "GET_FINANCIAL_FACTS_SPEC",
    "GET_FINANCIAL_SERIES_SPEC",
    "GET_SEGMENT_FACTS_SPEC",
    "MAX_LIMIT",
    "SCOPE_ANY",
    "SCOPE_ARGUMENTS",
    "SCOPE_GROUP",
    "SCOPE_SEGMENT",
    "SCOPE_UNKNOWN",
    "SERIES_PERIOD_TYPES",
    "VALIDATION_VALIDATED",
    "FactView",
    "Population",
    "register_fact_tools",
    "validate_get_financial_facts",
    "validate_get_financial_series",
    "validate_get_segment_facts",
]
