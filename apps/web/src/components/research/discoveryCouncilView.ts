// Derivation layer for the run-level Discovery Council review.
//
// The council has always produced this. `POST/GET
// /market-discovery/runs/{id}/council-review` returns a chair synthesis, a
// per-candidate internal-action placement, every agent's own notes, the run
// quality label and the evidence gaps — and the only surface that read any of
// it was the admin console, which showed it as a grid of stats plus a list of
// one-line agent summaries. The user-facing discovery page showed none of it.
//
// This module reshapes that SAME persisted payload for a reader. It derives
// nothing the council did not say:
//
//   - It never invents a ranking. The chair emits BUCKETS (research_next,
//     monitor_for_evidence, insufficient_data, reject_for_now), not an ordered
//     list, so candidates are shown in the order the council returned them and
//     the absence of an ordering is stated rather than filled in.
//   - It never manufactures disagreement from prose. A disagreement is two
//     agents assigning DIFFERENT `internal_action` values — the same closed
//     vocabulary, compared like for like.
//   - It never averages the internal scores into a composite.
//   - It never presents an internal research-workflow state as an investment
//     action. "research_next" is "highest research priority", not a call.

import type {
  DiscoveryCouncilAgentOutput,
  DiscoveryCouncilCandidateEntry,
  DiscoveryCouncilReview,
} from "@/types/api";

// ---------------------------------------------------------------------------
// Vocabulary
// ---------------------------------------------------------------------------

/** The eight run-level council agents, in the backend's own run order. */
export const DISCOVERY_AGENT_ORDER: string[] = [
  "run_coordinator",
  "candidate_prioritization",
  "novelty_coverage",
  "diversity_anti_convergence",
  "evidence_sufficiency",
  "risk_gatekeeper",
  "run_red_team",
  "discovery_chair",
];

export const DISCOVERY_AGENT_LABELS: Record<string, string> = {
  run_coordinator: "Run coordinator",
  candidate_prioritization: "Candidate prioritisation",
  novelty_coverage: "Novelty & coverage",
  diversity_anti_convergence: "Diversity & concentration",
  evidence_sufficiency: "Evidence sufficiency",
  risk_gatekeeper: "Risk gatekeeper",
  run_red_team: "Red team",
  discovery_chair: "Chair",
};

export const DISCOVERY_AGENT_ROLES: Record<string, string> = {
  run_coordinator: "Does the candidate set match what the run set out to find?",
  candidate_prioritization: "Which candidates warrant deeper research first?",
  novelty_coverage: "Which candidates look under-researched?",
  diversity_anti_convergence: "Is the run over-concentrated in one place?",
  evidence_sufficiency: "Is there enough sourced evidence to analyse each one?",
  risk_gatekeeper: "What should gate deeper work?",
  run_red_team: "What is wrong with this discovery result?",
  discovery_chair: "The council's synthesis of the run.",
};

/**
 * Internal research-workflow states, in human words.
 *
 * These are NOT recommendations, and the wording keeps that true: a candidate
 * is a research priority, never something to buy, hold or avoid.
 */
export const INTERNAL_ACTION_LABELS: Record<string, string> = {
  research_next: "Highest research priority",
  monitor_for_evidence: "Monitor for more evidence",
  insufficient_data: "Not enough evidence to judge",
  reject_for_now: "Set aside for now",
};

export const INTERNAL_ACTION_SHORT: Record<string, string> = {
  research_next: "Research next",
  monitor_for_evidence: "Monitor",
  insufficient_data: "Insufficient evidence",
  reject_for_now: "Set aside",
};

/** Run-quality labels are the council's view of the RUN, not of a company. */
export const RUN_QUALITY_LABELS: Record<string, string> = {
  strong: "Strong candidate set",
  adequate: "Adequate candidate set",
  thin: "Thin candidate set",
  failed: "Could not assess the candidate set",
};

export function internalActionLabel(action: string | null | undefined): string {
  if (!action) return "Not placed";
  return INTERNAL_ACTION_LABELS[action] ?? action.replace(/_/g, " ");
}

