"use client";

import Link from "next/link";
import Surface from "@/components/product/Surface";
import {
  candidateConcerns,
  candidateStrengths,
  READINESS_TONE,
  READINESS_WORD,
  researchReadiness,
} from "./candidateView";
import {
  councilPlacementFor,
  internalActionLabel,
  type DiscoveryCouncilView,
} from "@/components/research/discoveryCouncilView";
import {
  candidateResearchState,
  NO_RESEARCH_LINK,
  type ResearchLinkState,
} from "@/components/research/reportResolution";
import type { DiscoveryCandidate } from "@/types/api";

/**
 * One discovery candidate, in reading order.
 *
 * Company → why it surfaced → what the council made of it → strengths →
 * concerns → how much evidence exists → what you can do next. The operator
 * facts (source-quality word, missing count, blocking count, the full standing
 * disclaimer) used to occupy the first three of those slots on every card.
 * They are still here — as one readiness line and one disclosure — because
 * they are real, but they are no longer the first thing a reader meets.
 *
 * The three CTA states are distinct and never blur:
 *
 *   screening only     → "Run full research".
 *   current research   → "Open research report", refresh alongside.
 *   legacy artefact    → "Run full research" primary; the old draft is offered
 *                        as a secondary action LABELLED for what it is, never
 *                        as "the report for this company".
 */

const CTA_BASE =
  "rounded-lg border border-[color:var(--ib-line-strong)] px-3 py-1.5 text-sm text-[color:var(--ib-ink)] transition-colors hover:bg-[color:var(--ib-surface-raised)] disabled:cursor-not-allowed disabled:opacity-50";

