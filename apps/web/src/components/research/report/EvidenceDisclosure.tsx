import Surface from "@/components/product/Surface";
import EvidencePanel from "@/components/research/EvidencePanel";
import type {
  AppendixView,
  DisclosureView,
  EvidenceChannelView,
} from "@/components/research/reportView";
import type { ReportPrimaryDocumentsResponse } from "@/types/api";

/**
 * Source transparency, at the weight it should carry in a reading flow.
 *
 * Nothing is removed. The counts stay visible — and stay separate, because
 * documents read, official disclosures and persisted citations count different
 * things and were never summable — and the full inventory, including every
 * channel's state, is one click away. What changes is that a reader no longer
 * walks through the source architecture on the way to the research.
 */
export default function EvidenceDisclosure({
  primaryDocuments,
  disclosures,
  appendix,
  channels,
  reportId,
  sourceNotes = [],
}: {
  primaryDocuments: ReportPrimaryDocumentsResponse | null;
  disclosures: DisclosureView[];
  appendix: AppendixView;
  channels: EvidenceChannelView[];
  reportId: string;
  /**
   * The backend's own period and source notes, verbatim. The investor page
   * states the same facts in words because the originals name fields and tier
   * codes (`_current_period`, `T1_primary_filing`) — this is where the exact
   * text stays, unedited, so nothing is rewritten away.
   */
  sourceNotes?: string[];
}) {
  const documents = (primaryDocuments?.documents ?? []).filter(
    (d) => d.status !== "discovered",
  );
  const counts: string[] = [];
  if (documents.length > 0) {
    counts.push(
      `${documents.length} document${documents.length === 1 ? "" : "s"} read`,
    );
  }
  if (disclosures.length > 0) {
    counts.push(
      `${disclosures.length} official disclosure${disclosures.length === 1 ? "" : "s"}`,
    );
  }
  if (appendix.totalSources > 0) {
    counts.push(
      `${appendix.totalSources} source${appendix.totalSources === 1 ? "" : "s"} behind claims`,
    );
  }
  if (appendix.primaryReferenceCount > 0) {
    counts.push(`${appendix.primaryReferenceCount} located reference(s)`);
  }

  return (
    <Surface
      as="section"
      className="p-6 sm:p-7"
      testId="evidence-disclosure"
      id="evidence"
    >
      <details>
        <summary className="cursor-pointer list-none">
          <span className="flex flex-wrap items-baseline justify-between gap-3">
            <span className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
              Evidence &amp; sources
            </span>
            <span className="text-sm text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-4">
              View evidence
            </span>
          </span>
          <span className="ib-breakable mt-2 block text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
            {counts.length > 0
              ? `${counts.join(" · ")}. These count different things and are never added together.`
              : "No document, disclosure or persisted citation was recorded for this report."}
          </span>
        </summary>

        <div className="mt-6 border-t border-[color:var(--ib-line)] pt-5">
          <EvidencePanel
            primaryDocuments={primaryDocuments}
            disclosures={disclosures}
            appendix={appendix}
            channels={channels}
            reportId={reportId}
            variant="bare"
          />

          {sourceNotes.length > 0 && (
            <div
              className="mt-6 border-t border-[color:var(--ib-line)] pt-5"
              data-testid="source-notes"
            >
              <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
                Source notes, as recorded
              </p>
              <ul className="mt-2 space-y-2">
                {sourceNotes.map((note, i) => (
                  <li
                    key={i}
                    className="ib-breakable text-xs leading-relaxed text-[color:var(--ib-ink-3)]"
                  >
                    {note}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>
      </details>
    </Surface>
  );
}
