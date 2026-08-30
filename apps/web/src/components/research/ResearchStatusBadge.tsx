/**
 * The ONE persistent research-status treatment on the user-facing surfaces.
 *
 * The admin report page states the same facts six times, in six banners,
 * because an operator is expected to re-read them on every visit. Repeating
 * them after every section of a reader-facing report does not make them more
 * true — it makes them invisible. So the status is stated once, compactly, and
 * always in the same place, with the full wording one disclosure away.
 *
 * Nothing is softened here. "Evidence incomplete" and "human review required"
 * are shown exactly as the backend assessed them; only the repetition is gone.
 */

import type { ReactNode } from "react";

export type EvidenceLabel = "strong" | "adequate" | "weak" | "insufficient";

const EVIDENCE_TONE: Record<EvidenceLabel, string> = {
  strong: "text-emerald-300",
  adequate: "text-sky-300",
  weak: "text-amber-300",
  insufficient: "text-rose-300",
};

const EVIDENCE_WORD: Record<EvidenceLabel, string> = {
  strong: "Strong",
  adequate: "Adequate",
  weak: "Weak",
  insufficient: "Incomplete",
};

export function evidenceLabelOf(raw: unknown): EvidenceLabel | null {
  const v = typeof raw === "string" ? raw.toLowerCase() : "";
  return v === "strong" || v === "adequate" || v === "weak" || v === "insufficient"
    ? (v as EvidenceLabel)
    : null;
}

export function evidenceWord(label: EvidenceLabel): string {
  return EVIDENCE_WORD[label];
}

export default function ResearchStatusBadge({
  evidence,
  humanReviewRequired = true,
  extra,
  className = "",
  testId = "research-status",
}: {
  /** Canonical overall evidence label, or null when the report does not carry one. */
  evidence: EvidenceLabel | null;
  humanReviewRequired?: boolean;
  /** Optional trailing element (e.g. a "view technical report" link). */
  extra?: ReactNode;
  className?: string;
  testId?: string;
}) {
  return (
    <div
      data-testid={testId}
      className={`ib-panel flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5 text-sm ${className}`.trim()}
    >
      <span className="flex items-center gap-2 text-[color:var(--ib-ink)]">
        <span
          aria-hidden="true"
          className="h-1.5 w-1.5 rounded-full bg-[color:var(--ib-accent)]"
        />
        Internal research
      </span>

      {evidence && (
        <span className="text-[color:var(--ib-ink-3)]">
          Evidence{" "}
          <span className={EVIDENCE_TONE[evidence]}>
            {EVIDENCE_WORD[evidence]}
          </span>
        </span>
      )}

      {humanReviewRequired && (
        <span className="text-[color:var(--ib-ink-3)]">
          Human review required
        </span>
      )}

      <details className="group ml-auto text-[color:var(--ib-ink-3)]">
        <summary
          className="cursor-pointer list-none text-xs underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
          aria-label="What this research status means"
        >
          What this means
        </summary>
        <div className="mt-3 max-w-prose border-t border-[color:var(--ib-line)] pt-3 text-xs leading-relaxed">
          <p>
            This is internal research material produced by an automated
            evidence-and-analysis pipeline. It is not investment advice, and it
            contains no rating, price target, fair value or return projection.
          </p>
          <p className="mt-2">
            <strong className="text-[color:var(--ib-ink-2)]">Evidence</strong>{" "}
            reflects the weakest contributing dimension of what was actually
            sourced — identity, financial evidence and catalyst evidence are
            assessed separately and never averaged into a flattering summary.
          </p>
          <p className="mt-2">
            <strong className="text-[color:var(--ib-ink-2)]">
              Human review required
            </strong>{" "}
            means exactly that: the report is not final research until a person
            has read the evidence behind it. Nothing here is published publicly.
          </p>
        </div>
      </details>

      {extra}
    </div>
  );
}
