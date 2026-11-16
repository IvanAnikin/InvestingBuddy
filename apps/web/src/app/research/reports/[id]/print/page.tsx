/**
 * V3.20 slice 9.3 — the print view of one report: the full professional report or the
 * one-page memo (`?format=memo`), laid out for the browser's "Save as PDF". Both are
 * renderings of the same stored report; nothing here re-derives or re-words it.
 */

import Link from "next/link";
import { notFound } from "next/navigation";
import PrintButton from "@/components/research/report/PrintButton";
import ProfessionalResearchReport from "@/components/research/report/ProfessionalResearchReport";
import ResearchMemo from "@/components/research/report/ResearchMemo";
import { readV3Research } from "@/components/research/v3Research";
import { fetchReport } from "@/lib/api";

export default async function ReportPrintPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ [key: string]: string | string[] | undefined }>;
}) {
  const { id } = await params;
  const format = (await searchParams).format === "memo" ? "memo" : "report";
  let report;
  try {
    report = await fetchReport(id);
  } catch {
    report = null;
  }
  if (!report) notFound();
  const professional = readV3Research(report.source_summary_json)?.professionalResearch ?? null;

  return (
    <div data-print-root className="space-y-4" data-testid="report-print-view">
      <div data-print-hide className="flex flex-wrap items-center gap-3">
        <PrintButton />
        <Link
          href={`/research/reports/${encodeURIComponent(id)}/print${format === "memo" ? "" : "?format=memo"}`}
          className="text-sm text-[color:var(--ib-accent)] underline underline-offset-4"
        >
          {format === "memo" ? "Full report" : "One-page memo"}
        </Link>
        <Link
          href={`/research/reports/${encodeURIComponent(id)}`}
          className="text-sm text-[color:var(--ib-ink-3)] underline underline-offset-4"
        >
          Back to the report
        </Link>
      </div>
      {!professional ? (
        <p className="text-sm text-[color:var(--ib-ink-2)]">
          This report has no V3 research report to print; use the report page.
        </p>
      ) : format === "memo" ? (
        <ResearchMemo report={professional} title={report.title} createdAt={report.created_at} />
      ) : (
        <ProfessionalResearchReport report={professional} />
      )}
    </div>
  );
}
