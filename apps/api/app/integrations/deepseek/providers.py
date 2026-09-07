"""DeepSeek providers — V3.4 Slice 4.3.

DeepSeek is the **primary external research and model provider** (ADR-048/049). Three
adapters behind the interfaces slice 4.1 defined:

* ``DeepSeekModelProvider`` — cheap and bulk analyst work, document reasoning, structured
  extraction.
* ``DeepSeekSearchProvider`` — server-side web search on ``/responses``, verified live
  2026-09-07, which is the primary external general-web path and removes the need to buy
  Exa or Perplexity for initial V3.
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

THE SEARCH PARSER IS BUILT FROM A MEASURED CONTRACT (V3.11.1.2)
==============================================================
DeepSeek's server-side search **is real** and lives on ``POST /responses``; the earlier
conclusion that it did not exist came from probing ``/chat/completions``, which serves no
builtin tools. ``transport`` records the full measurement.

What that contract gives, and what it does not, decides everything below:

* It gives a **retrieval trace** — the queries issued, the pages opened, the opens that
  failed. Candidates come from there and from nowhere else, because an opened page is one
  the provider actually read.
* It gives **no structured citations at all**. ``annotations`` was empty in every live
  run. So ``parse_search_payload`` never presents a DeepSeek citation as structured
  provenance: the ``SourceCandidate`` fields it cannot honestly fill — title, publisher,
  date, score, snippet — are left ``None`` rather than derived from a hostname.

  Scoped to the *search* path deliberately. ``DeepSeekResearchProvider.investigate()``
  below still builds candidates from a model's **claimed** citation, filling title and
  publisher from what the model asserted, because a lead is explicitly a claim and every
  such field is named ``claimed_*``. Both are honest; they are honest about different
  things. ``verify_lead`` re-applies the fetch policy to either.
* It gives **no enforced domain filter and no enforced tool-call ceiling**, so both are
  enforced here, client-side, and the telemetry says which side enforced them.

A URL that appears only in the answer's prose is counted, never promoted: see
``parse_search_payload``.

DEGRADING HONESTLY IS PART OF THE CONTRACT
==========================================
An unconfigured or failing DeepSeek returns a ``failed`` result with a warning, never a
partial answer presented as complete and never an exception that ends a run. That is the
same property the council's deterministic chair fallback already has, and it is why
DeepSeek can be primary without being load-bearing.
"""

from __future__ import annotations

