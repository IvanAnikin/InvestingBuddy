"use client";

import { useState } from "react";

/**
 * Audience selector for the landing page.
 *
 * One panel at a time rather than six stacked marketing blocks: a reader
 * self-selects and reads only their own scenario. Built as a tablist so arrow
 * keys work and the relationship between tab and panel is announced.
 */

interface UseCase {
  id: string;
  audience: string;
  headline: string;
  workflow: string[];
  benefit: string;
  caveat?: string;
}

const USE_CASES: UseCase[] = [
  {
    id: "individual",
    audience: "Individual investors",
    headline:
      "Research more companies without spending the evening collecting filings.",
    workflow: [
      "Find the company",
      "Collect primary evidence",
      "Read current and historical financials",
      "Have the thesis challenged",
      "Review the report",
    ],
    benefit:
      "A structured research workflow, with far less of the repetitive gathering and re-typing that usually consumes it.",
  },
  {
    id: "adviser",
    audience: "Financial advisers",
    headline:
      "Understand a client's holding quickly when new results land.",
    workflow: [
      "Select the company",
      "See the latest reporting state",
      "Identify what changed",
      "Review the risks",
      "Check the source",
      "Prepare the client conversation",
    ],
    benefit:
      "The reporting state, the change and the source in one place, so a conversation starts from evidence.",
    caveat:
      "Suitability and fiduciary judgement remain entirely yours — InvestingBuddy assesses evidence, not client circumstances.",
  },
  {
    id: "pm",
    audience: "Portfolio managers",
    headline: "Move from a broad universe to a short list you have actually read.",
    workflow: [
      "Run discovery on the idea",
      "Prioritise candidates",
      "Launch deep dives",
      "Compare the evidence",
      "Challenge the thesis",
      "Take it to committee",
    ],
    benefit:
      "Comparable research packages across candidates, each traceable back to the issuer's own documents.",
  },
  {
    id: "analyst",
    audience: "Equity analysts",
    headline:
      "Spend less time gathering filings and more developing a differentiated view.",
    workflow: [
      "Document collection",
      "Financial extraction",
      "Multi-period trends",
      "Source review",
      "Research memo",
    ],
    benefit:
      "The mechanical layer of coverage is handled and auditable, leaving the judgement work to you.",
  },
  {
    id: "family-office",
    audience: "Family offices & boutique funds",
    headline: "Widen research coverage without building a large analyst team.",
    workflow: [
      "Start from a theme",
      "Generate candidates",
      "Produce standardised research packages",
      "Run human diligence",
    ],
    benefit:
      "A consistent research format across everything you look at, so coverage scales without the format drifting.",
  },
  {
    id: "team",
    audience: "Investment teams",
    headline:
      "Give everyone the same evidence base before the discussion starts.",
    workflow: [
      "Shared research library",
      "Same evidence pack per company",
      "Recorded disagreements and open questions",
      "Full provenance for anyone who wants to check",
    ],
    benefit:
      "Debate about the interpretation rather than about whose numbers are right.",
  },
];

export default function UseCaseSelector() {
  const [active, setActive] = useState(0);
  const uc = USE_CASES[active];

  return (
    <div className="grid gap-4 lg:grid-cols-[minmax(0,15rem)_minmax(0,1fr)]">
      <div
        role="tablist"
        aria-label="Who InvestingBuddy is for"
        aria-orientation="vertical"
        className="-mx-5 flex gap-1 overflow-x-auto px-5 pb-2 lg:mx-0 lg:flex-col lg:overflow-visible lg:px-0 lg:pb-0"
      >
        {USE_CASES.map((c, i) => {
          const selected = i === active;
          return (
            <button
              key={c.id}
              role="tab"
              id={`usecase-tab-${c.id}`}
              aria-selected={selected}
              aria-controls={`usecase-panel-${c.id}`}
              tabIndex={selected ? 0 : -1}
              type="button"
              onClick={() => setActive(i)}
              onKeyDown={(e) => {
                if (e.key === "ArrowDown" || e.key === "ArrowRight") {
                  e.preventDefault();
                  setActive((v) => (v + 1) % USE_CASES.length);
                } else if (e.key === "ArrowUp" || e.key === "ArrowLeft") {
                  e.preventDefault();
                  setActive((v) => (v - 1 + USE_CASES.length) % USE_CASES.length);
                }
              }}
              className={`shrink-0 whitespace-nowrap rounded-lg border px-3.5 py-2.5 text-left text-sm transition-colors lg:whitespace-normal ${
                selected
                  ? "border-[color:var(--ib-line-strong)] bg-[color:var(--ib-surface-raised)] text-[color:var(--ib-ink)]"
                  : "border-transparent text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
              }`}
            >
              {c.audience}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`usecase-panel-${uc.id}`}
        aria-labelledby={`usecase-tab-${uc.id}`}
        className="ib-panel p-5 sm:min-h-[15rem] sm:p-7"
      >
        <p className="max-w-xl text-lg leading-snug text-[color:var(--ib-ink)]">
          {uc.headline}
        </p>

        <ol className="mt-5 flex flex-wrap items-center gap-x-2 gap-y-2">
          {uc.workflow.map((step, i) => (
            <li key={step} className="flex items-center gap-2">
              <span className="rounded-md border border-[color:var(--ib-line)] px-2.5 py-1 text-xs text-[color:var(--ib-ink-2)]">
                {step}
              </span>
              {i < uc.workflow.length - 1 && (
                <span
                  aria-hidden="true"
                  className="text-xs text-[color:var(--ib-ink-3)]"
                >
                  →
                </span>
              )}
            </li>
          ))}
        </ol>

        <p className="mt-5 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          {uc.benefit}
        </p>
        {uc.caveat && (
          <p className="mt-3 border-t border-[color:var(--ib-line)] pt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            {uc.caveat}
          </p>
        )}
      </div>
    </div>
  );
}
