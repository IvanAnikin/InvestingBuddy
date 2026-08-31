import Link from "next/link";
import PrimaryCTA from "@/components/product/PrimaryCTA";
import Surface from "@/components/product/Surface";
import { buildLibraryRow, type LibraryRow } from "@/components/research/reportView";
import { classifyReports } from "@/components/research/reportResolution";
import { fetchReports } from "@/lib/api";
import ReportLibrary from "./ReportLibrary";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Research library — InvestingBuddy",
};

// The library reads the reports the API already returns and derives each row
// from the report's own stored content. It never re-runs anything, and it never
// substitutes a default for a value the report does not carry.
const LIBRARY_PAGE_SIZE = 50;

async function loadRows(): Promise<{ rows: LibraryRow[]; error: string | null }> {
  try {
    const data = await fetchReports(LIBRARY_PAGE_SIZE, 0);
    // Which report is a company's CURRENT research is a question about the
    // cohort, not about any one report — so it is answered here, once, where
    // the whole page of reports is in hand.
    const kinds = classifyReports(data.items);
    return {
      rows: data.items.map((report) =>
        buildLibraryRow(report, kinds.get(report.id) ?? "screening_draft"),
      ),
      error: null,
    };
  } catch (e) {
    return {
      rows: [],
      error: e instanceof Error ? e.message : "Could not reach the research API.",
    };
  }
}

export default async function ResearchLibraryPage() {
  const { rows, error } = await loadRows();

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

      <div className="flex flex-wrap items-end justify-between gap-4">
        <header className="max-w-2xl">
          <h1 className="text-3xl font-semibold tracking-tight text-[color:var(--ib-ink)]">
            Research library
          </h1>
          <p className="mt-3 text-base leading-relaxed text-[color:var(--ib-ink-2)]">
            Current research first. Screening drafts and superseded reports are
            kept — they are simply not presented as the current state.
          </p>
        </header>
        <PrimaryCTA href="/research/company" tone="secondary">
          Analyze a company
        </PrimaryCTA>
      </div>

      <div className="mt-8">
        {error ? (
          <Surface className="p-6">
            <p className="text-sm text-amber-300">
              The research API could not be reached.
            </p>
            <p className="mt-1 font-mono text-xs text-[color:var(--ib-ink-3)]">
              {error}
            </p>
          </Surface>
        ) : rows.length === 0 ? (
          <Surface className="p-10 text-center">
            <p className="text-sm text-[color:var(--ib-ink-2)]">
              Your research library is empty.
            </p>
            <p className="mt-1 text-sm text-[color:var(--ib-ink-3)]">
              Analyze a company, or start from a theme in discovery.
            </p>
            <div className="mt-6 flex flex-wrap justify-center gap-3">
              <PrimaryCTA href="/research/company">Analyze a company</PrimaryCTA>
              <PrimaryCTA href="/research/discover" tone="secondary">
                Start discovery
              </PrimaryCTA>
            </div>
          </Surface>
        ) : (
          <ReportLibrary rows={rows} />
        )}
      </div>

      <p className="mt-10 border-t border-[color:var(--ib-line)] pt-5 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Every report here is internal research requiring human review. The
        operational view — review workflow, raw report JSON, per-document
        extraction provenance — remains on the{" "}
        <Link
          href="/admin/reports"
          className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          admin draft reports page
        </Link>
        .
      </p>
    </div>
  );
}
