"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import {
  createDiscoveryRun,
  createThesisDiscoveryRun,
  getDiscoveryCandidate,
  getDiscoveryRun,
  listDiscoveryCandidates,
  listDiscoveryRuns,
  runCandidateAnalysis,
} from "@/lib/api";
import type {
  DiscoveryCandidate,
  DiscoveryCandidateDetail,
  DiscoveryRun,
  DiscoveryRunCreate,
  ThesisDiscoveryRunCreate,
} from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";

// Mirrors the backend DISCOVERY_MAX_UNIVERSE_SIZE default (client-side preview
// only — the server always enforces the real limit).
const CLIENT_MAX_UNIVERSE = 15;

// Phase 25.1 — runs are processed in the background; the UI polls run status
// until it reaches a terminal state.
const POLL_INTERVAL_MS = 3000;
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
  const [analysing, setAnalysing] = useState(false);
  const [analysisMsg, setAnalysisMsg] = useState<string | null>(null);
  const [reportId, setReportId] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    getDiscoveryCandidate(candidateId)
      .then((d) => {
        if (!cancelled) {
          setDetail(d);
          setReportId(d.analysis_report_id);
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

  async function handleRunAnalysis() {
    setAnalysing(true);
    setAnalysisMsg(null);
    try {
      const res = await runCandidateAnalysis(candidateId);
      setReportId(res.analysis_report_id);
      setAnalysisMsg(res.message);
    } catch (e) {
      setAnalysisMsg(e instanceof Error ? e.message : "Analysis failed.");
    } finally {
      setAnalysing(false);
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
      <GlassCard className="space-y-2 p-4">
        <p className="mb-1 text-sm font-semibold text-slate-200">
          Score breakdown — internal prioritization only
        </p>
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
            Fundamentals (SEC / derived)
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

      {/* Run Full Analysis */}
      <div className="flex flex-wrap items-center gap-3 border-t border-white/10 pt-3">
        <button
          onClick={handleRunAnalysis}
          disabled={analysing}
          className="rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50"
        >
          {analysing ? "Running full analysis…" : "Run Full Analysis"}
        </button>
        {reportId && (
          <Link
            href={`/admin/reports/${reportId}`}
            className="text-sm text-sky-400 hover:text-sky-300 hover:underline"
          >
            View generated report →
          </Link>
        )}
        {analysisMsg && <p className="text-xs text-slate-400">{analysisMsg}</p>}
      </div>
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

  // Selected run + live detail (polled while processing) + candidates
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<DiscoveryRun | null>(null);
  const [candTick, setCandTick] = useState(0);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [loadingCandidates, setLoadingCandidates] = useState(false);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [sort, setSort] = useState("candidate_score");

  const parsedTickers = manualTickers
    .split(",")
    .map((t) => t.trim().toUpperCase())
    .filter(Boolean);
  const manualCount = new Set(parsedTickers).size;

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
    const payload: ThesisDiscoveryRunCreate = {
      thesis_text: thesisText.trim(),
      region: thesisRegion.trim() || undefined,
      country: thesisCountry.trim() || undefined,
      sector: thesisSector.trim() || undefined,
      max_universe_size: parseInt(thesisMaxUniverse, 10) || 25,
      max_candidates: parseInt(thesisMaxCandidates, 10) || 10,
      lookback_days: parseInt(thesisLookback, 10) || 90,
      provider_name: "free_real",
    };
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

            <div className="grid grid-cols-1 gap-3 sm:grid-cols-3">
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Region (optional)
                </label>
                <input
                  className={inputCls}
                  value={thesisRegion}
                  onChange={(e) => setThesisRegion(e.target.value)}
                  placeholder="Europe"
                  maxLength={100}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Country (optional)
                </label>
                <input
                  className={inputCls}
                  value={thesisCountry}
                  onChange={(e) => setThesisCountry(e.target.value)}
                  placeholder="Germany"
                  maxLength={100}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-slate-300">
                  Sector (optional)
                </label>
                <input
                  className={inputCls}
                  value={thesisSector}
                  onChange={(e) => setThesisSector(e.target.value)}
                  placeholder="Industrials"
                  maxLength={100}
                />
              </div>
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
                A bounded real-company universe (max{" "}
                <span className="text-slate-200">{thesisMaxUniverse}</span>) is
                generated from a curated reference registry, then scanned with the
                free real-data stack. Every result is an internal research
                candidate — human review required. No public output is produced.
              </p>
            </div>

            {submitError && (
              <SafetyBanner variant="danger">
                <p data-testid="thesis-submit-error">
                  <strong>Cannot start run:</strong> {submitError}
                </p>
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
            {selectedRun.warnings && selectedRun.warnings.length > 0 && (
              <details className="text-xs text-amber-300/80">
                <summary className="cursor-pointer">
                  {selectedRun.warnings.length} warning(s)
                </summary>
                <ul className="mt-1 list-inside list-disc break-words text-slate-400">
                  {selectedRun.warnings.slice(0, 12).map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </details>
            )}
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
