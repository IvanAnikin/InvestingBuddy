"use client";

import { useState } from "react";
import { runAnalysis } from "@/lib/api";
import type { WorkflowRunResponse } from "@/lib/api";

export default function RunAnalysisPage() {
  const [ticker, setTicker] = useState("");
  const [exchange, setExchange] = useState("");
  const [provider, setProvider] = useState("mock");
  const [useLlm, setUseLlm] = useState(false);
  const [requireSchemaValid, setRequireSchemaValid] = useState(false);
  const [status, setStatus] = useState<
    "idle" | "submitting" | "success" | "error"
  >("idle");
  const [result, setResult] = useState<WorkflowRunResponse | null>(null);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setStatus("submitting");
    setErrorMsg(null);
    try {
      const res = await runAnalysis(ticker.trim(), exchange.trim());
      setResult(res);
      setStatus("success");
    } catch (err) {
      setStatus("error");
      setErrorMsg(err instanceof Error ? err.message : String(err));
    }
  }

  return (
    <div>
      <h1 className="text-2xl font-bold text-gray-900 mb-1">Run Analysis</h1>
      <p className="text-sm text-red-700 font-semibold mb-6">
        INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE — HUMAN REVIEW REQUIRED
      </p>

      <form
        onSubmit={handleSubmit}
        className="max-w-lg space-y-4"
        data-testid="run-analysis-form"
      >
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Ticker *
          </label>
          <input
            value={ticker}
            onChange={(e) => setTicker(e.target.value)}
            required
            placeholder="e.g. IBTEST"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-analysis-ticker"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Exchange *
          </label>
          <input
            value={exchange}
            onChange={(e) => setExchange(e.target.value)}
            required
            placeholder="e.g. MOCK"
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="input-analysis-exchange"
          />
        </div>
        <div>
          <label className="block text-sm font-medium text-gray-700 mb-1">
            Provider
          </label>
          <select
            value={provider}
            onChange={(e) => setProvider(e.target.value)}
            className="w-full rounded border border-gray-300 px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
            data-testid="select-provider"
          >
            <option value="mock">mock (no external API)</option>
            <option value="eodhd">eodhd (requires API key)</option>
          </select>
        </div>
        <div className="flex items-center gap-2">
          <input
            id="use-llm"
            type="checkbox"
            checked={useLlm}
            onChange={(e) => setUseLlm(e.target.checked)}
            className="rounded border-gray-300"
            data-testid="checkbox-use-llm"
          />
          <label htmlFor="use-llm" className="text-sm text-gray-700">
            Use LLM (requires Azure OpenAI)
          </label>
        </div>
        <div className="flex items-center gap-2">
          <input
            id="require-schema-valid"
            type="checkbox"
            checked={requireSchemaValid}
            onChange={(e) => setRequireSchemaValid(e.target.checked)}
            className="rounded border-gray-300"
            data-testid="checkbox-require-schema"
          />
          <label htmlFor="require-schema-valid" className="text-sm text-gray-700">
            Require schema validation
          </label>
        </div>

        <button
          type="submit"
          disabled={status === "submitting"}
          className="rounded bg-blue-600 px-6 py-2 text-sm text-white hover:bg-blue-700 disabled:opacity-50"
          data-testid="btn-run-analysis"
        >
          {status === "submitting" ? "Running…" : "Run Analysis"}
        </button>
      </form>

      {/* Results */}
      {status === "success" && result && (
        <div
          className="mt-6 rounded border border-green-200 bg-green-50 p-4 text-sm"
          data-testid="analysis-success"
        >
          <p className="font-semibold text-green-700 mb-2">
            ✓ Analysis completed
          </p>
          <p className="text-gray-700">
            Agent run: <code data-testid="result-agent-run-id">{result.agent_run_id}</code>
          </p>
          {result.draft_report_id && (
            <p className="text-gray-700 mt-1">
              Draft report:{" "}
              <a
                href={`/admin/reports/${result.draft_report_id}`}
                className="text-blue-600 underline"
                data-testid="result-report-link"
              >
                {result.draft_report_id}
              </a>
            </p>
          )}
          <p className="text-gray-700 mt-1">Status: {result.status}</p>
          <p className="text-gray-600 mt-1 italic">{result.summary}</p>
        </div>
      )}
      {status === "error" && (
        <div
          className="mt-6 rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700"
          data-testid="analysis-error"
        >
          ✗ Error: {errorMsg}
        </div>
      )}
    </div>
  );
}
