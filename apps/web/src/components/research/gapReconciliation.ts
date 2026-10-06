/**
 * Applying the backend's gap reconciliation to the V2 report's own gap statements.
 *
 * The V2 report (its `missing_information` list and the council's concerns) is
 * assembled BEFORE the V3 research runs, so it can say "capital expenditure is not
 * disclosed" on a page whose V3 findings state the capex. The backend labels each such
 * V2 item against the findings (`v3_research.gap_reconciliation.v2`); this module only
 * APPLIES the labels — it never judges a statement itself:
 *
 *  - a council CONCERN is never removed. One a finding speaks to is annotated with that
 *    finding; everything else is shown exactly as V2 wrote it. A concern that is really
 *    a business risk carries no label at all (the backend refuses to label one).
 *  - a missing-information FIELD NAME that a finding fully states is not listed as
 *    missing; one a finding partly states says which finding.
 */

import type { MissingItem } from "./reportView";
import type { OpenQuestion } from "./reportSections";
import type { V2ItemLabel, V3GapReconciliation } from "./v3Research";

/** The same normalisation the backend applies (`normalise_item_text`). */
export function normaliseItemText(text: string): string {
  return text
    .replace(/[‘’]/g, "'")
    .replace(/[“”]/g, '"')
    .replace(/\s+/g, " ")
    .trim()
    .toLowerCase()
    .replace(/[\s.;:!?]+$/, "");
}

function labelsFor(label: V2ItemLabel, findingLabels: Record<string, string>): string[] {
  return label.findingIds
    .map((id) => findingLabels[id])
    .filter((l): l is string => Boolean(l));
}

function annotation(label: V2ItemLabel, findingLabels: Record<string, string>): string {
  const labels = labelsFor(label, findingLabels);
  return labels.length > 0
    ? `partly addressed by ${labels.join(", ")}`
    : "partly addressed by a research finding";
}

const ANNOTATED = new Set(["closed", "partially_closed", "superseded"]);

/** Annotate concern texts a finding speaks to. Never removes one. */
export function reconcileConcernTexts(
  texts: string[],
  reconciliation: V3GapReconciliation | null,
  findingLabels: Record<string, string> = {},
): string[] {
  if (!reconciliation || reconciliation.v2CouncilConcerns.length === 0) return texts;
  const byKey = new Map(reconciliation.v2CouncilConcerns.map((l) => [l.key, l]));
  return texts.map((text) => {
    const label = byKey.get(normaliseItemText(text));
    return label && ANNOTATED.has(label.status)
      ? `${text} (${annotation(label, findingLabels)})`
      : text;
  });
}

export function reconcileOpenQuestions(
  questions: OpenQuestion[],
  reconciliation: V3GapReconciliation | null,
  findingLabels: Record<string, string> = {},
): OpenQuestion[] {
  if (!reconciliation || reconciliation.v2CouncilConcerns.length === 0) return questions;
  const texts = reconcileConcernTexts(
    questions.map((q) => q.question),
    reconciliation,
    findingLabels,
  );
  return questions.map((q, i) => (texts[i] === q.question ? q : { ...q, question: texts[i] }));
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
    if (label && (label.status === "closed" || label.status === "superseded")) {
      removed += 1;
      continue;
    }
    if (label && label.status === "partially_closed") {
      kept.push({ ...item, field: `${item.field} — ${annotation(label, findingLabels)}` });
      continue;
    }
    kept.push(item);
  }
  return { items: kept, total: Math.max(0, total - removed) };
}
