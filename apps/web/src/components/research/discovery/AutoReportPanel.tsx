"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import { getCandidateAnalysisJob } from "@/lib/api";
import type {
  DiscoveryAutoReport,
  RunCandidateAnalysisResponse,
} from "@/types/api";

/**
 * V3.21.3 (ADR-110) — the automatic report on a thesis run's best eligible
 * candidate: what happened, why, and a link to the research job or the report.
 *
 * A queued report is followed through the candidate's analysis job (the same
 * endpoint the candidate panels poll), so progress reads the same here as there.
 * Workflow states only — never an investment action.
 */

const POLL_INTERVAL_MS = 8000;
const IN_FLIGHT = new Set(["pending", "running"]);

const REASON_WORDS: Record<string, string> = {
  opted_out: "this run was started with the automatic report turned off",
  run_not_completed: "the run did not complete",
  no_eligible_candidate:
    "no candidate was eligible (verified size, not held back by the novelty gate)",
  company_not_found: "the candidate's company record was not found",
  fresh_report: "the company already has a recent report, which is linked instead",
  durable_jobs_off: "durable research jobs are off on this server",
  daily_cap: "the daily limit on automatic reports was reached",
  joined_in_flight_job: "research for this company was already running; this run joined it",
};

export function autoReportReasonWords(reason?: string | null): string | null {
  if (!reason) return null;
  if (REASON_WORDS[reason]) return REASON_WORDS[reason];
  if (reason.startsWith("submit_error") || reason.startsWith("error"))
    return "the research job could not be queued";
  return reason;
}

function stateHeadline(record: DiscoveryAutoReport, job: RunCandidateAnalysisResponse | null): string {
  const who = record.ticker ? `${record.ticker}${record.exchange ? ` (${record.exchange})` : ""}` : "the best candidate";
  switch (record.state) {
    case "queued": {
      const status = job?.status;
      if (status === "completed" || status === "completed_with_warnings")
        return `Automatic report on ${who} is ready`;
      if (status === "running") return `Automatic report on ${who} is running`;
      if (status && !IN_FLIGHT.has(status))
        return `Automatic report on ${who} did not finish`;
      return `Automatic report on ${who} is queued`;
    }
    case "linked":
      return `Automatic report: ${who} already has a recent report`;
    case "skipped":
      return "No automatic report for this run";
    case "failed":
      return "The automatic report could not be queued";
    default:
      return `Automatic report: ${record.state}`;
  }
}

export default function AutoReportPanel({
  record,
  reportHref = (id: string) => `/research/reports/${id}`,
  testId = "auto-report-panel",
}: {
  record: DiscoveryAutoReport | null | undefined;
  reportHref?: (id: string) => string;
  testId?: string;
}) {
  const [job, setJob] = useState<RunCandidateAnalysisResponse | null>(null);
  const candidateId = record?.state === "queued" ? record.candidate_id ?? null : null;
  const jobStatus = job?.status;

  useEffect(() => {
    if (!candidateId) return;
    if (jobStatus && !IN_FLIGHT.has(jobStatus)) return;
    let cancelled = false;
    const timer = setTimeout(
      async () => {
        try {
          const next = await getCandidateAnalysisJob(candidateId);
          if (!cancelled) setJob(next);
        } catch {
          /* transient, or no job yet — the job continues server-side */
        }
      },
      job ? POLL_INTERVAL_MS : 0,
    );
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [candidateId, jobStatus, job]);

  if (!record) return null;
  const reportId = job?.analysis_report_id ?? record.report_id ?? null;
  const reason = autoReportReasonWords(record.reason);

  return (
    <div
      className="rounded-lg border border-[color:var(--ib-line)] px-4 py-3 text-xs"
      data-testid={testId}
      data-state={record.state}
    >
      <p className="font-medium text-[color:var(--ib-ink)]" role="status">
        {stateHeadline(record, job)}
      </p>
      {reason && (
        <p className="mt-1 text-[color:var(--ib-ink-3)]" data-testid={`${testId}-reason`}>
          Why: {reason}.
        </p>
      )}
      {record.note && (
        <p className="mt-1 text-[color:var(--ib-ink-3)]">Note: {record.note}</p>
      )}
      <div className="mt-2 flex flex-wrap gap-3">
        {record.job_id && (
          <Link
            href={`/research/company?job=${encodeURIComponent(record.job_id)}`}
            className="text-[color:var(--ib-ink-2)] underline underline-offset-4 hover:text-[color:var(--ib-ink)]"
            data-testid={`${testId}-job-link`}
          >
            Research job{job?.status ? ` (${job.status})` : ""}
          </Link>
        )}
        {reportId && (
          <Link
            href={reportHref(reportId)}
            className="text-[color:var(--ib-ink-2)] underline underline-offset-4 hover:text-[color:var(--ib-ink)]"
            data-testid={`${testId}-report-link`}
          >
            Open report
          </Link>
        )}
      </div>
      <p className="mt-2 text-[11px] text-[color:var(--ib-ink-3)]">
        Internal research draft only. Not investment advice; human review required.
      </p>
    </div>
  );
}
