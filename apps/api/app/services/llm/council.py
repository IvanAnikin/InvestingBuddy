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
import uuid
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
    PersistableEvidence,
)
from app.services.sources.company_evidence import (
    collect_company_source_evidence,
    press_items_from_catalyst,
    sec_filings_from_catalyst,
)
from app.services.sources.connector_base import CompanyContext
from app.services.sources.event_evidence import (
    ThemeEventEvidence,
    collect_theme_event_evidence,
)
from app.services.sources.language import detect_language, language_name
from app.services.sources.macro_evidence import (
    ThemeMacroEvidence,
    collect_theme_macro_evidence,
)
from app.services.sources.registry import build_registry, registry_gap_messages
from app.services.sources.translation import get_translation_provider

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


# Phase 31 hotfix: extracted document TEXT excerpt types (exclude the parsed
# financial fact, which is handled by ``_primary_facts``).
_EXTRACTED_EXCERPT_TYPES = _DOCUMENT_SOURCE_TYPES - {"company_ir_financial_fact"}
# The data-quality labels the connector layer stamps on metadata-only items —
# a located primary-source REFERENCE, not extracted text and not a parsed fact.
_METADATA_ONLY_QUALITIES = frozenset({"metadata_only", "link_metadata_only"})
# Reference source_types that specifically locate an issuer DOCUMENT (report /
# index) as opposed to an IR profile / press index.
_DOCUMENT_REFERENCE_TYPES = frozenset(
    {"company_ir_annual_report", "company_ir_annual_reports_index"}
)


def _reference_type_for(source_type: str | None) -> str:
    return {
        "company_ir_profile": "ir_profile",
        "company_ir_annual_reports_index": "filing_index",
        "company_ir_annual_report": "filing_link",
        "company_ir_press_release_index": "press_index",
    }.get(source_type or "", "source_reference")


def _source_reference_summary(evidence_items: list[Any]) -> dict[str, Any]:
    """Bounded, secret-free summary of metadata-only PRIMARY-source references.

    A *reference* locates a verified primary source (issuer IR page / annual-report
    index / regulator venue) but is NOT extracted document text and NOT a parsed
    financial fact. Counts every metadata-only item; the reference LIST is limited
    to T1/T2 (primary/regulated) references. Never emits raw document text.
    """
    from urllib.parse import urlsplit

    references: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    metadata_only = 0
    document_references = 0
    extracted_documents = 0
    for it in evidence_items:
        stype = getattr(it, "source_type", None)
        dq = getattr(it, "data_quality", None)
        tier = getattr(it, "content_source_tier", None) or ""
        if stype in _EXTRACTED_EXCERPT_TYPES:
            extracted_documents += 1
            continue
        if dq in _METADATA_ONLY_QUALITIES:
            metadata_only += 1
            if stype in _DOCUMENT_REFERENCE_TYPES:
                document_references += 1
            # Reference LIST: primary/regulated tiers only (T1/T2).
            if tier.startswith(("T1", "T2")):
                url = getattr(it, "url", None) or ""
                title = (getattr(it, "title", None) or "").strip()
                key = (title, url.split("?")[0])
                if key in seen:
                    continue
                seen.add(key)
                domain = ""
                try:
                    domain = (urlsplit(url).hostname or "").replace("www.", "")
                except (ValueError, TypeError):
                    domain = ""
                references.append(
                    {
                        "title": title or "Primary source reference",
                        "domain": domain,
                        "url": url or None,
                        "tier": tier,
                        "source_type": stype,
                        "reference_type": _reference_type_for(stype),
                        "requires_translation": bool(
                            getattr(it, "requires_translation", False)
                        ),
                        "warnings": list(getattr(it, "warnings", None) or [])[:2],
                    }
                )
    references = references[:8]
    return {
        "references": references,
        "counts": {
            "primary_source_reference_count": len(references),
            "primary_document_reference_count": document_references,
            "metadata_only_source_count": metadata_only,
            "extracted_primary_document_count": extracted_documents,
        },
    }


