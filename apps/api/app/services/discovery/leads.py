"""External company discovery: LEADS, never candidates — V3.19.4.

THE LIMIT THIS REMOVES
======================
A discovery universe could only ever contain the ~60 companies hard-coded in
``THEME_COMPANY_REGISTRY``: a "European luxury" run returned the same eight names every
time, a "gallium and germanium" run returned US copper miners, and the V3.18 peer set was
"whatever the platform already holds". Nothing the platform did not already know could be
researched.

WHAT THIS DOES
==============
Asks the configured external research provider — the same retrieval-backed DeepSeek
``/responses`` search V3.12 made the Investigator's external rung — for listed companies
that fit the Discovery Intent, a bounded number of times, and parses its answer into
``CompanyLead``s.

A lead is a CLAIM that a company exists, is listed, and fits. It is not a candidate:
``discovery.identity`` must verify the listing on a page the platform fetches itself before
a lead can enter a universe. So a lead may come from anywhere the provider went — an
aggregator, a news article, the model's recall — because nothing here is trusted.

WHAT IS SENT OUT
================
Queries are built from the intent's CLOSED vocabulary (theme labels, material names,
region names, size words) — never from the user's raw text, the same rule V3.18 applied to
thesis questions: what a user typed is theirs.
"""

from __future__ import annotations

import json
import re
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any

from app.services.discovery.intent import SOFT, DiscoveryIntent

#: Hard ceilings (spec §7). Settings may narrow them, never widen past these.
HARD_MAX_LEAD_QUERIES = 8
HARD_MAX_RAW_LEADS = 100
DEFAULT_MAX_LEAD_QUERIES = 4
DEFAULT_MAX_RAW_LEADS = 60
#: Companies asked for per query — enough to find the long tail, few enough to answer
#: inside one output budget.
COMPANIES_PER_QUERY = 15

COMPANY_LEAD_SYSTEM_PROMPT = (
    "You are a research contractor for an evidence-first investment research platform. "
    "Your job is to FIND publicly listed companies that match a research brief. You are not "
    "recommending anything; every company you name will be independently verified.\n"
    'Return JSON: {"companies": [{"legal_name": str, "ticker": str, "exchange": str, '
    '"country": str|null, "listing_source_url": str|null, "evidence_url": str|null, '
    '"why": str}]}.\n'
    "Rules you must follow:\n"
    "- Only companies whose shares are CURRENTLY listed on a stock exchange. No private "
    "companies, no delisted companies, no ETFs or funds.\n"
    "- `ticker` and `exchange` exactly as the exchange lists them (e.g. exchange 'Euronext "
    "Paris', 'ASX', 'TSX Venture', 'London Stock Exchange AIM', 'Nasdaq', 'NYSE'). If you "
    "are not sure of the ticker, omit the company rather than guessing.\n"
    "- `listing_source_url`: a page on the EXCHANGE's own website, or on the COMPANY's own "
    "investor-relations website, that states the ticker. Not a news article, not a data "
    "aggregator. Null if you did not see one.\n"
    "- `evidence_url`: a page (ideally the company's own) that shows why it matches the "
    "brief. `why`: one sentence, the words that page uses.\n"
    "- Prefer smaller and less-covered companies over household names when the brief asks "
    "for smaller companies; include every good match you find, not only famous ones.\n"
    "- Do not invent anything. An empty list is an acceptable answer."
)

