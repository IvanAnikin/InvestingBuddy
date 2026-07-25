"use client";

import { useMemo, useState, type ReactNode } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import rehypeSanitize from "rehype-sanitize";
import ReportSectionNav from "./ReportSectionNav";
import { extractHeadings, nodeToText, slugify } from "./markdownUtils";

type View = "preview" | "raw";

function headingId(children: ReactNode): string {
  return slugify(nodeToText(children));
}

/**
 * Safe, readable markdown preview for internal draft reports.
 *
 * Rendering is sanitized: `react-markdown` builds the DOM from the markdown AST
 * (no `dangerouslySetInnerHTML`) and `rehype-sanitize` strips any unsafe nodes
 * or attributes. `remark-gfm` adds tables, task lists and strikethrough.
 *
 * A "Raw Markdown" toggle preserves the original, unformatted content for
 * debugging. The component never hides schema errors, warnings, missing-info
 * notes or safety disclaimers — those are rendered by the surrounding page and
 * remain visible in both views.
 */
export default function MarkdownReportPreview({
  content,
  title = "Report Content",
  subtitle,
}: {
  content: string;
  title?: string;
  /** Overrides the default draft subtitle (Phase 28A.2). */
  subtitle?: string;
}) {
  const [view, setView] = useState<View>("preview");
  const headings = useMemo(() => extractHeadings(content), [content]);

  return (
    <section className="rounded-2xl border border-white/10 bg-white/[0.045] p-5 shadow-[0_8px_30px_rgba(2,6,23,0.45)] backdrop-blur-xl">
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <div>
          <p className="text-xs font-semibold uppercase tracking-wide text-slate-400">
            {title}
          </p>
          <p className="mt-0.5 text-[11px] italic text-slate-500">
            {subtitle ??
              "Unvalidated internal admin draft produced by AI agents — not reviewed for accuracy, not investment advice."}
          </p>
        </div>
        {/* Preview / Raw toggle */}
        <div
          className="inline-flex rounded-lg border border-white/10 bg-white/5 p-0.5"
          role="group"
          aria-label="Report view mode"
        >
          <button
            type="button"
            data-testid="report-view-preview"
            aria-pressed={view === "preview"}
            onClick={() => setView("preview")}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
              view === "preview"
                ? "bg-white/15 text-white"
                : "text-slate-400 hover:text-slate-100"
            }`}
          >
            Preview
          </button>
          <button
            type="button"
            data-testid="report-view-raw"
            aria-pressed={view === "raw"}
            onClick={() => setView("raw")}
            className={`rounded-md px-3 py-1 text-xs font-medium transition-colors ${
              view === "raw"
                ? "bg-white/15 text-white"
                : "text-slate-400 hover:text-slate-100"
            }`}
          >
            Raw Markdown
          </button>
        </div>
      </div>

      {view === "preview" ? (
        <div className="flex gap-6">
          <ReportSectionNav headings={headings} />
          <div
            data-testid="report-markdown-preview"
            className="markdown-body min-w-0 flex-1 overflow-auto"
          >
            <ReactMarkdown
              remarkPlugins={[remarkGfm]}
              rehypePlugins={[rehypeSanitize]}
              components={{
                h1: ({ children }) => (
                  <h1 id={headingId(children)}>{children}</h1>
                ),
                h2: ({ children }) => (
                  <h2 id={headingId(children)}>{children}</h2>
                ),
                h3: ({ children }) => (
                  <h3 id={headingId(children)}>{children}</h3>
                ),
                a: ({ children, href }) => (
                  <a href={href} target="_blank" rel="noopener noreferrer">
                    {children}
                  </a>
                ),
              }}
            >
              {content}
            </ReactMarkdown>
          </div>
        </div>
      ) : (
        <pre
          data-testid="report-markdown-raw"
          className="max-h-[600px] overflow-auto rounded-xl border border-white/10 bg-slate-950/50 p-4 font-mono text-xs whitespace-pre-wrap text-slate-300"
        >
          {content}
        </pre>
      )}
    </section>
  );
}