export function internalActionShort(action: string | null | undefined): string {
  if (!action) return "Not placed";
  return INTERNAL_ACTION_SHORT[action] ?? action.replace(/_/g, " ");
}

export function runQualityLabel(quality: string | null | undefined): string {
  if (!quality) return "Not assessed";
  return RUN_QUALITY_LABELS[quality] ?? quality;
}

export function discoveryAgentLabel(name: string): string {
  return DISCOVERY_AGENT_LABELS[name] ?? name.replace(/_/g, " ");
}

// ---------------------------------------------------------------------------
// Council lifecycle
// ---------------------------------------------------------------------------

export type CouncilLifecycle =
  | "not_run"
  | "in_flight"
  | "completed"
  | "completed_with_warnings"
  | "failed"
  | "disabled";

export function councilLifecycle(
  review: DiscoveryCouncilReview | null,
): CouncilLifecycle {
  if (!review) return "not_run";
  const status = review.status ?? null;
  if (status === "disabled") return "disabled";
  if (status === "pending" || status === "running") return "in_flight";
  if (status === "completed_with_warnings") return "completed_with_warnings";
  if (status === "completed") return "completed";
  if (status === "failed") return "failed";
  // A response that predates the async lifecycle carries a review with no
  // status. Its own `run_quality` is the honest signal that it completed.
  return review.run_quality != null ? "completed" : "not_run";
}

export function councilHasReview(review: DiscoveryCouncilReview | null): boolean {
  if (!review) return false;
  if (review.review_available) return true;
  return review.status == null && review.run_quality != null;
}

// ---------------------------------------------------------------------------
// Derived view
// ---------------------------------------------------------------------------

export interface CouncilPriorityEntry {
  action: string;
  candidateRef: string | null;
  candidateId: string | null;
  ticker: string | null;
  exchange: string | null;
  /** The chair's own reason for placing this candidate here. */
  rationale: string | null;
  confidence: string | null;
  /** Other agents that placed this candidate in the SAME band, with why. */
  supporting: { agent: string; rationale: string | null }[];
  /** Other agents that placed it in a DIFFERENT band, with why. */
  concerns: { agent: string; action: string; rationale: string | null }[];
}

export interface CouncilAgentView {
  name: string;
  label: string;
  role: string | null;
  status: string;
  summary: string | null;
  /** The agent's cited run-level claims. */
  claims: { claim: string; confidence: string | null }[];
  evidenceGaps: string[];
  nextSourceTasks: string[];
  candidateNoteCount: number;
}

export interface CouncilDisagreement {
  ticker: string | null;
  exchange: string | null;
  candidateRef: string | null;
  positions: { agent: string; action: string; rationale: string | null }[];
}

export interface DiscoveryCouncilView {
  lifecycle: CouncilLifecycle;
  hasReview: boolean;
  llmUsed: boolean;
  error: string | null;
  message: string | null;

  candidatesReviewed: number;
  agentsCompleted: number;
  agentsFailed: number;
  agentsSkipped: number;
  evidenceItems: number;
  runQuality: string | null;
  humanReviewRequired: boolean;
  safetyValid: boolean;

  /** True when the chair never completed and the synthesis is a failure default. */
  chairIsFallback: boolean;
  chairErrorType: string | null;
  chairSynthesis: string | null;
  chairClaims: { claim: string; confidence: string | null }[];

  /** Buckets in the order the council returned them. Never re-sorted. */
  priority: Record<string, CouncilPriorityEntry[]>;
  /**
   * False whenever the council produced buckets rather than an ordered list —
   * which is always, because the chair's contract has no rank field. The UI
   * says so instead of inventing an order.
   */
  orderingEstablished: boolean;

  agents: CouncilAgentView[];
  disagreements: CouncilDisagreement[];
  evidenceGaps: string[];
  nextSourceTasks: string[];
  councilNotes: string[];
}

export const PRIORITY_BANDS: string[] = [
  "research_next",
  "monitor_for_evidence",
  "insufficient_data",
  "reject_for_now",
];

const BUCKET_FIELD: Record<
  string,
  keyof Pick<
    DiscoveryCouncilReview,
    | "candidates_to_research_next"
    | "candidates_to_monitor"
    | "candidates_insufficient_data"
    | "candidates_to_reject"
  >
