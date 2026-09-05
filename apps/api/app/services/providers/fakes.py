"""Fake providers — V3.4 Slice 4.1.

First-class, and the **only** clients the unit suite touches. The repository already
follows this rule (``fake_client.py``, ``fake_discovery_client.py``,
``fake_field_review_client.py``), and every new provider ships with a fake in the same
slice as the adapter.

A FAKE IS NOT A LESSER TEST HERE
===============================
What these slices must prove is what the platform does *with* a provider's output — that
a claim becomes a lead, that an unverifiable lead is counted as such, that governance
refuses a class, that an unconfigured slot degrades. A live client would make every one
of those proofs depend on a vendor's uptime and a credential, and would spend money to
demonstrate a refusal.

EACH FAKE CAN MISBEHAVE ON PURPOSE
==================================
The interesting provider behaviours are the bad ones: a lead with no cited URL, a partial
result, a timeout, a claim whose figure is not in the source it cites. Each fake can be
configured to produce them, because those are the paths the verification gate exists for
and the ones a well-behaved live client would never exercise.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime, timezone

from app.services.consumption import ConsumptionUnits
from app.services.providers.contracts import (
    STATUS_COMPLETED,
    BrowserResponse,
    CostEstimate,
    ModelResponse,
    QueryRecord,
    ResearchLead,
    ResearchProviderResult,
    SearchResponse,
    SourceCandidate,
)


@dataclass
class FakeModelProvider:
    """Returns a scripted payload and reports the tokens it claims to have used."""

    provider_id: str = "fake_model"
    model: str = "fake-model-1"
    payload: dict = field(default_factory=lambda: {"ok": True})
    prompt_tokens: int = 100
    completion_tokens: int = 50
    raises: Exception | None = None
    truncated: bool = False
    calls: list[tuple[str, str]] = field(default_factory=list)

    async def complete(
        self, *, system: str, user: str, max_tokens: int = 1200, timeout: int = 40
    ) -> ModelResponse:
        self.calls.append((system, user))
        if self.raises is not None:
            raise self.raises
        return ModelResponse(
            provider=self.provider_id,
            model=self.model,
            payload=dict(self.payload),
            consumption=ConsumptionUnits(
                model_calls=1,
                model_input_tokens=self.prompt_tokens,
                model_output_tokens=self.completion_tokens,
            ),
            instrumented_units=(
                "model_calls",
                "model_input_tokens",
                "model_output_tokens",
            ),
            cost=CostEstimate(basis="unknown"),
            truncated=self.truncated,
        )


@dataclass
class FakeSearchProvider:
    """Returns scripted candidates, and can return none.

    "No results" is a real provider answer and a different one from a failure, so it is
    scriptable rather than only reachable by breaking the fake.
    """

    provider_id: str = "fake_search"
    candidates: list[SourceCandidate] = field(default_factory=list)
    raises: Exception | None = None
    queries: list[str] = field(default_factory=list)

    async def search(
        self, *, query: str, top_k: int = 10, domains: Sequence[str] | None = None
    ) -> SearchResponse:
        self.queries.append(query)
        if self.raises is not None:
            raise self.raises
        selected = [
            candidate
            for candidate in self.candidates
            if not domains
            or any((candidate.url or "").find(domain) >= 0 for domain in domains)
        ][:top_k]
        return SearchResponse(
            provider=self.provider_id,
            query=query,
            candidates=selected,
            consumption=ConsumptionUnits(web_search_calls=1),
            instrumented_units=("web_search_calls",),
            cost=CostEstimate(basis="unknown"),
        )


@dataclass
class FakeBrowserProvider:
    """Renders scripted text. An escalation path, faked so it stays testable."""

    provider_id: str = "fake_browser"
    text: str | None = "rendered page text"
    status_code: int = 200
    minutes: float = 0.25
    raises: Exception | None = None
    urls: list[str] = field(default_factory=list)

    async def render(self, *, url: str, timeout: int = 30) -> BrowserResponse:
        self.urls.append(url)
        if self.raises is not None:
            raise self.raises
        return BrowserResponse(
            provider=self.provider_id,
            url=url,
            text=self.text,
            status_code=self.status_code,
            consumption=ConsumptionUnits(browser_minutes=self.minutes),
            instrumented_units=("browser_minutes",),
            cost=CostEstimate(basis="unknown"),
        )


@dataclass
class FakeResearchProvider:
    """A managed researcher, scriptable down to the shapes that matter.

    ``leads`` is supplied directly so a test can include an **unverifiable** lead — one
    citing no URL — which is the case the promotion gate exists for and the one a
    well-behaved live provider would rarely produce.
    """

    provider_id: str = "fake_research"
    model: str | None = "fake-research-1"
    status: str = STATUS_COMPLETED
    leads: list[ResearchLead] = field(default_factory=list)
    source_candidates: list[SourceCandidate] = field(default_factory=list)
    query_log: list[QueryRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    raises: Exception | None = None
    questions: list[str] = field(default_factory=list)

    async def investigate(
        self, *, question: str, context: str | None = None, max_seconds: int = 300
    ) -> ResearchProviderResult:
        self.questions.append(question)
        if self.raises is not None:
            raise self.raises
        started = datetime.now(timezone.utc)
        return ResearchProviderResult(
            provider=self.provider_id,
            model=self.model,
            task_id=str(uuid.uuid4()),
            status=self.status,
            started_at=started,
            completed_at=started,
            research_leads=list(self.leads),
            source_candidates=list(self.source_candidates),
            cited_urls=[
                lead.claimed_source_url
                for lead in self.leads
                if lead.claimed_source_url
            ],
            query_log=list(self.query_log),
            consumption=ConsumptionUnits(provider_research_runs=1),
            instrumented_units=("provider_research_runs",),
            cost=CostEstimate(basis="unknown"),
            warnings=list(self.warnings),
            raw_provider_metadata={"fake": True},
        )


__all__ = [
    "FakeBrowserProvider",
    "FakeModelProvider",
    "FakeResearchProvider",
    "FakeSearchProvider",
]
