"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import {
  createBacktestRun,
  listBacktestRuns,
} from "@/lib/api";
import type { BacktestRunCreate, BacktestRunResponse } from "@/types/api";
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

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40";

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
    <div className="ib-fade-up space-y-6">
      {/* Page header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold tracking-tight text-white">
            Backtesting
          </h1>
          <p className="mt-1 text-sm text-slate-400">
            Internal historical research quality evaluation. Admin use only.
          </p>
        </div>
        <button
          onClick={() => setShowForm((v) => !v)}
          className="rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-3.5 py-1.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5"
        >
          {showForm ? "Cancel" : "+ New Run"}
        </button>
      </div>

      {/* Disclaimer */}
      <SafetyBanner
        variant="danger"
        title="Internal Admin Use Only — Historical Evaluation Only"
      >
        <p>{DISCLAIMER}</p>
      </SafetyBanner>

      {/* Last created notification */}
      {lastCreated && (
        <div className="flex items-center justify-between rounded-xl border border-emerald-400/25 bg-emerald-500/[0.09] p-3">
          <p className="text-sm text-emerald-200">
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
            className="text-xs text-emerald-300 hover:text-emerald-100"
          >
            Dismiss
          </button>
        </div>
      )}

      {/* Create form */}
      {showForm && (
        <GlassCard className="p-5">
          <p className="mb-4 text-sm font-semibold text-slate-200">
            Create Backtest Run
          </p>
          <form onSubmit={handleCreate} className="space-y-4">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-slate-300">
                Name <span className="text-rose-400">*</span>
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
              <label className="text-sm font-medium text-slate-300">
                Description{" "}
                <span className="font-normal text-slate-500">(optional)</span>
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
                <label className="text-sm font-medium text-slate-300">
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
                <label className="text-sm font-medium text-slate-300">
                  Benchmark Symbol{" "}
                  <span className="font-normal text-slate-500">(optional)</span>
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
            <div className="rounded-xl border border-white/10 bg-white/[0.03] p-3">
              <p className="mb-1 text-xs font-semibold text-slate-400">
                Data Provider
              </p>
              <p className="text-xs text-slate-500">
                <span className="mr-1 rounded border border-white/15 bg-white/5 px-1.5 py-0.5 font-mono text-slate-300">
                  mock
                </span>
                Mock historical provider only — no live market data.
              </p>
            </div>

            {submitError && (
              <div className="rounded-lg border border-rose-400/25 bg-rose-500/[0.09] p-3">
                <p className="text-xs text-rose-200">
                  <strong>Error:</strong> {submitError}
                </p>
              </div>
            )}

            <button
              type="submit"
              disabled={submitting || !name.trim()}
              className="w-full rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
            >
              {submitting ? "Creating…" : "Create Backtest Run"}
            </button>
          </form>
        </GlassCard>
      )}

      {/* Fetch error */}
      {fetchError && (
        <SafetyBanner variant="warning">
          <p>Could not load backtest runs: {fetchError}</p>
          <button
            onClick={refresh}
            className="mt-1 text-xs text-amber-300 underline"
          >
            Retry
          </button>
        </SafetyBanner>
      )}

      {/* Loading */}
      {loading && (
        <GlassCard className="p-8 text-center text-sm text-slate-500">
          Loading backtest runs…
        </GlassCard>
      )}

      {/* Empty state */}
      {!loading && !fetchError && runs.length === 0 && (
        <GlassCard className="p-8 text-center text-slate-400">
          <p className="text-sm">No backtest runs yet.</p>
          <p className="mt-1 text-xs text-slate-500">
            Create a run above to begin internal historical evaluation.
          </p>
        </GlassCard>
      )}

      {/* Runs table */}
      {!loading && runs.length > 0 && (
        <GlassCard className="overflow-hidden">
          <div className="flex items-center justify-between border-b border-white/10 px-5 py-3">
            <p className="text-sm text-slate-400">
              {total} run{total !== 1 ? "s" : ""}
            </p>
            <div className="flex items-center gap-3">
              <StatusPill label="Admin Only" color="red" />
              <button
                onClick={refresh}
                className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
              >
                Refresh
              </button>
            </div>
          </div>
          <div className="overflow-x-auto">
            <table className="w-full text-sm">
              <thead>
                <tr className="border-b border-white/10 text-xs uppercase tracking-wide text-slate-500">
                  <th className="px-5 py-2.5 text-left font-medium">Name</th>
                  <th className="px-3 py-2.5 text-left font-medium">Status</th>
                  <th className="px-3 py-2.5 text-left font-medium">Horizon</th>
                  <th className="px-3 py-2.5 text-left font-medium">Provider</th>
                  <th className="px-3 py-2.5 text-left font-medium">
                    Benchmark
                  </th>
                  <th className="px-3 py-2.5 text-left font-medium">Created</th>
                  <th className="px-3 py-2.5 text-left font-medium">
                    Completed
                  </th>
                  <th className="px-3 py-2.5 text-left font-medium"></th>
                </tr>
              </thead>
              <tbody className="divide-y divide-white/5">
                {runs.map((run) => (
                  <tr
                    key={run.id}
                    className="transition-colors hover:bg-white/5"
                  >
                    <td className="max-w-xs px-5 py-3">
                      <Link
                        href={`/admin/backtesting/${run.id}`}
                        className="line-clamp-1 font-medium text-sky-300 hover:text-sky-200 hover:underline"
                      >
                        {run.name}
                      </Link>
                      {run.description && (
                        <p className="mt-0.5 line-clamp-1 text-xs text-slate-500">
                          {run.description}
                        </p>
                      )}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3">
                      <StatusPill
                        label={run.status}
                        color={runStatusColor(run.status)}
                      />
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-xs text-slate-400">
                      {run.horizon_days != null ? `${run.horizon_days}d` : "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 font-mono text-xs text-slate-400">
                      {run.provider_name}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-xs text-slate-400">
                      {run.benchmark_symbol ?? "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-xs text-slate-500">
                      {new Date(run.created_at).toLocaleDateString()}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3 text-xs text-slate-500">
                      {run.completed_at
                        ? new Date(run.completed_at).toLocaleDateString()
                        : "—"}
                    </td>
                    <td className="whitespace-nowrap px-3 py-3">
                      <Link
                        href={`/admin/backtesting/${run.id}`}
                        className="text-xs text-sky-400 hover:text-sky-300 hover:underline"
                      >
                        View →
                      </Link>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </GlassCard>
      )}
    </div>
  );
}
