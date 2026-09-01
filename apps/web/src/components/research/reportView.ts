// Derivation layer for the user-facing research report view.
//
// This module reads the SAME structured `report_content` the admin renderer
// reads (`components/reports/finalReportContent.ts`) and reshapes it for a
// reader rather than an operator. It derives nothing: every value here is one
// the backend already assembled, and every absence stays an absence.
//
// What it deliberately does NOT do:
//   - reconcile figures the backend reported as conflicting;
//   - fill a missing slot from a neighbouring one;
//   - average the evidence-quality dimensions into a single flattering word
//     (the backend's `overall_research_evidence_quality` already reports the
//     WEAKEST dimension, and that is the value shown);
//   - hide the annual/interim distinction, which is the single most dangerous
//     thing a financial UI can flatten.

import type {
  LlmCouncilAgent,
  LlmCouncilMetadata,
  Report,
} from "@/types/api";
import {
  extractFinalReportContent,
  isThinEvidenceReport,
  noteText,
  unwrap,
  type ReportContent,
} from "@/components/reports/finalReportContent";
import { formatNumber } from "@/lib/format";
import type { EvidenceLabel } from "./ResearchStatusBadge";
import { evidenceLabelOf } from "./ResearchStatusBadge";
import type { ResearchArtefactKind } from "./reportResolution";
import { partitionRecordGaps } from "./recordGaps";

// ---------------------------------------------------------------------------
// Small readers
// ---------------------------------------------------------------------------

export function asRecord(value: unknown): Record<string, unknown> | null {
  return value && typeof value === "object" && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function str(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value);
  }
  return null;
}

/** Unwrap a `{value, …}` envelope (or a bare value) to a display string. */
export function fieldText(value: unknown): string | null {
  const u = unwrap(value);
  return str(u.value);
}

/** Read a `{value: string[]}` envelope (or a bare array) as a string list. */
export function stringList(value: unknown): string[] {
  const u = unwrap(value);
  const raw = Array.isArray(u.value) ? u.value : [];
  return raw
    .map((item) => (typeof item === "string" ? item.trim() : null))
    .filter((item): item is string => Boolean(item));
}

// ---------------------------------------------------------------------------
// Financial datapoints
// ---------------------------------------------------------------------------

/** The extractor's own statement vocabulary, in reading order. */
export const FINANCIAL_FIELDS: { key: string; label: string }[] = [
  { key: "revenue", label: "Revenue" },
  { key: "operating_profit", label: "Operating profit" },
  { key: "recurring_operating_profit", label: "Recurring operating profit" },
  { key: "operating_margin", label: "Operating margin" },
  { key: "recurring_operating_margin", label: "Recurring operating margin" },
  { key: "net_income", label: "Net income" },
  { key: "operating_cash_flow", label: "Operating cash flow" },
  { key: "free_cash_flow", label: "Free cash flow" },
  { key: "operating_free_cash_flow", label: "Operating free cash flow" },
  { key: "total_assets", label: "Total assets" },
  { key: "total_equity", label: "Total equity" },
  { key: "cash_and_equivalents", label: "Cash & equivalents" },
  { key: "total_debt", label: "Total debt" },
  { key: "net_debt", label: "Net debt" },
  { key: "net_cash", label: "Net cash" },
];

/** Regulator/aggregator statement slots, present on SEC-registered issuers. */
export const STATEMENT_FIELDS: { key: string; label: string }[] = [
  { key: "revenue_usd_m", label: "Revenue" },
  { key: "gross_profit_usd_m", label: "Gross profit" },
  { key: "operating_income_usd_m", label: "Operating income" },
  { key: "net_income_usd_m", label: "Net income" },
  { key: "operating_cash_flow_usd_m", label: "Operating cash flow" },
  { key: "capital_expenditures_usd_m", label: "Capital expenditure" },
  { key: "free_cash_flow_usd_m", label: "Free cash flow" },
  { key: "total_assets_usd_m", label: "Total assets" },
  { key: "total_liabilities_usd_m", label: "Total liabilities" },
  { key: "shareholders_equity_usd_m", label: "Shareholders' equity" },
  { key: "cash_and_equivalents_usd_m", label: "Cash & equivalents" },
  { key: "total_debt_usd_m", label: "Total debt" },
  { key: "eps_diluted", label: "Diluted EPS" },
];

