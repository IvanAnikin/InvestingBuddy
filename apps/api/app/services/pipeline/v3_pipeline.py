"""The V3 research pipeline, end to end — V3.10 Slice 10.3.

THE PATH
========
::

    company research API -> durable job -> entity resolution -> Research Director
      -> playbook -> Research Ledger -> specialist investigations -> tools
      -> bounded gap follow-up -> Council V2 -> Red Team -> Chair
      -> persisted report -> existing report API / frontend -> Research Memory

WHAT THIS SLICE DOES **NOT** DO, AND WHY THAT IS DELIBERATE
===========================================================
It does not replace the report's narrative. The existing final-report generator still
assembles the report, so every section, the safety gate, the schema validation, the
server-side numeric verification and the frontend rendering are **untouched** — and the
brief for this phase says in as many words not to redesign the frontend.

What the V3 pipeline contributes is **persisted, citable research state** attached to that
report under ``source_summary_json["v3_research"]``: the ledger run, the findings with
their evidence ids, the gaps, the disagreements, the Red Team challenges and the Chair's
verdict.

Stating it plainly, because a reader of the RC report needs to know exactly what was
proved: **the V3 Council's verdict is recorded beside the report, not driving its prose.**
Making it drive the narrative is a further slice with its own compatibility surface, and
doing it here would have meant rewriting a 5,500-line generator during an acceptance
phase.

WHY THE V2 ASSEMBLY STILL RUNS
==============================
Three reasons, in order of weight. It is what the frontend renders; it is what the safety
gate and numeric verification are wired to; and it is what makes this reversible — with
the flag off, byte-for-byte nothing changes.

EVERY STEP DEGRADES
===================
No entity, no playbook, no model, no tools: each one narrows the run and none of them
fails it. The V3 contribution is *additive*, so a V3 failure must never cost a report the
V2 path would have produced — and a test asserts exactly that.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from app.services.agent_tools.builtin import register_builtins
from app.services.agent_tools.policy import policy_for
from app.services.agent_tools.registry import ToolRegistry
from app.services.agent_tools.session import ToolSession
from app.services.agents.chair import ChairVerdict, LLMChair
from app.services.agents.investigator import LLMInvestigator
from app.services.agents.red_team import LLMRedTeam, LLMResponder
from app.services.agents.routing import (
    SLOT_CHAIR,
    SLOT_INVESTIGATOR,
    SLOT_RED_TEAM,
    ModelRouting,
    resolve_routing,
)
from app.services.council_v2 import inputs as council_inputs
from app.services.council_v2.red_team import run_challenge_round
from app.services.director.loop import LoopResult, run_investigation
from app.services.director.planner import persist_plan, plan_research
from app.services.ledger import store as ledger
from app.services.memory import store as memory
from app.services.memory.delta import compute_delta, persist_delta
from app.services.playbooks import select as select_playbooks
from app.services.research_mode import budget_for, limits_for, parse_mode

#: The key the V3 research state is attached under. Additive on a JSONB column the
#: report API already returns and the frontend already tolerates unknown keys in.
SOURCE_SUMMARY_KEY = "v3_research"


class _PlaybookAdapter:
    """A `Playbook` in the shape the Director's Protocol expects."""

    def __init__(self, playbook: Any) -> None:
        self._playbook = playbook
        self.playbook_id = playbook.playbook_id
        self.version = playbook.version

    def mandatory_questions(self):  # noqa: ANN201
        return self._playbook.mandatory_questions()

    def specialist_roles(self):  # noqa: ANN201
        return self._playbook.specialist_role_ids()

    def completion_rules(self):  # noqa: ANN201
        return self._playbook.completion_rule_ids()


