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

const DISCLAIMER =
  "INTERNAL ADMIN USE ONLY. NOT INVESTMENT ADVICE. HISTORICAL EVALUATION ONLY. " +
  "No BUY/SELL/HOLD/WATCH recommendations are produced. " +
  "No price targets, fair values, or upside percentages are produced. " +
  "Human review required before any action.";

type BadgeColor = "gray" | "amber" | "green" | "red" | "blue" | "purple";

function Badge({ label, color }: { label: string; color: BadgeColor }) {
  const styles: Record<BadgeColor, string> = {
    gray: "bg-gray-100 text-gray-700",
    amber: "bg-amber-100 text-amber-800",
    green: "bg-green-100 text-green-800",
    red: "bg-red-100 text-red-800",
    blue: "bg-blue-100 text-blue-800",
    purple: "bg-purple-100 text-purple-800",
  };
  return (
    <span
      className={`inline-block px-2 py-0.5 rounded text-xs font-semibold ${styles[color]}`}
    >
      {label}
    </span>
  );
}

function runStatusColor(status: string): BadgeColor {
  if (status === "completed") return "green";
  if (status === "running") return "blue";
  if (status === "failed") return "red";
  if (status === "pending") return "amber";
  return "gray";
}

function MetaRow({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex gap-2 text-sm">
      <span className="text-gray-500 w-48 shrink-0">{label}</span>
      <span className="text-gray-800 font-mono text-xs break-all">{value}</span>
    </div>
  );
}

function JsonBlock({ label, data }: { label: string; data: unknown }) {
  if (data == null) return null;
  return (
    <div>
      <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-1">
        {label}
      </p>
      <pre className="whitespace-pre-wrap text-xs text-gray-700 font-mono bg-gray-50 rounded p-3 overflow-auto max-h-60 border border-gray-100">
        {JSON.stringify(data, null, 2)}
      </pre>
    </div>
  );
}

