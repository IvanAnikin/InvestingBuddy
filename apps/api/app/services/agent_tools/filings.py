"""The recent-filings tool — V3.10 corrective.

WHY THIS IS A CORRECTIVE AND NOT A FEATURE
==========================================
The first real MRNA acceptance run could not convene its Council. Biotech's **blocking**
``pipeline_state`` question requires ``get_recent_filings``, which was one of the twelve
names V3.3 reserved and never implemented — so the Director correctly refused to assign
it, correctly raised a ``tool_unavailable`` gap, and the run correctly reported
insufficient evidence.

Every one of those behaviours is right. The outcome is still that **a biotech run could
never complete**, and the failure looked like a coverage problem rather than a missing
capability. Only running a real issuer end to end surfaced it.

WHAT IT RETURNS AND WHAT IT DOES NOT
====================================
Filing *metadata* from the issuer's own regulator: form type, filing date, report date,
accession number and the SEC's own URL. **Not** the filing's contents — turning a filing
into citable text is the corpus's job, and a tool that returned prose would let an agent
cite a document nobody fetched.

Each item's ``id`` is the citable handle, and it is the provider's own deterministic
event id — derived from the accession number, so the same filing is the same id on every
run. An id minted per call would make a citation unresolvable the moment it was stored.

NETWORK, AND THE FLAG THAT GATES IT
===================================
This is the first agent tool that reaches outside the platform, so it is gated twice: the
tool surface's own ``V3_AGENT_TOOLS_ENABLED``, and ``V3_FILINGS_TOOL_ENABLED`` which
defaults **off**. With it off the tool returns an honest empty result naming the flag —
never a silent zero.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from app.services.agent_tools.contracts import (
    TOOL_GET_RECENT_FILINGS,
    ToolCost,
    ToolSpec,
)

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.services.agent_tools.registry import ToolRegistry
    from app.services.agent_tools.session import ToolContext

DEFAULT_LOOKBACK_DAYS = 400
MAX_LOOKBACK_DAYS = 1825
DEFAULT_LIMIT = 15
MAX_LIMIT = 50


def validate_get_recent_filings(arguments: dict[str, Any]) -> dict[str, Any]:
    ticker = str(arguments.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError(
            "get_recent_filings needs a ticker. It asks a regulator about an issuer, "
            "and an issuer is not a row id."
        )
    exchange = str(arguments.get("exchange") or "").strip() or None
    lookback = int(arguments.get("lookback_days") or DEFAULT_LOOKBACK_DAYS)
    if lookback < 1 or lookback > MAX_LOOKBACK_DAYS:
        raise ValueError(f"lookback_days must be between 1 and {MAX_LOOKBACK_DAYS}")
    limit = int(arguments.get("limit") or DEFAULT_LIMIT)
    if limit < 1 or limit > MAX_LIMIT:
        raise ValueError(f"limit must be between 1 and {MAX_LIMIT}")
    form_types = tuple(
        str(v).strip().upper()
        for v in (arguments.get("form_types") or [])
        if str(v).strip()
    )
    return {
        "ticker": ticker,
        "exchange": exchange,
        "lookback_days": lookback,
        "limit": limit,
        "form_types": form_types,
    }


async def _get_recent_filings(
    context: "ToolContext", arguments: dict[str, Any]
) -> dict[str, Any]:
    cfg = context.cfg
    if not getattr(cfg, "v3_filings_tool_enabled", False):
        # An honest empty result naming the flag, never a silent zero.
        return {
            "items": [],
            "population": {
                "definition": "recent regulator filings for this issuer",
                "filters": {k: v for k, v in arguments.items()},
                "returned": 0,
            },
            "summary": (
                "V3_FILINGS_TOOL_ENABLED is off, so no regulator was contacted. This is "
                "a statement about configuration, not about the issuer's filings."
            ),
            "contains_untrusted_content": False,
        }

    from app.integrations.providers.sec_recent_filings_provider import (
        SecRecentFilingsProvider,
    )
    from app.services.exchange_registry import is_sec_eligible

    if not is_sec_eligible(arguments["exchange"]):
        # The Boeing/BAE rule, applied here: SEC's ticker index covers US registrants,
        # and looking a non-US local ticker up in it returns an unrelated US issuer.
        return {
            "items": [],
            "population": {
                "definition": "recent SEC filings for this issuer",
                "filters": dict(arguments),
                "returned": 0,
            },
            "summary": (
                f"{arguments['exchange']!r} is not a venue SEC's ticker index covers, so "
                "no lookup was made. Looking a non-US ticker up in it does not fail — "
                "it returns an unrelated US issuer."
            ),
            "contains_untrusted_content": False,
        }

    provider = SecRecentFilingsProvider()
    result = await provider.get_recent_events(
        arguments["ticker"],
        exchange=arguments["exchange"],
        lookback_days=arguments["lookback_days"],
        max_events=arguments["limit"],
    )
    wanted = set(arguments["form_types"])
    items: list[dict[str, Any]] = []
    for event in result.events:
        form_type = str(getattr(event, "form_type", "") or "").upper()
        if wanted and form_type not in wanted:
            continue
        items.append(
            {
                # The provider's own deterministic id, derived from the accession
                # number: the same filing is the same id on every run, so a citation
                # recorded today still resolves.
                "id": str(getattr(event, "id", "") or ""),
                "form_type": form_type or None,
                "filing_date": str(getattr(event, "filing_date", "") or "") or None,
                "report_date": str(getattr(event, "report_date", "") or "") or None,
                "accession_number": getattr(event, "accession_number", None),
                "headline": getattr(event, "headline", None),
                "source_url": getattr(event, "source_url", None),
                "related_filing_url": getattr(event, "related_filing_url", None),
                "item_numbers": list(getattr(event, "item_numbers", None) or []),
                "source_tier": str(getattr(event, "source_tier", "") or "") or None,
            }
        )
        if len(items) >= arguments["limit"]:
            break

    # V3.16 — THE BRIDGE, run deterministically by the platform after discovery.
    #
    # Discovery and evidence stay separate: the items above are still metadata and no
    # prose is added to any of them. What this adds is `corpus_ready`, which answers a
    # question the specialist otherwise has to guess at — "can I actually search this
    # filing?" — and, when the answer is no, makes it true.
    #
    # The model cannot aim this. It supplied a ticker and a form list; the accessions
    # come from the regulator, and every URL is built inside the SEC pipeline from
    # code-defined constants. There is no argument through which a URL could arrive.
    bridge = await _ensure_filing_bodies(context, cfg=cfg, cik=result.cik, items=items)

    return {
        "items": items,
        "cik": result.cik,
        "corpus": bridge,
        "population": {
            "definition": (
                "filings the regulator lists for this issuer within the lookback window"
            ),
            "filters": dict(arguments),
            "row_limit": arguments["limit"],
            "returned": len(items),
        },
        "warnings": list(result.warnings),
        "summary": (
            f"{len(items)} filing(s) on record with the regulator. Metadata only — the "
            "filing's contents are not returned, because turning a filing into citable "
            "text is the corpus's job. "
            + _bridge_summary(bridge)
        ),
        # Headlines are the issuer's own words, from outside the platform.
        "contains_untrusted_content": True,
    }


async def _ensure_filing_bodies(
    context: "ToolContext",
    *,
    cfg: Any,
    cik: str | None,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """Make the discovered filings searchable, or record honestly why not.

    Returns counts only — never text, never a URL the caller did not already have. A
    filing that could not be acquired is reported as not ready rather than omitted,
    because a specialist needs to tell "there is nothing in this filing" from "this
    filing was never read".

    Never raises: evidence acquisition degrading must not fail a discovery call.
    """
    summary: dict[str, Any] = {
        "ready": 0,
        "acquired": 0,
        "unavailable": 0,
        "fetched": 0,
        "reasons": {},
    }
    if not getattr(cfg, "v3_filing_body_bridge_enabled", False):
        summary["enabled"] = False
        return summary
    summary["enabled"] = True

    session = getattr(context, "session", None)
    company_id = getattr(context, "company_id", None)
    if session is None or company_id is None or not cik:
        summary["reasons"]["no_company_context"] = len(items)
        return summary

    from app.services.corpus.filing_evidence import ensure_filing_corpus_evidence

    # BOUNDED. Acquiring every discovered filing would turn one question into fifty SEC
    # fetches. The cap applies to how many bodies may be ACQUIRED, not to how many
    # filings are examined: a filing already searchable costs nothing and is always
    # checked, so the budget is spent only on documents the platform does not yet hold.
    budget = max(0, int(getattr(cfg, "v3_filing_body_bridge_max_documents", 3) or 0))
    summary["acquire_budget"] = budget

    for item in items:
        try:
            # `ready_only` spends no budget: it answers from the database and stops.
            outcome = await ensure_filing_corpus_evidence(
                session,
                company_id=company_id,
                cik=cik,
                accession=item.get("accession_number"),
                form=item.get("form_type"),
                cfg=cfg,
                ready_only=budget <= 0,
            )
            if outcome.fetched:
                budget -= 1
        except Exception:  # noqa: BLE001 - discovery must survive a failed acquisition
            item["corpus_ready"] = False
            summary["unavailable"] += 1
            summary["reasons"]["bridge_error"] = (
                summary["reasons"].get("bridge_error", 0) + 1
            )
            continue

        item["corpus_ready"] = outcome.is_ready
        if outcome.fetched:
            summary["fetched"] += 1
        if outcome.is_ready:
            summary["ready" if outcome.reused else "acquired"] += 1
        else:
            summary["unavailable"] += 1
            reason = outcome.reason or "unknown"
            summary["reasons"][reason] = summary["reasons"].get(reason, 0) + 1
    return summary


def _bridge_summary(bridge: dict[str, Any]) -> str:
    """One sentence a reader can act on, including when nothing happened."""
    if not bridge.get("enabled"):
        return (
            "V3_FILING_BODY_BRIDGE_ENABLED is off, so no filing body was acquired and "
            "none of these filings is searchable."
        )
    ready = int(bridge.get("ready", 0))
    acquired = int(bridge.get("acquired", 0))
    unavailable = int(bridge.get("unavailable", 0))
    return (
        f"{ready + acquired} searchable in the corpus ({acquired} acquired now, "
        f"{ready} already held), {unavailable} not searchable."
    )


GET_RECENT_FILINGS_SPEC = ToolSpec(
    name=TOOL_GET_RECENT_FILINGS,
    description=(
        "Filing METADATA from the issuer's own regulator — form type, filing and report "
        "dates, accession number and the regulator's URL. Never the filing's contents. "
        "Returns an honest empty result, naming the reason, when the flag is off or the "
        "venue is not one the regulator's index covers."
    ),
    handler=_get_recent_filings,
    validate_arguments=validate_get_recent_filings,
    cost=ToolCost(fetches=1),
    instrumented_units=("url_fetch_calls",),
    may_contain_untrusted_content=True,
)


def register_filing_tools(registry: "ToolRegistry") -> "ToolRegistry":
    registry.register(GET_RECENT_FILINGS_SPEC)
    return registry


__all__ = [
    "DEFAULT_LIMIT",
    "DEFAULT_LOOKBACK_DAYS",
    "GET_RECENT_FILINGS_SPEC",
    "MAX_LIMIT",
    "register_filing_tools",
    "validate_get_recent_filings",
]
