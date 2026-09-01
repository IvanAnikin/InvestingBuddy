import Link from "next/link";
import { notFound } from "next/navigation";
import Surface from "@/components/product/Surface";
import MarkdownReportPreview from "@/components/reports/MarkdownReportPreview";
import BusinessQuality from "@/components/research/report/BusinessQuality";
import ChairSynthesis from "@/components/research/report/ChairSynthesis";
import EvidenceDisclosure from "@/components/research/report/EvidenceDisclosure";
import InvestmentSummary from "@/components/research/report/InvestmentSummary";
import KeyFinancials from "@/components/research/report/KeyFinancials";
import OpenQuestions from "@/components/research/report/OpenQuestions";
import RecentDevelopments from "@/components/research/report/RecentDevelopments";
import RedTeam from "@/components/research/report/RedTeam";
import ResilienceExposure from "@/components/research/report/ResilienceExposure";
import ResearchConfidence from "@/components/research/report/ResearchConfidence";
import ResearchCouncil from "@/components/research/report/ResearchCouncil";
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
import {
  buildInvestorReportView,
  buildResearchConfidence,
  findAgent,
  metricDirections,
  reconcileCouncilNumbers,
} from "@/components/research/reportSections";
import {
  buildResearchLinkState,
  NO_RESEARCH_LINK,
  type ResearchLinkState,
} from "@/components/research/reportResolution";
import { fetchReport, fetchReports, fetchReportPrimaryDocuments } from "@/lib/api";
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

/**
 * Where this report sits among its company's other reports.
 *
 * A reader who opens a legacy or superseded artefact must not be stranded in
 * it. Resolving that needs the company's own report list — a scoped read, not
 * the global newest report, and not a guess from the title.
 */
async function getResearchLink(report: Report): Promise<ResearchLinkState> {
  if (!report.company_id) {
    return buildResearchLinkState(report, [report]);
  }
  try {
    const cohort = await fetchReports(50, 0, { companyId: report.company_id });
    return buildResearchLinkState(report, cohort.items);
  } catch {
    // Without the cohort nothing can be claimed about supersession, so nothing
    // is: the report renders as itself with no "current research" offer.
    return NO_RESEARCH_LINK;
  }
}

