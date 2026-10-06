"use client";

import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import Surface from "@/components/product/Surface";
import DiscoveryIntentPanel from "@/components/research/discovery/DiscoveryIntentPanel";
import {
  createThesisDiscoveryRun,
  listDiscoveryRuns,
  listSupportedFilters,
  listSupportedThemes,
  parseThesis,
} from "@/lib/api";
import { formatDate } from "@/lib/format";
import {
  DISCOVERY_DEFAULTS,
  buildThesisDiscoveryRequest,
} from "@/lib/workflows";
import type {
  DiscoveryRun,
  ParseThesisResponse,
  SupportedFiltersResponse,
  SupportedThemesResponse,
} from "@/types/api";
import DiscoveryRunView from "./DiscoveryRunView";
import { discoveryRunPath } from "./runRoute";

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

function createdAtMs(run: DiscoveryRun): number {
  const ms = Date.parse(run.created_at);
  return Number.isNaN(ms) ? 0 : ms;
}

/**
 * `base` plus every run in `extra` it does not already hold, newest first.
 * Returns `base` itself when nothing is added, so a poll that re-reports a
 * known run causes no re-render.
 */
function mergeRuns(base: DiscoveryRun[], extra: DiscoveryRun[]): DiscoveryRun[] {
  const known = new Set(base.map((r) => r.id));
  const added = extra.filter((r) => !known.has(r.id));
  if (added.length === 0) return base;
  return [...base, ...added].sort((a, b) => createdAtMs(b) - createdAtMs(a));
}

