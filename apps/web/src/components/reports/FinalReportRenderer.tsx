// Phase 28A.2 — readable renderer for final-report-generator drafts.
//
// Renders the structured `report_content` as clean cards/lists instead of a raw
// JSON dump. Presentational only: it unwraps `{value, provenance, …}` envelopes
// but never fabricates data, preserves provenance chips, and produces NO
// recommendation, rating, price target, fair value, or upside/downside.

import GlassCard from "@/components/ui/GlassCard";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";
import {
  type ChecklistItem,
  type CitationSource,
  type ReportContent,
  META_KEYS,
  SECTION_LABELS,
  SECTION_ORDER,
  humanizeKey,
  isEmptyValue,
  noteText,
  unwrap,
} from "./finalReportContent";

const PROVENANCE: Record<string, { label: string; color: PillColor }> = {
  sourced_fact: { label: "sourced", color: "green" },
  model_interpretation: { label: "model", color: "purple" },
  missing_data: { label: "not sourced", color: "gray" },
};

function ProvenanceChip({ provenance }: { provenance?: string }) {
  if (!provenance) return null;
  const p = PROVENANCE[provenance] ?? { label: provenance, color: "gray" as PillColor };
  return <StatusPill label={p.label} color={p.color} />;
}

function Muted({ children }: { children: React.ReactNode }) {
  return <span className="italic text-slate-500">{children}</span>;
}

// --------------------------------------------------------------------------
// Value rendering
// --------------------------------------------------------------------------

function ScalarValue({ value, currency }: { value: unknown; currency?: string }) {
  if (isEmptyValue(value)) return <Muted>Not sourced</Muted>;
  if (typeof value === "boolean")
    return <span className="text-slate-200">{value ? "Yes" : "No"}</span>;
  if (typeof value === "number")
    return (
      <span className="text-slate-200">
        {value.toLocaleString()} {currency ?? ""}
      </span>
    );
  return <span className="whitespace-pre-line text-slate-200">{String(value)}</span>;
}

function ObjectLine({ obj }: { obj: Record<string, unknown> }) {
  // Common shapes: {field, source}; otherwise a compact key: value join.
  if ("field" in obj) {
    return (
      <span className="text-slate-300">
        <code className="rounded bg-white/10 px-1 font-mono text-xs">
          {String(obj.field)}
        </code>
        {obj.source ? (
          <span className="ml-1 text-xs text-slate-500">({String(obj.source)})</span>
        ) : null}
      </span>
    );
  }
  const scalar = (v: unknown): string =>
    isEmptyValue(v) ? "—" : typeof v === "object" ? "…" : String(v);
  const parts = Object.entries(obj)
    .filter(([k]) => k !== "type")
    .map(([k, v]) => `${humanizeKey(k)}: ${scalar(v)}`);
  return <span className="text-slate-300">{parts.join(" · ")}</span>;
}

function ValueBlock({ value, currency }: { value: unknown; currency?: string }) {
  if (isEmptyValue(value)) return <Muted>Not sourced</Muted>;
  if (Array.isArray(value)) {
    return (
      <ul className="space-y-1">
        {value.map((item, i) => (
          <li key={i} className="text-sm text-slate-300">
            {item && typeof item === "object" ? (
              <ObjectLine obj={item as Record<string, unknown>} />
            ) : (
              <span className="text-slate-300">• {String(item)}</span>
            )}
          </li>
        ))}
      </ul>
    );
  }
  if (value && typeof value === "object") {
    return <ObjectLine obj={value as Record<string, unknown>} />;
  }
  return <ScalarValue value={value} currency={currency} />;
}

function FieldRow({ label, field }: { label: string; field: unknown }) {
  const u = unwrap(field);
  return (
    <div className="border-b border-white/5 py-2 last:border-0">
      <div className="mb-1 flex items-center gap-2">
        <span className="text-xs font-semibold uppercase tracking-wide text-slate-400">
          {label}
        </span>
        <ProvenanceChip provenance={u.provenance} />
        {u.asOf ? <span className="text-[10px] text-slate-500">as of {u.asOf}</span> : null}
      </div>
      <ValueBlock value={u.value} currency={u.currency} />
    </div>
  );
}

