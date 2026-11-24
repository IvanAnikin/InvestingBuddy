"use client";

/**
 * V3.21 PDF export — buttons that build a PDF in the browser and download it. The PDF
 * library (@react-pdf/renderer) and the documents load only on the first click, so no
 * page pays for them up front. Nothing is sent anywhere: the report and run are the ones
 * the page already shows, plus the report's market data from the API.
 */

import { useState } from "react";
import { fetchReport, fetchReportMarketData } from "@/lib/api";
import type { DiscoveryCandidate, DiscoveryRun, ReportMarketData } from "@/types/api";
import { readV3Research } from "@/components/research/v3Research";
import { fileSlug } from "./format";

type Status = { busy: boolean; error: string | null };

async function saveBlob(blob: Blob, name: string): Promise<void> {
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = name;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 10_000);
}

function DownloadButton({
  label,
  busyLabel,
  testId,
  primary,
  onClick,
}: {
  label: string;
  busyLabel: string;
  testId: string;
  primary?: boolean;
  onClick: () => Promise<void>;
}) {
  const [st, setSt] = useState<Status>({ busy: false, error: null });
  const run = async () => {
    setSt({ busy: true, error: null });
    try {
      await onClick();
      setSt({ busy: false, error: null });
    } catch (e) {
      setSt({ busy: false, error: e instanceof Error ? e.message : "The PDF could not be built." });
    }
  };
  return (
    <span className="inline-flex flex-col">
      <button
        type="button"
        onClick={run}
        disabled={st.busy}
        data-testid={testId}
        data-print-hide
        className={
          primary
            ? "inline-flex items-center gap-2 rounded-lg bg-[color:var(--ib-accent)] px-3.5 py-2 text-sm font-medium text-slate-950 transition hover:brightness-110 disabled:cursor-wait disabled:opacity-60"
            : "inline-flex items-center gap-2 rounded-lg border border-[color:var(--ib-line-strong)] px-3.5 py-2 text-sm text-[color:var(--ib-ink)] transition hover:bg-[color:var(--ib-surface-raised)] disabled:cursor-wait disabled:opacity-60"
        }
      >
        <svg aria-hidden width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M12 3v12m0 0l-4-4m4 4l4-4M5 21h14" />
        </svg>
        {st.busy ? busyLabel : label}
      </button>
      {st.error && (
        <span role="alert" className="mt-1 text-xs text-rose-300">
          {st.error}
        </span>
      )}
    </span>
  );
}

/** Investor brief for one report: key figures, price and filed-results charts, catalysts, risks. */
export function ReportPdfButton({ reportId }: { reportId: string }) {
  return (
    <DownloadButton
      label="Download investor PDF"
      busyLabel="Building PDF…"
      testId="report-pdf-download"
      primary
      onClick={async () => {
        const [{ pdf }, { default: InvestorBriefPdf }, { buildInvestorBrief }, { registerFonts }] = await Promise.all([
          import("@react-pdf/renderer"),
          import("./InvestorBriefPdf"),
          import("./investorBrief"),
          import("./theme"),
        ]);
        const report = await fetchReport(reportId);
        const research = readV3Research(report.source_summary_json)?.professionalResearch;
        if (!research) throw new Error("This report has no V3 research to export.");
        // Market data is optional: without it the brief still prints, with the charts
        // saying why they are empty.
        let market: ReportMarketData | null = null;
        try {
          market = await fetchReportMarketData(reportId);
        } catch {
          market = null;
        }
        registerFonts(window.location.origin);
        const brief = buildInvestorBrief(report, research, market);
        const generatedAt = new Date().toISOString();
        const blob = await pdf(<InvestorBriefPdf brief={brief} generatedAt={generatedAt} />).toBlob();
        await saveBlob(blob, `${fileSlug(brief.ticker ?? brief.name)}-investor-brief-${generatedAt.slice(0, 10)}.pdf`);
      }}
    />
  );
}

/** The discovery run: the brief, what was checked, sizes vs. the mandate, the candidates. */
export function DiscoveryPdfButton({ run, candidates }: { run: DiscoveryRun; candidates: DiscoveryCandidate[] }) {
  return (
    <DownloadButton
      label="Download run PDF"
      busyLabel="Building PDF…"
      testId="discovery-pdf-download"
      onClick={async () => {
        const [{ pdf }, { default: DiscoveryRunPdf }, { buildDiscoveryBrief }, { registerFonts }] = await Promise.all([
          import("@react-pdf/renderer"),
          import("./DiscoveryRunPdf"),
          import("./discoveryBrief"),
          import("./theme"),
        ]);
        registerFonts(window.location.origin);
        const brief = buildDiscoveryBrief(run, candidates);
        const generatedAt = new Date().toISOString();
        const blob = await pdf(<DiscoveryRunPdf brief={brief} generatedAt={generatedAt} />).toBlob();
        await saveBlob(blob, `discovery-${fileSlug(brief.thesis).slice(0, 40)}-${run.created_at.slice(0, 10)}.pdf`);
      }}
    />
  );
}
