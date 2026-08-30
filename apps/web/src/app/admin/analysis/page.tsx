"use client";

import Link from "next/link";
import { useState } from "react";
import { runAnalysis } from "@/lib/api";
import {
  DATA_PROVIDERS,
  LLM_SECTION_PROVIDERS,
  buildCompanyAnalysisRequest,
  type DataProviderOption,
} from "@/lib/workflows";
import type {
  CommitteeChairSummary,
  QualityGateStatus,
  RiskSummary,
  ValuationGuardSummary,
  WorkflowRunResponse,
} from "@/types/api";
import GlassCard from "@/components/ui/GlassCard";
import SafetyBanner from "@/components/ui/SafetyBanner";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";

// Provider vocabulary is shared with the /research surface (src/lib/workflows.ts)
// so the two consoles can never offer different provider values for the same
// workflow. Only the LABEL differs: an operator recognises the identifier.
type ProviderTag = NonNullable<DataProviderOption["tag"]>;

const PROVIDER_TAG_STYLES: Record<
  ProviderTag,
  { label: string; color: PillColor }
> = {
  recommended: { label: "Recommended", color: "green" },
  paid: { label: "Paid / full provider", color: "red" },
  "price-only": { label: "Price only", color: "gray" },
  "us-only": { label: "U.S. only", color: "blue" },
  legacy: { label: "Legacy", color: "amber" },
  offline: { label: "Offline", color: "gray" },
};

const inputCls =
  "w-full rounded-lg border border-white/10 bg-white/5 px-3 py-2 text-sm text-slate-100 placeholder:text-slate-500 focus:border-sky-400/50 focus:outline-none focus:ring-2 focus:ring-sky-500/40";

function SummaryRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className="w-44 shrink-0 text-slate-500">{label}</span>
      <span className="text-slate-200">{value}</span>
    </div>
  );
}

function QualityGate({ gate }: { gate: QualityGateStatus }) {
  const items = [
    { label: "Source quality", ok: gate.source_quality_ok },
    { label: "Citation status", ok: gate.citation_status_ok },
    { label: "Schema valid", ok: gate.schema_valid },
    { label: "Valuation ready", ok: gate.valuation_ready },
    { label: "Research complete", ok: gate.research_complete },
  ];
  return (
    <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
      {items.map(({ label, ok }) => (
        <div
          key={label}
          className={`rounded-lg border px-3 py-2 text-xs font-medium ${
            ok
              ? "border-emerald-400/20 bg-emerald-500/10 text-emerald-300"
              : "border-rose-400/20 bg-rose-500/10 text-rose-300"
          }`}
        >
          {ok ? "✓" : "✗"} {label}
        </div>
      ))}
    </div>
  );
}

function CommitteeBlock({ chair }: { chair: CommitteeChairSummary }) {
  return (
    <div className="space-y-1 text-xs text-slate-300">
      <p>{chair.committee_summary}</p>
      <div className="mt-2 flex flex-wrap gap-2">
        <StatusPill label={`Balance: ${chair.bull_bear_balance}`} color="gray" />
        <StatusPill
          label={`Internal: ${chair.provisional_internal_status}`}
          color="amber"
        />
        {chair.human_review_required && (
          <StatusPill label="Human review required" color="red" />
        )}
      </div>
      <p className="mt-1 text-slate-500">
        {chair.open_questions_count} open questions ·{" "}
        {chair.research_next_steps_count} next steps
      </p>
    </div>
  );
}

function RiskBlock({ risk }: { risk: RiskSummary }) {
  return (
    <div className="space-y-1 text-xs text-slate-300">
      <p>{risk.risk_summary}</p>
      <div className="mt-1 flex flex-wrap gap-2">
        {(
          [
            ["Business", risk.business_risks_count],
            ["Financial", risk.financial_risks_count],
            ["Market", risk.market_risks_count],
            ["Data quality", risk.data_quality_risks_count],
            ["Source quality", risk.source_quality_risks_count],
          ] as [string, number][]
        ).map(([label, count]) => (
          <span
            key={label}
            className="rounded bg-white/5 px-2 py-0.5 text-slate-300"
          >
            {label}: {count}
          </span>
        ))}
      </div>
    </div>
  );
}