// --------------------------------------------------------------------------
// Section card
// --------------------------------------------------------------------------

function SectionShell({
  title,
  testId,
  children,
}: {
  title: string;
  testId?: string;
  children: React.ReactNode;
}) {
  return (
    <GlassCard testId={testId} className="p-5">
      <h3 className="mb-3 text-sm font-semibold text-slate-100">{title}</h3>
      {children}
    </GlassCard>
  );
}

function GenericSection({ title, section }: { title: string; section: Record<string, unknown> }) {
  const available = section.available;
  const note = noteText(section.note);
  const disclaimer = noteText(section.disclaimer);
  const fields = Object.entries(section).filter(([k]) => !META_KEYS.has(k) && k !== "note" && k !== "disclaimer");

  return (
    <SectionShell title={title}>
      {available === false ? (
        <p className="text-sm text-slate-400">
          Not available.{" "}
          {note ? <span className="text-slate-500">{note}</span> : null}
        </p>
      ) : fields.length === 0 ? (
        <p className="text-sm">
          <Muted>No data.</Muted>
        </p>
      ) : (
        <div>
          {fields.map(([k, v]) => (
            <FieldRow key={k} label={humanizeKey(k)} field={v} />
          ))}
        </div>
      )}
      {disclaimer ? (
        <p className="mt-3 text-[11px] italic text-slate-500">{disclaimer}</p>
      ) : null}
    </SectionShell>
  );
}

// --------------------------------------------------------------------------
// Bespoke sections
// --------------------------------------------------------------------------

function ExecutiveSummary({ section }: { section: Record<string, unknown> }) {
  const committee = unwrap(section.committee_note);
  const score = unwrap(section.score_note);
  return (
    <SectionShell title="Executive Summary" testId="report-section-executive_summary">
      <div className="mb-3 flex flex-wrap gap-2">
        {section.company_name ? (
          <StatusPill label={String(section.company_name)} color="blue" />
        ) : null}
        {section.ticker ? <StatusPill label={String(section.ticker)} color="gray" /> : null}
        {section.internal_status ? (
          <StatusPill label={`Status: ${String(section.internal_status)}`} color="amber" />
        ) : null}
        {section.overall_score != null ? (
          <StatusPill label={`Score: ${String(section.overall_score)}`} color="purple" />
        ) : null}
      </div>
      {!isEmptyValue(committee.value) ? (
        <p className="whitespace-pre-line text-sm text-slate-300">{String(committee.value)}</p>
      ) : null}
      {!isEmptyValue(score.value) ? (
        <p className="mt-2 text-xs text-slate-500">{String(score.value)}</p>
      ) : null}
    </SectionShell>
  );
}

function ChecklistSection({ section }: { section: Record<string, unknown> }) {
  const items = (Array.isArray(section) ? section : []) as ChecklistItem[];
  return (
    <SectionShell title="Human Review Checklist" testId="report-section-human_review_checklist">
      <ul className="space-y-2">
        {items.map((it, i) => (
          <li key={i} className="flex gap-2 text-sm">
            <span className={it.completed ? "text-emerald-400" : "text-slate-500"}>
              {it.completed ? "✓" : "○"}
            </span>
            <span className="min-w-0">
              <span className="text-slate-300">{it.item ?? it.label}</span>
              {it.required ? (
                <span className="ml-1 text-[10px] uppercase text-amber-400/80">required</span>
              ) : null}
              {it.note ? (
                <span className="mt-0.5 block text-xs text-slate-500">{it.note}</span>
              ) : null}
            </span>
          </li>
        ))}
      </ul>
    </SectionShell>
  );
}

