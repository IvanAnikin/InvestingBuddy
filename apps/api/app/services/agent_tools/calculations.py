"""``get_calculated_metrics`` — V3.3 Slice 3.3.

Binds facts to a calculation definition, runs the engine, persists the outcome and
returns it. The tool's job is **binding**, not arithmetic: choosing which fact fills
which role is where a wrong answer comes from, and the engine is what refuses the
combinations that binding cannot make safe.

IT NEVER PICKS BETWEEN TWO CANDIDATES
=====================================
When two active validated facts could fill one role for one period and scope, that is
the conflict ``get_financial_series`` already reports, and it is refused here for the
same reason: the platform does not choose between two figures it cannot reconcile.
Silently taking the first would make an unresolved contradiction into a number.

EVERY ATTEMPT IS RECORDED, INCLUDING THE REFUSALS
=================================================
A run that computed three metrics and refused seven has told the reader something
specific about the *extraction* layer, and none of it is visible from the three.
"""

from __future__ import annotations

import uuid
from decimal import Decimal, InvalidOperation
from typing import TYPE_CHECKING, Any

from sqlalchemy import select

from app.models.calculation import CalculationRecord
from app.models.extracted_document import ExtractedDocument, ExtractedFact
from app.services.agent_tools.contracts import (
    TOOL_GET_CALCULATED_METRICS,
    ToolCost,
    ToolSpec,
)
from app.services.agent_tools.facts import (
    SCOPE_ANY,
    SCOPE_ARGUMENTS,
    SCOPE_GROUP,
    SCOPE_SEGMENT,
    VALIDATION_VALIDATED,
    Population,
)
from app.services.calculations.definitions import (
    DEFINITIONS,
    PERIOD_TWO_OF_ONE_TYPE,
    SCOPE_SEGMENT_OVER_GROUP,
    CalculationDefinition,
    definition_for,
)
from app.services.calculations.engine import (
    REFUSED_MISSING_INPUT,
    STATUS_REFUSED,
    CalculationOutcome,
    calculate,
)
from app.services.calculations.quantities import Quantity
from app.services.sources.fact_scope import SCOPE_TYPE_GROUP, SCOPE_TYPE_SEGMENT, FactScope
from app.services.sources.financial_period import parse_period

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

#: A bound on definitions per call, so one request cannot become an unbounded batch.
MAX_METRICS_PER_CALL = 10

#: Refused because two facts could fill one role and the platform will not choose.
REFUSED_AMBIGUOUS_INPUT = "ambiguous_input"


def validate_get_calculated_metrics(arguments: dict[str, Any]) -> dict[str, Any]:
    company_id = str(arguments.get("company_id") or "").strip()
    if not company_id:
        raise ValueError("company_id is required.")
    try:
        uuid.UUID(company_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"company_id {company_id!r} is not a UUID.") from exc

    raw = arguments.get("metrics") or []
    if isinstance(raw, str):
        raw = [raw]
    metrics = [str(m).strip() for m in raw if str(m).strip()]
    if not metrics:
        raise ValueError(
            "metrics is required and must name at least one definition. Known: "
            f"{', '.join(sorted(DEFINITIONS))}."
        )
    unknown = [m for m in metrics if m not in DEFINITIONS]
    if unknown:
        raise ValueError(
            f"unknown metric(s): {', '.join(unknown)}. The set is closed; known: "
            f"{', '.join(sorted(DEFINITIONS))}."
        )
    if len(metrics) > MAX_METRICS_PER_CALL:
        raise ValueError(
            f"{len(metrics)} metrics requested; the limit is {MAX_METRICS_PER_CALL}."
        )

    scope = str(arguments.get("scope") or "").strip().lower()
    if scope not in SCOPE_ARGUMENTS or scope == SCOPE_ANY:
        raise ValueError(
            "scope is required and must be 'group', 'segment' or 'unknown'. 'any' is "
            "not available: arithmetic over a mixture of scopes is what this engine "
            "exists to refuse."
        )
    period_key = str(arguments.get("period_key") or "").strip()
    return {
        "company_id": company_id,
        "metrics": metrics,
        "scope": scope,
        "period_key": period_key or None,
        "scope_key": str(arguments.get("scope_key") or "").strip() or None,
        "start_period_key": str(arguments.get("start_period_key") or "").strip() or None,
        "end_period_key": str(arguments.get("end_period_key") or "").strip() or None,
        "persist": bool(arguments.get("persist", True)),
    }


