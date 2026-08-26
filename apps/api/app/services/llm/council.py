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

import asyncio
import logging
import random
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from app.core.config import Settings
from app.core.config import settings as default_settings
from app.core.structured_logging import log_event
from app.services.llm import prompts, retry_engine
from app.services.llm.citation_checker import check_and_sanitize
from app.services.llm.client import (
    LLMClient,
    LLMError,
    LLMRateLimitError,
    get_llm_client,
)
from app.services.llm.evidence_pack import build_evidence_pack
from app.services.llm.schemas import (
    AGENT_BUSINESS_MOAT,
    AGENT_CATALYST,
    AGENT_COMMITTEE_CHAIR,
    AGENT_FINANCIAL_ANALYST,
    AGENT_RED_TEAM,
    AGENT_RISK_GOVERNANCE,
    AGENT_SOURCE_QUALITY_CRITIC,
    AGENT_VALUATION_GUARD,
    COUNCIL_AGENT_ORDER,
    CRITICAL_ALWAYS,
    DEFAULT_COMMITTEE_LABEL,
    RESERVED_AGENTS,
    STATUS_COMPLETED,
    STATUS_FAILED,
    CouncilAgentOutput,
    CouncilResult,
    EvidencePack,
    PersistableEvidence,
    has_financial_evidence,
)
from app.services.llm.token_pacer import (
    CouncilUsageTracker,
    TokenBudgetPacer,
    estimate_request_tokens,
    get_shared_pacer,
)
from app.services.sources.company_evidence import (
    SEC_DOCUMENT_EXCERPT_TYPE,
    SEC_DOCUMENT_FACT_TYPE,
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

if TYPE_CHECKING:  # reuse lookup is a plain in-memory dict — never a DB session.
    from app.services.extracted_document_service import ReusedDocument

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
        # Phase 32A Slice 5B.1 hotfix: SEC filing-BODY evidence
        # (`company_evidence.sec_artifacts_to_evidence`) was never added here, so
        # a successful SEC extraction never appeared in `primary_documents` /
        # `extracted_primary_document_count` even though its citations resolved
        # correctly end-to-end — proven live on staging (AAPL 10-Q/8-K, a real
        # validated `cash_and_equivalents` fact). This was a report-summary gap,
        # not a fabrication or safety issue: the underlying evidence and
        # citations were always correct.
        SEC_DOCUMENT_EXCERPT_TYPE,
        SEC_DOCUMENT_FACT_TYPE,
    }
)

# Fact-shaped source_types within _DOCUMENT_SOURCE_TYPES — used to route an item
# to fact_count vs excerpt_count in _primary_document_summary.
_DOCUMENT_FACT_TYPES = frozenset({"company_ir_financial_fact", SEC_DOCUMENT_FACT_TYPE})


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
        if stype in _DOCUMENT_FACT_TYPES:
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


def _structured_facts(
    evidence_items: list[Any], *, confidences: "frozenset[str]"
) -> list[dict[str, Any]]:
    """Structured fact payloads from fact-shaped EvidenceItems.

    Reads only the STRUCTURED ``primary_fact`` payload each item carries
    (field / value / numeric_value / unit / currency / scale / period / scope +
    short page/excerpt provenance) — never the raw excerpt body or document
    text. The item's own token-stripped URL is preferred as provenance.
    """
    out: list[dict[str, Any]] = []
    for it in evidence_items:
        if getattr(it, "source_type", None) not in _DOCUMENT_FACT_TYPES:
            continue
        pf = getattr(it, "primary_fact", None)
        if pf is None:
            continue
        if getattr(pf, "confidence", None) not in confidences:
            continue
        data = pf.model_dump(mode="json") if hasattr(pf, "model_dump") else dict(pf)
        url = getattr(it, "url", None)
        if url:
            data["source_url"] = url
        out.append(data)
    return out


def _historical_facts_from_artifacts(artifacts: "list[Any] | None") -> list[dict[str, Any]]:
    # Local imports keep this module free of an import cycle with the
    # extraction layer (the same pattern the persistence writer uses).
    """Every validated fact from the ingested documents — UNCAPPED.

    Live-acceptance corrective (2026-08-26). ``_historical_facts`` reads the
    EVIDENCE ITEMS, and those are capped per document
    (``primary_document_evidence_cap``, default 10) so a rich document cannot
    flood the council prompt. That cap is correct for the prompt and fatal for a
    SERIES: the real Pandora annual report yields 52 period-scoped facts
    covering FY2021-FY2025, of which only ~10 became evidence items — so every
    metric arrived as a single FY2025 observation and the report said
    "no multi-period financial series was reconstructed" beside a database
    holding five years of them.

    The artifacts carry the COMPLETE validated fact set for each document, and
    they are populated on every deep-ingestion path regardless of the
    persistence flags. Reading them here keeps the prompt bound exactly where it
    belongs — PR-B already renders each series as ONE dense line — while letting
    the series see everything that was actually extracted.

    Low-confidence facts stay excluded, as in the evidence-item path.
    """
    from app.services.sources.extracted_fact_validator import VALIDATION_VALIDATED
    from app.services.sources.primary_document_extractor import _confidence_bucket

    out: list[dict[str, Any]] = []
    for artifact in artifacts or []:
        url = getattr(artifact, "source_url", None)
        for fact in getattr(artifact, "validated_facts", None) or []:
            if getattr(fact, "validation_status", None) != VALIDATION_VALIDATED:
                continue
            confidence = getattr(fact, "confidence", 0.0)
            if isinstance(confidence, str):
                bucket = confidence
            else:
                bucket = _confidence_bucket(float(confidence or 0.0))
            if bucket not in ("high", "medium"):
                continue
            out.append(
                {
                    "field": getattr(fact, "label", None),
                    "value": getattr(fact, "value_text", None),
                    "numeric_value": getattr(fact, "value_numeric", None),
                    "unit": getattr(fact, "unit", None),
                    "currency": getattr(fact, "currency", None),
                    "scale": getattr(fact, "scale", None),
                    "period": getattr(fact, "period", None),
                    "scope": getattr(fact, "scope", None),
                    "page_number": getattr(fact, "page_number", None),
                    "table_location": getattr(fact, "table_location", None),
                    "confidence": bucket,
                    "source_url": url,
                }
            )
    return out


