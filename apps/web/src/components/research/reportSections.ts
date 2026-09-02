// Investor-facing derivations on top of the SAME structured `report_content`.
//
// `reportView.ts` already reshapes the report for a reader. What it did not do
// was surface the research itself: the council's eight agents were reduced to
// a list of names and statuses, the business-quality analysis never appeared at
// all, catalysts were absent, and the questions a reader actually has ("is this
// growth durable? what explains the margin?") were buried under twenty-four
// raw machine field paths under a heading that called them "Important missing
// information".
//
// This module reads the persisted payload the pipeline already wrote and gives
// each of those a shape. Rules it keeps:
//
//   - Nothing is summarised by a model here. Where an agent wrote a summary,
//     that summary is shown; where it did not, the absence is stated. No LLM
//     call happens because a report was opened.
//   - A company risk and a research-confidence limitation are different things
//     and never share a heading. "Fundamentals missing; conclusions are
//     provisional" is not a risk to the business.
//   - Provenance survives. A sourced event and a model-derived interpretation
//     of that event are labelled differently, always.
//   - Implementation vocabulary (`T1_primary_filing`, `company_ir`) is
//     translated for display only. Stored values are never rewritten, and the
//     raw code stays available.

import type {
  LlmCommitteeSynthesis,
  LlmCouncilAgent,
  LlmCouncilMetadata,
} from "@/types/api";
import {
  extractFinalReportContent,
  noteText,
  unwrap,
  type ReportContent,
} from "@/components/reports/finalReportContent";
import { partitionRecordGaps } from "./recordGaps";
import {
  classifySignal,
  isEconomicSignal,
  type SignalContext,
} from "./investorSignal";
import {
  CONFLICT_NOTICE,
  buildCanonicalIndex,
  checkSentence,
  type CanonicalIndex,
} from "./numericConsistency";
import {
  asRecord,
  fieldText,
  stringList,
  type FinancialDatapoint,
  type FinancialSnapshotView,
  type NarrativeGroup,
  type TrendSeriesView,
} from "./reportView";

function str(value: unknown): string | null {
  if (typeof value === "string") return value.trim() || null;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  return null;
}

// ---------------------------------------------------------------------------
// Source vocabulary (display only — stored values are never changed)
// ---------------------------------------------------------------------------

const SOURCE_TIER_WORDS: Record<string, string> = {
  T1_primary_filing: "Issuer filing",
  T2_regulator_or_gov: "Regulatory filing",
  T3_exchange_or_venue: "Exchange disclosure",
  T4_established_media: "Established media",
  T5_api_aggregator: "Market-data provider",
  T6_model_estimate: "Model interpretation",
};

const SOURCE_TYPE_WORDS: Record<string, string> = {
  company_ir: "Issuer investor relations",
  company_ir_financial_fact: "Issuer filing figure",
  company_press_release: "Issuer announcement",
  sec_filing: "SEC filing",
  sec_xbrl: "SEC structured data",
  regulated_disclosure: "Regulated disclosure",
  exchange_profile: "Exchange profile",
  news: "News coverage",
  source_reference: "Located source",
  free_real: "Market-data provider",
};

/** Human words for a source tier code, with the code kept for the tooltip. */
export function sourceTierWord(tier: string | null | undefined): string | null {
  if (!tier) return null;
  return SOURCE_TIER_WORDS[tier] ?? tier.replace(/^T\d+_/, "").replace(/_/g, " ");
}

export function sourceTypeWord(type: string | null | undefined): string | null {
  if (!type) return null;
  return SOURCE_TYPE_WORDS[type] ?? type.replace(/_/g, " ");
}

// ---------------------------------------------------------------------------
// Implementation vocabulary, translated for display
// ---------------------------------------------------------------------------
//
// The deterministic layer writes its own identifiers straight into prose:
// "supported at T1_primary_filing", "provider returned free_real_not_sourced",
// "identity.isin absent". Those are true and they are useful — to an engineer
// reading the technical report. In a reader-facing bull case they are noise
// that makes correct analysis look like a log line.
//
// So they are TRANSLATED, not deleted. Deleting would lose the sentence's
// meaning, and rewriting the stored value would make the clean view and the
// technical view disagree about what the pipeline said. The raw record is
// untouched and stays on the technical report page; only the rendered string
// changes.

/** Provider / source-state codes the deterministic layer emits in prose. */
const IMPLEMENTATION_WORDS: Record<string, string> = {
  free_real_not_sourced: "not sourced by the market-data provider",
  issuer_primary_document: "the issuer's own document",
  primary_filing: "the issuer's filing",
  current_period: "the current reporting period",
  model_interpretation: "model interpretation",
  sourced_fact: "a sourced fact",
  missing_data: "not available",
  not_sourced: "not sourced",
};

/** A source-tier code as it appears inside a sentence. */
const TIER_CODE_RE = /\bT[1-6]_[a-z][a-z0-9_]*\b/g;

/**
 * A dotted machine field path inside a sentence — `identity.isin`,
 * `fundamentals.ebitda_mln`.
 *
 * Every segment must be lowercase and at least two characters, which is what
 * keeps ordinary prose ("e.g.", "i.e.", "vs.") out of it.
 */
const FIELD_PATH_RE = /\b[a-z][a-z0-9_]+(?:\.[a-z][a-z0-9_]+)+\b/g;

/** ACRONYMS a humanised field leaf should keep in capitals. */
const FIELD_ACRONYMS = new Set(["isin", "lei", "cik", "sec", "ebit", "ebitda", "eps", "fcf"]);

function humaniseFieldLeaf(path: string): string {
  const leaf = path.split(".").pop() ?? path;
  return leaf
    .split("_")
    .map((word) => (FIELD_ACRONYMS.has(word) ? word.toUpperCase() : word))
    .join(" ");
}

/**
 * One statement with its implementation vocabulary put into human words.
 *
 * Pure and idempotent: a sentence carrying none of it comes back unchanged.
 */
export function humaniseTechnical(text: string): string {
  let out = text;
  for (const [code, word] of Object.entries(IMPLEMENTATION_WORDS)) {
    out = out.replace(new RegExp(`\\b${code}\\b`, "g"), word);
  }
  out = out.replace(
    TIER_CODE_RE,
    (code) => sourceTierWord(code) ?? code.replace(/_/g, " "),
  );
  out = out.replace(FIELD_PATH_RE, (path) => {
    const known = SOURCE_TYPE_WORDS[path];
    return known ?? humaniseFieldLeaf(path);
  });
  return out;
}

/** True when a statement still carries implementation vocabulary. */
export function hasImplementationVocabulary(text: string): boolean {
  return humaniseTechnical(text) !== text;
}

