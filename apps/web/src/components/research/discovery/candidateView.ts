// Reader-facing derivation for a discovery candidate row.
//
// The candidate card used to lead with the things an operator needs — source
// quality, missing count, blocking count, and the full standing disclaimer
// under every single card. A reader needs a different order: what the company
// is, why it surfaced, what the council made of it, and whether there is
// enough evidence to research it properly.
//
// Everything here reads fields the screening pass already wrote. The scoring
// service emits a CLOSED label vocabulary (`ALLOWED_CANDIDATE_LABELS`), so
// translating those labels into words is a lookup, not an interpretation. No
// score is combined with another, and nothing is inferred from a company name.

import type { DiscoveryCandidate } from "@/types/api";
import {
  comparisonDimensionLabel,
  councilPlacementFor,
  discoveryAgentLabel,
  type CouncilPriorityEntry,
  type DiscoveryCouncilView,
} from "@/components/research/discoveryCouncilView";

/** The screening labels that describe why a candidate looks interesting. */
const STRENGTH_LABELS: Record<string, string> = {
  positive_momentum_candidate: "Positive price momentum",
  catalyst_rich_candidate: "Recent disclosure activity",
  fundamentals_available: "Fundamentals sourced",
};

/** The screening labels that describe a limit on what could be screened. */
const CONCERN_LABELS: Record<string, string> = {
  data_sparse: "Sparse data for this issuer",
  research_incomplete: "Screening evidence incomplete",
};

/**
 * Labels that are a standing property of every candidate, not a finding.
 * Rendering these per card is what turned the cards into disclaimer walls.
 */
const STANDING_LABELS = new Set([
  "internal_research_candidate",
  "needs_human_review",
]);

export type ResearchReadiness = "ready" | "partial" | "thin";

export const READINESS_WORD: Record<ResearchReadiness, string> = {
  ready: "Ready for full research",
  partial: "Partly evidenced",
  thin: "Thin evidence",
};

export const READINESS_TONE: Record<ResearchReadiness, string> = {
  ready: "text-emerald-300",
  partial: "text-sky-300",
  thin: "text-amber-300",
};

/**
 * How much evidence the screen actually found for this candidate.
 *
 * Read from the screen's own outputs — a blocking gap is the screen saying it
 * could not complete something, and `source_quality` is its own assessment of
 * what backed the numbers. Nothing is averaged; the weakest signal decides,
 * the same way the report's overall evidence label does.
 */
export function researchReadiness(c: DiscoveryCandidate): ResearchReadiness {
  const blocking = c.blocking_gap_count ?? 0;
  const quality = (c.source_quality ?? "").toLowerCase();
  if (blocking > 0 || quality === "weak" || quality === "insufficient") {
    return "thin";
  }
  if (quality === "strong" && (c.missing_info_count ?? 0) === 0) return "ready";
  return "partial";
}

/**
 * What could make this candidate more valuable.
 *
 * The council's own `upside_drivers` come first — they are a judgement about
 * THIS business. A screening label is a property of the data, so it follows,
 * and only when the council said nothing. A gap count never appears here at
 * all: "fewer missing fields" is not a reason to research a company.
 */
export function candidateStrengths(
  c: DiscoveryCandidate,
  placement: CouncilPriorityEntry | null,
): string[] {
  const out: string[] = [...(placement?.upsideDrivers ?? [])];

  if (out.length === 0) {
    for (const s of placement?.supporting ?? []) {
      if (s.rationale) out.push(`${discoveryAgentLabel(s.agent)}: ${s.rationale}`);
    }
  }
  if (out.length === 0) {
    for (const label of c.labels_json ?? []) {
      if (STANDING_LABELS.has(label)) continue;
      const word = STRENGTH_LABELS[label];
      if (word) out.push(word);
    }
  }
  return out.slice(0, 3);
}

/** What could pressure it. Same ordering rule, same exclusion of gap counts. */
export function candidateConcerns(
  c: DiscoveryCandidate,
  placement: CouncilPriorityEntry | null,
): string[] {
  const out: string[] = [...(placement?.downsideDrivers ?? [])];

  if (out.length === 0) {
    for (const x of placement?.concerns ?? []) {
      if (x.rationale) out.push(`${discoveryAgentLabel(x.agent)}: ${x.rationale}`);
    }
  }
  if (out.length === 0) {
    for (const label of c.labels_json ?? []) {
      if (STANDING_LABELS.has(label)) continue;
      const word = CONCERN_LABELS[label];
      if (word) out.push(word);
    }
  }
  return out.slice(0, 2);
}

/**
 * A comparison DIMENSION is only offered when at least one candidate actually
 * carries it. An empty column invites the reader to compare nothing.
 */
