"""
Single-company LLM analysis council orchestrator — Phase 28A.

Runs the eight council agents in order over a bounded evidence pack, enforces
citations + safety on every agent's output, and returns a ``CouncilResult`` with
honest run metadata. A single agent failing (timeout, malformed JSON, provider
error) is isolated: that agent is marked ``failed`` and the report still saves.

Logging is structured and safe (Phase 27.1D): it records ids, provider/model
names, statuses, counts and durations — never prompts, completions, evidence
excerpts, or credentials.

Entry points:
  run_council(pack, client, ...)  — run the council over a prepared pack.
  maybe_run_council(...)          — resolve a client from config; build the pack;
                                    run the council. Returns a disabled result
                                    (llm_used=False) when the council is off or
                                    no provider is available (deterministic path).
"""

from __future__ import annotations

import logging
import time
from typing import Any

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.llm import prompts
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.client import LLMClient, LLMError, get_llm_client
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.schemas import (
    AGENT_COMMITTEE_CHAIR,
    COUNCIL_AGENT_ORDER,
    STATUS_FAILED,
    CouncilAgentOutput,
    CouncilResult,
    EvidencePack,
)
from app.services.sources.company_evidence import (
    collect_company_source_evidence,
    press_items_from_catalyst,
    sec_filings_from_catalyst,
)
from app.services.sources.connector_base import CompanyContext
from app.services.sources.registry import build_registry, registry_gap_messages

_logger = logging.getLogger("app.services.llm.council")


def _company_context(
    company_snapshot: dict[str, Any] | None,
    ticker: str | None,
    exchange: str | None,
) -> CompanyContext:
    """Derive the connector CompanyContext from report identity (no secrets)."""
    ci = (company_snapshot or {}).get("company_identity") or {}
    profile = (company_snapshot or {}).get("profile") or {}
    return CompanyContext(
        ticker=ticker or ci.get("ticker"),
        exchange=exchange or ci.get("exchange"),
        company_name=ci.get("legal_name") or ci.get("name"),
        country=ci.get("country_domicile") or ci.get("country"),
        sector=ci.get("sector") or profile.get("sector"),
        industry=profile.get("industry"),
        cik=ci.get("cik"),
    )


_DOCUMENT_SOURCE_TYPES = frozenset(
    {
        "company_ir_annual_report_text",
        "company_ir_annual_report_excerpt",
        "company_ir_business_description",
        "company_ir_risk_excerpt",
        "company_ir_financial_fact",
    }
)


def _primary_document_summary(evidence_items: list[Any]) -> list[dict[str, Any]]:
    """Compact, secret-free summary of extracted primary-document evidence.

    Groups document-derived EvidenceItems by (url, title) and reports counts,
    domain, tier, translation flag and de-duplicated warnings — never raw text.
    """
    from urllib.parse import urlsplit

    groups: dict[tuple[str, str], dict[str, Any]] = {}
    for it in evidence_items:
        stype = getattr(it, "source_type", None)
        if stype not in _DOCUMENT_SOURCE_TYPES:
            continue
        url = getattr(it, "url", None) or ""
        title = getattr(it, "title", None) or ""
        key = (url.split("?")[0], (title.split(" — ")[0]).split(":")[0].strip())
        domain = ""
        try:
            domain = (urlsplit(url).hostname or "").replace("www.", "")
        except (ValueError, TypeError):
            domain = ""
        g = groups.setdefault(
            key,
            {
                "title": key[1] or "Annual report",
                "domain": domain,
                "tier": getattr(it, "content_source_tier", None),
                "excerpt_count": 0,
                "fact_count": 0,
                "requires_translation": bool(getattr(it, "requires_translation", False)),
                "warnings": [],
            },
        )
        if stype == "company_ir_financial_fact":
            g["fact_count"] += 1
        else:
            g["excerpt_count"] += 1
        g["requires_translation"] = g["requires_translation"] or bool(
            getattr(it, "requires_translation", False)
        )
        for w in getattr(it, "warnings", None) or []:
            if w not in g["warnings"]:
                g["warnings"].append(w)
    # Bound warnings per document so the metadata stays compact.
    for g in groups.values():
        g["warnings"] = g["warnings"][:4]
    return list(groups.values())


