"use client";

import Link from "next/link";
import { useCallback, useEffect, useRef, useState } from "react";
import Surface from "@/components/product/Surface";
import {
  createThesisDiscoveryRun,
  getCandidateAnalysisJob,
  getDiscoveryRun,
  listDiscoveryCandidates,
  listDiscoveryRuns,
  listSupportedFilters,
  listSupportedThemes,
  parseThesis,
  runCandidateAnalysis,
} from "@/lib/api";
import type {
  DiscoveryCandidate,
  DiscoveryRun,
  ParseThesisResponse,
  RunCandidateAnalysisResponse,
  SupportedFiltersResponse,
  SupportedThemesResponse,
} from "@/types/api";

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

const inputCls =
  "w-full rounded-lg border border-[color:var(--ib-line)] bg-[color:var(--ib-surface)] px-3.5 py-2.5 text-sm text-[color:var(--ib-ink)] placeholder:text-[color:var(--ib-ink-3)] focus:border-[color:var(--ib-line-strong)] focus:outline-none";

// Shown before the backend's theme list arrives (and if that request fails).
// Every one of these is a shape of request the thesis parser understands.
const FALLBACK_EXAMPLES = [
  "European luxury companies",
  "Small-cap European industrial automation",
  "Nordic businesses exposed to data-centre investment",
  "European companies benefiting from grid modernisation",
];

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

