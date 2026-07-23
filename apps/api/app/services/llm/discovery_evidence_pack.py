"""
Run-level Evidence Pack Builder — Phase 28B.

Turns the deterministic material for ONE discovery run — the run metadata plus
its ranked candidate set — into a bounded, cited evidence pack. The discovery
council may read nothing else. Design constraints (mirroring 28A):

  - Bounded: at most ``max_candidates`` candidates (config-capped); long
    ``raw_signal_json`` blobs are summarised, never passed whole.
  - Stable, unique ids: run-level facts ``R1, R2, …``; candidates ``C1, C2, …``.
  - No raw report bodies, no secrets, no prompts.

Inputs are plain dicts (no DB/ORM coupling), so the builder is trivially
unit-testable; the service adapts ORM rows into these dicts before calling it.
"""

from __future__ import annotations

from typing import Any

from app.services.llm.discovery_schemas import (
    DISCOVERY_EVIDENCE_PACK_VERSION,
    CandidateEvidence,
    DiscoveryEvidencePack,
    RunContext,
    RunFact,
)

_TEXT_MAX = 240
_MAX_WARNINGS_PER_CANDIDATE = 4
_MAX_KNOWN_GAPS = 20

_DO_NOT_INFER = [
    "Do not treat missing fundamentals as zero.",
    "Do not make investment recommendations.",
    "Do not rank by price target, fair value, or upside/downside.",
    "Do not fabricate analyst coverage or English-news volume.",
    "Internal scores are prioritization signals only, not a valuation.",
    "Only cite run facts (R#) and candidates (C#) that appear in this pack.",
]


def _clip(text: Any, limit: int = _TEXT_MAX) -> str | None:
    if text is None:
        return None
    s = str(text).strip()
    if not s:
        return None
    return s if len(s) <= limit else s[: limit - 1].rstrip() + "…"


def _first(*vals: Any) -> Any:
    for v in vals:
        if v not in (None, "", []):
            return v
    return None


def _compact(d: dict[str, Any]) -> dict[str, Any]:
    """Drop None values so the pack stays small and unambiguous."""
    return {k: v for k, v in d.items() if v is not None}


def _parsed_theme(parsed: dict[str, Any] | None) -> str | None:
    if not isinstance(parsed, dict):
        return None
    theme = parsed.get("theme")
    if theme:
        return str(theme)
    themes = parsed.get("themes")
    if isinstance(themes, list) and themes:
        return str(themes[0])
    return None


def _run_context(run: dict[str, Any]) -> RunContext:
    parsed = run.get("parsed_thesis") if isinstance(run.get("parsed_thesis"), dict) else {}
    config = run.get("config") if isinstance(run.get("config"), dict) else {}
    parsed = parsed or {}
    config = config or {}
    warnings = run.get("warnings") or []
    return RunContext(
        run_id=_clip(run.get("run_id"), 80),
        mode=run.get("mode"),
        status=run.get("status"),
        thesis_text=_clip(run.get("thesis_text"), _TEXT_MAX),
        parsed_theme=_parsed_theme(parsed),
        region=_first(config.get("region"), parsed.get("region")),
        country=_first(config.get("country"), parsed.get("country")),
        sector=_first(config.get("sector"), parsed.get("sector")),
        industry=_first(config.get("industry"), parsed.get("industry")),
        provider=run.get("provider"),
        lookback_days=run.get("lookback_days"),
        universe_count=int(run.get("universe_count") or 0),
        candidate_count=int(run.get("candidate_count") or 0),
        error_count=int(run.get("error_count") or 0),
        warning_count=len(warnings) if isinstance(warnings, list) else 0,
    )


def _run_facts(run: dict[str, Any], ctx: RunContext) -> list[RunFact]:
    """Build the cited run-level facts (R1, R2, …). Only add facts with content."""
    facts: list[RunFact] = []
    n = 0

    def add(label: str, detail: str | None) -> None:
        nonlocal n
        if detail is None:
            return
        n += 1
        facts.append(RunFact(id=f"R{n}", label=label, detail=detail))

    if ctx.mode == "thesis":
        thesis_bits = []
        if ctx.thesis_text:
            thesis_bits.append(f"thesis='{ctx.thesis_text}'")
        if ctx.parsed_theme:
            thesis_bits.append(f"theme={ctx.parsed_theme}")
        filt = _compact(
            {
                "region": ctx.region,
                "country": ctx.country,
                "sector": ctx.sector,
                "industry": ctx.industry,
            }
        )
        if filt:
            thesis_bits.append("filters=" + ", ".join(f"{k}:{v}" for k, v in filt.items()))
        add("thesis_and_filters", _clip("; ".join(thesis_bits), _TEXT_MAX) if thesis_bits else None)
    else:
        add(
            "ticker_run",
            _clip(f"manual/curated ticker run; universe_count={ctx.universe_count}"),
        )

    add(
        "universe_and_candidates",
        _clip(
            f"universe_count={ctx.universe_count}, candidate_count={ctx.candidate_count}, "
            f"error_count={ctx.error_count}"
        ),
    )
    add(
        "provider_and_lookback",
        _clip(f"provider={ctx.provider}, lookback_days={ctx.lookback_days}, status={ctx.status}"),
    )

    warnings = run.get("warnings") or []
    if isinstance(warnings, list) and warnings:
        add(
            "run_warnings",
            _clip("; ".join(str(w) for w in warnings[:6]), _TEXT_MAX),
        )
    return facts