_THEME_WORDS: dict[str, str] = {
    "luxury_goods": "luxury goods (fashion and leather goods, jewellery, watches, eyewear, "
    "cosmetics and fragrances, premium spirits, luxury retail)",
    "critical_materials": "critical and strategic materials",
    "mining_materials": "mining, processing and refining",
    "defense": "defence and aerospace",
    "semiconductors": "semiconductors and semiconductor equipment",
    "nuclear_energy": "nuclear energy and uranium",
    "grid_electrification": "power grid and electrification equipment",
    "robotics_automation": "robotics and industrial automation",
    "biotech_pharma": "biotechnology and pharmaceuticals",
    "banks_fintech": "banks and fintech",
    "ai_infrastructure": "AI infrastructure and data centres",
}
_SIZE_WORDS: dict[str, str] = {
    "micro_cap": "micro-cap (market capitalisation below USD 300 million)",
    "small_cap": "small-cap (market capitalisation USD 300 million to 2 billion)",
    "mid_cap": "mid-cap (USD 2 to 10 billion)",
    "large_cap": "large-cap (above USD 10 billion)",
    "mega_cap": "mega-cap (above USD 200 billion)",
}
_END_MARKET_WORDS: dict[str, str] = {
    "semiconductors": "semiconductors",
    "ai_data_centres": "AI hardware and data centres",
    "electric_vehicles": "electric vehicles and batteries",
    "electrification": "electrification",
    "defence": "defence",
    "renewables": "renewable energy",
    "nuclear_energy": "nuclear energy",
}
_CATALYST_WORDS: dict[str, str] = {
    "permitting": "permitting milestones",
    "government_funding": "government funding or grants",
    "offtake": "offtake agreements",
    "production": "production start or ramp-up",
    "contracts": "contract awards",
    "orders": "orders",
    "regulatory_approval": "regulatory approvals",
}


@dataclass(frozen=True)
class LeadQuery:
    text: str
    focus: str
    geography: str


@dataclass
class CompanyLead:
    name: str
    ticker: str | None
    exchange_raw: str | None
    country: str | None
    listing_source_url: str | None
    evidence_url: str | None
    why: str | None
    source: str  # external_search | curated_registry | platform_registry
    discovery_query: str | None = None
    provider: str | None = None
    task_id: str | None = None
    lead_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    #: Carried for registry-sourced leads: the curated/held classification.
    registry_item: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        return {k: v for k, v in out.items() if v not in (None, "", [], {})}


@dataclass
class LeadDiscoveryResult:
    available: bool
    leads: list[CompanyLead] = field(default_factory=list)
    queries: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    consumption: list[Any] = field(default_factory=list)  # ConsumptionUnits per call


def _bounded(value: Any, default: int, hard: int) -> int:
    try:
        n = int(value) if value else default
    except (TypeError, ValueError):
        n = default
    return max(1, min(n, hard))


def plan_lead_queries(intent: DiscoveryIntent, *, max_queries: int) -> list[LeadQuery]:
    """Bounded queries from the intent's closed vocabulary. Deterministic."""
    focus_parts: list[str] = []
    if intent.materials:
        names = [m.replace("_", " ") for m in intent.materials]
        # Materials in groups of three: one query per group keeps each answer focused.
        groups = [names[i : i + 3] for i in range(0, len(names), 3)]
        focus_parts = [
            "companies producing, developing, processing or recycling "
            + ", ".join(group)
            for group in groups
        ]
    else:
        for theme in intent.themes:
            words = _THEME_WORDS.get(theme)
            if words and "companies in " + words not in focus_parts:
                focus_parts.append("companies in " + words)
        if not focus_parts and intent.industries:
            focus_parts.append("companies in " + ", ".join(intent.industries[:3]))
    if not focus_parts:
        return []

    geographies = [*intent.regions, *intent.countries] or ["any country"]
    # Several geographies: one query each (a single "Europe or Australia" query answers
    # with the most famous names of the first). One geography: split by focus instead.
    size_clause = ""
    if intent.size is not None:
        bands = [_SIZE_WORDS[b] for b in intent.size.requested if b in _SIZE_WORDS]
        if bands:
            size_clause = (
                " Size: "
                + ("preferably " if intent.size.hardness == SOFT else "")
                + " or ".join(bands)
                + "."
            )
    context = ""
    if intent.end_markets:
        context += " End markets of interest: " + ", ".join(
            _END_MARKET_WORDS.get(m, m) for m in intent.end_markets
        ) + "."
    if intent.catalysts:
        context += " Useful context (not required): " + ", ".join(
            _CATALYST_WORDS.get(c, c) for c in intent.catalysts
        ) + "."

    queries: list[LeadQuery] = []
    pairs = [(f, g) for g in geographies for f in focus_parts]
    for focus, geography in pairs[:max_queries]:
        where = (
            "listed anywhere"
            if geography == "any country"
            else f"headquartered or listed in {geography}"
        )
        queries.append(
            LeadQuery(
                text=(
                    f"Find up to {COMPANIES_PER_QUERY} publicly listed {focus}, {where}."
                    f"{size_clause}{context} Give each company's exact ticker and exchange."
                ),
                focus=focus,
                geography=geography,
            )
        )
    return queries


