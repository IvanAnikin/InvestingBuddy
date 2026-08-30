"use client";

import Link from "next/link";
import { useEffect, useId, useMemo, useRef, useState } from "react";
import PrimaryCTA from "@/components/product/PrimaryCTA";
import Surface from "@/components/product/Surface";
import {
  createCompany,
  fetchCompanies,
  generateFinalReportFromReport,
  runAnalysis,
} from "@/lib/api";
import {
  DATA_PROVIDERS,
  LLM_SECTION_PROVIDERS,
  PROVIDER_FREE_REAL,
  buildCompanyAnalysisRequest,
  isOfflineProvider,
} from "@/lib/workflows";
import type { Company, WorkflowRunResponse } from "@/types/api";

// The research universe is the set of companies the backend can analyse: the
// workflow resolves a company by id or by (ticker, exchange) and fails closed
// when it finds neither. Rather than sending the reader to a second page to
// discover that, this form searches the universe and — when the company is not
// in it yet — registers it inline through the same endpoint the admin uses.
const UNIVERSE_PAGE_SIZE = 200;

// The clean flow runs the SAME two backend steps the admin console runs, in the
// same order: the company-analysis workflow, then the final-report generator.
// The admin console makes them two button presses on two pages; here they are
// one action — but they are the same two endpoints, and the reader is told
// which one is in flight.
type RunPhase = "analysis" | "report";

const PHASE_STAGES: Record<RunPhase, string[]> = {
  analysis: [
    "Resolving company identity",
    "Locating the issuer's primary documents",
    "Extracting period-labelled financial facts",
    "Retrieving regulated disclosures",
    "Assembling and citing the evidence",
  ],
  report: [
    "Reconciling the evidence into one research state",
    "Running the research council, if enabled on this server",
    "Assembling the structured research report",
  ],
};

const PHASE_LABEL: Record<RunPhase, string> = {
  analysis: "Collecting and extracting evidence",
  report: "Assembling the research report",
};

const inputCls =
  "w-full rounded-lg border border-[color:var(--ib-line)] bg-[color:var(--ib-surface)] px-3.5 py-2.5 text-sm text-[color:var(--ib-ink)] placeholder:text-[color:var(--ib-ink-3)] focus:border-[color:var(--ib-line-strong)] focus:outline-none";

function normalise(value: string): string {
  return value.trim().toLowerCase();
}