def _score_breakdown(cand: dict[str, Any]) -> dict[str, Any]:
    return _compact(
        {
            "momentum_score": cand.get("momentum_score"),
            "catalyst_score": cand.get("catalyst_score"),
            "fundamentals_score": cand.get("fundamentals_score"),
            "source_quality_score": cand.get("source_quality_score"),
            "data_completeness_score": cand.get("data_completeness_score"),
            "risk_penalty_score": cand.get("risk_penalty_score"),
        }
    )


def _data_coverage(cand: dict[str, Any]) -> dict[str, Any]:
    dc = cand.get("data_coverage")
    dc = dc if isinstance(dc, dict) else {}
    return _compact(
        {
            "profile_source": dc.get("profile_source"),
            "fundamentals_source": dc.get("fundamentals_source"),
            "sec_eligible": dc.get("sec_eligible"),
            "reason": _clip(dc.get("reason"), 160),
            "requires_human_research": dc.get("requires_human_research"),
            "source_quality": cand.get("source_quality"),
            "missing_info_count": cand.get("missing_info_count"),
            "blocking_gap_count": cand.get("blocking_gap_count"),
        }
    )


def _catalyst_summary(cand: dict[str, Any]) -> dict[str, Any]:
    return _compact(
        {
            "coverage_status": cand.get("catalyst_coverage_status"),
            "momentum_label": cand.get("momentum_label"),
            "positive_catalyst_count": cand.get("positive_catalyst_count"),
            "high_strength_catalyst_count": cand.get("high_strength_catalyst_count"),
            "filing_event_count": cand.get("filing_event_count"),
            "news_event_count": cand.get("news_event_count"),
            "press_release_event_count": cand.get("press_release_event_count"),
        }
    )


def _candidate_evidence(index: int, cand: dict[str, Any]) -> CandidateEvidence:
    raw_warnings = cand.get("warnings") or []
    warnings = [
        w
        for w in (_clip(x, 160) for x in raw_warnings[:_MAX_WARNINGS_PER_CANDIDATE])
        if w
    ]
    return CandidateEvidence(
        id=f"C{index}",
        candidate_id=_clip(cand.get("candidate_id"), 80),
        ticker=cand.get("ticker"),
        exchange=cand.get("exchange"),
        company_name=_clip(cand.get("company_name"), 120),
        country=cand.get("country"),
        sector=cand.get("sector"),
        industry=cand.get("industry"),
        thesis_relevance_score=cand.get("thesis_relevance_score"),
        combined_internal_score=cand.get("combined_internal_score"),
        candidate_score=cand.get("candidate_score"),
        candidate_score_grade=cand.get("candidate_score_grade"),
        score_breakdown=_score_breakdown(cand),
        data_coverage=_data_coverage(cand),
        catalyst_summary=_catalyst_summary(cand),
        safety_valid=cand.get("safety_valid"),
        human_review_required=bool(cand.get("human_review_required", True)),
        is_public=bool(cand.get("is_public", False)),
        warnings=warnings,
    )


def _known_gaps(candidates: list[CandidateEvidence]) -> list[str]:
    """Aggregate coverage gaps across candidates (deduped, bounded)."""
    gaps: list[str] = []
    sparse = [c for c in candidates if (c.data_coverage.get("missing_info_count") or 0) >= 3]
    if sparse:
        gaps.append(
            f"{len(sparse)} candidate(s) have sparse data (missing_info_count >= 3)."
        )
    non_sec = [
        c
        for c in candidates
        if c.data_coverage.get("sec_eligible") is False
    ]
    if non_sec:
        gaps.append(
            f"{len(non_sec)} candidate(s) are not SEC-eligible; fundamentals may be "
            "not_sourced and require human research."
        )
    unsafe = [c for c in candidates if c.safety_valid is False]
    if unsafe:
        gaps.append(f"{len(unsafe)} candidate(s) did not pass the candidate safety scan.")
    # Coverage/analyst-volume proxies are unavailable in this pack.
    gaps.append(
        "Sell-side analyst counts and English-news volume are not available in "
        "this pack; treat coverage as unknown, not zero."
    )
    return gaps[:_MAX_KNOWN_GAPS]


def build_discovery_evidence_pack(
    *,
    run: dict[str, Any],
    candidates: list[dict[str, Any]],
    max_candidates: int = 25,
) -> DiscoveryEvidencePack:
    """Build a bounded, cited evidence pack for one discovery run."""
    ctx = _run_context(run)
    facts = _run_facts(run, ctx)

    cap = max(1, max_candidates)
    evidence_candidates = [
        _candidate_evidence(i + 1, c)
        for i, c in enumerate(candidates[:cap])
        if isinstance(c, dict)
    ]

    run_warnings = run.get("warnings") or []
    run_warnings = [
        w for w in (_clip(x, 200) for x in run_warnings[:10]) if w
    ] if isinstance(run_warnings, list) else []

    known_gaps = _known_gaps(evidence_candidates)
    if len(candidates) > cap:
        known_gaps.insert(
            0,
            f"Only the top {cap} of {len(candidates)} candidates were included in "
            "this evidence pack (bounded for cost).",
        )

    return DiscoveryEvidencePack(
        evidence_pack_version=DISCOVERY_EVIDENCE_PACK_VERSION,
        run=ctx,
        run_facts=facts,
        candidates=evidence_candidates,
        known_gaps=known_gaps[:_MAX_KNOWN_GAPS],
        run_warnings=run_warnings,
        do_not_infer=list(_DO_NOT_INFER),
    )
