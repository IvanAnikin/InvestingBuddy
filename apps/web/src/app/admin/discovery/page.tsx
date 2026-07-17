"use client";

import Link from "next/link";
import { Fragment, useEffect, useState } from "react";
import {
  createDiscoveryRun,
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
// Main page
// ---------------------------------------------------------------------------

export default function DiscoveryPage() {
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [loadingRuns, setLoadingRuns] = useState(true);
  const [runsError, setRunsError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  // Form state
  const [provider, setProvider] = useState("free_real");
  const [universeSource, setUniverseSource] =
    useState<"curated_seed" | "manual_tickers">("curated_seed");
  const [manualTickers, setManualTickers] = useState("AAPL, MSFT, NVDA");
  const [exchange, setExchange] = useState("US");
  const [lookbackDays, setLookbackDays] = useState("90");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [startedMsg, setStartedMsg] = useState<string | null>(null);

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
        <p className="mb-4 text-sm font-semibold text-slate-200">
          Start a new discovery run
        </p>
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
                  Sort: score
                </option>
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
                    <th className="px-4 py-2.5 text-left font-medium">#</th>
                    <th className="px-3 py-2.5 text-left font-medium">Ticker</th>
                    <th className="px-3 py-2.5 text-left font-medium">Company</th>
                    <th className="px-3 py-2.5 text-left font-medium">Sector</th>
                    <th className="px-3 py-2.5 text-left font-medium">Score</th>
                    <th className="px-3 py-2.5 text-left font-medium">Grade</th>
                    <th className="px-3 py-2.5 text-left font-medium">Momentum</th>
                    <th className="px-3 py-2.5 text-left font-medium">Catalyst</th>
                    <th className="px-3 py-2.5 text-left font-medium">P/N/F</th>
                    <th className="px-3 py-2.5 text-left font-medium">Source</th>
                    <th className="px-3 py-2.5 text-left font-medium">Missing</th>
                    <th className="px-3 py-2.5 text-left font-medium">Review</th>
                    <th className="px-3 py-2.5 text-left font-medium"></th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-white/5">
                  {candidates.map((c) => (
                    <Fragment key={c.id}>
                      <tr
                        className="transition-colors hover:bg-white/5"
                        data-testid="candidate-row"
                      >
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
                        <td className="whitespace-nowrap px-3 py-3">
                          <button
                            onClick={() =>
                              setExpandedId(expandedId === c.id ? null : c.id)
                            }
                            className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
                            data-testid="candidate-toggle"
                          >
                            {expandedId === c.id ? "Close" : "Detail"}
                          </button>
                        </td>
                      </tr>
                      {expandedId === c.id && (
                        <tr>
                          <td colSpan={13} className="bg-black/20 p-0">
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