// ---------------------------------------------------------------------------
// Council — every agent, with what it actually concluded
// ---------------------------------------------------------------------------

export const COUNCIL_AGENT_ORDER: string[] = [
  "financial_analyst",
  "business_moat",
  "catalyst",
  "risk_governance",
  "valuation_guard",
  "source_quality_critic",
  "red_team",
  "committee_chair",
];

export const COUNCIL_AGENT_ROLES: Record<string, string> = {
  financial_analyst:
    "Revenue, margins, cash flow, debt and balance-sheet strength, from the evidence.",
  business_moat:
    "Business model, competitive position, end-market exposure and durability.",
  catalyst: "Recent and potential catalysts present in the evidence.",
  risk_governance:
    "Governance, liquidity, disclosure quality, concentration and leverage.",
  valuation_guard:
    "Which valuation inputs exist and which are missing — never a valuation.",
  source_quality_critic:
    "Uncited claims, weak or stale sources, and source-tier mismatches.",
  red_team: "The case against the strongest apparent claims.",
  committee_chair: "The council's synthesis over everything above.",
};

// Kept local so this module does not depend on reportView's ordering choices.
const COUNCIL_AGENT_LABELS_LOCAL: Record<string, string> = {
  financial_analyst: "Financial analyst",
  business_moat: "Business quality",
  catalyst: "Catalysts",
  risk_governance: "Risk & governance",
  valuation_guard: "Valuation guard",
  source_quality_critic: "Source critic",
  red_team: "Red team",
  committee_chair: "Chair",
};

export interface AgentImplicationView {
  statement: string;
  mechanism: string | null;
  /** supportive | pressuring | mixed | neutral. */
  direction: string;
  confidence: string | null;
}

export interface CouncilAgentDetail {
  name: string;
  label: string;
  role: string | null;
  status: string;
  completed: boolean;
  summary: string | null;
  /** The agent's own key findings, each with the confidence it stated. */
  findings: { claim: string; confidence: string | null; dataQuality: string | null }[];
  /**
   * What those findings MEAN. This is the agent's analysis; `findings` is the
   * evidence it rests on, and the two are never merged.
   */
  implications: AgentImplicationView[];
  /** What the agent flagged as a risk or a gap, with its severity. */
  concerns: { item: string; severity: string | null }[];
  unsupportedClaims: string[];
  committeeLabel: string | null;
}

function agentDetail(agent: LlmCouncilAgent): CouncilAgentDetail {
  return {
    name: agent.agent_name,
    label: COUNCIL_AGENT_LABELS_LOCAL[agent.agent_name] ?? agent.agent_name,
    role: COUNCIL_AGENT_ROLES[agent.agent_name] ?? null,
    status: agent.status,
    completed: agent.status === "completed",
    summary: str(agent.summary),
    findings: (agent.key_points ?? [])
      .map((p) => ({
        claim: str(p.claim),
        confidence: str(p.confidence),
        dataQuality: str(p.data_quality),
      }))
      .filter(
        (p): p is CouncilAgentDetail["findings"][number] => Boolean(p.claim),
      ),
    implications: (agent.implications ?? [])
      .map((i) => ({
        statement: str(i.statement),
        mechanism: str(i.mechanism),
        direction: str(i.direction) ?? "neutral",
        confidence: str(i.confidence),
      }))
      .filter((i): i is AgentImplicationView => Boolean(i.statement)),
    concerns: (agent.risks_or_gaps ?? [])
      .map((g) => ({ item: str(g.item), severity: str(g.severity) }))
      .filter((g): g is CouncilAgentDetail["concerns"][number] => Boolean(g.item)),
    unsupportedClaims: (agent.unsupported_claims ?? []).filter(Boolean),
    committeeLabel: str(agent.committee_label),
  };
}

export function buildCouncilAgentDetails(
  council: LlmCouncilMetadata | null,
): CouncilAgentDetail[] {
  const agents = council?.agents ?? [];
  const order = new Map(COUNCIL_AGENT_ORDER.map((name, i) => [name, i]));
  return [...agents]
    .sort(
      (a, b) =>
        (order.get(a.agent_name) ?? 99) - (order.get(b.agent_name) ?? 99),
    )
    .map(agentDetail);
}

export function findAgent(
  agents: CouncilAgentDetail[],
  name: string,
): CouncilAgentDetail | null {
  return agents.find((a) => a.name === name) ?? null;
}

// ---------------------------------------------------------------------------
// Chair / committee synthesis
// ---------------------------------------------------------------------------

/**
 * Internal status labels, in human words.
 *
 * `requires_more_evidence` is a research-queue state. Rendered raw it reads
 * like a machine field; rendered as an investment word it would be a rating.
 * "More evidence needed" is neither.
 */
const INTERNAL_STATUS_WORDS: Record<string, string> = {
  requires_more_evidence: "More evidence needed",
  research_incomplete: "Research incomplete",
  not_enough_data: "Not enough data",
  research_complete: "Research complete",
  evidence_sufficient: "Evidence sufficient",
};

const BALANCE_WORDS: Record<string, string> = {
  insufficient_data: "Not enough evidence to weigh the two cases",
  balanced: "Bull and bear cases are evenly matched on the evidence",
  bull_leaning: "The evidence leans towards the positive case",
  bear_leaning: "The evidence leans towards the negative case",
};

export function internalStatusWord(status: string | null): string | null {
  if (!status) return null;
  return INTERNAL_STATUS_WORDS[status] ?? status.replace(/_/g, " ");
}

export function bullBearBalanceWord(balance: string | null): string | null {
  if (!balance) return null;
  return BALANCE_WORDS[balance] ?? balance.replace(/_/g, " ");
}

/** The chair's investment-facing synthesis, as the backend persisted it. */
export interface CommitteeSynthesisView {
  present: boolean;
  fundamentalSetup: string | null;
  strongestPositive: string[];
  strongestNegative: string[];
  resilience: string[];
  fragility: string[];
  keyDebate: string | null;
  whatWouldStrengthen: string[];
  whatWouldWeaken: string[];
  whatToWatch: string[];
}

export const EMPTY_SYNTHESIS: CommitteeSynthesisView = {
  present: false,
  fundamentalSetup: null,
  strongestPositive: [],
  strongestNegative: [],
  resilience: [],
  fragility: [],
  keyDebate: null,
  whatWouldStrengthen: [],
  whatWouldWeaken: [],
  whatToWatch: [],
};

/**
 * The fundamental setup, in human words.
 *
 * A research characterisation of what the evidence supports. It has no
 * BUY/SELL/HOLD meaning and the wording is chosen so it cannot be read as one.
 */