export interface FinancialDatapoint {
  key: string;
  label: string;
  /** Ready-to-render figure, or null when the slot holds no value. */
  display: string | null;
  /**
   * The extractor's own number, unconverted, or null when the slot holds only
   * text. Carried so the report can compare a figure with another period of
   * the SAME metric, and so council prose can be reconciled against it —
   * never so this layer can rescale or restate it.
   */
  numericValue: number | null;
  /** The scale word the number was extracted under ("million", "billion"). */
  scale: string | null;
  unit: string | null;
  currency: string | null;
  period: string | null;
  scope: string | null;
  sourceUrl: string | null;
  sourceTier: string | null;
  /** The backend's own disclosure that a newer period exists below its bar. */
  newerPeriod: string | null;
  confidence: string | null;
}

const SCALE_WORD: Record<string, string> = {
  thousand: "thousand",
  million: "m",
  billion: "bn",
};

/**
 * Format a datapoint for display WITHOUT converting it.
 *
 * Rescaling here would mean this UI, not the extractor, deciding what "32,516"
 * means. The number, its scale word and its currency are shown exactly as they
 * were extracted.
 */
function formatDatapoint(dp: Record<string, unknown>): string | null {
  const numeric = dp["numeric_value"];
  const currency = str(dp["currency"]);
  const scale = str(dp["scale"]);
  const unit = str(dp["unit"]);

  if (typeof numeric === "number" && Number.isFinite(numeric)) {
    const parts = [formatNumber(numeric)];
    if (unit === "%") return `${parts.join(" ")}%`;
    if (scale && SCALE_WORD[scale]) parts.push(SCALE_WORD[scale]);
    if (currency) parts.push(currency);
    else if (unit && unit !== "%") parts.push(unit);
    return parts.join(" ");
  }

  const raw = dp["value"];
  if (typeof raw === "number" && Number.isFinite(raw)) {
    const parts = [formatNumber(raw)];
    if (currency) parts.push(currency);
    else if (unit) parts.push(unit);
    return parts.join(" ");
  }
  return str(raw);
}

function toDatapoint(
  key: string,
  label: string,
  raw: unknown,
): FinancialDatapoint | null {
  const dp = asRecord(raw);
  if (!dp) return null;
  const display = formatDatapoint(dp);
  if (display === null) return null;
  const newer = asRecord(dp["newer_period_available"]);
  const numeric = dp["numeric_value"];
  const bare = dp["value"];
  return {
    key,
    label,
    display,
    numericValue:
      typeof numeric === "number" && Number.isFinite(numeric)
        ? numeric
        : typeof bare === "number" && Number.isFinite(bare)
          ? bare
          : null,
    scale: str(dp["scale"]),
    unit: str(dp["unit"]),
    currency: str(dp["currency"]),
    period: str(dp["period"]),
    scope: str(dp["scope"]),
    sourceUrl: str(dp["source_url"]),
    sourceTier: str(dp["source_tier"]),
    newerPeriod: newer ? str(newer["period"]) : null,
    confidence: str(dp["confidence"]),
  };
}

export interface ReportingPeriods {
  latestAnnual: string | null;
  latestInterim: string | null;
  latestQuarter: string | null;
  latestCurrent: string | null;
  note: string | null;
}

export interface FinancialSnapshotView {
  present: boolean;
  periods: ReportingPeriods | null;
  annual: FinancialDatapoint[];
  currentPeriod: FinancialDatapoint[];
  /** Regulator/aggregator statement slots (SEC XBRL and similar). */
  statements: FinancialDatapoint[];
  statementsNote: string | null;
  currentPeriodNote: string | null;
  latestClose: FinancialDatapoint | null;
  fallbackNote: string | null;
}

