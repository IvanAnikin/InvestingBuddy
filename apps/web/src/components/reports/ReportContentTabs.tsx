"use client";

// Phase 28A.2 — tabbed report content for final-report-generator drafts.
//
// The readable, human-formatted view is the DEFAULT product view. The raw
// structured JSON stays available for debugging, but behind a developer tab —
// it is never the default. Legacy markdown reports do not use this component
// (the page renders the markdown preview directly).

import { useState } from "react";
import type { LlmCouncilMetadata, Report } from "@/types/api";
import type { ReportContent } from "./finalReportContent";
import FinalReportRenderer from "./FinalReportRenderer";
import LlmCouncilAnalysis, { LlmCouncilSummaryCard } from "./LlmCouncilAnalysis";
import MarkdownReportPreview from "./MarkdownReportPreview";

type TabId = "readable" | "council" | "json" | "markdown";

export default function ReportContentTabs({
  report,
  content,
  council,
  schemaValid,
  safetyValid,
}: {
  report: Report;
  content: ReportContent | null;
  council: LlmCouncilMetadata | null;
  schemaValid: boolean;
  safetyValid: boolean;
}) {
  const [tab, setTab] = useState<TabId>("readable");
  const llmUsed = Boolean(council?.llm_used);

  const tabs: { id: TabId; label: string }[] = [
    { id: "readable", label: "Readable Report" },
    ...(llmUsed ? [{ id: "council" as TabId, label: "LLM Council" }] : []),
    { id: "json", label: "Raw JSON" },
    { id: "markdown", label: "Raw Markdown" },
  ];

  return (
    <section data-testid="report-content-tabs" className="space-y-4">
      {/* Phase 28A.2 amendment — pin the compact LLM Council summary above the
          tabs so the council (the main product value) is visible immediately.
          Full per-agent detail stays in the LLM Council tab. */}
      {llmUsed && council && (
        <LlmCouncilSummaryCard
          council={council}
          schemaValid={schemaValid}
          safetyValid={safetyValid}
          onViewFull={() => setTab("council")}
        />
      )}

      <div className="mb-4 flex flex-wrap gap-1 border-b border-white/10">
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            data-testid={`report-tab-${t.id}`}
            onClick={() => setTab(t.id)}
            className={`-mb-px rounded-t-lg border-b-2 px-4 py-2 text-sm font-medium transition-colors ${
              tab === t.id
                ? "border-sky-400 text-white"
                : "border-transparent text-slate-400 hover:text-slate-200"
            }`}
          >
            {t.label}
          </button>
        ))}
      </div>

      {tab === "readable" &&
        (content ? (
          <FinalReportRenderer
            content={content}
            schemaValid={schemaValid}
            safetyValid={safetyValid}
          />
        ) : (
          <p className="text-sm italic text-slate-500">
            Structured report content is unavailable — see the Raw Markdown tab.
          </p>
        ))}

      {tab === "council" && council && <LlmCouncilAnalysis council={council} />}

      {tab === "json" && (
        <div data-testid="report-raw-json">
          <p className="mb-2 text-[11px] italic text-slate-500">
            Developer view — the structured report content as stored. Not the product view.
          </p>
          <pre className="max-h-[70vh] overflow-auto rounded-lg border border-white/10 bg-black/40 p-4 text-[11px] leading-relaxed text-slate-300">
            {JSON.stringify(content ?? {}, null, 2)}
          </pre>
        </div>
      )}

      {tab === "markdown" &&
        (report.content_markdown ? (
          <MarkdownReportPreview
            content={report.content_markdown}
            title="Raw Markdown"
            subtitle="Developer / debug view — the raw stored markdown, including the structured JSON block."
          />
        ) : (
          <p className="text-sm italic text-slate-500">No content markdown available.</p>
        ))}
    </section>
  );
}