const SETUP_WORDS: Record<string, string> = {
  constructive: "Constructive",
  mixed: "Mixed",
  cautious: "Cautious",
  insufficient_evidence: "Not enough evidence to characterise",
};

export function fundamentalSetupWord(setup: string | null): string | null {
  if (!setup) return null;
  return SETUP_WORDS[setup] ?? setup.replace(/_/g, " ");
}

function synthesisView(
  agents: CouncilAgentDetail[],
  raw: LlmCommitteeSynthesis | null | undefined,
): CommitteeSynthesisView {
  void agents;
  if (!raw) return EMPTY_SYNTHESIS;
  const list = (v: string[] | undefined) => (v ?? []).filter(Boolean);
  return {
    present: true,
    fundamentalSetup: str(raw.fundamental_setup),
    strongestPositive: list(raw.strongest_positive_evidence),
    strongestNegative: list(raw.strongest_negative_evidence),
    resilience: list(raw.resilience_factors),
    fragility: list(raw.fragility_factors),
    keyDebate: str(raw.key_debate),
    whatWouldStrengthen: list(raw.what_would_strengthen),
    whatWouldWeaken: list(raw.what_would_weaken),
    whatToWatch: list(raw.what_to_watch),
  };
}

export interface ChairView {
  present: boolean;
  /** The chair's synthesis prose, from the report's own committee section. */
  summary: string | null;
  /** The council chair agent's own summary, when the LLM council ran. */
  agentSummary: string | null;
  balance: string | null;
  internalStatus: string | null;
  openQuestions: string[];
  nextSteps: string[];
  note: string | null;
  /** The structured investment synthesis, when the chair produced one. */
  synthesis: CommitteeSynthesisView;
}

export function buildChair(
  content: ReportContent | null,
  agents: CouncilAgentDetail[],
  rawSynthesis: LlmCommitteeSynthesis | null = null,
): ChairView {
  const section = asRecord(content?.["committee_chair_summary"]);
  const chairAgent = findAgent(agents, "committee_chair");
  const synthesis = synthesisView(agents, rawSynthesis);
  if (!section) {
    return {
      present: Boolean(chairAgent?.summary) || synthesis.present,
      summary: null,
      agentSummary: chairAgent?.summary ?? null,
      balance: null,
      internalStatus: null,
      openQuestions: [],
      nextSteps: [],
      note: null,
      synthesis,
    };
  }
  return {
    present: section["available"] !== false || Boolean(chairAgent?.summary),
    summary: fieldText(section["committee_summary"]),
    agentSummary: chairAgent?.summary ?? null,
    balance: fieldText(section["bull_bear_balance"]),
    internalStatus: fieldText(section["provisional_internal_status"]),
    openQuestions: stringList(section["primary_open_questions"]),
    nextSteps: stringList(section["research_next_steps"]),
    note: noteText(section["note"]),
    synthesis,
  };
}

// ---------------------------------------------------------------------------
// Business & competitive position
// ---------------------------------------------------------------------------

export interface BusinessQualityView {
  present: boolean;
  summary: string | null;
  findings: CouncilAgentDetail["findings"];
  concerns: CouncilAgentDetail["concerns"];
  /** True when the agent ran but produced nothing usable. */
  ranButEmpty: boolean;
}

export function buildBusinessQuality(
  agents: CouncilAgentDetail[],
): BusinessQualityView {
  const agent = findAgent(agents, "business_moat");
  if (!agent) {
    return {
      present: false,
      summary: null,
      findings: [],
      concerns: [],
      ranButEmpty: false,
    };
  }
  const empty =
    !agent.summary && agent.findings.length === 0 && agent.concerns.length === 0;
  return {
    present: true,
    summary: agent.summary,
    findings: agent.findings,
    concerns: agent.concerns,
    ranButEmpty: empty,
  };
}

// ---------------------------------------------------------------------------
// Recent developments
// ---------------------------------------------------------------------------

export interface CatalystEvent {
  date: string | null;
  headline: string;
  sourceName: string | null;
  sourceUrl: string | null;
  sourceTier: string | null;
  /** Model-derived labels — visually separated from the event itself. */
  category: string | null;
  direction: string | null;
  strength: string | null;
  materiality: string | null;
  materialityReason: string | null;
  isModelLabelled: boolean;
}

function catalystEvent(raw: unknown): CatalystEvent | null {
  const e = asRecord(raw);
  if (!e) return null;
  const headline = str(e["headline"]);
  if (!headline) return null;
  return {
    date: str(e["event_date"]),
    headline,
    sourceName: str(e["source_name"]),
    sourceUrl: str(e["source_url"]),
    sourceTier: str(e["source_tier"]),
    category: str(e["catalyst_category"]),
    direction: str(e["catalyst_direction"]),
    strength: str(e["catalyst_strength"]),
    materiality: str(e["materiality"]),
    materialityReason: str(e["materiality_reason"]),
    isModelLabelled: true,
  };
}

export interface CatalystsView {
  available: boolean;
  coverageStatus: string | null;
  lookbackDays: number | null;
  companyEvents: CatalystEvent[];
  filingEvents: CatalystEvent[];
  /** The catalyst agent's reading of what the events mean. */
  interpretation: string | null;
  interpretationFindings: CouncilAgentDetail["findings"];
  note: string | null;
}

export function buildCatalysts(
  content: ReportContent | null,
  agents: CouncilAgentDetail[],
): CatalystsView {
  const section = asRecord(content?.["news_catalyst_discovery"]);
  const agent = findAgent(agents, "catalyst");

  const read = (key: string): CatalystEvent[] => {
    const raw = unwrap(section?.[key]).value;
    return (Array.isArray(raw) ? raw : [])
      .map(catalystEvent)
      .filter((e): e is CatalystEvent => e !== null);
  };

  const lookback = section?.["lookback_days"];
  return {
    available: Boolean(section) && section?.["available"] !== false,
    coverageStatus: str(section?.["coverage_status"]),
    lookbackDays: typeof lookback === "number" ? lookback : null,
    companyEvents: read("recent_events"),
    filingEvents: read("sec_filing_events"),
    interpretation: agent?.summary ?? null,
    interpretationFindings: agent?.findings ?? [],
    note: noteText(section?.["note"]),
  };
}

// ---------------------------------------------------------------------------
// Key financials, grouped for reading
// ---------------------------------------------------------------------------

export interface FinancialGroup {
  key: string;
  label: string;
  annual: FinancialDatapoint[];
  current: FinancialDatapoint[];
  statements: FinancialDatapoint[];
}