def _company_macro_theme(company: CompanyContext) -> str | None:
    """A broad macro theme for a company: its sector/industry (else None).

    Macro references are matched against this theme's keywords, so a copper miner
    (industry "Copper Mining") surfaces the commodity Pink Sheet, an energy name
    surfaces energy series, etc. This is deliberately coarse: macro context is
    thesis-level background, never a company-specific claim.
    """
    theme = " ".join(x for x in (company.sector, company.industry) if x).strip()
    return theme or None


def _macro_context_summary(macro: ThemeMacroEvidence) -> list[dict[str, Any]]:
    """Compact, secret-free MACRO CONTEXT reference summary — Phase 29C.1.

    One entry per macro source reference: its identity, official landing URL,
    tier, the indicators it publishes (reference text only — NO figures / index
    levels / dates), and the honest "figures not fetched" gap. Never a company
    catalyst and never a recommendation.
    """
    gap_by_source: dict[str | None, str] = {}
    for g in macro.source_gaps:
        gap_by_source.setdefault(g.source_id, g.as_message())
    out: list[dict[str, Any]] = []
    for it in macro.evidence_items:
        out.append(
            {
                "source_id": it.source_id,
                "source_name": it.source_name,
                "title": it.title,
                "url": it.url,
                "tier": it.content_source_tier,
                "reference": it.excerpt,
                "gap": gap_by_source.get(it.source_id),
            }
        )
    return out


def _company_event_theme(company: CompanyContext) -> str | None:
    """A broad procurement / tender theme for a company: its sector/industry.

    The event analog of ``_company_macro_theme``. Procurement / tender references
    are matched against this theme's keywords, so a defense contractor (industry
    "Aerospace & Defense") surfaces the procurement venues, an infrastructure /
    rail name surfaces the same, etc. Deliberately coarse: event context is
    thesis-level background, never a company-specific award or catalyst.
    """
    theme = " ".join(x for x in (company.sector, company.industry) if x).strip()
    return theme or None


def _event_context_summary(events: ThemeEventEvidence) -> list[dict[str, Any]]:
    """Compact, secret-free EVENT CONTEXT reference summary — Phase 29D.1.

    The event analog of ``_macro_context_summary``. One entry per procurement /
    tender source reference: its identity, official landing URL, tier, which
    tenders / awards the venue publishes (reference text only — NO specific award
    / contractor / amount / contract number / date), and the honest "live tenders
    / awards not fetched" gap. A WEAK thesis-level research-priority signal —
    never a company-specific claim, catalyst, materiality claim, or trade signal.
    """
    gap_by_source: dict[str | None, str] = {}
    for g in events.source_gaps:
        gap_by_source.setdefault(g.source_id, g.as_message())
    out: list[dict[str, Any]] = []
    for it in events.evidence_items:
        out.append(
            {
                "source_id": it.source_id,
                "source_name": it.source_name,
                "title": it.title,
                "url": it.url,
                "tier": it.content_source_tier,
                "reference": it.excerpt,
                "gap": gap_by_source.get(it.source_id),
            }
        )
    return out


