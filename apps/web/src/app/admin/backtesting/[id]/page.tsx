"use client";

import Link from "next/link";
import { use, useEffect, useState } from "react";
import {
  evaluateBacktestRun,
  getBacktestRun,
  getBacktestSummary,
  listBacktestResults,
} from "@/lib/api";
import type {
  BacktestResultResponse,
  BacktestRunResponse,
  BacktestRunSummary,
} from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";

const DISCLAIMER =
  "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. HISTORICAL EVALUATION ONLY. " +
  "No BUY/SELL/HOLD/WATCH recommendations are produced. " +
  "No price targets, fair values, or upside percentages are produced. " +
  "Human review required before any action.";

function runStatusColor(status: string): PillColor {
  if (status === "completed") return "green";
  if (status === "running") return "blue";
  if (status === "failed") return "red";
  if (status === "pending") return "amber";
  return "gray";
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="w-48 shrink-0 text-slate-500">{label}</span>
      <span className="break-all font-mono text-xs text-slate-300">
        {value}
      </span>
    </div>
  );
}

function JsonBlock({ label, data }: { label: string; data: unknown }) {
  if (data == null) return null;
  return (
    <div>
      <p className="mb-1 text-xs font-semibold uppercase tracking-wide text-slate-500">
        {label}
      </p>
      <pre className="max-h-60 overflow-auto rounded-xl border border-white/10 bg-slate-950/50 p-3 font-mono text-xs whitespace-pre-wrap text-slate-300">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}

function ResultCard({ result }: { result: BacktestResultResponse }) {
  return (
    <GlassCard className="space-y-3 p-4">
      <div className="flex flex-wrap items-center gap-2">
        <StatusPill label={result.status} color={runStatusColor(result.status)} />
        {result.ticker && (
          <span className="font-mono text-xs text-slate-300">
            {result.ticker}
            {result.exchange ? `.${result.exchange}` : ""}
          </span>
        )}
        {result.report_id && (
          <span className="truncate font-mono text-xs text-slate-500">
            report: {result.report_id}
          </span>
        )}
      </div>

      <div className="space-y-1 text-xs text-slate-400">
        {result.evaluation_start_date && (
          <p>
            Period: {result.evaluation_start_date} →{" "}
            {result.evaluation_end_date ?? "?"}
          </p>
        )}
        {result.horizon_days != null && (
          <p>Horizon: {result.horizon_days} days</p>
        )}
        {result.benchmark_symbol && (
          <p>Benchmark: {result.benchmark_symbol}</p>
        )}
      </div>

      <JsonBlock label="Outcome" data={result.outcome_json} />
      <JsonBlock label="Judge Evaluation" data={result.judge_evaluation_json} />

      {result.warnings_json && result.warnings_json.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold text-amber-300">Warnings</p>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-amber-300">
            {result.warnings_json.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {result.missing_data_json && result.missing_data_json.length > 0 && (
        <div>
          <p className="mb-1 text-xs font-semibold text-slate-400">
            Missing Data
          </p>
          <ul className="list-inside list-disc space-y-0.5 text-xs text-slate-400">
            {result.missing_data_json.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}
    </GlassCard>
  );
}

export default function BacktestRunDetailPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);

  const [run, setRun] = useState<BacktestRunResponse | null>(null);
  const [results, setResults] = useState<BacktestResultResponse[]>([]);
  const [summary, setSummary] = useState<BacktestRunSummary | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  const [evaluating, setEvaluating] = useState(false);
  const [evaluateError, setEvaluateError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    async function fetchAll() {
      setLoading(true);
      setError(null);
      try {
        const [runData, resultsData] = await Promise.all([
          getBacktestRun(id),
          listBacktestResults(id).catch(() => ({
            results: [],
            total: 0,
            disclaimer: "",
          })),
        ]);
        if (!cancelled) {
          setRun(runData);
          setResults(resultsData.results);
        }
        try {
          const sumData = await getBacktestSummary(id);
          if (!cancelled) setSummary(sumData);
        } catch {
          if (!cancelled) setSummary(null);
        }
      } catch (err) {
        if (!cancelled) {
          setError(
            err instanceof Error
              ? err.message
              : "Failed to load backtest run.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void fetchAll();
    return () => {
      cancelled = true;
    };
  }, [id, refreshTick]);

  function refresh() {
    setRefreshTick((t) => t + 1);
  }

  async function handleEvaluate() {
    setEvaluating(true);
    setEvaluateError(null);
    try {
      await evaluateBacktestRun(id);
      refresh();
    } catch (err) {
      setEvaluateError(
        err instanceof Error ? err.message : "Evaluation failed.",
      );
    } finally {
      setEvaluating(false);
    }
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center py-16 text-sm text-slate-500">
        Loading backtest run…
      </div>
    );
  }

  if (error ?? !run) {
    return (
      <div className="space-y-4">
        <Link
          href="/admin/backtesting"
          className="text-sm text-slate-500 transition-colors hover:text-slate-200"
        >
          ← All Runs
        </Link>
        <SafetyBanner variant="danger">
          <p>{error ?? "Backtest run not found."}</p>
        </SafetyBanner>
      </div>
    );
  }

  return (
    <div className="ib-fade-up mx-auto max-w-4xl space-y-6">
      <Link
        href="/admin/backtesting"
        className="text-sm text-slate-500 transition-colors hover:text-slate-200"
      >
        ← All Runs
      </Link>

      <div className="flex flex-wrap gap-2">
        <StatusPill label="Internal Admin Only" color="red" />
        <StatusPill label="Not Investment Advice" color="red" />
        <StatusPill label="Historical Evaluation Only" color="red" />
        <StatusPill
          label={`Status: ${run.status}`}
          color={runStatusColor(run.status)}
        />
      </div>

      <SafetyBanner
        variant="danger"
        title="Internal Admin Use Only — Historical Evaluation Only"
      >
        <p>{DISCLAIMER}</p>
      </SafetyBanner>

      <div>
        <h1 className="text-2xl font-bold tracking-tight text-white">
          {run.name}
        </h1>
        {run.description && (
          <p className="mt-2 text-sm text-slate-400">{run.description}</p>
        )}
      </div>

      {/* Metadata card */}
      <GlassCard className="space-y-2 p-5">
        <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
          Run Parameters
        </p>
        <MetaRow label="Run ID" value={run.id} />
        <MetaRow label="Status" value={run.status} />
        <MetaRow label="Provider" value={run.provider_name} />
        {run.horizon_days != null && (
          <MetaRow label="Horizon (days)" value={String(run.horizon_days)} />
        )}
        {run.benchmark_symbol && (
          <MetaRow label="Benchmark Symbol" value={run.benchmark_symbol} />
        )}
        <MetaRow
          label="Created"
          value={new Date(run.created_at).toLocaleString()}
        />
        {run.started_at && (
          <MetaRow
            label="Started"
            value={new Date(run.started_at).toLocaleString()}
          />
        )}
        {run.completed_at && (
          <MetaRow
            label="Completed"
            value={new Date(run.completed_at).toLocaleString()}
          />
        )}
        {run.error_message && (
          <MetaRow label="Error" value={run.error_message} />
        )}
      </GlassCard>

      {/* Summary card */}
      {summary && (
        <GlassCard className="space-y-2 p-5">
          <p className="mb-3 text-xs font-semibold uppercase tracking-wide text-slate-500">
            Summary
          </p>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3 text-center">
              <p className="text-2xl font-bold text-white">
                {summary.total_results}
              </p>
              <p className="mt-0.5 text-xs text-slate-500">Total</p>
            </div>
            <div className="rounded-xl border border-emerald-400/20 bg-emerald-500/[0.08] p-3 text-center">
              <p className="text-2xl font-bold text-emerald-300">
                {summary.completed_results}
              </p>
              <p className="mt-0.5 text-xs text-emerald-300/80">Completed</p>
            </div>
            <div className="rounded-xl border border-rose-400/20 bg-rose-500/[0.08] p-3 text-center">
              <p className="text-2xl font-bold text-rose-300">
                {summary.failed_results}
              </p>
              <p className="mt-0.5 text-xs text-rose-300/80">Failed</p>
            </div>
            <div className="rounded-xl border border-sky-400/20 bg-sky-500/[0.08] p-3 text-center">
              <p className="text-2xl font-bold text-sky-300">
                {summary.avg_judge_score != null
                  ? summary.avg_judge_score.toFixed(2)
                  : "—"}
              </p>
              <p className="mt-0.5 text-xs text-sky-300/80">Avg Judge Score</p>
            </div>
          </div>

          {Object.keys(summary.status_breakdown).length > 0 && (
            <div className="pt-2">
              <p className="mb-2 text-xs text-slate-500">Status breakdown</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(summary.status_breakdown).map(
                  ([status, count]) => (
                    <span
                      key={status}
                      className="rounded bg-white/5 px-2 py-0.5 font-mono text-xs text-slate-300"
                    >
                      {status}: {count}
                    </span>
                  ),
                )}
              </div>
            </div>
          )}

          {summary.warnings.length > 0 && (
            <div className="pt-1">
              <p className="mb-1 text-xs font-semibold text-amber-300">
                Summary Warnings
              </p>
              <ul className="list-inside list-disc space-y-0.5 text-xs text-amber-300">
                {summary.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </GlassCard>
      )}

      {/* Run parameters JSON */}
      {run.parameters_json && Object.keys(run.parameters_json).length > 0 && (
        <GlassCard className="p-5">
          <JsonBlock label="Parameters" data={run.parameters_json} />
        </GlassCard>
      )}

      {/* Summary JSON from run */}
      {run.summary_json && (
        <GlassCard className="p-5">
          <JsonBlock label="Run Summary JSON" data={run.summary_json} />
        </GlassCard>
      )}

      {/* Actions */}
      <GlassCard className="space-y-3 p-5">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Actions
        </p>

        <div className="flex flex-wrap items-center gap-3">
          <button
            onClick={handleEvaluate}
            disabled={evaluating}
            data-testid="evaluate-run-btn"
            className="rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
          >
            {evaluating ? "Evaluating…" : "Evaluate Run"}
          </button>

          <button
            onClick={refresh}
            disabled={loading}
            className="rounded-lg border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-slate-100 transition-colors hover:bg-white/10 disabled:opacity-50"
          >
            Refresh Results
          </button>
        </div>

        <p className="text-xs text-slate-500">
          Evaluate runs the mock historical outcome provider and judge scoring
          for all associated reports. No live market data is used.
        </p>

        {evaluateError && (
          <div className="rounded-lg border border-rose-400/25 bg-rose-500/[0.09] p-3">
            <p className="text-xs text-rose-200">
              <strong>Evaluate error:</strong> {evaluateError}
            </p>
          </div>
        )}
      </GlassCard>

      {/* Results */}
      <div className="space-y-4">
        <p className="text-sm font-semibold text-slate-200">
          Results ({results.length})
        </p>

        {results.length === 0 ? (
          <GlassCard className="p-6 text-center text-sm text-slate-500">
            No results yet. Add reports to this run and then evaluate.
          </GlassCard>
        ) : (
          results.map((result) => (
            <ResultCard key={result.id} result={result} />
          ))
        )}
      </div>
    </div>
  );
}