/**
 * Which reading group each canonical slot belongs to.
 *
 * A metric appears in exactly one group, and a group with nothing in it is not
 * rendered — an empty "Cash generation" card would read as "this company
 * generates no cash", which is a different statement from "no cash-flow figure
 * was sourced".
 */
const FIELD_GROUPS: { key: string; label: string; fields: string[] }[] = [
  {
    key: "profitability",
    label: "Profitability",
    fields: [
      "revenue",
      "revenue_usd_m",
      "gross_profit_usd_m",
      "operating_profit",
      "recurring_operating_profit",
      "operating_income_usd_m",
      "operating_margin",
      "recurring_operating_margin",
      "net_income",
      "net_income_usd_m",
      "eps_diluted",
    ],
  },
  {
    key: "cash",
    label: "Cash generation",
    fields: [
      "operating_cash_flow",
      "operating_cash_flow_usd_m",
      "free_cash_flow",
      "free_cash_flow_usd_m",
      "operating_free_cash_flow",
      "capital_expenditures_usd_m",
    ],
  },
  {
    key: "balance_sheet",
    label: "Balance sheet",
    fields: [
      "total_assets",
      "total_assets_usd_m",
      "total_equity",
      "shareholders_equity_usd_m",
      "total_liabilities_usd_m",
      "cash_and_equivalents",
      "cash_and_equivalents_usd_m",
      "total_debt",
      "total_debt_usd_m",
      "net_debt",
      "net_cash",
    ],
  },
];

export function groupFinancials(
  snapshot: FinancialSnapshotView,
): FinancialGroup[] {
  const groups: FinancialGroup[] = [];
  for (const group of FIELD_GROUPS) {
    const inGroup = (dp: FinancialDatapoint) => group.fields.includes(dp.key);
    const annual = snapshot.annual.filter(inGroup);
    const current = snapshot.currentPeriod.filter(inGroup);
    const statements = snapshot.statements.filter(inGroup);
    if (annual.length + current.length + statements.length === 0) continue;
    groups.push({
      key: group.key,
      label: group.label,
      annual,
      current,
      statements,
    });
  }

  // Anything the extractor produced that no group claims still gets shown —
  // silently dropping a sourced figure would be worse than an "Other" heading.
  const claimed = new Set(FIELD_GROUPS.flatMap((g) => g.fields));
  const rest = (dps: FinancialDatapoint[]) =>
    dps.filter((dp) => !claimed.has(dp.key));
  const otherAnnual = rest(snapshot.annual);
  const otherCurrent = rest(snapshot.currentPeriod);
  const otherStatements = rest(snapshot.statements);
  if (otherAnnual.length + otherCurrent.length + otherStatements.length > 0) {
    groups.push({
      key: "other",
      label: "Other reported figures",
      annual: otherAnnual,
      current: otherCurrent,
      statements: otherStatements,
    });
  }
  return groups;
}

// ---------------------------------------------------------------------------
// Direction, not just level
// ---------------------------------------------------------------------------

export interface MetricDirection {
  /** "+8.2%" for a value, "+120 bps" for something already in percent. */
  change: string;
  /** The two periods the change is measured between. */
  from: string;
  to: string;
  improving: boolean;
}

/**
 * The change in a metric between its two most recent comparable periods.
 *
 * A level without a direction is half the information: "operating margin 23.9%"
 * says much less than "23.9%, +120bps". But a direction computed across
 * incomparable periods is a fabrication, so this only ever reads a series the
 * BACKEND marked ``comparable``, of the SAME period type, for the same metric
 * and scope — and it never touches interim figures, because comparing a half
 * year to a full one is the error this whole report is built to avoid.
 *
 * Percentage metrics move in basis points; everything else in percent.
 */
export function metricDirections(
  series: TrendSeriesView[],
): Map<string, MetricDirection> {
  const out = new Map<string, MetricDirection>();
  for (const s of series) {
    if (!s.comparable) continue;
    if (s.periodType !== "annual") continue;
    const points = s.points.filter(
      (p): p is { period: string; value: number } => p.value !== null,
    );
    if (points.length < 2) continue;

    const previous = points[points.length - 2];
    const latest = points[points.length - 1];
    if (previous.value === 0) continue;

    const isPercent = (s.unit ?? "").trim() === "%";
    const delta = latest.value - previous.value;
    const change = isPercent
      ? `${delta >= 0 ? "+" : ""}${Math.round(delta * 100)} bps`
      : `${delta >= 0 ? "+" : ""}${((delta / Math.abs(previous.value)) * 100).toFixed(1)}%`;

    // One entry per metric+scope, so a segment series never overwrites the
    // Group's direction for the same metric.
    out.set(`${s.metric}::${s.scope ?? "group"}`, {
      change,
      from: previous.period,
      to: latest.period,
      improving: delta >= 0,
    });
  }
  return out;
}

/** The Group-level direction for one canonical metric key, when there is one. */
export function directionFor(
  directions: Map<string, MetricDirection>,
  metricKey: string,
): MetricDirection | null {
  return (
    directions.get(`${metricKey}::group`) ??
    directions.get(`${metricKey}::null`) ??
    directions.get(`${metricKey}::`) ??
    null
  );
}

// ---------------------------------------------------------------------------
// Risks — the company's, kept apart from the research's
// ---------------------------------------------------------------------------

export interface RiskGroups {
  /** Risks to the business. */
  company: { label: string; points: string[] }[];
  /** Limits on what this research could establish. NOT company risks. */
  researchLimitations: { label: string; points: string[] }[];
  summary: string | null;
}

const COMPANY_RISK_FIELDS: { field: string; label: string }[] = [
  { field: "business_risks", label: "Business" },
  { field: "financial_risks", label: "Financial" },
  { field: "market_risks", label: "Market" },
  { field: "regulatory_geopolitical_risks", label: "Regulatory & geopolitical" },
];

const RESEARCH_LIMITATION_FIELDS: { field: string; label: string }[] = [
  { field: "data_quality_risks", label: "Data quality" },
  { field: "source_quality_risks", label: "Source quality" },
];

