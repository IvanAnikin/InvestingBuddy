"use client";

import { useState } from "react";

/**
 * The seven-stage pipeline, explorable.
 *
 * Implemented as a real tablist so it is keyboard-navigable and announced
 * correctly. Every panel stays mounted-on-select rather than animated in, and
 * the container reserves its height on wide screens, so switching stages never
 * shifts the page.
 */

interface Stage {
  id: string;
  index: string;
  label: string;
  summary: string;
  items: string[];
}

const STAGES: Stage[] = [
  {
    id: "discover",
    index: "01",
    label: "Discover",
    summary:
      "Turn a written research idea into a bounded universe of companies worth looking at.",
    items: [
      "Theme, region, sector and market parsed from your own wording",
      "Universe generated from a curated, auditable company registry",
      "Candidates ranked with the reason each one surfaced",
      "Companies that were excluded — and why — stay visible",
    ],
  },
  {
    id: "source",
    index: "02",
    label: "Source",
    summary:
      "Locate the issuer's own documents and the venues its regulated disclosures appear on.",
    items: [
      "Annual reports and interim/quarterly reports",
      "Regulator and exchange disclosure venues",
      "Issuer investor-relations and newsroom pages",
      "Structured regulator filings where the issuer is registered",
    ],
  },
  {
    id: "extract",
    index: "03",
    label: "Extract",
    summary:
      "Read the documents and pull out figures with the context that makes them meaningful.",
    items: [
      "Revenue, operating profit, margin, cash flow, debt, equity",
      "Multi-year tables reconstructed from the page layout",
      "Group and segment figures kept apart",
      "Annual, half-year and quarterly periods labelled separately",
    ],
  },
  {
    id: "validate",
    index: "04",
    label: "Validate",
    summary:
      "Reconcile what was found into one state, and name what is still missing.",
    items: [
      "Each figure keeps its document, page, period, scope and currency",
      "Conflicting figures are surfaced, not silently merged",
      "Evidence quality assessed per dimension, never averaged",
      "Missing inputs recorded as findings",
    ],
  },
  {
    id: "analyze",
    index: "05",
    label: "Analyze",
    summary:
      "Several research agents read the same evidence pack from different angles.",
    items: [
      "Financial analysis",
      "Business quality and competitive position",
      "Risks and governance",
      "Bull case and bear case, argued separately",
    ],
  },
  {
    id: "challenge",
    index: "06",
    label: "Challenge",
    summary:
      "Attack the thesis before you do, and check that every claim is actually cited.",
    items: [
      "Red team argues against the emerging view",
      "Citation review flags claims without supporting evidence",
      "Source critic assesses how strong the underlying sources are",
      "Open questions and unresolved disagreements are recorded",
    ],
  },
  {
    id: "review",
    index: "07",
    label: "Review",
    summary:
      "You get a readable research report you can audit all the way back to the document.",
    items: [
      "Reporting state, key sourced financials and what changed",
      "Bull, bear, risks and red-team dissent",
      "Primary sources and the facts drawn from each",
      "Full technical provenance one click away",
    ],
  },
];

export default function WorkflowStages() {
  const [active, setActive] = useState(0);
  const stage = STAGES[active];

  return (
    <div>
      <div
        role="tablist"
        aria-label="Research pipeline stages"
        className="-mx-5 flex gap-1 overflow-x-auto px-5 pb-2 sm:mx-0 sm:flex-wrap sm:overflow-visible sm:px-0"
      >
        {STAGES.map((s, i) => {
          const selected = i === active;
          return (
            <button
              key={s.id}
              role="tab"
              id={`stage-tab-${s.id}`}
              aria-selected={selected}
              aria-controls={`stage-panel-${s.id}`}
              tabIndex={selected ? 0 : -1}
              type="button"
              onClick={() => setActive(i)}
              onKeyDown={(e) => {
                if (e.key === "ArrowRight" || e.key === "ArrowDown") {
                  e.preventDefault();
                  setActive((v) => (v + 1) % STAGES.length);
                } else if (e.key === "ArrowLeft" || e.key === "ArrowUp") {
                  e.preventDefault();
                  setActive((v) => (v - 1 + STAGES.length) % STAGES.length);
                }
              }}
              className={`flex shrink-0 items-center gap-2 rounded-lg border px-3 py-2 text-sm transition-colors ${
                selected
                  ? "border-[color:var(--ib-line-strong)] bg-[color:var(--ib-surface-raised)] text-[color:var(--ib-ink)]"
                  : "border-transparent text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
              }`}
            >
              <span className="font-mono text-[10px] opacity-70">{s.index}</span>
              {s.label}
            </button>
          );
        })}
      </div>

      <div
        role="tabpanel"
        id={`stage-panel-${stage.id}`}
        aria-labelledby={`stage-tab-${stage.id}`}
        className="ib-panel mt-3 p-5 sm:min-h-[13.5rem] sm:p-6"
      >
        <p className="max-w-2xl text-base leading-relaxed text-[color:var(--ib-ink)]">
          {stage.summary}
        </p>
        <ul className="mt-4 grid gap-x-8 gap-y-2 sm:grid-cols-2">
          {stage.items.map((item) => (
            <li
              key={item}
              className="flex gap-2.5 text-sm text-[color:var(--ib-ink-2)]"
            >
              <span
                aria-hidden="true"
                className="mt-2 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
              />
              <span>{item}</span>
            </li>
          ))}
        </ul>
      </div>
    </div>
  );
}
