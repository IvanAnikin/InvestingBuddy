"""``get_peer_set`` and ``get_peer_financials`` — V3.18.6.

Both were declared in the V3.3 tool vocabulary and never implemented, so the competitive
and valuation-context roles could never be seated on their own questions and every report
said "no peer comparison is available" — which was a statement about this platform.

WHAT COUNTS AS A VERIFIED PEER
==============================
A candidate is a company in the platform's own universe with the same canonical industry
— never a name a model suggested. It becomes a *verified* peer only when its regulator
filing can be read: its CIK resolves and its statements normalise to a reporting period.
The comparison is then made on figures produced by the SAME own-period rule and the SAME
defined metrics as the subject's, so "comparable" is a property of the pipeline rather
than an assumption.

Peers the platform does not hold — the world's largest producers of a commodity, many of
them listed outside the United States — are the job of the question's external research
rung, whose every claim InvestingBuddy fetches and verifies itself. This tool does not
pretend to know them.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from app.services.agent_tools.contracts import (
    TOOL_GET_PEER_FINANCIALS,
    TOOL_GET_PEER_SET,
    ToolCost,
    ToolSpec,
    units_for,
)

if TYPE_CHECKING:  # pragma: no cover
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

MAX_PEERS = 6
PEER_UNITS: tuple[str, ...] = ("url_fetch_calls",)
_SEC_FACTS_URL = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik}.json"
_USER_AGENT = "InvestingBuddy-Research-Platform/1.0 (contact: research@investingbuddy.com)"

#: The metrics compared. Chosen because each is defined for any industrial or resource
#: company from its statements; a sector metric (unit cost, grade) is not in a filer's
#: structured data and belongs to the question's external rung.
PEER_METRICS: tuple[str, ...] = (
    "operating_margin",
    "net_margin",
    "cash_conversion",
    "capex_to_ocf",
    "capex_intensity",
    "net_debt",
)


def validate_get_peer_set(arguments: dict[str, Any]) -> dict[str, Any]:
    company_id = str(arguments.get("company_id") or "").strip()
    if not company_id:
        raise ValueError("company_id is required: a peer set is a peer set OF someone.")
    limit = int(arguments.get("limit") or MAX_PEERS)
    return {
        "company_id": company_id,
        "commodity": (str(arguments.get("commodity") or "").strip().lower() or None),
        "limit": max(1, min(limit, MAX_PEERS)),
    }


async def _get_peer_set(context: "ToolContext", arguments: dict[str, Any]) -> dict[str, Any]:
    import uuid

    from sqlalchemy import select

    from app.models.company import Company
    from app.models.research_chunk import ResearchDocumentChunk
    from app.services.macro.commodities import identify_commodities
    from app.services.sector_taxonomy import normalize_industry

    session = context.session
    subject = await session.get(Company, uuid.UUID(arguments["company_id"]))
    if subject is None:
        return {"items": [], "gaps": ["the subject company is not held"], "summary": "no subject"}
    industry = normalize_industry(getattr(subject, "industry", None)) or getattr(
        subject, "industry", None
    )
    if not industry:
        return {
            "items": [],
            "gaps": ["the subject has no canonical industry, so no peer set can be derived"],
            "summary": "no industry",
        }
    rows = (
        await session.execute(
            select(Company).where(Company.id != subject.id).limit(400)
        )
    ).scalars().all()
    candidates = [
        c
        for c in rows
        if (normalize_industry(getattr(c, "industry", None)) or getattr(c, "industry", None))
        == industry
    ]
    items: list[dict[str, Any]] = []
    for company in candidates:
        texts = (
            await session.execute(
                select(ResearchDocumentChunk.text)
                .where(ResearchDocumentChunk.company_id == company.id)
                .limit(200)
            )
        ).scalars().all()
        mentions = identify_commodities([*texts, str(company.name or "")] if texts else [
            " ".join([str(company.name or "")] * 5)
        ])
        commodities = [m.commodity.slug for m in mentions]
        items.append(
            {
                "id": f"peer:{company.ticker}:{company.exchange}",
                "ticker": company.ticker,
                "exchange": company.exchange,
                "name": company.name,
                "industry": industry,
                "commodities": commodities,
                "commodity_basis": "corpus" if texts else "name_only",
                "shares_commodity": bool(
                    arguments["commodity"] and arguments["commodity"] in commodities
                ),
                "basis": "same canonical industry in the platform's company universe",
                "source_tier": "T3_curated_reference_list",
            }
        )
    items.sort(key=lambda i: (not i["shares_commodity"], i["commodity_basis"] != "corpus",
                              str(i["ticker"])))
    items = items[: arguments["limit"]]
    gaps = []
    if len([i for i in items if i["shares_commodity"]]) < 3:
        gaps.append(
            "Fewer than three peers in the platform's universe share this commodity; "
            "producers the platform does not hold must come from verified external research."
        )
    return {
        "items": items,
        "gaps": gaps,
        "summary": f"{len(items)} candidate peer(s) with industry {industry!r}",
        "contains_untrusted_content": False,
    }


def validate_get_peer_financials(arguments: dict[str, Any]) -> dict[str, Any]:
    """``listings``: ``[{"ticker", "exchange"}]``. Bare ``tickers`` are read as listings
    with NO exchange, which the SEC eligibility rule treats as its legacy US default."""
    listings: list[dict[str, str | None]] = []
    for raw in arguments.get("listings") or []:
        if isinstance(raw, dict) and str(raw.get("ticker") or "").strip():
            listings.append(
                {
                    "ticker": str(raw["ticker"]).strip().upper(),
                    "exchange": (str(raw.get("exchange")).strip().upper()
                                 if raw.get("exchange") else None),
                }
            )
    tickers = arguments.get("tickers") or []
    if isinstance(tickers, str):
        tickers = [tickers]
    listings.extend(
        {"ticker": str(t).strip().upper(), "exchange": None} for t in tickers if str(t).strip()
    )
    if not listings:
        raise ValueError("listings (or tickers) is required.")
    return {"listings": listings[: MAX_PEERS + 1]}


#: companyfacts for a large registrant runs to tens of megabytes; beyond this it is
#: not read.
MAX_FACTS_BYTES = 40 * 1024 * 1024


async def _facts_for(
    ticker: str, exchange: str | None, provider: Any
) -> tuple[dict | None, str | None]:
    """companyfacts for one SEC registrant, or (None, reason). Never raises.

    The listing's EXCHANGE decides eligibility first: a ticker on the ASX or Euronext
    resolved against SEC's US index is a DIFFERENT company ("MC" is Moelis, not LVMH),
    and its figures would be shown as the subject's.
    """
    import json

    import httpx

    from app.services.exchange_registry import is_sec_eligible

    if not is_sec_eligible(exchange):
        return None, f"listed on {exchange}, which SEC's index does not cover"
    try:
        cik = await provider.resolve_cik(ticker, exchange)
    except Exception as exc:  # noqa: BLE001
        return None, f"not a resolvable SEC registrant ({type(exc).__name__})"
    try:
        async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=25.0) as client:
            url = _SEC_FACTS_URL.format(cik=str(cik).zfill(10))
            async with client.stream("GET", url) as response:
                if response.status_code != 200:
                    return None, f"companyfacts returned HTTP {response.status_code}"
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > MAX_FACTS_BYTES:
                        return None, "companyfacts exceeds the size cap; not read"
            return json.loads(bytes(body)), None
    except Exception as exc:  # noqa: BLE001
        return None, f"companyfacts unreachable ({type(exc).__name__})"


def _peer_metrics(ticker: str, facts: dict) -> tuple[list[dict[str, Any]], list[str]]:
    """Normalise with the subject's own rules, then compute the defined metrics."""
    from app.integrations.sec_fundamentals_normalizer import normalize_company_facts
    from app.services.calculations.statement_metrics import compute_statement_metrics

    n = normalize_company_facts(facts, ticker)
    if n.fiscal_year is None or n.period_basis != "annual":
        return [], [f"{ticker}: no annual statements could be normalised"]
    # THE SUBJECT'S RULES, not a looser copy: metrics derived from an inconsistent
    # statement are withheld, and net debt needs BOTH debt legs reported for the period
    # — a missing leg makes total debt a partial sum, and a peer would show net cash.
    withheld = set((n.consistency or {}).get("withheld") or ())
    for finding in (n.consistency or {}).get("inconsistencies") or []:
        withheld.update(finding.get("withhold_derived") or ())
    legs = {"short_term_debt", "long_term_debt"}
    both_legs = (
        not (legs & set(n.withheld_fields))
        and n.short_term_debt is not None
        and n.long_term_debt is not None
    )
    if not both_legs:
        withheld.add("net_debt")
    values = {
        "revenue": n.revenue,
        "operating_profit": n.operating_income,
        "net_income": n.net_income,
        "operating_cash_flow": n.operating_cash_flow,
        "capital_expenditure": n.capital_expenditures,
        "total_debt": n.total_debt if both_legs else None,
        "cash_and_equivalents": n.cash_and_equivalents,
    }
    readings, refusals = compute_statement_metrics(
        values,
        period_label=f"FY{n.fiscal_year}",
        keys=[k for k in PEER_METRICS if k not in withheld],
    )
    items: list[dict[str, Any]] = [
        {
            "id": f"peerfin:{ticker}:FY{n.fiscal_year}:revenue",
            "ticker": ticker,
            "metric_id": "revenue",
            "value": n.revenue,
            "unit": "USD millions",
            "period": f"FY{n.fiscal_year}",
            "period_end": n.reporting_period_end,
            "source_tier": "T1_primary_filing",
            "source_ref": n.source_url,
        }
    ] if n.revenue is not None else []
    for reading in readings:
        items.append(
            {
                "id": f"peerfin:{ticker}:{reading.period_label}:{reading.key}",
                "ticker": ticker,
                **reading.to_dict(),
                "period_end": n.reporting_period_end,
                "source_tier": "T1_primary_filing",
                "source_ref": n.source_url,
                "computed_by": "InvestingBuddy, from the registrant's SEC statements",
            }
        )
    gaps = [f"{ticker}: {r.key} not computed ({r.reason})" for r in refusals]
    gaps.extend(
        f"{ticker}: {field} withheld — not reported for FY{n.fiscal_year}"
        for field in n.withheld_fields
    )
    return items, gaps