def _historical_facts(evidence_items: list[Any]) -> list[dict[str, Any]]:
    """High AND medium confidence facts — private-use readiness PR-B.

    Deliberately wider than ``_primary_facts``. A canonical single-value slot
    must not be filled by a medium-confidence figure; a five-year SERIES whose
    middle years are medium-confidence is still a real, citeable trend, and
    dropping them is how a report ends up asserting "no historical revenue
    trend information" beside a complete table. Low confidence stays out of
    both.
    """
    return _structured_facts(evidence_items, confidences=frozenset({"high", "medium"}))


def _primary_facts(evidence_items: list[Any]) -> list[dict[str, Any]]:
    """Structured, bounded HIGH-CONFIDENCE primary facts — Phase 29B.3.

    Reads the STRUCTURED ``primary_fact`` payload each fact-shaped EvidenceItem
    carries (field / value / numeric_value / unit / currency / scale / period +
    short page/excerpt provenance) — never the raw excerpt body or document
    text. Only ``confidence == "high"`` facts are surfaced: a matching
    high-confidence fact is precisely what lets the final report present a real
    T1 primary-filing datapoint. The item's own token-stripped URL is preferred
    as the fact's provenance URL.

    Phase 32A Slice 5B.2 fix: this previously matched ONLY
    ``"company_ir_financial_fact"``, so a SEC/XBRL-sourced fact
    (``SEC_DOCUMENT_FACT_TYPE`` — e.g. the already-live AAPL
    ``cash_and_equivalents`` fact) never reached ``primary_facts`` even though
    its citation resolved correctly end-to-end. ``_DOCUMENT_FACT_TYPES``
    (already used by ``_primary_document_summary``) is the correct, complete
    set of fact-shaped source_types — reusing it here is the fix.
    """
    return _structured_facts(evidence_items, confidences=frozenset({"high"}))


# Phase 31 hotfix: extracted document TEXT excerpt types (exclude the parsed
# IR financial fact, which is handled by ``_primary_facts``).
#
# NOTE (Slice 5B.1 hotfix 2): unlike ``company_ir_financial_fact``,
# ``SEC_DOCUMENT_FACT_TYPE`` is deliberately NOT excluded here. ``_primary_facts``
# only reads the IR fact shape, so a SEC-only filing (structured table data, no
# prose excerpt — exactly what a real AAPL 10-Q/8-K produced on staging) would
# otherwise be invisible to every counter if it were excluded here too.
# Pre-existing, unchanged caveat: ``extracted_documents`` below counts EVIDENCE
# ITEMS, not distinct documents (an excerpt and a fact from the SAME filing both
# increment it) — this was already true for company-IR excerpts before this
# change and is out of scope for this fix; ``_primary_document_summary`` is the
# function that correctly groups by document identity.
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


def _truncate_at_word(text: str, max_chars: int) -> str:
    """Deterministic word-boundary truncation with an explicit ellipsis."""
    if max_chars <= 0 or len(text) <= max_chars:
        return text
    cut = text[:max_chars].rsplit(" ", 1)[0].rstrip()
    return (cut or text[:max_chars].rstrip()) + " …"


def _compact_agent_line(o: CouncilAgentOutput, max_chars: int) -> str:
    """One bounded chair-input line for a completed agent (Phase 32A TPM slice).

    Deterministic EXTRACTION of the agent's own structured fields — never a
    re-summarization, never a new claim. Retains: the (truncated) conclusion,
    the cited evidence ids, the top risk/gap items, and the unsupported-claim
    count, so compaction can never hide a dissent or a citation from the chair.
    """
    parts = [_truncate_at_word((o.summary or "").strip(), max_chars)]
    cited: list[str] = []
    for kp in o.key_points:
        for cid in kp.citation_ids:
            if cid not in cited:
                cited.append(cid)
    if cited:
        parts.append("cites: " + ",".join(cited[:8]))
    risks = [
        _truncate_at_word((r.item or "").strip(), 90)
        for r in o.risks_or_gaps[:2]
        if (r.item or "").strip()
    ]
    if risks:
        parts.append("risks: " + "; ".join(risks))
    if o.unsupported_claims:
        parts.append(f"unsupported_claims: {len(o.unsupported_claims)}")
    return f"- {o.agent_name}: " + " | ".join(part for part in parts if part)


