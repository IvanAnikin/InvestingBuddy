"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  createBacktestRun,
  listBacktestRuns,
} from "@/lib/api";
import type { BacktestRunCreate, BacktestRunResponse } from "@/types/api";

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

const inputCls =
  "border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent w-full";

export default function BacktestingPage() {
  const [runs, setRuns] = useState<BacktestRunResponse[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [fetchError, setFetchError] = useState<string | null>(null);
  const [refreshTick, setRefreshTick] = useState(0);

  // Create form state
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [horizonDays, setHorizonDays] = useState("90");
  const [benchmarkSymbol, setBenchmarkSymbol] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);
  const [lastCreated, setLastCreated] = useState<BacktestRunResponse | null>(
    null,
  );

  useEffect(() => {
    let cancelled = false;
    async function fetchRuns() {
      setLoading(true);
      setFetchError(null);
      try {
        const data = await listBacktestRuns();
        if (!cancelled) {
          setRuns(data.runs);
          setTotal(data.total);
        }
      } catch (err) {
        if (!cancelled) {
          setFetchError(
            err instanceof Error
              ? err.message
              : "Failed to load backtest runs.",
          );
        }
      } finally {
        if (!cancelled) setLoading(false);
      }
    }
    void fetchRuns();
    return () => {
      cancelled = true;
    };
  }, [refreshTick]);

  function refresh() {
    setRefreshTick((t) => t + 1);
  }

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setSubmitError(null);
    setLastCreated(null);

    const payload: BacktestRunCreate = {
      name: name.trim(),
      description: description.trim() || undefined,
      horizon_days: horizonDays ? parseInt(horizonDays, 10) : 90,
      benchmark_symbol: benchmarkSymbol.trim() || undefined,
      provider_name: "mock",
    };

    try {
      const run = await createBacktestRun(payload);
      setLastCreated(run);
      setName("");
      setDescription("");
      setHorizonDays("90");
      setBenchmarkSymbol("");
      setShowForm(false);
      refresh();
    } catch (err) {
      setSubmitError(
        err instanceof Error ? err.message : "Failed to create backtest run.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-xl font-bold text-gray-900">Backtesting</h1>
          <p className="text-sm text-gray-500 mt-1">
            Internal historical research quality evaluation. Admin use only.
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="bg-blue-700 text-white rounded-md px-3 py-1.5 text-sm font-semibold hover:bg-blue-800 transition-colors"
        >
          {showForm ? "Cancel" : "+ New Run"}
        </button>
      </div>

      {/* Disclaimer */}
      <div className="rounded-lg border border-red-200 bg-red-50 p-4">
        <p className="text-xs font-semibold text-red-800 mb-1">
          Internal Admin Use Only — Historical Evaluation Only
        </p>
        <p className="text-xs text-red-700">{DISCLAIMER}</p>
      </div>

      {/* Last created notification */}
      {lastCreated && (
        <div className="rounded-lg border border-green-200 bg-green-50 p-3 flex items-center justify-between">
          <p className="text-sm text-green-800">
            Created run:{" "}
            <Link
              href={`/admin/backtesting/${lastCreated.id}`}
              className="font-semibold hover:underline"
            >
              {lastCreated.name}
            </Link>
          </p>
          <button
            onClick={() => setLastCreated(null)}
            className="text-xs text-green-600 hover:text-green-800"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
          <p className="text-sm font-semibold text-gray-800 mb-4">
            Create Backtest Run
          </p>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">
                Name <span className="text-red-500">*</span>
              </label>
              <input
                className={inputCls}
                value={name}
                onChange={(e) => setName(e.target.value)}
                placeholder="e.g. Q1 2024 Research Quality Audit"
                required
                maxLength={500}
              />
            </div>

            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">
                Description{" "}
                <span className="text-gray-400 font-normal">(optional)</span>
              </label>
              <textarea
                className={inputCls}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                placeholder="Internal notes about this backtest run"
                rows={2}
                maxLength={2000}
              />
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">
                  Horizon (days)
                </label>
                <input
                  type="number"
                  className={inputCls}
                  value={horizonDays}
                  onChange={(e) => setHorizonDays(e.target.value)}
                  min={1}
                  max={3650}
                />
              </div>
              <div className="flex flex-col gap-1">
                <label className="text-sm font-medium text-gray-700">
                  Benchmark Symbol{" "}
                  <span className="text-gray-400 font-normal">(optional)</span>
                </label>
                <input
                  className={inputCls}
                  value={benchmarkSymbol}
                  onChange={(e) => setBenchmarkSymbol(e.target.value)}
                  placeholder="e.g. SPY"
                  maxLength={20}
                />
              </div>
            </div>

            {/* Provider — mock only in UI */}
            <div className="rounded-md border border-gray-100 bg-gray-50 p-3">
              <p className="text-xs font-semibold text-gray-600 mb-1">
                Data Provider
              </p>
              <p className="text-xs text-gray-500">
                <span className="font-mono bg-white border border-gray-200 rounded px-1.5 py-0.5 mr-1">
                  mock
                </span>
                Mock historical provider only — no live EODHD or market data.
              </p>
            </div>

            {submitError && (
              <div className="rounded border border-red-200 bg-red-50 p-3">
                <p className="text-xs text-red-700">
                  <strong>Error:</strong> {submitError}
                </p>
              </div>
            )}

            <button
              type="submit"
              disabled={submitting || !name.trim()}
              className="w-full bg-blue-700 text-white rounded-md px-4 py-2 text-sm font-semibold hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {submitting ? "Creating…" : "Create Backtest Run"}
            </button>
          </form>
        </div>
      )}

      {/* Fetch error */}
      {fetchError && (
        <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
          <p className="text-sm text-amber-800">
            Could not load backtest runs: {fetchError}
          </p>
          <button
            onClick={refresh}
            className="text-xs text-amber-700 underline mt-1"
          >
            Retry
          </button>
        </div>
      )}

      {/* Loading */}
      {loading && (
        <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-gray-400 text-sm">
          Loading backtest runs…
        </div>
      )}

      {/* Empty state */}
      {!loading && !fetchError && runs.length === 0 && (
        <div className="rounded-lg border border-gray-200 bg-white p-8 text-center text-gray-500">
          <p className="text-sm">No backtest runs yet.</p>
          <p className="text-xs mt-1 text-gray-400">
            Create a run above to begin internal historical evaluation.
          </p>
        </div>
      )}

      {/* Runs table */}
      {!loading && runs.length > 0 && (
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm overflow-hidden">
          <div className="px-5 py-3 border-b border-gray-100 flex items-center justify-between">
            <p className="text-sm text-gray-500">
              {total} run{total !== 1 ? "s" : ""}
            </p>
            <div className="flex items-center gap-2">
              <Badge label="Admin Only" color="red" />
              <button
                onClick={refresh}
                className="text-xs text-blue-600 hover:underline"
              >
                Refresh
              </button>
            </div>
          </div>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-xs text-gray-500 uppercase tracking-wide border-b border-gray-100">
                <th className="px-5 py-2 text-left font-medium">Name</th>
                <th className="px-3 py-2 text-left font-medium">Status</th>
                <th className="px-3 py-2 text-left font-medium">Horizon</th>
                <th className="px-3 py-2 text-left font-medium">Provider</th>
                <th className="px-3 py-2 text-left font-medium">Benchmark</th>
                <th className="px-3 py-2 text-left font-medium">Created</th>
                <th className="px-3 py-2 text-left font-medium">Completed</th>
                <th className="px-3 py-2 text-left font-medium"></th>
              </tr>
            </thead>
            <tbody className="divide-y divide-gray-100">
              {runs.map((run) => (
                <tr key={run.id} className="hover:bg-gray-50">
                  <td className="px-5 py-3 max-w-xs">
                    <Link
                      href={`/admin/backtesting/${run.id}`}
                      className="text-blue-700 hover:underline line-clamp-1 font-medium"
                    >
                      {run.name}
                    </Link>
                    {run.description && (
                      <p className="text-xs text-gray-400 mt-0.5 line-clamp-1">
                        {run.description}
                      </p>
                    )}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    <Badge
                      label={run.status}
                      color={runStatusColor(run.status)}
                    />
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-600 whitespace-nowrap">
                    {run.horizon_days != null ? `${run.horizon_days}d` : "—"}
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-600 whitespace-nowrap font-mono">
                    {run.provider_name}
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-600 whitespace-nowrap">
                    {run.benchmark_symbol ?? "—"}
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-400 whitespace-nowrap">
                    {new Date(run.created_at).toLocaleDateString()}
                  </td>
                  <td className="px-3 py-3 text-xs text-gray-400 whitespace-nowrap">
                    {run.completed_at
                      ? new Date(run.completed_at).toLocaleDateString()
                      : "—"}
                  </td>
                  <td className="px-3 py-3 whitespace-nowrap">
                    <Link
                      href={`/admin/backtesting/${run.id}`}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      View →
                    </Link>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
