"""The two external tools — V3.12 Slice 12.1.

``search_web`` and ``fetch_public_source`` are the last two names in the V3.3 tool
vocabulary. V3.3 reserved them; ``builtin`` recorded that they "wait for the provider
runtime in V3.4"; V3.11.1.2 established that the runtime works. This module is that wait
ending, and it is deliberately the smallest surface that can close the gap the release
candidate names in §11.6:

    the ``ResearchLead`` -> Evidence path has never run with a real external provider.

WHY TWO TOOLS AND NOT ONE
=========================
Because they answer to different authorities, and merging them would hide that.

``search_web`` queries the **configured web search provider** (open-web W5; before W5 it
asked a DeepSeek model to "search"). What it returns is a list of CANDIDATES: a URL, a
title, a domain, a rank and a published hint, all of it untrusted third-party text. It
carries no claim, mints **no citable id**, and the absence is the point — a model cannot
cite a search result, because the platform has not seen the document yet.

``fetch_public_source`` reaches the **open web through InvestingBuddy's own fetcher** and
puts one claim through ``verify_lead``. Only that path can mint an evidence id, and it
mints one only when the gate returned ``verified`` — which by construction means the
platform fetched the bytes itself and holds their hash.

So the id an Investigator may cite exists if and only if InvestingBuddy retrieved and
verified the document. That is the whole promotion rule, expressed as which function is
allowed to build which payload.

WHAT THIS MODULE DOES NOT DO
============================
It adds no fetching, no SSRF logic, no redirect policy, no byte cap and no host
validation. Every one of those already exists in ``safe_web_fetcher`` and is reached
through ``verify_lead``, which is where the guarantees live and where the tests for them
already are. A second implementation of a security boundary is two boundaries that
disagree.

FLAGS
=====
``search_web`` is registered only when the company web path is on AND a web search
provider is selected (``routing.web_search_provider_for``: ``V3_COMPANY_WEB_RESEARCH_ENABLED``,
``V3_WEB_SEARCH_ENABLED`` and ``V3_WEB_SEARCH_PROVIDER`` ≠ ``none``). ``fetch_public_source``
needs no provider, so it is also registered under the legacy
``V3_DEEPSEEK_SEARCH_ENABLED`` (no regression for an environment that has it on).
With a tool unregistered, ``implemented_tools()`` reports it absent and the Director
declares a question that needs it **unassignable at plan time** — a coverage fact rather
than a mid-run mystery. A credential does not register them; a flag does.
``V3_DEEPSEEK_SEARCH_ENABLED`` no longer registers ``search_web`` (W5, decision U12): it
still gates the DeepSeek recall paths in Discovery, which are labelled ``model_recall``.
"""

from __future__ import annotations

import asyncio
import hashlib
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