def _prior_summaries(
    outputs: list[CouncilAgentOutput], max_chars: int = 0
) -> str:
    """The chair's prior-agent digest.

    ``max_chars <= 0`` (default) reproduces the historic behaviour byte-for-byte
    (full summaries, failed agents silently omitted). When > 0 each completed
    agent gets one bounded ``_compact_agent_line`` and the failed agents are
    named explicitly — the chair keeps the failure metadata without the cost of
    full prose. This is what keeps the LAST and LARGEST council request inside
    a constrained deployment's TPM window.
    """
    if max_chars <= 0:
        lines = []
        for o in outputs:
            if o.status == STATUS_FAILED:
                continue
            summary = (o.summary or "").strip()
            if summary:
                lines.append(f"- {o.agent_name}: {summary}")
        return "\n".join(lines)

    lines = []
    failed: list[str] = []
    for o in outputs:
        if o.status == STATUS_FAILED:
            failed.append(o.agent_name)
            continue
        if (o.summary or "").strip():
            lines.append(_compact_agent_line(o, max_chars))
    if failed:
        lines.append("- did_not_complete: " + ", ".join(failed))
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Phase 32A Slice 4 — single-agent attempt + retry orchestration
# ---------------------------------------------------------------------------


def _messages_for(
    agent_name: str,
    evidence_json: str,
    result: CouncilResult,
    *,
    chair_summary_max_chars: int = 0,
) -> tuple[str, str]:
    """Build (system, user) for one agent from the CURRENT council state.

    The committee chair's user message is rebuilt from the current (possibly
    recovered) prior summaries every time it is called, so a chair retry
    synthesizes over agents that recovered in the retry pass (req #10).
    ``chair_summary_max_chars`` > 0 compacts each prior summary deterministically
    (Phase 32A TPM slice) — 0 keeps the historic chair prompt byte-identical.
    """
    if agent_name == AGENT_COMMITTEE_CHAIR:
        system = prompts.committee_chair_system_prompt()
        user = prompts.build_user_message(
            evidence_json,
            _prior_summaries(result.agents, chair_summary_max_chars),
        )
    else:
        system = prompts.system_prompt_for(agent_name)
        user = prompts.build_user_message(evidence_json)
    return system, user


