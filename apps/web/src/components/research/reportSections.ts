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

import type { LlmCouncilAgent, LlmCouncilMetadata } from "@/types/api";
import {
  extractFinalReportContent,
  noteText,
  unwrap,
  type ReportContent,
} from "@/components/reports/finalReportContent";
import { isRecordGapStatement, partitionRecordGaps } from "./recordGaps";
import {
  asRecord,
  fieldText,
  stringList,
  type FinancialDatapoint,
  type FinancialSnapshotView,
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

export interface CouncilAgentDetail {
  name: string;
  label: string;
  role: string | null;
  status: string;
  completed: boolean;
  summary: string | null;
  /** The agent's own key findings, each with the confidence it stated. */
  findings: { claim: string; confidence: string | null; dataQuality: string | null }[];
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
}

export function buildChair(
  content: ReportContent | null,
  agents: CouncilAgentDetail[],
): ChairView {
  const section = asRecord(content?.["committee_chair_summary"]);
  const chairAgent = findAgent(agents, "committee_chair");
  if (!section) {
    return {
      present: Boolean(chairAgent?.summary),
      summary: null,
      agentSummary: chairAgent?.summary ?? null,
      balance: null,
      internalStatus: null,
      openQuestions: [],
      nextSteps: [],
      note: null,
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

export function buildRiskGroups(content: ReportContent | null): RiskGroups {
  const section = asRecord(content?.["risk_analysis"]);
  const collect = (defs: { field: string; label: string }[]) => {
    const out: { label: string; points: string[] }[] = [];
    for (const { field, label } of defs) {
      const points = stringList(section?.[field]);
      if (points.length > 0) out.push({ label, points });
    }
    return out;
  };
  return {
    company: collect(COMPANY_RISK_FIELDS),
    researchLimitations: collect(RESEARCH_LIMITATION_FIELDS),
    summary: fieldText(section?.["risk_summary_text"]),
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
): { questions: OpenQuestion[]; recordGaps: string[] } {
  const seen = new Set<string>();
  const questions: OpenQuestion[] = [];
  const recordGaps: string[] = [];

  const push = (question: string, source: string) => {
    const value = question.trim();
    const key = value.toLowerCase();
    if (!key || seen.has(key)) return;
    seen.add(key);
    if (isRecordGapStatement(value)) recordGaps.push(value);
    else questions.push({ question: value, source });
  };

  const redTeam = findAgent(agents, "red_team");
  for (const c of redTeam?.concerns ?? []) push(c.item, "Red team");

  for (const agent of agents) {
    if (agent.name === "red_team") continue;
    for (const c of agent.concerns) push(c.item, agent.label);
  }

  // Only when there is no council to read.
  if (agents.length === 0) {
    for (const q of chair.openQuestions) push(q, "Chair");
  }

  return { questions, recordGaps };
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
): ResearchConfidenceView {
  const limitations: string[] = [];
  for (const group of risks.researchLimitations) {
    limitations.push(...group.points);
  }
  const critic = findAgent(agents, "source_quality_critic");
  for (const concern of critic?.concerns ?? []) limitations.push(concern.item);

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
  businessQuality: BusinessQualityView;
  catalysts: CatalystsView;
  risks: RiskGroups;
  openQuestions: OpenQuestion[];
  /**
   * Every record-completeness entry routed out of a narrative section, so the
   * research-confidence section can report them without any being lost.
   */
  recordGaps: string[];
}

export function buildInvestorReportView(
  contentMarkdown: string | null,
  council: LlmCouncilMetadata | null,
): InvestorReportView {
  const content = extractFinalReportContent(contentMarkdown);
  const agents = buildCouncilAgentDetails(council);
  const chair = buildChair(content, agents);
  const risks = buildRiskGroups(content);
  const { questions, recordGaps } = buildOpenQuestions(chair, agents);

  // The chair's open-question list is deterministic and record-shaped on live
  // reports. Whether or not it was the source above, its record entries are
  // still reported — under research confidence, where they describe what they
  // actually describe.
  const chairRecords = partitionRecordGaps(chair.openQuestions).recordGaps;

  return {
    agents,
    chair,
    businessQuality: buildBusinessQuality(agents),
    catalysts: buildCatalysts(content, agents),
    risks,
    openQuestions: questions,
    recordGaps: [...recordGaps, ...chairRecords],
  };
}