def _primary_facts(evidence_items: list[Any]) -> list[dict[str, Any]]:
    """Structured, bounded HIGH-CONFIDENCE primary facts — Phase 29B.3.

    Reads the STRUCTURED ``primary_fact`` payload each ``company_ir_financial_fact``
    EvidenceItem carries (field / value / numeric_value / unit / currency / scale /
    period + short page/excerpt provenance) — never the raw excerpt body or
    document text. Only ``confidence == "high"`` facts are surfaced: a matching
    high-confidence fact is precisely what lets the final report present a real
    T1 primary-filing datapoint. The item's own token-stripped URL is preferred
    as the fact's provenance URL.
    """
    out: list[dict[str, Any]] = []
    for it in evidence_items:
        if getattr(it, "source_type", None) != "company_ir_financial_fact":
            continue
        pf = getattr(it, "primary_fact", None)
        if pf is None:
            continue
        if getattr(pf, "confidence", None) != "high":
            continue
        data = pf.model_dump(mode="json") if hasattr(pf, "model_dump") else dict(pf)
        url = getattr(it, "url", None)
        if url:
            data["source_url"] = url
        out.append(data)
    return out


def _coerce_output(agent_name: str, raw: dict[str, Any]) -> CouncilAgentOutput:
    """Validate the model's dict into CouncilAgentOutput, tolerating drift.

    The agent_name is always forced to the expected value — never trusted from
    the model — so an agent cannot impersonate another in the merged report.
    """
    payload = dict(raw) if isinstance(raw, dict) else {}
    payload["agent_name"] = agent_name
    try:
        return CouncilAgentOutput.model_validate(payload)
    except Exception:  # noqa: BLE001 - any validation drift becomes a failed agent
        return CouncilAgentOutput(
            agent_name=agent_name,
            status=STATUS_FAILED,
            summary="[Agent output could not be parsed into the required schema.]",
            safety_notes=["Malformed structured output rejected."],
        )


def _prior_summaries(outputs: list[CouncilAgentOutput]) -> str:
    lines = []
    for o in outputs:
        if o.status == STATUS_FAILED:
            continue
        summary = (o.summary or "").strip()
        if summary:
            lines.append(f"- {o.agent_name}: {summary}")
    return "\n".join(lines)


