// Phase 32A Slice 5B.3 — admin-only "Primary Documents" panel.
//
// Renders the bounded, read-only primary-document/OCR ingestion provenance
// view (GET /api/v1/reports/{id}/primary-documents): what was discovered,
// attempted, extracted (natively or via OCR), what facts were validated, and
// honest gap/failure states for the report's generating run. Diagnostic /
// provenance view only — never a recommendation, rating, or price target.
// All text (excerpts, titles, urls) is plain React children — never
// dangerouslySetInnerHTML.

import GlassCard from "@/components/ui/GlassCard";
import StatusPill, { type PillColor } from "@/components/ui/StatusPill";
import type {
  PrimaryDocument,
  PrimaryDocumentExcerpt,
  PrimaryDocumentFact,
  PrimaryDocumentIngestionSummary,
  ReportPrimaryDocumentsResponse,
} from "@/types/api";

// --------------------------------------------------------------------------
// Vocabulary → human-readable label/color maps. Every value here comes from
// the backend's CLOSED vocabularies (ingestion_status.py) — free text (a
// provider exception, a URL, a hostname) never reaches this component.
// --------------------------------------------------------------------------

const NON_TERMINAL_STATUS_LABELS: Record<string, string> = {
  discovered: "Discovered",
  fetched: "Fetched",
};

const FAILURE_STATUS_LABELS: Record<string, string> = {
  unsupported: "Unsupported format",
  encrypted: "Encrypted",
  password_protected: "Password-protected",
  malformed: "Malformed document",
  rejected_security: "Rejected (security)",
  timeout: "Timed out",
  extraction_failed: "Extraction failed",
};

function humanizeSnakeCase(value: string): string {
  return value.replace(/_/g, " ");
}

function statusBadge(doc: PrimaryDocument): { label: string; color: PillColor } {
  if (doc.status === "extracted") {
    if (doc.extraction_method === "ocr") {
      return { label: "OCR extraction", color: "purple" };
    }
    if (doc.extraction_method === "native_pdf" || doc.extraction_method === "html") {
      return { label: "Native extraction", color: "green" };
    }
    return { label: "Extracted", color: "green" };
  }
  if (doc.status === "metadata_only") {
    return { label: "Metadata only", color: "amber" };
  }
  if (doc.status in FAILURE_STATUS_LABELS) {
    return { label: FAILURE_STATUS_LABELS[doc.status], color: "red" };
  }
  if (doc.status in NON_TERMINAL_STATUS_LABELS) {
    return { label: NON_TERMINAL_STATUS_LABELS[doc.status], color: "gray" };
  }
  // Defensive fallback for any status value not explicitly mapped above —
  // never crashes, always shows something honest.
  return { label: humanizeSnakeCase(doc.status), color: "gray" };
}

function validationBadge(status: string): { label: string; color: PillColor } {
  if (status === "validated") return { label: "Validated", color: "green" };
  if (status === "excerpt_only") return { label: "Excerpt only", color: "amber" };
  if (status === "rejected") return { label: "Rejected", color: "red" };
  return { label: humanizeSnakeCase(status), color: "gray" };
}

function formatConfidence(confidence: number | null | undefined): string | null {
  if (confidence === null || confidence === undefined) return null;
  return `${Math.round(confidence * 100)}%`;
}

function formatMs(ms: number | null | undefined): string | null {
  if (ms === null || ms === undefined) return null;
  return `${ms}ms`;
}

// --------------------------------------------------------------------------
// Summary row
// --------------------------------------------------------------------------

function SummaryTile({ label, value }: { label: string; value: number }) {
  return (
    <div className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2 text-center">
      <p className="text-lg font-semibold text-slate-100">{value}</p>
      <p className="text-[11px] uppercase tracking-wide text-slate-500">{label}</p>
    </div>
  );
}

function SummaryRow({ summary }: { summary: PrimaryDocumentIngestionSummary }) {
  return (
    <div className="grid grid-cols-3 gap-2 sm:grid-cols-5 lg:grid-cols-9">
      <SummaryTile label="Discovered" value={summary.discovered_count} />
      <SummaryTile label="Attempted" value={summary.attempted_count} />
      <SummaryTile label="Extracted" value={summary.extracted_count} />
      <SummaryTile label="Native" value={summary.native_count} />
      <SummaryTile label="OCR" value={summary.ocr_count} />
      <SummaryTile label="Metadata only" value={summary.metadata_only_count} />
      <SummaryTile label="Failed" value={summary.failed_count} />
      <SummaryTile label="Validated facts" value={summary.validated_fact_count} />
      <SummaryTile label="Reused" value={summary.reused_count} />
    </div>
  );
}

// --------------------------------------------------------------------------
// Excerpt / fact rows (inside the expandable detail section)
// --------------------------------------------------------------------------

function ExcerptRow({ excerpt }: { excerpt: PrimaryDocumentExcerpt }) {
  const confidence = formatConfidence(excerpt.confidence);
  return (
    <li className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
      <p className="whitespace-pre-wrap text-xs text-slate-300">{excerpt.text}</p>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
        {excerpt.page_number != null && <span>Page {excerpt.page_number}</span>}
        {excerpt.section && <span>Section: {excerpt.section}</span>}
        {excerpt.heading && <span>Heading: {excerpt.heading}</span>}
        {excerpt.table_location && <span>Table: {excerpt.table_location}</span>}
        {excerpt.extraction_method && <span>Method: {excerpt.extraction_method}</span>}
        {confidence && <span>Confidence: {confidence}</span>}
      </div>
    </li>
  );
}