const EMPTY_SNAPSHOT: FinancialSnapshotView = {
  present: false,
  periods: null,
  annual: [],
  currentPeriod: [],
  statements: [],
  statementsNote: null,
  currentPeriodNote: null,
  latestClose: null,
  fallbackNote: null,
};

function buildFinancialSnapshot(
  content: ReportContent | null,
): FinancialSnapshotView {
  const section = asRecord(content?.["financial_snapshot"]);
  if (!section) return EMPTY_SNAPSHOT;

  const annual: FinancialDatapoint[] = [];
  const currentPeriod: FinancialDatapoint[] = [];
  for (const { key, label } of FINANCIAL_FIELDS) {
    const a = toDatapoint(key, label, section[`${key}_primary_filing`]);
    if (a) annual.push(a);
    const c = toDatapoint(key, label, section[`${key}_current_period`]);
    if (c) currentPeriod.push(c);
  }

  const statements: FinancialDatapoint[] = [];
  for (const { key, label } of STATEMENT_FIELDS) {
    const dp = toDatapoint(key, label, section[key]);
    if (dp) statements.push(dp);
  }

  const rp = asRecord(section["reporting_periods"]);
  const periods: ReportingPeriods | null = rp
    ? {
        latestAnnual: str(rp["latest_annual"]),
        latestInterim: str(rp["latest_interim"]),
        latestQuarter: str(rp["latest_quarter"]),
        latestCurrent: str(rp["latest_current_period"]),
        note: noteText(rp["note"]),
      }
    : null;

  return {
    present: true,
    periods,
    annual,
    currentPeriod,
    statements,
    statementsNote: noteText(section["fundamentals_note"]),
    currentPeriodNote: noteText(section["current_period_note"]),
    latestClose: toDatapoint(
      "latest_close",
      "Latest close",
      section["latest_close"],
    ),
    fallbackNote: noteText(section["note"]),
  };
}

// ---------------------------------------------------------------------------
// Historical trends
// ---------------------------------------------------------------------------

export interface TrendPoint {
  period: string;
  value: number | null;
}

export interface TrendSeriesView {
  metric: string;
  scope: string | null;
  periodType: string | null;
  unit: string | null;
  comparable: boolean;
  comparabilityReasons: string[];
  missingPeriods: string[];
  points: TrendPoint[];
}

function buildTrends(content: ReportContent | null): {
  series: TrendSeriesView[];
  note: string | null;
} {
  const section = asRecord(content?.["historical_trends"]);
  if (!section) return { series: [], note: null };

  const raw = unwrap(section["series"]).value;
  const rows = Array.isArray(raw) ? raw : [];
  const series: TrendSeriesView[] = [];

  for (const entry of rows) {
    const s = asRecord(entry);
    if (!s) continue;
    const periods = Array.isArray(s["periods"]) ? s["periods"] : [];
    const points: TrendPoint[] = [];
    for (const p of periods) {
      const point = asRecord(p);
      // A superseded period was replaced by a later restatement; charting it
      // beside its replacement would draw a movement that never happened.
      if (!point || point["superseded"] === true) continue;
      const period = str(point["period"]);
      if (!period) continue;
      const v = point["value"];
      points.push({
        period,
        value: typeof v === "number" && Number.isFinite(v) ? v : null,
      });
    }
    if (points.length === 0) continue;

    series.push({
      metric: str(s["metric"]) ?? "metric",
      scope: str(s["scope"]),
      periodType: str(s["period_type"]),
      unit: str(s["unit"]),
      comparable: s["comparability"] === "comparable",
      comparabilityReasons: (Array.isArray(s["comparability_reasons"])
        ? s["comparability_reasons"]
        : []
      )
        .map((r) => str(r))
        .filter((r): r is string => Boolean(r)),
      missingPeriods: (Array.isArray(s["missing_periods"])
        ? s["missing_periods"]
        : []
      )
        .map((r) => str(r))
        .filter((r): r is string => Boolean(r)),
      points,
    });
  }

  return { series, note: noteText(section["note"]) };
}

// ---------------------------------------------------------------------------
// Evidence
// ---------------------------------------------------------------------------

