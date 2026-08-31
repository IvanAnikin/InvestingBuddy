"use client";

import Link from "next/link";
import Surface from "@/components/product/Surface";
import {
  discoveryAgentLabel,
  internalActionLabel,
  internalActionShort,
  runQualityLabel,
  type CouncilPriorityEntry,
  type DiscoveryCouncilView,
} from "@/components/research/discoveryCouncilView";
import type { DiscoveryCouncilState } from "./useDiscoveryCouncil";

/**
 * The Research Council review of a whole discovery run.
 *
 * This surfaces the EXISTING run-level discovery council — the same job the
 * admin console starts, the same endpoint, the same persisted result. It does
 * not define a second council, it does not prompt a model from the browser,
 * and it does not start the council merely because a page loaded: a council run
 * costs real tokens, so a reader who wants one asks for it, exactly as an
 * operator does.
 *
 * Presentational only. The read/poll/trigger logic is in `useDiscoveryCouncil`
 * so the same review can drive this panel, the comparison table and the
 * candidate cards from ONE request.
 */

function bandTone(band: string): string {
  switch (band) {
    case "research_next":
      return "text-emerald-300";
    case "monitor_for_evidence":
      return "text-sky-300";
    case "reject_for_now":
      return "text-rose-300";
    default:
      return "text-[color:var(--ib-ink-3)]";
  }
}

function bandDot(band: string): string {
  switch (band) {
    case "research_next":
      return "bg-emerald-400";
    case "monitor_for_evidence":
      return "bg-sky-400";
    case "reject_for_now":
      return "bg-rose-400";
    default:
      return "bg-[color:var(--ib-line-strong)]";
  }
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div>
      <dt className="text-xs text-[color:var(--ib-ink-3)]">{label}</dt>
      <dd className="ib-breakable mt-0.5 text-sm text-[color:var(--ib-ink)]">
        {value}
      </dd>
    </div>
  );
}

function PriorityEntryCard({
  entry,
  index,
}: {
  entry: CouncilPriorityEntry;
  index: number;
}) {
  return (
    <li
      className="rounded-lg border border-[color:var(--ib-line)] p-4"
      data-testid="council-priority-entry"
    >
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span
          aria-hidden="true"
          className="font-mono text-xs text-[color:var(--ib-ink-3)]"
        >
          {index + 1}
        </span>
        <span className="ib-breakable text-base font-medium text-[color:var(--ib-ink)]">
          {entry.ticker ?? entry.candidateRef ?? "Candidate"}
        </span>
        {entry.exchange && (
          <span className="font-mono text-xs text-[color:var(--ib-ink-3)]">
            {entry.exchange}
          </span>
        )}
        {entry.confidence && (
          <span className="text-xs text-[color:var(--ib-ink-3)]">
            council confidence {entry.confidence}
          </span>
        )}
      </div>

      {entry.rationale && (
        <p className="ib-breakable mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {entry.rationale}
        </p>
      )}

      {entry.supporting.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Why it stands out
          </p>
          <ul className="mt-1.5 space-y-1">
            {entry.supporting.slice(0, 3).map((s, i) => (
              <li
                key={`${s.agent}-${i}`}
                className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                <span className="text-[color:var(--ib-ink-3)]">
                  {discoveryAgentLabel(s.agent)}:
                </span>{" "}
                {s.rationale ?? "agreed with this placement"}
              </li>
            ))}
          </ul>
        </div>
      )}

      {entry.concerns.length > 0 && (
        <div className="mt-3">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-amber-300/80">
            Principal concern
          </p>
          <ul className="mt-1.5 space-y-1">
            {entry.concerns.slice(0, 3).map((c, i) => (
              <li
                key={`${c.agent}-${i}`}
                className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                <span className="text-[color:var(--ib-ink-3)]">
                  {discoveryAgentLabel(c.agent)} placed it under{" "}
                  {internalActionShort(c.action).toLowerCase()}:
                </span>{" "}
                {c.rationale ?? "no reason recorded"}
              </li>
            ))}
          </ul>
        </div>
      )}
    </li>
  );
}

