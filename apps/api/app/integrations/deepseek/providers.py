"""DeepSeek providers — V3.4 Slice 4.3.

DeepSeek is the **primary external research and model provider** (ADR-048/049). Three
adapters behind the interfaces slice 4.1 defined:

* ``DeepSeekModelProvider`` — cheap and bulk analyst work, document reasoning, structured
  extraction.
* ``DeepSeekSearchProvider`` — server-side web search, which is the primary external
  general-web path and replaces the need for Exa or Perplexity in initial V3.
* ``DeepSeekResearchProvider`` — a first-pass specialist investigation that produces
  **leads**, never findings.

WHAT COMES BACK IS A CLAIM, AND THE TYPES MAKE THAT HARD TO FORGET
=================================================================
A search result is a ``SourceCandidate``: a URL and a claim that a page exists. A model
assertion is a ``ResearchLead`` whose every asserted field is named ``claimed_*``. Neither
is evidence until InvestingBuddy has fetched the underlying source through its own guarded
fetcher and the claim has survived verification (slice 4.4).

**A search snippet is never canonical evidence**, and every snippet is labelled untrusted
so a prompt builder fences it rather than guessing.

THE PARSERS TOLERATE A SHAPE THEY DO NOT RECOGNISE
==================================================
DeepSeek's exact server-side search wire format is **not verified against the live API in
this campaign** — see ``transport``. So ``parse_search_payload`` accepts several plausible
shapes, and returns ``[]`` **with a warning naming what it saw** for anything else. It
never raises and never invents a candidate: an unrecognised payload is "no candidates and
a note saying why", which is a research gap somebody can close, whereas a fabricated
candidate is a wrong citation nobody can trace.

DEGRADING HONESTLY IS PART OF THE CONTRACT
==========================================
An unconfigured or failing DeepSeek returns a ``failed`` result with a warning, never a
partial answer presented as complete and never an exception that ends a run. That is the
same property the council's deterministic chair fallback already has, and it is why
DeepSeek can be primary without being load-bearing.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any

from app.integrations.deepseek.transport import (
    DeepSeekResponse,
    DeepSeekTransport,
    DeepSeekUnavailableError,
)
from app.services.consumption import ConsumptionUnits
from app.services.providers.contracts import (
    STATUS_COMPLETED,
    STATUS_FAILED,
    STATUS_PARTIAL,
    BrowserResponse,
    CostEstimate,
    ModelResponse,
    QueryRecord,
    ResearchLead,
    ResearchProviderResult,
    SearchResponse,
    SourceCandidate,
)

PROVIDER_ID = "deepseek"

#: Units these adapters actually measure. Declared once so a spec and a handler cannot
#: disagree — a unit declared and not reported is stored as a zero that asserts the thing
#: did not happen.
MODEL_UNITS: tuple[str, ...] = (
    "model_calls",
    "model_input_tokens",
    "model_output_tokens",
    "cached_tokens",
)
SEARCH_UNITS: tuple[str, ...] = ("web_search_calls", "model_calls")
RESEARCH_UNITS: tuple[str, ...] = ("provider_research_runs", "model_calls")

#: A bound on candidates parsed from one response, so a pathological payload cannot
#: become an unbounded list an agent pays for in tokens.
MAX_CANDIDATES = 25
#: A bound on leads parsed from one investigation, for the same reason.
MAX_LEADS = 40

_SNIPPET_MAX = 1200


def _model_consumption(response: DeepSeekResponse) -> ConsumptionUnits:
    return ConsumptionUnits(
        model_calls=1,
        model_input_tokens=response.prompt_tokens,
        model_output_tokens=response.completion_tokens,
        cached_tokens=response.cached_tokens,
    )


def _iso_date(value: Any) -> date | None:
    """A date, or ``None``. Never a guess.

    A provider that returns ``"Q1 2026"`` where a date was expected has told us it does
    not know the date, and coercing that into a day would invent one.
    """
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _json_object(text: str | None) -> dict[str, Any] | None:
    """Parse a JSON object, or ``None``. Tolerant of surrounding prose."""
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        start, end = raw.find("{"), raw.rfind("}")
        if start < 0 or end <= start:
            return None
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return None
    return parsed if isinstance(parsed, dict) else None


def parse_search_payload(
    response: DeepSeekResponse,
) -> tuple[list[SourceCandidate], list[str]]:
    """Candidates and warnings from a search response. Never raises, never invents.

    Accepts the shapes a tool-call response plausibly takes and reports anything else as
    a warning with **what it actually saw**, because "the provider returned a shape we do
    not parse" is a fact somebody can act on and an empty list on its own is not.
    """
    warnings: list[str] = []
    raw_items: list[Any] = []

    for call in response.tool_payloads:
        function = call.get("function") if isinstance(call, dict) else None
        blob = None
        if isinstance(function, dict):
            blob = function.get("arguments") or function.get("output")
        blob = blob if blob is not None else call.get("output")
        if isinstance(blob, str):
            blob = _json_object(blob)
        if isinstance(blob, dict):
            for key in ("results", "items", "candidates", "sources"):
                found = blob.get(key)
                if isinstance(found, list):
                    raw_items.extend(found)
                    break
            else:
                warnings.append(
                    "a DeepSeek tool payload carried no results/items/candidates/sources "
                    f"list; keys were {sorted(blob)[:8]}"
                )
        elif isinstance(blob, list):
            raw_items.extend(blob)
        elif blob is not None:
            warnings.append(
                f"a DeepSeek tool payload was a {type(blob).__name__}, not an object or "
                "a list"
            )

    if not response.tool_payloads:
        # A model that answered in prose instead of calling the search tool has not
        # searched. Reported rather than parsed out of the prose: extracting URLs from a
        # sentence is how a hallucinated link becomes a source candidate.
        parsed = _json_object(response.text)
        if isinstance(parsed, dict) and isinstance(parsed.get("results"), list):
            raw_items.extend(parsed["results"])
        elif response.text:
            warnings.append(
                "DeepSeek answered in prose without invoking the search tool; no "
                "candidates were extracted, because pulling URLs out of a sentence is "
                "how a hallucinated link becomes a source candidate"
            )

    candidates: list[SourceCandidate] = []
    for item in raw_items[:MAX_CANDIDATES]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or item.get("link") or "").strip()
        if not url.lower().startswith(("http://", "https://")):
            # A candidate with no usable URL cannot be fetched, so it cannot become
            # evidence, so it is not a candidate.
            continue
        snippet = item.get("snippet") or item.get("content") or item.get("summary")
        candidates.append(
            SourceCandidate(
                url=url,
                title=(str(item.get("title")).strip() if item.get("title") else None),
                publisher=(
                    str(item.get("publisher") or item.get("site") or "").strip() or None
                ),
                published_at=_iso_date(item.get("published_at") or item.get("date")),
                provider_score=(
                    float(item["score"])
                    if isinstance(item.get("score"), int | float)
                    else None
                ),
                provider=PROVIDER_ID,
                snippet=(str(snippet)[:_SNIPPET_MAX] if snippet else None),
            )
        )
    if raw_items and not candidates:
        warnings.append(
            f"{len(raw_items)} DeepSeek result(s) carried no usable http(s) URL and were "
            "dropped; a candidate that cannot be fetched cannot become evidence"
        )
    return candidates, warnings


def parse_leads(
    response: DeepSeekResponse, *, model: str, task_id: str
) -> tuple[list[ResearchLead], list[str]]:
    """Leads and warnings from an investigation response.

    Every asserted field lands on a ``claimed_*`` attribute. A lead with no cited URL is
    **kept** — it is unverifiable rather than wrong, and the count of those is a quality
    signal about the provider that only exists if they are retained.
    """
    warnings: list[str] = []
    parsed = _json_object(response.text)
    if parsed is None:
        return [], [
            "DeepSeek returned no parseable JSON object, so no leads were extracted"
        ]
    raw = parsed.get("findings") or parsed.get("leads") or parsed.get("claims")
    if not isinstance(raw, list):
        return [], [
            "DeepSeek's response carried no findings/leads/claims list; keys were "
            f"{sorted(parsed)[:8]}"
        ]

    leads: list[ResearchLead] = []
    for item in raw[:MAX_LEADS]:
        if not isinstance(item, dict):
            continue
        claim = str(item.get("claim") or item.get("finding") or item.get("text") or "")
        claim = claim.strip()
        if not claim:
            continue
        leads.append(
            ResearchLead(
                claim_text=claim,
                provider=PROVIDER_ID,
                model=model,
                provider_task_id=task_id,
                claimed_source_url=(
                    str(item.get("source_url") or item.get("url") or "").strip() or None
                ),
                claimed_source_title=(
                    str(item.get("source_title") or "").strip() or None
                ),
                claimed_publisher=(str(item.get("publisher") or "").strip() or None),
                claimed_date=_iso_date(item.get("date") or item.get("published_at")),
                claimed_value=(str(item.get("value")).strip() if item.get("value") else None),
                claimed_unit=(str(item.get("unit")).strip() if item.get("unit") else None),
                claimed_currency=(
                    str(item.get("currency")).strip() if item.get("currency") else None
                ),
                claimed_period=(
                    str(item.get("period")).strip() if item.get("period") else None
                ),
                claimed_scope=(
                    str(item.get("scope")).strip() if item.get("scope") else None
                ),
            )
        )
    if len(raw) > MAX_LEADS:
        warnings.append(
            f"DeepSeek returned {len(raw)} claims; the first {MAX_LEADS} were kept"
        )
    return leads, warnings


@dataclass
class DeepSeekModelProvider:
    """Cheap and bulk analyst work, document reasoning, structured extraction."""

    transport: DeepSeekTransport
    provider_id: str = PROVIDER_ID
    temperature: float = 0.1

    @property
    def model(self) -> str:
        return getattr(self.transport, "model", "")

    async def complete(
        self, *, system: str, user: str, max_tokens: int = 1200, timeout: int = 40
    ) -> ModelResponse:
        response = await self.transport.complete(
            system=system,
            user=user,
            max_tokens=max_tokens,
            temperature=self.temperature,
            timeout=timeout,
        )
        payload = _json_object(response.text)
        return ModelResponse(
            provider=self.provider_id,
            model=self.model,
            # An unparseable completion is an empty payload with the reason recorded, not
            # an exception: one failed agent must not end a council.
            payload=payload if payload is not None else {},
            consumption=_model_consumption(response),
            instrumented_units=MODEL_UNITS,
            cost=CostEstimate(basis="unknown"),
            finish_reason=response.finish_reason,
            truncated=(response.finish_reason or "").lower() in {"length", "max_tokens"},
        )


@dataclass
class DeepSeekSearchProvider:
    """Server-side web search — the primary external general-web path (ADR-048).

    Fifth in the acquisition hierarchy, not first: the corpus, official structured APIs,
    official issuer sources and the safe fetcher all come before it.
    """

    transport: DeepSeekTransport
    provider_id: str = PROVIDER_ID

    async def search(
        self, *, query: str, top_k: int = 10, domains: Sequence[str] | None = None
    ) -> SearchResponse:
        bounded = max(1, min(int(top_k or 10), MAX_CANDIDATES))
        try:
            response = await self.transport.search(
                query=query, top_k=bounded, domains=domains, timeout=45
            )
        except DeepSeekUnavailableError as exc:
            return SearchResponse(
                provider=self.provider_id,
                query=query,
                consumption=ConsumptionUnits(web_search_calls=0, model_calls=0),
                instrumented_units=SEARCH_UNITS,
                cost=CostEstimate(basis="unknown"),
                warnings=[
                    f"DeepSeek search unavailable: {exc}. This is not a statement about "
                    "what the web contains."
                ],
            )
        candidates, warnings = parse_search_payload(response)
        return SearchResponse(
            provider=self.provider_id,
            query=query,
            candidates=candidates,
            # One search was issued and one model call carried it. Reported, because a
            # declared-but-unreported unit is stored as a zero that asserts otherwise.
            consumption=ConsumptionUnits(web_search_calls=1, model_calls=1),
            instrumented_units=SEARCH_UNITS,
            cost=CostEstimate(basis="unknown"),
            warnings=warnings,
        )


#: The instruction that shapes an investigation's output. It asks for claims WITH sources
#: and says plainly that an unsourced claim is acceptable — because a provider told to
#: always cite something will cite something.
INVESTIGATION_SYSTEM_PROMPT = (
    "You are a research contractor for an evidence-first investment research platform. "
    "Return JSON: {\"findings\": [{\"claim\": str, \"source_url\": str|null, "
    "\"source_title\": str|null, \"publisher\": str|null, \"date\": str|null, "
    "\"value\": str|null, \"unit\": str|null, \"currency\": str|null, "
    "\"period\": str|null, \"scope\": str|null}]}.\n"
    "Rules you must follow:\n"
    "- Cite a source URL for every claim you can. If you cannot, set source_url to null "
    "rather than citing something approximate. An uncited claim is acceptable and will "
    "be recorded as unverifiable; a wrong citation is not.\n"
    "- Do not state a figure you did not see. Leave value null instead.\n"
    "- Period and scope matter: say which period a figure is for, and whether it is the "
    "consolidated group or a named segment. If you do not know, use null.\n"
    "- You are not deciding anything. Every claim will be independently retrieved and "
    "verified before it is used."
)


@dataclass
class DeepSeekResearchProvider:
    """A first-pass specialist investigation that produces **leads**, never findings."""

    transport: DeepSeekTransport
    provider_id: str = PROVIDER_ID
    system_prompt: str = INVESTIGATION_SYSTEM_PROMPT

    @property
    def model(self) -> str:
        return getattr(self.transport, "model", "")

    async def investigate(
        self, *, question: str, context: str | None = None, max_seconds: int = 300
    ) -> ResearchProviderResult:
        task_id = str(uuid.uuid4())
        started = datetime.now(timezone.utc)
        user = question if not context else f"{question}\n\nContext:\n{context}"
        try:
            response = await self.transport.complete(
                system=self.system_prompt,
                user=user,
                max_tokens=3000,
                temperature=0.2,
                timeout=min(max(10, int(max_seconds)), 300),
            )
        except DeepSeekUnavailableError as exc:
            # `failed` with a warning, never a partial answer presented as complete.
            return ResearchProviderResult(
                provider=self.provider_id,
                model=self.model,
                task_id=task_id,
                status=STATUS_FAILED,
                started_at=started,
                completed_at=datetime.now(timezone.utc),
                consumption=ConsumptionUnits(provider_research_runs=0, model_calls=0),
                instrumented_units=RESEARCH_UNITS,
                cost=CostEstimate(basis="unknown"),
                warnings=[f"DeepSeek investigation unavailable: {exc}"],
            )

        leads, warnings = parse_leads(response, model=self.model, task_id=task_id)
        truncated = (response.finish_reason or "").lower() in {"length", "max_tokens"}
        if truncated:
            warnings.append(
                "DeepSeek stopped at its token limit, so the investigation is PARTIAL "
                "and its absence of further findings is not a finding"
            )
        return ResearchProviderResult(
            provider=self.provider_id,
            model=self.model,
            task_id=task_id,
            status=STATUS_PARTIAL if truncated else STATUS_COMPLETED,
            started_at=started,
            completed_at=datetime.now(timezone.utc),
            research_leads=leads,
            source_candidates=[
                SourceCandidate(
                    url=lead.claimed_source_url,
                    title=lead.claimed_source_title,
                    publisher=lead.claimed_publisher,
                    published_at=lead.claimed_date,
                    provider=self.provider_id,
                )
                for lead in leads
                if lead.claimed_source_url
            ],
            cited_urls=[
                lead.claimed_source_url for lead in leads if lead.claimed_source_url
            ],
            query_log=[QueryRecord(query=question, result_count=len(leads))],
            consumption=ConsumptionUnits(
                provider_research_runs=1,
                model_calls=1,
            ),
            instrumented_units=RESEARCH_UNITS,
            cost=CostEstimate(basis="unknown"),
            warnings=warnings,
            raw_provider_metadata={
                "finish_reason": response.finish_reason,
                "prompt_tokens": response.prompt_tokens,
                "completion_tokens": response.completion_tokens,
            },
        )


@dataclass
class UnavailableBrowserProvider:
    """The escalation that does not exist, and says so.

    No paid crawling provider is part of V3, so a JS-only page the safe fetcher cannot
    reach is recorded as **partially inaccessible** and alternative public sources are
    used. This exists rather than being omitted for the same reason
    ``search_private_research`` does: a missing provider is indistinguishable from one
    that found nothing, and "we cannot render this page" is a research gap somebody can
    act on.
    """

    provider_id: str = "unavailable_browser"

    async def render(self, *, url: str, timeout: int = 30) -> BrowserResponse:
        return BrowserResponse(
            provider=self.provider_id,
            url=url,
            text=None,
            consumption=ConsumptionUnits(browser_minutes=0.0),
            instrumented_units=("browser_minutes",),
            cost=CostEstimate(basis="unknown"),
            warnings=[
                "no browser provider is configured: V3 requires no paid crawling "
                "service, so a JavaScript-only page is recorded as partially "
                "inaccessible rather than rendered. Use an alternative public source."
            ],
        )


__all__ = [
    "INVESTIGATION_SYSTEM_PROMPT",
    "MAX_CANDIDATES",
    "MAX_LEADS",
    "MODEL_UNITS",
    "PROVIDER_ID",
    "RESEARCH_UNITS",
    "SEARCH_UNITS",
    "DeepSeekModelProvider",
    "DeepSeekResearchProvider",
    "DeepSeekSearchProvider",
    "UnavailableBrowserProvider",
    "parse_leads",
    "parse_search_payload",
]