export default function CompanyResearchForm() {
  const listboxId = useId();

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

  const [phase, setPhase] = useState<RunPhase | null>(null);
  const [elapsed, setElapsed] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<WorkflowRunResponse | null>(null);
  // The id of the STRUCTURED report the clean view renders. Null until the
  // final-report step succeeds — the draft the workflow writes is not one.
  const [finalReportId, setFinalReportId] = useState<string | null>(null);
  const [reportError, setReportError] = useState<string | null>(null);

  const running = phase !== null;

  const wrapperRef = useRef<HTMLDivElement | null>(null);

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

  // A running analysis is a long, synchronous request. Showing the elapsed time
  // is the honest alternative to a spinner that implies it is nearly done.
  useEffect(() => {
    if (!running) return;
    const started = Date.now();
    const id = window.setInterval(
      () => setElapsed(Math.floor((Date.now() - started) / 1000)),
      1000,
    );
    return () => window.clearInterval(id);
  }, [running]);

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

  function pick(company: Company) {
    setSelected(company);
    setQuery(`${company.name} · ${company.ticker}`);
    setOpenList(false);
    setShowRegister(false);
  }

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
      pick(company);
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

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!selected) return;

    setElapsed(0);
    setError(null);
    setReportError(null);
    setResult(null);
    setFinalReportId(null);

    // The request is built by the SAME helper the admin console uses, from the
    // selected Company record — never re-derived from what the input displays.
    const request = buildCompanyAnalysisRequest({
      company: selected,
      providerName: provider,
      useLlmSections,
      llmProvider: llmSectionProvider,
      requireSchemaValid,
    });

    setPhase("analysis");
    let run: WorkflowRunResponse;
    try {
      run = await runAnalysis(request);
      setResult(run);
    } catch (e) {
      setError(e instanceof Error ? e.message : "The research run failed.");
      setPhase(null);
      return;
    }

    // The workflow writes a DETERMINISTIC DRAFT. The structured report — the
    // one the research view renders, and the one the council contributes to —
    // is produced by the final-report generator, exactly as the admin console
    // does it from the draft's own page. Skipping this step would leave the
    // reader on a draft with no structured content.
    if (!run.draft_report_id) {
      setPhase(null);
      return;
    }

    setPhase("report");
    try {
      const finalReport = await generateFinalReportFromReport(run.draft_report_id);
      setFinalReportId(finalReport.report_id);
    } catch (e) {
      // The evidence run DID succeed. Say exactly that, and point at what
      // exists — never present a half-finished run as a finished report.
      setReportError(
        e instanceof Error
          ? e.message
          : "The research report could not be assembled.",
      );
    } finally {
      setPhase(null);
    }
  }

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
              disabled={running}
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
                  pick(matches[0]);
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
                      onClick={() => pick(company)}
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
              disabled={!selected || running}
              className="rounded-lg bg-[color:var(--ib-ink)] px-4 py-2.5 text-sm font-medium text-[#060913] transition-colors hover:bg-white disabled:cursor-not-allowed disabled:opacity-40"
            >
              {running ? "Researching…" : "Start research"}
            </button>
            {!selected && (
              <p className="text-xs text-[color:var(--ib-ink-3)]">
                Choose a company to continue.
              </p>
            )}
          </div>
        </form>
      </Surface>

      {/* In-flight */}
      {running && (
        <Surface className="p-6" testId="research-progress">
          <div className="flex flex-wrap items-baseline justify-between gap-2">
            <p className="text-sm font-medium text-[color:var(--ib-ink)]">
              {phase ? PHASE_LABEL[phase] : "Research in progress"}
              <span className="ml-2 text-xs font-normal text-[color:var(--ib-ink-3)]">
                step {phase === "report" ? "2" : "1"} of 2
              </span>
            </p>
            <p
              className="font-mono text-xs text-[color:var(--ib-ink-3)]"
              aria-live="polite"
            >
              {Math.floor(elapsed / 60)}m {String(elapsed % 60).padStart(2, "0")}s
              elapsed
            </p>
          </div>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            The full pipeline runs in one pass and typically takes several
            minutes — most of it is spent fetching and reading the issuer&apos;s
            own documents. Leave this page open; the run continues on the server
            either way and the report will appear in your research library.
          </p>
          <ol className="mt-4 grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
            {(phase ? PHASE_STAGES[phase] : []).map((stage: string) => (
              <li
                key={stage}
                className="flex gap-2.5 text-sm text-[color:var(--ib-ink-3)]"
              >
                <span
                  aria-hidden="true"
                  className="mt-2 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                />
                {stage}
              </li>
            ))}
          </ol>
        </Surface>
      )}

      {/* Error */}
      {error && (
        <Surface className="border-rose-400/25 p-6" testId="research-error">
          <p className="text-sm font-medium text-rose-200">
            The research run did not complete
          </p>
          <p className="mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {error}
          </p>
          <p className="mt-3 text-xs text-[color:var(--ib-ink-3)]">
            Nothing partial has been presented as a finished report. If the run
            reached the council before failing, a draft may still appear in the{" "}
            <Link
              href="/research/reports"
              className="underline underline-offset-4"
            >
              research library
            </Link>
            .
          </p>
        </Surface>
      )}

      {/* Result */}
      {result && (
        <Surface className="p-6 sm:p-7" testId="research-result">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            {finalReportId ? "Research complete" : "Evidence run complete"}
          </p>
          <h2 className="mt-2 text-xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
            {result.company_name ?? selected?.name}
            {result.ticker ? (
              <span className="ml-2 font-mono text-sm font-normal text-[color:var(--ib-ink-3)]">
                {result.ticker}
              </span>
            ) : null}
          </h2>
          <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {result.summary}
          </p>

          <dl className="mt-5 grid gap-x-8 gap-y-3 border-t border-[color:var(--ib-line)] pt-5 sm:grid-cols-3">
            <div>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">Data provider</dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {result.provider_name ?? "—"}
              </dd>
              {result.provider_name && isOfflineProvider(result.provider_name) && (
                <dd className="mt-1 text-xs font-medium text-amber-300">
                  Placeholder data — not real research.
                </dd>
              )}
            </div>
            <div>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">
                LLM research sections
              </dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {result.llm_used ? "Drafted" : "Not used"}
              </dd>
            </div>
            <div>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">
                Report structure
              </dt>
              <dd className="text-sm text-[color:var(--ib-ink)]">
                {result.schema_valid ? "Complete" : "Incomplete"}
              </dd>
            </div>
          </dl>

          {(result.research_team_warnings.length > 0 ||
            result.analysis_council_warnings.length > 0) && (
            <div className="mt-5 rounded-lg border border-amber-400/25 p-4">
              <p className="text-sm font-medium text-amber-200">
                The run recorded {result.research_team_warnings.length +
                  result.analysis_council_warnings.length}{" "}
                warning(s)
              </p>
              <ul className="mt-2 space-y-1 text-xs leading-relaxed text-[color:var(--ib-ink-2)]">
                {[
                  ...result.research_team_warnings,
                  ...result.analysis_council_warnings,
                ]
                  .slice(0, 6)
                  .map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
              </ul>
            </div>
          )}

          {/* The report step failed after a successful evidence run. Report
              exactly that: the evidence exists, the structured report does
              not, and the draft is reachable. Never a finished-looking link
              to something that was not finished. */}
          {reportError && (
            <div
              className="mt-5 rounded-lg border border-amber-400/25 p-4"
              data-testid="report-step-error"
            >
              <p className="text-sm font-medium text-amber-200">
                The evidence run finished, but the research report could not be
                assembled
              </p>
              <p className="mt-2 text-xs leading-relaxed text-[color:var(--ib-ink-2)]">
                {reportError}
              </p>
              {result.draft_report_id && (
                <p className="mt-2 text-xs text-[color:var(--ib-ink-3)]">
                  The evidence draft this run produced is at{" "}
                  <Link
                    href={`/admin/reports/${result.draft_report_id}`}
                    className="underline underline-offset-4"
                  >
                    the technical report page
                  </Link>
                  , where it can be assembled again.
                </p>
              )}
            </div>
          )}

          {finalReportId && (
            <div className="mt-6 flex flex-wrap items-center gap-3">
              <PrimaryCTA
                href={`/research/reports/${finalReportId}`}
                testId="open-research-report"
              >
                Open the research report
              </PrimaryCTA>
              <Link
                href={`/admin/reports/${finalReportId}`}
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