function Points({
  title,
  points,
  tone,
  testId,
}: {
  title: string;
  points: string[];
  tone: string;
  testId: string;
}) {
  if (points.length === 0) return null;
  return (
    <div data-testid={testId}>
      <p className={`text-xs font-medium uppercase tracking-[0.14em] ${tone}`}>
        {title}
      </p>
      <ul className="mt-1.5 space-y-1">
        {points.map((point, i) => (
          <li
            key={i}
            className="flex gap-2.5 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
          >
            <span
              aria-hidden="true"
              className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
            />
            <span className="ib-breakable">{point}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export default function CandidateCard({
  candidate,
  council,
  link = NO_RESEARCH_LINK,
  linkResolved,
  jobLabel,
  jobRunning,
  jobError,
  jobReportId,
  onResearch,
  candidateWarnings,
}: {
  candidate: DiscoveryCandidate;
  council: DiscoveryCouncilView;
  /** Resolved research state for this candidate's company. */
  link?: ResearchLinkState;
  /** False while the resolution is still in flight — the CTA waits rather than guessing. */
  linkResolved: boolean;
  jobLabel: string | null;
  jobRunning: boolean;
  jobError: string | null;
  /**
   * The report a full-analysis job produced in THIS session. It is by
   * construction newer than anything the candidate row was carrying, so it
   * takes precedence — a reader who just ran the research should be offered
   * the research they just ran, not a list refreshed a moment too early.
   */
  jobReportId: string | null;
  onResearch: () => void;
  /** Warning groups that name THIS candidate and no other. */
  candidateWarnings: string[];
}) {
  const c = candidate;
  const placement = councilPlacementFor(council, c);
  const strengths = candidateStrengths(c, placement);
  const concerns = candidateConcerns(c, placement);
  const readiness = researchReadiness(c);
  const state = candidateResearchState(link);
  const openReportId =
    jobReportId ?? (state === "current_research" ? link.currentReportId : null);

  return (
    <Surface className="p-5 sm:p-6" testId="candidate-card">
      <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-4">
        <div className="min-w-0">
          <p className="ib-breakable text-base font-medium text-[color:var(--ib-ink)]">
            {c.company_name ?? c.ticker}
          </p>
          <p className="ib-breakable mt-0.5 font-mono text-xs text-[color:var(--ib-ink-3)]">
            {c.ticker} · {c.exchange}
            {c.country ? ` · ${c.country}` : ""}
            {c.sector ? ` · ${c.sector}` : ""}
          </p>
        </div>

        <div className="flex shrink-0 flex-wrap items-center gap-3">
          {openReportId ? (
            <>
              <Link
                href={`/research/reports/${openReportId}`}
                data-testid="candidate-open-research"
                className={`ib-arrow-host ${CTA_BASE}`}
              >
                Open research report{" "}
                <span className="ib-arrow" aria-hidden="true">
                  →
                </span>
              </Link>
              <button
                type="button"
                data-testid="candidate-refresh-research"
                disabled={jobRunning}
                onClick={onResearch}
                className="text-xs text-[color:var(--ib-ink-3)] underline underline-offset-4 hover:text-[color:var(--ib-ink-2)] disabled:opacity-50"
              >
                {jobRunning ? (jobLabel ?? "Researching") : "Refresh research"}
              </button>
            </>
          ) : (
            <button
              type="button"
              data-testid="candidate-research"
              disabled={jobRunning || !linkResolved}
              onClick={onResearch}
              className={CTA_BASE}
            >
              {jobRunning ? (jobLabel ?? "Researching") : "Run full research"}
            </button>
          )}
        </div>
      </div>

      {/* What the council made of it */}
      {council.hasReview && (
        <p
          className="mt-4 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
          data-testid="candidate-council-view"
        >
          <span className="text-[color:var(--ib-ink-3)]">Council:</span>{" "}
          {placement ? (
            <>
              {internalActionLabel(placement.action)}
              {placement.rationale ? (
                <span className="ib-breakable"> — {placement.rationale}</span>
              ) : null}
            </>
          ) : (
            "the council did not place this candidate."
          )}
        </p>
      )}

      {(strengths.length > 0 || concerns.length > 0) && (
        <div className="mt-4 grid gap-4 sm:grid-cols-2">
          <Points
            title="Strengths"
            points={strengths}
            tone="text-emerald-300/80"
            testId="candidate-strengths"
          />
          <Points
            title="Concerns"
            points={concerns}
            tone="text-amber-300/80"
            testId="candidate-concerns"
          />
        </div>
      )}

      {candidateWarnings.length > 0 && (
        <ul className="mt-4 space-y-1" data-testid="candidate-warnings">
          {candidateWarnings.map((w, i) => (
            <li
              key={i}
              className="ib-breakable text-xs leading-relaxed text-amber-300/80"
            >
              {w}
            </li>
          ))}
        </ul>
      )}

      {/* Readiness + the deterministic score, secondary by design. */}
      <div className="mt-5 flex flex-wrap items-center gap-x-6 gap-y-2 border-t border-[color:var(--ib-line)] pt-3.5 text-xs">
        <span className={READINESS_TONE[readiness]}>
          {READINESS_WORD[readiness]}
        </span>
        {typeof c.candidate_score === "number" && (
          <span className="text-[color:var(--ib-ink-3)]">
            Research priority{" "}
            <span className="font-mono text-[color:var(--ib-ink-2)]">
              {c.candidate_score.toFixed(1)} / 100
            </span>
          </span>
        )}
        {c.score_explanation && (
          <details className="text-[color:var(--ib-ink-3)]">
            <summary className="cursor-pointer list-none underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
              Score components
            </summary>
            {/* The screening service's own wording, unedited. */}
            <p className="ib-breakable mt-2 max-w-prose leading-relaxed">
              {c.score_explanation}
            </p>
          </details>
        )}

        {/* State C: a linked artefact that is NOT current research. It stays
            reachable and is named for what it is. */}
        {state === "legacy_only" && link.linkedReportId && (
          <Link
            href={`/research/reports/${link.linkedReportId}`}
            data-testid="candidate-legacy-report"
            className="text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
          >
            {link.linkedKind === "superseded_research"
              ? "View superseded research"
              : "View historical screening draft"}
          </Link>
        )}
      </div>

      {jobError && (
        <p role="alert" className="mt-3 text-xs text-rose-300">
          {jobError}
        </p>
      )}
    </Surface>
  );
}
