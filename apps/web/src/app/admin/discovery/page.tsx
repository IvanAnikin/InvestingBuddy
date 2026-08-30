"use client";

import Link from "next/link";
import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import {
  createDiscoveryRun,
  createThesisDiscoveryRun,
  getCandidateAnalysisJob,
  getDiscoveryCandidate,
  getDiscoveryCouncilReview,
  getDiscoveryRun,
  getFieldReview,
  getFieldReviewEligibility,
  listDiscoveryCandidates,
  listDiscoveryRuns,
  listSupportedFilters,
  listSupportedThemes,
  parseThesis,
  runCandidateAnalysis,
  runDiscoveryCouncilReview,
  runFieldReview,
} from "@/lib/api";
import type {
  CountryFilterOption,
  DiscoveryCandidate,
  DiscoveryCandidateDetail,
  DiscoveryCouncilReview,
  DiscoveryRun,
  DiscoveryRunCreate,
  FieldPriorityEntry,
  FieldReview,
  FieldReviewEligibility,
  FieldReviewEligibilityCandidate,
  FilterOption,
  ParseThesisResponse,
  ReportLinkSummary,
  RunCandidateAnalysisResponse,
  SupportedFiltersResponse,
  SupportedThemesResponse,
  ThesisDiscoveryRunCreate,
} from "@/types/api";
import { buildThesisDiscoveryRequest } from "@/lib/workflows";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";

// Mirrors the backend DISCOVERY_MAX_UNIVERSE_SIZE default (client-side preview
// only — the server always enforces the real limit).
const CLIENT_MAX_UNIVERSE = 15;

// Phase 25.1 — runs are processed in the background; the UI polls run status
// until it reaches a terminal state.
const POLL_INTERVAL_MS = 3000;

// Full-analysis JOB lifecycle labels. These describe the background job only —
// never an investment action and never a rating.
const ANALYSIS_JOB_LABELS: Record<string, string> = {
  pending: "Analysis queued",
  running: "Analysis running",
  completed: "Analysis complete",
  completed_with_warnings: "Analysis complete (warnings)",
  failed: "Analysis failed",
};

const ANALYSIS_JOB_COLORS: Record<
  string,
  "gray" | "blue" | "green" | "amber" | "red" | "purple"
> = {
  pending: "gray",
  running: "blue",
  completed: "green",
  completed_with_warnings: "amber",
  failed: "red",
};
const TERMINAL_STATUSES = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "cancelled",
]);

function isTerminal(status: string | undefined | null): boolean {
  return status ? TERMINAL_STATUSES.has(status) : false;
}

const PROVIDERS = [
  { value: "free_real", label: "free_real — SEC + price + trend (recommended)" },
  { value: "eodhd_free_real", label: "eodhd_free_real — EODHD price + SEC" },
  { value: "mock", label: "mock — offline / dev only" },
];

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40";

// ---------------------------------------------------------------------------
// Searchable controlled select (Phase 27.1C)
//
// A combobox whose allowed values come from the backend. The admin can type to
// filter, but cannot commit a value outside the option set — arbitrary text is
// rejected on blur (reverting to the last valid selection). Empty = "not
// specified" and is always allowed. Selecting/clearing marks the field as
// manually edited so a later prompt-parse does not overwrite the admin's choice.
// ---------------------------------------------------------------------------

