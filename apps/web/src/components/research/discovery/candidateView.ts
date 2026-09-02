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
  councilPlacementFor,
  discoveryAgentLabel,
  type CouncilPriorityEntry,
  type DiscoveryCouncilView,
} from "@/components/research/discoveryCouncilView";
import { isEvidenceStatement } from "@/components/research/investorSignal";

/** The screening labels that describe why a candidate looks interesting. */
const STRENGTH_LABELS: Record<string, string> = {
  positive_momentum_candidate: "Positive price momentum",
  catalyst_rich_candidate: "Recent disclosure activity",
  fundamentals_available: "Fundamentals sourced",
};

/**
 * Screening labels that describe a limit on what could be SCREENED.
 *
 * These are not economic. "Sparse data for this issuer" says something about
 * this platform's coverage, not about the company's prospects, and the live
 * discovery page rendered it under "Could pressure value" — telling a reader
 * that thin evidence is a reason the business might be worth less. They are
 * reported as research limitations instead, which is what they are.
 */
const RESEARCH_LIMITATION_LABELS: Record<string, string> = {
  data_sparse: "Sparse evidence for this issuer",
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
  const out: string[] = economicOnly(placement?.upsideDrivers ?? []);

  if (out.length === 0) {
    for (const s of placement?.supporting ?? []) {
      if (s.rationale && !isEvidenceStatement(s.rationale)) {
        out.push(`${discoveryAgentLabel(s.agent)}: ${s.rationale}`);
      }
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

/**
 * Keep only what is about the BUSINESS.
 *
 * The same rule the reader-facing report uses, applied to the discovery
 * council's own driver lists. It is needed here for the same reason: on an
 * evidence-starved cohort the council writes "data gaps prevent risk
 * assessment" and "no price data limits momentum insights" into
 * `downside_drivers`, and rendering those under "Could pressure value" tells a
 * reader that this platform's coverage is a reason the company might be worth
 * less. Measured against a real local council run over six European luxury
 * names: every one of its twelve downside drivers was one of these.
 */
function economicOnly(points: string[]): string[] {
  return points.filter((point) => point.trim() && !isEvidenceStatement(point));
}

/**
 * The council's drivers that turned out to be about the EVIDENCE.
 *
 * Nothing is discarded — they are reported under research limitations, where
 * they describe what they actually describe.
 */
export function councilEvidenceNotes(
  placement: CouncilPriorityEntry | null,
): string[] {
  const out: string[] = [];
  for (const point of [
    ...(placement?.upsideDrivers ?? []),
    ...(placement?.downsideDrivers ?? []),
  ]) {
    if (point.trim() && isEvidenceStatement(point)) out.push(point);
  }
  return out;
}

/**
 * What could pressure the BUSINESS.
 *
 * The council's own `downside_drivers` first, then — only if it said nothing —
 * the reasons other agents placed the candidate in a lower band. A screening
 * label never appears here, and neither does a gap count: an evidence
 * limitation is not a reason a company might become less valuable, and
 * presenting it as one is a category error the reader has no way to see
 * through. When the council established no downside, this is EMPTY, and the
 * card says "Not established" rather than borrowing something.
 */
export function candidateConcerns(
  c: DiscoveryCandidate,
  placement: CouncilPriorityEntry | null,
): string[] {
  void c;
  const out: string[] = economicOnly(placement?.downsideDrivers ?? []);

  if (out.length === 0) {
    for (const x of placement?.concerns ?? []) {
      if (x.rationale && !isEvidenceStatement(x.rationale)) {
        out.push(`${discoveryAgentLabel(x.agent)}: ${x.rationale}`);
      }
    }
  }
  return out.slice(0, 2);
}

/**
 * What limited the SCREENING, in the screen's own words.
 *
 * Reported under research limitations, beside the evidence-confidence line —
 * never as an economic driver in either direction.
 */
export function candidateResearchLimitations(
  c: DiscoveryCandidate,
  placement: CouncilPriorityEntry | null = null,
): string[] {
  const out: string[] = [];
  for (const label of c.labels_json ?? []) {
    if (STANDING_LABELS.has(label)) continue;
    const word = RESEARCH_LIMITATION_LABELS[label];
    if (word) out.push(word);
  }
  // Anything the council wrote as a "driver" that turned out to be about the
  // evidence lands here too, in its own words.
  out.push(...councilEvidenceNotes(placement));
  return out;
}

/**
 * The value a dimension shows when the COUNCIL established nothing.
 *
 * "Not established" is a real answer and the honest one. Substituting evidence
 * completeness for it — showing a gap count where a growth signal belongs — is
 * what made the comparison a comparison of data packages.
 */
export const NOT_ESTABLISHED = "Not established";

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

/**
 * The council's own words for a candidate on one dimension.
 *
 * It is returned only when the council named THAT dimension as the candidate's
 * strongest; the key financial signal is the sentence it wrote about it. This
 * layer never infers a growth or margin signal from a score.
 */
function dimensionSignal(
  view: DiscoveryCouncilView,
  candidate: DiscoveryCandidate,
  dimension: string,
): string | null {
  const placement = councilPlacementFor(view, candidate);
  if (!placement || placement.strongestDimension !== dimension) return null;
  return placement.keyFinancialSignal ?? placement.rationale ?? null;
}

const ALL_DIMENSIONS: (ComparisonDimension & {
  supported: (candidates: DiscoveryCandidate[], view: DiscoveryCouncilView) => boolean;
})[] = [
  // ── What the council concluded about the BUSINESS ─────────────────────
  //
  // These come first because they are the comparison. A reader deciding where
  // to spend research time needs to know which company looks economically
  // interesting; how completely each one was screened qualifies that answer
  // and is reported after it.
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
    key: "growth",
    label: "Growth signal",
    hint: "What the council said about growth and its quality.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => dimensionSignal(view, c, "growth_quality")),
    value: (c, view) => dimensionSignal(view, c, "growth_quality") ?? NOT_ESTABLISHED,
  },
  {
    key: "profitability",
    label: "Profitability signal",
    hint: "Margin level and direction, where the council established one.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => dimensionSignal(view, c, "profitability")),
    value: (c, view) => dimensionSignal(view, c, "profitability") ?? NOT_ESTABLISHED,
  },
  {
    key: "cash",
    label: "Cash generation",
    hint: "Free cash flow, conversion or direction, where established.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => dimensionSignal(view, c, "cash_generation")),
    value: (c, view) => dimensionSignal(view, c, "cash_generation") ?? NOT_ESTABLISHED,
  },
  {
    key: "resilience",
    label: "Resilience",
    hint: "What the council judged would limit downside — net cash, leverage, recurring demand.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => councilPlacementFor(view, c)?.resilience),
    value: (c, view) =>
      councilPlacementFor(view, c)?.resilience ?? NOT_ESTABLISHED,
  },
  {
    key: "catalyst",
    label: "Key catalyst",
    hint: "The strongest thing the council thought could change the picture.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => councilPlacementFor(view, c)?.upsideDrivers.length),
    value: (c, view) =>
      councilPlacementFor(view, c)?.upsideDrivers[0] ?? NOT_ESTABLISHED,
  },
  {
    key: "downside",
    label: "Main downside",
    hint: "The strongest thing the council thought could pressure it.",
    numeric: false,
    supported: (candidates, view) =>
      candidates.some((c) => councilPlacementFor(view, c)?.downsideDrivers.length),
    value: (c, view) =>
      councilPlacementFor(view, c)?.downsideDrivers[0] ?? NOT_ESTABLISHED,
  },
  // The key financial signal is not a column of its own: it is the value the
  // dimension columns above already carry, and repeating it would widen the
  // table without adding a comparison. It stays on the candidate card.

  // ── Then how much to trust the above ──────────────────────────────────
  {
    key: "evidence",
    label: "Evidence confidence",
    hint: "How much weight the comparison above can carry. It qualifies the answer; it is not the answer.",
    numeric: false,
    supported: (candidates) => candidates.some((c) => c.source_quality),
    value: (c) => c.source_quality ?? "not assessed",
  },
  {
    key: "readiness",
    label: "Research readiness",
    hint: "Whether there is enough evidence to start a full analysis.",
    numeric: false,
    supported: () => true,
    value: (c) => READINESS_WORD[researchReadiness(c)],
  },
  {
    key: "priority",
    label: "Research priority",
    hint: "The deterministic screening score, 0-100. Not a rating, and not a view on value.",
    numeric: true,
    supported: (candidates) =>
      candidates.some((c) => typeof c.candidate_score === "number"),
    value: (c) => score(c.candidate_score),
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
