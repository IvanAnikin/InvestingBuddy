"""The builtin tools — V3.3 Slice 3.1.

``lookup_entity`` is here because it is the tool whose contract most needed
establishing before anything else was written against the machinery, and because it
needed no new capability: ``entities.resolution.resolve`` already returns a state.

The fact and series tools live in ``facts`` (slice 3.2) and are registered from here so
there is one place that answers "what can an agent reach". The calculation engine is
3.3, corpus search 3.4, and ``search_web`` / ``fetch_public_source`` wait for the
provider runtime in V3.4.

``lookup_entity`` RETURNS THE STATE, NOT A BEST MATCH
====================================================
This is the whole reason it is the first tool. A tool that handed an agent an
``ambiguous`` result as if it were resolved would undo the entirety of V3.2 — every
partial unique index and every refusal in the resolver exists to stop precisely that
attribution. So the payload has **no ``entity`` key at all** unless the state is
actionable, and candidates are returned under a separate key that cannot be mistaken
for an answer. An agent that wants to act on an entity has to find one where the
platform is prepared to say there is one.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.agent_tools.contracts import (
    TOOL_LOOKUP_ENTITY,
    ToolCost,
    ToolSpec,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

_MAX_CANDIDATES = 5


def _validate_lookup_entity(arguments: dict[str, Any]) -> dict[str, Any]:
    """Require at least one identifying input, and refuse a bare name-only lookup?

    No — a name-only lookup is *allowed*, because the honest answer to it is
    ``ambiguous`` and an agent is entitled to learn that. What is refused is a lookup
    with nothing in it at all, which is not a question.
    """
    ticker = str(arguments.get("ticker") or "").strip()
    exchange = str(arguments.get("exchange") or "").strip()
    legal_name = str(arguments.get("legal_name") or "").strip()
    identifiers = arguments.get("identifiers") or {}
    if not isinstance(identifiers, dict):
        raise ValueError("identifiers must be a mapping of scheme to value.")
    if not (ticker or legal_name or identifiers):
        raise ValueError(
            "lookup_entity needs a ticker, a legal name or an identifier. A lookup "
            "with nothing in it is not a question."
        )
    return {
        "ticker": ticker or None,
        "exchange": exchange or None,
        "legal_name": legal_name or None,
        "identifiers": {str(k): str(v) for k, v in identifiers.items()},
    }


async def _lookup_entity(context: "ToolContext", arguments: dict[str, Any]) -> dict[str, Any]:
    """Resolve an entity and report the STATE.

    Imported inside the handler so the tool surface does not drag the entity master
    into every module that merely enumerates tool names.
    """
    from app.services.entities.resolution import EntityQuery, resolve

    outcome = await resolve(
        context.session,
        EntityQuery(
            ticker=arguments.get("ticker"),
            exchange=arguments.get("exchange"),
            legal_name=arguments.get("legal_name"),
            identifiers=arguments.get("identifiers") or {},
        ),
        cfg=context.cfg,
    )

    payload: dict[str, Any] = {
        "resolution_state": outcome.state,
        "is_actionable": outcome.is_actionable,
        "reason": outcome.reason,
        # Candidates are ALWAYS under their own key, never promoted to an answer.
        "candidates": [
            {
                "legal_entity_id": str(candidate.entity.id),
                "legal_name": candidate.entity.legal_name,
                "entity_key": candidate.entity.entity_key,
                "jurisdiction": candidate.entity.jurisdiction,
                "best_evidence": candidate.best_strength,
                "evidence": [
                    {"kind": e.kind, "strength": e.strength, "detail": e.detail}
                    for e in candidate.evidence
                ],
            }
            for candidate in outcome.candidates[:_MAX_CANDIDATES]
        ],
        "invalid_inputs": [
            {"scheme": scheme, "problem": problem}
            for scheme, problem in outcome.invalid_inputs
        ],
        "summary": f"{outcome.state}: {outcome.reason}"[:400],
        # Nothing here comes from outside the platform.
        "contains_untrusted_content": False,
    }

    if outcome.is_actionable and outcome.entity is not None:
        # The ONLY branch that produces an `entity`. An agent reading this payload
        # cannot act on an unresolved issuer by accident, because there is nothing to
        # act on.
        payload["entity"] = {
            "legal_entity_id": str(outcome.entity.id),
            "legal_name": outcome.entity.legal_name,
            "entity_key": outcome.entity.entity_key,
            "jurisdiction": outcome.entity.jurisdiction,
            "entity_status": outcome.entity.entity_status,
        }
    return payload


LOOKUP_ENTITY_SPEC = ToolSpec(
    name=TOOL_LOOKUP_ENTITY,
    description=(
        "Resolve a ticker, legal name or identifier to a legal entity and report the "
        "resolution STATE. Returns an `entity` only when the state is actionable; "
        "candidates are always separate and are not an answer."
    ),
    handler=_lookup_entity,
    validate_arguments=_validate_lookup_entity,
    # No searches, no fetches, no documents: one indexed database read.
    cost=ToolCost(),
    # Nothing is measured here, and that is stated rather than reported as zeros.
    instrumented_units=(),
    access_classes=(),
    may_contain_untrusted_content=False,
)


def register_builtins(registry: "ToolRegistry") -> "ToolRegistry":
    """Register every builtin tool."""
    from app.services.agent_tools.calculations import register_calculation_tools
    from app.services.agent_tools.corpus_search import register_corpus_tools
    from app.services.agent_tools.facts import register_fact_tools
    from app.services.agent_tools.filings import register_filing_tools
    from app.services.agent_tools.ir_events import register_ir_event_tools
    from app.services.agent_tools.macro import register_macro_tools

    registry.register(LOOKUP_ENTITY_SPEC)
    register_fact_tools(registry)
    register_calculation_tools(registry)
    register_corpus_tools(registry)
    register_macro_tools(registry)
    register_ir_event_tools(registry)
    register_filing_tools(registry)
    return registry


__all__ = ["LOOKUP_ENTITY_SPEC", "register_builtins"]