function AppendixSection({ section }: { section: Record<string, unknown> }) {
  const sources = (unwrap(section.sources).value as CitationSource[] | null) ?? [];
  const citations = unwrap(section.citations);
  const total = unwrap(section.sources).total ?? sources.length;
  return (
    <SectionShell title="Source Citation Appendix" testId="report-section-source_citation_appendix">
      <p className="mb-2 text-xs text-slate-500">
        {total} source(s){citations.total != null ? ` · ${citations.total} citation(s)` : ""}
      </p>
      {sources.length === 0 ? (
        <p className="text-sm">
          <Muted>No sources cited yet — human review required to source claims.</Muted>
        </p>
      ) : (
        <div className="overflow-x-auto">
          <table className="w-full min-w-[520px] text-left text-xs">
            <thead className="text-slate-500">
              <tr>
                <th className="py-1 pr-3">Title</th>
                <th className="py-1 pr-3">Type</th>
                <th className="py-1 pr-3">Tier</th>
              </tr>
            </thead>
            <tbody className="text-slate-300">
              {sources.map((s, i) => (
                <tr key={i} className="border-t border-white/5 align-top">
                  <td className="py-1 pr-3">
                    {s.url ? (
                      <a
                        href={s.url}
                        target="_blank"
                        rel="noreferrer"
                        className="text-sky-400 hover:underline"
                      >
                        {s.title ?? s.url}
                      </a>
                    ) : (
                      (s.title ?? "—")
                    )}
                    {s.source_quote ? (
                      <span className="mt-0.5 block text-[11px] italic text-slate-500">
                        “{s.source_quote}”
                      </span>
                    ) : null}
                  </td>
                  <td className="py-1 pr-3">{s.source_type ?? "—"}</td>
                  <td className="py-1 pr-3 font-mono text-[10px]">{s.source_tier ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </SectionShell>
  );
}

function CouncilSummarySection({ section }: { section: Record<string, unknown> }) {
  const rows: [string, unknown][] = [
    ["Provider", section.provider],
    ["Model", section.model],
    ["Council Version", section.council_version],
    ["Agents Completed", section.agents_completed],
    ["Evidence Items", section.evidence_item_count],
    ["Committee Label", section.committee_label],
  ];
  return (
    <SectionShell title="LLM Council Analysis" testId="report-section-llm_council_analysis">
      <div className="flex flex-wrap gap-x-6 gap-y-1 text-sm">
        {rows
          .filter(([, v]) => !isEmptyValue(v))
          .map(([k, v]) => (
            <span key={k} className="text-slate-300">
              <span className="text-slate-500">{k}:</span> {String(v)}
            </span>
          ))}
      </div>
      <p className="mt-3 text-xs text-slate-500">
        Full agent-by-agent, citation-bound analysis is in the{" "}
        <strong className="text-slate-300">LLM Council</strong> tab. Every claim
        cites evidence ids; no rating or valuation conclusion is produced.
      </p>
    </SectionShell>
  );
}

// --------------------------------------------------------------------------
// Renderer
// --------------------------------------------------------------------------

export default function FinalReportRenderer({
  content,
  schemaValid,
  safetyValid,
}: {
  content: ReportContent;
  schemaValid: boolean;
  safetyValid: boolean;
}) {
  const validated = schemaValid && safetyValid;
  return (
    <div className="space-y-4" data-testid="readable-report">
      {/* Phase 28A.2 task 6 — validated vs cautionary draft disclaimer. The
          hard safety banner above the page is unchanged. */}
      <p
        data-testid="readable-report-disclaimer"
        className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-slate-400"
      >
        {validated
          ? "Validated internal admin draft. Not investment advice. Human review required. Public publishing is not implemented."
          : "Internal admin draft — schema or safety validation is incomplete. Not investment advice. Human review required. Public publishing is not implemented."}
      </p>

      {SECTION_ORDER.map((key) => {
        const section = content[key];
        if (section == null) return null;
        if (key === "executive_summary")
          return <ExecutiveSummary key={key} section={section as Record<string, unknown>} />;
        if (key === "human_review_checklist")
          return <ChecklistSection key={key} section={section as Record<string, unknown>} />;
        if (key === "source_citation_appendix")
          return <AppendixSection key={key} section={section as Record<string, unknown>} />;
        if (key === "llm_council_analysis")
          return <CouncilSummarySection key={key} section={section as Record<string, unknown>} />;
        return (
          <GenericSection
            key={key}
            title={SECTION_LABELS[key] ?? humanizeKey(key)}
            section={section as Record<string, unknown>}
          />
        );
      })}
    </div>
  );
}
