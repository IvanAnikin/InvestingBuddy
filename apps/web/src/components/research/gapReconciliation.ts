/**
 * Applying the backend's gap reconciliation to the V2 report's own gap statements.
 *
 * The V2 report (its `missing_information` list and the council's concerns) is
 * assembled BEFORE the V3 research runs, so it can say "capital expenditure is not
 * disclosed" on a page whose V3 findings state the capex. The backend labels each such
 * V2 item against the findings (`v3_research.gap_reconciliation.v2`); this module only
 * APPLIES the labels — it never judges a statement itself:
 *
 *  - `closed` / `superseded`: the item is not shown as open;
 *  - `partially_closed`: shown, and says which finding partly addresses it;
 *  - anything else, or no label at all: shown exactly as V2 wrote it.
 */

import type { MissingItem } from "./reportView";
import type { OpenQuestion } from "./reportSections";
import type { V2ItemLabel, V3GapReconciliation } from "./v3Research";

const HIDDEN = new Set(["closed", "superseded"]);

/** The same normalisation the backend applies to a concern's text. */
export function normaliseItemText(text: string): string {
  return text.replace(/\s+/g, " ").trim().toLowerCase();
}

function partialNote(label: V2ItemLabel, findingLabels: Record<string, string>): string {
  const labels = label.findingIds
    .map((id) => findingLabels[id])
    .filter((l): l is string => Boolean(l));
  return labels.length > 0
    ? `partially addressed by ${labels.join(", ")}`
    : "partially addressed by a research finding";
}

export function reconcileOpenQuestions(
  questions: OpenQuestion[],
  reconciliation: V3GapReconciliation | null,
  findingLabels: Record<string, string> = {},
): OpenQuestion[] {
  if (!reconciliation || reconciliation.v2CouncilConcerns.length === 0) return questions;
  const byKey = new Map(reconciliation.v2CouncilConcerns.map((l) => [l.key, l]));
  const out: OpenQuestion[] = [];
  for (const q of questions) {
    const label = byKey.get(normaliseItemText(q.question));
    if (label && HIDDEN.has(label.status)) continue;
    if (label && label.status === "partially_closed") {
      out.push({ ...q, question: `${q.question} (${partialNote(label, findingLabels)})` });
      continue;
    }
    out.push(q);
  }
  return out;
}

/** The same rule for a plain list of concern texts (research limitations routed out of
 *  the council's sections — where a "not disclosed" concern usually lands). */
export function reconcileConcernTexts(
  texts: string[],
  reconciliation: V3GapReconciliation | null,
  findingLabels: Record<string, string> = {},
): string[] {
  return reconcileOpenQuestions(
    texts.map((question) => ({ question, source: "" })),
    reconciliation,
    findingLabels,
  ).map((q) => q.question);
}

export function reconcileMissingItems(
  items: MissingItem[],
  total: number,
  reconciliation: V3GapReconciliation | null,
  findingLabels: Record<string, string> = {},
): { items: MissingItem[]; total: number } {
  if (!reconciliation || reconciliation.v2MissingInformation.length === 0) {
    return { items, total };
  }
  const byField = new Map(reconciliation.v2MissingInformation.map((l) => [l.key, l]));
  const kept: MissingItem[] = [];
  let removed = 0;
  for (const item of items) {
    const label = byField.get(item.field);
    if (label && HIDDEN.has(label.status)) {
      removed += 1;
      continue;
    }
    if (label && label.status === "partially_closed") {
      kept.push({ ...item, field: `${item.field} — ${partialNote(label, findingLabels)}` });
      continue;
    }
    kept.push(item);
  }
  return { items: kept, total: Math.max(0, total - removed) };
}
