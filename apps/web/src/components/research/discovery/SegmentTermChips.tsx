"use client";

/**
 * V3.21.1 — the free-form segment a thesis names, as chips.
 *
 * Shown only when no fixed theme matched: these sanitised terms are the hard industry
 * filter (a candidate must show verified exposure to one of them), and the only words
 * from the thesis that travel to recall and web search. Synonyms, when a run's model
 * expansion proposed any, are shown apart.
 */
export default function SegmentTermChips({
  terms,
  synonyms,
  testId = "segment-terms",
}: {
  terms: string[] | null | undefined;
  synonyms?: string[] | null;
  testId?: string;
}) {
  if (!terms || terms.length === 0) return null;
  return (
    <div className="flex flex-wrap items-center gap-1.5 text-xs" data-testid={testId}>
      <span className="text-slate-500">Segment:</span>
      {terms.map((term) => (
        <span
          key={term}
          className="rounded-full border border-sky-400/30 bg-sky-400/10 px-2 py-0.5 text-sky-200"
          data-testid="segment-term-chip"
        >
          {term}
        </span>
      ))}
      {synonyms && synonyms.length > 0 && (
        <>
          <span className="ml-1 text-slate-500">also:</span>
          {synonyms.map((syn) => (
            <span
              key={syn}
              className="rounded-full border border-white/10 px-2 py-0.5 text-slate-300"
              data-testid="segment-synonym-chip"
            >
              {syn}
            </span>
          ))}
        </>
      )}
    </div>
  );
}
