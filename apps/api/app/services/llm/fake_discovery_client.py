"""
Deterministic, offline fake LLM client for the run-level discovery council.

This is the ONLY client used in discovery-council tests and CI. It makes no
network calls and needs no credentials. It reads the run-fact ids (R#) and
candidate ids (C#) out of the user prompt and returns valid, citation-bound JSON
for whichever agent the system prompt names.

Test hooks (all optional, all deterministic) drive the edge cases the council
must handle safely:
  mode="valid"               normal structured output (default)
  mode="invalid_json_once"   first reply is malformed, the repair reply is valid
  mode="invalid_json_always" both replies malformed -> agent fails, no crash
  mode="timeout"             raises a timeout -> agent fails, no crash
  forbidden_agents={...}     inject a forbidden rating token for those agents
  bad_citation_agents={...}  cite an evidence id that is NOT in the pack
  uncited_agents={...}       emit a material run claim with no citations
  agent_failures={...}       Phase 32A Slice 6A: a per-agent QUEUE of exceptions,
                             popped once per call for that agent then falling
                             through to normal output — scripts 429/5xx/timeout
                             -then-success, retry-after, and retry exhaustion
                             (mirrors ``fake_client.FakeLLMClient``).

State (for assertions): ``self.calls`` counts raw calls per agent; ``self.
user_prompts`` records the user message seen per agent (so a test can assert the
discovery chair's rebuilt prompt contains a recovered agent's summary line).
"""

from __future__ import annotations

import json
import re

from app.services.llm.client import LLMClient, LLMTimeoutError
from app.services.llm.discovery_schemas import (
    AGENT_CANDIDATE_PRIORITIZATION,
    AGENT_DISCOVERY_CHAIR,
    AGENT_EVIDENCE_SUFFICIENCY,
    ALLOWED_INTERNAL_ACTIONS,
)

_AGENT_ID_RE = re.compile(r"agent id:\s*([a-z_]+)")
_CANDIDATE_ID_RE = re.compile(r'"id":\s*"(C\d+)"')
_RUN_FACT_ID_RE = re.compile(r'"id":\s*"(R\d+)"')
_REPAIR_MARKER = "Reply again"

# Deterministic, ordered internal actions (all allowed). Rotated by index so a
# multi-candidate run exercises every research bucket.
_ACTION_CYCLE = (
    "research_next",
    "monitor_for_evidence",
    "reject_for_now",
    "insufficient_data",
)
# Agents that emit per-candidate notes in the fake output.
_CANDIDATE_NOTE_AGENTS = frozenset(
    {AGENT_CANDIDATE_PRIORITIZATION, AGENT_EVIDENCE_SUFFICIENCY, AGENT_DISCOVERY_CHAIR}
)


class FakeDiscoveryLLMClient(LLMClient):
    def __init__(
        self,
        *,
        mode: str = "valid",
        forbidden_agents: set[str] | None = None,
        bad_citation_agents: set[str] | None = None,
        uncited_agents: set[str] | None = None,
        agent_failures: dict[str, list[Exception]] | None = None,
        model: str = "fake-discovery-council-model",
    ) -> None:
        self._mode = mode
        self._forbidden_agents = forbidden_agents or set()
        self._bad_citation_agents = bad_citation_agents or set()
        self._uncited_agents = uncited_agents or set()
        # Copy each queue so popping does not mutate the caller's dict/lists.
        self._agent_failures: dict[str, list[Exception]] = {
            agent: list(queue) for agent, queue in (agent_failures or {}).items()
        }
        # Assertion state (Phase 32A Slice 6A): raw calls per agent + the user
        # prompt(s) seen per agent.
        self.calls: dict[str, int] = {}
        self.user_prompts: dict[str, list[str]] = {}
        self._model = model
        # Guard against a rotation that would emit a disallowed action.
        assert all(a in ALLOWED_INTERNAL_ACTIONS for a in _ACTION_CYCLE)

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str | None:
        return self._model

    @property
    def is_fake(self) -> bool:
        return True

    async def _complete_raw(
        self,
        system: str,
        user: str,
        *,
        max_tokens: int,
        temperature: float,
        timeout: int,
    ) -> str:
        agent = self._agent_from_system(system)
        self.calls[agent] = self.calls.get(agent, 0) + 1
        self.user_prompts.setdefault(agent, []).append(user)

        # Phase 32A Slice 6A: per-agent scripted failure queue, popped once per
        # call, falling through to normal output when empty (mirrors
        # ``fake_client.FakeLLMClient``).
        queue = self._agent_failures.get(agent)
        if queue:
            raise queue.pop(0)

        if self._mode == "timeout":
            raise LLMTimeoutError("fake timeout")

        is_repair = _REPAIR_MARKER in system
        if self._mode == "invalid_json_always":
            return "not a json object at all"
        if self._mode == "invalid_json_once" and not is_repair:
            return "```\nthis is not valid json\n```"

        candidate_ids = _CANDIDATE_ID_RE.findall(user)
        run_fact_ids = _RUN_FACT_ID_RE.findall(user)
        return json.dumps(self._build_output(agent, candidate_ids, run_fact_ids))

    # ------------------------------------------------------------------
    # deterministic output construction
    # ------------------------------------------------------------------

    @staticmethod
    def _agent_from_system(system: str) -> str:
        match = _AGENT_ID_RE.search(system)
        return match.group(1) if match else "unknown_agent"

    def _build_output(
        self, agent: str, candidate_ids: list[str], run_fact_ids: list[str]
    ) -> dict:
        run_cite = run_fact_ids[:1] or candidate_ids[:1]
        summary = (
            f"Deterministic fake summary for the {agent} agent over "
            f"{len(candidate_ids)} candidate(s) and {len(run_fact_ids)} run "
            "fact(s). Internal draft only."
        )
        if agent in self._forbidden_agents:
            summary = summary + " Internal note: BUY."

        run_notes = []
        if run_cite:
            run_notes.append(
                {
                    "claim": (
                        f"The {agent} agent reviewed the run's evidence pack."
                    ),
                    "citation_ids": (
                        ["R999"] if agent in self._bad_citation_agents else list(run_cite)
                    ),
                    "confidence": "low",
                }
            )
        if agent in self._uncited_agents:
            run_notes.append(
                {
                    # A material factual claim with NO citation -> must be flagged.
                    "claim": "This run is broadly diversified across many sectors.",
                    "citation_ids": [],
                    "confidence": "medium",
                }
            )

        candidate_notes = []
        if agent in _CANDIDATE_NOTE_AGENTS:
            for i, cid in enumerate(candidate_ids[:3]):
                cite = ["C999"] if agent in self._bad_citation_agents else [cid]
                candidate_notes.append(
                    {
                        "candidate_ref": cid,
                        "internal_action": _ACTION_CYCLE[i % len(_ACTION_CYCLE)],
                        "rationale": (
                            "Internal prioritization based on the pack's scores "
                            "and data-coverage evidence."
                        ),
                        "citation_ids": cite,
                        "confidence": "low",
                    }
                )

        output: dict = {
            "agent_name": agent,
            "status": "completed",
            "summary": summary,
            "candidate_notes": candidate_notes,
            "run_notes": run_notes,
            "evidence_gaps": ["Some candidates lack sourced fundamentals."],
            "unsupported_claims": [],
            "safety_notes": [],
            "next_source_tasks": [],
        }
        if agent == AGENT_DISCOVERY_CHAIR:
            output["run_quality"] = "adequate" if candidate_ids else "failed"
            output["next_source_tasks"] = [
                "Obtain additional primary sourcing for sparse candidates."
            ]
        return output