export interface ComparisonDimension {
  key: string;
  label: string;
  /** A short note explaining what the column is, shown once above the table. */
  hint: string;
  /** Numeric columns right-align; text columns do not. */
  numeric: boolean;
  value: (c: DiscoveryCandidate, view: DiscoveryCouncilView) => string;
}

function score(value: number | null | undefined): string {
  return typeof value === "number" ? value.toFixed(1) : "—";
}

const ALL_DIMENSIONS: (ComparisonDimension & {
  supported: (candidates: DiscoveryCandidate[], view: DiscoveryCouncilView) => boolean;
})[] = [
  {
    key: "council",
    label: "Council view",
    hint: "Where the research council placed this candidate.",
    numeric: false,
    supported: (_c, view) => view.hasReview,
    value: (c, view) => {
      const placement = councilPlacementFor(view, c);
      return placement ? placement.action : "not placed";
    },
  },
  {
    key: "stands_out",
    label: "Stands out on",
    hint: "The business dimension the council judged strongest here.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => councilPlacementFor(view, c)?.strongestDimension),
    value: (c, view) =>
      comparisonDimensionLabel(
        councilPlacementFor(view, c)?.strongestDimension,
      ) ?? "—",
  },
  {
    key: "key_signal",
    label: "Key financial signal",
    hint: "The single number the council judged most telling for this issuer.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => councilPlacementFor(view, c)?.keyFinancialSignal),
    value: (c, view) =>
      councilPlacementFor(view, c)?.keyFinancialSignal ?? "—",
  },
  {
    key: "priority",
    label: "Research priority",
    hint: "The deterministic screening score, 0–100. Not a rating.",
    numeric: true,
    supported: (candidates) =>
      candidates.some((c) => typeof c.candidate_score === "number"),
    value: (c) => score(c.candidate_score),
  },
  {
    key: "thesis",
    label: "Thesis fit",
    hint: "How closely the screen matched this candidate to the description.",
    numeric: true,
    supported: (candidates) =>
      candidates.some((c) => typeof c.thesis_relevance_score === "number"),
    value: (c) => score(c.thesis_relevance_score),
  },
  {
    key: "evidence",
    label: "Evidence confidence",
    hint: "The screen's own assessment of what backed its numbers.",
    numeric: false,
    supported: (candidates) => candidates.some((c) => c.source_quality),
    value: (c) => c.source_quality ?? "not assessed",
  },
  {
    key: "disclosures",
    label: "Disclosure coverage",
    hint: "Whether recent official disclosures were found for the issuer.",
    numeric: false,
    supported: (candidates) =>
      candidates.some((c) => c.catalyst_coverage_status),
    value: (c) => c.catalyst_coverage_status ?? "not assessed",
  },
  {
    key: "gaps",
    label: "Known gaps",
    hint: "Fields the screen could not source. These reduce confidence in the comparison — they are not the comparison.",
    numeric: true,
    supported: (candidates) =>
      candidates.some(
        (c) => c.missing_info_count != null || c.blocking_gap_count != null,
      ),
    value: (c) =>
      `${c.missing_info_count ?? 0}${
        (c.blocking_gap_count ?? 0) > 0 ? ` · ${c.blocking_gap_count} blocking` : ""
      }`,
  },
  {
    key: "readiness",
    label: "Research readiness",
    hint: "Whether there is enough evidence to start a full analysis.",
    numeric: false,
    supported: () => true,
    value: (c) => READINESS_WORD[researchReadiness(c)],
  },
];

/**
 * The dimensions worth showing for THIS cohort.
 *
 * Never a composite: each column reports one thing the backend measured, and
 * two columns are never blended into a single "overall" number.
 */
export function comparisonDimensions(
  candidates: DiscoveryCandidate[],
  view: DiscoveryCouncilView,
): ComparisonDimension[] {
  return ALL_DIMENSIONS.filter((d) => d.supported(candidates, view)).map(
    (d) => ({
      key: d.key,
      label: d.label,
      hint: d.hint,
      numeric: d.numeric,
      value: d.value,
    }),
  );
}

/**
 * Warning groups split into "affects the whole cohort" and "affects one
 * candidate".
 *
 * The backend already deduplicates and classifies warnings, and each group
 * names the tickers it affects. A group naming exactly one candidate is that
 * candidate's limitation; everything else is a limitation of the run, and
 * repeating it under all six cards is what made the page unreadable.
 */
export function splitWarningSubjects(
  subjects: string[],
): { cohortWide: boolean; ticker: string | null } {
  const named = subjects.filter(Boolean);
  if (named.length === 1) return { cohortWide: false, ticker: named[0] };
  return { cohortWide: true, ticker: null };
}