export function buildRiskGroups(
  content: ReportContent | null,
): RiskGroups & { routedLimitations: string[] } {
  const section = asRecord(content?.["risk_analysis"]);
  const routedLimitations: string[] = [];

  const collect = (
    defs: { field: string; label: string }[],
    route: boolean,
  ) => {
    const out: { label: string; points: string[] }[] = [];
    for (const { field, label } of defs) {
      let points = stringList(section?.[field]);
      if (route) {
        // A company-risk slot holding an evidence statement is a
        // misclassification upstream, not a hazard the business faces.
        const kept: string[] = [];
        for (const point of points) {
          const signal = classifySignal(point, {
            agent: "risk_governance",
            slot: "company_risk",
          });
          // A genuine company risk may still be WRITTEN in implementation
          // vocabulary. It is translated for display, exactly as the two case
          // sections translate theirs; the stored value is untouched and the
          // raw record stays on the technical page.
          if (signal === "company_risk") kept.push(humaniseTechnical(point));
          else routedLimitations.push(point);
        }
        points = kept;
      }
      if (points.length > 0) out.push({ label, points });
    }
    return out;
  };

  // The section's own summary line. On live reports it is a RESEARCH-STATE
  // sentence, not a risk one:
  //
  //   "Risk assessment for PNDORA (PNDORA), sector not sourced, Denmark. Total
  //    risk flags: 19 (3 marked UNKNOWN due to missing data). Data quality:
  //    identity/price T6_model_estimate, financial statement facts
  //    T1_primary_filing. Assessment is incomplete …"
  //
  // That was the last route by which a source-tier code reached the
  // company-risk section on all three live reports. It goes through the same
  // rule as every point above it: an evidence statement is reported under
  // research confidence, and anything that survives is translated for display.
  const rawSummary = fieldText(section?.["risk_summary_text"]);
  let summary: string | null = null;
  if (rawSummary) {
    const signal = classifySignal(rawSummary, {
      agent: "risk_governance",
      slot: "company_risk",
    });
    if (signal === "company_risk") summary = humaniseTechnical(rawSummary);
    else routedLimitations.push(rawSummary);
  }

  return {
    company: collect(COMPANY_RISK_FIELDS, true),
    researchLimitations: collect(RESEARCH_LIMITATION_FIELDS, false),
    summary,
    routedLimitations,
  };
}

// ---------------------------------------------------------------------------
// Open research questions
// ---------------------------------------------------------------------------

export interface OpenQuestion {
  question: string;
  /** Who raised it — the red team, or a named council agent. */
  source: string;
}

/**
 * The questions that materially affect understanding the company.
 *
 * The COUNCIL is the source, and the order is source order, not a ranking this
 * UI invented: the red team's gaps are the adversarial check on the positive
 * reading, then each analyst's own gaps in the council's run order.
 *
 * The chair section's `primary_open_questions` is deliberately NOT the primary
 * source. It is assembled deterministically from the bear case's key unknowns
 * and the bull case's missing evidence, and on live reports it is dominated by
 * record entries — "Blocking gap: Required field missing: identity.isin" was
 * the FIRST thing it offered for three of four issuers checked. It is used
 * only when the council did not run, and record entries are routed to research
 * confidence either way.
 */
export function buildOpenQuestions(
  chair: ChairView,
  agents: CouncilAgentDetail[],
): {
  questions: OpenQuestion[];
  recordGaps: string[];
  researchLimitations: string[];
} {
  const seen = new Set<string>();
  const questions: OpenQuestion[] = [];
  const recordGaps: string[] = [];
  const researchLimitations: string[] = [];

  const push = (question: string, source: string, agent: string) => {
    const value = question.trim();
    const key = value.toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    const signal = classifySignal(value, { agent, slot: "risk_or_gap" });
    if (signal === "technical_gap") recordGaps.push(value);
    else if (signal === "research_limitation") researchLimitations.push(value);
    else questions.push({ question: value, source });
  };

  // The committee's own statement of what is unresolved. On live reports this
  // is the ONLY genuine investor question the council produces: every single
  // `risks_or_gaps` item across PNDORA and CFR — 49 of them — was a statement
  // about what evidence was missing, not about the business.
  if (chair.synthesis.keyDebate) {
    push(chair.synthesis.keyDebate, "Chair — key debate", "committee_chair");
  }

  const redTeam = findAgent(agents, "red_team");
  for (const c of redTeam?.concerns ?? []) push(c.item, "Red team", "red_team");

  for (const agent of agents) {
    if (agent.name === "red_team") continue;
    for (const c of agent.concerns) push(c.item, agent.label, agent.name);
  }

  // Only when there is no council to read.
  if (agents.length === 0) {
    for (const q of chair.openQuestions) push(q, "Chair", "committee_chair");
  }

  return { questions, recordGaps, researchLimitations };
}

// ---------------------------------------------------------------------------
// Numeric consistency
// ---------------------------------------------------------------------------

/**
 * Reconcile every numeric claim in council prose against the report's own
 * canonical figures, and WITHHOLD any that contradicts them.
 *
 * A report that shows "revenue of DKK 14,328m" in one section and a council
 * sentence asserting a different revenue for the same period is not showing two
 * views; it is contradicting itself, and a reader has no way to know which to
 * trust. Choosing one would be worse: this layer has no basis for preferring
 * the prose or the fact, and silently picking is exactly what the report must
 * not do.
 *
 * So a conflicting sentence is replaced with a statement that it conflicts.
 * The check runs only where a metric this report actually carries is named —
 * everything else is left exactly as written.
 */
export function reconcileNumbers<T>(
  items: T[],
  read: (item: T) => string,
  replace: (item: T, notice: string) => T,
  canonical: CanonicalIndex,
): { items: T[]; conflicts: number } {
  if (canonical.figures.size === 0) return { items, conflicts: 0 };
  let conflicts = 0;
  const out = items.map((item) => {
    const { verdict } = checkSentence(read(item), canonical);
    if (verdict !== "conflicting") return item;
    conflicts += 1;
    return replace(item, CONFLICT_NOTICE);
  });
  return { items: out, conflicts };
}

