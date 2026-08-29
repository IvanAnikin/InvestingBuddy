import Link from "next/link";
import { notFound } from "next/navigation";
import Surface from "@/components/product/Surface";
import MarkdownReportPreview from "@/components/reports/MarkdownReportPreview";
import CouncilSummary from "@/components/research/CouncilSummary";
import EvidencePanel from "@/components/research/EvidencePanel";
import FinancialSnapshot from "@/components/research/FinancialSnapshot";
import NarrativeSection from "@/components/research/NarrativeSection";
import ReportHeader from "@/components/research/ReportHeader";
import ResearchStatusBadge, {
  evidenceWord,
} from "@/components/research/ResearchStatusBadge";
import TrendChart from "@/components/research/TrendChart";
import {
  buildResearchReportView,
  readCouncilMetadata,
} from "@/components/research/reportView";
import { fetchReport, fetchReportPrimaryDocuments } from "@/lib/api";
import type { Report, ReportPrimaryDocumentsResponse } from "@/types/api";

export const dynamic = "force-dynamic";

export const metadata = {
  title: "Research report — InvestingBuddy",
};

async function getReport(id: string): Promise<Report | null> {
  try {
    return await fetchReport(id);
  } catch {
    return null;
  }
}

// A report with no ingestion activity returns an honest all-zero response, so a
// thrown error here means the fetch itself failed. Degrade to null rather than
// breaking the page — the evidence panel then simply has no document list.
async function getPrimaryDocuments(
  id: string,
): Promise<ReportPrimaryDocumentsResponse | null> {
  try {
    return await fetchReportPrimaryDocuments(id);
  } catch {
    return null;
  }
}

