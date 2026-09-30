"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import Surface from "@/components/product/Surface";
import CandidateCard from "@/components/research/discovery/CandidateCard";
import CandidateComparison from "@/components/research/discovery/CandidateComparison";
import DiscoveryCouncilPanel from "@/components/research/discovery/DiscoveryCouncilPanel";
import DiscoveryIntentPanel from "@/components/research/discovery/DiscoveryIntentPanel";
import ExcludedCandidates from "@/components/research/discovery/ExcludedCandidates";
import RunLimitations from "@/components/research/discovery/RunLimitations";
import { splitWarningSubjects } from "@/components/research/discovery/candidateView";
import { useDiscoveryCouncil } from "@/components/research/discovery/useDiscoveryCouncil";
import {
  buildResearchLinkState,
  NO_RESEARCH_LINK,
  type ResearchLinkState,
} from "@/components/research/reportResolution";
import {
  fetchReport,
  fetchReports,
  getCandidateAnalysisJob,
  getDiscoveryRun,
  isNotFound,
  listDiscoveryCandidates,
  runCandidateAnalysis,
} from "@/lib/api";
import type {
  DiscoveryCandidate,
  DiscoveryRun,
  Report,
  ReportList,
  RunCandidateAnalysisResponse,
} from "@/types/api";
import CopyRunLink from "./CopyRunLink";
import { DISCOVERY_BASE_PATH } from "./runRoute";

const POLL_INTERVAL_MS = 3000;

const TERMINAL_RUN_STATUSES = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "cancelled",
]);

const TERMINAL_JOB_STATUSES = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "interrupted",
]);

function runStateLabel(run: DiscoveryRun): string {
  switch (run.status) {
    case "pending":
      return "Queued";
    case "running":
    case "processing":
      return "Scanning the universe";
    case "completed":
      return "Complete";
    case "completed_with_warnings":
      return "Complete, with warnings";
    case "failed":
      return "Failed";
    default:
      return run.status;
  }
}

function jobStateLabel(status: string | undefined | null): string {
  switch (status) {
    case "pending":
      return "Queued";
    case "running":
      return "Researching";
    case "completed":
      return "Research complete";
    case "completed_with_warnings":
      return "Complete, with warnings";
    case "failed":
      return "Failed";
    case "interrupted":
      return "Interrupted";
    default:
      return status ?? "";
  }
}

/**
 * Everything that belongs to ONE discovery run: its state, its council review,
 * its candidates and the research jobs started from them.
 *
 * The workbench renders this keyed by the run id, so opening another run
 * mounts a fresh instance. Nothing from the previous run — candidates, jobs,
 * resolved report links, council state, an in-flight poll — can leak into the
 * next one, and no effect has to remember to clear it.
 */
