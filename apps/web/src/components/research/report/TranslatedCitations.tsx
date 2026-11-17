import type { TranslatedCitation } from "@/components/research/translatedCitations";

/**
 * V3.20 slice 10.2 — a finding's non-English sources: the original passage (the
 * evidence) and, when one is held, its English machine translation, labelled as such.
 * Renders nothing when none of the finding's evidence ids is a non-English passage.
 */
export default function TranslatedCitations({
  evidenceIds,
  citations,
}: {
  evidenceIds: string[];
  citations: Record<string, TranslatedCitation>;
}) {
  const shown = evidenceIds.map((id) => citations[id]).filter(Boolean);
  if (shown.length === 0) return null;
  return (
    <div className="mt-1 space-y-1" data-testid="translated-citations">
      {shown.map((c) => (
        <details
          key={c.evidenceId}
          className="rounded border border-[color:var(--ib-line)] px-2 py-1 text-xs"
        >
          <summary className="ib-breakable cursor-pointer text-[color:var(--ib-ink-3)]">
            Source in {c.languageName}
            {c.pageStart !== null ? ` (page ${c.pageStart})` : ""} —{" "}
            {c.translation ? "original and machine translation" : "original"}
          </summary>
          <p className="mt-1 font-medium text-[color:var(--ib-ink-2)]">
            Original ({c.languageName}) — the evidence
          </p>
          <blockquote
            lang={c.language}
            className="ib-breakable mt-0.5 whitespace-pre-line text-[color:var(--ib-ink-2)]"
          >
            {c.original}
            {c.originalTruncated ? " …" : ""}
          </blockquote>
          {c.translation ? (
            <>
              <p className="mt-2 font-medium text-[color:var(--ib-ink-2)]">
                Machine translation (English)
              </p>
              <blockquote
                lang="en"
                className="ib-breakable mt-0.5 whitespace-pre-line text-[color:var(--ib-ink-2)]"
              >
                {c.translation}
              </blockquote>
              {c.translationNote && (
                <p className="mt-1 text-[color:var(--ib-ink-3)]">{c.translationNote}</p>
              )}
            </>
          ) : (
            <p className="mt-1 text-[color:var(--ib-ink-3)]">
              No machine translation is held for this passage.
            </p>
          )}
        </details>
      ))}
    </div>
  );
}