export default async function ResearchReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const [report, primaryDocuments] = await Promise.all([
    getReport(id),
    getPrimaryDocuments(id),
  ]);

  if (!report) notFound();

  const council = readCouncilMetadata(report.source_summary_json);
  const view = buildResearchReportView(report, council);
  const isFinal = Boolean(report.final_report_version);

  const statusStrip = (
    <ResearchStatusBadge
      evidence={view.evidence.overall}
      humanReviewRequired={view.humanReviewRequired}
      extra={
        <Link
          href={`/admin/reports/${report.id}`}
          className="text-xs text-[color:var(--ib-ink-3)] underline decoration-dotted underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          Research diagnostics
        </Link>
      }
    />
  );

  return (
    <div className="ib-fade-up space-y-5">
      <nav aria-label="Breadcrumb">
        <Link
          href="/research/reports"
          className="text-sm text-[color:var(--ib-ink-3)] hover:text-[color:var(--ib-ink-2)]"
        >
          ← Research library
        </Link>
      </nav>

      <ReportHeader
        identity={view.identity}
        periods={view.snapshot.periods}
        council={view.council}
        evidenceWordLabel={
          view.evidence.overall ? evidenceWord(view.evidence.overall) : null
        }
        updatedAt={view.updatedAt}
        reportId={report.id}
        isFinal={isFinal}
      />

      {statusStrip}

      {/* A report the pipeline never wrote structured content for cannot be
          rendered as structured research. It keeps its own honest view rather
          than being forced through a renderer that would show empty sections. */}
      {!view.structured ? (
        <>
          <Surface className="p-6">
            <p className="text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
              This report has no structured research content — it predates the
              structured report format. Its original text is shown below, and the
              full record is in the technical view.
            </p>
            <p className="mt-3">
              <Link
                href={`/admin/reports/${report.id}`}
                className="text-sm text-[color:var(--ib-ink-3)] underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
              >
                Open the technical report
              </Link>
            </p>
          </Surface>
          {report.content_markdown && (
            <MarkdownReportPreview
              content={report.content_markdown}
              title="Report content"
            />
          )}
        </>
      ) : (
        <>
          {/* Evidence too thin for a full analysis: say so once, at the top,
              and let the sections below show what IS known. */}
          {view.thin && (
            <Surface
              className="border-amber-400/25 p-5"
              testId="thin-evidence-notice"
            >
              <p className="text-sm font-medium text-amber-200">
                Evidence is insufficient for a full company analysis
              </p>
              <p className="mt-2 max-w-3xl text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                What was found is shown below, together with what is missing.
                The analysis sections are omitted rather than presented empty —
                an argument built on evidence that does not exist is worse than
                no argument.
              </p>
            </Surface>
          )}

          {/* Summary */}
          {view.summary && (
            <Surface as="section" className="p-6 sm:p-7" testId="report-summary">
              <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
                Summary
              </h2>
              <p className="mt-3 max-w-3xl whitespace-pre-line text-sm leading-relaxed text-[color:var(--ib-ink-2)]">
                {view.summary}
              </p>
            </Surface>
          )}

          <FinancialSnapshot snapshot={view.snapshot} />

          {/* Historical trends */}
          {view.trends.series.length > 0 && (
            <Surface
              as="section"
              className="p-6 sm:p-7"
              testId="historical-trends"
              id="trends"
            >
              <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
                Historical trends
              </h2>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
                Reconstructed from the issuer&apos;s own multi-period tables.
                Historical only — nothing here is projected.
              </p>
              <ul className="mt-5 space-y-3">
                {view.trends.series.map((series, i) => (
                  <TrendChart
                    key={`${series.metric}-${series.scope}-${i}`}
                    series={series}
                  />
                ))}
              </ul>
              {view.trends.note && (
                <p className="mt-4 border-t border-[color:var(--ib-line)] pt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                  {view.trends.note}
                </p>
              )}
            </Surface>
          )}

          {/* Cases */}
          {!view.thin && (
            <div className="grid gap-5 lg:grid-cols-2">
              <NarrativeSection
                title="Bull case"
                accent="positive"
                groups={view.bull}
                testId="bull-case"
                emptyMessage="No bull-case argument was produced from the evidence available."
              />
              <NarrativeSection
                title="Bear case"
                accent="negative"
                groups={view.bear}
                testId="bear-case"
                emptyMessage="No bear-case argument was produced from the evidence available."
              />
            </div>
          )}

          {!view.thin && (
            <NarrativeSection
              title="Key risks"
              groups={view.risks}
              testId="risk-analysis"
              id="risks"
              emptyMessage="No risk was recorded against the evidence in this report. That is an absence of analysis, not an absence of risk."
            />
          )}

          <CouncilSummary council={view.council} reportId={report.id} />

          {/* Missing information — kept prominent on purpose. */}
          <Surface
            as="section"
            className="border-amber-400/20 p-6 sm:p-7"
            testId="missing-information"
            id="missing"
          >
            <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
              Important missing information
            </h2>
            {view.missing.items.length === 0 ? (
              <p className="mt-3 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
                No specific gap was recorded for this report.
              </p>
            ) : (
              <>
                <p className="mt-2 text-sm text-[color:var(--ib-ink-3)]">
                  {view.missing.total} item(s) the research could not source.
                </p>
                <ul className="mt-4 grid gap-x-8 gap-y-1.5 sm:grid-cols-2">
                  {view.missing.items.map((item, i) => (
                    <li key={i} className="text-sm text-[color:var(--ib-ink-2)]">
                      <span className="font-mono text-xs">{item.field}</span>
                      {item.source && (
                        <span className="ml-2 text-xs text-[color:var(--ib-ink-3)]">
                          ({item.source})
                        </span>
                      )}
                    </li>
                  ))}
                </ul>
              </>
            )}
          </Surface>

          <EvidencePanel
            primaryDocuments={primaryDocuments}
            disclosures={view.disclosures}
            appendix={view.appendix}
            channels={view.channels}
            reportId={report.id}
          />

          {/* Evidence quality, per dimension, with the basis for each. */}
          {view.evidence.dimensions.length > 0 && (
            <Surface as="section" className="p-6 sm:p-7" testId="evidence-quality">
              <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
                Evidence quality
              </h2>
              <p className="mt-2 max-w-2xl text-sm leading-relaxed text-[color:var(--ib-ink-3)]">
                Assessed once, per dimension. The overall figure is the weakest
                contributing dimension — never an average.
              </p>
              <dl className="mt-5 space-y-4">
                {view.evidence.dimensions.map((dim) => (
                  <div
                    key={dim.key}
                    className="border-b border-[color:var(--ib-line)] pb-4 last:border-0 last:pb-0"
                  >
                    <dt className="flex flex-wrap items-baseline gap-x-3">
                      <span className="text-sm font-medium text-[color:var(--ib-ink)]">
                        {dim.label}
                      </span>
                      <span className="text-sm text-[color:var(--ib-ink-2)]">
                        {dim.value ? evidenceWord(dim.value) : "not assessed"}
                      </span>
                    </dt>
                    {dim.basis.length > 0 && (
                      <dd className="mt-1.5 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                        {dim.basis.join(" · ")}
                      </dd>
                    )}
                  </div>
                ))}
              </dl>
            </Surface>
          )}

          {/* Next steps */}
          {view.nextSteps.length > 0 && (
            <Surface as="section" className="p-6 sm:p-7" testId="next-steps">
              <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
                Research next steps
              </h2>
              <ul className="mt-4 space-y-2">
                {view.nextSteps.map((step, i) => (
                  <li
                    key={i}
                    className="flex gap-3 text-sm leading-relaxed text-[color:var(--ib-ink-2)]"
                  >
                    <span
                      aria-hidden="true"
                      className="mt-2.5 h-px w-3 shrink-0 bg-[color:var(--ib-line-strong)]"
                    />
                    <span>{step}</span>
                  </li>
                ))}
              </ul>
            </Surface>
          )}
        </>
      )}

      <p className="pt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Every figure above keeps the document, period and scope it was extracted
        from. The full technical provenance — raw report JSON, per-document
        excerpts, per-fact page and table location, validation flags and the
        review timeline — is on the{" "}
        <Link
          href={`/admin/reports/${report.id}`}
          className="underline underline-offset-4 hover:text-[color:var(--ib-ink-2)]"
        >
          technical report page
        </Link>
        .
      </p>
    </div>
  );
}