async def _run_agent_attempt(
    agent_name: str,
    evidence_json: str,
    evidence_ids: set[str],
    result: CouncilResult,
    client: LLMClient,
    cfg: Settings,
    evidence_by_id: dict[str, Any] | None = None,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> tuple[CouncilAgentOutput, list[str], Exception | None]:
    """Run ONE attempt for an agent. Never raises.

    Returns ``(output, issues, exc)``. On success ``output`` is the sanitized
    agent output and ``exc`` is None (``output.status`` may still be ``failed``
    if the safety gate quarantined it — a PERMANENT outcome). On an ``LLMError``
    ``output`` is the failed placeholder and ``exc`` is the (possibly transient)
    exception. This is the single-agent primitive BOTH the OFF path and the
    ON (retry) path call.

    ``evidence_by_id`` (id -> evidence-pack ``EvidenceItem``) enables the
    citation checker's semantic-grounding check (Phase 32A hotfix).
    ``known_gaps`` (the run's ``EvidencePack.known_gaps``) enables the
    gap-attribution grounding check (corrective, post-#99/#100).
    """
    system, user = _messages_for(
        agent_name,
        evidence_json,
        result,
        chair_summary_max_chars=cfg.llm_council_chair_prior_summary_max_chars,
    )
    # Phase 32A TPM slice: advisory provider-aware pacing. Wait (bounded) for
    # window headroom before firing; the chair draws on its reserved slice.
    lease = None
    paced_wait = 0.0
    if pacer is not None:
        lease = await pacer.acquire(
            estimate_request_tokens(system, user, cfg.llm_max_output_tokens),
            reserve_tokens=cfg.llm_council_chair_token_reserve,
            use_reserve=(agent_name == AGENT_COMMITTEE_CHAIR),
            max_wait_seconds=cfg.llm_council_pacing_max_wait_seconds,
        )
        paced_wait = lease.waited_seconds
    try:
        raw = await client.complete_json(
            system,
            user,
            max_tokens=cfg.llm_max_output_tokens,
            temperature=cfg.llm_temperature,
            timeout=cfg.llm_request_timeout_seconds,
            repair_instruction=prompts.REPAIR_INSTRUCTION,
        )
    except LLMError as exc:
        usage = client.consume_usage()
        if pacer is not None and lease is not None:
            # A rate-limited request spent no quota; other failures keep the
            # estimate unless the provider reported real (partial) usage.
            if isinstance(exc, LLMRateLimitError):
                pacer.settle(lease, usage.total_tokens if usage else 0)
            else:
                pacer.settle(lease, usage.total_tokens if usage else None)
        if tracker is not None:
            tracker.record_attempt(
                agent_name,
                prompt_tokens=usage.prompt_tokens if usage else 0,
                completion_tokens=usage.completion_tokens if usage else 0,
                total_tokens=usage.total_tokens if usage else 0,
                estimated=bool(usage.estimated) if usage else False,
                error_type=type(exc).__name__,
                paced_wait_seconds=paced_wait,
            )
        placeholder = CouncilAgentOutput(
            agent_name=agent_name,
            status=STATUS_FAILED,
            summary="[Agent did not complete: provider error or timeout.]",
            safety_notes=[f"Agent failed ({type(exc).__name__})."],
        )
        return placeholder, [f"{agent_name}: {type(exc).__name__}"], exc
    usage = client.consume_usage()
    if pacer is not None and lease is not None:
        pacer.settle(lease, usage.total_tokens if usage else None)
    if tracker is not None:
        tracker.record_attempt(
            agent_name,
            prompt_tokens=usage.prompt_tokens if usage else 0,
            completion_tokens=usage.completion_tokens if usage else 0,
            total_tokens=usage.total_tokens if usage else 0,
            estimated=bool(usage.estimated) if usage else False,
            error_type=None,
            paced_wait_seconds=paced_wait,
        )
    output = _coerce_output(agent_name, raw)
    sanitized, issues = check_and_sanitize(
        output, evidence_ids, evidence_by_id, known_gaps
    )
    return sanitized, issues, None


async def _timed_attempt(
    agent_name: str,
    evidence_json: str,
    evidence_ids: set[str],
    result: CouncilResult,
    client: LLMClient,
    cfg: Settings,
    evidence_by_id: dict[str, Any] | None = None,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> tuple[CouncilAgentOutput, list[str], Exception | None, int]:
    """``_run_agent_attempt`` plus a wall-clock duration_ms for logging."""
    started = time.perf_counter()
    output, issues, exc = await _run_agent_attempt(
        agent_name,
        evidence_json,
        evidence_ids,
        result,
        client,
        cfg,
        evidence_by_id,
        known_gaps,
        pacer,
        tracker,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    return output, issues, exc, duration_ms


def _log_agent_outcome(
    log: logging.Logger,
    agent_name: str,
    output: CouncilAgentOutput,
    exc: Exception | None,
    duration_ms: int,
    *,
    cfg: Settings,
    client: LLMClient,
    report_id: str | None,
    ticker: str | None,
    attempt: int | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """Emit the safe completed/failed telemetry for one attempt (no prompts)."""
    # Phase 32A TPM slice: per-attempt token accounting (counts only). The
    # tracker's last record for this agent IS this attempt (attempts run
    # strictly sequentially per agent).
    last = tracker.last_by_agent.get(agent_name) if tracker is not None else None
    usage_fields: dict[str, Any] = {}
    if last is not None:
        usage_fields = {
            "prompt_tokens": last.prompt_tokens,
            "completion_tokens": last.completion_tokens,
            "total_tokens": last.total_tokens,
            "tokens_estimated": last.estimated or None,
        }
    if exc is not None:
        retry_after = getattr(exc, "retry_after", None)
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
            attempt=attempt,
            retry_after_seconds=retry_after,
            **usage_fields,
        )
    elif output.status == STATUS_FAILED:
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
            attempt=attempt,
            **usage_fields,
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
            status=output.status,
            key_point_count=len(output.key_points),
            attempt=attempt,
            **usage_fields,
        )


def _budget_exhausted_output(agent_name: str) -> CouncilAgentOutput:
    """A failed placeholder for an agent that could not START before the deadline.

    Thin company-specific adapter (Phase 32A Slice 6A step 1) over
    ``retry_engine.build_budget_exhausted_output``.
    """
    return retry_engine.build_budget_exhausted_output(
        agent_name, CouncilAgentOutput, failed_status=STATUS_FAILED
    )


def _critical_agents(evidence_pack: EvidencePack) -> frozenset[str]:
    """The critical-agent set for this pack (valuation_guard only if financial)."""
    if has_financial_evidence(evidence_pack):
        return CRITICAL_ALWAYS | {AGENT_VALUATION_GUARD}
    return CRITICAL_ALWAYS


def _retry_priority_order(critical: frozenset[str]) -> list[str]:
    """Order transiently-failed agents are retried in (chair last).

    Critical analytics first, then optional context agents, then the reserved
    red_team, then the committee chair. ``valuation_guard`` is retried ONLY when
    it is critical (the pack has financial evidence).
    """
    order = [AGENT_FINANCIAL_ANALYST]
    if AGENT_VALUATION_GUARD in critical:
        order.append(AGENT_VALUATION_GUARD)
    order.append(AGENT_SOURCE_QUALITY_CRITIC)
    order.extend([AGENT_BUSINESS_MOAT, AGENT_CATALYST, AGENT_RISK_GOVERNANCE])
    order.append(AGENT_RED_TEAM)
    order.append(AGENT_COMMITTEE_CHAIR)
    return order


def _replace_agent(
    result: CouncilResult,
    agent_name: str,
    output: CouncilAgentOutput,
    issues: list[str],
) -> None:
    """Replace a failed placeholder IN PLACE (never append) and refresh warnings.

    The agent's earlier per-agent warnings are removed so ``result.warnings``
    reflects the FINAL state — a recovered agent leaves no stale failure note and
    honest counts are preserved. ``len(result.agents)`` is unchanged (idempotent:
    exactly one entry per agent name).
    """
    for i, existing in enumerate(result.agents):
        if existing.agent_name == agent_name:
            result.agents[i] = output
            break
    prefix = f"{agent_name}: "
    result.warnings = [w for w in result.warnings if not w.startswith(prefix)]
    result.warnings.extend(issues)


def _deterministic_chair_fallback(
    agents: list[CouncilAgentOutput], order: tuple[str, ...]
) -> CouncilAgentOutput:
    """A deterministic, non-consensus committee summary (req #11-12).

    Built only from ALREADY-VALIDATED stored council outputs. It NEVER makes a
    recommendation, valuation conclusion, or numeric price objective: the label is
    the honest ``insufficient_data`` and ``key_points`` is empty (so it carries no
    citations). The wording deliberately avoids the forbidden safety substrings
    (e.g. "price target", "fair value") so it survives ``check_and_sanitize``.

    Thin company-specific adapter (Phase 32A Slice 6A) over
    ``retry_engine.build_deterministic_synthesis``: the shared engine returns a
    generic ``DeterministicSynthesis`` (completed/failed names + prose), and
    THIS function builds the actual ``CouncilAgentOutput`` — with the
    company-specific ``committee_label`` field and empty ``key_points`` /
    ``risks_or_gaps`` (so the fallback carries no citations) — because that
    field name/shape is specific to the company council, not the shared engine.
    """
    synthesis = retry_engine.build_deterministic_synthesis(
        agents,
        order,
        AGENT_COMMITTEE_CHAIR,
        completed_status=STATUS_COMPLETED,
        failed_status=STATUS_FAILED,
        summary_noun="committee",
    )
    return CouncilAgentOutput(
        agent_name=AGENT_COMMITTEE_CHAIR,
        status=STATUS_COMPLETED,
        committee_label=DEFAULT_COMMITTEE_LABEL,
        summary=synthesis.summary,
        key_points=[],
        risks_or_gaps=[],
        unsupported_claims=[],
        safety_notes=[synthesis.safety_note],
    )


async def _run_offline_pass(
    *,
    evidence_json: str,
    evidence_ids: set[str],
    result: CouncilResult,
    client: LLMClient,
    cfg: Settings,
    log: logging.Logger,
    report_id: str | None,
    ticker: str | None,
    evidence_by_id: dict[str, Any] | None = None,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """The OFF path: one attempt per agent, no retries — byte-identical to pre-Slice-4."""
    for agent_name in COUNCIL_AGENT_ORDER:
        output, issues, exc, duration_ms = await _timed_attempt(
            agent_name,
            evidence_json,
            evidence_ids,
            result,
            client,
            cfg,
            evidence_by_id,
            known_gaps,
            pacer,
            tracker,
        )
        result.agents.append(output)
        result.warnings.extend(issues)
        _log_agent_outcome(
            log,
            agent_name,
            output,
            exc,
            duration_ms,
            cfg=cfg,
            client=client,
            report_id=report_id,
            ticker=ticker,
            tracker=tracker,
        )


def _make_attempt(
    evidence_json: str,
    evidence_ids: set[str],
    result: CouncilResult,
    client: LLMClient,
    cfg: Settings,
    evidence_by_id: dict[str, Any] | None = None,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> retry_engine.AttemptFn:
    """Bind the company-specific single-attempt primitive for the retry engine.

    Wraps ``_timed_attempt`` (which already closes over evidence/result/client/
    cfg) into the ``agent_name -> (output, issues, exc, duration_ms)`` shape
    ``retry_engine`` expects.
    """

    async def _attempt(agent_name: str) -> retry_engine.AttemptResult:
        return await _timed_attempt(
            agent_name,
            evidence_json,
            evidence_ids,
            result,
            client,
            cfg,
            evidence_by_id,
            known_gaps,
            pacer,
            tracker,
        )

    return _attempt


def _make_replace_agent(result: CouncilResult) -> retry_engine.ReplaceAgentFn:
    """Bind ``_replace_agent`` to a specific ``result`` for the retry engine."""

    def _replace(agent_name: str, output: Any, issues: list[str]) -> None:
        _replace_agent(result, agent_name, output, issues)

    return _replace


def _make_status_of(result: CouncilResult) -> retry_engine.StatusOfFn:
    """The current status of an already-attempted agent, or ``None``."""

    def _status_of(agent_name: str) -> str | None:
        entry = next((a for a in result.agents if a.agent_name == agent_name), None)
        return entry.status if entry is not None else None

    return _status_of



def _chair_failure_reason(attempts: int, last_error: str | None) -> str:
    """Why the chair did not complete — never ``None`` when it failed.

    Phase 32A TPM corrective (live staging, 2026-08-23): a chair that never got
    an attempt (the wall budget was exhausted before its turn) recorded NO
    error, so the failure surfaced as an empty ``chair_error_type`` — reading
    like "no error" next to a failure-default label. The three outcomes are now
    always distinguishable:

      * ``budget_exhausted``       — never ran; council wall budget ran out.
      * a provider error class     — ran and failed transiently/permanently
                                     (e.g. ``LLMRateLimitError``).
      * ``quarantined_or_unparsed``— ran and returned, but the safety/schema
                                     gate rejected the output (a CONTENT
                                     outcome, not an infrastructure one).
    """
    if last_error:
        return last_error
    return "budget_exhausted" if attempts == 0 else "quarantined_or_unparsed"

def _make_log_outcome(
    log: logging.Logger,
    cfg: Settings,
    client: LLMClient,
    report_id: str | None,
    ticker: str | None,
    tracker: CouncilUsageTracker | None = None,
) -> retry_engine.LogOutcomeFn:
    """Bind ``_log_agent_outcome`` to the run's fixed logging context."""

    def _log_outcome(
        agent_name: str,
        output: Any,
        exc: Exception | None,
        duration_ms: int,
        attempt_number: int | None,
    ) -> None:
        _log_agent_outcome(
            log,
            agent_name,
            output,
            exc,
            duration_ms,
            cfg=cfg,
            client=client,
            report_id=report_id,
            ticker=ticker,
            attempt=attempt_number,
            tracker=tracker,
        )

    return _log_outcome


async def _run_council_with_retries(
    *,
    evidence_pack: EvidencePack,
    evidence_json: str,
    evidence_ids: set[str],
    result: CouncilResult,
    client: LLMClient,
    cfg: Settings,
    log: logging.Logger,
    report_id: str | None,
    ticker: str | None,
    clock: Callable[[], float],
    sleeper: Callable[[float], Awaitable[Any]],
    rng: random.Random,
    evidence_by_id: dict[str, Any] | None = None,
    known_gaps: list[str] | None = None,
    pacer: TokenBudgetPacer | None = None,
    tracker: CouncilUsageTracker | None = None,
) -> None:
    """The ON path: initial pass under a deadline + a priority retry pass.

    Thin company-specific adapter (Phase 32A Slice 6A step 1) over
    ``retry_engine.run_with_retries``: supplies the company council's agent
    order, critical-agent set, retry-priority order, and the ``result``
    mutation/lookup callbacks the generic engine needs.
    """
    critical = _critical_agents(evidence_pack)
    await retry_engine.run_with_retries(
        agent_order=COUNCIL_AGENT_ORDER,
        critical=critical,
        priority_order=_retry_priority_order(critical),
        reserved=RESERVED_AGENTS,
        attempt=_make_attempt(
            evidence_json,
            evidence_ids,
            result,
            client,
            cfg,
            evidence_by_id,
            known_gaps,
            pacer,
            tracker,
        ),
        append_output=result.agents.append,
        extend_warnings=result.warnings.extend,
        replace_agent=_make_replace_agent(result),
        status_of=_make_status_of(result),
        log_outcome=_make_log_outcome(log, cfg, client, report_id, ticker, tracker),
        budget_exhausted_output=_budget_exhausted_output,
        log=log,
        report_id=report_id,
        ticker=ticker,
        provider=client.provider_name,
        council_version=cfg.llm_council_version,
        clock=clock,
        sleeper=sleeper,
        rng=rng,
        total_budget_seconds=cfg.llm_council_total_budget_seconds,
        critical_reserve_seconds=cfg.llm_council_critical_reserve_seconds,
        max_retries=cfg.llm_council_max_retries,
        critical_max_retries=cfg.llm_council_critical_max_retries,
        base_backoff_seconds=cfg.llm_council_retry_base_backoff_seconds,
        max_backoff_seconds=cfg.llm_council_retry_max_backoff_seconds,
        max_retry_after_seconds=cfg.llm_council_retry_max_retry_after_seconds,
        completed_status=STATUS_COMPLETED,
        failed_status=STATUS_FAILED,
        initial_pass_delay_seconds=cfg.llm_council_initial_pass_delay_seconds,
    )


async def run_council(
    evidence_pack: EvidencePack,
    client: LLMClient,
    *,
    cfg: Settings | None = None,
    report_id: str | None = None,
    ticker: str | None = None,
    exchange: str | None = None,
    logger: logging.Logger | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleeper: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    rng: random.Random | None = None,
    pacer: TokenBudgetPacer | None = None,
) -> CouncilResult:
    """Run every council agent over the evidence pack and return the result.

    When ``cfg.llm_council_retry_enabled`` is False (default) the OFF path runs:
    one attempt per agent, no retries, no chair fallback — behaviorally identical
    to pre-Slice-4. When True, an initial pass plus a bounded, priority-ordered
    retry pass runs under a strict total wall-time budget, and a deterministic
    committee-chair fallback is attached if the LLM chair still fails.

    ``clock`` / ``sleeper`` / ``rng`` are injectable so tests can drive the budget
    and backoff deterministically (a fake clock advanced by a fake sleeper).
    """
    cfg = cfg or default_settings
    log = logger or _logger
    rng = rng if rng is not None else random.Random()
    run_started = clock()
    # Phase 32A TPM slice: per-run usage accounting + the process-shared
    # provider pacer (None when ``llm_council_tpm_capacity`` is 0 — the
    # default — so a plain deploy is byte-identical). Tests inject their own
    # pacer built on the fake clock/sleeper.
    tracker = CouncilUsageTracker()
    if pacer is None:
        pacer = get_shared_pacer(
            client.provider_name,
            client.deployment_name,
            cfg.llm_council_tpm_capacity,
        )
    evidence_ids = evidence_pack.evidence_ids()
    evidence_json = evidence_pack.model_dump_json()
    # Phase 32A hotfix: id -> EvidenceItem, so the citation checker's semantic-
    # grounding check can look up each cited item's scope/period/excerpt.
    evidence_by_id = {item.id: item for item in evidence_pack.evidence_items}
    # Corrective (post-#99/#100): the run's own structured gap state, so the
    # citation checker's gap-attribution grounding check can tell a genuine
    # cause from an invented one.
    known_gaps = evidence_pack.known_gaps

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

    if cfg.llm_council_retry_enabled:
        await _run_council_with_retries(
            evidence_pack=evidence_pack,
            evidence_json=evidence_json,
            evidence_ids=evidence_ids,
            result=result,
            client=client,
            cfg=cfg,
            log=log,
            report_id=report_id,
            ticker=ticker,
            clock=clock,
            sleeper=sleeper,
            rng=rng,
            evidence_by_id=evidence_by_id,
            known_gaps=known_gaps,
            pacer=pacer,
            tracker=tracker,
        )
    else:
        await _run_offline_pass(
            evidence_json=evidence_json,
            evidence_ids=evidence_ids,
            result=result,
            client=client,
            cfg=cfg,
            log=log,
            report_id=report_id,
            ticker=ticker,
            evidence_by_id=evidence_by_id,
            known_gaps=known_gaps,
            pacer=pacer,
            tracker=tracker,
        )

    result.recount()
    chair = next(
        (a for a in result.agents if a.agent_name == AGENT_COMMITTEE_CHAIR), None
    )
    result.committee_label = chair.committee_label if chair else None

    # Phase 32A Slice 4: when the retry bundle is on and the LLM committee chair
    # still did not complete, attach a DETERMINISTIC, non-consensus committee
    # summary so the report/memo has an honest synthesis to render — without
    # inventing a recommendation, valuation conclusion, or price objective. The
    # failed LLM chair entry is KEPT in ``agents`` (so the counts + warnings show
    # the council is visibly partial); the fallback is attached separately and is
    # excluded from the is_mock / recount tallies. It never flips
    # research_complete / publication_ready / human_review_required.
    if cfg.llm_council_retry_enabled and (
        chair is None or chair.status != STATUS_COMPLETED
    ):
        fallback = _deterministic_chair_fallback(result.agents, COUNCIL_AGENT_ORDER)
        # Defense-in-depth: run the fallback through the same safety/citation gate.
        sanitized_fallback, _fb_issues = check_and_sanitize(
            fallback, evidence_ids, evidence_by_id, known_gaps
        )
        result.deterministic_chair = sanitized_fallback
        result.chair_fallback_used = True
        result.committee_label = sanitized_fallback.committee_label
        log_event(
            log,
            "llm_committee_chair_fallback",
            level=logging.WARNING,
            report_id=report_id,
            ticker=ticker,
            provider=client.provider_name,
            council_version=cfg.llm_council_version,
            committee_label=sanitized_fallback.committee_label,
            agents_completed=result.agents_completed,
            agents_failed=result.agents_failed,
        )

    # Phase 32A TPM slice — failure-vs-judgement semantics + run accounting.
    # ``committee_label`` alone can no longer masquerade: every result records
    # WHO produced the synthesis, how many attempts the chair made, and (on
    # failure) WHICH provider error class ended it.
    if chair is not None and chair.status == STATUS_COMPLETED:
        result.chair_synthesis_basis = "llm_chair"
    elif result.chair_fallback_used:
        result.chair_synthesis_basis = "deterministic_fallback"
    result.chair_attempts = tracker.attempts_for(AGENT_COMMITTEE_CHAIR)
    if chair is None or chair.status != STATUS_COMPLETED:
        result.chair_error_type = _chair_failure_reason(
            result.chair_attempts, tracker.last_error_for(AGENT_COMMITTEE_CHAIR)
        )
    result.token_usage = tracker.usage_metadata()

    log_event(
        log,
        "llm_council_run_summary",
        report_id=report_id,
        ticker=ticker,
        provider=client.provider_name,
        council_version=cfg.llm_council_version,
        elapsed_ms=int((clock() - run_started) * 1000),
        agents_completed=result.agents_completed,
        agents_failed=result.agents_failed,
        chair_attempts=result.chair_attempts,
        chair_fallback_used=result.chair_fallback_used or None,
        committee_label=result.committee_label,
        committee_label_basis=result.chair_synthesis_basis,
        chair_error_type=result.chair_error_type,
        **tracker.summary_fields(),
    )

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
                document_content_hash=item.document_content_hash,
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
    reuse_lookup: "dict[str, ReusedDocument] | None" = None,
    logger: logging.Logger | None = None,
) -> CouncilResult:
    """Resolve a client, build the evidence pack, and run the council.

    Returns ``CouncilResult.disabled()`` (llm_used=False) when the council flag
    is off or no provider resolves — the signal to keep the deterministic path.
    Never raises: an unexpected failure degrades to the disabled result.

    ``reuse_lookup`` (Phase 32A Slice 5, 3c-iii) is an OPTIONAL in-memory lookup
    (NOT a DB session) built by the caller from persisted extracted documents; when
    the deep ingestion path runs, a candidate document already present is reused
    instead of re-fetched. None / empty ⇒ every candidate is fetched (byte-identical).
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
        historical_facts: list[dict[str, Any]] = []
        # Phase 31 hotfix: bounded, secret-free PRIMARY-source references (verified
        # metadata-only items) + their counts + honest source-gap strings. Stay
        # empty (and unattached) when the connector layer is off → byte-identical.
        primary_source_references: list[dict[str, Any]] = []
        source_reference_counts: dict[str, int] = {}
        source_gap_messages_bounded: list[str] = []
        # Phase 32A Slice 5 (3c-i): deep primary-document artifacts threaded OUT for
        # the report-write path to persist (ExtractedDocument / ExtractedFact). Only
        # captured when BOTH the ingestion + citation persistence flags are on so the
        # dark path (either flag off) stays byte-identical and holds no data.
        primary_document_artifacts: list[Any] = []
        if cfg.source_connector_enabled:
            try:
                # Phase 29B.2: when document extraction is also enabled, inject the
                # bounded live IR-page fetcher + document extractor so the council
                # reasons from real annual-report excerpts + parsed facts — not only
                # metadata. Off by default (both flags), preserving 29B.1 behaviour.
                #
                # Phase 32A Slice 5: when the MASTER flag
                # ``primary_document_ingestion_enabled`` is on, inject the DEEP
                # extractor instead (pdfplumber tables + stricter fact validation +
                # aggregate ingestion budget). This runs BEFORE the council deadline,
                # so ingestion + the ~150s council stays under the ~230s gateway. With
                # the master flag OFF the ``elif`` below is byte-identical to Slice 4.
                extract_kwargs: dict[str, Any] = {}
                if cfg.primary_document_ingestion_enabled:
                    from app.services.sources.live_fetchers import (
                        live_ir_page_fetcher,
                        live_primary_document_extractor,
                        live_sec_primary_document_extractor,
                    )

                    extract_kwargs = {
                        "ir_page_fetcher": live_ir_page_fetcher,
                        "primary_document_extractor": live_primary_document_extractor,
                        # Phase 32A Slice 5B.1: official SEC filing-BODY ingestion
                        # for US issuers. SUPPLEMENTS — never replaces — the
                        # SEC/XBRL structured facts, which stay authoritative for
                        # every financial number. Self-gates on its own
                        # ``primary_document_sec_body_enabled`` flag and returns []
                        # with no network when either flag is off.
                        "sec_primary_document_extractor": (
                            live_sec_primary_document_extractor
                        ),
                        # Phase 32A Slice 5 (3c-iii): reuse persisted extractions so a
                        # report regeneration skips the re-fetch/re-extract. Empty /
                        # None ⇒ every candidate is fetched (byte-identical).
                        "primary_document_reuse": reuse_lookup,
                    }
                    # Phase 32A Slice 5B.2: real-OCR fallback for a scanned issuer-IR
                    # document, gated on its OWN sub-flag (never implied by the master
                    # ingestion flag). ``get_ocr_provider`` returns NoOp unless BOTH
                    # the flag is on AND an Azure endpoint is configured, so this stays
                    # inert/byte-identical until a human provisions the resource.
                    if cfg.primary_document_ocr_enabled:
                        from app.services.sources.ocr_provider import get_ocr_provider

                        extract_kwargs["ocr_provider"] = get_ocr_provider(cfg)
                elif cfg.source_document_extraction_enabled:
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
                # Prefer the COMPLETE artifact fact sets; fall back to the
                # (capped) evidence items when no artifact is available, e.g.
                # the shallow/metadata-only path.
                historical_facts = _historical_facts_from_artifacts(
                    collected.primary_document_artifacts
                ) or _historical_facts(collected.evidence_items)
                # Live-acceptance corrective (2026-08-26). ``primary_facts``
                # decides which fact fills a CANONICAL slot, and it was read
                # from the per-document-CAPPED evidence items — so WHICH period
                # occupied the canonical slot depended on evidence-pack
                # ordering rather than on the period itself. Observed on a live
                # Kering report: the canonical revenue slot held a rounded
                # "€17.2 billion" FY2024 prose figure while a HIGH-confidence
                # FY2025 figure of €14,675m existed and simply had not survived
                # the cap.
                #
                # The high-confidence subset of the COMPLETE set is strictly
                # better: same confidence bar, nothing arbitrary about which
                # facts are visible. The cap still applies where it belongs —
                # to the evidence pack the council reads.
                complete_high = [
                    fact
                    for fact in historical_facts
                    if fact.get("confidence") == "high"
                ]
                primary_facts = complete_high or _primary_facts(
                    collected.evidence_items
                )
                # Phase 32A Slice 5 (3c-i): capture the deep artifacts for persistence
                # ONLY when both the ingestion + citation persistence flags are on.
                # Either flag off ⇒ list stays empty ⇒ nothing to persist downstream.
                if (
                    cfg.primary_document_ingestion_enabled
                    and cfg.report_citation_persistence_enabled
                ):
                    primary_document_artifacts = list(
                        collected.primary_document_artifacts
                    )
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
            # The COMPLETE (uncapped) fact set the multi-period series are built
            # from — see ``_historical_facts_from_artifacts``. The pack still
            # renders each series as ONE dense line, so this widens what the
            # series can SEE without widening the prompt.
            historical_facts=historical_facts,
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
        # Private-use readiness PR-B: the wider (high + medium) fact set the
        # multi-period series are built from. Attached only when non-empty →
        # dark-by-default byte-identical.
        if historical_facts:
            result.historical_facts = historical_facts
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
        # Phase 32A Slice 5 (3c-i): hand the deep primary-document artifacts to the
        # report-write path (runtime-only, excluded from serialization). Non-empty
        # ONLY when both gate flags are on ⇒ dark-by-default byte-identical.
        if primary_document_artifacts:
            result.primary_document_artifacts = primary_document_artifacts
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
