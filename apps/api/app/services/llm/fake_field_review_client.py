"""
Deterministic, offline fake LLM client for the DEEP FIELD REVIEW council.

This is the ONLY client used in field-review tests and CI. It makes no network
calls and needs no credentials. It reads the run-fact ids (R#) and company ids
(F#) out of the user prompt and returns valid, citation-bound JSON for whichever
agent the system prompt names.

Test hooks (all optional, all deterministic) drive the edge cases the council
must handle safely:
  mode="valid"                normal structured output (default)
  mode="invalid_json_once"    first reply is malformed, the repair reply is valid
  mode="invalid_json_always"  both replies malformed -> agent fails, no crash
  mode="timeout"              raises a timeout -> agent fails, no crash
  forbidden_agents={...}      inject forbidden rating language for those agents
  bad_citation_agents={...}   cite ids that are NOT in the field pack
  uncited_agents={...}        emit a material field claim with no citations
  transient_agents={...}      fail transiently for the first N attempts, then
                              succeed — drives the retry-recovery test
  transient_failures=N        how many transient failures before recovery
  transient_error="rate_limit" | "server" | "timeout"
"""

from __future__ import annotations

import json
import re

from app.services.llm.client import (
    LLMClient,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)
from app.services.llm.field_review_schemas import AGENT_FIELD_CHAIR

_AGENT_ID_RE = re.compile(r"agent id:\s*([a-z_]+)")
_COMPANY_ID_RE = re.compile(r'"id":\s*"(F\d+)"')
_RUN_FACT_ID_RE = re.compile(r'"id":\s*"(R\d+)"')
_REPAIR_MARKER = "Reply again"

# Deterministic bucket rotation so a multi-company review exercises every tier.
_BUCKET_CYCLE = (
    "strongest_candidates",
    "second_tier",
    "blocked_insufficient_evidence",
)


