import Link from "next/link";
import CompanyResearchForm from "./CompanyResearchForm";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Analyze a company — InvestingBuddy",
};

const WHAT_HAPPENS = [
  {
    title: "Company identity",
    body: "Ticker, exchange and legal identity are resolved before anything is fetched, so documents are matched to the right issuer.",
  },
  {
    title: "Primary-source discovery",
    body: "The issuer's own investor-relations domains and its regulated-disclosure venues are searched for annual, interim and quarterly reporting.",
  },
  {
    title: "Document ingestion",
    body: "Documents are fetched and read. Multi-year tables are reconstructed from the page layout where a plain text pass would lose the column headers.",
  },
  {
    title: "Financial extraction",
    body: "Figures are extracted with their period, scope, currency and scale. Group and segment stay separate; annual and interim stay separate.",
  },
  {
    title: "Evidence validation",
    body: "Everything found is reconciled into one state. Conflicts are surfaced, and what is missing is recorded as a finding rather than filled in.",
  },
  {
    title: "Council analysis",
    body: "Several agents read the same evidence pack — financial, business quality, risk, bull, bear — and a red team argues against the emerging view.",
  },
  {
    title: "Research report",
    body: "A readable report with reporting state, sourced financials, trends, cases, risks, gaps and the sources behind them.",
  },
];

export default function AnalyzeCompanyPage() {
  return (
    <div className="ib-fade-up">
      <nav aria-label="Breadcrumb" className="mb-6">
        <Link
          href="/research"
          className="text-sm text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
        >
          ← Research
        </Link>
      </nav>

      <div className="grid gap-10 lg:grid-cols-[minmax(0,1.15fr)_minmax(0,0.85fr)] lg:gap-14">
        <div>
          <h1 className="text-3xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
            Analyze a company
          </h1>
          <p className="mt-3 max-w-xl text-base leading-relaxed text-[color:var(--ib-ink-2)]">
            Pick the company. The pipeline finds its primary documents, extracts
            the figures with their period and scope, and puts the evidence
            through the research council.
          </p>

          <div className="mt-8">
            <CompanyResearchForm />
          </div>
        </div>

        <aside className="lg:pt-16">
          <h2 className="text-sm font-semibold text-[color:var(--ib-ink)]">
            What happens next
          </h2>
          <ol className="mt-4 space-y-4">
            {WHAT_HAPPENS.map((step, i) => (
              <li key={step.title} className="flex gap-3.5">
                <span className="mt-0.5 w-5 shrink-0 font-mono text-[10px] text-[color:var(--ib-ink-3)]">
                  {String(i + 1).padStart(2, "0")}
                </span>
                <span>
                  <span className="block text-sm font-medium text-[color:var(--ib-ink-2)]">
                    {step.title}
                  </span>
                  <span className="mt-0.5 block text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                    {step.body}
                  </span>
                </span>
              </li>
            ))}
          </ol>

          <p className="mt-6 border-t border-[color:var(--ib-line)] pt-4 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
            The output is internal research material requiring human review. It
            contains no rating, price target or return projection. Every
            pipeline setting remains available on the{" "}
            <Link
              href="/admin/analysis"
              className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
            >
              admin analysis runner
            </Link>
            .
          </p>
        </aside>
      </div>
    </div>
  );
}