/** Apply the reconciliation across everything a reader sees as council prose. */
export function reconcileCouncilNumbers(
  view: InvestorReportView,
  snapshot: FinancialSnapshotView,
  trends: TrendSeriesView[] = [],
): InvestorReportView & { numericConflicts: number } {
  // Every period AND every scope the report holds — headline slots and the
  // multi-year series, Group and segment alike. A segment claim is adjudicated
  // against that segment, never against the consolidated total.
  const canonical = buildCanonicalIndex(snapshot, trends);
  if (canonical.figures.size === 0) return { ...view, numericConflicts: 0 };

  let conflicts = 0;

  const agents = view.agents.map((agent) => {
    const findings = reconcileNumbers(
      agent.findings,
      (f) => f.claim,
      (f, notice) => ({ ...f, claim: notice }),
      canonical,
    );
    const implications = reconcileNumbers(
      agent.implications,
      (i) => `${i.statement} ${i.mechanism ?? ""}`,
      (i, notice) => ({ ...i, statement: notice, mechanism: null }),
      canonical,
    );
    conflicts += findings.conflicts + implications.conflicts;
    return {
      ...agent,
      findings: findings.items,
      implications: implications.items,
    };
  });

  const points = (list: DirectionalPoint[]) =>
    reconcileNumbers(
      list,
      (p) => `${p.statement} ${p.mechanism ?? ""}`,
      (p, notice) => ({ ...p, statement: notice, mechanism: null }),
      canonical,
    );
  const higher = points(view.reading.couldDriveHigher);
  const pressure = points(view.reading.couldPressure);
  const resilience = points(view.reading.resilience);
  const fragility = points(view.reading.fragility);
  conflicts +=
    higher.conflicts +
    pressure.conflicts +
    resilience.conflicts +
    fragility.conflicts;

  // The chair's own four lists and its strengthen/weaken conditions. These
  // are rendered as plain sentences in the committee synthesis AND, since the
  // two cases are assembled from the council rather than from the
  // deterministic narrative, as the bull and bear arguments themselves. A
  // number that contradicts the report's canonical figures must be withheld
  // there too — otherwise the guard would cover the agents' prose and not the
  // committee's conclusion drawn from it.
  const sentences = (list: string[]) =>
    reconcileNumbers(
      list,
      (t) => t,
      (_t, notice) => notice,
      canonical,
    );
  const chairPositive = sentences(view.reading.chairPositive);
  const chairNegative = sentences(view.reading.chairNegative);
  const chairResilience = sentences(view.reading.chairResilience);
  const chairFragility = sentences(view.reading.chairFragility);
  const strengthen = sentences(view.reading.whatWouldStrengthen);
  const weaken = sentences(view.reading.whatWouldWeaken);
  const watch = sentences(view.reading.whatToWatch);
  conflicts +=
    chairPositive.conflicts +
    chairNegative.conflicts +
    chairResilience.conflicts +
    chairFragility.conflicts +
    strengthen.conflicts +
    weaken.conflicts +
    watch.conflicts;

  return {
    ...view,
    agents,
    reading: {
      ...view.reading,
      chairPositive: chairPositive.items,
      chairNegative: chairNegative.items,
      chairResilience: chairResilience.items,
      chairFragility: chairFragility.items,
      couldDriveHigher: higher.items,
      couldPressure: pressure.items,
      resilience: resilience.items,
      fragility: fragility.items,
      whatWouldStrengthen: strengthen.items,
      whatWouldWeaken: weaken.items,
      whatToWatch: watch.items,
    },
    numericConflicts: conflicts,
  };
}

// ---------------------------------------------------------------------------
// The investment reading
// ---------------------------------------------------------------------------

export interface DirectionalPoint {
  statement: string;
  mechanism: string | null;
  source: string;
  confidence: string | null;
}

export interface InvestmentReading {
  /** The chair's overall characterisation, in human words. */
  setupWord: string | null;
  /**
   * The chair's OWN four lists, after routing. The committee-synthesis section
   * renders these rather than the raw fields so it cannot show as a "strongest
   * negative" something the summary just routed to research confidence.
   */
  chairPositive: string[];
  chairNegative: string[];
  chairResilience: string[];
  chairFragility: string[];
  /** What could make the business/equity materially more valuable. */
  couldDriveHigher: DirectionalPoint[];
  /** What could pressure it. */
  couldPressure: DirectionalPoint[];
  /** What limits downside if conditions deteriorate. */
  resilience: DirectionalPoint[];
  /** What could create disproportionate downside. */
  fragility: DirectionalPoint[];
  /** Specific, measurable things to monitor next. */
  whatToWatch: string[];
  keyDebate: string | null;
  whatWouldStrengthen: string[];
  whatWouldWeaken: string[];
  /** True when the council recorded no interpretation at all. */
  empty: boolean;
}

/**
 * The investment reading, assembled from what the council already persisted.
 *
 * Two sources, in order. The chair's structured synthesis is the committee's
 * own answer and comes first. Beneath it sit the individual agents'
 * implications, grouped by the DIRECTION each agent gave its own statement —
 * not by this layer's reading of the words.
 *
 * Nothing is generated here and nothing is summarised. An agent that recorded
 * no implication contributes nothing, and a report whose council predates the
 * implication field produces an empty reading, which the UI states rather than
 * papers over.
 */
export function buildInvestmentReading(
  chair: ChairView,
  agents: CouncilAgentDetail[],
): InvestmentReading & { routedLimitations: string[] } {
  const seen = new Set<string>();
  const routedLimitations: string[] = [];
  const take = (statement: string): boolean => {
    const key = statement.trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  };

  const higher: DirectionalPoint[] = [];
  const pressure: DirectionalPoint[] = [];
  const resilience: DirectionalPoint[] = [];
  const fragility: DirectionalPoint[] = [];

  /**
   * Place one statement, or route it out.
   *
   * Every economic section goes through here, so nothing reaches "what could
   * drive value higher" without having been asked whether it is about the
   * company at all. A statement that turns out to be about the EVIDENCE is not
   * dropped — it is collected for the research-confidence section, where it
   * describes what it actually describes.
   */
  const place = (
    statement: string,
    context: SignalContext,
    point: Omit<DirectionalPoint, "statement">,
  ) => {
    if (!take(statement)) return;
    const signal = classifySignal(statement, context);
    if (!isEconomicSignal(signal)) {
      routedLimitations.push(statement);
      return;
    }
    const full: DirectionalPoint = { statement, ...point };
    if (signal === "economic_support") higher.push(full);
    else if (signal === "economic_pressure") pressure.push(full);
    else if (signal === "resilience") resilience.push(full);
    else if (signal === "fragility") fragility.push(full);
  };

  const chairPoint = { mechanism: null, source: "Chair", confidence: null };
  const chairKept: Record<string, string[]> = {
    chair_positive: [],
    chair_negative: [],
    chair_resilience: [],
    chair_fragility: [],
  };
  for (const [slot, points] of [
    ["chair_positive", chair.synthesis.strongestPositive],
    ["chair_negative", chair.synthesis.strongestNegative],
    ["chair_resilience", chair.synthesis.resilience],
    ["chair_fragility", chair.synthesis.fragility],
  ] as const) {
    for (const point of points) {
      const signal = classifySignal(point, {
        agent: "committee_chair",
        slot,
      });
      if (isEconomicSignal(signal)) chairKept[slot].push(point);
      place(point, { agent: "committee_chair", slot }, chairPoint);
    }
  }

  for (const agent of agents) {
    if (agent.name === "committee_chair") continue;
    for (const imp of agent.implications) {
      place(
        imp.statement,
        { agent: agent.name, slot: "implication", direction: imp.direction },
        {
          mechanism: imp.mechanism,
          source: agent.label,
          confidence: imp.confidence,
        },
      );
    }
  }

  const reading: InvestmentReading & { routedLimitations: string[] } = {
    setupWord: fundamentalSetupWord(chair.synthesis.fundamentalSetup),
    chairPositive: chairKept.chair_positive,
    chairNegative: chairKept.chair_negative,
    chairResilience: chairKept.chair_resilience,
    chairFragility: chairKept.chair_fragility,
    couldDriveHigher: higher,
    couldPressure: pressure,
    resilience,
    fragility,
    whatToWatch: chair.synthesis.whatToWatch,
    keyDebate: chair.synthesis.keyDebate,
    whatWouldStrengthen: chair.synthesis.whatWouldStrengthen,
    whatWouldWeaken: chair.synthesis.whatWouldWeaken,
    empty: false,
    routedLimitations,
  };
  reading.empty =
    higher.length === 0 &&
    pressure.length === 0 &&
    resilience.length === 0 &&
    fragility.length === 0 &&
    reading.whatToWatch.length === 0;
  return reading;
}