import json
import re
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from app.integrations.deepseek.transport import (
    DEFAULT_SEARCH_TOOL_NAME,
    DeepSeekResponse,
    DeepSeekTransport,
    DeepSeekUnavailableError,
    canonical_search_url,
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
from app.services.sources.safe_web_fetcher import is_safe_public_host

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
#: ``/responses`` reports tokens for a search exactly as it does for a completion, so
#: they are measured rather than left absent. Before V3.11.1.2 a search reported only
#: two units, which made the platform's most expensive single call look like its
#: cheapest — one observed search spent ~41k tokens across eight tool calls.
#: ``web_search_calls`` counts QUERIES; ``url_fetch_calls`` counts pages the provider
#: opened. Review caught these being conflated: a run that issued one query and opened
#: seven pages reported eight web searches, and `cost_per_verified_finding` is the whole
#: reason this vocabulary is granular.
SEARCH_UNITS: tuple[str, ...] = (
    "web_search_calls",
    "url_fetch_calls",
    "model_calls",
    "model_input_tokens",
    "model_output_tokens",
    "cached_tokens",
)

#: The two ``action.type`` values measured to name a page the provider actually opened
#: and read. An ALLOW-list, not a deny-list: the first version special-cased ``search``
#: and promoted every other action carrying a ``url``, so an action type this campaign
#: has not seen — say one carrying unvisited *search result* URLs, which is exactly what
#: an ``include: [...sources]`` that started working would produce — would have been
#: silently promoted as retrieved. That is the failure this whole slice guards against.
RETRIEVAL_ACTIONS: frozenset[str] = frozenset({"open_page", "find_in_page"})

#: The action that names queries and returns no URLs.
QUERY_ACTION = "search"
RESEARCH_UNITS: tuple[str, ...] = ("provider_research_runs", "model_calls")

#: A bound on candidates parsed from one response, so a pathological payload cannot
#: become an unbounded list an agent pays for in tokens.
MAX_CANDIDATES = 25
#: A bound on leads parsed from one investigation, for the same reason.
MAX_LEADS = 40


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


@dataclass(frozen=True)
class SearchTrace:
    """What DeepSeek *did*, as distinct from what it said.

    The live contract exposes a retrieval trace and **no structured citations**, so this
    is the only machine-readable provenance available. It is kept because the two most
    useful questions about a search provider are not answerable from its prose: *did it
    actually search*, and *did it go where we asked*.
    """

    #: Queries the provider issued. It reports these without their results, so they are
    #: evidence of effort and of coverage, never of what the web contains.
    queries: tuple[str, ...] = ()
    #: Pages it opened and read. Canonical URLs — these become candidates.
    opened_urls: tuple[str, ...] = ()
    #: Pages it tried to open and could not. Not candidates: nothing was retrieved.
    failed_urls: tuple[str, ...] = ()
    #: URLs the answer cites that appear nowhere in the trace. **A fabrication signal.**
    #: The model's own knowledge cutoff predates most of what we ask about, so a cited
    #: page it never visited may be recalled rather than found.
    #:
    #: **UNFILTERED, unlike every other URL list here.** These are scraped from model
    #: prose and pass neither the host check nor the domain restriction, because the
    #: point of the field is to report what the model *said* — filtering it would hide
    #: the very cases worth seeing. Nothing fetches them, and nothing may: they are a
    #: diagnostic, never a candidate.
    cited_but_never_opened: tuple[str, ...] = ()
    #: Opened pages outside a domain restriction the caller asked for. DeepSeek accepts
    #: ``filters.allowed_domains`` and ignores it, so this is expected to be non-zero and
    #: is recorded rather than treated as an error.
    off_domain_urls: tuple[str, ...] = ()
    #: ``search`` actions — QUERIES issued. Distinct from pages opened: conflating the
    #: two reported one query and seven page-opens as eight web searches.
    query_call_count: int = 0
    #: Search actions that came back ``failed``. Effort spent, coverage not achieved.
    failed_query_call_count: int = 0
    #: ``open_page`` / ``find_in_page`` actions — pages FETCHED by the provider.
    retrieval_call_count: int = 0
    #: Tool calls whose ``action.type`` this parser does not recognise. Non-zero means
    #: the vendor changed something and a re-measurement is due.
    unknown_action_count: int = 0
    #: URLs dropped for naming a non-public or IP-literal host. A number worth watching:
    #: a provider that starts returning them has changed behaviour.
    unsafe_host_count: int = 0
    #: URLs dropped for carrying no usable http(s) scheme.
    unusable_url_count: int = 0
    #: True when the ``tools`` echo contains the tool we asked for. False means the
    #: request was accepted and the tool silently dropped, which returns 200 and zero
    #: results — indistinguishable from an empty web unless this is checked.
    tool_honoured: bool = False

    @property
    def provider_call_count(self) -> int:
        """Every ``web_search_call`` item, of whatever kind."""
        return self.query_call_count + self.retrieval_call_count + self.unknown_action_count

    def to_dict(self) -> dict[str, Any]:
        return {
            "queries": list(self.queries),
            "opened_urls": list(self.opened_urls),
            "failed_urls": list(self.failed_urls),
            # Named so no consumer mistakes it for a sibling of `opened_urls`, which is
            # host-checked and domain-filtered. This one is raw model output.
            "cited_but_never_opened_unfiltered": list(self.cited_but_never_opened),
            "off_domain_urls": list(self.off_domain_urls),
            "query_call_count": self.query_call_count,
            "failed_query_call_count": self.failed_query_call_count,
            "retrieval_call_count": self.retrieval_call_count,
            "unknown_action_count": self.unknown_action_count,
            "unsafe_host_count": self.unsafe_host_count,
            "unusable_url_count": self.unusable_url_count,
            "provider_call_count": self.provider_call_count,
            "tool_honoured": self.tool_honoured,
        }


#: URLs in prose. Deliberately conservative: it stops at whitespace and at the
#: punctuation that ordinarily ends a sentence, so a trailing full stop or bracket does
#: not become part of the URL.
_PROSE_URL_RE = re.compile(r"https?://[^\s<>\"'\)\]]+")


def _extract_prose_urls(text: str | None) -> list[str]:
    seen: dict[str, None] = {}
    for match in _PROSE_URL_RE.findall(text or ""):
        cleaned = canonical_search_url(match.rstrip(".,;:"))
        if cleaned:
            seen.setdefault(cleaned, None)
    return list(seen)


def _comparable(url: str) -> str:
    """A URL reduced to what makes it the same page, for TRACE COMPARISON ONLY.

    ``cited_but_never_opened`` is presented as a fabrication signal, so it has to
    survive the cosmetic differences ordinary model prose introduces — a trailing
    slash, ``http`` where the trace said ``https``. Review found
    ``https://a.example/x/`` flagged against an opened ``https://a.example/x``, which
    would have made the signal noisy enough to ignore.

    Never used for the candidate URL itself: what the fetcher receives is the URL the
    provider reported, canonicalised only of the provider's own ``#ws_call_id``
    fragment. Normalising a URL we are about to fetch is how a redirect becomes a
    different document.
    """
    text = (url or "").strip().lower()
    for prefix in ("https://", "http://"):
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text.rstrip("/")


def _host(url: str) -> str:
    """The URL's host, lowercased, with the root-zone dot removed.

    ``hostname`` already excludes userinfo and the port, which is what makes
    ``https://evil.example@allowed.example/`` resolve to ``evil.example`` here rather
    than to the domain an attacker wanted matched. The trailing dot is stripped for the
    same reason ``is_safe_public_host`` strips it: ``a.example.`` and ``a.example`` are
    the same host, and treating them differently would report a legitimate page as
    off-domain.
    """
    try:
        return (urlsplit(url).hostname or "").lower().rstrip(".")
    except ValueError:
        return ""


def _clean_domains(domains: Sequence[str] | None) -> list[str]:
    """The domain restriction actually asked for — blanks removed, lowercased."""
    return sorted(
        {
            # Both dots stripped: `_host` removes the root-zone dot, so a caller passing
            # "example.com." would otherwise match nothing at all.
            str(d or "").strip().lower().strip(".")
            for d in (domains or [])
            if str(d or "").strip().strip(".")
        }
    )


def _domain_allowed(url: str, domains: Sequence[str] | None) -> bool:
    """True when ``url``'s host is, or is under, one of ``domains``.

    Client-side because it has to be: the provider accepts a domain filter and does not
    apply it. Subdomain-aware and suffix-safe — ``notpandoragroup.com`` does not match
    ``pandoragroup.com``.
    """
    cleaned = _clean_domains(domains)
    if not cleaned:
        # No usable restriction was asked for. Review found `domains=[""]` — a truthy
        # list of nothing — rejecting every URL and reporting them as "outside the
        # requested domains", which is not what happened.
        return True
    host = _host(url)
    if not host:
        return False
    return any(host == d or host.endswith("." + d) for d in cleaned)


def parse_search_payload(
    response: DeepSeekResponse,
    *,
    expected_tool: str = DEFAULT_SEARCH_TOOL_NAME,
    domains: Sequence[str] | None = None,
) -> tuple[list[SourceCandidate], list[str], SearchTrace]:
    """Candidates, warnings and the retrieval trace. Never raises, never invents.

    Rewritten in V3.11.1.2 from the **measured** ``/responses`` contract. The shape it
    used to accept — a function tool call carrying a ``results`` array — is a shape this
    API has never produced.

    ONLY A PAGE THE PROVIDER ACTUALLY OPENED BECOMES A CANDIDATE
    ------------------------------------------------------------
    ``open_page`` and ``find_in_page`` name a URL that was fetched and read.
    A ``search`` action names queries and returns no URLs to us at all. So candidates
    come from the retrieval trace and from nothing else.

    URLs in the answer's prose are deliberately **not** promoted, even now that the
    search is real. A URL the model wrote but never opened is exactly what a model with
    a June 2024 cutoff produces from memory, and the platform already has a name for an
    assertion — a ``ResearchLead``, which the research provider emits and which is
    verified before it counts. What the prose URLs *do* earn is a count: cited-but-never-
    opened is a per-provider quality signal that only exists because the trace exists.
    """
    warnings: list[str] = []

    tool_honoured = any(
        isinstance(t, dict) and str(t.get("type") or "") == expected_tool
        for t in response.tools_echo
    )
    if not tool_honoured:
        warnings.append(
            f"DeepSeek did not echo a {expected_tool!r} tool, which means the request "
            "was accepted and the tool silently dropped — this endpoint returns 200 for "
            "a tool type it does not recognise. No search ran; this is not a statement "
            "about what the web contains."
        )

    queries: list[str] = []
    opened: list[str] = []
    failed: list[str] = []
    off_domain: list[str] = []
    unsafe = 0
    unusable = 0
    unknown_actions: list[str] = []
    failed_queries = 0
    query_calls = 0
    retrieval_calls = 0

    for call in response.tool_payloads:
        if not isinstance(call, dict):
            continue
        action = call.get("action")
        action = action if isinstance(action, dict) else {}
        kind = str(action.get("type") or "")
        completed = str(call.get("status") or "") == "completed"

        if kind == QUERY_ACTION:
            query_calls += 1
            if not completed:
                # A search that errored is effort spent and coverage NOT achieved.
                # Counting its queries as issued without saying they failed overstates
                # what the run looked at.
                failed_queries += 1
            for q in action.get("queries") or []:
                text = str(q or "").strip()
                # The provider slips its own call id into the query list. It is
                # bookkeeping, not a query somebody issued, and counting it would
                # overstate the search effort in every benchmark.
                if text and not text.startswith("ws_call_id="):
                    queries.append(text)
            continue

        if kind not in RETRIEVAL_ACTIONS:
            # Allow-list, deliberately. An unrecognised action may name a URL the
            # provider never opened, and promoting one as retrieved is precisely the
            # error this parser exists to prevent.
            unknown_actions.append(kind or "(no type)")
            continue

        retrieval_calls += 1
        url = canonical_search_url(action.get("url"))
        if not url:
            continue
        if not url.lower().startswith(("http://", "https://")):
            # A candidate that cannot be fetched cannot become evidence.
            unusable += 1
            continue
        if not is_safe_public_host(_host(url)):
            # A cheap, network-free SSRF pre-filter at the candidate boundary, so an
            # internal address never reaches the queue of things the platform is about
            # to fetch. It is NOT the whole guard: the safe fetcher additionally
            # resolves the host and re-checks the IP, which is what actually closes
            # DNS-rebinding.
            unsafe += 1
            continue
        if not completed:
            failed.append(url)
            continue
        if not _domain_allowed(url, domains):
            off_domain.append(url)
            continue
        opened.append(url)

    # Order-preserving de-duplication. The same page opened twice is one candidate and
    # two billed opens, and conflating those makes a wasteful run look thorough.
    seen: dict[str, None] = {}
    for url in opened:
        seen.setdefault(url, None)
    unique_opened = list(seen)

    prose_urls = _extract_prose_urls(response.text)
    trace_urls = {
        _comparable(u) for u in (*unique_opened, *failed, *off_domain)
    }
    cited_never_opened = [u for u in prose_urls if _comparable(u) not in trace_urls]

    if failed:
        warnings.append(
            f"{len(failed)} page(s) DeepSeek tried to open could not be retrieved; they "
            "are not candidates because nothing was read"
        )
    if off_domain:
        warnings.append(
            f"{len(off_domain)} page(s) DeepSeek opened were outside the requested "
            "domains and were dropped here. DeepSeek accepts a domain filter and does "
            "not apply it, so this restriction is enforced client-side"
        )
    if unsafe:
        warnings.append(
            f"{unsafe} URL(s) named a non-public or IP-literal host and were dropped "
            "before any fetch was queued"
        )
    if unusable:
        warnings.append(f"{unusable} URL(s) carried no usable http(s) scheme")
    if unknown_actions:
        warnings.append(
            f"{len(unknown_actions)} DeepSeek tool call(s) used an unrecognised action "
            f"type {sorted(set(unknown_actions))[:5]} and produced no candidates; only "
            f"{sorted(RETRIEVAL_ACTIONS)} name a page the provider actually opened"
        )
    if failed_queries:
        warnings.append(
            f"{failed_queries} of {query_calls} DeepSeek search call(s) failed; their "
            "queries are recorded as attempted, not as coverage achieved"
        )
    if cited_never_opened:
        warnings.append(
            f"{len(cited_never_opened)} URL(s) cited in the answer appear nowhere in the "
            "retrieval trace; they are NOT candidates. A cited page the provider never "
            "opened may be recalled rather than found"
        )
    if tool_honoured and not response.tool_payloads:
        warnings.append(
            "DeepSeek honoured the search tool but issued no search calls; no candidates "
            "were produced. This is not a statement about what the web contains"
        )

    candidates = [
        SourceCandidate(
            url=url,
            provider=PROVIDER_ID,
            # No title, publisher, date, score or snippet: the live contract supplies
            # none of them for an opened page, and inventing any of them here — even a
            # hostname dressed as a publisher — would put a guess in a citation.
        )
        for url in unique_opened[:MAX_CANDIDATES]
    ]
    if len(unique_opened) > MAX_CANDIDATES:
        warnings.append(
            f"DeepSeek opened {len(unique_opened)} distinct pages; the first "
            f"{MAX_CANDIDATES} were kept"
        )

    trace = SearchTrace(
        queries=tuple(queries),
        opened_urls=tuple(unique_opened),
        failed_urls=tuple(dict.fromkeys(failed)),
        cited_but_never_opened=tuple(cited_never_opened),
        off_domain_urls=tuple(dict.fromkeys(off_domain)),
        query_call_count=query_calls,
        failed_query_call_count=failed_queries,
        retrieval_call_count=retrieval_calls,
        unknown_action_count=len(unknown_actions),
        unsafe_host_count=unsafe,
        unusable_url_count=unusable,
        tool_honoured=tool_honoured,
    )
    return candidates, warnings, trace


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

    EVERY BOUND THAT MATTERS IS ENFORCED HERE
    -----------------------------------------
    The live API accepts ``max_tool_calls`` and ``filters.allowed_domains`` and applies
    neither. So this class — not the vendor — is where the domain restriction, the
    candidate ceiling and the de-duplication actually happen, and the module docstring
    of ``transport`` records the measurements that forced that decision.

    What a candidate still is not: evidence. It is a URL InvestingBuddy has not yet
    fetched. Promotion runs through the platform's own guarded fetcher and the
    verification gate, unchanged by this slice.
    """

    transport: DeepSeekTransport
    provider_id: str = PROVIDER_ID
    #: The tool the transport asks for, echoed back on success. Kept here too so the
    #: parser can check the echo against what was actually requested rather than against
    #: a constant that a configuration change would leave behind.
    search_tool_name: str = DEFAULT_SEARCH_TOOL_NAME
    #: Wall-clock ceiling for one search. Real: a search that opens several pages took
    #: 16-60s live, and the timeout is a bound the provider does honour.
    timeout_seconds: int = 120
    #: ``None`` means "read ``v3_deepseek_search_enabled`` at call time". A test that
    #: wants the search path says so explicitly.
    enabled: bool | None = None

    def _is_enabled(self) -> bool:
        """Whether this platform has *consented* to spend on external search.

        THE GATE THIS RESTORES. Until V3.11.1.2, ``search()`` raised unconditionally —
        that hard refusal, not the flag, was what actually kept spend off. Making the
        search work removed the brake, and review found ``v3_deepseek_search_enabled``
        had **no consumer in application code** while three documents claimed it held
        the line.

        It is the same rule the model leg already enforces in ``resolve_routing``, and
        the same rule V3.11.1.1 wrote down: a credential is not consent — and neither is
        a working endpoint.
        """
        if self.enabled is not None:
            return self.enabled
        from app.core.config import settings  # noqa: PLC0415

        return bool(getattr(settings, "v3_deepseek_search_enabled", False))

    def _degraded(self, query: str, reason: str) -> SearchResponse:
        """Empty, honest, and explicitly not a claim about the web."""
        return SearchResponse(
            provider=self.provider_id,
            query=query,
            consumption=ConsumptionUnits(web_search_calls=0, model_calls=0),
            instrumented_units=SEARCH_UNITS,
            cost=CostEstimate(basis="unknown"),
            warnings=[f"{reason} This is not a statement about what the web contains."],
        )

    async def search(
        self, *, query: str, top_k: int = 10, domains: Sequence[str] | None = None
    ) -> SearchResponse:
        if not self._is_enabled():
            return self._degraded(
                query,
                "DeepSeek web search is disabled: V3_DEEPSEEK_SEARCH_ENABLED is off. "
                "The capability is verified to work; spending on it is a separate "
                "decision and nothing enables it implicitly.",
            )
        bounded = max(1, min(int(top_k or 10), MAX_CANDIDATES))
        tool = getattr(self.transport, "search_tool_name", "") or self.search_tool_name
        try:
            response = await self.transport.search(
                query=query,
                top_k=bounded,
                domains=domains,
                timeout=self.timeout_seconds,
            )
        except DeepSeekUnavailableError as exc:
            return self._degraded(query, f"DeepSeek search unavailable: {exc}.")
        candidates, warnings, trace = parse_search_payload(
            response, expected_tool=tool, domains=domains
        )
        if len(candidates) > bounded:
            candidates = candidates[:bounded]

        # A search cut off at its output ceiling has stopped mid-investigation. Reported
        # rather than read as completeness — the same standard `investigate()` applies
        # two classes below, where a truncated result is `partial`, not `completed`.
        truncated = (response.finish_reason or "").lower() in {
            "max_output_tokens",
            "length",
            "max_tokens",
            "incomplete",
        }
        if truncated:
            warnings.append(
                "DeepSeek stopped at its output ceiling "
                f"({response.finish_reason!r}); the search was cut short and its "
                "candidate list is incomplete, not exhaustive"
            )

        return SearchResponse(
            provider=self.provider_id,
            query=query,
            candidates=candidates,
            # Queries and page-opens counted SEPARATELY. One live request issued 3
            # queries and opened 2 pages; reporting "5 web searches" would mis-state
            # both halves of `cost_per_verified_finding`.
            consumption=ConsumptionUnits(
                web_search_calls=trace.query_call_count,
                url_fetch_calls=trace.retrieval_call_count,
                model_calls=1,
                model_input_tokens=response.prompt_tokens,
                model_output_tokens=response.completion_tokens,
                cached_tokens=response.cached_tokens,
            ),
            instrumented_units=SEARCH_UNITS,
            cost=CostEstimate(basis="unknown"),
            warnings=warnings,
            finish_reason=response.finish_reason,
            truncated=truncated,
            raw_provider_metadata={
                "trace": trace.to_dict(),
                "served_model": response.served_model,
                "requested_domains": _clean_domains(domains),
                # Said plainly so no reader of this record infers a guarantee the
                # provider does not give.
                "domain_filter_enforced_by": "investingbuddy_client_side",
                "structured_citations_available": False,
            },
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
    "DeepSeekModelProvider",
    "DeepSeekResearchProvider",
    "DeepSeekSearchProvider",
    "INVESTIGATION_SYSTEM_PROMPT",
    "MAX_CANDIDATES",
    "MAX_LEADS",
    "MODEL_UNITS",
    "parse_leads",
    "parse_search_payload",
    "PROVIDER_ID",
    "RESEARCH_UNITS",
    "SEARCH_UNITS",
    "SearchTrace",
    "UnavailableBrowserProvider",
]
