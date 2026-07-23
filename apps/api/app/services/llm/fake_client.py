"""
Deterministic, offline fake LLM client for the analysis council.

This is the ONLY client used in tests and CI. It makes no network calls and
needs no credentials. It reads the evidence ids out of the user prompt and
returns valid, citation-bound JSON for whichever agent the system prompt names.

Test hooks (all optional, all deterministic) let a test drive the edge cases the
council must handle safely:
  mode="valid"               normal structured output (default)
  mode="invalid_json_once"   first reply is malformed, the repair reply is valid
  mode="invalid_json_always" both replies malformed -> agent fails, no crash
  mode="timeout"             raises a timeout -> agent fails, no crash
  forbidden_agents={...}     inject a forbidden rating token for those agents
  bad_citation_agents={...}  cite an evidence id that is NOT in the pack
  uncited_agents={...}       emit a material claim with no citations
"""

from __future__ import annotations

import json
import re

from app.services.llm.client import LLMClient, LLMTimeoutError
from app.services.llm.schemas import (
    AGENT_COMMITTEE_CHAIR,
    DEFAULT_COMMITTEE_LABEL,
)

_AGENT_ID_RE = re.compile(r"agent id:\s*([a-z_]+)")
_EVIDENCE_ID_RE = re.compile(r'"id":\s*"(E\d+)"')
_REPAIR_MARKER = "Reply again"


class FakeLLMClient(LLMClient):
    def __init__(
        self,
        *,
        mode: str = "valid",
        forbidden_agents: set[str] | None = None,
        bad_citation_agents: set[str] | None = None,
        uncited_agents: set[str] | None = None,
        model: str = "fake-council-model",
    ) -> None:
        self._mode = mode
        self._forbidden_agents = forbidden_agents or set()
        self._bad_citation_agents = bad_citation_agents or set()
        self._uncited_agents = uncited_agents or set()
        self._model = model

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
        if self._mode == "timeout":
            raise LLMTimeoutError("fake timeout")

        is_repair = _REPAIR_MARKER in system
        if self._mode == "invalid_json_always":
            return "not a json object at all"
        if self._mode == "invalid_json_once" and not is_repair:
            return "```\nthis is not valid json\n```"

        agent = self._agent_from_system(system)
        evidence_ids = _EVIDENCE_ID_RE.findall(user)
        return json.dumps(self._build_output(agent, evidence_ids))

    # ------------------------------------------------------------------
    # deterministic output construction
    # ------------------------------------------------------------------

    @staticmethod
    def _agent_from_system(system: str) -> str:
        match = _AGENT_ID_RE.search(system)
        return match.group(1) if match else "unknown_agent"

    def _build_output(self, agent: str, evidence_ids: list[str]) -> dict:
        # Cite up to two real evidence ids so citation checks pass by default.
        primary = evidence_ids[:1]
        secondary = evidence_ids[1:2] or primary

        cite = list(primary)
        if agent in self._bad_citation_agents:
            cite = ["E999"]  # deliberately not in the pack
        summary = (
            f"Deterministic fake summary for the {agent} agent based on "
            f"{len(evidence_ids)} evidence item(s). Internal draft only."
        )
        if agent in self._forbidden_agents:
            # A forbidden rating token the safety scanner must catch.
            summary = summary + " Internal note: BUY."

        key_points = []
        if evidence_ids:
            key_points.append(
                {
                    "claim": (
                        f"The {agent} agent observed an evidenced datapoint "
                        "in the pack."
                    ),
                    "citation_ids": cite,
                    "confidence": "low",
                    "data_quality": "C",
                }
            )
        if agent in self._uncited_agents:
            key_points.append(
                {
                    # A material factual claim with NO citation -> must be flagged.
                    "claim": "Revenue grew materially year over year.",
                    "citation_ids": [],
                    "confidence": "medium",
                    "data_quality": "B",
                }
            )

        output: dict = {
            "agent_name": agent,
            "status": "completed",
            "summary": summary,
            "key_points": key_points,
            "risks_or_gaps": [
                {
                    "item": "Evidence is bounded and may be incomplete.",
                    "citation_ids": secondary,
                    "severity": "low",
                }
            ],
            "unsupported_claims": [],
            "safety_notes": [],
        }
        if agent == AGENT_COMMITTEE_CHAIR:
            # Deterministic, allowed internal label — never a recommendation.
            output["committee_label"] = (
                "requires_more_evidence" if evidence_ids else DEFAULT_COMMITTEE_LABEL
            )
        return output
