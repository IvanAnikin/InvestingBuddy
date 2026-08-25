// Phase 28A.2 — helpers for the readable final-report renderer.
//
// A final-report-generator draft stores its structured `report_content` as a
// single fenced ```json block inside `content_markdown` (see
// `_save_final_report_draft` in the backend). These helpers extract and shape
// that data for a human-readable view — no raw JSON dump in the product UI.
//
// Everything here is honest: values are unwrapped from their `{value, …}`
// envelopes but never fabricated, and provenance (sourced_fact /
// model_interpretation / missing_data) is preserved so the reader can tell
// real data from model interpretation. No recommendation/valuation logic.

export type ReportContent = Record<string, unknown>;

/**
 * Extract the structured report_content object from a final report's
 * `content_markdown`. Returns null when absent or unparseable (the caller then
 * falls back to the markdown preview).
 */
export function extractFinalReportContent(
  markdown: string | null | undefined,
): ReportContent | null {
  if (!markdown) return null;
  const start = markdown.indexOf("```json");
  const end = markdown.lastIndexOf("```");
  if (start === -1 || end <= start) return null;
  const jsonStr = markdown.slice(start + "```json".length, end).trim();
  try {
    const obj = JSON.parse(jsonStr);
    return obj && typeof obj === "object" && !Array.isArray(obj)
      ? (obj as ReportContent)
      : null;
  } catch {
    return null;
  }
}

export interface Unwrapped {
  value: unknown;
  provenance?: string;
  source?: string;
  currency?: string;
  asOf?: string;
  total?: number;
}

/** Unwrap a `{value, provenance, source, …}` envelope, or pass a bare value. */
export function unwrap(field: unknown): Unwrapped {
  if (field && typeof field === "object" && !Array.isArray(field) && "value" in field) {
    const f = field as Record<string, unknown>;
    return {
      value: f.value,
      provenance: typeof f.provenance === "string" ? f.provenance : undefined,
      source: typeof f.source === "string" ? f.source : undefined,
      currency: typeof f.currency === "string" ? f.currency : undefined,
      asOf: typeof f.as_of === "string" ? f.as_of : undefined,
      total: typeof f.total === "number" ? f.total : undefined,
    };
  }
  return { value: field };
}

/**
 * Coerce a section `note` / `disclaimer` to a display string. These are
 * sometimes bare strings and sometimes `{value, provenance}` envelopes — the
 * latter must be unwrapped, otherwise `String(obj)` renders "[object Object]"
 * (the "Discovery Rationale: Not available. [object Object]" bug). Returns null
 * when there is nothing safe to show (e.g. a nested object value).
 */
export function noteText(field: unknown): string | null {
  if (field === null || field === undefined) return null;
  if (typeof field === "string") return field.trim() || null;
  if (typeof field === "number" || typeof field === "boolean") return String(field);
  if (typeof field === "object" && !Array.isArray(field)) {
    const u = unwrap(field);
    if (typeof u.value === "string") return u.value.trim() || null;
    if (u.value === null || u.value === undefined) return null;
    if (typeof u.value === "object") return null; // never stringify an object
    return String(u.value);
  }
  return null;
}

export function isEmptyValue(v: unknown): boolean {
  return (
    v === null ||
    v === undefined ||
    v === "" ||
    (Array.isArray(v) && v.length === 0)
  );
}

/** snake_case / dotted key -> "Title Case" label. */
export function humanizeKey(key: string): string {
  return key
    .replace(/[._]+/g, " ")
    .replace(/\b\w/g, (c) => c.toUpperCase())
    .replace(/\bTtm\b/g, "TTM")
    .replace(/\bLei\b/g, "LEI")
    .replace(/\bIsin\b/g, "ISIN")
    .replace(/\bLlm\b/g, "LLM")
    .replace(/\bUsd\b/g, "USD");
}