function CouncilBody({ view }: { view: DiscoveryCouncilView }) {
  const research = view.priority["research_next"] ?? [];
  const otherBands = ["monitor_for_evidence", "insufficient_data", "reject_for_now"];

  return (
    <div className="mt-6 space-y-7">
      {/* Overview */}
      <dl
        className="grid gap-x-8 gap-y-4 border-t border-[color:var(--ib-line)] pt-5 sm:grid-cols-3 lg:grid-cols-5"
        data-testid="council-overview"
      >
        <Stat
          label="Candidates reviewed"
          value={String(view.candidatesReviewed)}
        />
        <Stat
          label="Agents completed"
          value={
            view.agentsFailed > 0
              ? `${view.agentsCompleted} of ${view.agentsCompleted + view.agentsFailed}`
              : String(view.agentsCompleted)
          }
        />
        <Stat label="Candidate set" value={runQualityLabel(view.runQuality)} />
        <Stat label="Evidence items" value={String(view.evidenceItems)} />
        <Stat
          label="Status"
          value={view.humanReviewRequired ? "Human review required" : "Reviewed"}
        />
      </dl>

      {/* The chair never completed: say so in words, before the synthesis. */}
      {view.chairIsFallback && (
        <p
          data-testid="council-chair-fallback"
          className="rounded-lg border border-amber-400/25 px-4 py-3 text-sm leading-relaxed text-amber-200"
        >
          The chair agent did not complete
          {view.chairErrorType ? ` (${view.chairErrorType})` : ""}, so the
          synthesis below is a deterministic failure default — not the
          council&apos;s judgement about these candidates.
        </p>
      )}

      {/* Chair synthesis */}
      {view.chairSynthesis && (
        <section data-testid="council-chair-synthesis">
          <h3 className="text-sm font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Chair synthesis
          </h3>
          <p className="ib-breakable mt-3 max-w-3xl whitespace-pre-line text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {view.chairSynthesis}
          </p>
          {view.chairClaims.length > 0 && (
            <ul className="mt-3 space-y-1.5">
              {view.chairClaims.slice(0, 5).map((c, i) => (
                <li
                  key={i}
                  className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                >
                  <span
                    aria-hidden="true"
                    className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                  />
                  <span className="ib-breakable">{c.claim}</span>
                </li>
              ))}
            </ul>
          )}
        </section>
      )}

      {/* Highest research priority */}
      <section data-testid="council-research-priority">
        <h3 className="text-sm font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
          Highest research priority
        </h3>
        {research.length === 0 ? (
          <p
            className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]"
            data-testid="council-no-priority"
          >
            The council placed no candidate in the highest research-priority
            band. That is its finding, not a gap in this view.
          </p>
        ) : (
          <>
            <p
              className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]"
              data-testid="council-ordering-note"
            >
              {view.orderingEstablished
                ? "Listed in the council's own order of priority."
                : "The council places candidates into priority bands; it does not rank within a band. These are shown in the order it returned them, and their numbering is position, not rank."}
            </p>
            <ul className="mt-4 space-y-3">
              {research.map((entry, i) => (
                <PriorityEntryCard
                  key={`${entry.ticker ?? entry.candidateRef ?? i}`}
                  entry={entry}
                  index={i}
                />
              ))}
            </ul>
          </>
        )}
      </section>

      {/* The remaining bands, compactly */}
      {otherBands.some((b) => (view.priority[b] ?? []).length > 0) && (
        <section data-testid="council-other-bands">
          <h3 className="text-sm font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Everything else the council placed
          </h3>
          <div className="mt-3 space-y-4">
            {otherBands.map((band) => {
              const entries = view.priority[band] ?? [];
              if (entries.length === 0) return null;
              return (
                <div key={band}>
                  <p
                    className={`flex items-center gap-2 text-sm ${bandTone(band)}`}
                  >
                    <span
                      aria-hidden="true"
                      className={`h-1.5 w-1.5 rounded-full ${bandDot(band)}`}
                    />
                    {internalActionLabel(band)} ({entries.length})
                  </p>
                  <ul className="mt-1.5 space-y-1">
                    {entries.map((entry, i) => (
                      <li
                        key={`${band}-${entry.ticker ?? i}`}
                        className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                      >
                        <span className="font-medium text-[color:var(--ib-ink)]">
                          {entry.ticker ?? entry.candidateRef ?? "Candidate"}
                        </span>
                        {entry.rationale ? ` — ${entry.rationale}` : ""}
                      </li>
                    ))}
                  </ul>
                </div>
              );
            })}
          </div>
        </section>
      )}

      {/* Disagreement */}
      {view.disagreements.length > 0 && (
        <section data-testid="council-disagreements">
          <h3 className="text-sm font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Where the council disagreed
          </h3>
          <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
            Two agents placed the same candidate in different priority bands.
            Both positions are shown; neither was reconciled away.
          </p>
          <ul className="mt-4 space-y-3">
            {view.disagreements.map((d, i) => (
              <li
                key={`${d.ticker ?? d.candidateRef ?? i}`}
                className="rounded-lg border border-[color:var(--ib-line)] p-4"
                data-testid="council-disagreement"
              >
                <p className="ib-breakable text-sm font-medium text-[color:var(--ib-ink)]">
                  {d.ticker ?? d.candidateRef ?? "Candidate"}
                  {d.exchange ? (
                    <span className="ml-2 font-mono text-xs text-[color:var(--ib-ink-3)]">
                      {d.exchange}
                    </span>
                  ) : null}
                </p>
                <ul className="mt-2 space-y-1">
                  {d.positions.map((p, j) => (
                    <li
                      key={`${p.agent}-${j}`}
                      className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                    >
                      <span className={bandTone(p.action)}>
                        {internalActionShort(p.action)}
                      </span>{" "}
                      <span className="text-[color:var(--ib-ink-3)]">
                        — {discoveryAgentLabel(p.agent)}
                      </span>
                      {p.rationale ? `: ${p.rationale}` : ""}
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        </section>
      )}

      {/* Per-agent detail, progressively disclosed */}
      {view.agents.length > 0 && (
        <section data-testid="council-agents">
          <h3 className="text-sm font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            The council
          </h3>
          <ul className="mt-3 space-y-2">
            {view.agents.map((agent) => (
              <li key={agent.name}>
                <details
                  className="rounded-lg border border-[color:var(--ib-line)] px-4 py-3"
                  data-testid="council-agent-card"
                >
                  <summary className="flex cursor-pointer list-none flex-wrap items-baseline gap-x-3 gap-y-1">
                    <span
                      aria-hidden="true"
                      className={`h-1.5 w-1.5 shrink-0 self-center rounded-full ${
                        agent.status === "completed"
                          ? "bg-emerald-400"
                          : "bg-amber-400"
                      }`}
                    />
                    <span className="text-sm font-medium text-[color:var(--ib-ink)]">
                      {agent.label}
                    </span>
                    <span className="text-xs text-[color:var(--ib-ink-3)]">
                      {agent.status === "completed" ? "completed" : agent.status}
                      {agent.candidateNoteCount > 0
                        ? ` · ${agent.candidateNoteCount} candidate note(s)`
                        : ""}
                    </span>
                  </summary>
                  {agent.role && (
                    <p className="mt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                      {agent.role}
                    </p>
                  )}
                  {agent.summary ? (
                    <p className="ib-breakable mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                      {agent.summary}
                    </p>
                  ) : (
                    <p className="mt-2 text-sm text-[color:var(--ib-ink-3)]">
                      This agent recorded no summary.
                    </p>
                  )}
                  {agent.claims.length > 0 && (
                    <ul className="mt-2.5 space-y-1">
                      {agent.claims.slice(0, 5).map((c, i) => (
                        <li
                          key={i}
                          className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                        >
                          <span
                            aria-hidden="true"
                            className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                          />
                          <span className="ib-breakable">{c.claim}</span>
                        </li>
                      ))}
                    </ul>
                  )}
                  {agent.evidenceGaps.length > 0 && (
                    <p className="ib-breakable mt-2.5 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                      Evidence gaps: {agent.evidenceGaps.slice(0, 3).join(" · ")}
                    </p>
                  )}
                </details>
              </li>
            ))}
          </ul>
        </section>
      )}
    </div>
  );
}

export default function DiscoveryCouncilPanel({
  council,
  runIsTerminal,
  candidateCount,
}: {
  council: DiscoveryCouncilState;
  /** The council reviews a finished run's candidate set. */
  runIsTerminal: boolean;
  candidateCount: number;
}) {
  const { view, lifecycle, loading, starting, error, absent, start } = council;
  const inFlight = lifecycle === "in_flight";
  const disabled = lifecycle === "disabled";
  const canRun = runIsTerminal || candidateCount > 0;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="discovery-council"
      id="council"
    >
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
        <div className="max-w-2xl">
          <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
            Research Council review
          </h2>
          <p className="mt-2 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
            Eight agents read the whole candidate set together and decide which
            names deserve deeper research, and why. Research priority only —
            never a view on what to buy or sell.
          </p>
        </div>

        {!disabled && (
          <button
            type="button"
            onClick={() => void start()}
            disabled={starting || inFlight || loading || !canRun}
            data-testid="council-run"
            className="shrink-0 rounded-lg border border-[color:var(--ib-line-strong)] px-3.5 py-2 text-sm text-[color:var(--ib-ink)] transition-colors hover:bg-[color:var(--ib-surface-raised)] disabled:cursor-not-allowed disabled:opacity-40"
          >
            {starting || inFlight
              ? "Council reviewing…"
              : view.hasReview
                ? "Re-run council review"
                : "Run council review"}
          </button>
        )}
      </div>

      {loading && !view.hasReview && (
        <p className="mt-5 text-sm text-[color:var(--ib-ink-3)]">
          Reading the council review…
        </p>
      )}

      {disabled && (
        <p
          data-testid="council-disabled"
          className="mt-5 rounded-lg border border-[color:var(--ib-line)] px-4 py-3 text-sm leading-relaxed text-[color:var(--ib-ink-3)]"
        >
          The research council is switched off in this environment, and no
          review has been recorded for this run.
        </p>
      )}

      {error && (
        <p
          role="alert"
          data-testid="council-error"
          className="mt-5 rounded-lg border border-rose-400/25 px-4 py-3 text-sm leading-relaxed text-rose-300"
        >
          {error}
        </p>
      )}

      {inFlight && (
        <p
          data-testid="council-in-flight"
          aria-live="polite"
          className="mt-5 rounded-lg border border-[color:var(--ib-line)] px-4 py-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
        >
          The council is reviewing this run
          {view.agentsCompleted + view.agentsFailed > 0
            ? ` — ${view.agentsCompleted} of 8 agents finished`
            : ""}
          . This takes a few minutes; the page updates itself.
        </p>
      )}

      {lifecycle === "failed" && !view.hasReview && (
        <p
          data-testid="council-failed"
          className="mt-5 rounded-lg border border-amber-400/25 px-4 py-3 text-sm leading-relaxed text-amber-200"
        >
          The council review did not complete
          {view.error ? ` (${view.error})` : ""}. Nothing was recorded for this
          run — you can run it again.
        </p>
      )}

      {!loading && !disabled && !inFlight && absent && (
        <p
          data-testid="council-not-run"
          className="mt-5 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]"
        >
          {canRun
            ? "The council has not reviewed this run yet. The candidates below are the deterministic screen only — no agent has compared them."
            : "The council reviews a finished run. This one has produced no candidates yet."}
        </p>
      )}

      {view.hasReview && <CouncilBody view={view} />}

      {view.hasReview && (
        <p className="mt-7 border-t border-[color:var(--ib-line)] pt-4 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          Internal research triage requiring human review — the council decides
          research priority, never an investment action. The raw council
          payload, token accounting and per-agent failure detail are on the{" "}
          <Link
            href="/admin/discovery"
            className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
          >
            admin discovery workspace
          </Link>
          .
        </p>
      )}
    </Surface>
  );
}