function ResultCard({ result }: { result: BacktestResultResponse }) {
  return (
    <div className="rounded-lg border border-gray-200 bg-white p-4 shadow-sm space-y-3">
      <div className="flex flex-wrap items-center gap-2">
        <Badge label={result.status} color={runStatusColor(result.status)} />
        {result.ticker && (
          <span className="text-xs font-mono text-gray-700">
            {result.ticker}
            {result.exchange ? `.${result.exchange}` : ""}
          </span>
        )}
        {result.report_id && (
          <span className="text-xs text-gray-400 font-mono truncate">
            report: {result.report_id}
          </span>
        )}
      </div>

      <div className="space-y-1 text-xs text-gray-600">
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
          <p className="text-xs font-semibold text-amber-700 mb-1">Warnings</p>
          <ul className="text-xs text-amber-700 list-disc list-inside space-y-0.5">
            {result.warnings_json.map((w, i) => (
              <li key={i}>{w}</li>
            ))}
          </ul>
        </div>
      )}
      {result.missing_data_json && result.missing_data_json.length > 0 && (
        <div>
          <p className="text-xs font-semibold text-gray-500 mb-1">
            Missing Data
          </p>
          <ul className="text-xs text-gray-500 list-disc list-inside space-y-0.5">
            {result.missing_data_json.map((m, i) => (
              <li key={i}>{m}</li>
            ))}
          </ul>
        </div>
      )}
    </div>
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
      <div className="flex items-center justify-center py-16 text-gray-400 text-sm">
        Loading backtest run…
      </div>
    );
  }

  if (error ?? !run) {
    return (
      <div className="space-y-4">
        <Link
          href="/admin/backtesting"
          className="text-sm text-gray-400 hover:text-gray-700"
        >
          ← All Runs
        </Link>
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-800">
            {error ?? "Backtest run not found."}
          </p>
        </div>
      </div>
    );
  }

  return (
    <div className="max-w-4xl space-y-6">
      <Link
        href="/admin/backtesting"
        className="text-sm text-gray-400 hover:text-gray-700"
      >
        ← All Runs
      </Link>

      <div className="flex flex-wrap gap-2">
        <Badge label="Internal Admin Only" color="red" />
        <Badge label="Not Investment Advice" color="red" />
        <Badge label="Historical Evaluation Only" color="red" />
        <Badge
          label={`Status: ${run.status}`}
          color={runStatusColor(run.status)}
        />
      </div>

      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="text-xs font-semibold text-red-800 mb-1">
          Internal Admin Use Only — Historical Evaluation Only
        </p>
        <p className="text-xs text-red-700">{DISCLAIMER}</p>
      </div>

      <div>
        <h1 className="text-2xl font-bold text-gray-900">{run.name}</h1>
        {run.description && (
          <p className="text-sm text-gray-600 mt-2">{run.description}</p>
        )}
      </div>

      {/* Metadata card */}
      <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm space-y-2">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
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
      </div>

      {/* Summary card */}
      {summary && (
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm space-y-2">
          <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide mb-3">
            Summary
          </p>
          <div className="grid grid-cols-2 sm:grid-cols-4 gap-3">
            <div className="rounded bg-gray-50 p-3 text-center border border-gray-100">
              <p className="text-2xl font-bold text-gray-900">
                {summary.total_results}
              </p>
              <p className="text-xs text-gray-500 mt-0.5">Total</p>
            </div>
            <div className="rounded bg-green-50 p-3 text-center border border-green-100">
              <p className="text-2xl font-bold text-green-800">
                {summary.completed_results}
              </p>
              <p className="text-xs text-green-700 mt-0.5">Completed</p>
            </div>
            <div className="rounded bg-red-50 p-3 text-center border border-red-100">
              <p className="text-2xl font-bold text-red-800">
                {summary.failed_results}
              </p>
              <p className="text-xs text-red-700 mt-0.5">Failed</p>
            </div>
            <div className="rounded bg-blue-50 p-3 text-center border border-blue-100">
              <p className="text-2xl font-bold text-blue-800">
                {summary.avg_judge_score != null
                  ? summary.avg_judge_score.toFixed(2)
                  : "—"}
              </p>
              <p className="text-xs text-blue-700 mt-0.5">Avg Judge Score</p>
            </div>
          </div>

          {Object.keys(summary.status_breakdown).length > 0 && (
            <div className="pt-2">
              <p className="text-xs text-gray-500 mb-2">Status breakdown</p>
              <div className="flex flex-wrap gap-2">
                {Object.entries(summary.status_breakdown).map(
                  ([status, count]) => (
                    <span
                      key={status}
                      className="text-xs bg-gray-100 text-gray-700 px-2 py-0.5 rounded font-mono"
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
              <p className="text-xs font-semibold text-amber-700 mb-1">
                Summary Warnings
              </p>
              <ul className="text-xs text-amber-700 list-disc list-inside space-y-0.5">
                {summary.warnings.map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}
        </div>
      )}

      {/* Run parameters JSON */}
      {run.parameters_json && Object.keys(run.parameters_json).length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <JsonBlock label="Parameters" data={run.parameters_json} />
        </div>
      )}

      {/* Summary JSON from run */}
      {run.summary_json && (
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <JsonBlock label="Run Summary JSON" data={run.summary_json} />
        </div>
      )}

      {/* Actions */}
      <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm space-y-3">
        <p className="text-xs font-semibold text-gray-500 uppercase tracking-wide">
          Actions
        </p>

        <div className="flex flex-wrap gap-3 items-center">
          <button
            onClick={handleEvaluate}
            disabled={evaluating}
            data-testid="evaluate-run-btn"
            className="bg-blue-700 text-white rounded-md px-4 py-2 text-sm font-semibold hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {evaluating ? "Evaluating…" : "Evaluate Run"}
          </button>

          <button
            onClick={refresh}
            disabled={loading}
            className="bg-white text-gray-700 border border-gray-300 rounded-md px-4 py-2 text-sm font-semibold hover:bg-gray-50 disabled:opacity-50 transition-colors"
          >
            Refresh Results
          </button>
        </div>

        <p className="text-xs text-gray-400">
          Evaluate runs the mock historical outcome provider and judge scoring
          for all associated reports. No live market data is used.
        </p>

        {evaluateError && (
          <div className="rounded border border-red-200 bg-red-50 p-3">
            <p className="text-xs text-red-700">
              <strong>Evaluate error:</strong> {evaluateError}
            </p>
          </div>
        )}
      </div>

      {/* Results */}
      <div className="space-y-4">
        <p className="text-sm font-semibold text-gray-700">
          Results ({results.length})
        </p>

        {results.length === 0 ? (
          <div className="rounded-lg border border-gray-200 bg-white p-6 text-center text-gray-400 text-sm">
            No results yet. Add reports to this run and then evaluate.
          </div>
        ) : (
          results.map((result) => (
            <ResultCard key={result.id} result={result} />
          ))
        )}
      </div>
    </div>
  );
}