async def _collect_translated_excerpts(
    evidence_items: list[Any],
    cfg: Settings,
    *,
    client: LLMClient | None = None,
) -> list[dict[str, Any]]:
    """Bounded, machine-assisted English renderings of non-English excerpts — 30A.

    For each connector ``EvidenceItem`` whose excerpt is non-English — declared via
    ``requires_translation`` OR detected by ``language.detect_language`` — produce
    ONE bounded translated excerpt via the configured provider (the deterministic
    *fake* provider by default). The ORIGINAL excerpt and its token-stripped source
    URL are ALWAYS preserved so a council / report can cite the original source; the
    translation is clearly marked machine-assisted and needs human review — never an
    official translation, and the original evidence is never removed or replaced.

    Bounded by ``source_translation_max_excerpts`` items, each excerpt bounded by
    ``source_translation_max_chars`` (both input and output) — never a whole
    document. Text-free by construction here: only counts + language codes are
    logged by the caller; the provider itself never logs prompts / original /
    translated text.
    """
    max_excerpts = max(0, int(cfg.source_translation_max_excerpts))
    if max_excerpts == 0:
        return []
    max_chars = cfg.source_translation_max_chars
    provider = get_translation_provider(cfg, client=client)
    out: list[dict[str, Any]] = []
    for it in evidence_items:
        if len(out) >= max_excerpts:
            break
        excerpt = (getattr(it, "excerpt", None) or "").strip()
        if not excerpt:
            continue
        original_language = getattr(it, "original_language", None)
        detected = detect_language(excerpt, hint=original_language)
        needs = bool(getattr(it, "requires_translation", False)) or detected != "en"
        if not needs:
            continue
        source_language = (original_language or detected or "und").strip().lower()[:2]
        translation = await provider.translate(
            excerpt, source_language, max_chars=max_chars
        )
        out.append(
            {
                # Token-stripped by the EvidenceItem model already; preserved so the
                # ORIGINAL source stays the citation of record.
                "source_url": getattr(it, "url", None),
                "title": getattr(it, "title", None),
                "source_type": getattr(it, "source_type", None),
                "original_language": translation.source_language,
                "original_language_name": language_name(translation.source_language),
                "original_excerpt": translation.original_text,
                "translated_excerpt": translation.translated_text,
                "target_language": translation.target_language,
                "provider": translation.provider_name,
                "needs_human_review": True,
                "warning": translation.warning,
            }
        )
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

    # Phase 32A Slice 3: retain a runtime-only snapshot of the (post-budget)
    # evidence pack so a cited ``E#`` alias can be resolved to a canonical
    # Source/Citation when the report is persisted. E# is a run-local presentation
    # alias only; ``uid`` is a stable per-item identity. Gated on the flag +
    # excluded from serialization ⇒ the dark path is byte-identical.
    if cfg.report_citation_persistence_enabled:
        result.persistable_evidence = [
            PersistableEvidence(
                uid=uuid.uuid4().hex,
                alias=item.id,
                source_tier=item.source_tier,
                source_type=item.source_type,
                provider_transport=item.provider_transport,
                transport_tier=item.transport_tier,
                content_tier=item.content_tier,
                title=item.title,
                url=item.url,
                date=item.date,
                excerpt=item.excerpt,
                data_quality=item.data_quality,
                fields_supported=list(item.fields_supported),
                relevance_level=item.relevance_level,
                source_id=item.source_id,
                primary_fact=item.primary_fact,
                provenance=list(item.provenance),
            )
            for item in evidence_pack.evidence_items
        ]

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
        # Phase 31 hotfix: bounded, secret-free PRIMARY-source references (verified
        # metadata-only items) + their counts + honest source-gap strings. Stay
        # empty (and unattached) when the connector layer is off → byte-identical.
        primary_source_references: list[dict[str, Any]] = []
        source_reference_counts: dict[str, int] = {}
        source_gap_messages_bounded: list[str] = []
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
                # Phase 31 hotfix: classify metadata-only PRIMARY-source references
                # (issuer IR / annual-report index / regulator venue) so the report
                # can surface them, distinct from extracted text and parsed facts.
                reference_summary = _source_reference_summary(collected.evidence_items)
                primary_source_references = reference_summary["references"]
                source_reference_counts = dict(reference_summary["counts"])
                source_reference_counts["source_gap_count"] = len(
                    connector_gap_messages or []
                )
                source_gap_messages_bounded = list(connector_gap_messages or [])[:8]
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
                    primary_source_reference_count=source_reference_counts[
                        "primary_source_reference_count"
                    ],
                    metadata_only_source_count=source_reference_counts[
                        "metadata_only_source_count"
                    ],
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

        # Phase 29C.1: optional MACRO CONTEXT. When ``source_macro_enabled`` is on,
        # collect bounded, reference-only macro sources for the company's broad
        # theme (sector/industry). Dark by default (flag off → empty, byte-identical
        # behaviour); never fetches figures; never crashes the council.
        macro_context: list[dict[str, Any]] = []
        if cfg.source_macro_enabled:
            try:
                company_ctx = _company_context(company_snapshot, ticker, exchange)
                macro = await collect_theme_macro_evidence(
                    _company_macro_theme(company_ctx), company_ctx.country, cfg
                )
                macro_context = _macro_context_summary(macro)
                log_event(
                    log,
                    "macro_context_collected",
                    report_id=report_id,
                    ticker=ticker,
                    exchange=exchange,
                    macro_item_count=len(macro_context),
                )
            except Exception as exc:  # noqa: BLE001 - macro layer never crashes a report
                log_event(
                    log,
                    "macro_context_failed",
                    level=logging.WARNING,
                    report_id=report_id,
                    ticker=ticker,
                    exception_type=type(exc).__name__,
                )

        # Phase 29D.1: optional EVENT CONTEXT. When ``source_event_enabled`` is on,
        # collect bounded, reference-only procurement / tender sources for the
        # company's broad theme (sector/industry, else country/region). Dark by
        # default and independent of the macro flag (event off → empty,
        # byte-identical behaviour); never fetches an award; never crashes the
        # council. WEAK research-priority CONTEXT only — never a company catalyst.
        event_context: list[dict[str, Any]] = []
        if cfg.source_event_enabled:
            try:
                company_ctx = _company_context(company_snapshot, ticker, exchange)
                events = await collect_theme_event_evidence(
                    _company_event_theme(company_ctx), company_ctx.country, cfg
                )
                event_context = _event_context_summary(events)
                log_event(
                    log,
                    "event_context_collected",
                    report_id=report_id,
                    ticker=ticker,
                    exchange=exchange,
                    event_item_count=len(event_context),
                )
            except Exception as exc:  # noqa: BLE001 - event layer never crashes a report
                log_event(
                    log,
                    "event_context_failed",
                    level=logging.WARNING,
                    report_id=report_id,
                    ticker=ticker,
                    exception_type=type(exc).__name__,
                )

        # Phase 30A: optional TRANSLATED EVIDENCE. When ``source_translation_enabled``
        # is on, machine-translate any non-English connector excerpt into bounded
        # English research CONTEXT, ALWAYS preserving the original text + source URL
        # (the citation of record) and clearly marking it machine-assisted / needs
        # human review. Dark by default (flag off → empty, byte-identical behaviour);
        # never a whole document; never crashes the council. Additive only — the
        # original evidence in the pack is untouched.
        translated_excerpts: list[dict[str, Any]] = []
        if cfg.source_translation_enabled:
            try:
                translated_excerpts = await _collect_translated_excerpts(
                    connector_evidence or [], cfg, client=resolved
                )
                log_event(
                    log,
                    "translated_excerpts_collected",
                    report_id=report_id,
                    ticker=ticker,
                    exchange=exchange,
                    translated_excerpt_count=len(translated_excerpts),
                    source_languages=sorted(
                        {t["original_language"] for t in translated_excerpts}
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - translation never crashes a report
                log_event(
                    log,
                    "translated_excerpts_failed",
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
        # Phase 31 hotfix: attach the bounded metadata-only PRIMARY-source
        # references + counts + honest gaps so the report / memo can surface which
        # verified primary sources were located (distinct from extracted text and
        # parsed facts). Attach only when non-empty → dark-by-default byte-identical.
        if primary_source_references:
            result.primary_source_references = primary_source_references
        if any(source_reference_counts.values()):
            result.source_reference_counts = source_reference_counts
        if source_gap_messages_bounded:
            result.source_gaps = source_gap_messages_bounded
        # Phase 29C.1: attach the bounded macro CONTEXT references so the report can
        # render an optional macro-context block (background only, never a catalyst).
        if macro_context:
            result.macro_context = macro_context
        # Phase 29D.1: attach the bounded EVENT CONTEXT references so the report can
        # render an optional event-context block (WEAK research-priority background
        # only, never a company-specific award, catalyst, or trade signal).
        if event_context:
            result.event_context = event_context
        # Phase 30A: attach the bounded machine-assisted translated excerpts so the
        # report can render an optional translated-evidence block. Each entry keeps
        # the original text + source URL and is clearly marked NOT official.
        if translated_excerpts:
            result.translated_excerpts = translated_excerpts
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