// ---------------------------------------------------------------------------
// The two cases, as the COUNCIL argued them
// ---------------------------------------------------------------------------

/**
 * The bull and bear cases a reader is shown.
 *
 * WHAT WAS WRONG. The clean report rendered `bull_case` and `bear_case`
 * verbatim from the deterministic Phase-9 layer. Those sections are written
 * for an engineer: on live PNDORA / CFR / MRNA reports their points named
 * source tiers (`T1_primary_filing`, `T6_model_estimate`), provider states
 * (`free_real_not_sourced`), machine field paths (`identity.isin`) and
 * blocking-gap counts. A reader opening "Bear case" was told, as the argument
 * against owning the business, that an ISIN had not been sourced.
 *
 * Meanwhile the council HAD argued both cases — in `implications`, in the
 * chair's `strongest_positive_evidence` / `strongest_negative_evidence`, in
 * `resilience_factors` / `fragility_factors`, in `what_would_strengthen` /
 * `what_would_weaken`, and in the red team's own economic challenges. None of
 * it reached these two sections.
 *
 * So the cases are now ASSEMBLED from that structured output. Deterministic
 * rendering only: no model is called because a report was opened, nothing is
 * summarised, and every line is something the council already wrote. Each line
 * passes the same signal rule the rest of the reader-facing view uses, so an
 * evidence statement can never appear as an investment argument — it is routed
 * to research confidence, where it describes what it actually describes.
 *
 * LEGACY. A report whose council predates the structured fields still renders:
 * the deterministic narrative is used, but each point is put through the same
 * routing and its implementation vocabulary is translated. The unedited
 * original stays on the technical report page, and no stored record is
 * changed.
 */
export interface InvestmentCase {
  groups: NarrativeGroup[];
  /**
   * Where the argument came from. "council" is the structured council output;
   * "legacy" is the deterministic narrative, routed and translated; "none"
   * means neither produced an argument, which the section states.
   */
  basis: "council" | "legacy" | "none";
}

export interface InvestmentCases {
  bull: InvestmentCase;
  bear: InvestmentCase;
  /** Statements routed out of either case because their subject was evidence. */
  routedLimitations: string[];
  /** Record-completeness entries routed out of either case. */
  recordGaps: string[];
}

/** A directional point rendered as one line: the claim, then its mechanism. */
function pointLine(point: DirectionalPoint): string {
  const mechanism = point.mechanism?.trim();
  return mechanism ? `${point.statement} — ${mechanism}` : point.statement;
}