export interface EvidenceDimension {
  key: string;
  label: string;
  value: EvidenceLabel | null;
  basis: string[];
}

export interface DisclosureView {
  title: string;
  date: string | null;
  venue: string | null;
  url: string | null;
  channelCount: number;
  requiresTranslation: boolean;
  language: string | null;
}

export interface SourceView {
  title: string;
  url: string | null;
  sourceType: string | null;
  sourceTier: string | null;
}

export interface EvidenceChannelView {
  label: string;
  available: boolean;
  detail: string;
}

const DIMENSIONS: { key: string; label: string }[] = [
  { key: "overall_research_evidence_quality", label: "Overall" },
  { key: "identity_quality", label: "Company identity" },
  { key: "financial_evidence_quality", label: "Financial evidence" },
  { key: "catalyst_evidence_quality", label: "Catalyst evidence" },
];

function buildEvidenceQuality(content: ReportContent | null): {
  overall: EvidenceLabel | null;
  dimensions: EvidenceDimension[];
} {
  const section = asRecord(content?.["evidence_quality"]);
  if (!section) return { overall: null, dimensions: [] };

  const dimensions: EvidenceDimension[] = [];
  for (const { key, label } of DIMENSIONS) {
    const dim = asRecord(section[key]);
    if (!dim) continue;
    dimensions.push({
      key,
      label,
      value: evidenceLabelOf(dim["label"]),
      basis: (Array.isArray(dim["basis"]) ? dim["basis"] : [])
        .map((b) => str(b))
        .filter((b): b is string => Boolean(b)),
    });
  }

  const overall =
    dimensions.find((d) => d.key === "overall_research_evidence_quality")
      ?.value ?? null;
  return { overall, dimensions };
}

function buildChannels(content: ReportContent | null): EvidenceChannelView[] {
  const section = asRecord(content?.["evidence_channels"]);
  const raw = section?.["channels"];
  if (!Array.isArray(raw)) return [];
  const out: EvidenceChannelView[] = [];
  for (const entry of raw) {
    const c = asRecord(entry);
    if (!c) continue;
    const label = str(c["label"]);
    if (!label) continue;
    out.push({
      label,
      available: c["available"] === true,
      detail: str(c["detail"]) ?? "",
    });
  }
  return out;
}

function buildDisclosures(content: ReportContent | null): DisclosureView[] {
  const section = asRecord(content?.["regulated_disclosures"]);
  const raw = unwrap(section?.["events"]).value;
  if (!Array.isArray(raw)) return [];
  const out: DisclosureView[] = [];
  for (const entry of raw) {
    const e = asRecord(entry);
    if (!e) continue;
    out.push({
      title: str(e["title"]) ?? "Untitled disclosure",
      date: str(e["date"]),
      venue: str(e["venue"]),
      url: str(e["url"]),
      channelCount:
        typeof e["channel_count"] === "number"
          ? (e["channel_count"] as number)
          : 1,
      requiresTranslation: e["requires_translation"] === true,
      language: str(e["language"]),
    });
  }
  return out;
}

export interface AppendixView {
  sources: SourceView[];
  totalSources: number;
  totalCitations: number | null;
  primaryReferenceCount: number;
  note: string | null;
}

function buildAppendix(content: ReportContent | null): AppendixView {
  const section = asRecord(content?.["source_citation_appendix"]);
  if (!section) {
    return {
      sources: [],
      totalSources: 0,
      totalCitations: null,
      primaryReferenceCount: 0,
      note: null,
    };
  }
  const sourcesEnvelope = unwrap(section["sources"]);
  const raw = Array.isArray(sourcesEnvelope.value) ? sourcesEnvelope.value : [];
  const sources: SourceView[] = [];
  for (const entry of raw) {
    const s = asRecord(entry);
    if (!s) continue;
    sources.push({
      title: str(s["title"]) ?? str(s["url"]) ?? "Untitled source",
      url: str(s["url"]),
      sourceType: str(s["source_type"]),
      sourceTier: str(s["source_tier"]),
    });
  }
  const citations = unwrap(section["citations"]);
  const primaryRefs = section["primary_source_reference_count"];
  return {
    sources,
    totalSources: sourcesEnvelope.total ?? sources.length,
    totalCitations: citations.total ?? null,
    primaryReferenceCount: typeof primaryRefs === "number" ? primaryRefs : 0,
    note: noteText(section["note"]),
  };
}