// Sections rendered, in product order. `committee_chair_summary` is folded in
// after the executive summary; `admin_disclaimer` and `workflow_status` are
// intentionally omitted (shown as the page disclaimer / metadata card).
export const SECTION_ORDER: string[] = [
  "executive_summary",
  "committee_chair_summary",
  // Phase 31 — the internal research memo is a prominent synthesis block placed
  // near the top (it condenses the sections below). Present only when the
  // OFF-by-default backend flag is enabled; legacy reports omit the key entirely.
  "research_memo",
  "company_identity",
  "data_availability_summary",
  // Phase C2 — the ONE canonical evidence-quality answer (four dimensions,
  // each with its basis). Placed high because "how good is this evidence" is
  // the second question a researcher asks, right after "what is this".
  "evidence_quality",
  // Product readiness — the explicit evidence-CHANNEL inventory. "Issuer
  // primary document", "regulator structured facts", "regulator filing
  // events", "issuer newsroom" and "persisted citations" are five different
  // things; the report used to conflate them and claim "primary filings
  // required" for a company whose SEC XBRL statements were fully sourced.
  "evidence_channels",
  "financial_snapshot",
  // Private-use readiness PR-B — the multi-period series reconstructed from
  // the issuer's own primary documents. Sits directly under the snapshot: "what
  // is it now" then "what changed over time" is the order a researcher reads in.
  "historical_trends",
  "discovery_rationale",
  "internal_scorecard",
  "llm_council_analysis",
  "bull_case",
  "bear_case",
  "risk_analysis",
  "valuation_readiness",
  "source_quality_review",
  "citation_validation_review",
  "research_completeness_review",
  "missing_information",
  "news_catalyst_discovery",
  "human_review_checklist",
  "source_citation_appendix",
];

export const SECTION_LABELS: Record<string, string> = {
  executive_summary: "Executive Summary",
  committee_chair_summary: "Committee Summary",
  research_memo: "Internal Research Memo",
  company_identity: "Company Identity",
  data_availability_summary: "Data Availability Summary",
  evidence_quality: "Evidence Quality",
  thin_evidence_state: "Evidence Status",
  evidence_channels: "Evidence Channels",
  financial_snapshot: "Financial Snapshot",
  historical_trends: "Historical Trends",
  discovery_rationale: "Discovery Rationale",
  internal_scorecard: "Internal Scorecard",
  llm_council_analysis: "LLM Council Analysis",
  bull_case: "Bull Case",
  bear_case: "Bear Case",
  risk_analysis: "Risk Analysis",
  valuation_readiness: "Valuation Readiness",
  source_quality_review: "Source Quality Review",
  citation_validation_review: "Citation Validation",
  research_completeness_review: "Research Completeness",
  missing_information: "Missing Information",
  news_catalyst_discovery: "News & Catalyst Discovery",
  human_review_checklist: "Human Review Checklist",
  source_citation_appendix: "Source Citation Appendix",
};

// Keys that are section-level metadata — not rendered as fields by the generic
// renderer (they are surfaced separately or intentionally hidden).
export const META_KEYS = new Set([
  "type",
  "available",
  "is_mock",
  "human_review_required",
  "retrieved_at",
  "source_tier",
  "source",
  "provenance",
]);

export interface CitationSource {
  source_type?: string;
  source_tier?: string;
  title?: string;
  url?: string;
  source_quote?: string;
}

export interface ChecklistItem {
  item?: string;
  label?: string;
  required?: boolean;
  completed?: boolean;
  note?: string | null;
}


/**
 * Phase C2 — the SHORT-FORM section order for an evidence-thin company.
 *
 * A metadata-only issuer previously rendered the full skeleton: twenty
 * sections of "Not sourced", plus Bull/Bear/Risk blocks reasoning about
 * evidence that does not exist. Failing closed is correct; looking broken
 * while doing it is not. This order shows what IS known, states plainly what
 * is missing (grouped, once), and stops — the analysis sections are omitted
 * rather than rendered empty.
 */
export const THIN_SECTION_ORDER: string[] = [
  "thin_evidence_state",
  "company_identity",
  "discovery_rationale",
  "evidence_quality",
  "financial_snapshot",
  "committee_chair_summary",
  "human_review_checklist",
  "source_citation_appendix",
];

/**
 * True when the backend's canonical ThinEvidenceAssessment marked this report
 * evidence-thin. The judgement is made ONCE server-side from the reconciled
 * evidence inventory; the UI never re-derives it.
 */
export function isThinEvidenceReport(
  content: Record<string, unknown> | null | undefined,
): boolean {
  if (!content) return false;
  const state = content["thin_evidence_state"];
  if (!state || typeof state !== "object") return false;
  return (state as Record<string, unknown>)["is_thin"] === true;
}

/** The section order this report should render, full or short-form. */
export function sectionOrderFor(
  content: Record<string, unknown> | null | undefined,
): string[] {
  return isThinEvidenceReport(content) ? THIN_SECTION_ORDER : SECTION_ORDER;
}