function SearchableSelect({
  label,
  value,
  options,
  onChange,
  onManualEdit,
  placeholder,
  testId,
  filterOption,
}: {
  label: string;
  value: string;
  options: FilterOption[];
  onChange: (v: string) => void;
  onManualEdit?: () => void;
  placeholder?: string;
  testId: string;
  filterOption?: (opt: FilterOption) => boolean;
}) {
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [focused, setFocused] = useState(false);
  const [dirty, setDirty] = useState(false);

  const selectedLabel = options.find((o) => o.value === value)?.label ?? value;
  const display = focused ? query : selectedLabel;
  const available = options.filter((o) =>
    filterOption ? filterOption(o) : true,
  );
  const filtered = available.filter((o) =>
    o.label.toLowerCase().includes(query.trim().toLowerCase()),
  );

  function commit(v: string) {
    onChange(v);
    onManualEdit?.();
  }

  function handleBlur() {
    // Delay so an option's onClick registers before we close.
    window.setTimeout(() => {
      setFocused(false);
      setOpen(false);
      if (!dirty) return; // focused and left without typing — keep selection
      const q = query.trim().toLowerCase();
      setDirty(false);
      setQuery("");
      if (!q) {
        if (value) commit(""); // cleared the text -> unset
        return;
      }
      const exact = available.find((o) => o.label.toLowerCase() === q);
      if (exact) {
        if (exact.value !== value) commit(exact.value);
      }
      // Arbitrary text that matches nothing is rejected: value is left unchanged.
    }, 120);
  }

  return (
    <div className="flex flex-col gap-1">
      <label className="text-sm font-medium text-slate-300">{label}</label>
      <div className="relative">
        <input
          className={inputCls}
          data-testid={testId}
          role="combobox"
          aria-expanded={open}
          aria-controls={`${testId}-options`}
          aria-label={label}
          autoComplete="off"
          placeholder={placeholder}
          value={display}
          onFocus={() => {
            setFocused(true);
            setOpen(true);
            setDirty(false);
            setQuery("");
          }}
          onChange={(e) => {
            setQuery(e.target.value);
            setDirty(true);
            setOpen(true);
          }}
          onBlur={handleBlur}
        />
        {value && !focused && (
          <button
            type="button"
            aria-label={`Clear ${label}`}
            data-testid={`${testId}-clear`}
            onClick={() => commit("")}
            className="absolute right-2 top-1/2 -translate-y-1/2 px-1 text-slate-500 hover:text-slate-200"
          >
            ×
          </button>
        )}
        {open && filtered.length > 0 && (
          <ul
            id={`${testId}-options`}
            className="absolute z-30 mt-1 max-h-56 w-full overflow-auto rounded-lg border border-white/10 bg-slate-900/95 py-1 shadow-xl backdrop-blur"
            data-testid={`${testId}-options`}
          >
            {filtered.slice(0, 40).map((o) => (
              <li key={o.value}>
                <button
                  type="button"
                  data-testid={`${testId}-option`}
                  onMouseDown={(e) => e.preventDefault()}
                  onClick={() => {
                    commit(o.value);
                    setQuery("");
                    setDirty(false);
                    setFocused(false);
                    setOpen(false);
                  }}
                  className={`block w-full px-3 py-1.5 text-left text-sm transition-colors hover:bg-sky-500/15 ${
                    o.value === value ? "text-sky-200" : "text-slate-200"
                  }`}
                >
                  {o.label}
                </button>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

function runStatusColor(status: string): PillColor {
  if (status === "completed") return "green";
  if (status === "completed_with_warnings") return "amber";
  if (status === "running") return "blue";
  if (status === "failed") return "red";
  if (status === "pending") return "gray";
  return "gray";
}

function gradeColor(grade: string | null): PillColor {
  switch (grade) {
    case "high_internal_interest":
      return "blue";
    case "medium_internal_interest":
      return "cyan";
    case "low_internal_interest":
      return "gray";
    default:
      return "amber";
  }
}

function fmt(n: number | null | undefined, digits = 1): string {
  return n === null || n === undefined ? "—" : n.toFixed(digits);
}

// ---------------------------------------------------------------------------
// Candidate detail (inline, expandable)
// ---------------------------------------------------------------------------

function ScoreBar({ label, value }: { label: string; value: number | null }) {
  const pct = Math.max(0, Math.min(100, value ?? 0));
  return (
    <div className="flex items-center gap-2 text-xs">
      <span className="w-32 shrink-0 text-slate-400">{label}</span>
      <div className="h-2 flex-1 overflow-hidden rounded-full bg-white/10">
        <div
          className="h-full rounded-full bg-gradient-to-r from-sky-500 to-violet-500"
          style={{ width: `${pct}%` }}
        />
      </div>
      <span className="w-10 shrink-0 text-right font-mono text-slate-300">
        {fmt(value, 0)}
      </span>
    </div>
  );
}

function CandidateDetailPanel({ candidateId }: { candidateId: string }) {
  const [detail, setDetail] = useState<DiscoveryCandidateDetail | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [showRaw, setShowRaw] = useState(false);
  const [analysisMsg, setAnalysisMsg] = useState<string | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);
  const [reportSummary, setReportSummary] = useState<ReportLinkSummary | null>(
    null,
  );
  // Product readiness — the full-analysis JOB envelope. `null` means no job has
  // ever run for this candidate. `starting` covers the brief window between the
  // click and the 202 coming back.
  const [job, setJob] = useState<RunCandidateAnalysisResponse | null>(null);
  const [starting, setStarting] = useState(false);
  const jobStatus = job?.status;
  const jobInFlight =
    starting || jobStatus === "pending" || jobStatus === "running";

  useEffect(() => {
    let cancelled = false;
    getDiscoveryCandidate(candidateId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setReportId(d.analysis_report_id);
          setReportSummary(d.latest_report ?? null);
        }
      })
      .catch((e) => {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "Failed to load candidate.");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [candidateId]);

  // Load any existing full-analysis job for THIS candidate on mount. A 404 just
  // means no analysis has ever been run — not an error.
  useEffect(() => {
    let cancelled = false;
    getCandidateAnalysisJob(candidateId)
      .then((j) => {
        if (cancelled) return;
        setJob(j);
        if (j.analysis_report_id) setReportId(j.analysis_report_id);
        if (j.report) setReportSummary(j.report);
      })
      .catch(() => {
        // No job yet (404) or transient error — leave the panel in its
        // "never analysed" state.
      });
    return () => {
      cancelled = true;
    };
  }, [candidateId]);

  // Poll the async job while it is in flight. The browser request that STARTS
  // the analysis returns in milliseconds (HTTP 202); the expensive council work
  // happens server-side. Polling stops on any terminal status.
  useEffect(() => {
    if (jobStatus !== "pending" && jobStatus !== "running") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    function schedule() {
      timer = setTimeout(async () => {
        try {
          const next = await getCandidateAnalysisJob(candidateId);
          if (cancelled) return;
          setJob(next);
          setAnalysisMsg(next.message);
          if (next.analysis_report_id) setReportId(next.analysis_report_id);
          if (next.report) setReportSummary(next.report);
          if (next.status === "pending" || next.status === "running") schedule();
        } catch {
          // Transient error — keep polling; the job is still running server-side.
          if (!cancelled) schedule();
        }
      }, POLL_INTERVAL_MS);
    }

    schedule();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [candidateId, jobStatus]);

  async function handleRunAnalysis() {
    setStarting(true);
    setAnalysisMsg(null);
    try {
      // Returns immediately (202) with a pending job — never blocks on the
      // council. `force` is required to pay for a second run once one completed.
      const res = await runCandidateAnalysis(candidateId, {
        force:
          job?.status === "completed" || job?.status === "completed_with_warnings",
      });
      setJob(res);
      setAnalysisMsg(res.message);
      if (res.analysis_report_id) setReportId(res.analysis_report_id);
      if (res.report) setReportSummary(res.report);
    } catch (e) {
      setAnalysisMsg(e instanceof Error ? e.message : "Analysis failed to start.");
    } finally {
      setStarting(false);
    }
  }

  if (loading)
    return (
      <div className="p-4 text-xs text-slate-500" data-testid="candidate-detail-loading">
        Loading candidate detail…
      </div>
    );
  if (error)
    return (
      <div className="p-4">
        <SafetyBanner variant="warning">
          <p>Could not load candidate: {error}</p>
        </SafetyBanner>
      </div>
    );
  if (!detail) return null;

  return (
    <div className="space-y-4 p-4" data-testid="candidate-detail">
      <SafetyBanner variant="info" title="Internal candidate only">
        <p>
          Internal candidate only. Not investment advice. No recommendation has
          been made. The candidate score is an internal prioritization signal
          only. Human review required.
        </p>
      </SafetyBanner>

      {/* Labels */}
      <div className="flex flex-wrap gap-1.5">
        {(detail.labels_json ?? []).map((l) => (
          <StatusPill key={l} label={l} color="gray" />
        ))}
        <StatusPill label="Human review required" color="red" />
      </div>

      {/* Score breakdown */}
      <GlassCard className="space-y-2 p-4" testId="discovery-score-breakdown">
        <p className="mb-1 text-sm font-semibold text-slate-200">
          Score breakdown — internal prioritization only
        </p>
        {/*
          Private-use readiness PR-C — discovery scores are computed ONCE, at
          discovery time, and are deliberately immutable: re-deriving them after
          a full analysis would destroy the record of why the candidate was
          surfaced. But once a full analysis exists, an unlabelled
          "Fundamentals 0 / Data completeness 0" beside a report carrying a
          validated T1 revenue figure reads as a live contradiction. Label the
          snapshot rather than recompute it, and point at the current state.
        */}
        {reportId && (
          <p
            className="rounded border border-amber-400/20 bg-amber-400/5 px-2 py-1.5 text-[11px] text-amber-200"
            data-testid="discovery-stage-snapshot-note"
          >
            Discovery-stage snapshot — these scores describe what was known AT
            DISCOVERY and are never recomputed. A full analysis has since run;
            the Final Analysis report below is the current research state.
          </p>
        )}
        <ScoreBar label="Momentum" value={detail.momentum_score} />
        <ScoreBar label="Catalyst" value={detail.catalyst_score} />
        <ScoreBar label="Fundamentals" value={detail.fundamentals_score} />
        <ScoreBar label="Source quality" value={detail.source_quality_score} />
        <ScoreBar label="Data completeness" value={detail.data_completeness_score} />
        <ScoreBar label="Risk penalty" value={detail.risk_penalty_score} />
        {detail.score_explanation && (
          <p className="mt-2 border-t border-white/10 pt-2 text-xs text-slate-400">
            {detail.score_explanation}
          </p>
        )}
      </GlassCard>

      {/* Thesis relevance (Phase 27 — thesis runs only) */}
      {detail.thesis_match_json && (
        <GlassCard className="space-y-2 p-4" testId="thesis-relevance-card">
          <p className="text-sm font-semibold text-slate-200">
            Thesis relevance — internal prioritization only
          </p>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="rounded bg-white/5 px-2 py-1 text-slate-300">
              Thesis relevance:{" "}
              <span className="font-mono text-slate-100">
                {fmt(detail.thesis_relevance_score, 0)}
              </span>
            </span>
            <span className="rounded bg-white/5 px-2 py-1 text-slate-300">
              Discovery score:{" "}
              <span className="font-mono text-slate-100">
                {fmt(detail.candidate_score, 0)}
              </span>
            </span>
            <span className="rounded bg-sky-500/10 px-2 py-1 text-sky-200">
              Combined internal:{" "}
              <span className="font-mono">
                {fmt(detail.combined_internal_score, 0)}
              </span>
            </span>
            <StatusPill
              label={String(
                detail.thesis_match_json.internal_interest_label ?? "—",
              )}
              color="cyan"
            />
          </div>
          {detail.thesis_match_json.relevance_reason ? (
            <p className="text-xs text-slate-400">
              <span className="font-semibold text-slate-300">Why matched:</span>{" "}
              {String(detail.thesis_match_json.relevance_reason)}
            </p>
          ) : null}
          {Array.isArray(detail.thesis_match_json.matched_keywords) &&
          detail.thesis_match_json.matched_keywords.length > 0 ? (
            <div className="flex flex-wrap gap-1">
              {(detail.thesis_match_json.matched_keywords as string[]).map((k) => (
                <span
                  key={k}
                  className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
                >
                  {k}
                </span>
              ))}
            </div>
          ) : null}
          <p className="border-t border-white/10 pt-2 text-[11px] text-slate-500">
            Source: {String(detail.thesis_match_json.universe_source ?? "—")} ·{" "}
            {String(detail.thesis_match_json.source_tier ?? "—")}. Internal
            research triage only — not investment advice, not a recommendation.
          </p>
        </GlassCard>
      )}

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {/* Trend */}
        <GlassCard className="space-y-1 p-4 text-xs text-slate-300">
          <p className="mb-1 text-sm font-semibold text-slate-200">
            Trend (T6 model-derived)
          </p>
          <p>Momentum label: {detail.momentum_label ?? "—"}</p>
          <p>1M / 3M / 6M return: {fmt(detail.return_1m)}% / {fmt(detail.return_3m)}% / {fmt(detail.return_6m)}%</p>
          <p>% above MA50 / MA200: {fmt(detail.pct_above_ma50)} / {fmt(detail.pct_above_ma200)}</p>
        </GlassCard>

        {/* Catalyst */}
        <GlassCard className="space-y-1 p-4 text-xs text-slate-300">
          <p className="mb-1 text-sm font-semibold text-slate-200">Catalysts</p>
          <p>Coverage: {detail.catalyst_coverage_status ?? "—"}</p>
          <p>
            Press {detail.press_release_event_count} · News {detail.news_event_count} · Filings{" "}
            {detail.filing_event_count}
          </p>
          <p>
            Primary/regulator {detail.primary_or_regulator_event_count} · Aggregator-only{" "}
            {detail.aggregator_only_event_count}
          </p>
          <p>Latest catalyst: {detail.latest_catalyst_date ?? "—"}</p>
        </GlassCard>

        {/* Fundamentals */}
        <GlassCard className="space-y-1 p-4 text-xs text-slate-300">
          <p className="mb-1 text-sm font-semibold text-slate-200">
            Financial Fundamentals
          </p>
          <p>Latest annual: {detail.latest_annual_fy ?? "—"}</p>
          <p>Revenue: {fmt(detail.revenue_mln, 0)}M · YoY {fmt(detail.revenue_growth_yoy_pct)}%</p>
          <p>Net income: {fmt(detail.net_income_mln, 0)}M · FCF {fmt(detail.free_cash_flow_mln, 0)}M</p>
          <p>Market cap: {fmt(detail.market_cap_mln, 0)}M · EV {fmt(detail.enterprise_value_mln, 0)}M · P/E {fmt(detail.pe_ratio)}</p>
        </GlassCard>

        {/* Source quality */}
        <GlassCard className="space-y-1 p-4 text-xs text-slate-300">
          <p className="mb-1 text-sm font-semibold text-slate-200">
            Source quality & completeness
          </p>
          <p>Overall source quality: {detail.source_quality ?? "—"}</p>
          <p>Missing info: {detail.missing_info_count ?? "—"} · Blocking gaps: {detail.blocking_gap_count ?? "—"}</p>
          <p>Safety valid: {String(detail.safety_valid)} · Schema valid: {String(detail.schema_valid)}</p>
          <div className="mt-1 flex flex-wrap gap-1">
            {Object.entries(detail.source_tiers_json ?? {}).map(([tier, n]) => (
              <span
                key={tier}
                className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-400"
              >
                {tier}: {n}
              </span>
            ))}
          </div>
        </GlassCard>
      </div>

      {/* Warnings + missing fields */}
      {(detail.warnings_json?.length || detail.missing_fields_json?.length) ? (
        <GlassCard className="space-y-2 p-4 text-xs text-slate-400">
          {detail.warnings_json?.length ? (
            <div>
              <p className="font-semibold text-slate-300">Warnings</p>
              <ul className="list-inside list-disc break-words">
                {detail.warnings_json.slice(0, 12).map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          ) : null}
          {detail.missing_fields_json?.length ? (
            <p>
              <span className="font-semibold text-slate-300">Missing fields:</span>{" "}
              {detail.missing_fields_json.slice(0, 20).join(", ")}
            </p>
          ) : null}
        </GlassCard>
      ) : null}

      {/* Raw JSON (collapsible) */}
      <div>
        <button
          onClick={() => setShowRaw((v) => !v)}
          className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
        >
          {showRaw ? "Hide raw signal JSON" : "Show raw signal JSON"}
        </button>
        {showRaw && (
          <pre className="mt-2 max-h-64 overflow-auto rounded-lg border border-white/10 bg-black/30 p-3 text-[11px] text-slate-300">
            {JSON.stringify(detail.raw_signal_json, null, 2)}
          </pre>
        )}
      </div>

      {/* Run Full Analysis — async job (202 + poll), never a blocking request */}
      <div className="flex flex-wrap items-center gap-3 border-t border-white/10 pt-3">
        <button
          onClick={handleRunAnalysis}
          disabled={jobInFlight}
          data-testid="run-full-analysis"
          className="rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {jobInFlight
            ? "Running full analysis…"
            : job?.status === "completed" ||
                job?.status === "completed_with_warnings"
              ? "Re-run Full Analysis"
              : "Run Full Analysis"}
        </button>
        {job && (
          <span data-testid="analysis-job-status">
            <StatusPill
              label={ANALYSIS_JOB_LABELS[job.status] ?? job.status}
              color={ANALYSIS_JOB_COLORS[job.status] ?? "gray"}
            />
          </span>
        )}
        {reportId && (
          <Link
            href={`/admin/reports/${reportId}`}
            data-testid="candidate-report-link"
            className="text-sm text-sky-400 hover:text-sky-300 hover:underline"
          >
            {reportSummary?.report_kind === "legacy"
              ? "View Legacy Draft (this candidate) →"
              : "View Latest Final Report (this candidate) →"}
          </Link>
        )}
        {analysisMsg && <p className="text-xs text-slate-400">{analysisMsg}</p>}
      </div>
      {jobInFlight && (
        <p className="text-xs text-slate-500">
          The analysis runs in the background — you can leave this page open. The
          report link appears automatically when it finishes.
        </p>
      )}
      {job?.status === "failed" && (
        <SafetyBanner variant="warning">
          <p>
            The full-analysis job failed{job.error ? ` (${job.error})` : ""}. No
            report was linked. Re-run to try again.
          </p>
        </SafetyBanner>
      )}

      {/* Phase 28A.1 — honest label for the linked report so the reviewer knows
          whether they're opening a modern final report or an old draft. */}
      {reportId && reportSummary && (
        <div
          data-testid="candidate-report-summary"
          className="flex flex-wrap items-center gap-2 text-xs text-slate-400"
        >
          {reportSummary.report_kind === "final" ? (
            <>
              <StatusPill label="Final Internal Report Draft" color="blue" />
              <StatusPill
                label={
                  reportSummary.llm_used
                    ? "LLM Council: Used"
                    : "LLM Council: Not Used"
                }
                color={reportSummary.llm_used ? "purple" : "gray"}
              />
              {reportSummary.schema_valid != null && (
                <span>
                  schema {reportSummary.schema_valid ? "valid" : "invalid"}
                </span>
              )}
              {reportSummary.safety_valid != null && (
                <span>
                  · safety {reportSummary.safety_valid ? "passed" : "failed"}
                </span>
              )}
              {reportSummary.generated_at && (
                <span>
                  · {new Date(reportSummary.generated_at).toLocaleString()}
                </span>
              )}
            </>
          ) : (
            <>
              <StatusPill label="Legacy deterministic draft" color="amber" />
              <span data-testid="candidate-legacy-warning">
                This draft predates LLM council generation. Re-run Full Analysis
                to produce a final report.
              </span>
            </>
          )}
        </div>
      )}
    </div>
  );
}

// ---------------------------------------------------------------------------
// Thesis summary — parsed thesis + generated universe (Phase 27)
// ---------------------------------------------------------------------------

function Chips({ items }: { items: string[] | undefined | null }) {
  if (!items || items.length === 0)
    return <span className="text-slate-500">—</span>;
  return (
    <span className="flex flex-wrap gap-1">
      {items.map((it) => (
        <span
          key={it}
          className="rounded bg-white/5 px-1.5 py-0.5 font-mono text-[10px] text-slate-300"
        >
          {it}
        </span>
      ))}
    </span>
  );
}

// ---------------------------------------------------------------------------
// Supported theme examples (Phase 27.1B)
//
// Clickable example queries that fill the thesis textarea. Sourced from
// GET /market-discovery/supported-themes so the UI cannot offer a theme the
// parser does not support. Every example describes a SEARCH, never an action.
// ---------------------------------------------------------------------------

function SupportedThemeExamples({
  supported,
  onPick,
  title,
}: {
  supported: SupportedThemesResponse | null;
  onPick: (example: string) => void;
  title: string;
}) {
  if (!supported || supported.themes.length === 0) return null;

  return (
    <div
      className="space-y-2 rounded-xl border border-white/10 bg-white/[0.03] p-3"
      data-testid="supported-themes"
    >
      <p className="text-xs font-semibold text-slate-300">{title}</p>
      <div className="flex flex-wrap gap-1.5">
        {supported.themes.map((theme) =>
          theme.examples.map((example) => (
            <button
              key={`${theme.id}-${example}`}
              type="button"
              data-testid="theme-example-chip"
              title={`${theme.label} — ${theme.universe_company_count} curated issuer(s)`}
              onClick={() => onPick(example)}
              className="rounded-full border border-white/10 bg-white/5 px-2.5 py-1 text-[11px] text-slate-300 transition-colors hover:border-sky-400/40 hover:bg-sky-500/15 hover:text-sky-100"
            >
              {example}
            </button>
          )),
        )}
      </div>
      <p className="text-[11px] text-slate-500" data-testid="coverage-note">
        {supported.coverage_note}
      </p>
    </div>
  );
}

function ThesisSummaryPanel({ run }: { run: DiscoveryRun }) {
  const parsed = run.parsed_thesis_json ?? null;
  const universe = run.universe_json ?? null;
  if (run.mode !== "thesis") return null;

  return (
    <GlassCard className="space-y-4 p-5" testId="thesis-summary">
      <div className="flex items-center justify-between">
        <p className="text-sm font-semibold text-slate-200">
          Thesis &amp; generated universe
        </p>
        <StatusPill label="Internal only" color="red" />
      </div>

      {run.thesis_text && (
        <p className="rounded-lg border border-white/10 bg-white/[0.03] p-3 text-sm text-slate-300">
          <span className="font-semibold text-slate-200">Thesis:</span>{" "}
          {run.thesis_text}
        </p>
      )}

      {/* Parsed thesis */}
      {parsed && (
        <div
          className="grid grid-cols-1 gap-2 text-xs text-slate-400 sm:grid-cols-2"
          data-testid="parsed-thesis"
        >
          <div className="flex items-start gap-2">
            <span className="w-24 shrink-0 text-slate-500">Themes</span>
            <Chips items={parsed.themes} />
          </div>
          <div className="flex items-start gap-2">
            <span className="w-24 shrink-0 text-slate-500">Regions</span>
            <Chips items={parsed.regions} />
          </div>
          <div className="flex items-start gap-2">
            <span className="w-24 shrink-0 text-slate-500">Sectors</span>
            <Chips items={parsed.sectors} />
          </div>
          <div className="flex items-start gap-2">
            <span className="w-24 shrink-0 text-slate-500">Industries</span>
            <Chips items={parsed.industries} />
          </div>
          <div className="flex items-start gap-2">
            <span className="w-24 shrink-0 text-slate-500">Keywords</span>
            <Chips items={parsed.keywords} />
          </div>
          <div className="flex items-start gap-2">
            <span className="w-24 shrink-0 text-slate-500">Confidence</span>
            <span className="font-mono text-slate-300">
              {(parsed.confidence * 100).toFixed(0)}%
            </span>
          </div>
        </div>
      )}

      {/* Universe warnings / needs narrowing */}
      {universe?.warnings && universe.warnings.length > 0 && (
        <SafetyBanner variant="warning" title="Universe notes">
          <ul className="list-inside list-disc break-words">
            {universe.warnings.slice(0, 6).map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </SafetyBanner>
      )}

      {/* Generated universe */}
      {universe?.items && universe.items.length > 0 && (
        <div className="overflow-x-auto" data-testid="generated-universe">
          <p className="mb-2 text-xs font-semibold text-slate-300">
            Generated universe — {universe.items.length} bounded candidate(s)
          </p>
          <table className="w-full text-xs">
            <thead>
              <tr className="border-b border-white/10 text-[10px] uppercase tracking-wide text-slate-500">
                <th className="px-2 py-1.5 text-left font-medium">Ticker</th>
                <th className="px-2 py-1.5 text-left font-medium">Company</th>
                <th className="px-2 py-1.5 text-left font-medium">Country</th>
                <th className="px-2 py-1.5 text-left font-medium">Sector</th>
                <th className="px-2 py-1.5 text-left font-medium">Relevance</th>
                <th className="px-2 py-1.5 text-left font-medium">Why matched</th>
                <th className="px-2 py-1.5 text-left font-medium">Source</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-white/5">
              {universe.items.map((it) => (
                <tr key={`${it.ticker}-${it.exchange}`} data-testid="universe-item">
                  <td className="px-2 py-1.5 font-semibold text-slate-100">
                    {it.ticker}
                  </td>
                  <td className="max-w-[10rem] px-2 py-1.5 text-slate-400">
                    <span className="line-clamp-1">{it.company_name ?? "—"}</span>
                  </td>
                  <td className="px-2 py-1.5 text-slate-400">{it.country ?? "—"}</td>
                  <td className="px-2 py-1.5 text-slate-400">{it.sector ?? "—"}</td>
                  <td className="px-2 py-1.5 font-mono text-slate-300">
                    {fmt(it.relevance_score_pre_scan, 0)}
                  </td>
                  <td className="max-w-[14rem] px-2 py-1.5 text-slate-500">
                    <span className="line-clamp-1">{it.relevance_reason}</span>
                  </td>
                  <td className="px-2 py-1.5 text-slate-500">{it.source_tier}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Excluded companies */}
      {universe?.excluded && universe.excluded.length > 0 && (
        <details className="text-xs text-slate-500">
          <summary className="cursor-pointer">
            {universe.excluded.length} company/ies excluded from the universe
          </summary>
          <ul className="mt-1 list-inside list-disc break-words">
            {universe.excluded.slice(0, 12).map((e, i) => (
              <li key={i}>
                <span className="font-mono text-slate-400">{e.ticker}</span> —{" "}
                {e.reason}
              </li>
            ))}
          </ul>
        </details>
      )}
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Discovery council review (Phase 28B — run-level LLM triage)
// ---------------------------------------------------------------------------

// Internal research-workflow actions only — NOT recommendations. No BUY / SELL /
// HOLD / WATCH, no price target / fair value / upside/downside anywhere.
const COUNCIL_ACTION_LABELS: Record<string, string> = {
  research_next: "research next",
  monitor_for_evidence: "monitor",
  insufficient_data: "insufficient data",
  reject_for_now: "reject for now",
};

function councilActionLabel(action: string | null): string {
  return action ? COUNCIL_ACTION_LABELS[action] ?? action : "";
}

function councilActionBadgeCls(action: string | null): string {
  switch (action) {
    case "research_next":
      return "border border-emerald-400/30 bg-emerald-500/10 text-emerald-300";
    case "monitor_for_evidence":
      return "border border-sky-400/30 bg-sky-500/10 text-sky-300";
    case "reject_for_now":
      return "border border-rose-400/30 bg-rose-500/10 text-rose-300";
    default:
      return "border border-white/15 bg-white/5 text-slate-300";
  }
}

// ---------------------------------------------------------------------------
// Run warnings — GROUPED (Phase 32D2c)
//
// The backend has emitted `warning_groups` since Phase C: canonical, deduped,
// severity-classified, bounded to 8 groups, with the raw instances retained for
// diagnostics. This surface kept rendering `warnings` — the RAW list — because
// the TypeScript type never declared the grouped field, so nobody noticed the
// backend already had the answer. A real European run produced 200 raw strings
// here, mostly the same handful repeated per candidate.
//
// Grouping is PRESENTATION ONLY. Nothing is dropped: the raw instances stay one
// click away, the count of collapsed instances is shown per group, and a
// BLOCKING group is never merged or hidden.
// ---------------------------------------------------------------------------

const WARNING_SEVERITY_COLOR: Record<string, PillColor> = {
  blocking: "red",
  warning: "amber",
  info: "blue",
};

function RunWarnings({ run }: { run: DiscoveryRun }) {
  const groups = run.warning_groups ?? [];
  const raw = run.warnings ?? [];
  const rawCount = run.warning_raw_count ?? raw.length;
  if (groups.length === 0 && raw.length === 0) return null;

  // A run whose backend predates grouping still shows something honest.
  if (groups.length === 0) {
    return (
      <details className="text-xs text-amber-300/80" data-testid="run-warnings-raw-only">
        <summary className="cursor-pointer">{rawCount} warning(s)</summary>
        <ul className="mt-1 list-inside list-disc break-words text-slate-400">
          {raw.slice(0, 12).map((w, i) => (
            <li key={i}>{w}</li>
          ))}
        </ul>
      </details>
    );
  }

  return (
    <div className="space-y-1.5" data-testid="run-warning-groups">
      <p className="text-xs font-semibold text-slate-300">
        {groups.length} warning group(s) from {rawCount} instance(s)
      </p>
      <ul className="space-y-1">
        {groups.map((g) => (
          <li
            key={`${g.code}:${g.message}`}
            className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2"
            data-testid="run-warning-group"
          >
            <div className="flex flex-wrap items-center gap-2">
              <StatusPill
                label={g.severity}
                color={WARNING_SEVERITY_COLOR[g.severity] ?? "gray"}
              />
              <span className="font-mono text-[11px] text-slate-400">
                {g.code}
              </span>
              <span className="text-[11px] text-slate-500">
                &times;{g.count}
                {g.scope === "run" ? " (run-level)" : ""}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-300">{g.message}</p>
            {g.subjects.length > 0 && (
              <p className="mt-0.5 text-[11px] text-slate-500">
                Affects: {g.subjects.join(", ")}
              </p>
            )}
            {g.samples.length > 0 && (
              <details className="mt-1 text-[11px] text-slate-500">
                <summary className="cursor-pointer">
                  Original wording ({g.samples.length} sample
                  {g.samples.length === 1 ? "" : "s"})
                </summary>
                <ul className="mt-1 list-inside list-disc break-words">
                  {g.samples.map((sample, i) => (
                    <li key={i}>{sample}</li>
                  ))}
                </ul>
              </details>
            )}
          </li>
        ))}
      </ul>
      {raw.length > 0 && (
        <details
          className="text-[11px] text-slate-500"
          data-testid="run-warnings-raw"
        >
          <summary className="cursor-pointer">
            Show all {rawCount} raw instance(s)
          </summary>
          <ul className="mt-1 max-h-64 list-inside list-disc overflow-y-auto break-words">
            {raw.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}


function CouncilStat({
  label,
  value,
  testid,
}: {
  label: string;
  value: string;
  testid?: string;
}) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/5 px-3 py-2">
      <p className="text-[10px] uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <p className="text-sm font-semibold text-slate-100" data-testid={testid}>
        {value}
      </p>
    </div>
  );
}

function CouncilBucket({
  title,
  action,
  entries,
  testid,
}: {
  title: string;
  action: string;
  entries: DiscoveryCouncilReview["candidates_to_research_next"];
  testid: string;
}) {
  if (!entries || entries.length === 0) return null;
  return (
    <div data-testid={testid}>
      <p className="text-xs font-semibold text-slate-200">
        {title} ({entries.length})
      </p>
      <ul className="mt-1 space-y-1">
        {entries.map((e, i) => (
          <li key={`${e.ticker ?? "?"}-${i}`} className="text-xs text-slate-400">
            <span
              className={`mr-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${councilActionBadgeCls(
                action,
              )}`}
            >
              {councilActionLabel(action)}
            </span>
            <span className="font-semibold text-slate-200">
              {e.ticker ?? "—"}
            </span>
            {e.exchange ? (
              <span className="text-slate-500">.{e.exchange}</span>
            ) : null}
            {e.rationale ? ` — ${e.rationale}` : ""}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CouncilStringList({
  title,
  items,
  testid,
}: {
  title: string;
  items: string[] | undefined;
  testid?: string;
}) {
  if (!items || items.length === 0) return null;
  return (
    <div data-testid={testid}>
      <p className="text-xs font-semibold text-slate-200">{title}</p>
      <ul className="mt-1 list-disc space-y-0.5 pl-5">
        {items.map((it, i) => (
          <li key={i} className="text-xs text-slate-400">
            {it}
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------
// Agent summaries (Phase 32D2c)
//
// These were rendered inside a COLLAPSED <details>, so a reviewer opening the
// panel saw an empty area where every other council section (evidence gaps,
// next source tasks, council notes) renders inline. The payload was never the
// problem — a live run returned eight agents each with a non-empty summary —
// and the e2e assertion used `toContainText`, which reads collapsed DOM text
// and therefore passed while a human saw nothing.
//
// The per-agent summary IS the reasoning a reviewer needs, so it renders like
// the sections beside it: visible, no interaction required. The e2e test now
// asserts VISIBILITY, which a collapsed disclosure cannot satisfy.
// ---------------------------------------------------------------------------

function AgentSummaries({
  summaries,
  testid,
}: {
  summaries: { name: string; summary: string }[];
  testid: string;
}) {
  if (summaries.length === 0) return null;
  return (
    <div data-testid={testid}>
      <p className="text-xs font-semibold text-slate-200">
        Agent summaries ({summaries.length})
      </p>
      <ul className="mt-1 space-y-1">
        {summaries.map(({ name, summary }) => (
          <li
            key={name}
            className="text-xs text-slate-400"
            data-testid="agent-summary-row"
          >
            <span className="font-semibold text-slate-300">{name}:</span>{" "}
            {summary}
          </li>
        ))}
      </ul>
    </div>
  );
}


function DiscoveryCouncilBody({ review }: { review: DiscoveryCouncilReview }) {
  const agentSummaries = Object.entries(review.agent_outputs ?? {})
    .map(([name, out]) => {
      const summary = (out as { summary?: string } | null)?.summary;
      return summary ? { name, summary } : null;
    })
    .filter((x): x is { name: string; summary: string } => x !== null);

  // Phase 32A Slice 6A: the deterministic discovery-chair fallback fires when
  // the LLM discovery chair could not complete. Its `summary` is a bounded,
  // non-consensus synthesis — never a recommendation or valuation conclusion.
  const chairFallbackSummary =
    (review.deterministic_discovery_chair as { summary?: string } | null)
      ?.summary ?? null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        <CouncilStat label="LLM used" value={review.llm_used ? "yes" : "no"} />
        <CouncilStat label="Provider" value={review.provider ?? "—"} />
        <CouncilStat label="Model" value={review.model ?? "—"} />
        <CouncilStat label="Council" value={review.council_version ?? "—"} />
        <CouncilStat
          label="Run quality"
          value={review.run_quality ?? "—"}
          testid="council-run-quality"
        />
        <CouncilStat
          label="Agents ok/fail"
          value={`${review.agents_completed ?? 0}/${review.agents_failed ?? 0}`}
        />
        <CouncilStat
          label="Evidence items"
          value={String(review.evidence_item_count ?? 0)}
        />
        <CouncilStat
          label="Safety"
          value={review.safety_valid ? "valid" : "flagged"}
        />
        <CouncilStat
          label="Human review"
          value={review.human_review_required === false ? "—" : "required"}
        />
        <CouncilStat
          label="Publishable"
          value={review.publication_ready ? "yes" : "no"}
        />
      </div>

      {review.chair_fallback_used && (
        <div
          data-testid="council-chair-fallback"
          className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
        >
          <StatusPill label="Deterministic fallback used" color="amber" />
          <p className="mt-1">
            The LLM discovery chair did not complete, so a deterministic,
            non-consensus summary was attached instead.
          </p>
          {chairFallbackSummary && (
            <p className="mt-1 text-slate-300">{chairFallbackSummary}</p>
          )}
        </div>
      )}

      <CouncilBucket
        title="Research next"
        action="research_next"
        entries={review.candidates_to_research_next}
        testid="council-research-next"
      />
      <CouncilBucket
        title="Monitor for evidence"
        action="monitor_for_evidence"
        entries={review.candidates_to_monitor}
        testid="council-monitor"
      />
      <CouncilBucket
        title="Insufficient data"
        action="insufficient_data"
        entries={review.candidates_insufficient_data}
        testid="council-insufficient"
      />
      <CouncilBucket
        title="Rejected for now"
        action="reject_for_now"
        entries={review.candidates_to_reject}
        testid="council-reject"
      />

      <CouncilStringList
        title="Evidence gaps"
        items={review.evidence_gaps}
        testid="council-evidence-gaps"
      />
      <CouncilStringList
        title="Next source tasks"
        items={review.next_source_tasks}
        testid="council-next-tasks"
      />
      <CouncilStringList
        title="Council notes"
        items={review.warnings}
        testid="council-warnings"
      />

      <AgentSummaries
        summaries={agentSummaries}
        testid="council-agent-summaries"
      />

      <p className="text-[11px] text-slate-500">{review.disclaimer}</p>
    </div>
  );
}

function DiscoveryCouncilPanel({
  review,
  loading,
  error,
  disabled,
  onRun,
}: {
  review: DiscoveryCouncilReview | null;
  loading: boolean;
  error: string | null;
  disabled: boolean;
  onRun: () => void;
}) {
  const status = review?.status ?? null;
  const inFlight = status === "pending" || status === "running";
  const failed = status === "failed";
  // A usable completed review is attached. Fall back to detecting review content
  // for a legacy response that predates the async `review_available` flag.
  const hasReview =
    (review?.review_available ?? false) ||
    (review != null && status == null && review.run_quality != null);
  const busy = loading || inFlight;

  // Phase 28B.3 — explicit, deterministic-vs-council status so the reviewer
  // never assumes the LLM council already ran just because candidates exist.
  const councilStatusLabel = disabled
    ? "Disabled"
    : inFlight
      ? "Running"
      : failed && !hasReview
        ? "Failed"
        : hasReview
          ? "Completed"
          : "Not run";
  const councilStatusColor: PillColor = disabled
    ? "gray"
    : inFlight
      ? "blue"
      : failed && !hasReview
        ? "red"
        : hasReview
          ? "green"
          : "gray";

  return (
    <GlassCard className="overflow-hidden" testId="council-review-panel">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-5 py-3">
        <div>
          <p className="text-sm font-semibold text-slate-200">
            Discovery Council Review
          </p>
          <p className="text-xs text-slate-500">
            Internal, citation-bound run-level LLM triage — runs asynchronously,
            human review required. Not investment advice. The candidate queue
            below is deterministic; the LLM council runs only when triggered
            here.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill
            label={`Council Review: ${councilStatusLabel}`}
            color={councilStatusColor}
            testId="council-status-pill"
          />
          <StatusPill label="Internal only" color="red" />
          <button
            type="button"
            onClick={onRun}
            disabled={busy || disabled}
            title={
              disabled
                ? "Discovery council is disabled on this environment."
                : undefined
            }
            data-testid="council-run-button"
            className="rounded-lg bg-gradient-to-r from-violet-500 to-indigo-600 px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy
              ? "Running council…"
              : hasReview
                ? "Re-run Discovery Council Review"
                : "Run Discovery Council Review"}
          </button>
        </div>
      </div>
      <div className="space-y-3 px-5 py-4">
        {disabled && (
          <p
            data-testid="council-disabled"
            className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
          >
            {error || "Discovery council is disabled."}
          </p>
        )}
        {!disabled && error && (
          <p
            data-testid="council-error"
            className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            {error}
          </p>
        )}
        {!disabled && inFlight && (
          <p
            data-testid="council-progress"
            className="rounded-lg border border-indigo-500/30 bg-indigo-500/10 px-3 py-2 text-xs text-indigo-200"
          >
            Council review in progress ({status})
            {(review?.agents_completed ?? 0) + (review?.agents_failed ?? 0) > 0
              ? ` — agents ${review?.agents_completed ?? 0} ok / ${review?.agents_failed ?? 0} failed`
              : "…"}
          </p>
        )}
        {!disabled && failed && !hasReview && (
          <p
            data-testid="council-failed"
            className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            Council review failed
            {review?.error ? ` (${review.error})` : ""}. You can re-run it.
          </p>
        )}
        {!disabled && status === "completed_with_warnings" && (
          <p
            data-testid="council-completed-warnings"
            className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
          >
            Completed with warnings — some agents failed or output was flagged.
            Review the notes below.
          </p>
        )}
        {!review && !disabled && !error && !inFlight && (
          <p data-testid="council-empty" className="text-xs text-slate-500">
            No council review yet. Run the council to generate an internal
            research-priority review of this run&apos;s candidate set.
          </p>
        )}
        {hasReview && review && <DiscoveryCouncilBody review={review} />}
      </div>
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Deep Field Review (Phase 32A Slice 6D)
//
// A SEPARATE council from the Discovery Council above. The Discovery Council
// triages this run's CANDIDATE LIST *before* any full analysis exists. The Deep
// Field Review runs *after* 2+ of those candidates already HAVE a completed full
// analysis, and compares those completed analyses against each other. The two
// panels are labelled and styled distinctly on purpose.
//
// Internal research-PRIORITY buckets only — never a recommendation, rating,
// price target, fair value, or return projection.
// ---------------------------------------------------------------------------

// Fallback threshold used ONLY before the backend's eligibility summary has
// loaded. The authoritative number is `required_candidate_count` on that
// summary (FIELD_REVIEW_MIN_CANDIDATES); the server always enforces it.
const FIELD_REVIEW_MIN_COMPANIES = 2;

// Why a candidate cannot be compared, in admin-readable words. Mirrors the
// closed vocabulary emitted by the backend's candidate resolver.
const FIELD_REVIEW_EXCLUSION_LABELS: Record<string, string> = {
  no_analysis_run: "no full analysis yet",
  report_deleted: "linked analysis report no longer exists",
  draft_only: "analysis never produced a final report",
  not_schema_valid: "final report failed schema validation",
  over_company_cap: "beyond this review's company cap",
};

const FIELD_TIER_LABELS: Record<string, string> = {
  strongest_candidates: "research first",
  second_tier: "research after",
  blocked_insufficient_evidence: "evidence too thin",
};

function fieldTierBadgeCls(tier: string): string {
  switch (tier) {
    case "strongest_candidates":
      return "border border-emerald-400/30 bg-emerald-500/10 text-emerald-300";
    case "second_tier":
      return "border border-sky-400/30 bg-sky-500/10 text-sky-300";
    case "blocked_insufficient_evidence":
      return "border border-amber-400/30 bg-amber-500/10 text-amber-200";
    default:
      return "border border-white/15 bg-white/5 text-slate-300";
  }
}

// Why a candidate could not be compared. Kept verbose on purpose: an excluded
// candidate is never silently dropped from the admin's view.
const FIELD_EXCLUSION_LABELS: Record<string, string> = {
  no_analysis_run: "no full analysis has been run for it yet",
  report_deleted: "its analysis report no longer exists",
  draft_only: "its analysis is still a draft (no final report)",
  not_schema_valid: "its final report did not pass schema validation",
  over_company_cap: "beyond this review's company cap",
};

function FieldTierBucket({
  title,
  tier,
  entries,
  testid,
}: {
  title: string;
  tier: string;
  entries: FieldPriorityEntry[] | undefined;
  testid: string;
}) {
  if (!entries || entries.length === 0) return null;
  return (
    <div data-testid={testid}>
      <p className="text-xs font-semibold text-slate-200">
        {title} ({entries.length})
      </p>
      <ul className="mt-1 space-y-1">
        {entries.map((e, i) => (
          <li
            key={`${e.company_ref ?? e.ticker ?? "?"}-${i}`}
            className="text-xs text-slate-400"
          >
            <span
              className={`mr-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${fieldTierBadgeCls(
                tier,
              )}`}
            >
              {FIELD_TIER_LABELS[tier] ?? tier}
            </span>
            <span className="font-semibold text-slate-200">
              {e.ticker ?? e.company_ref ?? "—"}
            </span>
            {e.exchange ? (
              <span className="text-slate-500">.{e.exchange}</span>
            ) : null}
            {e.rationale ? ` — ${e.rationale}` : ""}
            {e.caveats && e.caveats.length > 0 ? (
              <span className="ml-1 text-amber-300/80">
                [{e.caveats.join("; ")}]
              </span>
            ) : null}
          </li>
        ))}
      </ul>
    </div>
  );
}

function FieldReviewBody({ review }: { review: FieldReview }) {
  const candidates = review.candidates ?? [];
  const missing = candidates.filter((c) => !c.included);
  const included = candidates.filter((c) => c.included);
  const caveated = included.filter(
    (c) => c.data_provenance && c.data_provenance !== "real",
  );
  const agentSummaries = Object.entries(review.agent_outputs ?? {})
    .map(([name, out]) => {
      const summary = (out as { summary?: string } | null)?.summary;
      return summary ? { name, summary } : null;
    })
    .filter((x): x is { name: string; summary: string } => x !== null);

  // The deterministic field-chair fallback fires when the LLM field chair could
  // not complete. Its `summary` is a bounded, non-consensus synthesis — never a
  // ranking, a recommendation, or a valuation conclusion.
  const chairFallbackSummary =
    (review.deterministic_field_chair as { summary?: string } | null)?.summary ??
    null;

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-2 sm:grid-cols-3 lg:grid-cols-4">
        <CouncilStat label="LLM used" value={review.llm_used ? "yes" : "no"} />
        <CouncilStat label="Provider" value={review.provider ?? "—"} />
        <CouncilStat label="Model" value={review.model ?? "—"} />
        <CouncilStat label="Council" value={review.council_version ?? "—"} />
        <CouncilStat
          label="Field quality"
          value={review.field_quality ?? "—"}
          testid="field-review-quality"
        />
        <CouncilStat
          label="Compared"
          value={String(review.included_candidate_count ?? 0)}
          testid="field-review-included-count"
        />
        <CouncilStat
          label="Not comparable"
          value={String(review.missing_candidate_count ?? 0)}
          testid="field-review-missing-count"
        />
        <CouncilStat
          label="Agents ok/fail"
          value={`${review.agents_completed ?? 0}/${review.agents_failed ?? 0}`}
          testid="field-review-agents"
        />
        <CouncilStat
          label="Safety"
          value={review.safety_valid === false ? "flagged" : "valid"}
        />
        <CouncilStat
          label="Human review"
          value={review.human_review_required === false ? "—" : "required"}
        />
        <CouncilStat
          label="Publishable"
          value={review.publication_ready ? "yes" : "no"}
        />
      </div>

      {review.chair_fallback_used && (
        <div
          data-testid="field-review-chair-fallback"
          className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
        >
          <StatusPill label="Deterministic fallback used" color="amber" />
          <p className="mt-1">
            The LLM field chair did not complete, so a deterministic,
            non-consensus summary was attached instead. No comparative ranking
            was produced — the priority buckets below are empty for that reason,
            not because any company was assessed and set aside.
          </p>
          {chairFallbackSummary && (
            <p className="mt-1 text-slate-300">{chairFallbackSummary}</p>
          )}
        </div>
      )}

      {caveated.length > 0 && (
        <p
          data-testid="field-review-evidence-limits"
          className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
        >
          Evidence limitations:{" "}
          {caveated
            .map(
              (c) =>
                `${c.ticker ?? c.citation_ref} data_provenance=${c.data_provenance}`,
            )
            .join(", ")}
          . These companies are still compared, but their figures are not
          authoritative.
        </p>
      )}

      <FieldTierBucket
        title="Strongest candidates (research first)"
        tier="strongest_candidates"
        entries={review.strongest_candidates}
        testid="field-review-strongest"
      />
      <FieldTierBucket
        title="Second tier"
        tier="second_tier"
        entries={review.second_tier}
        testid="field-review-second-tier"
      />
      <FieldTierBucket
        title="Blocked — insufficient evidence"
        tier="blocked_insufficient_evidence"
        entries={review.blocked_insufficient_evidence}
        testid="field-review-blocked"
      />

      {missing.length > 0 && (
        <div data-testid="field-review-missing">
          <p className="text-xs font-semibold text-slate-200">
            Not comparable ({missing.length})
          </p>
          <ul className="mt-1 list-disc space-y-0.5 pl-5">
            {missing.map((c) => (
              <li key={c.citation_ref} className="text-xs text-slate-400">
                <span className="font-semibold text-slate-200">
                  {c.ticker ?? "—"}
                </span>
                {c.exchange ? (
                  <span className="text-slate-500">.{c.exchange}</span>
                ) : null}{" "}
                —{" "}
                {c.exclusion_reason
                  ? (FIELD_EXCLUSION_LABELS[c.exclusion_reason] ??
                    c.exclusion_reason)
                  : "reason not recorded"}
              </li>
            ))}
          </ul>
        </div>
      )}

      <CouncilStringList
        title="Field uncertainties"
        items={review.field_uncertainties}
        testid="field-review-uncertainties"
      />
      <CouncilStringList
        title="Evidence gaps"
        items={review.evidence_gaps}
        testid="field-review-evidence-gaps"
      />
      <CouncilStringList
        title="Next research tasks"
        items={review.next_research_tasks}
        testid="field-review-next-tasks"
      />
      <CouncilStringList
        title="Review notes"
        items={review.warnings}
        testid="field-review-warnings"
      />

      <AgentSummaries
        summaries={agentSummaries}
        testid="field-review-agent-summaries"
      />

      <p className="text-[11px] text-slate-500">{review.disclaimer}</p>
    </div>
  );
}

function FieldReviewPanel({
  review,
  eligibility,
  loading,
  error,
  disabled,
  candidateCount,
  onRun,
}: {
  review: FieldReview | null;
  eligibility: FieldReviewEligibility | null;
  loading: boolean;
  error: string | null;
  disabled: boolean;
  candidateCount: number;
  onRun: () => void;
}) {
  const status = review?.status ?? null;
  const inFlight = status === "pending" || status === "running";
  const failed = status === "failed";
  const insufficient = status === "insufficient_candidates";
  const hasReview = review?.review_available ?? false;
  const busy = loading || inFlight;

  // Every eligibility number below comes from the backend's own candidate
  // resolver (GET .../field-review-eligibility) — the SAME code the review runs.
  // The client deliberately does not re-derive it: "has an analysis_report_id"
  // is looser than what the review enforces (the linked report must also exist,
  // be FINAL and be schema-valid), so a client-side guess would advertise
  // companies the backend then refuses to compare.
  const requiredCompanies =
    eligibility?.required_candidate_count ?? FIELD_REVIEW_MIN_COMPANIES;
  const withFullAnalysisCount = eligibility?.with_full_analysis_count ?? 0;
  const comparableNowCount = eligibility?.included_count ?? 0;
  const notComparableCount = eligibility?.not_comparable_count ?? 0;

  // Gate ONLY on an answer we actually received. If the summary could not be
  // loaded the button stays live: the backend answers a premature run with a
  // structured 422 carrying this same message, which is far better than a
  // silently dead button.
  const tooFewCompanies =
    eligibility !== null && withFullAnalysisCount < requiredCompanies;

  // Candidates that still need a completed full analysis before they can be
  // compared — named, so the admin knows what to run next.
  const awaitingAnalysis: FieldReviewEligibilityCandidate[] = (
    eligibility?.candidates ?? []
  ).filter((c) => !c.has_full_analysis);

  const statusLabel = disabled
    ? "Disabled"
    : inFlight
      ? "Running"
      : insufficient
        ? "Not enough analyses"
        : failed && !hasReview
          ? "Failed"
          : hasReview
            ? "Completed"
            : "Not run";
  const statusColor: PillColor = disabled
    ? "gray"
    : inFlight
      ? "blue"
      : insufficient
        ? "amber"
        : failed && !hasReview
          ? "red"
          : hasReview
            ? "green"
            : "gray";

  const insufficientMessage = `Needs at least ${requiredCompanies} candidates with completed full analyses from this discovery run.`;
  const runDisabledReason = disabled
    ? "Deep Field Review is disabled on this environment."
    : tooFewCompanies
      ? `${insufficientMessage} Currently ${withFullAnalysisCount} of ${candidateCount}.`
      : undefined;

  return (
    <GlassCard className="overflow-hidden" testId="field-review-panel">
      <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-5 py-3">
        <div>
          <p className="text-sm font-semibold text-slate-200">
            Deep Field Review
          </p>
          <p className="text-xs text-slate-500">
            A <span className="font-semibold text-slate-400">separate</span>{" "}
            council from the Discovery Council Review above. It compares the
            companies in this run that{" "}
            <span className="font-semibold text-slate-400">
              already have a completed full analysis
            </span>{" "}
            — it does not re-analyse or re-fetch anything — and produces an
            internal research-priority shortlist. Not investment advice, no
            rating, no valuation. Human review required.
          </p>
        </div>
        <div className="flex items-center gap-2">
          <StatusPill
            label={`Deep Field Review: ${statusLabel}`}
            color={statusColor}
            testId="field-review-status-pill"
          />
          <StatusPill label="Internal only" color="red" />
          <button
            type="button"
            onClick={onRun}
            disabled={busy || disabled || tooFewCompanies}
            title={runDisabledReason}
            data-testid="field-review-run-button"
            className="rounded-lg bg-gradient-to-r from-teal-500 to-cyan-600 px-4 py-2 text-sm font-semibold text-white transition hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {busy
              ? "Running field review…"
              : hasReview
                ? "Re-run Deep Field Review"
                : "Run Deep Field Review"}
          </button>
        </div>
      </div>
      <div className="space-y-3 px-5 py-4">
        <div className="grid grid-cols-2 gap-2 sm:grid-cols-4">
          <CouncilStat
            label="Candidates in run"
            value={String(candidateCount)}
            testid="field-review-run-candidates"
          />
          {/* "—" (not "0") while the backend's answer is unavailable: a failed
              fetch must never be displayed as a real count of zero. */}
          <CouncilStat
            label="With full analysis"
            value={eligibility ? String(withFullAnalysisCount) : "—"}
            testid="field-review-analyzed-candidates"
          />
          <CouncilStat
            label="Comparable now"
            value={eligibility ? String(comparableNowCount) : "—"}
            testid="field-review-included-candidates"
          />
          <CouncilStat
            label="Not comparable (now)"
            value={eligibility ? String(notComparableCount) : "—"}
            testid="field-review-missing-candidates"
          />
        </div>

        {!disabled && !eligibility && (
          <p
            data-testid="field-review-eligibility-unavailable"
            className="rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-400"
          >
            Could not load which candidates are comparable. The counts above are
            unknown, not zero — running the review will still report the exact
            reason.
          </p>
        )}

        {disabled && (
          <p
            data-testid="field-review-disabled"
            className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
          >
            {error || "Deep Field Review is disabled."}
          </p>
        )}
        {!disabled && tooFewCompanies && !hasReview && (
          <div
            data-testid="field-review-too-few"
            className="space-y-1 rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-xs text-slate-400"
          >
            <p>{runDisabledReason}</p>
            {awaitingAnalysis.length > 0 && (
              <>
                <p className="text-slate-500">
                  Still needs a completed full analysis:
                </p>
                <ul
                  data-testid="field-review-awaiting-analysis"
                  className="list-disc space-y-0.5 pl-4"
                >
                  {awaitingAnalysis.map((c) => (
                    <li key={c.candidate_id}>
                      <span className="font-semibold text-slate-300">
                        {c.ticker ?? "—"}
                      </span>
                      {c.exclusion_reason
                        ? ` — ${
                            FIELD_REVIEW_EXCLUSION_LABELS[c.exclusion_reason] ??
                            c.exclusion_reason
                          }`
                        : ""}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </div>
        )}
        {!disabled && error && (
          <p
            data-testid="field-review-error"
            className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            {error}
          </p>
        )}
        {!disabled && inFlight && (
          <p
            data-testid="field-review-progress"
            className="rounded-lg border border-teal-500/30 bg-teal-500/10 px-3 py-2 text-xs text-teal-200"
          >
            Deep Field Review in progress ({status})
            {(review?.agents_completed ?? 0) + (review?.agents_failed ?? 0) > 0
              ? ` — agents ${review?.agents_completed ?? 0} ok / ${review?.agents_failed ?? 0} failed`
              : "…"}
          </p>
        )}
        {!disabled && insufficient && (
          <p
            data-testid="field-review-insufficient"
            className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
          >
            {insufficientMessage} Run &quot;Full Analysis&quot; on more
            candidates, then re-run this review.
          </p>
        )}
        {!disabled && failed && !hasReview && (
          <p
            data-testid="field-review-failed"
            className="rounded-lg border border-rose-500/30 bg-rose-500/10 px-3 py-2 text-xs text-rose-200"
          >
            Deep Field Review failed
            {review?.error ? ` (${review.error})` : ""}. You can re-run it.
          </p>
        )}
        {!disabled && status === "completed_with_warnings" && (
          <p
            data-testid="field-review-completed-warnings"
            className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200"
          >
            Completed with warnings — some agents failed or output was flagged.
            Review the notes below.
          </p>
        )}
        {!review && !disabled && !error && !inFlight && !tooFewCompanies && (
          <p data-testid="field-review-empty" className="text-xs text-slate-500">
            No Deep Field Review yet. Run it to compare the completed analyses in
            this run and produce an internal research-priority shortlist.
          </p>
        )}
        {(hasReview || insufficient) && review && (
          <FieldReviewBody review={review} />
        )}
      </div>
    </GlassCard>
  );
}

// ---------------------------------------------------------------------------
// Main page
// ---------------------------------------------------------------------------

export default function DiscoveryPage() {
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  // Discovery mode: Phase 25 ticker/curated vs Phase 27 thesis/segment.
  const [discoveryMode, setDiscoveryMode] = useState<"ticker" | "thesis">(
    "ticker",
  );

  // Ticker-mode form state
  const [provider, setProvider] = useState("free_real");
  const [universeSource, setUniverseSource] =
    useState<"curated_seed" | "manual_tickers">("curated_seed");
  const [manualTickers, setManualTickers] = useState("AAPL, MSFT, NVDA");
  const [exchange, setExchange] = useState("US");
  const [lookbackDays, setLookbackDays] = useState("90");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [startedMsg, setStartedMsg] = useState<string | null>(null);

  // Thesis-mode form state (Phase 27)
  const [thesisText, setThesisText] = useState("");
  const [thesisRegion, setThesisRegion] = useState("");
  const [thesisSector, setThesisSector] = useState("");
  const [thesisCountry, setThesisCountry] = useState("");
  const [thesisMaxUniverse, setThesisMaxUniverse] = useState("25");
  const [thesisMaxCandidates, setThesisMaxCandidates] = useState("10");
  const [thesisLookback, setThesisLookback] = useState("90");

  // Phase 27.1B — supported themes / example queries, fetched from the backend
  // so the UI can never advertise a theme the parser does not support.
  const [supported, setSupported] = useState<SupportedThemesResponse | null>(
    null,
  );

  // Phase 27.1C — controlled selector options + prompt-derived autofill.
  const [filters, setFilters] = useState<SupportedFiltersResponse | null>(null);
  const [detected, setDetected] = useState<ParseThesisResponse | null>(null);
  // A field the admin has manually set is never overwritten by a later parse.
  const regionEdited = useRef(false);
  const countryEdited = useRef(false);
  const sectorEdited = useRef(false);

  // Selected run + live detail (polled while processing) + candidates
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<DiscoveryRun | null>(null);
  const [candTick, setCandTick] = useState(0);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [sort, setSort] = useState("candidate_score");

  // Phase 28B — run-level LLM discovery council review (manual admin-triggered).
  const [councilReview, setCouncilReview] =
    useState<DiscoveryCouncilReview | null>(null);
  const [councilLoading, setCouncilLoading] = useState(false);
  const [councilError, setCouncilError] = useState<string | null>(null);
  const [councilDisabled, setCouncilDisabled] = useState(false);

  // Phase 32A Slice 6D — Deep Field Review. A SEPARATE, later-stage council
  // over the candidates that already have a completed full analysis.
  const [fieldReview, setFieldReview] = useState<FieldReview | null>(null);
  const [fieldReviewLoading, setFieldReviewLoading] = useState(false);
  const [fieldReviewError, setFieldReviewError] = useState<string | null>(null);
  const [fieldReviewDisabled, setFieldReviewDisabled] = useState(false);
  // The backend's own eligibility verdict for this run. Null until it loads (or
  // if it fails) — the panel never substitutes a client-side guess for it.
  const [fieldEligibility, setFieldEligibility] =
    useState<FieldReviewEligibility | null>(null);

  const parsedTickers = manualTickers
    .split(",")
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  const manualCount = new Set(parsedTickers).size;

  // Supported themes are static per deploy — fetched once, and a failure is
  // non-fatal: the thesis form still works, it just offers no example chips.
  useEffect(() => {
    let cancelled = false;
    async function fetchThemes() {
      try {
        const data = await listSupportedThemes();
        if (!cancelled) setSupported(data);
      } catch {
        // Non-fatal: examples are a convenience, not a prerequisite.
      }
    }
    void fetchThemes();
    return () => {
      cancelled = true;
    };
  }, []);

  // Phase 27.1C — controlled selector options. Fetched once; a failure is
  // non-fatal (the selects simply render no options until it succeeds).
  useEffect(() => {
    let cancelled = false;
    async function fetchFilters() {
      try {
        const data = await listSupportedFilters();
        if (!cancelled) setFilters(data);
      } catch {
        // Non-fatal.
      }
    }
    void fetchFilters();
    return () => {
      cancelled = true;
    };
  }, []);

  // Phase 27.1C — debounced prompt-derived autofill. As the admin types (or
  // pastes) a thesis, detect the Region/Country/Sector and auto-fill any field
  // the admin has NOT manually edited. Manual edits are preserved.
  useEffect(() => {
    const text = thesisText.trim();
    let cancelled = false;
    const handle = window.setTimeout(async () => {
      if (text.length < 3) {
        if (!cancelled) setDetected(null);
        return;
      }
      try {
        const d = await parseThesis(text);
        if (cancelled) return;
        setDetected(d);
        if (!regionEdited.current) setThesisRegion(d.region ?? "");
        if (!countryEdited.current) setThesisCountry(d.country ?? "");
        if (!sectorEdited.current) setThesisSector(d.sector ?? "");
      } catch {
        // Non-fatal: autofill is a convenience, manual entry still works.
      }
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [thesisText]);

  useEffect(() => {
    let cancelled = false;
    async function fetchRuns() {
      try {
        const data = await listDiscoveryRuns();
        if (cancelled) return;
        setRuns(data.runs);
        setSelectedRunId((current) =>
          current ?? (data.runs.length > 0 ? data.runs[0].id : null),
        );
      } catch (e) {
        if (!cancelled)
          setRunsError(e instanceof Error ? e.message : "Failed to load runs.");
      } finally {
        if (!cancelled) setLoadingRuns(false);
      }
    }
    void fetchRuns();
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    async function fetchCandidates(runId: string, sortKey: string) {
      try {
        const data = await listDiscoveryCandidates(runId, { sort: sortKey });
        if (!cancelled) setCandidates(data.candidates);
      } catch (e) {
        if (!cancelled)
          setCandidatesError(
            e instanceof Error ? e.message : "Failed to load candidates.",
          );
      } finally {
        if (!cancelled) setLoadingCandidates(false);
      }
    }
    void fetchCandidates(selectedRunId, sort);
    return () => {
      cancelled = true;
    };
  }, [selectedRunId, sort, candTick]);

  // Phase 28B / 28B.2 — load the current discovery-council job state for the
  // selected run. A 404 (no job has run and the council is enabled) is not an
  // error; a `disabled` status flips the panel into its disabled state; a
  // pending/running status hands off to the poll effect below. State is reset
  // whenever the run changes.
  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    async function fetchReview(runId: string) {
      // Reset prior run's review state, then load the current job state.
      if (!cancelled) {
        setCouncilReview(null);
        setCouncilError(null);
        setCouncilDisabled(false);
      }
      try {
        const review = await getDiscoveryCouncilReview(runId);
        if (cancelled) return;
        if (review.status === "disabled") {
          setCouncilDisabled(true);
        } else {
          setCouncilReview(review);
        }
      } catch {
        // No job yet (404) or transient error — leave the panel empty.
      }
    }
    void fetchReview(selectedRunId);
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  // Phase 28B.2 — poll the async council job while it is in flight. Each GET
  // returns the current job envelope; polling stops once the status is terminal
  // (completed / completed_with_warnings / failed). Mirrors the run-status poll.
  useEffect(() => {
    if (!selectedRunId) return;
    const status = councilReview?.status;
    if (status !== "pending" && status !== "running") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    function schedule(runId: string) {
      timer = setTimeout(async () => {
        try {
          const next = await getDiscoveryCouncilReview(runId);
          if (cancelled) return;
          setCouncilReview(next);
          if (next.status === "pending" || next.status === "running") {
            schedule(runId);
          }
        } catch {
          // Transient error — keep polling; the job is still running server-side.
          if (!cancelled) schedule(runId);
        }
      }, POLL_INTERVAL_MS);
    }

    schedule(selectedRunId);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedRunId, councilReview?.status]);

  // Phase 32A Slice 6D — load the current Deep Field Review job state for the
  // selected run. A 404 (no review has run and the feature is enabled) is not an
  // error; a `disabled` status flips the panel into its disabled state; a
  // pending/running status hands off to the poll effect below.
  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    async function fetchFieldReview(runId: string) {
      if (!cancelled) {
        setFieldReview(null);
        setFieldReviewError(null);
        setFieldReviewDisabled(false);
      }
      try {
        const review = await getFieldReview(runId);
        if (cancelled) return;
        if (review.status === "disabled") {
          setFieldReviewDisabled(true);
        } else {
          setFieldReview(review);
        }
      } catch {
        // No review yet (404) or transient error — leave the panel empty.
      }
    }
    void fetchFieldReview(selectedRunId);
    return () => {
      cancelled = true;
    };
  }, [selectedRunId]);

  // The authoritative "what could a field review compare right now?" answer.
  // Refetched whenever the candidate list changes, so finishing a full analysis
  // updates the stats and un-gates the button without a page reload. A failure
  // leaves it null: the panel then keeps the button live and lets the backend's
  // structured 422 explain the situation, rather than guessing client-side.
  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    async function fetchEligibility(runId: string) {
      try {
        const summary = await getFieldReviewEligibility(runId);
        if (!cancelled) setFieldEligibility(summary);
      } catch {
        if (!cancelled) setFieldEligibility(null);
      }
    }
    void fetchEligibility(selectedRunId);
    return () => {
      cancelled = true;
    };
  }, [selectedRunId, candidates]);

  // Poll the async Deep Field Review job while it is in flight. Polling stops
  // once the status is terminal. Mirrors the discovery-council poll above.
  useEffect(() => {
    if (!selectedRunId) return;
    const status = fieldReview?.status;
    if (status !== "pending" && status !== "running") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    function schedule(runId: string) {
      timer = setTimeout(async () => {
        try {
          const next = await getFieldReview(runId);
          if (cancelled) return;
          setFieldReview(next);
          if (next.status === "pending" || next.status === "running") {
            schedule(runId);
          }
        } catch {
          // Transient error — keep polling; the job is still running server-side.
          if (!cancelled) schedule(runId);
        }
      }, POLL_INTERVAL_MS);
    }

    schedule(selectedRunId);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedRunId, fieldReview?.status]);

  // Poll the selected run's status while it is processing in the background.
  // Each poll refreshes the live run detail and triggers a candidate refetch so
  // the queue fills as tickers finish. Polling stops on a terminal status.
  useEffect(() => {
    if (!selectedRunId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll(runId: string) {
      let status: string | undefined;
      try {
        const detail = await getDiscoveryRun(runId);
        if (cancelled) return;
        status = detail.status;
        setRunDetail(detail);
        setRuns((prev) =>
          prev.map((r) => (r.id === runId ? { ...r, ...detail } : r)),
        );
        setCandTick((t) => t + 1);
      } catch {
        // Non-fatal: keep the last known detail; the manual Refresh still works.
      }
      if (cancelled) return;
      if (!isTerminal(status)) {
        timer = setTimeout(() => void poll(runId), POLL_INTERVAL_MS);
      }
    }

    void poll(selectedRunId);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [selectedRunId]);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    setStartedMsg(null);
    const payload: DiscoveryRunCreate = {
      provider_name: provider,
      universe_source: universeSource,
      exchange: exchange.trim().toUpperCase() || "US",
      lookback_days: parseInt(lookbackDays, 10) || 90,
      tickers: universeSource === "manual_tickers" ? parsedTickers : undefined,
    };
    try {
      // Phase 25.1: POST returns immediately with a pending/running run; the
      // backend processes tickers in the background and the UI polls status.
      const run = await createDiscoveryRun(payload);
      setSelectedRunId(run.id);
      setRunDetail(run);
      setExpandedId(null);
      setStartedMsg(
        run.message ??
          "Discovery run started. Processing in the background — progress updates automatically.",
      );
      setRefreshTick((t) => t + 1);
    } catch (e) {
      // The async POST should not time out, but if the request fails or a
      // gateway 504 slips through, the backend may still complete the run.
      setSubmitError(
        `${e instanceof Error ? e.message : "Failed to start run."} — the ` +
          "request may have timed out. The backend may still be processing; " +
          "refresh recent runs below to check.",
      );
      setRefreshTick((t) => t + 1);
    } finally {
      setSubmitting(false);
    }
  }

  async function handleThesisSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    setStartedMsg(null);
    // Shared with the /research/discover surface (src/lib/workflows.ts) so an
    // identical description produces an identical run on either surface.
    const payload: ThesisDiscoveryRunCreate = buildThesisDiscoveryRequest({
      thesisText,
      region: thesisRegion,
      country: thesisCountry,
      sector: thesisSector,
      maxUniverseSize: parseInt(thesisMaxUniverse, 10) || undefined,
      maxCandidates: parseInt(thesisMaxCandidates, 10) || undefined,
      lookbackDays: parseInt(thesisLookback, 10) || undefined,
    });
    try {
      const run = await createThesisDiscoveryRun(payload);
      setSelectedRunId(run.id);
      setRunDetail(run);
      setExpandedId(null);
      setStartedMsg(
        run.message ??
          "Thesis discovery run started. A bounded universe was generated and is being scanned in the background.",
      );
      setRefreshTick((t) => t + 1);
    } catch (e) {
      // A 422 here means the thesis needs narrowing / matched no company — show
      // the backend's guidance verbatim.
      setSubmitError(
        e instanceof Error ? e.message : "Failed to start thesis run.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const selectedRun =
    (runDetail && runDetail.id === selectedRunId ? runDetail : null) ??
    runs.find((r) => r.id === selectedRunId) ??
    null;
  const selectedRunning = selectedRun ? !isTerminal(selectedRun.status) : false;
  const progressPct =
    selectedRun?.progress_pct ??
    (selectedRun && selectedRun.universe_count
      ? Math.round(
          (selectedRun.processed_count / selectedRun.universe_count) * 100,
        )
      : 0);
  const previewCount =
    universeSource === "manual_tickers" ? manualCount : null;
  const overLimit = previewCount !== null && previewCount > CLIENT_MAX_UNIVERSE;

  // Phase 28B.2 — start the async run-level discovery council job. POST returns
  // immediately with a pending/running status (or an existing completed review);
  // the poll effect then drives it to a terminal state. A disabled response (409,
  // or a `disabled` status) flips the panel into a clearly-labelled disabled
  // state; no fake result is ever fabricated on the client.
  async function handleRunCouncilReview() {
    if (!selectedRunId) return;
    setCouncilLoading(true);
    setCouncilError(null);
    setCouncilDisabled(false);
    try {
      const resp = await runDiscoveryCouncilReview(selectedRunId);
      if (resp.status === "disabled") {
        setCouncilDisabled(true);
        setCouncilReview(null);
      } else {
        // pending/running → the poll effect takes over; completed → shown now.
        setCouncilReview(resp);
      }
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Discovery council review failed.";
      setCouncilError(msg);
      if (/disabled|not available/i.test(msg)) setCouncilDisabled(true);
    } finally {
      setCouncilLoading(false);
    }
  }

  // Phase 32A Slice 6D — trigger the async Deep Field Review. Returns
  // immediately with a pending/running status (or an existing completed review);
  // the poll effect drives it to a terminal state. A 409 (disabled) flips the
  // panel into its disabled state; a 422 (too few completed analyses) is
  // surfaced verbatim. No result is ever fabricated on the client.
  async function handleRunFieldReview() {
    if (!selectedRunId) return;
    setFieldReviewLoading(true);
    setFieldReviewError(null);
    setFieldReviewDisabled(false);
    try {
      const resp = await runFieldReview(selectedRunId);
      if (resp.status === "disabled") {
        setFieldReviewDisabled(true);
        setFieldReview(null);
      } else {
        setFieldReview(resp);
      }
    } catch (err) {
      const msg =
        err instanceof Error ? err.message : "Deep Field Review failed.";
      setFieldReviewError(msg);
      if (/disabled|not available/i.test(msg)) setFieldReviewDisabled(true);
      // A rejected start (e.g. the 422 for too few completed analyses) means our
      // eligibility snapshot is stale — refresh it so the panel and the button
      // immediately agree with the backend.
      try {
        setFieldEligibility(await getFieldReviewEligibility(selectedRunId));
      } catch {
        // Non-fatal: the error message above already explains the refusal.
      }
    } finally {
      setFieldReviewLoading(false);
    }
  }

  // Map a candidate (ticker+exchange) to the council's internal action, so the
  // candidate table can show it inline. Keyed precisely, with a ticker-only
  // fallback. Internal research-workflow states only — never a recommendation.
  const councilActionByKey = useMemo(() => {
    const m = new Map<string, string>();
    if (!councilReview) return m;
    const add = (
      entries: DiscoveryCouncilReview["candidates_to_research_next"],
      action: string,
    ) => {
      for (const e of entries ?? []) {
        if (!e.ticker) continue;
        m.set(`${e.ticker}:${e.exchange ?? ""}`, action);
        if (!m.has(e.ticker)) m.set(e.ticker, action);
      }
    };
    add(councilReview.candidates_to_research_next, "research_next");
    add(councilReview.candidates_to_monitor, "monitor_for_evidence");
    add(councilReview.candidates_insufficient_data, "insufficient_data");
    add(councilReview.candidates_to_reject, "reject_for_now");
    return m;
  }, [councilReview]);

  function councilActionFor(c: DiscoveryCandidate): string | null {
    return (
      councilActionByKey.get(`${c.ticker}:${c.exchange ?? ""}`) ??
      councilActionByKey.get(c.ticker) ??
      null
    );
  }

  return (
    <div className="ib-fade-up space-y-6">
      {/* Header */}
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-white">
          Market Candidate Discovery
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Internal research queue only. Not investment advice. No
          recommendations. Human review required.
        </p>
      </div>

      {/* Safety banner */}
      <SafetyBanner
        variant="danger"
        title="Internal Admin Only — Research Candidate Queue"
      >
        <ul className="list-inside list-disc space-y-0.5">
          <li>Internal admin only — not for publication.</li>
          <li>Not investment advice. No BUY/SELL/HOLD/WATCH. No price targets.</li>
          <li>
            The candidate score is an internal prioritization signal only — it is
            not a recommendation.
          </li>
          <li>Every candidate requires human review before any use.</li>
        </ul>
      </SafetyBanner>

      {/* Start new run */}
      <GlassCard className="p-5">
        <p className="mb-3 text-sm font-semibold text-slate-200">
          Start a new discovery run
        </p>

        {/* Mode tabs — Phase 25 ticker vs Phase 27 thesis */}
        <div
          className="mb-4 inline-flex rounded-lg border border-white/10 bg-white/[0.03] p-0.5"
          role="tablist"
          aria-label="Discovery mode"
        >
          <button
            type="button"
            role="tab"
            aria-selected={discoveryMode === "ticker"}
            data-testid="mode-tab-ticker"
            onClick={() => setDiscoveryMode("ticker")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              discoveryMode === "ticker"
                ? "bg-sky-500/20 text-sky-200"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Manual / curated tickers
          </button>
          <button
            type="button"
            role="tab"
            aria-selected={discoveryMode === "thesis"}
            data-testid="mode-tab-thesis"
            onClick={() => setDiscoveryMode("thesis")}
            className={`rounded-md px-3 py-1.5 text-xs font-medium transition-colors ${
              discoveryMode === "thesis"
                ? "bg-sky-500/20 text-sky-200"
                : "text-slate-400 hover:text-slate-200"
            }`}
          >
            Thesis / market segment
          </button>
        </div>

        {discoveryMode === "thesis" && (
          <form
            onSubmit={handleThesisSubmit}
            className="space-y-4"
            data-testid="thesis-form"
          >
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-slate-300">
                Market segment / thesis
              </label>
              <textarea
                className={`${inputCls} min-h-[80px] resize-y`}
                value={thesisText}
                onChange={(e) => setThesisText(e.target.value)}
                placeholder="European defense suppliers benefiting from NATO spending"
                maxLength={2000}
                data-testid="thesis-text"
              />
              <p className="text-xs text-slate-500">
                Describe a market segment, theme, region, sector or industry. The
                system builds a bounded universe of internal research candidates —
                not investment advice, not a recommendation.
              </p>
            </div>

            <SupportedThemeExamples
              supported={supported}
              onPick={setThesisText}
              title="Supported themes — click an example to use it"
            />

            {/* Controlled Region / Country / Sector selectors (Phase 27.1C).
                Values come from the backend; arbitrary text cannot be
                submitted. Auto-filled from the prompt unless manually edited. */}
            <div className="space-y-3" data-testid="thesis-filters">
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
                <SearchableSelect
                  label="Region (optional)"
                  testId="thesis-region"
                  value={thesisRegion}
                  options={filters?.regions ?? []}
                  onChange={setThesisRegion}
                  onManualEdit={() => {
                    regionEdited.current = true;
                  }}
                  placeholder="Any region"
                />
                <SearchableSelect
                  label="Country (optional)"
                  testId="thesis-country"
                  value={thesisCountry}
                  options={filters?.countries ?? []}
                  onChange={setThesisCountry}
                  onManualEdit={() => {
                    countryEdited.current = true;
                  }}
                  placeholder="Any country"
                  filterOption={(o) =>
                    !thesisRegion ||
                    (o as CountryFilterOption).region === thesisRegion
                  }
                />
                <SearchableSelect
                  label="Sector (optional)"
                  testId="thesis-sector"
                  value={thesisSector}
                  options={filters?.sectors ?? []}
                  onChange={setThesisSector}
                  onManualEdit={() => {
                    sectorEdited.current = true;
                  }}
                  placeholder="Any sector"
                />
              </div>

              {/* Prompt-derived detection preview + reset action. */}
              {detected &&
                (detected.region ||
                  detected.country ||
                  detected.sector ||
                  detected.theme) && (
                  <div
                    className="flex flex-wrap items-center gap-2 text-xs text-slate-400"
                    data-testid="thesis-detected"
                  >
                    <span className="text-slate-500">Detected:</span>
                    <span className="text-slate-300">
                      {[
                        detected.region,
                        detected.country,
                        detected.sector,
                        detected.theme,
                      ]
                        .filter(Boolean)
                        .join(" · ")}
                    </span>
                    <button
                      type="button"
                      data-testid="thesis-reset-detected"
                      onClick={() => {
                        regionEdited.current = false;
                        countryEdited.current = false;
                        sectorEdited.current = false;
                        setThesisRegion(detected.region ?? "");
                        setThesisCountry(detected.country ?? "");
                        setThesisSector(detected.sector ?? "");
                      }}
                      className="rounded border border-white/10 px-2 py-0.5 text-[11px] text-sky-300 transition-colors hover:border-sky-400/40 hover:text-sky-200"
                    >
                      Reset to detected
                    </button>
                  </div>
                )}

              {/* Conflict: an explicit selection contradicts the prompt. The
                  explicit choice is kept; the admin is told the prompt differs. */}
              {detected &&
                (() => {
                  const conflicts: string[] = [];
                  if (
                    detected.country &&
                    thesisCountry &&
                    detected.country !== thesisCountry
                  )
                    conflicts.push(
                      `Prompt mentions ${detected.country}, but Country=${thesisCountry} was selected.`,
                    );
                  if (
                    detected.region &&
                    thesisRegion &&
                    detected.region !== thesisRegion
                  )
                    conflicts.push(
                      `Prompt mentions ${detected.region}, but Region=${thesisRegion} was selected.`,
                    );
                  if (
                    detected.sector &&
                    thesisSector &&
                    detected.sector !== thesisSector
                  )
                    conflicts.push(
                      `Prompt implies ${detected.sector}, but Sector=${thesisSector} was selected.`,
                    );
                  if (conflicts.length === 0) return null;
                  return (
                    <div data-testid="thesis-conflict-warning">
                      <SafetyBanner
                        variant="warning"
                        title="Selection differs from the prompt"
                      >
                        <ul className="list-inside list-disc break-words">
                          {conflicts.map((c) => (
                            <li key={c}>{c}</li>
                          ))}
                        </ul>
                      </SafetyBanner>
                    </div>
                  );
                })()}
            </div>

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-4">
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Max universe
                </label>
                <input
                  type="number"
                  className={inputCls}
                  value={thesisMaxUniverse}
                  onChange={(e) => setThesisMaxUniverse(e.target.value)}
                  min={1}
                  max={50}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Max candidates
                </label>
                <input
                  type="number"
                  className={inputCls}
                  value={thesisMaxCandidates}
                  onChange={(e) => setThesisMaxCandidates(e.target.value)}
                  min={1}
                  max={50}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Lookback days
                </label>
                <input
                  type="number"
                  className={inputCls}
                  value={thesisLookback}
                  onChange={(e) => setThesisLookback(e.target.value)}
                  min={1}
                  max={365}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Provider
                </label>
                <input
                  className={`${inputCls} opacity-60`}
                  value="free_real"
                  readOnly
                  aria-readonly
                />
              </div>
            </div>

            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-xs text-slate-400">
              <p>
                Current thesis discovery uses a bounded curated universe
                bootstrap — it does not scan all global equities. A bounded
                real-company universe (max{" "}
                <span className="text-slate-200">{thesisMaxUniverse}</span>) is
                generated from a curated reference registry, then scanned with the
                free real-data stack. Every result is an internal research
                candidate — human review required. No public output is produced.
              </p>
            </div>

            {/* The backend's own guidance is always shown verbatim — the
                example chips are added alongside it, never in place of it. */}
            {submitError && (
              <SafetyBanner variant="danger">
                <p data-testid="thesis-submit-error">
                  <strong>Cannot start run:</strong> {submitError}
                </p>
                <p className="mt-2" data-testid="thesis-no-match-help">
                  Could not build a bounded universe for this thesis yet. Try
                  one of the supported theme examples below.
                </p>
                <div className="mt-2">
                  <SupportedThemeExamples
                    supported={supported}
                    onPick={setThesisText}
                    title="Supported theme examples"
                  />
                </div>
              </SafetyBanner>
            )}

            {startedMsg && !submitError && (
              <SafetyBanner variant="info" title="Thesis discovery run started">
                <p data-testid="run-started-msg">{startedMsg}</p>
              </SafetyBanner>
            )}

            <button
              type="submit"
              disabled={submitting || thesisText.trim().length < 3}
              className="w-full rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
              data-testid="thesis-submit"
            >
              {submitting
                ? "Building universe & scanning…"
                : "Build Universe & Scan (Internal)"}
            </button>
          </form>
        )}

        {discoveryMode === "ticker" && (
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-slate-300">
                Data provider
              </label>
              <select
                className={inputCls}
                value={provider}
                onChange={(e) => setProvider(e.target.value)}
              >
                {PROVIDERS.map((p) => (
                  <option key={p.value} value={p.value} className="bg-slate-900">
                    {p.label}
                  </option>
                ))}
              </select>
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-slate-300">
                Universe source
              </label>
              <select
                className={inputCls}
                value={universeSource}
                onChange={(e) =>
                  setUniverseSource(
                    e.target.value as "curated_seed" | "manual_tickers",
                  )
                }
              >
                <option value="curated_seed" className="bg-slate-900">
                  Curated seed universe
                </option>
                <option value="manual_tickers" className="bg-slate-900">
                  Manual tickers
                </option>
              </select>
            </div>
          </div>

          {universeSource === "manual_tickers" && (
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="flex flex-col gap-1 sm:col-span-2">
                <label className="text-sm font-medium text-slate-300">
                  Tickers (comma-separated)
                </label>
                <input
                  className={inputCls}
                  value={manualTickers}
                  onChange={(e) => setManualTickers(e.target.value)}
                  placeholder="AAPL, MSFT, NVDA"
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Exchange
                </label>
                <input
                  className={inputCls}
                  value={exchange}
                  onChange={(e) => setExchange(e.target.value)}
                  placeholder="US"
                  maxLength={20}
                />
              </div>
            </div>
          )}

          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-slate-300">
                Lookback days
              </label>
              <input
                type="number"
                className={inputCls}
                value={lookbackDays}
                onChange={(e) => setLookbackDays(e.target.value)}
                min={1}
                max={365}
              />
            </div>
          </div>

          {/* Pre-submit preview */}
          <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-xs text-slate-400">
            <p>
              <span className="font-semibold text-slate-300">Before you run:</span>{" "}
              provider <code className="rounded bg-white/10 px-1 font-mono">{provider}</code>
              {universeSource === "manual_tickers" ? (
                <>
                  {" "}· universe size{" "}
                  <span
                    className={overLimit ? "font-semibold text-rose-300" : "text-slate-200"}
                    data-testid="universe-size"
                  >
                    {previewCount}
                  </span>{" "}
                  ticker(s)
                </>
              ) : (
                <> · curated seed universe (server-configured, kept small)</>
              )}
            </p>
            {overLimit && (
              <p className="mt-1 text-rose-300">
                Universe exceeds the internal cap of {CLIENT_MAX_UNIVERSE}. The
                run will be rejected — reduce the list.
              </p>
            )}
            <p className="mt-1">
              Internal-only discovery. Runs the free real-data stack per ticker.
              No public output is produced.
            </p>
          </div>

          {submitError && (
            <SafetyBanner variant="danger">
              <p>
                <strong>Error:</strong> {submitError}
              </p>
            </SafetyBanner>
          )}

          {startedMsg && !submitError && (
            <SafetyBanner variant="info" title="Discovery run started">
              <p data-testid="run-started-msg">{startedMsg}</p>
            </SafetyBanner>
          )}

          <button
            type="submit"
            disabled={submitting || overLimit}
            className="w-full rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
          >
            {submitting ? "Running internal discovery…" : "Start Internal Discovery Run"}
          </button>
        </form>
        )}
      </GlassCard>

      {/* Recent runs */}
      <GlassCard className="overflow-hidden">
        <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
          <p className="text-sm font-semibold text-slate-200">
            Recent discovery runs
          </p>
          <button
            onClick={() => setRefreshTick((t) => t + 1)}
            className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
          >
            Refresh
          </button>
        </div>
        {runsError && (
          <div className="p-4">
            <SafetyBanner variant="warning">
              <p>Could not load runs: {runsError}</p>
            </SafetyBanner>
          </div>
        )}
        {loadingRuns ? (
          <div className="p-6 text-center text-sm text-slate-500">
            Loading runs…
          </div>
        ) : runs.length === 0 ? (
          <div className="p-6 text-center text-sm text-slate-500">
            No discovery runs yet. Start one above.
          </div>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-5 py-2.5 text-left font-medium">Created</th>
                  <th className="px-3 py-2.5 text-left font-medium">Status</th>
                  <th className="px-3 py-2.5 text-left font-medium">Provider</th>
                  <th className="px-3 py-2.5 text-left font-medium">Universe</th>
                  <th className="px-3 py-2.5 text-left font-medium">Processed</th>
                  <th className="px-3 py-2.5 text-left font-medium">Candidates</th>
                  <th className="px-3 py-2.5 text-left font-medium">Errors</th>
                  <th className="px-3 py-2.5 text-left font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    className={`transition-colors hover:bg-white/5 ${
                      run.id === selectedRunId ? "bg-white/[0.06]" : ""
                    }`}
                  >
                    <td className="whitespace-nowrap px-5 py-3 text-xs text-slate-400">
                      {new Date(run.created_at).toLocaleString()}
                    </td>
                    <td className="px-3 py-3">
                      <StatusPill label={run.status} color={runStatusColor(run.status)} />
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 font-mono text-xs text-slate-400">
                      {run.provider_name}
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-400">
                      {run.universe_count}
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-400">
                      {run.processed_count}
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-300">
                      {run.candidate_count}
                    </td>
                    <td className="px-3 py-3 text-xs text-slate-400">
                      {run.error_count}
                    </td>
                    <td className="px-3 py-3">
                      <button
                        onClick={() => {
                          setSelectedRunId(run.id);
                          setExpandedId(null);
                        }}
                        className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
                      >
                        Open →
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </GlassCard>

      {/* Thesis summary (Phase 27 — parsed thesis + generated universe) */}
      {selectedRun && selectedRun.mode === "thesis" && (
        <ThesisSummaryPanel run={selectedRun} />
      )}

      {/* Discovery council review (Phase 28B — run-level LLM triage) */}
      {selectedRun && (
        <DiscoveryCouncilPanel
          review={councilReview}
          loading={councilLoading}
          error={councilError}
          disabled={councilDisabled}
          onRun={handleRunCouncilReview}
        />
      )}

      {/* Deep Field Review (Phase 32A Slice 6D — comparative review of the
          candidates that ALREADY have a completed full analysis). Deliberately
          rendered directly below, and clearly distinguished from, the Discovery
          Council Review above: the two councils answer different questions. */}
      {selectedRun && (
        <FieldReviewPanel
          review={fieldReview}
          eligibility={fieldEligibility}
          loading={fieldReviewLoading}
          error={fieldReviewError}
          disabled={fieldReviewDisabled}
          candidateCount={candidates.length}
          onRun={handleRunFieldReview}
        />
      )}

      {/* Candidate queue */}
      {selectedRun && (
        <GlassCard className="overflow-hidden">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-white/10 px-5 py-3">
            <div>
              <p className="text-sm font-semibold text-slate-200">
                Candidate queue
              </p>
              <p className="text-xs text-slate-500">
                Run {selectedRun.id.slice(0, 8)} · {candidates.length} internal
                research candidate(s)
              </p>
            </div>
            <div className="flex items-center gap-2">
              <StatusPill label="Internal only" color="red" />
              <select
                className="rounded-lg border border-white/10 bg-white/5 px-2 py-1 text-xs text-slate-200"
                value={sort}
                onChange={(e) => setSort(e.target.value)}
                aria-label="Sort candidates"
              >
                <option value="candidate_score" className="bg-slate-900">
                  Sort: discovery score
                </option>
                {selectedRun.mode === "thesis" && (
                  <>
                    <option
                      value="combined_internal_score"
                      className="bg-slate-900"
                    >
                      Sort: combined internal
                    </option>
                    <option
                      value="thesis_relevance_score"
                      className="bg-slate-900"
                    >
                      Sort: thesis relevance
                    </option>
                  </>
                )}
                <option value="momentum_score" className="bg-slate-900">
                  Sort: momentum
                </option>
                <option value="catalyst_score" className="bg-slate-900">
                  Sort: catalyst
                </option>
                <option value="fundamentals_score" className="bg-slate-900">
                  Sort: fundamentals
                </option>
                <option value="latest_catalyst_date" className="bg-slate-900">
                  Sort: latest catalyst
                </option>
              </select>
            </div>
          </div>

          {/* Run progress (Phase 25.1 — background processing + polling) */}
          <div
            className="space-y-2 border-b border-white/10 px-5 py-3"
            data-testid="run-progress"
          >
            <div className="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-slate-400">
              <span className="flex items-center gap-1.5">
                <StatusPill
                  label={selectedRun.status}
                  color={runStatusColor(selectedRun.status)}
                />
              </span>
              <span data-testid="run-progress-counts">
                Processed{" "}
                <span className="font-mono text-slate-200">
                  {selectedRun.processed_count}
                </span>{" "}
                / {selectedRun.universe_count}
              </span>
              <span>
                Candidates{" "}
                <span className="font-mono text-slate-200">
                  {selectedRun.candidate_count}
                </span>
              </span>
              <span>
                Errors{" "}
                <span className="font-mono text-slate-200">
                  {selectedRun.error_count}
                </span>
              </span>
            </div>
            <div className="h-2 w-full overflow-hidden rounded-full bg-white/10">
              <div
                className={`h-full rounded-full transition-all ${
                  selectedRun.status === "failed"
                    ? "bg-rose-500/70"
                    : "bg-gradient-to-r from-sky-500 to-violet-500"
                }`}
                style={{ width: `${Math.max(0, Math.min(100, progressPct))}%` }}
                data-testid="run-progress-bar"
              />
            </div>
            {selectedRunning && (
              <p className="text-xs text-slate-500" data-testid="run-processing-note">
                Processing in the background. Progress updates automatically — you
                can also refresh this page.
              </p>
            )}
            <RunWarnings run={selectedRun} />
          </div>

          {candidatesError && (
            <div className="p-4">
              <SafetyBanner variant="warning">
                <p>Could not load candidates: {candidatesError}</p>
              </SafetyBanner>
            </div>
          )}

          {loadingCandidates ? (
            <div className="p-6 text-center text-sm text-slate-500">
              Loading candidates…
            </div>
          ) : candidates.length === 0 ? (
            <div
              className="p-6 text-center text-sm text-slate-500"
              data-testid="candidates-empty"
            >
              {selectedRunning
                ? "Candidates will appear as tickers finish processing."
                : "No candidates for this run."}
            </div>
          ) : (
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-white/10 text-xs uppercase tracking-wide text-slate-500">
                    <th className="px-4 py-2.5 text-left font-medium">Detail</th>
                    <th className="px-4 py-2.5 text-left font-medium">#</th>
                    <th className="px-3 py-2.5 text-left font-medium">Ticker</th>
                    <th className="px-3 py-2.5 text-left font-medium">Company</th>
                    <th className="px-3 py-2.5 text-left font-medium">Sector</th>
                    {selectedRun.mode === "thesis" && (
                      <>
                        <th className="px-3 py-2.5 text-left font-medium">
                          Relevance
                        </th>
                        <th className="px-3 py-2.5 text-left font-medium">
                          Combined
                        </th>
                      </>
                    )}
                    <th className="px-3 py-2.5 text-left font-medium">Score</th>
                    <th className="px-3 py-2.5 text-left font-medium">Grade</th>
                    <th className="px-3 py-2.5 text-left font-medium">Momentum</th>
                    <th className="px-3 py-2.5 text-left font-medium">Catalyst</th>
                    <th className="px-3 py-2.5 text-left font-medium">P/N/F</th>
                    <th className="px-3 py-2.5 text-left font-medium">Source</th>
                    <th className="px-3 py-2.5 text-left font-medium">Missing</th>
                    <th className="px-3 py-2.5 text-left font-medium">Review</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {candidates.map((c) => (
                    <Fragment key={c.id}>
                      <tr
                        className="cursor-pointer transition-colors hover:bg-white/5"
                        data-testid="candidate-row"
                        onClick={() =>
                          setExpandedId(expandedId === c.id ? null : c.id)
                        }
                        title="Open candidate detail"
                      >
                        <td className="px-4 py-3">
                          <button
                            type="button"
                            onClick={(e) => {
                              e.stopPropagation();
                              setExpandedId(expandedId === c.id ? null : c.id);
                            }}
                            className="whitespace-nowrap rounded-md border border-sky-400/30 bg-sky-500/10 px-2.5 py-1 text-xs font-medium text-sky-300 transition-colors hover:bg-sky-500/20"
                            data-testid="candidate-toggle"
                            aria-expanded={expandedId === c.id}
                          >
                            {expandedId === c.id ? "Close ▾" : "Detail ▸"}
                          </button>
                        </td>
                        <td className="px-4 py-3 text-xs text-slate-500">
                          {c.rank ?? "—"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 font-semibold text-slate-100">
                          {c.ticker}
                          {councilActionFor(c) && (
                            <span
                              data-testid="council-action"
                              className={`ml-2 rounded px-1.5 py-0.5 text-[10px] font-medium ${councilActionBadgeCls(
                                councilActionFor(c),
                              )}`}
                              title="Internal research action (LLM discovery council)"
                            >
                              {councilActionLabel(councilActionFor(c))}
                            </span>
                          )}
                        </td>
                        <td className="max-w-[10rem] px-3 py-3 text-xs text-slate-400">
                          <span className="line-clamp-1">{c.company_name ?? "—"}</span>
                        </td>
                        <td className="px-3 py-3 text-xs text-slate-400">
                          {c.sector ?? "—"}
                        </td>
                        {selectedRun.mode === "thesis" && (
                          <>
                            <td
                              className="px-3 py-3 font-mono text-xs text-slate-300"
                              data-testid="candidate-relevance"
                            >
                              {fmt(c.thesis_relevance_score, 0)}
                            </td>
                            <td
                              className="px-3 py-3 font-mono text-sm font-semibold text-slate-100"
                              data-testid="candidate-combined"
                            >
                              {fmt(c.combined_internal_score, 0)}
                            </td>
                          </>
                        )}
                        <td className="px-3 py-3 font-mono text-sm text-slate-200">
                          {fmt(c.candidate_score)}
                        </td>
                        <td className="px-3 py-3">
                          <StatusPill
                            label={c.candidate_score_grade ?? "—"}
                            color={gradeColor(c.candidate_score_grade)}
                          />
                        </td>
                        <td className="px-3 py-3 text-xs text-slate-400">
                          {c.momentum_label ?? "—"}
                        </td>
                        <td className="px-3 py-3 text-xs text-slate-400">
                          {c.catalyst_coverage_status ?? "—"}
                        </td>
                        <td className="whitespace-nowrap px-3 py-3 font-mono text-xs text-slate-400">
                          {c.press_release_event_count}/{c.news_event_count}/
                          {c.filing_event_count}
                        </td>
                        <td className="px-3 py-3 text-xs text-slate-400">
                          {c.source_quality ?? "—"}
                        </td>
                        <td className="px-3 py-3 text-xs text-slate-400">
                          {c.missing_info_count ?? "—"}
                        </td>
                        <td className="px-3 py-3">
                          {c.human_review_required && (
                            <StatusPill label="required" color="red" />
                          )}
                        </td>
                      </tr>
                      {expandedId === c.id && (
                        <tr>
                          <td
                            colSpan={selectedRun.mode === "thesis" ? 15 : 13}
                            className="bg-black/20 p-0"
                          >
                            <CandidateDetailPanel candidateId={c.id} />
                          </td>
                        </tr>
                      )}
                    </Fragment>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </GlassCard>
      )}
    </div>
  );
}