class FakeFieldReviewLLMClient(LLMClient):
    def __init__(
        self,
        *,
        mode: str = "valid",
        forbidden_agents: set[str] | None = None,
        bad_citation_agents: set[str] | None = None,
        uncited_agents: set[str] | None = None,
        transient_agents: set[str] | None = None,
        transient_failures: int = 1,
        transient_error: str = "rate_limit",
        truncate_agents: set[str] | None = None,
        model: str = "fake-field-review-model",
    ) -> None:
        self._mode = mode
        self._forbidden_agents = forbidden_agents or set()
        self._bad_citation_agents = bad_citation_agents or set()
        self._uncited_agents = uncited_agents or set()
        self._transient_agents = transient_agents or set()
        self._transient_failures = transient_failures
        self._transient_error = transient_error
        self._truncate_agents = truncate_agents or set()
        self._model = model
        # Per-agent attempt counter — lets a transient failure "recover".
        self.attempts: dict[str, int] = {}
        # Per-agent record of the output budget the council asked for, so a test
        # can assert the SCALED ``max_tokens`` actually reaches the provider.
        self.max_tokens_seen: dict[str, int] = {}

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_name(self) -> str | None:
        return self._model

    @property
    def is_fake(self) -> bool:
        return True

    def _raise_transient(self) -> None:
        if self._transient_error == "server":
            raise LLMServerError("fake 5xx")
        if self._transient_error == "timeout":
            raise LLMTimeoutError("fake timeout")
        raise LLMRateLimitError("fake 429", retry_after=0.01)

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

        agent = self._agent_from_system(system)
        self.max_tokens_seen[agent] = max_tokens
        is_repair = _REPAIR_MARKER in system
        if not is_repair:
            self.attempts[agent] = self.attempts.get(agent, 0) + 1
        # Simulate a reply cut off at the output-token limit: the provider
        # reports a length finish reason and the JSON stops mid-object. Both
        # the first reply and the one-shot repair truncate, because the repair
        # reuses the SAME budget — which is exactly why truncation is permanent.
        if agent in self._truncate_agents:
            self._record_finish_reason("length")
            return '{"agent_name": "' + agent + '", "company_notes": [{"comp'
        if (
            agent in self._transient_agents
            and self.attempts.get(agent, 0) <= self._transient_failures
        ):
            self._raise_transient()

        if self._mode == "invalid_json_always":
            return "not a json object at all"
        if self._mode == "invalid_json_once" and not is_repair:
            return "```\nthis is not valid json\n```"

        company_ids = _COMPANY_ID_RE.findall(user)
        run_fact_ids = _RUN_FACT_ID_RE.findall(user)
        return json.dumps(self._build_output(agent, company_ids, run_fact_ids))

    # ------------------------------------------------------------------
    # deterministic output construction
    # ------------------------------------------------------------------

    @staticmethod
    def _agent_from_system(system: str) -> str:
        match = _AGENT_ID_RE.search(system)
        return match.group(1) if match else "unknown_agent"

    def _build_output(
        self, agent: str, company_ids: list[str], run_fact_ids: list[str]
    ) -> dict:
        run_cite = run_fact_ids[:1] or company_ids[:1]
        summary = (
            f"Deterministic fake comparative summary for the {agent} agent over "
            f"{len(company_ids)} company summar(ies) and {len(run_fact_ids)} run "
            "fact(s). Internal draft only."
        )
        if agent in self._forbidden_agents:
            # Adversarial output: an explicit forbidden rating label + a
            # forbidden valuation phrase. Both must be quarantined, not passed.
            summary = summary + " Internal note: BUY. Price target reached."

        field_notes = []
        if run_cite:
            field_notes.append(
                {
                    "claim": (
                        f"The {agent} agent compared the persisted analyses in "
                        "this field pack."
                    ),
                    "citation_ids": (
                        ["R999"]
                        if agent in self._bad_citation_agents
                        else list(run_cite)
                    ),
                    "confidence": "low",
                }
            )
        if agent in self._uncited_agents:
            field_notes.append(
                {
                    # A material factual claim with NO citation -> must be flagged.
                    "claim": "Every company in this field is equally well evidenced.",
                    "citation_ids": [],
                    "confidence": "medium",
                }
            )

        company_notes = []
        for cid in company_ids[:5]:
            company_notes.append(
                {
                    "company_ref": cid,
                    "rationale": (
                        "Comparative note derived from the persisted analysis "
                        "summary and its evidence coverage."
                    ),
                    "citation_ids": (
                        ["F999"] if agent in self._bad_citation_agents else [cid]
                    ),
                    "confidence": "low",
                }
            )

        output: dict = {
            "agent_name": agent,
            "status": "completed",
            "summary": summary,
            "company_notes": company_notes,
            "field_notes": field_notes,
            "evidence_gaps": [
                "Some companies lack an extracted primary document."
            ],
            "unsupported_claims": [],
            "safety_notes": [],
            "next_research_tasks": [],
        }
        if agent == AGENT_FIELD_CHAIR:
            buckets: dict[str, list[dict]] = {b: [] for b in _BUCKET_CYCLE}
            for i, cid in enumerate(company_ids):
                bucket = _BUCKET_CYCLE[i % len(_BUCKET_CYCLE)]
                buckets[bucket].append(
                    {
                        "company_ref": cid,
                        "rationale": (
                            "Internal research-priority placement based on the "
                            "persisted evidence coverage in this pack."
                        ),
                        "citation_ids": (
                            ["F999"]
                            if agent in self._bad_citation_agents
                            else [cid]
                        ),
                        "confidence": "low",
                        "caveats": [],
                    }
                )
            output["chair_verdict"] = {
                **buckets,
                "field_uncertainties": [
                    "Evidence depth differs across companies in this field."
                ],
                "field_quality": "adequate" if company_ids else "failed",
            }
            output["next_research_tasks"] = [
                "Obtain additional primary sourcing for the thinly-evidenced "
                "companies."
            ]
        return output
