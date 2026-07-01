"use client";

import Link from "next/link";
import { useState } from "react";
import { runAnalysis } from "@/lib/api";
import type {
  CommitteeChairSummary,
  QualityGateStatus,
  RiskSummary,
  ValuationGuardSummary,
  WorkflowRunResponse,
} from "@/types/api";

const PROVIDERS = ["mock", "stooq", "gleif", "sec_edgar", "eodhd"];
const LLM_PROVIDERS = ["mock", "azure_openai"];

const inputCls =
  "border border-gray-300 rounded-md px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent w-full";

function Badge({
  label,
  color,
}: {
  label: string;
  color: "green" | "red" | "amber" | "gray" | "blue" | "purple";
}) {
  const styles: Record<string, string> = {
    green: "bg-green-100 text-green-800",
    red: "bg-red-100 text-red-800",
    amber: "bg-amber-100 text-amber-800",
    gray: "bg-gray-100 text-gray-700",
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

function SummaryRow({
  label,
  value,
}: {
  label: string;
  value: React.ReactNode;
}) {
  return (
    <div className="flex items-start gap-2 text-sm">
      <span className="text-gray-500 w-44 shrink-0">{label}</span>
      <span className="text-gray-900">{value}</span>
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
    <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
      {items.map(({ label, ok }) => (
        <div
          key={label}
          className={`rounded px-3 py-2 text-xs font-medium ${
            ok
              ? "bg-green-50 text-green-800 border border-green-200"
              : "bg-red-50 text-red-800 border border-red-200"
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
    <div className="text-xs text-gray-700 space-y-1">
      <p>{chair.committee_summary}</p>
      <div className="flex flex-wrap gap-2 mt-2">
        <Badge label={`Balance: ${chair.bull_bear_balance}`} color="gray" />
        <Badge
          label={`Internal: ${chair.provisional_internal_status}`}
          color="amber"
        />
        {chair.human_review_required && (
          <Badge label="Human review required" color="red" />
        )}
      </div>
      <p className="text-gray-500 mt-1">
        {chair.open_questions_count} open questions ·{" "}
        {chair.research_next_steps_count} next steps
      </p>
    </div>
  );
}

function RiskBlock({ risk }: { risk: RiskSummary }) {
  return (
    <div className="text-xs text-gray-700 space-y-1">
      <p>{risk.risk_summary}</p>
      <div className="flex flex-wrap gap-2 mt-1">
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
            className="bg-gray-100 px-2 py-0.5 rounded text-gray-700"
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
    <div className="text-xs text-gray-700 space-y-1">
      <p>
        Readiness:{" "}
        <strong
          className={
            val.valuation_readiness === "ready"
              ? "text-green-700"
              : "text-red-700"
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

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    setResult(null);

    try {
      const res = await runAnalysis({
        ticker: ticker.trim().toUpperCase() || undefined,
        exchange: exchange.trim().toUpperCase() || undefined,
        provider_name: providerName,
        use_llm: useLlm,
        llm_provider: useLlm ? llmProvider : undefined,
        require_schema_valid: requireSchemaValid,
      });
      setResult(res);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Unexpected error");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div className="max-w-2xl space-y-6">
      <div>
        <h1 className="text-xl font-bold text-gray-900">Run Analysis</h1>
        <p className="text-sm text-gray-500 mt-1">
          Trigger the 19-node company analysis workflow. Output is an admin
          draft only — not investment advice.
        </p>
      </div>

      {/* Form */}
      <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
        <form onSubmit={handleSubmit} className="space-y-4">
          <div className="grid grid-cols-2 gap-3">
            <div className="flex flex-col gap-1">
              <label className="text-sm font-medium text-gray-700">
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
              <label className="text-sm font-medium text-gray-700">
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
            <label className="text-sm font-medium text-gray-700">
              Data Provider
            </label>
            <select
              className={inputCls}
              value={providerName}
              onChange={(e) => setProviderName(e.target.value)}
            >
              {PROVIDERS.map((p) => (
                <option key={p} value={p}>
                  {p}
                  {p === "mock" ? " (offline / CI-safe)" : ""}
                </option>
              ))}
            </select>
          </div>

          {/* Advanced options */}
          <div className="rounded-md border border-gray-100 bg-gray-50 p-3 space-y-3">
            <p className="text-xs font-semibold text-gray-600 uppercase tracking-wide">
              Advanced Options
            </p>

            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={useLlm}
                onChange={(e) => setUseLlm(e.target.checked)}
                className="rounded"
              />
              Use LLM research sections (optional; requires Azure OpenAI if
              non-mock)
            </label>

            {useLlm && (
              <div className="flex flex-col gap-1 ml-5">
                <label className="text-xs font-medium text-gray-600">
                  LLM Provider
                </label>
                <select
                  className={inputCls}
                  value={llmProvider}
                  onChange={(e) => setLlmProvider(e.target.value)}
                >
                  {LLM_PROVIDERS.map((p) => (
                    <option key={p} value={p}>
                      {p}
                    </option>
                  ))}
                </select>
              </div>
            )}

            <label className="flex items-center gap-2 text-sm text-gray-700 cursor-pointer">
              <input
                type="checkbox"
                checked={requireSchemaValid}
                onChange={(e) => setRequireSchemaValid(e.target.checked)}
                className="rounded"
              />
              Require schema valid (fail workflow if schema draft is invalid)
            </label>
          </div>

          <button
            type="submit"
            disabled={submitting}
            className="w-full bg-blue-700 text-white rounded-md px-4 py-2 text-sm font-semibold hover:bg-blue-800 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
          >
            {submitting ? "Running analysis…" : "Run Analysis"}
          </button>
        </form>
      </div>

      {/* Error */}
      {error && (
        <div className="rounded-lg border border-red-200 bg-red-50 p-4">
          <p className="text-sm text-red-700">
            <strong>Error:</strong> {error}
          </p>
        </div>
      )}

      {/* Result */}
      {result && (
        <div className="space-y-4">
          {/* Result header */}
          <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm space-y-3">
            <div className="flex flex-wrap gap-2">
              <Badge
                label={result.status}
                color={result.status === "completed" ? "green" : "red"}
              />
              <Badge label="Admin Draft Only" color="red" />
              <Badge label="Not Investment Advice" color="red" />
              {result.is_mock && <Badge label="Mock Data" color="amber" />}
              {result.llm_used ? (
                <Badge label="LLM Used" color="purple" />
              ) : (
                <Badge label="LLM Not Used" color="gray" />
              )}
              {result.schema_valid ? (
                <Badge label="Schema Valid" color="green" />
              ) : (
                <Badge label="Schema Invalid" color="amber" />
              )}
              {result.human_review_required && (
                <Badge label="Human Review Required" color="red" />
              )}
            </div>

            <p className="text-sm text-gray-700">{result.summary}</p>

            <div className="space-y-1.5">
              <SummaryRow label="Company" value={result.company_name ?? "—"} />
              <SummaryRow label="Ticker" value={result.ticker ?? "—"} />
              <SummaryRow label="Provider" value={result.provider_name ?? "—"} />
              <SummaryRow
                label="Internal status"
                value={
                  result.provisional_internal_status ? (
                    <span className="font-mono text-xs bg-amber-100 text-amber-800 px-1.5 py-0.5 rounded">
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
                      className="text-blue-600 hover:underline text-xs font-mono"
                    >
                      {result.draft_report_id} →
                    </Link>
                  }
                />
              )}
            </div>

            {result.provisional_internal_status && (
              <p className="text-xs text-gray-500 border-t border-gray-100 pt-2 mt-2">
                <strong>Note:</strong>{" "}
                <code className="font-mono">
                  {result.provisional_internal_status}
                </code>{" "}
                is an internal workflow status only — not a public
                recommendation.
              </p>
            )}
          </div>

          {/* Quality Gate */}
          {result.quality_gate_status && (
            <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <p className="text-sm font-semibold text-gray-800 mb-3">
                Quality Gate
              </p>
              <QualityGate gate={result.quality_gate_status} />
            </div>
          )}

          {/* Warnings */}
          {(result.research_team_warnings.length > 0 ||
            result.analysis_council_warnings.length > 0) && (
            <div className="rounded-lg border border-amber-200 bg-amber-50 p-4">
              <p className="text-xs font-semibold text-amber-800 mb-2">
                Workflow Warnings
              </p>
              <ul className="text-xs text-amber-700 space-y-0.5 list-disc list-inside">
                {[
                  ...result.research_team_warnings,
                  ...result.analysis_council_warnings,
                ].map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {/* Committee Chair */}
          {result.committee_chair_summary && (
            <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <p className="text-sm font-semibold text-gray-800 mb-3">
                Committee Chair Summary
              </p>
              <CommitteeBlock
                chair={result.committee_chair_summary as CommitteeChairSummary}
              />
            </div>
          )}

          {/* Bull / Bear */}
          {(result.bull_case_summary ?? result.bear_case_summary) && (
            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              {result.bull_case_summary && (
                <div className="rounded-lg border border-green-100 bg-white p-4 shadow-sm">
                  <p className="text-xs font-semibold text-green-800 mb-2">
                    Bull Case
                  </p>
                  <div className="text-xs text-gray-700 space-y-0.5">
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
                </div>
              )}
              {result.bear_case_summary && (
                <div className="rounded-lg border border-red-100 bg-white p-4 shadow-sm">
                  <p className="text-xs font-semibold text-red-800 mb-2">
                    Bear Case
                  </p>
                  <div className="text-xs text-gray-700 space-y-0.5">
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
                      {result.bear_case_summary.key_unknowns_count}{" "}
                      unknowns
                    </p>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Risk */}
          {result.risk_summary && (
            <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <p className="text-sm font-semibold text-gray-800 mb-3">
                Risk Summary
              </p>
              <RiskBlock risk={result.risk_summary as RiskSummary} />
            </div>
          )}

          {/* Valuation Guard */}
          {result.valuation_guard_summary && (
            <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <p className="text-sm font-semibold text-gray-800 mb-3">
                Valuation Guard
              </p>
              <ValuationBlock
                val={result.valuation_guard_summary as ValuationGuardSummary}
              />
              <p className="text-xs text-gray-500 mt-2">
                No price target or fair value estimate is produced by this
                platform.
              </p>
            </div>
          )}

          {/* Validation errors */}
          {result.validation_errors.length > 0 && (
            <div className="rounded-lg border border-gray-200 bg-white p-5 shadow-sm">
              <p className="text-xs font-semibold text-gray-700 mb-2">
                Schema Validation Errors
              </p>
              <ul className="text-xs text-red-700 space-y-0.5 list-disc list-inside">
                {result.validation_errors.map((e, i) => (
                  <li key={i} className="font-mono">
                    {e}
                  </li>
                ))}
              </ul>
            </div>
          )}

          {/* View full report */}
          {result.draft_report_id && (
            <div className="pt-2">
              <Link
                href={`/admin/reports/${result.draft_report_id}`}
                className="inline-block bg-gray-800 text-white rounded-md px-4 py-2 text-sm font-semibold hover:bg-gray-900 transition-colors"
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
