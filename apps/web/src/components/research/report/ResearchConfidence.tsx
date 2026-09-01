import Surface from "@/components/product/Surface";
import { evidenceWord } from "@/components/research/ResearchStatusBadge";
import type { ResearchConfidenceView } from "@/components/research/reportSections";
import type { EvidenceDimension, MissingItem } from "@/components/research/reportView";

/**
 * How much this research can carry, and where it runs out.
 *
 * The report used to make this point three times: a per-dimension evidence
 * table, a 24-item list of machine field paths under "Important missing
 * information", and a data-quality risk block sitting beside business risk.
 * They are the same subject, so they are one section — stated compactly, with
 * the exhaustive machine list one disclosure away rather than in the reading
 * flow.
 *
 * Nothing is softened. The overall figure is still the WEAKEST dimension, never
 * an average, and the limitations are the report's own words.
 */
export default function ResearchConfidence({
  dimensions,
  confidence,
  missingItems,
  numericConflicts = 0,
  reportId,
}: {
  dimensions: EvidenceDimension[];
  confidence: ResearchConfidenceView;
  missingItems: MissingItem[];
  /** Council sentences withheld because they contradicted a canonical figure. */
  numericConflicts?: number;
  reportId: string;
}) {
  const overall = dimensions.find(
    (d) => d.key === "overall_research_evidence_quality",
  );
  const rest = dimensions.filter(
    (d) => d.key !== "overall_research_evidence_quality",
  );
  const hasAnything =
    dimensions.length > 0 ||
    confidence.limitations.length > 0 ||
    confidence.recordGaps.length > 0 ||
    numericConflicts > 0 ||
    missingItems.length > 0;
  if (!hasAnything) return null;

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="research-confidence"
      id="confidence"
    >
      <div className="flex flex-wrap items-baseline justify-between gap-3">
        <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Research confidence
        </h2>
        {overall?.value && (
          <p className="text-sm text-[color:var(--ib-ink-2)]">
            Overall: {evidenceWord(overall.value)}
          </p>
        )}
      </div>

      {rest.length > 0 && (
        <dl
          className="mt-5 grid gap-x-8 gap-y-4 sm:grid-cols-3"
          data-testid="confidence-dimensions"
        >
          {rest.map((dim) => (
            <div key={dim.key}>
              <dt className="text-xs text-[color:var(--ib-ink-3)]">
                {dim.label}
              </dt>
              <dd className="mt-0.5 text-sm text-[color:var(--ib-ink)]">
                {dim.value ? evidenceWord(dim.value) : "Not assessed"}
              </dd>
            </div>
          ))}
        </dl>
      )}

      {overall && overall.basis.length > 0 && (
        <p className="ib-breakable mt-4 max-w-2xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
          The overall figure is the weakest contributing dimension, never an
          average — {overall.basis.join(" · ")}.
        </p>
      )}

      {confidence.limitations.length > 0 && (
        <div className="mt-6" data-testid="confidence-limitations">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Key limitations
          </p>
          <ul className="mt-2 space-y-1.5">
            {confidence.limitations.slice(0, 5).map((l, i) => (
              <li
                key={i}
                className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                <span
                  aria-hidden="true"
                  className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                />
                <span className="ib-breakable">{l}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {numericConflicts > 0 && (
        <p
          className="mt-6 rounded-lg border border-amber-400/25 px-4 py-3 text-sm leading-relaxed text-amber-200"
          data-testid="numeric-conflicts"
        >
          {numericConflicts} council statement
          {numericConflicts === 1 ? "" : "s"} quoted a figure that does not
          reconcile with this report&apos;s own canonical financials, so
          {numericConflicts === 1 ? " it was" : " they were"} withheld rather
          than shown beside a number it contradicts. The technical view has
          both.
        </p>
      )}

      {confidence.unsupportedClaims.length > 0 && (
        <div className="mt-6 rounded-lg border border-amber-400/25 p-4">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-amber-200">
            Claims the citation review could not support
          </p>
          <ul className="mt-2 space-y-1 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
            {confidence.unsupportedClaims.slice(0, 5).map((claim, i) => (
              <li key={i} className="ib-breakable">
                {claim}
              </li>
            ))}
          </ul>
        </div>
      )}

      {confidence.recordGaps.length > 0 && (
        <div className="mt-6" data-testid="record-gaps">
          <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
            Recorded gaps
          </p>
          <p className="mt-1.5 max-w-2xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            Completeness entries the pipeline recorded. They describe the
            record, not the business, so they are reported here rather than as
            risks or open questions.
          </p>
          <ul className="mt-2 space-y-1">
            {confidence.recordGaps.map((gap, i) => (
              <li
                key={i}
                className="ib-breakable text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
              >
                {gap}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* The exhaustive machine-level list. Present, complete, and collapsed —
          it describes the record, not the company. */}
      {missingItems.length > 0 && (
        <details className="mt-6" data-testid="technical-gaps">
          <summary className="cursor-pointer list-none text-sm text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]">
            View all {confidence.technicalGapCount} technical gaps
          </summary>
          <p className="mt-3 max-w-2xl text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            Fields the research could not source, named as the pipeline names
            them. These describe the completeness of the record.
          </p>
          <ul className="mt-3 grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
            {missingItems.map((item, i) => (
              <li key={i} className="ib-breakable text-sm">
                <span className="ib-breakable font-mono text-xs text-[color:var(--ib-ink-2)]">
                  {item.field}
                </span>
                {item.source && (
                  <span className="ml-2 text-xs text-[color:var(--ib-ink-3)]">
                    ({item.source})
                  </span>
                )}
              </li>
            ))}
          </ul>
        </details>
      )}

      <p className="mt-6 border-t border-[color:var(--ib-line)] pt-4 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Every figure in this report keeps the document, period and scope it was
        extracted from. The full provenance — per-document excerpts, per-fact
        page and table location, validation flags and the review timeline — is
        on the{" "}
        <a
          href={`/admin/reports/${reportId}`}
          className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          technical report page
        </a>
        .
      </p>
    </Surface>
  );
}