@dataclass
class V3ResearchOutcome:
    """What one V3 run produced. Serialised onto the report."""

    research_run_id: uuid.UUID | None = None
    mode: str = "standard"
    playbook_versions: dict[str, int] = field(default_factory=dict)
    routing: dict[str, Any] = field(default_factory=dict)
    loop: dict[str, Any] = field(default_factory=dict)
    council: dict[str, Any] = field(default_factory=dict)
    challenges: dict[str, Any] = field(default_factory=dict)
    chair: dict[str, Any] = field(default_factory=dict)
    delta: dict[str, Any] | None = None
    findings: list[dict[str, Any]] = field(default_factory=list)
    gaps: list[dict[str, Any]] = field(default_factory=list)
    consumption: dict[str, Any] = field(default_factory=dict)
    elapsed_seconds: float = 0.0
    #: Why the run produced less than a full one. Never empty on a degraded run: a
    #: pipeline that narrowed silently is one nobody can widen.
    degraded: list[str] = field(default_factory=list)
    error: str | None = None

    @property
    def ran(self) -> bool:
        return self.research_run_id is not None and self.error is None

    def to_dict(self) -> dict[str, Any]:
        return {
            "research_run_id": (
                str(self.research_run_id) if self.research_run_id else None
            ),
            "mode": self.mode,
            "playbook_versions": dict(self.playbook_versions),
            "routing": dict(self.routing),
            "loop": dict(self.loop),
            "council": dict(self.council),
            "challenges": dict(self.challenges),
            "chair": dict(self.chair),
            "delta": self.delta,
            "findings": list(self.findings),
            "gaps": list(self.gaps),
            "consumption": dict(self.consumption),
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "degraded": list(self.degraded),
            "error": self.error,
            # Stated in the payload, not only in a doc: a reader of the stored report
            # must be able to tell what this block is and is not.
            "note": (
                "V3 research state, recorded beside the report. The report's narrative "
                "is assembled by the existing generator; this is the ledger, the "
                "council's verdict and the Red Team's challenges, with citable ids."
            ),
        }


async def run_v3_research(
    session: Any,
    company: Any,
    *,
    cfg: Any = None,
    mode: str | None = None,
    search_backend: Any = None,
    routing: ModelRouting | None = None,
    now: Any = None,
) -> V3ResearchOutcome:
    """Run the V3 pipeline for one company. **Never raises.**

    A V3 failure must never cost a report the V2 path would have produced, so every
    exception is caught, recorded on the outcome, and returned.
    """
    if cfg is None:
        from app.core.config import settings as default_cfg

        cfg = default_cfg
    clock = now or time.monotonic
    started = clock()
    outcome = V3ResearchOutcome()
    try:
        await _run(session, company, cfg=cfg, outcome=outcome,
                   mode=mode, search_backend=search_backend, routing=routing)
    except Exception as exc:  # noqa: BLE001 - additive work must not fail the report
        outcome.error = type(exc).__name__
        outcome.degraded.append(f"the V3 pipeline raised {type(exc).__name__}")
    outcome.elapsed_seconds = clock() - started
    return outcome