async def _candidate_facts(
    session: Any, *, company_id: str, labels: set[str]
) -> list[ExtractedFact]:
    stmt = (
        select(ExtractedFact)
        .join(
            ExtractedDocument,
            ExtractedDocument.id == ExtractedFact.extracted_document_id,
        )
        .where(
            ExtractedDocument.company_id == uuid.UUID(company_id),
            ExtractedFact.is_active.is_(True),
            ExtractedFact.validation_status == VALIDATION_VALIDATED,
            ExtractedFact.label.in_(sorted(labels)),
            ExtractedFact.value_numeric.isnot(None),
        )
        .order_by(ExtractedFact.label, ExtractedFact.created_at)
    )
    return list((await session.execute(stmt)).scalars().all())


def _quantity(row: ExtractedFact) -> Quantity | None:
    try:
        value = Decimal(str(row.value_numeric))
    except (InvalidOperation, TypeError):  # pragma: no cover - guarded by the query
        return None
    return Quantity(
        value=value,
        unit=row.unit or "",
        currency=row.currency,
        scale=row.scale,
        period=parse_period(row.period),
        scope=FactScope(scope_type=row.scope_type, scope_name=row.scope_name),
        fact_id=str(row.id),
        label=row.label,
    )


def _scope_filter(scope: str) -> str | None:
    if scope == SCOPE_GROUP:
        return SCOPE_TYPE_GROUP
    if scope == SCOPE_SEGMENT:
        return SCOPE_TYPE_SEGMENT
    return None


def _bind(
    definition: CalculationDefinition,
    rows: list[ExtractedFact],
    arguments: dict[str, Any],
) -> tuple[dict[str, Quantity], str | None, str]:
    """Choose which fact fills which role, or say why it cannot.

    Returns ``(inputs, refusal_reason, detail)``. Two candidates for one role is a
    refusal, never a choice: the platform does not decide between two figures it cannot
    reconcile, and taking the first would turn an unresolved contradiction into a number.
    """
    scope = arguments["scope"]
    wanted_scope = _scope_filter(scope)
    scope_key = arguments["scope_key"]

    def matching(labels: tuple[str, ...], *, period: str | None,
                 scope_type: str | None, require_scope_key: bool) -> list[ExtractedFact]:
        out: list[ExtractedFact] = []
        for row in rows:
            if row.label not in labels:
                continue
            if scope_type is None:
                if row.scope_type is not None and scope == "unknown":
                    continue
            elif row.scope_type != scope_type:
                continue
            if require_scope_key and scope_key and (row.scope_key or "") != scope_key:
                continue
            if period is not None and (parse_period(row.period).key or "") != period:
                continue
            out.append(row)
        return out

    inputs: dict[str, Quantity] = {}
    for spec in definition.inputs:
        if definition.period_rule == PERIOD_TWO_OF_ONE_TYPE:
            period = (
                arguments["start_period_key"]
                if spec.role == "start"
                else arguments["end_period_key"]
            )
            if not period:
                return (
                    inputs,
                    REFUSED_MISSING_INPUT,
                    f"{definition.key} needs start_period_key and end_period_key",
                )
        else:
            period = arguments["period_key"]

        role_scope: str | None
        if definition.scope_rule == SCOPE_SEGMENT_OVER_GROUP:
            # The one cross-scope definition: the numerator is a segment and the
            # denominator is Group, so each role gets its own scope.
            role_scope = (
                SCOPE_TYPE_SEGMENT if spec.role == "segment_revenue" else SCOPE_TYPE_GROUP
            )
            require_key = spec.role == "segment_revenue"
        else:
            role_scope = wanted_scope
            require_key = True

        candidates = matching(
            spec.labels, period=period, scope_type=role_scope,
            require_scope_key=require_key,
        )
        if not candidates:
            return (
                inputs,
                REFUSED_MISSING_INPUT,
                f"no active validated fact fills {spec.role} "
                f"(labels {list(spec.labels)}, period {period or 'any'}, scope "
                f"{role_scope or 'unknown'})",
            )
        if len(candidates) > 1:
            values = sorted({str(c.value_numeric) for c in candidates})
            if len(values) > 1:
                return (
                    inputs,
                    REFUSED_AMBIGUOUS_INPUT,
                    f"{len(candidates)} facts could fill {spec.role} with differing "
                    f"values {values}; the platform does not choose between two figures "
                    "it cannot reconcile",
                )
        quantity = _quantity(candidates[0])
        if quantity is None:  # pragma: no cover - guarded by the query
            return inputs, REFUSED_MISSING_INPUT, f"{spec.role} has no numeric value"
        inputs[spec.role] = quantity
    return inputs, None, ""


