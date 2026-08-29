/**
 * Manual research versus the same work inside InvestingBuddy.
 *
 * Deliberately makes no time claim. The honest difference is not "ten hours
 * saved" — it is that the collection and reconciliation steps leave a record
 * you can audit, instead of living in a browser history and a spreadsheet.
 */

const MANUAL = [
  "Search",
  "Investor-relations site",
  "Download PDFs",
  "Copy figures into a spreadsheet",
  "Scattered notes",
  "News search",
  "Ask an AI chat",
  "Write the memo",
];

const PLATFORM = [
  "Research request",
  "Primary evidence located",
  "Structured, labelled facts",
  "Research council",
  "Report",
  "Human review",
];

function Chain({
  steps,
  emphasis,
}: {
  steps: string[];
  emphasis: boolean;
}) {
  return (
    <ol className="flex flex-wrap items-center gap-x-1.5 gap-y-2">
      {steps.map((step, i) => (
        <li key={step} className="flex items-center gap-1.5">
          <span
            className={`rounded-md border px-2.5 py-1 text-xs ${
              emphasis
                ? "border-[color:var(--ib-line-strong)] text-[color:var(--ib-ink)]"
                : "border-[color:var(--ib-line)] text-[color:var(--ib-ink-3)]"
            }`}
          >
            {step}
          </span>
          {i < steps.length - 1 && (
            <span
              aria-hidden="true"
              className="text-xs text-[color:var(--ib-ink-3)]"
            >
              →
            </span>
          )}
        </li>
      ))}
    </ol>
  );
}

export default function ComparisonPanel() {
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <div className="ib-panel p-5 sm:p-6">
        <p className="mb-4 text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
          Doing it by hand
        </p>
        <Chain steps={MANUAL} emphasis={false} />
        <p className="mt-5 text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
          The evidence exists, but only in your browser history and a
          spreadsheet. Six months later, nobody can tell which figure came from
          which document, or which period it covered.
        </p>
      </div>

      <div className="ib-panel p-5 sm:p-6">
        <p className="mb-4 text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-2)]">
          With InvestingBuddy
        </p>
        <Chain steps={PLATFORM} emphasis />
        <p className="mt-5 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
          Spend less time collecting and reconciling evidence, and more
          evaluating the investment — with every figure still attached to the
          document, period and scope it came from.
        </p>
      </div>
    </div>
  );
}