// ---------------------------------------------------------------------------
// Narrative
// ---------------------------------------------------------------------------

export interface NarrativeGroup {
  label: string;
  points: string[];
}

function buildNarrative(
  content: ReportContent | null,
  key: string,
  groups: { field: string; label: string }[],
  /**
   * Fields whose contents are routed through the record-gap partition. The
   * deterministic layer writes machine-record entries into some narrative
   * slots (`bear_case.key_unknowns` is dominated by them on live reports), and
   * an argument section is the wrong place for them. Nothing is discarded —
   * the caller collects them and reports them under research confidence.
   */
  partitionFields: { fields: Set<string>; collect: string[] } | null = null,
): NarrativeGroup[] {
  const section = asRecord(content?.[key]);
  if (!section) return [];
  const out: NarrativeGroup[] = [];
  for (const { field, label } of groups) {
    let points = stringList(section[field]);
    if (partitionFields?.fields.has(field)) {
      const split = partitionRecordGaps(points);
      partitionFields.collect.push(...split.recordGaps);
      points = split.analytical;
    }
    if (points.length > 0) out.push({ label, points });
  }
  return out;
}

export interface MissingItem {
  field: string;
  source: string | null;
}

function buildMissing(content: ReportContent | null): {
  items: MissingItem[];
  total: number;
} {
  const section = asRecord(content?.["missing_information"]);
  if (!section) return { items: [], total: 0 };
  const raw = unwrap(section["missing_items"]).value;
  const items: MissingItem[] = [];
  for (const entry of Array.isArray(raw) ? raw : []) {
    const m = asRecord(entry);
    if (m) {
      const field = str(m["field"]);
      if (field) items.push({ field, source: str(m["source"]) });
    } else {
      const asString = str(entry);
      if (asString) items.push({ field: asString, source: null });
    }
  }
  const total =
    typeof section["total_missing_items"] === "number"
      ? (section["total_missing_items"] as number)
      : items.length;
  return { items, total };
}

// ---------------------------------------------------------------------------
// Council
// ---------------------------------------------------------------------------

export const COUNCIL_AGENT_LABELS: Record<string, string> = {
  financial_analyst: "Financial analyst",
  business_moat: "Business quality",
  catalyst: "Catalysts",
  risk_governance: "Risk & governance",
  valuation_guard: "Valuation guard",
  source_quality_critic: "Source critic",
  red_team: "Red team",
  committee_chair: "Chair",
};

export interface CouncilView {
  used: boolean;
  agents: LlmCouncilAgent[];
  completed: number;
  failed: number;
  skipped: number;
  evidenceItems: number;
  /** True when the committee label came from a failure default, not a judgement. */
  labelIsFallback: boolean;
  chairErrorType: string | null;
  redTeam: LlmCouncilAgent | null;
  openQuestions: string[];
  unsupportedClaims: string[];
}

function buildCouncil(council: LlmCouncilMetadata | null): CouncilView {
  const agents = council?.agents ?? [];
  const redTeam = agents.find((a) => a.agent_name === "red_team") ?? null;

  const openQuestions: string[] = [];
  const unsupportedClaims: string[] = [];
  for (const agent of agents) {
    for (const gap of agent.risks_or_gaps ?? []) {
      if (gap.item) openQuestions.push(gap.item);
    }
    for (const claim of agent.unsupported_claims ?? []) {
      if (claim) unsupportedClaims.push(claim);
    }
  }

  return {
    used: Boolean(council?.llm_used),
    agents,
    completed: council?.agents_completed ?? 0,
    failed: council?.agents_failed ?? 0,
    skipped: council?.agents_skipped ?? 0,
    evidenceItems: council?.evidence_item_count ?? 0,
    labelIsFallback: council?.committee_label_basis === "deterministic_fallback",
    chairErrorType: council?.chair_error_type ?? null,
    redTeam,
    openQuestions,
    unsupportedClaims,
  };
}