export default function DiscoveryRunView({
  runId,
  initialRun,
  onRunLoaded,
}: {
  runId: string;
  /** The run as returned by create, so a new run shows before its first poll. */
  initialRun: DiscoveryRun | null;
  /** Reports each fetched run upward so the run selector can list it. */
  onRunLoaded: (run: DiscoveryRun) => void;
}) {
  const [run, setRun] = useState<DiscoveryRun | null>(initialRun);
  // True once the backend has said this run does not exist. Polling stops and
  // the page says so — it never quietly shows some other run instead.
  const [missing, setMissing] = useState(false);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);

  // --- per-candidate research jobs -----------------------------------------
  const [jobs, setJobs] = useState<
    Record<string, RunCandidateAnalysisResponse | undefined>
  >({});
  const [jobErrors, setJobErrors] = useState<Record<string, string | undefined>>(
    {},
  );

  // --- which report is each candidate's CURRENT research? -------------------
  //
  // `candidate.analysis_report_id` is NOT that answer. The screening pass links
  // the deterministic draft it produced for every ticker it touched, so a
  // freshly screened candidate already points at a report that says
  // "pre-council historical draft". Resolving the real answer needs the
  // report's company and then that company's reports — both plain reads.
  const [links, setLinks] = useState<Record<string, ResearchLinkState>>({});
  const [linksResolved, setLinksResolved] = useState(false);

  // --- the run-level research council ---------------------------------------
  // Read-only on mount; started only when the reader asks.
  const council = useDiscoveryCouncil(missing ? null : runId);

  const loadCandidates = useCallback(async (id: string) => {
    try {
      // V3.19 — the run's own ranking (eligibility first on a verified run).
      const data = await listDiscoveryCandidates(id, { sort: "rank" });
      setCandidates(data.candidates);
      setCandidatesError(null);
    } catch (e) {
      setCandidatesError(
        e instanceof Error ? e.message : "Could not load candidates.",
      );
    }
  }, []);

  // Poll the run until it reaches a terminal state, refreshing the candidate
  // list as the queue fills. A 404 is final: the run does not exist.
  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll() {
      let status: string | undefined;
      try {
        const detail = await getDiscoveryRun(runId);
        if (cancelled) return;
        status = detail.status;
        setRun(detail);
        onRunLoaded(detail);
        await loadCandidates(runId);
      } catch (e) {
        if (cancelled) return;
        if (isNotFound(e)) {
          setMissing(true);
          return;
        }
        /* transient — keep the last known state and try again */
      }
      if (cancelled) return;
      if (!status || !TERMINAL_RUN_STATUSES.has(status)) {
        timer = setTimeout(() => void poll(), POLL_INTERVAL_MS);
      }
    }

    void poll();
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, loadCandidates, onRunLoaded]);

  // Resolve, for every candidate that points at a report, which report is that
  // company's CURRENT research — and whether the one it points at IS that.
  //
  // Two reads per candidate: the linked report (which carries the company FK)
  // and that company's own report list. Both are plain reads of endpoints that
  // already exist, keyed off the candidate set so a poll tick that returns the
  // same candidates does not re-run them. A candidate with no linked report
  // needs neither read — it is screening-only, which is already the answer.
  const linkedReportKey = candidates
    .map((c) => c.analysis_report_id ?? "")
    .join("|");

  useEffect(() => {
    const linked = candidates.filter((c) => c.analysis_report_id);
    if (linked.length === 0) {
      setLinks({});
      setLinksResolved(true);
      return;
    }
    let cancelled = false;
    setLinksResolved(false);

    void (async () => {
      const cohorts = new Map<string, ReportList>();
      const next: Record<string, ResearchLinkState> = {};

      await Promise.all(
        linked.map(async (c) => {
          try {
            const report = await fetchReport(c.analysis_report_id as string);
            const companyId = report.company_id;
            let cohort: Report[] = [];
            if (companyId) {
              let list = cohorts.get(companyId);
              if (!list) {
                list = await fetchReports(50, 0, { companyId });
                cohorts.set(companyId, list);
              }
              cohort = list.items;
            }
            next[c.id] = buildResearchLinkState(report, cohort);
          } catch {
            // A report that cannot be read is not evidence that research
            // exists. The candidate stays in its screening-only state.
            next[c.id] = NO_RESEARCH_LINK;
          }
        }),
      );

      if (cancelled) return;
      setLinks(next);
      setLinksResolved(true);
    })();

    return () => {
      cancelled = true;
    };
    // `linkedReportKey` is the candidate set's report linkage, which is what
    // actually needs re-resolving — not every poll-refreshed candidate object.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [linkedReportKey]);

  // Poll every in-flight per-candidate research job.
  useEffect(() => {
    const pending = Object.entries(jobs).filter(
      ([, job]) => job && !TERMINAL_JOB_STATUSES.has(job.status),
    );
    if (pending.length === 0) return;
    let cancelled = false;
    const timer = setTimeout(async () => {
      for (const [candidateId] of pending) {
        try {
          const next = await getCandidateAnalysisJob(candidateId);
          if (cancelled) return;
          setJobs((prev) => ({ ...prev, [candidateId]: next }));
        } catch {
          /* transient — the job continues server-side */
        }
      }
      if (!cancelled) void loadCandidates(runId);
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobs, runId, loadCandidates]);

  async function startCandidateResearch(candidate: DiscoveryCandidate) {
    setJobErrors((prev) => ({ ...prev, [candidate.id]: undefined }));
    try {
      const job = await runCandidateAnalysis(candidate.id);
      setJobs((prev) => ({ ...prev, [candidate.id]: job }));
    } catch (e) {
      setJobErrors((prev) => ({
        ...prev,
        [candidate.id]:
          e instanceof Error ? e.message : "Could not start research.",
      }));
    }
  }

  // The backend already deduplicates warnings into canonical groups and names
  // the candidates each one affects. A group naming exactly ONE candidate is
  // that candidate's limitation and belongs on its card; everything else is a
  // limitation of the run and is stated once, not six times.
  const allWarningGroups = (run?.warning_groups ?? []).filter(
    (g) => g.severity === "blocking" || g.severity === "warning",
  );
  const cohortWarningGroups = allWarningGroups.filter(
    (g) => splitWarningSubjects(g.subjects).cohortWide,
  );
  const warningsByTicker = useMemo(() => {
    const out: Record<string, string[]> = {};
    for (const g of allWarningGroups) {
      const { cohortWide, ticker } = splitWarningSubjects(g.subjects);
      if (cohortWide || !ticker) continue;
      (out[ticker] ??= []).push(g.message);
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [run?.warning_groups]);

  const runIsTerminal = Boolean(run && TERMINAL_RUN_STATUSES.has(run.status));

  if (missing) {
    return (
      <Surface className="p-8 text-center" testId="discovery-run-not-found">
        <p className="text-sm font-medium text-[color:var(--ib-ink)]" role="status">
          This discovery run does not exist.
        </p>
        <p className="mt-1 text-sm text-[color:var(--ib-ink-3)]">
          The link may be mistyped, or the run may no longer be available.
        </p>
        <Link
          href={DISCOVERY_BASE_PATH}
          data-testid="discovery-run-not-found-back"
          className="mt-4 inline-block text-sm text-[color:var(--ib-ink-2)] underline underline-offset-4 hover:text-[color:var(--ib-ink)]"
        >
          Back to Discovery
        </Link>
      </Surface>
    );
  }

  return (
    <>
      {/* ---------------------------------------------------------------- */}
      {/* Run state                                                          */}
      {/* ---------------------------------------------------------------- */}
      {run && (
        <Surface className="p-6" testId="discovery-run-state">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-[color:var(--ib-ink)] [overflow-wrap:anywhere]">
                {run.thesis_text ?? "Discovery run"}
              </p>
              <p className="mt-1 text-xs text-[color:var(--ib-ink-3)]">
                {runStateLabel(run)} · {run.processed_count} of{" "}
                {run.universe_count} screened · {run.candidate_count} candidate
                {run.candidate_count === 1 ? "" : "s"}
                {run.error_count > 0 ? ` · ${run.error_count} error(s)` : ""}
              </p>
            </div>
            <Link
              href="/admin/discovery"
              className="shrink-0 text-xs text-[color:var(--ib-ink-3)] underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
            >
              Run diagnostics
            </Link>
          </div>

          <div className="mt-3">
            <CopyRunLink runId={runId} />
          </div>

          {!TERMINAL_RUN_STATUSES.has(run.status) && (
            <div
              className="mt-4 h-1 w-full overflow-hidden rounded-full bg-[color:var(--ib-line)]"
              role="progressbar"
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={Math.round(run.progress_pct ?? 0)}
              aria-label="Discovery progress"
            >
              <div
                className="h-full bg-[color:var(--ib-accent)] transition-[width] duration-500"
                style={{ width: `${Math.max(3, Math.round(run.progress_pct ?? 0))}%` }}
              />
            </div>
          )}

          {/* V3.19 — what this run understood, and the verification funnel. */}
          <div className="mt-4 space-y-3">
            <DiscoveryIntentPanel
              intent={run.parsed_thesis_json?.discovery_intent}
              testId="run-intent"
            />
            <ExcludedCandidates stage={run.universe_json?.dynamic} />
          </div>
        </Surface>
      )}

      {/* ---------------------------------------------------------------- */}
      {/* Research Council review                                            */}
      {/* ---------------------------------------------------------------- */}
      <DiscoveryCouncilPanel
        council={council}
        runIsTerminal={runIsTerminal}
        candidateCount={candidates.length}
      />

      {/* ---------------------------------------------------------------- */}
      {/* Candidates                                                         */}
      {/* ---------------------------------------------------------------- */}
      {candidatesError && (
        <Surface className="p-5">
          <p className="text-sm text-amber-300">{candidatesError}</p>
        </Surface>
      )}

      {candidates.length > 0 && (
        <CandidateComparison candidates={candidates} council={council.view} />
      )}

      {cohortWarningGroups.length > 0 && (
        <RunLimitations
          groups={cohortWarningGroups}
          rawCount={run?.warning_raw_count ?? run?.warnings?.length ?? 0}
        />
      )}

      {candidates.length > 0 && (
        <section aria-label="Discovery candidates" className="space-y-3">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
              Candidates
            </h2>
            <p className="text-xs text-[color:var(--ib-ink-3)]">
              {candidates.length} candidate
              {candidates.length === 1 ? "" : "s"}
            </p>
          </div>

          {/* One page-level explanation of the score, instead of the same
              paragraph repeated under every card. */}
          <p className="max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
            The screening score in the comparison is an internal, deterministic
            score out of 100 (it includes share-price momentum). It is not the
            council&apos;s research priority, not a rating, says nothing about
            what a company is worth, and implies no investment action. Candidates
            are ordered by how fully they were verified against your requirements.
          </p>

          <ul className="space-y-3 pt-1" data-testid="discovery-candidates">
            {candidates.map((c) => {
              const job = jobs[c.id];
              const jobRunning = Boolean(
                job && !TERMINAL_JOB_STATUSES.has(job.status),
              );
              return (
                <li key={c.id}>
                  <CandidateCard
                    candidate={c}
                    council={council.view}
                    link={links[c.id] ?? NO_RESEARCH_LINK}
                    linkResolved={linksResolved}
                    jobLabel={job ? jobStateLabel(job.status) : null}
                    jobRunning={jobRunning}
                    jobError={jobErrors[c.id] ?? job?.error ?? null}
                    jobReportId={
                      job && TERMINAL_JOB_STATUSES.has(job.status)
                        ? job.analysis_report_id
                        : null
                    }
                    onResearch={() => void startCandidateResearch(c)}
                    candidateWarnings={warningsByTicker[c.ticker] ?? []}
                  />
                </li>
              );
            })}
          </ul>
        </section>
      )}

      {run &&
        TERMINAL_RUN_STATUSES.has(run.status) &&
        candidates.length === 0 &&
        !candidatesError && (
          <Surface className="p-8 text-center">
            <p className="text-sm text-[color:var(--ib-ink-2)]">
              No candidate cleared the screen for this description.
            </p>
            <p className="mt-1 text-sm text-[color:var(--ib-ink-3)]">
              Try a broader region or sector, or describe the theme differently.
            </p>
          </Surface>
        )}
    </>
  );
}
