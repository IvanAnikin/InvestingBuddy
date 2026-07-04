"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { generateFinalReport, getReport, validateReport } from "@/lib/api";
import type {
  GenerateFinalReportResponse,
  Report,
  ValidateReportResponse,
} from "@/lib/api";

export default function ReportDetailPage() {
  const params = useParams<{ id: string }>();
  const reportId = params.id;

  const [report, setReport] = useState<Report | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  // Final report actions
  const [generateStatus, setGenerateStatus] = useState<
    "idle" | "running" | "done" | "error"
  >("idle");
  const [generateResult, setGenerateResult] =
    useState<GenerateFinalReportResponse | null>(null);

  const [validateStatus, setValidateStatus] = useState<
    "idle" | "running" | "done" | "error"
  >("idle");
  const [validateResult, setValidateResult] =
    useState<ValidateReportResponse | null>(null);

  const [actionError, setActionError] = useState<string | null>(null);

  useEffect(() => {
    if (!reportId) return;
    getReport(reportId)
      .then(setReport)
      .catch((err) =>
        setError(err instanceof Error ? err.message : String(err)),
      )
      .finally(() => setLoading(false));
  }, [reportId]);

  async function handleGenerateFinal() {
    if (!reportId) return;
    setGenerateStatus("running");
    setActionError(null);
    try {
      const res = await generateFinalReport(reportId);
      setGenerateResult(res);
      setGenerateStatus("done");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
      setGenerateStatus("error");
    }
  }

  async function handleValidate() {
    if (!reportId) return;
    setValidateStatus("running");
    setActionError(null);
    try {
      const res = await validateReport(reportId);
      setValidateResult(res);
      setValidateStatus("done");
    } catch (err) {
      setActionError(err instanceof Error ? err.message : String(err));
      setValidateStatus("error");
    }
  }

  if (loading) {
    return (
      <p className="text-gray-500 text-sm" data-testid="report-loading">
        Loading report…
      </p>
    );
  }

  if (error || !report) {
    return (
      <div
        className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700"
        data-testid="report-error"
      >
        ✗ {error ?? "Report not found"}
      </div>
    );
  }

  return (
    <div>
      <div className="mb-6">
        <h1
          className="text-2xl font-bold text-gray-900 mb-1"
          data-testid="report-title"
        >
          {report.title}
        </h1>
        <p className="text-sm text-red-700 font-semibold mb-2">
          INTERNAL ADMIN ONLY — NOT INVESTMENT ADVICE — NOT FOR PUBLICATION —
          HUMAN REVIEW REQUIRED
        </p>
        <div className="flex gap-4 text-xs text-gray-400">
          <span data-testid="report-type">{report.report_type}</span>
          <span
            className="font-medium text-amber-600"
            data-testid="report-status"
          >
            {report.status}
          </span>
          <span>{new Date(report.created_at).toLocaleDateString()}</span>
        </div>
      </div>

      {/* Summary */}
      {report.summary && (
        <section className="mb-6">
          <h2 className="text-lg font-semibold text-gray-800 mb-2">Summary</h2>
          <p
            className="text-sm text-gray-600 leading-relaxed"
            data-testid="report-summary"
          >
            {report.summary}
          </p>
        </section>
      )}

      {/* Content */}
      {report.content_markdown && (
        <section className="mb-6">
          <h2 className="text-lg font-semibold text-gray-800 mb-2">Content</h2>
          <pre
            className="whitespace-pre-wrap text-sm text-gray-700 bg-gray-50 rounded border border-gray-200 p-4"
            data-testid="report-content"
          >
            {report.content_markdown}
          </pre>
        </section>
      )}

      {/* Review panel — Phase 20 final report actions */}
      <section
        className="rounded-lg border border-amber-200 bg-amber-50 p-5"
        data-testid="review-panel"
      >
        <h2 className="text-lg font-semibold text-amber-800 mb-1">
          Admin Review Panel
        </h2>
        <p className="text-xs text-amber-700 mb-4">
          INTERNAL ADMIN ONLY — Actions below do not publish. Human review
          required before any publication decision.
        </p>

        <div className="flex flex-wrap gap-3 mb-4">
          {/* Generate Internal Final Report Draft */}
          <button
            onClick={handleGenerateFinal}
            disabled={generateStatus === "running"}
            className="rounded bg-blue-700 px-4 py-2 text-sm text-white hover:bg-blue-800 disabled:opacity-50"
            data-testid="btn-generate-final"
          >
            {generateStatus === "running"
              ? "Generating…"
              : "Generate Internal Final Report Draft"}
          </button>

          {/* Validate Final Report */}
          <button
            onClick={handleValidate}
            disabled={validateStatus === "running"}
            className="rounded bg-amber-700 px-4 py-2 text-sm text-white hover:bg-amber-800 disabled:opacity-50"
            data-testid="btn-validate-final"
          >
            {validateStatus === "running" ? "Validating…" : "Validate Final Report"}
          </button>

          {/* Regenerate Section (placeholder) */}
          <button
            disabled
            className="rounded bg-gray-500 px-4 py-2 text-sm text-white opacity-50 cursor-not-allowed"
            data-testid="btn-regenerate-section"
            title="Regenerate Section — available after final report is generated"
          >
            Regenerate Section
          </button>
        </div>

        {/* Generate result */}
        {generateStatus === "done" && generateResult && (
          <div
            className="rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-700 mb-3"
            data-testid="generate-final-result"
          >
            ✓ {generateResult.message}
          </div>
        )}

        {/* Validate result */}
        {validateStatus === "done" && validateResult && (
          <div
            className={`rounded border p-3 text-sm mb-3 ${
              validateResult.validation_passed
                ? "border-green-200 bg-green-50 text-green-700"
                : "border-amber-200 bg-amber-100 text-amber-700"
            }`}
            data-testid="validate-final-result"
          >
            <p className="font-medium">
              {validateResult.validation_passed
                ? "✓ Validation passed"
                : "⚠ Validation issues found"}
            </p>
            {validateResult.issues.length > 0 && (
              <ul className="mt-1 list-disc list-inside">
                {validateResult.issues.map((issue, i) => (
                  <li key={i}>{issue}</li>
                ))}
              </ul>
            )}
            <p className="mt-1 text-xs">{validateResult.message}</p>
          </div>
        )}

        {actionError && (
          <div
            className="rounded border border-red-200 bg-red-50 p-3 text-sm text-red-700"
            data-testid="action-error"
          >
            ✗ {actionError}
          </div>
        )}
      </section>
    </div>
  );
}
