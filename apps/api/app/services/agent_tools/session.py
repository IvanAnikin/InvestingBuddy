"""The tool-call choke point — V3.3 Slice 3.1.

Exactly one path from an agent to a tool:

    permit → validate → budget (BEFORE spending) → invoke → record → charge

Everything the security boundary claims is enforced here, and nowhere else. A second
call path would be a second permission model, and the one thing worse than no
permission check is two that disagree.

THE BUDGET IS CHECKED BEFORE THE CALL, AND THE TEST PROVES IT
=============================================================
``ToolSpend.would_exceed`` is asked about the *projected* total, following
``consumption.ResearchBudget.check`` — a check that only looks at what has already
happened turns a budget into a report. The test for this asserts the underlying
callable was **never invoked**, rather than asserting a counter afterwards, because a
counter is satisfied by a spend that happened and was then noticed.

A REFUSAL IS RECORDED, NOT RAISED
=================================
Every outcome — ok, refused, error — returns a ``ToolCallResult`` and writes a row.
Refusals are the most informative rows in the table: ``tool_not_permitted`` per role
says the *Director* gave that role the wrong tools. Raising instead would let a caller
swallow the most diagnostic signal in the system with a bare ``except``.

AN EXCEPTION IN A TOOL IS CONTAINED
===================================
It becomes an ``error`` outcome carrying the exception **type** and never its message:
a message can contain a fragment of fetched content, and this record is read by humans
*and by prompts*. One tool failing must not end an investigation that has other
questions left.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from app.models.research_tool_call import ResearchToolCall
from app.services.agent_tools.contracts import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    OUTCOME_REFUSED,
    REFUSED_BUDGET_EXCEEDED,
    REFUSED_DISABLED,
    REFUSED_INVALID_ARGUMENTS,
    REFUSED_ITERATION_LIMIT,
    REFUSED_TOOL_NOT_PERMITTED,
    REFUSED_UNKNOWN_TOOL,
    ToolCallResult,
    ToolSpec,
    ToolSpend,
)
from app.services.agent_tools.policy import RoleToolPolicy
from app.services.agent_tools.registry import ToolRegistry
from app.services.consumption import ConsumptionUnits

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.core.config import Settings

_SUMMARY_MAX = 500
_ARGS_MAX_KEYS = 40

#: A hard ceiling on RECORDED calls per session, whatever their outcome.
#:
#: Distinct from ``ToolBudget``, and deliberately non-zero. A refusal costs nothing
#: external, so it correctly does not consume an iteration — which means a role with a
#: bad policy could otherwise call a forbidden tool forever, refused every time, and
#: never terminate. The bounded-investigation-loop invariant is "no infinite loops", and
#: it cannot rely on the caller alone.
#:
#: It is a SAFETY ceiling rather than a spend limit, so unlike every monetary bound in
#: ``consumption`` it does not default to unbounded: the numbers there are OPEN
#: DECISIONS the user owns, and "the process stops eventually" is not one of them.
MAX_RECORDED_CALLS_PER_SESSION = 200


@dataclass
class ToolContext:
    """What a tool is allowed to know about the run it is serving.

    Deliberately narrow. A tool receives a session, a config and the identifiers of
    what is being researched — not the run's findings, not the other agents' output,
    and not a mutable handle on anything. A tool that can see a conclusion can be
    argued into confirming it.
    """

    session: Any
    cfg: "Settings"
    company_id: uuid.UUID | None = None
    legal_entity_id: uuid.UUID | None = None
    role: str = ""
    task_ref: str | None = None
    #: The corpus search backend, when one is configured. Injected rather than chosen:
    #: OPEN DECISION #1 (Azure AI Search vs PostgreSQL + pgvector) is the user's, and a
    #: tool that picked one would take it by accident.
    search_backend: Any = None


@dataclass
class ToolSession:
    """One role's bounded, audited access to the tool surface."""

    registry: ToolRegistry
    policy: RoleToolPolicy
    cfg: "Settings"
    db: Any
    research_job_id: uuid.UUID | None = None
    company_id: uuid.UUID | None = None
    legal_entity_id: uuid.UUID | None = None
    #: Handed to every tool via the context. See ``ToolContext.search_backend``.
    search_backend: Any = None
    spend: ToolSpend = field(default_factory=ToolSpend)
    #: The safety ceiling. See ``MAX_RECORDED_CALLS_PER_SESSION``.
    max_recorded_calls: int = MAX_RECORDED_CALLS_PER_SESSION
    #: Every result this session produced, newest last. The in-memory trace; the
    #: durable one is ``research_tool_calls``.
    calls: list[ToolCallResult] = field(default_factory=list)

    @property
    def enabled(self) -> bool:
        return bool(getattr(self.cfg, "v3_agent_tools_enabled", False))

    async def call(
        self,
        tool_name: str,
        arguments: dict[str, Any] | None = None,
        *,
        task_ref: str | None = None,
    ) -> ToolCallResult:
        """Invoke a tool through the only supported path.

        Always returns a result and always records a row — see the module docstring for
        why refusals are not raised.
        """
        args = dict(arguments or {})
        # What the agent ASKED FOR, kept before validation normalises it. Recording the
        # normalised form instead would lose a real signal — "this agent requested
        # 10,000 rows" is behaviour worth seeing — and would gain nothing, because
        # validation is deterministic, so the raw arguments reproduce the call exactly.
        # What was actually APPLIED travels in the tool's own `population` block.
        as_asked = dict(args)
        started = time.perf_counter()

        if len(self.calls) >= self.max_recorded_calls:
            # The safety ceiling, and the one refusal that is NOT recorded — a session
            # that has hit it is producing rows faster than anything reads them, and
            # writing one more to say so would be the same unbounded loop with a
            # database behind it.
            return ToolCallResult(
                tool_name=tool_name,
                role=self.policy.role,
                outcome=OUTCOME_REFUSED,
                refusal_reason=REFUSED_ITERATION_LIMIT,
                limit_hit="max_recorded_calls",
                summary=(
                    f"refused: session ceiling of {self.max_recorded_calls} recorded "
                    "calls reached"
                ),
                latency_ms=0,
            )

        if not self.enabled:
            return await self._record(
                tool_name,
                as_asked,
                self._refusal(tool_name, REFUSED_DISABLED),
                started,
                task_ref,
            )

        spec = self.registry.get(tool_name)
        if spec is None:
            # Recorded, not silently dropped: what an agent TRIED to call is exactly
            # what an audit needs, and a hallucinated tool name is a real signal about
            # the prompt that produced it.
            return await self._record(
                tool_name,
                as_asked,
                self._refusal(tool_name, REFUSED_UNKNOWN_TOOL),
                started,
                task_ref,
            )

        if not self.policy.permits(spec.name):
            return await self._record(
                spec.name,
                as_asked,
                self._refusal(spec.name, REFUSED_TOOL_NOT_PERMITTED),
                started,
                task_ref,
            )

        if not self._access_classes_permitted(spec):
            return await self._record(
                spec.name,
                as_asked,
                self._refusal(
                    spec.name,
                    "access_class_not_permitted",
                    detail=(
                        f"{spec.name} reads {sorted(spec.access_classes)}; role "
                        f"{self.policy.role} may read "
                        f"{sorted(self.policy.access_classes)}"
                    ),
                ),
                started,
                task_ref,
            )

        cap = self.policy.budget.max_iterations
        if cap and self.spend.iterations >= cap:
            return await self._record(
                spec.name,
                as_asked,
                self._refusal(
                    spec.name, REFUSED_ITERATION_LIMIT, limit_hit="max_iterations"
                ),
                started,
                task_ref,
            )

        if spec.validate_arguments is not None:
            try:
                args = spec.validate_arguments(args)
            except ValueError as exc:
                return await self._record(
                    spec.name,
                    as_asked,
                    self._refusal(
                        spec.name, REFUSED_INVALID_ARGUMENTS, detail=str(exc)
                    ),
                    started,
                    task_ref,
                )

        # BEFORE the call. This is the line the whole module exists for.
        limit = self.spend.would_exceed(self.policy.budget, spec.cost)
        if limit is not None:
            return await self._record(
                spec.name,
                as_asked,
                self._refusal(spec.name, REFUSED_BUDGET_EXCEEDED, limit_hit=limit),
                started,
                task_ref,
            )

        context = ToolContext(
            session=self.db,
            cfg=self.cfg,
            company_id=self.company_id,
            legal_entity_id=self.legal_entity_id,
            role=self.policy.role,
            task_ref=task_ref,
            search_backend=self.search_backend,
        )
        try:
            payload = await spec.handler(context, args)
        except Exception as exc:  # noqa: BLE001 - containment is the contract
            # The TYPE, never the message: a message can carry fetched content, and
            # this record is read by humans and by prompts.
            self.spend.charge(spec.cost)
            self.spend.iterations += 1
            result = ToolCallResult(
                tool_name=spec.name,
                role=self.policy.role,
                outcome=OUTCOME_ERROR,
                error_type=type(exc).__name__,
                summary=f"{spec.name} raised {type(exc).__name__}",
                instrumented_units=spec.instrumented_units,
            )
            return await self._record(spec.name, as_asked, result, started, task_ref)

        self.spend.charge(spec.cost)
        self.spend.iterations += 1
        result = _result_from_payload(spec, self.policy.role, payload)
        return await self._record(spec.name, as_asked, result, started, task_ref)

    # ── internals ────────────────────────────────────────────────────────── #

    def _access_classes_permitted(self, spec: ToolSpec) -> bool:
        if not spec.access_classes:
            # Platform-internal only. Always permitted: the material never left the
            # platform, so there is no governance question to answer.
            return True
        return set(spec.access_classes) <= set(self.policy.access_classes)

    def _refusal(
        self,
        tool_name: str,
        reason: str,
        *,
        limit_hit: str | None = None,
        detail: str | None = None,
    ) -> ToolCallResult:
        summary = f"refused: {reason}"
        if limit_hit:
            summary = f"{summary} ({limit_hit})"
        if detail:
            summary = f"{summary} — {detail}"
        return ToolCallResult(
            tool_name=tool_name,
            role=self.policy.role,
            outcome=OUTCOME_REFUSED,
            refusal_reason=reason,
            limit_hit=limit_hit,
            summary=summary[:_SUMMARY_MAX],
        )

    async def _record(
        self,
        tool_name: str,
        arguments: dict[str, Any],
        result: ToolCallResult,
        started: float,
        task_ref: str | None,
    ) -> ToolCallResult:
        result.latency_ms = max(0, int((time.perf_counter() - started) * 1000))
        result.call_id = uuid.uuid4()
        row = ResearchToolCall(
            id=result.call_id,
            research_job_id=self.research_job_id,
            company_id=self.company_id,
            legal_entity_id=self.legal_entity_id,
            role=self.policy.role[:60],
            tool_name=tool_name[:60],
            task_ref=(task_ref or None),
            arguments_json=_safe_arguments(arguments),
            outcome=result.outcome,
            refusal_reason=result.refusal_reason,
            limit_hit=result.limit_hit,
            error_type=result.error_type,
            summary=(result.summary or None),
            item_count=result.item_count,
            contains_untrusted_content=result.contains_untrusted_content,
            consumption_json=(
                result.consumption.to_dict() if result.instrumented_units else None
            ),
            instrumented_units_json=list(result.instrumented_units) or None,
            latency_ms=result.latency_ms,
        )
        self.db.add(row)
        await self.db.flush()
        self.calls.append(result)
        return result

    # ── reporting ────────────────────────────────────────────────────────── #

    def refusals(self) -> list[ToolCallResult]:
        return [c for c in self.calls if c.refused]

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.policy.role,
            "policy": self.policy.to_dict(),
            "spend": self.spend.to_dict(),
            "calls": [c.to_dict() for c in self.calls],
            "refusal_reasons": sorted(
                {c.refusal_reason for c in self.refusals() if c.refusal_reason}
            ),
        }