// ---------------------------------------------------------------------------
// Identity
// ---------------------------------------------------------------------------

export interface IdentityView {
  companyName: string | null;
  ticker: string | null;
  exchange: string | null;
  sector: string | null;
  isin: string | null;
  lei: string | null;
}

function buildIdentity(content: ReportContent | null): IdentityView {
  const identity = asRecord(content?.["company_identity"]);
  const exec = asRecord(content?.["executive_summary"]);
  return {
    companyName:
      fieldText(identity?.["legal_name"]) ?? str(exec?.["company_name"]),
    ticker: fieldText(identity?.["ticker"]) ?? str(exec?.["ticker"]),
    exchange: fieldText(identity?.["exchange"]),
    sector: fieldText(identity?.["sector"]),
    isin: fieldText(identity?.["isin"]),
    lei: fieldText(identity?.["lei"]),
  };
}

// ---------------------------------------------------------------------------
// The view
// ---------------------------------------------------------------------------

export interface ResearchReportView {
  /** False for a legacy pre-council draft, which has no structured content. */
  structured: boolean;
  /** The backend's own judgement that evidence is too thin for a full analysis. */
  thin: boolean;
  identity: IdentityView;
  summary: string | null;
  evidence: { overall: EvidenceLabel | null; dimensions: EvidenceDimension[] };
  channels: EvidenceChannelView[];
  snapshot: FinancialSnapshotView;
  trends: { series: TrendSeriesView[]; note: string | null };
  disclosures: DisclosureView[];
  bull: NarrativeGroup[];
  bear: NarrativeGroup[];
  /** Record-completeness entries lifted out of the narrative sections. */
  narrativeRecordGaps: string[];
  /** The agent's own stated confidence in each case, when it recorded one. */
  bullConfidence: string | null;
  bearConfidence: string | null;
  /** Risks to the BUSINESS. Research limitations are reported separately. */
  risks: NarrativeGroup[];
  missing: { items: MissingItem[]; total: number };
  appendix: AppendixView;
  council: CouncilView;
  nextSteps: string[];
  humanReviewRequired: boolean;
  updatedAt: string;
}

export function buildResearchReportView(
  report: Report,
  council: LlmCouncilMetadata | null,
): ResearchReportView {
  const content = extractFinalReportContent(report.content_markdown);
  const exec = asRecord(content?.["executive_summary"]);

  // Phase 31's research memo carries a deterministic "research next steps"
  // synthesis. It is OFF by default, so its absence is normal and the view
  // simply has no next-steps block rather than inventing one.
  const memo = asRecord(content?.["research_memo"]);
  // Record entries routed out of the narrative sections, handed back so the
  // research-confidence section can report them.
  const narrativeRecordGaps: string[] = [];

  const memoSteps = asRecord(memo?.["research_next_steps"]);
  const nextSteps = memoSteps
    ? Object.entries(memoSteps)
        .filter(([k]) => !["type", "note", "disclaimer"].includes(k))
        .flatMap(([, v]) => stringList(v))
    : [];

  return {
    structured: content !== null,
    thin: isThinEvidenceReport(content),
    identity: buildIdentity(content),
    summary: fieldText(exec?.["committee_note"]) ?? report.summary,
    evidence: buildEvidenceQuality(content),
    channels: buildChannels(content),
    snapshot: buildFinancialSnapshot(content),
    trends: buildTrends(content),
    disclosures: buildDisclosures(content),
    bull: buildNarrative(content, "bull_case", [
      { field: "positive_thesis_points", label: "Thesis points" },
      { field: "potential_tailwinds", label: "Potential tailwinds" },
      { field: "assumptions", label: "What needs to be true" },
    ]),
    bear: buildNarrative(
      content,
      "bear_case",
      [
        { field: "negative_thesis_points", label: "Thesis points" },
        { field: "potential_headwinds", label: "Potential headwinds" },
        { field: "key_unknowns", label: "Key unknowns" },
      ],
      { fields: new Set(["key_unknowns"]), collect: narrativeRecordGaps },
    ),
    bullConfidence: fieldText(
      asRecord(content?.["bull_case"])?.["confidence_level"],
    ),
    bearConfidence: fieldText(
      asRecord(content?.["bear_case"])?.["confidence_level"],
    ),
    // Company risks ONLY. `data_quality_risks` and `source_quality_risks` are
    // limits on what this research could establish, not risks to the business,
    // and listing them beside "Business" made a missing EBITDA field read like
    // a hazard the company faces. They are reported under research confidence.
    risks: buildNarrative(content, "risk_analysis", [
      { field: "business_risks", label: "Business" },
      { field: "financial_risks", label: "Financial" },
      { field: "market_risks", label: "Market" },
      {
        field: "regulatory_geopolitical_risks",
        label: "Regulatory & geopolitical",
      },
    ]),
    narrativeRecordGaps,
    missing: buildMissing(content),
    appendix: buildAppendix(content),
    council: buildCouncil(council),
    nextSteps,
    humanReviewRequired: report.human_review_required,
    updatedAt: report.updated_at,
  };
}