function ValuationBlock({ val }: { val: ValuationGuardSummary }) {
  return (
    <div className="space-y-1 text-xs text-slate-300">
      <p>
        Readiness:{" "}
        <strong
          className={
            val.valuation_readiness === "ready"
              ? "text-emerald-300"
              : "text-rose-300"
          }
        >
          {val.valuation_readiness}
        </strong>
      </p>
      <p>
        {val.blockers_count} blocker(s) · {val.available_inputs_count} inputs
        available · {val.missing_inputs_count} missing
      </p>
    </div>
  );
}

export default function AnalysisPage() {
  const [ticker, setTicker] = useState("");
  const [exchange, setExchange] = useState("");
  const [providerName, setProviderName] = useState("mock");
  const [useLlm, setUseLlm] = useState(false);
  const [llmProvider, setLlmProvider] = useState("mock");
  const [requireSchemaValid, setRequireSchemaValid] = useState(false);

  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<WorkflowRunResponse | null>(null);
  const [error, setError] = useState<string | null>(null);

  const selectedProvider = DATA_PROVIDERS.find((p) => p.value === providerName);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);

    try {
      const res = await runAnalysis(
        buildCompanyAnalysisRequest({
          ticker,
          exchange,
          providerName,
          useLlmSections: useLlm,
          llmProvider,
          requireSchemaValid,
        }),
      );
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="ib-fade-up max-w-2xl space-y-6">
      <div>
        <h1 className="text-2xl font-bold tracking-tight text-white">
          Run Analysis
        </h1>
        <p className="mt-1 text-sm text-slate-400">
          Trigger the 19-node company analysis workflow. Output is an admin
          draft only — not investment advice.
        </p>
      </div>

      {/* Form */}
      <GlassCard className="p-5">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-slate-300">
                Ticker
              </label>
              <input
                className={inputCls}
                value={ticker}
                onChange={(e) => setTicker(e.target.value)}
                placeholder="e.g. NOVO B"
                maxLength={20}
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
                placeholder="e.g. CPH"
                maxLength={20}
              />
            </div>
          </div>

          <div className="flex flex-col gap-1">
            <label className="text-sm font-medium text-slate-300">
              Data Provider
            </label>
            <select
              className={inputCls}
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
            >
              {DATA_PROVIDERS.map((p) => (
                <option key={p.value} value={p.value} className="bg-slate-900">
                  {p.adminLabel}
                </option>
              ))}
            </select>

            {/* Static guidance for the real-data stack */}
            <p className="mt-1 text-xs text-slate-500">
              Use{" "}
              <code className="rounded bg-white/10 px-1 font-mono text-slate-300">
                free_real
              </code>{" "}
              for the current free real-data workflow. It combines SEC EDGAR
              data, price data, and internal trend signals. The{" "}
              <code className="rounded bg-white/10 px-1 font-mono text-slate-300">
                eodhd
              </code>{" "}
              full provider requires paid Fundamentals access.
            </p>

            {/* Per-provider note for the current selection */}
            {selectedProvider && (
              <div className="mt-1.5 flex items-start gap-2">
                {selectedProvider.tag && (
                  <StatusPill
                    label={PROVIDER_TAG_STYLES[selectedProvider.tag].label}
                    color={PROVIDER_TAG_STYLES[selectedProvider.tag].color}
                  />
                )}
                {selectedProvider.note && (
                  <p className="text-xs text-slate-400">
                    {selectedProvider.note}
                  </p>
                )}
              </div>
            )}
          </div>

          {/* Advanced options */}
          <div className="space-y-3 rounded-xl border border-white/10 bg-white/[0.03] p-3">
            <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
              Advanced Options
            </p>

            <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={useLlm}
                onChange={(e) => setUseLlm(e.target.checked)}
                className="rounded accent-sky-500"
              />
              Use LLM research sections (optional; requires Azure OpenAI if
              non-mock)
            </label>

            {useLlm && (
              <div className="ml-5 flex flex-col gap-1">
                <label className="text-xs font-medium text-slate-400">
                  LLM Provider
                </label>
                <select
                  className={inputCls}
                  value={llmProvider}
                  onChange={(e) => setLlmProvider(e.target.value)}
                >
                  {LLM_SECTION_PROVIDERS.map((p) => (
                    <option key={p} value={p} className="bg-slate-900">
                      {p}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <label className="flex cursor-pointer items-center gap-2 text-sm text-slate-300">
              <input
                type="checkbox"
                checked={requireSchemaValid}
                onChange={(e) => setRequireSchemaValid(e.target.checked)}
                className="rounded accent-sky-500"
              />
              Require schema valid (fail workflow if schema draft is invalid)
            </label>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full rounded-lg bg-gradient-to-r from-sky-500 to-blue-600 px-4 py-2.5 text-sm font-semibold text-white shadow-lg shadow-sky-500/20 transition-all hover:-translate-y-0.5 hover:shadow-sky-500/30 disabled:cursor-not-allowed disabled:opacity-50 disabled:hover:translate-y-0"
          >
            {submitting ? "Running analysis…" : "Run Analysis"}
          </button>
        </form>
      </GlassCard>

      {/* Error */}
      {error && (
        <SafetyBanner variant="danger">
          <p>
            <strong>Error:</strong> {error}
          </p>
        </SafetyBanner>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-4">
          {/* Result header */}
          <GlassCard className="space-y-3 p-5">
            <div className="flex flex-wrap gap-2">
              <StatusPill
                label={result.status}
                color={result.status === "completed" ? "green" : "red"}
              />
              <StatusPill label="Admin Draft Only" color="red" />
              <StatusPill label="Not Investment Advice" color="red" />
              {result.is_mock && <StatusPill label="Mock Data" color="amber" />}
              {result.llm_used ? (
                <StatusPill label="LLM Used" color="purple" />
              ) : (
                <StatusPill label="LLM Not Used" color="gray" />
              )}
              {result.schema_valid ? (
                <StatusPill label="Schema Valid" color="green" />
              ) : (
                <StatusPill label="Schema Invalid" color="amber" />
              )}
              {result.human_review_required && (
                <StatusPill label="Human Review Required" color="red" />
              )}
            </div>

            <p className="text-sm text-slate-300">{result.summary}</p>

            <div className="space-y-1.5">
              <SummaryRow label="Company" value={result.company_name ?? "—"} />
              <SummaryRow label="Ticker" value={result.ticker ?? "—"} />
              <SummaryRow
                label="Provider"
                value={result.provider_name ?? "—"}
              />
              <SummaryRow
                label="Internal status"
                value={
                  result.provisional_internal_status ? (
                    <span className="rounded bg-amber-500/15 px-1.5 py-0.5 font-mono text-xs text-amber-300">
                      {result.provisional_internal_status}
                    </span>
                  ) : (
                    "—"
                  )
                }
              />
              {result.draft_report_id && (
                <SummaryRow
                  label="Draft Report"
                  value={
                    <Link
                      href={`/admin/reports/${result.draft_report_id}`}
                      className="font-mono text-xs text-sky-400 hover:text-sky-300 hover:underline"
                    >
                      {result.draft_report_id} →
                    </Link>
                  }
                />
              )}
            </div>

            {result.provisional_internal_status && (
              <p className="mt-2 border-t border-white/10 pt-2 text-xs text-slate-500">
                <strong>Note:</strong>{" "}
                <code className="rounded bg-white/10 px-1 font-mono text-slate-300">
                  {result.provisional_internal_status}
                </code>{" "}
                is an internal workflow status only — not a public
                recommendation.
              </p>
            )}
          </GlassCard>

          {/* Quality Gate */}
          {result.quality_gate_status && (
            <GlassCard className="p-5">
              <p className="mb-3 text-sm font-semibold text-slate-200">
                Quality Gate
              </p>
              <QualityGate gate={result.quality_gate_status} />
            </GlassCard>
          )}

          {/* Warnings */}
          {(result.research_team_warnings.length > 0 ||
            result.analysis_council_warnings.length > 0) && (
            <SafetyBanner variant="warning" title="Workflow Warnings">
              <ul className="list-inside list-disc space-y-0.5">
                {[
                  ...result.research_team_warnings,
                  ...result.analysis_council_warnings,
                ].map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </SafetyBanner>
          )}

          {/* Committee Chair */}
          {result.committee_chair_summary && (
            <GlassCard className="p-5">
              <p className="mb-3 text-sm font-semibold text-slate-200">
                Committee Chair Summary
              </p>
              <CommitteeBlock
                chair={result.committee_chair_summary as CommitteeChairSummary}
              />
            </GlassCard>
          )}

          {/* Bull / Bear */}
          {(result.bull_case_summary ?? result.bear_case_summary) && (
            <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
              {result.bull_case_summary && (
                <GlassCard className="border-emerald-400/15 p-4">
                  <p className="mb-2 text-xs font-semibold text-emerald-300">
                    Bull Case
                  </p>
                  <div className="space-y-0.5 text-xs text-slate-300">
                    <p>
                      Confidence:{" "}
                      <strong>
                        {result.bull_case_summary.confidence_level}
                      </strong>
                    </p>
                    <p>
                      {result.bull_case_summary.positive_thesis_points_count}{" "}
                      thesis points
                    </p>
                    <p>
                      {result.bull_case_summary.potential_tailwinds_count}{" "}
                      tailwinds
                    </p>
                  </div>
                </GlassCard>
              )}
              {result.bear_case_summary && (
                <GlassCard className="border-rose-400/15 p-4">
                  <p className="mb-2 text-xs font-semibold text-rose-300">
                    Bear Case
                  </p>
                  <div className="space-y-0.5 text-xs text-slate-300">
                    <p>
                      Confidence:{" "}
                      <strong>
                        {result.bear_case_summary.confidence_level}
                      </strong>
                    </p>
                    <p>
                      {result.bear_case_summary.negative_thesis_points_count}{" "}
                      thesis points
                    </p>
                    <p>
                      {result.bear_case_summary.key_unknowns_count} unknowns
                    </p>
                  </div>
                </GlassCard>
              )}
            </div>
          )}

          {/* Risk */}
          {result.risk_summary && (
            <GlassCard className="p-5">
              <p className="mb-3 text-sm font-semibold text-slate-200">
                Risk Summary
              </p>
              <RiskBlock risk={result.risk_summary as RiskSummary} />
            </GlassCard>
          )}

          {/* Valuation Guard */}
          {result.valuation_guard_summary && (
            <GlassCard className="p-5">
              <p className="mb-3 text-sm font-semibold text-slate-200">
                Valuation Guard
              </p>
              <ValuationBlock
                val={result.valuation_guard_summary as ValuationGuardSummary}
              />
              <p className="mt-2 text-xs text-slate-500">
                No price target or fair value estimate is produced by this
                platform.
              </p>
            </GlassCard>
          )}

          {/* Validation errors */}
          {result.validation_errors.length > 0 && (
            <GlassCard className="p-5">
              <p className="mb-2 text-xs font-semibold text-slate-400">
                Schema Validation Errors
              </p>
              <ul className="list-inside list-disc space-y-0.5 text-xs text-rose-300">
                {result.validation_errors.map((e, i) => (
                  <li key={i} className="font-mono">
                    {e}
                  </li>
                ))}
              </ul>
            </GlassCard>
          )}

          {/* View full report */}
          {result.draft_report_id && (
            <div className="pt-2">
              <Link
                href={`/admin/reports/${result.draft_report_id}`}
                className="inline-block rounded-lg border border-white/15 bg-white/5 px-4 py-2 text-sm font-semibold text-slate-100 transition-colors hover:bg-white/10"
              >
                View Full Draft Report →
              </Link>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
