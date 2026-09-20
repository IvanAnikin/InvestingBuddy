/**
 * How a research decision reads on screen. V3.17.5.
 *
 * Pure functions, deliberately. The evidence numbers themselves come from the backend
 * and are never recomputed here — two implementations of the same arithmetic drift, and
 * the one on screen is the one a human acts on. What lives here is only *presentation*:
 * which words, which colour, which of three very different endings this is.
 */
import type { PillColor } from "@/components/ui/StatusPill";
import type {
  DecisionSpend,
  EvidenceDelta,
  ResearchDecision,
} from "@/types/api";

/**
 * The council's word and the platform's execution state are DIFFERENT CONCEPTS and are
 * never merged into one badge. "The council said research next" is a suggestion;
 * "queued" is work that exists. Collapsing them would let a reader think a suggestion
 * had started something.
 */
export const COUNCIL_LABEL: Record<string, string> = {
  research_next: "Council: research next",
  monitor_for_evidence: "Council: monitor",
  reject_for_now: "Council: reject for now",
  insufficient_data: "Council: insufficient data",
};

export const EXECUTION_LABEL: Record<string, string> = {
  research_required: "Decision taken",
  queued: "Queued",
  running: "Researching",
  evidence_updated: "Measuring evidence",
  reanalysis: "Next round",
  completed: "Completed",
  exhausted: "Exhausted",
  abandoned: "Stopped",
  monitored: "Monitoring",
  rejected: "Rejected",
  insufficient_evidence: "Insufficient evidence",
};

export function executionColor(status: string): PillColor {
  switch (status) {
    case "running":
    case "reanalysis":
      return "cyan";
    case "queued":
    case "research_required":
    case "evidence_updated":
      return "amber";
    case "completed":
      return "green";
    case "exhausted":
      return "gray";
    case "abandoned":
      return "red";
    default:
      return "gray";
  }
}

/**
 * Three endings a reader must never confuse.
 *
 * "Completed" means the research answered the question. "Exhausted" means research RAN
 * and there was nothing new to find — a statement about the world. "Could not complete"
 * means the platform did not manage to look — a statement about us. Showing the third as
 * the second reads as a finished investigation, and nobody goes back to it.
 */
export function outcomeSentence(d: ResearchDecision): string | null {
  switch (d.terminal_reason) {
    case "evidence_sufficient":
      return "Research completed and no closable gap remains open.";
    case "exhausted_no_improvement":
      return "Research ran and found no new evidence. Further rounds would repeat it at the same cost.";
    case "research_did_not_complete":
      return "Research could not complete, so nothing can be concluded about the available evidence. This is a platform failure, not a finding.";
    case "job_dead_lettered":
      return "The research job exhausted its attempts and was dead-lettered. The platform did not manage to look.";
    case "max_rounds_reached":
      return `Evidence was still improving, but all ${d.max_rounds} permitted round(s) were used.`;
    case "cost_cap_reached":
      return "Spend reached the configured cap before the question was answered.";
    case "cost_unknown":
      return "The cost of the work so far could not be determined, and unknown spend is treated as unaffordable rather than as free.";
    case "operator_cancelled":
      return "Stopped by an operator.";
    case "evidence_baseline_missing":
      return "No pre-round evidence snapshot exists for this decision, so what the round acquired cannot be measured. Stopped rather than guessed at — this is not a finding that nothing was acquired.";
    default:
      return null;
  }
}

/**
 * Cost, honestly.
 *
 * `null` is UNKNOWN, never 0. Production reports cost as null because no price book is
 * configured; rendering that as "$0.00" would tell an operator the work was free.
 */
export function costLabel(cost: number | null): string {
  if (cost === null || cost === undefined) return "unknown";
  return `$${cost.toFixed(4)}`;
}

/**
 * WHY the cost is unknown, in one short phrase. Empty when it is known.
 *
 * `costLabel` deliberately still answers exactly "unknown" — an operator must never
 * read a cost as free. This says which unknown it is, because they need different
 * fixes: "not attributed" is a defect in the platform's lineage, "not recorded" means
 * the consumption recorder is switched off, and "not priced" means the measurement
 * exists and no price book covers it.
 */
export function costUnknownReason(
  spend: DecisionSpend | null | undefined,
): string {
  if (!spend || spend.cost_usd_total !== null) return "";
  switch (spend.basis) {
    case "no_jobs":
      return "no research job yet";
    case "no_consumption_recorded":
      return "consumption not recorded";
    case "unpriced_consumption":
      return "measured, not priced";
    default:
      return "";
  }
}

export function roundLabel(d: ResearchDecision): string {
  return `round ${d.escalation_round + 1} of ${d.max_rounds}`;
}

/**
 * The delta lines a reader sees, in the backend's own numbers.
 *
 * An UNMEASURABLE round renders as a sentence, not as a row of zeros. `measurable:
 * false` means no pre-round snapshot existed, so the counts are schema defaults and
 * showing "0 searchable chunk(s)" would assert a measurement nobody took.
 */
export function deltaLines(delta: EvidenceDelta | null): string[] {
  if (!delta) return [];
  if (delta.measurable === false)
    return [
      "No pre-round evidence snapshot exists, so what this round acquired cannot be measured.",
    ];
  const lines: string[] = [];
  if (delta.indexed_chunks_added)
    lines.push(`${delta.indexed_chunks_added} searchable chunk(s)`);
  if (delta.searchable_documents_added)
    lines.push(`${delta.searchable_documents_added} searchable document(s)`);
  if (delta.closable_gaps_closed)
    lines.push(`${delta.closable_gaps_closed} gap(s) closed`);
  if (delta.closable_gaps_opened)
    lines.push(`${delta.closable_gaps_opened} new gap(s) found`);
  if (delta.facts_added) lines.push(`${delta.facts_added} fact(s)`);
  if (delta.verified_findings_added)
    lines.push(`${delta.verified_findings_added} finding(s) — secondary`);
  return lines;
}
