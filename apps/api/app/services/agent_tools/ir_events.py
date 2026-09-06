"""IR event and transcript tools — V3.4 Slice 4.8.

Two more of the twelve names V3.3 reserved and could not implement.

WHAT THESE TOOLS SAY THAT A NAIVE ONE WOULD NOT
===============================================
``get_transcripts`` never returns an empty list to mean two different things. For every
event it names the transcript's **state**:

* ``held`` — ingested, citable;
* ``published`` — exists at a URL, not yet retrieved;
* ``not_published`` — the issuer published none. **A research gap**, and the reason
  [ADR-051](../../../docs/DECISIONS.md) is liveable: a human can close it by other means;
* ``paywalled`` — published behind a paywall, which this platform does not scrape;
* ``not_checked`` — nobody has looked. **Not** a research gap: a task.

An agent handed a bare empty list cannot tell "this company holds no calls" from "we
never looked", and would write the same sentence for both.
"""

from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

from app.services.agent_tools.contracts import (
    TOOL_GET_IR_EVENTS,
    TOOL_GET_TRANSCRIPTS,
    ToolCost,
    ToolSpec,
)
from app.services.ir_events import service as ir

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

DEFAULT_LIMIT = 20
MAX_LIMIT = 200


def _validate_common(arguments: dict[str, Any]) -> dict[str, Any]:
    raw = arguments.get("company_id")
    if not raw:
        raise ValueError("company_id is required: an IR event belongs to one issuer.")
    try:
        company_id = raw if isinstance(raw, uuid.UUID) else uuid.UUID(str(raw))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError("company_id must be a UUID") from exc
    limit = int(arguments.get("limit") or DEFAULT_LIMIT)
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    types = tuple(
        str(value).strip()
        for value in (arguments.get("event_types") or [])
        if str(value).strip()
    )
    unknown = set(types) - ir.EVENT_TYPES
    if unknown:
        raise ValueError(
            f"{sorted(unknown)} are not IR event types. Recognised: "
            f"{', '.join(sorted(ir.EVENT_TYPES))}."
        )
    periods = tuple(
        str(value).strip()
        for value in (arguments.get("period_keys") or [])
        if str(value).strip()
    )
    return {
        "company_id": company_id,
        "limit": limit,
        "event_types": types,
        "period_keys": periods,
    }


def validate_get_ir_events(arguments: dict[str, Any]) -> dict[str, Any]:
    return _validate_common(arguments)


def validate_get_transcripts(arguments: dict[str, Any]) -> dict[str, Any]:
    validated = _validate_common(arguments)
    # A transcript belongs to an event that produced speech. Defaulting to every event
    # type would report "no transcript" for an AGM notice, which is not a finding.
    if not validated["event_types"]:
        validated["event_types"] = (
            ir.EVENT_EARNINGS_CALL,
            ir.EVENT_CAPITAL_MARKETS_DAY,
            ir.EVENT_INVESTOR_DAY,
        )
    return validated


def _event_payload(event: Any) -> dict[str, Any]:
    return {
        "event_id": str(event.id),
        "event_type": event.event_type,
        "title": event.title,
        "fiscal_period_key": event.fiscal_period_key,
        "period_type": event.period_type,
        "status": event.status,
        "scheduled_at": (
            event.scheduled_at.isoformat() if event.scheduled_at else None
        ),
        "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
        "source_url": event.source_url,
    }


async def _get_ir_events(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    events = await ir.events_for_company(
        context.session,
        arguments["company_id"],
        event_types=arguments["event_types"],
        period_keys=arguments["period_keys"],
        limit=arguments["limit"],
    )
    return {
        "items": [_event_payload(event) for event in events],
        "population": {
            "definition": "IR events recorded for this company",
            "filters": {
                "company_id": str(arguments["company_id"]),
                "event_types": list(arguments["event_types"]),
                "period_keys": list(arguments["period_keys"]),
            },
            "row_limit": arguments["limit"],
            "returned": len(events),
        },
        "summary": (
            f"{len(events)} IR event(s) on record. An empty result means none has been "
            "recorded, which is a statement about this platform's coverage and not "
            "about the issuer's calendar."
        ),
        "contains_untrusted_content": False,
    }


async def _get_transcripts(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    events = await ir.events_for_company(
        context.session,
        arguments["company_id"],
        event_types=arguments["event_types"],
        period_keys=arguments["period_keys"],
        limit=arguments["limit"],
    )
    items: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    for event in events:
        state = await ir.transcript_state(context.session, event)
        entry = {
            **_event_payload(event),
            "transcript_state": state.state,
            "transcript_url": state.url,
            "research_document_version_id": (
                str(state.research_document_version_id)
                if state.research_document_version_id
                else None
            ),
            "checked_at": state.checked_at.isoformat() if state.checked_at else None,
            "note": state.note,
        }
        items.append(entry)
        if state.is_a_research_gap:
            gaps.append(
                {
                    "event_id": str(event.id),
                    "fiscal_period_key": event.fiscal_period_key,
                    "state": state.state,
                    "why_it_matters": (
                        "Management language for this period cannot be read. The event "
                        "is on record and no transcript is obtainable from a free "
                        "public source."
                    ),
                }
            )
    held = sum(1 for entry in items if entry["transcript_state"] == "held")
    return {
        "items": items,
        "held_count": held,
        "research_gaps": gaps,
        "summary": (
            f"{held} of {len(items)} event(s) have an ingested transcript; "
            f"{len(gaps)} are a research gap (published none, or paywalled). "
            "A state of 'not_checked' is a task, not a gap."
        ),
        # A transcript is issuer-published text. Once one is ingested and quoted back,
        # the payload carries content from outside the platform.
        "contains_untrusted_content": True,
    }


GET_IR_EVENTS_SPEC = ToolSpec(
    name=TOOL_GET_IR_EVENTS,
    description=(
        "IR events on record for one issuer — earnings calls, results releases, "
        "capital-markets days — with the issuer's own reporting period, not a calendar "
        "one. An empty result is a statement about this platform's coverage."
    ),
    handler=_get_ir_events,
    validate_arguments=validate_get_ir_events,
    cost=ToolCost(),
    instrumented_units=(),
)

GET_TRANSCRIPTS_SPEC = ToolSpec(
    name=TOOL_GET_TRANSCRIPTS,
    description=(
        "Transcript availability per IR event, as a STATE: held, published, "
        "not_published, paywalled or not_checked. 'The issuer published none' is "
        "returned as a research gap; 'nobody looked' is not, because it is a task."
    ),
    handler=_get_transcripts,
    validate_arguments=validate_get_transcripts,
    cost=ToolCost(),
    instrumented_units=(),
    may_contain_untrusted_content=True,
)


def register_ir_event_tools(registry: "ToolRegistry") -> "ToolRegistry":
    registry.register(GET_IR_EVENTS_SPEC)
    registry.register(GET_TRANSCRIPTS_SPEC)
    return registry


__all__ = [
    "DEFAULT_LIMIT",
    "GET_IR_EVENTS_SPEC",
    "GET_TRANSCRIPTS_SPEC",
    "MAX_LIMIT",
    "register_ir_event_tools",
    "validate_get_ir_events",
    "validate_get_transcripts",
]
