"""
Deep Field Review evidence pack builder — Phase 32A Slice 6D.

Turns ONE candidate's ALREADY-PERSISTED artefacts (its ``DiscoveryCandidate``
row, the ``Report`` its full analysis produced, and that report's
``PrimaryDocumentSummary``) into a bounded ``FieldReviewCompanySummary``, and
assembles the per-run ``FieldReviewPack`` the comparative council reads.

Hard rules this module exists to enforce:

  * NOTHING is computed, fetched, re-analysed, or inferred. Every field
    re-presents a value that is already persisted. A field with no persisted
    source stays ``None`` / empty and renders as not-available — it is never
    guessed, never defaulted to a plausible number, and never carried over from
    another company.
  * The report body is parsed with the SAME helper the existing from-report
    regeneration flow uses (``final_report_generator._extract_from_report_content``)
    rather than a second, drifting markdown/JSON parser.
  * Every list-valued sub-field is CAPPED. This pack feeds one LLM prompt for
    potentially a dozen companies at once and must stay bounded.
  * No valuation number, price objective, or return figure is ever carried
    across: only the qualitative ``valuation_readiness`` LABEL.
"""

from __future__ import annotations

from typing import Any

from app.models.discovery import DiscoveryCandidate, DiscoveryRun
from app.models.report import Report
from app.schemas.primary_document import PrimaryDocumentSummary
from app.services.final_report_generator import _extract_from_report_content
from app.services.llm.field_review_schemas import (
    FieldCompanyCouncilVerdict,
    FieldCouncilCompletion,
    FieldDiscoveryRelevance,
    FieldDocumentCoverage,
    FieldEvidenceQuality,
    FieldNamedValue,
    FieldReportingPeriods,
    FieldReviewCompanySummary,
    FieldReviewPack,
    FieldRunContext,
    FieldRunFact,
)

__all__ = [
    "MAX_LIST_ITEMS",
    "MAX_TEXT_CHARS",
    "build_company_summary",
    "build_field_review_pack",
]

# Per-list cap inside ONE company summary (key points, risks, gaps, facts…).
MAX_LIST_ITEMS = 5
# Per-string cap for any free-text item carried into the pack.
MAX_TEXT_CHARS = 400
# Cap on the number of run-level facts.
MAX_RUN_FACTS = 20

# Company-council agent names whose STORED summaries the field review re-reads.
# None of these agents is ever re-run — this is a read of persisted output.
_AGENT_FINANCIAL_ANALYST = "financial_analyst"
_AGENT_SOURCE_QUALITY_CRITIC = "source_quality_critic"
_AGENT_RED_TEAM = "red_team"
_AGENT_COMMITTEE_CHAIR = "committee_chair"

# Report-content keys that carry a financial datapoint dict but are NOT a fact
# (they are notes / provenance markers).
_NON_FACT_SNAPSHOT_KEYS = frozenset(
    {"type", "note", "fundamentals_note", "human_review_required"}
)


