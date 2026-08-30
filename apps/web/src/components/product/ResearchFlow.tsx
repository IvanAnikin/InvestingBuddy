/**
 * The hero's product visual: the path a research request actually takes.
 *
 * Rendered entirely on the server — the travelling highlight on each connector
 * is a pure CSS animation (`.ib-flow-line`), so this diagram ships no
 * JavaScript and is disabled wholesale under `prefers-reduced-motion`.
 *
 * The stage names and examples mirror the real pipeline, not a marketing
 * abstraction of it: documents are ingested, facts are extracted and labelled
 * by period and scope, a council examines the evidence, and a human reviews.
 */

const STAGES: { label: string; detail: string }[] = [
  { label: "Company", detail: "Ticker, exchange, legal identity" },
  { label: "Primary documents", detail: "Annual, interim, regulated filings" },
  { label: "Financial facts", detail: "Period- and scope-labelled figures" },
  { label: "Evidence pack", detail: "Cited, bounded, traceable" },
  { label: "Research council", detail: "Analysis, bull, bear, red team" },
  { label: "Human review", detail: "You read the evidence and decide" },
];

export default function ResearchFlow() {
  return (
    <div
      className="ib-panel p-5 sm:p-6"
      role="img"
      aria-label={
        "Research pipeline: " + STAGES.map((s) => s.label).join(", then ") + "."
      }
    >
      <p className="mb-5 text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
        Research pipeline
      </p>

      <ol className="flex flex-col gap-0" aria-hidden="true">
        {STAGES.map((stage, i) => (
          <li key={stage.label}>
            <div className="flex items-start gap-3.5">
              <span className="mt-1.5 grid h-6 w-6 shrink-0 place-items-center rounded-md border border-[color:var(--ib-line-strong)] font-mono text-[10px] text-[color:var(--ib-ink-2)]">
                {String(i + 1).padStart(2, "0")}
              </span>
              <span className="min-w-0 pb-0.5">
                <span className="block text-sm font-medium text-[color:var(--ib-ink)]">
                  {stage.label}
                </span>
                <span className="block text-xs text-[color:var(--ib-ink-3)]">
                  {stage.detail}
                </span>
              </span>
            </div>
            {i < STAGES.length - 1 && (
              <div className="ml-3 flex h-6 w-px items-stretch">
                <span className="ib-flow-line ib-flow-line-vertical w-px" />
              </div>
            )}
          </li>
        ))}
      </ol>

      <p className="mt-5 border-t border-[color:var(--ib-line)] pt-4 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Every figure keeps the document, period and scope it came from. Nothing
        that could not be sourced is filled in.
      </p>
    </div>
  );
}
