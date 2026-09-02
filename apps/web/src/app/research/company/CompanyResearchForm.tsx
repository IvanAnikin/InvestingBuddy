"use client";

import Link from "next/link";
import { usePathname, useRouter, useSearchParams } from "next/navigation";
import { useCallback, useEffect, useId, useMemo, useRef, useState } from "react";
import PrimaryCTA from "@/components/product/PrimaryCTA";
import Surface from "@/components/product/Surface";
import {
  createCompany,
  fetchCompanies,
  getCompanyResearchJob,
  getLatestCompanyResearchJob,
  isNotFound,
  startCompanyResearchJob,
} from "@/lib/api";
import {
  DATA_PROVIDERS,
  LLM_SECTION_PROVIDERS,
  PROVIDER_FREE_REAL,
  isOfflineProvider,
} from "@/lib/workflows";
import type { Company, CompanyResearchJob } from "@/types/api";

// The research universe is the set of companies the backend can analyse: the
// workflow resolves a company by id or by (ticker, exchange) and fails closed
// when it finds neither. Rather than sending the reader to a second page to
// discover that, this form searches the universe and — when the company is not
// in it yet — registers it inline through the same endpoint the admin uses.
const UNIVERSE_PAGE_SIZE = 200;

// SUBMIT, THEN POLL.
//
// This form used to run the pipeline inside two long-lived HTTP requests: the
// company-analysis workflow, then the final-report generator. On live data
// that is ~154s of document ingestion plus ~145-190s of council, against an
// Azure gateway ceiling of ~230s — so the reader waited five minutes and got a
// 502 or a 504, and the transaction rolled back. Keeping the tab open was
// load-bearing, and it was not enough.
//
// Now the submit creates a durable job and returns in well under a second. The
// run continues on the server whatever the browser does; this polls it. The
// job id goes in the URL, so a refresh reattaches to the same run rather than
// starting a second one, and the id is recoverable from the backend by company
// even if the URL is lost.
const POLL_INTERVAL_MS = 3000;

/** Statuses where the job is still working and polling should continue. */
const IN_FLIGHT = new Set(["pending", "running"]);
/** Statuses where nothing more will happen without a human. */
const TERMINAL = new Set([
  "completed",
  "completed_with_warnings",
  "failed",
  "interrupted",
]);

const inputCls =
  "w-full rounded-lg border border-[color:var(--ib-line)] bg-[color:var(--ib-surface)] px-3.5 py-2.5 text-sm text-[color:var(--ib-ink)] placeholder:text-[color:var(--ib-ink-3)] focus:border-[color:var(--ib-line-strong)] focus:outline-none";

function normalise(value: string): string {
  return value.trim().toLowerCase();
}

