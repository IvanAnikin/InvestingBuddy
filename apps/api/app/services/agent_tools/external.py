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

``search_web`` reaches a **vendor**. Everything it returns is a claim: a URL the provider
says it opened, a sentence the provider says is true. It mints **no citable id**, and the
absence is the point — a model cannot cite a search result, because the platform has not
seen the document yet.

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
Both tools are registered only when ``V3_DEEPSEEK_SEARCH_ENABLED`` is on. With it off
they are not in the registry, so ``implemented_tools()`` reports them absent and the
Director declares a question that needs one **unassignable at plan time** — a coverage
fact rather than a mid-run mystery. A credential does not register them; a flag does.
"""

from __future__ import annotations

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


async def _search_web(context: "ToolContext", arguments: dict[str, Any]) -> dict[str, Any]:
    """One retrieval-backed external investigation. Returns CLAIMS, never evidence.

    The provider searches, opens pages and reports what it found. What comes back is a
    list of ``ResearchLead``-shaped claims, each carrying a URL the provider says it
    opened — and every one of them is still a claim. The payload has no ``evidence_id``
    anywhere in it, which is what stops the Investigator citing a search result: the
    model may only cite ids the tools returned, and this tool returns none.

    A lead whose URL is **not in the retrieval trace** is counted and **kept**. An
    earlier draft dropped them, and a live run showed why that is wrong: the provider
    found the right SEC exhibit through a *search result*, whose URLs this contract never
    exposes, and the guard discarded the only good lead of the run. ``opened_urls`` is a
    subset of what the provider legitimately saw, so absence from it is a quality signal
    and not a verdict — the verdict belongs to our own fetch (ADR-056 §4).
    """
    from app.services.agents.routing import research_provider_for

    provider = research_provider_for(context.cfg)
    if provider is None:
        return {
            "available": False,
            "reason": (
                "No external research provider is configured and enabled. This is a "
                "statement about this platform's configuration, not about the web."
            ),
            "leads": [],
            "candidates": [],
        }

    question = arguments["query"]
    result = await provider.investigate(
        question=question,
        context=arguments.get("context"),
        max_seconds=int(
            getattr(context.cfg, "v3_external_search_timeout_seconds", 0) or 180
        ),
        domains=arguments.get("domains") or None,
    )

    metadata = result.raw_provider_metadata or {}
    trace = metadata.get("trace") or {}
    spent = result.consumption
    leads = [
        {
            # Deliberately NOT "evidence_id" and NOT "id": `_citation_of` in the
            # investigator looks for exactly those keys, and a claim must not be
            # citable. `lead_ref` is an index into this payload, nothing more.
            "lead_ref": index,
            "claim": (lead.claim_text or "")[:MAX_CLAIM_CHARS],
            "claimed_source_url": lead.claimed_source_url,
            "claimed_value": lead.claimed_value,
            "claimed_unit": lead.claimed_unit,
            "claimed_currency": lead.claimed_currency,
            "claimed_period": lead.claimed_period,
            "claimed_scope": lead.claimed_scope,
            "claimed_publisher": lead.claimed_publisher,
            "verified_by_investingbuddy": False,
        }
        for index, lead in enumerate(result.research_leads[:MAX_LEADS_RETURNED])
    ]
    return {
        "available": True,
        "status": result.status,
        "provider": result.provider,
        "model": result.model,
        "leads": leads,
        # The pages the provider actually opened. Candidates for OUR fetcher, not
        # sources — nothing here has been retrieved by this platform yet.
        "candidates": list(trace.get("opened_urls") or []),
        "queries_issued": list(trace.get("queries") or []),
        # Named for what it is. It was `dropped_…` while an earlier draft dropped them;
        # they are kept now, and a key that says "dropped" about kept leads is the kind
        # of quiet inaccuracy a reader has no way to catch.
        "leads_citing_unopened_pages_kept": metadata.get(
            "leads_citing_unopened_pages", 0
        ),
        "answer_excerpt": (result.raw_provider_metadata or {}).get("answer_excerpt"),
        "warnings": list(result.warnings or []),
        # What this call actually cost, reported in the units the spec declares. A tool
        # that declares units and reports none leaves the run's consumption record
        # silently short by the most expensive call in it — and `cost_per_verified_
        # finding`, the metric the whole provider strategy turns on, is computed from
        # exactly these rows.
        "consumption": units_for(
            SEARCH_WEB_UNITS,
            web_search_calls=spent.web_search_calls,
            url_fetch_calls=spent.url_fetch_calls,
            model_calls=spent.model_calls,
            model_input_tokens=spent.model_input_tokens,
            model_output_tokens=spent.model_output_tokens,
            cached_tokens=spent.cached_tokens,
        ),
        "note": (
            "Every item here is a CLAIM from an external vendor. None of it is evidence "
            "and none of it is citable. Use fetch_public_source to have InvestingBuddy "
            "retrieve and verify a claim before relying on it."
        ),
    }


SEARCH_WEB_SPEC = ToolSpec(
    name=TOOL_SEARCH_WEB,
    description=(
        "Ask the configured external research provider a question. It searches the web, "
        "opens pages and returns CLAIMS with the URLs it opened. Nothing it returns is "
        "evidence and nothing it returns is citable — use fetch_public_source to verify "
        "a claim against a document InvestingBuddy retrieves itself."
    ),
    handler=_search_web,
    validate_arguments=_validate_search_web,
    # Declared, because `ToolSpend.would_exceed` reads THIS and not the consumption a
    # call reports afterwards — a budget is checked before the spend or it is a report.
    # Zero here would make `ToolBudget(max_searches=…)` inert against the only two tools
    # that spend money outside the platform, which is the one place it must not be.
    # `fetches=1` too: this provider retrieves pages as part of searching.
    cost=ToolCost(searches=1, fetches=1),
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
        "provider": str(arguments.get("provider") or "external").strip()[:64],
    }


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
    from app.services.providers.leads import known_leads_for, persist_lead, verify_lead

    lead = ResearchLead(
        claim_text=arguments["claim"],
        provider=arguments["provider"],
        model=None,
        provider_task_id=str(uuid.uuid4()),
        claimed_source_url=arguments["url"],
        claimed_publisher=arguments.get("claimed_publisher"),
        claimed_value=arguments.get("claimed_value"),
        claimed_period=arguments.get("claimed_period"),
        claimed_scope=arguments.get("claimed_scope"),
    )

    session = getattr(context, "session", None)
    subject = str(context.company_id) if context.company_id else None
    known: Sequence[Any] = ()
    if session is not None and context.company_id is not None:
        try:
            known = await known_leads_for(session, company_id=context.company_id)
        except Exception:  # noqa: BLE001 - a lookup failure must not block the gate
            known = ()

    outcome = await verify_lead(
        lead,
        cfg=context.cfg,
        # The lead's own host becomes the allowlist. Not "anything": every other guard
        # in the fetcher still applies, and a redirect off that host is still refused.
        allow_public_web=True,
        known_leads=known,
        subject=subject,
    )

    # Minted BEFORE persisting, so the row records the id a finding will actually cite.
    minted = (
        external_evidence_id(
            outcome.content_hash, outcome.fetched_url or arguments["url"]
        )
        if outcome.status == LEAD_VERIFIED and outcome.content_hash
        else None
    )

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
                    company_id=context.company_id,
                    legal_entity_id=context.legal_entity_id,
                    subject=subject,
                    promoted_evidence_id=minted,
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

    if minted:
        record["evidence_id"] = minted
        record["claim"] = arguments["claim"]
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
    """Whether the external tool surface exists at all for this process.

    One flag, read in one place. `V3_DEEPSEEK_SEARCH_ENABLED` gates the vendor call in
    `DeepSeekSearchProvider`; this gates whether the *tool* is registered, which is what
    decides whether the Director can plan a question that needs it. Both, because a
    surface that exists and always refuses teaches an agent to keep asking.
    """
    return bool(getattr(cfg, "v3_deepseek_search_enabled", False))


def register_external_tools(
    registry: "ToolRegistry", *, cfg: Any = None
) -> "ToolRegistry":
    """Register the external tools **only when the flag is on**.

    Absent, ``implemented_tools()`` does not list them and the Director marks a question
    that needs one unassignable at plan time. That is the fail-closed direction: a
    coverage fact a reader can see, rather than a tool refusal mid-run.
    """
    if cfg is None:
        from app.core.config import settings as cfg  # noqa: PLW0127
    if not external_tools_enabled(cfg):
        return registry
    registry.register(SEARCH_WEB_SPEC)
    registry.register(FETCH_PUBLIC_SOURCE_SPEC)
    return registry


__all__ = [
    "EXTERNAL_EVIDENCE_PREFIX",
    "FETCH_PUBLIC_SOURCE_SPEC",
    "MAX_LEADS_RETURNED",
    "SEARCH_WEB_SPEC",
    "external_evidence_id",
    "external_tools_enabled",
    "register_external_tools",
]