def parse_company_leads(
    text: str | None, *, query: LeadQuery, provider: str | None, task_id: str | None
) -> tuple[list[CompanyLead], list[str]]:
    """``CompanyLead``s from the provider's JSON answer. Untrusted input; never raises."""
    from app.integrations.deepseek.providers import _json_object

    parsed = _json_object(text)
    if parsed is None:
        return [], ["the provider returned no parseable JSON object"]
    raw = parsed.get("companies")
    if not isinstance(raw, list):
        return [], [f"the provider's answer carried no companies list; keys {sorted(parsed)[:6]}"]
    leads: list[CompanyLead] = []
    for item in raw[: COMPANIES_PER_QUERY * 2]:
        if not isinstance(item, dict):
            continue
        name = _clean(item.get("legal_name") or item.get("name"), 200)
        if not name:
            continue
        leads.append(
            CompanyLead(
                name=name,
                ticker=_clean_ticker(item.get("ticker")),
                exchange_raw=_clean(item.get("exchange"), 80),
                country=_clean(item.get("country"), 80),
                listing_source_url=_clean_url(item.get("listing_source_url")),
                evidence_url=_clean_url(item.get("evidence_url")),
                why=_clean(item.get("why"), 400),
                source="external_search",
                discovery_query=query.text,
                provider=provider,
                task_id=task_id,
            )
        )
    return leads, []


def _clean(value: Any, limit: int) -> str | None:
    if value is None:
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text[:limit] or None


def _clean_ticker(value: Any) -> str | None:
    text = _clean(value, 24)
    if not text:
        return None
    # "ASX: XYZ", "XYZ.AX", "EPA:KER" → the bare symbol; the exchange travels separately.
    text = re.sub(r"^[A-Za-z .]+:\s*", "", text).strip()
    return text.upper() or None


def _clean_url(value: Any) -> str | None:
    text = _clean(value, 600)
    if not text or not text.lower().startswith("https://"):
        return None
    return text.split("#", 1)[0]


