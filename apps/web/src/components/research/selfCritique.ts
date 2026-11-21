/**
 * V3.20 slice 9.3 — reading the report's self-critique.
 *
 * WHERE THE KEYS COME FROM (producer → this reader, key for key)
 * ==============================================================
 *   app/services/pipeline/self_critique.self_critique_block → professional_research.self_critique
 *     {version, state, reason, publication_ready, note,
 *      bear_case[] of {kind, label, section?, weakness_class?, statement?, author?},
 *      bear_case_total, challenges_withheld_for_safety,
 *      weakest_links[] of {kind, key, text},
 *      data_quality_warnings[] of {label, section, reason}, platform_evidence_gaps,
 *      red_team {challenges, resolved, partially_resolved, unresolved, withdrawn_findings,
 *                confidence_lowered},
 *      disconfirming_search {adverse_findings, supportive_findings, counter_thesis_findings,
 *                            challenges_raised, statement}}
 */

const isRecord = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() ? v.trim() : null;

const num = (v: unknown): number => (typeof v === "number" && Number.isFinite(v) ? v : 0);

const recs = (v: unknown): Record<string, unknown>[] => (Array.isArray(v) ? v.filter(isRecord) : []);

export type BearItem = {
  kind: string;
  label: string | null;
  section: string | null;
  weaknessClass: string | null;
  statement: string | null;
};

export type SelfCritiqueBlock = {
  complete: boolean;
  reason: string | null;
  publicationReady: boolean;
  bearCase: BearItem[];
  bearCaseTotal: number;
  withheldForSafety: number;
  weakestLinks: { kind: string; text: string }[];
  dataQualityWarnings: { label: string | null; section: string | null; reason: string | null }[];
  platformEvidenceGaps: number;
  redTeam: { challenges: number; resolved: number; unresolved: number; withdrawnFindings: number };
  disconfirming: string | null;
  note: string | null;
};

export function readSelfCritiqueBlock(v: unknown): SelfCritiqueBlock | null {
  if (!isRecord(v) || (v.state !== "complete" && v.state !== "incomplete")) return null;
  const red = isRecord(v.red_team) ? v.red_team : {};
  const search = isRecord(v.disconfirming_search) ? v.disconfirming_search : {};
  return {
    complete: v.state === "complete",
    reason: str(v.reason),
    publicationReady: v.publication_ready === true,
    bearCase: recs(v.bear_case).map((b) => ({
      kind: str(b.kind) ?? "item",
      label: str(b.label),
      section: str(b.section),
      weaknessClass: str(b.weakness_class),
      statement: str(b.statement),
    })),
    bearCaseTotal: num(v.bear_case_total),
    withheldForSafety: num(v.challenges_withheld_for_safety),
    weakestLinks: recs(v.weakest_links)
      .map((w) => ({ kind: str(w.kind) ?? "", text: str(w.text) ?? "" }))
      .filter((w) => w.text),
    dataQualityWarnings: recs(v.data_quality_warnings).map((w) => ({
      label: str(w.label),
      section: str(w.section),
      reason: str(w.reason),
    })),
    platformEvidenceGaps: num(v.platform_evidence_gaps),
    redTeam: {
      challenges: num(red.challenges),
      resolved: num(red.resolved),
      unresolved: num(red.unresolved),
      withdrawnFindings: num(red.withdrawn_findings),
    },
    disconfirming: str(search.statement),
    note: str(v.note),
  };
}