async def _get_calculated_metrics(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    population = Population(
        definition=(
            "one calculation per requested metric, bound from active validated facts "
            f"at scope {arguments['scope']}"
        ),
        filters={
            "company_id": arguments["company_id"],
            "metrics": arguments["metrics"],
            "scope": arguments["scope"],
            "scope_key": arguments["scope_key"],
            "period_key": arguments["period_key"],
            "is_active": True,
            "validation_status": VALIDATION_VALIDATED,
        },
        filtered_in_query=["company_id", "is_active", "validation_status", "labels"],
        filtered_after_query=["scope", "scope_key", "period_key"],
    )

    definitions = [definition_for(key) for key in arguments["metrics"]]
    labels = {label for d in definitions for spec in d.inputs for label in spec.labels}
    rows = await _candidate_facts(
        context.session, company_id=arguments["company_id"], labels=labels
    )

    computed: list[dict[str, Any]] = []
    refused: list[dict[str, Any]] = []
    for definition in definitions:
        inputs, reason, detail = _bind(definition, rows, arguments)
        if reason is not None:
            outcome = CalculationOutcome(
                definition_key=definition.key,
                definition_version=definition.version,
                status=STATUS_REFUSED,
                refusal_reason=reason,
                detail=detail,
                inputs={role: q.to_dict() for role, q in inputs.items()},
                input_fact_ids=[q.fact_id for q in inputs.values() if q.fact_id],
                formula=definition.formula,
            )
        else:
            outcome = calculate(definition, inputs)

        if arguments["persist"]:
            await _persist(context, outcome, arguments)
        (computed if outcome.computed else refused).append(outcome.to_dict())

    population.returned = len(computed)
    for entry in refused:
        population.exclude(str(entry.get("refusal_reason") or "refused"))

    return {
        "items": computed,
        "refused": refused,
        "population": population.to_dict(),
        "summary": (
            f"{len(computed)} computed, {len(refused)} refused "
            f"({', '.join(sorted({str(r.get('refusal_reason')) for r in refused}))})"
            if refused
            else f"{len(computed)} computed, 0 refused"
        ),
        "contains_untrusted_content": False,
    }


async def _persist(
    context: "ToolContext", outcome: CalculationOutcome, arguments: dict[str, Any]
) -> None:
    """Store the attempt. Refusals included — see the module docstring."""
    record = CalculationRecord(
        id=uuid.uuid4(),
        company_id=uuid.UUID(arguments["company_id"]),
        legal_entity_id=context.legal_entity_id,
        definition_key=outcome.definition_key,
        definition_version=outcome.definition_version,
        formula=(outcome.formula or None),
        status=outcome.status,
        value=outcome.value,
        result_unit=outcome.result_unit,
        result_currency=outcome.result_currency,
        result_scale=outcome.result_scale,
        period_key=outcome.period_key,
        period_type=outcome.period_type,
        scope_type=outcome.scope_type,
        scope_key=outcome.scope_key,
        refusal_reason=outcome.refusal_reason,
        detail=outcome.detail[:500] if outcome.detail else None,
        inputs_json=outcome.inputs or None,
        input_fact_ids_json=list(outcome.input_fact_ids) or None,
    )
    context.session.add(record)
    await context.session.flush()
    outcome.calculation_id = record.id


GET_CALCULATED_METRICS_SPEC = ToolSpec(
    name=TOOL_GET_CALCULATED_METRICS,
    description=(
        "Compute named deterministic metrics from this company's validated facts. "
        "`scope` is required and 'any' is not available. A calculation whose inputs "
        "span periods, scopes, currencies or incompatible scales is REFUSED with a "
        "reason, and the refusal is returned alongside what was computed."
    ),
    handler=_get_calculated_metrics,
    validate_arguments=validate_get_calculated_metrics,
    cost=ToolCost(),
    instrumented_units=(),
)


def register_calculation_tools(registry: "ToolRegistry") -> "ToolRegistry":
    registry.register(GET_CALCULATED_METRICS_SPEC)
    return registry


__all__ = [
    "GET_CALCULATED_METRICS_SPEC",
    "MAX_METRICS_PER_CALL",
    "REFUSED_AMBIGUOUS_INPUT",
    "register_calculation_tools",
    "validate_get_calculated_metrics",
]
