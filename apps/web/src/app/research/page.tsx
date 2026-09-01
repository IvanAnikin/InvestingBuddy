import Link from "next/link";
import PrimaryCTA from "@/components/product/PrimaryCTA";
import Surface from "@/components/product/Surface";
import { classifyReports } from "@/components/research/reportResolution";
import { fetchReports } from "@/lib/api";
import type { Report } from "@/types/api";
import { reportCompanyLabel } from "@/components/research/reportView";
import { formatDate } from "@/lib/format";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Research — InvestingBuddy",
};

const ENTRY_POINTS = [
  {
    href: "/research/company",
    kicker: "Path A",
    title: "Analyze a company",
    body: "Give it a ticker. The pipeline locates the issuer's own documents, extracts period-labelled facts, and runs the research council.",
    cta: "Start company research",
  },
  {
    href: "/research/discover",
    kicker: "Path B",
    title: "Discover opportunities",
    body: "Describe a theme, market or idea in your own words. Discovery builds a bounded universe and ranks the candidates worth reading.",
    cta: "Start discovery",
  },
];

async function recentReports(): Promise<{
  items: Report[];
  total: number;
  error: string | null;
}> {
  try {
    const data = await fetchReports(50, 0);
    // Show CURRENT research, the same thing the library leads with. The list
    // used to be every report newest-first, so a screening draft written by the
    // discovery scan could sit at the top of "recent research" looking exactly
    // like research. When no current report exists yet, everything is shown —
    // an empty list would be the wrong answer, not a tidier one.
    const kinds = classifyReports(data.items);
    const current = data.items.filter(
      (r) => kinds.get(r.id) === "current_research",
    );
    const items = current.length > 0 ? current : data.items;
    return { items: items.slice(0, 6), total: items.length, error: null };
  } catch (e) {
    return {
      items: [],
      total: 0,
      error: e instanceof Error ? e.message : "Could not reach the research API.",
    };
  }
}

export default async function ResearchHomePage() {
  const { items, total, error } = await recentReports();

  return (
    <div className="ib-fade-up">
      <header className="max-w-2xl">
        <h1 className="text-3xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
          Research workspace
        </h1>
        <p className="mt-3 text-base leading-relaxed text-[color:var(--ib-ink-2)]">
          Start from a company you already have in mind, or from the idea behind
          it. Everything you run lands in the research library with its evidence
          attached.
        </p>
      </header>

      {/* Entry points */}
      <div className="mt-10 grid gap-4 lg:grid-cols-2">
        {ENTRY_POINTS.map((entry) => (
          <Surface
            key={entry.href}
            as="article"
            hover
            className="flex flex-col p-6 sm:p-7"
          >
            <p className="text-xs font-medium uppercase tracking-[0.14em] text-[color:var(--ib-ink-3)]">
              {entry.kicker}
            </p>
            <h2 className="mt-2 text-xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
              {entry.title}
            </h2>
            <p className="mt-2 flex-1 text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
              {entry.body}
            </p>
            <div className="mt-6">
              <PrimaryCTA href={entry.href}>{entry.cta}</PrimaryCTA>
            </div>
          </Surface>
        ))}
      </div>

      {/* Recent research */}
      <section className="mt-14">
        <div className="flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
            Recent research
          </h2>
          <Link
            href="/research/reports"
            className="ib-arrow-host text-sm text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
          >
            Open the research library{" "}
            <span className="ib-arrow" aria-hidden="true">
              →
            </span>
          </Link>
        </div>

        {error && (
          <Surface className="mt-4 p-5">
            <p className="text-sm text-amber-300">
              The research API could not be reached, so recent work is not shown.
            </p>
            <p className="mt-1 font-mono text-xs text-[color:var(--ib-ink-3)]">
              {error}
            </p>
          </Surface>
        )}

        {!error && items.length === 0 && (
          <Surface className="mt-4 p-8 text-center">
            <p className="text-sm text-[color:var(--ib-ink-2)]">
              Nothing researched yet.
            </p>
            <p className="mt-1 text-sm text-[color:var(--ib-ink-3)]">
              Run your first company analysis and it will appear here.
            </p>
            <div className="mt-5 flex justify-center">
              <PrimaryCTA href="/research/company" tone="secondary">
                Analyze a company
              </PrimaryCTA>
            </div>
          </Surface>
        )}

        {items.length > 0 && (
          <Surface className="mt-4 overflow-hidden">
            <ul className="divide-y divide-[color:var(--ib-line)]">
              {items.map((report) => {
                const { company, ticker } = reportCompanyLabel(report);
                return (
                  <li key={report.id}>
                    <Link
                      href={`/research/reports/${report.id}`}
                      className="ib-arrow-host flex flex-wrap items-center gap-x-4 gap-y-1 px-5 py-4 transition-colors hover:bg-[color:var(--ib-surface-raised)]"
                    >
                      <span className="min-w-0 flex-1">
                        {/* A report with no sourced company name falls back to
                            its own title, which can be long. It WRAPS rather
                            than being cut off mid-word: a clipped label is a
                            legibility failure, not a tidy one. */}
                        <span className="ib-breakable block text-sm font-medium text-[color:var(--ib-ink)]">
                          {company ?? report.title}
                        </span>
                        <span className="ib-breakable block text-xs text-[color:var(--ib-ink-3)]">
                          {ticker ? `${ticker} · ` : ""}
                          {formatDate(report.updated_at)}
                        </span>
                      </span>
                      <span className="ib-arrow text-sm text-[color:var(--ib-ink-3)]">
                        →
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            {total > items.length && (
              <div className="border-t border-[color:var(--ib-line)] px-5 py-3">
                <Link
                  href="/research/reports"
                  className="text-xs text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
                >
                  {total} reports in total →
                </Link>
              </div>
            )}
          </Surface>
        )}
      </section>

      {/* The admin surface is reachable from the navigation and the footer on
          every page. A research workspace does not need a content block about
          pipeline diagnostics — one quiet line is the right weight. */}
      <p
        className="mt-14 border-t border-[color:var(--ib-line)] pt-6 text-xs leading-relaxed text-[color:var(--ib-ink-3)]"
        data-testid="admin-diagnostics-link"
      >
        Raw report JSON, per-document extraction provenance, source-connector
        health, the review workflow, backtesting and the ticker-mode discovery
        runner are in{" "}
        <Link
          href="/admin"
          className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          admin &amp; diagnostics
        </Link>
        .
      </p>
    </div>
  );
}