/**
 * Read the council metadata out of `source_summary_json`. Returns null when
 * absent (legacy or deterministic runs), which keeps the honest "council did
 * not run" state rather than implying it did.
 */
export function readCouncilMetadata(
  payload: Record<string, unknown> | null,
): LlmCouncilMetadata | null {
  const council = asRecord(payload)?.["llm_council"];
  return asRecord(council) as LlmCouncilMetadata | null;
}

/** Company + ticker for a report list row, read from the report's own content. */
export function reportCompanyLabel(report: Report): {
  company: string | null;
  ticker: string | null;
} {
  const identity = buildIdentity(
    extractFinalReportContent(report.content_markdown),
  );
  return { company: identity.companyName, ticker: identity.ticker };
}

// ---------------------------------------------------------------------------
// Research library rows
// ---------------------------------------------------------------------------

export interface LibraryRow {
  id: string;
  kind: ResearchArtefactKind;
  title: string;
  company: string | null;
  ticker: string | null;
  exchange: string | null;
  latestAnnual: string | null;
  latestCurrent: string | null;
  evidence: EvidenceLabel | null;
  councilUsed: boolean;
  councilCompleted: number;
  reviewStatus: string;
  humanReviewRequired: boolean;
  /** False for a pre-council historical draft, which is a different artefact. */
  isFinal: boolean;
  updatedAt: string;
  createdAt: string;
}

/**
 * One research-library row, read from the report's own stored content.
 *
 * Everything here is a value the report already carries. A report that never
 * reached a given stage reports null for it rather than a plausible-looking
 * default — "we do not know this issuer's latest interim period" and "this
 * issuer published no interim report" are different statements, and the row
 * makes neither on the other's behalf.
 */
export function buildLibraryRow(
  report: Report,
  /**
   * Where this report sits among its company's others. Resolved by the caller,
   * which is the only place that holds the cohort — a single report cannot know
   * whether a newer one exists.
   */
  kind: ResearchArtefactKind = "screening_draft",
): LibraryRow {
  const content = extractFinalReportContent(report.content_markdown);
  const identity = buildIdentity(content);
  const snapshot = buildFinancialSnapshot(content);
  const quality = buildEvidenceQuality(content);
  const council = readCouncilMetadata(report.source_summary_json);

  return {
    id: report.id,
    kind,
    title: report.title,
    company: identity.companyName,
    ticker: identity.ticker,
    exchange: identity.exchange,
    latestAnnual: snapshot.periods?.latestAnnual ?? null,
    latestCurrent: snapshot.periods?.latestCurrent ?? null,
    evidence: quality.overall,
    councilUsed: Boolean(council?.llm_used),
    councilCompleted: council?.agents_completed ?? 0,
    reviewStatus: report.review_status ?? "draft",
    humanReviewRequired: report.human_review_required,
    isFinal: Boolean(report.final_report_version),
    updatedAt: report.updated_at,
    createdAt: report.created_at,
  };
}