# ---------------------------------------------------------------------------
# Small, defensive readers (a persisted report is JSONB — never trust its shape)
# ---------------------------------------------------------------------------


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _as_list(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def _clip(text: Any) -> str | None:
    """Render a value as a bounded string, or None when there is nothing to say."""
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    return s[:MAX_TEXT_CHARS]


def _clip_list(values: Any, *, limit: int = MAX_LIST_ITEMS) -> list[str]:
    """Bounded, de-duplicated list of bounded strings. Never fabricates entries.

    A scalar (a persisted section often stores a single note rather than a list)
    is treated as a one-item list; ``None`` yields an empty list.
    """
    if values is None:
        items: list[Any] = []
    elif isinstance(values, list):
        items = values
    else:
        items = [values]
    out: list[str] = []
    seen: set[str] = set()
    for raw in items:
        if isinstance(raw, dict):
            # Tolerate `{"value": ...}` / `{"item": ...}` / `{"claim": ...}` shapes.
            raw = raw.get("value") or raw.get("item") or raw.get("claim")
        s = _clip(raw)
        if s and s not in seen:
            seen.add(s)
            out.append(s)
        if len(out) >= limit:
            break
    return out


def _dp_value(section: dict[str, Any], key: str) -> Any:
    """Read a `{"value": …}` datapoint's value out of a report section."""
    entry = section.get(key)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _dp_list(section: dict[str, Any], key: str, *, limit: int = MAX_LIST_ITEMS) -> list[str]:
    return _clip_list(_dp_value(section, key), limit=limit)


# ---------------------------------------------------------------------------
# Section extractors
# ---------------------------------------------------------------------------


def _financial_facts(
    snapshot: dict[str, Any],
) -> tuple[list[FieldNamedValue], list[str]]:
    """Re-present the report's already-persisted financial datapoints.

    Returns ``(facts, missing_field_names)``. A datapoint whose stored ``value``
    is None is NOT turned into a fact — it is recorded honestly as a missing
    field name so the council sees the gap instead of a fabricated number.
    """
    facts: list[FieldNamedValue] = []
    missing: list[str] = []
    for key, entry in snapshot.items():
        if key in _NON_FACT_SNAPSHOT_KEYS or not isinstance(entry, dict):
            continue
        if "value" not in entry:
            continue
        value = entry.get("value")
        if value is None:
            missing.append(key)
            continue
        if len(facts) >= MAX_LIST_ITEMS:
            continue
        facts.append(
            FieldNamedValue(
                field=key,
                value=_clip(value),
                unit=_clip(entry.get("unit") or entry.get("currency")),
                as_of=_clip(entry.get("as_of") or entry.get("period")),
                source=_clip(entry.get("source")),
                source_tier=_clip(entry.get("source_tier")),
                provenance=_clip(entry.get("provenance")),
            )
        )
    return facts, sorted(missing)[:MAX_LIST_ITEMS]


def _reporting_periods(snapshot: dict[str, Any]) -> FieldReportingPeriods:
    """The four reporting states, read off THIS report's own snapshot.

    Never recomputed and never borrowed: a state the linked report does not
    show stays ``None``, so the freshness comparison is over each company's
    exact linked report state rather than an inference across companies.
    """
    block = snapshot.get("reporting_periods")
    if not isinstance(block, dict):
        return FieldReportingPeriods()
    return FieldReportingPeriods(
        latest_annual=_clip(block.get("latest_annual")),
        latest_interim=_clip(block.get("latest_interim")),
        latest_quarter=_clip(block.get("latest_quarter")),
        latest_current_period=_clip(block.get("latest_current_period")),
    )


# Private-use readiness PR-C — the identity fields a comparative review is
# routinely asked about. Kept small and explicit: each must be answerable
# strictly from ONE company's own persisted report section.
IDENTITY_COMPLETENESS_FIELDS: tuple[str, ...] = (
    "legal_name",
    "ticker",
    "exchange",
    "country_domicile",
    "isin",
    "lei",
    "sector",
    "reporting_currency",
)


def _identity_completeness(identity: dict[str, Any]) -> tuple[list[str], list[str]]:
    """(present, missing) identity field names for ONE company's OWN report.

    Reads only the datapoints of the report section handed in, so a field can
    never be marked missing because a DIFFERENT company lacked it. A datapoint
    the section does not carry at all counts as missing (honest: the report did
    not answer it) rather than being omitted from both lists, which would leave
    the council unable to distinguish "absent" from "not asked".
    """
    present: list[str] = []
    missing: list[str] = []
    for name in IDENTITY_COMPLETENESS_FIELDS:
        value = _dp_value(identity, name)
        if value is None or (isinstance(value, str) and not value.strip()):
            missing.append(name)
        else:
            present.append(name)
    return present, missing


def _evidence_quality(
    review: dict[str, Any], source_summary: dict[str, Any]
) -> FieldEvidenceQuality:
    distribution_raw = _dp_value(review, "source_type_distribution")
    distribution: dict[str, int] = {}
    for name, count in _as_dict(distribution_raw).items():
        if isinstance(count, int):
            distribution[str(name)[:60]] = count
    total_sources = review.get("total_sources")
    return FieldEvidenceQuality(
        total_sources=(
            total_sources
            if isinstance(total_sources, int)
            else int(source_summary.get("total_sources") or 0)
        ),
        total_citations=int(source_summary.get("total_citations") or 0),
        overall_source_quality=_clip(_dp_value(review, "overall_source_quality")),
        strong_sources_count=(
            review.get("strong_sources_count")
            if isinstance(review.get("strong_sources_count"), int)
            else None
        ),
        weak_sources_count=(
            review.get("weak_sources_count")
            if isinstance(review.get("weak_sources_count"), int)
            else None
        ),
        source_type_distribution=dict(list(distribution.items())[:MAX_LIST_ITEMS]),
        source_tiers=_clip_list(source_summary.get("source_types")),
    )


def _council_agent_map(source_summary: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """agent_name -> stored agent output, from ``source_summary_json.llm_council``."""
    council = _as_dict(source_summary.get("llm_council"))
    agents: dict[str, dict[str, Any]] = {}
    for raw in _as_list(council.get("agents")):
        if isinstance(raw, dict) and isinstance(raw.get("agent_name"), str):
            agents[raw["agent_name"]] = raw
    return agents


def _council_completion(source_summary: dict[str, Any]) -> FieldCouncilCompletion:
    council = _as_dict(source_summary.get("llm_council"))
    return FieldCouncilCompletion(
        llm_used=bool(council.get("llm_used")),
        agents_completed=int(council.get("agents_completed") or 0),
        agents_failed=int(council.get("agents_failed") or 0),
        agents_skipped=int(council.get("agents_skipped") or 0),
        chair_fallback_used=bool(council.get("chair_fallback_used")),
    )


def _chair_verdict(
    chair_section: dict[str, Any],
    council_agents: dict[str, dict[str, Any]],
    source_summary: dict[str, Any],
) -> FieldCompanyCouncilVerdict:
    """The company council chair's STORED verdict. Never re-run, never re-decided."""
    council = _as_dict(source_summary.get("llm_council"))
    chair_agent = _as_dict(council_agents.get(_AGENT_COMMITTEE_CHAIR))
    return FieldCompanyCouncilVerdict(
        committee_label=_clip(
            chair_agent.get("committee_label") or council.get("committee_label")
        ),
        provisional_internal_status=_clip(
            _dp_value(chair_section, "provisional_internal_status")
        ),
        quality_gate_status=_clip(_dp_value(chair_section, "quality_gate_status")),
        summary=_clip(
            chair_agent.get("summary") or _dp_value(chair_section, "committee_summary")
        ),
        primary_open_questions=_dp_list(chair_section, "primary_open_questions"),
    )


def _agent_summary(
    council_agents: dict[str, dict[str, Any]], agent_name: str
) -> str | None:
    """A single company-council agent's STORED summary, bounded. Read-only."""
    agent = _as_dict(council_agents.get(agent_name))
    if agent.get("status") == "failed":
        # Honest: the agent did not produce a usable summary for this company.
        return None
    return _clip(agent.get("summary"))


def _catalyst_notes(catalyst_section: dict[str, Any]) -> list[str]:
    """Bounded catalyst headlines/categories already persisted on the report.

    Catalyst labels are model-derived (T6) on the source report and are NOT
    recommendations; they are carried across verbatim, never re-derived.
    """
    notes: list[str] = []
    for raw in _as_list(catalyst_section.get("events")):
        if not isinstance(raw, dict):
            continue
        parts = [
            raw.get("event_date"),
            raw.get("catalyst_category"),
            raw.get("catalyst_direction"),
            raw.get("headline"),
        ]
        line = _clip(" | ".join(str(p) for p in parts if p))
        if line:
            notes.append(line)
        if len(notes) >= MAX_LIST_ITEMS:
            break
    return notes


def _unresolved_gaps(
    completeness: dict[str, Any], missing: dict[str, Any]
) -> list[str]:
    """Gaps the source report itself already recorded. Never newly invented."""
    gaps = _dp_list(completeness, "incomplete_sections")
    for raw in _as_list(missing.get("missing_items")):
        if len(gaps) >= MAX_LIST_ITEMS:
            break
        if isinstance(raw, dict):
            item = _clip(raw.get("item") or raw.get("field") or raw.get("reason"))
        else:
            item = _clip(raw)
        if item and item not in gaps:
            gaps.append(item)
    return gaps[:MAX_LIST_ITEMS]


def _report_provenance(source_summary: dict[str, Any]) -> str:
    """The report's own tri-state provenance label — never inferred from data.

    Prefers the explicit Phase 32A ``data_provenance`` field. Falls back to the
    legacy explicit ``is_mock`` boolean. An absent signal is ``unknown``, which
    is NEVER coerced to "real" (it would overstate) or "mock" (it would
    understate).
    """
    provenance = source_summary.get("data_provenance")
    if isinstance(provenance, str) and provenance in {
        "real",
        "mock",
        "mixed",
        "unknown",
    }:
        return provenance
    is_mock = source_summary.get("is_mock")
    if is_mock is True:
        return "mock"
    if is_mock is False:
        return "real"
    return "unknown"


# ---------------------------------------------------------------------------
# Public builders
# ---------------------------------------------------------------------------


def build_company_summary(
    *,
    citation_ref: str,
    candidate: DiscoveryCandidate,
    report: Report,
    document_summary: PrimaryDocumentSummary | None = None,
) -> FieldReviewCompanySummary:
    """Build ONE bounded company summary from already-persisted data only."""
    content = _extract_from_report_content(report)
    source_summary = _as_dict(report.source_summary_json)

    identity = _as_dict(content.get("company_identity"))
    snapshot = _as_dict(content.get("financial_snapshot"))
    bull = _as_dict(content.get("bull_case"))
    risk = _as_dict(content.get("risk_analysis"))
    source_review = _as_dict(content.get("source_quality_review"))
    completeness = _as_dict(content.get("research_completeness_review"))
    missing = _as_dict(content.get("missing_information"))
    valuation = _as_dict(content.get("valuation_readiness"))
    chair_section = _as_dict(content.get("committee_chair_summary"))
    catalysts = _as_dict(content.get("news_catalyst_discovery"))

    council_agents = _council_agent_map(source_summary)
    facts, missing_fields = _financial_facts(snapshot)
    reporting_periods = _reporting_periods(snapshot)
    identity_present, identity_missing = _identity_completeness(identity)
    provenance = _report_provenance(source_summary)

    caveats: list[str] = []
    if provenance != "real":
        caveats.append(f"data_provenance={provenance}")
    completion = _council_completion(source_summary)
    if completion.agents_failed:
        caveats.append(
            f"company council partial: {completion.agents_failed} agent(s) failed"
        )
    if completion.chair_fallback_used:
        caveats.append("company council chair used the deterministic fallback")
    if not facts:
        caveats.append("no sourced financial datapoint persisted on this report")

    # No persisted document view ⇒ an honest all-zero coverage block (never a
    # borrowed or assumed count).
    docs = (
        FieldDocumentCoverage(
            attempted_count=document_summary.attempted_count,
            extracted_count=document_summary.extracted_count,
            metadata_only_count=document_summary.metadata_only_count,
            failed_count=document_summary.failed_count,
            native_count=document_summary.native_count,
            ocr_count=document_summary.ocr_count,
            validated_fact_count=document_summary.validated_fact_count,
            reused_count=document_summary.reused_count,
        )
        if document_summary is not None
        else FieldDocumentCoverage()
    )

    return FieldReviewCompanySummary(
        id=citation_ref,
        discovery_candidate_id=str(candidate.id) if candidate.id else None,
        report_id=str(report.id) if report.id else None,
        # Identity: prefer the candidate row (the run's own identity resolution),
        # falling back to the report's persisted identity section.
        ticker=candidate.ticker or _clip(_dp_value(identity, "ticker")),
        exchange=candidate.exchange or _clip(_dp_value(identity, "exchange")),
        company_name=(
            candidate.company_name
            or candidate.legal_name
            or _clip(_dp_value(identity, "legal_name"))
        ),
        country=candidate.country or _clip(_dp_value(identity, "country_domicile")),
        sector=candidate.sector or _clip(_dp_value(identity, "sector")),
        industry=candidate.industry,
        identity_fields_present=identity_present,
        identity_fields_missing=identity_missing,
        discovery=FieldDiscoveryRelevance(
            rank=candidate.rank,
            candidate_score=candidate.candidate_score,
            candidate_score_grade=candidate.candidate_score_grade,
            thesis_relevance_score=candidate.thesis_relevance_score,
            combined_internal_score=candidate.combined_internal_score,
            labels=_clip_list(candidate.labels_json),
            source_quality=candidate.source_quality,
            catalyst_coverage_status=candidate.catalyst_coverage_status,
        ),
        reporting_periods=reporting_periods,
        financial_facts=facts,
        missing_financial_fields=missing_fields,
        primary_documents=docs,
        evidence_quality=_evidence_quality(source_review, source_summary),
        financial_strength_notes=_clip_list(
            [
                _agent_summary(council_agents, _AGENT_FINANCIAL_ANALYST),
                *(_dp_list(snapshot, "fundamentals_note", limit=1)),
            ]
        ),
        business_moat_notes=_dp_list(bull, "positive_thesis_points"),
        catalyst_notes=_catalyst_notes(catalysts),
        catalyst_coverage_status=_clip(catalysts.get("coverage_status")),
        risk_notes=_clip_list(
            [
                *_as_list(_dp_value(risk, "business_risks")),
                *_as_list(_dp_value(risk, "financial_risks")),
                *_as_list(_dp_value(risk, "data_quality_risks")),
            ]
        ),
        # The qualitative readiness LABEL only. No number, no valuation, no
        # price objective ever crosses into the field review.
        valuation_readiness=_clip(_dp_value(valuation, "readiness")),
        company_council_verdict=_chair_verdict(
            chair_section, council_agents, source_summary
        ),
        financial_analyst_summary=_agent_summary(
            council_agents, _AGENT_FINANCIAL_ANALYST
        ),
        source_critic_summary=_agent_summary(
            council_agents, _AGENT_SOURCE_QUALITY_CRITIC
        ),
        red_team_summary=_agent_summary(council_agents, _AGENT_RED_TEAM),
        unresolved_gaps=_unresolved_gaps(completeness, missing),
        # Counts are reported ONLY when the report actually carried the section
        # (an absent section stays None — a real 0 is never turned into "unknown"
        # and an unknown is never turned into 0).
        research_completeness_sections_complete=(
            len(_as_list(_dp_value(completeness, "complete_sections")))
            if "complete_sections" in completeness
            else None
        ),
        research_completeness_sections_incomplete=(
            len(_as_list(_dp_value(completeness, "incomplete_sections")))
            if "incomplete_sections" in completeness
            else None
        ),
        research_completeness_blocking_gaps=(
            completeness.get("blocking_gaps_count")
            if isinstance(completeness.get("blocking_gaps_count"), int)
            else None
        ),
        council_completion=completion,
        data_provenance=provenance,
        caveats=caveats[:MAX_LIST_ITEMS],
    )


def _run_facts(
    run: DiscoveryRun, missing: list[dict[str, Any]]
) -> list[FieldRunFact]:
    """Bounded, citeable run-level facts (R#), including the honest exclusions."""
    facts: list[FieldRunFact] = [
        FieldRunFact(
            id="R1",
            label="run_shape",
            detail=(
                f"Discovery run mode={run.mode}, status={run.status}, "
                f"candidates={run.candidate_count or 0}. Every company below "
                "comes from THIS run only."
            ),
        )
    ]
    if run.thesis_text:
        facts.append(
            FieldRunFact(
                id=f"R{len(facts) + 1}",
                label="run_thesis",
                detail=_clip(run.thesis_text),
            )
        )
    for entry in missing:
        if len(facts) >= MAX_RUN_FACTS:
            break
        ticker = entry.get("ticker") or "unknown"
        reason = entry.get("exclusion_reason") or "unknown"
        facts.append(
            FieldRunFact(
                id=f"R{len(facts) + 1}",
                label="excluded_candidate",
                detail=(
                    f"{ticker}: not comparable in this field review "
                    f"(reason={reason}). No analysis content for it was "
                    "assumed, substituted, or fabricated."
                ),
            )
        )
    return facts


def _identity_gap_spread(
    companies: list[FieldReviewCompanySummary],
) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """(fields missing for EVERY company, fields missing for SOME) — exact.

    Computed from each company's own ``identity_fields_missing``, so it can
    never disagree with the per-company lists it summarises. A field nobody is
    missing appears in neither list.
    """
    if not companies:
        return [], []
    per_company = {
        (c.ticker or c.id): set(c.identity_fields_missing or []) for c in companies
    }
    every_field: set[str] = set()
    for gaps in per_company.values():
        every_field |= gaps

    all_missing: list[str] = []
    some_missing: list[tuple[str, list[str]]] = []
    for field in sorted(every_field):
        lacking = sorted(
            name for name, gaps in per_company.items() if field in gaps
        )
        if len(lacking) == len(per_company):
            all_missing.append(field)
        elif lacking:
            some_missing.append((field, lacking))
    return all_missing, some_missing


def build_field_review_pack(
    *,
    run: DiscoveryRun,
    companies: list[FieldReviewCompanySummary],
    missing: list[dict[str, Any]],
    analyzed_candidate_count: int,
) -> FieldReviewPack:
    """Assemble the bounded pack the comparative council reads."""
    parsed = _as_dict(run.parsed_thesis_json)
    config = _as_dict(run.config_json)

    known_gaps: list[str] = []
    # Private-use readiness (live DFR corrective, 2026-08-26) — state, ONCE and
    # deterministically, which identity fields are missing for EVERY company
    # and which for only some.
    #
    # PR-C gave each company its own present/missing lists, and that removed the
    # false claims about individual companies. What it did not remove was the
    # temptation to generalise across them: a live review correctly reported
    # ISIN and sector as missing for all five companies, then wrote a research
    # task saying to source "(ISIN, LEI)" for all of them — while three of the
    # five already had a sourced LEI. A model asked to summarise five lists will
    # merge them; the fix is to hand it the merged answer as a FACT rather than
    # leave it to infer one.
    all_missing, some_missing = _identity_gap_spread(companies)
    if all_missing:
        known_gaps.append(
            "Identity fields missing for EVERY company in this review: "
            + ", ".join(all_missing)
            + "."
        )
    if some_missing:
        known_gaps.append(
            "Identity fields missing for SOME companies only — never say these "
            "are missing for all: "
            + "; ".join(
                f"{field} (missing for {', '.join(tickers)})"
                for field, tickers in some_missing
            )
            + "."
        )
    if missing:
        known_gaps.append(
            f"{len(missing)} candidate(s) in this run could not be compared; "
            "their exclusion reasons are listed as run facts (R#)."
        )
    provenance_caveated = [c.id for c in companies if c.data_provenance != "real"]
    if provenance_caveated:
        known_gaps.append(
            "Data provenance is not 'real' for: "
            + ", ".join(provenance_caveated)
            + ". Treat their figures as non-authoritative."
        )
    no_documents = [
        c.id for c in companies if c.primary_documents.extracted_count == 0
    ]
    if no_documents:
        known_gaps.append(
            "No primary document was successfully extracted for: "
            + ", ".join(no_documents)
            + "."
        )

    return FieldReviewPack(
        run=FieldRunContext(
            discovery_run_id=str(run.id),
            mode=run.mode,
            status=run.status,
            thesis_text=_clip(run.thesis_text),
            parsed_theme=_clip(parsed.get("theme")),
            region=_clip(config.get("region") or parsed.get("region")),
            country=_clip(config.get("country") or parsed.get("country")),
            sector=_clip(config.get("sector") or parsed.get("sector")),
            candidate_count=run.candidate_count or 0,
            analyzed_candidate_count=analyzed_candidate_count,
            included_company_count=len(companies),
            missing_candidate_count=len(missing),
        ),
        run_facts=_run_facts(run, missing),
        companies=companies,
        known_gaps=known_gaps,
        do_not_infer=[
            "Do not infer a price target, fair value, intrinsic value, upside, "
            "downside, or expected return for any company.",
            "Do not infer a rating or trading action for any company.",
            "Do not infer a financial figure that is absent from a company's "
            "summary — say it is missing instead.",
            "Do not compare a company against anything outside this pack.",
        ],
    )