async def run_council(
    evidence_pack: EvidencePack,
    client: LLMClient,
    *,
    cfg: Settings | None = None,
    report_id: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
    logger: logging.Logger | None = None,
) -> CouncilResult:
    """Run every council agent over the evidence pack and return the result."""
    cfg = cfg or default_settings
    log = logger or _logger
    evidence_ids = evidence_pack.evidence_ids()
    evidence_json = evidence_pack.model_dump_json()

    result = CouncilResult(
        council_version=cfg.llm_council_version,
        llm_used=True,
        provider=client.provider_name,
        model=client.model_name,
        deployment=client.deployment_name,
        evidence_pack_version=evidence_pack.evidence_pack_version,
        evidence_item_count=evidence_pack.item_count,
    )

    log_event(
        log,
        "llm_council_started",
        report_id=report_id,
        ticker=ticker,
        exchange=exchange,
        provider=client.provider_name,
        model=client.model_name,
        council_version=cfg.llm_council_version,
        evidence_item_count=evidence_pack.item_count,
    )

    for agent_name in COUNCIL_AGENT_ORDER:
        started = time.perf_counter()
        if agent_name == AGENT_COMMITTEE_CHAIR:
            system = prompts.committee_chair_system_prompt()
            user = prompts.build_user_message(evidence_json, _prior_summaries(result.agents))
        else:
            system = prompts.system_prompt_for(agent_name)
            user = prompts.build_user_message(evidence_json)

        try:
            raw = await client.complete_json(
                system,
                user,
                max_tokens=cfg.llm_max_output_tokens,
                temperature=cfg.llm_temperature,
                timeout=cfg.llm_request_timeout_seconds,
                repair_instruction=prompts.REPAIR_INSTRUCTION,
            )
            output = _coerce_output(agent_name, raw)
            sanitized, issues = check_and_sanitize(output, evidence_ids)
            result.agents.append(sanitized)
            result.warnings.extend(issues)
            duration_ms = int((time.perf_counter() - started) * 1000)
            if sanitized.status == STATUS_FAILED:
                log_event(
                    log,
                    "llm_agent_failed",
                    level=logging.WARNING,
                    report_id=report_id,
                    ticker=ticker,
                    agent_name=agent_name,
                    provider=client.provider_name,
                    council_version=cfg.llm_council_version,
                    duration_ms=duration_ms,
                    status=STATUS_FAILED,
                    reason="quarantined_or_unparsed",
                )
            else:
                log_event(
                    log,
                    "llm_agent_completed",
                    report_id=report_id,
                    ticker=ticker,
                    agent_name=agent_name,
                    provider=client.provider_name,
                    council_version=cfg.llm_council_version,
                    duration_ms=duration_ms,
                    status=sanitized.status,
                    key_point_count=len(sanitized.key_points),
                )
        except LLMError as exc:
            duration_ms = int((time.perf_counter() - started) * 1000)
            result.agents.append(
                CouncilAgentOutput(
                    agent_name=agent_name,
                    status=STATUS_FAILED,
                    summary="[Agent did not complete: provider error or timeout.]",
                    safety_notes=[f"Agent failed ({type(exc).__name__})."],
                )
            )
            result.warnings.append(f"{agent_name}: {type(exc).__name__}")
            log_event(
                log,
                "llm_agent_failed",
                level=logging.WARNING,
                report_id=report_id,
                ticker=ticker,
                agent_name=agent_name,
                provider=client.provider_name,
                council_version=cfg.llm_council_version,
                duration_ms=duration_ms,
                status=STATUS_FAILED,
                reason=type(exc).__name__,
            )

    result.recount()
    chair = next(
        (a for a in result.agents if a.agent_name == AGENT_COMMITTEE_CHAIR), None
    )
    result.committee_label = chair.committee_label if chair else None

    log_event(
        log,
        "llm_council_completed",
        report_id=report_id,
        ticker=ticker,
        exchange=exchange,
        provider=client.provider_name,
        model=client.model_name,
        council_version=cfg.llm_council_version,
        evidence_item_count=evidence_pack.item_count,
        agents_completed=result.agents_completed,
        agents_failed=result.agents_failed,
        committee_label=result.committee_label,
    )
    return result