export default function DiscoveryWorkbench() {
  // --- form -----------------------------------------------------------------
  const [thesis, setThesis] = useState("");
  const [detected, setDetected] = useState<ParseThesisResponse | null>(null);
  const [filters, setFilters] = useState<SupportedFiltersResponse | null>(null);
  const [themes, setThemes] = useState<SupportedThemesResponse | null>(null);

  const [region, setRegion] = useState("");
  const [country, setCountry] = useState("");
  const [sector, setSector] = useState("");
  const [industry, setIndustry] = useState("");
  const [maxUniverse, setMaxUniverse] = useState("25");
  const [maxCandidates, setMaxCandidates] = useState("10");

  // A field the reader has set by hand is never overwritten by a later parse.
  const regionEdited = useRef(false);
  const countryEdited = useRef(false);
  const sectorEdited = useRef(false);
  const industryEdited = useRef(false);

  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState<string | null>(null);

  // --- runs -----------------------------------------------------------------
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [runId, setRunId] = useState<string | null>(null);
  const [run, setRun] = useState<DiscoveryRun | null>(null);
  const [candidates, setCandidates] = useState<DiscoveryCandidate[]>([]);
  const [candidatesError, setCandidatesError] = useState<string | null>(null);

  // --- per-candidate research jobs -----------------------------------------
  const [jobs, setJobs] = useState<
    Record<string, RunCandidateAnalysisResponse | undefined>
  >({});
  const [jobErrors, setJobErrors] = useState<Record<string, string | undefined>>(
    {},
  );

  // Supported themes + selector options. Both are conveniences: a failure
  // leaves the form fully usable, it just offers no examples or options.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await listSupportedThemes();
        if (!cancelled) setThemes(data);
      } catch {
        /* non-fatal */
      }
    })();
    void (async () => {
      try {
        const data = await listSupportedFilters();
        if (!cancelled) setFilters(data);
      } catch {
        /* non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // Debounced autofill from the written thesis.
  useEffect(() => {
    const text = thesis.trim();
    if (text.length < 3) {
      setDetected(null);
      return;
    }
    let cancelled = false;
    const handle = window.setTimeout(async () => {
      try {
        const d = await parseThesis(text);
        if (cancelled) return;
        setDetected(d);
        if (!regionEdited.current) setRegion(d.region ?? "");
        if (!countryEdited.current) setCountry(d.country ?? "");
        if (!sectorEdited.current) setSector(d.sector ?? "");
        if (!industryEdited.current) setIndustry(d.industry ?? "");
      } catch {
        /* non-fatal: manual entry still works */
      }
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [thesis]);

  // Recent runs, so returning to the page resumes where you left off.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await listDiscoveryRuns();
        if (cancelled) return;
        setRuns(data.runs);
        setRunId((current) => current ?? data.runs[0]?.id ?? null);
      } catch {
        /* non-fatal */
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  const loadCandidates = useCallback(async (id: string) => {
    try {
      const data = await listDiscoveryCandidates(id, { sort: "candidate_score" });
      setCandidates(data.candidates);
      setCandidatesError(null);
    } catch (e) {
      setCandidatesError(
        e instanceof Error ? e.message : "Could not load candidates.",
      );
    }
  }, []);

  // Poll the selected run until it reaches a terminal state, refreshing the
  // candidate list as the queue fills.
  useEffect(() => {
    if (!runId) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;

    async function poll(id: string) {
      let status: string | undefined;
      try {
        const detail = await getDiscoveryRun(id);
        if (cancelled) return;
        status = detail.status;
        setRun(detail);
        await loadCandidates(id);
      } catch {
        /* transient — keep the last known state */
      }
      if (cancelled) return;
      if (!status || !TERMINAL_RUN_STATUSES.has(status)) {
        timer = setTimeout(() => void poll(id), POLL_INTERVAL_MS);
      }
    }

    setCandidates([]);
    void poll(runId);
    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
    };
  }, [runId, loadCandidates]);

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
      if (!cancelled && runId) void loadCandidates(runId);
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [jobs, runId, loadCandidates]);

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!thesis.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    try {
      const created = await createThesisDiscoveryRun({
        thesis_text: thesis.trim(),
        region: region || undefined,
        country: country || undefined,
        sector: sector || undefined,
        industry: industry || undefined,
        max_universe_size: parseInt(maxUniverse, 10) || undefined,
        max_candidates: parseInt(maxCandidates, 10) || undefined,
      });
      setRuns((prev) => [created, ...prev]);
      setRun(created);
      setRunId(created.id);
      setJobs({});
    } catch (e) {
      setSubmitError(
        e instanceof Error ? e.message : "Could not start the discovery run.",
      );
    } finally {
      setSubmitting(false);
    }
  }

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

  const examples = themes?.examples?.length ? themes.examples : FALLBACK_EXAMPLES;
  const warningGroups = (run?.warning_groups ?? []).filter(
    (g) => g.severity === "blocking" || g.severity === "warning",
  );

  return (
    <div className="space-y-8">
      {/* ---------------------------------------------------------------- */}
      {/* Research intent                                                    */}
      {/* ---------------------------------------------------------------- */}
      <Surface className="p-6 sm:p-7">
        <form onSubmit={handleSubmit} className="space-y-5">
          <div>
            <label
              htmlFor="thesis"
              className="mb-1.5 block text-sm font-medium text-[color:var(--ib-ink)]"
            >
              What are you looking for?
            </label>
            <textarea
              id="thesis"
              data-testid="discovery-thesis"
              rows={3}
              className={inputCls}
              placeholder="Describe the theme, market or idea in your own words."
              value={thesis}
              onChange={(e) => setThesis(e.target.value)}
            />
            <p className="mt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
              Discovery searches a bounded, auditable company registry and
              returns candidates worth reading — never a list of things to buy.
            </p>
          </div>

          <div className="flex flex-wrap gap-2">
            {examples.slice(0, 5).map((example) => (
              <button
                key={example}
                type="button"
                onClick={() => setThesis(example)}
                className="rounded-lg border border-[color:var(--ib-line)] px-3 py-1.5 text-left font-mono text-xs text-[color:var(--ib-ink-3)] transition-colors hover:border-[color:var(--ib-line-strong)] hover:text-[color:var(--ib-ink-2)]"
              >
                {example}
              </button>
            ))}
          </div>

          {detected && (
            <p
              className="text-xs text-[color:var(--ib-ink-3)]"
              data-testid="thesis-detected"
              aria-live="polite"
            >
              {detected.needs_narrowing
                ? "No theme or sector recognised yet — add a sector, industry or region so the universe stays bounded."
                : `Detected: ${[
                    detected.region,
                    detected.country,
                    detected.sector,
                    detected.industry,
                  ]
                    .filter(Boolean)
                    .join(" · ")}. Adjust anything below.`}
            </p>
          )}

          <details className="rounded-lg border border-[color:var(--ib-line)] px-4 py-3">
            <summary className="cursor-pointer list-none text-sm text-[color:var(--ib-ink-2)]">
              Filters{" "}
              <span className="text-xs text-[color:var(--ib-ink-3)]">
                — region, market, sector, size of the search
              </span>
            </summary>
            <div className="mt-4 grid gap-4 sm:grid-cols-2">
              <label className="text-xs text-[color:var(--ib-ink-3)]">
                Region
                <select
                  className={`${inputCls} mt-1`}
                  value={region}
                  onChange={(e) => {
                    regionEdited.current = true;
                    setRegion(e.target.value);
                  }}
                >
                  <option value="" className="bg-[#0a0f1c]">
                    Any
                  </option>
                  {(filters?.regions ?? []).map((o) => (
                    <option key={o.value} value={o.value} className="bg-[#0a0f1c]">
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="text-xs text-[color:var(--ib-ink-3)]">
                Country
                <select
                  className={`${inputCls} mt-1`}
                  value={country}
                  onChange={(e) => {
                    countryEdited.current = true;
                    setCountry(e.target.value);
                  }}
                >
                  <option value="" className="bg-[#0a0f1c]">
                    Any
                  </option>
                  {(filters?.countries ?? [])
                    .filter((o) => !region || o.region === region)
                    .map((o) => (
                      <option key={o.value} value={o.value} className="bg-[#0a0f1c]">
                        {o.label}
                      </option>
                    ))}
                </select>
              </label>

              <label className="text-xs text-[color:var(--ib-ink-3)]">
                Sector
                <select
                  className={`${inputCls} mt-1`}
                  value={sector}
                  onChange={(e) => {
                    sectorEdited.current = true;
                    setSector(e.target.value);
                  }}
                >
                  <option value="" className="bg-[#0a0f1c]">
                    Any
                  </option>
                  {(filters?.sectors ?? []).map((o) => (
                    <option key={o.value} value={o.value} className="bg-[#0a0f1c]">
                      {o.label}
                    </option>
                  ))}
                </select>
              </label>

              <label className="text-xs text-[color:var(--ib-ink-3)]">
                Industry
                <select
                  className={`${inputCls} mt-1`}
                  value={industry}
                  onChange={(e) => {
                    industryEdited.current = true;
                    setIndustry(e.target.value);
                  }}
                >
                  <option value="" className="bg-[#0a0f1c]">
                    Any
                  </option>
                  {(filters?.industries ?? [])
                    .filter((o) => !sector || o.sector === sector)
                    .map((o) => (
                      <option key={o.value} value={o.value} className="bg-[#0a0f1c]">
                        {o.label}
                      </option>
                    ))}
                </select>
              </label>

              <label className="text-xs text-[color:var(--ib-ink-3)]">
                Companies to screen
                <input
                  type="number"
                  min={1}
                  max={50}
                  className={`${inputCls} mt-1`}
                  value={maxUniverse}
                  onChange={(e) => setMaxUniverse(e.target.value)}
                />
              </label>

              <label className="text-xs text-[color:var(--ib-ink-3)]">
                Candidates to return
                <input
                  type="number"
                  min={1}
                  max={50}
                  className={`${inputCls} mt-1`}
                  value={maxCandidates}
                  onChange={(e) => setMaxCandidates(e.target.value)}
                />
              </label>
            </div>
          </details>

          <div className="flex flex-wrap items-center gap-4">
            <button
              type="submit"
              data-testid="run-discovery"
              disabled={submitting || !thesis.trim()}
              className="rounded-lg bg-[color:var(--ib-ink)] px-4 py-2.5 text-sm font-medium text-[#060913] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? "Starting…" : "Run discovery"}
            </button>
            {runs.length > 1 && (
              <label className="text-xs text-[color:var(--ib-ink-3)]">
                Previous runs{" "}
                <select
                  className="ml-1 rounded-lg border border-[color:var(--ib-line)] bg-[color:var(--ib-surface)] px-2 py-1.5 text-xs text-[color:var(--ib-ink-2)]"
                  value={runId ?? ""}
                  onChange={(e) => setRunId(e.target.value || null)}
                >
                  {runs.map((r) => (
                    <option key={r.id} value={r.id} className="bg-[#0a0f1c]">
                      {(r.thesis_text ?? r.universe_source ?? "run").slice(0, 48)}{" "}
                      · {new Date(r.created_at).toLocaleDateString()}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          {submitError && (
            <p role="alert" className="text-sm text-rose-300">
              {submitError}
            </p>
          )}
        </form>
      </Surface>

      {/* ---------------------------------------------------------------- */}
      {/* Run state                                                          */}
      {/* ---------------------------------------------------------------- */}
      {run && (
        <Surface className="p-6" testId="discovery-run-state">
          <div className="flex flex-wrap items-baseline justify-between gap-3">
            <div className="min-w-0">
              <p className="text-sm font-medium text-[color:var(--ib-ink)]">
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

          {warningGroups.length > 0 && (
            <ul className="mt-4 space-y-1.5 border-t border-[color:var(--ib-line)] pt-4">
              {warningGroups.slice(0, 4).map((g) => (
                <li
                  key={g.code}
                  className={`text-xs leading-relaxed ${
                    g.severity === "blocking"
                      ? "text-rose-300"
                      : "text-amber-300/90"
                  }`}
                >
                  {g.message}
                  {g.count > 1 ? ` (${g.count} occurrences)` : ""}
                </li>
              ))}
            </ul>
          )}
        </Surface>
      )}

      {/* ---------------------------------------------------------------- */}
      {/* Candidates                                                         */}
      {/* ---------------------------------------------------------------- */}
      {candidatesError && (
        <Surface className="p-5">
          <p className="text-sm text-amber-300">{candidatesError}</p>
        </Surface>
      )}

      {candidates.length > 0 && (
        <section aria-label="Discovery candidates" className="space-y-3">
          <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
            Candidates
          </h2>
          <ul className="space-y-3" data-testid="discovery-candidates">
            {candidates.map((c) => {
              const job = jobs[c.id];
              const reportId = job?.analysis_report_id ?? c.analysis_report_id;
              const jobRunning =
                job && !TERMINAL_JOB_STATUSES.has(job.status);
              return (
                <li key={c.id}>
                  <Surface className="p-5">
                    <div className="flex flex-wrap items-start justify-between gap-4">
                      <div className="min-w-0">
                        <p className="text-base font-medium text-[color:var(--ib-ink)]">
                          {c.company_name ?? c.ticker}
                        </p>
                        <p className="mt-0.5 font-mono text-xs text-[color:var(--ib-ink-3)]">
                          {c.ticker} · {c.exchange}
                          {c.country ? ` · ${c.country}` : ""}
                          {c.sector ? ` · ${c.sector}` : ""}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-3">
                        {reportId && (
                          <Link
                            href={`/research/reports/${reportId}`}
                            className="ib-arrow-host rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] hover:bg-[color:var(--ib-surface-raised)]"
                          >
                            Open research{" "}
                            <span className="ib-arrow" aria-hidden="true">
                              →
                            </span>
                          </Link>
                        )}
                        {!reportId && (
                          <button
                            type="button"
                            disabled={Boolean(jobRunning)}
                            onClick={() => void startCandidateResearch(c)}
                            className="rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] transition-colors hover:bg-[color:var(--ib-surface-raised)] disabled:opacity-50"
                          >
                            {jobRunning
                              ? jobStateLabel(job?.status)
                              : "Research this company"}
                          </button>
                        )}
                      </div>
                    </div>

                    {c.score_explanation && (
                      <p className="mt-3 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                        {c.score_explanation}
                      </p>
                    )}

                    <dl className="mt-4 flex flex-wrap gap-x-8 gap-y-2 border-t border-[color:var(--ib-line)] pt-3.5 text-xs">
                      <div>
                        <dt className="text-[color:var(--ib-ink-3)]">
                          Source quality
                        </dt>
                        <dd className="text-[color:var(--ib-ink-2)]">
                          {c.source_quality ?? "not assessed"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[color:var(--ib-ink-3)]">
                          Disclosure coverage
                        </dt>
                        <dd className="text-[color:var(--ib-ink-2)]">
                          {c.catalyst_coverage_status ?? "not assessed"}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[color:var(--ib-ink-3)]">
                          Known gaps
                        </dt>
                        <dd className="text-[color:var(--ib-ink-2)]">
                          {c.missing_info_count ?? 0} missing
                          {(c.blocking_gap_count ?? 0) > 0
                            ? ` · ${c.blocking_gap_count} blocking`
                            : ""}
                        </dd>
                      </div>
                      <div>
                        <dt className="text-[color:var(--ib-ink-3)]">
                          Research state
                        </dt>
                        <dd className="text-[color:var(--ib-ink-2)]">
                          {reportId
                            ? "Researched"
                            : job
                              ? jobStateLabel(job.status)
                              : "Not researched"}
                        </dd>
                      </div>
                    </dl>

                    {job?.error && (
                      <p className="mt-3 text-xs text-rose-300">{job.error}</p>
                    )}
                    {jobErrors[c.id] && (
                      <p role="alert" className="mt-3 text-xs text-rose-300">
                        {jobErrors[c.id]}
                      </p>
                    )}
                  </Surface>
                </li>
              );
            })}
          </ul>

          <p className="pt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            Candidate scores are internal research-priority signals derived from
            the evidence found so far. They are not ratings, and they say nothing
            about what a company is worth.
          </p>
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
    </div>
  );
}