function FactRow({ fact }: { fact: PrimaryDocumentFact }) {
  const badge = validationBadge(fact.validation_status);
  const confidence = formatConfidence(fact.confidence);
  const value =
    fact.value_numeric !== null && fact.value_numeric !== undefined
      ? fact.value_numeric.toLocaleString()
      : (fact.value_text ?? "n/a");
  return (
    <li className="rounded-lg border border-white/10 bg-white/[0.03] px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="text-sm font-semibold text-slate-100">{fact.label}</span>
        <StatusPill label={badge.label} color={badge.color} />
        {fact.needs_human_review && (
          <StatusPill label="Needs human review" color="amber" />
        )}
      </div>
      <p className="mt-1 text-sm text-slate-300">
        {value}
        {fact.unit && <span className="text-slate-500"> {fact.unit}</span>}
        {fact.currency && <span className="text-slate-500"> {fact.currency}</span>}
        {fact.period && <span className="text-slate-500"> · {fact.period}</span>}
      </p>
      <div className="mt-1 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
        {fact.page_number != null && <span>Page {fact.page_number}</span>}
        {fact.table_location && <span>Table: {fact.table_location}</span>}
        <span>Method: {fact.extraction_method}</span>
        {confidence && <span>Confidence: {confidence}</span>}
      </div>
    </li>
  );
}

// --------------------------------------------------------------------------
// Per-document card
// --------------------------------------------------------------------------

function DocumentCard({ doc }: { doc: PrimaryDocument }) {
  const badge = statusBadge(doc);
  const timingParts = [
    doc.fetch_ms != null ? `Fetch ${formatMs(doc.fetch_ms)}` : null,
    doc.extraction_ms != null ? `Extract ${formatMs(doc.extraction_ms)}` : null,
    doc.total_ms != null ? `Total ${formatMs(doc.total_ms)}` : null,
  ].filter((p): p is string => Boolean(p));
  const hasDetail = doc.excerpts.length > 0 || doc.facts.length > 0;

  return (
    <GlassCard className="space-y-2 p-4" testId={`primary-document-${doc.attempt_id}`}>
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="truncate text-sm font-semibold text-slate-100">
            {doc.title || doc.canonical_url}
          </p>
          <p className="mt-0.5 text-[11px] text-slate-500">
            {doc.source_tier}
            {doc.doc_kind ? ` · ${doc.doc_kind}` : ""}
            {doc.discovery_strategy ? ` · ${doc.discovery_strategy}` : ""}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1.5">
          <StatusPill label={badge.label} color={badge.color} />
          {doc.reused && (
            <StatusPill label="Reused from prior extraction" color="blue" />
          )}
        </div>
      </div>

      {doc.failure_code && (
        <p className="text-[11px] text-rose-300/80">
          Failure code: {humanizeSnakeCase(doc.failure_code)}
        </p>
      )}

      <div className="flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-slate-500">
        {doc.page_count != null && <span>{doc.page_count} page(s)</span>}
        {timingParts.length > 0 && <span>{timingParts.join(" · ")}</span>}
        {doc.mime_type && <span>{doc.mime_type}</span>}
        <span>{new Date(doc.attempted_at).toLocaleString()}</span>
      </div>

      <a
        href={doc.canonical_url}
        target="_blank"
        rel="noopener noreferrer"
        className="block truncate text-[11px] text-sky-400 hover:text-sky-300 hover:underline"
      >
        {doc.canonical_url}
      </a>

      {hasDetail && (
        <details className="group text-xs">
          <summary className="cursor-pointer select-none text-slate-400 hover:text-slate-200">
            {doc.excerpts.length} excerpt(s), {doc.facts.length} fact(s) — show
            detail
          </summary>
          <div className="mt-2 space-y-3">
            {doc.excerpts.length > 0 && (
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  Excerpts
                </p>
                <ul className="space-y-1.5">
                  {doc.excerpts.map((excerpt, i) => (
                    <ExcerptRow key={i} excerpt={excerpt} />
                  ))}
                </ul>
              </div>
            )}
            {doc.facts.length > 0 && (
              <div>
                <p className="mb-1 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  Facts
                </p>
                <ul className="space-y-1.5">
                  {doc.facts.map((fact) => (
                    <FactRow key={fact.id} fact={fact} />
                  ))}
                </ul>
              </div>
            )}
          </div>
        </details>
      )}
    </GlassCard>
  );
}

// --------------------------------------------------------------------------
// Panel
// --------------------------------------------------------------------------

export default function PrimaryDocumentsPanel({
  data,
}: {
  data: ReportPrimaryDocumentsResponse;
}) {
  return (
    <GlassCard testId="primary-documents-panel" className="space-y-4 p-5">
      <div className="flex flex-wrap items-center gap-2">
        <p className="text-xs font-semibold uppercase tracking-wide text-slate-500">
          Primary Documents
        </p>
        <StatusPill label="Provenance / Diagnostic View" color="purple" />
        <StatusPill label="Not Investment Advice" color="red" />
      </div>
      <p className="text-xs text-slate-400">
        What the primary-document ingestion pipeline discovered, attempted and
        extracted (natively or via OCR) for this report&apos;s generating run.
        Facts are unverified until reviewed; this is a diagnostic provenance
        view, not a recommendation.
      </p>

      <SummaryRow summary={data.summary} />

      {data.documents.length === 0 ? (
        <p className="text-sm italic text-slate-500">
          No primary-document ingestion activity for this report.
        </p>
      ) : (
        <div className="space-y-3">
          {data.documents.map((doc) => (
            <DocumentCard key={doc.attempt_id} doc={doc} />
          ))}
        </div>
      )}
    </GlassCard>
  );
}