> = {
  research_next: "candidates_to_research_next",
  monitor_for_evidence: "candidates_to_monitor",
  insufficient_data: "candidates_insufficient_data",
  reject_for_now: "candidates_to_reject",
};

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function agentOutputs(
  review: DiscoveryCouncilReview,
): DiscoveryCouncilAgentOutput[] {
  const raw = review.agent_outputs ?? {};
  const known = DISCOVERY_AGENT_ORDER.filter((name) => name in raw);
  const extra = Object.keys(raw).filter(
    (name) => !DISCOVERY_AGENT_ORDER.includes(name),
  );
  return [...known, ...extra].map((name) => ({
    ...(raw[name] ?? {}),
    agent_name: raw[name]?.agent_name ?? name,
  }));
}

/** Key a candidate note by whatever identity it actually carries. */
function noteKey(note: {
  candidate_ref?: string | null;
  ticker?: string | null;
  exchange?: string | null;
}): string | null {
  const ref = text(note.candidate_ref);
  if (ref) return `ref:${ref}`;
  const ticker = text(note.ticker);
  if (ticker) return `tic:${ticker}.${text(note.exchange) ?? ""}`;
  return null;
}

const EMPTY_VIEW: DiscoveryCouncilView = {
  lifecycle: "not_run",
  hasReview: false,
  llmUsed: false,
  error: null,
  message: null,
  candidatesReviewed: 0,
  agentsCompleted: 0,
  agentsFailed: 0,
  agentsSkipped: 0,
  evidenceItems: 0,
  runQuality: null,
  humanReviewRequired: true,
  safetyValid: true,
  chairIsFallback: false,
  chairErrorType: null,
  chairSynthesis: null,
  chairClaims: [],
  priority: {},
  orderingEstablished: false,
  agents: [],
  disagreements: [],
  evidenceGaps: [],
  nextSourceTasks: [],
  councilNotes: [],
};