function nonEmptyGroup(
  label: string,
  points: string[],
): NarrativeGroup | null {
  const cleaned = points.map((p) => humaniseTechnical(p.trim())).filter(Boolean);
  const seen = new Set<string>();
  const deduped = cleaned.filter((p) => {
    const key = p.toLowerCase();
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
  return deduped.length > 0 ? { label, points: deduped } : null;
}

export function buildInvestmentCases(
  reading: InvestmentReading,
  agents: CouncilAgentDetail[],
  legacyBull: NarrativeGroup[],
  legacyBear: NarrativeGroup[],
): InvestmentCases {
  const routedLimitations: string[] = [];
  const recordGaps: string[] = [];

  /** Keep only what is an economic argument; file the rest where it belongs. */
  const economic = (points: string[], context: SignalContext): string[] => {
    const kept: string[] = [];
    for (const point of points) {
      const value = point.trim();
      if (!value) continue;
      const signal = classifySignal(value, context);
      if (signal === "technical_gap") recordGaps.push(value);
      else if (!isEconomicSignal(signal)) routedLimitations.push(value);
      else kept.push(value);
    }
    return kept;
  };

  // ── Bull ────────────────────────────────────────────────────────────────
  const bullGroups: NarrativeGroup[] = [];
  const positive = nonEmptyGroup(
    "The committee's strongest positive evidence",
    reading.chairPositive,
  );
  if (positive) bullGroups.push(positive);

  const higher = nonEmptyGroup(
    "What could make the business more valuable",
    reading.couldDriveHigher.map(pointLine),
  );
  if (higher) bullGroups.push(higher);

  const resilience = nonEmptyGroup(
    "What limits the downside",
    reading.resilience.map(pointLine),
  );
  if (resilience) bullGroups.push(resilience);

  // The catalyst agent's OWN interpretation of the events it read. A catalyst
  // is only part of the positive case when the agent said it pointed that way.
  const catalystAgent = findAgent(agents, "catalyst");
  const catalystPoints = economic(
    (catalystAgent?.implications ?? [])
      .filter((i) => i.direction === "supportive")
      .map((i) => (i.mechanism ? `${i.statement} — ${i.mechanism}` : i.statement)),
    { agent: "catalyst", slot: "catalyst" },
  );
  const catalystGroup = nonEmptyGroup(
    "Developments that could change the picture",
    catalystPoints,
  );
  if (catalystGroup) bullGroups.push(catalystGroup);

  const strengthen = nonEmptyGroup(
    "What would strengthen the case",
    economic(reading.whatWouldStrengthen, {
      agent: "committee_chair",
      slot: "chair_positive",
    }),
  );
  if (strengthen) bullGroups.push(strengthen);

  // ── Bear ────────────────────────────────────────────────────────────────
  const bearGroups: NarrativeGroup[] = [];
  const negative = nonEmptyGroup(
    "The committee's strongest negative evidence",
    reading.chairNegative,
  );
  if (negative) bearGroups.push(negative);

  const pressure = nonEmptyGroup(
    "What could pressure the business",
    reading.couldPressure.map(pointLine),
  );
  if (pressure) bearGroups.push(pressure);

  const fragility = nonEmptyGroup(
    "Where it is fragile",
    reading.fragility.map(pointLine),
  );
  if (fragility) bearGroups.push(fragility);

  // The red team's ECONOMIC challenge. Its `concerns` are overwhelmingly about
  // the evidence and are routed to research confidence by the same rule; its
  // implications are where its argument against the business actually is.
  const redTeam = findAgent(agents, "red_team");
  const redTeamPoints = economic(
    (redTeam?.implications ?? []).map((i) =>
      i.mechanism ? `${i.statement} — ${i.mechanism}` : i.statement,
    ),
    { agent: "red_team", slot: "implication", direction: "pressuring" },
  );
  const redTeamGroup = nonEmptyGroup(
    "The red team's challenge",
    redTeamPoints,
  );
  if (redTeamGroup) bearGroups.push(redTeamGroup);

  const weaken = nonEmptyGroup(
    "What would weaken the case",
    economic(reading.whatWouldWeaken, {
      agent: "committee_chair",
      slot: "chair_negative",
    }),
  );
  if (weaken) bearGroups.push(weaken);

  // ── Legacy fallback ─────────────────────────────────────────────────────
  //
  // Only when the council produced no argument at all. The deterministic
  // narrative is routed through the SAME rule and translated, so an old report
  // stays readable without bringing tier codes and field paths back with it.
  const routeLegacy = (
    groups: NarrativeGroup[],
    slot: SignalContext["slot"],
  ): NarrativeGroup[] => {
    const out: NarrativeGroup[] = [];
    for (const group of groups) {
      const kept = economic(group.points, { agent: "deterministic", slot });
      const built = nonEmptyGroup(group.label, kept);
      if (built) out.push(built);
    }
    return out;
  };

  let bull: InvestmentCase = { groups: bullGroups, basis: "council" };
  if (bullGroups.length === 0) {
    const legacy = routeLegacy(legacyBull, "chair_positive");
    bull = {
      groups: legacy,
      basis: legacy.length > 0 ? "legacy" : "none",
    };
  }

  let bear: InvestmentCase = { groups: bearGroups, basis: "council" };
  if (bearGroups.length === 0) {
    const legacy = routeLegacy(legacyBear, "chair_negative");
    bear = {
      groups: legacy,
      basis: legacy.length > 0 ? "legacy" : "none",
    };
  }

  return { bull, bear, routedLimitations, recordGaps };
}

// ---------------------------------------------------------------------------
// Research confidence
// ---------------------------------------------------------------------------

export interface ResearchConfidenceView {
  /** The limitations that carry analytical weight, in the report's own words. */
  limitations: string[];
  /** How many machine-level gaps exist, without listing them here. */
  technicalGapCount: number;
  /**
   * Record-completeness entries routed out of the bear case and the chair's
   * open questions. They are reported, in full, where they belong — nothing is
   * dropped, it is filed.
   */
  recordGaps: string[];
  /** The council's own citation-review failures, when any. */
  unsupportedClaims: string[];
}

export function buildResearchConfidence(
  risks: RiskGroups,
  missingTotal: number,
  agents: CouncilAgentDetail[],
  recordGaps: string[] = [],
  /** Statements routed out of the investment sections by the signal rule. */
  routed: string[] = [],
): ResearchConfidenceView {
  const limitations: string[] = [];
  for (const group of risks.researchLimitations) {
    limitations.push(...group.points);
  }
  const critic = findAgent(agents, "source_quality_critic");
  for (const concern of critic?.concerns ?? []) limitations.push(concern.item);
  // The Source Quality Critic's whole output belongs here, including anything
  // it phrased economically — its subject is the evidence, and evidence
  // weakness changes CONFIDENCE in a conclusion, not the conclusion.
  for (const imp of critic?.implications ?? []) limitations.push(imp.statement);
  limitations.push(...routed);

  const seen = new Set<string>();
  const deduped = limitations.filter((l) => {
    const key = l.trim().toLowerCase();
    if (!key || seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  const unsupported: string[] = [];
  for (const agent of agents) unsupported.push(...agent.unsupportedClaims);

  const seenGaps = new Set<string>();
  const dedupedGaps = recordGaps.filter((g) => {
    const key = g.trim().toLowerCase();
    if (!key || seenGaps.has(key)) return false;
    seenGaps.add(key);
    return true;
  });

  return {
    limitations: deduped,
    technicalGapCount: missingTotal,
    recordGaps: dedupedGaps,
    unsupportedClaims: unsupported,
  };
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

export interface InvestorReportView {
  agents: CouncilAgentDetail[];
  chair: ChairView;
  /** What the council concluded about the business, grouped for a reader. */
  reading: InvestmentReading;
  businessQuality: BusinessQualityView;
  catalysts: CatalystsView;
  risks: RiskGroups;
  openQuestions: OpenQuestion[];
  /**
   * Every record-completeness entry routed out of a narrative section, so the
   * research-confidence section can report them without any being lost.
   */
  recordGaps: string[];
  /**
   * Statements routed out of the investment sections because their subject was
   * the EVIDENCE, not the company. Source weakness changes confidence in a
   * conclusion; it does not change a company's value.
   */
  routedLimitations: string[];
}

export function buildInvestorReportView(
  contentMarkdown: string | null,
  council: LlmCouncilMetadata | null,
): InvestorReportView {
  const content = extractFinalReportContent(contentMarkdown);
  const agents = buildCouncilAgentDetails(council);
  const rawSynthesis =
    (council?.agents ?? []).find((a) => a.agent_name === "committee_chair")
      ?.synthesis ?? null;
  const chair = buildChair(content, agents, rawSynthesis);
  const risks = buildRiskGroups(content);
  const reading = buildInvestmentReading(chair, agents);
  const { questions, recordGaps, researchLimitations } = buildOpenQuestions(
    chair,
    agents,
  );

  // The chair's open-question list is deterministic and record-shaped on live
  // reports. Whether or not it was the source above, its record entries are
  // still reported — under research confidence, where they describe what they
  // actually describe.
  const chairRecords = partitionRecordGaps(chair.openQuestions).recordGaps;

  return {
    agents,
    chair,
    reading,
    businessQuality: buildBusinessQuality(agents),
    catalysts: buildCatalysts(content, agents),
    risks,
    openQuestions: questions,
    recordGaps: [...recordGaps, ...chairRecords],
    // Everything routed out of an investment section because its subject was
    // the evidence rather than the company. Reported under research
    // confidence — nothing is dropped, it is filed.
    routedLimitations: [
      ...reading.routedLimitations,
      ...risks.routedLimitations,
      ...researchLimitations,
    ],
  };
}