export default function DiscoveryWorkbench({ runId }: { runId?: string }) {
  const router = useRouter();

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
  // Which run is open is the URL's business (`runId`); this is only the list
  // the selector offers.
  const [runs, setRuns] = useState<DiscoveryRun[]>([]);
  const [runsLoaded, setRunsLoaded] = useState(false);
  // The run just created here, handed to its run view so it shows at once.
  const [createdRun, setCreatedRun] = useState<DiscoveryRun | null>(null);
  // True from the moment a run is being created until its address is the
  // page, so the bare page's "open the newest run" step cannot race it — not
  // even when the run list arrives while the create request is in flight.
  const navigatingToRun = useRef(false);

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
  //
  // Text too short to parse is handled where the text changes
  // (`updateThesis`), not here: clearing state synchronously inside an effect
  // costs a second render for nothing.
  function clearInferred() {
    if (!regionEdited.current) setRegion("");
    if (!countryEdited.current) setCountry("");
    if (!sectorEdited.current) setSector("");
  }

  function updateThesis(value: string) {
    setThesis(value);
    if (value.trim().length < 3) {
      setDetected(null);
      setParseFailed(false);
      clearInferred();
    }
  }

  useEffect(() => {
    const text = thesis.trim();
    if (text.length < 3) return;

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
        if (!regionEdited.current) setRegion("");
        if (!countryEdited.current) setCountry("");
        if (!sectorEdited.current) setSector("");
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

  // The reader's recent runs, for the run selector — and, on the bare
  // `/research/discover`, to open the newest one.
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const data = await listDiscoveryRuns();
        if (cancelled) return;
        // The list is the newest 50. A run opened by its URL that is older than
        // that has already been merged in; keep it.
        setRuns((prev) => mergeRuns(data.runs, prev));
      } catch {
        /* non-fatal — the selector just has less to offer */
      } finally {
        if (!cancelled) setRunsLoaded(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  // A run opened by its URL is added to the selector even when it is not
  // among the newest 50 the list returns.
  //
  // The create-time snapshot has done its job once the run's own poll answers;
  // dropping it means a later Back/Forward remount shows the polled run, not
  // the stale "pending" envelope from creation.
  const handleRunLoaded = useCallback((run: DiscoveryRun) => {
    setRuns((prev) => mergeRuns(prev, [run]));
    setCreatedRun((prev) => (prev?.id === run.id ? null : prev));
  }, []);

  // Once a run's own address is showing, a later visit to the bare page may
  // move to the newest run again.
  useEffect(() => {
    if (runId !== undefined) navigatingToRun.current = false;
  }, [runId]);

  // The bare `/research/discover` opens the newest run by REPLACING the
  // address, so Back does not bounce the reader through this page again. With
  // no runs at all it stays on the empty form.
  useEffect(() => {
    if (runId !== undefined || !runsLoaded || navigatingToRun.current) return;
    const latest = runs[0];
    if (latest) router.replace(discoveryRunPath(latest.id));
  }, [runId, runsLoaded, runs, router]);

  function openRun(id: string) {
    if (id === runId) return;
    router.push(discoveryRunPath(id));
  }

  const otherRuns = runs.some((r) => r.id !== runId);
  const selectedInList = Boolean(runId && runs.some((r) => r.id === runId));

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault();
    if (!thesis.trim()) return;
    setSubmitting(true);
    setSubmitError(null);
    navigatingToRun.current = true;
    try {
      // Built by the SAME helper the admin console uses, so an identical
      // description produces an identical run on either surface — including
      // the lookback window and provider the admin console has always sent.
      // V3.19 — only filters the READER set travel as filters. The backend reads the
      // sentence itself into a multi-valued Discovery Intent ("Europe, North America
      // and Australia"); echoing the single detected value back as an explicit
      // selector would override the text and silently narrow it to one region.
      const created = await createThesisDiscoveryRun(
        buildThesisDiscoveryRequest({
          thesisText: thesis,
          region: regionEdited.current ? region : undefined,
          country: countryEdited.current ? country : undefined,
          sector: sectorEdited.current ? sector : undefined,
          industry,
          maxUniverseSize: parseInt(maxUniverse, 10) || undefined,
          maxCandidates: parseInt(maxCandidates, 10) || undefined,
        }),
      );
      // The new run's own address becomes the page. The run is handed to the
      // run view directly so it shows before its first poll returns.
      setCreatedRun(created);
      const path = discoveryRunPath(created.id);
      // From the bare page, REPLACE: pushing would leave `/research/discover`
      // in history, and Back would land there only to be sent straight on to
      // the newest run — Back would appear to do nothing. From another run's
      // address, push, so Back returns to that run.
      if (runId === undefined) router.replace(path);
      else router.push(path);
    } catch (e) {
      navigatingToRun.current = false;
      setSubmitError(
        e instanceof Error ? e.message : "Could not start the discovery run.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  const examples = themes?.examples?.length ? themes.examples : FALLBACK_EXAMPLES;

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
              onChange={(e) => updateThesis(e.target.value)}
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
                onClick={() => updateThesis(example)}
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

          {detected?.discovery_intent && !detected.needs_narrowing && (
            <DiscoveryIntentPanel
              intent={detected.discovery_intent}
              openDiscovery={detected.dynamic_discovery_enabled ?? null}
            />
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
            {otherRuns && (
              <label className="flex min-w-0 max-w-full flex-wrap items-center gap-1.5 text-xs text-[color:var(--ib-ink-3)]">
                Runs
                <select
                  data-testid="discovery-run-select"
                  className="min-w-0 max-w-full rounded-lg border border-[color:var(--ib-line)] bg-[color:var(--ib-surface)] px-2 py-1.5 text-xs text-[color:var(--ib-ink-2)]"
                  value={selectedInList ? (runId ?? "") : ""}
                  onChange={(e) => {
                    if (e.target.value) openRun(e.target.value);
                  }}
                >
                  {!selectedInList && (
                    <option value="" disabled className="bg-[#0a0f1c]">
                      Choose a run
                    </option>
                  )}
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

      {runId && (
        <DiscoveryRunView
          key={runId}
          runId={runId}
          initialRun={createdRun?.id === runId ? createdRun : null}
          onRunLoaded={handleRunLoaded}
        />
      )}
    </div>
  );
}