from app.services.agent_tools.contracts import (
    TOOL_FETCH_PUBLIC_SOURCE,
    TOOL_SEARCH_WEB,
    ToolCost,
    ToolSpec,
    units_for,
)
from app.services.providers.contracts import (
    LEAD_VERIFIED,
    ResearchLead,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

#: Evidence ids minted here. Distinct from the corpus's ``ev:c:`` on purpose: a reader
#: of a citation should be able to tell that this one came from the open web and was
#: verified against bytes we fetched, rather than from a document already in the corpus.
EXTERNAL_EVIDENCE_PREFIX = "ev:x:"

#: Units the search tool measures. Named once so the spec and the payload cannot drift:
#: a unit declared and not reported is stored as a zero that asserts it did not happen.
SEARCH_WEB_UNITS: tuple[str, ...] = (
    "web_search_calls",
    # Open-web W5: the provider bills in credits (Tavily), and a search that reached the
    # vendor without a credit figure marks them ``unreported``.
    "tavily_credits",
    "url_fetch_calls",
    "model_calls",
    "model_input_tokens",
    "model_output_tokens",
    "cached_tokens",
)

#: The fetch tool measures exactly one thing: our own retrieval.
FETCH_UNITS: tuple[str, ...] = ("url_fetch_calls",)

#: How many leads one search may return to the prompt. A provider that returns forty
#: claims is a provider whose forty claims we would pay to verify.
MAX_LEADS_RETURNED = 8

#: How much of a provider's claim reaches a payload. Untrusted text, fenced by the
#: prompt builder — but a fence around ten thousand words is still ten thousand words of
#: somebody else's writing in our token budget. (The answer excerpt is capped separately,
#: at the point it is produced, in `providers.py`.)
MAX_CLAIM_CHARS = 600


def _canonical_period(raw: str | None) -> str | None:
    """The platform's own period identity for a provider's period string, or ``None``."""
    from app.services.sources.financial_period import parse_period

    if not raw:
        return None
    period = parse_period(raw)
    return period.key if not period.is_unknown else None


def external_evidence_id(content_hash: str, url: str) -> str:
    """The id minted for one verified external source.

    Derived from the **content hash of the bytes InvestingBuddy fetched** plus the URL,
    so the same document verified twice in a run is the same id, and a different document
    at the same URL is a different one. Neither input comes from the provider.
    """
    digest = hashlib.sha256(f"{content_hash}|{url}".encode()).hexdigest()[:24]
    return f"{EXTERNAL_EVIDENCE_PREFIX}{digest}"


# ── search_web ───────────────────────────────────────────────────────────── #


def _validate_search_web(arguments: dict[str, Any]) -> dict[str, Any]:
    query = str(arguments.get("query") or "").strip()
    if not query:
        raise ValueError("search_web needs a query.")
    if len(query) > 500:
        raise ValueError("search_web query is too long; ask a narrower question.")
    domains = arguments.get("domains") or []
    if not isinstance(domains, list | tuple):
        raise ValueError("domains must be a list of hostnames.")
    return {
        "query": query,
        "domains": [str(d).strip() for d in domains if str(d).strip()][:20],
        "context": str(arguments.get("context") or "").strip()[:1000] or None,
    }


#: Candidates one ``search_web`` call returns, in rank order.
MAX_CANDIDATES_RETURNED = 10

#: The ``QueryFamily`` an Investigator search is recorded under: a follow-up on a
#: question's gap (spec §4.2 ``GAP``).
INVESTIGATOR_ORIGIN = "investigator"


def _candidate_of(item: Any) -> dict[str, Any]:
    """One search result as the Investigator sees it: a pointer, never a claim.

    ``title`` is untrusted third-party text kept for display and for the model to choose
    what to look at; there is no snippet, no ``claim`` and no id. Nothing here is
    ``evidence_id``-shaped, so ``_citation_of`` in the investigator cannot read one.
    """
    published = getattr(item, "published_hint", None)
    return {
        "rank": int(item.rank),
        "url": item.url,
        "domain": item.domain,
        "title": (item.title or "")[:200] or None,
        "published_hint": published.isoformat() if published else None,
        "untrusted": True,
        "verified_by_investingbuddy": False,
    }


async def _search_web(context: "ToolContext", arguments: dict[str, Any]) -> dict[str, Any]:
    """One external search on the configured provider. Returns CANDIDATES, never evidence.

    The query is cleaned by the W1 sanitiser, then goes through ``run_searches`` — the
    one path that sends a query to a provider — so it is gated (operators, URLs, private
    tokens, blocklist), governed, budgeted, cached, and written as a provenance row
    exactly like the company web stage's searches. Whether anything was searched is read
    from the NETWORK FACT in that row (spec §22.3): an unconfigured provider, an outage
    or an exhausted budget is ``web_search_unavailable`` and a payload with no candidates;
    nothing here substitutes a model's recall for it.

    The payload has no ``leads`` (an empty list, kept so older readers find the key), no
    ``claim`` and no ``evidence_id``/``id`` anywhere in it. ``fetch_public_source``
    remains the only function that may mint an evidence id.
    """
    from app.services.agents.routing import web_search_provider_for
    from app.services.providers.contracts import QueryFamily, SearchRequest
    from app.services.web_research.planner import QUERY_TEMPLATE_VERSION
    from app.services.web_research.queries import sanitise_query
    from app.services.web_research.search import (
        STATE_DISABLED,
        SearchContext,
        run_searches,
    )
    from app.services.web_research.stage import persist_factory_for

    unavailable = {
        "available": False,
        "web_search": "web_search_unavailable",
        "reason": (
            "No web search provider is configured and enabled. This is a statement about "
            "this platform's configuration, not about the web."
        ),
        "leads": [],
        "candidates": [],
    }
    provider = web_search_provider_for(context.cfg)
    if provider is None:
        return unavailable

    cleaned = sanitise_query(arguments["query"])
    if not cleaned.ok:
        return {
            **unavailable,
            "available": True,
            "reason": f"the query was refused before it left the platform ({cleaned.refusal})",
            "refusal": cleaned.refusal,
        }
    request = SearchRequest(
        query=cleaned.text,
        family=QueryFamily.GAP,
        max_results=MAX_CANDIDATES_RETURNED,
        include_domains=tuple(arguments.get("domains") or ()),
        origin=INVESTIGATOR_ORIGIN,
        template_version=f"{QUERY_TEMPLATE_VERSION}:investigator",
    )
    run = await run_searches(
        context.session,
        [request],
        SearchContext(
            research_job_id=context.research_job_id,
            company_id=context.company_id,
            stage="investigator",
            budget_profile="followup",
        ),
        provider=provider,
        cfg=context.cfg,
        persist_session_factory=persist_factory_for(context.session),
    )
    outcome = run.outcomes[0] if run.outcomes else None
    executed = bool(outcome is not None and outcome.execution.executed)
    candidates = (
        [_candidate_of(item) for item in outcome.results[:MAX_CANDIDATES_RETURNED]]
        if executed and outcome is not None
        else []
    )
    if run.state == STATE_DISABLED:
        return unavailable
    if executed:
        summary = (
            f"search_web: {len(candidates)} candidate URL(s) from 1 executed search; "
            "none retrieved or verified yet"
        )
    else:
        code = outcome.execution.error_code if outcome is not None else None
        summary = (
            "web_search_unavailable: the provider executed no search"
            + (f" ({code})" if code else "")
        )
    return {
        "available": True,
        "summary": summary,
        "discovery_mode": "search" if executed else "web_search_unavailable",
        "web_search": "executed" if executed else "web_search_unavailable",
        "executed_search_queries": 1 if executed else 0,
        "state": run.state,
        "provider": run.provider,
        # Kept so a reader written for the claim-shaped payload finds an empty list, not
        # a missing key. There are no claims here.
        "leads": [],
        "candidates": candidates,
        "consumption": run.consumption,
        "note": (
            "Every item here is a CANDIDATE URL from a search provider: untrusted, "
            "unretrieved, not evidence and not citable. Use fetch_public_source to have "
            "InvestingBuddy retrieve and verify a claim against a document."
        ),
    }


SEARCH_WEB_SPEC = ToolSpec(
    name=TOOL_SEARCH_WEB,
    description=(
        "Search the web through the configured search provider. Returns CANDIDATE URLs "
        "(url, title, domain, rank, published hint) — untrusted pointers, not claims. "
        "Nothing it returns is evidence and nothing it returns is citable; "
        "fetch_public_source retrieves a document and verifies a claim against it."
    ),
    handler=_search_web,
    validate_arguments=_validate_search_web,
    # Declared, because `ToolSpend.would_exceed` reads THIS and not the consumption a
    # call reports afterwards — a budget is checked before the spend or it is a report.
    # `fetches=0` now: the provider returns URLs; the platform's own fetcher retrieves.
    cost=ToolCost(searches=1),
    instrumented_units=SEARCH_WEB_UNITS,
    access_classes=("public_web",),
    may_contain_untrusted_content=True,
)


# ── fetch_public_source ──────────────────────────────────────────────────── #


def _validate_fetch_public_source(arguments: dict[str, Any]) -> dict[str, Any]:
    url = str(arguments.get("url") or "").strip()
    if not url:
        raise ValueError("fetch_public_source needs a url.")
    if not url.lower().startswith(("http://", "https://")):
        raise ValueError("fetch_public_source takes an http(s) URL.")
    claim = str(arguments.get("claim") or "").strip()
    if not claim:
        raise ValueError(
            "fetch_public_source needs the claim to check. Fetching a page without a "
            "claim to verify against it retrieves bytes and decides nothing."
        )
    return {
        "url": url,
        "claim": claim[:MAX_CLAIM_CHARS],
        "claimed_value": str(arguments.get("claimed_value") or "").strip() or None,
        "claimed_period": str(arguments.get("claimed_period") or "").strip() or None,
        "claimed_scope": str(arguments.get("claimed_scope") or "").strip() or None,
        "claimed_publisher": str(arguments.get("claimed_publisher") or "").strip() or None,
        "claimed_unit": str(arguments.get("claimed_unit") or "").strip()[:40] or None,
        "claimed_currency": str(arguments.get("claimed_currency") or "").strip()[:10] or None,
        "claimed_metric": str(arguments.get("claimed_metric") or "").strip()[:120] or None,
        "claimed_geography": str(arguments.get("claimed_geography") or "").strip()[:80] or None,
        "provider": str(arguments.get("provider") or "external").strip()[:64],
    }


async def _company_name(session: Any, company_id: Any) -> str | None:
    """The researched company's name, whose words never count as a claim's context."""
    from sqlalchemy import select

    from app.models.company import Company

    try:
        # A SAVEPOINT for the reason `persist_lead` below has one: an error swallowed
        # here must not leave the V2 report's transaction aborted.
        async with session.begin_nested():
            name = await session.scalar(
                select(Company.name).where(Company.id == company_id)
            )
        return str(name) if name else None
    except Exception:  # noqa: BLE001 - a lookup failure costs the exclusion, not the gate
        return None


async def _fetch_public_source(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    """Put one external claim through the platform's own retrieval and verification.

    This is the only function in the external path that may mint an evidence id, and it
    mints one only for ``LEAD_VERIFIED`` — a status ``LeadVerificationOutcome`` refuses
    to construct without a content hash, so "verified" cannot exist without bytes we
    fetched. Every other status returns a payload with **no citable id** and the reason,
    because a rejected lead is a research fact worth recording and is not a source.

    The fetch itself is ``verify_lead``'s, which means HTTPS-only, no internal or
    IP-literal host, DNS resolution to a public address, a redirect chain that may not
    leave the cited host, and the byte cap — all of it the existing boundary, none of it
    reimplemented here.
    """
    from app.services.providers.leads import (
        known_leads_for,
        lead_key_for,
        persist_lead,
        reusable_verification,
        slot_key_for,
        verify_lead,
    )
    from app.services.sources.publisher_tiers import publisher_tier
    from app.services.sources.safe_web_fetcher import open_web_fetch_refusal

    # W0 / D13: this is the open-web fetch (the host is model-chosen). On a runtime
    # whose `ipaddress` classification is not trusted, refuse before anything else.
    runtime_refusal = open_web_fetch_refusal()
    if runtime_refusal is not None:
        return {
            "items": [],
            "verified": False,
            "refused": True,
            "summary": f"refused: {runtime_refusal}",
            "consumption": units_for(FETCH_UNITS, url_fetch_calls=0),
        }

    lead = ResearchLead(
        claim_text=arguments["claim"],
        provider=arguments["provider"],
        model=None,
        provider_task_id=str(uuid.uuid4()),
        claimed_source_url=arguments["url"],
        claimed_publisher=arguments.get("claimed_publisher"),
        claimed_value=arguments.get("claimed_value"),
        claimed_unit=arguments.get("claimed_unit"),
        claimed_currency=arguments.get("claimed_currency"),
        claimed_period=arguments.get("claimed_period"),
        claimed_scope=arguments.get("claimed_scope"),
        claimed_metric=arguments.get("claimed_metric"),
        claimed_geography=arguments.get("claimed_geography"),
    )

    session = getattr(context, "session", None)
    subject = str(context.company_id) if context.company_id else None
    known: Sequence[Any] = ()
    subject_name: str | None = None
    if session is not None and context.company_id is not None:
        try:
            known = await known_leads_for(session, company_id=context.company_id)
        except Exception:  # noqa: BLE001 - a lookup failure must not block the gate
            known = ()
        subject_name = await _company_name(session, context.company_id)

    # V3.18.3 — a claim this platform ALREADY verified, for this company, is cited again
    # rather than rejected. The gate's duplicate rule exists so one claim is not recorded
    # twice; it was also, in effect, a rule that a fact verified in last week's run could
    # never be cited in this week's — so every re-run of a company lost its external
    # evidence. The evidence id is the one minted from bytes this platform fetched then;
    # nothing is re-asserted, and no new lead row is written.
    #
    # Only a verification that kept its matched passage, and a recent one: see
    # `reusable_verification`.
    key = lead_key_for(lead, subject=subject)
    reusable = reusable_verification(
        known, key, slot_key=slot_key_for(lead, subject=subject)
    )
    if reusable is not None:
        reused: dict[str, Any] = {
            "url": arguments["url"],
            "status": LEAD_VERIFIED,
            "verified": True,
            "reused_from_earlier_verification": True,
            "evidence_id": reusable.promoted_evidence_id,
            "claim": reusable.claim_text or arguments["claim"],
            "source_excerpt": reusable.matched_excerpt,
            "fetched_url": reusable.fetched_url,
            "source_tier": publisher_tier(reusable.fetched_url),
            "period_key": (
                _canonical_period(reusable.claimed_period)
                if reusable.period_verified
                else None
            ),
            "scope_key": None,
            # The labels stored WITH the verification, never this call's arguments:
            # nothing checked today's labels against the stored page.
            "claimed_metric": reusable.claimed_metric,
            "claimed_unit": reusable.claimed_unit,
            "claimed_currency": reusable.claimed_currency,
            "claimed_geography": reusable.claimed_geography,
        }
        return {
            "items": [reused],
            "verified": True,
            "consumption": units_for(FETCH_UNITS, url_fetch_calls=0),
        }

    outcome = await verify_lead(
        lead,
        cfg=context.cfg,
        # The lead's own host becomes the allowlist. Not "anything": every other guard
        # in the fetcher still applies, and a redirect off that host is still refused.
        allow_public_web=True,
        known_leads=known,
        subject=subject,
        subject_name=subject_name,
    )

    # Minted BEFORE persisting, so the row records the id a finding will actually cite.
    minted = (
        external_evidence_id(
            outcome.content_hash, outcome.fetched_url or arguments["url"]
        )
        if outcome.status == LEAD_VERIFIED and outcome.content_hash
        else None
    )

    # Open-web W3: a VERIFIED document joins the corpus through the one web write path
    # (dedup by content hash), so the `ev:x:` id resolves to a stored version. Its own
    # savepoint: a failed ingestion costs the link, never the verification.
    version_id = None
    stored_lead: Any = None
    if session is not None and minted and getattr(outcome, "fetched_content", None):
        stored_lead = await _ingest_verified(context, outcome, arguments["url"])
        version_id = stored_lead.version_id if stored_lead is not None else None

    if session is not None:
        try:
            # A SAVEPOINT, because the bare `except` below is otherwise a trap: a
            # database error here aborts the whole transaction, and swallowing the
            # exception leaves the caller to discover it at commit — by which point the
            # V2 report is lost too. That is not hypothetical; it is exactly how the
            # `research_job_id` foreign key destroyed the report before V3.13.1. The
            # savepoint means a failed record costs the record and nothing else.
            async with session.begin_nested():
                await persist_lead(
                    session,
                    lead,
                    outcome,
                    # V3.17.9. `persist_lead` has taken this since V3.13 and no caller
                    # passed it, so every `research_leads` row was unattributable to the
                    # job that paid the provider for it.
                    research_job_id=context.research_job_id,
                    company_id=context.company_id,
                    legal_entity_id=context.legal_entity_id,
                    subject=subject,
                    promoted_evidence_id=minted,
                    research_document_version_id=version_id,
                )
        except Exception:  # noqa: BLE001 - the decision stands even if the record fails
            pass

    record: dict[str, Any] = {
        "url": arguments["url"],
        "status": outcome.status,
        "verified": outcome.verified,
        "rejection_reason": outcome.rejection_reason,
        "detail": outcome.detail,
        "fetch_attempted": outcome.fetch_attempted,
        "fetched_url": outcome.fetched_url,
        "content_hash": outcome.content_hash,
        "period_verified": outcome.period_verified,
        "scope_verified": outcome.scope_verified,
        # `period_verified` above says whether the claim's period was CONFIRMED against
        # the periods the retrieved document names about itself. False means one of two
        # different things — the claim named no period, or the document named none the
        # scan could read — and neither is a refutation. A conflicting period is not
        # reported here at all: it is a rejection, above.
        #
        # Scope is the honest gap. `verify_lead` compares a claimed scope only against
        # one the platform independently determined, and a raw public fetch produces
        # none — the extraction that does runs over corpus documents. So a Group figure
        # claimed as a segment one would not be caught on this path, and the consequence
        # is carried below: the evidence travels with no scope_key, so a finding
        # inherits none.
        "scope_checked": False,
        "scope_limitation": (
            "InvestingBuddy did not independently determine this document's reporting "
            "scope, so the claim's scope was not compared against it. The evidence "
            "carries no scope_key and a finding built on it inherits none."
        ),
    }

    # Who published the page, from its host. Never a verdict on the claim — `verify_lead`
    # is that — but what the question's evidence contract needs to tell an independent
    # statistical agency from a content farm.
    record["source_tier"] = publisher_tier(outcome.fetched_url or arguments["url"])
    if minted:
        record["evidence_id"] = minted
        # Whether the verified bytes are ALSO in the corpus. None is "verified, not
        # stored" (refused by TDM / a wall / the flag), never "missing evidence".
        record["corpus_version_id"] = str(version_id) if version_id else None
        # Open-web W4 (review M5): the STORED version's class and origin, so the
        # investigator ranks and counts the lead exactly as a later read of the version
        # will — not by a different origin string. Absent when nothing was stored.
        if stored_lead is not None:
            record["origin_key"] = stored_lead.origin_key
            record["stored_source_class"] = stored_lead.source_class
        record["claim"] = arguments["claim"]
        # The document's OWN words at the point verification matched. A finding built on
        # this lead reads the source, not the provider's paraphrase of it.
        record["source_excerpt"] = getattr(outcome, "matched_excerpt", None)
        record["verification_basis"] = getattr(outcome, "verification_basis", None)
        record["claimed_value"] = arguments.get("claimed_value")
        record["claimed_unit"] = arguments.get("claimed_unit")
        record["claimed_currency"] = arguments.get("claimed_currency")
        record["claimed_metric"] = arguments.get("claimed_metric")
        record["claimed_geography"] = arguments.get("claimed_geography")
        # Period and scope travel with the evidence ONLY where the platform confirmed
        # them against the document. Period now can be: `periods_in` reads the periods a
        # retrieved document names about itself, so a claim matching one of them at the
        # same granularity is confirmed against OUR bytes. Scope still cannot — see
        # `scope_checked` below. A provider-asserted period passed through unconfirmed
        # would be indistinguishable, downstream, from one the platform verified.
        # CANONICAL, not the provider's wording. `parse_period(...).key` is the identity
        # the rest of the platform compares on — a vendor's "Q2 2026" set beside corpus
        # evidence's "2026-Q2" reads as a conflict and opens a `conflicting_sources` gap
        # about two descriptions of the same quarter. Latent while `period_verified` was
        # always False; `periods_in` makes it routine.
        record["period_key"] = (
            _canonical_period(arguments.get("claimed_period"))
            if outcome.period_verified
            else None
        )
        record["scope_key"] = (
            arguments.get("claimed_scope") if outcome.scope_verified else None
        )
    else:
        record["evidence_id"] = None
        record["note"] = (
            "Not verified, so there is no citable id. A claim InvestingBuddy could not "
            "confirm against a document it retrieved itself is not a source."
        )
    # `items` is the platform's payload convention — `_harvest` in the investigator
    # reads it, and a tool returning a bare record contributes nothing citable however
    # well it verified. One result, because one fetch decides one claim.
    return {
        "items": [record],
        "verified": record.get("evidence_id") is not None,
        # One fetch, counted only when one actually happened: a policy refusal decided
        # before the network is not a retrieval, and reporting it as one would inflate
        # the denominator of every cost-per-finding figure.
        "consumption": units_for(
            FETCH_UNITS,
            url_fetch_calls=1 if outcome.fetch_attempted else 0,
        ),
    }


#: Bound on preparing a verified lead's document for the corpus (robots/TDMRep check,
#: pool warm-up and extraction). Generous next to the extraction kill timeout; the
#: point is that it is bounded and runs OUTSIDE any database transaction.
LEAD_INGEST_PREPARE_TIMEOUT_SECONDS = 180.0


async def _ingest_verified(context: "ToolContext", outcome: Any, url: str) -> Any:
    """The stored ``WebIngestResult`` of a verified lead's bytes, or ``None``. Never raises.

    Gated by ``V3_WEB_CORPUS_INGEST_ENABLED``. Two phases (W3 review F6): everything
    slow — robots/TDMRep, the pool, extraction — runs FIRST, outside any transaction and
    under a tool-level timeout; only the writes run inside a SAVEPOINT on the research
    transaction, so a failure there costs the corpus link and nothing else.
    """
    from app.services.web_research.ingest import (
        PreparedLeadDocument,
        ingest_enabled,
        prepare_verified_lead_document,
        store_verified_lead_document,
    )

    if not ingest_enabled(context.cfg) or context.company_id is None:
        return None
    try:
        prepared = await asyncio.wait_for(
            prepare_verified_lead_document(
                content=outcome.fetched_content,
                url=url,
                fetched_url=outcome.fetched_url,
                truncated=bool(getattr(outcome, "fetched_truncated", False)),
                headers=dict(getattr(outcome, "fetched_headers", None) or {}),
                cfg=context.cfg,
                company_id=context.company_id,
                research_job_id=context.research_job_id,
                web_search_result_id=getattr(outcome.lead, "web_search_result_id", None),
                provider="lead",
            ),
            timeout=LEAD_INGEST_PREPARE_TIMEOUT_SECONDS,
        )
    except Exception:  # noqa: BLE001 - the verification stands without the corpus link
        return None
    if not isinstance(prepared, PreparedLeadDocument):
        return None  # refused (TDM, wall, robots, …) or not extracted: nothing stored
    session = context.session
    try:
        async with session.begin_nested():
            ingested = await store_verified_lead_document(
                session,
                prepared,
                cfg=context.cfg,
                backend=getattr(context, "search_backend", None),
            )
    except Exception:  # noqa: BLE001 - the verification stands without the corpus link
        return None
    return ingested if ingested.stored else None


FETCH_PUBLIC_SOURCE_SPEC = ToolSpec(
    name=TOOL_FETCH_PUBLIC_SOURCE,
    description=(
        "Have InvestingBuddy retrieve a public URL through its own guarded fetcher and "
        "check whether a specific claim is actually supported by the document. Returns a "
        "citable evidence_id ONLY when verification passed against bytes InvestingBuddy "
        "fetched itself."
    ),
    handler=_fetch_public_source,
    validate_arguments=_validate_fetch_public_source,
    cost=ToolCost(fetches=1, documents=1),
    instrumented_units=FETCH_UNITS,
    access_classes=("public_web",),
    may_contain_untrusted_content=True,
)


def external_tools_enabled(cfg: Any) -> bool:
    """Whether ``search_web`` exists for this process (kept under its old name).

    The company web path is on AND a web search provider is selected
    (``routing.web_search_provider_for``). ``V3_DEEPSEEK_SEARCH_ENABLED`` no longer counts
    (W5, decision U12).
    """
    from app.services.agents.routing import web_search_provider_for

    return web_search_provider_for(cfg) is not None


def fetch_public_source_enabled(cfg: Any) -> bool:
    """Whether ``fetch_public_source`` exists for this process.

    It needs NO search provider — it retrieves a URL the caller names and verifies a claim
    against the bytes — so it must not vanish just because the new search flags are off.
    It stays available under the legacy ``V3_DEEPSEEK_SEARCH_ENABLED`` as before W5 (that
    flag registered both tools; it now registers only this one), and with the new web path.
    """
    return external_tools_enabled(cfg) or bool(
        getattr(cfg, "v3_deepseek_search_enabled", False)
    )


def register_external_tools(
    registry: "ToolRegistry", *, cfg: Any = None
) -> "ToolRegistry":
    """Register the external tools. The only tools whose presence is a spending decision.

    Absent, ``implemented_tools()`` does not list them and the Director marks a question
    that needs one unassignable at plan time. That is the fail-closed direction: a
    coverage fact a reader can see, rather than a tool refusal mid-run.
    """
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    if external_tools_enabled(cfg):
        registry.register(SEARCH_WEB_SPEC)
    if fetch_public_source_enabled(cfg):
        registry.register(FETCH_PUBLIC_SOURCE_SPEC)
    return registry


__all__ = [
    "EXTERNAL_EVIDENCE_PREFIX",
    "FETCH_PUBLIC_SOURCE_SPEC",
    "MAX_CANDIDATES_RETURNED",
    "MAX_LEADS_RETURNED",
    "SEARCH_WEB_SPEC",
    "external_evidence_id",
    "external_tools_enabled",
    "fetch_public_source_enabled",
    "register_external_tools",
]