async def _run(
    session: Any,
    company: Any,
    *,
    cfg: Any,
    outcome: V3ResearchOutcome,
    mode: str | None,
    search_backend: Any,
    routing: ModelRouting | None,
) -> None:
    resolved_mode = parse_mode(mode or getattr(cfg, "v3_research_mode_default", None))
    limits = limits_for(resolved_mode)
    outcome.mode = resolved_mode.value

    model_routing = routing or resolve_routing(cfg)
    outcome.routing = model_routing.to_dict()
    if not model_routing.any_resolved:
        outcome.degraded.append(
            "no model provider resolved; investigations run tools and report gaps"
        )

    # 1. Prior research — the "before" a delta compares against, and the gaps that
    #    become this run's questions.
    prior = await memory.recall(session, company_id=company.id)
    carry_forward = prior.carry_forward_questions if prior.exists else ()

    # 2. Playbook selection. An unclassifiable company gets NONE and a recorded reason,
    #    never the nearest-looking one.
    selection = select_playbooks(
        sector=getattr(company, "sector", None),
        industry=getattr(company, "industry", None),
    )
    if selection.is_empty:
        outcome.degraded.append(f"no playbook applied: {selection.reason}")
    playbooks = [_PlaybookAdapter(p) for p in selection.playbooks]
    outcome.playbook_versions = dict(selection.versions)

    # 3. The ledger run.
    run = await ledger.open_run(
        session,
        mode=resolved_mode.value,
        company_id=company.id,
        legal_entity_id=getattr(company, "legal_entity_id", None),
        budget=budget_for(resolved_mode, cfg).__dict__,
        playbook_versions=dict(selection.versions),
    )
    outcome.research_run_id = run.id

    # 4. Plan.
    plan = await plan_research(
        subject=f"{company.ticker}:{company.exchange}",
        mode=resolved_mode,
        playbooks=playbooks,
        prior_open_gaps=carry_forward,
    )
    await persist_plan(session, run, plan)

    # 5. Investigate, with real tools and a real model where one resolved.
    registry = register_builtins(ToolRegistry())
    investigator_client = model_routing.client_for(SLOT_INVESTIGATOR)

    class _RoleRoutedInvestigator:
        """One session per role, because a tool policy is per role.

        Building it here rather than in the loop keeps the loop ignorant of tool
        policy — it owns control flow and nothing about how a specialist works.
        """

        def __init__(self) -> None:
            self.fabricated: list[str] = []
            self.tool_calls = 0

        async def investigate(self, *, role_id, questions, round_index, remaining_tool_calls):  # noqa: ANN001, ANN201
            from app.services.director.roles import role_for

            role = role_for(role_id)
            tools = role.tools if role is not None else frozenset()
            tool_session = ToolSession(
                registry=registry,
                policy=policy_for(role_id, tools=tools),
                cfg=cfg,
                db=session,
                research_job_id=run.id,
                company_id=company.id,
                legal_entity_id=getattr(company, "legal_entity_id", None),
                search_backend=search_backend,
            )
            worker = LLMInvestigator(
                session=tool_session,
                company_id=company.id,
                client=investigator_client,
            )
            result = await worker.investigate(
                role_id=role_id,
                questions=questions,
                round_index=round_index,
                remaining_tool_calls=remaining_tool_calls,
            )
            self.fabricated.extend(worker.fabricated_citations)
            self.tool_calls += result.tool_calls
            return result

    investigator = _RoleRoutedInvestigator()
    loop_result: LoopResult = await run_investigation(
        session,
        run,
        plan,
        investigator=investigator,
        limits=limits,
        completion_rules=selection.completion_rules,
    )
    outcome.loop = loop_result.to_dict()
    outcome.loop["fabricated_citations_discarded"] = len(investigator.fabricated)

    # 6. Council V2 input — which may refuse to convene, with a reason.
    council = await council_inputs.assemble(session, run)
    outcome.council = council.to_dict()

    # 7. Red Team, one bounded round.
    citable = {eid for f in council.findings for eid in f.evidence_ids}
    red = LLMRedTeam(client=model_routing.client_for(SLOT_RED_TEAM))
    responder = LLMResponder(
        client=model_routing.client_for(SLOT_RED_TEAM), citable_ids=frozenset(citable)
    )
    challenge_result = await run_challenge_round(
        session, run, council, red_team=red, responder=responder
    )
    outcome.challenges = challenge_result.to_dict()
    outcome.challenges["discarded_unknown_targets"] = len(
        red.discarded_unknown_targets
    )

    # 8. Chair. Re-assembled first, because the Red Team may have withdrawn a finding
    #    and the Chair must not see one that was retired.
    council = await council_inputs.assemble(session, run)
    verdict: ChairVerdict = await LLMChair(
        client=model_routing.client_for(SLOT_CHAIR)
    ).deliberate(council)
    outcome.chair = verdict.to_dict()
    if verdict.deterministic_fallback:
        outcome.degraded.append("the chair fell back to the deterministic verdict")

    # 9. What a reader can cite.
    outcome.findings = [f.to_dict() for f in council.findings[:60]]
    outcome.gaps = [g.to_dict() for g in council.gaps[:40]]

    # 10. Research memory: the delta against the previous run.
    #
    # The previous run is the one captured at step 1, BEFORE this run existed. Asking
    # `previous_run` again here would find THIS run — it is now `reviewing` or `stopped`
    # and is the most recent — and the guard against comparing a run with itself would
    # then produce no delta at all. That is the failure mode where the feature looks
    # implemented and silently returns nothing, and it is what this comment is for.
    if prior.exists and prior.run_id is not None:
        from app.models.ledger import ResearchRun

        previous = await session.get(ResearchRun, prior.run_id)
        if previous is not None and previous.id != run.id:
            delta = await compute_delta(session, to_run=run, from_run=previous)
            await persist_delta(session, delta)
            outcome.delta = delta.to_dict()

    summary = await ledger.summarise(session, run)
    outcome.consumption = {
        "tool_calls": loop_result.tool_calls,
        "tasks_run": loop_result.tasks_run,
        "rounds": len(loop_result.rounds),
        "findings": summary.findings_total,
        "gaps_open": summary.gaps_open,
    }
    await session.flush()


def attach_to_report(report: Any, outcome: V3ResearchOutcome) -> Any:
    """Write the V3 state onto an already-assembled report.

    Additive: an existing ``source_summary_json`` is preserved and one key is added, so a
    report written before V3 and one written after differ by exactly that key. The report
    API already returns the column and the frontend already tolerates keys it does not
    read.
    """
    summary = dict(getattr(report, "source_summary_json", None) or {})
    summary[SOURCE_SUMMARY_KEY] = outcome.to_dict()
    report.source_summary_json = summary
    return report


__all__ = [
    "SOURCE_SUMMARY_KEY",
    "V3ResearchOutcome",
    "attach_to_report",
    "run_v3_research",
]