export function buildDiscoveryCouncilView(
  review: DiscoveryCouncilReview | null,
): DiscoveryCouncilView {
  if (!review) return EMPTY_VIEW;

  const outputs = agentOutputs(review);

  // Every agent's per-candidate placement, indexed by candidate. This is the
  // ONLY basis for "who agreed with whom": one closed vocabulary, compared
  // like for like, never two pieces of prose held up against each other.
  const positionsByCandidate = new Map<
    string,
    {
      ticker: string | null;
      exchange: string | null;
      candidateRef: string | null;
      positions: { agent: string; action: string; rationale: string | null }[];
    }
  >();

  for (const output of outputs) {
    if (output.agent_name === "discovery_chair") continue;
    for (const note of output.candidate_notes ?? []) {
      const key = noteKey(note);
      const action = text(note.internal_action);
      if (!key || !action) continue;
      const entry = positionsByCandidate.get(key) ?? {
        ticker: text(note.ticker),
        exchange: text(note.exchange),
        candidateRef: text(note.candidate_ref),
        positions: [],
      };
      entry.ticker = entry.ticker ?? text(note.ticker);
      entry.exchange = entry.exchange ?? text(note.exchange);
      entry.candidateRef = entry.candidateRef ?? text(note.candidate_ref);
      entry.positions.push({
        agent: output.agent_name,
        action,
        rationale: text(note.rationale),
      });
      positionsByCandidate.set(key, entry);
    }
  }

  const priority: Record<string, CouncilPriorityEntry[]> = {};
  for (const band of PRIORITY_BANDS) {
    const entries = (review[BUCKET_FIELD[band]] ??
      []) as DiscoveryCouncilCandidateEntry[];
    priority[band] = entries.map((entry) => {
      const key = noteKey(entry);
      const others = key ? positionsByCandidate.get(key)?.positions ?? [] : [];
      return {
        action: band,
        candidateRef: text(entry.candidate_ref),
        candidateId: text(entry.candidate_id),
        ticker: text(entry.ticker),
        exchange: text(entry.exchange),
        rationale: text(entry.rationale),
        confidence: text(entry.confidence),
        supporting: others
          .filter((p) => p.action === band)
          .map((p) => ({ agent: p.agent, rationale: p.rationale })),
        concerns: others
          .filter((p) => p.action !== band)
          .map((p) => ({
            agent: p.agent,
            action: p.action,
            rationale: p.rationale,
          })),
      };
    });
  }

  const disagreements: CouncilDisagreement[] = [];
  for (const entry of positionsByCandidate.values()) {
    const actions = new Set(entry.positions.map((p) => p.action));
    if (actions.size < 2) continue;
    disagreements.push({
      ticker: entry.ticker,
      exchange: entry.exchange,
      candidateRef: entry.candidateRef,
      positions: entry.positions,
    });
  }

  const chair =
    outputs.find((o) => o.agent_name === "discovery_chair") ?? null;
  const fallbackChair = review.chair_fallback_used
    ? review.deterministic_discovery_chair ?? null
    : null;
  const chairSource = fallbackChair ?? chair;

  const agents: CouncilAgentView[] = outputs.map((output) => ({
    name: output.agent_name,
    label: discoveryAgentLabel(output.agent_name),
    role: DISCOVERY_AGENT_ROLES[output.agent_name] ?? null,
    status: output.status ?? "completed",
    summary: text(output.summary),
    claims: (output.run_notes ?? [])
      .map((n) => ({ claim: text(n.claim), confidence: text(n.confidence) }))
      .filter((n): n is { claim: string; confidence: string | null } =>
        Boolean(n.claim),
      ),
    evidenceGaps: (output.evidence_gaps ?? []).filter(Boolean),
    nextSourceTasks: (output.next_source_tasks ?? []).filter(Boolean),
    candidateNoteCount: (output.candidate_notes ?? []).length,
  }));

  return {
    lifecycle: councilLifecycle(review),
    hasReview: councilHasReview(review),
    llmUsed: Boolean(review.llm_used),
    error: text(review.error),
    message: text(review.message),
    candidatesReviewed: review.candidate_count ?? 0,
    agentsCompleted: review.agents_completed ?? 0,
    agentsFailed: review.agents_failed ?? 0,
    agentsSkipped: review.agents_skipped ?? 0,
    evidenceItems: review.evidence_item_count ?? 0,
    runQuality: text(review.run_quality),
    humanReviewRequired: review.human_review_required !== false,
    safetyValid: review.safety_valid !== false,
    chairIsFallback: Boolean(review.chair_fallback_used),
    chairErrorType: text(review.chair_error_type),
    chairSynthesis: text(chairSource?.summary),
    chairClaims: (chairSource?.run_notes ?? [])
      .map((n) => ({ claim: text(n.claim), confidence: text(n.confidence) }))
      .filter((n): n is { claim: string; confidence: string | null } =>
        Boolean(n.claim),
      ),
    priority,
    // The chair's JSON contract has no rank field: it places candidates into
    // bands. Presenting the first entry of a band as "the strongest" would be
    // this UI's judgement, not the council's.
    orderingEstablished: false,
    agents,
    disagreements,
    evidenceGaps: (review.evidence_gaps ?? []).filter(Boolean),
    nextSourceTasks: (review.next_source_tasks ?? []).filter(Boolean),
    councilNotes: (review.warnings ?? []).filter(Boolean),
  };
}

/**
 * The council's placement for one candidate, looked up by ticker/exchange.
 *
 * Candidate cards and the comparison table both need "what did the council say
 * about THIS row", and the council answers by ticker — the candidate UUID is
 * present only when the chair echoed a citation id the pack could resolve.
 */
export function councilPlacementFor(
  view: DiscoveryCouncilView,
  candidate: { id: string; ticker: string; exchange: string },
): CouncilPriorityEntry | null {
  for (const band of PRIORITY_BANDS) {
    for (const entry of view.priority[band] ?? []) {
      if (entry.candidateId && entry.candidateId === candidate.id) return entry;
      if (
        entry.ticker &&
        entry.ticker.toUpperCase() === candidate.ticker.toUpperCase() &&
        (!entry.exchange ||
          entry.exchange.toUpperCase() === candidate.exchange.toUpperCase())
      ) {
        return entry;
      }
    }
  }
  return null;
}