async def maybe_run_council(
    *,
    report_content: dict[str, Any],
    company_snapshot: dict[str, Any] | None = None,
    catalyst_discovery: dict[str, Any] | None = None,
    source_rows: list[dict[str, Any]] | None = None,
    report_id: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
    cfg: Settings | None = None,
    client: LLMClient | None = None,
    logger: logging.Logger | None = None,
) -> CouncilResult:
    """Resolve a client, build the evidence pack, and run the council.

    Returns ``CouncilResult.disabled()`` (llm_used=False) when the council flag
    is off or no provider resolves — the signal to keep the deterministic path.
    Never raises: an unexpected failure degrades to the disabled result.
    """
    cfg = cfg or default_settings
    log = logger or _logger
    resolved = client or get_llm_client(cfg)
    if resolved is None:
        return CouncilResult.disabled()

    try:
        # Phase 29A: surface planned-source coverage gaps to the source critic.
        source_gaps = registry_gap_messages(build_registry(cfg))

        # Phase 29B: optionally run the source-registry connectors over
        # already-fetched deterministic data (no new network calls) and inject
        # their tiered evidence + honest gaps. Gated by ``source_connector_enabled``
        # so a plain deploy keeps the exact Phase 29A behaviour. Never crashes the
        # council: a failure degrades to no connector evidence.
        connector_evidence = None
        connector_gap_messages = None
        primary_documents: list[dict[str, Any]] = []
        primary_facts: list[dict[str, Any]] = []
        if cfg.source_connector_enabled:
            try:
                # Phase 29B.2: when document extraction is also enabled, inject the
                # bounded live IR-page fetcher + document extractor so the council
                # reasons from real annual-report excerpts + parsed facts — not only
                # metadata. Off by default (both flags), preserving 29B.1 behaviour.
                extract_kwargs: dict[str, Any] = {}
                if cfg.source_document_extraction_enabled:
                    from app.services.sources.live_fetchers import (
                        live_document_extractor,
                        live_ir_page_fetcher,
                    )

                    extract_kwargs = {
                        "ir_page_fetcher": live_ir_page_fetcher,
                        "document_extractor": live_document_extractor,
                    }
                collected = await collect_company_source_evidence(
                    company=_company_context(company_snapshot, ticker, exchange),
                    filings=sec_filings_from_catalyst(catalyst_discovery),
                    press_items=press_items_from_catalyst(catalyst_discovery),
                    cfg=cfg,
                    **extract_kwargs,
                )
                connector_evidence = collected.evidence_items
                connector_gap_messages = collected.gap_messages()
                primary_documents = _primary_document_summary(collected.evidence_items)
                primary_facts = _primary_facts(collected.evidence_items)
                log_event(
                    log,
                    "source_connector_evidence_collected",
                    report_id=report_id,
                    ticker=ticker,
                    exchange=exchange,
                    connector_item_count=len(connector_evidence),
                    connector_gap_count=len(connector_gap_messages),
                    primary_document_count=len(primary_documents),
                    primary_fact_count=len(primary_facts),
                )
            except Exception as exc:  # noqa: BLE001 - connectors never crash a report
                log_event(
                    log,
                    "source_connector_evidence_failed",
                    level=logging.WARNING,
                    report_id=report_id,
                    ticker=ticker,
                    exception_type=type(exc).__name__,
                )

        pack = build_evidence_pack(
            report_content=report_content,
            company_snapshot=company_snapshot,
            catalyst_discovery=catalyst_discovery,
            source_rows=source_rows,
            max_items=cfg.llm_council_max_evidence_items,
            extra_known_gaps=source_gaps,
            connector_evidence=connector_evidence,
            connector_gap_messages=connector_gap_messages,
            # Compress the pack when the connector layer is on (staging) so a
            # larger primary-source pack cannot balloon the prompt / TPM budget.
            apply_budget=cfg.source_connector_enabled,
            budget_cfg=cfg,
        )
        log_event(
            log,
            "evidence_pack_built",
            report_id=report_id,
            ticker=ticker,
            exchange=exchange,
            evidence_pack_version=pack.evidence_pack_version,
            evidence_item_count=pack.item_count,
            known_gap_count=len(pack.known_gaps),
        )
        result = await run_council(
            pack,
            resolved,
            cfg=cfg,
            report_id=report_id,
            ticker=ticker,
            exchange=exchange,
            logger=log,
        )
        # Attach the bounded primary-document summary so the report can surface
        # which annual-report excerpts/facts backed the council (metadata only).
        if primary_documents:
            result.primary_documents = primary_documents
        # Phase 29B.3: attach the structured high-confidence primary facts so the
        # report can present real T1 datapoints (with each fact's own provenance).
        if primary_facts:
            result.primary_facts = primary_facts
        return result
    except Exception as exc:  # noqa: BLE001 - never let the council crash a report
        log_event(
            log,
            "llm_council_failed",
            level=logging.ERROR,
            report_id=report_id,
            ticker=ticker,
            exception_type=type(exc).__name__,
        )
        return CouncilResult.disabled()
