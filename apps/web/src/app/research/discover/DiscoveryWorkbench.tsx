"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Surface from "@/components/product/Surface";
import CandidateCard from "@/components/research/discovery/CandidateCard";
import CandidateComparison from "@/components/research/discovery/CandidateComparison";
import DiscoveryCouncilPanel from "@/components/research/discovery/DiscoveryCouncilPanel";
import RunLimitations from "@/components/research/discovery/RunLimitations";
import { splitWarningSubjects } from "@/components/research/discovery/candidateView";
import { useDiscoveryCouncil } from "@/components/research/discovery/useDiscoveryCouncil";
import {
  buildResearchLinkState,
  NO_RESEARCH_LINK,
  type ResearchLinkState,
} from "@/components/research/reportResolution";
import {
  createThesisDiscoveryRun,
  fetchReport,
  fetchReports,
  getCandidateAnalysisJob,
  getDiscoveryRun,
  listDiscoveryCandidates,
  listDiscoveryRuns,
  listSupportedFilters,
  listSupportedThemes,
  parseThesis,
  runCandidateAnalysis,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  DISCOVERY_DEFAULTS,
  buildThesisDiscoveryRequest,
} from "@/lib/workflows";
import type {
  DiscoveryCandidate,
  DiscoveryRun,
  ParseThesisResponse,
  Report,
  ReportList,
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
  const [maxUniverse, setMaxUniverse] = useState(
    String(DISCOVERY_DEFAULTS.maxUniverseSize),
  );
  const [maxCandidates, setMaxCandidates] = useState(
    String(DISCOVERY_DEFAULTS.maxCandidates),
  );
  // True when the last parse attempt failed, so the scope shown is unknown
  // rather than merely empty.
  const [parseFailed, setParseFailed] = useState(false);

  // A field the reader has set by hand is never overwritten by a later parse.
  // Industry has no entry here because it is never auto-filled at all — see
  // `ThesisDiscoveryInput.industry` in src/lib/workflows.ts.
  const regionEdited = useRef(false);
  const countryEdited = useRef(false);
  const sectorEdited = useRef(false);

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
  const council = useDiscoveryCouncil(runId);

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

  // Debounced scope detection from the written thesis.
  //
  // Two rules make this safe to run on every keystroke. Inference only ever
  // fills region / country / sector — the same three the admin console fills —
  // and never industry, so the universe is never silently narrowed to a
  // category the reader did not ask for. And whenever detection produces
  // nothing, whether because the text is too short, the parser found no scope,
  // or the request failed, the inferred fields are CLEARED. A filter inferred
  // from the previous thesis must never survive into the next one.
  useEffect(() => {
    const text = thesis.trim();

    function clearInferred() {
      if (!regionEdited.current) setRegion("");
      if (!countryEdited.current) setCountry("");
      if (!sectorEdited.current) setSector("");
    }

    if (text.length < 3) {
      setDetected(null);
      setParseFailed(false);
      clearInferred();
      return;
    }

    let cancelled = false;
    const handle = window.setTimeout(async () => {
      try {
        const d = await parseThesis(text);
        if (cancelled) return;
        setDetected(d);
        setParseFailed(false);
        if (!regionEdited.current) setRegion(d.region ?? "");
        if (!countryEdited.current) setCountry(d.country ?? "");
        if (!sectorEdited.current) setSector(d.sector ?? "");
      } catch {
        if (cancelled) return;
        setDetected(null);
        setParseFailed(true);
        clearInferred();
      }
    }, 400);
    return () => {
      cancelled = true;
      window.clearTimeout(handle);
    };
  }, [thesis]);

  // Hand every filter back to inference. Used by the "reset to detected scope"
  // control, which is what makes a sticky manual edit correctable instead of
  // silently riding along on an unrelated later query.
  function resetFiltersToDetected() {
    regionEdited.current = false;
    countryEdited.current = false;
    sectorEdited.current = false;
    setRegion(detected?.region ?? "");
    setCountry(detected?.country ?? "");
    setSector(detected?.sector ?? "");
    setIndustry("");
  }

  // True when a filter differs from what the parser detects for the CURRENT
  // text — i.e. the request will be narrower or broader than the words say.
  const filtersOverridden =
    industry.trim() !== "" ||
    (detected !== null &&
      (region !== (detected.region ?? "") ||
        country !== (detected.country ?? "") ||
        sector !== (detected.sector ?? "")));

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
      // Built by the SAME helper the admin console uses, so an identical
      // description produces an identical run on either surface — including
      // the lookback window and provider the admin console has always sent.
      const created = await createThesisDiscoveryRun(
        buildThesisDiscoveryRequest({
          thesisText: thesis,
          region,
          country,
          sector,
          industry,
          maxUniverseSize: parseInt(maxUniverse, 10) || undefined,
          maxCandidates: parseInt(maxCandidates, 10) || undefined,
        }),
      );
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

          {parseFailed && (
            <p
              className="text-xs text-amber-300"
              data-testid="thesis-parse-failed"
              aria-live="polite"
            >
              The scope of this description could not be read just now, so no
              filter has been inferred from it. You can still set the filters
              yourself, or leave them open.
            </p>
          )}

          {detected && (
            <p
              className="text-xs text-[color:var(--ib-ink-3)]"
              data-testid="thesis-detected"
              aria-live="polite"
            >
              {detected.needs_narrowing
                ? "No theme or sector recognised yet — add a sector or region below so the universe stays bounded."
                : `Detected scope: ${
                    [detected.region, detected.country, detected.sector]
                      .filter(Boolean)
                      .join(" · ") || "none"
                  }.${
                    detected.industry
                      ? ` The backend reads this as ${detected.industry} and will apply that itself — it is not added as a filter here.`
                      : ""
                  }`}
            </p>
          )}

          {/* A filter you set by hand stays set, deliberately. This says so out
              loud and offers one click back to the detected scope, so an
              override from an earlier query can never quietly narrow a later
              one. */}
          {filtersOverridden && (
            <p
              className="flex flex-wrap items-center gap-2 text-xs text-[color:var(--ib-ink-3)]"
              data-testid="filters-overridden"
            >
              Filters below differ from the detected scope and will be sent as
              you set them.
              <button
                type="button"
                onClick={resetFiltersToDetected}
                className="rounded-md border border-[color:var(--ib-line)] px-2 py-1 text-xs text-[color:var(--ib-ink-2)] transition-colors hover:border-[color:var(--ib-line-strong)]"
              >
                Reset to detected scope
              </button>
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
                Industry{" "}
                <span className="text-[color:var(--ib-ink-3)]">
                  — narrows further; never inferred
                </span>
                <select
                  className={`${inputCls} mt-1`}
                  data-testid="industry-filter"
                  value={industry}
                  onChange={(e) => setIndustry(e.target.value)}
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
                      · {formatDate(r.created_at)}
                    </option>
                  ))}
                </select>
              </label>
            )}
          </div>

          {submitError && (
            <p
              role="alert"
              data-testid="discovery-error"
              className="text-sm text-rose-300"
            >
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

        </Surface>
      )}

      {/* ---------------------------------------------------------------- */}
      {/* Research Council review                                            */}
      {/* ---------------------------------------------------------------- */}
      {runId && (
        <DiscoveryCouncilPanel
          council={council}
          runIsTerminal={runIsTerminal}
          candidateCount={candidates.length}
        />
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
            Research priority is an internal screening score out of 100. It
            ranks candidates for human research triage — it is not a rating, it
            says nothing about what a company is worth, and it implies no
            investment action.
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
    </div>
  );
}
