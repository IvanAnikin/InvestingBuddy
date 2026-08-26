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
  isThinEvidenceReport,
  sectionOrderFor,
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
  // Phase 31 hotfix — the appendix now carries a metadata-only PRIMARY-SOURCE
  // REFERENCE count (issuer IR / annual-report index / regulator venue) even when
  // there are 0 DB-persisted citations. When those references exist, the card must
  // NOT imply "zero sources" — it points the reader to the memo's Primary Evidence.
  // Optional integer reads — undefined when a key is absent (legacy / flag-off
  // reports, which must render exactly as before).
  const numCount = (key: string): number | undefined =>
    typeof section[key] === "number" ? (section[key] as number) : undefined;
  const primaryRefCount = numCount("primary_source_reference_count") ?? 0;
  // Phase 32A Slice 3 — six honest, side-by-side reconciliation counts. They are
  // NEVER summed (each measures a different thing); rendered as a labelled stat
  // grid, with the backend `note` as the authoritative reconciling caption. Only
  // present when the persistence flag is on — absent ⇒ this whole block is skipped
  // and the appendix renders byte-identically to before.
  const reconcile: [string, number | undefined][] = [
    ["Primary-source references", numCount("primary_source_reference_count")],
    ["Extracted evidence items", numCount("extracted_evidence_count")],
    ["Structured financial facts", numCount("structured_financial_fact_count")],
    ["DB-persisted sources", numCount("db_persisted_source_count")],
    ["DB-persisted citations", numCount("db_persisted_citation_count")],
    ["Council claim citations", numCount("council_claim_citation_count")],
  ];
  const hasReconcile = reconcile.some(([, v]) => v != null);
  const councilClaimCitations = numCount("council_claim_citation_count") ?? 0;
  const dbCitationCount = numCount("db_persisted_citation_count") ?? 0;
  const dbSourceCount = numCount("db_persisted_source_count") ?? 0;
  // Honest empty-state guard: never imply "no sources" when the council cited
  // evidence or any source/citation is persisted or a primary-source reference
  // was located.
  const hasAnyEvidence =
    primaryRefCount > 0 ||
    councilClaimCitations > 0 ||
    dbCitationCount > 0 ||
    dbSourceCount > 0;
  const appendixNote = noteText(section.note);
  return (
    <SectionShell title="Source Citation Appendix" testId="report-section-source_citation_appendix">
      <p className="mb-2 text-xs text-slate-500">
        {total} source(s){citations.total != null ? ` · ${citations.total} citation(s)` : ""}
        {primaryRefCount > 0 ? ` · ${primaryRefCount} primary-source reference(s)` : ""}
      </p>
      {hasReconcile ? (
        <dl
          className="mb-3 grid grid-cols-2 gap-x-4 gap-y-1 text-[11px] sm:grid-cols-3"
          data-testid="appendix-reconcile-counts"
        >
          {reconcile.map(([label, val]) => (
            <div key={label} className="flex items-baseline justify-between gap-2">
              <dt className="text-slate-500">{label}</dt>
              <dd className="font-mono text-slate-300">{val ?? 0}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {sources.length === 0 ? (
        hasAnyEvidence ? (
          <p className="text-sm" data-testid="appendix-primary-references">
            <Muted>
              {councilClaimCitations > 0
                ? `${councilClaimCitations} council claim citation(s) link to evidence-pack items` +
                  (dbCitationCount > 0
                    ? ` · ${dbCitationCount} DB-persisted citation(s)`
                    : " · DB citation persistence incomplete") +
                  `. ${primaryRefCount} primary-source reference(s) located (metadata only — not extracted facts). Human review required.`
                : `${primaryRefCount} primary-source reference(s) located — see the Internal Research Memo (Primary Evidence). Metadata-only references are not extracted facts; human review required.`}
            </Muted>
          </p>
        ) : (
          <p className="text-sm">
            <Muted>No sources cited yet — human review required to source claims.</Muted>
          </p>
        )
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
      {appendixNote ? (
        <p className="mt-2 text-[11px] italic text-slate-500">{appendixNote}</p>
      ) : null}
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
// Phase 31 — Internal Research Memo
//
// The memo is an OFF-by-default synthesis of the sections above: a set of nested
// sub-blocks, each holding `{value, provenance}` leaf fields. The generic
// renderer collapses those nested objects to "…", so this bespoke component
// renders each sub-block as a labelled group (reusing the shared FieldRow /
// provenance-chip styling). It surfaces the human-review + disclaimer
// prominently, makes "what is missing" visually distinct, and renders the
// `disallowed_outputs` block as a plain NOTICE — never as a rating/BUY/SELL UI.
// --------------------------------------------------------------------------

// Keys handled specially (header/notice/badges) or intentionally hidden — not
// rendered as generic leaf fields inside a memo sub-block.
const MEMO_SUB_META = new Set([
  "type",
  "note",
  "disclaimer",
  "prominent",
  "human_review_required",
]);

const MEMO_SUBSECTION_ORDER: string[] = [
  "company_identity",
  "why_surfaced",
  "what_is_sourced",
  "what_is_missing",
  "primary_evidence_summary",
  "catalyst_event_evidence",
  "financial_facts_summary",
  "business_risk_summary",
  "council_disagreement_red_team",
  "research_next_steps",
  "human_review_checklist",
  "source_appendix",
];

const MEMO_SUBSECTION_LABELS: Record<string, string> = {
  company_identity: "Company Identity",
  why_surfaced: "Why It Surfaced",
  what_is_sourced: "What Is Sourced",
  what_is_missing: "What Is Missing",
  primary_evidence_summary: "Primary Evidence",
  catalyst_event_evidence: "Catalyst & Event Evidence",
  financial_facts_summary: "Financial Facts",
  business_risk_summary: "Business & Risk",
  council_disagreement_red_team: "Council Disagreement / Red Team",
  research_next_steps: "Research Next Steps",
  human_review_checklist: "Human Review Checklist",
  source_appendix: "Source Appendix",
};

function MemoSubBlock({
  label,
  data,
  prominent = false,
  testId,
}: {
  label: string;
  data: Record<string, unknown>;
  prominent?: boolean;
  testId?: string;
}) {
  const note = noteText(data.note);
  const humanReview = data.human_review_required === true;
  const fields = Object.entries(data).filter(([k]) => !MEMO_SUB_META.has(k));
  return (
    <div
      data-testid={testId}
      className={`rounded-lg border px-3 py-2 ${
        prominent
          ? "border-amber-400/30 bg-amber-500/[0.06]"
          : "border-white/10 bg-white/[0.02]"
      }`}
    >
      <div className="mb-1 flex items-center gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-300">
          {label}
        </h4>
        {humanReview ? <StatusPill label="human review" color="amber" /> : null}
      </div>
      {fields.length === 0 ? (
        note ? null : (
          <p className="text-sm">
            <Muted>No data.</Muted>
          </p>
        )
      ) : (
        <div>
          {fields.map(([k, v]) => (
            <FieldRow key={k} label={humanizeKey(k)} field={v} />
          ))}
        </div>
      )}
      {note ? (
        <p className="mt-2 text-[11px] italic text-slate-500">{note}</p>
      ) : null}
    </div>
  );
}

function DisallowedOutputsNotice({ data }: { data: Record<string, unknown> }) {
  const notice = noteText(data.notice);
  const terms = Array.isArray(data.forbidden_terms)
    ? (data.forbidden_terms as unknown[]).map((t) => String(t))
    : [];
  // Rendered as plain, muted NOTICE text only — never as buttons/pills that
  // could read as a recommendation. This lists what the memo will NOT produce.
  return (
    <div
      data-testid="memo-disallowed-outputs"
      className="rounded-lg border border-white/10 bg-white/[0.02] px-3 py-2"
    >
      <div className="mb-1 flex items-center gap-2">
        <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-300">
          Disallowed Outputs
        </h4>
        <StatusPill label="notice" color="gray" />
      </div>
      {notice ? <p className="text-xs text-slate-400">{notice}</p> : null}
      {terms.length > 0 ? (
        <p className="mt-1 text-[11px] italic text-slate-500">
          Never produced: {terms.join(", ")}.
        </p>
      ) : null}
    </div>
  );
}

// --------------------------------------------------------------------------
// Historical Trends (private-use readiness PR-B)
//
// The generic renderer would flatten a series into an unreadable nested blob.
// A trend is a table: one row per metric/scope, one column per period. Scope
// and unit are shown on every row, because a segment trend read as the Group's
// is exactly the contradiction this section exists to prevent.
// --------------------------------------------------------------------------

type TrendPeriod = {
  period?: string;
  value?: number | null;
  superseded?: boolean;
};

type TrendSeries = {
  metric?: string;
  scope?: string;
  scope_type?: string | null;
  period_type?: string;
  unit?: string | null;
  periods?: TrendPeriod[];
  comparability?: string;
  comparability_reasons?: string[];
  completeness?: string;
  missing_periods?: string[];
  derived_changes?: {
    calculation?: string;
    from_period?: string;
    to_period?: string;
    value?: number;
    unit?: string;
  }[];
};

function TrendRow({ series }: { series: TrendSeries }) {
  const periods = (series.periods ?? []).filter((p) => !p.superseded);
  const comparable = series.comparability === "comparable";
  return (
    <li className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">
          {humanizeKey(series.metric ?? "metric")}
        </span>
        {series.scope ? (
          <span className="rounded bg-white/10 px-1.5 py-0.5 text-[10px] text-slate-300">
            {series.scope}
          </span>
        ) : null}
        {series.unit ? (
          <span className="text-[10px] text-slate-500">{series.unit}</span>
        ) : null}
        {series.period_type && series.period_type !== "annual" ? (
          <span className="rounded bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-200">
            {series.period_type}
          </span>
        ) : null}
        {!comparable ? (
          <span className="rounded bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-200">
            not comparable
          </span>
        ) : null}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-4 gap-y-1">
        {periods.map((p) => (
          <span key={p.period} className="text-sm text-slate-300">
            <span className="text-[10px] text-slate-500">{p.period}</span>{" "}
            {p.value != null ? p.value.toLocaleString() : "n/a"}
          </span>
        ))}
      </div>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
        {(series.derived_changes ?? []).map((c, i) => (
          <span key={i}>
            {humanizeKey(c.calculation ?? "change")} {c.from_period}&rarr;{c.to_period}:{" "}
            {c.value != null ? c.value.toLocaleString() : "n/a"}
            {c.unit}
          </span>
        ))}
        {(series.missing_periods ?? []).length > 0 ? (
          <span className="text-amber-300">
            missing: {(series.missing_periods ?? []).join(", ")}
          </span>
        ) : null}
        {!comparable && (series.comparability_reasons ?? []).length > 0 ? (
          <span className="text-amber-300">
            {(series.comparability_reasons ?? []).join(", ")}
          </span>
        ) : null}
      </div>
    </li>
  );
}

function HistoricalTrendsSection({ section }: { section: Record<string, unknown> }) {
  const rows = ((section.series as { value?: TrendSeries[] } | undefined)?.value ??
    []) as TrendSeries[];
  const note = noteText(section.note);
  return (
    <SectionShell title="Historical Trends" testId="section-historical-trends">
      {rows.length === 0 ? (
        <p className="text-sm text-slate-400">
          {note ??
            "No multi-period financial series was reconstructed for this company."}
        </p>
      ) : (
        <>
          <ul className="flex flex-col gap-2">
            {rows.map((s, i) => (
              <TrendRow key={`${s.metric}-${s.scope}-${i}`} series={s} />
            ))}
          </ul>
          {note ? <p className="mt-3 text-[11px] text-slate-500">{note}</p> : null}
        </>
      )}
    </SectionShell>
  );
}

// --------------------------------------------------------------------------
// Regulated Disclosures (private-use readiness PR-E)
//
// Each row is what an official venue published. The provenance list matters:
// an announcement carried by BOTH the issuer and the exchange is better
// sourced than one carried by a single channel, and merging them without
// showing that would throw the signal away.
// --------------------------------------------------------------------------

type DisclosureRow = {
  title?: string | null;
  date?: string | null;
  venue?: string | null;
  url?: string | null;
  language?: string | null;
  requires_translation?: boolean;
  provenance?: string[];
};

function RegulatedDisclosuresSection({ section }: { section: Record<string, unknown> }) {
  const rows = ((section.events as { value?: DisclosureRow[] } | undefined)?.value ??
    []) as DisclosureRow[];
  const note = noteText(section.note);
  return (
    <SectionShell title="Regulated Disclosures" testId="section-regulated-disclosures">
      {rows.length === 0 ? (
        <p className="text-sm text-slate-400">
          {note ?? "No regulated disclosure was retrieved for this issuer."}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {rows.map((e, i) => (
            <li
              key={`${e.date}-${i}`}
              className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2"
            >
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-[10px] text-slate-500">{e.date ?? "date not stated"}</span>
                {e.url ? (
                  <a
                    href={e.url}
                    target="_blank"
                    rel="noreferrer noopener"
                    className="text-sm font-semibold text-sky-200 underline decoration-dotted"
                  >
                    {e.title ?? "Untitled disclosure"}
                  </a>
                ) : (
                  <span className="text-sm font-semibold text-slate-100">
                    {e.title ?? "Untitled disclosure"}
                  </span>
                )}
                {e.requires_translation ? (
                  <span className="rounded bg-amber-400/10 px-1.5 py-0.5 text-[10px] text-amber-200">
                    {e.language ?? "local language"}
                  </span>
                ) : null}
              </div>
              <div className="mt-1 flex flex-wrap gap-x-3 text-[11px] text-slate-500">
                {e.venue ? <span>{e.venue}</span> : null}
                {(e.provenance ?? []).length > 1 ? (
                  <span className="text-emerald-300">
                    confirmed by {(e.provenance ?? []).length} channels
                  </span>
                ) : null}
              </div>
            </li>
          ))}
        </ul>
      )}
      <p className="mt-3 text-[11px] text-slate-500">
        {noteText(section.disclaimer)}
      </p>
    </SectionShell>
  );
}

function ResearchMemoSection({ section }: { section: Record<string, unknown> }) {
  const header = noteText(section.header);
  const memoNote = noteText(section.note);
  const disclaimer = noteText(section.disclaimer);
  const disallowed = section.disallowed_outputs;
  return (
    <SectionShell title="Internal Research Memo" testId="report-section-research_memo">
      <div className="mb-3 flex flex-wrap gap-2">
        <StatusPill label="Internal Research Aid" color="purple" />
        <StatusPill label="Not Investment Advice" color="red" />
        {section.human_review_required === true ? (
          <StatusPill label="Human Review Required" color="amber" />
        ) : null}
      </div>
      {header ? (
        <p className="mb-3 rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-xs text-slate-400">
          {header}
        </p>
      ) : null}
      <div className="space-y-2">
        {MEMO_SUBSECTION_ORDER.map((key) => {
          const sub = section[key];
          if (sub == null || typeof sub !== "object" || Array.isArray(sub)) return null;
          return (
            <MemoSubBlock
              key={key}
              label={MEMO_SUBSECTION_LABELS[key] ?? humanizeKey(key)}
              data={sub as Record<string, unknown>}
              prominent={key === "what_is_missing"}
              testId={`memo-subsection-${key}`}
            />
          );
        })}
        {disallowed && typeof disallowed === "object" && !Array.isArray(disallowed) ? (
          <DisallowedOutputsNotice data={disallowed as Record<string, unknown>} />
        ) : null}
      </div>
      {memoNote ? (
        <p className="mt-3 text-[11px] italic text-slate-500">{memoNote}</p>
      ) : null}
      {disclaimer ? (
        <p className="mt-2 text-[11px] font-medium italic text-amber-300/80">
          {disclaimer}
        </p>
      ) : null}
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
  // Phase C2: the backend decides thin-ness once, from the reconciled evidence
  // inventory. The UI only picks which section order to walk.
  const isThin = isThinEvidenceReport(content as Record<string, unknown>);
  const sectionOrder = sectionOrderFor(content as Record<string, unknown>);
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

      {/* Phase C2: an evidence-thin company renders the SHORT form. The
          judgement is the backend's canonical ThinEvidenceAssessment; the UI
          only chooses which section order to walk. */}
      {isThin && (
        <p
          data-testid="thin-evidence-notice"
          className="rounded-lg border border-amber-400/25 bg-amber-500/[0.09] px-3 py-2 text-xs text-amber-200"
        >
          Evidence is currently insufficient for a full company analysis. The
          sections below show what is known and what is missing; analysis
          sections are omitted rather than shown empty.
        </p>
      )}

      {sectionOrder.map((key) => {
        const section = content[key];
        if (section == null) return null;
        if (key === "executive_summary")
          return <ExecutiveSummary key={key} section={section as Record<string, unknown>} />;
        if (key === "research_memo")
          return <ResearchMemoSection key={key} section={section as Record<string, unknown>} />;
        if (key === "human_review_checklist")
          return <ChecklistSection key={key} section={section as Record<string, unknown>} />;
        if (key === "source_citation_appendix")
          return <AppendixSection key={key} section={section as Record<string, unknown>} />;
        if (key === "regulated_disclosures")
          return (
            <RegulatedDisclosuresSection
              key={key}
              section={section as Record<string, unknown>}
            />
          );
        if (key === "historical_trends")
          return <HistoricalTrendsSection key={key} section={section as Record<string, unknown>} />;
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