def _result_from_payload(
    spec: ToolSpec, role: str, payload: Any
) -> ToolCallResult:
    """Wrap a handler's return value in the envelope.

    A handler returning a bare string is a **programming error**, not a payload: tool
    results carry provenance, not prose, and a string cannot carry a period, a scope or
    an evidence id. It surfaces as an ``error`` rather than being wrapped, so it is
    caught by the first test that exercises the tool.
    """
    if isinstance(payload, ToolCallResult):
        payload.tool_name = spec.name
        payload.role = role
        payload.instrumented_units = payload.instrumented_units or spec.instrumented_units
        return payload
    if not isinstance(payload, dict):
        return ToolCallResult(
            tool_name=spec.name,
            role=role,
            outcome=OUTCOME_ERROR,
            error_type="InvalidToolPayload",
            summary=(
                f"{spec.name} returned {type(payload).__name__}; a tool result must be "
                "a typed structure, because prose cannot carry a period, a scope or an "
                "evidence id"
            ),
            instrumented_units=spec.instrumented_units,
        )
    items = payload.get("items")
    reported = payload.get("consumption")
    if not isinstance(reported, ConsumptionUnits):
        # A handler returning a bare dict here would raise at flush time on
        # `.to_dict()`, and a persistence failure in the AUDIT path would take down the
        # call it was auditing. An unusable report is dropped, not guessed at.
        reported = ConsumptionUnits()
    return ToolCallResult(
        tool_name=spec.name,
        role=role,
        outcome=OUTCOME_OK,
        payload=payload,
        summary=str(payload.get("summary") or f"{spec.name} ok")[:_SUMMARY_MAX],
        item_count=len(items) if isinstance(items, list) else 0,
        # The flag may only be RAISED. A payload from an external tool claiming False
        # would un-fence text from the open web, so the spec's declaration is a floor
        # rather than a default the handler can override downwards.
        contains_untrusted_content=(
            spec.may_contain_untrusted_content
            or bool(payload.get("contains_untrusted_content"))
        ),
        consumption=reported,
        instrumented_units=spec.instrumented_units,
    )


def _safe_arguments(arguments: dict[str, Any]) -> dict[str, Any] | None:
    """Arguments, bounded, JSON-safe, and never a live object.

    Bounded because an audit row must stay readable, and JSON-safe because a UUID or a
    date reaching JSONB serialisation raises at flush time — a persistence failure in
    the audit path would take down the call it was auditing.
    """
    if not arguments:
        return None
    out: dict[str, Any] = {}
    for key in sorted(arguments)[:_ARGS_MAX_KEYS]:
        value = arguments[key]
        if isinstance(value, str | int | float | bool) or value is None:
            out[str(key)] = value
        elif isinstance(value, list | tuple):
            out[str(key)] = [str(v) for v in list(value)[:20]]
        else:
            out[str(key)] = str(value)
    return out


__all__ = ["ToolContext", "ToolSession"]