async def discover_leads(
    intent: DiscoveryIntent,
    *,
    cfg: Any,
    provider: Any = None,
    max_queries: int | None = None,
    max_leads: int | None = None,
) -> LeadDiscoveryResult:
    """Run the bounded lead queries. Never raises; an absent provider is stated."""
    import asyncio

    if provider is None:
        from app.services.agents.routing import research_provider_for

        provider = research_provider_for(cfg)
    if provider is None or getattr(provider, "transport", None) is None:
        return LeadDiscoveryResult(
            available=False,
            warnings=[
                "external company discovery is unavailable: no external research provider "
                "is configured and enabled (a statement about this platform, not the web)"
            ],
        )
    queries = plan_lead_queries(
        intent,
        max_queries=_bounded(
            max_queries if max_queries is not None
            else getattr(cfg, "v3_discovery_max_lead_queries", None),
            DEFAULT_MAX_LEAD_QUERIES,
            HARD_MAX_LEAD_QUERIES,
        ),
    )
    limit = _bounded(
        max_leads if max_leads is not None else getattr(cfg, "v3_discovery_max_raw_leads", None),
        DEFAULT_MAX_RAW_LEADS,
        HARD_MAX_RAW_LEADS,
    )
    result = LeadDiscoveryResult(available=True)
    if not queries:
        result.warnings.append("the intent names nothing a company search can be built from")
        return result

    async def _one(query: LeadQuery) -> tuple[LeadQuery, Any, str | None]:
        try:
            return query, await _ask(provider, query, cfg), None
        except Exception as exc:  # noqa: BLE001 - one failed query costs its leads only
            return query, None, type(exc).__name__

    answers = await asyncio.gather(*(_one(q) for q in queries))
    for query, answer, error in answers:
        record: dict[str, Any] = {"query": query.text, "focus": query.focus,
                                  "geography": query.geography}
        if error or answer is None:
            record["status"] = "failed"
            record["error"] = error or "no answer"
            result.queries.append(record)
            continue
        leads, warnings = parse_company_leads(
            answer["text"], query=query, provider=answer["provider"], task_id=answer["task_id"]
        )
        record.update(
            status="completed" if not answer["truncated"] else "partial",
            leads=len(leads),
            opened_urls=answer["opened_urls"][:20],
            warnings=warnings + answer["warnings"],
        )
        result.queries.append(record)
        result.consumption.append(answer["consumption"])
        result.leads.extend(leads)
    result.leads = result.leads[:limit]
    return result


async def _ask(provider: Any, query: LeadQuery, cfg: Any) -> dict[str, Any]:
    """One retrieval-backed call with the company-lead prompt, measured like any other."""
    from app.integrations.deepseek.providers import (
        DEFAULT_SEARCH_TOOL_NAME,
        RETRIEVAL_BUDGET_PROMPT,
        _vendor_usage,
        parse_search_payload,
    )
    from app.services.providers.contracts import ConsumptionUnits

    transport = provider.transport
    timeout = int(getattr(cfg, "v3_external_search_timeout_seconds", 0) or 180)
    response = await transport.investigate_with_search(
        system=COMPANY_LEAD_SYSTEM_PROMPT + RETRIEVAL_BUDGET_PROMPT,
        question=query.text,
        domains=None,
        max_output_tokens=int(getattr(provider, "max_output_tokens", 12_000) or 12_000),
        timeout=min(max(10, timeout), 300),
    )
    _candidates, trace_warnings, trace = parse_search_payload(
        response,
        expected_tool=getattr(transport, "search_tool_name", "") or DEFAULT_SEARCH_TOOL_NAME,
    )
    truncated = (response.finish_reason or "").lower() in {
        "length", "max_tokens", "max_output_tokens", "incomplete",
    } or (bool(response.tool_payloads) and not (response.text or "").strip())
    return {
        "text": response.text,
        "provider": getattr(provider, "provider_id", None),
        "task_id": str(uuid.uuid4()),
        "truncated": truncated,
        "opened_urls": list(trace.opened_urls),
        "warnings": list(trace_warnings)
        + (["the answer was cut off; its list is partial, not exhaustive"] if truncated else []),
        "consumption": ConsumptionUnits(
            provider_research_runs=1,
            model_calls=1,
            web_search_calls=trace.query_call_count,
            url_fetch_calls=trace.retrieval_call_count,
            model_input_tokens=response.prompt_tokens,
            model_output_tokens=response.completion_tokens,
            cached_tokens=response.cached_tokens,
            by_vendor=(_vendor_usage(response),),
        ),
    }


def leads_to_json(leads: list[CompanyLead]) -> str:
    return json.dumps([lead.to_dict() for lead in leads])


__all__ = [
    "COMPANY_LEAD_SYSTEM_PROMPT",
    "CompanyLead",
    "LeadDiscoveryResult",
    "LeadQuery",
    "discover_leads",
    "parse_company_leads",
    "plan_lead_queries",
]