function elapsedLabel(seconds: number): string {
  return `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

export default function CompanyResearchForm() {
  const listboxId = useId();
  const router = useRouter();
  const pathname = usePathname();
  const searchParams = useSearchParams();
  const jobFromUrl = searchParams.get("job");

  const [universe, setUniverse] = useState<Company[]>([]);
  const [universeError, setUniverseError] = useState<string | null>(null);
  const [loadingUniverse, setLoadingUniverse] = useState(true);

  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<Company | null>(null);
  const [openList, setOpenList] = useState(false);

  const [showRegister, setShowRegister] = useState(false);
  const [newName, setNewName] = useState("");
  const [newTicker, setNewTicker] = useState("");
  const [newExchange, setNewExchange] = useState("");
  const [registering, setRegistering] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);

  const [showAdvanced, setShowAdvanced] = useState(false);
  const [provider, setProvider] = useState(PROVIDER_FREE_REAL);
  // Mirrors the admin console's default. `use_llm` gates the LLM-drafted
  // research-sections node, NOT the research council — the council is a
  // server-side setting applied when the final report is generated, and no
  // request flag can turn it on or off.
  const [useLlmSections, setUseLlmSections] = useState(false);
  const [llmSectionProvider, setLlmSectionProvider] = useState("azure_openai");
  const [requireSchemaValid, setRequireSchemaValid] = useState(false);

  const [job, setJob] = useState<CompanyResearchJob | null>(null);
  const [submitting, setSubmitting] = useState(false);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);

  const wrapperRef = useRef<HTMLDivElement | null>(null);

  const running = job !== null && IN_FLIGHT.has(job.status);
  const busy = submitting || running;

  useEffect(() => {
    let cancelled = false;
    async function load() {
      try {
        const data = await fetchCompanies(UNIVERSE_PAGE_SIZE, 0);
        if (!cancelled) setUniverse(data.items);
      } catch (e) {
        if (!cancelled) {
          setUniverseError(
            e instanceof Error ? e.message : "Could not load the research universe.",
          );
        }
      } finally {
        if (!cancelled) setLoadingUniverse(false);
      }
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, []);

  // Reattach to a run named in the URL. This is the refresh path: the reader
  // reloads the page mid-run and lands back on the same job, at whatever stage
  // it has reached, without starting a second one.
  useEffect(() => {
    if (!jobFromUrl || job?.job_id === jobFromUrl) return;
    let cancelled = false;
    (async () => {
      try {
        const recovered = await getCompanyResearchJob(jobFromUrl);
        if (!cancelled) setJob(recovered);
      } catch (e) {
        if (!cancelled && !isNotFound(e)) {
          setError(
            e instanceof Error ? e.message : "Could not read the research run.",
          );
        }
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [jobFromUrl, job?.job_id]);

  // Poll while the job is working. Polling stops the moment it reaches a
  // terminal state — a completed report is not a thing to keep asking about.
  useEffect(() => {
    if (!job || !IN_FLIGHT.has(job.status)) return;
    let cancelled = false;
    const id = window.setInterval(async () => {
      try {
        const next = await getCompanyResearchJob(job.job_id);
        if (!cancelled) setJob(next);
      } catch {
        // A single failed poll is not a failed run. The job is on the server;
        // the next tick asks again.
      }
    }, POLL_INTERVAL_MS);
    return () => {
      cancelled = true;
      window.clearInterval(id);
    };
  }, [job]);

  // Elapsed time, counted from the job's OWN start timestamp rather than from
  // when this component mounted — otherwise a refresh would restart the clock
  // and understate how long the run has been going.
  useEffect(() => {
    if (!running || !job?.started_at) return;
    const started = new Date(job.started_at).getTime();
    const tick = () =>
      setElapsed(Math.max(0, Math.floor((Date.now() - started) / 1000)));
    tick();
    const id = window.setInterval(tick, 1000);
    return () => window.clearInterval(id);
  }, [running, job?.started_at]);

  // Clicking outside the combobox closes its list.
  useEffect(() => {
    if (!openList) return;
    function onPointerDown(event: MouseEvent) {
      if (!wrapperRef.current?.contains(event.target as Node)) setOpenList(false);
    }
    document.addEventListener("mousedown", onPointerDown);
    return () => document.removeEventListener("mousedown", onPointerDown);
  }, [openList]);

  const matches = useMemo(() => {
    const q = normalise(query);
    if (!q) return universe.slice(0, 8);
    return universe
      .filter(
        (c) =>
          normalise(c.name).includes(q) ||
          normalise(c.ticker).includes(q) ||
          normalise(`${c.ticker} ${c.exchange}`).includes(q),
      )
      .slice(0, 8);
  }, [query, universe]);

  const noMatch = query.trim().length > 0 && matches.length === 0 && !selected;

  const rememberJob = useCallback(
    (next: CompanyResearchJob) => {
      setJob(next);
      // The URL is the durable client-side handle. `replace` rather than
      // `push`: reattaching to a run is not a navigation the back button
      // should undo.
      router.replace(`${pathname}?job=${next.job_id}`, { scroll: false });
    },
    [pathname, router],
  );

  const pick = useCallback(
    async (company: Company) => {
      setSelected(company);
      setQuery(`${company.name} · ${company.ticker}`);
      setOpenList(false);
      setShowRegister(false);
      setJob(null);
      if (jobFromUrl) return;
      // If a run for this company is ALREADY IN FLIGHT, attach to it rather
      // than inviting a second one — the backend is the source of truth for
      // that, not this tab, and the run may have been started somewhere else
      // entirely.
      //
      // Only an in-flight job. A run that finished last month is not this
      // session's work and showing it here would read as "research complete"
      // for something the reader has not just done; it belongs in the research
      // library, which is where it is.
      try {
        const existing = await getLatestCompanyResearchJob(company.id);
        if (IN_FLIGHT.has(existing.status)) rememberJob(existing);
      } catch {
        // 404 is the normal answer for a company never researched.
      }
    },
    [jobFromUrl, rememberJob],
  );

  async function handleRegister(event: React.FormEvent) {
    event.preventDefault();
    setRegistering(true);
    setRegisterError(null);
    try {
      const company = await createCompany({
        name: newName.trim(),
        ticker: newTicker.trim().toUpperCase(),
        exchange: newExchange.trim().toUpperCase(),
      });
      setUniverse((prev) => [company, ...prev]);
      await pick(company);
      setNewName("");
      setNewTicker("");
      setNewExchange("");
    } catch (e) {
      setRegisterError(
        e instanceof Error ? e.message : "Could not add the company.",
      );
    } finally {
      setRegistering(false);
    }
  }

  async function submit(company: Company) {
    setError(null);
    setElapsed(0);
    setSubmitting(true);
    try {
      // Identity is sent as the canonical company_id from the selected record —
      // never re-derived from what the input happens to display.
      const started = await startCompanyResearchJob({
        company_id: company.id,
        provider_name: provider,
        use_llm: useLlmSections,
        llm_provider: useLlmSections ? llmSectionProvider : null,
        require_schema_valid: requireSchemaValid,
      });
      rememberJob(started);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The research run could not start.");
    } finally {
      setSubmitting(false);
    }
  }

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected || busy) return;
    await submit(selected);
  }

  const reportId = job?.analysis_report_id ?? null;
  const failed = job?.status === "failed";
  const interrupted = job?.status === "interrupted";

  return (
    <div className="space-y-6">
      <Surface className="p-6 sm:p-7">
        <form onSubmit={handleSubmit} className="space-y-5">
          {/* Company combobox */}
          <div ref={wrapperRef} className="relative">
            <label
              htmlFor="company-query"
              className="mb-1.5 block text-sm font-medium text-[color:var(--ib-ink)]"
            >
              Company name or ticker
            </label>
            <input
              id="company-query"
              data-testid="company-query"
              role="combobox"
              aria-expanded={openList}
              aria-controls={listboxId}
              aria-autocomplete="list"
              autoComplete="off"
              className={inputCls}
              placeholder="e.g. Pandora, PNDORA, Richemont"
              value={query}
              disabled={busy}
              onChange={(e) => {
                setQuery(e.target.value);
                setSelected(null);
                setOpenList(true);
              }}
              onFocus={() => setOpenList(true)}
              onKeyDown={(e) => {
                if (e.key === "Escape") setOpenList(false);
                if (e.key === "Enter" && openList && matches.length === 1) {
                  e.preventDefault();
                  void pick(matches[0]);
                }
              }}
            />

            {openList && matches.length > 0 && (
              <ul
                id={listboxId}
                role="listbox"
                aria-label="Matching companies"
                className="absolute z-20 mt-1 max-h-72 w-full overflow-auto rounded-lg border border-[color:var(--ib-line-strong)] bg-[#0a0f1c] py-1 shadow-xl"
              >
                {matches.map((company) => (
                  <li key={company.id} role="option" aria-selected={selected?.id === company.id}>
                    <button
                      type="button"
                      onClick={() => void pick(company)}
                      className="flex w-full items-baseline gap-2 px-3.5 py-2 text-left text-sm text-[color:var(--ib-ink-2)] hover:bg-[color:var(--ib-surface-raised)] hover:text-[color:var(--ib-ink)]"
                    >
                      <span className="truncate">{company.name}</span>
                      <span className="ml-auto shrink-0 font-mono text-xs text-[color:var(--ib-ink-3)]">
                        {company.ticker} · {company.exchange}
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}

            <p className="mt-2 text-xs text-[color:var(--ib-ink-3)]">
              {loadingUniverse
                ? "Loading your research universe…"
                : universeError
                  ? "The research universe could not be loaded — add the company below to continue."
                  : selected
                    ? `Selected: ${selected.name} (${selected.ticker} · ${selected.exchange})`
                    : `${universe.length} compan${universe.length === 1 ? "y" : "ies"} in your research universe.`}
            </p>
          </div>

          {/* Not in the universe yet */}
          {(noMatch || universeError || showRegister) && !selected && (
            <div className="rounded-lg border border-[color:var(--ib-line)] p-4">
              {!showRegister ? (
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <p className="text-sm text-[color:var(--ib-ink-2)]">
                    Not in your research universe yet.
                  </p>
                  <button
                    type="button"
                    onClick={() => {
                      setShowRegister(true);
                      setNewName(query.trim());
                    }}
                    className="rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] hover:bg-[color:var(--ib-surface-raised)]"
                  >
                    Add this company
                  </button>
                </div>
              ) : (
                <div className="space-y-3">
                  <p className="text-sm font-medium text-[color:var(--ib-ink)]">
                    Add the company
                  </p>
                  <p className="text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                    Give the ticker exactly as the exchange lists it, and the
                    exchange separately. A combined code (for example
                    “PNDORA.CO”) prevents the issuer registry from matching, and
                    the run then finds no primary documents.
                  </p>
                  <div className="grid gap-3 sm:grid-cols-3">
                    <label className="text-xs text-[color:var(--ib-ink-3)]">
                      Company name
                      <input
                        className={`${inputCls} mt-1`}
                        value={newName}
                        onChange={(e) => setNewName(e.target.value)}
                        required
                      />
                    </label>
                    <label className="text-xs text-[color:var(--ib-ink-3)]">
                      Ticker
                      <input
                        className={`${inputCls} mt-1`}
                        value={newTicker}
                        onChange={(e) => setNewTicker(e.target.value)}
                        placeholder="PNDORA"
                        required
                      />
                    </label>
                    <label className="text-xs text-[color:var(--ib-ink-3)]">
                      Exchange
                      <input
                        className={`${inputCls} mt-1`}
                        value={newExchange}
                        onChange={(e) => setNewExchange(e.target.value)}
                        placeholder="CO"
                        required
                      />
                    </label>
                  </div>
                  {registerError && (
                    <p role="alert" className="text-sm text-rose-300">
                      {registerError}
                    </p>
                  )}
                  <div className="flex gap-2">
                    <button
                      type="button"
                      disabled={
                        registering ||
                        !newName.trim() ||
                        !newTicker.trim() ||
                        !newExchange.trim()
                      }
                      onClick={handleRegister}
                      className="rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] hover:bg-[color:var(--ib-surface-raised)] disabled:opacity-50"
                    >
                      {registering ? "Adding…" : "Add company"}
                    </button>
                    <button
                      type="button"
                      onClick={() => setShowRegister(false)}
                      className="rounded-lg px-3 py-1.5 text-sm text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
                    >
                      Cancel
                    </button>
                  </div>
                </div>
              )}
            </div>
          )}

          {/* Advanced */}
          <details
            className="rounded-lg border border-[color:var(--ib-line)] px-4 py-3"
            onToggle={(e) => setShowAdvanced((e.target as HTMLDetailsElement).open)}
          >
            <summary className="cursor-pointer list-none text-sm text-[color:var(--ib-ink-2)]">
              Advanced options{" "}
              <span className="text-xs text-[color:var(--ib-ink-3)]">
                {showAdvanced ? "" : "— data provider, research council"}
              </span>
            </summary>
            <div className="mt-4 space-y-4">
              <div>
                <label
                  htmlFor="provider"
                  className="mb-1.5 block text-sm text-[color:var(--ib-ink-2)]"
                >
                  Data provider
                </label>
                <select
                  id="provider"
                  data-testid="provider-select"
                  className={inputCls}
                  value={provider}
                  onChange={(e) => setProvider(e.target.value)}
                >
                  {DATA_PROVIDERS.map((p) => (
                    <option key={p.value} value={p.value} className="bg-[#0a0f1c]">
                      {p.productLabel}
                    </option>
                  ))}
                </select>
                <p className="mt-1.5 text-xs text-[color:var(--ib-ink-3)]">
                  {DATA_PROVIDERS.find((p) => p.value === provider)?.note}
                </p>
                {isOfflineProvider(provider) && (
                  <p className="mt-1.5 text-xs font-medium text-amber-300">
                    This provider fabricates placeholder data. Nothing it
                    produces is real research.
                  </p>
                )}
              </div>

              <label className="flex cursor-pointer items-start gap-2.5 text-sm text-[color:var(--ib-ink-2)]">
                <input
                  type="checkbox"
                  data-testid="use-llm-sections"
                  checked={useLlmSections}
                  onChange={(e) => setUseLlmSections(e.target.checked)}
                  className="mt-0.5 accent-sky-400"
                />
                <span>
                  Add LLM-drafted research sections
                  <span className="block text-xs text-[color:var(--ib-ink-3)]">
                    An optional extra drafting pass over the collected evidence.
                    It requires Azure OpenAI credentials. It is not the research
                    council — the council is configured on the server and runs
                    when the report is assembled, whatever this is set to.
                  </span>
                </span>
              </label>

              {useLlmSections && (
                <div className="ml-6">
                  <label
                    htmlFor="llm-section-provider"
                    className="mb-1.5 block text-xs text-[color:var(--ib-ink-3)]"
                  >
                    LLM backend for those sections
                  </label>
                  <select
                    id="llm-section-provider"
                    className={inputCls}
                    value={llmSectionProvider}
                    onChange={(e) => setLlmSectionProvider(e.target.value)}
                  >
                    {LLM_SECTION_PROVIDERS.map((v) => (
                      <option key={v} value={v} className="bg-[#0a0f1c]">
                        {v}
                      </option>
                    ))}
                  </select>
                </div>
              )}

              <label className="flex cursor-pointer items-start gap-2.5 text-sm text-[color:var(--ib-ink-2)]">
                <input
                  type="checkbox"
                  checked={requireSchemaValid}
                  onChange={(e) => setRequireSchemaValid(e.target.checked)}
                  className="mt-0.5 accent-sky-400"
                />
                <span>
                  Fail the run if the report schema is invalid
                  <span className="block text-xs text-[color:var(--ib-ink-3)]">
                    Off by default: a structurally incomplete report is still
                    worth reading, and it says which parts are incomplete.
                  </span>
                </span>
              </label>
            </div>
          </details>

          <div className="flex flex-wrap items-center gap-4">
            <button
              type="submit"
              data-testid="start-research"
              disabled={!selected || busy}
              className="rounded-lg bg-[color:var(--ib-ink)] px-4 py-2.5 text-sm font-medium text-[#060913] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {submitting ? "Starting…" : running ? "Researching…" : "Start research"}
            </button>
            {!selected && (
              <p className="text-xs text-[color:var(--ib-ink-3)]">
                Choose a company to continue.
              </p>
            )}
          </div>
        </form>
      </Surface>

      {/* In flight */}
      {running && job && (
        <Surface className="p-6" testId="research-progress">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm font-medium text-[color:var(--ib-ink)]">
              {job.stage_label}
              {job.company?.ticker ? (
                <span className="ml-2 font-mono text-xs font-normal text-[color:var(--ib-ink-3)]">
                  {job.company.ticker}
                </span>
              ) : null}
            </p>
            <p
              className="font-mono text-xs text-[color:var(--ib-ink-3)]"
              aria-live="polite"
              data-testid="research-elapsed"
            >
              {elapsedLabel(elapsed)} elapsed
            </p>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            The run is on the server. You can close this page, refresh it, or
            come back later — it keeps going either way, and the report will be
            in your research library when it is done.
          </p>
          <ol className="mt-4 grid gap-x-8 gap-y-1.5 sm:grid-cols-2" data-testid="research-stages">
            {job.stages
              .filter((stage) => stage.key !== "completed")
              .map((stage) => (
                <li
                  key={stage.key}
                  aria-current={stage.current ? "step" : undefined}
                  className={`flex gap-2.5 text-sm ${
                    stage.current
                      ? "text-[color:var(--ib-ink)]"
                      : stage.complete
                        ? "text-[color:var(--ib-ink-2)]"
                        : "text-[color:var(--ib-ink-3)]"
                  }`}
                >
                  <span
                    aria-hidden="true"
                    className={`mt-2 h-px w-3 shrink-0 ${
                      stage.complete || stage.current
                        ? "bg-[color:var(--ib-ink-2)]"
                        : "bg-[color:var(--ib-line-strong)]"
                    }`}
                  />
                  {stage.label}
                </li>
              ))}
          </ol>
          <p className="mt-4 border-t border-[color:var(--ib-line)] pt-3 text-xs text-[color:var(--ib-ink-3)]">
            Run reference{" "}
            <span className="font-mono">{job.job_id}</span>. This page is
            bookmarkable — reopening it reattaches to this run.
          </p>
        </Surface>
      )}

      {/* The submit itself failed — no job exists. */}
      {error && (
        <Surface className="border-rose-400/25 p-6" testId="research-error">
          <p className="text-sm font-medium text-rose-200">
            The research run could not be started
          </p>
          <p className="mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {error}
          </p>
        </Surface>
      )}

      {/* The job reached a terminal state. */}
      {job && TERMINAL.has(job.status) && (
        <Surface
          className={`p-6 sm:p-7 ${failed || interrupted ? "border-amber-400/25" : ""}`}
          testId="research-result"
        >
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            {failed
              ? "Research did not complete"
              : interrupted
                ? "Research was interrupted"
                : "Research complete"}
          </p>
          <h2 className="mt-2 text-xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
            {job.company?.name ?? job.company?.ticker ?? "Research run"}
            {job.company?.ticker ? (
              <span className="ml-2 font-mono text-sm font-normal text-[color:var(--ib-ink-3)]">
                {job.company.ticker} · {job.company.exchange}
              </span>
            ) : null}
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {job.message}
          </p>

          <dl className="mt-5 grid gap-x-8 gap-y-3 border-t border-[color:var(--ib-line)] pt-5 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">Data provider</dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {job.provider_name}
              </dd>
              {isOfflineProvider(job.provider_name) && (
                <dd className="mt-1 text-xs font-medium text-amber-300">
                  Placeholder data — not real research.
                </dd>
              )}
            </div>
            <div>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">
                Research council
              </dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {job.report?.llm_used ? "Ran" : "Did not run"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">
                Report structure
              </dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {job.report?.schema_valid === true
                  ? "Complete"
                  : job.report?.schema_valid === false
                    ? "Incomplete"
                    : "—"}
              </dd>
            </div>
          </dl>

          {(job.warnings?.length ?? 0) > 0 && (
            <div className="mt-5 rounded-lg border border-amber-400/25 p-4">
              <p className="text-sm font-medium text-amber-200">
                The run recorded {job.warnings?.length} warning(s)
              </p>
              <ul className="mt-2 space-y-1 text-xs leading-relaxed text-[color:var(--ib-ink-2)]">
                {(job.warnings ?? []).slice(0, 6).map((w, i) => (
                  <li key={i}>{w}</li>
                ))}
              </ul>
            </div>
          )}

          {(failed || interrupted) && (
            <div className="mt-5 space-y-3" data-testid="research-retry">
              <p className="text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                {interrupted
                  ? job.interrupted_reason
                  : "Nothing partial has been presented as a finished report. Anything the run collected before it failed is still recorded."}
              </p>
              <div className="flex flex-wrap items-center gap-3">
                <button
                  type="button"
                  data-testid="retry-research"
                  disabled={busy || !job.company}
                  onClick={() => {
                    const company =
                      universe.find((c) => c.id === job.company?.id) ?? selected;
                    if (company) void submit(company);
                  }}
                  className="rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] hover:bg-[color:var(--ib-surface-raised)] disabled:opacity-50"
                >
                  Retry research
                </button>
                {job.legacy_draft_report_id && (
                  <Link
                    href={`/admin/reports/${job.legacy_draft_report_id}`}
                    className="text-sm text-[color:var(--ib-ink-3)] underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
                  >
                    Technical details
                  </Link>
                )}
              </div>
              {job.error && (
                <p className="font-mono text-xs text-[color:var(--ib-ink-3)]">
                  {job.error}
                </p>
              )}
            </div>
          )}

          {reportId && !failed && (
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <PrimaryCTA
                href={`/research/reports/${reportId}`}
                testId="open-research-report"
              >
                Open the research report
              </PrimaryCTA>
              <Link
                href={`/admin/reports/${reportId}`}
                className="text-sm text-[color:var(--ib-ink-3)] underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
              >
                View technical report
              </Link>
            </div>
          )}
        </Surface>
      )}
    </div>
  );
}