export default async function ResearchReportPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = await params;
  const report = await getReport(id);
  if (!report) notFound();

  const [primaryDocuments, link] = await Promise.all([
    getPrimaryDocuments(id),
    getResearchLink(report),
  ]);

  const council = readCouncilMetadata(report.source_summary_json);
  const view = buildResearchReportView(report, council);
  // Council prose and the canonical figures are two representations of the
  // same facts. Where they disagree the sentence is withheld and said to
  // conflict — never silently resolved in favour of one of them.
  const investor = reconcileCouncilNumbers(
    buildInvestorReportView(report.content_markdown, council),
    view.snapshot,
    view.trends.series,
  );
  const confidence = buildResearchConfidence(
    investor.risks,
    view.missing.total,
    investor.agents,
    // Record-completeness entries lifted out of the bear case and the chair's
    // open-question list. They are reported here, where they describe what
    // they actually describe, rather than as investment arguments.
    [...investor.recordGaps, ...view.narrativeRecordGaps],
    investor.routedLimitations,
  );
  const isFinal = Boolean(report.final_report_version);

  // A newer structured report exists for this company and this is not it.
  const supersededBy =
    link.currentReportId && link.currentReportId !== report.id
      ? link.currentReportId
      : null;

  const councilLine = view.council.used
    ? `${view.council.completed} council agent${view.council.completed === 1 ? "" : "s"}`
    : "council did not run";

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
        supersededBy={supersededBy}
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

          {/* 1. The research itself, first. */}
          <InvestmentSummary
            chair={investor.chair}
            reading={investor.reading}
            summary={view.summary}
            evidenceWordLabel={
              view.evidence.overall ? evidenceWord(view.evidence.overall) : null
            }
            councilLine={councilLine}
          />

          {/* 2. The numbers. */}
          <KeyFinancials
            snapshot={view.snapshot}
            directions={metricDirections(view.trends.series)}
          />

          {/* 3. What changed over time. */}
          {view.trends.series.length > 0 && (
            <Surface
              as="section"
              className="p-6 sm:p-7"
              testId="historical-trends"
              id="trends"
            >
              <h2 className="text-lg font-semibold tracking-tight text-[color:var(--ib-ink)]">
                Historical performance
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
                <p className="ib-breakable mt-4 border-t border-[color:var(--ib-line)] pt-3 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
                  {view.trends.note}
                </p>
              )}
            </Surface>
          )}

          {/* 4. The business. */}
          {!view.thin && <BusinessQuality business={investor.businessQuality} />}

          {/* 5. What has happened lately. */}
          {!view.thin && (
            <RecentDevelopments
              catalysts={investor.catalysts}
              disclosures={view.disclosures}
            />
          )}

          {/* 6. The two cases. */}
          {!view.thin && (
            <div className="grid gap-5 lg:grid-cols-2">
              <NarrativeSection
                title="Bull case"
                accent="positive"
                groups={view.bull}
                testId="bull-case"
                emptyMessage="No bull-case argument was produced from the evidence available."
                footnote={
                  view.bullConfidence
                    ? `Stated confidence: ${view.bullConfidence}.`
                    : undefined
                }
              />
              <NarrativeSection
                title="Bear case"
                accent="negative"
                groups={view.bear}
                testId="bear-case"
                emptyMessage="No bear-case argument was produced from the evidence available."
                footnote={
                  view.bearConfidence
                    ? `Stated confidence: ${view.bearConfidence}.`
                    : undefined
                }
              />
            </div>
          )}

          {!view.thin && <ResilienceExposure reading={investor.reading} />}

          {/* 7. Risks to the BUSINESS. Research limitations are further down,
                 under research confidence, where they belong. */}
          {!view.thin && (
            <NarrativeSection
              title="Key risks"
              groups={investor.risks.company}
              testId="risk-analysis"
              id="risks"
              emptyMessage="No risk to the business was recorded against the evidence in this report. That is an absence of analysis, not an absence of risk."
              footnote={investor.risks.summary ?? undefined}
            />
          )}

          {/* 8. Who looked at it, and what each of them concluded. */}
          <ResearchCouncil
            council={view.council}
            agents={investor.agents}
            reportId={report.id}
          />

          {view.council.used && (
            <RedTeam redTeam={findAgent(investor.agents, "red_team")} />
          )}

          <ChairSynthesis
            chair={investor.chair}
            chairAgent={findAgent(investor.agents, "committee_chair")}
            reading={investor.reading}
          />

          <OpenQuestions questions={investor.openQuestions} />

          {/* 9. How far the evidence goes. */}
          <ResearchConfidence
            dimensions={view.evidence.dimensions}
            confidence={confidence}
            missingItems={view.missing.items}
            numericConflicts={investor.numericConflicts}
            reportId={report.id}
          />

          {/* 10. Source transparency, kept complete and kept collapsed. */}
          <EvidenceDisclosure
            primaryDocuments={primaryDocuments}
            disclosures={view.disclosures}
            appendix={view.appendix}
            channels={view.channels}
            reportId={report.id}
            sourceNotes={[
              view.snapshot.currentPeriodNote,
              view.snapshot.statementsNote,
              view.snapshot.periods?.note ?? null,
            ].filter((n): n is string => Boolean(n))}
          />

          {/* Deterministic research memo next steps, when the backend flag that
              produces them is on. */}
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
                    <span className="ib-breakable">{step}</span>
                  </li>
                ))}
              </ul>
            </Surface>
          )}
        </>
      )}

      <p className="pt-2 text-xs leading-relaxed text-[color:var(--ib-ink-3)]">
        Internal research requiring human review. The full technical record —
        raw report JSON, per-document excerpts, per-fact page and table
        location, validation flags and the review timeline — is on the{" "}
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