async def _get_peer_financials(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    from app.integrations.providers.sec_edgar_fundamentals import SecEdgarFundamentalsProvider

    items: list[dict[str, Any]] = []
    gaps: list[str] = []
    fetches = 0
    # ONE provider for the call: it caches SEC's ticker index, which a fresh provider
    # per ticker downloaded again every time.
    provider = SecEdgarFundamentalsProvider()
    for listing in arguments["listings"]:
        ticker, exchange = listing["ticker"], listing["exchange"]
        facts, reason = await _facts_for(ticker, exchange, provider)
        fetches += 1
        if facts is None:
            gaps.append(f"{ticker}: {reason}")
            continue
        found, notes = await asyncio.to_thread(_peer_metrics, ticker, facts)
        items.extend(found)
        gaps.extend(notes)
    verified = sorted({i["ticker"] for i in items})
    return {
        "items": items,
        "gaps": gaps,
        "verified_peers": verified,
        "summary": (
            f"Comparable metrics for {len(verified)} registrant(s), each normalised with "
            "the same own-period rule and defined metrics as the subject"
        ),
        "consumption": units_for(PEER_UNITS, url_fetch_calls=fetches),
        "contains_untrusted_content": False,
    }


GET_PEER_SET_SPEC = ToolSpec(
    name=TOOL_GET_PEER_SET,
    description=(
        "Candidate peers from the platform's own company universe with the same canonical "
        "industry, ranked by whether their own documents show the same commodity."
    ),
    handler=_get_peer_set,
    validate_arguments=validate_get_peer_set,
    cost=ToolCost(),
)

GET_PEER_FINANCIALS_SPEC = ToolSpec(
    name=TOOL_GET_PEER_FINANCIALS,
    description=(
        "Comparable, defined metrics for US registrants from their SEC statements, "
        "normalised with the same own-period rule as the subject."
    ),
    handler=_get_peer_financials,
    validate_arguments=validate_get_peer_financials,
    cost=ToolCost(fetches=MAX_PEERS + 1),
    instrumented_units=PEER_UNITS,
    access_classes=("public_official",),
)


def register_peer_tools(registry: "ToolRegistry") -> "ToolRegistry":
    registry.register(GET_PEER_SET_SPEC)
    registry.register(GET_PEER_FINANCIALS_SPEC)
    return registry


__all__ = [
    "GET_PEER_FINANCIALS_SPEC",
    "GET_PEER_SET_SPEC",
    "PEER_METRICS",
    "register_peer_tools",
    "validate_get_peer_financials",
    "validate_get_peer_set",
]
